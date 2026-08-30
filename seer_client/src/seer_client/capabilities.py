"""SEER capability catalog and Factsheet filtering.

The shared Adapter contains a broad JIBOT action catalog.  A SEER runtime must
only advertise actions that can actually execute with the currently available
SEER/client/host devices.  This module keeps that policy explicit and
machine-readable so tests, documentation, the WebUI and the VDA5050 Factsheet
can use the same source of truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence


SUPPORTED = "supported"
SIMULATOR_ONLY = "simulator_only"
CONDITIONAL = "conditional"
BLOCKED = "blocked"


@dataclass(frozen=True)
class SeerCapability:
    action_type: str
    status: str
    reason: str
    condition: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


# Built-in Adapter handlers that are valid against SeerClient today.  Registry
# actions (manualMove, switchMap, seerPathNav, configured extensions, ...) are
# merged at runtime because their executable handler is the strongest proof of
# support.
_ALWAYS_SUPPORTED = {
    "cancelOrder",
    "stateRequest",
    "factsheetRequest",
    "startPause",
    "stopPause",
    "initPosition",
    "localize",
    "getMap",
    "getParameters",
    "setParameters",
    "switchMap",
    "clearInstantActions",
    "clearZoneActions",
    "clearErrors",
    "manualDrive",
    "manualStop",
    "enableMotor",
    "disableMotor",
    "gotoNearestNode",
}

_SIMULATOR_ONLY = {
    # The shared Adapter writes a JIBOT map file on a real vehicle.  SEER real
    # map upload is not documented in the supplied Robokit API, while the
    # simulator supports in-memory round trips safely.
    "setMapSnapshot",
    "setMap",
}

_SOUND_ACTIONS = {
    "setSoundVolume",
    "testSound",
    "stopSound",
    "uploadSound",
}

_VIDEO_ACTIONS = {"requestVideo"}

_VENDOR_BLOCKED = {
    "startCharging",
    "stopCharging",
    "chargeInPlace",
    "requestLaser",
    "stopLaser",
    "jibotCommand",
    "syncJibotParams",
    "loading",
    "unloading",
    "stopLoading",
    "stopUnloading",
    "logReport",
}

# Known shared Adapter/JIBOT action surface.  Extension registry actions are
# appended dynamically; this list ensures unsupported built-ins are still
# visible in the capability report instead of silently disappearing.
KNOWN_JIBOT_ACTIONS = tuple(
    sorted(
        _ALWAYS_SUPPORTED
        | _SIMULATOR_ONLY
        | _SOUND_ACTIONS
        | _VIDEO_ACTIONS
        | _VENDOR_BLOCKED
        | {
            "chargeInPlace",
            "manualMove",
            "jibotMotionRule",
            "requestVideo",
            "requestLaser",
            "stopLaser",
            "clamp",
            "unclamp",
            "clampTeach",
            "clampOn",
            "clampOff",
            "clampStop",
            "clampMin",
            "clampMax",
            "clampHome",
            "clampMoveTo",
            "pioInit",
            "pioReadIn",
            "pioWriteOut",
            "pioDisconnect",
            "pioScenario",
            "ezioReadIn",
            "photoSensorRead",
            "ezioWriteOut",
            "ezioWaitIn",
            "airShowerEnter",
            "airShowerInside",
            "airShowerPassed",
            "elevatorEnter",
            "elevatorInside",
            "elevatorPassed",
        }
    )
)


def _enabled(config: Any, section_name: str) -> bool:
    section = getattr(config, section_name, None)
    return bool(section is not None and getattr(section, "enabled", False))


def advertised_action_types(adapter: Any) -> tuple[str, ...]:
    """Return the action types this concrete SEER Adapter may advertise.

    Runtime ActionRegistry entries are included because they have an executable
    handler.  Known vendor-blocked actions are removed even if the shared
    Adapter exposes a built-in branch for them; that branch assumes JIBOT APIs.
    """

    result = set(_ALWAYS_SUPPORTED)
    is_simulator = False
    checker = getattr(adapter, "_is_simulator", None)
    if callable(checker):
        try:
            is_simulator = bool(checker())
        except Exception:
            is_simulator = False
    if is_simulator:
        result.update(_SIMULATOR_ONLY)

    config = getattr(adapter, "config", None)
    if _enabled(config, "sound_settings"):
        result.update(_SOUND_ACTIONS)
    if _enabled(config, "video"):
        result.update(_VIDEO_ACTIONS)

    registry = getattr(adapter, "_action_registry", None)
    action_types = getattr(registry, "action_types", None)
    if callable(action_types):
        result.update(str(item) for item in action_types())

    result.difference_update(_VENDOR_BLOCKED)
    vehicle = getattr(adapter, "_vehicle", None)
    # Jack is advertised only after the controller model has positively
    # confirmed compatibility.  Unknown/stale capability state is treated as
    # unavailable so the FMS cannot issue a hardware action before detection.
    if getattr(vehicle, "_jack_supported", None) is not True:
        result.difference_update({"seerJackLoad", "seerJackUnload"})
    return tuple(sorted(result))



def filter_factsheet_actions(
    factsheet: Mapping[str, Any],
    adapter: Any,
    *,
    action_scopes: Optional[Mapping[str, Sequence[str]]] = None,
) -> Mapping[str, Any]:
    """Return ``factsheet`` with agvActions replaced by SEER capabilities."""

    scopes = action_scopes or {}
    features = factsheet.setdefault("protocolFeatures", {})
    features["agvActions"] = [
        {
            "actionType": action_type,
            "actionScopes": list(scopes.get(action_type, ("INSTANT",))),
        }
        for action_type in advertised_action_types(adapter)
    ]
    return factsheet

def capability_report(
    *,
    registry_actions: Iterable[str] = (),
    simulator: bool = False,
    sound_enabled: bool = False,
    video_enabled: bool = False,
) -> tuple[SeerCapability, ...]:
    """Build a deterministic report covering known and registry actions."""

    registry = {str(item) for item in registry_actions}
    all_actions = set(KNOWN_JIBOT_ACTIONS) | registry
    report: list[SeerCapability] = []
    for action in sorted(all_actions):
        if action in _VENDOR_BLOCKED:
            reason = {
                "startCharging": "SEER docking/charging API is not confirmed",
                "stopCharging": "SEER charging-stop API is not confirmed",
                "chargeInPlace": "external charge-circuit contract is not configured",
                "requestLaser": "SEER laser streaming API is not confirmed",
                "stopLaser": "SEER laser streaming API is not confirmed",
                "jibotCommand": "raw JIBOT commands must never reach a SEER controller",
                "syncJibotParams": "JIBOT parameter synchronization is vendor-specific",
            }.get(action, "JIBOT-specific device/workflow is not configured for SEER")
            report.append(SeerCapability(action, BLOCKED, reason))
        elif action in _SIMULATOR_ONLY:
            report.append(
                SeerCapability(
                    action,
                    SUPPORTED if simulator else SIMULATOR_ONLY,
                    "safe in-memory map operation in the SEER simulator",
                    "simulator=true",
                )
            )
        elif action in _SOUND_ACTIONS:
            report.append(
                SeerCapability(
                    action,
                    SUPPORTED if sound_enabled else CONDITIONAL,
                    "adapter-host sound function",
                    "sound_settings.enabled=true",
                )
            )
        elif action in _VIDEO_ACTIONS:
            report.append(
                SeerCapability(
                    action,
                    SUPPORTED if video_enabled else CONDITIONAL,
                    "adapter-host video function",
                    "video.enabled=true",
                )
            )
        elif action in _ALWAYS_SUPPORTED or action in registry:
            report.append(
                SeerCapability(
                    action,
                    SUPPORTED,
                    "SEER-compatible built-in or executable ActionRegistry handler",
                )
            )
        else:
            report.append(
                SeerCapability(action, BLOCKED, "no SEER-compatible handler is registered")
            )
    return tuple(report)


def capability_report_json(**kwargs: Any) -> list[Mapping[str, str]]:
    return [item.to_dict() for item in capability_report(**kwargs)]

# ---------------------------------------------------------------------------
# Robot-model-derived hardware capability helpers
# ---------------------------------------------------------------------------
import re


def extract_robot_model(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for key in ("robot.model", "robot_model", "robotModel", "model"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    robot = payload.get("robot")
    if isinstance(robot, Mapping):
        for key in ("model", "model_name", "modelName", "type"):
            value = robot.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)):
                return str(value)
    return ""


def _explicit_jack_flag(payload: Mapping[str, Any] | None) -> Optional[bool]:
    if not isinstance(payload, Mapping):
        return None
    candidates = [payload]
    robot = payload.get("robot")
    if isinstance(robot, Mapping):
        candidates.append(robot)
        model = robot.get("model")
        if isinstance(model, Mapping):
            candidates.append(model)
    for obj in candidates:
        for key in (
            "supports_jack", "support_jack", "jack_supported", "jack",
            "has_jack", "has_lift", "supports_lift", "lifting",
        ):
            if key not in obj:
                continue
            value = obj.get(key)
            if isinstance(value, bool):
                return value
            text = str(value or "").strip().lower()
            if text in {"1", "true", "yes", "on", "supported", "enable", "enabled"}:
                return True
            if text in {"0", "false", "no", "off", "unsupported", "disable", "disabled", "none"}:
                return False
    return None


def jack_support_from_model(
    model: str,
    payload: Mapping[str, Any] | None = None,
) -> Optional[bool]:
    explicit = _explicit_jack_flag(payload)
    if explicit is not None:
        return explicit
    text = str(model or "").strip().upper()
    if not text:
        return None
    compact = re.sub(r"[^A-Z0-9]+", "", text)
    if any(token in compact for token in ("JACK", "LIFT", "LIFTER", "SJV", "SLR")):
        return True
    if re.search(r"AMB\d+(?:JZ|JS)", compact):
        return True
    if compact.startswith("SBA"):
        return False
    return None


def detect_jack_support(
    payload: Mapping[str, Any] | None,
) -> tuple[str, Optional[bool]]:
    model = extract_robot_model(payload)
    return model, jack_support_from_model(model, payload)


def _iter_model_text(value: Any, *, depth: int = 0):
    """Yield normalized keys/string values from an API-1500 robot.model document."""
    if depth > 24:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _iter_model_text(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_model_text(item, depth=depth + 1)
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            # Some controller generations may wrap a subtree as a JSON string.
            if text[:1] in {"{", "["}:
                try:
                    import json
                    decoded = json.loads(text)
                except Exception:
                    decoded = None
                if isinstance(decoded, (Mapping, list, tuple)):
                    yield from _iter_model_text(decoded, depth=depth + 1)
            yield text


def robot_model_label(payload: Mapping[str, Any] | None) -> str:
    """Return a compact human-readable label from an API-1500 model document."""
    if not isinstance(payload, Mapping):
        return ""
    # Do not recursively grab an arbitrary device's model name.  Only top-level
    # identifiers are suitable for display beside ``robot.model`` in the UI.
    for key in (
        "robot_model", "robotModel", "model_name", "modelName",
        "robot_type", "robotType", "name", "model",
    ):
        value = payload.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            text = str(value).strip()
            if len(text) <= 120:
                return text
    return ""


def _boolish(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disable", "disabled", "none"}:
        return False
    return None


def _jack_device_control_type(device: Mapping[str, Any]) -> str:
    """Return the selected robot.model Jack control backend when available."""
    params = device.get("deviceParams")
    if not isinstance(params, (list, tuple)):
        return ""
    for param in params:
        if not isinstance(param, Mapping) or str(param.get("key", "")).lower() != "type":
            continue
        combo = param.get("comboParam")
        if isinstance(combo, Mapping):
            return str(combo.get("childKey", "") or "").strip()
    return ""


def jack_model_capability(payload: Mapping[str, Any] | None) -> tuple[Optional[bool], str]:
    """Read the *enabled* Jack device from an API-1500 robot.model document.

    RoboShop may show a Jack device with a prohibition icon even though the
    ``jack`` schema is present in robot.model.  The model explicitly represents
    that case as ``isEnabled: false``.  Presence of the word ``jack`` therefore
    must never be treated as support by itself.
    """
    if not isinstance(payload, Mapping) or not payload:
        return None, "robot.model 미수신"

    device_types = payload.get("deviceTypes")
    if isinstance(device_types, (list, tuple)):
        jack_types = [
            item for item in device_types
            if isinstance(item, Mapping)
            and str(item.get("name", "") or "").strip().lower() == "jack"
        ]
        if jack_types:
            saw_device = False
            saw_explicit_disabled = False
            for dtype in jack_types:
                devices = dtype.get("devices")
                if not isinstance(devices, (list, tuple)):
                    continue
                for device in devices:
                    if not isinstance(device, Mapping):
                        continue
                    saw_device = True
                    enabled = _boolish(device.get("isEnabled"))
                    if enabled is False:
                        saw_explicit_disabled = True
                        continue
                    if enabled is not True:
                        continue
                    control_type = _jack_device_control_type(device)
                    if control_type.lower() == "none":
                        saw_explicit_disabled = True
                        continue
                    suffix = f" · type={control_type}" if control_type else ""
                    return True, f"robot.model Jack isEnabled=true{suffix}"
            if saw_explicit_disabled:
                return False, "robot.model Jack isEnabled=false/제어방식 none"
            if saw_device:
                return None, "robot.model Jack 장치 활성 상태 불명"
            return False, "robot.model Jack device 없음"

    # Compatibility for older controller/model layouts.  Respect an explicit
    # isEnabled flag first; only accept a legacy Jack module when it is clearly
    # marked enabled.  We intentionally do not fall back to a plain text search.
    def walk(value: Any, depth: int = 0):
        if depth > 24:
            return
        if isinstance(value, Mapping):
            name = " ".join(
                str(value.get(key, "") or "")
                for key in ("name", "type", "className", "deviceType")
            ).lower()
            if "jack" in name:
                explicit_enabled = None
                for key in ("isEnabled", "enabled", "enable", "jack_enable"):
                    if key in value:
                        explicit_enabled = _boolish(value.get(key))
                        if explicit_enabled is not None:
                            break
                if explicit_enabled is not None:
                    yield explicit_enabled
            for item in value.values():
                yield from walk(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from walk(item, depth + 1)

    legacy = list(walk(payload))
    if any(flag is True for flag in legacy):
        return True, "robot.model legacy Jack enabled=true"
    if any(flag is False for flag in legacy):
        return False, "robot.model legacy Jack enabled=false"
    return False, "robot.model에 활성 Jack 장치 없음"


def jack_support_from_robot_model(payload: Mapping[str, Any] | None) -> Optional[bool]:
    return jack_model_capability(payload)[0]

def detect_jack_support_from_robot_model(
    payload: Mapping[str, Any] | None,
) -> tuple[str, Optional[bool]]:
    return robot_model_label(payload), jack_support_from_robot_model(payload)
