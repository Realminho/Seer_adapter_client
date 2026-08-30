"""Camera-based staged docking control used by simulation and the real AMR."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import time

import numpy as np

from .controller import DockPhase
from .geometry import transform_from_xyz_rpy, validate_transform, wrap_angle
from .model import DockParams, VelocityCommand, clamp
from .vision import marker_horizontal_pitch_deg


OBSERVATION_SCHEMA = "camera_du_pitch_reliability_recovery_v3"
ACTION_SCHEMA = "signed_linear_angular_v1"


@dataclass(slots=True)
class VisualTarget:
    """Expected camera measurements when the base is centred and straight."""

    du_px: float
    pitch_rad: float
    tag_depth_m: float


@dataclass(slots=True)
class VisualDockObservation:
    """Only the visual quantities needed by the docking policy/controller."""

    base_distance_m: float
    goal_distance_m: float
    du_px: float
    target_du_px: float
    marker_pitch_rad: float
    target_pitch_rad: float
    fx_px: float
    tag_depth_m: float
    v: float = 0.0
    omega: float = 0.0
    visible: bool = True
    angle_reliable: bool = True
    fov_margin_normalized: float = 0.0
    fused_lateral_m: float | None = None
    fused_yaw_rad: float | None = None
    # Coarse marker-normal geometry may be available before the pose is precise
    # enough for millimetre-class centreline control.  It is used only to aim
    # the chassis toward the centreline immediately at startup.
    coarse_lateral_m: float | None = None
    coarse_yaw_rad: float | None = None

    @property
    def remaining_m(self) -> float:
        return float(self.base_distance_m - self.goal_distance_m)

    @property
    def du_error_px(self) -> float:
        return float(self.du_px - self.target_du_px)

    @property
    def bearing_error_rad(self) -> float:
        return math.atan2(self.du_error_px, max(1.0, float(self.fx_px)))

    @property
    def pitch_error_rad(self) -> float:
        return wrap_angle(self.marker_pitch_rad - self.target_pitch_rad)


def visual_target_for_base_distance(
    t_base_camera: np.ndarray,
    t_marker_dock: np.ndarray,
    camera_matrix: np.ndarray,
    base_distance_m: float,
) -> VisualTarget:
    """Project the centred/yaw-zero target for the configured camera offset.

    A non-zero camera x/y/yaw offset means camera optical centre and marker
    centre generally should *not* coincide.  The target du is consequently
    recomputed for the current approach distance.
    """

    t_b_c = validate_transform(t_base_camera)
    t_m_d = validate_transform(t_marker_dock)
    k = np.asarray(camera_matrix, dtype=float)
    if k.shape != (3, 3):
        raise ValueError("camera_matrix must be 3x3")
    t_d_b = transform_from_xyz_rpy(
        [float(base_distance_m), 0.0, 0.0],
        [0.0, 0.0, math.pi],
    )
    t_m_b = t_m_d @ t_d_b
    t_c_m = np.linalg.inv(t_b_c) @ np.linalg.inv(t_m_b)
    x, _, z = [float(value) for value in t_c_m[:3, 3]]
    if z <= 1e-6:
        raise ValueError("configured centred docking pose places marker behind camera")
    du_px = float(k[0, 0]) * x / z
    return VisualTarget(
        du_px=du_px,
        pitch_rad=math.radians(marker_horizontal_pitch_deg(t_c_m)),
        tag_depth_m=z,
    )


class CameraVisualServoController:
    """Camera docking controller with a strict geometric phase sequence.

    Normal docking uses the camera-derived pose of the AMR *rotation centre*
    in the dock frame. The sequence is deliberately decoupled:

    1. move the AMR rotation centre onto the tag/dock centreline (y=0),
    2. stop and rotate in place until body yaw=0,
    3. drive straight to the requested standoff.

    The previous controller continuously blended DU centring, intercept path
    tracking and final yaw correction. That could work, but it also meant the
    robot kept changing translation and heading at the same time. This version
    makes each geometric objective explicit and restarts an earlier phase when
    drift exceeds tolerance instead of curving during the final leg.

    ``fused_lateral_m`` / ``fused_yaw_rad`` are expected to come from the
    marker-centre NORMAL AXIS -> AMR rotation-centre geometry, temporally
    smoothed by the wheel/IMU EKF.  In the default live-style configuration
    camera DU is never used to define the physical docking centreline. Before
    a reliable marker normal is available it is retained for visibility/FOV
    protection; during the final precision leg only the offset-corrected DU
    residual receives a small secondary trim toward zero.
    """

    def __init__(
        self,
        params: DockParams | None = None,
        du_bearing_gain: float = 2.4,
        pitch_gain: float = 1.65,
        distance_gain: float = 0.65,
        du_tolerance_px: float = 10.0,
        pitch_tolerance_deg: float = 1.0,
        command_deadband_px: float = 1.0,
        command_deadband_deg: float = 0.25,
        camera_forward_m: float = 0.0,
        camera_left_m: float = 0.0,
        camera_yaw_rad: float = 0.0,
        angle_required_base_distance_m: float = 0.95,
        centerline_tolerance_m: float = 0.002,
        centerline_recenter_tolerance_m: float = 0.006,
        centerline_max_heading_deg: float = 35.0,
        centerline_min_run_m: float = 0.15,
        centerline_final_clearance_m: float = 0.18,
        centerline_turn_gain: float = 2.2,
        centerline_drive_gain: float = 0.80,
        centerline_heading_gain: float = 1.25,
        centerline_heading_limit_rps: float = 0.06,
        centerline_realign_heading_deg: float = 9.0,
        yaw_align_gain: float = 2.4,
        yaw_align_speed_deg_s: float = 1.0,
        straight_realign_yaw_deg: float = 0.75,
        straight_heading_hold_gain: float = 1.4,
        straight_heading_limit_rps: float = 0.02,
        straight_du_hold_gain: float = 1.15,
        straight_du_hold_limit_rps: float = 0.018,
        straight_du_slowdown_start_px: float = 5.0,
        recovery_enabled: bool = True,
        recovery_reverse_distance_m: float = 0.75,
        visibility_recovery_reverse_distance_m: float = 0.40,
        recovery_max_standoff_m: float = 1.90,
        recovery_reverse_speed_mps: float = 0.07,
        recovery_max_attempts: int = 3,
        recovery_fov_trigger_normalized: float = -0.70,
        require_marker_axis_pose: bool = True,
        allow_du_steering_before_axis_lock: bool = False,
        coarse_axis_start_enabled: bool = True,
        coarse_axis_turn_gain: float = 1.8,
        coarse_axis_turn_limit_rps: float = 0.10,
        coarse_axis_heading_tolerance_deg: float = 5.0,
        visibility_acquire_turn_gain: float = 1.2,
        visibility_acquire_turn_limit_rps: float = 0.08,
        far_fov_no_reverse_distance_m: float = 1.80,
        final_tag_depth_m: float = 0.50,
        final_tag_depth_tolerance_m: float = 0.03,
        final_axis_gate_depth_m: float = 0.70,
        centerline_lock_stable_steps: int = 4,
        yaw_lock_stable_steps: int = 4,
        centerline_hold_s: float = 3.0,
        yaw_hold_s: float = 3.0,
        verification_window: int = 7,
        straight_drift_lateral_stop_m: float = 0.012,
        straight_drift_yaw_stop_deg: float = 3.0,
        straight_drift_hold_s: float = 0.60,
        near_goal_recovery_reverse_distance_m: float = 0.22,
        final_success_hold_s: float = 3.0,
    ) -> None:
        self.params = params or DockParams()
        self.du_bearing_gain = float(du_bearing_gain)
        self.pitch_gain = float(pitch_gain)  # retained for config compatibility
        self.distance_gain = float(distance_gain)
        self.du_tolerance_px = max(0.1, float(du_tolerance_px))
        self.pitch_tolerance_rad = math.radians(max(0.05, float(pitch_tolerance_deg)))
        self.command_deadband_px = max(0.0, float(command_deadband_px))
        self.command_deadband_rad = math.radians(max(0.0, float(command_deadband_deg)))
        self.camera_forward_m = float(camera_forward_m)
        self.camera_left_m = float(camera_left_m)
        self.camera_yaw_rad = float(camera_yaw_rad)
        self.angle_required_base_distance_m = max(
            self.params.goal_standoff_m,
            float(angle_required_base_distance_m),
        )

        self.centerline_tolerance_m = max(
            self.params.lateral_tolerance_m,
            float(centerline_tolerance_m),
        )
        # Aim tighter than the acceptance band so camera noise and braking
        # transients do not leave the physical rotation centre sitting right
        # on the 2 mm boundary.
        # 실물 로그(2026-08-27)에서 1 mm 단일-frame lock이 센서 잡음에
        # 의해 TURN/DRIVE 전환을 반복시켰다. 허용 오차 안에서 여러 frame
        # 연속으로 안정된 경우에만 다음 단계로 넘어가도록 한다.
        self.centerline_lock_tolerance_m = min(
            self.centerline_tolerance_m,
            max(0.0015, self.params.lateral_tolerance_m),
        )
        self.yaw_lock_tolerance_rad = min(
            self.params.yaw_tolerance_rad,
            math.radians(0.30),
        )
        self.centerline_recenter_tolerance_m = max(
            self.centerline_tolerance_m + 0.004,
            float(centerline_recenter_tolerance_m),
        )
        self.centerline_max_heading_rad = math.radians(
            float(np.clip(centerline_max_heading_deg, 8.0, 70.0))
        )
        self.centerline_min_run_m = max(0.05, float(centerline_min_run_m))
        self.centerline_final_clearance_m = max(
            self.params.distance_tolerance_m + 0.05,
            float(centerline_final_clearance_m),
        )
        self.centerline_turn_gain = max(0.1, float(centerline_turn_gain))
        self.centerline_drive_gain = max(0.05, float(centerline_drive_gain))
        self.centerline_heading_gain = max(0.0, float(centerline_heading_gain))
        self.centerline_heading_limit_rps = max(0.0, float(centerline_heading_limit_rps))
        self.centerline_realign_heading_rad = math.radians(
            max(1.0, float(centerline_realign_heading_deg))
        )
        self.yaw_align_gain = max(0.1, float(yaw_align_gain))  # retained for config compatibility
        self.yaw_align_speed_rps = min(
            self.params.max_angular_rps,
            math.radians(max(0.1, float(yaw_align_speed_deg_s))),
        )
        self.straight_realign_yaw_rad = math.radians(
            max(math.degrees(self.params.yaw_tolerance_rad), float(straight_realign_yaw_deg))
        )
        self.straight_heading_hold_gain = max(0.0, float(straight_heading_hold_gain))
        self.straight_heading_limit_rps = max(0.0, float(straight_heading_limit_rps))
        self.straight_du_hold_gain = max(0.0, float(straight_du_hold_gain))
        self.straight_du_hold_limit_rps = max(
            0.0, min(float(straight_du_hold_limit_rps), self.params.max_angular_rps)
        )
        self.straight_du_slowdown_start_px = max(
            0.5, min(float(straight_du_slowdown_start_px), self.du_tolerance_px)
        )

        self.recovery_enabled = bool(recovery_enabled)
        self.recovery_reverse_distance_m = max(0.15, float(recovery_reverse_distance_m))
        self.visibility_recovery_reverse_distance_m = max(
            0.20,
            float(visibility_recovery_reverse_distance_m),
        )
        self.recovery_max_standoff_m = max(
            self.params.staging_standoff_m + 0.10,
            float(recovery_max_standoff_m),
        )
        self.recovery_reverse_speed_mps = max(
            0.005,
            min(float(recovery_reverse_speed_mps), self.params.max_linear_mps),
        )
        self.recovery_max_attempts = max(0, int(recovery_max_attempts))
        self.recovery_fov_trigger_normalized = float(
            np.clip(recovery_fov_trigger_normalized, -1.0, 1.0)
        )
        self.require_marker_axis_pose = bool(require_marker_axis_pose)
        self.allow_du_steering_before_axis_lock = bool(allow_du_steering_before_axis_lock)
        self.coarse_axis_start_enabled = bool(coarse_axis_start_enabled)
        self.coarse_axis_turn_gain = max(0.1, float(coarse_axis_turn_gain))
        self.coarse_axis_turn_limit_rps = max(0.02, min(float(coarse_axis_turn_limit_rps), self.params.max_angular_rps))
        self.coarse_axis_heading_tolerance_rad = math.radians(max(1.0, float(coarse_axis_heading_tolerance_deg)))
        self.visibility_acquire_turn_gain = max(0.1, float(visibility_acquire_turn_gain))
        self.visibility_acquire_turn_limit_rps = max(0.02, min(float(visibility_acquire_turn_limit_rps), self.params.max_angular_rps))
        self.far_fov_no_reverse_distance_m = max(self.params.staging_standoff_m + 0.20, float(far_fov_no_reverse_distance_m))
        self.final_tag_depth_m = max(0.12, float(final_tag_depth_m))
        self.final_tag_depth_tolerance_m = max(0.01, float(final_tag_depth_tolerance_m))
        self.final_axis_gate_depth_m = max(
            self.final_tag_depth_m + self.final_tag_depth_tolerance_m + 0.05,
            float(final_axis_gate_depth_m),
        )
        self.centerline_lock_stable_steps = max(1, int(centerline_lock_stable_steps))
        self.yaw_lock_stable_steps = max(1, int(yaw_lock_stable_steps))
        self.centerline_hold_s = max(0.0, float(centerline_hold_s))
        self.yaw_hold_s = max(0.5, float(yaw_hold_s))
        self.verification_window = max(3, int(verification_window))
        if self.verification_window % 2 == 0:
            self.verification_window += 1
        self.straight_drift_lateral_stop_m = max(
            self.centerline_recenter_tolerance_m, float(straight_drift_lateral_stop_m)
        )
        self.straight_drift_yaw_stop_rad = math.radians(max(1.0, float(straight_drift_yaw_stop_deg)))
        self.straight_drift_hold_s = max(0.20, float(straight_drift_hold_s))
        self.near_goal_recovery_reverse_distance_m = max(0.12, float(near_goal_recovery_reverse_distance_m))
        self.final_success_hold_s = max(0.5, float(final_success_hold_s))

        self.phase = DockPhase.CENTERLINE_TURN
        self._success_count = 0
        self._centerline_lock_count = 0
        self._yaw_lock_count = 0
        self._centerline_hold_started_at: float | None = None
        self._yaw_hold_started_at: float | None = None
        self._straight_drift_started_at: float | None = None
        self._centerline_verified = False
        self._yaw_verified = False
        self._straight_axis_latched = False
        self._straight_latched_lateral_m: float | None = None
        self._straight_latched_yaw_rad: float | None = None
        self._verification_lateral_history: deque[float] = deque(maxlen=self.verification_window)
        self._verification_yaw_history: deque[float] = deque(maxlen=self.verification_window)
        self._verification_lateral_m: float | None = None
        self._verification_yaw_rad: float | None = None
        self._angle_was_reliable = True
        self._centerline_target_m: float | None = None
        self._recovery_attempts = 0
        self._recovery_target_m = self.params.staging_standoff_m
        self._recovery_reason = "none"
        self._precision_settle_steps = 0
        self._last_straight_du_abs_px: float | None = None

    def reset(self) -> None:
        self.phase = DockPhase.CENTERLINE_TURN
        self._success_count = 0
        self._centerline_lock_count = 0
        self._yaw_lock_count = 0
        self._centerline_hold_started_at = None
        self._yaw_hold_started_at = None
        self._straight_drift_started_at = None
        self._centerline_verified = False
        self._yaw_verified = False
        self._straight_axis_latched = False
        self._straight_latched_lateral_m = None
        self._straight_latched_yaw_rad = None
        self._verification_lateral_history.clear()
        self._verification_yaw_history.clear()
        self._verification_lateral_m = None
        self._verification_yaw_rad = None
        self._angle_was_reliable = True
        self._centerline_target_m = None
        self._recovery_attempts = 0
        self._recovery_target_m = self.params.staging_standoff_m
        self._recovery_reason = "none"
        self._precision_settle_steps = 0
        self._last_straight_du_abs_px = None

    def abort(self) -> VelocityCommand:
        self.phase = DockPhase.ABORTED
        return VelocityCommand(0.0, 0.0)

    @property
    def centerline_hold_elapsed_s(self) -> float:
        if self._centerline_hold_started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._centerline_hold_started_at)

    @property
    def yaw_hold_elapsed_s(self) -> float:
        if self._yaw_hold_started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._yaw_hold_started_at)

    @property
    def verification_lateral_m(self) -> float | None:
        return self._verification_lateral_m

    @property
    def verification_yaw_rad(self) -> float | None:
        return self._verification_yaw_rad

    @property
    def centerline_verified(self) -> bool:
        return bool(self._centerline_verified)

    @property
    def yaw_verified(self) -> bool:
        return bool(self._yaw_verified)

    @property
    def straight_axis_latched(self) -> bool:
        return bool(self._straight_axis_latched)

    def _clear_verification_pose(self) -> None:
        self._verification_lateral_history.clear()
        self._verification_yaw_history.clear()
        self._verification_lateral_m = None
        self._verification_yaw_rad = None

    def _reset_alignment_holds(self, *, clear_straight_latch: bool = True) -> None:
        self._centerline_lock_count = 0
        self._yaw_lock_count = 0
        self._centerline_hold_started_at = None
        self._yaw_hold_started_at = None
        self._straight_drift_started_at = None
        if clear_straight_latch:
            self._centerline_verified = False
            self._yaw_verified = False
            self._straight_axis_latched = False
            self._straight_latched_lateral_m = None
            self._straight_latched_yaw_rad = None

    def _update_verification_pose(self, lateral: float, yaw: float) -> tuple[float, float]:
        """Robust pose used only for phase verification, never active steering.

        The live camera/LiDAR fusion can jump a few millimetres for one frame.
        A short median window prevents that single sample from knocking an AMR
        that is physically stationary off the verified marker centre axis.
        """

        lateral = float(lateral)
        yaw = wrap_angle(float(yaw))
        self._verification_lateral_history.append(lateral)
        self._verification_yaw_history.append(yaw)
        self._verification_lateral_m = float(np.median(np.asarray(self._verification_lateral_history, dtype=float)))
        yaw_ref = yaw
        yaw_values = np.asarray(
            [yaw_ref + wrap_angle(float(value) - yaw_ref) for value in self._verification_yaw_history],
            dtype=float,
        )
        self._verification_yaw_rad = wrap_angle(float(np.median(yaw_values)))
        return self._verification_lateral_m, self._verification_yaw_rad

    @staticmethod
    def _estimated_yaw(observation: VisualDockObservation) -> float:
        return wrap_angle(-observation.pitch_error_rad)

    def _estimated_lateral(self, observation: VisualDockObservation) -> float:
        """Fallback base-centre lateral estimate from DU/pitch + mount offset."""

        yaw = self._estimated_yaw(observation)
        bearing = math.atan2(observation.du_px, max(1.0, observation.fx_px))
        camera_x = (
            observation.base_distance_m
            - self.camera_forward_m * math.cos(yaw)
            + self.camera_left_m * math.sin(yaw)
        )
        ray_direction = math.pi + yaw + self.camera_yaw_rad - bearing
        camera_y = camera_x * math.tan(ray_direction)
        return (
            camera_y
            + self.camera_forward_m * math.sin(yaw)
            + self.camera_left_m * math.cos(yaw)
        )

    def _control_pose(self, observation: VisualDockObservation) -> tuple[float, float]:
        """Return centreline error y and yaw error used by the controller."""

        if observation.fused_lateral_m is not None and observation.fused_yaw_rad is not None:
            return (
                float(observation.fused_lateral_m),
                wrap_angle(float(observation.fused_yaw_rad)),
            )
        if self.require_marker_axis_pose:
            raise RuntimeError("marker-axis pose is required for geometric docking")
        return self._estimated_lateral(observation), self._estimated_yaw(observation)

    def _plan_centerline_target(
        self,
        observation: VisualDockObservation,
        lateral: float,
    ) -> bool:
        """Latch the earliest safe intercept on the tag centreline.

        The previous planner capped the intercept at ``staging_standoff_m``.
        That made the AMR spend the first part of a long approach on a shallow
        heading and then appear to "turn onto the axis" only near a particular
        distance.  We instead choose the nearest reachable point on y=0 from
        the *current* camera-derived pose, subject only to the configured
        maximum intercept heading and the final-clearance reserve.  Coarse and
        precise PnP share this same latched target, so there is no distance-
        triggered replanning kink when precise axis pose becomes available.
        """

        p = self.params
        min_target = p.goal_standoff_m + self.centerline_final_clearance_m
        if observation.base_distance_m <= min_target + 0.03:
            return False

        required_dx = max(
            self.centerline_min_run_m,
            abs(lateral)
            / max(math.tan(self.centerline_max_heading_rad), 1e-6)
            * 1.10,
        )
        target = max(min_target, observation.base_distance_m - required_dx)
        dx = observation.base_distance_m - target
        if dx <= 0.05:
            return False
        desired = math.atan2(lateral, dx)
        if abs(desired) > self.centerline_max_heading_rad + math.radians(2.0):
            return False
        self._centerline_target_m = target
        return True

    def _startup_centerline_heading(
        self,
        observation: VisualDockObservation,
        lateral: float,
    ) -> float:
        """Use the same latched centreline target from the first usable PnP frame."""

        if self._centerline_target_m is None:
            if not self._plan_centerline_target(observation, lateral):
                return 0.0
        assert self._centerline_target_m is not None
        dx = max(observation.base_distance_m - self._centerline_target_m, 1e-6)
        return float(
            np.clip(
                math.atan2(lateral, dx),
                -self.centerline_max_heading_rad,
                self.centerline_max_heading_rad,
            )
        )

    def _centerline_heading(
        self,
        observation: VisualDockObservation,
        lateral: float,
    ) -> float:
        if self._centerline_target_m is None:
            if not self._plan_centerline_target(observation, lateral):
                return 0.0
        assert self._centerline_target_m is not None
        dx = max(observation.base_distance_m - self._centerline_target_m, 1e-6)
        return math.atan2(lateral, dx)

    def _curve_with_fov_priority(
        self,
        observation: VisualDockObservation,
        path_v: float,
        path_omega: float,
    ) -> VelocityCommand:
        """FOV assistance used only while repositioning, never as final docking law."""

        p = self.params
        edge_weight = float(
            np.clip((-0.25 - observation.fov_margin_normalized) / 0.65, 0.0, 1.0)
        )
        centre_omega = clamp(
            -self.du_bearing_gain * observation.bearing_error_rad,
            -p.max_angular_rps,
            p.max_angular_rps,
        )
        omega = (1.0 - edge_weight) * path_omega + edge_weight * centre_omega
        velocity_scale = 1.0 - 0.80 * edge_weight
        return VelocityCommand(
            path_v * velocity_scale,
            clamp(omega, -p.max_angular_rps, p.max_angular_rps),
        )

    def _begin_recovery(
        self,
        observation: VisualDockObservation,
        *,
        reverse_distance_m: float | None = None,
        reason: str = "geometry",
    ) -> VelocityCommand:
        """Create longitudinal room, then restart centreline alignment.

        Recovery is intentionally straight: it never treats DU as a docking
        alignment target.  A shorter retreat is used when the predictive FOV
        shield would otherwise park the robot at the image edge.
        """

        if not self.recovery_enabled or self._recovery_attempts >= self.recovery_max_attempts:
            return self.abort()
        self._recovery_attempts += 1
        retreat = (
            self.recovery_reverse_distance_m
            if reverse_distance_m is None
            else max(0.12, float(reverse_distance_m))
        )
        # 최종 접근 근처에서는 긴 recovery가 도킹을 반복시키므로
        # 정렬 공간만 짧게 만든다.
        if observation.tag_depth_m <= self.final_axis_gate_depth_m + 0.08:
            retreat = min(retreat, self.near_goal_recovery_reverse_distance_m)
        requested = max(
            self.params.staging_standoff_m + 0.25,
            observation.base_distance_m + retreat,
        )
        self._recovery_target_m = min(self.recovery_max_standoff_m, requested)
        if self._recovery_target_m <= observation.base_distance_m + 0.08:
            return self.abort()
        self._success_count = 0
        self._centerline_target_m = None
        self._reset_alignment_holds(clear_straight_latch=True)
        self._clear_verification_pose()
        self._recovery_reason = str(reason)
        self.phase = DockPhase.RECOVERY_REVERSE
        return VelocityCommand(0.0, 0.0)

    def request_visibility_recovery(
        self,
        observation: VisualDockObservation,
    ) -> VelocityCommand:
        """Escape a predictive FOV deadlock by facing the dock, then retreating.

        The outer FOV guard calls this only when it cannot execute the requested
        geometric docking command without losing the tag.  First the base turns
        in place toward marker-axis yaw=0, which normally pulls the tag away from
        the image edge without using DU as a docking objective.  Then it backs
        away at omega=0 and restarts normal centreline docking from scratch.
        """

        # When the robot is already far from the tag, reversing farther is the
        # wrong recovery and previously caused an immediate ABORT because the
        # reverse target was capped below the current distance.  At long range
        # use a small in-place camera-visibility turn only.  DU is used here as
        # an emergency FOV signal, never as the docking-centre target.
        if observation.base_distance_m >= self.far_fov_no_reverse_distance_m:
            bearing = observation.bearing_error_rad
            if abs(bearing) <= math.radians(1.0):
                return VelocityCommand(0.0, 0.0)
            self._recovery_reason = "far-fov-visibility-turn"
            return VelocityCommand(
                0.0,
                clamp(
                    -self.visibility_acquire_turn_gain * bearing,
                    -self.visibility_acquire_turn_limit_rps,
                    self.visibility_acquire_turn_limit_rps,
                ),
            )

        if self.phase in (DockPhase.RECOVERY_ALIGN, DockPhase.RECOVERY_REVERSE):
            return VelocityCommand(0.0, 0.0)
        # 태그가 가까우면 긴 FOV recovery 대신 짧은 직선 후퇴만 허용한다.
        if observation.tag_depth_m <= self.final_axis_gate_depth_m + 0.08:
            return self._begin_recovery(
                observation,
                reverse_distance_m=self.near_goal_recovery_reverse_distance_m,
                reason="near-goal-fov",
            )
        if not self.recovery_enabled or self._recovery_attempts >= self.recovery_max_attempts:
            return self.abort()
        self._recovery_attempts += 1
        requested = max(
            self.params.staging_standoff_m + 0.25,
            observation.base_distance_m + self.visibility_recovery_reverse_distance_m,
        )
        self._recovery_target_m = min(self.recovery_max_standoff_m, requested)
        if self._recovery_target_m <= observation.base_distance_m + 0.08:
            return self.abort()
        self._success_count = 0
        self._centerline_target_m = None
        self._reset_alignment_holds(clear_straight_latch=True)
        self._clear_verification_pose()
        self._recovery_reason = "fov-predictive-stall"
        self.phase = DockPhase.RECOVERY_ALIGN
        return VelocityCommand(0.0, 0.0)

    def command(self, observation: VisualDockObservation) -> VelocityCommand:
        if self.phase in (DockPhase.DOCKED, DockPhase.ABORTED):
            return VelocityCommand(0.0, 0.0)
        if not observation.visible:
            self._success_count = 0
            return VelocityCommand(0.0, 0.0)

        p = self.params
        remaining = observation.remaining_m
        du_error = observation.du_error_px
        bearing_error = observation.bearing_error_rad

        # A usable axis pose means we already know the marker-centre NORMAL
        # axis relative to the AMR rotation centre.  Once this has been locked,
        # a brief per-frame angle-reliability drop can be bridged by the EKF.
        axis_pose_available = bool(
            observation.fused_lateral_m is not None
            and observation.fused_yaw_rad is not None
        )
        axis_pose_usable = bool(
            (axis_pose_available and (observation.angle_reliable or self._angle_was_reliable))
            or (not self.require_marker_axis_pose and observation.angle_reliable)
        )

        if self.phase in (DockPhase.RECOVERY_ALIGN, DockPhase.RECOVERY_REVERSE) and not axis_pose_usable:
            return self.abort()

        if not axis_pose_usable:
            self._angle_was_reliable = False
            self._success_count = 0
            self._centerline_target_m = None
            self._reset_alignment_holds(clear_straight_latch=True)
            self._clear_verification_pose()
            self.phase = DockPhase.VISION_APPROACH

            coarse_axis_available = bool(
                self.coarse_axis_start_enabled
                and observation.coarse_lateral_m is not None
                and observation.coarse_yaw_rad is not None
            )
            if coarse_axis_available:
                # Start by pointing toward the marker NORMAL centreline as soon
                # as coarse PnP is available.  Translation stays stopped during
                # this turn.  The coarse pose is never used for the final 2 mm
                # lock; precise PnP/EKF takes over as soon as it is available.
                coarse_lateral = float(observation.coarse_lateral_m)
                coarse_yaw = wrap_angle(float(observation.coarse_yaw_rad))
                desired = self._startup_centerline_heading(observation, coarse_lateral)
                heading_error = wrap_angle(desired - coarse_yaw)
                if abs(heading_error) > self.coarse_axis_heading_tolerance_rad:
                    return VelocityCommand(
                        0.0,
                        clamp(
                            self.coarse_axis_turn_gain * heading_error,
                            -self.coarse_axis_turn_limit_rps,
                            self.coarse_axis_turn_limit_rps,
                        ),
                    )
                # Keep following the SAME centreline intercept from startup.
                # A small heading hold is allowed while the marker grows; when
                # precise PnP locks, the target is carried across unchanged.
                if observation.base_distance_m <= self.angle_required_base_distance_m:
                    return VelocityCommand(0.0, 0.0)
                approach_remaining = max(
                    0.0, observation.base_distance_m - self.angle_required_base_distance_m
                )
                v = min(p.max_linear_mps, self.distance_gain * approach_remaining)
                v *= max(0.25, math.cos(heading_error))
                omega = clamp(
                    self.centerline_heading_gain * heading_error,
                    -min(self.coarse_axis_turn_limit_rps, self.centerline_heading_limit_rps),
                    min(self.coarse_axis_turn_limit_rps, self.centerline_heading_limit_rps),
                )
                return VelocityCommand(v, omega)

            # If even coarse marker-normal geometry is not usable yet, DU may
            # only be used as a visibility-acquisition signal.  Previously a
            # tag beyond 25 deg simply produced v=0,w=0 forever.  Now the base
            # gently turns in place to keep/reacquire the tag, then coarse/
            # precise marker-axis control takes over.
            if self.require_marker_axis_pose:
                if abs(bearing_error) > math.radians(25.0):
                    return VelocityCommand(
                        0.0,
                        clamp(
                            -self.visibility_acquire_turn_gain * bearing_error,
                            -self.visibility_acquire_turn_limit_rps,
                            self.visibility_acquire_turn_limit_rps,
                        ),
                    )
                if observation.base_distance_m <= self.angle_required_base_distance_m:
                    return VelocityCommand(0.0, 0.0)
                approach_remaining = max(
                    0.0,
                    observation.base_distance_m - self.angle_required_base_distance_m,
                )
                v = min(p.max_linear_mps, self.distance_gain * approach_remaining)
                v *= max(0.20, math.cos(bearing_error))
                omega = 0.0
                if self.allow_du_steering_before_axis_lock:
                    if abs(du_error) <= self.command_deadband_px:
                        bearing_error = 0.0
                    omega = clamp(
                        -self.du_bearing_gain * bearing_error,
                        -p.max_angular_rps,
                        p.max_angular_rps,
                    )
                return VelocityCommand(v, omega)

            # Legacy simulation/fallback mode only.
            if observation.base_distance_m <= self.angle_required_base_distance_m:
                return VelocityCommand(0.0, 0.0)
            if abs(du_error) <= self.command_deadband_px:
                bearing_error = 0.0
            approach_remaining = max(
                0.0, observation.base_distance_m - self.angle_required_base_distance_m
            )
            v = min(p.max_linear_mps, self.distance_gain * approach_remaining)
            if abs(bearing_error) > math.radians(25.0):
                v = 0.0
            else:
                v *= max(0.2, math.cos(bearing_error))
            omega = clamp(
                -self.du_bearing_gain * bearing_error,
                -p.max_angular_rps,
                p.max_angular_rps,
            )
            return VelocityCommand(v, omega)

        # From here on, y/yaw come from the marker physical centre axis and the
        # AMR rotation centre. DU does not define that physical axis; only the
        # final precision leg may use its offset-corrected residual as a small
        # secondary trim toward zero.
        lateral, yaw = self._control_pose(observation)
        verify_lateral, verify_yaw = self._update_verification_pose(lateral, yaw)
        if not self._angle_was_reliable:
            # Precise PnP takes over without throwing away the intercept that was
            # already selected from coarse camera geometry at startup.  This is
            # what removes the visible distance-triggered heading change.
            self.phase = DockPhase.CENTERLINE_TURN
        self._angle_was_reliable = True

        stopped = abs(observation.v) <= 0.025 and abs(observation.omega) <= 0.06
        at_final_depth = abs(observation.tag_depth_m - self.final_tag_depth_m) <= self.final_tag_depth_tolerance_m
        geometrically_aligned = (
            abs(verify_lateral) <= p.lateral_tolerance_m
            and abs(verify_yaw) <= p.yaw_tolerance_rad
        )
        # 최종 성공은 회전중심 거리 대신 실제 tag_depth 약 0.50 m에서
        # 판정한다. 3초 유지 시간은 LIVE loop에서 wall-clock으로 검증한다.
        if at_final_depth and geometrically_aligned and self._straight_axis_latched:
            # 목표 깊이에 들어온 순간부터 0속도로 감속한다. 실제 SUCCESS는
            # 완전 정지 후 3초 유지가 확인된 뒤 LIVE loop가 판정한다.
            self.phase = DockPhase.FINAL_HOLD
            return VelocityCommand(0.0, 0.0)
        self._success_count = 0
        if self.phase == DockPhase.FINAL_HOLD:
            # Final depth is a stop-only verification zone. A single noisy
            # lateral/yaw sample must never trigger a twitching correction.
            # Only a large *persistent* robust drift exits the hold.
            severe_final_drift = bool(
                abs(verify_lateral) > self.straight_drift_lateral_stop_m
                or abs(verify_yaw) > self.straight_drift_yaw_stop_rad
            )
            if severe_final_drift:
                self._straight_drift_started_at = self._straight_drift_started_at or time.monotonic()
                if time.monotonic() - self._straight_drift_started_at >= self.straight_drift_hold_s:
                    self._straight_axis_latched = False
                    self._yaw_verified = False
                    self._centerline_hold_started_at = None
                    self._yaw_hold_started_at = None
                    if abs(verify_lateral) > self.straight_drift_lateral_stop_m:
                        self._centerline_verified = False
                        self.phase = DockPhase.CENTERLINE_TURN
                        self._centerline_target_m = None
                    else:
                        self.phase = DockPhase.YAW_ALIGN
                    self._straight_drift_started_at = None
            else:
                self._straight_drift_started_at = None
            return VelocityCommand(0.0, 0.0)

        if self.phase == DockPhase.RECOVERY_ALIGN:
            # Visibility recovery is deliberately marker-axis based, not
            # camera-centre based.  Turning toward yaw=0 tends to bring an
            # oblique tag back into the camera while also leaving the robot in
            # a clean heading for the following straight reverse.
            if abs(observation.v) > 0.005:
                return VelocityCommand(0.0, 0.0)
            if abs(yaw) <= math.radians(2.0) and abs(observation.omega) <= 0.03:
                self.phase = DockPhase.RECOVERY_REVERSE
                return VelocityCommand(0.0, 0.0)
            return VelocityCommand(
                0.0,
                clamp(
                    -self.yaw_align_gain * yaw,
                    -p.max_angular_rps,
                    p.max_angular_rps,
                ),
            )

        # Once straight recovery has started, finish it before evaluating the
        # usual too-close trigger again. Otherwise every recovery frame would
        # start a new attempt instead of actually backing away.
        if self.phase == DockPhase.RECOVERY_REVERSE:
            remaining_reverse = self._recovery_target_m - observation.base_distance_m
            if remaining_reverse <= p.intercept_tolerance_m:
                # Recovery is only for creating room. Stop first, then restart
                # the normal camera-axis docking sequence from CENTERLINE_TURN.
                self.phase = DockPhase.CENTERLINE_TURN
                self._centerline_target_m = None
                return VelocityCommand(0.0, 0.0)

            # Fast STRAIGHT recovery. Do not steer with DU, marker-axis error,
            # yaw, or FOV while backing away. The robot keeps its current
            # heading (omega=0), creates longitudinal room quickly, then the
            # ordinary centreline -> yaw-zero -> straight docking sequence
            # starts again from scratch.
            braking_remaining = max(0.0, remaining_reverse - p.intercept_tolerance_m)
            braking_cap = math.sqrt(max(0.0, 2.0 * p.max_linear_accel_mps2 * braking_remaining))
            v_mag = min(self.recovery_reverse_speed_mps, braking_cap)
            return VelocityCommand(-v_mag, 0.0)

        # 0.50 m final gate는 모든 정상 phase보다 우선한다. 중심축 정렬이
        # 끝나지 않았더라도 이 깊이보다 더 가까이 진행하는 명령은 금지한다.
        if observation.tag_depth_m <= self.final_tag_depth_m + self.final_tag_depth_tolerance_m:
            # After centreline + yaw have each been verified for three seconds,
            # the last leg is intentionally straight-only. At the depth gate we
            # stop first and evaluate robust geometry while stationary instead
            # of reacting to one noisy camera frame.
            if self._straight_axis_latched:
                self.phase = DockPhase.FINAL_HOLD
                return VelocityCommand(0.0, 0.0)
            if not self._centerline_verified:
                if abs(verify_lateral) <= self.centerline_lock_tolerance_m:
                    # The rotation centre is already on the marker normal axis.
                    # Do not wait at a separate centreline hold; freeze translation
                    # and begin the slow yaw alignment immediately.
                    self._centerline_verified = True
                    self._yaw_verified = False
                    self.phase = DockPhase.YAW_ALIGN
                    self._centerline_target_m = None
                    self._centerline_hold_started_at = None
                    self._centerline_lock_count = 0
                    self._verification_yaw_history.clear()
                    self._verification_yaw_history.append(float(yaw))
                    self._verification_yaw_rad = float(yaw)
                    return VelocityCommand(0.0, 0.0)
                return self._begin_recovery(
                    observation,
                    reverse_distance_m=self.near_goal_recovery_reverse_distance_m,
                    reason="final-gate-before-centerline-verify",
                )
            if abs(verify_lateral) <= self.centerline_recenter_tolerance_m:
                self.phase = DockPhase.YAW_ALIGN
                self._yaw_verified = False
                return VelocityCommand(0.0, 0.0)
            return self._begin_recovery(
                observation,
                reverse_distance_m=self.near_goal_recovery_reverse_distance_m,
                reason="final-gate-axis",
            )

        if remaining < -0.03:
            return self._begin_recovery(observation)

        # If the robot is already too close and too far from the centreline to
        # make a bounded intercept, create room first instead of trying a tight
        # curve next to the dock.
        late_geometry_failure = (
            observation.base_distance_m <= p.staging_standoff_m + 0.08
            and abs(lateral) > 1.6 * self.centerline_tolerance_m
            and observation.fov_margin_normalized <= self.recovery_fov_trigger_normalized
        )
        if late_geometry_failure:
            command = self.request_visibility_recovery(observation)
            if self.phase == DockPhase.RECOVERY_ALIGN:
                self._recovery_reason = "fov-edge-before-stall"
            return command

        # PHASE 1: centreline alignment using the camera-derived base rotation
        # centre error. Rotation toward the intercept is done in place first.
        if self.phase in (DockPhase.VISION_APPROACH, DockPhase.CENTERLINE_TURN, DockPhase.CENTERLINE_DRIVE):
            if abs(verify_lateral) <= self.centerline_lock_tolerance_m:
                # User-requested fast hand-off: as soon as the robust centre-axis
                # estimate says the AMR rotation centre is on the tag normal axis,
                # stop translation and go directly to yaw alignment.  There is no
                # wall-clock centreline verification wait.
                self._centerline_verified = True
                self._yaw_verified = False
                self.phase = DockPhase.YAW_ALIGN
                self._centerline_target_m = None
                self._last_straight_du_abs_px = None
                self._centerline_lock_count = 0
                self._centerline_hold_started_at = None
                self._verification_yaw_history.clear()
                self._verification_yaw_history.append(float(yaw))
                self._verification_yaw_rad = float(yaw)
                return VelocityCommand(0.0, 0.0)
            self._centerline_lock_count = 0
            self._centerline_hold_started_at = None
            if self._centerline_target_m is None and not self._plan_centerline_target(observation, lateral):
                return self._begin_recovery(observation)

        if self.phase == DockPhase.CENTERLINE_HOLD:
            # Compatibility for an in-flight/old saved phase.  CENTERLINE_HOLD
            # no longer waits: valid geometry proceeds immediately to yaw.
            if abs(verify_lateral) <= self.centerline_recenter_tolerance_m:
                self._centerline_verified = True
                self._yaw_verified = False
                self.phase = DockPhase.YAW_ALIGN
                self._centerline_target_m = None
                self._centerline_hold_started_at = None
                self._centerline_lock_count = 0
                self._verification_yaw_history.clear()
                self._verification_yaw_history.append(float(yaw))
                self._verification_yaw_rad = float(yaw)
            else:
                self._centerline_verified = False
                self._yaw_verified = False
                self.phase = DockPhase.CENTERLINE_TURN
                self._centerline_target_m = None
            return VelocityCommand(0.0, 0.0)

        if self.phase == DockPhase.CENTERLINE_TURN:
            desired = self._centerline_heading(observation, lateral)
            heading_error = wrap_angle(desired - yaw)
            if (
                abs(heading_error) <= p.align_before_drive_rad
                and abs(observation.omega) <= 0.06
            ):
                self.phase = DockPhase.CENTERLINE_DRIVE
                return VelocityCommand(0.0, 0.0)
            return VelocityCommand(
                0.0,
                clamp(
                    self.centerline_turn_gain * heading_error,
                    -p.max_angular_rps,
                    p.max_angular_rps,
                ),
            )

        if self.phase == DockPhase.CENTERLINE_DRIVE:
            desired = self._centerline_heading(observation, lateral)
            heading_error = wrap_angle(desired - yaw)
            if abs(heading_error) > self.centerline_realign_heading_rad:
                # Do not keep turning toward a nearly-passed stale intercept.
                # Re-plan immediately from the current camera-derived pose so
                # the heading stays smooth and the tag remains in view.
                self._centerline_target_m = None
                if not self._plan_centerline_target(observation, lateral):
                    return self._begin_recovery(observation)
                self.phase = DockPhase.CENTERLINE_TURN
                return VelocityCommand(0.0, 0.0)

            assert self._centerline_target_m is not None
            intercept_distance = math.hypot(
                observation.base_distance_m - self._centerline_target_m,
                lateral,
            )
            if intercept_distance <= 0.02 and abs(lateral) > self.centerline_lock_tolerance_m:
                self._centerline_target_m = None
                if not self._plan_centerline_target(observation, lateral):
                    return self._begin_recovery(observation)
                self.phase = DockPhase.CENTERLINE_TURN
                return VelocityCommand(0.0, 0.0)

            v = min(p.max_linear_mps, self.centerline_drive_gain * intercept_distance)
            v *= max(0.35, math.cos(heading_error))
            omega_limit = min(p.max_angular_rps, self.centerline_heading_limit_rps)
            omega = clamp(
                self.centerline_heading_gain * heading_error,
                -omega_limit,
                omega_limit,
            )
            return self._curve_with_fov_priority(observation, v, omega)

        # PHASE 2: rotation centre is already on the marker normal axis.
        # Rotate in place only; translation remains zero.
        if self.phase == DockPhase.YAW_ALIGN:
            if abs(verify_lateral) > self.centerline_recenter_tolerance_m:
                self._centerline_verified = False
                self._yaw_verified = False
                self.phase = DockPhase.CENTERLINE_TURN
                self._centerline_target_m = None
                self._centerline_hold_started_at = None
                return VelocityCommand(0.0, 0.0)
            if abs(observation.v) > 0.005:
                return VelocityCommand(0.0, 0.0)
            if abs(verify_yaw) <= self.yaw_lock_tolerance_rad and abs(observation.omega) <= 0.01:
                self.phase = DockPhase.YAW_HOLD
                self._yaw_hold_started_at = None
                self._yaw_lock_count = 0
                self._verification_yaw_history.clear()
                self._verification_yaw_history.append(float(yaw))
                self._verification_yaw_rad = float(yaw)
                return VelocityCommand(0.0, 0.0)
            self._yaw_hold_started_at = None
            self._yaw_lock_count = 0
            # Slow, deterministic final yaw alignment.  Use the robust median
            # verification yaw so one noisy camera frame cannot flip direction.
            # The requested angular speed is exactly 1 deg/s by default.
            yaw_for_align = float(verify_yaw)
            omega = -math.copysign(self.yaw_align_speed_rps, yaw_for_align)
            return VelocityCommand(0.0, omega)

        if self.phase == DockPhase.YAW_HOLD:
            # Verify yaw for a full 3 s after immediate centreline lock. No
            # translation or steering is allowed during verification.
            if abs(verify_lateral) > self.centerline_recenter_tolerance_m:
                self._centerline_verified = False
                self._yaw_verified = False
                self.phase = DockPhase.CENTERLINE_TURN
                self._centerline_target_m = None
                self._yaw_hold_started_at = None
                self._yaw_lock_count = 0
                return VelocityCommand(0.0, 0.0)
            if abs(observation.v) > 0.005 or abs(observation.omega) > 0.01:
                self._yaw_hold_started_at = None
                self._yaw_lock_count = 0
                return VelocityCommand(0.0, 0.0)
            if abs(verify_yaw) <= self.yaw_lock_tolerance_rad:
                self._yaw_lock_count += 1
                if self._yaw_lock_count >= self.yaw_lock_stable_steps:
                    self._yaw_hold_started_at = self._yaw_hold_started_at or time.monotonic()
                if (
                    self._yaw_hold_started_at is not None
                    and time.monotonic() - self._yaw_hold_started_at >= self.yaw_hold_s
                ):
                    self._yaw_verified = True
                    self.phase = DockPhase.STRAIGHT_APPROACH
                    self._straight_axis_latched = bool(self._centerline_verified and self._yaw_verified)
                    self._straight_latched_lateral_m = float(verify_lateral)
                    self._straight_latched_yaw_rad = float(verify_yaw)
                    self._straight_drift_started_at = None
                    self._last_straight_du_abs_px = abs(du_error)
                    self._yaw_hold_started_at = None
                    self._yaw_lock_count = 0
                return VelocityCommand(0.0, 0.0)
            self._yaw_hold_started_at = None
            self._yaw_lock_count = 0
            self._yaw_verified = False
            if abs(verify_yaw) > self.straight_realign_yaw_rad:
                self.phase = DockPhase.YAW_ALIGN
            return VelocityCommand(0.0, 0.0)

        # PHASE 3: after centreline lock + the 3 s yaw verification, moving commands are
        # strictly straight (omega=0). Camera/LiDAR yaw and lateral noise is
        # diagnostic only. A large persistent drift first STOPS the AMR, then
        # returns to alignment; it never steers while translating.
        severe_straight_drift = bool(
            abs(verify_lateral) > self.straight_drift_lateral_stop_m
            or abs(verify_yaw) > self.straight_drift_yaw_stop_rad
        )
        if severe_straight_drift:
            self._straight_drift_started_at = self._straight_drift_started_at or time.monotonic()
            if time.monotonic() - self._straight_drift_started_at >= self.straight_drift_hold_s:
                self._straight_axis_latched = False
                self._yaw_verified = False
                self._last_straight_du_abs_px = None
                self._straight_drift_started_at = None
                if abs(verify_lateral) > self.straight_drift_lateral_stop_m:
                    self._centerline_verified = False
                    self.phase = DockPhase.CENTERLINE_TURN
                    self._centerline_target_m = None
                else:
                    self.phase = DockPhase.YAW_ALIGN
            # The robust median has already rejected one-frame spikes. Once a
            # large deviation survives that filter, stop translation first.
            # If it clears before the persistence timer expires, straight-only
            # approach resumes without ever issuing a steering command.
            return VelocityCommand(0.0, 0.0)
        else:
            self._straight_drift_started_at = None
        self._precision_settle_steps = 0

        # PHASE 3 does not try to make DU decrease monotonically from frame to
        # frame. Camera noise makes that requirement counterproductive. Instead
        # it continuously regulates the *current offset-corrected DU residual*
        # toward zero, with a small deadband around zero. Marker-axis geometry
        # remains primary, so a real lateral/yaw drift still returns to an
        # earlier geometric alignment phase above.
        final_depth_remaining = max(0.0, observation.tag_depth_m - self.final_tag_depth_m)
        v = min(p.max_final_linear_mps, self.distance_gain * final_depth_remaining)

        du_abs = abs(du_error)
        if du_abs > self.straight_du_slowdown_start_px:
            span = max(1.0, self.du_tolerance_px - self.straight_du_slowdown_start_px)
            excess = (du_abs - self.straight_du_slowdown_start_px) / span
            v *= float(np.clip(1.0 - 0.50 * excess, 0.35, 1.0))

        omega = 0.0
        if self.straight_heading_hold_gain > 0.0 or self.straight_du_hold_gain > 0.0:
            # Short look-ahead keeps the physical AMR rotation centre on the
            # marker normal axis.  DU correction is a secondary fine trim that
            # simply seeks residual=0; there is no previous-frame trend test.
            lookahead_m = max(0.18, min(0.35, max(0.0, remaining) + 0.10))
            desired_heading = math.atan2(lateral, lookahead_m)
            geometry_error = wrap_angle(desired_heading - yaw)
            geometry_limit = min(p.max_angular_rps, self.straight_heading_limit_rps)
            geometry_omega = clamp(
                self.straight_heading_hold_gain * geometry_error,
                -geometry_limit,
                geometry_limit,
            )
            du_limit = min(p.max_angular_rps, self.straight_du_hold_limit_rps)
            du_omega = 0.0
            if du_abs > self.command_deadband_px:
                du_omega = clamp(
                    -self.straight_du_hold_gain * observation.bearing_error_rad,
                    -du_limit,
                    du_limit,
                )
            combined_limit = min(
                p.max_angular_rps,
                max(self.straight_heading_limit_rps, self.straight_du_hold_limit_rps),
            )
            omega = clamp(geometry_omega + du_omega, -combined_limit, combined_limit)

        return VelocityCommand(v, omega)

def visual_observation_vector(
    observation: VisualDockObservation,
    params: DockParams,
    last_action: np.ndarray,
    uncertainty: float,
) -> np.ndarray:
    """Stable-Baselines observation shared verbatim by training and runtime."""

    values = np.array(
        [
            np.clip(observation.remaining_m / 2.2, -1.0, 1.0),
            np.clip(observation.du_error_px / 320.0, -1.0, 1.0),
            math.sin(observation.pitch_error_rad),
            math.cos(observation.pitch_error_rad),
            np.clip(observation.v / params.max_linear_mps, -1.0, 1.0),
            np.clip(observation.omega / params.max_angular_rps, -1.0, 1.0),
            float(last_action[0]),
            float(last_action[1]),
            1.0 if observation.visible else -1.0,
            1.0 if observation.angle_reliable else -1.0,
            2.0 * float(np.clip(uncertainty, 0.0, 1.0)) - 1.0,
            float(np.clip(observation.fov_margin_normalized, -1.0, 1.0)),
        ],
        dtype=np.float32,
    )
    return values
