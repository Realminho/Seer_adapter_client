from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUN_ADAPTER = Path(__file__).resolve().parents[1] / "run_adapter.py"
SPEC = importlib.util.spec_from_file_location("seer_run_adapter_test", RUN_ADAPTER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SeerRuntimeOptionsTest(unittest.TestCase):
    def test_seer_flags_become_environment_and_are_removed(self):
        with patch.dict(os.environ, {}, clear=True):
            remaining = MODULE._apply_seer_runtime_options(
                [
                    "--robot", "SEER-1",
                    "--seer-state-port", "29204",
                    "--seer-control-port", "29205",
                    "--seer-task-port", "29206",
                    "--seer-config-port", "29207",
                    "--seer-other-port", "29210",
                    "--seer-motor-names", "left,right",
                    "--seer-protocol-version", "2",
                    "--seer-command-timeout", "4.5",
                    "--seer-min-request-interval", "0.15",
                    "--seer-status-poll-interval", "0.3",
                    "--seer-navigation-timeout", "120",
                    "--vehicle-smoke-test",
                ]
            )
            self.assertEqual(
                remaining,
                ["--robot", "SEER-1", "--vehicle-smoke-test"],
            )
            self.assertEqual(os.environ["SEER_STATE_PORT"], "29204")
            self.assertEqual(os.environ["SEER_CONTROL_PORT"], "29205")
            self.assertEqual(os.environ["SEER_TASK_PORT"], "29206")
            self.assertEqual(os.environ["SEER_CONFIG_PORT"], "29207")
            self.assertEqual(os.environ["SEER_OTHER_PORT"], "29210")
            self.assertEqual(os.environ["SEER_MOTOR_NAMES"], "left,right")
            self.assertEqual(os.environ["SEER_PROTOCOL_VERSION"], "2")
            self.assertEqual(os.environ["SEER_COMMAND_TIMEOUT_SEC"], "4.5")
            self.assertEqual(os.environ["SEER_MIN_REQUEST_INTERVAL_SEC"], "0.15")
            self.assertEqual(os.environ["SEER_STATUS_POLL_INTERVAL_SEC"], "0.3")
            self.assertEqual(os.environ["SEER_NAVIGATION_TIMEOUT_SEC"], "120.0")

    def test_dropin_ipc_root_changes_only_runtime_module_state(self):
        from core import ipc_paths  # pyright: ignore[reportMissingImports]

        original = ipc_paths.RUNTIME_ROOT
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"SEER_RUNTIME_ROOT": temp}, clear=False):
                MODULE._apply_dropin_ipc_root()
            self.assertEqual(ipc_paths.RUNTIME_ROOT, Path(temp))
        ipc_paths.RUNTIME_ROOT = original

    def test_dropin_ipc_defaults_inside_seer_client_runtime(self):
        from core import ipc_paths  # pyright: ignore[reportMissingImports]

        original = ipc_paths.RUNTIME_ROOT
        try:
            with patch.dict(os.environ, {}, clear=True):
                MODULE._apply_dropin_ipc_root()
                expected = RUN_ADAPTER.parent / "runtime" / "ipc"
                self.assertEqual(ipc_paths.RUNTIME_ROOT, expected)
                self.assertEqual(Path(os.environ["SEER_RUNTIME_ROOT"]), expected)
        finally:
            ipc_paths.RUNTIME_ROOT = original


if __name__ == "__main__":
    unittest.main()
