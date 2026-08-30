"""SEER IMU attitude parsing and relative-yaw fusion for planar docking.

RoboKit status API 1014 exposes chassis attitude as yaw/roll/pitch in radians.
The absolute IMU yaw does not have to share the SEER map's zero heading, so the
fusion below uses *changes* in IMU yaw and lets fresh SEER localization gently
correct the absolute map heading.  This gives the docking loop a second heading
sensor without assuming that the two coordinate frames are pre-calibrated.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np

from .geometry import wrap_angle


@dataclass(frozen=True, slots=True)
class ImuAttitude:
    yaw_rad: float
    roll_rad: float | None = None
    pitch_rad: float | None = None
    schema: str = "yaw/roll/pitch"


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_seer_imu_attitude(payload: Any) -> ImuAttitude | None:
    """Parse RoboKit API 1014 attitude data.

    The documented SEER response uses ``yaw``, ``roll`` and ``pitch`` in
    radians.  A few controller generations wrap the response in ``data`` or
    ``result`` objects, so those compatibility wrappers are accepted as well.
    No conversion from degrees is attempted because API 1014 defines radians.
    """

    if not isinstance(payload, Mapping):
        return None

    yaw = _finite_float(payload.get("yaw"))
    if yaw is not None:
        roll = _finite_float(payload.get("roll"))
        pitch = _finite_float(payload.get("pitch"))
        return ImuAttitude(
            yaw_rad=wrap_angle(yaw),
            roll_rad=roll,
            pitch_rad=pitch,
            schema="yaw/roll/pitch",
        )

    for wrapper in ("imu", "data", "result", "status", "payload"):
        child = payload.get(wrapper)
        if isinstance(child, Mapping):
            parsed = parse_seer_imu_attitude(child)
            if parsed is not None:
                return ImuAttitude(
                    yaw_rad=parsed.yaw_rad,
                    roll_rad=parsed.roll_rad,
                    pitch_rad=parsed.pitch_rad,
                    schema=f"{wrapper}.{parsed.schema}",
                )
    return None


@dataclass(frozen=True, slots=True)
class ImuYawFusionDiagnostics:
    map_yaw_rad: float
    fused_yaw_rad: float
    yaw_rate_rps: float
    imu_delta_rad: float | None
    map_innovation_rad: float | None
    used_imu_delta: bool
    source: str


class ImuMapYawFusion:
    """Blend relative IMU yaw changes with absolute SEER map heading.

    IMU yaw is treated as a relative sensor.  This avoids assuming its zero
    angle is the same as RoboKit's world-frame zero.  Between map localization
    updates, accepted IMU yaw changes advance the fused heading.  Whenever a
    new SEER map pose arrives, a small complementary correction reins in drift.
    Large disagreements or physically implausible IMU jumps fall back to the
    SEER map heading instead of contaminating the docking pose.
    """

    def __init__(
        self,
        *,
        map_correction_gain: float = 0.20,
        max_map_innovation_deg: float = 12.0,
        max_imu_step_deg: float = 45.0,
        max_imu_rate_rps: float = 2.5,
        yaw_rate_alpha: float = 0.35,
        yaw_rate_deadband_rps: float = 0.004,
    ) -> None:
        self.map_correction_gain = float(np.clip(map_correction_gain, 0.0, 1.0))
        self.max_map_innovation_rad = math.radians(max(0.5, float(max_map_innovation_deg)))
        self.max_imu_step_rad = math.radians(max(1.0, float(max_imu_step_deg)))
        self.max_imu_rate_rps = max(0.1, float(max_imu_rate_rps))
        self.yaw_rate_alpha = float(np.clip(yaw_rate_alpha, 0.01, 1.0))
        self.yaw_rate_deadband_rps = max(0.0, float(yaw_rate_deadband_rps))
        self.reset()

    def reset(self) -> None:
        self._fused_yaw: float | None = None
        self._last_map_pose: tuple[float, float, float] | None = None
        self._last_imu_yaw: float | None = None
        self._last_imu_sample_mono: float | None = None
        self._yaw_rate_rps = 0.0
        self._imu_was_fresh = False

    @staticmethod
    def _map_pose_changed(
        previous: tuple[float, float, float] | None,
        current: tuple[float, float, float],
    ) -> bool:
        if previous is None:
            return True
        return (
            abs(float(current[0]) - float(previous[0])) > 1e-9
            or abs(float(current[1]) - float(previous[1])) > 1e-9
            or abs(wrap_angle(float(current[2]) - float(previous[2]))) > 1e-9
        )

    def update(
        self,
        map_pose: tuple[float, float, float],
        *,
        imu_yaw_rad: float | None,
        imu_sample_mono: float | None,
        imu_fresh: bool,
    ) -> ImuYawFusionDiagnostics:
        map_pose = (float(map_pose[0]), float(map_pose[1]), wrap_angle(float(map_pose[2])))
        map_yaw = map_pose[2]
        map_changed = self._map_pose_changed(self._last_map_pose, map_pose)
        self._last_map_pose = map_pose
        if self._fused_yaw is None:
            self._fused_yaw = map_yaw

        used_imu_delta = False
        imu_delta: float | None = None
        source_parts: list[str] = []
        # If the IMU stream went stale, the map pose may already have covered
        # motion during the gap.  Re-anchor the first returning sample instead
        # of integrating a long-gap delta a second time.
        if imu_fresh and not self._imu_was_fresh:
            if imu_yaw_rad is not None and imu_sample_mono is not None:
                self._last_imu_yaw = wrap_angle(float(imu_yaw_rad))
                self._last_imu_sample_mono = float(imu_sample_mono)
                self._yaw_rate_rps = 0.0
                source_parts.append("IMU_REANCHORED")
        self._imu_was_fresh = bool(imu_fresh)
        new_imu_sample = (
            imu_fresh
            and imu_yaw_rad is not None
            and imu_sample_mono is not None
            and (
                self._last_imu_sample_mono is None
                or float(imu_sample_mono) > float(self._last_imu_sample_mono) + 1e-9
            )
        )
        if new_imu_sample:
            imu_yaw = wrap_angle(float(imu_yaw_rad))
            sample_mono = float(imu_sample_mono)
            if self._last_imu_yaw is not None and self._last_imu_sample_mono is not None:
                dt = max(1e-3, sample_mono - float(self._last_imu_sample_mono))
                delta = wrap_angle(imu_yaw - float(self._last_imu_yaw))
                rate = delta / dt
                imu_delta = delta
                if abs(delta) <= self.max_imu_step_rad and abs(rate) <= self.max_imu_rate_rps:
                    self._fused_yaw = wrap_angle(float(self._fused_yaw) + delta)
                    filtered_rate = (
                        self.yaw_rate_alpha * rate
                        + (1.0 - self.yaw_rate_alpha) * float(self._yaw_rate_rps)
                    )
                    self._yaw_rate_rps = 0.0 if abs(filtered_rate) < self.yaw_rate_deadband_rps else filtered_rate
                    used_imu_delta = True
                    source_parts.append("IMU_DELTA")
                else:
                    # Preserve stream continuity, but do not integrate a jump.
                    self._yaw_rate_rps = 0.0
                    source_parts.append("IMU_JUMP_REJECTED")
            else:
                source_parts.append("IMU_ANCHORED")
            self._last_imu_yaw = imu_yaw
            self._last_imu_sample_mono = sample_mono

        map_innovation: float | None = None
        if map_changed:
            map_innovation = wrap_angle(map_yaw - float(self._fused_yaw))
            if not imu_fresh:
                self._fused_yaw = map_yaw
                source_parts.append("SEER_MAP")
            elif abs(map_innovation) > self.max_map_innovation_rad:
                # A large frame disagreement is safer to resolve in favor of the
                # localization frame used by world-coordinate laser points.
                self._fused_yaw = map_yaw
                self._yaw_rate_rps = 0.0
                source_parts.append("SEER_MAP_REANCHOR")
            else:
                self._fused_yaw = wrap_angle(
                    float(self._fused_yaw) + self.map_correction_gain * map_innovation
                )
                source_parts.append("SEER_MAP_CORRECTION")

        if not source_parts:
            source_parts.append("IMU_HOLD" if imu_fresh else "SEER_MAP_HOLD")
        return ImuYawFusionDiagnostics(
            map_yaw_rad=map_yaw,
            fused_yaw_rad=wrap_angle(float(self._fused_yaw)),
            yaw_rate_rps=float(self._yaw_rate_rps),
            imu_delta_rad=imu_delta,
            map_innovation_rad=map_innovation,
            used_imu_delta=used_imu_delta,
            source="+".join(source_parts),
        )
