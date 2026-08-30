"""Predictive image-space guard that keeps an AprilTag inside camera FOV."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .model import VelocityCommand, clamp


@dataclass(slots=True)
class FovGuardParams:
    enabled: bool = True
    activation_depth_m: float = 0.90
    soft_margin_px: float = 24.0
    hard_margin_px: float = 8.0
    prediction_horizon_s: float = 0.65
    simulation_dt_s: float = 0.025
    angular_samples: int = 31


@dataclass(slots=True)
class FovGuardResult:
    command: VelocityCommand
    limited: bool
    current_margin_px: float
    predicted_margin_px: float
    reason: str


def tag_object_points(tag_size_m: float) -> np.ndarray:
    half = 0.5 * float(tag_size_m)
    return np.array(
        [[-half, half, 0.0], [half, half, 0.0], [half, -half, 0.0], [-half, -half, 0.0]],
        dtype=float,
    )


def camera_points_from_tag(t_camera_marker: np.ndarray, tag_size_m: float) -> np.ndarray:
    transform = np.asarray(t_camera_marker, dtype=float)
    if transform.shape != (4, 4):
        raise ValueError("t_camera_marker must be 4x4")
    points = tag_object_points(tag_size_m)
    return (transform[:3, :3] @ points.T).T + transform[:3, 3]


def project_camera_points(points_camera: np.ndarray, camera_matrix: np.ndarray) -> np.ndarray:
    points = np.asarray(points_camera, dtype=float)
    k = np.asarray(camera_matrix, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or k.shape != (3, 3):
        raise ValueError("camera points must be Nx3 and camera_matrix must be 3x3")
    z = points[:, 2]
    pixels = np.full((len(points), 2), np.nan, dtype=float)
    valid = z > 1e-4
    pixels[valid, 0] = k[0, 0] * points[valid, 0] / z[valid] + k[0, 2]
    pixels[valid, 1] = k[1, 1] * points[valid, 1] / z[valid] + k[1, 2]
    return pixels


def image_margin_px(pixels: np.ndarray, image_width: int, image_height: int) -> float:
    points = np.asarray(pixels, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        return float("-inf")
    distances = np.concatenate(
        [
            points[:, 0],
            (float(image_width) - 1.0) - points[:, 0],
            points[:, 1],
            (float(image_height) - 1.0) - points[:, 1],
        ]
    )
    return float(np.min(distances))


def _motion_transform(
    command: VelocityCommand,
    current_v: float,
    current_w: float,
    horizon_s: float,
    simulation_dt_s: float,
    max_linear_accel_mps2: float,
    max_angular_accel_rps2: float,
) -> np.ndarray:
    x = y = yaw = 0.0
    v, w = float(current_v), float(current_w)
    steps = max(1, int(math.ceil(horizon_s / simulation_dt_s)))
    dt = float(horizon_s) / steps
    for _ in range(steps):
        v += clamp(command.v - v, -max_linear_accel_mps2 * dt, max_linear_accel_mps2 * dt)
        w += clamp(command.omega - w, -max_angular_accel_rps2 * dt, max_angular_accel_rps2 * dt)
        yaw_mid = yaw + 0.5 * dt * w
        x += dt * v * math.cos(yaw_mid)
        y += dt * v * math.sin(yaw_mid)
        yaw += dt * w
    c, s = math.cos(yaw), math.sin(yaw)
    transform = np.eye(4, dtype=float)
    transform[:3, :3] = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    transform[:3, 3] = (x, y, 0.0)
    return transform


def predict_camera_points(
    points_camera: np.ndarray,
    t_base_camera: np.ndarray,
    command: VelocityCommand,
    current_v: float,
    current_w: float,
    params: FovGuardParams,
    max_linear_accel_mps2: float,
    max_angular_accel_rps2: float,
) -> np.ndarray:
    points = np.asarray(points_camera, dtype=float)
    t_b_c = np.asarray(t_base_camera, dtype=float)
    if t_b_c.shape != (4, 4):
        raise ValueError("t_base_camera must be 4x4")
    homogeneous = np.column_stack([points, np.ones(len(points), dtype=float)]).T
    points_old_base = t_b_c @ homogeneous
    t_old_new = _motion_transform(
        command,
        current_v,
        current_w,
        params.prediction_horizon_s,
        params.simulation_dt_s,
        max_linear_accel_mps2,
        max_angular_accel_rps2,
    )
    points_new_camera = np.linalg.inv(t_b_c) @ np.linalg.inv(t_old_new) @ points_old_base
    return points_new_camera[:3].T


class TagFovGuard:
    """Sample admissible commands and choose the closest FOV-safe command."""

    def __init__(self, params: FovGuardParams | None = None) -> None:
        self.params = params or FovGuardParams()

    def filter_points(
        self,
        command: VelocityCommand,
        points_camera: np.ndarray,
        t_base_camera: np.ndarray,
        camera_matrix: np.ndarray,
        image_size: tuple[int, int],
        current_v: float,
        current_w: float,
        max_linear_mps: float,
        max_angular_rps: float,
        max_linear_accel_mps2: float,
        max_angular_accel_rps2: float,
    ) -> FovGuardResult:
        p = self.params
        width, height = int(image_size[0]), int(image_size[1])
        current_pixels = project_camera_points(points_camera, camera_matrix)
        current_margin = image_margin_px(current_pixels, width, height)
        center_depth = float(np.mean(np.asarray(points_camera, dtype=float)[:, 2]))

        def margin_for(candidate: VelocityCommand) -> float:
            predicted = predict_camera_points(
                points_camera,
                t_base_camera,
                candidate,
                current_v,
                current_w,
                p,
                max_linear_accel_mps2,
                max_angular_accel_rps2,
            )
            return image_margin_px(project_camera_points(predicted, camera_matrix), width, height)

        original_margin = margin_for(command)
        if not p.enabled or center_depth > p.activation_depth_m or original_margin >= p.soft_margin_px:
            return FovGuardResult(command, False, current_margin, original_margin, "clear")

        angular_count = max(9, int(p.angular_samples) | 1)
        angular_values = np.linspace(-max_angular_rps, max_angular_rps, angular_count)
        angular_values = np.unique(np.append(angular_values, [command.omega, 0.0]))
        # Preserve the requested sign so the same predictive shield can safely
        # limit a bounded reverse-recovery command as well as forward motion.
        linear_values = np.unique(np.array([command.v, 0.5 * command.v, 0.0]))
        safe: list[tuple[float, float, VelocityCommand]] = []
        recovery: list[tuple[float, float, VelocityCommand]] = []
        for v in linear_values:
            for w in angular_values:
                candidate = VelocityCommand(float(v), float(w))
                margin = margin_for(candidate)
                distance = (
                    1.4 * abs(candidate.v - command.v) / max(max_linear_mps, 1e-6)
                    + abs(candidate.omega - command.omega) / max(max_angular_rps, 1e-6)
                )
                recovery.append((margin, -distance, candidate))
                if margin >= p.soft_margin_px:
                    safe.append((distance, -margin, candidate))
        if safe:
            _, negative_margin, selected = min(safe, key=lambda item: (item[0], item[1]))
            predicted_margin = -negative_margin
            return FovGuardResult(selected, True, current_margin, predicted_margin, "FOV soft-margin shield")

        best_margin, _, selected = max(recovery, key=lambda item: (item[0], item[1]))
        reason = "FOV recovery" if best_margin >= p.hard_margin_px else "FOV emergency recovery"
        return FovGuardResult(selected, True, current_margin, best_margin, reason)

    def filter_detection(
        self,
        command: VelocityCommand,
        t_camera_marker: np.ndarray,
        tag_size_m: float,
        t_base_camera: np.ndarray,
        camera_matrix: np.ndarray,
        image_size: tuple[int, int],
        current_v: float,
        current_w: float,
        max_linear_mps: float,
        max_angular_rps: float,
        max_linear_accel_mps2: float,
        max_angular_accel_rps2: float,
    ) -> FovGuardResult:
        return self.filter_points(
            command,
            camera_points_from_tag(t_camera_marker, tag_size_m),
            t_base_camera,
            camera_matrix,
            image_size,
            current_v,
            current_w,
            max_linear_mps,
            max_angular_rps,
            max_linear_accel_mps2,
            max_angular_accel_rps2,
        )
