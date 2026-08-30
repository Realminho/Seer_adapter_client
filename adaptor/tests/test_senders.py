"""Tests for web.senders (Control-view command send seam)."""

import json
import socket
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from web.senders import MqttSender, UdsSender


class _FakeMon:
    def __init__(self, ok):
        self.ok = ok
        self.calls = []

    def publish_json(self, topic, obj, qos=0):
        self.calls.append((topic, obj))
        return self.ok


class MqttSenderTest(unittest.TestCase):
    def test_send_delivers_on_publish_ok(self):
        mon = _FakeMon(True)
        delivered, msg = MqttSender(mon).send({"x": 1}, {"source_user": "op"})
        self.assertTrue(delivered)
        self.assertEqual(mon.calls[0][0], "instantActions")
        self.assertEqual(mon.calls[0][1], {"x": 1})

    def test_send_reports_failure(self):
        delivered, msg = MqttSender(_FakeMon(False)).send({"x": 1}, {})
        self.assertFalse(delivered)


def _serve_once(sock_path, response):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(json.dumps(response).encode())
        conn.close()
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


class UdsSenderTest(unittest.TestCase):
    def test_round_trip_delivered(self):
        with TemporaryDirectory() as d:
            sock = str(Path(d) / "control.sock")
            t = _serve_once(sock, {"delivered": True, "action_ids": ["a1"]})
            delivered, msg = UdsSender(sock).send({"x": 1}, {"source_user": "op"})
            t.join(timeout=2)
        self.assertTrue(delivered)

    def test_fail_closed_when_adapter_absent(self):
        with TemporaryDirectory() as d:
            sock = str(Path(d) / "missing.sock")
            delivered, msg = UdsSender(sock).send({"x": 1}, {})
        self.assertFalse(delivered)
        self.assertIn("not delivered", msg)


if __name__ == "__main__":
    unittest.main()
