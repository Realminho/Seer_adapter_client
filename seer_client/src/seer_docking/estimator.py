"""Small planar EKF for tag pose + wheel/IMU temporal fusion."""

from __future__ import annotations

import math

import numpy as np

from .geometry import wrap_angle
from .model import DockState


class PlanarDockEKF:
    """Estimate [x, y, yaw] in the dock frame.

    Wheel forward speed and IMU yaw rate drive prediction.  AprilTag/PnP gives
    the absolute correction.  This improves continuity and dropout handling;
    it does not replace camera-to-base extrinsic calibration.
    """

    def __init__(
        self,
        initial: DockState,
        position_sigma_m: float = 0.03,
        yaw_sigma_rad: float = math.radians(3.0),
    ) -> None:
        self.x = np.array([initial.x, initial.y, initial.yaw], dtype=float)
        self.p = np.diag([0.10**2, 0.10**2, math.radians(10.0) ** 2])
        self.q_base = np.diag([0.002**2, 0.002**2, math.radians(0.15) ** 2])
        self.r = np.diag([position_sigma_m**2, position_sigma_m**2, yaw_sigma_rad**2])
        self.last_innovation_mahalanobis = 0.0

    def predict(self, wheel_v_mps: float, imu_yaw_rate_rps: float, dt: float) -> DockState:
        x, y, yaw = self.x
        yaw_mid = yaw + 0.5 * dt * imu_yaw_rate_rps
        self.x[0] = x - dt * wheel_v_mps * math.cos(yaw_mid)
        self.x[1] = y - dt * wheel_v_mps * math.sin(yaw_mid)
        self.x[2] = wrap_angle(yaw + dt * imu_yaw_rate_rps)
        f = np.eye(3)
        f[0, 2] = dt * wheel_v_mps * math.sin(yaw_mid)
        f[1, 2] = -dt * wheel_v_mps * math.cos(yaw_mid)
        speed_scale = 1.0 + 2.5 * abs(wheel_v_mps) + abs(imu_yaw_rate_rps)
        self.p = f @ self.p @ f.T + self.q_base * max(dt, 1e-3) * speed_scale
        return self.state(wheel_v_mps, imu_yaw_rate_rps)

    def update_tag(
        self,
        measured: DockState,
        covariance: np.ndarray | None = None,
        gate_sigma: float = 4.0,
    ) -> bool:
        z = np.array([measured.x, measured.y, measured.yaw], dtype=float)
        innovation = z - self.x
        innovation[2] = wrap_angle(float(innovation[2]))
        r = self.r if covariance is None else np.asarray(covariance, dtype=float)
        if r.shape != (3, 3):
            raise ValueError("tag covariance must be 3x3")
        s = self.p + r
        mahalanobis = float(innovation.T @ np.linalg.solve(s, innovation))
        self.last_innovation_mahalanobis = mahalanobis
        if mahalanobis > gate_sigma**2:
            return False
        k = self.p @ np.linalg.inv(s)
        self.x = self.x + k @ innovation
        self.x[2] = wrap_angle(float(self.x[2]))
        # Joseph form preserves positive semidefiniteness.
        i_k = np.eye(3) - k
        self.p = i_k @ self.p @ i_k.T + k @ r @ k.T
        return True

    def state(self, v: float = 0.0, omega: float = 0.0) -> DockState:
        return DockState(float(self.x[0]), float(self.x[1]), float(self.x[2]), v, omega)
