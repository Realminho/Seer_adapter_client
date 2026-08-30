import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable, Optional

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from protocol.vda5050_3_0.messages import InstantActions
from protocol.vda5050_3_0.transport import Vda5050V3Transport


class DummyMqtt:
    def __init__(self) -> None:
        self.callback: Optional[Callable[[str, Any], None]] = None

    def subscribe(
        self,
        topic: str,
        qos: int = 0,
        callback: Optional[Callable[[str, Any], None]] = None,
        use_prefix: bool = True,
    ) -> str:
        self.callback = callback
        return f"amr/v3/robot/{topic}" if use_prefix else topic


class Vda5050V3TransportTest(unittest.TestCase):
    def test_malformed_instant_actions_does_not_escape_mqtt_callback(self) -> None:
        mqtt = DummyMqtt()
        transport = Vda5050V3Transport(mqtt)
        received: list[tuple[str, Any]] = []

        transport.subscribe_instant_actions(
            lambda topic, payload: received.append((topic, payload))
        )

        self.assertIsNotNone(mqtt.callback)
        output = io.StringIO()
        with redirect_stdout(output):
            mqtt.callback("amr/v3/robot/instantActions", b'{"actions":[]}')

        self.assertEqual(received, [])
        self.assertIn('payload={"actions":[]}', output.getvalue())

    def test_valid_instant_actions_is_decoded_before_callback(self) -> None:
        mqtt = DummyMqtt()
        transport = Vda5050V3Transport(mqtt)
        received: list[tuple[str, Any]] = []

        transport.subscribe_instant_actions(
            lambda topic, payload: received.append((topic, payload))
        )

        self.assertIsNotNone(mqtt.callback)
        mqtt.callback(
            "amr/v3/robot/instantActions",
            b"""
            {
                "headerId": 1,
                "timestamp": "2026-06-12T00:00:00.000Z",
                "version": "3.0.0",
                "manufacturer": "jibot",
                "serialNumber": "HN-SH6-TR-002",
                "actions": []
            }
            """,
        )

        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0][1], InstantActions)


if __name__ == "__main__":
    unittest.main()
