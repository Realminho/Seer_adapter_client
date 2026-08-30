import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import MotionRule, _motion_rule_from_dict, get_config


class MotionRuleConfigTest(unittest.TestCase):
    def test_dataclass_defaults(self):
        rule = MotionRule(to="X")
        self.assertEqual(rule.to, "X")
        self.assertEqual(rule.mode, "goto")
        self.assertIsNone(rule.from_node)
        self.assertIsNone(rule.approach)
        self.assertIsNone(rule.fail_timeout_sec)

    def test_from_key_maps_to_from_node(self):
        # TOML key `from` is readable but a Python keyword -> mapped to from_node.
        rule = _motion_rule_from_dict({"from": "A", "to": "B", "mode": "move"})
        self.assertEqual(rule.from_node, "A")
        self.assertEqual(rule.to, "B")
        self.assertEqual(rule.mode, "move")

    def test_move_rule_fields(self):
        rule = _motion_rule_from_dict(
            {
                "from": "A",
                "to": "B",
                "mode": "move",
                "distance": 2500,
                "speed": 200,
                "obs_avoid_dist": 1200,
                "side_avoid_dist": 80,
                "use_io": True,
                "note": 3,
            }
        )
        self.assertEqual(rule.distance, 2500)
        self.assertEqual(rule.speed, 200)
        self.assertEqual(rule.obs_avoid_dist, 1200)
        self.assertEqual(rule.side_avoid_dist, 80)
        self.assertTrue(rule.use_io)
        self.assertEqual(rule.note, 3)
        # absent move fields default to None (speed falls back at runtime)
        bare = MotionRule(to="B", mode="move", from_node="A")
        self.assertIsNone(bare.distance)
        self.assertIsNone(bare.speed)
        self.assertIsNone(bare.obs_avoid_dist)
        self.assertIsNone(bare.side_avoid_dist)

    def test_config_toml_dock_rule_is_loaded(self):
        # The deployed dock rule is a directional from->to segment: `from` is the
        # approach (_BEFORE) node, `to` is the charger. UmDock fires only when
        # arriving along from->to (see _dock_segment_rule). motion_rules now lives
        # in config.toml (fleet-common), not the jibot-config.toml overlay.
        rules = get_config().motion_rules
        dock = [r for r in rules if r.mode == "dock" and r.to == "F2_90_S2CH"]
        self.assertEqual(len(dock), 1)
        self.assertEqual(dock[0].from_node, "F2_90_S2CH_BEFORE")
        # No per-rule override -> runtime falls back to the global [dock]
        # fail_timeout_sec, which config.toml sets to 200.0.
        self.assertIsNone(dock[0].fail_timeout_sec)
        self.assertEqual(get_config().dock.fail_timeout_sec, 200.0)

    def test_hana_1_01ch_is_an_unconditional_dock_target(self):
        rules = get_config().motion_rules
        dock = [r for r in rules if r.mode == "dock" and r.to == "1_01CH"]
        self.assertEqual(len(dock), 1)
        # A destination whose map object is Dock always means UmDock. It is not
        # sent as UmGoto merely because the preceding path-point ID varies.
        self.assertIsNone(dock[0].from_node)


if __name__ == "__main__":
    unittest.main()
