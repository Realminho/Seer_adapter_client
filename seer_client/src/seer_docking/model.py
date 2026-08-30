"""Planar docking model shared by simulator, controller and PPO environment."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .geometry import wrap_angle


@dataclass(slots=True)
class DockState:
    """Base rotation-centre pose in the floor-aligned dock frame.

    x: distance outward from marker plane [m]
    y: lateral error, positive to dock-frame left [m]
    yaw: base heading error; 0 means facing straight toward the marker [rad]
    v, omega: realised forward and angular velocity
    """

    x: float
    y: float
    yaw: float
    v: float = 0.0
    omega: float = 0.0


@dataclass(slots=True)
class VelocityCommand:
    v: float
    omega: float


@dataclass(slots=True)
class DockParams:
    goal_standoff_m: float = 0.45
    staging_standoff_m: float = 0.90
    max_linear_mps: float = 0.22
    max_final_linear_mps: float = 0.08
    max_angular_rps: float = 0.55
    max_linear_accel_mps2: float = 0.35
    max_angular_accel_rps2: float = 1.0
    lateral_tolerance_m: float = 0.002
    yaw_tolerance_rad: float = math.radians(0.25)
    distance_tolerance_m: float = 0.012
    intercept_tolerance_m: float = 0.035
    align_before_drive_rad: float = math.radians(3.0)
    success_hold_steps: int = 8


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def rate_limit(target: float, current: float, max_rate: float, dt: float) -> float:
    return current + clamp(target - current, -max_rate * dt, max_rate * dt)


def step_unicycle(
    state: DockState,
    command: VelocityCommand,
    dt: float,
    params: DockParams,
    linear_gain: float = 1.0,
    angular_gain: float = 1.0,
) -> DockState:
    """Integrate a non-holonomic AMR with actuator acceleration limits.

    Desired heading is toward -dock-x.  Thus xdot=-v*cos(yaw) and
    ydot=-v*sin(yaw).
    """
    target_v = clamp(command.v, -params.max_linear_mps, params.max_linear_mps) * linear_gain
    target_w = clamp(command.omega, -params.max_angular_rps, params.max_angular_rps) * angular_gain
    v = rate_limit(target_v, state.v, params.max_linear_accel_mps2, dt)
    w = rate_limit(target_w, state.omega, params.max_angular_accel_rps2, dt)
    yaw_mid = state.yaw + 0.5 * dt * w
    x = state.x - dt * v * math.cos(yaw_mid)
    y = state.y - dt * v * math.sin(yaw_mid)
    yaw = wrap_angle(state.yaw + dt * w)
    return DockState(x=x, y=y, yaw=yaw, v=v, omega=w)
