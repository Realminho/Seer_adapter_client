"""Tests for the JIBOT simulator charging behaviour.

In simulator mode a startCharging/UmDock should make the battery climb like a
real charger (1% per second), hold at 100%, and stop when the robot drives or
is told to stop.
"""

import asyncio
import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_ROOT.parent
for p in (ADAPTER_ROOT, REPO_ROOT / "jibot-simulator", REPO_ROOT / "jibot-client" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from adapter_jibot import Adapter
from cls_jibot_simulator import SimulatedJIBOT
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionState, ActionStatus
from protocol.vda5050_3_0.messages import Order


class SimulatorChargingTest(unittest.TestCase):
    def _make_sim(self) -> SimulatedJIBOT:
        sim = SimulatedJIBOT(config=Adapter().config)
        # Speed the 1%/sec tick up so the test stays fast and deterministic.
        sim._charge_tick_sec = 0.02
        return sim

    def test_dock_starts_charging_and_battery_climbs(self) -> None:
        async def scenario() -> None:
            sim = self._make_sim()
            await sim.connect_socket()
            sim._battery = 50.0
            try:
                await sim._apply_command({"#CMD#": "UmDock"})

                self.assertTrue(sim._charging)
                self.assertEqual(sim._status, sim.config.jibot_status.charging)

                # After ~4 ticks the battery has climbed by roughly 4%.
                await asyncio.sleep(sim._charge_tick_sec * 4 + sim._charge_tick_sec / 2)
                self.assertGreaterEqual(sim._battery, 53.0)
                self.assertLessEqual(sim._battery, 56.0)
            finally:
                await sim.disconnect()

        asyncio.run(scenario())

    def test_charging_holds_at_full(self) -> None:
        async def scenario() -> None:
            sim = self._make_sim()
            await sim.connect_socket()
            sim._battery = 99.0
            try:
                await sim._apply_command({"#CMD#": "UmDock"})
                await asyncio.sleep(sim._charge_tick_sec * 4)

                self.assertEqual(sim._battery, 100.0)
                # Full battery still reports as docked & charging.
                self.assertTrue(sim._charging)
                self.assertEqual(sim._status, sim.config.jibot_status.charging)
            finally:
                await sim.disconnect()

        asyncio.run(scenario())

    def test_driving_cancels_charging(self) -> None:
        async def scenario() -> None:
            sim = self._make_sim()
            await sim.connect_socket()
            sim._battery = 50.0
            try:
                await sim._apply_command({"#CMD#": "UmDock"})
                self.assertTrue(sim._charging)

                # Any motion command leaves the charger: charging stops.
                await sim.goto_xyz(1000.0, 0.0, 0.0)
                self.assertFalse(sim._charging)

                battery_after_drive = sim._battery
                await asyncio.sleep(sim._charge_tick_sec * 4)
                self.assertEqual(sim._battery, battery_after_drive)
            finally:
                await sim.disconnect()

        asyncio.run(scenario())

    def test_stop_cancels_charging(self) -> None:
        async def scenario() -> None:
            sim = self._make_sim()
            await sim.connect_socket()
            sim._battery = 50.0
            try:
                await sim._apply_command({"#CMD#": "UmDock"})
                self.assertTrue(sim._charging)

                await sim._apply_command({"#CMD#": "UmStop"})
                self.assertFalse(sim._charging)
                self.assertEqual(sim._status, sim.config.jibot_status.stop)
            finally:
                await sim.disconnect()

        asyncio.run(scenario())


class SimulatorInitialBatteryTest(unittest.TestCase):
    def test_initial_battery_sets_starting_charge(self) -> None:
        sim = SimulatedJIBOT(config=Adapter().config, initial_battery=42.0)
        self.assertEqual(sim._battery, 42.0)

    def test_initial_battery_clamps_above_full(self) -> None:
        sim = SimulatedJIBOT(config=Adapter().config, initial_battery=150.0)
        self.assertEqual(sim._battery, 100.0)

    def test_initial_battery_clamps_below_empty(self) -> None:
        sim = SimulatedJIBOT(config=Adapter().config, initial_battery=-10.0)
        self.assertEqual(sim._battery, 0.0)

    def test_invalid_initial_battery_falls_back_to_random(self) -> None:
        sim = SimulatedJIBOT(config=Adapter().config, initial_battery="not-a-number")
        self.assertGreaterEqual(sim._battery, 30.0)
        self.assertLessEqual(sim._battery, 100.0)

    def test_no_initial_battery_uses_random_default(self) -> None:
        sim = SimulatedJIBOT(config=Adapter().config)
        self.assertGreaterEqual(sim._battery, 30.0)
        self.assertLessEqual(sim._battery, 100.0)


class SimulatorInitialChargingTest(unittest.TestCase):
    def test_initial_charging_starts_docked_and_climbs(self) -> None:
        async def scenario() -> None:
            sim = SimulatedJIBOT(
                config=Adapter().config,
                initial_charging=True,
                initial_battery=50.0,
            )
            sim._charge_tick_sec = 0.02
            await sim.connect_socket()
            await sim.connect()
            try:
                self.assertTrue(sim._charging)
                self.assertEqual(sim._status, sim.config.jibot_status.charging)

                # The charger sim runs on connect, so the battery climbs.
                await asyncio.sleep(sim._charge_tick_sec * 4 + sim._charge_tick_sec / 2)
                self.assertGreaterEqual(sim._battery, 53.0)
                self.assertLessEqual(sim._battery, 56.0)
            finally:
                await sim.disconnect()

        asyncio.run(scenario())

    def test_no_initial_charging_starts_stopped(self) -> None:
        async def scenario() -> None:
            sim = SimulatedJIBOT(config=Adapter().config)
            await sim.connect_socket()
            await sim.connect()
            try:
                self.assertFalse(sim._charging)
                self.assertEqual(sim._status, sim.config.jibot_status.stop)
            finally:
                await sim.disconnect()

        asyncio.run(scenario())


class OrderStartChargingIntegrationTest(unittest.TestCase):
    def test_order_action_charges_the_simulator(self) -> None:
        """End-to-end: an order startCharging action makes the sim battery climb."""

        async def scenario() -> None:
            adapter = Adapter()
            vehicle = SimulatedJIBOT(config=adapter.config)
            vehicle._charge_tick_sec = 0.02
            vehicle._battery = 50.0
            adapter.set_vehicle(vehicle)
            adapter._loop = asyncio.get_running_loop()
            await vehicle.connect_socket()
            try:
                order = Order.from_dict(
                    {
                        "headerId": 1,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "orderId": "order-charge",
                        "orderUpdateId": 0,
                        "nodes": [
                            {
                                "nodeId": "F2_90_S2CH",
                                "sequenceId": 0,
                                "released": True,
                                "actions": [
                                    {
                                        "actionType": "startCharging",
                                        "actionId": "F2_90_S2CH-order-0-startCharging",
                                        "blockingType": "SOFT",
                                        "actionParameters": [],
                                    }
                                ],
                            }
                        ],
                        "edges": [],
                    }
                )
                action = order.nodes[0].actions[0]
                action_state = ActionState(
                    action_id=action.action_id,
                    action_status=ActionStatus.WAITING,
                    action_type="startCharging",
                )

                await adapter._execute_order_action(action, action_state)

                self.assertEqual(action_state.action_status, ActionStatus.FINISHED)
                self.assertTrue(vehicle._charging)
                self.assertEqual(vehicle._status, vehicle.config.jibot_status.charging)

                await asyncio.sleep(vehicle._charge_tick_sec * 3 + vehicle._charge_tick_sec / 2)
                self.assertGreaterEqual(vehicle._battery, 52.0)
            finally:
                await vehicle.disconnect()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
