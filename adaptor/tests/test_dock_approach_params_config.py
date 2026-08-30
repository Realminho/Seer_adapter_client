import unittest
from config.config import DockApproachParams, get_config


class TestDockApproachParams(unittest.TestCase):
    def test_defaults_are_all_none_and_as_params_empty(self):
        p = DockApproachParams()
        self.assertIsNone(p.detect_charging_signal)
        self.assertEqual(p.as_params(), {})

    def test_as_params_drops_none_keeps_set(self):
        p = DockApproachParams(detect_charging_signal=True, clearance_back_min=120.0)
        self.assertEqual(
            p.as_params(),
            {"detect_charging_signal": True, "clearance_back_min": 120.0},
        )

    def test_get_config_default_dock_has_empty_approach_params(self):
        cfg = get_config()
        self.assertEqual(cfg.dock.approach_params.as_params(), {})

    def test_motion_rule_carries_approach_params(self):
        from config.config import _motion_rule_from_dict
        rule = _motion_rule_from_dict({
            "to": "CH", "mode": "dock",
            "approach_params": {"detect_charging_signal": True},
        })
        self.assertEqual(rule.approach_params, {"detect_charging_signal": True})


class TestAdapterDockApproachResolver(unittest.TestCase):
    def _adapter(self, dock_params, rules):
        from types import SimpleNamespace
        from adapter_jibot import Adapter
        adapter = Adapter.__new__(Adapter)
        adapter.config = SimpleNamespace(
            dock=SimpleNamespace(approach_params=dock_params),
            motion_rules=rules,
        )
        return adapter

    def test_global_only(self):
        from config.config import DockApproachParams, MotionRule
        a = self._adapter(DockApproachParams(detect_charging_signal=True), [])
        self.assertEqual(a._dock_approach_params("CH"), {"detect_charging_signal": True})

    def test_rule_overrides_global(self):
        from config.config import DockApproachParams, MotionRule
        rule = MotionRule(to="CH", mode="dock",
                          approach_params={"detect_charging_signal": False})
        a = self._adapter(DockApproachParams(detect_charging_signal=True), [rule])
        self.assertEqual(a._dock_approach_params("CH"), {"detect_charging_signal": False})
