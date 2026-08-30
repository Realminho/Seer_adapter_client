"""Read / validate / edit ``config/config.toml`` for the Config view.

Editing is comment-preserving: :func:`rewrite_scalar` rewrites a single
``key = value`` line in place (keeping indentation and the aligned inline
comment) instead of re-serializing the whole document — stdlib has no TOML
writer and the file's comments are valuable operator documentation. Full
edits use ``$EDITOR`` (handled in the app).

Validation re-runs ``config.get_config()`` so a bad edit is caught against the
same dataclasses the adaptor uses at startup. Note the adaptor reads config
only once at boot, so applying changes always requires a service restart.
"""

from __future__ import annotations

import fcntl
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
    import tomli as tomllib  # type: ignore

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "config.toml"


@dataclass(frozen=True)
class ConfigLocation:
    path: str
    line: int
    key_path: str


@dataclass(frozen=True)
class ConfigScalar:
    section: str
    key: str
    kind: str
    value: Any
    location: Optional[ConfigLocation] = None
    badges: Tuple[str, ...] = field(default_factory=tuple)

    def __iter__(self):
        yield self.key
        yield self.kind
        yield self.value


@dataclass(frozen=True)
class ConfigReadonly:
    section: str
    key: str
    value: Any
    location: Optional[ConfigLocation] = None
    badges: Tuple[str, ...] = ("read-only",)

    def __iter__(self):
        yield self.key
        yield self.value

# Editable fields are auto-enumerated from the live TOML — see
# iter_config_sections() below. (The former curated HOT_FIELDS / FACTSHEET_FIELDS
# lists were removed once /config exposed every config value per-field; factsheet
# scalars now edit on /config, and /factsheet is a read-only rendered preview.)

_FIELD_DESCRIPTIONS = {
    ("mqtt_broker", "host"): (
        "기본 MQTT 브로커 주소. robots.hcl 의 mqtt_host 가 있으면 그 어댑터 인스턴스에서는 "
        "이 값을 덮어씁니다."
    ),
    ("mqtt_broker", "port"): (
        "기본 MQTT 브로커 포트. robots.hcl 의 mqtt_port 가 있으면 그 어댑터 인스턴스에서는 "
        "이 값을 덮어씁니다."
    ),
    ("vehicle", "serial_number"): (
        "기본 로봇 식별자. robots.hcl 의 robot id 가 있으면 이 값을 덮어씁니다."
    ),
    ("vehicle", "vehicle_ip"): (
        "기본 로봇 제어 주소. robots.hcl 의 vehicle_ip 가 있으면 이 값을 덮어씁니다."
    ),
    ("vehicle", "vehicle_port"): (
        "기본 로봇 제어 포트. robots.hcl 의 vehicle_port 가 있으면 이 값을 덮어씁니다."
    ),
    ("ezi", "ezi_io"): (
        "기본 EZI IO 주소. robots.hcl 의 ezi_io 가 있으면 이 값을 덮어씁니다."
    ),
    ("ezi", "ezi_motor"): (
        "기본 EZI 모터 주소. robots.hcl 의 ezi_motor 가 있으면 이 값을 덮어씁니다."
    ),
    ("sound_settings", "enabled"): "어댑터 호스트에서 OS 사운드 재생을 사용할지 여부입니다.",
    ("sound_settings", "sink"): (
        "어댑터 사운드가 나갈 PulseAudio 출력 sink. scripts/test-sound-devices.sh 로 "
        "실제 소리가 나는 sink 를 찾아 여기 적고 어댑터를 재시작합니다."
    ),
    ("sound_settings", "sound_dir"): (
        "사운드 파일이 들어 있는 디렉터리. 상대 경로는 어댑터 디렉터리 기준으로 풉니다."
    ),
    ("sound_settings", "player"): "SoundPlayer 가 쓰는 재생 프로그램. 보통 mplayer 입니다.",
    ("sound_settings", "startup_volume"): (
        "어댑터 기동 시 1회 적용하는 OS sink 볼륨(%). WebUI 사운드 프리셋의 초기값으로도 씁니다."
    ),
    ("sound_settings", "sound_test_duration_sec"): (
        "WebUI testSound 액션에 duration 을 안 주었을 때 쓰는 기본 재생 시간(초)."
    ),
    ("dock", "nodes"): (
        "도킹 작업 노드 목록. 목록은 WebUI 에서 읽기 전용이므로 config.toml 을 직접 고칩니다."
    ),
    ("dock", "stop_charging_on_arrival"): (
        "도킹 작업 노드 동작: 도킹 후 충전을 멈춰 이재 작업을 이어갈 수 있게 합니다."
    ),
    ("dock", "fail_timeout_sec"): (
        "UmDock 후 charging 상태를 기다리는 기본 시간(초). 너무 짧으면 실제 충전이 "
        "늦게 시작되어도 JIBOT_DOCK_FAILED 가 발생합니다. 충전기별 개별 설정은 "
        "motion_rules 의 fail_timeout_sec 를 씁니다."
    ),
    ("dock", "stop_charging_repeat_count"): "도크 충전을 멈출 때 UmStop 을 몇 번 반복할지입니다.",
    ("dock", "stop_charging_repeat_gap_sec"): "반복하는 UmStop 사이의 간격(초).",
    ("dock", "stop_charging_verify_timeout_sec"): (
        "stopCharging 뒤 charging=false 가 되기를 기다리는 시간(초)."
    ),
    ("dock", "start_charging_verify_timeout_sec"): (
        "startCharging instant action 뒤 charging=true 가 되기를 기다리는 시간(초)."
    ),
    ("dock", "dock_wait_poll_interval_sec"): "도크 충전이 감지될 때까지 상태를 확인하는 주기(초).",
    ("dock", "dock_approach_poll_interval_sec"): "도크 접근 자세를 확인하는 주기(초).",
    ("dock", "charging_start_poll_interval_sec"): "충전 시작을 확인하는 동안의 확인 주기(초).",
    ("dock", "charging_stop_poll_interval_sec"): "충전 정지를 확인하는 동안의 확인 주기(초).",
    ("dock", "dock_approach_unreached_delay_sec"): (
        "목표 구역 밖에 멈춰 있는 상태가 이 시간(초)을 넘으면 도크 접근을 실패로 봅니다."
    ),
}


def field_description(section: str, key: str, source: str = "config.toml") -> str:
    """Operator-facing help text for one config field.

    Known operational knobs get explicit wording. The fallback deliberately
    stays non-empty so every scalar field in /config has at least a basic
    description instead of an unlabeled raw value.

    ``source`` names the file the field actually lives in. It is a parameter
    rather than a hardcoded string because EPR publishes items from four files
    (config.toml / extensions.hcl / recipes.hcl / robots.hcl) through this same
    helper; saying "config.toml" for all of them sends operators to edit the
    wrong file. The default keeps the /config web UI, which only ever renders
    config.toml, unchanged.

    :param section: 설정 섹션명
    :param key: 필드명
    :param source: 이 필드가 실제로 들어 있는 파일 이름
    """
    if (section, key) in _FIELD_DESCRIPTIONS:
        return _FIELD_DESCRIPTIONS[(section, key)]
    return f"{source} 의 [{section}].{key} 설정입니다. 여기서 고친 뒤 어댑터 서비스를 재시작해야 반영됩니다."


#: extensions.hcl이 소유하는 섹션. Config 화면에 보이되 편집은 막는다.
#: rewrite_scalar가 TOML 한 줄을 고치는 방식이라 HCL에는 쓸 수 없기 때문이다.
EXTENSION_SECTIONS = (
    "pio",
    "pio_advanced",
    "ezi",
    "air_shower_pio",
    "elevator_pio",
)


def load_raw(path: Path = CONFIG_PATH, *, extensions_path=None) -> Any:
    """config.toml을 읽고 extensions.hcl 섹션을 합쳐 돌려준다.

    설정을 두 파일로 나눈 뒤에도 Config 화면이 PIO/EZI/설비 값을 보여줘야
    운영자가 현재 상태를 확인할 수 있다. extensions.hcl을 못 읽으면 그 부분만
    빼고 계속한다 — Config 화면이 부팅 실패를 진단하는 창구이므로 여기서
    같이 죽으면 안 된다.
    """
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)

    try:
        from config.extensions import load_extensions

        for section, body in load_extensions(extensions_path).items():
            if section in EXTENSION_SECTIONS:
                raw.setdefault(section, body)
    except Exception as exc:  # noqa: BLE001 - operator-facing view must survive
        print(f"[CONFIG VIEW] extensions.hcl not shown: {exc}")
    return raw


def read_text(path: Path = CONFIG_PATH) -> str:
    return path.read_text(encoding="utf-8")


def toml_literal(value: Any, kind: str) -> str:
    """Format a Python value as a TOML scalar literal."""
    if kind == "bool":
        return "true" if bool(value) else "false"
    if kind in ("int",):
        return str(int(value))
    if kind in ("num", "float"):
        text = str(value)
        return text
    # string
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def coerce(raw: str, kind: str) -> Any:
    raw = raw.strip()
    if kind == "bool":
        low = raw.lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ValueError("expected true/false")
    if kind == "int":
        return int(raw)
    if kind in ("num", "float"):
        return float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
    return raw


def _split_comment(line: str) -> Tuple[str, str]:
    """Split a line into (code, comment) honoring quoted strings.

    Backslash escapes inside a basic (double-quoted) string are skipped so an
    embedded ``\\"`` does not look like the closing quote; literal
    (single-quoted) TOML strings have no escapes.
    """
    in_str: Optional[str] = None
    index = 0
    length = len(line)
    while index < length:
        char = line[index]
        if in_str is not None:
            if in_str == '"' and char == "\\":
                index += 2  # consume the escaped character
                continue
            if char == in_str:
                in_str = None
        elif char in ('"', "'"):
            in_str = char
        elif char == "#":
            return line[:index], line[index:]
        index += 1
    return line, ""


def _parse_section_header(code: str) -> Optional[str]:
    """Return the section name for a ``[section]`` / ``[[array]]`` header line.

    Comments are already stripped by the caller. Array-of-tables headers
    (``[[a.b]]``) return their dotted name so their keys are never attributed to
    a plain ``[section]`` we are editing.
    """
    stripped = code.strip()
    if stripped.startswith("[[") and stripped.endswith("]]"):
        return stripped[2:-2].strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip()
    return None


def scan_scalar_locations(text: str, path: str | Path = CONFIG_PATH) -> Mapping[Tuple[str, str], ConfigLocation]:
    """Return source locations for scalar key assignments in plain TOML tables."""
    locations: dict[Tuple[str, str], ConfigLocation] = {}
    current: Optional[str] = None
    display_path = str(path)
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        code, _comment = _split_comment(raw_line)
        header = _parse_section_header(code)
        if header is not None:
            stripped = code.strip()
            current = None if stripped.startswith("[[") else header
            continue
        if current is None or "=" not in code:
            continue
        left = code.split("=", 1)[0].strip()
        if not left or any(ch.isspace() for ch in left):
            continue
        locations[(current, left)] = ConfigLocation(
            path=display_path,
            line=line_no,
            key_path=f"[{current}].{left}",
        )
    return locations


def rewrite_scalar(text: str, section: str, key: str, new_literal: str) -> str:
    """Replace the value of ``key`` under ``[section]``, preserving the comment.

    Raises ``KeyError`` if the section/key is not found so the caller can
    surface a clear error rather than silently writing nothing.
    """
    lines = text.splitlines(keepends=True)
    current: Optional[str] = None
    key_re = re.compile(r"^(\s*)(" + re.escape(key) + r")(\s*=\s*)(.*?)(\s*)$")
    for i, raw_line in enumerate(lines):
        newline = "\n" if raw_line.endswith("\n") else ""
        line = raw_line[: -len(newline)] if newline else raw_line

        code, comment = _split_comment(line)
        header = _parse_section_header(code)
        if header is not None:
            current = header
            continue
        if current != section:
            continue

        match = key_re.match(code)
        if not match:
            continue

        indent = match.group(1)
        new_code = f"{indent}{key} = {new_literal}"
        if comment:
            comment_col = len(code)  # where '#' started in the original line
            pad = max(1, comment_col - len(new_code))
            rebuilt = new_code + (" " * pad) + comment
        else:
            rebuilt = new_code.rstrip()
        lines[i] = rebuilt + newline
        return "".join(lines)

    raise KeyError(f"{section}.{key} not found in config")


# HCL 블록 헤더. `extension "pio" {` / `recipe "x" {` / `pio {` 를 모두 잡는다.
# 값 줄(`output_signals = {`)과 구별하려면 `=` 가 없어야 한다.
_HCL_BLOCK_RE = re.compile(r'^\s*[A-Za-z_][\w-]*(?:\s+"([^"]*)")*\s*\{\s*$')
_HCL_LABEL_RE = re.compile(r'"([^"]*)"')
_HCL_NAME_RE = re.compile(r"^\s*([A-Za-z_][\w-]*)")

#: 줄단위로 편집할 수 없는 값 시작 문자(맵/여러 줄 리스트)
_HCL_STRUCTURED_STARTS = ("{", "[")


def set_scalar(text: str, section: str, key: str, new_literal: str) -> str:
    """Replace ``section.key`` or append it to an existing scalar section."""
    try:
        return rewrite_scalar(text, section, key, new_literal)
    except KeyError:
        pass

    lines = text.splitlines(keepends=True)
    current: Optional[str] = None
    insert_at: Optional[int] = None
    found_section = False

    for i, raw_line in enumerate(lines):
        newline = "\n" if raw_line.endswith("\n") else ""
        line = raw_line[: -len(newline)] if newline else raw_line
        code, _comment = _split_comment(line)
        header = _parse_section_header(code)
        if header is None:
            continue
        if header == section:
            current = header
            insert_at = i + 1
            found_section = True
            continue
        if current == section:
            break
        current = header

    if not found_section or insert_at is None:
        raise KeyError(f"{section}.{key} not found in config")

    while insert_at < len(lines) and lines[insert_at].strip() and _parse_section_header(
        _split_comment(lines[insert_at].rstrip("\n"))[0]
    ) is None:
        insert_at += 1
    lines.insert(insert_at, f"{key} = {new_literal}\n")
    return "".join(lines)


def validate_on_disk(path=None) -> Tuple[bool, str]:
    """Re-parse the on-disk config via the adaptor's own loader.

    If *path* is given, that file is validated instead of the default
    ``config.toml``.  Passing ``None`` keeps the original default behaviour.
    """
    try:
        from config.config import get_config

        get_config(path) if path is not None else get_config()
        return True, "config valid"
    except Exception as exc:  # noqa: BLE001 - surface any loader error verbatim
        return False, f"{type(exc).__name__}: {exc}"


WRITE_LOCK_TIMEOUT_S = 10.0


def _atomic_write(path: Path, text: str) -> None:
    """같은 디렉터리 임시 파일에 쓴 뒤 rename 으로 갈아끼운다.

    중간에 죽어도 반쪽짜리 config.toml 이 남지 않는다. 반쪽 파일이 남으면
    adaptor 가 아예 기동하지 못해서 web UI 로도 복구할 수 없다.
    """
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_config(
    path: Path,
    transform: Any,
    *,
    validate: Any = None,
    lock_timeout: float = WRITE_LOCK_TIMEOUT_S,
) -> str:
    """설정 파일의 읽기-수정-쓰기 전체를 프로세스 간 배타적으로 수행한다.

    이 파일에는 writer 가 여러 갈래 있고(web UI 저장, adapter 의 음량 기억,
    배포 스크립트, WCS 설비 설정 형상관리의 적용) 전부 read -> 문자열 수정 ->
    write 형태다. 각자 write 만 배타적으로 해서는 부족하다. 읽은 뒤 쓰기 전에
    다른 writer 가 끼어들면 나중 writer 가 앞 writer 의 수정을 통째로 되돌린다.
    그래서 읽기부터 쓰기까지를 이 함수 하나로 묶는다.

    락은 사이드카 파일(``<name>.lock``)에 건다. 설정 파일 자체에 걸면
    :func:`_atomic_write` 의 ``os.replace`` 가 inode 를 바꿔서 락이 새 파일에
    걸리지 않는다.

    :param path: 대상 설정 파일
    :param transform: 락 안에서 다시 읽은 현재 텍스트를 받아 새 텍스트를 반환하는 함수
    :param validate: 저장 후 호출해 ``(ok, message)`` 를 받는 함수. 실패하면 원본 복구
    :param lock_timeout: 락 대기 상한(초)
    :returns: 저장된 새 텍스트
    :raises TimeoutError: 제한 시간 안에 락을 얻지 못함. 강행하지 않음
    :raises ValueError: ``validate`` 실패로 원본을 되돌림
    """
    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + lock_timeout
    with open(lock_path, "a+") as lock_file:
        while True:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"config write lock busy: {lock_path}")
                time.sleep(0.05)
        try:
            original = read_text(path)
            original_mtime_ns = path.stat().st_mtime_ns
            new_text = transform(original)
            _atomic_write(path, new_text)
            if validate is not None:
                ok, message = validate()
                if not ok:
                    _atomic_write(path, original)
                    # 되돌렸으면 파일은 쓰기 전과 같은 내용이다. mtime 까지 되돌려야
                    # EPR 리비전(4개 파일 mtime 의 최댓값)이 헛돌지 않는다. 안 그러면
                    # 실패한 적용 하나가 열려 있는 모든 스냅샷을 STALE 로 만든다.
                    os.utime(path, ns=(original_mtime_ns, original_mtime_ns))
                    raise ValueError(message)
            return new_text
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def current_value(raw_config: Any, section: str, key: str) -> Any:
    try:
        return raw_config[section][key]
    except (KeyError, TypeError):
        return None


def _kind_of(value: Any) -> Optional[str]:
    """Edit-kind for a TOML scalar, or None for lists/tables/other non-scalars.

    bool is checked before int because ``bool`` is a subclass of ``int``.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "num"
    if isinstance(value, str):
        return "str"
    return None


_ROBOT_OVERRIDE_FIELDS = {
    ("vehicle", "serial_number"),
    ("vehicle", "vehicle_ip"),
    ("vehicle", "vehicle_port"),
    ("ezi", "ezi_io"),
    ("ezi", "ezi_motor"),
    ("mqtt_broker", "host"),
    ("mqtt_broker", "port"),
}

_JIBOT_OVERLAY_FIELDS = {
    ("jibot_client", "user"),
    ("jibot_client", "password"),
    ("jibot_client", "device_type"),
}

_ADVANCED_SECTIONS = {
    "pio_advanced",
    "internal_actions",
    "web_ui",
    "bms_ros",
    "charge_circuit",
}

_ADVANCED_SUFFIXES = (
    "_sec",
    "_timeout",
    "_timeout_sec",
    "_delay",
    "_poll_sec",
    "_interval_sec",
    "_bytes",
)


def field_badges(section: str, key: str, *, readonly: bool = False) -> Tuple[str, ...]:
    badges: list[str] = []
    if readonly:
        badges.append("read-only")
    if section in EXTENSION_SECTIONS or section.split(".", 1)[0] in EXTENSION_SECTIONS:
        badges.append("extensions.hcl")
    if (section, key) in _ROBOT_OVERRIDE_FIELDS:
        badges.append("robot override")
    if section == "dock.approach_params" or (section, key) in _JIBOT_OVERLAY_FIELDS:
        badges.append("jibot overlay")
    if section in _ADVANCED_SECTIONS or key.endswith(_ADVANCED_SUFFIXES):
        badges.append("advanced")
    if not badges and not readonly:
        badges.append("base config")
    return tuple(badges)


def iter_config_sections(
    raw: Any,
    *,
    locations: Optional[Mapping[Tuple[str, str], ConfigLocation]] = None,
    source_path: str | Path = CONFIG_PATH,
) -> List[Tuple[str, List[ConfigScalar], List[ConfigReadonly]]]:
    """Enumerate every ``config.toml`` ``[section]`` for per-field editing.

    Returns a list of ``(section, scalars, readonly)`` where:
      - ``scalars``  is ``[(key, kind, value), ...]`` — each editable on its own
        (kind is "str" | "int" | "num" | "bool").
      - ``readonly`` is ``[(key, value), ...]`` — lists / arrays that the WebUi
        form does not edit (edit those directly in ``config.toml``).

    A nested table (``[a.b]``) is emitted as its own dotted-name section right
    after its parent. A top-level array (e.g. ``motion_rules``) becomes a
    section with a single read-only ``("(array)", value)`` entry.
    """
    locs = locations or {}
    source = str(source_path)

    def scalar(section: str, key: str, kind: str, val: Any) -> ConfigScalar:
        return ConfigScalar(
            section=section,
            key=key,
            kind=kind,
            value=val,
            location=locs.get((section, key)),
            badges=field_badges(section, key),
        )

    def readonly(section: str, key: str, val: Any) -> ConfigReadonly:
        location = locs.get((section, key))
        if location is None:
            key_path = f"[{section}]" if key in ("(array)", "(value)") else f"[{section}].{key}"
            location = ConfigLocation(path=source, line=0, key_path=key_path)
        return ConfigReadonly(
            section=section,
            key=key,
            value=val,
            location=location,
            badges=field_badges(section, key, readonly=True),
        )

    out: List[Tuple[str, List[ConfigScalar], List[ConfigReadonly]]] = []
    for section, body in raw.items():
        if isinstance(body, dict):
            scalars: List[ConfigScalar] = []
            readonly_rows: List[ConfigReadonly] = []
            nested: List[Tuple[str, dict]] = []
            for key, val in body.items():
                if isinstance(val, dict):
                    nested.append((f"{section}.{key}", val))
                    continue
                kind = _kind_of(val)
                # extensions.hcl 소유 섹션은 보여주되 편집은 막는다.
                # rewrite_scalar는 TOML 한 줄을 고치는 방식이라 HCL에 못 쓴다.
                if kind is not None and section not in EXTENSION_SECTIONS:
                    scalars.append(scalar(section, key, kind, val))
                else:
                    readonly_rows.append(readonly(section, key, val))
            out.append((section, scalars, readonly_rows))
            for nsec, nbody in nested:
                nsc: List[ConfigScalar] = []
                nro: List[ConfigReadonly] = []
                for key, val in nbody.items():
                    kind = _kind_of(val)
                    if kind is not None:
                        nsc.append(scalar(nsec, key, kind, val))
                    else:
                        nro.append(readonly(nsec, key, val))
                out.append((nsec, nsc, nro))
        elif isinstance(body, list):
            out.append((section, [], [readonly(section, "(array)", body)]))
        else:
            out.append((section, [], [readonly(section, "(value)", body)]))
    return out
