from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SEER_CLIENT_ROOT = Path(__file__).resolve().parents[1]
if str(SEER_CLIENT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(SEER_CLIENT_ROOT / "src"))

from seer_client.fleet import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    SeerFleetError,
    SeerRobotConfig,
    load_seer_fleet,
    validate_seer_fleet,
)


class SeerFleetConfigTests(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fleet.toml"
            path.write_text(text, encoding="utf-8")
            return load_seer_fleet(path)

    def test_loads_mixed_simulator_and_real_fleet(self) -> None:
        robots = self._load(
            """
[[robot]]
id = "SEER-SIM-001"
simulator = true
x = 1000
battery = 65
motor_names = ["left", "right"]

[[robot]]
id = "SEER-REAL-001"
vehicle_ip = "192.168.192.5"
control_port = 20205
auto_start = false
"""
        )
        self.assertEqual([item.serial for item in robots], ["SEER-SIM-001", "SEER-REAL-001"])
        self.assertTrue(robots[0].simulator)
        self.assertEqual(robots[0].motor_names, "left,right")
        self.assertEqual(robots[1].vehicle_ip, "192.168.192.5")
        self.assertEqual(robots[1].control_port, 20205)
        self.assertFalse(robots[1].auto_start)

    def test_rejects_duplicate_ids(self) -> None:
        with self.assertRaisesRegex(SeerFleetError, "duplicate robot id"):
            self._load(
                """
[[robot]]
id = "SEER-001"
simulator = true
[[robot]]
id = "SEER-001"
simulator = true
"""
            )

    def test_rejects_real_robot_without_ip(self) -> None:
        with self.assertRaisesRegex(SeerFleetError, "vehicle_ip is required"):
            self._load('[[robot]]\nid="SEER-REAL"\nsimulator=false\n')

    def test_rejects_unsafe_id_and_unknown_key(self) -> None:
        with self.assertRaisesRegex(SeerFleetError, "may contain only"):
            self._load('[[robot]]\nid="SEER / 1"\nsimulator=true\n')
        with self.assertRaisesRegex(SeerFleetError, "unknown keys"):
            self._load('[[robot]]\nid="SEER-1"\nsimulator=true\nstat_port=1\n')

    def test_programmatic_validation_rejects_duplicate_ids(self) -> None:
        robot = SeerRobotConfig(serial="SEER-1", simulator=True)
        with self.assertRaisesRegex(SeerFleetError, "duplicate robot id"):
            validate_seer_fleet((robot, robot))


if __name__ == "__main__":
    unittest.main()
