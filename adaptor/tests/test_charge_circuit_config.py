import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import get_config


class ChargeCircuitConfigTest(unittest.TestCase):
    def test_defaults_present_and_enabled(self):
        cfg = get_config().charge_circuit
        # Enabled by default: JIBOT needs the /jcmd charge relay for in-place
        # (chargeInPlace) recharge while already docked.
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.jcmd_topic, "/jcmd")
        self.assertEqual(cfg.publish_rate_hz, 2.0)
        self.assertEqual(cfg.ros_master_uri, "http://localhost:11311")
        self.assertEqual(cfg.verify_timeout_sec, 5.0)
        self.assertEqual(cfg.ros_setup, "/usr/local/urobot/jarvis/setup.bash")


if __name__ == "__main__":
    unittest.main()
