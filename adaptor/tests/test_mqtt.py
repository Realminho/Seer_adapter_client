import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from utils.mqtt_client import MQTTClient


class _BrokerConfig:
    host = "192.0.2.10"
    port = 11883
    vda_interface = "amr"


class _VehicleConfig:
    serial_number = "SIM-001"
    vda_version = "v3"


class _Config:
    mqtt_broker = _BrokerConfig()
    vehicle = _VehicleConfig()


class _FakePahoClient:
    attempts = 0

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self.on_connect = None
        self.on_message = None
        self.loop_started = False

    def connect(self, host: str, port: int, keepalive: int = 60) -> None:
        _FakePahoClient.attempts += 1
        if _FakePahoClient.attempts < 3:
            raise TimeoutError("timed out")

    def loop_start(self) -> None:
        self.loop_started = True


class MQTTClientTest(unittest.TestCase):
    def test_connect_retries_failed_initial_connection_and_logs_attempts(self) -> None:
        _FakePahoClient.attempts = 0

        with patch("utils.mqtt_client.mqtt.Client", _FakePahoClient):
            client = MQTTClient(config=_Config())
            output = io.StringIO()

            with redirect_stdout(output):
                client.connect(retry_interval=0)

        self.assertEqual(_FakePahoClient.attempts, 3)
        self.assertIn("[MQTT CONNECT RETRY] attempt=1 error=timed out", output.getvalue())
        self.assertIn("[MQTT CONNECT RETRY] attempt=2 error=timed out", output.getvalue())
        self.assertIn(
            "[MQTT CONNECTING] robot=SIM-001 broker=192.0.2.10:11883",
            output.getvalue(),
        )


class MqttConnectionHookTest(unittest.TestCase):
    def _client(self, cb):
        with patch("utils.mqtt_client.mqtt.Client"):
            return MQTTClient(config=_Config(), on_connection_change=cb)

    def test_on_connect_success_fires_true(self) -> None:
        seen = []
        client = self._client(seen.append)
        output = io.StringIO()
        with redirect_stdout(output):
            client._on_connect(None, None, None, 0)
        self.assertEqual(seen, [True])
        self.assertIn(
            "[MQTT CONNECTED] robot=SIM-001 broker=192.0.2.10:11883",
            output.getvalue(),
        )

    def test_on_connect_failure_fires_false(self) -> None:
        seen = []
        self._client(seen.append)._on_connect(None, None, None, 5)
        self.assertEqual(seen, [False])

    def test_on_disconnect_fires_false(self) -> None:
        seen = []
        client = self._client(seen.append)
        output = io.StringIO()
        with redirect_stdout(output):
            client._on_disconnect(None, None, 7)
        self.assertEqual(seen, [False])
        self.assertIn("rc=7 reconnect=automatic", output.getvalue())

    def test_on_disconnect_handler_is_registered(self) -> None:
        with patch("utils.mqtt_client.mqtt.Client") as fake_cls:
            client = MQTTClient(config=_Config())
            self.assertEqual(fake_cls.return_value.on_disconnect, client._on_disconnect)
            self.assertEqual(fake_cls.return_value.on_connect_fail, client._on_connect_fail)

    def test_connect_background_is_nonblocking(self) -> None:
        with patch("utils.mqtt_client.mqtt.Client") as fake_cls:
            client = MQTTClient(config=_Config())
            client.connect_background()
        inst = fake_cls.return_value
        inst.connect_async.assert_called_once()
        inst.loop_start.assert_called_once()
        inst.connect.assert_not_called()  # must NOT use the blocking connect


class MqttResubscribeTest(unittest.TestCase):
    """A subscribe issued while the broker is down must be replayed on connect.

    Regression: the robot booted before the broker, paho dropped both SUBSCRIBEs,
    and the adaptor ran for hours publishing state while never receiving an order
    — no error anywhere, the FMS only saw orderCompareStatus=MISMATCH with an
    empty receivedOrderId.
    """

    def test_subscribe_made_while_offline_is_replayed_on_connect(self) -> None:
        with patch("utils.mqtt_client.mqtt.Client") as fake_cls:
            client = MQTTClient(config=_Config())
            inst = fake_cls.return_value
            with redirect_stdout(io.StringIO()):
                client.subscribe("order", qos=0, callback=lambda *_: None)
                client.subscribe("instantActions", qos=1, callback=lambda *_: None)
                inst.subscribe.reset_mock()  # drop the (dropped-by-paho) initial calls
                client._on_connect(None, None, None, 0)

        self.assertEqual(
            inst.subscribe.call_args_list,
            [
                call("amr/v3/SIM-001/order", 0),
                call("amr/v3/SIM-001/instantActions", 1),
            ],
        )

    def test_failed_connect_does_not_resubscribe(self) -> None:
        with patch("utils.mqtt_client.mqtt.Client") as fake_cls:
            client = MQTTClient(config=_Config())
            inst = fake_cls.return_value
            with redirect_stdout(io.StringIO()):
                client.subscribe("order", qos=0, callback=lambda *_: None)
                inst.subscribe.reset_mock()
                client._on_connect(None, None, None, 5)

        inst.subscribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
