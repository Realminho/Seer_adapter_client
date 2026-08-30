import unittest
from config.config import get_config
from core.factsheet import ACTION_SCOPES, build_factsheet, INSTANT_ACTION_TYPES


class TestFactsheet(unittest.TestCase):
    def test_full_factsheet_matches_expected_structure(self):
        cfg = get_config()
        fs = build_factsheet(cfg, header_id=7, timestamp="T", simulation=False,
                             load_positions=["slot1", "slot2"], video_streams=None)
        expected = {
            "headerId": 7,
            "timestamp": "T",
            "version": cfg.vehicle.vda_full_version,
            "manufacturer": cfg.vehicle.manufacturer,
            "serialNumber": cfg.vehicle.serial_number,
            "coordinateUnits": {"position": "mm", "orientation": "deg"},
            "simulation": False,
            "typeSpecification": {
                "seriesName": "JIBOT", "agvKinematic": "DIFF", "agvClass": "CARRIER",
                "localizationTypes": ["NATURAL"], "navigationTypes": ["AUTONOMOUS"],
            },
            "physicalParameters": {"speedMin": 0.0, "speedMax": float(cfg.settings.speed)},
            "protocolLimits": {
                "maxStringLens": {}, "maxArrayLens": {},
                "timing": {"minOrderInterval": 1.0,
                           "minStateInterval": float(cfg.settings.state_publish_delay)},
            },
            "protocolFeatures": {
                "optionalParameters": [],
                "agvActions": (
                    [{"actionType": a, "actionScopes": ACTION_SCOPES.get(a, ["INSTANT"])}
                     for a in INSTANT_ACTION_TYPES]
                ),
            },
            "agvGeometry": {},
            "loadSpecification": {"loadPositions": ["slot1", "slot2"]},
        }
        self.assertEqual(fs, expected)

    def test_video_streams_injected_only_when_present(self):
        cfg = get_config()
        self.assertNotIn("videoStreams",
                         build_factsheet(cfg, header_id=0, timestamp="T",
                                         simulation=False, load_positions=[],
                                         video_streams=None))
        fs = build_factsheet(cfg, header_id=0, timestamp="T", simulation=False,
                             load_positions=[], video_streams=["http://x/s"])
        self.assertEqual(fs["videoStreams"], ["http://x/s"])

    def test_adapter_action_catalog_is_core_single_source(self):
        from adapter_jibot import Adapter
        from core.factsheet import INSTANT_ACTION_TYPES
        self.assertIs(Adapter.SUPPORTED_INSTANT_ACTIONS, INSTANT_ACTION_TYPES)

    def test_goto_nearest_node_is_advertised(self):
        from core.factsheet import INSTANT_ACTION_TYPES
        assert "gotoNearestNode" in INSTANT_ACTION_TYPES

    def test_switch_map_is_advertised_for_instant_and_node(self):
        cfg = get_config()
        factsheet = build_factsheet(
            cfg, header_id=0, timestamp="T", simulation=False, load_positions=[]
        )
        action = next(
            item for item in factsheet["protocolFeatures"]["agvActions"]
            if item["actionType"] == "switchMap"
        )
        self.assertEqual(action["actionScopes"], ["INSTANT", "NODE"])

    def test_factsheet_uses_supplied_instant_action_types(self):
        cfg = get_config()
        fs = build_factsheet(
            cfg,
            header_id=0,
            timestamp="T",
            simulation=False,
            load_positions=[],
            instant_action_types=("stateRequest", "customDoorOpen"),
        )
        actions = [
            action["actionType"]
            for action in fs["protocolFeatures"]["agvActions"]
            if action["actionScopes"] == ["INSTANT"]
        ]

        self.assertEqual(actions, ["stateRequest", "customDoorOpen"])

    def test_adapter_factsheet_reflects_registry_disabled_first_party_action(self):
        from adapter_jibot import Adapter
        from config.config import get_config

        cfg = get_config()
        cfg.actions.append(
            type(
                "Action",
                (),
                {
                    "action_type": "pioReadIn",
                    "enabled": False,
                    "runner": "inline",
                    "module": None,
                    "command": [],
                    "timeout_sec": 0,
                    "motion": False,
                    "snapshot_fields": [],
                },
            )()
        )
        adapter = Adapter(config=cfg)
        fs = adapter._build_factsheet()
        action_types = {
            action["actionType"]
            for action in fs["protocolFeatures"]["agvActions"]
        }

        self.assertNotIn("pioReadIn", action_types)
        self.assertIn("pioWriteOut", action_types)
        self.assertIn("clampMoveTo", action_types)

    def test_adapter_factsheet_reflects_disabled_photo_sensor_action(self):
        from adapter_jibot import Adapter
        from config.config import get_config

        cfg = get_config()
        cfg.actions.append(
            type(
                "Action",
                (),
                {
                    "action_type": "photoSensorRead",
                    "enabled": False,
                    "runner": "inline",
                    "module": None,
                    "command": [],
                    "timeout_sec": 0,
                    "motion": False,
                    "snapshot_fields": [],
                },
            )()
        )
        adapter = Adapter(config=cfg)
        fs = adapter._build_factsheet()
        action_types = {
            action["actionType"]
            for action in fs["protocolFeatures"]["agvActions"]
        }

        self.assertFalse(adapter._action_registry.has("photoSensorRead"))
        self.assertNotIn("photoSensorRead", action_types)
        self.assertIn("ezioReadIn", action_types)
