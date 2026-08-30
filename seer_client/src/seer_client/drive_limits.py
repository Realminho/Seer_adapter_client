"""SEER-native manual-drive defaults and safety validation.

The Robokit open-loop motion API (2010) uses metres/second and radians/second,
but does not publish one universal velocity maximum: the usable maximum belongs
to each robot model/configuration.  These conservative WebUI limits can be
overridden per deployment without changing the shared adapter.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass


DEFAULT_LINEAR_MPS = 0.05
DEFAULT_ANGULAR_DEG_S = 5.0
DEFAULT_MAX_LINEAR_MPS = 0.5
DEFAULT_MAX_ANGULAR_DEG_S = 60.0
DEFAULT_MOTION_DURATION_MS = 1200


def _positive_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return float(default)
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


@dataclass(frozen=True)
class ManualDriveLimits:
    """Operator-facing SEER speed envelope.

    ``max_*`` are WebUI/client safety bounds, not vendor-wide hardware maxima.
    Configure them no higher than the limits of the deployed chassis.
    """

    default_linear_mps: float = DEFAULT_LINEAR_MPS
    default_angular_deg_s: float = DEFAULT_ANGULAR_DEG_S
    max_linear_mps: float = DEFAULT_MAX_LINEAR_MPS
    max_angular_deg_s: float = DEFAULT_MAX_ANGULAR_DEG_S
    motion_duration_ms: int = DEFAULT_MOTION_DURATION_MS

    @classmethod
    def from_env(cls) -> "ManualDriveLimits":
        duration = int(
            _positive_env(
                "SEER_MANUAL_MOTION_DURATION_MS", DEFAULT_MOTION_DURATION_MS
            )
        )
        if duration > 5000:
            raise ValueError(
                "SEER_MANUAL_MOTION_DURATION_MS must be in the API range 1..5000"
            )
        return cls(
            default_linear_mps=_positive_env(
                "SEER_WEBUI_DEFAULT_LINEAR_MPS", DEFAULT_LINEAR_MPS
            ),
            default_angular_deg_s=_positive_env(
                "SEER_WEBUI_DEFAULT_ANGULAR_DEG_S", DEFAULT_ANGULAR_DEG_S
            ),
            max_linear_mps=_positive_env(
                "SEER_WEBUI_MAX_LINEAR_MPS", DEFAULT_MAX_LINEAR_MPS
            ),
            max_angular_deg_s=_positive_env(
                "SEER_WEBUI_MAX_ANGULAR_DEG_S", DEFAULT_MAX_ANGULAR_DEG_S
            ),
            motion_duration_ms=duration,
        ).validated()

    def validated(self) -> "ManualDriveLimits":
        if self.default_linear_mps > self.max_linear_mps:
            raise ValueError("default linear speed exceeds SEER WebUI maximum")
        if self.default_angular_deg_s > self.max_angular_deg_s:
            raise ValueError("default angular speed exceeds SEER WebUI maximum")
        if not 1 <= int(self.motion_duration_ms) <= 5000:
            raise ValueError("SEER motion duration must be in 1..5000 ms")
        return self

    def validate_command(
        self, *, vx_mps: float, vy_mps: float, angular_deg_s: float
    ) -> None:
        values = (float(vx_mps), float(vy_mps), float(angular_deg_s))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("SEER manual-drive values must be finite")
        linear = math.hypot(values[0], values[1])
        if linear > self.max_linear_mps + 1e-12:
            raise ValueError(
                f"SEER manual linear speed {linear:.3f} m/s exceeds configured "
                f"WebUI limit {self.max_linear_mps:.3f} m/s"
            )
        if abs(values[2]) > self.max_angular_deg_s + 1e-12:
            raise ValueError(
                f"SEER manual angular speed {abs(values[2]):.3f} deg/s exceeds "
                f"configured WebUI limit {self.max_angular_deg_s:.3f} deg/s"
            )

