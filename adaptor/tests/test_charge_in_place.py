# adaptor/tests/test_charge_in_place.py
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapter_jibot import Adapter
from config.config import MotionRule
from extensions.charge import release_charge_in_place, run_charge_in_place
from extensions.charge import should_charge_in_place
from utils.charge_circuit import NullChargeCircuit, FakeChargeCircuit


class ChargeCircuitInjectionTest(unittest.TestCase):
    def test_defaults_to_null_circuit(self):
        adapter = Adapter(config=None)
        self.assertIsInstance(adapter._charge_circuit, NullChargeCircuit)

    def test_set_charge_circuit_replaces(self):
        adapter = Adapter(config=None)
        fake = FakeChargeCircuit()
        adapter.set_charge_circuit(fake)
        self.assertIs(adapter._charge_circuit, fake)


import asyncio
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from protocol.vda5050_common import ErrorLevel


class _FakeVehicleCharging:
    def __init__(self, charging):
        self._charging = charging


class _StateWithErrors:
    def __init__(self):
        self.errors = []


def _make_instant(action_id, action_type):
    class A:
        pass
    a = A()
    a.action_id = action_id
    a.action_type = action_type
    a.action_parameters = []
    a.action_descriptor = ""
    return a


class ChargeInPlaceActionTest(unittest.TestCase):
    def _adapter(self, charging):
        adapter = Adapter(config=None)
        adapter.config.charge_circuit.enabled = True
        adapter.config.charge_circuit.verify_timeout_sec = 0.2
        adapter._vehicle = _FakeVehicleCharging(charging)
        adapter._loop = asyncio.get_event_loop()
        self.fake_cc = FakeChargeCircuit()
        adapter.set_charge_circuit(self.fake_cc)
        return adapter

    def test_charge_in_place_finishes_when_charging(self):
        async def scenario():
            adapter = self._adapter(charging=True)
            ok, _desc = await run_charge_in_place(adapter)
            self.assertTrue(ok)
            self.assertEqual(self.fake_cc.start_calls, 1)
            self.assertEqual(self.fake_cc.stop_calls, 0)  # left charging
        asyncio.run(scenario())

    def test_charge_in_place_fails_and_releases_when_not_charging(self):
        async def scenario():
            adapter = self._adapter(charging=False)
            ok, _desc = await run_charge_in_place(adapter)
            self.assertFalse(ok)
            self.assertEqual(self.fake_cc.start_calls, 1)
            self.assertEqual(self.fake_cc.stop_calls, 1)  # released on failure
        asyncio.run(scenario())

    def test_charge_in_place_disabled_fails_immediately_with_critical_log(self):
        async def scenario():
            adapter = self._adapter(charging=False)
            adapter.config.charge_circuit.enabled = False

            out = io.StringIO()
            with redirect_stdout(out):
                ok, desc = await run_charge_in_place(adapter)

            self.assertFalse(ok)
            self.assertIn("charge_circuit.enabled=false", desc)
            self.assertIn("[charge_circuit].enabled=true", desc)
            self.assertIn("adaptor/config/config.toml", desc)
            self.assertEqual(self.fake_cc.start_calls, 0)
            self.assertEqual(self.fake_cc.stop_calls, 0)
            self.assertIn("[CHARGE IN PLACE CRITICAL]", out.getvalue())
            self.assertIn("charge_circuit.enabled=false", out.getvalue())
            self.assertIn("[charge_circuit].enabled=true", out.getvalue())
            self.assertIn("adaptor/config/config.toml", out.getvalue())

        asyncio.run(scenario())

    def test_charge_in_place_disabled_adds_critical_state_error(self):
        async def scenario():
            adapter = self._adapter(charging=False)
            adapter.config.charge_circuit.enabled = False
            adapter.state = _StateWithErrors()

            with redirect_stdout(io.StringIO()):
                ok, _desc = await run_charge_in_place(adapter)

            self.assertFalse(ok)
            self.assertEqual(len(adapter.state.errors), 1)
            error = adapter.state.errors[0]
            # CRITICAL, not FATAL: a disabled in-place charge circuit means the
            # robot cannot perform this charge action (stop driving / can't
            # continue the order), but it is otherwise healthy and can still
            # take new orders. FATAL would wrongly imply user intervention and
            # refusing new orders. VDA5050 errorLevel = WARNING/URGENT/CRITICAL/FATAL.
            self.assertEqual(error.error_level, ErrorLevel.CRITICAL)
            self.assertIn("charge_circuit.enabled=false", error.error_description)
            self.assertIn("[charge_circuit].enabled=true", error.error_description)
            self.assertIn("adaptor/config/config.toml", error.error_description)

        asyncio.run(scenario())


class _FakeVehicleDock:
    def __init__(self, charging=True):
        self._charging = charging
        self.um_dock_calls = 0

    async def um_dock(self, **params):
        self.um_dock_calls += 1


class AutoRouteTest(unittest.TestCase):
    def _adapter(self):
        adapter = Adapter(config=None)
        adapter.config.charge_circuit.enabled = True
        adapter.config.motion_rules = [MotionRule(to="CH1", mode="dock")]  # CH1 is a dock node
        adapter.config.charge_circuit.verify_timeout_sec = 0.2
        self.fake_cc = FakeChargeCircuit()
        adapter.set_charge_circuit(self.fake_cc)
        self.vehicle = _FakeVehicleDock(charging=True)
        adapter._vehicle = self.vehicle
        return adapter

    def test_in_place_when_charge_node_is_current_node(self):
        adapter = self._adapter()
        adapter._last_node_id = "CH1"
        self.assertTrue(should_charge_in_place(adapter, "CH1"))

    def test_um_dock_when_charge_node_differs_from_current(self):
        adapter = self._adapter()
        adapter._last_node_id = "P5"   # robot parked elsewhere
        self.assertFalse(should_charge_in_place(adapter, "CH1"))

    def test_not_in_place_for_non_dock_current_node(self):
        adapter = self._adapter()
        adapter._last_node_id = "P5"
        self.assertFalse(should_charge_in_place(adapter, "P5"))


class ReleaseTest(unittest.TestCase):
    def test_release_stops_active_hold(self):
        adapter = Adapter(config=None)
        adapter.config.charge_circuit.enabled = True
        fake = FakeChargeCircuit()
        adapter.set_charge_circuit(fake)
        fake.start_hold()
        adapter._charge_in_place_active = True

        release_charge_in_place(adapter)

        self.assertFalse(adapter._charge_in_place_active)
        self.assertEqual(fake.stop_calls, 1)

    def test_release_is_noop_when_inactive(self):
        adapter = Adapter(config=None)
        adapter.config.charge_circuit.enabled = True
        fake = FakeChargeCircuit()
        adapter.set_charge_circuit(fake)

        release_charge_in_place(adapter)

        self.assertEqual(fake.stop_calls, 0)


class _FakeVehicleOrderNode:
    """Minimal vehicle fake for order-node in-place charge tests.

    Keeps ``_charging`` False throughout so the charge relay verify step
    times out and ``_run_charge_in_place`` returns ``(False, ...)``.
    """

    def __init__(self, charging=False):
        self._charging = charging
        self._status = "Stopped"
        self._mode = "auto"
        self._x = 0.0
        self._y = 0.0
        self._th = 0.0
        self._battery = 80.0
        self._localization_score = 1.0
        self._motor_flag = 1
        self.is_simulator = False

    def is_connected(self) -> bool:
        return True

    def is_rx_stale(self, timeout: float) -> bool:
        return False

    def seconds_since_last_rx(self) -> float:
        return 0.0


class OrderNodeChargeInPlaceTest(unittest.TestCase):
    """The order-node in-place-charge failure path must NOT hang.

    When ``_run_charge_in_place`` returns ``(False, ...)`` (robot never drew
    current within the verify window), ``_send_node_motion`` must surface the
    failure so ``_process_v3_node_step`` exits with ``False`` — it must NOT
    return ``None`` and let the caller enter the infinite
    ``_wait_until_docking_complete`` loop.
    """

    def _make_adapter(self, charging=False):
        """Return an adapter wired for an in-place charge scenario.

        ``CH1`` is both a charge node and the current last-node, so
        ``_should_charge_in_place("CH1")`` returns True.
        A very short ``verify_timeout_sec`` ensures the test finishes quickly
        even when the bug is present (the loop times out, not the test suite).
        """
        from protocol.vda5050_3_0.messages import Node as VDANode

        adapter = Adapter(config=None)
        adapter.config.charge_circuit.enabled = True
        adapter.config.motion_rules = [MotionRule(to="CH1", mode="dock")]
        adapter.config.charge_circuit.verify_timeout_sec = 0.1

        fake_cc = FakeChargeCircuit()
        adapter.set_charge_circuit(fake_cc)

        vehicle = _FakeVehicleOrderNode(charging=charging)
        adapter._vehicle = vehicle
        adapter._last_node_id = "CH1"  # robot already at CH1 → in-place

        self._fake_cc = fake_cc
        self._vehicle = vehicle
        return adapter

    def _make_ch1_node(self):
        from protocol.vda5050_3_0.messages import Node as VDANode
        from adapter_jibot import OrderStep

        node = VDANode.from_dict(
            {
                "nodeId": "CH1",
                "sequenceId": 2,
                "released": True,
                "actions": [],
            }
        )
        return node, OrderStep("node", node.sequence_id, node)

    # ------------------------------------------------------------------
    # Test 1: _send_node_motion returns a non-None rejection-reason string
    #         when in-place charging fails (robot never charges).
    # ------------------------------------------------------------------
    def test_send_node_motion_returns_rejection_reason_on_in_place_failure(self):
        async def scenario():
            adapter = self._make_adapter(charging=False)
            node, _step = self._make_ch1_node()

            result = await adapter._send_node_motion(node)

            # Must NOT be None — None is the "success / proceed to wait" signal.
            self.assertIsNotNone(
                result,
                "_send_node_motion must return a rejection-reason string on "
                "in-place charge failure, not None",
            )
            self.assertIsInstance(result, str)

        asyncio.run(scenario())

    # ------------------------------------------------------------------
    # Test 2: _process_v3_node_step returns False (not hang) on failure.
    #
    # We run _process_v3_node_step with a short asyncio.wait_for timeout
    # that would fire if the code entered _wait_until_docking_complete.
    # With the bug the task never completes; with the fix it returns False
    # quickly (within the verify window, which is 0.1 s).
    # ------------------------------------------------------------------
    def test_process_v3_node_step_returns_false_on_in_place_charge_failure(self):
        async def scenario():
            adapter = self._make_adapter(charging=False)
            adapter._loop = asyncio.get_running_loop()

            # Start state publisher (required for error-setting helpers).
            state_task = asyncio.create_task(
                adapter.publish_state("state", interval_sec=0.05)
            )
            for _ in range(100):
                if adapter.state is not None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(adapter.state)

            _node, step = self._make_ch1_node()
            try:
                # Allow 2 s — far longer than the 0.1 s verify window.
                # If the code enters the infinite docking wait, the timeout
                # fires and the test fails with TimeoutError (not a hang).
                result = await asyncio.wait_for(
                    adapter._process_v3_node_step(step),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                self.fail(
                    "_process_v3_node_step timed out — the in-place charge "
                    "failure path entered the infinite docking-complete wait "
                    "(the hang bug is still present)"
                )
            finally:
                state_task.cancel()

            self.assertFalse(
                result,
                "_process_v3_node_step must return False when in-place "
                "charging fails",
            )

        asyncio.run(scenario())

    # ------------------------------------------------------------------
    # Test 3: success path still works — _send_node_motion returns None
    #         and node_id is tracked in _docking_started_node_ids.
    # ------------------------------------------------------------------
    def test_send_node_motion_returns_none_on_in_place_success(self):
        async def scenario():
            adapter = self._make_adapter(charging=True)  # charging from the start
            node, _step = self._make_ch1_node()

            result = await adapter._send_node_motion(node)

            self.assertIsNone(result, "success path must still return None")
            self.assertIn(
                "CH1",
                adapter._docking_started_node_ids,
                "success path must add node_id to _docking_started_node_ids",
            )

        asyncio.run(scenario())


class OrderClearReleasesHoldTest(unittest.TestCase):
    """m1: _clear_order_queue must release an active in-place charge hold."""

    def test_clear_order_queue_releases_active_hold(self):
        adapter = Adapter(config=None)
        adapter.config.charge_circuit.enabled = True
        fake = FakeChargeCircuit()
        adapter.set_charge_circuit(fake)
        fake.start_hold()
        adapter._charge_in_place_active = True

        adapter._clear_order_queue()

        self.assertEqual(fake.stop_calls, 1, "stop_hold must be called once")
        self.assertFalse(
            adapter._charge_in_place_active,
            "_charge_in_place_active must be cleared",
        )


if __name__ == "__main__":
    unittest.main()
