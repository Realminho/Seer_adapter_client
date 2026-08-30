from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("seer_client/src", "adaptor"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from seer_client.dropin_devices import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    SeerNullEziIo,
    SeerNullEziMotor,
    install_dropin_device_modules,
)


class DropInDeviceTest(unittest.IsolatedAsyncioTestCase):
    async def test_null_ezi_devices_require_no_network(self):
        io = SeerNullEziIo("not-used")
        await io.connect()
        await io.turn_on_output(3)
        self.assertEqual((await io.get_output())["outputs"][3], 1)
        self.assertEqual((await io.get_input())["inputs"], [0] * 16)

        motor = SeerNullEziMotor("not-used")
        self.assertEqual((await motor.get_board_info())["status"], 0)
        self.assertEqual((await motor.servo_enable(True))["status"], 0)
        self.assertTrue(motor.servo_on)

    def test_install_exposes_original_import_names(self):
        with patch.dict(sys.modules):
            install_dropin_device_modules()
            self.assertIs(sys.modules["utils.ezi_io"].EZIIOClient, SeerNullEziIo)
            self.assertIs(
                sys.modules["utils.ezi_motor"].EziMotorClient,
                SeerNullEziMotor,
            )


if __name__ == "__main__":
    unittest.main()
