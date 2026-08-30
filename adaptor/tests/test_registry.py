import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import registry
from core.systemd import EdgeAgentUnit


class EdgeAgentRegistryTest(unittest.TestCase):
    def test_make_edge_agent_spec_for_template_instance(self):
        spec = registry.make_edge_agent_spec(
            EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service"),
            edge_root=Path("/tmp/missing-edge-agent"),
        )

        self.assertEqual(spec.key, "edge-agent:cell-a")
        self.assertEqual(spec.display_name, "Edge Agent cell-a")
        self.assertEqual(spec.unit, "edge-agent@cell-a.service")
        self.assertEqual(spec.exec_script, "")
        self.assertEqual(spec.manufacturer, "dobot")
        self.assertEqual(spec.serial, "cell-a")
        self.assertEqual(spec.vda_full_version, "")
        self.assertEqual(spec.topic_prefix, "")
        self.assertEqual(spec.monitor_kind, "none")
        self.assertEqual(spec.instant_actions, ())
        self.assertEqual(spec.runnable, ())

    def test_make_edge_agent_spec_for_non_template_unit(self):
        spec = registry.make_edge_agent_spec(
            EdgeAgentUnit(id="default", unit="edge-agent.service"),
            edge_root=Path("/tmp/missing-edge-agent"),
        )

        self.assertEqual(spec.key, "edge-agent")
        self.assertEqual(spec.display_name, "Edge Agent")
        self.assertEqual(spec.serial, "edge-agent")

    def test_edge_agent_diagnostics_prefer_local_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / ".venv" / "bin"
            bin_dir.mkdir(parents=True)
            pytest = bin_dir / "pytest"
            ruff = bin_dir / "ruff"
            pytest.write_text("#!/bin/sh\n", encoding="utf-8")
            ruff.write_text("#!/bin/sh\n", encoding="utf-8")

            spec = registry.make_edge_agent_spec(
                EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service"),
                edge_root=root,
            )

            self.assertEqual(spec.runnable[0].argv[0], str(pytest))
            self.assertEqual(spec.runnable[1].argv[0], str(pytest))
            self.assertEqual(spec.runnable[2].argv[0], str(ruff))

    def test_build_registry_appends_discovered_edge_agents(self):
        from config.config import AdapterConfig

        class Vehicle:
            serial_number = "HN"
            vda_full_version = "3.0.0"
            vda_version = "v3"
            vehicle_ip = "10.0.0.21"
            vehicle_port = 7273

        class Broker:
            vda_interface = "uagv"
            host = "127.0.0.1"
            port = 1883

        class Config:
            vehicle = Vehicle()
            mqtt_broker = Broker()
            adapter = AdapterConfig(vendor="hexplorer")

        units = [EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service")]
        with patch("core.registry.discover_edge_agent_units", return_value=units):
            specs = registry.build_registry(Config())

        self.assertEqual(
            [spec.key for spec in specs], ["hexplorer", "edge-agent:cell-a"]
        )
        self.assertEqual(specs[0].vehicle_host, "10.0.0.21")
        self.assertEqual(specs[0].vehicle_port, 7273)

    def test_jibot_spec_adds_clamp_pio_and_ezi_diagnostics(self):
        class Vehicle:
            serial_number = "HN"
            vda_full_version = "3.0.0"
            vda_version = "v3"
            manufacturer = "jibot"
            vehicle_ip = "10.0.0.11"
            vehicle_port = 7273

        class Broker:
            vda_interface = "amr"
            host = "127.0.0.1"
            port = 1883

        class Ezi:
            ezi_io = "10.8.8.87"
            tray_slot_pin = [8, 9, 10, 11, 12, 13]

        class Config:
            vehicle = Vehicle()
            mqtt_broker = Broker()
            ezi_config = Ezi()
            action_modules = []

        spec = registry._jibot_spec(
            Config(),
            key="jibot",
            display_name="JIBOT Adapter",
            unit="amr-adaptor.service",
        )
        self.assertEqual(spec.vehicle_host, "10.0.0.11")
        self.assertEqual(spec.vehicle_port, 7273)
        by_label = {diag.label: diag for diag in spec.runnable}

        # Combined clamp button is replaced by per-function clamp buttons.
        self.assertNotIn("unittest: clamp actions", by_label)
        # Per-function clamp buttons so each test can be run on its own.
        for label, method in (
            ("unittest: clamp - factsheet contents", "test_factsheet_contents"),
            (
                "unittest: clamp - instant action moves to position",
                "test_clamp_instant_action_moves_to_position",
            ),
            (
                "unittest: clamp - order action handler",
                "test_order_clamp_action_uses_shared_hardware_handler",
            ),
        ):
            self.assertIn(label, by_label)
            self.assertTrue(by_label[label].requires_manual)
            self.assertIn(registry._test_id(method), by_label[label].argv)
        # Combined PIO button is replaced by per-function PIO buttons.
        self.assertNotIn("unittest: PIO actions", by_label)
        for label, method in (
            (
                "unittest: pio - scenario out/in/delay steps",
                "test_pio_scenario_executes_out_in_delay_steps",
            ),
            (
                "unittest: pio - scenario input timeout step",
                "test_pio_scenario_reports_input_timeout_step",
            ),
            (
                "unittest: pio - read in returns inputs 1-8",
                "test_pio_read_in_returns_inputs_one_through_eight",
            ),
        ):
            self.assertIn(label, by_label)
            self.assertTrue(by_label[label].requires_manual)
            self.assertIn(registry._test_id(method), by_label[label].argv)
        self.assertIn("unittest: EZI IO / photo sensors", by_label)
        self.assertIn("pytest: EZI IO utilities", by_label)
        self.assertIn("diagnostic: EZI IO input read", by_label)
        self.assertTrue(by_label["diagnostic: EZI IO input read"].requires_manual)
        self.assertIn("10.8.8.87", by_label["diagnostic: EZI IO input read"].argv)
        self.assertIn("8,9,10,11,12,13", by_label["diagnostic: EZI IO input read"].argv)

        # Control view exposes manual clamp/servo buttons; all require confirm.
        actions = {a.action_type: a for a in spec.instant_actions}
        for action_type in (
            "clampOn", "clampOff", "clamp", "unclamp", "clampStop",
            "clampMin", "clampMax", "clampHome",
        ):
            self.assertIn(action_type, actions)
            self.assertTrue(actions[action_type].motion)

    def test_jibot_spec_exposes_discovered_action_modules_in_whitelist(self):
        from config.config import ActionPluginConfig, get_config

        config = get_config()
        config.actions.append(ActionPluginConfig(action_type="clampOn"))

        spec = registry._jibot_spec(
            config,
            key="jibot",
            display_name="JIBOT Adapter",
            unit="amr-adaptor.service",
        )

        modules = {module.title: module for module in spec.action_modules}
        clamp_on = next(
            action
            for action in modules["Clamp"].actions
            if action.action_type == "clampOn"
        )
        action_types = [action.action_type for action in spec.instant_actions]

        self.assertIsNotNone(modules["Clamp"].panel_template)
        self.assertTrue(clamp_on.motion)
        self.assertIn("clampOn", action_types)
        self.assertEqual(action_types.count("clampOn"), 1)
        self.assertIn("pioInit", action_types)
        self.assertIsNone(modules["EZIO"].panel_template)
        self.assertIn("stateRequest", action_types)

    def test_jibot_spec_omits_disabled_module_specs_like_action_registry(self):
        from types import SimpleNamespace

        from config.config import get_config
        from core.action_modules import first_party_action_specs
        from core.action_registry import ActionSpec, build_registry_from_config

        module = SimpleNamespace(
            MODULE_TITLE="Mixed",
            action_specs=lambda: (
                ActionSpec(action_type="keepModuleAction"),
                ActionSpec(action_type="dropModuleAction", enabled=False),
            ),
        )
        # This test replaces the first-party modules with "mixed", so the
        # extensions the shipped recipes.hcl builds on are absent. Drop the
        # recipes: a recipe referencing an unavailable extension is a boot
        # failure by design, and that rule is not what this test covers.
        config = dataclasses.replace(get_config(), recipes=[])
        with (
            patch("core.action_modules.default_module_names", return_value=("mixed",)),
            patch("core.action_modules.import_module", return_value=module),
            patch("core.action_modules._read_panel", return_value="panel"),
        ):
            spec = registry._jibot_spec(
                config,
                key="jibot",
                display_name="JIBOT Adapter",
                unit="amr-adaptor.service",
            )
            action_registry = build_registry_from_config(
                [], first_party_specs=first_party_action_specs(config)
            )

        module_action_types = {
            action.action_type
            for module_view in spec.action_modules
            for action in module_view.actions
        }
        instant_action_types = {
            action.action_type for action in spec.instant_actions
        }

        self.assertIn("keepModuleAction", module_action_types)
        self.assertNotIn("dropModuleAction", module_action_types)
        self.assertNotIn("dropModuleAction", instant_action_types)
        self.assertTrue(action_registry.has("keepModuleAction"))
        self.assertFalse(action_registry.has("dropModuleAction"))

    def test_jibot_instant_actions_include_manual_control(self):
        from core.registry import _JIBOT_INSTANT_ACTIONS
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        assert "manualDrive" in by_type and by_type["manualDrive"].motion is True
        assert "manualMove" in by_type and by_type["manualMove"].motion is True
        assert "manualStop" in by_type and by_type["manualStop"].motion is False
        assert "enableMotor" in by_type and by_type["enableMotor"].motion is True

    def test_jibot_instant_actions_include_stop_charging(self):
        # stopCharging is handled by the adapter (ends in-place/dock charge) but
        # absent from the factsheet; the WebUi may still send it. motion=False so
        # it is never confirm/busy-gated (a stop must always go through).
        from core.registry import _JIBOT_INSTANT_ACTIONS
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        assert "stopCharging" in by_type and by_type["stopCharging"].motion is False

    def test_jibot_instant_actions_include_goto_nearest(self):
        from core.registry import _JIBOT_INSTANT_ACTIONS
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        assert "gotoNearestNode" in by_type
        assert by_type["gotoNearestNode"].motion is True

    def test_jibot_instant_actions_include_clear_errors(self):
        # clearErrors clears sticky state.errors; the adapter already handles it
        # and the factsheet advertises it, but it must be registered so /action
        # accepts it and the WebUi renders a Clear-errors button. motion=False:
        # clearing the error list moves nothing, so it is never confirm-gated.
        from core.registry import _JIBOT_INSTANT_ACTIONS
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        assert "clearErrors" in by_type
        assert by_type["clearErrors"].motion is False

    def test_jibot_localize_offers_a_map_node_parameter(self):
        # `node` is the only localize input that is absolute AND survives a map
        # re-survey, so the WebUi must expose it next to the raw x/y/theta boxes.
        from core.registry import _JIBOT_INSTANT_ACTIONS
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        names = [p.name for p in by_type["localize"].parameters]
        assert "node" in names

    def test_jibot_rotate_to_offers_a_heading_parameter(self):
        # rotateTo is useless without a heading box: the WebUI cannot ask for an
        # arrival angle otherwise, and motion=True gates it behind the confirm
        # keystroke like every other command that turns the robot.
        from core.registry import _JIBOT_INSTANT_ACTIONS
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        assert by_type["rotateTo"].motion is True
        names = [p.name for p in by_type["rotateTo"].parameters]
        assert "thetaDeg" in names

    def test_jibot_spec_adds_configured_custom_actions(self):
        from config.config import ActionPluginConfig

        class Vehicle:
            serial_number = "HN"
            vda_full_version = "3.0.0"
            vda_version = "v3"
            manufacturer = "jibot"
            vehicle_ip = "10.0.0.11"
            vehicle_port = 7273

        class Broker:
            vda_interface = "amr"
            host = "127.0.0.1"
            port = 1883

        class Ezi:
            ezi_io = "10.8.8.87"
            tray_slot_pin = [8, 9, 10, 11, 12, 13]

        class Config:
            vehicle = Vehicle()
            mqtt_broker = Broker()
            ezi_config = Ezi()
            action_modules = []
            actions = [
                ActionPluginConfig(action_type="customDoorOpen", runner="subprocess"),
                ActionPluginConfig(action_type="customMove", motion=True),
            ]

        spec = registry._jibot_spec(
            Config(),
            key="jibot",
            display_name="JIBOT Adapter",
            unit="amr-adaptor.service",
        )

        actions = {a.action_type: a for a in spec.instant_actions}
        assert "customDoorOpen" in actions
        assert actions["customDoorOpen"].motion is False
        assert "customMove" in actions
        assert actions["customMove"].motion is True

    def test_jibot_spec_skips_configured_builtin_action_duplicate(self):
        from config.config import ActionPluginConfig

        class Vehicle:
            serial_number = "HN"
            vda_full_version = "3.0.0"
            vda_version = "v3"
            manufacturer = "jibot"
            vehicle_ip = "10.0.0.11"
            vehicle_port = 7273

        class Broker:
            vda_interface = "amr"
            host = "127.0.0.1"
            port = 1883

        class Ezi:
            ezi_io = "10.8.8.87"
            tray_slot_pin = [8, 9, 10, 11, 12, 13]

        class Config:
            vehicle = Vehicle()
            mqtt_broker = Broker()
            ezi_config = Ezi()
            action_modules = []
            actions = [
                ActionPluginConfig(action_type="manualDrive", runner="subprocess"),
                ActionPluginConfig(action_type="customDoorOpen", runner="subprocess"),
            ]

        spec = registry._jibot_spec(
            Config(),
            key="jibot",
            display_name="JIBOT Adapter",
            unit="amr-adaptor.service",
        )

        self.assertEqual(
            [a.action_type for a in spec.instant_actions].count("manualDrive"),
            1,
        )
        self.assertIn(
            "customDoorOpen",
            {action.action_type for action in spec.instant_actions},
        )

    def test_jibot_spec_hides_disabled_actions(self):
        from config.config import ActionPluginConfig

        class Vehicle:
            serial_number = "HN"
            vda_full_version = "3.0.0"
            vda_version = "v3"
            manufacturer = "jibot"
            vehicle_ip = "10.0.0.11"
            vehicle_port = 7273

        class Broker:
            vda_interface = "amr"
            host = "127.0.0.1"
            port = 1883

        class Ezi:
            ezi_io = "10.8.8.87"
            tray_slot_pin = [8, 9, 10, 11, 12, 13]

        class Config:
            vehicle = Vehicle()
            mqtt_broker = Broker()
            ezi_config = Ezi()
            action_modules = []
            actions = [
                ActionPluginConfig(action_type="clampMin", enabled=False),
                ActionPluginConfig(action_type="customDoorOpen", enabled=False),
                ActionPluginConfig(action_type="customMove", motion=True),
            ]

        spec = registry._jibot_spec(
            Config(),
            key="jibot",
            display_name="JIBOT Adapter",
            unit="amr-adaptor.service",
        )
        action_types = {action.action_type for action in spec.instant_actions}

        self.assertNotIn("clampMin", action_types)
        self.assertNotIn("customDoorOpen", action_types)
        self.assertIn("clampMax", action_types)
        self.assertIn("customMove", action_types)


if __name__ == "__main__":
    unittest.main()
