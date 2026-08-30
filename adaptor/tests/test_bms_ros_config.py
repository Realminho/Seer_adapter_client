import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from config.config import BmsRosConfig, get_config


class BmsRosConfigTest(unittest.TestCase):
    def test_defaults(self):
        c = BmsRosConfig()
        self.assertTrue(c.enabled)
        self.assertEqual(c.topic, "/jrobot_status")
        self.assertEqual(c.ros_setup, "/usr/local/urobot/jarvis/setup.bash")
        self.assertEqual(c.ros_master_uri, "http://localhost:11311")
        self.assertEqual(c.stale_after_sec, 30.0)
        self.assertEqual(c.restart_backoff_sec, 3.0)

    def test_config_exposes_bms_ros(self):
        cfg = get_config()
        self.assertIsInstance(cfg.bms_ros, BmsRosConfig)


if __name__ == "__main__":
    unittest.main()
