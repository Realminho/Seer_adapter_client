"""MQTT client wrapper for the Jibot adapter.

Builds VDA5050 topic names, handles subscriptions, publishing, and MQTT Last Will setup.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import paho.mqtt.client as mqtt
from config.config import Config, get_config


class MQTTClient:
    def __init__(
        self,
        client_id: Optional[str] = None,
        config: Optional[Config] = None,
        on_connection_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        # Accept an injected config so multiple adaptors in separate processes
        # can each use their own serial_number/broker. Falls back to the shared
        # config.toml when called without one.
        # config를 주입받아 adaptor를 여러 개 띄울 때 인스턴스마다 다른
        # serial_number/broker를 쓰게 한다. 없으면 공용 config.toml을 읽는다.
        if config is None:
            config = get_config()


        self.host: str = config.mqtt_broker.host
        self.port: int = config.mqtt_broker.port
        # self.topic_prefix: str = f"{config.mqtt_broker.vda_interface}/{config.vehicle.vda_version}/{config.vehicle.manufacturer}/{config.vehicle.serial_number}"
        self.topic_prefix: str = f"{config.mqtt_broker.vda_interface}/{config.vehicle.vda_version}/{config.vehicle.serial_number}"

        # A unique client_id per adaptor. Two MQTT clients sharing an id evict
        # each other from the broker, so default it to the serial_number (plus
        # PID for safety) when the caller does not provide one.
        # adaptor마다 고유한 client_id. 같은 id를 가진 두 클라이언트는 브로커에서
        # 서로를 끊어버리므로, 지정이 없으면 serial_number(+PID)로 기본값을 만든다.
        if client_id is None:
            client_id = f"adaptor-{config.vehicle.serial_number}-{os.getpid()}"

        self.client_id = client_id
        self.robot_id = config.vehicle.serial_number
        self._on_connection_change = on_connection_change
        self._client = mqtt.Client(client_id=client_id)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._subscriptions: Dict[str, Callable[[str, Any], None]] = {}
        # Every topic ever handed to subscribe(), with its qos. paho drops a
        # subscribe() issued while the socket is down and never replays it, and
        # a PID-suffixed client_id with clean_session=True means the broker has
        # no session to restore either. Without replaying these on_connect the
        # adaptor becomes write-only — state keeps publishing while orders and
        # instantActions are silently never delivered.
        self._subscription_qos: Dict[str, int] = {}
        self._connected: bool = False


    def connect(
        self,
        keepalive: int = 60,
        retry_interval: float = 5.0,
        max_retries: Optional[int] = None,
    ) -> None:
        attempt = 0
        while True:
            attempt += 1
            print(
                f"[MQTT CONNECTING] robot={self.robot_id} "
                f"broker={self.host}:{self.port} client_id={self.client_id} "
                f"topic_prefix={self.topic_prefix} keepalive={keepalive} "
                f"attempt={attempt}"
            )
            try:
                self._client.connect(self.host, self.port, keepalive=keepalive)
            except Exception as exc:
                if max_retries is not None and attempt > max_retries:
                    raise
                print(
                    f"[MQTT CONNECT RETRY] attempt={attempt} error={exc}; "
                    f"retry in {retry_interval:g}s"
                )
                if retry_interval > 0:
                    time.sleep(retry_interval)
                continue

            self._client.loop_start()
            return

    def connect_background(self, keepalive: int = 60) -> None:
        """Non-blocking connect with background auto-reconnect (paho loop thread).

        Unlike :meth:`connect`, this returns immediately even when the broker is
        unreachable, so the caller's startup is not blocked by broker
        availability — the adapter's local IPC (state.json writer + control
        socket) must come up regardless. ``on_connect``/``on_disconnect`` fire as
        the link comes and goes, and queued QoS>0 messages flush on connect.
        """
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        print(
            f"[MQTT CONNECTING] robot={self.robot_id} "
            f"broker={self.host}:{self.port} client_id={self.client_id} "
            f"topic_prefix={self.topic_prefix} keepalive={keepalive} "
            "mode=async reconnect=automatic"
        )
        self._client.connect_async(self.host, self.port, keepalive=keepalive)
        self._client.loop_start()

    def set_last_will(
        self,
        topic: str,
        payload: Any,
        qos: int = 0,
        retain: bool = False,
        use_prefix: bool = True,
    ) -> None:
        full_topic = f"{self.topic_prefix}/{topic}" if use_prefix else topic

        if not isinstance(payload, (str, bytes, bytearray)):
            payload = json.dumps(payload, separators=(",", ":"))

        self._client.will_set(full_topic, payload=payload, qos=qos, retain=retain)

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False

    def _on_connect(self, client, userdata, flags, rc) -> None:  # type: ignore[override]
        self._connected = rc == 0
        if self._connected:
            print(
                f"[MQTT CONNECTED] robot={self.robot_id} "
                f"broker={self.host}:{self.port} client_id={self.client_id} rc={rc}"
            )
            self._resubscribe_all()
        else:
            print(
                f"[MQTT CONNECT FAILED] robot={self.robot_id} "
                f"broker={self.host}:{self.port} client_id={self.client_id} rc={rc}"
            )
        if self._on_connection_change is not None:
            self._on_connection_change(self._connected)

    def _resubscribe_all(self) -> None:
        """Re-send every recorded SUBSCRIBE now that the socket is up.

        Runs on every successful connect, including reconnects, so a broker that
        comes up after the adaptor (or drops and returns) still leaves the
        downlink armed. Re-subscribing an already-subscribed topic is a no-op at
        the broker, so this is safe to repeat.
        """
        if not self._subscription_qos:
            return
        for full_topic, qos in self._subscription_qos.items():
            print(f"[MQTT RESUBSCRIBE] topic='{full_topic}' qos={qos}")
            self._client.subscribe(full_topic, qos)

    def _on_connect_fail(self, client, userdata) -> None:  # type: ignore[override]
        print(
            f"[MQTT CONNECT FAILED] robot={self.robot_id} "
            f"broker={self.host}:{self.port} client_id={self.client_id} "
            "reconnect=automatic"
        )

    def _on_disconnect(self, client, userdata, rc) -> None:  # type: ignore[override]
        self._connected = False
        reconnect = "automatic" if rc != 0 else "no"
        print(
            f"[MQTT DISCONNECTED] robot={self.robot_id} "
            f"broker={self.host}:{self.port} client_id={self.client_id} "
            f"rc={rc} reconnect={reconnect}"
        )
        if self._on_connection_change is not None:
            self._on_connection_change(False)

    def _on_message(self, client, userdata, msg) -> None:  # type: ignore[override]
        callback = self._subscriptions.get(msg.topic)
        if callback:
            callback(msg.topic, msg.payload)

 
    def subscribe(
        self,
        topic: str,
        qos: int = 0,
        callback: Optional[Callable[[str, Any], None]] = None,
        use_prefix: bool = True,
        vehicle_id: Optional[str] = None,
    ) -> str:
        full_topic = f"{self.topic_prefix}/{topic}" if use_prefix else topic
        print(f"MQTT subscribing to topic '{full_topic}'")
        self._subscription_qos[full_topic] = qos
        self._client.subscribe(full_topic, qos)

        if callback:
            self._subscriptions[full_topic] = callback

        return full_topic

    def publish(
        self,
        topic: str,
        payload: Any,
        qos: int = 0,
        retain: bool = False,
        use_prefix: bool = True,
        vehicle_id: Optional[str] = None,
    ) -> None:
        full_topic = self.topic_prefix + "/" + topic

        if not isinstance(payload, (str, bytes, bytearray)):
            payload = json.dumps(payload, separators=(",", ":"))

        self._client.publish(full_topic, payload=payload, qos=qos, retain=retain)

        # print(f"MQTT published to topic '{full_topic}': {payload}")
