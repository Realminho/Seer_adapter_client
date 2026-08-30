"""Tests for the robot fleet loader and the per-robot TUI registry.

config/robots.hcl(여러 대)을 읽어 인스턴스별 config override를 만들고, TUI
대시보드가 로봇마다 spec(고유 systemd 유닛 + MQTT 토픽)을 만드는지 검증한다.
"""

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config import fleet
from config.config import get_config
from core.registry import build_registry


FLEET_HCL = textwrap.dedent(
    """
    robot "ROBOT-A" {
      vehicle_ip = "10.0.0.11"
      ezi_io     = "10.8.8.87"
      simulator  = true
    }

    robot "ROBOT-B" {
      vehicle_ip = "10.0.0.12"
      mqtt_port  = 12000
    }
    """
).strip()

SINGLE_FLEET_HCL = textwrap.dedent(
    """
    robot "ROBOT-A" {
      vehicle_ip = "10.0.0.11"
      mqtt_port  = 12000
      simulator  = true
    }
    """
).strip()


def _write(tmp: str, body: str) -> str:
    path = Path(tmp) / "robots.hcl"
    path.write_text(body, encoding="utf-8")
    return str(path)


class LoadFleetTest(unittest.TestCase):
    def test_loads_and_maps_overrides(self):
        with TemporaryDirectory() as tmp:
            robots = fleet.load_fleet(_write(tmp, FLEET_HCL))
        self.assertEqual([r["id"] for r in robots], ["ROBOT-A", "ROBOT-B"])

        ov_a = fleet.robot_overrides(fleet.find_robot(robots, "ROBOT-A"))
        self.assertEqual(ov_a["vehicle"]["serial_number"], "ROBOT-A")
        self.assertEqual(ov_a["vehicle"]["vehicle_ip"], "10.0.0.11")
        self.assertEqual(ov_a["ezi"]["ezi_io"], "10.8.8.87")
        # simulator is not a config override.
        self.assertNotIn("simulator", ov_a.get("vehicle", {}))

        ov_b = fleet.robot_overrides(fleet.find_robot(robots, "ROBOT-B"))
        self.assertEqual(ov_b["mqtt_broker"]["port"], 12000)
        self.assertNotIn("ezi", ov_b)

    def test_missing_file_raises(self):
        with self.assertRaises(fleet.FleetError):
            fleet.load_fleet("/no/such/robots.hcl")

    def test_find_unknown_id_raises(self):
        with TemporaryDirectory() as tmp:
            robots = fleet.load_fleet(_write(tmp, FLEET_HCL))
        with self.assertRaises(fleet.FleetError):
            fleet.find_robot(robots, "NOPE")


class RegistryFleetTest(unittest.TestCase):
    def _build_with_fleet(self, body):
        """Point fleet.DEFAULT_FLEET_PATH at a temp file for build_registry."""
        original = fleet.DEFAULT_FLEET_PATH
        with TemporaryDirectory() as tmp:
            fleet.DEFAULT_FLEET_PATH = Path(_write(tmp, body))
            try:
                return build_registry(get_config())
            finally:
                fleet.DEFAULT_FLEET_PATH = original

    def test_registry_uses_multi_robot_fleet_by_default(self):
        specs = self._build_with_fleet(FLEET_HCL)
        keys = [s.key for s in specs]
        self.assertEqual(keys, ["jibot:ROBOT-A", "jibot:ROBOT-B"])
        by_key = {s.key: s for s in specs}
        self.assertEqual(by_key["jibot:ROBOT-A"].unit, "amr-adaptor.service")
        self.assertEqual(by_key["jibot:ROBOT-A"].serial, "ROBOT-A")
        self.assertEqual(by_key["jibot:ROBOT-A"].vehicle_host, "10.0.0.11")
        self.assertEqual(by_key["jibot:ROBOT-B"].unit, "amr-adaptor.service")
        self.assertEqual(by_key["jibot:ROBOT-B"].serial, "ROBOT-B")
        self.assertEqual(by_key["jibot:ROBOT-B"].mqtt_port, 12000)

    def test_single_robot_fleet_uses_robot_identity(self):
        specs = self._build_with_fleet(SINGLE_FLEET_HCL)
        keys = [s.key for s in specs]
        self.assertEqual(keys, ["jibot"])
        jibot = specs[0]
        self.assertEqual(jibot.display_name, "JIBOT Adapter")
        self.assertEqual(jibot.unit, "amr-adaptor.service")
        self.assertEqual(jibot.serial, "ROBOT-A")
        self.assertEqual(jibot.vehicle_host, "10.0.0.11")

    def test_jibot_registry_requires_fleet(self):
        original = fleet.DEFAULT_FLEET_PATH
        fleet.DEFAULT_FLEET_PATH = Path("/no/such/robots.hcl")
        try:
            with self.assertRaises(fleet.FleetError):
                build_registry(get_config())
        finally:
            fleet.DEFAULT_FLEET_PATH = original

    def test_hexplorer_default_vendor_uses_amr_adapter_unit(self):
        from config.config import AdapterConfig
        cfg = get_config()
        cfg.adapter = AdapterConfig(vendor="hexplorer")
        specs = build_registry(cfg)
        keys = [s.key for s in specs]
        self.assertEqual(keys, ["hexplorer"])
        self.assertEqual(specs[0].unit, "amr-adaptor.service")
        self.assertEqual(specs[0].exec_script, "run-hexplorer.sh")
        action_types = {a.action_type for a in specs[0].instant_actions}
        self.assertEqual(action_types, {"getCameraInfo", "stop", "standUp", "standDown", "walkMode"})

    def test_instances_produce_named_units(self):
        from config.config import AdapterConfig, AdapterInstance
        cfg = get_config()
        cfg.adapter = AdapterConfig(
            instances=[
                AdapterInstance(name="j1", vendor="jibot"),
                AdapterInstance(name="h1", vendor="hexplorer"),
            ]
        )
        by_key = {s.key: s for s in build_registry(cfg)}
        self.assertEqual(by_key["jibot:j1"].unit, "amr-adaptor@j1.service")
        self.assertEqual(by_key["hexplorer:h1"].unit, "amr-adaptor@h1.service")

    def test_instance_config_path_is_loaded(self):
        from unittest.mock import patch
        from config.config import AdapterConfig, AdapterInstance
        real = get_config()
        real.adapter = AdapterConfig(instances=[AdapterInstance(name="j1", vendor="jibot", config="config/special.toml")])
        with patch("config.config.get_config", return_value=real) as gc:
            specs = build_registry(real)
        gc.assert_any_call(config_path="config/special.toml")
        self.assertEqual(specs[0].unit, "amr-adaptor@j1.service")

    def test_relative_robot_config_path_resolves_against_fleet_parent(self):
        """Regression: a fleet robot's `config = "..."` must resolve against
        robots.hcl's own directory (like main.py/web/server.py), not the
        process CWD -- otherwise build_registry() can silently load a
        different file than the one the WebUI validated/saved."""
        from unittest.mock import patch

        original = fleet.DEFAULT_FLEET_PATH
        with TemporaryDirectory() as tmp:
            fleet_path = Path(
                _write(tmp, 'robot "ROBOT-A" {\n  config = "sub/robot-a.toml"\n}\n')
            )
            fleet.DEFAULT_FLEET_PATH = fleet_path
            real = get_config()
            try:
                with patch("config.config.get_config", return_value=real) as gc:
                    build_registry(real)
            finally:
                fleet.DEFAULT_FLEET_PATH = original
        gc.assert_any_call(
            config_path=(Path(tmp) / "sub" / "robot-a.toml").resolve(),
            overrides={"vehicle": {"serial_number": "ROBOT-A"}},
            extensions_path=None,
            recipes_path=None,
        )

    def test_relative_robot_extensions_path_is_loaded(self):
        from unittest.mock import patch

        original = fleet.DEFAULT_FLEET_PATH
        with TemporaryDirectory() as tmp:
            fleet_path = Path(
                _write(
                    tmp,
                    'robot "ROBOT-A" {\n'
                    '  extensions = "sub/extensions.hcl"\n'
                    '}\n',
                )
            )
            fleet.DEFAULT_FLEET_PATH = fleet_path
            real = get_config()
            try:
                with patch("config.config.get_config", return_value=real) as gc:
                    build_registry(real)
            finally:
                fleet.DEFAULT_FLEET_PATH = original
        gc.assert_any_call(
            config_path=None,
            overrides={"vehicle": {"serial_number": "ROBOT-A"}},
            extensions_path=(Path(tmp) / "sub" / "extensions.hcl").resolve(),
            recipes_path=None,
        )


class FleetErrorTest(unittest.TestCase):
    def test_missing_file(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(Path(tmp) / "missing.hcl")

    def test_no_robot_blocks(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, '# 비어 있음\n')
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.load_fleet(path)
        self.assertIn("no robot", str(ctx.exception))

    def test_duplicate_id(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" {}\n\nrobot "A" {}\n')
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.load_fleet(path)
        self.assertIn("duplicate robot id: A", str(ctx.exception))

    def test_empty_label_rejected(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "" {\n  vehicle_ip = "10.0.0.1"\n}\n')
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(path)

    def test_syntax_error_becomes_fleet_error(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" {\n  vehicle_ip =\n}\n')
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(path)

    def test_unlabelled_robot_block_rejected(self):
        """A dropped quote (`robot { ... }`) must not silently vanish a key."""
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot {\n  vehicle_ip = "10.0.0.1"\n}\n')
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(path)

    def test_extra_labelled_robot_block_rejected(self):
        """`robot "A" "B" { ... }` must not silently drop vehicle_ip under "B"."""
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" "B" {\n  vehicle_ip = "1"\n}\n')
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(path)


class FieldSchemaTest(unittest.TestCase):
    """robots.hcl은 값 타입을 강제하지 않으므로 로더가 검증해야 한다."""

    def _load(self, body: str):
        with TemporaryDirectory() as tmp:
            return fleet.load_fleet(_write(tmp, body))

    def test_quoted_bool_rejected(self):
        """`simulator = "false"`가 통과하면 실차가 시뮬레이터로 뜬다."""
        with self.assertRaises(fleet.FleetError) as ctx:
            self._load('robot "R" {\n  simulator = "false"\n}\n')
        self.assertIn("simulator", str(ctx.exception))
        self.assertIn("R", str(ctx.exception))

    def test_bool_accepted(self):
        robots = self._load('robot "R" {\n  simulator = true\n}\n')
        self.assertIs(robots[0]["simulator"], True)

    def test_quoted_port_rejected(self):
        with self.assertRaises(fleet.FleetError) as ctx:
            self._load('robot "R" {\n  vehicle_port = "7273"\n}\n')
        self.assertIn("vehicle_port", str(ctx.exception))

    def test_bool_is_not_a_port(self):
        with self.assertRaises(fleet.FleetError):
            self._load('robot "R" {\n  mqtt_port = true\n}\n')

    def test_port_range_enforced(self):
        with self.assertRaises(fleet.FleetError) as ctx:
            self._load('robot "R" {\n  vehicle_port = 70000\n}\n')
        self.assertIn("1-65535", str(ctx.exception))

    def test_string_extra_args_rejected(self):
        """문자열이면 run_multi가 문자 단위로 쪼개 CLI 인자를 만든다."""
        with self.assertRaises(fleet.FleetError) as ctx:
            self._load('robot "R" {\n  extra_args = "--simulator"\n}\n')
        self.assertIn("extra_args", str(ctx.exception))

    def test_string_list_accepted(self):
        robots = self._load('robot "R" {\n  extra_args = ["--x", "--y"]\n}\n')
        self.assertEqual(robots[0]["extra_args"], ["--x", "--y"])

    def test_non_string_ip_rejected(self):
        with self.assertRaises(fleet.FleetError) as ctx:
            self._load('robot "R" {\n  vehicle_ip = 10\n}\n')
        self.assertIn("vehicle_ip", str(ctx.exception))

    def test_unknown_field_rejected(self):
        """오타를 조용히 무시하면 운영자가 설정이 먹은 줄 안다."""
        with self.assertRaises(fleet.FleetError) as ctx:
            self._load('robot "R" {\n  vehicel_ip = "10.0.0.1"\n}\n')
        self.assertIn("vehicel_ip", str(ctx.exception))

    def test_shipped_fleet_files_pass_schema(self):
        for name in ("config/robots.hcl", "config/robots.hcl.example"):
            with self.subTest(name=name):
                self.assertTrue(fleet.load_fleet(name))


class ResolveRobotPathTest(unittest.TestCase):
    def test_relative_path_resolves_against_fleet_parent(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                'robot "A" {\n  config = "sub/robot-a.toml"\n}\n',
            )
            robots = fleet.load_fleet(path)
            resolved = fleet.resolve_robot_path(robots[0], "config", path)
            # resolve_robot_path는 .resolve()로 끝난다. macOS의 임시 디렉터리는
            # /var -> /private/var 심볼릭 링크 아래라, 기대값도 같이 풀지 않으면
            # 리눅스에서만 통과하고 여기서는 경로 앞부분이 달라 실패한다.
            expected = (Path(tmp) / "sub" / "robot-a.toml").resolve()
        self.assertEqual(resolved, expected)

    def test_absolute_path_kept(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" {\n  config = "/etc/robot-a.toml"\n}\n')
            robots = fleet.load_fleet(path)
            resolved = fleet.resolve_robot_path(robots[0], "config", path)
        self.assertEqual(resolved, Path("/etc/robot-a.toml"))

    def test_missing_key_returns_none(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" {}\n')
            robots = fleet.load_fleet(path)
        self.assertIsNone(fleet.resolve_robot_path(robots[0], "config", path))


EXTENSION_OVERRIDE_HCL = textwrap.dedent(
    """
    robot "ROBOT-A" {
      vehicle_ip = "10.0.0.11"

      extension "pio" {
        pio_serial_port = "/dev/ttyUSB9"
        vehicle_num     = "AMR002"
        advanced = {
          select_off_delay_sec = 1.25
        }
      }

      extension "ezi" {
        clamp_position = 30000
      }
    }
    """
).strip()


class ExtensionOverrideTest(unittest.TestCase):
    """robots.hcl의 extension 블록이 extensions.hcl 값을 덮는지."""

    def _overrides(self, body: str):
        with TemporaryDirectory() as tmp:
            robots = fleet.load_fleet(_write(tmp, body))
        return fleet.robot_overrides(robots[0])

    def test_extension_block_becomes_section_override(self):
        overrides = self._overrides(EXTENSION_OVERRIDE_HCL)
        self.assertEqual(overrides["pio"]["pio_serial_port"], "/dev/ttyUSB9")
        self.assertEqual(overrides["pio"]["vehicle_num"], "AMR002")
        self.assertEqual(overrides["ezi"]["clamp_position"], 30000)
        # 로봇이 적지 않은 키는 override에 없어야 extensions.hcl 값이 남는다.
        self.assertNotIn("pio_baudrate", overrides["pio"])

    def test_pio_advanced_is_promoted_to_its_own_section(self):
        overrides = self._overrides(EXTENSION_OVERRIDE_HCL)
        self.assertEqual(overrides["pio_advanced"]["select_off_delay_sec"], 1.25)
        # advanced는 [pio]가 아니라 [pio_advanced]로 간다. 그대로 두면
        # PioConfig(**...)가 모르는 키로 죽는다.
        self.assertNotIn("advanced", overrides["pio"])

    def test_identity_keys_still_map(self):
        overrides = self._overrides(EXTENSION_OVERRIDE_HCL)
        self.assertEqual(overrides["vehicle"]["serial_number"], "ROBOT-A")
        self.assertEqual(overrides["vehicle"]["vehicle_ip"], "10.0.0.11")

    def test_unknown_extension_label_is_rejected(self):
        body = 'robot "A" {\n  extension "pioo" {\n    x = 1\n  }\n}\n'
        with TemporaryDirectory() as tmp:
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.load_fleet(_write(tmp, body))
        self.assertIn("pioo", str(ctx.exception))

    def test_duplicate_extension_label_is_rejected(self):
        body = (
            'robot "A" {\n'
            '  extension "pio" {\n    vehicle_num = "A"\n  }\n'
            '  extension "pio" {\n    vehicle_num = "B"\n  }\n'
            "}\n"
        )
        with TemporaryDirectory() as tmp:
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(_write(tmp, body))

    def test_override_wins_over_extensions_hcl(self):
        with TemporaryDirectory() as tmp:
            robots = fleet.load_fleet(_write(tmp, EXTENSION_OVERRIDE_HCL))
        config = get_config(overrides=fleet.robot_overrides(robots[0]))
        self.assertEqual(config.pio_config.pio_serial_port, "/dev/ttyUSB9")
        self.assertEqual(config.pio_config.vehicle_num, "AMR002")
        self.assertEqual(config.pio_advanced.select_off_delay_sec, 1.25)
        self.assertEqual(config.ezi_config.clamp_position, 30000)
        # 덮지 않은 값은 extensions.hcl 것이 그대로 남는다.
        self.assertEqual(config.pio_config.pio_baudrate, 38400)



ENABLED_FLEET_HCL = textwrap.dedent(
    """
    robot "ROBOT-A" {
      vehicle_ip = "10.0.0.11"
    }

    robot "ROBOT-B" {
      vehicle_ip = "10.0.0.12"
      enabled    = false
    }
    """
).strip()


class EnabledFlagTest(unittest.TestCase):
    """enabled = false인 robot 블록은 이 기계에서 돌지 않는다."""

    def test_disabled_robot_is_not_in_the_fleet(self):
        with TemporaryDirectory() as tmp:
            robots = fleet.load_fleet(_write(tmp, ENABLED_FLEET_HCL))
        self.assertEqual([r["id"] for r in robots], ["ROBOT-A"])

    def test_absent_enabled_means_enabled(self):
        with TemporaryDirectory() as tmp:
            robots = fleet.load_fleet(_write(tmp, FLEET_HCL))
        self.assertEqual([r["id"] for r in robots], ["ROBOT-A", "ROBOT-B"])

    def test_include_disabled_returns_every_block(self):
        with TemporaryDirectory() as tmp:
            robots = fleet.load_fleet(
                _write(tmp, ENABLED_FLEET_HCL), include_disabled=True
            )
        self.assertEqual([r["id"] for r in robots], ["ROBOT-A", "ROBOT-B"])

    def test_robot_ids_skips_disabled(self):
        with TemporaryDirectory() as tmp:
            ids = fleet.robot_ids(_write(tmp, ENABLED_FLEET_HCL))
        self.assertEqual(ids, ["ROBOT-A"])

    def test_find_robot_names_the_disabled_block(self):
        # 블록이 파일에 눈앞에 있는데 "not in fleet"이라고만 하면 운영자가
        # 오타를 찾는 데 시간을 쓴다. 꺼져 있다고 말해 준다.
        with TemporaryDirectory() as tmp:
            robots = fleet.load_fleet(
                _write(tmp, ENABLED_FLEET_HCL), include_disabled=True
            )
        with self.assertRaises(fleet.FleetError) as ctx:
            fleet.find_robot(robots, "ROBOT-B")
        message = str(ctx.exception)
        self.assertIn("ROBOT-B", message)
        self.assertIn("enabled", message)

    def test_all_disabled_is_an_error(self):
        body = 'robot "A" {\n  enabled = false\n}\n'
        with TemporaryDirectory() as tmp:
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.load_fleet(_write(tmp, body))
        self.assertIn("disabled", str(ctx.exception))

    def test_enabled_must_be_a_bare_bool(self):
        body = 'robot "A" {\n  enabled = "false"\n}\n'
        with TemporaryDirectory() as tmp:
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(_write(tmp, body))

    def test_duplicate_id_is_rejected_even_when_one_is_disabled(self):
        body = (
            'robot "A" {\n  vehicle_ip = "10.0.0.11"\n}\n'
            'robot "A" {\n  enabled = false\n}\n'
        )
        with TemporaryDirectory() as tmp:
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(_write(tmp, body))



if __name__ == "__main__":
    unittest.main()
