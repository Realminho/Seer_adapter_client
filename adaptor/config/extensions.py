"""extensions.hcl을 config.toml과 같은 섹션 dict로 읽는다.

extension 전용 설정(PIO/EZI/에어샤워/엘리베이터 튜닝값, 액션 모듈·플러그인
등록)을 config.toml에서 분리한 파일이다. 반환 모양을 config.toml의 섹션
구조와 똑같이 맞춰서, get_config()가 기존 dict에 그대로 병합할 수 있게 한다.
그래야 dataclass와 소비자 코드를 건드리지 않는다.

블록 이름 -> config 섹션:
    extension "pio"        -> [pio], 그 안의 advanced 블록 -> [pio_advanced]
    extension "ezi"        -> [ezi]
    extension "airshower"  -> [air_shower_pio]
    extension "elevator"   -> [elevator_pio], motion_rule 블록 -> elevator_motion_rules
    module "<import path>"  -> [[action_modules]]
    action "<actionType>"   -> [[actions]]
    state_action "<state>"  -> [[state_actions]]
    joystick "<profile>"    -> [joystick]
    joystick_action "1..16" -> [joystick.actions]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from config.errors import ConfigError
from config.hcl import HclError, blocks, load_hcl

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTENSIONS_PATH = ADAPTER_ROOT / "config" / "extensions.hcl"

#: Renamed when -/+ stopped changing the volume. Deployed robots still carry the
#: old names and extensions.hcl rejects unknown fields, so a bare rename would
#: stop them booting.
_LEGACY_JOYSTICK_KEYS = {
    "volume_down_button": "speed_down_button",
    "volume_up_button": "speed_up_button",
    "xboxdrv_volume_down_button_code": "xboxdrv_speed_down_button_code",
    "xboxdrv_volume_up_button_code": "xboxdrv_speed_up_button_code",
}

#: Accepted and dropped: the joystick no longer touches the volume at all.
_DROPPED_JOYSTICK_KEYS = ("volume_step",)

#: Defaults for the joystick speed-scale keys. Not prefixed with `_`: config.py
#: already imports ExtensionsError/load_extensions from this module (no import
#: cycle that direction), so JoystickConfig's dataclass field defaults import
#: these too instead of repeating the literals.
DEFAULT_SPEED_STEP_PERCENT = 20
DEFAULT_SPEED_MIN_PERCENT = 20
DEFAULT_SPEED_MAX_PERCENT = 100
DEFAULT_SPEED_START_PERCENT = 100

#: extension 블록 라벨 -> config.toml 섹션 이름.
_EXTENSION_SECTIONS = {
    "pio": "pio",
    "ezi": "ezi",
    "airshower": "air_shower_pio",
    "elevator": "elevator_pio",
}

_WORKING_STATES = {
    "IDLE",
    "DRIVING",
    "ACTING",
    "CHARGING",
    "PAUSED",
    "BLOCKED",
    "ERROR",
}


class ExtensionsError(ConfigError):
    """extensions.hcl이 없거나 형식이 잘못됐을 때."""


def load_extensions(
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """extensions.hcl을 읽어 config 섹션 구조의 dict를 돌려준다."""
    file_path = Path(path) if path is not None else DEFAULT_EXTENSIONS_PATH
    try:
        data = load_hcl(file_path)
    except HclError as exc:
        raise ExtensionsError(
            f"{exc} (config/extensions.hcl.example 을 복사해 만드세요)"
        ) from exc

    result: Dict[str, Any] = {}

    seen_extensions = set()
    for label, body in blocks(data, "extension"):
        if label in seen_extensions:
            raise ExtensionsError(f"duplicate extension '{label}' in {file_path}")
        seen_extensions.add(label)
        result.update(extension_sections(label, body, str(file_path)))

    missing = set(_EXTENSION_SECTIONS) - seen_extensions
    if missing:
        raise ExtensionsError(
            f"missing required extension block(s) in {file_path}: "
            f"{', '.join(sorted(missing))}"
        )

    module_entries = blocks(data, "module")
    _reject_duplicate_labels(module_entries, "module", file_path)
    result["action_modules"] = []
    for label, body in module_entries:
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ExtensionsError(
                f"module '{label}' in {file_path}: enabled must be true or false"
            )
        result["action_modules"].append({"module": label, "enabled": enabled})

    action_entries = blocks(data, "action")
    _reject_duplicate_labels(action_entries, "action", file_path)
    result["actions"] = []
    for label, body in action_entries:
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ExtensionsError(
                f"action '{label}' in {file_path}: enabled must be true or false"
            )
        result["actions"].append(dict(body, action_type=label, enabled=enabled))

    state_action_entries = blocks(data, "state_action")
    _reject_duplicate_labels(state_action_entries, "state_action", file_path)
    result["state_actions"] = []
    for label, body in state_action_entries:
        state = label.strip()
        if state not in _WORKING_STATES:
            raise ExtensionsError(
                f"state_action '{label}' in {file_path}: unknown workingState "
                f"(known: {', '.join(sorted(_WORKING_STATES))})"
            )
        unknown = set(body) - {"start", "end"}
        if unknown:
            raise ExtensionsError(
                f"state_action '{label}' in {file_path}: unknown field(s): "
                f"{', '.join(sorted(unknown))}"
            )
        entry = {"state": state}
        for phase in ("start", "end"):
            raw = body.get(phase)
            if raw is None:
                continue
            if not isinstance(raw, dict):
                raise ExtensionsError(
                    f"state_action '{label}' in {file_path}: {phase} must be an object"
                )
            phase_unknown = set(raw) - {"action", "parameters"}
            if phase_unknown:
                raise ExtensionsError(
                    f"state_action '{label}' {phase}: unknown field(s): "
                    f"{', '.join(sorted(phase_unknown))}"
                )
            action = raw.get("action")
            if not isinstance(action, str) or not action.strip():
                raise ExtensionsError(
                    f"state_action '{label}' in {file_path}: "
                    f"{phase}.action must be a non-empty string"
                )
            parameters = raw.get("parameters", {})
            if not isinstance(parameters, dict):
                raise ExtensionsError(
                    f"state_action '{label}' in {file_path}: "
                    f"{phase}.parameters must be an object"
                )
            entry[phase] = {
                "action": action.strip(),
                "parameters": dict(parameters),
            }
        if "start" not in entry and "end" not in entry:
            raise ExtensionsError(
                f"state_action '{label}' in {file_path}: start or end is required"
            )
        result["state_actions"].append(entry)

    joystick_entries = blocks(data, "joystick")
    if len(joystick_entries) > 1:
        raise ExtensionsError(f"only one joystick block is allowed in {file_path}")
    joystick = dict(joystick_entries[0][1]) if joystick_entries else {}
    if joystick_entries:
        joystick["profile"] = joystick_entries[0][0]

    for legacy, current in _LEGACY_JOYSTICK_KEYS.items():
        if legacy not in joystick:
            continue
        value = joystick.pop(legacy)
        if current in joystick:
            print(f"[EXTENSIONS] {file_path}: '{legacy}' ignored; '{current}' wins")
            continue
        joystick[current] = value
        print(f"[EXTENSIONS] {file_path}: '{legacy}' is deprecated; rename it to '{current}'")
    for dropped in _DROPPED_JOYSTICK_KEYS:
        if dropped in joystick:
            joystick.pop(dropped)
            print(
                f"[EXTENSIONS] {file_path}: '{dropped}' no longer does anything; "
                "-/+ step the drive speed"
            )

    allowed_joystick = {
        "enabled", "vendor_id", "product_id", "device_name_contains",
        "forward_axis", "reverse_axis", "steering_axis", "stop_button",
        "speed_down_button", "speed_up_button", "trigger_threshold",
        "speed_step_percent", "speed_min_percent", "speed_max_percent",
        "speed_start_percent",
        "heartbeat_timeout_ms", "dpad_x_axis", "dpad_y_axis", "a_button",
        "b_button", "x_button", "y_button", "micro_enabled",
        "micro_device_name", "micro_dpad_x_code", "micro_dpad_y_code",
        "micro_neutral_threshold", "micro_stop_button", "micro_reconnect_sec",
        "ultimate2_enabled", "steering_deadzone", "joystick_reconnect_sec",
        "xboxdrv_device_name", "xboxdrv_forward_axis_code",
        "xboxdrv_reverse_axis_code", "xboxdrv_steering_axis_code",
        "xboxdrv_stop_button_code", "xboxdrv_dpad_x_code", "xboxdrv_dpad_y_code",
        "xboxdrv_a_button_code", "xboxdrv_b_button_code", "xboxdrv_x_button_code",
        "xboxdrv_y_button_code", "xboxdrv_speed_down_button_code",
        "xboxdrv_speed_up_button_code", "xboxdrv_receiver_guard",
        "actions_enabled",
    }
    unknown = set(joystick) - allowed_joystick - {"profile"}
    if unknown:
        raise ExtensionsError(
            f"joystick in {file_path}: unknown field(s): {', '.join(sorted(unknown))}"
        )
    if "enabled" in joystick and not isinstance(joystick["enabled"], bool):
        raise ExtensionsError(f"joystick in {file_path}: enabled must be true or false")
    if joystick.get("micro_enabled") and joystick.get("ultimate2_enabled"):
        raise ExtensionsError(
            f"joystick in {file_path}: enable only one driving controller at a time"
        )

    speed_step = _int_joystick_field(
        joystick, "speed_step_percent", DEFAULT_SPEED_STEP_PERCENT, file_path
    )
    speed_min = _int_joystick_field(
        joystick, "speed_min_percent", DEFAULT_SPEED_MIN_PERCENT, file_path
    )
    speed_max = _int_joystick_field(
        joystick, "speed_max_percent", DEFAULT_SPEED_MAX_PERCENT, file_path
    )
    speed_start = _int_joystick_field(
        joystick, "speed_start_percent", DEFAULT_SPEED_START_PERCENT, file_path
    )
    if speed_step < 1:
        raise ExtensionsError(
            f"joystick in {file_path}: speed_step_percent must be at least 1"
        )
    if speed_max > 100:
        raise ExtensionsError(
            f"joystick in {file_path}: speed_max_percent must not exceed 100; "
            "100% is the configured manual_control magnitude"
        )
    if speed_min < 1 or speed_min > speed_max:
        raise ExtensionsError(
            f"joystick in {file_path}: speed_min_percent must be between 1 and "
            "speed_max_percent"
        )
    if not speed_min <= speed_start <= speed_max:
        raise ExtensionsError(
            f"joystick in {file_path}: speed_start_percent must be between "
            "speed_min_percent and speed_max_percent"
        )

    action_entries = blocks(data, "joystick_action")
    _reject_duplicate_labels(action_entries, "joystick_action", file_path)
    actions = []
    for label, body in action_entries:
        try:
            slot = int(label)
        except ValueError as exc:
            raise ExtensionsError(
                f"joystick_action '{label}' in {file_path}: slot must be 1..16"
            ) from exc
        if not 1 <= slot <= 16 or str(slot) != label:
            raise ExtensionsError(
                f"joystick_action '{label}' in {file_path}: slot must be 1..16"
            )
        unknown = set(body) - {"action", "target", "parameters", "enabled"}
        if unknown:
            raise ExtensionsError(
                f"joystick_action '{label}' in {file_path}: unknown field(s): "
                f"{', '.join(sorted(unknown))}"
            )
        action = body.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ExtensionsError(
                f"joystick_action '{label}' in {file_path}: action is required"
            )
        target = body.get("target", "action")
        if target not in {"action", "extension", "recipe"}:
            raise ExtensionsError(
                f"joystick_action '{label}' in {file_path}: target must be "
                "action, extension, or recipe"
            )
        parameters = body.get("parameters", {})
        enabled = body.get("enabled", True)
        if not isinstance(parameters, dict) or not isinstance(enabled, bool):
            raise ExtensionsError(
                f"joystick_action '{label}' in {file_path}: parameters must be "
                "an object and enabled must be true or false"
            )
        actions.append({
            "slot": slot,
            "action": action.strip(),
            "target": target,
            "parameters": dict(parameters),
            "enabled": enabled,
        })
    joystick["actions"] = actions
    result["joystick"] = joystick

    return result


def _int_joystick_field(
    joystick: Dict[str, Any], field: str, default: int, file_path: Path
) -> int:
    """Cast a joystick speed field to int, naming the file/field on failure.

    A bare int(...) raises "invalid literal for int() with base 10: 'abc'" with
    no file name and no field name -- the only error in this function that
    doesn't tell the operator what to fix.
    """
    raw = joystick.get(field, default)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ExtensionsError(
            f"joystick in {file_path}: {field} must be an integer (got {raw!r})"
        ) from exc


def _reject_duplicate_labels(entries, kind: str, file_path: Path) -> None:
    seen = set()
    for label, _body in entries:
        if label in seen:
            raise ExtensionsError(f"duplicate {kind} '{label}' in {file_path}")
        seen.add(label)


def extension_sections(
    label: str, body: Mapping[str, Any], where: str
) -> Dict[str, Dict[str, Any]]:
    """extension 블록 하나를 config 섹션 dict들로 바꾼다 (라벨 검증 포함).

    ``{"pio": {...}, "pio_advanced": {...}}``처럼 승격된 섹션까지 함께 돌려준다.
    로봇별 override를 읽는 config/fleet.py도 이 함수를 부른다 — 라벨 -> 섹션
    짝이 두 곳으로 갈라지면 robots.hcl에 적은 값이 조용히 다른 섹션으로 들어가
    아무 일도 일어나지 않는다.

    선언된 키만 담는다. 없는 키를 기본값으로 채우면 robots.hcl이 적지 않은
    값까지 extensions.hcl 위에 덮어써 버린다(예: motion_rules를 빈 목록으로).
    """
    section = _EXTENSION_SECTIONS.get(label)
    if section is None:
        known = ", ".join(sorted(_EXTENSION_SECTIONS))
        raise ExtensionsError(
            f"unknown extension '{label}' in {where} (known: {known})"
        )

    sections: Dict[str, Dict[str, Any]] = {}
    values = {
        key: value
        for key, value in body.items()
        if key not in ("advanced", "motion_rules")
    }
    if label == "pio":
        advanced = body.get("advanced")
        if advanced is not None:
            if not isinstance(advanced, dict):
                raise ExtensionsError(
                    f"extension 'pio' in {where}: advanced must be an object"
                )
            sections["pio_advanced"] = dict(advanced)
    if label == "elevator":
        rules = body.get("motion_rules")
        if rules is not None:
            if not isinstance(rules, list) or not all(
                isinstance(rule, dict) for rule in rules
            ):
                raise ExtensionsError(
                    f"extension 'elevator' in {where}: "
                    "motion_rules must be a list of objects"
                )
            values["elevator_motion_rules"] = [dict(rule) for rule in rules]
    sections[section] = values
    return sections
