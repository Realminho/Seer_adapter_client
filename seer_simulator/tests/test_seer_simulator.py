from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SIM_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SIM_ROOT.parent
for source in (
    REPO_ROOT,
    REPO_ROOT / "seer_client" / "src",
    REPO_ROOT / "amr-client-contract" / "src",
    REPO_ROOT / "jibot-client" / "src",
    REPO_ROOT / "adaptor",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_simulator import SimulatedJIBOT, SimulatedSEER  # pyright: ignore[reportMissingImports]
from seer_client.map_view import (  # pyright: ignore[reportMissingImports]
    find_route_options,
    normalize_map,
)


def _config():
    return SimpleNamespace(
        factsheet=SimpleNamespace(
            coordinate_unit_position="mm",
            coordinate_unit_orientation="deg",
        ),
        settings=SimpleNamespace(map_id="webui-sim", nearest_node_mode="pathPoint"),
        bms_ros=SimpleNamespace(enabled=True),
    )


class SimulatedSeerTest(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_compatibility_alias_uses_real_seer_tcp(self):
        self.assertIs(SimulatedJIBOT, SimulatedSEER)
        client = SimulatedSEER(
            config=_config(),
            initial_position={"x": 1000, "y": -500, "theta": 90},
            initial_battery=65,
        )
        try:
            await client.connect_socket()
            await client.connect()
            await client.get_robot_info()
            self.assertTrue(client.is_connected())
            self.assertTrue(client.is_simulator)
            self.assertEqual(client.vendor, "seer")
            self.assertAlmostEqual(client.x, 1000)
            self.assertAlmostEqual(client.y, -500)
            self.assertAlmostEqual(client.th, 90)
            self.assertAlmostEqual(client.battery or 0, 65)

            model = normalize_map(client._simulator_server.map_data)
            routes = find_route_options(model, "SIM_START", "SIM_GOAL")
            self.assertEqual(len(routes), 2)
            self.assertEqual(
                {tuple(route["points"]) for route in routes},
                {
                    ("SIM_START", "SIM_UPPER", "SIM_GOAL"),
                    ("SIM_START", "SIM_LOWER", "SIM_GOAL"),
                },
            )

            await client.set_do(3, True)
            io = await client.send_command("query_io")
            self.assertTrue(io["DO"][3]["status"])

            self.assertTrue(await client.read_di(2))
            await client.jack_load()
            active = await client.get_task_status()
            self.assertEqual(active["task_status"], 2)
            self.assertTrue(await client.read_di(2))
            await client.navigation.wait_jack_until_terminal("JackLoad")
            self.assertFalse(await client.read_di(2))
            self.assertTrue(client._simulator_server.jack_loaded)
            await client.jack_unload()
            active = await client.get_task_status()
            self.assertEqual(active["task_status"], 2)
            self.assertFalse(await client.read_di(2))
            await client.navigation.wait_jack_until_terminal("JackUnLoadAndResetShelf")
            self.assertTrue(await client.read_di(2))
            self.assertFalse(client._simulator_server.jack_loaded)

            await client.goto_xyz(1500, 250, 45)
            await asyncio.sleep(0.35)
            await client.get_robot_info()
            self.assertAlmostEqual(client.x, 1500)
            self.assertAlmostEqual(client.y, 250)
            self.assertAlmostEqual(client.th, 45)

            # This is the exact southbound method the unchanged Adapter uses
            # when a VDA5050 order node carries nodePosition in simulator mode.
            await client.goto_node_position("FMS_NODE", 1750, -250, 30)
            await asyncio.sleep(0.35)
            await client.get_robot_info()
            self.assertAlmostEqual(client.x, 1750)
            self.assertAlmostEqual(client.y, -250)
            self.assertAlmostEqual(client.th, 30)
        finally:
            await client.disconnect()


class SimulatedSeerDirectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_negative_translation_and_clockwise_turn_match_seer_semantics(self):
        client = SimulatedSEER(config=_config(), initial_position={"x": 0, "y": 0, "theta": 0})
        try:
            await client.connect_socket()
            await client.move_distance(-1000, 200)
            await asyncio.sleep(0.35)
            await client.get_robot_info()
            self.assertAlmostEqual(client.x, -1000, delta=1.0)

            await client.turn(-90, 30)
            await asyncio.sleep(0.35)
            await client.get_robot_info()
            self.assertAlmostEqual(client.th, -90, delta=1.0)
        finally:
            await client.disconnect()


if __name__ == "__main__":
    unittest.main()
