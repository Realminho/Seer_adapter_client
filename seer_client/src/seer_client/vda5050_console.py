"""Small FMS-side VDA5050 MQTT console used by ``manual_test.py``.

The normal SEER client is the Adapter's southbound Robokit TCP/IP driver.  This
module deliberately sits on the other side of the unchanged Adapter: it sends
``order`` and ``instantActions`` messages over MQTT and keeps the latest message
from the robot's VDA5050 topic tree.  Received messages are shown only when the
operator requests them.  It is therefore useful for proving the complete FMS ->
MQTT -> Adapter -> SEER path without a production FMS.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import shlex
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from .interactive_prompt import CompletionCatalog, InteractiveCommandPrompt
from .vda5050_order import build_route_order, build_vda_action


VDA_TOPIC_SUPPORT = (
    ("order", "FMS -> Adapter", "SUPPORTED: subscribed by Adapter"),
    ("instantActions", "FMS -> Adapter", "SUPPORTED: subscribed by Adapter"),
    ("state", "Adapter -> FMS", "SUPPORTED: periodic/event publish"),
    ("connection", "Adapter -> FMS", "SUPPORTED: retained + Last Will"),
    ("factsheet", "Adapter -> FMS", "SUPPORTED: retained publish"),
    ("visualization", "Adapter -> FMS", "NOT IMPLEMENTED by original Adapter"),
    ("zoneSet", "FMS -> Adapter", "NOT IMPLEMENTED by original Adapter"),
    ("responses", "Adapter -> FMS", "NOT IMPLEMENTED by original Adapter"),
)


VDA_HELP_TEXT = """VDA5050 MQTT commands:
  help                                  show this help
  topics                                show exact topics, direction, and support
  last [topic]                          show the last received JSON message
  watch [seconds]                       show live RX temporarily (default: 10 seconds)
  status                                request and show the next state once
  factsheet                             request and show the next factsheet once
  pause | resume | cancel | stop        publish the matching instant action
  jack_load | jack_unload               publish SEER Jack VDA5050 instant action
  emergency                             toggle SEER software emergency switch
  emc                                   set SEER software emergency switch
  emc_release                           release SEER software emergency switch
  motor <on|off>                        publish enableMotor/disableMotor
  do <id> <on|off>                      publish seerSetDO
  goto <target> [source]                publish a VDA5050 order (nodes + edges)
  goto_route <point1> <point2> ...      publish one released VDA5050 route order
  goto_xyz <x> <y> <theta_deg>          publish VDA5050 instantActions/seerCoordinateNav
  free <x> <y> <theta_deg>              alias of goto_xyz
  translate <distance_m> <speed_mps>    publish seerTranslate
  turn <angle_deg> <speed_deg_s>        publish seerTurn
  action <type> [key=value ...]         publish VDA5050 instantActions
  order <order_id> <node1> [node2 ...] publish a released VDA route order
  order_action <order_id> <node> <type> [key=value ...]
                                        publish a node action inside /order
  edge_action <order_id> <from> <to> <type> [key=value ...]
                                        publish an edge action inside /order
  file <order|instantActions> <path>    retarget and publish a JSON file
  raw <order|instantActions> <json>     retarget and publish inline JSON
  quit | exit                           disconnect and finish

Every publish is printed as [VDA5050 MQTT TX].  Broker messages are stored
silently by default; use last/watch, or status/factsheet for a one-shot display.
Motion/write commands require --allow-write.
"""


VDA_COMPLETION_CATALOG = CompletionCatalog(
    commands={
        "help": "show command help",
        "topics": "show VDA5050 topic support",
        "last": "show one cached MQTT message",
        "watch": "temporarily show live MQTT RX",
        "status": "request one state",
        "factsheet": "request one factsheet",
        "pause": "pause current task",
        "resume": "resume paused task",
        "cancel": "cancel current order",
        "stop": "stop current motion",
        "emergency": "toggle software emergency",
        "emc": "set software emergency",
        "emc_release": "release software emergency",
        "motor": "enable or disable motors",
        "do": "set one digital output",
        "goto": "navigate to a named point",
        "goto_route": "navigate through one designated point route",
        "goto_xyz": "navigate to coordinates",
        "jack_load": "raise SEER jack through VDA5050",
        "jack_unload": "lower SEER jack through VDA5050",
        "free": "free navigation to coordinates",
        "translate": "relative straight movement",
        "turn": "relative rotation",
        "action": "publish a VDA5050 instant action",
        "order": "publish a VDA5050 order",
        "order_action": "publish a node action inside a VDA5050 order",
        "edge_action": "publish an edge action inside a VDA5050 order",
        "file": "publish a JSON sample file",
        "raw": "publish inline JSON",
        "quit": "disconnect and finish",
        "exit": "disconnect and finish",
    },
    argument_choices={
        "last": {0: tuple(name for name, _, _ in VDA_TOPIC_SUPPORT)},
        "watch": {0: ("1", "5", "10", "30")},
        "motor": {0: ("on", "off")},
        "do": {1: ("on", "off")},
        "goto": {1: ("SELF_POSITION",)},
        "file": {0: ("order", "instantActions")},
        "raw": {0: ("order", "instantActions")},
    },
    path_arguments={("file", 1)},
)


def _utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_scalar(value: str) -> Any:
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return value


@dataclass(frozen=True)
class Vda5050Identity:
    serial_number: str
    manufacturer: str = "seer"
    interface: str = "amr"
    topic_version: str = "v3"
    message_version: str = "3.0.0"

    @property
    def topic_prefix(self) -> str:
        # This intentionally matches adaptor/utils/mqtt_client.py.  The supplied
        # Adapter omits manufacturer from its VDA topic prefix.
        return f"{self.interface}/{self.topic_version}/{self.serial_number}"

    def topic(self, suffix: str) -> str:
        return f"{self.topic_prefix}/{suffix}"


class Vda5050MessageFactory:
    """Create current-project VDA5050 v3 payloads with unique identifiers."""

    def __init__(
        self,
        identity: Vda5050Identity,
        *,
        action_counter_path: Optional[Path] = None,
        order_counter_path: Optional[Path] = None,
    ) -> None:
        self.identity = identity
        self._header_id = int(time.time() * 1000) % 2_000_000_000
        self._lock = threading.Lock()
        self._action_counter_path = Path(action_counter_path) if action_counter_path else None
        self._order_counter_path = Path(order_counter_path) if order_counter_path else None
        self._action_counts = self._load_counter_file(self._action_counter_path)
        self._order_counts = self._load_counter_file(self._order_counter_path)

    @staticmethod
    def _load_counter_file(counter_path: Optional[Path]) -> Dict[str, int]:
        counts_out: Dict[str, int] = {}
        if counter_path is None:
            return counts_out
        try:
            raw = json.loads(counter_path.read_text(encoding="utf-8"))
            counts = raw.get("counts", {}) if isinstance(raw, dict) else {}
            if isinstance(counts, dict):
                for key, value in counts.items():
                    try:
                        counts_out[str(key)] = max(0, int(value))
                    except (TypeError, ValueError):
                        continue
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return counts_out

    @staticmethod
    def _persist_counter_file(counter_path: Optional[Path], counts: Mapping[str, int]) -> None:
        if counter_path is None:
            return
        try:
            counter_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = counter_path.with_name(counter_path.name + ".tmp")
            temp_path.write_text(
                json.dumps({"schema": 1, "counts": dict(counts)}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            temp_path.replace(counter_path)
        except OSError:
            pass

    def next_action_id(self, action_type: str) -> str:
        """Return a human-readable ``<actionType>-<execution count>`` ID.

        WebUI traces are operator-facing, so UUID-only IDs make Pause/Resume and
        other controls unnecessarily hard to distinguish.  When a counter path
        is supplied, counts survive WebUI/Adapter restarts for this AMR.
        """

        name = str(action_type or "action").strip() or "action"
        with self._lock:
            count = max(0, int(self._action_counts.get(name, 0) or 0)) + 1
            self._action_counts[name] = count
            self._persist_counter_file(self._action_counter_path, self._action_counts)
        return f"{name}-{count:03d}"

    def next_order_id(self, order_type: str) -> str:
        """Return a readable ``<OrderType>-<execution count>`` order ID.

        Counts are tracked independently per order type.  When an order counter
        path is supplied by the WebUI trace store, the sequence survives WebUI
        restarts for the selected AMR.
        """

        name = str(order_type or "Order").strip() or "Order"
        with self._lock:
            count = max(0, int(self._order_counts.get(name, 0) or 0)) + 1
            self._order_counts[name] = count
            self._persist_counter_file(self._order_counter_path, self._order_counts)
        return f"{name}-{count:03d}"

    def _next_header(self) -> Dict[str, Any]:
        with self._lock:
            self._header_id += 1
            header_id = self._header_id
        return {
            "headerId": header_id,
            "timestamp": _utc_timestamp(),
            "version": self.identity.message_version,
            "manufacturer": self.identity.manufacturer,
            "serialNumber": self.identity.serial_number,
        }

    def instant_action(
        self,
        action_type: str,
        parameters: Optional[Mapping[str, Any]] = None,
        *,
        blocking_type: str = "NONE",
        action_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = self._next_header()
        payload["actions"] = [
            {
                "actionId": str(action_id or self.next_action_id(action_type)),
                "actionType": str(action_type),
                "blockingType": str(blocking_type).upper(),
                "actionParameters": [
                    {"key": str(key), "value": value}
                    for key, value in (parameters or {}).items()
                ],
            }
        ]
        return payload

    def order(
        self,
        order_id: str,
        node_ids: Sequence[str],
        *,
        node_positions: Optional[Mapping[str, Mapping[str, Any]]] = None,
        edge_ids: Optional[Mapping[tuple[str, str], str]] = None,
        order_update_id: int = 0,
        node_actions: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
        edge_actions: Optional[Mapping[tuple[str, str], Sequence[Mapping[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """Build a standards-shaped released VDA5050 order.

        ``actions`` are preserved as VDA action objects inside nodes/edges, so a
        test order can match an FMS payload without translating actions into a
        SEER-only command format.
        """
        if len(node_ids) < 1:
            raise ValueError("order requires at least one node")
        payload = self._next_header()
        positions = node_positions or {}
        node_action_map = node_actions or {}
        nodes = []
        for index, node_id in enumerate(node_ids):
            node_name = str(node_id)
            node = {
                "nodeId": node_name,
                "sequenceId": index * 2,
                "released": True,
                "actions": json.loads(json.dumps(list(node_action_map.get(node_name, ())))),
            }
            position = positions.get(node_name)
            if isinstance(position, Mapping):
                node["nodePosition"] = json.loads(json.dumps(dict(position)))
            nodes.append(node)
        edges = []
        edge_names = edge_ids or {}
        edge_action_map = edge_actions or {}
        for index in range(len(node_ids) - 1):
            source = str(node_ids[index])
            target = str(node_ids[index + 1])
            edge_name = str(
                edge_names.get((source, target))
                or f"manual-edge-{index + 1}-{source}-{target}"
            )
            edges.append(
                {
                    "edgeId": edge_name,
                    "sequenceId": index * 2 + 1,
                    "released": True,
                    "startNodeId": source,
                    "endNodeId": target,
                    "actions": json.loads(json.dumps(list(edge_action_map.get((source, target), ())))),
                }
            )
        payload.update(
            {
                "orderId": str(order_id),
                "orderUpdateId": int(order_update_id),
                "nodes": nodes,
                "edges": edges,
            }
        )
        return payload

    def retarget(self, suffix: str, source: Mapping[str, Any]) -> Dict[str, Any]:
        """Copy a sample and replace stale Jibot identity/header fields.

        Older Jibot samples use ``instantActions`` and ``actionParameter``.
        Normalize those aliases to the current v3 names accepted and emitted by
        this project's Adapter.
        """

        payload = json.loads(json.dumps(dict(source)))
        payload.update(self._next_header())
        if suffix == "instantActions":
            if "actions" not in payload and "instantActions" in payload:
                payload["actions"] = payload.pop("instantActions")
            actions = payload.get("actions")
            if not isinstance(actions, list):
                raise ValueError("instantActions JSON requires an actions array")
            for action in actions:
                if not isinstance(action, dict):
                    raise ValueError("each actions item must be an object")
                if "actionParameters" not in action and "actionParameter" in action:
                    action["actionParameters"] = action.pop("actionParameter")
                action["actionId"] = str(
                    action.get("actionId")
                    or self.next_action_id(str(action.get("actionType", "action") or "action"))
                )
        elif suffix == "order":
            if not isinstance(payload.get("nodes"), list):
                raise ValueError("order JSON requires a nodes array")
            if not isinstance(payload.get("edges"), list):
                raise ValueError("order JSON requires an edges array")
        else:
            raise ValueError("file/raw topic must be order or instantActions")
        return payload


class Vda5050MqttConsole:
    """Interactive MQTT publisher/monitor representing a minimal test FMS."""

    READ_ONLY_ACTIONS = frozenset({"stateRequest", "factsheetRequest"})
    ONE_SHOT_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        identity: Vda5050Identity,
        *,
        host: str,
        port: int,
        username: Optional[str] = None,
        password: Optional[str] = None,
        qos: int = 0,
        allow_write: bool = False,
        output: Callable[[str], None] = print,
        mqtt_client: Any = None,
        map_cache_path: Optional[Path] = None,
        position_unit: str = "mm",
        orientation_unit: str = "deg",
        map_id: str = "",
    ) -> None:
        self.identity = identity
        self.host = str(host)
        self.port = int(port)
        self.username = username
        self.password = password
        self.qos = int(qos)
        self.allow_write = bool(allow_write)
        self.output = output
        self.factory = Vda5050MessageFactory(identity)
        self.map_cache_path = Path(map_cache_path) if map_cache_path else None
        self.position_unit = str(position_unit or "mm")
        self.orientation_unit = str(orientation_unit or "deg")
        self.map_id = str(map_id or "")
        self._client = mqtt_client
        self._connected = threading.Event()
        self._connect_rc: Any = None
        self._last_by_suffix: Dict[str, Any] = {}
        self._rx_lock = threading.Lock()
        self._watching = False
        self._show_next_until: Dict[str, float] = {}
        self._closed = False

    def connect(self, timeout: float = 5.0) -> None:
        if self._client is None:
            try:
                from paho.mqtt import client as mqtt
            except ImportError as exc:
                raise RuntimeError(
                    "paho-mqtt is required for --vda5050; install Adapter dependencies"
                ) from exc
            self._client = mqtt.Client(
                client_id=f"seer-vda-manual-{self.identity.serial_number}-{uuid.uuid4().hex[:8]}"
            )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        if self.username:
            self._client.username_pw_set(self.username, self.password)
        self.output(
            f"[VDA5050 MQTT CONNECTING] {self.host}:{self.port} "
            f"prefix={self.identity.topic_prefix}"
        )
        self._client.connect(self.host, self.port, keepalive=60)
        self._client.loop_start()
        if not self._connected.wait(max(0.1, float(timeout))):
            self.close()
            raise TimeoutError(
                f"MQTT connect timeout: {self.host}:{self.port}"
            )
        if self._rc_value(self._connect_rc) != 0:
            rc = self._connect_rc
            self.close()
            raise ConnectionError(f"MQTT connection rejected: rc={rc}")

    @staticmethod
    def _rc_value(rc: Any) -> int:
        try:
            return int(rc)
        except (TypeError, ValueError):
            return int(getattr(rc, "value", -1))

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: Any, *extra: Any) -> None:
        del userdata, flags, extra
        self._connect_rc = rc
        if self._rc_value(rc) == 0:
            wildcard = f"{self.identity.topic_prefix}/#"
            client.subscribe(wildcard, qos=self.qos)
            self.output(f"[VDA5050 MQTT CONNECTED] subscribed={wildcard}")
        else:
            self.output(f"[VDA5050 MQTT CONNECT FAILED] rc={rc}")
        self._connected.set()

    def _on_disconnect(self, client: Any, userdata: Any, rc: Any, *extra: Any) -> None:
        del client, userdata, extra
        if not self._closed:
            self.output(f"[VDA5050 MQTT DISCONNECTED] rc={rc}")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        del client, userdata
        raw = bytes(message.payload).decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        suffix = str(message.topic).rsplit("/", 1)[-1]
        with self._rx_lock:
            self._last_by_suffix[suffix] = payload
            deadline = self._show_next_until.pop(suffix, None)
            show_one_shot = deadline is not None and deadline >= time.monotonic()
            show_received = self._watching or show_one_shot
        if show_received:
            self._show_message("RX", str(message.topic), payload)

    def _arm_one_shot(self, suffix: str) -> None:
        with self._rx_lock:
            self._show_next_until[suffix] = (
                time.monotonic() + self.ONE_SHOT_TIMEOUT_SECONDS
            )

    def _cancel_one_shot(self, suffix: str) -> None:
        with self._rx_lock:
            self._show_next_until.pop(suffix, None)

    def _show_message(self, direction: str, topic: str, payload: Any) -> None:
        pretty = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        self.output(f"[VDA5050 MQTT {direction}] topic={topic}\n{pretty}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            try:
                self._client.loop_stop()
            finally:
                self._client.disconnect()

    def publish(self, suffix: str, payload: Mapping[str, Any]) -> None:
        if suffix not in {"order", "instantActions"}:
            raise ValueError("manual publisher supports only order or instantActions")
        topic = self.identity.topic(suffix)
        body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
        self._show_message("TX", topic, payload)
        result = self._client.publish(topic, body, qos=self.qos, retain=False)
        rc = getattr(result, "rc", 0)
        if self._rc_value(rc) != 0:
            raise RuntimeError(f"MQTT publish failed: rc={rc}")

    def publish_action(
        self, action_type: str, parameters: Optional[Mapping[str, Any]] = None
    ) -> None:
        if not self.allow_write and action_type not in self.READ_ONLY_ACTIONS:
            raise PermissionError(
                f"{action_type} blocked; restart --vda5050 with --allow-write"
            )
        self.publish(
            "instantActions", self.factory.instant_action(action_type, parameters)
        )

    def _show_topics(self) -> None:
        rows = [f"VDA5050 prefix: {self.identity.topic_prefix}"]
        rows.extend(
            f"  {self.identity.topic(name):<55} {direction:<16} {support}"
            for name, direction, support in VDA_TOPIC_SUPPORT
        )
        self.output("\n".join(rows))

    def _show_last(self, suffix: Optional[str]) -> None:
        with self._rx_lock:
            latest = dict(self._last_by_suffix)
        if suffix:
            if suffix not in latest:
                self.output(f"[VDA5050 LAST] no message for {suffix}")
                return
            self._show_message(
                "LAST", self.identity.topic(suffix), latest[suffix]
            )
            return
        if not latest:
            self.output("[VDA5050 LAST] no messages received")
            return
        for name in sorted(latest):
            self._show_message(
                "LAST", self.identity.topic(name), latest[name]
            )

    @staticmethod
    def _require(args: Sequence[str], count: int, usage: str) -> None:
        if len(args) != count:
            raise ValueError(f"usage: {usage}")

    def _next_manual_order_id(self, order_type: str = "PathNav") -> str:
        return self.factory.next_order_id(order_type)

    def _current_last_node_id(self) -> str:
        with self._rx_lock:
            state = self._last_by_suffix.get("state")
        if not isinstance(state, Mapping):
            return ""
        return str(state.get("lastNodeId") or state.get("last_node_id") or "").strip()

    def publish_route_order(
        self,
        route_points: Sequence[str],
        *,
        order_id: Optional[str] = None,
        explicit_positions: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not self.allow_write:
            raise PermissionError("order blocked; restart --vda5050 with --allow-write")
        points = tuple(str(point).strip() for point in route_points if str(point).strip())
        if not points:
            raise ValueError("VDA5050 route order requires at least one node")
        payload = build_route_order(
            self.factory,
            order_id=order_id or self._next_manual_order_id("PathNav"),
            route_points=points,
            map_cache_path=self.map_cache_path,
            position_unit=self.position_unit,
            orientation_unit=self.orientation_unit,
            map_id=self.map_id,
            explicit_positions=explicit_positions,
        )
        self.publish("order", payload)
        return payload

    async def execute(self, line: str) -> bool:
        tokens = shlex.split(line)
        if not tokens:
            return True
        command = tokens[0].lower().replace("-", "_")
        args = tokens[1:]
        if command in {"quit", "exit"}:
            return False
        if command == "help":
            self.output(VDA_HELP_TEXT.rstrip())
            return True
        if command == "topics":
            self._require(args, 0, "topics")
            self._show_topics()
            return True
        if command == "last":
            if len(args) > 1:
                raise ValueError("usage: last [topic]")
            self._show_last(args[0] if args else None)
            return True
        if command == "watch":
            if len(args) > 1:
                raise ValueError("usage: watch [seconds]")
            seconds = float(args[0]) if args else 10.0
            if seconds < 0:
                raise ValueError("watch seconds must be >= 0")
            self.output(f"[VDA5050 WATCH] showing live RX for {seconds:g}s ...")
            with self._rx_lock:
                self._watching = True
            try:
                await asyncio.sleep(seconds)
            finally:
                with self._rx_lock:
                    self._watching = False
            self.output("[VDA5050 WATCH] quiet mode restored")
            return True

        simple_actions = {
            "status": "stateRequest",
            "factsheet": "factsheetRequest",
            "pause": "startPause",
            "resume": "stopPause",
            "cancel": "cancelOrder",
            "stop": "manualStop",
            "emergency": "seerEmergencySwitch",
            "jack_load": "seerJackLoad",
            "jack_unload": "seerJackUnload",
        }
        if command in simple_actions:
            self._require(args, 0, command)
            response_suffix = {
                "status": "state",
                "factsheet": "factsheet",
            }.get(command)
            if response_suffix:
                self._arm_one_shot(response_suffix)
            try:
                self.publish_action(simple_actions[command])
            except Exception:
                if response_suffix:
                    self._cancel_one_shot(response_suffix)
                raise
            return True
        if command in {"emc", "emc_release"}:
            self._require(args, 0, command)
            self.publish_action(
                "seerEmergencySwitch",
                {"status": "on" if command == "emc" else "off"},
            )
            return True
        if command == "motor":
            self._require(args, 1, "motor <on|off>")
            enabled = args[0].lower()
            if enabled not in {"on", "off"}:
                raise ValueError("motor expects on or off")
            self.publish_action("enableMotor" if enabled == "on" else "disableMotor")
            return True
        if command == "do":
            self._require(args, 2, "do <id> <on|off>")
            status = args[1].lower()
            if status not in {"on", "off"}:
                raise ValueError("do expects on or off")
            self.publish_action("seerSetDO", {"id": int(args[0]), "status": status})
            return True
        if command == "goto":
            if len(args) not in {1, 2}:
                raise ValueError("usage: goto <target> [source]")
            target = str(args[0]).strip()
            source = str(args[1]).strip() if len(args) == 2 else self._current_last_node_id()
            route = [source, target] if source and source != target else [target]
            self.publish_route_order(route)
            return True
        if command == "goto_route":
            if len(args) < 2:
                raise ValueError("usage: goto_route <point1> <point2> ...")
            self.publish_route_order(args)
            return True
        if command in {"goto_xyz", "free"}:
            self._require(args, 3, f"{command} <x> <y> <theta_deg>")
            x, y, theta = map(float, args)
            # The unchanged real-SEER order handler resolves named map nodes and
            # does not drive an invented node id from nodePosition. Keep free
            # coordinate motion in the VDA5050 instantActions envelope so real
            # hardware and simulator follow the same FMS-side JSON path.
            self.publish_action(
                "seerCoordinateNav", {"x": x, "y": y, "theta_deg": theta}
            )
            return True
        if command == "translate":
            self._require(args, 2, "translate <distance_m> <speed_mps>")
            distance, speed = map(float, args)
            self.publish_action(
                "seerTranslate",
                {"distance_m": distance, "linear_speed_mps": speed},
            )
            return True
        if command == "turn":
            self._require(args, 2, "turn <angle_deg> <speed_deg_s>")
            angle, speed = map(float, args)
            self.publish_action(
                "seerTurn",
                {"angle_deg": angle, "angular_speed_deg_s": speed},
            )
            return True
        if command == "action":
            if not args:
                raise ValueError("usage: action <type> [key=value ...]")
            parameters: Dict[str, Any] = {}
            for token in args[1:]:
                if "=" not in token:
                    raise ValueError(f"action parameter must be key=value: {token!r}")
                key, value = token.split("=", 1)
                if not key:
                    raise ValueError("action parameter key cannot be empty")
                parameters[key] = _parse_scalar(value)
            self.publish_action(args[0], parameters)
            return True
        if command == "order":
            if len(args) < 2:
                raise ValueError("usage: order <order_id> <node1> [node2 ...]")
            self.publish_route_order(args[1:], order_id=args[0])
            return True
        if command == "order_action":
            if len(args) < 3:
                raise ValueError(
                    "usage: order_action <order_id> <node> <action_type> [key=value ...]"
                )
            if not self.allow_write:
                raise PermissionError(
                    "order action blocked; restart --vda5050 with --allow-write"
                )
            parameters = {}
            for item in args[3:]:
                if "=" not in item:
                    raise ValueError(f"action parameter must be key=value: {item}")
                key, value = item.split("=", 1)
                parameters[key] = _parse_scalar(value)
            node_id = str(args[1])
            action = build_vda_action(str(args[2]), parameters)
            payload = build_route_order(
                self.factory,
                order_id=str(args[0]),
                route_points=[node_id],
                map_cache_path=self.map_cache_path,
                position_unit=self.position_unit,
                orientation_unit=self.orientation_unit,
                map_id=self.map_id,
                node_actions={node_id: [action]},
            )
            self.publish("order", payload)
            return True
        if command == "edge_action":
            if len(args) < 4:
                raise ValueError(
                    "usage: edge_action <order_id> <from> <to> <action_type> [key=value ...]"
                )
            if not self.allow_write:
                raise PermissionError(
                    "edge action blocked; restart --vda5050 with --allow-write"
                )
            parameters = {}
            for item in args[4:]:
                if "=" not in item:
                    raise ValueError(f"action parameter must be key=value: {item}")
                key, value = item.split("=", 1)
                parameters[key] = _parse_scalar(value)
            source, target = str(args[1]), str(args[2])
            action = build_vda_action(str(args[3]), parameters)
            payload = build_route_order(
                self.factory,
                order_id=str(args[0]),
                route_points=[source, target],
                map_cache_path=self.map_cache_path,
                position_unit=self.position_unit,
                orientation_unit=self.orientation_unit,
                map_id=self.map_id,
                edge_actions={(source, target): [action]},
            )
            self.publish("order", payload)
            return True
        if command in {"file", "raw"}:
            if len(args) != 2:
                raise ValueError(f"usage: {command} <order|instantActions> <path|json>")
            suffix = args[0]
            if suffix not in {"order", "instantActions"}:
                raise ValueError("topic must be order or instantActions")
            if not self.allow_write:
                raise PermissionError(
                    f"{suffix} publish blocked; restart --vda5050 with --allow-write"
                )
            if command == "file":
                source = json.loads(Path(args[1]).read_text(encoding="utf-8-sig"))
            else:
                source = json.loads(args[1])
            if not isinstance(source, Mapping):
                raise ValueError("JSON root must be an object")
            self.publish(suffix, self.factory.retarget(suffix, source))
            return True
        raise ValueError(f"unknown VDA5050 command: {command!r}; enter 'help'")

    async def repl(self) -> None:
        prompt = InteractiveCommandPrompt(VDA_COMPLETION_CATALOG)
        self.output(
            "SEER VDA5050 MQTT console connected. RX is quiet by default."
        )
        self.output(
            "Use 'last state', 'status', or 'watch 5' when you want to see RX. "
            "Press Tab twice for command candidates; enter 'help' for details."
        )
        if not prompt.completion_enabled:
            self.output(
                "[TAB COMPLETION DISABLED] This input is not attached to a Windows "
                f"console: {prompt.unavailable_reason}"
            )
        while True:
            try:
                line = await prompt.read("vda5050> ")
                if not await self.execute(line):
                    return
            except EOFError:
                return
            except (ValueError, PermissionError, RuntimeError, OSError) as exc:
                self.output(f"[ERROR] {exc}")
