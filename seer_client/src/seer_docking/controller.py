"""Interpretable geometric baseline controller for staged docking."""

from __future__ import annotations

from enum import Enum, auto
import math

from .geometry import wrap_angle
from .model import DockParams, DockState, VelocityCommand, clamp


class DockPhase(Enum):
    """Docking phases.

    The canonical names describe the strict sequence used by the live camera
    controller. Legacy names are kept as aliases so older scripts/configs that
    reference them continue to work.
    """

    VISION_APPROACH = auto()
    CENTERLINE_TURN = auto()
    CENTERLINE_DRIVE = auto()
    CENTERLINE_HOLD = auto()
    YAW_ALIGN = auto()
    YAW_HOLD = auto()
    STRAIGHT_APPROACH = auto()
    RECOVERY_REVERSE = auto()
    RECOVERY_ALIGN = auto()
    FINAL_HOLD = auto()
    DOCKED = auto()
    ABORTED = auto()

    # Backward-compatible aliases used by older logs/tests.
    INTERCEPT_ALIGN = CENTERLINE_TURN
    INTERCEPT_DRIVE = CENTERLINE_DRIVE
    FINAL_ALIGN = YAW_ALIGN
    FINAL_APPROACH = STRAIGHT_APPROACH


class AnalyticDockingController:
    """Strict centreline -> yaw-zero -> straight-approach baseline.

    A differential-drive AMR cannot translate sideways, so lateral error is
    removed by first turning toward one fixed point on the docking centreline,
    then driving to that line. Only after the *rotation centre* is on the line
    does the AMR rotate in place to yaw=0. The final leg is straight.

    This baseline intentionally avoids the old continuously-curved final
    approach, which could mix lateral and angular corrections and make docking
    slower or less predictable.
    """

    def __init__(self, params: DockParams | None = None) -> None:
        self.params = params or DockParams()
        self.phase = DockPhase.CENTERLINE_TURN
        self._success_count = 0
        self._centerline_target_x: float | None = None
        self._centerline_tolerance_m = max(self.params.lateral_tolerance_m, 0.002)
        self._centerline_lock_tolerance_m = min(self._centerline_tolerance_m, max(0.0005, 0.5 * self.params.lateral_tolerance_m))
        self._yaw_lock_tolerance_rad = min(self.params.yaw_tolerance_rad, math.radians(0.10))
        self._recenter_tolerance_m = max(0.006, 3.0 * self._centerline_tolerance_m)
        self._max_intercept_heading_rad = math.radians(35.0)
        self._final_clearance_m = 0.18

    def reset(self) -> None:
        self.phase = DockPhase.CENTERLINE_TURN
        self._success_count = 0
        self._centerline_target_x = None

    def abort(self) -> VelocityCommand:
        self.phase = DockPhase.ABORTED
        return VelocityCommand(0.0, 0.0)

    def _plan_centerline_target(self, state: DockState) -> bool:
        """Latch a reachable point on y=0 while leaving room for final drive."""

        p = self.params
        lateral = abs(state.y)
        min_target_x = p.goal_standoff_m + self._final_clearance_m
        if state.x <= min_target_x + 0.03:
            return False

        required_dx = max(
            0.15,
            lateral / max(math.tan(self._max_intercept_heading_rad), 1e-6) * 1.10,
        )
        # Intercept the centreline as early as the maximum heading allows; do
        # not wait for a fixed staging distance before committing to the axis.
        target_x = max(min_target_x, state.x - required_dx)
        dx = state.x - target_x
        if dx <= 0.05:
            return False
        desired = math.atan2(state.y, dx)
        if abs(desired) > self._max_intercept_heading_rad + math.radians(2.0):
            return False
        self._centerline_target_x = target_x
        return True

    def _centerline_heading(self, state: DockState) -> float:
        if self._centerline_target_x is None:
            if not self._plan_centerline_target(state):
                return 0.0
        assert self._centerline_target_x is not None
        dx = max(state.x - self._centerline_target_x, 1e-6)
        return math.atan2(state.y, dx)

    def _at_goal(self, state: DockState) -> bool:
        p = self.params
        return (
            abs(state.x - p.goal_standoff_m) <= p.distance_tolerance_m
            and abs(state.y) <= p.lateral_tolerance_m
            and abs(state.yaw) <= p.yaw_tolerance_rad
            and abs(state.v) <= 0.025
            and abs(state.omega) <= 0.06
        )

    def command(self, state: DockState) -> VelocityCommand:
        p = self.params
        if self.phase in (DockPhase.DOCKED, DockPhase.ABORTED):
            return VelocityCommand(0.0, 0.0)

        if self._at_goal(state):
            self._success_count += 1
            if self._success_count >= p.success_hold_steps:
                self.phase = DockPhase.DOCKED
            return VelocityCommand(0.0, 0.0)
        self._success_count = 0

        if state.x < p.goal_standoff_m - 0.03:
            return self.abort()

        # 1) Put the base rotation centre on the tag/dock centreline.
        if self.phase in (DockPhase.CENTERLINE_TURN, DockPhase.CENTERLINE_DRIVE):
            if abs(state.y) <= self._centerline_lock_tolerance_m:
                if abs(state.v) > 0.005 or abs(state.omega) > 0.015:
                    return VelocityCommand(0.0, 0.0)
                self.phase = DockPhase.YAW_ALIGN
                self._centerline_target_x = None
                return VelocityCommand(0.0, 0.0)
            if self._centerline_target_x is None and not self._plan_centerline_target(state):
                return self.abort()

        if self.phase == DockPhase.CENTERLINE_TURN:
            desired = self._centerline_heading(state)
            heading_error = wrap_angle(desired - state.yaw)
            if abs(heading_error) <= p.align_before_drive_rad and abs(state.omega) <= 0.08:
                self.phase = DockPhase.CENTERLINE_DRIVE
                return VelocityCommand(0.0, 0.0)
            return VelocityCommand(
                0.0,
                clamp(2.2 * heading_error, -p.max_angular_rps, p.max_angular_rps),
            )

        if self.phase == DockPhase.CENTERLINE_DRIVE:
            desired = self._centerline_heading(state)
            heading_error = wrap_angle(desired - state.yaw)
            if abs(heading_error) > math.radians(9.0):
                self._centerline_target_x = None
                if not self._plan_centerline_target(state):
                    return self.abort()
                self.phase = DockPhase.CENTERLINE_TURN
                return VelocityCommand(0.0, 0.0)

            assert self._centerline_target_x is not None
            distance = math.hypot(state.x - self._centerline_target_x, state.y)
            if distance <= 0.02 and abs(state.y) > self._centerline_lock_tolerance_m:
                self._centerline_target_x = None
                if not self._plan_centerline_target(state):
                    return self.abort()
                self.phase = DockPhase.CENTERLINE_TURN
                return VelocityCommand(0.0, 0.0)

            v = min(p.max_linear_mps, 0.8 * distance)
            v *= max(0.35, math.cos(heading_error))
            w_limit = min(p.max_angular_rps, 0.16)
            w = clamp(1.25 * heading_error, -w_limit, w_limit)
            return VelocityCommand(v, w)

        # 2) Once centred, stop and rotate the body to exactly yaw=0.
        if self.phase == DockPhase.YAW_ALIGN:
            if abs(state.y) > self._recenter_tolerance_m:
                self._centerline_target_x = None
                self.phase = DockPhase.CENTERLINE_TURN
                return VelocityCommand(0.0, 0.0)
            if abs(state.v) > 0.005:
                return VelocityCommand(0.0, 0.0)
            if abs(state.yaw) <= self._yaw_lock_tolerance_rad and abs(state.omega) <= 0.01:
                self.phase = DockPhase.STRAIGHT_APPROACH
                return VelocityCommand(0.0, 0.0)
            return VelocityCommand(
                0.0,
                clamp(-2.4 * state.yaw, -p.max_angular_rps, p.max_angular_rps),
            )

        # 3) Final approach is intentionally straight. If drift grows, stop and
        # re-run the preceding stage instead of curving toward the dock.
        if abs(state.y) > self._recenter_tolerance_m:
            self._centerline_target_x = None
            self.phase = DockPhase.CENTERLINE_TURN
            return VelocityCommand(0.0, 0.0)
        if abs(state.yaw) > math.radians(0.75):
            self.phase = DockPhase.YAW_ALIGN
            return VelocityCommand(0.0, 0.0)

        remaining = max(0.0, state.x - p.goal_standoff_m)
        v = min(p.max_final_linear_mps, 0.55 * remaining)
        return VelocityCommand(v, 0.0)
