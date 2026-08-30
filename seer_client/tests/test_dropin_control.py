from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
source = str(REPO_ROOT / "seer_client" / "src")
if source not in sys.path:
    sys.path.insert(0, source)

from seer_client.dropin_control import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    TcpControlSender,
    _completed_order_is_retained,
    _prepare_manual_to_order_handoff,
    _request_has_action,
    _wait_for_emergency_state_publish,
    install_dropin_control_server,
)


class TcpControlSenderTest(unittest.TestCase):
    def test_sender_round_trips_delivered_response(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        host, port = server.getsockname()
        received = {}

        def serve_once():
            connection, _address = server.accept()
            with connection:
                chunks = []
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                received.update(json.loads(b"".join(chunks).decode("utf-8")))
                connection.sendall(b'{"delivered":true}')
            server.close()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        sender = TcpControlSender(host, port)
        ok, message = sender.send({"actions": []}, {"source_user": "test"})
        thread.join(timeout=2)

        self.assertTrue(ok)
        self.assertEqual(message, "delivered")
        self.assertEqual(received["meta"]["source_user"], "test")

    def test_sender_can_send_complete_order_payload(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        host, port = server.getsockname()
        received = {}

        def serve_once():
            connection, _address = server.accept()
            with connection:
                chunks = []
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                received.update(json.loads(b"".join(chunks).decode("utf-8")))
                connection.sendall(b'{"delivered":true,"order_id":"web-order"}')
            server.close()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        sender = TcpControlSender(host, port)
        order = {
            "headerId": 1,
            "timestamp": "2026-08-13T00:00:00Z",
            "version": "3.0.0",
            "manufacturer": "seer",
            "serialNumber": "SEER-REAL-001",
            "orderId": "web-order",
            "orderUpdateId": 0,
            "nodes": [{"nodeId": "LM1", "sequenceId": 0, "released": True, "actions": []}],
            "edges": [],
        }
        ok, message = sender.send_order(order, {"confirmed": True})
        thread.join(timeout=2)

        self.assertTrue(ok, message)
        self.assertEqual(received["order"], order)
        self.assertTrue(received["meta"]["confirmed"])
        self.assertNotIn("instantActions", received)

    def test_offline_fails_closed(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        host, port = probe.getsockname()
        probe.close()
        ok, message = TcpControlSender(host, port, timeout=0.2).send({}, {})
        self.assertFalse(ok)
        self.assertIn("offline", message)

    def test_detects_seer_emergency_action(self):
        self.assertTrue(
            _request_has_action(
                {
                    "instantActions": {
                        "actions": [{"actionType": "seerEmergencySwitch"}]
                    }
                },
                "seerEmergencySwitch",
            )
        )


class DropInOrderDispatchPatchTest(unittest.TestCase):
    def test_local_order_uses_vda_order_parser_and_shared_order_handler(self):
        parsed = SimpleNamespace(
            serial_number="SEER-REAL-001",
            order_id="order-1",
            nodes=[SimpleNamespace()],
            edges=[SimpleNamespace()],
        )

        class FakeOrder:
            @classmethod
            def from_dict(cls, payload):
                self.assertEqual(payload["orderId"], "order-1")
                return parsed

        class FakeAdapter:
            def _process_control_request(self, request):
                return {"delivered": False, "error": "original-only"}

            def _is_command_for_this_robot(self, topic, serial):
                self.checked = (topic, serial)
                return serial == "SEER-REAL-001"

            def _print_v3_order_preview(self, topic, order):
                self.preview = (topic, order)

            def _handle_v3_order(self, order):
                self.handled = order

        fake_module = SimpleNamespace(Adapter=FakeAdapter, Order=FakeOrder)
        with patch("seer_client.dropin_control.importlib.import_module", return_value=fake_module):
            install_dropin_control_server()

        adapter = FakeAdapter()
        adapter.config = SimpleNamespace(
            vehicle=SimpleNamespace(serial_number="SEER-REAL-001")
        )
        response = adapter._process_control_request(
            {
                "order": {"orderId": "order-1"},
                "meta": {"source_user": "webui", "confirmed": True},
            }
        )
        self.assertTrue(response["delivered"])
        self.assertEqual(response["order_id"], "order-1")
        self.assertIs(adapter.handled, parsed)
        self.assertIs(adapter.preview[1], parsed)
        self.assertEqual(adapter.checked[1], "SEER-REAL-001")


class ManualHandoffTest(unittest.IsolatedAsyncioTestCase):
    async def test_handoff_drains_manual_before_order(self):
        calls = []

        class Vehicle:
            async def um_stop(self):
                calls.append("stop")

        watchdog = object()
        adapter = SimpleNamespace(
            _vehicle=Vehicle(),
            _manual_control_active=True,
            _manual_drive_watchdog=watchdog,
            _seer_manual_drive_pending=(0.05, 0.0, 0.05, 0.0),
            _seer_manual_drive_generation=3,
            _seer_manual_drive_runner=None,
        )

        def cancel_watchdog():
            adapter._manual_drive_watchdog = None
            calls.append("cancel-watchdog")

        adapter._cancel_manual_drive_watchdog = cancel_watchdog
        changed = await _prepare_manual_to_order_handoff(adapter)
        self.assertTrue(changed)
        self.assertFalse(adapter._manual_control_active)
        self.assertIsNone(adapter._seer_manual_drive_pending)
        self.assertEqual(adapter._seer_manual_drive_generation, 4)
        self.assertEqual(calls, ["cancel-watchdog", "stop"])

    async def test_completed_retained_order_does_not_count_as_active_work(self):
        queue = asyncio.Queue()
        adapter = SimpleNamespace(
            order=SimpleNamespace(order_id="done-order"),
            order_worker_task=None,
            order_queue=queue,
            current_order_step=None,
            state=SimpleNamespace(node_states=[], edge_states=[]),
        )
        self.assertTrue(_completed_order_is_retained(adapter))
        await queue.put(object())
        self.assertFalse(_completed_order_is_retained(adapter))


class EmergencyStatePublishWaitTest(unittest.IsolatedAsyncioTestCase):
    async def test_waits_until_vda_state_matches_toggled_vehicle(self):
        publish_reasons = []
        vehicle = SimpleNamespace(_soft_emergency=False, _emergency=False)
        adapter = SimpleNamespace(
            _vehicle=vehicle,
            state=SimpleNamespace(
                safety_state=SimpleNamespace(e_stop=SimpleNamespace(value="NONE"))
            ),
            request_state_publish=publish_reasons.append,
        )
        waiting = asyncio.create_task(
            _wait_for_emergency_state_publish(adapter, False, timeout=0.5)
        )
        await asyncio.sleep(0.03)
        vehicle._soft_emergency = True
        vehicle._emergency = True
        await asyncio.sleep(0.03)
        self.assertFalse(waiting.done())
        adapter.state.safety_state.e_stop = SimpleNamespace(value="REMOTE")
        self.assertTrue(await waiting)
        self.assertIn("SEER emergency WebUI synchronization", publish_reasons)


if __name__ == "__main__":
    unittest.main()
