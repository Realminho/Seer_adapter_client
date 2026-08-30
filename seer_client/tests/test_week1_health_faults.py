from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from seer_client.client import SeerClient  # pyright: ignore[reportMissingImports]
from seer_client.protocol import ApiNumber, ApiPort, SeerApiError  # pyright: ignore[reportMissingImports]
from seer_client.simulator import SeerSimulatorServer  # pyright: ignore[reportMissingImports]


class Week1HealthFaultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = SeerSimulatorServer(travel_time_sec=0.05)
        ports = await self.server.start()
        self.client = SeerClient(
            "127.0.0.1",
            port_map=ports,
            command_timeout=0.05,
            status_poll_interval_sec=0.02,
            allow_legacy_echo_response=False,
        )
        await self.client.connect_socket()
        await self.client._poll_once()

    async def asyncTearDown(self):
        await self.client.disconnect()
        await self.server.stop()

    async def test_port_health_exposes_per_port_metrics(self):
        health = self.client.connection_health(stale_after=0.5)
        self.assertEqual(health["state"], "ONLINE")
        self.assertEqual(
            set(health["ports"]),
            {"STATE", "CONTROL", "TASK", "CONFIG", "OTHER"},
        )
        state = health["ports"]["STATE"]
        self.assertTrue(state["connected"])
        self.assertGreaterEqual(state["generation"], 1)
        self.assertIsNotNone(state["last_latency_sec"])

    async def test_suspended_task_port_is_degraded_and_recovers(self):
        await self.server.suspend_port(ApiPort.TASK)
        with self.assertRaises(Exception):
            await self.client.pause_navigation()
        health = self.client.connection_health(stale_after=1.0)
        self.assertEqual(health["state"], "DEGRADED")
        self.assertFalse(health["ports"]["TASK"]["connected"])

        await self.server.resume_port(ApiPort.TASK)
        await self.client._ports[ApiPort.TASK].connect()
        await self.client.pause_navigation()
        self.assertEqual(self.client.connection_health(stale_after=1.0)["state"], "ONLINE")

    async def test_ret_code_fault_is_correlated_error_without_disconnect(self):
        self.server.inject_fault(
            ApiPort.STATE,
            ApiNumber.STATUS_BATTERY,
            "ret_code",
            ret_code=40051,
            message="map not found",
        )
        with self.assertRaises(SeerApiError):
            await self.client.get_battery_info()
        snapshot = self.client._ports[ApiPort.STATE].health_snapshot()
        self.assertTrue(snapshot["connected"])
        self.assertGreaterEqual(snapshot["error_count"], 1)

    async def test_wrong_sequence_and_partial_body_recover_cleanly(self):
        for kind in ("wrong_sequence", "partial_body"):
            self.server.inject_fault(ApiPort.STATE, ApiNumber.STATUS_BATTERY, kind)
            with self.assertRaises(Exception):
                await self.client.get_battery_info()
            result = await self.client.get_battery_info()
            self.assertEqual(result["ret_code"], 0)
        snapshot = self.client._ports[ApiPort.STATE].health_snapshot()
        self.assertGreaterEqual(snapshot["reconnect_count"], 2)

    async def test_timeout_does_not_retry_write_command(self):
        self.server.inject_fault(
            ApiPort.OTHER,
            ApiNumber.OTHER_SETDO,
            "delay",
            delay_sec=0.12,
        )
        before = len(
            [item for item in self.server.command_log if item["api"] == int(ApiNumber.OTHER_SETDO)]
        )
        with self.assertRaises(asyncio.TimeoutError):
            await self.client.set_do(1, True)
        await asyncio.sleep(0.15)
        after = len(
            [item for item in self.server.command_log if item["api"] == int(ApiNumber.OTHER_SETDO)]
        )
        self.assertEqual(after - before, 1)


if __name__ == "__main__":
    unittest.main()
