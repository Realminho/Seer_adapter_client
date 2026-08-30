"""Tests for persistent WebUI username/password configuration."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src", "seer_client"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from run_webui import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    _is_local_mqtt_host,
    _mosquitto_autostart_log_path,
    ensure_local_mqtt_broker,
    parse_args,
)


class WebUiCredentialsFileTests(unittest.TestCase):
    def write_credentials(self, directory: str) -> Path:
        path = Path(directory) / "credentials.toml"
        path.write_text(
            '[webui]\nusername = "file-user"\npassword = "safe-file-secret-123"\n',
            encoding="utf-8",
        )
        return path

    def test_credentials_file_supplies_username_and_password(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = self.write_credentials(directory)
            with patch.dict(
                os.environ,
                {"SEER_WEBUI_USERNAME": "", "SEER_WEBUI_PASSWORD": ""},
                clear=False,
            ):
                args = parse_args(
                    ["--simulator", "--credentials-file", str(credentials)]
                )

        self.assertEqual(args.username, "file-user")
        self.assertEqual(args.password, "safe-file-secret-123")

    def test_credentials_file_overrides_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = self.write_credentials(directory)
            with patch.dict(
                os.environ,
                {
                    "SEER_WEBUI_USERNAME": "environment-user",
                    "SEER_WEBUI_PASSWORD": "safe-environment-secret-123",
                },
                clear=False,
            ):
                args = parse_args(
                    ["--simulator", "--credentials-file", str(credentials)]
                )

        self.assertEqual(args.username, "file-user")
        self.assertEqual(args.password, "safe-file-secret-123")

    def test_webui_vda_transport_defaults_to_mqtt_and_can_switch_to_local(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = self.write_credentials(directory)
            with patch.dict(os.environ, {"SEER_WEBUI_VDA_TRANSPORT": ""}, clear=False):
                mqtt_args = parse_args(["--simulator", "--credentials-file", str(credentials)])
            local_args = parse_args(
                [
                    "--simulator",
                    "--credentials-file",
                    str(credentials),
                    "--webui-vda-transport",
                    "local",
                ]
            )
        self.assertEqual(mqtt_args.webui_vda_transport, "mqtt")
        self.assertEqual(local_args.webui_vda_transport, "local")

    def test_command_line_overrides_environment_and_credentials_file(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = self.write_credentials(directory)
            with patch.dict(
                os.environ,
                {
                    "SEER_WEBUI_USERNAME": "environment-user",
                    "SEER_WEBUI_PASSWORD": "safe-environment-secret-123",
                },
                clear=False,
            ):
                args = parse_args(
                    [
                        "--simulator",
                        "--credentials-file",
                        str(credentials),
                        "--username",
                        "cli-user",
                        "--password",
                        "safe-cli-secret-123",
                    ]
                )

        self.assertEqual(args.username, "cli-user")
        self.assertEqual(args.password, "safe-cli-secret-123")


class LocalMqttAutoStartTests(unittest.TestCase):
    def test_local_host_detection_is_conservative(self):
        self.assertTrue(_is_local_mqtt_host("127.0.0.1"))
        self.assertTrue(_is_local_mqtt_host("localhost"))
        self.assertTrue(_is_local_mqtt_host("::1"))
        self.assertFalse(_is_local_mqtt_host("192.168.1.20"))
        self.assertFalse(_is_local_mqtt_host(None))

    @patch("run_webui._tcp_reachable", return_value=True)
    @patch("run_webui._find_mosquitto_executable")
    def test_online_local_broker_is_reused(self, find_executable, _reachable):
        started = ensure_local_mqtt_broker("127.0.0.1", 1883)
        self.assertFalse(started)
        find_executable.assert_not_called()

    def test_mosquitto_autostart_log_can_live_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            wanted = Path(directory) / "mqtt" / "broker.log"
            with patch.dict(
                os.environ,
                {"SEER_MOSQUITTO_LOG_PATH": str(wanted)},
                clear=False,
            ):
                actual = _mosquitto_autostart_log_path()
        self.assertEqual(actual, wanted.resolve())
        self.assertFalse(str(actual).startswith(str(REPO_ROOT / "seer_client" / "runtime")))

    def test_windows_default_mosquitto_log_uses_localappdata(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": directory,
                    "SEER_MOSQUITTO_LOG_PATH": "",
                },
                clear=False,
            ):
                actual = _mosquitto_autostart_log_path("nt")
        self.assertEqual(
            actual,
            Path(directory) / "SEER Client" / "logs" / "mosquitto-autostart.log",
        )

    @patch("run_webui._tcp_reachable")
    @patch("run_webui._find_mosquitto_config")
    @patch("run_webui._find_mosquitto_executable")
    @patch("run_webui.subprocess.Popen")
    def test_offline_local_broker_is_started_and_left_detached(
        self, popen, find_executable, find_config, reachable
    ):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mosquitto.exe"
            executable.write_bytes(b"")
            config = Path(directory) / "mosquitto.conf"
            config.write_text("listener 1883\n", encoding="utf-8")
            find_executable.return_value = executable
            find_config.return_value = config
            reachable.side_effect = [False, True]
            popen.return_value = MagicMock()

            external_log = Path(directory) / "outside-project" / "mosquitto.log"
            with patch.dict(
                os.environ,
                {"SEER_MOSQUITTO_LOG_PATH": str(external_log)},
                clear=False,
            ):
                started = ensure_local_mqtt_broker("127.0.0.1", 1883)

        self.assertTrue(started)
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], [str(executable), "-c", str(config)])
        self.assertEqual(command[-1], "-v")
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(executable.parent))
        self.assertEqual(Path(popen.call_args.kwargs["stdout"].name), external_log.resolve())

    @patch("run_webui._tcp_reachable")
    def test_remote_broker_is_never_auto_started(self, reachable):
        started = ensure_local_mqtt_broker("192.168.43.50", 1883)
        self.assertFalse(started)
        reachable.assert_not_called()

    def test_auto_start_can_be_disabled_from_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = WebUiCredentialsFileTests().write_credentials(directory)
            args = parse_args(
                [
                    "--simulator",
                    "--credentials-file",
                    str(credentials),
                    "--mqtt-host",
                    "127.0.0.1",
                    "--mqtt-port",
                    "1883",
                    "--no-auto-start-mqtt",
                ]
            )
        self.assertTrue(args.no_auto_start_mqtt)


if __name__ == "__main__":
    unittest.main()
