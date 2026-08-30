"""tree-sitter 기반 HCL 수술 편집기.

파싱한 값을 다시 써 내려가는 방식(`hcl2.dumps` / `hcl2.reconstruct`)은 쓸 수 없다.
실측: 아무것도 고치지 않고 왕복만 해도 `extensions.hcl` 189줄 중 236줄이 바뀐다
(`dumps`는 주석 전체 소실, `reconstruct`는 정렬 소실). 이 파일들은 운영자가 읽는
문서이고 로봇 web UI에서 손으로도 고치는 대상이라 그 손실을 받아들일 수 없다.

그래서 **바이트 범위 교체**를 한다. tree-sitter 가 HCL 공식 문법으로 CST 를 만들고,
각 노드가 정확한 바이트 범위를 갖는다. 값 노드의 범위만 갈아끼우므로 **편집하지 않은
부분은 손댈 수가 없다** — 주석·정렬 보존이 후처리 노력이 아니라 구조적 결과다.

경로는 튜플로 지목한다. 블록 라벨, 속성 이름, 맵 키는 문자열이고 리스트 원소는 정수다::

    ("pio", "pio_baudrate")                    extension "pio" { pio_baudrate = … }
    ("pio", "output_signals", "elevatorOpen")  여러 줄 맵 내부
    ("pio", "motion_rules", 2)                 리스트 3번째 원소
    ("openDoor", "pioSelect", "timeout_sec")   recipe 안 step 블록

읽기(의미 해석·`${var.X}` 치환·블록 정규화)는 계속 `python-hcl2` 가 담당한다.
여기는 편집 전용이다.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple, Union

from tree_sitter import Language, Node, Parser
import tree_sitter_hcl

class RawExpression(str):
    """리터럴이 아닌 HCL 표현식 원문(`var.pioStationId` 등).

    문자열로 취급하면 되쓸 때 따옴표가 붙어 recipe 의 파라미터 자리표시자가
    literal 문자열로 뭉개진다. 이 타입은 :func:`to_literal` 이 원문 그대로 내보낸다.
    """

    __slots__ = ()


#: 경로 한 마디. 블록 라벨·속성명·맵 키는 str, 리스트 원소는 int
PathStep = Union[str, int]
#: 값 하나를 지목하는 경로
ValuePath = Tuple[PathStep, ...]

_PARSER = Parser(Language(tree_sitter_hcl.language()))

#: 리스트/맵 구조에서 구분자로만 쓰여 원소가 아닌 노드.
#: tree-sitter-hcl 은 괄호를 `tuple_start`/`object_end` 같은 이름 붙은 노드로 준다.
#: 이걸 안 걸러내면 리스트 인덱스가 통째로 밀려 다른 원소를 고치게 된다.
_PUNCTUATION = frozenset({
    "[", "]", "{", "}", ",", ":", "=",
    "tuple_start", "tuple_end", "object_start", "object_end",
    "comment", "new_line", "\n",
})


class FoundValue:
    """스캔 결과 항목 하나.

    :ivar path: 값을 지목하는 경로
    :ivar value: 파싱된 파이썬 값. 해석 불가하면 원문 문자열
    :ivar line: 1부터 세는 줄 번호
    :ivar raw: 값 원문
    """

    __slots__ = ("path", "value", "line", "raw")

    def __init__(self, path: ValuePath, value: Any, line: int, raw: str) -> None:
        self.path = path
        self.value = value
        self.line = line
        self.raw = raw

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"FoundValue({self.path!r}, {self.value!r}, line={self.line})"


def _text(src: bytes, node: Node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8")


def parse_error_count(text: str) -> int:
    """문법 오류 노드 수를 센다.

    :param text: HCL 텍스트
    :returns: ERROR/MISSING 노드 개수. 0이면 문법이 온전함
    """
    root = _PARSER.parse(text.encode("utf-8")).root_node

    def count(node: Node) -> int:
        total = 1 if node.type == "ERROR" or node.is_missing else 0
        for child in node.children:
            total += count(child)
        return total

    return count(root)


def _block_label(src: bytes, block: Node) -> Optional[str]:
    """블록 노드의 이름을 구한다. 라벨이 있으면 마지막 라벨, 없으면 블록 타입명."""
    labels = [_text(src, c).strip('"') for c in block.children if c.type == "string_lit"]
    if labels:
        return labels[-1]
    ident = next((c for c in block.children if c.type == "identifier"), None)
    return _text(src, ident) if ident else None


def _bodies(node: Node) -> List[Node]:
    return [c for c in node.children if c.type == "body"]


def _labelled_blocks(src: bytes, body: Node) -> List[Tuple[str, Node]]:
    """본문의 블록을 (경로 마디, 블록) 목록으로 만든다.

    **같은 라벨이 여러 번 나올 수 있다.** recipe 안의 `step "pioWriteOut"` 은 켜고 끄는
    두 단계로 두 번 선언된다. 라벨만 쓰면 스캔은 마지막 것을, 조회는 첫 것을 가리켜
    엉뚱한 단계를 고치게 된다. 그래서 두 번째부터 `라벨#N` 으로 구분한다.

    :param src: 원본 바이트
    :param body: 본문 노드
    :returns: (경로 마디, 블록 노드) 목록. 선언 순서를 유지함
    """
    seen: Dict[str, int] = {}
    out: List[Tuple[str, Node]] = []
    for node in body.children:
        if node.type != "block":
            continue
        label = _block_label(src, node)
        if label is None:
            continue
        count = seen.get(label, 0)
        seen[label] = count + 1
        out.append((label if count == 0 else f"{label}#{count}", node))
    return out


def _unwrap(expr: Node) -> Node:
    """expression 노드에서 실제 값 노드(literal/object/tuple)를 꺼낸다.

    자식이 하나일 때만 내려간다. `var.pioStationId` 는 `variable_expr` + `get_attr`
    두 자식을 갖는데, 첫 자식만 취하면 값이 `var` 로 잘려서 되쓸 때 recipe 의
    파라미터 자리표시자가 문자열 `"var"` 로 뭉개진다.
    """
    node = expr
    while (
        len(node.children) == 1
        and node.type in ("expression", "literal_value", "collection_value")
    ):
        node = node.children[0]
    return node


def _parse_literal(raw: str) -> Any:
    """값 원문을 파이썬 값으로 바꾼다. 해석 불가하면 원문을 그대로 돌려준다."""
    text = raw.strip()
    if not text:
        return text
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    if text == "null":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def to_literal(value: Any) -> str:
    """파이썬 값을 HCL 리터럴 원문으로 만든다.

    :param value: 쓸 값
    :returns: 파일에 그대로 들어갈 원문
    :raises ValueError: HCL 로 표현할 수 없는 타입
    """
    if isinstance(value, RawExpression):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(to_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{k} = {to_literal(v)}" for k, v in value.items())
        return "{ " + inner + " }"
    raise ValueError(f"HCL 로 표현할 수 없는 타입: {type(value).__name__}")


def _children_of(src: bytes, node: Node) -> List[Tuple[PathStep, Node]]:
    """값 노드의 하위 요소를 (경로 마디, 값 노드) 목록으로 편다.

    - object → 맵 키별
    - tuple  → 0부터의 인덱스별
    """
    out: List[Tuple[PathStep, Node]] = []
    if node.type == "object":
        for elem in node.children:
            if elem.type != "object_elem":
                continue
            parts = [c for c in elem.children if c.type not in _PUNCTUATION]
            if len(parts) < 2:
                continue
            key = _text(src, parts[0]).strip('"')
            out.append((key, _unwrap(parts[-1])))
        return out
    elif node.type == "tuple":
        index = 0
        for elem in node.children:
            if elem.type in _PUNCTUATION:
                continue
            out.append((index, _unwrap(elem)))
            index += 1
    return out


def _walk_body(src: bytes, body: Node, prefix: ValuePath, found: Dict[ValuePath, FoundValue]) -> None:
    for step, block in _labelled_blocks(src, body):
        for inner_body in _bodies(block):
            _walk_body(src, inner_body, prefix + (step,), found)
    for node in body.children:
        if node.type == "attribute":
            ident = next((c for c in node.children if c.type == "identifier"), None)
            expr = next((c for c in node.children if c.type == "expression"), None)
            if ident is None or expr is None:
                continue
            _collect(src, _unwrap(expr), prefix + (_text(src, ident),), found)


def _collect(src: bytes, node: Node, path: ValuePath, found: Dict[ValuePath, FoundValue]) -> Any:
    """값 노드를 기록하고, 컨테이너면 하위까지 내려간다.

    컨테이너 값은 원문 문자열이 아니라 **구조로** 담는다. 원문 문자열로 두면
    그 값을 그대로 다시 쓸 때 따옴표가 붙어 맵이 문자열로 뭉개진다.

    :returns: 이 노드의 파이썬 값
    """
    raw = _text(src, node)
    if node.type == "object":
        value: Any = {
            step: _collect(src, child, path + (step,), found)
            for step, child in _children_of(src, node)
        }
    elif node.type == "tuple":
        value = [
            _collect(src, child, path + (step,), found)
            for step, child in _children_of(src, node)
        ]
    elif node.type == "expression":
        # _unwrap 이 더 줄이지 못한 것 = 리터럴이 아닌 표현식(var.X, 연산 등)
        value = RawExpression(raw)
    else:
        value = _parse_literal(raw)
    found[path] = FoundValue(path, value, node.start_point[0] + 1, raw)
    return value


def scan_values(text: str) -> Dict[ValuePath, FoundValue]:
    """편집 가능한 모든 값을 경로별로 훑는다.

    컨테이너 자체와 그 내부를 모두 담는다. 호출자가 필요한 입도를 고른다.

    :param text: HCL 텍스트
    :returns: 경로 → 찾은 값
    """
    src = text.encode("utf-8")
    root = _PARSER.parse(src).root_node
    found: Dict[ValuePath, FoundValue] = {}
    for body in _bodies(root):
        _walk_body(src, body, (), found)
    return found


def _locate(src: bytes, path: ValuePath) -> Node:
    """경로가 가리키는 값 노드를 찾는다.

    :raises KeyError: 경로를 찾지 못함
    """
    root = _PARSER.parse(src).root_node
    bodies = _bodies(root)
    if not bodies:
        raise KeyError(f"HCL body 없음: {path}")

    node: Optional[Node] = None
    current_bodies = bodies
    index = 0

    while index < len(path):
        step = path[index]
        if node is None:
            # 아직 블록/속성 단계다
            target = None
            for body in current_bodies:
                for label, block in _labelled_blocks(src, body):
                    if label == step:
                        target = ("block", block)
                        break
                if target:
                    break
                for child in body.children:
                    if child.type != "attribute":
                        continue
                    ident = next((c for c in child.children if c.type == "identifier"), None)
                    if ident is not None and _text(src, ident) == step:
                        expr = next((c for c in child.children if c.type == "expression"), None)
                        if expr is not None:
                            target = ("attribute", _unwrap(expr))
                            break
                if target:
                    break
            if target is None:
                raise KeyError(f"경로를 찾을 수 없음: {path} (막힌 마디 {step!r})")
            kind, hit = target
            if kind == "block":
                current_bodies = _bodies(hit)
            else:
                node = hit
            index += 1
            continue

        # 값 안으로 내려간다
        for child_step, child in _children_of(src, node):
            if child_step == step:
                node = child
                break
        else:
            raise KeyError(f"경로를 찾을 수 없음: {path} (막힌 마디 {step!r})")
        index += 1

    if node is None:
        raise KeyError(f"값이 아니라 블록을 가리킴: {path}")
    return node


def set_value(text: str, path: ValuePath, value: Any, *, create: bool = False) -> str:
    """경로가 가리키는 값만 바꾼다.

    바이트 범위 교체이므로 편집 범위 밖은 한 바이트도 바뀌지 않는다.

    :param text: 원본 HCL 텍스트
    :param path: 값 경로
    :param value: 새 값
    :param create: 참이면 없는 속성을 블록 안에 새로 적어 넣는다(기본값 고정용)
    :returns: 값이 바뀐 텍스트
    :raises KeyError: 경로를 찾지 못함(또는 create 없이 없는 키를 씀)
    :raises ValueError: HCL 로 표현할 수 없는 값
    """
    src = text.encode("utf-8")
    try:
        node = _locate(src, path)
    except KeyError:
        if not create:
            raise
        return _insert_attribute(text, path, value)
    literal = to_literal(value).encode("utf-8")
    return (src[: node.start_byte] + literal + src[node.end_byte :]).decode("utf-8")


def _insert_attribute(text: str, path: ValuePath, value: Any) -> str:
    """블록 안에 새 속성 줄을 넣는다.

    블록 닫는 `}` 바로 앞에 넣고, 들여쓰기는 같은 블록의 기존 줄에서 베낀다.
    상위 블록이 없으면 있는 데까지 내려간 뒤 없는 블록을 만들어 가며 넣는다. 기본값만
    쓰는 섹션은 파일에 아예 없기 때문이다(`pio { advanced { ... } }`). 로더가 아는
    키인지는 상위 계층의 creatable_keys 가 이미 걸렀다.

    :param text: 원본 HCL 텍스트
    :param path: 넣을 속성 경로. 마지막 마디가 속성명
    :param value: 넣을 값
    :returns: 줄이 추가된 텍스트
    :raises KeyError: 컨테이너 안에 넣으려 하거나 만들 수 없는 블록을 지정함
    """
    if len(path) < 2 or isinstance(path[-1], int):
        raise KeyError(f"블록 안 속성만 새로 넣을 수 있음: {path}")
    src = text.encode("utf-8")
    parent_path, name = path[:-1], str(path[-1])

    root = _PARSER.parse(src).root_node
    # 있는 데까지 내려간다. 남은 마디는 새로 만든다
    block: Optional[Node] = None
    depth = len(parent_path)
    while depth > 0:
        block = _find_block(src, _bodies(root), parent_path[:depth])
        if block is not None:
            break
        depth -= 1
    missing = parent_path[depth:]
    for step in missing:
        # `label#2` 는 이미 있는 중복 라벨을 가리키는 표기다. 없는 것을 만들 때는
        # 몇 번째인지가 뜻을 잃으므로 만들지 않는다
        if not isinstance(step, str) or "#" in step:
            raise KeyError(f"만들 수 없는 블록 마디: {step!r} (경로 {parent_path})")

    if block is None:
        # 최상위에 새로 만든다. 파일 끝에 붙인다
        base_indent = ""
        prefix = text if text.endswith("\n") or not text else text + "\n"
        return prefix + _nested_block(missing, name, value, base_indent)

    body = _bodies(block)
    end_node = next((c for c in block.children if c.type == "block_end"), None)
    if end_node is None:
        raise KeyError(f"블록 끝을 찾을 수 없음: {parent_path[:depth]}")

    indent = "  "
    if body:
        attrs = [c for c in body[0].children if c.type == "attribute"]
        if attrs:
            line = text.splitlines()[attrs[0].start_point[0]]
            indent = line[: len(line) - len(line.lstrip())]

    insert_at = end_node.start_byte
    # 닫는 `}` 앞 들여쓰기까지 되짚어 그 줄 앞에 넣는다
    line_start = src.rfind(b"\n", 0, insert_at) + 1
    new_line = _nested_block(missing, name, value, indent).encode("utf-8")
    return (src[:line_start] + new_line + src[line_start:]).decode("utf-8")


def _nested_block(missing: ValuePath, name: str, value: Any, indent: str) -> str:
    """없는 블록들을 감싼 속성 한 줄을 만든다.

    `missing` 이 비면 속성 줄 하나만 돌려준다.

    :param missing: 새로 만들 블록 이름들. 바깥에서 안쪽 순서
    :param name: 속성명
    :param value: 속성 값
    :param indent: 가장 바깥 줄의 들여쓰기
    :returns: 개행으로 끝나는 HCL 조각
    """
    lines = [f"{indent}{'  ' * i}{block} {{" for i, block in enumerate(missing)]
    lines.append(f"{indent}{'  ' * len(missing)}{name} = {to_literal(value)}")
    lines.extend(f"{indent}{'  ' * i}}}" for i in reversed(range(len(missing))))
    return "\n".join(lines) + "\n"


def _find_block(src: bytes, bodies: List[Node], path: ValuePath) -> Optional[Node]:
    """경로가 가리키는 블록 노드를 찾는다."""
    current = bodies
    block: Optional[Node] = None
    for step in path:
        block = None
        for body in current:
            for label, candidate in _labelled_blocks(src, body):
                if label == step:
                    block = candidate
                    break
            if block is not None:
                break
        if block is None:
            return None
        current = _bodies(block)
    return block


def remove_value(text: str, path: ValuePath) -> str:
    """경로가 가리키는 속성 줄을 지운다(기본값 복원용 unset).

    맵 항목·리스트 원소가 아니라 **블록 직속 속성**만 지운다. 컨테이너 내부 원소를
    지우면 남은 원소의 인덱스가 밀려 다른 요청의 경로가 조용히 다른 값을 가리킨다.

    :param text: 원본 HCL 텍스트
    :param path: 지울 속성 경로
    :returns: 해당 줄이 빠진 텍스트
    :raises KeyError: 경로를 찾지 못함
    :raises ValueError: 컨테이너 내부 원소를 지우려 함
    """
    if any(isinstance(step, int) for step in path):
        raise ValueError(f"리스트 원소는 지울 수 없음(인덱스가 밀림): {path}")
    src = text.encode("utf-8")
    node = _locate(src, path)

    # 값 노드에서 그 값을 담은 attribute 줄 전체로 넓힌다
    attribute = node
    while attribute.parent is not None and attribute.type != "attribute":
        attribute = attribute.parent
    if attribute.type != "attribute":
        raise KeyError(f"속성이 아님: {path}")

    lines = text.splitlines(keepends=True)
    start_line = attribute.start_point[0]
    end_line = attribute.end_point[0]
    del lines[start_line : end_line + 1]
    return "".join(lines)
