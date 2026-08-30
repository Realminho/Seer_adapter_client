import math
import numpy as np

from seer_docking.geometry import camera_mount_transform, wrap_angle
from seer_docking.lidar_pose import estimate_lidar_tag_pose
from seer_docking.localization_fusion import SeerTagRelativeFusion
from seer_docking.model import DockState


def _corners_for_base_point(point_xy, *, k, t_b_c, side_px=28.0):
    # For the default optical mounting: base [x,y,z] -> optical [-y,-z,x].
    target_b = np.array([float(point_xy[0]), float(point_xy[1]), 0.62])
    origin_b = t_b_c[:3, 3]
    vec_b = target_b - origin_b
    r_c_b = t_b_c[:3, :3].T
    vec_c = r_c_b @ vec_b
    u = k[0, 2] + k[0, 0] * vec_c[0] / vec_c[2]
    v = k[1, 2] + k[1, 1] * vec_c[1] / vec_c[2]
    h = side_px / 2.0
    return np.array([[u-h,v-h],[u+h,v-h],[u+h,v+h],[u-h,v+h]], dtype=float)


def test_lidar_rgb_pose_ignores_depth_and_recovers_wall_geometry():
    rng = np.random.default_rng(7)
    k = np.array([[600.0,0.0,320.0],[0.0,600.0,240.0],[0.0,0.0,1.0]])
    t_b_c = camera_mount_transform([0.375,0.0,0.62],[0.0,0.0,0.0])
    robot_pose = (0.0, 0.0, 0.0)
    tag_base = (1.0, 0.15)
    corners = _corners_for_base_point(tag_base, k=k, t_b_c=t_b_c)

    ys = np.linspace(-0.9,0.9,150)
    wall = np.column_stack([np.ones_like(ys), ys])
    wall += rng.normal(0.0, 0.002, wall.shape)
    clutter = rng.uniform([-0.2,-1.0],[1.6,1.0],size=(35,2))
    points = np.vstack([wall, clutter])

    pose = estimate_lidar_tag_pose(
        points,
        robot_map_pose=robot_pose,
        corners_px=corners,
        camera_matrix=k,
        t_base_camera=t_b_c,
        min_points=15,
        ransac_threshold_m=0.012,
        min_wall_span_m=0.3,
    )
    assert pose is not None
    assert abs(pose.x_m - 1.0) < 0.015
    assert abs(pose.y_m - 0.15) < 0.02
    assert abs(math.degrees(pose.yaw_rad)) < 0.8
    assert pose.wall_rms_m < 0.008
    assert pose.confidence > 0.55


def test_lidar_pose_tracks_robot_closer_using_world_laser_and_seer_pose():
    k = np.array([[600.0,0.0,320.0],[0.0,600.0,240.0],[0.0,0.0,1.0]])
    t_b_c = camera_mount_transform([0.375,0.0,0.62],[0.0,0.0,0.0])
    robot_pose = (0.4, 0.0, 0.0)
    tag_world = np.array([1.0, 0.12])
    tag_base = tag_world - np.array(robot_pose[:2])
    corners = _corners_for_base_point(tag_base, k=k, t_b_c=t_b_c)
    ys = np.linspace(-0.8,0.8,120)
    points_world = np.column_stack([np.ones_like(ys), ys])

    pose = estimate_lidar_tag_pose(
        points_world,
        robot_map_pose=robot_pose,
        corners_px=corners,
        camera_matrix=k,
        t_base_camera=t_b_c,
        min_points=12,
    )
    assert pose is not None
    assert abs(pose.x_m - 0.6) < 0.01
    assert abs(pose.y_m - 0.12) < 0.015


def test_seer_localization_anchor_holds_pose_without_new_camera_measurement():
    fusion = SeerTagRelativeFusion(min_measurement_confidence=0.2)
    initial_map = (2.0, 3.0, 0.25)
    initial = DockState(1.2, -0.08, math.radians(2.0), 0.0, 0.0)
    state, diag = fusion.update(
        initial_map, initial,
        measurement_confidence=0.9,
        measurement_source="LIDAR_WALL+RGB_CENTER",
        v=0.0, w=0.0,
    )
    assert state is not None and diag.anchored
    assert abs(state.x - initial.x) < 1e-9
    assert abs(state.y - initial.y) < 1e-9

    held, diag2 = fusion.update(
        initial_map, None,
        measurement_confidence=0.0,
        measurement_source="-",
        v=0.0, w=0.0,
    )
    assert held is not None
    assert abs(held.x - initial.x) < 1e-9
    assert abs(held.y - initial.y) < 1e-9
    assert "HOLD" in diag2.source


def test_seer_localization_rejects_large_visual_jump():
    fusion = SeerTagRelativeFusion(
        min_measurement_confidence=0.2,
        max_position_innovation_m=0.2,
        max_yaw_innovation_deg=10.0,
    )
    map_pose = (0.0,0.0,0.0)
    initial = DockState(1.0,0.0,0.0,0.0,0.0)
    fusion.update(map_pose, initial, measurement_confidence=0.9, measurement_source="LIDAR", v=0,w=0)
    outlier = DockState(2.0,0.8,math.radians(40),0,0)
    state, diag = fusion.update(map_pose, outlier, measurement_confidence=0.95, measurement_source="DEPTH_BAD", v=0,w=0)
    assert state is not None
    assert abs(state.x - 1.0) < 1e-6
    assert abs(state.y) < 1e-6
    assert not diag.accepted_correction
    assert "OUTLIER" in diag.source


def test_stationary_lock_freezes_pose_against_pnp_and_localization_jitter():
    fusion = SeerTagRelativeFusion(
        min_measurement_confidence=0.2,
        pnp_anchor_window=3,
        stationary_lock_enabled=True,
        stationary_map_step_m=0.01,
        stationary_map_step_yaw_deg=0.3,
    )
    base_map = (4.015, -0.088, math.radians(0.01))
    pnp_samples = [
        DockState(1.54, -0.55, math.radians(-15.0), 0.0, 0.0),
        DockState(1.56, -0.53, math.radians(-16.0), 0.0, 0.0),
        DockState(1.53, -0.54, math.radians(-15.5), 0.0, 0.0),
    ]
    state = None
    for m in pnp_samples:
        state, _ = fusion.update(
            base_map, m, measurement_confidence=0.40,
            measurement_source="PNP_RGB_ONLY", v=0.0, w=0.0,
        )
    assert state is not None
    anchor = state

    # Camera PnP and SEER map pose both wobble while the physical AMR is still.
    for i in range(40):
        map_jitter = (
            base_map[0] + 0.0025 * math.sin(i * 0.7),
            base_map[1] + 0.0020 * math.cos(i * 0.9),
            base_map[2] + math.radians(0.05 * math.sin(i)),
        )
        pnp_jitter = DockState(
            1.54 + 0.06 * math.sin(i * 0.8),
            -0.54 + 0.07 * math.cos(i * 0.6),
            math.radians(-15.5 + 4.0 * math.sin(i * 0.5)),
            0.0, 0.0,
        )
        out, diag = fusion.update(
            map_jitter, pnp_jitter, measurement_confidence=0.40,
            measurement_source="PNP_RGB_ONLY", v=0.0, w=0.0,
        )
        assert out is not None
        assert abs(out.x - anchor.x) < 1e-10
        assert abs(out.y - anchor.y) < 1e-10
        assert abs(wrap_angle(out.yaw - anchor.yaw)) < 1e-10
        assert "STATIONARY LOCK" in diag.source


def test_stationary_lock_releases_smoothly_when_seer_reports_motion():
    fusion = SeerTagRelativeFusion(
        min_measurement_confidence=0.2,
        pnp_anchor_window=1,
        stationary_lock_enabled=True,
    )
    initial_map = (0.0, 0.0, 0.0)
    initial = DockState(1.0, 0.0, 0.0, 0.0, 0.0)
    anchored, _ = fusion.update(
        initial_map, initial, measurement_confidence=0.8,
        measurement_source="PNP_RGB_ONLY", v=0.0, w=0.0,
    )
    assert anchored is not None

    # Small localization jitter is hidden first.
    held, _ = fusion.update(
        (0.003, -0.002, math.radians(0.05)),
        DockState(1.02, 0.03, math.radians(2.0), 0.0, 0.0),
        measurement_confidence=0.4, measurement_source="PNP_RGB_ONLY", v=0.0, w=0.0,
    )
    assert held is not None
    assert abs(held.x - anchored.x) < 1e-10

    # Once actual velocity/map motion appears, SEER localization moves the pose.
    moved, _ = fusion.update(
        (0.103, -0.002, math.radians(0.05)),
        None, measurement_confidence=0.0, measurement_source="-", v=0.10, w=0.0,
    )
    assert moved is not None
    assert abs(moved.x - held.x) > 0.05


def test_world_axis_lock_freezes_camera_corrections_while_seer_imu_yaw_moves():
    fusion = SeerTagRelativeFusion(min_measurement_confidence=0.2, stationary_lock_enabled=False)
    initial_map = (4.0, 1.5, math.radians(10.0))
    initial = DockState(1.20, 0.001, math.radians(2.0), 0.0, 0.0)
    anchor = SeerTagRelativeFusion.anchor_pose_from_measurement(initial_map, initial)
    fusion.restore_anchor_pose(anchor, frozen=True)

    base, diag0 = fusion.update(
        initial_map,
        initial,
        measurement_confidence=0.95,
        measurement_source="LIDAR_WALL+RGB_CENTER",
        v=0.0,
        w=0.0,
    )
    assert base is not None
    assert fusion.corrections_frozen
    assert "WORLD AXIS LOCK" in diag0.source

    # A wildly wrong visual measurement must not move the locked centreline.
    noisy = DockState(1.7, 0.25, math.radians(-18.0), 0.0, 0.0)
    rotated_map = (initial_map[0], initial_map[1], initial_map[2] + math.radians(5.0))
    rotated, diag1 = fusion.update(
        rotated_map,
        noisy,
        measurement_confidence=1.0,
        measurement_source="LIDAR_WALL+RGB_CENTER",
        v=0.0,
        w=math.radians(1.0),
    )
    assert rotated is not None
    assert abs(rotated.x - base.x) < 1e-9
    assert abs(rotated.y - base.y) < 1e-9
    assert abs(math.degrees(wrap_angle(rotated.yaw - base.yaw)) - 5.0) < 1e-7
    assert not diag1.accepted_correction
    assert "WORLD AXIS LOCK" in diag1.source


def test_world_axis_anchor_round_trip_restore_predict():
    fusion = SeerTagRelativeFusion(min_measurement_confidence=0.2)
    map_pose = (2.1, -0.7, math.radians(-25.0))
    measurement = DockState(1.4, -0.032, math.radians(7.5), 0.0, 0.0)
    anchor = SeerTagRelativeFusion.anchor_pose_from_measurement(map_pose, measurement)
    fusion.restore_anchor_pose(anchor, frozen=True)
    predicted = fusion.predict(map_pose, v=0.0, w=0.0)
    assert predicted is not None
    assert abs(predicted.x - measurement.x) < 1e-9
    assert abs(predicted.y - measurement.y) < 1e-9
    assert abs(wrap_angle(predicted.yaw - measurement.yaw)) < 1e-9
