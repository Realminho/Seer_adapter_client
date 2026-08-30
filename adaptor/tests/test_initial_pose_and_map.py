"""Tests for runtime pose resolution and simulator map loading."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_ROOT.parent
for p in (ADAPTER_ROOT, REPO_ROOT / "jibot-simulator", REPO_ROOT / "jibot-client" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from main import resolve_initial_position, resolve_simulator_initial_position


class ResolveInitialPositionTest(unittest.TestCase):
    def test_no_snapshot_no_override_returns_none(self):
        self.assertIsNone(resolve_initial_position(None))

    def test_snapshot_used_when_no_override(self):
        snap = {"x": 1.0, "y": 2.0, "theta": 0.5}
        self.assertEqual(resolve_initial_position(snap), snap)

    def test_cli_overrides_each_axis(self):
        snap = {"x": 1.0, "y": 2.0, "theta": 0.5}
        result = resolve_initial_position(snap, x=9.0, theta=0.0)
        self.assertEqual(result, {"x": 9.0, "y": 2.0, "theta": 0.0})

    def test_override_without_snapshot_fills_origin(self):
        result = resolve_initial_position(None, x=3.0)
        self.assertEqual(result, {"x": 3.0, "y": 0.0, "theta": 0.0})

    def test_simulator_loaded_map_without_cli_pose_randomizes_from_map(self):
        snapshot = {"x": 1.0, "y": 2.0, "theta": 0.5}
        initial_map = {"nodes": {"S1": (10.0, 20.0, 0.0)}}

        self.assertIsNone(
            resolve_simulator_initial_position(snapshot, initial_map)
        )

    def test_simulator_cli_pose_overrides_loaded_map_randomization(self):
        snapshot = {"x": 1.0, "y": 2.0, "theta": 0.5}
        initial_map = {"nodes": {"S1": (10.0, 20.0, 0.0)}}

        self.assertEqual(
            resolve_simulator_initial_position(snapshot, initial_map, x=9.0),
            {"x": 9.0, "y": 2.0, "theta": 0.5},
        )

    def test_simulator_without_loaded_map_uses_snapshot(self):
        snapshot = {"x": 1.0, "y": 2.0, "theta": 0.5}

        self.assertEqual(
            resolve_simulator_initial_position(snapshot, None),
            snapshot,
        )


class SimulatorInitialMapTest(unittest.TestCase):
    def _make_sim(self, **kwargs):
        from cls_jibot_simulator import SimulatedJIBOT

        return SimulatedJIBOT(config=None, **kwargs)

    def test_initial_map_populates_nodes(self):
        initial_map = {
            "map_id": "m1",
            "nodes": {"S1": (1.0, 2.0, 0.5), "S2": [3.0, 4.0]},
            "raw": {"Objs": {}},
        }
        sim = self._make_sim(initial_map=initial_map)
        self.assertEqual(sim._map_nodes["S1"], (1.0, 2.0, 0.5))
        self.assertEqual(sim._map_nodes["S2"], (3.0, 4.0, 0.0))
        self.assertEqual(sim._map_raw, {"Objs": {}})

    def test_no_initial_map_leaves_nodes_empty(self):
        sim = self._make_sim()
        self.assertEqual(sim._map_nodes, {})

    def test_get_map_returns_loaded_nodes(self):
        import asyncio

        initial_map = {"map_id": "m1", "nodes": {"S1": (1.0, 2.0, 0.0)}, "raw": None}
        sim = self._make_sim(initial_map=initial_map)
        nodes = asyncio.run(sim.get_map())
        self.assertEqual(nodes, {"S1": (1.0, 2.0, 0.0)})

    def test_initial_battery_is_randomized(self):
        with patch("random.randint", return_value=87):
            sim = self._make_sim()

        self.assertEqual(sim._battery, 87.0)

    def test_node_motion_consumes_one_percent_battery(self):
        import asyncio

        async def scenario():
            sim = self._make_sim()
            sim._battery = 87.0
            await sim.connect_socket()
            try:
                await sim.goto_node_position("S2", 1000.0, 0.0, 0.0)
                self.assertEqual(sim._battery, 86.0)

                await sim.get_robot_info(100_000)
                self.assertEqual(sim._battery, 86.0)
            finally:
                await sim.disconnect()

        asyncio.run(scenario())

    def test_goto_point_moves_to_loaded_map_node(self):
        import asyncio

        async def scenario():
            sim = self._make_sim(
                initial_map={
                    "map_id": "m1",
                    "nodes": {"S1": (0.0, 0.0, 0.0), "S2": (80.0, 0.0, 0.2)},
                    "raw": None,
                },
                initial_position={"x": 0.0, "y": 0.0, "theta": 0.0},
            )
            await sim.connect_socket()
            try:
                await sim.goto_point("S2")
                for _ in range(100):
                    if sim._status == sim.config.jibot_status.stop:
                        break
                    await asyncio.sleep(0.05)
            finally:
                await sim.disconnect()

            self.assertAlmostEqual(sim._x, 80.0)
            self.assertAlmostEqual(sim._y, 0.0)
            self.assertAlmostEqual(sim._th, 0.2)

        asyncio.run(scenario())

    def test_simulator_node_travel_sec_overrides_speed_for_node_motion(self):
        import asyncio

        async def scenario():
            sim = self._make_sim(
                initial_map={
                    "map_id": "m1",
                    "nodes": {"S1": (0.0, 0.0, 0.0), "S2": (10_000.0, 0.0, 0.0)},
                    "raw": None,
                },
                initial_position={"x": 0.0, "y": 0.0, "theta": 0.0},
            )
            sim.config.settings.simulator_node_travel_sec = 0.2
            await sim.connect_socket()
            try:
                await sim.goto_point("S2")
                await asyncio.sleep(0.35)
            finally:
                await sim.disconnect()

            self.assertAlmostEqual(sim._x, 10_000.0)
            self.assertAlmostEqual(sim._y, 0.0)
            self.assertEqual(sim._status, sim.config.jibot_status.stop)

        asyncio.run(scenario())

    def test_initial_map_places_simulator_on_random_loaded_node(self):
        initial_map = {
            "map_id": "m1",
            "nodes": {
                "S1": (100.0, 200.0, 0.1),
                "S2": (300.0, 400.0, 0.2),
            },
            "raw": None,
        }
        sim = self._make_sim(initial_map=initial_map)

        self.assertIn(sim._station, initial_map["nodes"])
        self.assertEqual(
            (sim._x, sim._y, sim._th),
            initial_map["nodes"][sim._station],
        )


class FakeVehicle:
    """Minimal real-robot stand-in for exercising robot_info_loop's map save."""

    def __init__(self, connected=True, parsed=True):
        self.is_simulator = False
        self._connected = connected
        self._map_nodes = {}
        self._map_path_points = {}
        # _map_raw is set only when a fresh UmGetMap was actually parsed.
        self._map_raw = {"Objs": {}} if parsed else None
        self._x, self._y, self._th = 0.0, 0.0, 0.0

    def is_connected(self):
        return self._connected

    async def get_robot_info(self, interval_ms):
        pass

    async def get_motor_state(self, interval_ms):
        pass

    async def get_localization_info(self, interval_ms):
        pass

    async def get_map(self, interval_ms=-1, timeout=5.0):
        self._map_nodes = {"S1": (1.0, 2.0, 0.0)}
        self._map_path_points = {"p40": (3.0, 4.0, 90.0)}
        return self._map_nodes


class SimulatorLastNodeFromMapTest(unittest.TestCase):
    """Simulator: loaded map + current pose -> computed lastNodeId."""

    def test_last_node_id_resolved_from_loaded_map_and_pose(self):
        from adapter_jibot import Adapter
        from cls_jibot_simulator import SimulatedJIBOT

        initial_map = {
            "map_id": "lab2m",
            "nodes": {
                "S1": (0.0, 0.0, 0.0),
                "S2": (1000.0, 500.0, 0.0),
                "S3": (3000.0, 4000.0, 1.57),
            },
            "raw": {"Objs": {}},
        }
        adapter = Adapter()
        vehicle = SimulatedJIBOT(
            config=adapter.config,
            initial_position={"x": 980.0, "y": 520.0, "theta": 0.0},
            initial_map=initial_map,
        )
        adapter.set_vehicle(vehicle)

        # The map flows through to the adapter's node resolution.
        self.assertEqual(adapter._map_nodes(), initial_map["nodes"])

        # The same call the state loop makes every cycle.
        adapter._update_nearest_node_from_position(vehicle._x, vehicle._y, vehicle._th)
        self.assertEqual(adapter._last_node_id, "S2")
        self.assertEqual(adapter._nearest_node_id, "S2")

    def test_last_node_id_starts_on_random_loaded_map_node(self):
        from adapter_jibot import Adapter
        from cls_jibot_simulator import SimulatedJIBOT

        initial_map = {
            "map_id": "lab2m",
            "nodes": {
                "S1": (100.0, 200.0, 0.0),
                "S2": (1000.0, 500.0, 0.0),
                "S3": (3000.0, 4000.0, 1.57),
            },
            "raw": {"Objs": {}},
        }
        adapter = Adapter()
        vehicle = SimulatedJIBOT(config=adapter.config, initial_map=initial_map)
        adapter.set_vehicle(vehicle)

        adapter._update_nearest_node_from_position(vehicle._x, vehicle._y, vehicle._th)
        self.assertIn(adapter._last_node_id, initial_map["nodes"])
        self.assertEqual(adapter._last_node_id, vehicle._station)
        self.assertEqual(adapter._nearest_node_id, adapter._last_node_id)


class RobotInfoLoopMapSaveTest(unittest.TestCase):
    def _run_one_iteration(self, vehicle, map_path):
        import asyncio
        import io
        from contextlib import redirect_stdout

        import main

        async def driver():
            # Break out of the infinite loop after its first sleep.
            real_sleep = asyncio.sleep

            async def stop_after_first(_delay):
                raise asyncio.CancelledError

            main.asyncio.sleep = stop_after_first
            try:
                await main.robot_info_loop(
                    vehicle,
                    interval_sec=0,
                    map_store_path=map_path,
                    position_map_id="m1",
                    position_serial_number="SIM-001",
                )
            except asyncio.CancelledError:
                pass
            finally:
                main.asyncio.sleep = real_sleep

        buf = io.StringIO()
        with redirect_stdout(buf):
            asyncio.run(driver())
        return buf.getvalue()

    def test_saves_and_logs_location_when_connected(self):
        import tempfile

        from jibot_map_store import load_map_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jibot-map.json"
            out = self._run_one_iteration(FakeVehicle(connected=True), path)
            self.assertTrue(path.exists())
            self.assertIn("[JIBOT MAP SAVED]", out)
            self.assertIn(str(path.resolve()), out)
            self.assertEqual(load_map_snapshot(path)["nodes"]["S1"], (1.0, 2.0, 0.0))
            self.assertEqual(
                load_map_snapshot(path)["nodes"]["p40"],
                (3.0, 4.0, 90.0),
            )

    def test_no_save_when_disconnected(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path = Path(tmp) / "jibot-map.json"
            out = self._run_one_iteration(FakeVehicle(connected=False), path)
            self.assertFalse(path.exists())
            self.assertNotIn("[JIBOT MAP SAVED]", out)


if __name__ == "__main__":
    unittest.main()
