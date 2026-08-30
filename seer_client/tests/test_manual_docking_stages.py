from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "seer_client" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from seer_client.docking_action import (  # noqa: E402
    _manual_straight_heading_hold,
    _manual_yaw_only_command,
)


class ManualDockingStageTests(unittest.TestCase):
    def test_yaw_stage_is_translation_free_and_exactly_one_degree_per_second(self):
        speed = math.radians(1.0)
        cmd, done = _manual_yaw_only_command(
            math.radians(4.0), speed_rps=speed, tolerance_rad=math.radians(0.3)
        )
        self.assertFalse(done)
        self.assertEqual(cmd.v, 0.0)
        self.assertAlmostEqual(abs(cmd.omega), speed, places=10)

        cmd2, done2 = _manual_yaw_only_command(
            math.radians(0.2), speed_rps=speed, tolerance_rad=math.radians(0.3)
        )
        self.assertTrue(done2)
        self.assertEqual(cmd2.v, 0.0)
        self.assertEqual(cmd2.omega, 0.0)

    def test_straight_stage_uses_heading_hold_and_reports_cross_track(self):
        anchor = (1.0, 2.0, 0.0)
        current = (1.4, 2.012, math.radians(-1.0))
        cmd, heading_error, cross_track = _manual_straight_heading_hold(
            anchor,
            current,
            speed_mps=0.025,
            kp=0.8,
            deadband_rad=math.radians(0.05),
            limit_rps=math.radians(0.35),
        )
        self.assertAlmostEqual(cmd.v, 0.025)
        # Current heading is 1 deg clockwise from target, so positive/CCW w corrects it.
        self.assertGreater(cmd.omega, 0.0)
        self.assertAlmostEqual(cmd.omega, math.radians(0.35), places=9)
        self.assertAlmostEqual(math.degrees(heading_error), 1.0, places=7)
        self.assertAlmostEqual(cross_track, 0.012, places=7)

    def test_straight_stage_deadband_keeps_w_zero(self):
        cmd, _, _ = _manual_straight_heading_hold(
            (0.0, 0.0, math.radians(10.0)),
            (0.1, 0.0, math.radians(10.02)),
            speed_mps=0.025,
            kp=0.8,
            deadband_rad=math.radians(0.05),
            limit_rps=math.radians(0.35),
        )
        self.assertEqual(cmd.omega, 0.0)

    def test_webui_exposes_three_one_shot_stage_buttons(self):
        webui = (SRC / "seer_client" / "webui.py").read_text(encoding="utf-8")
        self.assertIn('name="stage_mode" value="centerline"', webui)
        self.assertIn('name="stage_mode" value="yaw"', webui)
        self.assertIn('name="stage_mode" value="straight"', webui)
        self.assertIn('① 회전중심 맞추기', webui)
        self.assertIn('② 각도 맞추기 · 1°/s', webui)
        self.assertIn('③ 그대로 직진 · IMU/SEER hold', webui)
        self.assertIn('dock-straight-cross', webui)
        self.assertIn('dock-straight-error', webui)


    def test_bridge_accepts_hidden_stage_mode_without_changing_normal_action_default(self):
        bridge = (SRC / "seer_client" / "bridge.py").read_text(encoding="utf-8")
        self.assertIn('stage_mode = str(value_or_default(context, "stage_mode", "")', bridge)
        self.assertIn('manual_stage=stage_mode or None', bridge)
        self.assertIn('runtime_default = 300.0', bridge)
        # Keep the generic seerCameraDock action a normal full-docking action;
        # the stage selector exists only in the dedicated Camera Docking page.
        dock_block = bridge[bridge.index('action_type="seerCameraDock"'):bridge.index('action_type="seerWait"')]
        self.assertNotIn('ActionParameterSpec(\n                        "stage_mode"', dock_block)

    def test_manual_stage_config_keeps_40mm_tag_and_live_300(self):
        cfg = json.loads((ROOT / "seer_client" / "config" / "docking.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(float(cfg["tag_size_m"]), 0.04)
        self.assertAlmostEqual(float(cfg["live_max_runtime_s"]), 300.0)
        stage = cfg["manual_stage"]
        self.assertAlmostEqual(float(stage["yaw_speed_deg_s"]), 1.0)
        self.assertAlmostEqual(float(stage["straight_heading_limit_deg_s"]), 0.35)
        self.assertAlmostEqual(float(stage["straight_stop_tag_depth_m"]), 0.50)
        self.assertAlmostEqual(float(stage["initial_pose_timeout_s"]), 12.0)
        self.assertAlmostEqual(float(stage["visual_command_hold_s"]), 0.50)
        self.assertAlmostEqual(float(stage["visual_reacquire_timeout_s"]), 3.0)

    def test_manual_stage_waits_for_first_pose_instead_of_infinite_gap_failure(self):
        docking = (SRC / "seer_client" / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn('manual_stage and not stage_pose_acquired_once', docking)
        self.assertIn('MANUAL_{manual_stage.upper()}_WAIT_POSE', docking)
        self.assertIn('waiting for initial tag/axis pose', docking)
        self.assertIn('manual stage could not acquire a valid tag/axis pose within', docking)
        # The old first-frame bug came from treating None as an infinite visual gap.
        # That expression may still exist for post-acquisition loss handling, but
        # it must now be preceded by the explicit initial-acquisition branch.
        self.assertLess(
            docking.index('manual_stage and not stage_pose_acquired_once'),
            docking.index('elif visual_gap_s > control_visual_grace_s'),
        )

    def test_manual_stage_tolerates_short_post_acquisition_pose_dropouts_without_motion_recovery(self):
        docking = (SRC / "seer_client" / "docking_action.py").read_text(encoding="utf-8")
        self.assertIn('stage_visual_command_hold_s', docking)
        self.assertIn('stage_visual_reacquire_timeout_s', docking)
        self.assertIn('holding last dead-man command', docking)
        self.assertIn('waiting stationary for reacquisition', docking)
        self.assertIn('MANUAL_{manual_stage.upper()}_REACQUIRE_POSE', docking)
        self.assertIn('automatic motion recovery disabled', docking)
        # The previous build failed immediately once the common ~0.2 s visual
        # grace elapsed.  Manual stages must now have a dedicated longer
        # dropout policy and must not request physical reverse/re-approach.
        manual_loss = docking.index('elif manual_stage and stage_pose_acquired_once:')
        auto_loss = docking.index('elif visual_gap_s > control_visual_grace_s:', manual_loss)
        self.assertLess(manual_loss, auto_loss)



if __name__ == "__main__":
    unittest.main()


def test_world_axis_lock_is_lidar_median_frozen_and_persisted():
    docking = (SRC / "seer_client" / "docking_action.py").read_text(encoding="utf-8")
    cfg = json.loads((ROOT / "seer_client" / "config" / "docking.json").read_text(encoding="utf-8"))
    world = cfg["pose_fusion"]["seer_localization"]["world_axis_lock"]
    assert world["enabled"] is True
    assert int(world["samples"]) >= 5
    assert "LIDAR_WALL+RGB_CENTER" in docking
    assert "anchor_pose_from_measurement" in docking
    assert "restore_anchor_pose(stable_anchor, frozen=True)" in docking
    assert "WORLD AXIS LOCKED" in docking
    assert "seer-docking-world-axis-lock.json" in docking


def test_yaw_and_straight_can_restore_the_same_persisted_world_axis():
    docking = (SRC / "seer_client" / "docking_action.py").read_text(encoding="utf-8")
    assert 'manual_stage in {"yaw", "straight"}' in docking
    assert "_load_world_axis_lock(" in docking
    assert "tag_localization_fusion.restore_anchor_pose(anchor_pose, frozen=True)" in docking
    assert "world_axis_lock_loaded = True" in docking


def test_full_straight_uses_locked_geometry_not_camera_du_for_steering():
    cfg = json.loads((ROOT / "seer_client" / "config" / "docking.json").read_text(encoding="utf-8"))
    servo = cfg["visual_servo"]
    assert float(servo["straight_heading_hold_gain"]) > 0.0
    assert math.isclose(float(servo["straight_heading_limit_rps"]), math.radians(0.35), rel_tol=1e-9)
    assert float(servo["straight_du_hold_gain"]) == 0.0
    assert float(servo["straight_du_hold_limit_rps"]) == 0.0
