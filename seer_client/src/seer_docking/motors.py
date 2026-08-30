"""Convert body twist to equivalent differential/skid-steer wheel values."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .model import VelocityCommand


@dataclass(slots=True)
class WheelMotorValues:
    left_rad_s: float
    right_rad_s: float
    left_rpm: float
    right_rpm: float


def differential_wheel_values(
    command: VelocityCommand,
    wheel_radius_m: float = 0.075,
    track_width_m: float = 0.469,
) -> WheelMotorValues:
    """Inverse kinematics for a differential/skid-steer base.

    omega_L=(v-b*omega/2)/r and omega_R=(v+b*omega/2)/r.
    Defaults are taken from SBA-400EU.model: wheel radius 0.075 m and
    drive-wheel centre separation 2 * 0.2345 = 0.469 m.
    """
    if wheel_radius_m <= 0.0 or track_width_m <= 0.0:
        raise ValueError("wheel radius and track width must be positive")
    left = (command.v - 0.5 * track_width_m * command.omega) / wheel_radius_m
    right = (command.v + 0.5 * track_width_m * command.omega) / wheel_radius_m
    scale = 60.0 / (2.0 * math.pi)
    return WheelMotorValues(left, right, left * scale, right * scale)
