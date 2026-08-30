import time
import unittest

from tests._stop_reason_harness import make_adapter


class StopReasonTest(unittest.TestCase):
    def _adapter_with_safety(self, safety, age_sec=0.0):
        adapter = make_adapter()
        v = adapter._vehicle
        v._robot_safety = safety
        v._robot_safety_last_update = time.monotonic() - age_sec
        return adapter

    def test_press_on_to_enable_is_manual(self):
        a = self._adapter_with_safety(
            {"system_status": "Press ON to Enable.", "motor_enable": "0", "hmi_estop": "0"}
        )
        self.assertEqual(a._derive_jibot_stop_reason(), "MANUAL")

    def test_estop_pressed_is_emergency(self):
        a = self._adapter_with_safety(
            {"system_status": "Estop Pressed!", "hmi_estop": "1", "motor_enable": "0"}
        )
        self.assertEqual(a._derive_jibot_stop_reason(), "EMERGENCY")

    def test_pc_estop_is_protective_stop(self):
        a = self._adapter_with_safety({"system_status": "Enter Estop!", "pc_estop": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "PROTECTIVE_STOP")

    def test_motor_error_is_motor_fault(self):
        a = self._adapter_with_safety({"system_status": "Normal...", "motor_error": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "MOTOR_FAULT")

    def test_bumper_text_is_bumper(self):
        # BUMPER is classified from the system_status TEXT, and the bumpe_stop flag
        # in the fixture is intentionally NOT what drives the classification (its polarity
        # is untrusted by design).
        a = self._adapter_with_safety({"system_status": "bumper Trigger!", "bumpe_stop": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "BUMPER")

    def test_bumper_flag_alone_does_not_classify_bumper(self):
        # bumpe_stop polarity is untrusted: a raised flag with a Normal status
        # must NOT classify as BUMPER (classification is system_status text-driven).
        a = self._adapter_with_safety(
            {"system_status": "Normal...", "motor_enable": "1", "bumpe_stop": "1"}
        )
        self.assertEqual(a._derive_jibot_stop_reason(), "NONE")

    def test_normal_is_none(self):
        a = self._adapter_with_safety({"system_status": "Normal...", "motor_enable": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "NONE")

    def test_emergency_wins_over_motor_error(self):
        a = self._adapter_with_safety({"system_status": "Estop Pressed!", "hmi_estop": "1", "motor_error": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "EMERGENCY")

    def test_stale_returns_none(self):
        a = self._adapter_with_safety(
            {"system_status": "Press ON to Enable.", "motor_enable": "0"}, age_sec=999.0
        )
        self.assertIsNone(a._derive_jibot_stop_reason())

    def test_absent_returns_none(self):
        adapter = make_adapter()
        adapter._vehicle._robot_safety = {}
        adapter._vehicle._robot_safety_last_update = 0.0
        self.assertIsNone(adapter._derive_jibot_stop_reason())


if __name__ == "__main__":
    unittest.main()
