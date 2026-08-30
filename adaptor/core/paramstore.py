"""EPR source 라우팅과 opaque key 직렬화.

WCS 설비 파라미터 레지스트리(EPR)는 항목을 `{source}:{path}` opaque 문자열로 지목한다.
WCS 는 이 문자열을 파싱하지 않고 그대로 돌려주며, 해석은 설비(여기)가 한다.

형식이 다른 파일을 같은 계약으로 다루기 위해 편집기 두 개를 여기서 라우팅한다.

    config.toml      → core.tomledit  (tomlkit, 스타일 보존)
    extensions.hcl   → core.hcledit   (tree-sitter, 바이트 수술)
    recipes.hcl      → core.hcledit
    robots.hcl       → core.hcledit

경로 표기::

    config.toml:mqtt_broker.port
    config.toml:motion_rules[0].mode
    extensions.hcl:elevator.motion_rules[2].mode
    recipes.hcl:openDoor.pioWriteOut#1.parameters.state

`#1` 은 같은 라벨 블록이 반복될 때의 순번이다(`step "pioWriteOut"` 이 켜기/끄기로 두 번).
`[N]` 은 리스트 원소 번호다. 둘 다 안 붙이면 다른 항목을 고치게 된다.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from core import hcledit, tomledit

#: 경로 한 마디
PathStep = hcledit.PathStep
#: 값 하나를 지목하는 경로
ValuePath = Tuple[PathStep, ...]

#: EPR 이 다루는 설정 파일. 파일명이 곧 source 이름이다
SOURCES: Tuple[str, ...] = ("config.toml", "extensions.hcl", "recipes.hcl", "robots.hcl")

_EDITORS = {
    "config.toml": tomledit,
    "extensions.hcl": hcledit,
    "recipes.hcl": hcledit,
    "robots.hcl": hcledit,
}

#: `name[3]` 형태를 이름과 인덱스로 가른다
_INDEXED = re.compile(r"^([^\[\]]*)((?:\[\d+\])+)$")
_INDEX = re.compile(r"\[(\d+)\]")


def editor_for(source: str):
    """source 에 맞는 편집기 모듈을 돌려준다.

    :param source: 설정 묶음 이름(파일명)
    :returns: `scan_values`/`set_value`/`remove_value` 를 가진 모듈
    :raises ValueError: 모르는 source
    """
    editor = _EDITORS.get(source)
    if editor is None:
        raise ValueError(f"편집기를 모르는 source: {source!r} (가능: {', '.join(SOURCES)})")
    return editor


def encode_key(source: str, path: ValuePath) -> str:
    """경로를 EPR opaque key 로 만든다.

    :param source: 설정 묶음 이름
    :param path: 값 경로
    :returns: `{source}:{path}` 문자열
    """
    parts = []
    for step in path:
        if isinstance(step, int):
            if not parts:
                raise ValueError(f"경로가 인덱스로 시작할 수 없음: {path}")
            parts[-1] += f"[{step}]"
        else:
            parts.append(str(step))
    return f"{source}:{'.'.join(parts)}"


def decode_key(key: str) -> Tuple[str, ValuePath]:
    """EPR opaque key 를 (source, 경로) 로 되돌린다.

    :param key: `{source}:{path}` 문자열
    :returns: (source, 경로)
    :raises ValueError: 형식이 맞지 않음
    """
    source, sep, rest = key.partition(":")
    if not sep or not source or not rest:
        raise ValueError(f"key 형식이 '{{source}}:{{path}}' 가 아님: {key!r}")

    path: list = []
    for part in rest.split("."):
        if not part:
            raise ValueError(f"빈 경로 마디: {key!r}")
        match = _INDEXED.match(part)
        if match is None:
            if "[" in part or "]" in part:
                raise ValueError(f"인덱스 표기가 깨짐: {key!r}")
            path.append(part)
            continue
        name, indices = match.groups()
        if name:
            path.append(name)
        elif not path:
            raise ValueError(f"경로가 인덱스로 시작함: {key!r}")
        path.extend(int(n) for n in _INDEX.findall(indices))
    return source, tuple(path)


def scan(source: str, text: str) -> Dict[ValuePath, Any]:
    """source 의 텍스트에서 편집 가능한 값을 전부 훑는다.

    :param source: 설정 묶음 이름
    :param text: 파일 텍스트
    :returns: 경로 → 편집기의 FoundValue
    """
    return editor_for(source).scan_values(text)


def set_value(source: str, text: str, path: ValuePath, value: Any, *, create: bool = False) -> str:
    """경로가 가리키는 값만 바꾼다.

    :param source: 설정 묶음 이름
    :param text: 원본 텍스트
    :param path: 값 경로
    :param value: 새 값
    :param create: 없는 키를 새로 적어 넣을지(기본값 고정용)
    :returns: 바뀐 텍스트
    """
    editor = editor_for(source)
    if create:
        return editor.set_value(text, path, value, create=True)
    return editor.set_value(text, path, value)


def remove_value(source: str, text: str, path: ValuePath) -> str:
    """경로가 가리키는 키를 지운다(기본값 복원).

    :param source: 설정 묶음 이름
    :param text: 원본 텍스트
    :param path: 지울 키 경로
    :returns: 바뀐 텍스트
    """
    return editor_for(source).remove_value(text, path)
