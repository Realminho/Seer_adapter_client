"""Safety regression tests for SEER emergency and suspended navigation."""

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

from seer_client.client import SeerClient  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.protocol import ApiNumber  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.simulator import SeerSimulatorServer  # pyright: ignore[reportMissingImports]  # noqa: E402


class FakeEmergencyIO:
    def __init__(self, client: SeerClient, calls: list[tuple[str, object]]) -> None:
        self.client = client
        self.calls = calls

    async def set_soft_emergency(self, status: bool) -> None:
        enabled = bool(status)
        self.calls.append(("emergency", enabled))
        self.client._soft_emergency = enabled


class FakeNavigation:
    def __init__(
        self, calls: list[tuple[str, object]], *, failures: int = 0
    ) -> None:
        self.calls = calls
        self.failures = int(failures)

    async def cancel(self) -> None:
        self.calls.append(("cancel", None))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("simulated TASK_CANCEL failure")


def safety_client(*, cancel_failures: int = 0) -> tuple[SeerClient, list]:
    client = SeerClient.__new__(SeerClient)
    calls: list[tuple[str, object]] = []
    client._soft_emergency = False
    client._emergency_cancel_confirmed = False
    client.io = FakeEmergencyIO(client, calls)
    client.navigation = FakeNavigation(calls, failures=cancel_failures)
    return client, calls


class EmergencyNavigationCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_activation_asserts_emergency_before_canceling_navigation(self):
        client, calls = safety_client()

        self.assertTrue(await client.set_soft_emergency(True))

        self.assertEqual(calls, [("emergency", True), ("cancel", None)])
        self.assertTrue(client._emergency_cancel_confirmed)

    async def test_failed_activation_cancel_is_retried_before_release(self):
        client, calls = safety_client(cancel_failures=1)

        with self.assertRaisesRegex(RuntimeError, "emergency is ACTIVE"):
            await client.set_soft_emergency(True)

        self.assertTrue(client._soft_emergency)
        self.assertFalse(client._emergency_cancel_confirmed)
        self.assertFalse(await client.set_soft_emergency(False))
        self.assertEqual(
            calls,
            [
                ("emergency", True),
                ("cancel", None),
                ("cancel", None),
                ("emergency", False),
            ],
        )

    async def test_release_fails_closed_when_cancel_cannot_be_confirmed(self):
        client, calls = safety_client(cancel_failures=1)
        client._soft_emergency = True

        with self.assertRaisesRegex(RuntimeError, "remains ACTIVE"):
            await client.set_soft_emergency(False)

        self.assertTrue(client._soft_emergency)
        self.assertEqual(calls, [("cancel", None)])

    async def test_simulated_path_nav_does_not_resume_after_emergency_release(self):
        server = SeerSimulatorServer(
            travel_time_sec=0.5,
            landmarks={"GOAL": (5.0, 0.0, 0.0)},
        )
        ports = await server.start()
        client = SeerClient(
            "127.0.0.1",
            port_map=ports,
            is_simulator=True,
            adapter_position_unit="m",
            adapter_orientation_unit="rad",
        )
        try:
            await client.connect_socket()
            await client.path_navigation("GOAL", source_id="SELF_POSITION")
            await asyncio.sleep(0.08)

            await client.set_soft_emergency(True)
            stopped_x = server.x
            await client.set_soft_emergency(False)
            await asyncio.sleep(0.55)

            self.assertAlmostEqual(server.x, stopped_x)
            self.assertEqual(server.task_status, 6)
            safety_commands = [
                (entry["api"], entry["body"])
                for entry in server.command_log
                if entry["api"]
                in {
                    int(ApiNumber.OTHER_SOFT_EMERGENCY),
                    int(ApiNumber.TASK_CANCEL),
                }
            ]
            self.assertEqual(
                safety_commands,
                [
                    (int(ApiNumber.OTHER_SOFT_EMERGENCY), {"status": True}),
                    (int(ApiNumber.TASK_CANCEL), {}),
                    (int(ApiNumber.OTHER_SOFT_EMERGENCY), {"status": False}),
                ],
            )
        finally:
            await client.disconnect()
            await server.stop()


if __name__ == "__main__":
    unittest.main()
