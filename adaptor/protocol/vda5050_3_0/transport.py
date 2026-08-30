from typing import Any, Callable, Optional

from protocol.vda5050_3_0.codec import decode_message


def _format_decode_error_payload(payload: Any) -> str:
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    if isinstance(payload, bytearray):
        return bytes(payload).decode("utf-8", errors="replace")
    return str(payload)


class Vda5050V3Transport:
    """Send/receive helpers that adapt v3.0 message classes to MQTTClient."""

    def __init__(self, mqtt_client: Any) -> None:
        self._mqtt = mqtt_client

    def publish(
        self,
        topic: str,
        message: Any,
        qos: int = 0,
        retain: bool = False,
        use_prefix: bool = True,
    ) -> None:
        payload = message.to_dict() if hasattr(message, "to_dict") else message
        self._mqtt.publish(
            topic,
            payload=payload,
            qos=qos,
            retain=retain,
            use_prefix=use_prefix,
        )

    def publish_state(self, state: Any, qos: int = 0) -> None:
        self.publish("state", state, qos=qos, retain=False)

    def publish_connection(
        self,
        connection: Any,
        qos: int = 0,
        retain: bool = True,
    ) -> None:
        self.publish("connection", connection, qos=qos, retain=retain)

    def set_connection_last_will(
        self,
        connection: Any,
        qos: int = 0,
        retain: bool = True,
    ) -> None:
        payload = connection.to_dict() if hasattr(connection, "to_dict") else connection
        self._mqtt.set_last_will("connection", payload=payload, qos=qos, retain=retain)

    def subscribe(
        self,
        topic: str,
        callback: Callable[[str, Any], None],
        qos: int = 0,
        use_prefix: bool = True,
    ) -> str:
        def _decode_callback(full_topic: str, payload: Any) -> None:
            try:
                decoded = decode_message(full_topic, payload)
            except Exception as exc:
                print(
                    "[VDA5050 v3.0 MQTT DECODE ERROR] "
                    f"topic={full_topic} error={exc} "
                    f"payload={_format_decode_error_payload(payload)}"
                )
                return
            callback(full_topic, decoded)

        return self._mqtt.subscribe(
            topic,
            qos=qos,
            callback=_decode_callback,
            use_prefix=use_prefix,
        )

    def subscribe_order(
        self,
        callback: Callable[[str, Any], None],
        qos: int = 0,
    ) -> str:
        return self.subscribe("order", callback=callback, qos=qos)

    def subscribe_instant_actions(
        self,
        callback: Callable[[str, Any], None],
        qos: int = 0,
    ) -> str:
        return self.subscribe("instantActions", callback=callback, qos=qos)


def publish_v3_message(
    mqtt_client: Any,
    topic: str,
    message: Any,
    qos: int = 0,
    retain: bool = False,
) -> None:
    Vda5050V3Transport(mqtt_client).publish(
        topic,
        message,
        qos=qos,
        retain=retain,
    )


def subscribe_v3_message(
    mqtt_client: Any,
    topic: str,
    callback: Callable[[str, Any], None],
    qos: int = 0,
) -> str:
    return Vda5050V3Transport(mqtt_client).subscribe(
        topic,
        callback=callback,
        qos=qos,
    )
