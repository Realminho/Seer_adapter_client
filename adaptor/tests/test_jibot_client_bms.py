import sys
import unittest
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[2] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from jibot_client import JIBOT


class BmsCacheTest(unittest.TestCase):
    def test_set_bms_caches_values_and_timestamp(self):
        v = JIBOT("127.0.0.1", 7273)
        self.assertIsNone(v._battery_voltage)
        self.assertIsNone(v._battery_current)
        v.set_bms(54.6, 7.2)
        self.assertEqual(v._battery_voltage, 54.6)
        self.assertEqual(v._battery_current, 7.2)
        self.assertGreater(v._bms_last_update, 0.0)

    def test_clear_bms_resets_to_none(self):
        v = JIBOT("127.0.0.1", 7273)
        v.set_bms(54.6, 7.2)
        v.clear_bms()
        self.assertIsNone(v._battery_voltage)
        self.assertIsNone(v._battery_current)

    def test_charging_disable_status_sets_charging_cache(self):
        v = JIBOT("127.0.0.1", 7273)
        payload = '{"#CMD#":"UmGetRobotInfo","status":"charging#disable"}'

        v.process_data(f"$#{len(payload)}##{payload}$~")

        self.assertEqual(v._status, "charging#disable")
        self.assertTrue(v._charging)


if __name__ == "__main__":
    unittest.main()
