"""LiDAR + RGB AprilTag centre geometry for depth-independent docking pose.

SEER API 1009 returns 2-D laser points in the map/world frame.  For a tag fixed
on a wall we can recover the full planar relative pose without RealSense depth:

1. transform laser points into the AMR base frame using SEER localization,
2. fit the wall line in the tag viewing sector,
3. cast a ray through the RGB tag centre from the calibrated camera origin,
4. intersect that ray with the wall line to locate the tag centre on the wall,
5. express the AMR rotation centre relative to the wall normal/tag centre.

This makes X/Y/Yaw robust when RealSense depth is noisy or invalid at close
range.  Depth remains available for obstacle safety and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np

from .geometry import wrap_angle


@dataclass(frozen=True)
class LidarTagPose:
    x_m: float
    y_m: float
    yaw_rad: float
    confidence: float
    wall_distance_m: float
    wall_rms_m: float
    inlier_ratio: float
    inlier_count: int
    wall_span_m: float
    tag_intersection_range_m: float
    tag_point_base_xy: tuple[float, float]
    ray_bearing_rad: float


def _camera_tag_ray_base(
    corners_px: np.ndarray,
    camera_matrix: np.ndarray,
    t_base_camera: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    corners = np.asarray(corners_px, dtype=float).reshape(-1, 2)
    k = np.asarray(camera_matrix, dtype=float)
    t_b_c = np.asarray(t_base_camera, dtype=float)
    if corners.shape[0] < 4 or k.shape != (3, 3) or t_b_c.shape != (4, 4):
        raise ValueError("invalid tag/camera geometry")
    center = np.mean(corners, axis=0)
    fx, fy = float(k[0, 0]), float(k[1, 1])
    cx, cy = float(k[0, 2]), float(k[1, 2])
    if fx <= 0.0 or fy <= 0.0:
        raise ValueError("invalid camera focal length")
    ray_c = np.array([(float(center[0]) - cx) / fx, (float(center[1]) - cy) / fy, 1.0], dtype=float)
    ray_b = t_b_c[:3, :3] @ ray_c
    ray_xy = np.asarray(ray_b[:2], dtype=float)
    norm = float(np.linalg.norm(ray_xy))
    if not math.isfinite(norm) or norm < 1e-9:
        raise ValueError("camera tag ray is degenerate in the floor plane")
    ray_xy /= norm
    origin_xy = np.asarray(t_b_c[:2, 3], dtype=float)
    return origin_xy, ray_xy


def _world_to_base(points_world: np.ndarray, robot_x: float, robot_y: float, robot_yaw: float) -> np.ndarray:
    points = np.asarray(points_world, dtype=float).reshape(-1, 2)
    rel = points - np.array([float(robot_x), float(robot_y)], dtype=float)
    c, s = math.cos(float(robot_yaw)), math.sin(float(robot_yaw))
    # R_B_M = R_M_B^T
    r = np.array([[c, s], [-s, c]], dtype=float)
    return rel @ r.T


def _fit_line_ransac(
    points: np.ndarray,
    *,
    threshold_m: float,
    iterations: int,
    min_inliers: int,
) -> tuple[np.ndarray, float, np.ndarray, float, float] | None:
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    if pts.shape[0] < max(2, int(min_inliers)):
        return None
    rng = np.random.default_rng(20260827)
    best_mask: np.ndarray | None = None
    best_count = -1
    best_rms = float("inf")
    n = pts.shape[0]
    for _ in range(max(20, int(iterations))):
        i, j = rng.choice(n, 2, replace=False)
        a, b = pts[i], pts[j]
        direction = b - a
        norm = float(np.linalg.norm(direction))
        if norm < 1e-6:
            continue
        direction /= norm
        normal = np.array([-direction[1], direction[0]], dtype=float)
        d = float(np.dot(normal, a))
        residual = np.abs(pts @ normal - d)
        mask = residual <= float(threshold_m)
        count = int(np.count_nonzero(mask))
        if count < min_inliers:
            continue
        rms = float(np.sqrt(np.mean(np.square(residual[mask]))))
        if count > best_count or (count == best_count and rms < best_rms):
            best_mask = mask
            best_count = count
            best_rms = rms
    if best_mask is None:
        return None

    inliers = pts[best_mask]
    center = np.mean(inliers, axis=0)
    centered = inliers - center
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    direction = np.asarray(vt[0], dtype=float)
    direction /= max(float(np.linalg.norm(direction)), 1e-12)
    normal = np.array([-direction[1], direction[0]], dtype=float)
    d = float(np.dot(normal, center))
    # Make n point from the robot toward the wall so d is positive.
    if d < 0.0:
        normal = -normal
        d = -d
        direction = -direction
    residual = np.abs(inliers @ normal - d)
    rms = float(np.sqrt(np.mean(np.square(residual))))
    along = inliers @ direction
    span = float(np.max(along) - np.min(along)) if len(along) else 0.0
    return normal, d, best_mask, rms, span


def estimate_lidar_tag_pose(
    laser_beams_world: Iterable[Iterable[float]],
    *,
    robot_map_pose: tuple[float, float, float],
    corners_px: np.ndarray,
    camera_matrix: np.ndarray,
    t_base_camera: np.ndarray,
    min_range_m: float = 0.12,
    max_range_m: float = 4.0,
    sector_half_angle_deg: float = 38.0,
    sector_lateral_pad_m: float = 0.45,
    ransac_threshold_m: float = 0.018,
    ransac_iterations: int = 90,
    min_points: int = 18,
    min_inlier_ratio: float = 0.35,
    min_wall_span_m: float = 0.22,
    max_wall_rms_m: float = 0.025,
    min_ray_wall_sin: float = 0.18,
) -> LidarTagPose | None:
    """Estimate tag-relative base pose from world-frame laser points + RGB centre."""

    raw = np.asarray(list(laser_beams_world), dtype=float)
    if raw.ndim != 2 or raw.shape[1] < 2:
        return None
    pts_world = raw[:, :2]
    finite = np.all(np.isfinite(pts_world), axis=1)
    pts_world = pts_world[finite]
    if pts_world.shape[0] < int(min_points):
        return None

    rx, ry, rth = [float(v) for v in robot_map_pose]
    pts_b = _world_to_base(pts_world, rx, ry, rth)
    ranges = np.linalg.norm(pts_b, axis=1)
    origin_xy, ray_xy = _camera_tag_ray_base(corners_px, camera_matrix, t_base_camera)
    ray_bearing = math.atan2(float(ray_xy[1]), float(ray_xy[0]))
    bearings = np.arctan2(pts_b[:, 1] - origin_xy[1], pts_b[:, 0] - origin_xy[0])
    angle_diff = np.arctan2(np.sin(bearings - ray_bearing), np.cos(bearings - ray_bearing))
    half = math.radians(float(sector_half_angle_deg))
    # Require points in front of the base/camera and in a broad cone around the RGB tag.
    front_projection = (pts_b - origin_xy) @ ray_xy
    lateral_axis = np.array([-ray_xy[1], ray_xy[0]], dtype=float)
    lateral = np.abs((pts_b - origin_xy) @ lateral_axis)
    cone_width = float(sector_lateral_pad_m) + np.maximum(front_projection, 0.0) * math.tan(half)
    mask = (
        (ranges >= float(min_range_m))
        & (ranges <= float(max_range_m))
        & (front_projection > 0.03)
        & (np.abs(angle_diff) <= half)
        & (lateral <= cone_width)
    )
    candidates = pts_b[mask]
    if candidates.shape[0] < int(min_points):
        return None

    fit = _fit_line_ransac(
        candidates,
        threshold_m=float(ransac_threshold_m),
        iterations=int(ransac_iterations),
        min_inliers=int(min_points),
    )
    if fit is None:
        return None
    n_to_wall, d, inlier_mask, rms, span = fit
    inlier_count = int(np.count_nonzero(inlier_mask))
    ratio = inlier_count / max(1, candidates.shape[0])
    if ratio < float(min_inlier_ratio) or span < float(min_wall_span_m) or rms > float(max_wall_rms_m):
        return None

    denom = float(np.dot(n_to_wall, ray_xy))
    if abs(denom) < float(min_ray_wall_sin):
        return None
    ray_t = (float(d) - float(np.dot(n_to_wall, origin_xy))) / denom
    if not math.isfinite(ray_t) or ray_t <= 0.0 or ray_t > float(max_range_m) * 1.35:
        return None
    tag_point = origin_xy + ray_t * ray_xy

    # Outward normal is wall -> robot.  The fitted normal points robot -> wall.
    normal_out = -n_to_wall
    marker_to_base = -tag_point
    lateral_out = np.array([-normal_out[1], normal_out[0]], dtype=float)
    x = float(np.dot(marker_to_base, normal_out))
    y = float(np.dot(marker_to_base, lateral_out))
    normal_angle = math.atan2(float(normal_out[1]), float(normal_out[0]))
    yaw = wrap_angle(math.pi - normal_angle)
    if x <= 0.0 or not all(math.isfinite(v) for v in (x, y, yaw)):
        return None

    rms_score = math.exp(-rms / max(1e-6, float(max_wall_rms_m)))
    span_score = float(np.clip(span / max(float(min_wall_span_m) * 2.0, 1e-6), 0.0, 1.0))
    count_score = float(np.clip(inlier_count / max(float(min_points) * 2.0, 1.0), 0.0, 1.0))
    confidence = float(np.clip(0.38 * ratio + 0.24 * rms_score + 0.22 * span_score + 0.16 * count_score, 0.0, 1.0))

    return LidarTagPose(
        x_m=x,
        y_m=y,
        yaw_rad=yaw,
        confidence=confidence,
        wall_distance_m=float(d),
        wall_rms_m=rms,
        inlier_ratio=float(ratio),
        inlier_count=inlier_count,
        wall_span_m=span,
        tag_intersection_range_m=float(ray_t),
        tag_point_base_xy=(float(tag_point[0]), float(tag_point[1])),
        ray_bearing_rad=float(ray_bearing),
    )
