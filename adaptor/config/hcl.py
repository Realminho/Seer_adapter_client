"""HCL(HashiCorp Configuration Language) 로딩과 라벨 블록 정규화.

robots.hcl / extensions.hcl / recipes.hcl이 모두 이 모듈을 통해 읽힌다.
python-hcl2는 기본적으로 문자열에 따옴표를 남기고 __comments__ 키를 끼워
넣으므로, strip_string_quotes/with_comments 옵션을 항상 같이 준다.

explicit_blocks=True로 파싱해 __is_block__ 마커를 남겨 둔다. 라벨이 없는
블록(``robot { ... }``)이나 라벨이 하나 더 붙은 블록(``robot "A" "B" { ... }``)
은 마커 없이 보면 일반 body dict와 구별이 안 되기 때문이다(예:
``parameters = { index = 1 }``도 body가 `{key: dict}` 모양이라 똑같이
보인다). _strip_blocks가 이 마커로 라벨 개수를 검증하며 재귀적으로 벗겨내
반환값에는 마커가 남지 않는다.

var.X 참조는 파서가 평가하지 않고 "${var.X}" 문자열로 남긴다. 이걸
액션 파라미터 자리표시자로 쓰므로 그대로 보존한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple, Union

import hcl2
from hcl2.utils import SerializationOptions


_OPTIONS = SerializationOptions(
    with_comments=False,
    explicit_blocks=True,
    strip_string_quotes=True,
)

_IS_BLOCK_KEY = "__is_block__"

#: 문자열 전체가 ${var.NAME}인 경우에만 일치한다. 부분 삽입은 따로 처리한다.
PARAM_PATTERN = re.compile(r"\$\{var\.([A-Za-z_][A-Za-z0-9_]*)\}")


class HclError(ValueError):
    """HCL 파일이 없거나 문법이 잘못됐을 때."""


def load_hcl(path: Union[str, Path]) -> Dict[str, Any]:
    """HCL 파일을 읽어 정규화된 dict를 돌려준다."""
    file_path = Path(path)
    if not file_path.exists():
        raise HclError(f"HCL file not found: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            raw = hcl2.load(fh, serialization_options=_OPTIONS)
    except Exception as exc:
        raise HclError(f"invalid HCL in {file_path}: {exc}") from exc
    try:
        return _strip_blocks(raw)
    except HclError as exc:
        raise HclError(f"{exc} ({file_path})") from exc


def _strip_blocks(value: Any) -> Any:
    """__is_block__ 마커를 재귀적으로 벗겨내며 목록 항목의 라벨 개수를 검증한다."""
    if isinstance(value, list):
        return [_strip_list_item(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_blocks(item) for key, item in value.items() if key != _IS_BLOCK_KEY
        }
    return value


def _has_block_marker(value: Any) -> bool:
    """값 어딘가에 블록 마커가 있으면 True (라벨 체인을 따라 내려간다)."""
    if not isinstance(value, dict):
        return False
    if _IS_BLOCK_KEY in value:
        return True
    return any(_has_block_marker(item) for item in value.values())


def _strip_list_item(item: Any) -> Any:
    """블록 목록(``kind -> [{label: body}, ...]``)의 항목 하나를 검증/정규화한다."""
    if not isinstance(item, dict):
        return _strip_blocks(item)
    if not _has_block_marker(item):
        # 블록이 아니라 평범한 속성 값이다. 파서는 블록에만 마커를 붙이므로
        # 마커가 전혀 없으면 라벨 검증 대상이 아니다. 이 갈래가 없으면
        # `scenario = [{ type = "in" }]` 같은 정상 리스트가 블록으로 오판된다.
        return _strip_blocks(item)
    if _IS_BLOCK_KEY in item:
        # 블록 속성이 라벨 래핑 없이 항목 자신에 바로 붙어 있다 = 라벨이 없다
        # (예: `robot { vehicle_ip = "10.0.0.1" }`, 따옴표를 빠뜨린 흔한 실수).
        raise HclError("block has no label")
    if len(item) == 1:
        label, body = next(iter(item.items()))
        if isinstance(body, dict) and _IS_BLOCK_KEY not in body:
            # body 자신이 아니라 그 안의 한 키가 진짜 블록 내용을 담고 있다
            # = 라벨이 하나 더 있다(예: `robot "A" "B" { ... }`). 이 상태로
            # 두면 "B" 밑의 실제 속성이 조용히 다른 키로 들어가 버린다.
            raise HclError(f"block '{label}' has too many labels")
    return _strip_blocks(item)


def blocks(data: Mapping[str, Any], kind: str) -> List[Tuple[str, Dict[str, Any]]]:
    """``kind`` 블록들을 (라벨, 본문) 목록으로 돌려준다 (선언 순서 보존)."""
    result: List[Tuple[str, Dict[str, Any]]] = []
    for index, item in enumerate(data.get(kind, []) or []):
        if not isinstance(item, dict) or len(item) != 1:
            raise HclError(
                f"{kind} block #{index} must have exactly one label"
            )
        label, body = next(iter(item.items()))
        if not isinstance(body, dict):
            raise HclError(
                f"{kind} block #{index} ('{label}') body must be an object"
            )
        result.append((str(label), body))
    return result
