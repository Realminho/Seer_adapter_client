from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src", "adaptor"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from core.factsheet import INSTANT_ACTION_TYPES  # pyright: ignore[reportMissingImports]
from seer_client.capabilities import (  # pyright: ignore[reportMissingImports]
    BLOCKED,
    CONDITIONAL,
    SIMULATOR_ONLY,
    SUPPORTED,
    advertised_action_types,
    capability_report,
    filter_factsheet_actions,
)


class _Registry:
    def __init__(self, actions):
        self._actions = tuple(actions)

    def action_types(self):
        return self._actions


class _Adapter:
    def __init__(self, *, simulator=False, registry=()):
        self._sim = simulator
        self._action_registry = _Registry(registry)
        self.config = SimpleNamespace(
            sound_settings=SimpleNamespace(enabled=False),
            video=SimpleNamespace(enabled=False),
        )

    def _is_simulator(self):
        return self._sim


class Week1CapabilityTests(unittest.TestCase):
    def test_catalog_covers_shared_factsheet_actions(self):
        report = {item.action_type: item for item in capability_report()}
        missing = sorted(set(INSTANT_ACTION_TYPES) - set(report))
        self.assertEqual(missing, [])
        self.assertEqual(report["manualDrive"].status, SUPPORTED)
        self.assertEqual(report["startCharging"].status, BLOCKED)
        self.assertEqual(report["setMap"].status, SIMULATOR_ONLY)
        self.assertEqual(report["testSound"].status, CONDITIONAL)

    def test_advertised_actions_exclude_vendor_blocked_and_include_registry(self):
        adapter = _Adapter(registry=("manualMove", "seerEmergencySwitch"))
        actions = set(advertised_action_types(adapter))
        self.assertIn("manualDrive", actions)
        self.assertIn("manualMove", actions)
        self.assertIn("seerEmergencySwitch", actions)
        self.assertNotIn("startCharging", actions)
        self.assertNotIn("jibotCommand", actions)
        self.assertNotIn("setMap", actions)


    def test_seer_path_nav_does_not_use_jibot_motor_auto_enable_guard(self):
        import asyncio
        import main as adapter_main
        from config.config import get_config
        from seer_client.bridge import (
            _disable_jibot_only_runtime_sources,
            install_into_adapter_main,
        )

        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter

        class FakeVehicle:
            def __init__(self):
                self._charging = False
                self._motor_flag = False
                self._mode = "auto"
                self._status = "Stopped"
                self.is_simulator = False
                self.calls = []

            def get_path_point_pose(self, _name):
                return None

            async def goto_point(self, target):
                self.calls.append(target)

        async def scenario():
            config = copy.deepcopy(get_config())
            _disable_jibot_only_runtime_sources(config)
            adapter = Adapter(config=config)
            vehicle = FakeVehicle()
            adapter._vehicle = vehicle
            node = SimpleNamespace(node_id="LM9", sequence_id=0, node_position=None)
            reason = await adapter._send_node_motion(node)
            self.assertIsNone(reason)
            self.assertEqual(vehicle.calls, ["LM9"])

        asyncio.run(scenario())

    def test_simulator_advertises_safe_in_memory_map_actions(self):
        actions = set(advertised_action_types(_Adapter(simulator=True)))
        self.assertIn("setMap", actions)
        self.assertIn("setMapSnapshot", actions)


    def test_runtime_adapter_factsheet_uses_filtered_seer_catalog(self):
        from config.config import get_config
        import main as adapter_main
        from seer_client.bridge import (
            _disable_jibot_only_runtime_sources,
            install_into_adapter_main,
        )

        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        self.assertFalse(config.settings.order_motor_auto_enable)
        self.assertGreaterEqual(config.manual_control.watchdog_ms, 2000)
        adapter = Adapter(config=config)
        adapter._vehicle = SimpleNamespace(is_simulator=True)
        actions = {
            item["actionType"]
            for item in adapter._build_factsheet()["protocolFeatures"]["agvActions"]
        }
        self.assertIn("seerSetDO", actions)
        self.assertIn("manualDrive", actions)
        self.assertNotIn("startCharging", actions)
        self.assertNotIn("jibotCommand", actions)

    def test_factsheet_filter_replaces_unsupported_shared_catalog(self):
        factsheet = {
            "protocolFeatures": {
                "agvActions": [{"actionType": "startCharging", "actionScopes": ["INSTANT"]}]
            }
        }
        result = filter_factsheet_actions(
            factsheet,
            _Adapter(registry=("seerSetDO",)),
            action_scopes={"switchMap": ["INSTANT", "NODE"]},
        )
        actions = {
            item["actionType"]: item["actionScopes"]
            for item in result["protocolFeatures"]["agvActions"]
        }
        self.assertNotIn("startCharging", actions)
        self.assertIn("seerSetDO", actions)
        self.assertEqual(actions["switchMap"], ["INSTANT", "NODE"])


if __name__ == "__main__":
    unittest.main()
