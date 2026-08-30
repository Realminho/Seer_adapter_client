"""Tests for the interactive/manual SEER command console."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src", "seer_client"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from manual_test import SeerManualTestConsole  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.bridge import SeerSimulatedAdapterClient  # pyright: ignore[reportMissingImports]  # noqa: E402


def adapter_config():
    return SimpleNamespace(
        factsheet=SimpleNamespace(
            coordinate_unit_position="mm",
            coordinate_unit_orientation="deg",
        ),
        settings=SimpleNamespace(map_id="console-test"),
        bms_ros=SimpleNamespace(enabled=True),
    )


class ManualTestConsoleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.output = []
        self.client = SeerSimulatedAdapterClient(
            config=adapter_config(),
            initial_position={"x": 1000, "y": -250, "theta": 90},
            initial_battery=65,
        )
        await self.client.connect_socket()
        self.console = SeerManualTestConsole(
            self.client,
            allow_write=True,
            output=self.output.append,
        )

    async def asyncTearDown(self):
        await self.client.disconnect()

    async def test_status_do_and_io_flow(self):
        self.assertTrue(await self.console.execute("status"))
        self.assertTrue(await self.console.execute("do 3 on"))
        self.assertTrue(await self.console.execute("io"))

        combined = "\n".join(self.output)
        self.assertIn('"x": 1000.0', combined)
        self.assertIn('"battery_percent": 65.0', combined)
        self.assertTrue(self.client._simulator_server.digital_outputs[3])
        self.assertIn('"id": 3', combined)
        self.assertIn('"status": true', combined)

    async def test_real_device_write_guard_blocks_do(self):
        read_only = SeerManualTestConsole(
            self.client,
            allow_write=False,
            output=self.output.append,
        )
        with self.assertRaises(PermissionError):
            await read_only.execute("do 1 on")

    async def test_emergency_query_set_and_toggle(self):
        self.assertTrue(await self.console.execute("emergency"))
        self.assertTrue(await self.console.execute("soft_emergency on"))
        self.assertTrue(self.client._soft_emergency)
        self.assertTrue(await self.console.execute("toggle_emergency"))
        self.assertFalse(self.client._soft_emergency)
        combined = "\n".join(self.output)
        self.assertIn('"soft_emc": false', combined)
        self.assertIn('"enabled": true', combined)
        self.assertIn('"enabled": false', combined)

    async def test_goto_route_uses_one_designated_route_request(self):
        self.assertTrue(
            await self.console.execute("goto_route SIM_START SIM_UPPER SIM_GOAL")
        )
        request = self.client._simulator_server.command_log[-1]
        self.assertEqual(request["api"], 3066)
        self.assertEqual(
            [item["id"] for item in request["body"]["move_task_list"]],
            ["SIM_UPPER", "SIM_GOAL"],
        )
        self.assertIn('"api": 3066', "\n".join(self.output))

    async def test_emc_and_emc_release_are_explicit(self):
        self.assertTrue(await self.console.execute("emc"))
        self.assertTrue(self.client._soft_emergency)
        self.assertTrue(await self.console.execute("emc"))
        self.assertTrue(self.client._soft_emergency)
        self.assertTrue(await self.console.execute("emc_release"))
        self.assertFalse(self.client._soft_emergency)
        self.assertTrue(await self.console.execute("emc_release"))
        self.assertFalse(self.client._soft_emergency)
        combined = "\n".join(self.output)
        self.assertIn("[EMC]", combined)
        self.assertIn("[EMC_RELEASE]", combined)

    async def test_real_device_write_guard_blocks_emergency_write(self):
        read_only = SeerManualTestConsole(
            self.client,
            allow_write=False,
            output=self.output.append,
        )
        with self.assertRaises(PermissionError):
            await read_only.execute("soft_emergency on")
        with self.assertRaises(PermissionError):
            await read_only.execute("emc")
        with self.assertRaises(PermissionError):
            await read_only.execute("emc_release")

    async def test_quit_returns_false(self):
        self.assertFalse(await self.console.execute("quit"))


if __name__ == "__main__":
    unittest.main()
