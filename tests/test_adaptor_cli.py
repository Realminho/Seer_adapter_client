import argparse
import ast
import sys
import textwrap
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTOR_ROOT = REPO_ROOT / "adaptor"
if str(ADAPTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_ROOT))

import main
from utils.mqtt_client import MQTTClient


class AdaptorCliTest(unittest.TestCase):
    def test_main_does_not_import_simulator_at_module_load_time(self):
        tree = ast.parse((ADAPTOR_ROOT / "main.py").read_text())
        top_level_imports = [
            node
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]

        imported_modules = {
            node.module
            for node in top_level_imports
            if isinstance(node, ast.ImportFrom)
        }
        imported_modules.update(
            alias.name
            for node in top_level_imports
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        self.assertNotIn("cls_jibot_simulator", imported_modules)

    def test_mqtt_address_overrides_broker_host_and_port(self):
        args = main.parse_args(["--mqtt-address", "mqtt.local:1884"])
        config = SimpleNamespace(
            mqtt_broker=SimpleNamespace(host="192.168.3.108", port=11883)
        )

        main.apply_cli_overrides(config, args)

        self.assertEqual(config.mqtt_broker.host, "mqtt.local")
        self.assertEqual(config.mqtt_broker.port, 1884)

    def test_mqtt_host_and_port_override_broker_individually(self):
        args = main.parse_args(["--mqtt-host", "10.0.0.5", "--mqtt-port", "1885"])
        config = SimpleNamespace(
            mqtt_broker=SimpleNamespace(host="192.168.3.108", port=11883)
        )

        main.apply_cli_overrides(config, args)

        self.assertEqual(config.mqtt_broker.host, "10.0.0.5")
        self.assertEqual(config.mqtt_broker.port, 1885)

    def test_mqtt_client_uses_supplied_config(self):
        config = SimpleNamespace(
            mqtt_broker=SimpleNamespace(
                host="mqtt.override",
                port=1886,
                vda_interface="uagv",
            ),
            vehicle=SimpleNamespace(
                vda_version="v2",
                serial_number="JIBOT-001",
            ),
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            client = MQTTClient(config=config)

        self.assertEqual(client.host, "mqtt.override")
        self.assertEqual(client.port, 1886)
        self.assertEqual(client.topic_prefix, "uagv/v2/JIBOT-001")

    def test_mqtt_address_requires_host_colon_port(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            main.parse_mqtt_address("mqtt.local")

    def test_default_service_uses_single_robot_fleet_entry(self):
        body = textwrap.dedent(
            """
            robot "ROBOT-A" {
              vehicle_ip = "10.0.0.11"
              simulator  = true
              mqtt_port  = 12000
            }
            """
        )
        with TemporaryDirectory() as tmp:
            robots = Path(tmp) / "robots.hcl"
            robots.write_text(body, encoding="utf-8")

            args = main.parse_args(["--robots", str(robots)])
            overrides, config_path, simulator, extensions_path, recipes_path = main.resolve_instance(args)

        self.assertIsNone(config_path)
        self.assertIsNone(extensions_path)
        self.assertIsNone(recipes_path)
        self.assertTrue(simulator)
        self.assertEqual(overrides["vehicle"]["serial_number"], "ROBOT-A")
        self.assertEqual(overrides["vehicle"]["vehicle_ip"], "10.0.0.11")
        self.assertEqual(overrides["mqtt_broker"]["port"], 12000)

    def test_default_service_rejects_multi_robot_fleet_without_robot(self):
        body = textwrap.dedent(
            """
            robot "ROBOT-A" {}

            robot "ROBOT-B" {}
            """
        )
        with TemporaryDirectory() as tmp:
            robots = Path(tmp) / "robots.hcl"
            robots.write_text(body, encoding="utf-8")

            args = main.parse_args(["--robots", str(robots)])
            with self.assertRaises(SystemExit):
                main.resolve_instance(args)

    def test_explicit_robot_uses_fleet_entry(self):
        body = textwrap.dedent(
            """
            robot "ROBOT-A" {
              vehicle_ip = "10.0.0.11"
              simulator  = true
              mqtt_port  = 12000
            }
            """
        )
        with TemporaryDirectory() as tmp:
            robots = Path(tmp) / "robots.hcl"
            robots.write_text(body, encoding="utf-8")

            args = main.parse_args(["--robots", str(robots), "--robot", "ROBOT-A"])
            overrides, config_path, simulator, extensions_path, recipes_path = main.resolve_instance(args)

        self.assertIsNone(config_path)
        self.assertIsNone(extensions_path)
        self.assertIsNone(recipes_path)
        self.assertTrue(simulator)
        self.assertEqual(overrides["vehicle"]["serial_number"], "ROBOT-A")
        self.assertEqual(overrides["vehicle"]["vehicle_ip"], "10.0.0.11")
        self.assertEqual(overrides["mqtt_broker"]["port"], 12000)

    def test_relative_config_path_resolves_against_fleet_file(self):
        body = textwrap.dedent(
            """
            robot "ROBOT-A" {
              config = "robot-a.toml"
            }
            """
        )
        with TemporaryDirectory() as tmp:
            robots = Path(tmp) / "robots.hcl"
            robots.write_text(body, encoding="utf-8")

            args = main.parse_args(["--robots", str(robots), "--robot", "ROBOT-A"])
            _, config_path, _, extensions_path, _ = main.resolve_instance(args)

        self.assertEqual(Path(config_path), Path(tmp) / "robot-a.toml")
        self.assertIsNone(extensions_path)

    def test_relative_extensions_path_resolves_against_fleet_file(self):
        body = textwrap.dedent(
            """
            robot "ROBOT-A" {
              extensions = "robot-a-extensions.hcl"
            }
            """
        )
        with TemporaryDirectory() as tmp:
            robots = Path(tmp) / "robots.hcl"
            robots.write_text(body, encoding="utf-8")

            args = main.parse_args(["--robots", str(robots), "--robot", "ROBOT-A"])
            _, _, _, extensions_path, _ = main.resolve_instance(args)

        self.assertEqual(
            Path(extensions_path),
            Path(tmp) / "robot-a-extensions.hcl",
        )

    def test_relative_recipes_path_resolves_against_fleet_file(self):
        body = 'robot "ROBOT-A" { recipes = "robot-a-recipes.hcl" }\n'
        with TemporaryDirectory() as tmp:
            robots = Path(tmp) / "robots.hcl"
            robots.write_text(body, encoding="utf-8")
            args = main.parse_args(["--robots", str(robots), "--robot", "ROBOT-A"])
            _, _, _, _, recipes_path = main.resolve_instance(args)
        self.assertEqual(Path(recipes_path), Path(tmp) / "robot-a-recipes.hcl")


if __name__ == "__main__":
    unittest.main()
