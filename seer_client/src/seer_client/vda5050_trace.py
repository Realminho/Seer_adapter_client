"""Thread-safe VDA5050 MQTT trace store and SEER WebUI renderer."""

from __future__ import annotations

import collections
import datetime as dt
import html
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Deque, Dict, Mapping, Optional, Sequence
from urllib.parse import quote

from .vda5050_console import VDA_TOPIC_SUPPORT, Vda5050Identity, Vda5050MessageFactory


TRACE_TOPICS = frozenset({"order", "instantActions", "state", "connection"})


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Vda5050TraceStore:
    """Observe one robot's four core VDA topics and retain a bounded history."""

    def __init__(
        self,
        identity: Vda5050Identity,
        *,
        host: str,
        port: int,
        history_path: Optional[Path] = None,
        max_records: int = 100,
        mqtt_client: Any = None,
    ) -> None:
        self.identity = identity
        self.host = str(host)
        self.port = int(port)
        self.history_path = Path(history_path) if history_path else None
        action_counter_path = (
            self.history_path.with_name("action-id-counts.json")
            if self.history_path is not None
            else None
        )
        order_counter_path = (
            self.history_path.with_name("order-id-counts.json")
            if self.history_path is not None
            else None
        )
        self.factory = Vda5050MessageFactory(
            identity,
            action_counter_path=action_counter_path,
            order_counter_path=order_counter_path,
        )
        self._records: Deque[Dict[str, Any]] = collections.deque(maxlen=max_records)
        self._counts = {name: 0 for name in TRACE_TOPICS}
        self._last = {name: None for name in TRACE_TOPICS}
        self._lock = threading.RLock()
        self._client = mqtt_client
        self._started = False
        self._connected = False
        self._error = "not started"

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            if self._client is None:
                from paho.mqtt import client as mqtt

                self._client = mqtt.Client(
                    client_id=(
                        f"seer-web-vda-trace-{self.identity.serial_number}-"
                        f"{uuid.uuid4().hex[:8]}"
                    )
                )
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message
            with self._lock:
                self._error = "connecting"
            if hasattr(self._client, "reconnect_delay_set"):
                self._client.reconnect_delay_set(min_delay=1, max_delay=10)
            if hasattr(self._client, "connect_async"):
                self._client.connect_async(self.host, self.port, keepalive=60)
            else:
                self._client.connect(self.host, self.port, keepalive=60)
            self._client.loop_start()
        except Exception as exc:  # WebUI must remain usable without a broker.
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _rc_value(rc: Any) -> int:
        try:
            return int(rc)
        except (TypeError, ValueError):
            return int(getattr(rc, "value", -1))

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: Any, *extra: Any) -> None:
        del userdata, flags, extra
        value = self._rc_value(rc)
        with self._lock:
            self._connected = value == 0
            self._error = "" if value == 0 else f"MQTT rejected rc={rc}"
        if value == 0:
            client.subscribe(f"{self.identity.topic_prefix}/#", qos=0)

    def _on_disconnect(self, client: Any, userdata: Any, rc: Any, *extra: Any) -> None:
        del client, userdata, extra
        with self._lock:
            self._connected = False
            self._error = f"disconnected rc={rc}"

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        del client, userdata
        suffix = str(message.topic).rsplit("/", 1)[-1]
        if suffix not in TRACE_TOPICS:
            return
        raw = bytes(message.payload).decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        self.record(
            suffix,
            payload,
            direction="MQTT RX",
            transport="broker",
            topic=str(message.topic),
        )

    def record(
        self,
        suffix: str,
        payload: Any,
        *,
        direction: str,
        transport: str,
        topic: Optional[str] = None,
    ) -> None:
        if suffix not in TRACE_TOPICS:
            return
        entry = {
            "timestamp": _now(),
            "suffix": suffix,
            "topic": topic or self.identity.topic(suffix),
            "direction": str(direction),
            "transport": str(transport),
            "payload": payload,
        }
        with self._lock:
            self._records.appendleft(entry)
            self._counts[suffix] += 1
            self._last[suffix] = entry
            path = self.history_path
            if path is not None:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                except OSError as exc:
                    self._error = f"trace log write failed: {exc}"

    def publish_exact(
        self,
        suffix: str,
        payload: Mapping[str, Any],
        *,
        direction: str = "WEBUI MQTT TX",
    ) -> Dict[str, Any]:
        """Publish one already-complete VDA5050 payload without rewriting it.

        Normal WebUI controls use this path so the Adapter receives exactly the
        same MQTT topic and JSON object that a production FMS would send.
        """

        if suffix not in {"order", "instantActions"}:
            raise ValueError("WebUI can publish only order or instantActions")
        cloned = json.loads(json.dumps(dict(payload)))
        serial = str(cloned.get("serialNumber", "") or "").strip()
        if serial and serial != self.identity.serial_number:
            raise ValueError(
                f"VDA5050 serialNumber mismatch: {serial!r} != "
                f"{self.identity.serial_number!r}"
            )
        with self._lock:
            if not self._connected:
                raise ConnectionError(f"FMS MQTT offline: {self._error}")
            client = self._client
        topic = self.identity.topic(suffix)
        result = client.publish(
            topic,
            json.dumps(cloned, ensure_ascii=False, separators=(",", ":")),
            qos=0,
            retain=False,
        )
        rc = getattr(result, "rc", 0)
        if self._rc_value(rc) != 0:
            raise RuntimeError(f"MQTT publish failed: rc={rc}")
        self.record(
            suffix,
            cloned,
            direction=direction,
            transport="MQTT broker → Adapter (FMS-identical path)",
            topic=topic,
        )
        return cloned

    def publish_test(self, suffix: str, source: Mapping[str, Any]) -> Dict[str, Any]:
        if suffix not in {"order", "instantActions"}:
            raise ValueError("WebUI can test-publish only order or instantActions")
        with self._lock:
            if not self._connected:
                raise ConnectionError(f"FMS MQTT offline: {self._error}")
        payload = self.factory.retarget(suffix, source)
        return self.publish_exact(suffix, payload, direction="WEBUI MQTT TEST TX")

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "serial": self.identity.serial_number,
                "prefix": self.identity.topic_prefix,
                "host": self.host,
                "port": self.port,
                "connected": self._connected,
                "error": self._error,
                "counts": dict(self._counts),
                "last": dict(self._last),
                "records": list(self._records),
            }

    def stop(self) -> None:
        client = self._client
        self._started = False
        if client is not None:
            try:
                client.loop_stop()
            finally:
                try:
                    client.disconnect()
                except OSError:
                    pass
        with self._lock:
            self._connected = False
            self._error = "stopped"






class Vda5050WebSender:
    """Selectable WebUI command transport with FMS MQTT as the default.

    ``local`` sends the complete VDA5050 JSON to the Adapter over localhost TCP.
    ``mqtt`` publishes the same JSON on the production FMS MQTT topic.  Only the
    transport changes; the order/instantActions payload does not.
    """

    VALID_MODES = frozenset({"local", "mqtt"})

    def __init__(self, trace: Vda5050TraceStore, local_sender: Any, *, mode: str = "mqtt") -> None:
        self.trace = trace
        self.local_sender = local_sender
        self._lock = threading.RLock()
        self._mode = "mqtt"
        self.set_mode(mode)

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> str:
        token = str(mode or "").strip().lower()
        if token not in self.VALID_MODES:
            raise ValueError("WebUI VDA5050 transport must be 'local' or 'mqtt'")
        with self._lock:
            self._mode = token
        return token

    @staticmethod
    def _looks_auto_generated_action_id(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        if text.startswith("manual-") or text.startswith("seer-"):
            tail = text.rsplit("-", 1)[-1]
            if tail and all(char in "0123456789abcdefABCDEF" for char in tail):
                return True
        try:
            uuid.UUID(text)
            return True
        except (ValueError, AttributeError, TypeError):
            return False

    def _validate_identity(self, payload: Mapping[str, Any], *, suffix: str = "") -> Dict[str, Any]:
        cloned = json.loads(json.dumps(dict(payload)))
        serial = str(cloned.get("serialNumber", "") or "").strip()
        if serial and serial != self.trace.identity.serial_number:
            raise ValueError(
                f"VDA5050 serialNumber mismatch: {serial!r} != "
                f"{self.trace.identity.serial_number!r}"
            )
        # The unchanged shared WebUI creates UUID actionIds.  Keep that shared
        # code untouched and normalize only SEER WebUI-generated instant actions
        # at the transport boundary. Explicit readable IDs (Recipe-001 etc.) are
        # preserved byte-for-byte.
        if suffix == "instantActions":
            actions = cloned.get("actions")
            if isinstance(actions, list):
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if not self._looks_auto_generated_action_id(action.get("actionId")):
                        continue
                    action_type = str(action.get("actionType", "action") or "action")
                    action["actionId"] = self.trace.factory.next_action_id(action_type)
        return cloned

    def send_exact(
        self,
        suffix: str,
        payload: Mapping[str, Any],
        meta: Optional[Mapping[str, Any]] = None,
    ):
        if suffix not in {"order", "instantActions"}:
            return False, f"unsupported WebUI VDA5050 topic: {suffix}"
        try:
            cloned = self._validate_identity(payload, suffix=suffix)
            mode = self.mode
            if mode == "mqtt":
                self.trace.publish_exact(suffix, cloned)
                return True, "delivered via FMS MQTT"
            delivered, message = self.local_sender.send_vda(
                suffix, cloned, dict(meta or {})
            )
            if delivered:
                self.trace.record(
                    suffix,
                    cloned,
                    direction="WEBUI LOCAL VDA TX",
                    transport="localhost TCP → Adapter (same VDA5050 parser)",
                )
            return delivered, message
        except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
            return False, str(exc)

    def send(self, payload: dict, meta: dict):
        return self.send_exact("instantActions", payload, meta)

    def send_order(self, payload: Mapping[str, Any], meta: Optional[Mapping[str, Any]] = None):
        return self.send_exact("order", payload, meta)


class Vda5050MqttSender:
    """Common WebUI sender that publishes VDA5050 instantActions over MQTT.

    The shared WebUI calls ``send(payload, meta)``.  ``meta`` remains local
    audit context only and is deliberately not wrapped around the wire payload,
    keeping the MQTT JSON byte-for-byte in VDA5050 message shape.
    """

    def __init__(self, trace: Vda5050TraceStore) -> None:
        self.trace = trace

    def send(self, payload: dict, meta: dict):
        del meta
        try:
            self.trace.publish_exact("instantActions", payload)
            return True, "delivered via VDA5050 MQTT"
        except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
            return False, str(exc)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _sample_payload(trace: Vda5050TraceStore) -> str:
    payload = trace.factory.instant_action("stateRequest")
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_vda5050_page(
    render: Any,
    *,
    traces: Mapping[str, Vda5050TraceStore],
    senders: Optional[Mapping[str, Any]] = None,
    specs: Sequence[Any],
    csrf: str,
    query: Mapping[str, str],
) -> str:
    selected_key = str(query.get("robot", "") or "")
    selected = next(
        (spec for spec in specs if spec.key == selected_key),
        specs[0] if specs else None,
    )
    if selected is None or selected.key not in traces:
        return render.page(
            "VDA5050",
            '<p class="error-notice">SEER VDA5050 추적기가 없습니다.</p>',
            current="/vda5050",
        )
    trace = traces[selected.key]
    snap = trace.snapshot()
    sender = (senders or {}).get(selected.key)
    command_mode = str(getattr(sender, "mode", "mqtt") or "mqtt").lower()
    command_mode_label = "Local VDA5050" if command_mode == "local" else "FMS MQTT"
    options = "".join(
        f'<option value="{_esc(spec.key)}"'
        f'{" selected" if spec.key == selected.key else ""}>'
        f'{_esc(spec.display_name or spec.serial)}</option>'
        for spec in specs
    )
    picker = (
        '<form class="poll-controls" method="get" action="/vda5050">'
        '<label>AMR <select name="robot" onchange="this.form.submit()">'
        f'{options}</select></label></form>'
    )
    broker_class = "good" if snap["connected"] else "bad"
    broker_text = "CONNECTED" if snap["connected"] else f'OFFLINE · {snap["error"]}'
    cards = []
    descriptions = {
        "order": "FMS MQTT 또는 WebUI Local VDA5050 → Adapter",
        "instantActions": "FMS MQTT 또는 WebUI Local VDA5050 → Adapter",
        "state": "Adapter MQTT → FMS",
        "connection": "Adapter MQTT → FMS (retained/LWT)",
    }
    for suffix in ("order", "instantActions", "state", "connection"):
        last = snap["last"].get(suffix)
        latest = "아직 관측 안 됨"
        if last:
            latest = f'{last["direction"]} · {last["timestamp"]}'
        cards.append(
            '<article class="card"><span class="eyebrow">VDA5050</span>'
            f'<h3>{_esc(suffix)}</h3><strong>{snap["counts"].get(suffix, 0)} messages</strong>'
            f'<p class="muted">{_esc(descriptions[suffix])}</p>'
            f'<p class="muted">{_esc(latest)}</p></article>'
        )
    records = []
    for item in snap["records"]:
        raw = json.dumps(item["payload"], ensure_ascii=False, indent=2, default=str)
        records.append(
            '<details class="card vda-record">'
            f'<summary><strong>{_esc(item["suffix"])}</strong> · '
            f'{_esc(item["direction"])} · {_esc(item["transport"])} · '
            f'{_esc(item["timestamp"])}</summary>'
            f'<p class="muted">{_esc(item["topic"])}</p><pre>{_esc(raw)}</pre></details>'
        )
    if not records:
        records.append('<p class="muted">아직 관측된 핵심 VDA5050 메시지가 없습니다.</p>')
    flash = ""
    if query.get("err"):
        flash = f'<p class="error-notice">{_esc(query["err"])}</p>'
    elif query.get("msg"):
        flash = f'<p class="ok">{_esc(query["msg"])}</p>'
    local_checked = " checked" if command_mode == "local" else ""
    mqtt_checked = " checked" if command_mode == "mqtt" else ""
    transport_form = (
        '<section class="card"><h2>WebUI 명령 전송 모드</h2>'
        '<p class="muted"><strong>Local VDA5050</strong>는 order/instantActions JSON을 그대로 localhost TCP로 Adapter에 전달합니다. '
        'MQTT가 불안정하거나 끊겨도 WebUI 버튼 명령은 Adapter까지 전달됩니다. '
        '<strong>FMS MQTT</strong>는 실제 FMS와 동일하게 broker를 거쳐 검증할 때 사용합니다.</p>'
        '<form method="post" action="/vda5050">'
        f'<input type="hidden" name="csrf_token" value="{_esc(csrf)}">'
        f'<input type="hidden" name="robot" value="{_esc(selected.key)}">'
        '<input type="hidden" name="operation" value="set_transport">'
        f'<label style="margin-right:16px"><input type="radio" name="transport" value="local"{local_checked}> Local VDA5050 (보조)</label>'
        f'<label><input type="radio" name="transport" value="mqtt"{mqtt_checked}> FMS MQTT (기본/권장)</label>'
        '<div style="margin-top:10px"><button class="btn primary" type="submit">전송 모드 적용</button></div>'
        '</form></section>'
    )

    publish_form = (
        '<section class="card"><h2>VDA5050 MQTT 테스트 발행</h2>'
        '<p class="muted">이 테스트 폼은 위 WebUI 명령 모드와 관계없이 항상 MQTT broker로 발행합니다. '
        'order 또는 instantActions의 header identity와 시간은 선택한 AMR에 맞게 자동 교체됩니다.</p>'
        '<form method="post" action="/vda5050">'
        f'<input type="hidden" name="csrf_token" value="{_esc(csrf)}">'
        f'<input type="hidden" name="robot" value="{_esc(selected.key)}">'
        '<label>Topic <select name="topic"><option value="instantActions">instantActions</option>'
        '<option value="order">order</option></select></label>'
        f'<textarea name="payload" rows="18" spellcheck="false" '
        f'style="width:100%;font-family:monospace">{_esc(_sample_payload(trace))}</textarea>'
        '<label class="confirm"><input type="checkbox" name="confirm" required> '
        '실제 MQTT 명령 발행 확인 (실물 AMR은 움직일 수 있음)</label>'
        '<button class="btn primary" type="submit">VDA5050 JSON 발행</button></form></section>'
    )
    support = "".join(
        f'<tr><td>{_esc(name)}</td><td>{_esc(direction)}</td><td>{_esc(status)}</td></tr>'
        for name, direction, status in VDA_TOPIC_SUPPORT
        if name in TRACE_TOPICS
    )
    body = (
        f'{flash}<div class="section-head"><div><h1>VDA5050 · {_esc(snap["serial"])}</h1>'
        '<p class="muted">WebUI는 기본적으로 VDA5050 JSON을 FMS MQTT broker에 publish하며, 필요할 때만 Local 전송으로 전환할 수 있습니다.</p>'
        f'</div>{picker}</div>'
        f'<p class="tele good"><span>WebUI Command</span><strong>{_esc(command_mode_label)}</strong>'
        f'<small>{"localhost TCP → Adapter" if command_mode == "local" else "MQTT broker → Adapter"}</small></p>'
        f'<p class="tele {broker_class}"><span>FMS MQTT</span><strong>{_esc(broker_text)}</strong>'
        f'<small>{_esc(snap["host"])}:{snap["port"]} · {_esc(snap["prefix"])}</small></p>'
        f'{transport_form}<div class="cards">{"".join(cards)}</div>'
        '<section class="card"><h2>현재 구현 범위</h2><div class="table-wrap"><table>'
        f'<thead><tr><th>Topic</th><th>방향</th><th>검증</th></tr></thead><tbody>{support}</tbody>'
        '</table></div></section>'
        f'{publish_form}<section><h2>최근 원문 메시지</h2>{"".join(records)}</section>'
        '<style>.vda-record{margin-bottom:8px}.vda-record pre{max-height:360px;overflow:auto;'
        'white-space:pre-wrap;word-break:break-word}.vda-record summary{cursor:pointer}</style>'
    )
    try:
        refresh = max(0, min(30, int(str(query.get("refresh", "0") or "0"))))
    except ValueError:
        refresh = 0
    selected_url = quote(selected.key, safe=":")
    return render.page(
        "VDA5050",
        body,
        refresh=refresh or None,
        current="/vda5050",
        brand_title="SEER VDA5050 Monitor",
        head_actions=(
            f'<a class="btn" href="/vda5050?robot={selected_url}">Refresh</a>'
            + (
                f'<a class="btn" href="/vda5050?robot={selected_url}&refresh=0">Auto stop</a>'
                if refresh
                else f'<a class="btn" href="/vda5050?robot={selected_url}&refresh=1">Auto 1s</a>'
            )
        ),
    )
