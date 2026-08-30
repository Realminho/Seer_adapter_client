"""Tests for get_config_with_fallback.

When the per-instance config TOML cannot be loaded, the adapter still needs a
usable config (broker + serialNumber) to report the failure over MQTT. The
fallback re-loads base config.toml plus the instance overrides so the right
robot identity/broker survive even though its dedicated file is gone.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

import config.config as configmod
from config.config import get_config_with_fallback


class TestGetConfigWithFallback(unittest.TestCase):
    def test_default_path_returns_config_and_no_error(self):
        cfg, err = get_config_with_fallback(config_path=None, overrides=None)
        self.assertIsNone(err)
        self.assertIsNotNone(cfg)

    def test_missing_instance_file_falls_back_to_base_with_overrides(self):
        cfg, err = get_config_with_fallback(
            config_path="config/__definitely_missing_instance__.toml",
            overrides={
                "vehicle": {"serial_number": "HN-SH6-TR-002"},
                "mqtt_broker": {"host": "192.168.2.61", "port": 11883},
            },
        )
        self.assertIsInstance(err, FileNotFoundError)
        # Fallback used base config.toml but the instance overrides still apply,
        # so topic suffix (serialNumber) + broker remain correct for reporting.
        self.assertEqual(cfg.vehicle.serial_number, "HN-SH6-TR-002")
        self.assertEqual(cfg.mqtt_broker.host, "192.168.2.61")

    def test_fallback_also_failing_propagates(self):
        # Even base config.toml unreadable => nothing to fall back to => raise,
        # so the caller can exit non-zero and let systemd restart.
        with patch.object(configmod, "_CONFIG_PATH", Path("/no/such/base-config.toml")):
            with self.assertRaises(FileNotFoundError):
                get_config_with_fallback(
                    config_path="config/__definitely_missing_instance__.toml",
                    overrides={"vehicle": {"serial_number": "X"}},
                )

    def test_default_path_failure_propagates_without_double_try(self):
        # config_path is None (base config.toml is the primary) and it fails:
        # there is no separate fallback, so it propagates.
        with patch.object(configmod, "_CONFIG_PATH", Path("/no/such/base-config.toml")):
            with self.assertRaises(FileNotFoundError):
                get_config_with_fallback(config_path=None, overrides=None)


class TestBrokenExtensionsStillReports(unittest.TestCase):
    """깨진 extensions.hcl은 로봇을 세우되 입을 막지는 않는다.

    예전에는 ExtensionsError가 그대로 전파돼 main()이 SystemExit(1)로 죽었고,
    systemd가 되살리면 같은 자리에서 또 죽었다 — journalctl을 열 수 있는 사람만
    원인을 알 수 있는 크래시 루프였다. 이제는 (config, error)를 돌려주어 호출부가
    config-error 모드로 들어가고, FATAL CONFIG_LOAD_FAILED가 MQTT로 나간다.
    vehicle/motor/IO는 그 모드에서 시작하지 않으므로 로봇은 여전히 안 움직인다.
    """

    def _stale_extensions(self, tmp):
        """이 현장에서 실제로 나올 모양: 옮겨진 키가 pio 블록에 남은 것.

        나머지는 출하 파일 그대로라 "블록 누락" 같은 다른 이유로 먼저 걸리지
        않는다 — 부팅을 막는 것이 정말 그 키인지 확인하려는 것이다.
        """
        text = (ADAPTER_ROOT / "config" / "extensions.hcl").read_text(encoding="utf-8")
        text = text.replace(
            'extension "pio" {', 'extension "pio" {\n  station_id = "123456"'
        )
        path = Path(tmp) / "extensions.hcl"
        path.write_text(text, encoding="utf-8")
        return path

    def test_broken_extensions_returns_a_reporting_config_and_the_error(self):
        from tempfile import TemporaryDirectory

        from config.extensions import ExtensionsError

        with TemporaryDirectory() as tmp:
            broken = self._stale_extensions(tmp)
            cfg, err = get_config_with_fallback(
                config_path=None,
                overrides={
                    "vehicle": {"serial_number": "HN-SH6-TR-001"},
                    "mqtt_broker": {"host": "192.168.2.61", "port": 11883},
                },
                extensions_path=broken,
            )

        self.assertIsInstance(err, ExtensionsError)
        # 보고에 필요한 두 값은 config.toml + robots.hcl override에서 오므로
        # extensions.hcl이 깨져도 멀쩡하다.
        self.assertEqual(cfg.vehicle.serial_number, "HN-SH6-TR-001")
        self.assertEqual(cfg.mqtt_broker.host, "192.168.2.61")

    def test_the_reported_reason_names_what_to_edit(self):
        """ConfigErrorReporter가 str(error)를 그대로 errorDescription에 싣는다.

        그래서 여기서 문구가 비면 FMS에는 빈 FATAL만 뜬다.
        """
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            broken = self._stale_extensions(tmp)
            _cfg, err = get_config_with_fallback(
                config_path=None,
                overrides={"vehicle": {"serial_number": "X"}},
                extensions_path=broken,
            )

        self.assertIn("station_id", str(err))
        self.assertIn("airshower", str(err))

    def test_a_broken_example_fallback_propagates(self):
        # 대체 파일마저 못 읽으면 보고할 브로커도 못 찾는다 — 그때는 예전처럼
        # 전파해서 호출부가 non-zero로 빠지고 systemd가 재시작하게 둔다.
        from tempfile import TemporaryDirectory

        from config.extensions import ExtensionsError

        with TemporaryDirectory() as tmp:
            broken = self._stale_extensions(tmp)
            with patch.object(
                configmod, "_REPORTING_EXTENSIONS_PATH", Path("/no/such/extensions.hcl")
            ):
                with self.assertRaises(ExtensionsError):
                    get_config_with_fallback(
                        config_path=None,
                        overrides={"vehicle": {"serial_number": "X"}},
                        extensions_path=broken,
                    )


class TestBrokenMotionRuleStillReports(unittest.TestCase):
    """motion_rules 한 줄이 부팅을 죽이지 않는다.

    2026-08-23 20:44 현장: `MotionRule.__init__() missing 1 required positional
    argument: 'to'`. ConfigError가 아니라 TypeError였고, 깨진 파일이 base
    config.toml이라 fallback도 같은 자리에서 또 실패해 main()이 SystemExit(1)로
    죽었다 — systemd가 되살릴 때마다 반복되는 크래시 루프였다.
    """

    def _base_config_with(self, tmp, rules_line):
        """base config.toml의 motion_rules만 바꿔 임시 파일로 만든다."""
        text = (ADAPTER_ROOT / "config" / "config.toml").read_text(encoding="utf-8")
        start = text.index("motion_rules = [")
        end = text.index("]", start) + 1
        text = text[:start] + f"motion_rules = [\n  {rules_line},\n]" + text[end:]
        path = Path(tmp) / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_to_in_base_config_still_reports(self):
        """깨진 파일이 base config.toml이어도 보고용 config가 나온다.

        로봇별 config_path로만 시험하면 사다리의 2단계(base config.toml)가
        가려서, 정작 현장에서 죽은 경로를 검증하지 못한다.
        """
        from tempfile import TemporaryDirectory

        from config.errors import ConfigError

        with TemporaryDirectory() as tmp:
            broken = self._base_config_with(tmp, '{ from = "F1_60", mode = "move" }')
            with patch.object(configmod, "_CONFIG_PATH", broken):
                cfg, err = get_config_with_fallback(
                    config_path=None,
                    overrides={
                        "vehicle": {"serial_number": "HN-SH6-TR-001"},
                        "mqtt_broker": {"host": "192.168.2.61", "port": 11883},
                    },
                )

        self.assertIsInstance(err, ConfigError)
        # 보고에 필요한 두 값이 살아 있어야 MQTT로 말할 수 있다.
        self.assertEqual(cfg.vehicle.serial_number, "HN-SH6-TR-001")
        self.assertEqual(cfg.mqtt_broker.host, "192.168.2.61")
        # 깨진 룰은 보고용 config에서 빠진다(이 config로는 주행하지 않는다).
        self.assertEqual(cfg.motion_rules, [])

    def test_the_reported_reason_names_the_rule_to_edit(self):
        """str(error)가 그대로 errorDescription이 되므로 문구가 곧 단서다."""
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            broken = self._base_config_with(tmp, '{ from = "F1_60", mode = "move" }')
            with patch.object(configmod, "_CONFIG_PATH", broken):
                _cfg, err = get_config_with_fallback(
                    config_path=None, overrides={"vehicle": {"serial_number": "X"}}
                )

        message = str(err)
        self.assertIn("motion_rules 1번째 항목", message)
        self.assertIn("to", message)
        self.assertIn('{ from = "F1_60", mode = "move" }', message)
        self.assertEqual(err.path, str(broken))

    def test_unknown_key_in_a_rule_is_labelled_too(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            broken = self._base_config_with(tmp, '{ to = "F1_60", mdoe = "move" }')
            with patch.object(configmod, "_CONFIG_PATH", broken):
                _cfg, err = get_config_with_fallback(
                    config_path=None, overrides={"vehicle": {"serial_number": "X"}}
                )

        self.assertIn("mdoe", str(err))
        self.assertIn("motion_rules 1번째 항목", str(err))

    def test_running_path_stays_strict(self):
        """운행 경로는 그대로 멈춘다 — 보고용(_lenient)만 관대하다."""
        from tempfile import TemporaryDirectory

        from config.config import get_config
        from config.errors import ConfigError

        with TemporaryDirectory() as tmp:
            broken = self._base_config_with(tmp, '{ from = "F1_60", mode = "move" }')
            with self.assertRaises(ConfigError):
                get_config(config_path=broken)


if __name__ == "__main__":
    unittest.main()
