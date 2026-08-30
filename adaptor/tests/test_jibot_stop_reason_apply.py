import asyncio
import time
import unittest

from protocol.vda_2_0_0.vda5050_2_0_0_state import EStop, ErrorType, OperatingMode
from tests._stop_reason_harness import make_adapter, start_state


def _run_with_safety(safety, motor_flag=0, status=None):
    """Start the publish loop with the given /jrobot_status safety dict cached,
    let one cycle apply it, and return the live state."""
    async def scenario():
        adapter = make_adapter()
        v = adapter._vehicle
        v._motor_flag = motor_flag
        v._robot_safety = safety
        v._robot_safety_last_update = time.monotonic()
        if status is not None:
            v._status = status
        task = await start_state(adapter)
        try:
            await asyncio.sleep(0.1)
            return {
                "e_stop": adapter.state.safety_state.e_stop,
                "field_violation": adapter.state.safety_state.field_violation,
                "operating_mode": adapter.state.operating_mode,
                "error_types": [e.error_type for e in adapter.state.errors],
                "driving": adapter.state.driving,
            }
        finally:
            task.cancel()
    return asyncio.run(scenario())


class StopReasonApplyTest(unittest.TestCase):
    def test_manual_no_estop_and_operating_mode_manual(self):
        s = _run_with_safety({"system_status": "Press ON to Enable.", "motor_enable": "0", "hmi_estop": "0"})
        self.assertEqual(s["e_stop"], EStop.NONE)
        self.assertEqual(s["operating_mode"], OperatingMode.MANUAL)

    def test_emergency_sets_estop_manual(self):
        s = _run_with_safety({"system_status": "Estop Pressed!", "hmi_estop": "1"})
        self.assertEqual(s["e_stop"], EStop.MANUAL)

    def test_protective_stop_sets_estop_remote(self):
        s = _run_with_safety({"system_status": "Enter Estop!", "pc_estop": "1"})
        self.assertEqual(s["e_stop"], EStop.REMOTE)

    def test_bumper_forces_field_violation_and_not_driving(self):
        # status="Driving" makes _derive_driving() return True, so this assertion
        # proves the bumper override forces driving=False even when otherwise driving.
        s = _run_with_safety({"system_status": "bumper Trigger!"}, status="Driving")
        self.assertTrue(s["field_violation"])
        self.assertFalse(s["driving"])

    def test_motor_fault_adds_fatal_error(self):
        s = _run_with_safety({"system_status": "Normal...", "motor_error": "1"})
        self.assertIn(ErrorType.JIBOT_MOTOR_FAULT, s["error_types"])

    def test_no_motor_fault_when_clear(self):
        s = _run_with_safety({"system_status": "Normal...", "motor_error": "0", "motor_enable": "1"})
        self.assertNotIn(ErrorType.JIBOT_MOTOR_FAULT, s["error_types"])

    def test_stale_falls_back_to_legacy_autoack(self):
        s = _run_with_safety({}, motor_flag=0)  # no fresh safety → legacy motor_flag path
        self.assertEqual(s["e_stop"], EStop.AUTOACK)


if __name__ == "__main__":
    unittest.main()
