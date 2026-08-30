import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from seer_client.jack_status import read_jack_status, write_jack_status
from seer_client.navigation import SeerNavigationService


class _Status:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.index = 0

    async def get_task(self):
        item = self.payloads[min(self.index, len(self.payloads) - 1)]
        self.index += 1
        return item


class JackCompletionTest(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_active_then_completed_load(self):
        active = {
            "task_status": 2,
            "running_status": 1,
            "move_status_info": json.dumps({
                "BlockMotor": {"operation": "JackLoad", "status": 1},
                "args": {"operation": "JackLoad"},
                "load": {"operation_status": 1, "task_id": 0},
                "status": 1,
            }),
        }
        done = {
            "task_status": 4,
            "running_status": 0,
            "move_status_info": json.dumps({
                "args": {"operation": "JackLoad"},
                "load": {"operation_status": 3, "task_id": 1},
                "status": 3,
            }),
        }
        client = SimpleNamespace(_task_status=None, status_service=_Status([active, done]))
        service = SeerNavigationService(client)
        result = await service.wait_jack_until_terminal(
            "JackLoad", timeout_sec=1.0, poll_interval_sec=0.05
        )
        self.assertEqual(result["task_status"], 4)

    async def test_stale_completed_same_operation_does_not_finish_before_active(self):
        stale = {
            "task_status": 4,
            "move_status_info": json.dumps({
                "args": {"operation": "JackLoad"},
                "load": {"operation_status": 3},
                "status": 3,
            }),
        }
        active = {
            "task_status": 2,
            "move_status_info": json.dumps({
                "args": {"operation": "JackLoad"},
                "load": {"operation_status": 1},
                "status": 1,
            }),
        }
        done = dict(stale)
        client = SimpleNamespace(_task_status=None, status_service=_Status([stale, active, done]))
        service = SeerNavigationService(client)
        result = await service.wait_jack_until_terminal(
            "JackLoad", timeout_sec=1.0, poll_interval_sec=0.05
        )
        self.assertEqual(result["task_status"], 4)
        self.assertGreaterEqual(client.status_service.index, 3)

    async def test_unload_uses_unload_operation_status(self):
        active = {
            "task_status": 2,
            "move_status_info": json.dumps({
                "args": {"operation": "JackUnLoadAndResetShelf"},
                "unload": {"operation_status": 1},
                "status": 1,
            }),
        }
        done = {
            "task_status": 4,
            "move_status_info": json.dumps({
                "args": {"operation": "JackUnLoadAndResetShelf"},
                "unload": {"operation_status": 3},
                "status": 3,
            }),
        }
        client = SimpleNamespace(_task_status=None, status_service=_Status([active, done]))
        service = SeerNavigationService(client)
        result = await service.wait_jack_until_terminal(
            "JackUnLoadAndResetShelf", timeout_sec=1.0, poll_interval_sec=0.05
        )
        self.assertEqual(result["task_status"], 4)

    def test_status_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jack.json"
            write_jack_status(
                path,
                phase="up",
                operation="JackLoad",
                task_status=4,
                operation_status=3,
                message="done",
            )
            payload = read_jack_status(path)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["phase"], "up")
            self.assertEqual(payload["task_status"], 4)
            self.assertEqual(payload["operation_status"], 3)


if __name__ == "__main__":
    unittest.main()
