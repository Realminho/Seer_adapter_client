"""tomlkit 기반 TOML 수술 편집기.

:mod:`core.hcledit` 와 같은 경로 기반 인터페이스를 제공한다. 상위 계층(EPR 적용 경로)이
파일 형식을 몰라도 되게 하려는 것이다.

`tomlkit` 은 스타일 보존 파서다. 실측: 실제 306줄 `config.toml` 을 무수정 왕복하면
**바이트 동일**이고, 값 하나를 고치면 **그 줄만** 바뀐다(주석·정렬 그대로). 손수 만든
줄 치환기와 달리 여러 줄 리스트 원소와 root array-of-tables 안쪽까지 닿는다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import tomlkit
from tomlkit.items import AoT, Array, Table
from tomlkit.toml_document import TOMLDocument

#: 경로 한 마디. 테이블·키는 str, 배열 원소는 int
PathStep = Union[str, int]
#: 값 하나를 지목하는 경로
ValuePath = Tuple[PathStep, ...]


class FoundValue:
    """스캔 결과 항목 하나.

    :ivar path: 값을 지목하는 경로
    :ivar value: 파이썬 값
    :ivar line: 1부터 세는 줄 번호. 구할 수 없으면 0
    """

    __slots__ = ("path", "value", "line", "raw")

    def __init__(self, path: ValuePath, value: Any, line: int = 0, raw: str = "") -> None:
        self.path = path
        self.value = value
        self.line = line
        self.raw = raw

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"FoundValue({self.path!r}, {self.value!r})"


def parse(text: str) -> TOMLDocument:
    """스타일을 보존한 문서로 읽는다.

    :param text: TOML 텍스트
    :returns: tomlkit 문서
    """
    return tomlkit.parse(text)


def dumps(document: TOMLDocument) -> str:
    """문서를 다시 텍스트로 만든다. 편집하지 않았다면 원문과 바이트 동일하다.

    :param document: tomlkit 문서
    :returns: TOML 텍스트
    """
    return tomlkit.dumps(document)


def _plain(value: Any) -> Any:
    """tomlkit 래퍼를 순수 파이썬 값으로 바꾼다."""
    if isinstance(value, (Table, dict)):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (AoT, Array, list)):
        return [_plain(v) for v in value]
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value


def _line_of(node: Any) -> int:
    """값 노드의 줄 번호를 구한다. tomlkit 이 안 주면 0."""
    trivia = getattr(node, "trivia", None)
    return getattr(trivia, "line", 0) or 0


def _raw_of(node: Any) -> str:
    """값 노드의 원문. 여러 줄인지 판정하는 데 쓴다. 구할 수 없으면 빈 문자열."""
    as_string = getattr(node, "as_string", None)
    if not callable(as_string):
        return ""
    try:
        return str(as_string())
    except Exception:  # noqa: BLE001 - 원문은 부가 정보다. 못 구해도 스캔은 계속한다
        return ""


def _walk(node: Any, prefix: ValuePath, found: Dict[ValuePath, FoundValue]) -> None:
    if isinstance(node, (Table, TOMLDocument, dict)):
        items = node.items()
    elif isinstance(node, (AoT, Array, list)):
        items = enumerate(node)
    else:
        return
    for step, child in items:
        path = prefix + (step,)
        found[path] = FoundValue(path, _plain(child), _line_of(child), _raw_of(child))
        _walk(child, path, found)


def scan_values(text: str) -> Dict[ValuePath, FoundValue]:
    """편집 가능한 모든 값을 경로별로 훑는다.

    컨테이너 자체와 그 내부를 모두 담는다. 호출자가 필요한 입도를 고른다.

    :param text: TOML 텍스트
    :returns: 경로 → 찾은 값
    """
    found: Dict[ValuePath, FoundValue] = {}
    _walk(parse(text), (), found)
    return found


def _navigate(
    document: TOMLDocument, path: ValuePath, *, create: bool = False
) -> Tuple[Any, PathStep]:
    """마지막 마디 직전까지 내려가 (부모, 마지막 마디) 를 돌려준다.

    :param create: 참이면 없는 중간 **테이블**을 만들어 가며 내려간다. 배열 인덱스는
        만들지 않는다 — 없는 원소를 만들면 뒤 인덱스가 밀려 다른 요청의 경로가 조용히
        다른 값을 가리킨다
    :raises KeyError: 중간 경로가 없음(또는 create 없이 없는 테이블을 지남)
    """
    if not path:
        raise KeyError("빈 경로")
    node: Any = document
    for step in path[:-1]:
        try:
            node = node[step]
            continue
        except (KeyError, IndexError, TypeError) as exc:
            if not (create and isinstance(step, str) and hasattr(node, "__setitem__")):
                raise KeyError(f"경로를 찾을 수 없음: {path} (막힌 마디 {step!r})") from exc
        # 기본값만 있는 섹션은 파일에 아예 없다(`[pio_advanced]`). 그 섹션의 값을
        # 처음 고정하려면 테이블부터 만들어야 한다. 로더가 아는 키인지는 상위
        # 계층의 creatable_keys 가 이미 걸렀다.
        node[step] = tomlkit.table()
        node = node[step]
    return node, path[-1]


def set_value(text: str, path: ValuePath, value: Any, *, create: bool = False) -> str:
    """경로가 가리키는 값만 바꾼다.

    :param text: 원본 TOML 텍스트
    :param path: 값 경로
    :param value: 새 값
    :param create: 참이면 없는 키를, 필요하면 없는 중간 테이블까지 만들어 적어 넣는다
        (dataclass 기본값 고정용). 배열 인덱스는 만들지 않는다
    :returns: 값이 바뀐 텍스트
    :raises KeyError: 경로를 찾지 못함(또는 create 없이 없는 키를 씀)
    """
    document = parse(text)
    parent, last = _navigate(document, path, create=create)
    try:
        parent[last]
    except (KeyError, IndexError, TypeError) as exc:
        if not (create and isinstance(last, str)):
            raise KeyError(f"경로를 찾을 수 없음: {path} (막힌 마디 {last!r})") from exc
    parent[last] = value
    return dumps(document)


def remove_value(text: str, path: ValuePath) -> str:
    """경로가 가리키는 키를 지운다(기본값 복원용 unset).

    배열 원소는 지우지 않는다. 지우면 남은 원소의 인덱스가 밀려 다른 요청의 경로가
    조용히 다른 값을 가리킨다.

    :param text: 원본 TOML 텍스트
    :param path: 지울 키 경로
    :returns: 해당 키가 빠진 텍스트
    :raises KeyError: 경로를 찾지 못함
    :raises ValueError: 배열 원소를 지우려 함
    """
    if any(isinstance(step, int) for step in path):
        raise ValueError(f"배열 원소는 지울 수 없음(인덱스가 밀림): {path}")
    document = parse(text)
    parent, last = _navigate(document, path)
    try:
        del parent[last]
    except (KeyError, IndexError, TypeError) as exc:
        raise KeyError(f"경로를 찾을 수 없음: {path}") from exc
    return dumps(document)
