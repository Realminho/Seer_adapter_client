"""Coordinate transforms used by vision and control.

Notation follows robotics convention: ``T_A_B`` maps a point expressed in
frame B into frame A.  OpenCV PnP returns T_C_M (marker -> camera).  The fixed
camera extrinsic is T_B_C (camera -> base/rotation-centre).  Therefore:

    T_M_B = inverse(T_B_C @ T_C_M)
    T_D_B = inverse(T_M_D) @ T_M_B

The dock frame D has +x pointing out from the docking wall, +y to the left, and
+z upward.  A correctly docked base faces -x, hence yaw_error = yaw(T_D_B)-pi.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def wrap_angle(angle: float) -> float:
    """Wrap radians to [-pi, pi)."""
    return float((angle + math.pi) % (2.0 * math.pi) - math.pi)


def se2(x: float, y: float, yaw: float) -> np.ndarray:
    """Create a 3x3 SE(2) homogeneous transform."""
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[c, -s, x], [s, c, y], [0.0, 0.0, 1.0]], dtype=float)


def pose2(transform: np.ndarray) -> tuple[float, float, float]:
    """Extract x, y and yaw from an SE(2) transform."""
    t = np.asarray(transform, dtype=float)
    if t.shape != (3, 3):
        raise ValueError("SE(2) transform must be 3x3")
    return float(t[0, 2]), float(t[1, 2]), math.atan2(t[1, 0], t[0, 0])


def transform_from_xyz_rpy(
    xyz: Iterable[float], rpy: Iterable[float]
) -> np.ndarray:
    """Create a 4x4 transform from xyz and fixed-axis roll/pitch/yaw."""
    x, y, z = [float(v) for v in xyz]
    roll, pitch, yaw = [float(v) for v in rpy]
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]], dtype=float)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=float)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]], dtype=float)
    result = np.eye(4, dtype=float)
    result[:3, :3] = rz @ ry @ rx
    result[:3, 3] = (x, y, z)
    return result


def camera_mount_transform(
    xyz_m: Iterable[float], mount_rpy_rad: Iterable[float]
) -> np.ndarray:
    """Return T_B_C for a forward-facing OpenCV optical camera.

    Base convention: +x forward, +y left, +z up.  OpenCV optical convention:
    +x image-right, +y image-down, +z optical-forward.  ``mount_rpy_rad`` is
    roll/pitch/yaw of the camera's forward direction relative to base forward.
    """
    xyz = [float(v) for v in xyz_m]
    rpy = [float(v) for v in mount_rpy_rad]
    if len(xyz) != 3 or len(rpy) != 3:
        raise ValueError("camera xyz and rpy must each contain three values")
    housing = transform_from_xyz_rpy(xyz, rpy)
    optical_to_forward = np.eye(4, dtype=float)
    optical_to_forward[:3, :3] = np.array(
        [[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]],
        dtype=float,
    )
    return housing @ optical_to_forward


def validate_transform(transform: np.ndarray, shape: tuple[int, int] = (4, 4)) -> np.ndarray:
    t = np.asarray(transform, dtype=float)
    if t.shape != shape or not np.all(np.isfinite(t)):
        raise ValueError(f"transform must be a finite {shape[0]}x{shape[1]} matrix")
    return t


def base_pose_in_dock(
    t_base_camera: np.ndarray,
    t_camera_marker: np.ndarray,
    t_marker_dock: np.ndarray,
) -> tuple[float, float, float]:
    """Return base rotation-centre x, y, yaw error in the dock frame.

    ``t_marker_dock`` defines how the printed marker frame is mounted relative
    to the floor-aligned dock frame.  It must be measured once for the station.
    """
    t_b_c = validate_transform(t_base_camera)
    t_c_m = validate_transform(t_camera_marker)
    t_m_d = validate_transform(t_marker_dock)
    t_m_b = np.linalg.inv(t_b_c @ t_c_m)
    t_d_b = np.linalg.inv(t_m_d) @ t_m_b
    x, y = float(t_d_b[0, 3]), float(t_d_b[1, 3])
    base_yaw = math.atan2(float(t_d_b[1, 0]), float(t_d_b[0, 0]))
    return x, y, wrap_angle(base_yaw - math.pi)


def planar_camera_marker_to_dock(
    t_base_camera_se2: np.ndarray,
    t_camera_marker_se2: np.ndarray,
    t_marker_dock_se2: np.ndarray | None = None,
) -> tuple[float, float, float]:
    """2-D equivalent of :func:`base_pose_in_dock`, useful in unit tests."""
    t_b_c = validate_transform(t_base_camera_se2, (3, 3))
    t_c_m = validate_transform(t_camera_marker_se2, (3, 3))
    t_m_d = np.eye(3) if t_marker_dock_se2 is None else validate_transform(t_marker_dock_se2, (3, 3))
    t_d_b = np.linalg.inv(t_m_d) @ np.linalg.inv(t_b_c @ t_c_m)
    x, y, yaw = pose2(t_d_b)
    return x, y, wrap_angle(yaw - math.pi)


def base_pose_from_marker_center_axis(
    t_base_camera: np.ndarray,
    t_camera_marker: np.ndarray,
) -> tuple[float, float, float]:
    """Return AMR rotation-centre pose relative to the marker's physical centre axis.

    This is the direct geometry requested for docking.  It does *not* use the
    camera optical centre as the docking target and it does not need a separate
    floor/dock transform.  The detected marker centre and the marker +Z normal
    define the docking axis.  The fixed camera extrinsic converts both into the
    AMR base frame whose origin is the AMR rotation centre.

    Output convention matches :class:`DockState` used by the controller:

    * ``x``: distance from marker centre to AMR rotation centre measured along
      the marker normal (positive on the approach side),
    * ``y``: signed perpendicular distance from the AMR rotation centre to the
      marker-centre normal axis,
    * ``yaw``: AMR heading error, zero when the AMR +X axis points directly
      toward the marker along that normal axis.

    OpenCV ArUco/AprilTag object points lie in the marker XY plane, therefore
    marker +Z is the plane normal.  PnP can occasionally return the opposite
    normal sign for a planar target, so the normal is flipped when necessary
    to point from the marker toward the AMR rotation centre.
    """
    t_b_c = validate_transform(t_base_camera)
    t_c_m = validate_transform(t_camera_marker)

    # Marker frame expressed in the AMR base frame.  Base origin is the AMR
    # rotation centre, so p_BM is the vector rotation-centre -> marker-centre.
    t_b_m = t_b_c @ t_c_m
    p_bm = np.asarray(t_b_m[:2, 3], dtype=float)

    # Marker +Z is the physical normal / centre-axis direction.  Project to the
    # AMR floor plane because the controller is planar.
    normal_xy = np.asarray(t_b_m[:2, 2], dtype=float)
    norm = float(np.linalg.norm(normal_xy))
    if not math.isfinite(norm) or norm < 1e-6:
        raise ValueError("marker normal is degenerate after floor-plane projection")
    normal_xy /= norm

    # Vector marker-centre -> AMR rotation-centre, expressed in base frame.
    marker_to_base = -p_bm

    # Choose the normal direction that points from the marker toward the AMR.
    # This removes the +/- plane-normal ambiguity without using camera DU.
    if float(np.dot(marker_to_base, normal_xy)) < 0.0:
        normal_xy = -normal_xy

    # Axis-local +Y is 90 degrees left of +X(normal).  This definition exactly
    # matches the dock-frame lateral sign used by the staged controller.
    lateral_axis = np.array([-normal_xy[1], normal_xy[0]], dtype=float)

    x = float(np.dot(marker_to_base, normal_xy))
    y = float(np.dot(marker_to_base, lateral_axis))

    # In the AMR base frame, perfect docking sees the marker outward normal at
    # angle pi (straight behind the robot's +X direction).  Convert that to the
    # controller's yaw-error convention where perfect alignment is zero.
    normal_angle_in_base = math.atan2(float(normal_xy[1]), float(normal_xy[0]))
    yaw_error = wrap_angle(math.pi - normal_angle_in_base)
    return x, y, yaw_error
