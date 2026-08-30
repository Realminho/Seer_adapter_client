"""Robust RGB-D wall/tag pose estimation for AprilTag docking.

The small printed tag is excellent for pixel centre localisation but a poor
single-frame yaw sensor at long range: a sub-pixel corner change can rotate the
IPPE plane normal by several degrees.  A RealSense provides a second, metric
measurement of the *wall plane* surrounding the fixed tag.  This module fits
that larger plane, intersects the tag-centre camera ray with it, and converts
that geometry directly into the AMR rotation-centre frame.

The result deliberately separates translation from planar-PnP orientation.
PnP remains available as a fallback, while the primary live pose can use the
larger RGB-D wall patch when it is geometrically trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .geometry import validate_transform, wrap_angle


@dataclass(slots=True)
class RgbdWallPose:
    """AMR pose measured from a robust RGB-D wall plane around the tag."""

    x_m: float
    y_m: float
    yaw_rad: float
    tag_center_camera_m: np.ndarray
    wall_normal_camera: np.ndarray
    plane_rms_m: float
    inlier_ratio: float
    inlier_count: int
    roi_width_px: int
    roi_height_px: int
    horizontal_span_m: float
    position_sigma_m: float
    yaw_sigma_rad: float
    confidence: float


def _fit_plane(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    if points.shape[0] < 3:
        raise ValueError("at least three 3-D points are required")
    centroid = np.mean(points, axis=0)
    centered = points - centroid
    covariance = centered.T @ centered / max(points.shape[0], 1)
    values, vectors = np.linalg.eigh(covariance)
    normal = vectors[:, int(np.argmin(values))]
    normal /= max(float(np.linalg.norm(normal)), 1e-12)
    residuals = np.abs(centered @ normal)
    return centroid, normal, residuals


def _deproject_grid(
    u: np.ndarray,
    v: np.ndarray,
    z: np.ndarray,
    camera_matrix: np.ndarray,
) -> np.ndarray:
    k = np.asarray(camera_matrix, dtype=float)
    fx, fy = float(k[0, 0]), float(k[1, 1])
    cx, cy = float(k[0, 2]), float(k[1, 2])
    x = (np.asarray(u, dtype=float) - cx) * np.asarray(z, dtype=float) / fx
    y = (np.asarray(v, dtype=float) - cy) * np.asarray(z, dtype=float) / fy
    return np.column_stack((x, y, np.asarray(z, dtype=float)))


def _pose_from_center_normal(
    t_base_camera: np.ndarray,
    tag_center_camera_m: np.ndarray,
    wall_normal_camera: np.ndarray,
) -> tuple[float, float, float]:
    """Convert camera-frame tag centre + wall normal to base marker-axis pose."""

    t_b_c = validate_transform(t_base_camera)
    p_c = np.asarray(tag_center_camera_m, dtype=float).reshape(3)
    n_c = np.asarray(wall_normal_camera, dtype=float).reshape(3)
    if not np.all(np.isfinite(p_c)) or not np.all(np.isfinite(n_c)):
        raise ValueError("tag centre and normal must be finite")

    r_b_c = t_b_c[:3, :3]
    p_b_tag = r_b_c @ p_c + t_b_c[:3, 3]
    n_b = r_b_c @ n_c
    n_xy = np.asarray(n_b[:2], dtype=float)
    norm_xy = float(np.linalg.norm(n_xy))
    if not math.isfinite(norm_xy) or norm_xy < 1e-6:
        raise ValueError("wall normal is degenerate in the AMR floor plane")
    n_xy /= norm_xy

    marker_to_base = -np.asarray(p_b_tag[:2], dtype=float)
    # Use the outward wall normal, i.e. the normal that points from the wall/tag
    # toward the AMR rotation centre. This resolves the plane-normal sign.
    if float(np.dot(marker_to_base, n_xy)) < 0.0:
        n_xy = -n_xy
    lateral = np.array([-n_xy[1], n_xy[0]], dtype=float)

    x_m = float(np.dot(marker_to_base, n_xy))
    y_m = float(np.dot(marker_to_base, lateral))
    normal_angle_in_base = math.atan2(float(n_xy[1]), float(n_xy[0]))
    yaw_rad = wrap_angle(math.pi - normal_angle_in_base)
    return x_m, y_m, yaw_rad


def estimate_rgbd_wall_pose(
    depth_m: np.ndarray | None,
    corners_px: np.ndarray,
    camera_matrix: np.ndarray,
    t_base_camera: np.ndarray,
    *,
    min_valid_m: float = 0.10,
    max_valid_m: float = 6.0,
    roi_scale: float = 4.0,
    min_roi_half_px: int = 24,
    max_roi_half_px: int = 180,
    sample_step_px: int = 3,
    depth_band_m: float = 0.06,
    depth_band_ratio: float = 0.04,
    min_points: int = 80,
    min_inlier_ratio: float = 0.45,
    max_plane_rms_m: float = 0.010,
    min_horizontal_span_m: float = 0.055,
) -> RgbdWallPose | None:
    """Estimate marker-axis AMR pose from the wall plane around the AprilTag.

    The ROI is deliberately larger than the printed marker because the tag is
    assumed to be fixed flat on a wall.  A robust depth band plus two-stage
    plane fit suppresses foreground/background contamination.  The tag centre
    itself comes from the AprilTag pixel centre, intersected with the fitted
    metric plane, so translation does not inherit PnP's planar yaw ambiguity.
    """

    if depth_m is None or np.asarray(depth_m).ndim != 2:
        return None
    depth = np.asarray(depth_m, dtype=float)
    corners = np.asarray(corners_px, dtype=float).reshape(4, 2)
    if not np.all(np.isfinite(corners)):
        return None
    k = np.asarray(camera_matrix, dtype=float)
    if k.shape != (3, 3) or not np.all(np.isfinite(k)):
        return None

    h, w = depth.shape
    center_px = np.mean(corners, axis=0)
    edges = np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)
    side_px = max(1.0, float(np.median(edges)))
    half = int(round(max(float(min_roi_half_px), 0.5 * float(roi_scale) * side_px)))
    half = max(4, min(int(max_roi_half_px), half))
    cu, cv = float(center_px[0]), float(center_px[1])
    x0, x1 = max(0, int(math.floor(cu)) - half), min(w, int(math.ceil(cu)) + half + 1)
    y0, y1 = max(0, int(math.floor(cv)) - half), min(h, int(math.ceil(cv)) + half + 1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None

    # Establish the wall's approximate distance from a small centre patch. If
    # that patch happens to be invalid, fall back to the entire expanded ROI.
    cr = max(2, min(8, int(round(side_px * 0.12))))
    cx0, cx1 = max(0, int(round(cu)) - cr), min(w, int(round(cu)) + cr + 1)
    cy0, cy1 = max(0, int(round(cv)) - cr), min(h, int(round(cv)) + cr + 1)
    centre_values = depth[cy0:cy1, cx0:cx1].reshape(-1)
    centre_values = centre_values[
        np.isfinite(centre_values)
        & (centre_values >= float(min_valid_m))
        & (centre_values <= float(max_valid_m))
    ]
    roi_values = depth[y0:y1, x0:x1].reshape(-1)
    roi_values = roi_values[
        np.isfinite(roi_values)
        & (roi_values >= float(min_valid_m))
        & (roi_values <= float(max_valid_m))
    ]
    if centre_values.size >= 5:
        reference_depth = float(np.median(centre_values))
    elif roi_values.size >= max(20, int(min_points) // 2):
        reference_depth = float(np.median(roi_values))
    else:
        return None

    step = max(1, int(sample_step_px))
    vv, uu = np.mgrid[y0:y1:step, x0:x1:step]
    zz = depth[vv, uu]
    band = max(float(depth_band_m), float(depth_band_ratio) * reference_depth)
    valid = (
        np.isfinite(zz)
        & (zz >= float(min_valid_m))
        & (zz <= float(max_valid_m))
        & (np.abs(zz - reference_depth) <= band)
    )
    if int(np.count_nonzero(valid)) < int(min_points):
        return None
    points = _deproject_grid(uu[valid], vv[valid], zz[valid], k)

    try:
        centroid, normal, residuals = _fit_plane(points)
    except (ValueError, np.linalg.LinAlgError):
        return None

    # Robustly reject non-wall pixels. The floor/objects may share a similar
    # depth, so use actual point-to-plane residual rather than depth alone.
    med = float(np.median(residuals))
    mad = float(np.median(np.abs(residuals - med)))
    robust_sigma = max(0.0005, 1.4826 * mad)
    threshold = max(0.0025, med + 3.5 * robust_sigma)
    inliers = residuals <= threshold
    if int(np.count_nonzero(inliers)) < int(min_points):
        return None
    try:
        centroid, normal, residuals2 = _fit_plane(points[inliers])
    except (ValueError, np.linalg.LinAlgError):
        return None

    rms = float(np.sqrt(np.mean(np.square(residuals2))))
    inlier_count = int(np.count_nonzero(inliers))
    inlier_ratio = inlier_count / max(points.shape[0], 1)
    if not math.isfinite(rms) or rms > float(max_plane_rms_m):
        return None
    if inlier_ratio < float(min_inlier_ratio):
        return None

    # Wall/tag outward normal points back toward the camera, so it should have
    # negative optical Z for a visible wall in front of the RealSense.
    if float(normal[2]) > 0.0:
        normal = -normal

    fx, fy = float(k[0, 0]), float(k[1, 1])
    cx, cy = float(k[0, 2]), float(k[1, 2])
    ray = np.array([(cu - cx) / fx, (cv - cy) / fy, 1.0], dtype=float)
    denominator = float(np.dot(normal, ray))
    if abs(denominator) < 1e-5:
        return None
    ray_scale = float(np.dot(normal, centroid) / denominator)
    if not math.isfinite(ray_scale) or ray_scale <= 0.0:
        return None
    tag_center_camera = ray * ray_scale

    # Estimate the physical horizontal baseline represented by the fitted ROI.
    # A wider baseline and lower residual give a more trustworthy wall normal.
    horizontal_span_m = abs(ray_scale) * float(x1 - x0) / max(abs(fx), 1e-6)
    if horizontal_span_m < float(min_horizontal_span_m):
        return None

    try:
        x_m, y_m, yaw_rad = _pose_from_center_normal(
            t_base_camera,
            tag_center_camera,
            normal,
        )
    except ValueError:
        return None

    # Conservative measurement uncertainty for the downstream EKF.  The angle
    # floor prevents us from over-trusting structured RealSense depth error.
    position_sigma = max(0.0015, min(0.020, 1.35 * rms + 0.0006 * ray_scale))
    geometric_angle = math.atan2(max(rms, 0.0007), max(horizontal_span_m, 0.04))
    yaw_sigma = max(math.radians(0.15), min(math.radians(4.0), 1.35 * geometric_angle))
    rms_score = float(np.clip(1.0 - rms / max(float(max_plane_rms_m), 1e-6), 0.0, 1.0))
    span_score = float(np.clip(horizontal_span_m / 0.18, 0.0, 1.0))
    confidence = float(np.clip(0.50 * inlier_ratio + 0.30 * rms_score + 0.20 * span_score, 0.0, 1.0))

    return RgbdWallPose(
        x_m=float(x_m),
        y_m=float(y_m),
        yaw_rad=float(yaw_rad),
        tag_center_camera_m=np.asarray(tag_center_camera, dtype=float),
        wall_normal_camera=np.asarray(normal, dtype=float),
        plane_rms_m=rms,
        inlier_ratio=float(inlier_ratio),
        inlier_count=inlier_count,
        roi_width_px=int(x1 - x0),
        roi_height_px=int(y1 - y0),
        horizontal_span_m=float(horizontal_span_m),
        position_sigma_m=float(position_sigma),
        yaw_sigma_rad=float(yaw_sigma),
        confidence=confidence,
    )
