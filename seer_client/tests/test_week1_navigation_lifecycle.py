from __future__ import annotations

import asyncio
import math
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from seer_client.client import SeerClient  # pyright: ignore[reportMissingImports]
from seer_client.drive_limits import ManualDriveLimits  # pyright: ignore[reportMissingImports]
from seer_client.navigation import SeerNavigationService  # pyright: ignore[reportMissingImports]
from seer_client.protocol import ApiNumber, SeerApiError  # pyright: ignore[reportMissingImports]
from seer_client.simulator import SeerSimulatorServer  # pyright: ignore[reportMissingImports]


class Week1NavigationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_wait_survives_transient_tcp_loss_without_resending(self):
        responses = iter(
            (
                ConnectionError("task port disconnected"),
                TimeoutError("task response timed out"),
                ValueError("SEER desync on task port"),
                {"task_status": 2, "target_id": "LM9"},
                {"task_status": 4, "target_id": "LM9"},
            )
        )

        class StatusService:
            async def get_task(self):
                response = next(responses)
                if isinstance(response, Exception):
                    raise response
                return response

        client = type(
            "RecoveryClient",
            (),
            {
                "status_service": StatusService(),
                "_task_status": 0,
                "_target_id": "LM9",
                "_station": "",
            },
        )()
        result = await SeerNavigationService(client).wait_until_terminal(
            expected_station="LM9",
            timeout_sec=1.0,
            poll_interval_sec=0.001,
        )
        self.assertEqual(result["task_status"], 4)

    async def _make(self, *, travel_time: float = 0.15):
        server = SeerSimulatorServer(
            travel_time_sec=travel_time,
            landmarks={"LM1": (1.0, 0.0, 0.0), "LM2": (2.0, 1.0, 0.5)},
        )
        ports = await server.start()
        client = SeerClient(
            "127.0.0.1",
            port_map=ports,
            command_timeout=0.5,
            status_poll_interval_sec=0.02,
            allow_legacy_echo_response=False,
        )
        await client.connect_socket()
        return server, client

    async def test_command_acceptance_is_not_navigation_completion(self):
        server, client = await self._make(travel_time=0.2)
        try:
            await client.path_navigation("LM1")
            self.assertEqual(server.task_status, 2)
            waiter = asyncio.create_task(
                client.wait_navigation_terminal(
                    expected_station="LM1", timeout_sec=1.0
                )
            )
            await asyncio.sleep(0.04)
            self.assertFalse(waiter.done(), "accepted command must not finish the action")
            result = await waiter
            self.assertEqual(int(result["task_status"]), 4)
            self.assertAlmostEqual(server.x, 1.0, places=6)
            self.assertEqual(client._station, "LM1")
        finally:
            await client.disconnect()
            await server.stop()

    async def test_unknown_station_is_explicit_failure_not_in_place_success(self):
        server, client = await self._make()
        try:
            with self.assertRaises(SeerApiError):
                await client.path_navigation("MISSING")
            self.assertAlmostEqual(server.x, 0.0, places=6)
            self.assertAlmostEqual(server.y, 0.0, places=6)
            self.assertEqual(server.task_status, 0)
        finally:
            await client.disconnect()
            await server.stop()

    async def test_timeout_cancels_active_navigation_without_retrying_goto(self):
        server, client = await self._make(travel_time=0.5)
        try:
            await client.path_navigation("LM1")
            with self.assertRaises(asyncio.TimeoutError):
                await client.wait_navigation_terminal(
                    expected_station="LM1",
                    timeout_sec=0.05,
                    cancel_on_timeout=True,
                )
            await asyncio.sleep(0.03)
            self.assertEqual(server.task_status, 6)
            self.assertEqual(server.target_id, "")
            self.assertEqual((server.vx, server.vy, server.w), (0.0, 0.0, 0.0))
            gotos = [
                item for item in server.command_log
                if item["api"] == int(ApiNumber.TASK_GOTARGET)
            ]
            cancels = [
                item for item in server.command_log
                if item["api"] == int(ApiNumber.TASK_CANCEL)
            ]
            self.assertEqual(len(gotos), 1)
            self.assertEqual(len(cancels), 1)
        finally:
            await client.disconnect()
            await server.stop()

    async def test_manual_drive_controller_duration_is_deadman_watchdog(self):
        server, client = await self._make()
        try:
            client.manual_drive_limits = ManualDriveLimits(motion_duration_ms=60)
            await client.um_drive(0.1, 0.0, 0.0, 0.0)
            self.assertGreater(server.vx, 0.0)
            await asyncio.sleep(0.11)
            self.assertEqual((server.vx, server.vy, server.w), (0.0, 0.0, 0.0))
            self.assertEqual(server.task_status, 0)
        finally:
            await client.disconnect()
            await server.stop()

    async def test_translate_and_turn_wait_for_verified_terminal_pose(self):
        server, client = await self._make(travel_time=0.06)
        try:
            await client.move_distance(-0.5, 0.1)
            await client.wait_navigation_terminal(
                expected_pose=(-0.5, 0.0, 0.0), timeout_sec=1.0
            )
            self.assertAlmostEqual(server.x, -0.5, places=6)

            await client.turn(-math.pi / 2.0, math.pi / 4.0)
            await client.wait_navigation_terminal(
                expected_pose=(-0.5, 0.0, -math.pi / 2.0), timeout_sec=1.0
            )
            self.assertAlmostEqual(server.angle, -math.pi / 2.0, places=6)
        finally:
            await client.disconnect()
            await server.stop()


if __name__ == "__main__":
    unittest.main()
