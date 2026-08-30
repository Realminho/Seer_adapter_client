from __future__ import annotations

import sys
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


SEER_CLIENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SEER_CLIENT_ROOT.parent
for source in (
    SEER_CLIENT_ROOT / "src",
    REPO_ROOT / "adaptor",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.webui import DropInPaths, SeerWebUiApplication  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.webui import SeerProcessController  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.recorder import SeerRecorder  # pyright: ignore[reportMissingImports]  # noqa: E402


class _FakeServerThread:
    def __init__(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


class _FakeWeb:
    def __init__(self) -> None:
        self._thread = _FakeServerThread()


class _TerminatedProcess:
    pid = 12345

    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 1

    def wait(self, timeout=None):
        del timeout
        return self.returncode


class WebUiShutdownTests(unittest.TestCase):
    def test_default_mutable_paths_stay_inside_seer_client(self) -> None:
        paths = DropInPaths.defaults()
        robot_paths = DropInPaths.for_robot("SEER-DEFAULT-PATH")
        expected_runtime = SEER_CLIENT_ROOT / "runtime"
        self.assertEqual(paths.runtime_dir, expected_runtime)
        self.assertEqual(paths.ipc_root, expected_runtime / "ipc")
        self.assertEqual(robot_paths.runtime_dir, expected_runtime / "SEER-DEFAULT-PATH")
        self.assertEqual(robot_paths.ipc_root, expected_runtime / "ipc")
        with mock.patch.dict(
            os.environ,
            {"SEER_RECORD_DIR": "", "SEER_RECORD_FILE": ""},
            clear=False,
        ):
            os.environ.pop("SEER_RECORD_DIR", None)
            os.environ.pop("SEER_RECORD_FILE", None)
            record_path = SeerRecorder._default_file_path()
        self.assertEqual(record_path.parent, expected_runtime / "records")

    def test_adapter_subprocess_disables_external_bytecode_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = SeerProcessController(
                lambda: (sys.executable, "unused.py"),
                workdir=root,
                log_path=root / "runtime" / "seer.log",
                ipc_root=root / "runtime" / "ipc",
                control_host="127.0.0.1",
                control_port=12345,
            )
            process = _TerminatedProcess()
            with mock.patch(
                "seer_client.webui.subprocess.Popen", return_value=process
            ) as popen:
                ok, _ = controller.start()
                self.assertTrue(ok)
                self.assertEqual(
                    popen.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"],
                    "1",
                )
                controller.stop()

    def test_operator_stop_is_inactive_even_when_windows_exit_is_nonzero(self) -> None:
        controller = SeerProcessController(
            lambda: (),
            workdir=Path.cwd(),
            log_path=Path.cwd() / "unused.log",
            ipc_root=Path.cwd(),
            control_host="127.0.0.1",
            control_port=12345,
        )
        controller._process = _TerminatedProcess()
        ok, message = controller.stop()
        metrics = controller.poll()
        self.assertTrue(ok)
        self.assertIn("exit=1", message)
        self.assertEqual(metrics.active_state, "inactive")
        self.assertEqual(metrics.sub_state, "dead")

    def test_sigint_requests_graceful_shutdown(self) -> None:
        stop_requested = threading.Event()
        installed = {}

        def remember(signum, handler):
            installed[signum] = handler

        with mock.patch("signal.getsignal", return_value="previous"), mock.patch(
            "signal.signal", side_effect=remember
        ):
            restore = SeerWebUiApplication._install_shutdown_handlers(
                stop_requested
            )
            installed[signal.SIGINT](signal.SIGINT, None)
            self.assertTrue(stop_requested.is_set())
            restore()

    def test_wait_for_stop_observes_event_without_unbounded_join(self) -> None:
        web = _FakeWeb()
        stop_requested = threading.Event()
        timer = threading.Timer(0.05, stop_requested.set)
        timer.start()
        started = time.monotonic()
        try:
            SeerWebUiApplication._wait_for_stop(web, stop_requested)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 1.0)

    def test_vehicle_and_internal_control_ports_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = DropInPaths(
                runtime_dir=root / "runtime",
                config_path=root / "runtime" / "config.toml",
                robots_path=root / "runtime" / "robots.toml",
                log_path=root / "runtime" / "seer.log",
                ipc_root=root / "ipc",
            )
            with mock.patch(
                "seer_client.dropin_control.reserve_local_port", return_value=54321
            ):
                app = SeerWebUiApplication(
                    serial="SEER-TEST",
                    simulator=True,
                    vehicle_ip="",
                    username="seer",
                    password="safe-test-value-123",
                    control_port=19205,
                    paths=paths,
                )
                self.assertEqual(app.controller._workdir, paths.runtime_dir)

        command = list(app.adapter_command())
        port_index = command.index("--seer-control-port") + 1
        self.assertEqual(app.control_port, 19205)
        self.assertEqual(app.control_ipc_port, 54321)
        self.assertEqual(app.controller._control_port, 54321)
        self.assertEqual(command[port_index], "19205")


if __name__ == "__main__":
    unittest.main()
