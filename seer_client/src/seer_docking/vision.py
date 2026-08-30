"""Configurable ArUco/AprilTag detection with IPPE square pose estimation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np


@dataclass(slots=True)
class TagDetection:
    tag_id: int
    t_camera_marker: np.ndarray
    corners_px: np.ndarray
    reprojection_error_px: float
    marker_pitch_deg: float
    marker_pitch_raw_deg: float
    marker_side_px: float = 0.0
    angle_reliable: bool = True
    angle_reliability_reason: str = ""


def effective_marker_side_px(corners_px: np.ndarray) -> float:
    """Return a foreshortening-aware effective square side length in pixels."""

    corners = np.asarray(corners_px, dtype=float).reshape(4, 2)
    edge_lengths = np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)
    horizontal = 0.5 * (edge_lengths[0] + edge_lengths[2])
    vertical = 0.5 * (edge_lengths[1] + edge_lengths[3])
    return float(np.sqrt(max(0.0, horizontal * vertical)))


def marker_horizontal_pitch_deg(t_camera_marker: np.ndarray) -> float:
    """Return the marker's left/right face angle using the user's pitch convention.

    OpenCV optical axes are x-right, y-down and z-forward. A marker facing the
    camera has a normal near ``-z``. This angle therefore uses the normal's x/z
    components and changes when the marker is turned left or right; vertical
    up/down tilt is intentionally excluded.
    """

    transform = np.asarray(t_camera_marker, dtype=float)
    if transform.shape != (4, 4):
        raise ValueError("t_camera_marker must be a 4x4 matrix")
    marker_normal = transform[:3, 2].copy()
    if marker_normal[2] > 0.0:
        marker_normal = -marker_normal
    return float(
        np.degrees(
            np.arctan2(float(marker_normal[0]), max(1e-9, -float(marker_normal[2])))
        )
    )


def wrap_degrees(angle_deg: float) -> float:
    return float((float(angle_deg) + 180.0) % 360.0 - 180.0)


class StableAngleFilter:
    """Median plus adaptive low-pass filter for a nearly static marker angle."""

    def __init__(
        self,
        median_window: int = 7,
        hold_deadband_deg: float = 0.20,
        slow_alpha: float = 0.10,
        fast_alpha: float = 0.55,
        fast_threshold_deg: float = 2.0,
        oblique_deadband_gain: float = 0.015,
        max_hold_deadband_deg: float = 1.20,
    ) -> None:
        window = max(1, int(median_window))
        if window % 2 == 0:
            window += 1
        self.samples: deque[float] = deque(maxlen=window)
        self.hold_deadband_deg = max(0.0, float(hold_deadband_deg))
        self.slow_alpha = float(np.clip(slow_alpha, 0.01, 1.0))
        self.fast_alpha = float(np.clip(fast_alpha, self.slow_alpha, 1.0))
        self.fast_threshold_deg = max(self.hold_deadband_deg + 1e-6, float(fast_threshold_deg))
        self.oblique_deadband_gain = max(0.0, float(oblique_deadband_gain))
        self.max_hold_deadband_deg = max(
            self.hold_deadband_deg,
            float(max_hold_deadband_deg),
        )
        self.value_deg: float | None = None

    def reset(self, value_deg: float | None = None) -> float | None:
        self.samples.clear()
        self.value_deg = None if value_deg is None else wrap_degrees(value_deg)
        if self.value_deg is not None:
            self.samples.append(self.value_deg)
        return self.value_deg

    def update(self, measurement_deg: float) -> float:
        measurement = wrap_degrees(measurement_deg)
        if self.value_deg is None:
            self.reset(measurement)
            return measurement

        # Unwrap the new sample around the current estimate before median
        # filtering, so values near -180/+180 do not average toward zero.
        unwrapped = self.value_deg + wrap_degrees(measurement - self.value_deg)
        self.samples.append(unwrapped)
        target = float(np.median(np.asarray(self.samples, dtype=float)))
        delta = target - self.value_deg
        magnitude = abs(delta)
        effective_deadband = min(
            self.max_hold_deadband_deg,
            self.hold_deadband_deg
            + self.oblique_deadband_gain * min(80.0, abs(self.value_deg)),
        )
        if magnitude <= effective_deadband:
            return wrap_degrees(self.value_deg)

        effective_fast_threshold = max(
            self.fast_threshold_deg,
            effective_deadband + 0.5,
        )
        blend = np.clip(
            (magnitude - effective_deadband)
            / (effective_fast_threshold - effective_deadband),
            0.0,
            1.0,
        )
        # Oblique views are noisier, so the slow path becomes more conservative
        # as |pitch| grows. Large confirmed changes still use fast_alpha.
        oblique_slow_scale = max(0.35, 1.0 - min(70.0, abs(self.value_deg)) / 100.0)
        slow_alpha = self.slow_alpha * oblique_slow_scale
        alpha = slow_alpha + float(blend) * (self.fast_alpha - slow_alpha)
        self.value_deg += alpha * delta
        self.value_deg = wrap_degrees(self.value_deg)
        return self.value_deg


def normalize_dictionary_name(name: str) -> str:
    """Return an OpenCV predefined-dictionary constant name.

    Both ``4X4_50`` and ``DICT_4X4_50`` are accepted so config files remain
    easy to edit by hand.
    """

    normalized = str(name).strip().upper()
    if not normalized:
        raise ValueError("marker dictionary name must not be empty")
    normalized = normalized if normalized.startswith("DICT_") else f"DICT_{normalized}"
    # OpenCV spells the AprilTag family separator with a lowercase ``h``.
    april_tag_names = {
        "DICT_APRILTAG_16H5": "DICT_APRILTAG_16h5",
        "DICT_APRILTAG_25H9": "DICT_APRILTAG_25h9",
        "DICT_APRILTAG_36H10": "DICT_APRILTAG_36h10",
        "DICT_APRILTAG_36H11": "DICT_APRILTAG_36h11",
    }
    return april_tag_names.get(normalized, normalized)


def load_camera_calibration(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return np.asarray(data["camera_matrix"], dtype=float), np.asarray(data["dist_coeffs"], dtype=float)


class SquareMarkerPoseDetector:
    """Detect one configured ArUco/AprilTag ID and estimate its square pose."""

    def __init__(
        self,
        camera_matrix: np.ndarray,
        distortion: np.ndarray,
        tag_size_m: float,
        target_id: int,
        dictionary_name: str = "DICT_APRILTAG_36h11",
        corner_smoothing_alpha: float = 1.0,
        corner_median_window: int = 5,
        corner_outlier_px: float = 1.0,
        corner_motion_reset_px: float = 18.0,
        max_tracking_misses: int = 5,
        angle_median_window: int = 7,
        angle_hold_deadband_deg: float = 0.20,
        angle_slow_alpha: float = 0.10,
        angle_fast_alpha: float = 0.55,
        angle_fast_threshold_deg: float = 2.0,
        angle_oblique_deadband_gain: float = 0.015,
        angle_max_hold_deadband_deg: float = 1.20,
        pose_candidate_error_margin_px: float = 0.35,
        min_angle_marker_side_px: float = 32.0,
        angle_reliability_hysteresis_px: float = 5.0,
    ) -> None:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover
            raise ImportError("Install OpenCV support with: pip install -e .[vision]") from exc
        self.cv2 = cv2
        self.k = np.asarray(camera_matrix, dtype=float)
        self.dist = np.asarray(distortion, dtype=float)
        self.tag_size_m = float(tag_size_m)
        self.target_id = int(target_id)
        self.corner_smoothing_alpha = float(np.clip(corner_smoothing_alpha, 0.01, 1.0))
        corner_window = max(1, int(corner_median_window))
        if corner_window % 2 == 0:
            corner_window += 1
        self.corner_outlier_px = max(0.0, float(corner_outlier_px))
        self.corner_motion_reset_px = max(1.0, float(corner_motion_reset_px))
        self._corner_history: deque[np.ndarray] = deque(maxlen=corner_window)
        self.max_tracking_misses = max(1, int(max_tracking_misses))
        self.pose_candidate_error_margin_px = max(0.0, float(pose_candidate_error_margin_px))
        self.min_angle_marker_side_px = max(1.0, float(min_angle_marker_side_px))
        self.angle_reliability_hysteresis_px = max(
            0.0,
            float(angle_reliability_hysteresis_px),
        )
        self._angle_reliable_latched = False
        self._filtered_corners_px: np.ndarray | None = None
        self._tracking_misses = 0
        self._last_rotation: np.ndarray | None = None
        self._pitch_filter = StableAngleFilter(
            median_window=angle_median_window,
            hold_deadband_deg=angle_hold_deadband_deg,
            slow_alpha=angle_slow_alpha,
            fast_alpha=angle_fast_alpha,
            fast_threshold_deg=angle_fast_threshold_deg,
            oblique_deadband_gain=angle_oblique_deadband_gain,
            max_hold_deadband_deg=angle_max_hold_deadband_deg,
        )
        self.dictionary_name = normalize_dictionary_name(dictionary_name)
        dictionary_id = getattr(cv2.aruco, self.dictionary_name, None)
        if dictionary_id is None:
            available = sorted(name for name in dir(cv2.aruco) if name.startswith("DICT_"))
            raise ValueError(
                f"unknown OpenCV marker dictionary {self.dictionary_name!r}; "
                f"available examples: {', '.join(available[:12])}"
            )
        dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        marker_count = int(dictionary.bytesList.shape[0])
        if not 0 <= self.target_id < marker_count:
            raise ValueError(
                f"tag_id={self.target_id} is outside {self.dictionary_name} "
                f"range 0..{marker_count - 1}"
            )
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        params.cornerRefinementWinSize = 7
        params.cornerRefinementMaxIterations = 50
        params.cornerRefinementMinAccuracy = 0.01
        self.detector = cv2.aruco.ArucoDetector(dictionary, params)
        h = 0.5 * self.tag_size_m
        self.object_points = np.array(
            [[-h, h, 0.0], [h, h, 0.0], [h, -h, 0.0], [-h, -h, 0.0]],
            dtype=np.float32,
        )

    def reset_tracking(self) -> None:
        self._filtered_corners_px = None
        self._corner_history.clear()
        self._tracking_misses = 0
        self._pitch_filter.reset()
        self._last_rotation = None
        self._angle_reliable_latched = False

    def _target_corners(self, frame_bgr: np.ndarray) -> np.ndarray | None:
        corners, ids, _ = self.detector.detectMarkers(frame_bgr)
        if ids is None:
            return None
        matches = np.flatnonzero(ids.reshape(-1) == self.target_id)
        if matches.size == 0:
            return None
        return np.asarray(corners[int(matches[0])], dtype=np.float32).reshape(4, 2)

    def _filter_corners(self, raw_points: np.ndarray, reacquired: bool = False) -> np.ndarray:
        """Suppress isolated corner jitter without creating a moving crop.

        The detector still searches the complete image on every frame.  This
        filter only stabilises the four detected corner coordinates before PnP:
        a short temporal median rejects a single vibrating corner, then an EMA
        removes sub-pixel shimmer.  A coherent/fast centre movement clears the
        history immediately so hand-held or moving-camera tracking does not lag.
        """

        raw = np.asarray(raw_points, dtype=np.float32).reshape(4, 2)
        if self._filtered_corners_px is None or reacquired:
            self._corner_history.clear()
            self._corner_history.append(raw.copy())
            return raw.copy()

        displacement = raw - self._filtered_corners_px
        coherent_motion = float(np.linalg.norm(np.median(displacement, axis=0)))
        fast_corner_count = int(
            np.count_nonzero(np.linalg.norm(displacement, axis=1) >= self.corner_motion_reset_px)
        )
        if coherent_motion >= self.corner_motion_reset_px or fast_corner_count >= 3:
            self._corner_history.clear()
            self._corner_history.append(raw.copy())
            return raw.copy()

        self._corner_history.append(raw.copy())
        history = np.stack(tuple(self._corner_history), axis=0)
        median_points = np.median(history, axis=0).astype(np.float32)

        # If just one corner departs from the temporal consensus, cap its
        # contribution.  Coherent motion of the whole square is retained.
        corner_displacement = raw - self._filtered_corners_px
        coherent_shift = np.median(corner_displacement, axis=0)
        predicted_points = self._filtered_corners_px + coherent_shift
        incoherent = raw - predicted_points
        incoherent_norm = np.linalg.norm(incoherent, axis=1)
        robust_points = 0.75 * median_points + 0.25 * raw
        if self.corner_outlier_px > 0.0:
            for index, magnitude in enumerate(incoherent_norm):
                if magnitude > self.corner_outlier_px:
                    direction = incoherent[index] / max(float(magnitude), 1e-6)
                    robust_points[index] = (
                        predicted_points[index] + direction * self.corner_outlier_px
                    )
        alpha = self.corner_smoothing_alpha
        return np.asarray(
            alpha * robust_points + (1.0 - alpha) * self._filtered_corners_px,
            dtype=np.float32,
        )

    def detect(self, frame_bgr: np.ndarray) -> TagDetection | None:
        cv2 = self.cv2
        # Always search the full image. This is more robust than a fixed crop
        # when the camera or marker moves rapidly across the frame.
        raw_points = self._target_corners(frame_bgr)
        if raw_points is None:
            self._tracking_misses += 1
            if self._tracking_misses >= self.max_tracking_misses:
                self.reset_tracking()
            return None

        reacquired = self._tracking_misses > 0
        self._tracking_misses = 0
        image_points = self._filter_corners(raw_points, reacquired=reacquired)
        self._filtered_corners_px = image_points.copy()
        marker_side_px = effective_marker_side_px(image_points)
        reliability_threshold_px = self.min_angle_marker_side_px
        if self._angle_reliable_latched:
            reliability_threshold_px -= self.angle_reliability_hysteresis_px
        angle_reliable = marker_side_px >= max(1.0, reliability_threshold_px)
        self._angle_reliable_latched = angle_reliable
        result = cv2.solvePnPGeneric(
            self.object_points,
            image_points,
            self.k,
            self.dist,
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )
        ok, rvecs, tvecs = bool(result[0]), result[1], result[2]
        errors = result[3] if len(result) > 3 else [np.array([np.inf])] * len(rvecs)
        if not ok or len(rvecs) == 0:
            return None
        candidates: list[tuple[float, np.ndarray, np.ndarray, np.ndarray]] = []
        for rvec, tvec, error in zip(rvecs, tvecs, errors):
            t = np.asarray(tvec, dtype=float).reshape(3)
            if t[2] <= 0.0:
                continue
            rvec_array = np.asarray(rvec, dtype=float)
            rotation, _ = cv2.Rodrigues(rvec_array)
            candidates.append(
                (float(np.asarray(error).reshape(-1)[0]), rvec_array, t, rotation)
            )
        if not candidates:
            return None
        minimum_error = min(item[0] for item in candidates)
        eligible = [
            item
            for item in candidates
            if item[0] <= minimum_error + self.pose_candidate_error_margin_px
        ]
        if self._last_rotation is None or len(eligible) == 1:
            error, rvec, tvec, rotation = min(eligible, key=lambda item: item[0])
        else:
            def rotation_jump(candidate: tuple[float, np.ndarray, np.ndarray, np.ndarray]) -> float:
                relative = self._last_rotation.T @ candidate[3]
                cosine = np.clip((np.trace(relative) - 1.0) * 0.5, -1.0, 1.0)
                return float(np.arccos(cosine))

            error, rvec, tvec, rotation = min(eligible, key=rotation_jump)

        if hasattr(cv2, "solvePnPRefineLM"):
            try:
                refined_rvec, refined_tvec = cv2.solvePnPRefineLM(
                    self.object_points,
                    image_points,
                    self.k,
                    self.dist,
                    rvec.copy(),
                    np.asarray(tvec, dtype=float).reshape(3, 1).copy(),
                )
                refined_t = np.asarray(refined_tvec, dtype=float).reshape(3)
                if refined_t[2] > 0.0:
                    rvec = np.asarray(refined_rvec, dtype=float)
                    tvec = refined_t
                    rotation, _ = cv2.Rodrigues(rvec)
                    projected, _ = cv2.projectPoints(
                        self.object_points,
                        rvec,
                        np.asarray(tvec, dtype=float).reshape(3, 1),
                        self.k,
                        self.dist,
                    )
                    residual = projected.reshape(4, 2) - image_points
                    error = float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))
            except cv2.error:
                pass

        t_c_m = np.eye(4, dtype=float)
        t_c_m[:3, :3] = rotation
        t_c_m[:3, 3] = tvec
        raw_pitch_deg = marker_horizontal_pitch_deg(t_c_m)
        if not angle_reliable:
            # A tiny planar square can have a low reprojection residual while
            # its IPPE orientation jumps by many degrees.  Preserve a previous
            # trustworthy value for display only; callers must honor the
            # reliability flag and must not use this sample for motion.
            filtered_pitch_deg = (
                raw_pitch_deg
                if self._pitch_filter.value_deg is None
                else float(self._pitch_filter.value_deg)
            )
            reliability_reason = (
                f"marker {marker_side_px:.1f}px < "
                f"{self.min_angle_marker_side_px:.1f}px"
            )
        else:
            self._last_rotation = rotation.copy()
            reliability_reason = ""
            if reacquired or self._pitch_filter.value_deg is None:
                filtered_pitch_deg = float(self._pitch_filter.reset(raw_pitch_deg))
            else:
                filtered_pitch_deg = self._pitch_filter.update(raw_pitch_deg)
        return TagDetection(
            tag_id=self.target_id,
            t_camera_marker=t_c_m,
            corners_px=image_points,
            reprojection_error_px=error,
            marker_pitch_deg=filtered_pitch_deg,
            marker_pitch_raw_deg=raw_pitch_deg,
            marker_side_px=marker_side_px,
            angle_reliable=angle_reliable,
            angle_reliability_reason=reliability_reason,
        )


# Backward-compatible import name used by earlier versions of this project.
AprilTagPoseDetector = SquareMarkerPoseDetector
