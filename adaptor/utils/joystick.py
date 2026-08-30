"""Linux joystick event decoding and logical action mapping utilities."""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any, Mapping, Sequence


JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02
JS_EVENT_INIT = 0x80
JS_EVENT_FORMAT = "IhBB"
JS_EVENT_SIZE = struct.calcsize(JS_EVENT_FORMAT)


@dataclass(frozen=True)
class JoystickEvent:
    timestamp_ms: int
    value: int
    event_type: str
    number: int
    initial: bool = False


@dataclass
class EvdevDpadDrive:
    """Track a two-axis evdev D-pad and resolve safe digital drive actions."""

    x_code: int = 0
    y_code: int = 1
    neutral_threshold: int = 1000
    x: int = 0
    y: int = 0
    blocked_until_neutral: bool = False

    def update(self, code: int, value: int) -> str | None:
        if code not in {self.x_code, self.y_code}:
            return None
        direction = 0 if abs(value) <= self.neutral_threshold else (1 if value > 0 else -1)
        if code == self.x_code:
            self.x = direction
        else:
            self.y = direction
        if self.x and self.y:
            self.blocked_until_neutral = True
            return "manual_stop"
        if self.blocked_until_neutral:
            if not self.x and not self.y:
                self.blocked_until_neutral = False
            return "manual_stop"
        if self.y < 0:
            return "forward"
        if self.y > 0:
            return "reverse"
        if self.x < 0:
            return "turn_left"
        if self.x > 0:
            return "turn_right"
        return "manual_stop"


def decode_event(data: bytes) -> JoystickEvent:
    """Decode one Linux ``js_event`` record from ``/dev/input/jsN``."""
    if len(data) != JS_EVENT_SIZE:
        raise ValueError(f"expected {JS_EVENT_SIZE} bytes, got {len(data)}")
    timestamp_ms, value, raw_type, number = struct.unpack(JS_EVENT_FORMAT, data)
    initial = bool(raw_type & JS_EVENT_INIT)
    base_type = raw_type & ~JS_EVENT_INIT
    event_type = {
        JS_EVENT_BUTTON: "button",
        JS_EVENT_AXIS: "axis",
    }.get(base_type, f"unknown:{base_type}")
    return JoystickEvent(timestamp_ms, value, event_type, number, initial)


def normalize_axis(value: int, deadzone: float = 0.15) -> float:
    """Normalize signed 16-bit axis input and apply a centred deadzone."""
    normalized = max(-1.0, min(1.0, value / 32767.0))
    if abs(normalized) <= deadzone:
        return 0.0
    scaled = (abs(normalized) - deadzone) / (1.0 - deadzone)
    return round((-1.0 if normalized < 0 else 1.0) * scaled, 4)


def mapped_action(
    event: JoystickEvent,
    mapping: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Map an input event using ``axes`` and ``buttons`` number-to-action maps."""
    section_name = "axes" if event.event_type == "axis" else "buttons"
    section = mapping.get(section_name, {})
    entry = section.get(str(event.number)) if isinstance(section, Mapping) else None
    if entry is None:
        return None
    entry = {"action": entry} if isinstance(entry, str) else dict(entry)
    action = entry.get("action")
    if not action:
        return None
    if event.event_type == "axis":
        deadzone = float(entry.get("deadzone", mapping.get("deadzone", 0.15)))
        if entry.get("trigger", False):
            # xpad exposes analogue triggers as -32767 (released) .. 32767
            # (fully pressed), unlike centred stick axes.
            value = round(max(0.0, min(1.0, (event.value + 32767) / 65534.0)), 4)
            if value <= deadzone:
                value = 0.0
        else:
            value = normalize_axis(event.value, deadzone)
        if entry.get("invert", False):
            value = -value
    else:
        value = 1 if event.value else 0
    return {"action": str(action), "value": value, "input": section_name, "number": event.number}


def choose_preferred_device(devices: Sequence[Mapping[str, str]]) -> Mapping[str, str] | None:
    """Prefer an 8BitDo USB joystick, then another USB joystick, deterministically."""
    if not devices:
        return None

    def rank(info: Mapping[str, str]) -> tuple[int, int, str]:
        identity = f"{info.get('name', '')} {info.get('vendor', '')}".lower()
        is_8bitdo = "8bitdo" in identity or info.get("vendor", "").lower() == "2dc8"
        is_usb = info.get("connection", "").lower() == "usb"
        return (0 if is_8bitdo and is_usb else 1 if is_usb else 2, 0 if is_8bitdo else 1, info.get("device", ""))

    return min(devices, key=rank)


def mapped_chord_action(
    dpad_direction: str,
    face_button: str,
    mapping: Mapping[str, Any],
) -> str | None:
    """Resolve a D-pad direction plus A/B/X/Y chord to one logical action."""
    direction = dpad_direction.strip().lower()
    button = face_button.strip().lower()
    if direction not in {"up", "right", "down", "left"}:
        return None
    if button not in {"a", "b", "x", "y"}:
        return None
    chords = mapping.get("chords", {})
    if not isinstance(chords, Mapping):
        return None
    action = chords.get(f"dpad_{direction}+{button}")
    return str(action) if action else None


#: Fixed slot order shared by the config file and the runtime:
#: 1..4=Up+A/B/X/Y, 5..8=Right+..., 9..12=Down+..., 13..16=Left+...
CHORD_DIRECTIONS = ("up", "right", "down", "left")
CHORD_FACES = ("a", "b", "x", "y")


def chord_direction(x: int, y: int) -> str | None:
    """Resolve a D-pad direction from two raw axis values, or None.

    Works for both backends because neutral is exactly 0 in each: joydev reports
    the D-pad as +/-32767 hat axes and xboxdrv's uinput pad as +/-1 ABS_HAT0*.
    A diagonal is deliberately no direction — it would otherwise pick whichever
    axis happened to be tested first and fire the wrong slot.
    """
    horizontal = 0 if x == 0 else (1 if x > 0 else -1)
    vertical = 0 if y == 0 else (1 if y > 0 else -1)
    if horizontal and vertical:
        return None
    if vertical < 0:
        return "up"
    if vertical > 0:
        return "down"
    if horizontal > 0:
        return "right"
    if horizontal < 0:
        return "left"
    return None


def chord_slot(direction: str | None, face: str | None) -> int | None:
    """Map a held D-pad direction plus a face button to its 1..16 slot."""
    if direction not in CHORD_DIRECTIONS or face not in CHORD_FACES:
        return None
    return CHORD_DIRECTIONS.index(direction) * 4 + CHORD_FACES.index(face) + 1


def step_speed_percent(
    current: int, direction: int, step: int, minimum: int, maximum: int
) -> int:
    """Clamp one -/+ press into the configured manual-drive speed range.

    Returning ``current`` unchanged at a bound is the signal the runtime uses
    to skip re-sending a drive command that would be identical to the last one.
    """
    if direction == 0 or step <= 0:
        return current
    target = current + (step if direction > 0 else -step)
    return max(minimum, min(maximum, target))


def validate_joystick_actions(config: Any, registry: Any) -> None:
    """Fail startup when an enabled joystick slot names an unknown action."""
    if config is None or not bool(getattr(config, "enabled", False)):
        return
    for entry in getattr(config, "actions", ()):
        if not bool(getattr(entry, "enabled", True)):
            continue
        action = str(getattr(entry, "action", "")).strip()
        if not action or not registry.has(action):
            slot = getattr(entry, "slot", "?")
            raise ValueError(
                f"joystick_action slot {slot}: unknown or disabled action '{action}'"
            )
