"""Live MQTT monitoring of the adaptor's VDA5050 boundary.

The WebUi subscribes to the same broker/topic prefix the adaptor publishes to
(``{prefix}/state`` and ``{prefix}/connection``) and renders a snapshot of the
robot's live state. :func:`extract_state` is a pure, schema-tolerant parser
(this repo carries both ``mobileRobotPosition``/``agvPosition`` and
``powerSupply``/``batteryState`` variants) and is unit tested without a broker.

The same connection is reused to publish instant actions (the Control view) so
the WebUi holds a single MQTT session per broker.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core import ipc_paths

try:
    import paho.mqtt.client as mqtt

    _HAVE_PAHO = True
except Exception:  # pragma: no cover - exercised only when paho is absent
    mqtt = None  # type: ignore
    _HAVE_PAHO = False


@dataclass
class StateSnapshot:
    """Latest values extracted from the adaptor's MQTT messages."""

    connection_state: Optional[str] = None
    connection_ts: Optional[float] = None
    operating_mode: Optional[str] = None
    active_emergency_stop: Optional[str] = None
    motor_state: Optional[str] = None  # authoritative JIBOT motor flag: "running"/"stopped"
    working_state: Optional[str] = None  # adapter-computed AMR_STATE workingState token
    working_state_detail: Optional[str] = None  # AMR_STATE workingStateDetail (LOADING/DOCKING…)
    active_action_type: Optional[str] = None  # 현재 RUNNING 인 order/instant 액션
    active_step_action_type: Optional[str] = None  # recipe 내부에서 지금 도는 step
    field_violation: Optional[bool] = None
    driving: Optional[bool] = None
    paused: Optional[bool] = None
    battery_soc: Optional[float] = None
    charging: Optional[bool] = None
    x: Optional[float] = None
    y: Optional[float] = None
    theta: Optional[float] = None
    map_id: Optional[str] = None
    localization_score: Optional[float] = None
    last_node_id: Optional[str] = None
    order_id: Optional[str] = None
    order_update_id: Optional[int] = None
    node_states: List[Dict[str, Any]] = field(default_factory=list)
    edge_states: List[Dict[str, Any]] = field(default_factory=list)
    action_states: List[Dict[str, Any]] = field(default_factory=list)
    instant_action_states: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    header_id: Optional[int] = None
    state_ts: Optional[float] = None  # wall time the last state msg arrived

    def age(self, now: float) -> Optional[float]:
        if self.state_ts is None:
            return None
        return max(0.0, now - self.state_ts)


@dataclass
class EzioState:
    configured: bool = False
    ip: Optional[str] = None
    port: Optional[int] = None
    connected: bool = False
    board: Optional[str] = None
    inputs: Optional[List[int]] = None
    inputs_updated_at: Optional[float] = None
    outputs: Optional[List[int]] = None
    outputs_updated_at: Optional[float] = None
    error: str = ""


@dataclass
class PioState:
    configured: bool = False
    port: Optional[str] = None
    baudrate: Optional[int] = None
    connected: bool = False
    inputs: Optional[Dict[str, str]] = None
    inputs_updated_at: Optional[float] = None
    outputs: Optional[Dict[str, str]] = None
    outputs_updated_at: Optional[float] = None
    error: str = ""


@dataclass
class IoSnapshot:
    updated_at: Optional[float] = None
    ezio: Optional[EzioState] = None
    pio: Optional[PioState] = None


def _first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _num(value: Any) -> Optional[float]:
    """Coerce a JSON value to float, or None if it is not numeric.

    The state schema is tolerant of who publishes it, so a rogue/malformed
    message may carry a string where a number is expected. Returning None keeps
    those out of the numeric format/gauge code paths that the dashboard runs.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def extract_state(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pull monitor-relevant fields out of a VDA5050 state payload.

    Tolerant of the two schema variants in this repo and of missing keys.
    Returns a partial dict of :class:`StateSnapshot` field names.
    """
    out: Dict[str, Any] = {}
    if not isinstance(payload, dict):
        return out

    out["header_id"] = payload.get("headerId")
    if "operatingMode" in payload:
        out["operating_mode"] = payload.get("operatingMode")
    safety = payload.get("safetyState")
    if isinstance(safety, dict):
        if "activeEmergencyStop" in safety:
            out["active_emergency_stop"] = safety.get("activeEmergencyStop")
        elif "eStop" in safety:
            out["active_emergency_stop"] = safety.get("eStop")
        if "fieldViolation" in safety:
            out["field_violation"] = safety.get("fieldViolation")
    if "driving" in payload:
        out["driving"] = payload.get("driving")
    if "paused" in payload:
        out["paused"] = payload.get("paused")
    if "lastNodeId" in payload:
        out["last_node_id"] = payload.get("lastNodeId")
    if "orderId" in payload:
        out["order_id"] = payload.get("orderId")
    if "orderUpdateId" in payload:
        out["order_update_id"] = payload.get("orderUpdateId")

    # Order progress / remaining path. The adaptor publishes the full VDA5050
    # state, so these are present (often empty lists when idle) on every message;
    # an empty list legitimately clears a finished order from the dashboard.
    for src, dst in (
        ("nodeStates", "node_states"),
        ("edgeStates", "edge_states"),
        ("actionStates", "action_states"),
        ("instantActionStates", "instant_action_states"),
    ):
        value = payload.get(src)
        if isinstance(value, list):
            out[dst] = value

    position = _first(payload, "mobileRobotPosition", "agvPosition")
    if isinstance(position, dict):
        out["x"] = _num(position.get("x"))
        out["y"] = _num(position.get("y"))
        out["theta"] = _num(position.get("theta"))
        out["map_id"] = position.get("mapId")
        if position.get("localizationScore") is not None:
            out["localization_score"] = _num(position.get("localizationScore"))

    # localizationScore can also live at the top level of the state message.
    if payload.get("localizationScore") is not None:
        out["localization_score"] = _num(payload.get("localizationScore"))

    power = _first(payload, "powerSupply", "batteryState")
    if isinstance(power, dict):
        soc = _num(power.get("stateOfCharge"))
        # A negative SoC is the adapter's "unknown" sentinel (the JIBOT link is
        # down; VDA5050 forbids a null numeric so it sends -1). Treat it as
        # unknown so the dashboard renders "—" instead of a literal "-1%".
        out["battery_soc"] = None if (soc is not None and soc < 0) else soc
        if power.get("charging") is not None:
            out["charging"] = power.get("charging")

    errors = payload.get("errors")
    if isinstance(errors, list):
        out["errors"] = errors

    # Authoritative JIBOT motor flag, carried as a vendor information entry
    # (jibotMotorState) alongside the other jibot* values. Lets the dashboard
    # show the real fetched motor state instead of inferring it from eStop.
    info_list = payload.get("information")
    if isinstance(info_list, list):
        for info in info_list:
            if not isinstance(info, dict):
                continue
            for ref in info.get("infoReferences") or ():
                if not isinstance(ref, dict):
                    continue
                if ref.get("referenceKey") == "jibotMotorState":
                    out["motor_state"] = ref.get("referenceValue")
                elif ref.get("referenceKey") == "workingState":
                    # Adapter-computed AMR_STATE block (info_type "AMR_STATE"):
                    # ERROR/BLOCKED/PAUSED/CHARGING/DRIVING/ACTING/IDLE.
                    out["working_state"] = ref.get("referenceValue")
                elif ref.get("referenceKey") == "workingStateDetail":
                    out["working_state_detail"] = ref.get("referenceValue")
                elif ref.get("referenceKey") == "activeActionType":
                    # 진행 화면이 "지금 무슨 액션"을 actionStates 스캔 없이 바로 쓰기
                    # 위해 필요. 어댑터가 이미 AMR_STATE 에 싣고 있었는데(1550 근처)
                    # WebUi 쪽 파서만 workingState 하나만 꺼내고 있었다.
                    out["active_action_type"] = ref.get("referenceValue")
                elif ref.get("referenceKey") == "activeStepActionType":
                    # recipe 는 step 단위로 도는데 actionStates 에는 부모 액션만
                    # 남아서, step 진행은 이 값으로만 보인다.
                    out["active_step_action_type"] = ref.get("referenceValue")
    return out


def extract_connection(payload: Dict[str, Any]) -> Optional[str]:
    if isinstance(payload, dict):
        return payload.get("connectionState")
    return None


def extract_io(payload: Dict[str, Any]) -> IoSnapshot:
    """Parse io.json (local-only IO diagnostics) into an IoSnapshot.

    Schema-tolerant like :func:`extract_state`: a missing/garbled block yields
    None for that sub-state, never raises.
    """
    snap = IoSnapshot()
    if not isinstance(payload, dict):
        return snap
    ts = payload.get("updated_at")
    snap.updated_at = float(ts) if isinstance(ts, (int, float)) else None
    ez = payload.get("ezio")
    if isinstance(ez, dict):
        snap.ezio = EzioState(
            configured=bool(ez.get("configured")),
            ip=ez.get("ip"),
            port=ez.get("port"),
            connected=bool(ez.get("connected")),
            board=ez.get("board"),
            inputs=ez.get("inputs") if isinstance(ez.get("inputs"), list) else None,
            inputs_updated_at=_num(ez.get("inputs_updated_at")),
            outputs=ez.get("outputs") if isinstance(ez.get("outputs"), list) else None,
            outputs_updated_at=_num(ez.get("outputs_updated_at")),
            error=str(ez.get("error") or ""),
        )
    pio = payload.get("pio")
    if isinstance(pio, dict):
        snap.pio = PioState(
            configured=bool(pio.get("configured")),
            port=pio.get("port"),
            baudrate=pio.get("baudrate"),
            connected=bool(pio.get("connected")),
            inputs=pio.get("inputs") if isinstance(pio.get("inputs"), dict) else None,
            inputs_updated_at=_num(pio.get("inputs_updated_at")),
            outputs=pio.get("outputs") if isinstance(pio.get("outputs"), dict) else None,
            outputs_updated_at=_num(pio.get("outputs_updated_at")),
            error=str(pio.get("error") or ""),
        )
    return snap


class MqttMonitor:
    """Background MQTT subscriber + publisher for one broker/topic prefix."""

    def __init__(
        self,
        host: str,
        port: int,
        topic_prefix: str,
        client_id: str = "adaptor-core-monitor",
    ) -> None:
        self.host = host
        self.port = port
        self.topic_prefix = topic_prefix
        self.client_id = client_id
        self._lock = threading.Lock()
        self._snapshot = StateSnapshot()
        self._connected = False
        self._started = False
        self._last_error: Optional[str] = None
        self._client = None

    @property
    def available(self) -> bool:
        return _HAVE_PAHO

    @property
    def broker_connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def get_snapshot(self) -> StateSnapshot:
        with self._lock:
            # Shallow copy is enough; fields are immutable scalars except errors.
            snap = StateSnapshot(**self._snapshot.__dict__)
            snap.errors = list(self._snapshot.errors)
            snap.node_states = list(self._snapshot.node_states)
            snap.edge_states = list(self._snapshot.edge_states)
            snap.action_states = list(self._snapshot.action_states)
            snap.instant_action_states = list(self._snapshot.instant_action_states)
            return snap

    def get_io(self) -> IoSnapshot:
        # IO diagnostics are file-only (io.json); the broker path carries none.
        return IoSnapshot()

    def start(self) -> None:
        if self._started or not _HAVE_PAHO:
            if not _HAVE_PAHO:
                self._last_error = "paho-mqtt not installed"
            return
        self._started = True
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        # Keep retrying in the background; never raise into the UI thread.
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        try:
            self._client.connect_async(self.host, self.port, keepalive=30)
            self._client.loop_start()
        except Exception as exc:  # pragma: no cover - network dependent
            self._last_error = f"{type(exc).__name__}: {exc}"

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._started = False

    def publish_json(self, topic_suffix: str, obj: Any, qos: int = 0) -> bool:
        if self._client is None or not self._connected:
            return False
        topic = f"{self.topic_prefix}/{topic_suffix}"
        payload = json.dumps(obj, separators=(",", ":"))
        try:
            info = self._client.publish(topic, payload=payload, qos=qos)
            return info.rc == 0
        except Exception as exc:  # pragma: no cover - network dependent
            self._last_error = f"publish: {exc}"
            return False

    # -- paho callbacks ----------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = getattr(reason_code, "value", reason_code) == 0
        if self._connected:
            self._last_error = None
            client.subscribe(f"{self.topic_prefix}/state", qos=0)
            client.subscribe(f"{self.topic_prefix}/connection", qos=1)
        else:
            self._last_error = f"connect rc={reason_code}"

    def _on_disconnect(self, client, userdata, *args):
        self._connected = False

    def _on_message(self, client, userdata, msg):
        suffix = msg.topic.rsplit("/", 1)[-1]
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return
        now = time.time()
        with self._lock:
            if suffix == "state":
                for key, value in extract_state(payload).items():
                    setattr(self._snapshot, key, value)
                self._snapshot.state_ts = now
            elif suffix == "connection":
                state = extract_connection(payload)
                if state is not None:
                    self._snapshot.connection_state = state
                    self._snapshot.connection_ts = now


class FileMonitor:
    """Reads adapter state/health files instead of subscribing to MQTT.

    Drop-in for the WebUi read path: exposes ``get_snapshot``/``broker_connected``/
    ``last_error`` like :class:`MqttMonitor`, plus ``acs_broker_connected`` from
    health.json. ``broker_connected``/``adapter_online`` here mean "adapter alive"
    (state.json present and fresh), not a broker session.
    """

    def __init__(self, serial: str, stale_after: float = 15.0) -> None:
        self.serial = serial
        self.stale_after = stale_after
        self._fresh = False
        self._last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return True

    @property
    def adapter_online(self) -> bool:
        """True when state.json is present and fresh (adapter is alive)."""
        return self._fresh

    @property
    def broker_connected(self) -> bool:
        # Back-compat alias: server historically read broker_connected. For
        # FileMonitor this means "adapter alive", NOT a broker session.
        return self._fresh

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def acs_broker_connected(self) -> bool:
        data = self._read_json(ipc_paths.health_path(self.serial))
        return bool(data.get("acs_broker_connected")) if isinstance(data, dict) else False

    def _read_json(self, path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    def get_snapshot(self) -> StateSnapshot:
        snap = StateSnapshot()
        data = self._read_json(ipc_paths.state_path(self.serial))
        if isinstance(data, dict):
            for key, value in extract_state(data).items():
                setattr(snap, key, value)
            ts = data.get("updated_at")
            snap.state_ts = float(ts) if isinstance(ts, (int, float)) else None
            self._last_error = None
        else:
            snap.state_ts = None
        now = time.time()
        self._fresh = (
            snap.state_ts is not None and (now - snap.state_ts) <= self.stale_after
        )
        return snap

    def get_io(self) -> IoSnapshot:
        data = self._read_json(ipc_paths.io_path(self.serial))
        return extract_io(data) if isinstance(data, dict) else IoSnapshot()
