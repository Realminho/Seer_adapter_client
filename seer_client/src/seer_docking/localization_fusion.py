"""Tag-relative fusion using SEER map localization as a motion backbone.

A reliable tag/LiDAR observation establishes the transform between the SEER map
frame and the dock/tag frame.  Subsequent SEER x/y/theta updates then move the
pose even if camera depth is missing.  Fresh absolute tag observations slowly
correct the map-to-dock transform, while large visual jumps are rejected.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import numpy as np

from .geometry import pose2, se2, wrap_angle
from .model import DockState


@dataclass(frozen=True)
class FusionDiagnostics:
    anchored: bool
    accepted_correction: bool
    innovation_position_m: float | None
    innovation_yaw_deg: float | None
    source: str


class SeerTagRelativeFusion:
    """Fuse a tag-relative pose with SEER localization without stationary shimmer.

    SEER localization is the motion backbone once an anchor has been established.
    Camera-only PnP is intentionally treated as a *coarse anchor / weak drift
    correction* because a small planar tag can produce several degrees of pose
    jitter while the robot is physically motionless.

    The important invariant is: when SEER reports essentially zero motion and
    its map pose only jitters inside the configured deadband, the dock-relative
    output is held exactly still.  The map-to-dock transform is counter-adjusted
    underneath the hold so releasing the lock does not create a jump.
    """

    def __init__(
        self,
        *,
        position_correction_gain: float = 0.12,
        yaw_correction_gain: float = 0.10,
        stationary_gain_scale: float = 1.0,
        max_position_innovation_m: float = 0.25,
        max_yaw_innovation_deg: float = 15.0,
        min_measurement_confidence: float = 0.30,
        stationary_lock_enabled: bool = True,
        stationary_v_threshold_mps: float = 0.006,
        stationary_w_threshold_rps: float = 0.010,
        stationary_map_step_m: float = 0.008,
        stationary_map_step_yaw_deg: float = 0.20,
        pnp_anchor_window: int = 9,
        pnp_anchor_max_position_spread_m: float = 0.10,
        pnp_anchor_max_yaw_spread_deg: float = 8.0,
        pnp_moving_position_gain_scale: float = 0.10,
        pnp_moving_yaw_gain_scale: float = 0.08,
    ) -> None:
        self.position_correction_gain = float(np.clip(position_correction_gain, 0.0, 1.0))
        self.yaw_correction_gain = float(np.clip(yaw_correction_gain, 0.0, 1.0))
        # Kept for backwards-compatible config loading.  Stationary visual
        # corrections are no longer amplified; the stationary lock wins.
        self.stationary_gain_scale = max(0.0, float(stationary_gain_scale))
        self.max_position_innovation_m = max(0.01, float(max_position_innovation_m))
        self.max_yaw_innovation_rad = math.radians(max(0.5, float(max_yaw_innovation_deg)))
        self.min_measurement_confidence = float(np.clip(min_measurement_confidence, 0.0, 1.0))
        self.stationary_lock_enabled = bool(stationary_lock_enabled)
        self.stationary_v_threshold_mps = max(0.0, float(stationary_v_threshold_mps))
        self.stationary_w_threshold_rps = max(0.0, float(stationary_w_threshold_rps))
        self.stationary_map_step_m = max(0.0, float(stationary_map_step_m))
        self.stationary_map_step_yaw_rad = math.radians(max(0.0, float(stationary_map_step_yaw_deg)))
        self.pnp_anchor_window = max(1, int(pnp_anchor_window))
        self.pnp_anchor_max_position_spread_m = max(0.005, float(pnp_anchor_max_position_spread_m))
        self.pnp_anchor_max_yaw_spread_rad = math.radians(max(0.5, float(pnp_anchor_max_yaw_spread_deg)))
        self.pnp_moving_position_gain_scale = float(np.clip(pnp_moving_position_gain_scale, 0.0, 1.0))
        self.pnp_moving_yaw_gain_scale = float(np.clip(pnp_moving_yaw_gain_scale, 0.0, 1.0))
        self._t_d_m: np.ndarray | None = None
        self._last_map_pose: tuple[float, float, float] | None = None
        self._last_output: DockState | None = None
        self._pnp_anchor_samples: deque[DockState] = deque(maxlen=self.pnp_anchor_window)
        self._corrections_frozen = False

    def reset(self) -> None:
        self._t_d_m = None
        self._last_map_pose = None
        self._last_output = None
        self._pnp_anchor_samples.clear()
        self._corrections_frozen = False

    @staticmethod
    def _map_transform(map_pose: tuple[float, float, float]) -> np.ndarray:
        return se2(float(map_pose[0]), float(map_pose[1]), float(map_pose[2]))

    @staticmethod
    def _dock_transform(state: DockState) -> np.ndarray:
        # DockState yaw=0 means robot faces toward the wall, i.e. base yaw pi in D.
        return se2(float(state.x), float(state.y), wrap_angle(float(state.yaw) + math.pi))

    @staticmethod
    def _state_from_transform(t_d_b: np.ndarray, v: float, w: float) -> DockState:
        x, y, base_yaw = pose2(t_d_b)
        return DockState(float(x), float(y), wrap_angle(float(base_yaw) - math.pi), float(v), float(w))

    @staticmethod
    def _is_pnp_source(source: str) -> bool:
        return "PNP" in str(source or "").upper()

    def _stable_pnp_anchor(self, measurement: DockState) -> DockState | None:
        """Return a median PnP anchor only after a short stable sample window."""
        self._pnp_anchor_samples.append(measurement)
        if len(self._pnp_anchor_samples) < self.pnp_anchor_window:
            return None
        samples = list(self._pnp_anchor_samples)
        xs = np.asarray([s.x for s in samples], dtype=float)
        ys = np.asarray([s.y for s in samples], dtype=float)
        yaw_ref = float(samples[-1].yaw)
        yaws = np.asarray([yaw_ref + wrap_angle(float(s.yaw) - yaw_ref) for s in samples], dtype=float)
        mx, my, myaw = float(np.median(xs)), float(np.median(ys)), float(np.median(yaws))
        pos_spread = max(math.hypot(float(s.x) - mx, float(s.y) - my) for s in samples)
        yaw_spread = max(abs(wrap_angle(float(s.yaw) - myaw)) for s in samples)
        if pos_spread > self.pnp_anchor_max_position_spread_m or yaw_spread > self.pnp_anchor_max_yaw_spread_rad:
            return None
        return DockState(mx, my, wrap_angle(myaw), float(measurement.v), float(measurement.omega))


    @classmethod
    def anchor_pose_from_measurement(
        cls, map_pose: tuple[float, float, float], measurement: DockState
    ) -> tuple[float, float, float]:
        """Return the map->dock SE(2) anchor implied by one absolute dock pose."""
        t_m_b = cls._map_transform(map_pose)
        t_d_b = cls._dock_transform(measurement)
        x, y, yaw = pose2(t_d_b @ np.linalg.inv(t_m_b))
        return float(x), float(y), wrap_angle(float(yaw))

    def export_anchor_pose(self) -> tuple[float, float, float] | None:
        """Export the current map->dock anchor as x/y/yaw for persistence."""
        if self._t_d_m is None:
            return None
        x, y, yaw = pose2(self._t_d_m)
        return float(x), float(y), wrap_angle(float(yaw))

    def restore_anchor_pose(
        self, anchor_pose: tuple[float, float, float], *, frozen: bool = True
    ) -> None:
        """Restore a previously locked world dock axis.

        When ``frozen`` is true, later camera/LiDAR measurements are diagnostic
        only.  Motion is propagated solely by SEER localization plus the fused
        IMU yaw supplied by the caller.
        """
        x, y, yaw = [float(v) for v in anchor_pose]
        self._t_d_m = se2(x, y, wrap_angle(yaw))
        self._last_map_pose = None
        self._last_output = None
        self._pnp_anchor_samples.clear()
        self._corrections_frozen = bool(frozen)

    def freeze_corrections(self) -> bool:
        if self._t_d_m is None:
            return False
        self._corrections_frozen = True
        return True

    def unfreeze_corrections(self) -> None:
        self._corrections_frozen = False

    @property
    def corrections_frozen(self) -> bool:
        return bool(self._corrections_frozen)

    def predict(self, map_pose: tuple[float, float, float], *, v: float, w: float) -> DockState | None:
        if self._t_d_m is None:
            return None
        return self._state_from_transform(self._t_d_m @ self._map_transform(map_pose), v, w)

    def _motion_is_stationary(self, map_pose: tuple[float, float, float], *, v: float, w: float) -> bool:
        command_still = abs(float(v)) <= self.stationary_v_threshold_mps and abs(float(w)) <= self.stationary_w_threshold_rps
        if not command_still:
            return False
        if self._last_map_pose is None:
            return True
        dx = float(map_pose[0]) - float(self._last_map_pose[0])
        dy = float(map_pose[1]) - float(self._last_map_pose[1])
        dyaw = wrap_angle(float(map_pose[2]) - float(self._last_map_pose[2]))
        return math.hypot(dx, dy) <= self.stationary_map_step_m and abs(dyaw) <= self.stationary_map_step_yaw_rad

    def update(
        self,
        map_pose: tuple[float, float, float],
        measurement: DockState | None,
        *,
        measurement_confidence: float,
        measurement_source: str,
        v: float,
        w: float,
    ) -> tuple[DockState | None, FusionDiagnostics]:
        map_pose = (float(map_pose[0]), float(map_pose[1]), float(map_pose[2]))
        t_m_b = self._map_transform(map_pose)
        stationary = self._motion_is_stationary(map_pose, v=v, w=w)
        self._last_map_pose = map_pose

        if self._t_d_m is None:
            if measurement is None or float(measurement_confidence) < self.min_measurement_confidence:
                return None, FusionDiagnostics(False, False, None, None, "WAITING_FOR_ANCHOR")
            anchor = measurement
            if self._is_pnp_source(measurement_source):
                anchor = self._stable_pnp_anchor(measurement)
                if anchor is None:
                    return None, FusionDiagnostics(False, False, None, None, "WAITING_FOR_STABLE_PNP_ANCHOR")
                anchor_source = f"STABLE PNP({self.pnp_anchor_window})"
            else:
                self._pnp_anchor_samples.clear()
                anchor_source = measurement_source
            self._t_d_m = self._dock_transform(anchor) @ np.linalg.inv(t_m_b)
            state = self.predict(map_pose, v=v, w=w)
            self._last_output = state
            return state, FusionDiagnostics(True, True, 0.0, 0.0, f"ANCHOR {anchor_source}")

        pred = self.predict(map_pose, v=v, w=w)
        if pred is None:
            return None, FusionDiagnostics(False, False, None, None, "NO_PREDICTION")

        # A frozen world-axis lock is immutable. Camera/LiDAR remain useful for
        # depth/visibility diagnostics, but they must not move the centreline or
        # desired yaw after the lock has been established.
        if self._corrections_frozen:
            self._last_output = pred
            return pred, FusionDiagnostics(
                True, False, None, None, "SEER+IMU WORLD AXIS LOCK"
            )

        # Physical robot is stationary: hold the dock-relative pose *exactly*.
        # At the same time absorb SEER localization jitter into T_d_m so the
        # first moving frame continues from the held pose without a snap.
        if self.stationary_lock_enabled and stationary and self._last_output is not None:
            held = DockState(
                float(self._last_output.x),
                float(self._last_output.y),
                float(self._last_output.yaw),
                float(v),
                float(w),
            )
            self._t_d_m = self._dock_transform(held) @ np.linalg.inv(t_m_b)
            self._last_output = held
            if measurement is not None and float(measurement_confidence) >= self.min_measurement_confidence:
                lock_dpos = math.hypot(float(measurement.x) - held.x, float(measurement.y) - held.y)
                lock_dyaw = wrap_angle(float(measurement.yaw) - held.yaw)
                if lock_dpos > self.max_position_innovation_m or abs(lock_dyaw) > self.max_yaw_innovation_rad:
                    return held, FusionDiagnostics(
                        True, False, lock_dpos, math.degrees(lock_dyaw), f"{measurement_source} OUTLIER / STATIONARY LOCK"
                    )
            src = "SEER LOCALIZATION HOLD / STATIONARY LOCK"
            if measurement is not None and self._is_pnp_source(measurement_source):
                src += " / PNP JITTER IGNORED"
            return held, FusionDiagnostics(True, False, None, None, src)

        if measurement is None or float(measurement_confidence) < self.min_measurement_confidence:
            self._last_output = pred
            return pred, FusionDiagnostics(True, False, None, None, "SEER LOCALIZATION HOLD")

        dpos = math.hypot(float(measurement.x) - pred.x, float(measurement.y) - pred.y)
        dyaw = wrap_angle(float(measurement.yaw) - pred.yaw)
        if dpos > self.max_position_innovation_m or abs(dyaw) > self.max_yaw_innovation_rad:
            self._last_output = pred
            return pred, FusionDiagnostics(True, False, dpos, math.degrees(dyaw), f"{measurement_source} OUTLIER")

        candidate = self._dock_transform(measurement) @ np.linalg.inv(t_m_b)
        cx, cy, cyaw = pose2(candidate)
        fx, fy, fyaw = pose2(self._t_d_m)
        conf_scale = float(np.clip(measurement_confidence, 0.25, 1.0))
        pg_scale = 1.0
        yg_scale = 1.0
        if self._is_pnp_source(measurement_source):
            # PnP is useful for the initial coarse anchor but should not drag a
            # high-confidence SEER localization backbone frame by frame.
            pg_scale = self.pnp_moving_position_gain_scale
            yg_scale = self.pnp_moving_yaw_gain_scale
        pg = float(np.clip(self.position_correction_gain * conf_scale * pg_scale, 0.0, 0.45))
        yg = float(np.clip(self.yaw_correction_gain * conf_scale * yg_scale, 0.0, 0.45))
        fx += pg * (cx - fx)
        fy += pg * (cy - fy)
        fyaw = wrap_angle(fyaw + yg * wrap_angle(cyaw - fyaw))
        self._t_d_m = se2(fx, fy, fyaw)
        state = self.predict(map_pose, v=v, w=w)
        self._last_output = state
        label = f"SEER LOC + {measurement_source}"
        if self._is_pnp_source(measurement_source):
            label += " (WEAK CORRECTION)"
        return state, FusionDiagnostics(True, True, dpos, math.degrees(dyaw), label)

