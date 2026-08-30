"""UmDock accepts optional dock parameters.

urobot's UmDock handler (JModeCharge::Start) reads JSON params such as
``detect_charging_signal`` (charge in place without the reflector back-up
maneuver), ``goal`` (charger name / "auto"), and several distance/clearance
tuning values. The client previously hard-rejected every UmDock param
(``COMMAND_SPECS["UmDock"] == ()``), so the adapter could only ever send a
bare UmDock that always runs the full dock approach. These tests pin the
optional params through, while still rejecting truly unknown keys.
"""

import asyncio
import sys
import unittest
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[2] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from jibot_client import JIBOT


class UmDockParamTest(unittest.TestCase):
    def _make_client(self) -> JIBOT:
        return JIBOT("127.0.0.1", 7273)

    def test_build_command_allows_detect_charging_signal(self) -> None:
        client = self._make_client()
        cmd = client.build_command("UmDock", detect_charging_signal=True)
        self.assertEqual(cmd["#CMD#"], "UmDock")
        self.assertEqual(cmd["detect_charging_signal"], True)

    def test_build_command_allows_goal_and_distance_params(self) -> None:
        client = self._make_client()
        cmd = client.build_command(
            "UmDock",
            goal="auto",
            dock_move_additional_dist=0.0,
            dock_rotate_additional_angle=0.0,
        )
        self.assertEqual(cmd["goal"], "auto")
        self.assertIn("dock_move_additional_dist", cmd)
        self.assertIn("dock_rotate_additional_angle", cmd)

    def test_build_command_still_rejects_unknown_param(self) -> None:
        client = self._make_client()
        with self.assertRaises(ValueError):
            client.build_command("UmDock", bogus_param=1)

    def test_um_dock_forwards_params_to_command(self) -> None:
        client = self._make_client()
        captured = {}

        async def fake_send_command(command, gap=-1, **params):
            captured["command"] = command
            captured["gap"] = gap
            captured["params"] = params

        client.send_command = fake_send_command

        asyncio.run(client.um_dock(detect_charging_signal=True))

        self.assertEqual(captured["command"], "UmDock")
        self.assertEqual(captured["params"].get("detect_charging_signal"), True)

    def test_um_dock_no_params_still_works(self) -> None:
        client = self._make_client()
        captured = {}

        async def fake_send_command(command, gap=-1, **params):
            captured["command"] = command
            captured["params"] = params

        client.send_command = fake_send_command

        asyncio.run(client.um_dock())

        self.assertEqual(captured["command"], "UmDock")
        self.assertEqual(captured["params"], {})


if __name__ == "__main__":
    unittest.main()
