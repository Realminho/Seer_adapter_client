import asyncio
import sys
import unittest
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[2] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from jibot_client import JIBOT


class TestConnectionConfig(unittest.TestCase):
    def test_defaults_preserve_current_behavior(self):
        v = JIBOT("1.2.3.4")
        self.assertEqual((v.user, v.password, v.device_type), ("test", "test", "pc"))
        self.assertEqual(v.recv_buffer_bytes, 32768)
        self.assertEqual(v.status_log_interval_sec, 5.0)
        self.assertEqual(v.battery_log_interval_sec, 30.0)

    def test_um_connect_sends_configured_credentials(self):
        v = JIBOT("1.2.3.4", user="ops", password="s3cret", device_type="amr")
        sent = {}

        async def fake_send(cmd):
            sent.update(cmd)

        v.json_cmd_to_jibot = fake_send
        asyncio.run(v.um_connect())
        self.assertEqual(sent["user"], "ops")
        self.assertEqual(sent["password"], "s3cret")
        self.assertEqual(sent["device_type"], "amr")
