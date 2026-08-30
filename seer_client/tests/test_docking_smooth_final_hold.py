from __future__ import annotations

import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from seer_docking.controller import DockPhase
from seer_docking.model import DockParams
from seer_docking.visual_servo import CameraVisualServoController, VisualDockObservation


ROOT = Path(__file__).resolve().parents[1]


def obs(*, depth: float, distance: float = 0.95, lateral: float = 0.0, yaw_deg: float = 0.0,
        v: float = 0.0, omega: float = 0.0) -> VisualDockObservation:
    return VisualDockObservation(
        base_distance_m=distance,
        goal_distance_m=0.93,
        du_px=0.0,
        target_du_px=0.0,
        marker_pitch_rad=0.0,
        target_pitch_rad=0.0,
        fx_px=600.0,
        tag_depth_m=depth,
        v=v,
        omega=omega,
        visible=True,
        angle_reliable=True,
        fused_lateral_m=lateral,
        fused_yaw_rad=math.radians(yaw_deg),
    )


class SmoothFinalDockingTests(unittest.TestCase):
    def setUp(self) -> None:
        cfg = json.loads((ROOT / "config" / "docking.json").read_text(encoding="utf-8"))
        self.cfg = cfg
        params = DockParams(**cfg["dock_params"])
        self.controller = CameraVisualServoController(
            params,
            **cfg["visual_servo"],
            camera_forward_m=0.375,
        )

    def call_at(self, t: float, observation: VisualDockObservation):
        with patch("seer_docking.visual_servo.time.monotonic", return_value=t):
            return self.controller.command(observation)

    def verify_centerline(self, start: float = 0.0, *, yaw_deg: float = 2.0) -> float:
        stable = obs(depth=0.78, distance=1.18, lateral=0.001, yaw_deg=yaw_deg)
        self.controller.phase = DockPhase.CENTERLINE_TURN
        self.call_at(start, stable)
        self.assertEqual(self.controller.phase, DockPhase.YAW_ALIGN)
        self.assertTrue(self.controller.centerline_verified)
        self.assertAlmostEqual(self.controller.centerline_hold_elapsed_s, 0.0, places=9)
        return start

    def verify_yaw(self, start: float, *, yaw_deg: float = 0.1) -> float:
        aligned = obs(depth=0.78, distance=1.18, lateral=0.001, yaw_deg=yaw_deg)
        self.controller.phase = DockPhase.YAW_ALIGN
        t = start
        for _ in range(self.controller.verification_window + 2):
            self.call_at(t, aligned)
            if self.controller.phase == DockPhase.YAW_HOLD:
                break
            t += 0.1
        self.assertEqual(self.controller.phase, DockPhase.YAW_HOLD)
        t += 0.1
        for _ in range(self.controller.yaw_lock_stable_steps):
            self.call_at(t, aligned)
            t += 0.1
        self.assertEqual(self.controller.phase, DockPhase.YAW_HOLD)
        self.call_at(t + self.controller.yaw_hold_s - 0.2, aligned)
        self.assertEqual(self.controller.phase, DockPhase.YAW_HOLD)
        t = t + self.controller.yaw_hold_s + 0.1
        self.call_at(t, aligned)
        self.assertEqual(self.controller.phase, DockPhase.STRAIGHT_APPROACH)
        self.assertTrue(self.controller.yaw_verified)
        self.assertTrue(self.controller.straight_axis_latched)
        return t

    def test_live_config_uses_immediate_centerline_one_degree_yaw_and_half_meter_goal(self):
        self.assertAlmostEqual(self.cfg["visual_servo"]["final_tag_depth_m"], 0.50, places=9)
        self.assertAlmostEqual(self.cfg["visual_servo"]["final_success_hold_s"], 3.0, places=9)
        self.assertAlmostEqual(self.cfg["visual_servo"]["centerline_hold_s"], 0.0, places=9)
        self.assertAlmostEqual(self.cfg["visual_servo"]["yaw_hold_s"], 3.0, places=9)
        self.assertAlmostEqual(self.cfg["visual_servo"]["yaw_align_speed_deg_s"], 1.0, places=9)
        self.assertAlmostEqual(self.cfg["live_max_runtime_s"], 300.0, places=9)
        self.assertLessEqual(self.cfg["dock_params"]["max_angular_rps"], 0.08)
        self.assertLessEqual(self.cfg["live_motion_smoothing"]["max_angular_accel_rps2"], 0.10)
        self.assertGreater(self.cfg["visual_servo"]["straight_heading_hold_gain"], 0.0)
        self.assertAlmostEqual(
            self.cfg["visual_servo"]["straight_heading_limit_rps"], math.radians(0.35), places=12
        )
        self.assertEqual(self.cfg["visual_servo"]["straight_du_hold_gain"], 0.0)
        self.assertAlmostEqual(self.cfg["seer"]["motion_command_hz"], 5.0, places=9)
        self.assertEqual(self.cfg["seer"]["duration_ms"], 450)
        self.assertLessEqual(self.cfg["seer"]["motion_ack_timeout_s"], 0.30)
        self.assertGreaterEqual(self.cfg["seer"]["max_consecutive_motion_ack_timeouts"], 2)

    def test_centerline_goes_directly_to_yaw_alignment_without_hold(self):
        self.verify_centerline()

    def test_yaw_alignment_command_is_exactly_one_degree_per_second(self):
        self.verify_centerline(yaw_deg=5.0)
        for i in range(self.controller.verification_window):
            cmd = self.call_at(0.1 + i * 0.1, obs(depth=0.78, distance=1.18, lateral=0.001, yaw_deg=5.0))
        self.assertEqual(self.controller.phase, DockPhase.YAW_ALIGN)
        self.assertAlmostEqual(abs(cmd.omega), math.radians(1.0), places=10)
        self.assertLess(cmd.omega, 0.0)

    def test_single_camera_axis_spike_does_not_knock_yaw_alignment_off_axis(self):
        stable = obs(depth=0.78, distance=1.18, lateral=0.001, yaw_deg=2.0)
        spike = obs(depth=0.78, distance=1.18, lateral=0.020, yaw_deg=2.0)
        self.verify_centerline(yaw_deg=2.0)
        # Build a robust history while yaw alignment is proceeding.
        for i in range(self.controller.verification_window):
            self.call_at(0.1 + i * 0.1, stable)
        self.assertEqual(self.controller.phase, DockPhase.YAW_ALIGN)
        self.call_at(1.0, spike)
        self.assertEqual(self.controller.phase, DockPhase.YAW_ALIGN)
        self.assertLess(abs(self.controller.verification_lateral_m or 0.0), self.controller.centerline_recenter_tolerance_m)

    def test_sustained_axis_error_during_yaw_returns_to_centerline_alignment(self):
        stable = obs(depth=0.78, distance=1.18, lateral=0.001, yaw_deg=2.0)
        bad = obs(depth=0.78, distance=1.18, lateral=0.020, yaw_deg=2.0)
        self.verify_centerline(yaw_deg=2.0)
        for i in range(self.controller.verification_window):
            self.call_at(0.1 + i * 0.1, stable)
        for i in range(self.controller.verification_window + 2):
            self.call_at(1.0 + i * 0.1, bad)
        self.assertEqual(self.controller.phase, DockPhase.CENTERLINE_TURN)
        self.assertFalse(self.controller.centerline_verified)

    def test_yaw_still_holds_for_three_seconds_after_immediate_centerline_lock(self):
        t = self.verify_centerline(yaw_deg=2.0)
        self.verify_yaw(t + 0.2, yaw_deg=0.1)

    def test_final_straight_leg_uses_only_tiny_locked_geometry_heading_hold(self):
        t = self.verify_centerline(yaw_deg=2.0)
        t = self.verify_yaw(t + 0.2, yaw_deg=0.1)
        noisy = obs(depth=0.60, distance=1.02, lateral=0.006, yaw_deg=1.5)
        cmd = self.call_at(t + 0.2, noisy)
        self.assertEqual(self.controller.phase, DockPhase.STRAIGHT_APPROACH)
        self.assertGreater(cmd.v, 0.0)
        self.assertLessEqual(abs(cmd.omega), math.radians(0.35) + 1e-12)
        self.assertNotEqual(cmd.omega, 0.0)

    def test_large_straight_drift_must_persist_before_realigning(self):
        t = self.verify_centerline(yaw_deg=2.0)
        t = self.verify_yaw(t + 0.2, yaw_deg=0.1)
        bad = obs(depth=0.60, distance=1.02, lateral=0.020, yaw_deg=4.0)
        cmd = self.call_at(t + 0.2, bad)
        self.assertEqual(self.controller.phase, DockPhase.STRAIGHT_APPROACH)
        self.assertLessEqual(abs(cmd.omega), math.radians(0.35) + 1e-12)
        tt = t + 0.3
        for _ in range(self.controller.verification_window + 2):
            cmd = self.call_at(tt, bad)
            tt += 0.1
        self.call_at(tt + self.controller.straight_drift_hold_s + 0.1, bad)
        self.assertIn(self.controller.phase, (DockPhase.CENTERLINE_TURN, DockPhase.YAW_ALIGN))
        self.assertFalse(self.controller.straight_axis_latched)

    def test_half_meter_final_hold_requires_centerline_lock_and_yaw_verification(self):
        close = obs(depth=0.515, distance=0.94, lateral=0.001, yaw_deg=0.1)
        self.controller.phase = DockPhase.CENTERLINE_DRIVE
        self.call_at(0.0, close)
        self.assertEqual(self.controller.phase, DockPhase.YAW_ALIGN)
        self.assertTrue(self.controller.centerline_verified)
        self.assertFalse(self.controller.straight_axis_latched)

        self.controller.reset()
        t = self.verify_centerline(yaw_deg=2.0)
        t = self.verify_yaw(t + 0.2, yaw_deg=0.1)
        cmd = self.call_at(t + 0.2, close)
        self.assertEqual(self.controller.phase, DockPhase.FINAL_HOLD)
        self.assertEqual((cmd.v, cmd.omega), (0.0, 0.0))

    def test_near_goal_fov_recovery_is_short_not_long_retreat(self):
        self.controller.phase = DockPhase.CENTERLINE_DRIVE
        o = obs(depth=0.62, distance=1.04, lateral=0.01, yaw_deg=2.0)
        self.controller.request_visibility_recovery(o)
        self.assertIn(self.controller.phase, (DockPhase.RECOVERY_ALIGN, DockPhase.RECOVERY_REVERSE))
        self.assertLessEqual(
            self.controller._recovery_target_m - o.base_distance_m,
            self.controller.near_goal_recovery_reverse_distance_m + 0.26,
        )


if __name__ == "__main__":
    unittest.main()
