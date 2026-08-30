import math
import unittest

import numpy as np

from seer_docking.geometry import camera_mount_transform
from seer_docking.rgbd_pose import estimate_rgbd_wall_pose


class RgbdWallPoseTests(unittest.TestCase):
    @staticmethod
    def _synthetic_depth(width=640, height=480, yaw_deg=0.0, z0=1.0):
        fx = fy = 600.0
        cx, cy = width / 2.0, height / 2.0
        k = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=float)
        # Visible wall outward normal points toward the camera (negative Z).
        a = math.radians(yaw_deg)
        n = np.array([math.sin(a), 0.0, -math.cos(a)], dtype=float)
        p0 = np.array([0.0, 0.0, z0], dtype=float)
        vv, uu = np.mgrid[0:height, 0:width]
        rx = (uu - cx) / fx
        ry = (vv - cy) / fy
        denom = n[0] * rx + n[1] * ry + n[2]
        numer = float(np.dot(n, p0))
        depth = numer / denom
        depth = depth.astype(np.float32)
        return k, depth

    def test_front_wall_gives_zero_yaw_and_mount_corrected_distance(self):
        k, depth = self._synthetic_depth(yaw_deg=0.0, z0=1.0)
        corners = np.array([[300, 220], [340, 220], [340, 260], [300, 260]], dtype=float)
        t_b_c = camera_mount_transform([0.375, 0.0, 0.62], [0.0, 0.0, 0.0])
        pose = estimate_rgbd_wall_pose(depth, corners, k, t_b_c)
        self.assertIsNotNone(pose)
        assert pose is not None
        self.assertAlmostEqual(pose.x_m, 1.375, places=2)
        self.assertAlmostEqual(pose.y_m, 0.0, places=2)
        self.assertAlmostEqual(math.degrees(pose.yaw_rad), 0.0, places=1)
        self.assertLess(pose.plane_rms_m, 0.001)

    def test_wall_plane_yaw_recovers_from_large_roi_not_small_tag_pnp(self):
        k, depth = self._synthetic_depth(yaw_deg=12.0, z0=1.2)
        corners = np.array([[308, 228], [332, 228], [332, 252], [308, 252]], dtype=float)
        t_b_c = camera_mount_transform([0.375, 0.0, 0.62], [0.0, 0.0, 0.0])
        pose = estimate_rgbd_wall_pose(depth, corners, k, t_b_c, roi_scale=5.0)
        self.assertIsNotNone(pose)
        assert pose is not None
        # Sign follows the existing marker-axis controller convention; magnitude
        # is the important synthetic plane-recovery check here.
        self.assertAlmostEqual(abs(math.degrees(pose.yaw_rad)), 12.0, delta=0.4)
        self.assertGreater(pose.horizontal_span_m, 0.10)
        self.assertGreater(pose.confidence, 0.6)

    def test_foreground_depth_outliers_are_rejected(self):
        k, depth = self._synthetic_depth(yaw_deg=-8.0, z0=0.9)
        # Inject a foreground object into part of the expanded wall ROI.
        depth[180:230, 360:430] = 0.65
        corners = np.array([[300, 220], [340, 220], [340, 260], [300, 260]], dtype=float)
        t_b_c = camera_mount_transform([0.375, 0.0, 0.62], [0.0, 0.0, 0.0])
        pose = estimate_rgbd_wall_pose(depth, corners, k, t_b_c)
        self.assertIsNotNone(pose)
        assert pose is not None
        self.assertAlmostEqual(abs(math.degrees(pose.yaw_rad)), 8.0, delta=0.8)
        self.assertLess(pose.plane_rms_m, 0.004)


if __name__ == "__main__":
    unittest.main()
