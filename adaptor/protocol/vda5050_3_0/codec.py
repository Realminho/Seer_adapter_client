import json
from typing import Any, Dict, Union

from protocol.vda5050_3_0.messages import InstantActions, Order


Payload = Union[str, bytes, bytearray, Dict[str, Any]]


def decode_payload(payload: Payload) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    return json.loads(payload)


def encode_message(message: Any) -> str:
    payload = message.to_dict() if hasattr(message, "to_dict") else message
    return json.dumps(payload, separators=(",", ":"))


def parse_order(payload: Payload) -> Order:
    return Order.from_dict(decode_payload(payload))


def parse_instant_actions(payload: Payload) -> InstantActions:
    return InstantActions.from_dict(decode_payload(payload))


def decode_message(topic: str, payload: Payload) -> Any:
    if topic.endswith("/order"):
        return parse_order(payload)
    if topic.endswith("/instantActions"):
        return parse_instant_actions(payload)
    return decode_payload(payload)
