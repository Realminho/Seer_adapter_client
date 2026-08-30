import sys
import time
import unittest
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[2] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from jibot_client import JIBOT


class RobotSafetyCacheTest(unittest.TestCase):
    def _vehicle(self):
        return JIBOT(robot_ip="127.0.0.1", robot_port=7273)

    def test_defaults_empty(self):
        v = self._vehicle()
        self.assertEqual(v._robot_safety, {})
        self.assertEqual(v._robot_safety_last_update, 0.0)

    def test_set_caches_copy_and_stamps_time(self):
        v = self._vehicle()
        src = {"system_status": "Press ON to Enable.", "motor_enable": "0"}
        before = time.monotonic()
        v.set_robot_safety(src)
        self.assertEqual(v._robot_safety["system_status"], "Press ON to Enable.")
        self.assertGreaterEqual(v._robot_safety_last_update, before)
        src["motor_enable"] = "1"  # mutating source must not affect cache
        self.assertEqual(v._robot_safety["motor_enable"], "0")

    def test_clear_empties_dict(self):
        v = self._vehicle()
        v.set_robot_safety({"hmi_estop": "1"})
        v.clear_robot_safety()
        self.assertEqual(v._robot_safety, {})


if __name__ == "__main__":
    unittest.main()
