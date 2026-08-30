from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from seer_client.diagnostics import (  # pyright: ignore[reportMissingImports]
    SeerConfigurationError,
    validate_runtime_settings,
)
from seer_client.drive_limits import ManualDriveLimits  # pyright: ignore[reportMissingImports]
from seer_client.protocol import ApiPort  # pyright: ignore[reportMissingImports]


PORTS = {
    ApiPort.STATE: 19204,
    ApiPort.CONTROL: 19205,
    ApiPort.TASK: 19206,
    ApiPort.CONFIG: 19207,
    ApiPort.OTHER: 19210,
}


class Week1DiagnosticsTests(unittest.TestCase):
    def _validate(self, **overrides):
        values = dict(
            port_map=PORTS,
            protocol_version=1,
            command_timeout=3.0,
            recv_chunk_bytes=4096,
            status_poll_interval_sec=0.2,
            min_request_interval_sec=0.1,
            navigation_timeout_sec=300.0,
            motor_names=("left", "right"),
            manual_drive_limits=ManualDriveLimits(),
        )
        values.update(overrides)
        return validate_runtime_settings(**values)

    def test_valid_configuration_has_no_warning(self):
        self.assertEqual(self._validate().warnings, ())

    def test_duplicate_ports_fail_before_connection(self):
        ports = dict(PORTS)
        ports[ApiPort.TASK] = ports[ApiPort.CONTROL]
        with self.assertRaisesRegex(SeerConfigurationError, "must be distinct"):
            self._validate(port_map=ports)

    def test_invalid_protocol_and_duplicate_motor_names_fail(self):
        with self.assertRaisesRegex(SeerConfigurationError, "PROTOCOL_VERSION"):
            self._validate(protocol_version=3)
        with self.assertRaisesRegex(SeerConfigurationError, "duplicates"):
            self._validate(motor_names=("motor", "motor"))
        with self.assertRaisesRegex(SeerConfigurationError, "NAVIGATION_TIMEOUT"):
            self._validate(navigation_timeout_sec=0)

    def test_polling_faster_than_vendor_spacing_is_visible_warning(self):
        result = self._validate(
            status_poll_interval_sec=0.05,
            min_request_interval_sec=0.1,
            navigation_timeout_sec=300.0,
        )
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("rate-limited", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
