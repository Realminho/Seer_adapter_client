"""Camera-based AprilTag docking action executed through the existing SeerClient.

The camera loop owns no SEER TCP sockets.  All motion goes through the Adapter's
already-connected :class:`seer_client.client.SeerClient`, so STATE/CONTROL port
serialization, sequence validation, reconnect accounting and the API-2010
watchdog remain in one transport layer.
"""

from __future__ import annotations

import asyncio
from collections import deque
import csv
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Mapping, Optional

import numpy as np

from seer_docking import (
    CameraVisualServoController,
    DockParams,
    DockPhase,
    DockState,
    PlanarDockEKF,
    VelocityCommand,
    VisualDockObservation,
    differential_wheel_values,
    visual_target_for_base_distance,
)
from seer_docking.camera_source import (
    create_camera_source,
    depth_clearance_in_roi,
    depth_colormap,
    median_depth_at_pixel,
    probe_realsense_camera,
    probe_realsense_camera_quick,
)
from seer_docking.fov_guard import FovGuardParams, TagFovGuard, image_margin_px
from seer_docking.geometry import (
    base_pose_in_dock,
    base_pose_from_marker_center_axis,
    camera_mount_transform,
    wrap_angle,
)
from seer_docking.safety import SafetyGate
from seer_docking.seer_protocol import LiveVelocitySmoother
from seer_docking.vision import SquareMarkerPoseDetector, TagDetection
from seer_docking.rgbd_pose import RgbdWallPose, estimate_rgbd_wall_pose
from seer_docking.lidar_pose import LidarTagPose, estimate_lidar_tag_pose
from seer_docking.localization_fusion import SeerTagRelativeFusion
from seer_docking.imu_fusion import ImuMapYawFusion, ImuAttitude, parse_seer_imu_attitude

from .runtime_mailbox import atomic_write_bytes, atomic_write_json, read_json_locked, remove_file_locked
from .protocol import ApiNumber, ApiPort




class CameraBusyError(RuntimeError):
    """The physical RealSense is already owned by another Adapter action."""


class CameraOpenError(RuntimeError):
    """RealSense preflight/open failed before the first camera frame."""


def _manual_yaw_only_command(
    yaw_error_rad: float,
    *,
    speed_rps: float,
    tolerance_rad: float,
) -> tuple[VelocityCommand, bool]:
    """One-shot yaw stage command.  Translation is always disabled."""

    error = wrap_angle(float(yaw_error_rad))
    if abs(error) <= max(0.0, float(tolerance_rad)):
        return VelocityCommand(0.0, 0.0), True
    return VelocityCommand(0.0, -math.copysign(abs(float(speed_rps)), error)), False


def _manual_straight_heading_hold(
    anchor_pose: tuple[float, float, float],
    current_pose: tuple[float, float, float],
    *,
    speed_mps: float,
    kp: float,
    deadband_rad: float,
    limit_rps: float,
) -> tuple[VelocityCommand, float, float]:
    """Hold the start heading using SEER/IMU only and report cross-track drift."""

    ax, ay, target_yaw = [float(v) for v in anchor_pose]
    cx, cy, current_yaw = [float(v) for v in current_pose]
    heading_error = wrap_angle(target_yaw - current_yaw)
    dx = cx - ax
    dy = cy - ay
    cross_track = -math.sin(target_yaw) * dx + math.cos(target_yaw) * dy
    if abs(heading_error) <= max(0.0, float(deadband_rad)):
        omega = 0.0
    else:
        omega = float(np.clip(float(kp) * heading_error, -abs(float(limit_rps)), abs(float(limit_rps))))
    return VelocityCommand(float(speed_mps), omega), heading_error, cross_track


def _stable_world_axis_anchor(
    samples: deque[tuple[float, float, float]],
    *,
    required_samples: int,
    max_position_spread_m: float,
    max_yaw_spread_rad: float,
) -> tuple[float, float, float] | None:
    """Median a short series of map->dock anchors and reject an unstable lock."""

    if len(samples) < int(required_samples):
        return None
    recent = list(samples)[-int(required_samples):]
    xs = np.asarray([v[0] for v in recent], dtype=float)
    ys = np.asarray([v[1] for v in recent], dtype=float)
    yaw_ref = float(recent[-1][2])
    yaws = np.asarray([yaw_ref + wrap_angle(float(v[2]) - yaw_ref) for v in recent], dtype=float)
    mx, my, myaw = float(np.median(xs)), float(np.median(ys)), wrap_angle(float(np.median(yaws)))
    pos_spread = max(math.hypot(float(x) - mx, float(y) - my) for x, y, _ in recent)
    yaw_spread = max(abs(wrap_angle(float(yaw) - myaw)) for _, _, yaw in recent)
    if pos_spread > float(max_position_spread_m) or yaw_spread > float(max_yaw_spread_rad):
        return None
    return mx, my, myaw


def _world_axis_lock_path(status_path: Path) -> Path:
    return Path(status_path).parent / "seer-docking-world-axis-lock.json"


def _save_world_axis_lock(
    path: Path,
    *,
    anchor_pose: tuple[float, float, float],
    robot_ip: str,
    source: str,
    sample_count: int,
) -> None:
    atomic_write_json(
        path,
        {
            "schema": 1,
            "saved_at": time.time(),
            "robot_ip": str(robot_ip or ""),
            "source": str(source or "-"),
            "sample_count": int(sample_count),
            "map_to_dock": [float(anchor_pose[0]), float(anchor_pose[1]), float(anchor_pose[2])],
        },
    )


def _load_world_axis_lock(
    path: Path,
    *,
    robot_ip: str,
    max_age_s: float,
) -> tuple[tuple[float, float, float], dict[str, Any]] | None:
    try:
        payload = read_json_locked(path, timeout_s=0.35)
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None
    values = payload.get("map_to_dock")
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return None
    saved_robot = str(payload.get("robot_ip", "") or "")
    if saved_robot and str(robot_ip or "") and saved_robot != str(robot_ip or ""):
        return None
    try:
        saved_at = float(payload.get("saved_at", 0.0) or 0.0)
        anchor = tuple(float(v) for v in values)
    except (TypeError, ValueError):
        return None
    if saved_at <= 0.0 or time.time() - saved_at > max(1.0, float(max_age_s)):
        return None
    if not all(math.isfinite(v) for v in anchor):
        return None
    return (anchor[0], anchor[1], wrap_angle(anchor[2])), dict(payload)


class _StableDisplayPoseFilter:
    """Camera-only temporal filter for human-facing marker-axis geometry.

    Planar square PnP orientation is much noisier than translation, even when
    the camera and marker are physically motionless.  The controller already
    has its own EKF, so this filter is deliberately display-only: it keeps the
    WebUI/preview pose quiet without changing the motion controller's source of
    truth.  Real movement is still followed quickly through the fast path.
    """

    def __init__(
        self,
        *,
        median_window: int = 17,
        moving_median_window: int = 3,
        position_deadband_m: float = 0.0008,
        position_slow_alpha: float = 0.08,
        position_moving_alpha: float = 0.28,
        position_fast_alpha: float = 0.50,
        position_fast_threshold_m: float = 0.025,
        yaw_hold_deadband_deg: float = 0.18,
        yaw_slow_alpha: float = 0.05,
        yaw_moving_alpha: float = 0.28,
        yaw_fast_alpha: float = 0.50,
        yaw_fast_threshold_deg: float = 1.20,
    ) -> None:
        window = max(3, int(median_window))
        if window % 2 == 0:
            window += 1
        self._x_samples: deque[float] = deque(maxlen=window)
        self._y_samples: deque[float] = deque(maxlen=window)
        self._yaw_samples: deque[float] = deque(maxlen=window)
        self.moving_median_window = max(1, min(window, int(moving_median_window)))
        self.position_deadband_m = max(0.0, float(position_deadband_m))
        self.position_slow_alpha = float(np.clip(position_slow_alpha, 0.01, 1.0))
        self.position_moving_alpha = float(np.clip(position_moving_alpha, self.position_slow_alpha, 1.0))
        self.position_fast_alpha = float(np.clip(position_fast_alpha, self.position_moving_alpha, 1.0))
        self.position_fast_threshold_m = max(self.position_deadband_m + 1e-6, float(position_fast_threshold_m))
        self.yaw_hold_deadband_deg = max(0.0, float(yaw_hold_deadband_deg))
        self.yaw_slow_alpha = float(np.clip(yaw_slow_alpha, 0.01, 1.0))
        self.yaw_moving_alpha = float(np.clip(yaw_moving_alpha, self.yaw_slow_alpha, 1.0))
        self.yaw_fast_alpha = float(np.clip(yaw_fast_alpha, self.yaw_moving_alpha, 1.0))
        self.yaw_fast_threshold_deg = max(self.yaw_hold_deadband_deg + 1e-6, float(yaw_fast_threshold_deg))
        self.x: float | None = None
        self.y: float | None = None
        self.yaw_deg: float | None = None

    @staticmethod
    def _wrap_deg(value: float) -> float:
        return float((float(value) + 180.0) % 360.0 - 180.0)

    def reset(self) -> None:
        self._x_samples.clear()
        self._y_samples.clear()
        self._yaw_samples.clear()
        self.x = None
        self.y = None
        self.yaw_deg = None

    def update(
        self,
        x_m: float,
        y_m: float,
        yaw_deg: float,
        *,
        moving: bool,
    ) -> tuple[float, float, float]:
        x_m = float(x_m)
        y_m = float(y_m)
        yaw_deg = self._wrap_deg(yaw_deg)
        if self.x is None or self.y is None or self.yaw_deg is None:
            self.x, self.y, self.yaw_deg = x_m, y_m, yaw_deg
            self._x_samples.append(x_m)
            self._y_samples.append(y_m)
            self._yaw_samples.append(yaw_deg)
            return self.x, self.y, self.yaw_deg

        self._x_samples.append(x_m)
        self._y_samples.append(y_m)
        # Unwrap each incoming yaw around the current displayed estimate before
        # taking a temporal median, so +/-180 degree crossings stay continuous.
        yaw_unwrapped = self.yaw_deg + self._wrap_deg(yaw_deg - self.yaw_deg)
        self._yaw_samples.append(yaw_unwrapped)

        # Always reject one-frame spikes before drawing. During motion use a
        # shorter median window so the picture follows the real chassis with
        # only a small visual lag; while stationary use the full window.
        active_n = self.moving_median_window if moving else len(self._x_samples)
        active_n = max(1, min(active_n, len(self._x_samples)))
        tx = float(np.median(np.asarray(list(self._x_samples)[-active_n:], dtype=float)))
        ty = float(np.median(np.asarray(list(self._y_samples)[-active_n:], dtype=float)))
        tyaw = float(np.median(np.asarray(list(self._yaw_samples)[-active_n:], dtype=float)))

        dpos = math.hypot(tx - self.x, ty - self.y)
        if dpos > self.position_deadband_m:
            if dpos >= self.position_fast_threshold_m:
                pa = self.position_fast_alpha
            elif moving:
                pa = self.position_moving_alpha
            else:
                pa = self.position_slow_alpha
            self.x += pa * (tx - self.x)
            self.y += pa * (ty - self.y)

        dyaw = tyaw - self.yaw_deg
        yaw_deadband = min(self.yaw_hold_deadband_deg, 0.05) if moving else self.yaw_hold_deadband_deg
        if abs(dyaw) > yaw_deadband:
            if abs(dyaw) >= self.yaw_fast_threshold_deg:
                ya = self.yaw_fast_alpha
            elif moving:
                ya = self.yaw_moving_alpha
            else:
                ya = self.yaw_slow_alpha
            self.yaw_deg = self._wrap_deg(self.yaw_deg + ya * dyaw)

        return float(self.x), float(self.y), float(self.yaw_deg)


class CameraLease:
    """Cross-process lease for one physical RealSense device.

    The WebUI can launch several SEER Adapter processes for several robot IPs,
    while the PC still has only one RealSense.  Without a process-wide lease a
    PREVIEW action on one Adapter and a LIVE action on another can both call
    ``pipeline.start()``, leaving the second action stuck at CAMERA_OPEN and
    the browser showing the last JPEG from the first run.
    """

    def __init__(self, path: Path, token: str, owner: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self.token = str(token)
        self.owner = dict(owner)
        self.acquired = False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if os.name == "nt":
            try:
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                SYNCHRONIZE = 0x00100000
                WAIT_TIMEOUT = 0x00000102
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid)
                )
                if not handle:
                    return False
                try:
                    return int(kernel32.WaitForSingleObject(handle, 0)) == WAIT_TIMEOUT
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                # If Windows process inspection is unavailable, do not steal a
                # possibly valid camera lease.  A clean owner removes it.
                return True
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    @classmethod
    def for_camera(
        cls,
        camera_cfg: Mapping[str, Any],
        *,
        robot_ip: str,
        action_id: str,
        run_mode: str,
    ) -> "CameraLease":
        camera_type = str(camera_cfg.get("type", "realsense") or "realsense").lower()
        serial = str(camera_cfg.get("serial", "") or "").strip()
        key_raw = serial or ("default" if camera_type == "realsense" else camera_type)
        key = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key_raw) or "default"
        root = Path(tempfile.gettempdir()) / "seer-camera-docking"
        path = root / f"camera-{key}.lease.json"
        token = uuid.uuid4().hex
        owner = {
            "schema": 1,
            "token": token,
            "pid": os.getpid(),
            "robot_ip": str(robot_ip or ""),
            "action_id": str(action_id or ""),
            "run_mode": str(run_mode or ""),
            "camera_key": key,
            "created_at": time.time(),
        }
        return cls(path, token, owner)

    def acquire(self, *, wait_timeout_s: float = 0.0, poll_interval_s: float = 0.05) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        timeout_s = max(0.0, min(10.0, float(wait_timeout_s)))
        poll_s = max(0.02, min(0.25, float(poll_interval_s)))
        deadline = time.monotonic() + timeout_s
        last_busy = ""
        while True:
            try:
                fd = os.open(
                    str(self.path),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                current: dict[str, Any] = {}
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    current = {}
                try:
                    owner_pid = int(current.get("pid", 0) or 0)
                except (TypeError, ValueError):
                    owner_pid = 0
                if owner_pid and self._pid_alive(owner_pid):
                    last_busy = (
                        "RealSense is already in use: "
                        f"pid={owner_pid} robot={current.get('robot_ip','?')} "
                        f"mode={current.get('run_mode','?')} action={current.get('action_id','?')}"
                    )
                    if time.monotonic() < deadline:
                        time.sleep(poll_s)
                        continue
                    raise CameraBusyError(last_busy)
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    if time.monotonic() < deadline:
                        time.sleep(poll_s)
                        continue
                    raise CameraBusyError(f"stale camera lease cannot be cleared: {exc}") from exc
                continue
            else:
                try:
                    raw = json.dumps(self.owner, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    os.write(fd, raw)
                finally:
                    os.close(fd)
                self.acquired = True
                return

            if time.monotonic() >= deadline:
                break
        raise CameraBusyError(last_busy or "RealSense camera lease could not be acquired")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        if str(current.get("token", "") or "") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False


def preview_telemetry_path(preview_path: Path) -> Path:
    """Sidecar written from the exact worker that publishes each JPEG frame."""

    preview_path = Path(preview_path)
    return preview_path.with_name(preview_path.stem + "-telemetry.json")


def _clear_stale_preview(path: Path) -> None:
    """Invalidate both the old JPEG and its frame telemetry.

    Deletion can lose a short race with an already-connected MJPEG reader on
    Windows.  The WebUI therefore also checks the per-run ``session_id`` in the
    telemetry sidecar before it serves a frame.  Even if deletion loses that
    race, an old picture can no longer masquerade as the new camera session.
    """

    for target in (Path(path), preview_telemetry_path(Path(path))):
        try:
            remove_file_locked(target, timeout_s=0.35)
        except (OSError, TimeoutError):
            pass


@dataclass(slots=True)
class DockingRunResult:
    success: bool
    message: str
    phase: str


_RECORD_CSV_FIELDS = (
    "timestamp_iso",
    "timestamp_epoch_s",
    "elapsed_s",
    "status",
    "phase",
    "manual_stage",
    "world_axis_locked",
    "world_axis_lock_source",
    "world_axis_lock_samples",
    "world_axis_lock_loaded",
    "tag_visible",
    "tag_control_valid",
    "tag_display_held",
    "axis_distance_m",
    "axis_error_m",
    "yaw_error_deg",
    "pose_measurement_source",
    "display_pose_source",
    "pose_measurement_confidence",
    "wall_plane_rms_mm",
    "wall_plane_inlier_ratio",
    "pose_yaw_sigma_deg",
    "pnp_yaw_error_deg",
    "rgbd_axis_distance_m",
    "lidar_wall_distance_m",
    "lidar_wall_rms_mm",
    "laser_age_ms",
    "laser_point_count",
    "imu_age_ms",
    "imu_yaw_deg",
    "imu_yaw_rate_rps",
    "imu_yaw_fusion_source",
    "fusion_map_yaw_deg",
    "seer_map_x_m",
    "seer_map_y_m",
    "seer_map_yaw_deg",
    "seer_localization_score",
    "straight_target_heading_deg",
    "straight_current_heading_deg",
    "straight_heading_error_deg",
    "straight_cross_track_m",
    "straight_heading_source",
    "straight_camera_steering",
    "fusion_innovation_position_m",
    "fusion_innovation_yaw_deg",
    "fusion_correction_accepted",
    "du_error_px",
    "tag_depth_m",
    "final_depth_target_m",
    "verification_axis_error_m",
    "verification_yaw_error_deg",
    "centerline_verified",
    "centerline_hold_elapsed_s",
    "centerline_hold_required_s",
    "yaw_verified",
    "yaw_hold_elapsed_s",
    "yaw_hold_required_s",
    "straight_axis_latched",
    "final_hold_elapsed_s",
    "final_hold_required_s",
    "clearance_m",
    "target_v_mps",
    "target_w_rps",
    "sent_v_mps",
    "sent_w_rps",
    "motor_left_cmd_rpm",
    "motor_right_cmd_rpm",
    "seer_vx_mps",
    "seer_w_rps",
    "motor_enabled",
    "motor_flag_source",
    "electric_state",
    "motor_warning",
    "blocked",
    "block_reason",
    "blocked_event_count",
    "blocked_duration_ms",
    "hard_stop_latched",
    "motion_command_hz",
    "motion_ack_timeout_streak",
    "motion_tx_deferred",
    "control_connected",
    "control_reconnect_count",
    "control_latency_ms",
    "control_last_error",
    "control_recovery_error_count",
    "control_recovery_last_error",
    "last_motion_error",
    "terminal_message",
    "emergency",
    "camera_loop_hz",
    "preview_fps_actual",
    "note",
)


class DockingSessionRecorder:
    """Non-blocking LIVE docking recorder.

    The control loop only enqueues tiny telemetry dictionaries and occasional
    preview frames.  CSV/video I/O and MJPEG encoding live on a daemon worker,
    so recording cannot drag the camera/control loop down when disk I/O stalls.
    The recorded video is the same RGB+Depth diagnostic view shown in WebUI,
    with an extra command band containing sent v/w, wheel RPM and measured
    SEER motion.  ``telemetry.csv`` keeps the high-rate values for analysis.
    """

    def __init__(
        self,
        root: Path,
        *,
        session_id: str,
        started_at: float,
        robot_ip: str,
        action_id: str,
        run_mode: str,
        config: Mapping[str, Any] | None,
    ) -> None:
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", True))
        if bool(cfg.get("live_only", True)) and str(run_mode).upper() != "LIVE":
            self.enabled = False
        self.root = Path(root)
        self.session_id = str(session_id)
        self.started_at = float(started_at)
        self.robot_ip = str(robot_ip or "")
        self.action_id = str(action_id or "")
        self.run_mode = str(run_mode or "")
        self.video_fps = max(1.0, min(30.0, float(cfg.get("video_fps", 10.0))))
        self.retain_sessions = max(1, min(200, int(cfg.get("retain_sessions", 20))))
        self.session_name = ""
        self.session_dir: Path | None = None
        self.video_path: Path | None = None
        self.csv_path: Path | None = None
        self.meta_path: Path | None = None
        self.video_frames = 0
        self.video_drop_count = 0
        self.telemetry_rows = 0
        self.telemetry_drop_count = 0
        self.video_error = ""
        self.worker_error = ""
        self._last_video_submit_mono = 0.0
        self._telemetry_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4096)
        self._video_q: queue.Queue[tuple[np.ndarray, dict[str, Any]]] = queue.Queue(maxsize=3)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._final_meta: dict[str, Any] = {}
        if not self.enabled:
            return

        stamp = datetime.fromtimestamp(self.started_at).astimezone().strftime("%Y%m%d-%H%M%S")
        self.session_name = f"{stamp}_{self.run_mode.lower()}_{self.session_id[:12]}"
        self.session_dir = self.root / self.session_name
        self.video_path = self.session_dir / "camera_commands.avi"
        self.csv_path = self.session_dir / "telemetry.csv"
        self.meta_path = self.session_dir / "session.json"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._prune_old_sessions()
        self._write_meta(status="recording", phase="STARTING", message="LIVE docking recording started")
        self._thread = threading.Thread(
            target=self._worker,
            name=f"seer-docking-rec-{self.session_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def _prune_old_sessions(self) -> None:
        try:
            dirs = [item for item in self.root.iterdir() if item.is_dir() and item != self.session_dir]
            dirs.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            for stale in dirs[max(0, self.retain_sessions - 1):]:
                try:
                    shutil.rmtree(stale)
                except OSError:
                    pass
        except OSError:
            pass

    def _meta_payload(self, **extra: Any) -> dict[str, Any]:
        payload = {
            "schema": 1,
            "session_id": self.session_id,
            "session_name": self.session_name,
            "started_at": self.started_at,
            "started_at_iso": datetime.fromtimestamp(self.started_at).astimezone().isoformat(timespec="milliseconds"),
            "robot_ip": self.robot_ip,
            "action_id": self.action_id,
            "run_mode": self.run_mode,
            "recording_enabled": bool(self.enabled),
            "video_file": "camera_commands.avi" if self.enabled else "",
            "telemetry_file": "telemetry.csv" if self.enabled else "",
            "video_fps": self.video_fps,
            "video_frames": int(self.video_frames),
            "video_drop_count": int(self.video_drop_count),
            "telemetry_rows": int(self.telemetry_rows),
            "telemetry_drop_count": int(self.telemetry_drop_count),
            "video_error": self.video_error,
            "worker_error": self.worker_error,
            **self._final_meta,
            **extra,
        }
        return payload

    def _write_meta(self, **extra: Any) -> None:
        if not self.enabled or self.meta_path is None:
            return
        try:
            atomic_write_json(self.meta_path, self._meta_payload(**extra), timeout_s=0.30)
        except (OSError, TimeoutError):
            pass

    def status_fields(self) -> dict[str, Any]:
        return {
            "recording_enabled": bool(self.enabled),
            "recording_active": bool(self.enabled and not self._stop.is_set()),
            "recording_session": self.session_name or "",
            "recording_video_fps": float(self.video_fps) if self.enabled else None,
            "recording_video_frames": int(self.video_frames),
            "recording_video_drop_count": int(self.video_drop_count),
            "recording_telemetry_rows": int(self.telemetry_rows),
            "recording_telemetry_drop_count": int(self.telemetry_drop_count),
            "recording_error": self.video_error or self.worker_error,
        }

    @staticmethod
    def _bool_cell(value: Any) -> int:
        return 1 if bool(value) else 0

    def log_telemetry(self, fields: Mapping[str, Any]) -> None:
        if not self.enabled or self._stop.is_set():
            return
        row = dict(fields)
        now = float(row.pop("timestamp_epoch_s", time.time()) or time.time())
        row["timestamp_epoch_s"] = f"{now:.6f}"
        row["timestamp_iso"] = datetime.fromtimestamp(now).astimezone().isoformat(timespec="milliseconds")
        row["elapsed_s"] = f"{max(0.0, now - self.started_at):.6f}"
        for key in ("tag_visible", "tag_control_valid", "tag_display_held", "blocked", "emergency"):
            if key in row:
                row[key] = self._bool_cell(row[key])
        compact = {field: row.get(field, "") for field in _RECORD_CSV_FIELDS}
        try:
            self._telemetry_q.put_nowait(compact)
        except queue.Full:
            self.telemetry_drop_count += 1

    def submit_frame(self, frame: np.ndarray, fields: Mapping[str, Any]) -> None:
        if not self.enabled or self._stop.is_set() or frame is None or frame.size == 0:
            return
        now_mono = time.monotonic()
        if self._last_video_submit_mono and now_mono - self._last_video_submit_mono < (1.0 / self.video_fps) * 0.92:
            return
        self._last_video_submit_mono = now_mono
        item = (np.ascontiguousarray(frame.copy()), dict(fields))
        try:
            self._video_q.put_nowait(item)
        except queue.Full:
            # Prefer the newest view.  Throw away one stale queued video frame,
            # never a command sample, then enqueue the current frame.
            try:
                self._video_q.get_nowait()
                self.video_drop_count += 1
            except queue.Empty:
                pass
            try:
                self._video_q.put_nowait(item)
            except queue.Full:
                self.video_drop_count += 1

    def _annotate_record_frame(self, frame: np.ndarray, fields: Mapping[str, Any]) -> np.ndarray:
        import cv2

        h, w = frame.shape[:2]
        band_h = 118
        out = np.zeros((h + band_h, w, 3), dtype=np.uint8)
        out[:h] = frame
        now = float(fields.get("frame_updated_at", fields.get("updated_at", time.time())) or time.time())
        elapsed = max(0.0, now - self.started_at)
        tag_state = "OK" if fields.get("tag_visible") and fields.get("tag_control_valid") else ("REJECT" if fields.get("tag_visible") else ("HOLD" if fields.get("tag_display_held") else "LOST"))

        def fnum(key: str, digits: int = 3, scale: float = 1.0, sign: bool = False) -> str:
            value = fields.get(key)
            if value is None or value == "":
                return "-"
            try:
                number = float(value) * scale
                return f"{number:{'+' if sign else ''}.{digits}f}"
            except (TypeError, ValueError):
                return str(value)

        lines = [
            f"REC {elapsed:7.2f}s  {self.run_mode}  PHASE={fields.get('phase','-')}  TAG={tag_state}",
            f"POSE x={fnum('axis_distance_m')}m  y={fnum('axis_error_m',1,1000.0,True)}mm  yaw={fnum('yaw_error_deg',2,1.0,True)}deg  du={fnum('du_error_px',1,1.0,True)}px  depth={fnum('tag_depth_m')}m",
            f"POSE SRC={fields.get('display_pose_source', fields.get('pose_measurement_source','-'))}  conf={fnum('pose_measurement_confidence',2)}  lidar={fnum('lidar_wall_distance_m')}m/{fnum('lidar_wall_rms_mm',1)}mm  depthX={fnum('rgbd_axis_distance_m')}m",
            f"CMD target v/w={fnum('target_v_mps')}/{fnum('target_w_rps')}  sent={fnum('sent_v_mps')}/{fnum('sent_w_rps')}  motor L/R={fnum('motor_left_cmd_rpm',1)}/{fnum('motor_right_cmd_rpm',1)} rpm",
            f"SEER vx/w={fnum('seer_vx_mps')}/{fnum('seer_w_rps')}  SAFETY blocked={1 if fields.get('blocked') else 0} emergency={1 if fields.get('emergency') else 0}",
        ]
        y0 = h + 18
        cv2.circle(out, (12, h + 14), 5, (0, 0, 255), -1)
        for index, text in enumerate(lines):
            cv2.putText(out, text, (24 if index == 0 else 9, y0 + index * 22), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (235, 235, 235), 1, cv2.LINE_AA)
        return out

    def _worker(self) -> None:
        if not self.enabled or self.csv_path is None or self.video_path is None:
            return
        writer = None
        csv_file = None
        csv_writer = None
        last_flush = time.monotonic()
        try:
            import cv2

            csv_file = self.csv_path.open("w", encoding="utf-8-sig", newline="", buffering=64 * 1024)
            csv_writer = csv.DictWriter(csv_file, fieldnames=list(_RECORD_CSV_FIELDS), extrasaction="ignore")
            csv_writer.writeheader()
            while not self._stop.is_set() or not self._telemetry_q.empty() or not self._video_q.empty():
                did_work = False
                for _ in range(96):
                    try:
                        row = self._telemetry_q.get_nowait()
                    except queue.Empty:
                        break
                    csv_writer.writerow(row)
                    self.telemetry_rows += 1
                    did_work = True
                try:
                    frame, fields = self._video_q.get(timeout=0.015 if not did_work else 0.0)
                except queue.Empty:
                    frame = None
                    fields = None
                if frame is not None and fields is not None:
                    recorded = self._annotate_record_frame(frame, fields)
                    vh, vw = recorded.shape[:2]
                    if writer is None:
                        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
                        writer = cv2.VideoWriter(str(self.video_path), fourcc, self.video_fps, (vw, vh))
                        if not writer.isOpened():
                            self.video_error = "OpenCV MJPG VideoWriter could not open camera_commands.avi"
                            writer.release()
                            writer = None
                    if writer is not None:
                        writer.write(recorded)
                        self.video_frames += 1
                    did_work = True
                if csv_file is not None and time.monotonic() - last_flush >= 0.5:
                    csv_file.flush()
                    last_flush = time.monotonic()
                if not did_work:
                    time.sleep(0.004)
        except Exception as exc:
            self.worker_error = f"{type(exc).__name__}: {exc}"
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            if csv_file is not None:
                try:
                    csv_file.flush()
                    csv_file.close()
                except Exception:
                    pass

    def finish(self, *, status: str, phase: str, message: str, stop_result: str = "") -> None:
        if not self.enabled:
            return
        self._final_meta = {
            "status": str(status),
            "phase": str(phase),
            "message": str(message),
            "stop_result": str(stop_result or ""),
            "ended_at": time.time(),
        }
        self._final_meta["ended_at_iso"] = datetime.fromtimestamp(float(self._final_meta["ended_at"])).astimezone().isoformat(timespec="milliseconds")
        self._final_meta["duration_s"] = max(0.0, float(self._final_meta["ended_at"]) - self.started_at)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=4.0)
            if self._thread.is_alive():
                self.worker_error = self.worker_error or "recording worker did not stop within 4s"
        self._write_meta()


def _matrix4(config: Mapping[str, Any], key: str) -> np.ndarray:
    value = np.asarray(config[key], dtype=float)
    if value.shape != (4, 4):
        raise ValueError(f"{key} must be a 4x4 matrix")
    return value


def _mount_transform(cfg: Mapping[str, Any]) -> np.ndarray:
    mount = cfg.get("camera_mount")
    if isinstance(mount, Mapping):
        return camera_mount_transform(
            mount.get("xyz_m", (0.0, 0.0, 0.0)),
            [math.radians(float(value)) for value in mount.get("rpy_deg", (0.0, 0.0, 0.0))],
        )
    return _matrix4(cfg, "t_base_camera")


def _runtime_paths() -> tuple[Path, Path]:
    status_override = str(os.getenv("SEER_DOCKING_STATUS_PATH", "") or "").strip()
    preview_override = str(os.getenv("SEER_DOCKING_PREVIEW_PATH", "") or "").strip()
    if status_override and preview_override:
        return Path(status_override), Path(preview_override)
    status_cache = str(os.getenv("SEER_STATUS_CACHE_PATH", "") or "").strip()
    if status_cache:
        base = Path(status_cache).parent
    else:
        runtime_root = str(os.getenv("SEER_RUNTIME_ROOT", "") or "").strip()
        base = Path(runtime_root) if runtime_root else Path.cwd() / "runtime"
    return (
        Path(status_override) if status_override else base / "seer-docking-status.json",
        Path(preview_override) if preview_override else base / "seer-docking-preview.jpg",
    )


def _seer_map_pose(vehicle: Any) -> tuple[tuple[float, float, float] | None, float | None]:
    """Return current SEER localization pose in SI units and normalized score."""

    try:
        raw_x = float(getattr(vehicle, "_x"))
        raw_y = float(getattr(vehicle, "_y"))
        raw_th = float(getattr(vehicle, "_th"))
        to_m = getattr(vehicle, "position_to_native", None)
        to_rad = getattr(vehicle, "angle_to_native", None)
        pose = (
            float(to_m(raw_x) if callable(to_m) else raw_x),
            float(to_m(raw_y) if callable(to_m) else raw_y),
            float(to_rad(raw_th) if callable(to_rad) else raw_th),
        )
    except (TypeError, ValueError, AttributeError):
        return None, None
    if not all(math.isfinite(v) for v in pose):
        return None, None
    raw_score = getattr(vehicle, "_localization_score", None)
    score: float | None
    try:
        score = float(raw_score)
        if not math.isfinite(score):
            score = None
        elif score > 1.0:
            score = score / 100.0
        score = None if score is None else float(np.clip(score, 0.0, 1.0))
    except (TypeError, ValueError):
        score = None
    return pose, score


def _xy_point(item: Any) -> list[float] | None:
    """Normalize one SEER laser point into ``[world_x, world_y]`` when possible."""

    if isinstance(item, Mapping):
        x = item.get("x", item.get("X"))
        y = item.get("y", item.get("Y"))
        if x is None or y is None:
            return None
    elif isinstance(item, (list, tuple)) and len(item) >= 2:
        x, y = item[0], item[1]
    else:
        return None
    try:
        px, py = float(x), float(y)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(px) and math.isfinite(py)):
        return None
    return [px, py]


def _points_from_sequence(value: Any) -> list[list[float]]:
    """Extract a flat XY point list from a known point-cloud sequence shape."""

    if not isinstance(value, (list, tuple)):
        return []
    direct: list[list[float]] = []
    for item in value:
        point = _xy_point(item)
        if point is not None:
            direct.append(point)
    if direct:
        return direct

    # Newer RoboKit builds may expose ``lasers`` as one list per physical
    # scanner.  Recurse only through list/dict containers; scalar metadata is
    # deliberately ignored.
    nested: list[list[float]] = []
    for item in value:
        if isinstance(item, (list, tuple)):
            nested.extend(_points_from_sequence(item))
        elif isinstance(item, Mapping):
            for key in (
                "laser_beams", "laserBeams", "points", "point_cloud", "pointCloud",
                "beams", "data", "scan", "cloud",
            ):
                if key in item:
                    nested.extend(_points_from_sequence(item.get(key)))
                    if nested:
                        break
    return nested


def _points_from_cloud_value(value: Any) -> list[list[float]]:
    """Extract XY points from one *known sensor field*.

    Newer SRC builds can expose a 2-D scan as ``laser_beams`` or a scanner
    cloud as ``beams3D``/``mid360``.  We intentionally recurse only after a
    sensor field name has been matched so arbitrary status arrays (DI/DO,
    task lists, etc.) can never be mistaken for LiDAR geometry.
    """

    point = _xy_point(value)
    if point is not None:
        return [point]
    if isinstance(value, (list, tuple)):
        return _points_from_sequence(value)
    if isinstance(value, Mapping):
        for key in (
            "laser_beams", "laserBeams", "lasers", "points", "point_cloud",
            "pointCloud", "beams", "beams3D", "beams_3d", "mid360",
            "mid360_beams", "data", "scan", "cloud",
        ):
            if key not in value:
                continue
            points = _points_from_cloud_value(value.get(key))
            if points:
                return points
    return []


def _robot_local_to_world_xy(
    local_x: float,
    local_y: float,
    robot_map_pose: tuple[float, float, float],
) -> list[float]:
    """Convert one robot-local LiDAR point to SEER world/map coordinates."""

    rx, ry, yaw = (float(robot_map_pose[0]), float(robot_map_pose[1]), float(robot_map_pose[2]))
    c, s = math.cos(yaw), math.sin(yaw)
    return [
        rx + c * float(local_x) - s * float(local_y),
        ry + s * float(local_x) + c * float(local_y),
    ]


def _classic_gui_polar_laser_points(
    lasers: Any,
    *,
    robot_map_pose: tuple[float, float, float] | None,
) -> list[list[float]]:
    """Decode the ``lasers[].beams[]`` schema used by the old map GUI.

    That GUI receives each valid beam as ``angle`` in degrees and ``dist`` in
    metres, applies ``install_info`` (x/y/yaw), then treats the result as a
    robot-local point.  The docking wall fitter consumes world points, so this
    function performs the final robot-local -> world transform as well.
    """

    if robot_map_pose is None or not isinstance(lasers, (list, tuple)):
        return []
    points: list[list[float]] = []
    for laser in lasers:
        if not isinstance(laser, Mapping):
            continue
        install = laser.get("install_info", laser.get("installInfo", {}))
        if not isinstance(install, Mapping):
            install = {}
        try:
            install_x = float(install.get("x", 0.0) or 0.0)
            install_y = float(install.get("y", 0.0) or 0.0)
            install_yaw_deg = float(install.get("yaw", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (install_x, install_y, install_yaw_deg)):
            continue
        install_yaw = math.radians(install_yaw_deg)
        ci, si = math.cos(install_yaw), math.sin(install_yaw)
        beams = laser.get("beams", [])
        if not isinstance(beams, (list, tuple)):
            continue
        for beam in beams:
            if not isinstance(beam, Mapping):
                continue
            # The old GUI only plotted valid beams.  Some firmware omits the
            # flag, in which case a finite positive distance is sufficient.
            if "valid" in beam and not bool(beam.get("valid")):
                continue
            try:
                angle_deg = float(beam.get("angle"))
                dist = float(beam.get("dist"))
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(angle_deg) and math.isfinite(dist)) or dist <= 0.0:
                continue
            angle = math.radians(angle_deg)
            bx = math.cos(angle) * dist
            by = math.sin(angle) * dist
            local_x = ci * bx - si * by + install_x
            local_y = si * bx + ci * by + install_y
            points.append(_robot_local_to_world_xy(local_x, local_y, robot_map_pose))
    return points


def _extract_laser_points(
    payload: Any,
    *,
    robot_map_pose: tuple[float, float, float] | None = None,
) -> tuple[list[list[float]], str]:
    """Accept classic GUI polar, world-XY and newer 3-D RoboKit schemas."""

    if not isinstance(payload, Mapping):
        return [], "non-mapping"

    # Public NetProtocol API 1009/1101 ``laser_beams`` points are world XY.
    for key in ("laser_beams", "laserBeams"):
        if key in payload:
            points = _points_from_cloud_value(payload.get(key))
            if points:
                return points, key

    # The user's older map GUI used another API-1009 shape: one entry per
    # physical laser, with robot-local polar beams and installation offsets.
    # First preserve compatibility with newer ``lasers[].points`` wrappers;
    # then decode the polar form exactly as the GUI did.
    if "lasers" in payload:
        points = _points_from_cloud_value(payload.get("lasers"))
        if points:
            return points, "lasers"
        points = _classic_gui_polar_laser_points(
            payload.get("lasers"), robot_map_pose=robot_map_pose
        )
        if points:
            return points, "lasers.polar_beams_world"

    # Some current controller builds do not populate classic ``laser_beams``
    # for a 3-D/Mid-360 scanner.  API 1100 can expose these heavier fields when
    # their return_* switches are enabled.  Only X/Y are needed for wall fitting.
    for key in (
        "beams3D", "beams_3d", "mid360", "mid360_beams",
        "point_cloud", "pointCloud", "cloud",
    ):
        if key in payload:
            points = _points_from_cloud_value(payload.get(key))
            if points:
                return points, key

    # Compatibility wrappers used by some controller/plugin generations.
    for wrapper in ("data", "result", "status", "payload"):
        child = payload.get(wrapper)
        if isinstance(child, Mapping):
            points, schema = _extract_laser_points(child, robot_map_pose=robot_map_pose)
            if points:
                return points, f"{wrapper}.{schema}"
    return [], "missing"


def _sensor_shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, Mapping):
        return f"object[{len(value)}]"
    if isinstance(value, (list, tuple)):
        return f"array[{len(value)}]"
    return type(value).__name__


def _laser_response_diagnostic(payload: Any) -> str:
    """Compact, UI-safe summary of what the controller actually returned."""

    if not isinstance(payload, Mapping):
        return f"response={type(payload).__name__}"
    interesting = []
    lowered_tokens = ("laser", "lidar", "beam", "mid360", "cloud", "scan")
    for key, value in payload.items():
        name = str(key)
        if any(token in name.lower() for token in lowered_tokens):
            interesting.append(f"{name}:{_sensor_shape(value)}")
    ret = payload.get("ret_code", payload.get("ret_core", "-"))
    err = str(payload.get("err_msg", "") or "").replace("\n", " ").strip()
    if len(err) > 80:
        err = err[:77] + "..."
    sensor = ",".join(interesting[:12]) or "no-laser-fields"
    return f"ret={ret} {sensor}" + (f" err={err}" if err else "")


def _extract_netprotocol_laser_step(payload: Any) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    plugin = payload.get("NetProtocol")
    if not isinstance(plugin, Mapping):
        # A few builds wrap parameter data.
        for wrapper in ("data", "result", "status"):
            child = payload.get(wrapper)
            if isinstance(child, Mapping):
                value = _extract_netprotocol_laser_step(child)
                if value is not None:
                    return value
        return None
    raw = plugin.get("laserStep")
    if isinstance(raw, Mapping):
        raw = raw.get("value")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(0, value)


async def _query_netprotocol_laser_step(vehicle: Any) -> tuple[int | None, str]:
    """Read NetProtocol.laserStep once for diagnostics.

    API 1009's explicit ``step`` overrides this parameter, but seeing it in the
    UI is useful to distinguish a controller configuration issue from a sensor
    exposure issue.
    """

    cached = getattr(vehicle, "_docking_laser_step_diag", None)
    if isinstance(cached, tuple) and len(cached) == 2:
        return cached
    conn = getattr(vehicle, "_ports", {}).get(ApiPort.STATE)
    if conn is None:
        result = (None, "STATE unavailable")
        setattr(vehicle, "_docking_laser_step_diag", result)
        return result
    try:
        response = await conn.request(
            int(ApiNumber.STATUS_PARAMS),
            {"plugin": "NetProtocol", "param": "laserStep"},
            timeout=1.2,
        )
        value = _extract_netprotocol_laser_step(response)
        result = (value, _laser_response_diagnostic(response))
    except Exception as exc:
        result = (None, f"API1400 {type(exc).__name__}: {exc}")
    setattr(vehicle, "_docking_laser_step_diag", result)
    return result


async def _request_laser_points(vehicle: Any, *, step: int = 8) -> dict[str, Any]:
    """Fetch real LiDAR points across RoboKit controller generations.

    The dedicated, documented API 1009 is tried with raw sampling first.  If a
    controller has a Mid-360/3-D scanner and intentionally leaves classic
    ``laser_beams`` empty, API 1100 is also probed with the newer heavy-sensor
    return switches enabled.  A successful source is cached so ordinary polling
    goes back to one request per sample.
    """

    started = time.monotonic()
    conn = getattr(vehicle, "_ports", {}).get(ApiPort.STATE)
    if conn is None:
        return {
            "ok": False,
            "error": "STATE port unavailable",
            "latency_ms": 0.0,
            "points": [],
            "source": "-",
            "schema": "-",
            "attempts": [],
            "diagnostics": [],
            "laser_step_param": None,
        }

    requested_step = max(0, int(step))
    hint = str(getattr(vehicle, "_docking_laser_source_hint", "") or "")
    laser_step_param, laser_step_diag = await _query_netprotocol_laser_step(vehicle)

    # ``None`` and ``{}`` both serialize as a zero-length data section in this
    # client, matching the protocol's "JSON data: none" requests.
    sensor_all1_body: Mapping[str, Any] = {
        "keys": [
            "laser_beams", "lasers", "beams3D", "mid360",
            "point_cloud", "pointCloud", "cloud",
        ],
        "return_laser": True,
        "return_beams3D": True,
        "return_mid360": True,
    }

    requests: dict[str, tuple[int, Mapping[str, Any] | None]] = {
        # Match the older map-edit GUI first.  On the user's controller this
        # path can return ``lasers[].beams[]`` with angle/dist/install_info.
        "API1009_GUI_CLASSIC": (int(ApiNumber.STATUS_LASER), {"return_beams3D": False}),
        # Raw/all beams fallbacks. RoboKit docs define step 0 or 1 as no sampling.
        "API1009_STEP1": (int(ApiNumber.STATUS_LASER), {"step": 1}),
        "API1009_STEP0": (int(ApiNumber.STATUS_LASER), {"step": 0}),
        # Also test the configured/default path because some firmware rejects 0.
        "API1009_CONFIG": (int(ApiNumber.STATUS_LASER), None),
        "API1009_REQUESTED": (int(ApiNumber.STATUS_LASER), {"step": requested_step}),
        # all2 officially includes the 1009 payload on classic NetProtocol.
        "API1101_ALL2": (int(ApiNumber.STATUS_ALL2), None),
        # Current builds may gate 3-D/Mid-360 data behind these switches.
        "API1100_SENSOR_FLAGS": (
            int(ApiNumber.STATUS_ALL1),
            {"return_laser": True, "return_beams3D": True, "return_mid360": True},
        ),
        "API1100_SENSOR_KEYS": (int(ApiNumber.STATUS_ALL1), sensor_all1_body),
        "API1100_ALL1": (int(ApiNumber.STATUS_ALL1), None),
    }

    order: list[str] = []
    if hint in requests:
        order.append(hint)
    for name in (
        "API1009_GUI_CLASSIC", "API1009_STEP1", "API1009_STEP0", "API1009_CONFIG",
        "API1009_REQUESTED", "API1101_ALL2", "API1100_SENSOR_FLAGS",
        "API1100_SENSOR_KEYS", "API1100_ALL1",
    ):
        if name not in order:
            order.append(name)

    attempts: list[str] = []
    diagnostics: list[str] = [
        f"API1400 laserStep={laser_step_param if laser_step_param is not None else '-'} ({laser_step_diag})"
    ]
    last_error = ""
    for name in order:
        api, body = requests[name]
        req_started = time.monotonic()
        try:
            response = await conn.request(api, None if body is None else dict(body), timeout=1.8)
        except Exception as exc:
            last_error = f"{name}: {type(exc).__name__}: {exc}"
            attempts.append(f"{name}=ERR")
            diagnostics.append(last_error)
            continue

        laser_map_pose, _ = _seer_map_pose(vehicle)
        points, schema = _extract_laser_points(response, robot_map_pose=laser_map_pose)
        elapsed_ms = 1000.0 * (time.monotonic() - req_started)
        diag = _laser_response_diagnostic(response)
        diagnostics.append(f"{name} {diag}")
        if points:
            setattr(vehicle, "_docking_laser_source_hint", name)
            return {
                "ok": True,
                "error": "",
                "latency_ms": 1000.0 * (time.monotonic() - started),
                "request_latency_ms": elapsed_ms,
                "points": points,
                "source": name,
                "schema": schema,
                "attempts": attempts + [f"{name}={len(points)}"],
                "diagnostics": diagnostics,
                "laser_step_param": laser_step_param,
                "response_keys": sorted(str(k) for k in response.keys()) if isinstance(response, Mapping) else [],
            }

        attempts.append(f"{name}=0")
        keys = sorted(str(k) for k in response.keys()) if isinstance(response, Mapping) else []
        sensor_keys = [
            key for key in keys
            if any(token in key.lower() for token in ("laser", "lidar", "beam", "mid360", "cloud", "scan"))
        ]
        key_text = ",".join(sensor_keys[:16]) or ",".join(keys[:18]) or "-"
        last_error = f"{name}: no usable LiDAR XY points (keys={key_text}; {diag})"

        # A remembered source has stopped working. Drop the hint immediately so
        # this same cycle can probe the compatibility fallbacks.
        if hint == name:
            try:
                delattr(vehicle, "_docking_laser_source_hint")
            except AttributeError:
                pass
            hint = ""

    return {
        "ok": False,
        "error": last_error or "No RoboKit LiDAR source returned usable points",
        "latency_ms": 1000.0 * (time.monotonic() - started),
        "points": [],
        "source": "NETPROTOCOL_EMPTY",
        "schema": "-",
        "attempts": attempts,
        "diagnostics": diagnostics,
        "laser_step_param": laser_step_param,
        "retry_after_s": 2.0,
    }


def default_config_path() -> Path:
    override = str(os.getenv("SEER_DOCKING_CONFIG_PATH", "") or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "config" / "docking.json"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    # WebUI and Adapter are separate Windows processes.  Coordinate the
    # shared runtime file so os.replace never races a WebUI reader.
    atomic_write_json(path, payload, timeout_s=0.20)


async def _request_imu_attitude(vehicle: Any) -> dict[str, Any]:
    """Fetch SEER chassis yaw/roll/pitch from RoboKit status API 1014.

    API 1014 is a no-JSON-data request.  The docking loop owns no extra TCP
    socket; it reuses the Adapter's serialized STATE connection exactly like
    the LiDAR polling path.
    """

    started = time.monotonic()
    conn = getattr(vehicle, "_ports", {}).get(ApiPort.STATE)
    if conn is None:
        return {
            "ok": False, "error": "STATE port unavailable", "latency_ms": 0.0,
            "attitude": None, "source": "API1014", "schema": "-",
        }
    try:
        response = await conn.request(int(ApiNumber.STATUS_IMU), None, timeout=1.2)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"API1014 {type(exc).__name__}: {exc}",
            "latency_ms": 1000.0 * (time.monotonic() - started),
            "attitude": None,
            "source": "API1014",
            "schema": "-",
        }
    attitude = parse_seer_imu_attitude(response)
    if attitude is None:
        keys = sorted(str(k) for k in response.keys()) if isinstance(response, Mapping) else []
        return {
            "ok": False,
            "error": f"API1014 returned no usable yaw/roll/pitch (keys={','.join(keys[:16]) or '-'})",
            "latency_ms": 1000.0 * (time.monotonic() - started),
            "attitude": None,
            "source": "API1014",
            "schema": "missing",
        }
    return {
        "ok": True,
        "error": "",
        "latency_ms": 1000.0 * (time.monotonic() - started),
        "attitude": attitude,
        "source": "API1014",
        "schema": attitude.schema,
    }


def read_docking_status(path: Path, *, max_age_sec: float = 5.0) -> dict[str, Any]:
    try:
        payload = read_json_locked(Path(path), timeout_s=0.05)
    except (OSError, ValueError, json.JSONDecodeError, TimeoutError):
        return {"schema": 1, "status": "idle", "phase": "IDLE", "message": "도킹 실행 기록 없음"}
    if not isinstance(payload, dict):
        return {"schema": 1, "status": "idle", "phase": "IDLE", "message": "도킹 상태 형식 오류"}
    try:
        age = max(0.0, time.time() - float(payload.get("updated_at", 0.0) or 0.0))
    except (TypeError, ValueError):
        age = float("inf")
    payload["age_sec"] = age
    if payload.get("status") in {"starting", "running"} and age > max_age_sec:
        payload = dict(payload)
        payload.update(status="stale", phase="STALE", message="도킹 heartbeat가 끊겼습니다")
    return payload


def _write_preview(path: Path, frame: np.ndarray, *, jpeg_quality: int = 78) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    quality = max(45, min(95, int(jpeg_quality)))
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return
    atomic_write_bytes(path, encoded.tobytes(), timeout_s=0.08)


def _validate_depth(
    detection: TagDetection,
    depth_m: np.ndarray | None,
    depth_cfg: Mapping[str, Any],
) -> tuple[bool, float | None, str]:
    center = np.asarray(detection.corners_px, dtype=float).mean(axis=0)
    tag_depth = median_depth_at_pixel(
        depth_m,
        center,
        radius_px=int(depth_cfg.get("tag_patch_radius_px", 6)),
        min_valid_m=float(depth_cfg.get("min_valid_m", 0.12)),
        max_valid_m=float(depth_cfg.get("max_valid_m", 6.0)),
    )
    required = bool(depth_cfg.get("require_valid_tag_depth", True))
    if tag_depth is None:
        return (not required), None, "tag depth missing"
    pnp_depth = float(detection.t_camera_marker[2, 3])
    tolerance = max(
        float(depth_cfg.get("tag_depth_tolerance_m", 0.15)),
        float(depth_cfg.get("tag_depth_tolerance_ratio", 0.10)) * pnp_depth,
    )
    error = abs(tag_depth - pnp_depth)
    if error > tolerance:
        return False, tag_depth, f"PnP/depth mismatch {error:.3f}m > {tolerance:.3f}m"
    return True, tag_depth, "depth valid"


def _phase_name(controller: Any) -> str:
    phase = getattr(controller, "phase", "UNKNOWN")
    return phase.name if hasattr(phase, "name") else str(phase)


def _motor_status_snapshot(vehicle: Any) -> tuple[bool, str, Any]:
    """Return cached motor flag plus the field that produced it.

    On some SEER 3.4 controller variants STATUS 1100 can expose ``electric``
    as false even though open-loop CONTROL motion is available.  Explicit
    ``motor_flag``/``motor_enabled``/``motor_enable_status`` values are still
    treated as authoritative safety information.
    """

    enabled = bool(getattr(vehicle, "_motor_flag", True))
    source = str(getattr(vehicle, "_motor_flag_source", "legacy") or "legacy")
    electric = getattr(vehicle, "_electric_state", None)
    return enabled, source, electric


def _motion_command_observed_in_state(
    vehicle: Any,
    cmd: VelocityCommand,
    *,
    linear_eps_mps: float = 0.003,
    angular_eps_rps: float = math.radians(0.08),
) -> tuple[bool, float, float]:
    """STATE 속도가 최신 명령과 같은 방향으로 반영됐는지 보조 확인한다.

    Wi-Fi에서 CONTROL 응답 패킷만 유실되고 명령 자체는 로봇에 도착하는 경우가
    실물 시험에서 반복됐다. STATE가 계속 신선할 때만 이 값을 ACK의 보조 증거로
    사용하며, emergency/blocked/stale 안전 판정은 별도로 계속 fail-closed 한다.
    """

    try:
        actual_v = float(getattr(vehicle, "_vx", 0.0) or 0.0)
        actual_w = float(getattr(vehicle, "_w", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False, 0.0, 0.0

    target_v = float(cmd.v)
    target_w = float(cmd.omega)

    if abs(target_v) <= linear_eps_mps:
        linear_ok = abs(actual_v) <= max(linear_eps_mps * 2.0, 0.006)
    else:
        linear_ok = (actual_v * target_v) > 0.0 and abs(actual_v) >= min(abs(target_v) * 0.25, 0.006)

    if abs(target_w) <= angular_eps_rps:
        angular_ok = abs(actual_w) <= max(angular_eps_rps * 2.0, math.radians(0.25))
    else:
        angular_ok = (actual_w * target_w) > 0.0 and abs(actual_w) >= min(abs(target_w) * 0.25, math.radians(0.08))

    return bool(linear_ok and angular_ok), actual_v, actual_w


def _vehicle_safety(vehicle: Any, *, stale_after_s: float) -> tuple[bool, str]:
    if vehicle is None or not bool(getattr(vehicle, "is_connected", lambda: False)()):
        return False, "SEER STATE 연결 없음"
    try:
        if bool(vehicle.is_rx_stale(stale_after_s)):
            return False, f"SEER STATE stale > {stale_after_s:.1f}s"
    except Exception:
        return False, "SEER STATE freshness 확인 실패"
    if bool(getattr(vehicle, "_emergency", False)) or bool(getattr(vehicle, "_physical_emergency", False)) or bool(getattr(vehicle, "_driver_emergency", False)):
        return False, "SEER emergency"
    if bool(getattr(vehicle, "_blocked", False)):
        return False, "SEER blocked"
    motor_enabled, motor_source, _electric = _motor_status_snapshot(vehicle)
    # ``electric`` from combined STATUS 1100 is firmware-dependent and is not
    # authoritative enough to prevent even a zero-velocity CONTROL probe.
    # We never auto-enable the motors here.  If motion is genuinely disabled,
    # the SEER controller rejects API 2010 and the existing ret_code/exception
    # path stops docking safely.  Explicit motor_* signals still fail closed.
    if not motor_enabled and motor_source != "electric":
        return False, "SEER motor disabled"
    return True, "safe"


async def _safe_stop(vehicle: Any) -> str:
    """도킹의 open-loop 속도만 빠르게 정지하고 TASK 상태는 건드리지 않는다."""

    if vehicle is None:
        return "vehicle unavailable"

    # 2026-08-27 실물 로그에서 blocked 순간마다 공용 control.stop()이
    # API 2000 뒤에 TASK_CANCEL(3003)까지 호출하면서 약 2~3초의 응답 지연이
    # 생겼다. 도킹은 Path Nav가 아니라 API 2010 dead-man 속도 제어이므로
    # 여기서는 CONTROL 포트의 정지 명령만 사용해야 다음 zero-probe 복구가
    # 같은 포트에서 결정론적으로 이어진다.
    stop_timeout_s = max(0.25, min(0.80, float(getattr(vehicle, "command_timeout", 3.0))))
    try:
        sender = getattr(vehicle, "send_command", None)
        if callable(sender):
            await asyncio.wait_for(sender("stop", timeout=stop_timeout_s), timeout=stop_timeout_s + 0.15)
        else:
            # 테스트 더블/구버전 호환용이다. 실물 SeerClient는 위 경로를 탄다.
            await asyncio.wait_for(vehicle.control.stop(), timeout=stop_timeout_s + 0.15)
        vehicle._vx = 0.0
        vehicle._vy = 0.0
        vehicle._w = 0.0
        return "API 2000 open-loop stop confirmed"
    except Exception as first:
        try:
            await asyncio.wait_for(
                vehicle.control.drive_native(
                    vx_mps=0.0, vy_mps=0.0, w_rad_s=0.0, duration_ms=100
                ),
                timeout=stop_timeout_s + 0.15,
            )
            vehicle._vx = 0.0
            vehicle._vy = 0.0
            vehicle._w = 0.0
            return f"API 2010 zero fallback confirmed after stop error: {first}"
        except Exception as second:
            return f"stop transport failed: {type(first).__name__}: {first}; zero fallback: {type(second).__name__}: {second}"


async def _zero_control_probe(vehicle: Any, *, timeout_s: float = 0.9) -> tuple[bool, str, Any]:
    """blocked 해제 뒤 비영속 zero 명령으로 CONTROL 연결만 안전하게 확인한다."""

    try:
        response = await asyncio.wait_for(
            vehicle.control.drive_native(
                vx_mps=0.0, vy_mps=0.0, w_rad_s=0.0, duration_ms=100
            ),
            timeout=max(0.25, float(timeout_s)),
        )
        return True, "CONTROL zero probe accepted", response
    except Exception as exc:
        return False, f"CONTROL zero probe {type(exc).__name__}: {exc}", None


def _camera_display_metrics(
    detection: Optional[TagDetection],
    depth_m: np.ndarray | None,
    *,
    source: Any,
    t_base_camera: np.ndarray,
    t_marker_dock: np.ndarray,
    cfg: Mapping[str, Any],
    depth_cfg: Mapping[str, Any],
    rgbd_pose: RgbdWallPose | None = None,
) -> dict[str, Any]:
    """Return the exact tracker values shown in the RGB overlay and WebUI table.

    Primary pose selection is intentionally confidence ordered:

    1. RGB-D wall-plane pose, when a robust plane can be fitted around the
       wall-mounted tag.  This uses a much wider physical baseline than the
       40-mm square itself and avoids planar-PnP yaw blow-up at long range.
    2. IPPE/PnP marker-centre normal geometry as a fallback.

    The PnP values are retained in separate diagnostic fields so the operator
    can see when the small-square solution disagrees with the RGB-D wall pose.
    These are display measurements, not motion-validity decisions.
    """

    metrics: dict[str, Any] = {
        "axis_distance_m": None,
        "axis_error_m": None,
        "yaw_error_deg": None,
        "du_error_px": None,
        "tag_depth_m": None,
        "tag_depth_source": "-",
        "marker_pitch_error_deg": None,
        "marker_pitch_deg": None,
        "marker_pitch_raw_deg": None,
        "marker_side_px": None,
        "reprojection_error_px": None,
        "display_target_du_px": None,
        "display_du_px": None,
        "display_target_pitch_deg": None,
        "display_bearing_x_deg": None,
        "display_bearing_y_deg": None,
        "display_estimated_tag_size_m": None,
        "pose_measurement_source": "-",
        "pose_measurement_confidence": None,
        "wall_plane_rms_mm": None,
        "wall_plane_inlier_ratio": None,
        "wall_plane_inliers": None,
        "wall_plane_span_m": None,
        "pose_position_sigma_m": None,
        "pose_yaw_sigma_deg": None,
        "pnp_axis_distance_m": None,
        "pnp_axis_error_m": None,
        "pnp_yaw_error_deg": None,
    }
    if detection is None:
        return metrics

    cx = float(source.camera_matrix[0, 2])
    cy = float(source.camera_matrix[1, 2])
    center = np.asarray(detection.corners_px, dtype=float).mean(axis=0)
    du = float(center[0] - cx)
    dv = float(center[1] - cy)
    metrics["display_du_px"] = du
    metrics["display_bearing_x_deg"] = math.degrees(
        math.atan2(du, float(source.camera_matrix[0, 0]))
    )
    metrics["display_bearing_y_deg"] = math.degrees(
        math.atan2(dv, float(source.camera_matrix[1, 1]))
    )
    metrics["marker_side_px"] = float(detection.marker_side_px)
    metrics["reprojection_error_px"] = float(detection.reprojection_error_px)
    metrics["marker_pitch_raw_deg"] = float(detection.marker_pitch_raw_deg)
    if detection.angle_reliable:
        metrics["marker_pitch_deg"] = float(detection.marker_pitch_deg)

    # Always keep the direct PnP result for diagnostics/fallback.  Translation
    # from PnP is usually useful even when the tiny planar target's normal is
    # not trustworthy enough for yaw control.
    pnp_axis: tuple[float, float, float] | None = None
    try:
        pnp_axis = base_pose_from_marker_center_axis(
            t_base_camera,
            detection.t_camera_marker,
        )
        metrics["pnp_axis_distance_m"] = float(pnp_axis[0])
        metrics["pnp_axis_error_m"] = float(pnp_axis[1])
        metrics["pnp_yaw_error_deg"] = math.degrees(float(pnp_axis[2]))
    except (ValueError, FloatingPointError):
        pnp_axis = None

    if rgbd_pose is not None:
        axis_x = float(rgbd_pose.x_m)
        axis_y = float(rgbd_pose.y_m)
        axis_yaw = float(rgbd_pose.yaw_rad)
        metrics["pose_measurement_source"] = "RGBD_WALL_PLANE"
        metrics["pose_measurement_confidence"] = float(rgbd_pose.confidence)
        metrics["wall_plane_rms_mm"] = 1000.0 * float(rgbd_pose.plane_rms_m)
        metrics["wall_plane_inlier_ratio"] = float(rgbd_pose.inlier_ratio)
        metrics["wall_plane_inliers"] = int(rgbd_pose.inlier_count)
        metrics["wall_plane_span_m"] = float(rgbd_pose.horizontal_span_m)
        metrics["pose_position_sigma_m"] = float(rgbd_pose.position_sigma_m)
        metrics["pose_yaw_sigma_deg"] = math.degrees(float(rgbd_pose.yaw_sigma_rad))
    elif pnp_axis is not None:
        axis_x, axis_y, axis_yaw = pnp_axis
        metrics["pose_measurement_source"] = "PNP_FALLBACK"
        # Pixel footprint is a useful intuitive confidence proxy for the small
        # planar target.  Do not overstate precision at long range.
        side = max(1.0, float(detection.marker_side_px))
        metrics["pose_measurement_confidence"] = float(np.clip(side / 80.0, 0.10, 0.85))
        metrics["pose_yaw_sigma_deg"] = float(np.clip(90.0 / side, 0.35, 6.0))
        metrics["pose_position_sigma_m"] = max(0.003, 0.003 * float(detection.t_camera_marker[2, 3]))
    else:
        axis_x = axis_y = axis_yaw = None

    if axis_x is not None:
        metrics["axis_distance_m"] = float(axis_x)
        metrics["axis_error_m"] = float(axis_y)
        metrics["yaw_error_deg"] = math.degrees(float(axis_yaw))
        try:
            target = visual_target_for_base_distance(
                t_base_camera,
                t_marker_dock,
                source.camera_matrix,
                float(axis_x),
            )
            metrics["display_target_du_px"] = float(target.du_px)
            metrics["du_error_px"] = du - float(target.du_px)
            target_pitch_deg = math.degrees(float(target.pitch_rad))
            metrics["display_target_pitch_deg"] = target_pitch_deg
            if detection.angle_reliable:
                pitch_error = (
                    (float(detection.marker_pitch_deg) - target_pitch_deg + 180.0) % 360.0
                ) - 180.0
                metrics["marker_pitch_error_deg"] = pitch_error
        except (ValueError, FloatingPointError):
            metrics["display_target_du_px"] = 0.0
            metrics["du_error_px"] = du
            metrics["display_target_pitch_deg"] = 0.0
            if detection.angle_reliable:
                metrics["marker_pitch_error_deg"] = float(detection.marker_pitch_deg)
    else:
        # DU and depth are still useful when a pose cannot be formed.
        metrics["display_target_du_px"] = 0.0
        metrics["du_error_px"] = du

    # For display, keep the value alive at close range.  Motion safety still uses
    # _validate_depth() with the stricter configured minimum/tolerance.  The
    # RGB-D plane intersection is the preferred tag-centre depth because it is
    # spatially consistent with the pose being shown.
    if rgbd_pose is not None:
        metrics["tag_depth_m"] = float(rgbd_pose.tag_center_camera_m[2])
        metrics["tag_depth_source"] = "rgbd-plane"
    else:
        display_min_valid_m = max(0.01, float(depth_cfg.get("display_min_valid_m", 0.05)))
        metric_depth = median_depth_at_pixel(
            depth_m,
            center,
            radius_px=int(depth_cfg.get("tag_patch_radius_px", 6)),
            min_valid_m=display_min_valid_m,
            max_valid_m=float(depth_cfg.get("max_valid_m", 6.0)),
        )
        pnp_depth = float(detection.t_camera_marker[2, 3])
        if metric_depth is not None:
            metrics["tag_depth_m"] = float(metric_depth)
            metrics["tag_depth_source"] = "depth"
            if pnp_depth > 1e-6:
                metrics["display_estimated_tag_size_m"] = (
                    float(cfg["tag_size_m"]) * float(metric_depth) / pnp_depth
                )
        elif math.isfinite(pnp_depth) and pnp_depth > 0.0:
            metrics["tag_depth_m"] = pnp_depth
            metrics["tag_depth_source"] = "pnp-fallback"
    return metrics


def _draw_legacy_camera_preview(
    frame: np.ndarray,
    depth_m: np.ndarray | None,
    *,
    source: Any,
    detector: SquareMarkerPoseDetector,
    detection: Optional[TagDetection],
    t_base_camera: np.ndarray,
    t_marker_dock: np.ndarray,
    cfg: Mapping[str, Any],
    depth_cfg: Mapping[str, Any],
    clearance_m: float,
    control_valid: bool,
    control_note: str,
    display_metrics: Mapping[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Render low-latency RGB + depth using the same values as the status table."""

    import cv2

    metrics = dict(
        display_metrics
        if display_metrics is not None
        else _camera_display_metrics(
            detection,
            depth_m,
            source=source,
            t_base_camera=t_base_camera,
            t_marker_dock=t_marker_dock,
            cfg=cfg,
            depth_cfg=depth_cfg,
        )
    )

    # Keep detection at the full 1280x720 source resolution, but render the web
    # preview from a smaller RGB panel before depth colormap/JPEG encoding.  This
    # removes a large amount of CPU work without changing the docking math.
    camera_cfg = cfg.get("camera", {}) if isinstance(cfg.get("camera", {}), Mapping) else {}
    src_h, src_w = frame.shape[:2]
    target_w = max(320, min(src_w, int(camera_cfg.get("preview_rgb_width_px", 800))))
    target_h_limit = max(180, int(camera_cfg.get("preview_rgb_height_px", 450)))
    scale = min(1.0, target_w / max(1, src_w), target_h_limit / max(1, src_h))
    out_w = max(1, int(round(src_w * scale)))
    out_h = max(1, int(round(src_h * scale)))
    if out_w != src_w or out_h != src_h:
        color = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)
    else:
        color = frame.copy()
    sx = out_w / float(src_w)
    sy = out_h / float(src_h)

    cx_full = float(source.camera_matrix[0, 2])
    cy_full = float(source.camera_matrix[1, 2])
    cx = int(round(cx_full * sx))
    cy = int(round(cy_full * sy))
    cv2.drawMarker(color, (cx, cy), (255, 255, 0), cv2.MARKER_CROSS, 22, 2)

    if detection is not None:
        center_full = np.asarray(detection.corners_px, dtype=float).mean(axis=0)
        center = (int(round(center_full[0] * sx)), int(round(center_full[1] * sy)))
        corners = np.asarray(detection.corners_px, dtype=float).copy()
        corners[:, 0] *= sx
        corners[:, 1] *= sy
        corners_i = np.round(corners).astype(np.int32)
        cv2.polylines(color, [corners_i], True, (0, 255, 0), 2)
        cv2.circle(color, center, 4, (0, 0, 255), -1)
        cv2.line(color, (cx, cy), center, (0, 0, 255), 2, cv2.LINE_AA)

    if detection is None:
        hud_lines = [
            (f"DICT={detector.dictionary_name}   ID={detector.target_id}   TAG=LOST", (0, 80, 255)),
            (f"DISTANCE source={'RGB-D' if bool(getattr(source, 'has_depth', False)) else 'ARUCO PnP'}", (235, 235, 235)),
        ]
    else:
        du = metrics.get("display_du_px")
        target_du = metrics.get("display_target_du_px")
        du_error = metrics.get("du_error_px")
        pitch = metrics.get("marker_pitch_deg")
        raw_pitch = metrics.get("marker_pitch_raw_deg")
        target_pitch = metrics.get("display_target_pitch_deg")
        pitch_error = metrics.get("marker_pitch_error_deg")
        axis_x = metrics.get("axis_distance_m")
        axis_y = metrics.get("axis_error_m")
        axis_yaw = metrics.get("yaw_error_deg")
        tag_depth = metrics.get("tag_depth_m")
        depth_source = metrics.get("tag_depth_source", "-")
        estimated_tag_size_m = metrics.get("display_estimated_tag_size_m")
        bearing_x = metrics.get("display_bearing_x_deg")
        bearing_y = metrics.get("display_bearing_y_deg")
        if detection.angle_reliable and None not in (pitch, raw_pitch, target_pitch, pitch_error):
            pitch_text = (
                f"PITCH filtered/raw/target/error={float(pitch):+.2f}/"
                f"{float(raw_pitch):+.2f}/{float(target_pitch):+.2f}/"
                f"{float(pitch_error):+.2f}deg"
            )
        else:
            pose_src = str(metrics.get("pose_measurement_source", "-") or "-")
            suffix = "RGBD AXIS=OK" if pose_src == "RGBD_WALL_PLANE" else "AXIS PNP UNRELIABLE"
            pitch_text = f"PNP PITCH=UNRELIABLE  {detection.angle_reliability_reason}  {suffix}"
        axis_text = (
            f"AXIS distance={float(axis_x):.3f}m   error={float(axis_y):+.3f}m   yaw={float(axis_yaw):+.2f}deg"
            if None not in (axis_x, axis_y, axis_yaw)
            else "AXIS pose=n/a"
        )
        depth_text = f"DISTANCE PnP={float(detection.t_camera_marker[2, 3]):.3f}m"
        if bool(getattr(source, "has_depth", False)):
            depth_text += (
                f"   shown={float(tag_depth):.3f}m({depth_source})"
                if tag_depth is not None
                else "   shown=n/a"
            )
            depth_text += f"   clearance={clearance_m:.3f}m"
        size_text = f"SIZE configured={1000.0 * float(cfg['tag_size_m']):.0f}mm"
        size_text += (
            f"   depth-estimated={1000.0 * float(estimated_tag_size_m):.0f}mm"
            if estimated_tag_size_m is not None
            else "   depth-estimated=n/a"
        )
        pose_source = str(metrics.get("display_pose_source", metrics.get("pose_measurement_source", "-")) or "-")
        pose_conf = metrics.get("pose_measurement_confidence")
        plane_rms = metrics.get("wall_plane_rms_mm")
        yaw_sigma = metrics.get("pose_yaw_sigma_deg")
        quality_text = f"POSE source={pose_source}"
        if pose_conf is not None:
            quality_text += f"   conf={float(pose_conf):.2f}"
        if plane_rms is not None:
            quality_text += f"   wall-RMS={float(plane_rms):.2f}mm"
        if yaw_sigma is not None:
            quality_text += f"   yaw-sigma={float(yaw_sigma):.2f}deg"
        hud_lines = [
            (f"DICT={detector.dictionary_name}   ID={detector.target_id}", (80, 255, 80)),
            (
                f"DU now/target/error={float(du):+.1f}/{float(target_du):+.1f}/{float(du_error):+.1f}px"
                if None not in (du, target_du, du_error)
                else "DU=n/a",
                (80, 255, 80),
            ),
            (
                f"BEARING horizontal={float(bearing_x):+.2f}deg   vertical={float(bearing_y):+.2f}deg"
                if None not in (bearing_x, bearing_y)
                else "BEARING=n/a",
                (235, 235, 235),
            ),
            (pitch_text, (235, 235, 235) if detection.angle_reliable else (0, 80, 255)),
            (
                f"MARKER effective side={detection.marker_side_px:.1f}px   angle minimum={detector.min_angle_marker_side_px:.1f}px",
                (235, 235, 235),
            ),
            (axis_text, (255, 255, 255)),
            (quality_text, (100, 230, 180)),
            (depth_text, (235, 235, 235)),
            (size_text, (235, 235, 235)),
        ]
        if not control_valid and control_note:
            hud_lines.append((f"CONTROL REJECTED: {control_note[:96]}", (0, 165, 255)))

    line_height = 18
    panel_height = min(color.shape[0] - 2, 10 + line_height * len(hud_lines))
    panel = color.copy()
    cv2.rectangle(panel, (2, 2), (color.shape[1] - 3, panel_height), (10, 10, 10), -1)
    cv2.addWeighted(panel, 0.82, color, 0.18, 0.0, color)
    for index, (text, text_color) in enumerate(hud_lines):
        cv2.putText(
            color,
            text,
            (9, 16 + index * line_height),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            text_color,
            1,
            cv2.LINE_AA,
        )

    preview = color
    if bool(getattr(source, "has_depth", False)) and depth_m is not None:
        if depth_m.shape[:2] != (out_h, out_w):
            depth_small = cv2.resize(depth_m, (out_w, out_h), interpolation=cv2.INTER_NEAREST)
        else:
            depth_small = depth_m
        depth = depth_colormap(depth_small, float(depth_cfg.get("preview_max_m", 4.0)))
        preview = np.hstack([color, depth])
    return preview, metrics

def _render_and_write_preview(
    preview_path: Path,
    frame: np.ndarray,
    depth_m: np.ndarray | None,
    *,
    source: Any,
    detector: SquareMarkerPoseDetector,
    detection: Optional[TagDetection],
    t_base_camera: np.ndarray,
    t_marker_dock: np.ndarray,
    cfg: Mapping[str, Any],
    depth_cfg: Mapping[str, Any],
    clearance_m: float,
    control_valid: bool,
    control_note: str,
    jpeg_quality: int,
    display_metrics: Mapping[str, Any] | None = None,
    telemetry_path: Path | None = None,
    telemetry: Mapping[str, Any] | None = None,
    recorder: DockingSessionRecorder | None = None,
) -> dict[str, Any]:
    """Render + JPEG encode + publish entirely off the docking control loop."""

    started = time.monotonic()
    preview, rendered_metrics = _draw_legacy_camera_preview(
        frame,
        depth_m,
        source=source,
        detector=detector,
        detection=detection,
        t_base_camera=t_base_camera,
        t_marker_dock=t_marker_dock,
        cfg=cfg,
        depth_cfg=depth_cfg,
        clearance_m=clearance_m,
        control_valid=control_valid,
        control_note=control_note,
        display_metrics=display_metrics,
    )
    _write_preview(preview_path, preview, jpeg_quality=jpeg_quality)
    completed_wall = time.time()
    frame_status = dict(telemetry or {})
    # The image HUD, recorder and browser telemetry all use the exact same
    # tracker snapshot.  This keeps replay, live table and camera overlay aligned.
    frame_status.update(rendered_metrics)
    frame_status["frame_updated_at"] = completed_wall
    frame_status["updated_at"] = completed_wall
    if recorder is not None:
        recorder.submit_frame(preview, frame_status)
    if telemetry_path is not None and telemetry is not None:
        try:
            atomic_write_json(Path(telemetry_path), frame_status, timeout_s=0.20)
        except (OSError, TimeoutError):
            # The image has already been published.  A later frame gets another
            # chance to publish telemetry, so never stall the detector/control
            # loop on this debug/UI sidecar.
            pass
    ended = time.monotonic()
    return {
        "duration_ms": 1000.0 * (ended - started),
        "completed_monotonic": ended,
        "completed_wall": completed_wall,
    }


def _control_port_health(vehicle: Any) -> dict[str, Any]:
    """Return a local-only snapshot of the existing SeerClient CONTROL port."""

    getter = getattr(vehicle, "connection_health", None)
    if not callable(getter):
        return {}
    try:
        health = getter()
    except Exception as exc:
        return {"connected": False, "last_error": f"{type(exc).__name__}: {exc}"}
    ports = health.get("ports", {}) if isinstance(health, Mapping) else {}
    control = ports.get("CONTROL", {}) if isinstance(ports, Mapping) else {}
    return dict(control) if isinstance(control, Mapping) else {}


async def _refresh_live_state(vehicle: Any) -> str:
    """LIVE 안전 판정 직전에 STATE 캐시를 한 번 최신화한다.

    카메라 준비나 PREVIEW→LIVE 전환이 길어지면 기존 캐시가 1~2초 이상 오래될 수 있다.
    LIVE 시작 직전에는 같은 SeerClient의 직렬화된 STATE 요청을 한 번 수행해 stale 캐시 때문에
    실제로 안전한 장비를 시작 단계에서 거부하지 않도록 한다.
    """

    poll_once = getattr(vehicle, "_poll_once", None)
    if not callable(poll_once):
        service = getattr(vehicle, "status_service", None)
        poll_once = getattr(service, "poll_once", None)
    if not callable(poll_once):
        return "STATE refresh unavailable"
    try:
        await poll_once()
        return "STATE refreshed"
    except Exception as exc:
        return f"STATE refresh failed: {type(exc).__name__}: {exc}"


async def _live_control_preflight(
    vehicle: Any,
    cfg: Mapping[str, Any],
    publish: Any,
) -> tuple[Any, dict[str, Any], str]:
    """Verify live motion without treating a recoverable CONTROL socket as fatal.

    SEER logical ports reconnect independently.  A CONTROL socket can be down
    while STATE is healthy, and ``drive_native`` already reconnects that port
    *before* sending a request.  The old live preflight inspected the local
    health snapshot first and failed immediately, which prevented that safe
    reconnect path from ever running.

    A zero-velocity API-2010 request is the authoritative live-motion probe.
    It is retried only when no non-zero motion has been requested.  Emergency,
    blocked and motor-disabled states are never bypassed.
    """

    seer_cfg = dict(cfg.get("seer", {})) if isinstance(cfg.get("seer", {}), Mapping) else {}
    retries = max(1, min(10, int(seer_cfg.get("preflight_retries", 3))))
    retry_delay_s = max(0.0, min(2.0, float(seer_cfg.get("preflight_retry_delay_s", 0.25))))
    stale_after_s = max(0.2, float(seer_cfg.get("status_timeout_s", seer_cfg.get("state_timeout_s", 1.5))))

    model = str(getattr(vehicle, "_robot_model", "") or "")
    expected_model = str(seer_cfg.get("expected_model_contains", "SBA-400EU") or "").strip()
    if bool(seer_cfg.get("strict_model_match", False)) and expected_model and expected_model not in model:
        raise RuntimeError(
            f"LIVE preflight refused: unexpected robot model {model or '-'}; "
            f"expected contains {expected_model}"
        )

    mode = str(getattr(vehicle, "_mode", "") or "").strip()
    mode_warning = ""
    if bool(seer_cfg.get("require_manual_mode", False)) and mode and "manual" not in mode.lower():
        # Firmware revisions differ in the text/value exposed for operation
        # mode.  Do not reject solely from that cached label.  The correlated
        # zero-velocity API-2010 response below is the real authority on whether
        # the controller currently accepts open-loop motion.
        mode_warning = f"configured manual mode preferred; reported mode={mode}"

    last_detail = "preflight not attempted"
    last_health: dict[str, Any] = {}
    for attempt in range(1, retries + 1):
        state_refresh = await _refresh_live_state(vehicle)
        safe, reason = _vehicle_safety(vehicle, stale_after_s=stale_after_s)
        motor_enabled, motor_source, electric_state = _motor_status_snapshot(vehicle)
        motor_warning = (
            "STATUS 1100 electric=false is advisory; CONTROL API 2010 will decide motion acceptance"
            if (not motor_enabled and motor_source == "electric")
            else ""
        )
        last_health = _control_port_health(vehicle)
        publish(
            "starting",
            "LIVE_PREFLIGHT",
            f"SEER live-motion preflight {attempt}/{retries}: {reason}",
            state_refresh=state_refresh,
            preflight_attempt=attempt,
            preflight_retries=retries,
            seer_mode=mode or "-",
            seer_mode_warning=mode_warning,
            motor_enabled=motor_enabled,
            motor_flag_source=motor_source,
            electric_state=electric_state,
            motor_warning=motor_warning,
            control_connected=last_health.get("connected"),
            control_reconnect_count=last_health.get("reconnect_count"),
            control_last_error=last_health.get("last_error", ""),
        )
        if not safe:
            last_detail = reason
            # These are real safety interlocks, not transport races.  Waiting
            # and silently retrying them could surprise the operator.
            if reason in {"SEER emergency", "SEER blocked", "SEER motor disabled"}:
                break
        else:
            try:
                response = await vehicle.control.drive_native(
                    vx_mps=0.0, vy_mps=0.0, w_rad_s=0.0, duration_ms=100
                )
                last_health = _control_port_health(vehicle)
                publish(
                    "starting",
                    "LIVE_READY",
                    "SEER CONTROL zero-velocity probe accepted; waiting for first camera frame",
                    preflight_attempt=attempt,
                    preflight_retries=retries,
                    seer_mode=mode or "-",
                    seer_mode_warning=mode_warning,
                    motor_enabled=motor_enabled,
                    motor_flag_source=motor_source,
                    electric_state=electric_state,
                    motor_warning=motor_warning,
                    control_probe_response=response,
                    control_connected=last_health.get("connected"),
                    control_reconnect_count=last_health.get("reconnect_count"),
                    control_last_error=last_health.get("last_error", ""),
                )
                return response, last_health, mode_warning
            except Exception as exc:
                last_detail = f"API 2010 zero probe: {type(exc).__name__}: {exc}"
                last_health = _control_port_health(vehicle)

        if attempt < retries:
            await asyncio.sleep(retry_delay_s)

    health_detail = str(last_health.get("last_error", "") or "").strip()
    suffix = f"; CONTROL={health_detail}" if health_detail else ""
    mode_suffix = f"; mode={mode}" if mode else ""
    raise RuntimeError(
        f"LIVE preflight refused after {retries} attempt(s): {last_detail}{suffix}{mode_suffix}"
    )


async def run_camera_docking(
    vehicle: Any,
    *,
    live: bool,
    action_id: str = "",
    max_runtime_s: Optional[float] = None,
    config_path: Optional[Path] = None,
    manual_stage: Optional[str] = None,
) -> DockingRunResult:
    """Run the AprilTag docking loop while reusing an existing SeerClient."""

    config_path = Path(config_path or default_config_path())
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    status_path, preview_path = _runtime_paths()
    telemetry_path = preview_telemetry_path(preview_path)
    manual_stage = str(manual_stage or "").strip().lower()
    if manual_stage not in {"", "centerline", "yaw", "straight"}:
        raise ValueError("manual_stage must be centerline, yaw, or straight")
    if manual_stage and not live:
        raise ValueError("manual_stage requires live=True")
    session_id = uuid.uuid4().hex
    run_mode = f"STAGE_{manual_stage.upper()}" if manual_stage else ("LIVE" if live else "PREVIEW")
    start_wall = time.time()
    # Remove the previous run's final JPEG immediately.  If the new camera open
    # fails, the WebUI must not make a frozen old frame look like a live stream.
    _clear_stale_preview(preview_path)
    start_mono = time.monotonic()
    last_status_write = 0.0
    status_write_error_count = 0
    status_last_write_error = ""
    last_preview_write = 0.0
    preview_task: Optional[asyncio.Task[Any]] = None
    laser_task: Optional[asyncio.Task[Any]] = None
    imu_task: Optional[asyncio.Task[Any]] = None
    latest_laser_points: list[list[float]] = []
    latest_laser_mono: float | None = None
    laser_last_error = ""
    laser_latency_ms: float | None = None
    laser_source = "-"
    laser_schema = "-"
    laser_attempts: list[str] = []
    laser_diagnostics: list[str] = []
    laser_step_param: int | None = None
    laser_poll_count = 0
    latest_imu_attitude: ImuAttitude | None = None
    latest_imu_mono: float | None = None
    imu_last_error = ""
    imu_latency_ms: float | None = None
    imu_source = "-"
    imu_schema = "-"
    imu_poll_count = 0
    preview_seq = 0
    preview_drop_count = 0
    preview_last_complete_mono: Optional[float] = None
    preview_last_duration_ms: Optional[float] = None
    preview_window_started = time.monotonic()
    preview_window_completed = 0
    preview_fps_actual = 0.0
    camera_window_started = time.monotonic()
    camera_window_frames = 0
    camera_loop_hz = 0.0
    camera_cfg = cfg.get("camera", {}) if isinstance(cfg.get("camera", {}), Mapping) else {}
    preview_fps = max(1.0, min(30.0, float(camera_cfg.get("web_preview_fps", 20.0))))
    preview_interval_s = 1.0 / preview_fps
    preview_jpeg_quality = max(45, min(95, int(camera_cfg.get("web_preview_jpeg_quality", 78))))
    runtime_limit = float(max_runtime_s if max_runtime_s is not None else (cfg.get("live_max_runtime_s", 300.0) if live else 300.0))
    runtime_limit = max(1.0, min(runtime_limit, 1800.0))
    recording_cfg = cfg.get("recording", {}) if isinstance(cfg.get("recording", {}), Mapping) else {}
    recorder = DockingSessionRecorder(
        status_path.parent / "seer-docking-recordings",
        session_id=session_id,
        started_at=start_wall,
        robot_ip=str(getattr(vehicle, "robot_ip", "") or ""),
        action_id=action_id,
        run_mode=run_mode,
        config=recording_cfg,
    )
    last_recorded_lifecycle: tuple[str, str, str] | None = None

    def publish(status: str, phase: str, message: str, **fields: Any) -> None:
        nonlocal last_status_write, status_write_error_count, status_last_write_error, last_recorded_lifecycle
        now = time.time()
        payload = {
            "schema": 1,
            "session_id": session_id,
            "updated_at": now,
            "started_at": start_wall,
            "status": status,
            "run_mode": run_mode,
            "manual_stage": manual_stage or "",
            "phase": phase,
            "message": message,
            "action_id": action_id,
            "robot_ip": str(getattr(vehicle, "robot_ip", "") or ""),
            "camera_mount_x_m": float(cfg.get("camera_mount", {}).get("xyz_m", [0.0])[0]),
            "camera_mount_y_m": float(cfg.get("camera_mount", {}).get("xyz_m", [0.0, 0.0])[1]),
            **recorder.status_fields(),
            **fields,
        }
        try:
            _atomic_json(status_path, payload)
            last_status_write = now
            status_last_write_error = ""
        except (OSError, TimeoutError) as exc:
            status_write_error_count += 1
            status_last_write_error = f"{type(exc).__name__}: {exc}"
        lifecycle = (str(status), str(phase), str(message))
        if lifecycle != last_recorded_lifecycle:
            recorder.log_telemetry(
                {
                    "timestamp_epoch_s": now,
                    "status": status,
                    "phase": phase,
                    "note": message,
                    "blocked": bool(getattr(vehicle, "_blocked", False)),
                    "emergency": bool(getattr(vehicle, "_emergency", False)),
                    **fields,
                }
            )
            last_recorded_lifecycle = lifecycle

    if live:
        if manual_stage:
            publish("starting", "STAGE_START", f"수동 단계 테스트 시작: {manual_stage} (자동 복구 없음)")
        else:
            publish("starting", "LIVE_START", "실물 도킹 시작 준비 중입니다")
    else:
        publish("starting", "CAMERA_OPEN", "RealSense 카메라를 여는 중입니다")
    camera_cfg = dict(cfg.get("camera", {}))
    tracking_cfg = dict(cfg.get("marker_tracking", {}))
    display_hold_s = max(0.0, min(2.0, float(tracking_cfg.get("display_hold_s", 0.50))))
    display_pose_filter = _StableDisplayPoseFilter(
        median_window=int(tracking_cfg.get("display_pose_median_window", 17)),
        moving_median_window=int(tracking_cfg.get("display_moving_median_window", 3)),
        position_deadband_m=float(tracking_cfg.get("display_position_deadband_m", 0.0008)),
        position_slow_alpha=float(tracking_cfg.get("display_position_slow_alpha", 0.08)),
        position_moving_alpha=float(tracking_cfg.get("display_position_moving_alpha", 0.28)),
        position_fast_alpha=float(tracking_cfg.get("display_position_fast_alpha", 0.50)),
        position_fast_threshold_m=float(tracking_cfg.get("display_position_fast_threshold_m", 0.025)),
        yaw_hold_deadband_deg=float(tracking_cfg.get("display_yaw_hold_deadband_deg", 0.18)),
        yaw_slow_alpha=float(tracking_cfg.get("display_yaw_slow_alpha", 0.05)),
        yaw_moving_alpha=float(tracking_cfg.get("display_yaw_moving_alpha", 0.28)),
        yaw_fast_alpha=float(tracking_cfg.get("display_yaw_fast_alpha", 0.50)),
        yaw_fast_threshold_deg=float(tracking_cfg.get("display_yaw_fast_threshold_deg", 1.20)),
    )
    last_display_metrics: dict[str, Any] | None = None
    last_display_seen_mono: float | None = None
    source = None
    smoother = None
    camera_lease: Optional[CameraLease] = None
    control_probe_response: Any = None
    last_motion_response: Any = None
    last_motion_error = ""
    last_runtime_fields: dict[str, Any] = {}
    first_frame_seen = False
    final_phase = "ABORTED"
    final_message = "도킹 중단"
    try:
        if live:
            # 실제 이동 가능 여부를 카메라 점유보다 먼저 확인한다. CONTROL/STATE 문제라면
            # RealSense를 열었다 닫는 부수효과 없이 즉시 원인을 표시할 수 있다.
            control_probe_response, control_health, _mode_warning = await _live_control_preflight(
                vehicle, cfg, publish
            )

        camera_lease = CameraLease.for_camera(
            camera_cfg,
            robot_ip=str(getattr(vehicle, "robot_ip", "") or ""),
            action_id=action_id,
            run_mode=run_mode,
        )
        try:
            camera_wait_s = (
                max(0.0, min(10.0, float(camera_cfg.get("live_handoff_wait_s", 5.0))))
                if live else 0.0
            )
            if live:
                publish(
                    "starting",
                    "CAMERA_HANDOFF",
                    "PREVIEW camera release 확인 후 LIVE RealSense ownership을 인계받는 중입니다",
                    camera_handoff_wait_s=camera_wait_s,
                    control_probe_response=control_probe_response,
                )
            await asyncio.to_thread(camera_lease.acquire, wait_timeout_s=camera_wait_s)
        except CameraBusyError as exc:
            raise RuntimeError(f"CAMERA_BUSY: {exc}") from exc
        camera_type = str(camera_cfg.get("type", "usb") or "usb").lower()
        camera_profile = (
            f"{int(camera_cfg.get('width', 640))}x{int(camera_cfg.get('height', 480))}"
            f"@{int(camera_cfg.get('fps', 30))} RGB-D"
        )
        open_timeout_s = max(1.0, float(camera_cfg.get("open_timeout_s", 6.0)))
        camera_probe_status = "N/A"
        camera_probe: dict[str, Any] = {}
        if camera_type in {"realsense", "intel_realsense", "rgbd"}:
            probe_mode = str(camera_cfg.get("probe_mode", "quick") or "quick").strip().lower()
            publish(
                "starting",
                "CAMERA_PROBE",
                f"RealSense ownership secured; checking {camera_profile}",
                camera_owner_pid=os.getpid(),
                camera_lease=str(camera_lease.path),
                camera_profile=camera_profile,
                camera_open_timeout_s=open_timeout_s,
                camera_probe_status="running",
            )
            if probe_mode in {"full", "diagnostic", "open"}:
                camera_probe = await asyncio.to_thread(
                    probe_realsense_camera,
                    camera_cfg,
                    timeout_s=open_timeout_s,
                )
            else:
                # Default path is intentionally lightweight: validate device/profile
                # without starting the stream, then let the Adapter open it once.
                camera_probe = await asyncio.to_thread(probe_realsense_camera_quick, camera_cfg)
            if not bool(camera_probe.get("ok", False)):
                code = str(camera_probe.get("code", "OPEN_FAILED") or "OPEN_FAILED")
                detail = str(camera_probe.get("message", "RealSense probe failed") or "RealSense probe failed")
                camera_probe_status = code
                publish(
                    "failed",
                    "CAMERA_OPEN_FAILED",
                    f"{code}: {detail}",
                    camera_owner_pid=os.getpid(),
                    camera_profile=camera_profile,
                    camera_open_timeout_s=open_timeout_s,
                    camera_probe_status=code,
                    camera_probe=camera_probe,
                )
                raise CameraOpenError(f"{code}: {detail}")
            camera_probe_status = str(camera_probe.get("code", "OK_FAST") or "OK_FAST")
            publish(
                "starting",
                "CAMERA_OPEN",
                f"RealSense profile OK; opening {camera_profile} in Adapter",
                camera_owner_pid=os.getpid(),
                camera_lease=str(camera_lease.path),
                camera_profile=camera_profile,
                camera_open_timeout_s=open_timeout_s,
                camera_probe_status=camera_probe_status,
                camera_probe=camera_probe,
            )
            if probe_mode in {"full", "diagnostic", "open"}:
                # Only deep diagnostic mode opens/closes the device before the
                # Adapter, so only that mode needs a release delay.
                await asyncio.sleep(max(0.0, min(1.0, float(camera_cfg.get("probe_release_delay_s", 0.10)))))
        else:
            publish(
                "starting",
                "CAMERA_OPEN",
                f"Camera ownership secured; opening {camera_profile}",
                camera_owner_pid=os.getpid(),
                camera_lease=str(camera_lease.path),
                camera_profile=camera_profile,
            )
        try:
            source = await asyncio.to_thread(
                create_camera_source,
                camera_cfg,
                cfg.get("camera_calibration"),
            )
        except Exception as exc:
            if camera_type in {"realsense", "intel_realsense", "rgbd"}:
                raise CameraOpenError(f"ADAPTER_OPEN_FAILED: {type(exc).__name__}: {exc}") from exc
            raise
        publish(
            "starting",
            "CAMERA_READY",
            "RealSense camera opened; initializing tracker",
            camera_owner_pid=os.getpid(),
            camera_profile=camera_profile,
            camera_probe_status=camera_probe_status,
            camera_probe=camera_probe,
            camera_open_timeout_s=open_timeout_s,
        )
        detector = SquareMarkerPoseDetector(
            source.camera_matrix,
            source.distortion,
            cfg["tag_size_m"],
            cfg["tag_id"],
            cfg.get("marker_dictionary", "DICT_4X4_50"),
            corner_smoothing_alpha=float(tracking_cfg.get("corner_smoothing_alpha", 1.0)),
            corner_median_window=int(tracking_cfg.get("corner_median_window", 5)),
            corner_outlier_px=float(tracking_cfg.get("corner_outlier_px", 1.0)),
            corner_motion_reset_px=float(tracking_cfg.get("corner_motion_reset_px", 18.0)),
            max_tracking_misses=int(tracking_cfg.get("max_missed_frames", 5)),
            angle_median_window=int(tracking_cfg.get("angle_median_window", 7)),
            angle_hold_deadband_deg=float(tracking_cfg.get("angle_hold_deadband_deg", 0.2)),
            angle_slow_alpha=float(tracking_cfg.get("angle_slow_alpha", 0.1)),
            angle_fast_alpha=float(tracking_cfg.get("angle_fast_alpha", 0.55)),
            angle_fast_threshold_deg=float(tracking_cfg.get("angle_fast_threshold_deg", 2.0)),
            angle_oblique_deadband_gain=float(tracking_cfg.get("angle_oblique_deadband_gain", 0.015)),
            angle_max_hold_deadband_deg=float(tracking_cfg.get("angle_max_hold_deadband_deg", 1.2)),
            pose_candidate_error_margin_px=float(tracking_cfg.get("pose_candidate_error_margin_px", 0.35)),
            min_angle_marker_side_px=float(tracking_cfg.get("min_angle_marker_side_px", 14.0)),
            angle_reliability_hysteresis_px=float(tracking_cfg.get("angle_reliability_hysteresis_px", 2.0)),
        )
        depth_cfg = dict(camera_cfg.get("depth", {}))
        depth_enabled = bool(getattr(source, "has_depth", False) and depth_cfg.get("enabled", True))
        if live and bool(depth_cfg.get("required_for_live", True)) and not depth_enabled:
            raise RuntimeError("LIVE 도킹은 RealSense metric depth가 필요합니다")
        t_b_c = _mount_transform(cfg)
        t_m_d = _matrix4(cfg, "t_marker_dock")
        fusion_cfg = dict(cfg.get("pose_fusion", {}))
        lidar_cfg = dict(fusion_cfg.get("lidar", {}))
        imu_cfg = dict(fusion_cfg.get("imu", {}))
        localization_cfg = dict(fusion_cfg.get("seer_localization", {}))
        lidar_enabled = bool(lidar_cfg.get("enabled", True))
        lidar_poll_hz = max(0.5, min(10.0, float(lidar_cfg.get("poll_hz", 3.0))))
        lidar_poll_interval_s = 1.0 / lidar_poll_hz
        lidar_step = max(0, int(lidar_cfg.get("step", 8)))
        lidar_max_age_s = max(0.15, float(lidar_cfg.get("max_age_s", 0.8)))
        lidar_failure_retry_s = max(0.5, min(10.0, float(lidar_cfg.get("failure_retry_s", 2.0))))
        laser_next_poll_mono = 0.0
        imu_enabled = bool(imu_cfg.get("enabled", True))
        imu_poll_hz = max(0.5, min(5.0, float(imu_cfg.get("poll_hz", 2.0))))
        imu_poll_interval_s = 1.0 / imu_poll_hz
        imu_max_age_s = max(0.15, float(imu_cfg.get("max_age_s", 0.75)))
        imu_failure_retry_s = max(0.5, min(10.0, float(imu_cfg.get("failure_retry_s", 2.0))))
        imu_yaw_rate_weight = max(0.0, min(1.0, float(imu_cfg.get("yaw_rate_imu_weight", 0.70))))
        imu_next_poll_mono = 0.0
        imu_yaw_fusion = ImuMapYawFusion(
            map_correction_gain=float(imu_cfg.get("map_yaw_correction_gain", 0.20)),
            max_map_innovation_deg=float(imu_cfg.get("max_map_yaw_innovation_deg", 12.0)),
            max_imu_step_deg=float(imu_cfg.get("max_imu_step_deg", 45.0)),
            max_imu_rate_rps=float(imu_cfg.get("max_imu_rate_rps", 2.5)),
            yaw_rate_alpha=float(imu_cfg.get("yaw_rate_alpha", 0.35)),
            yaw_rate_deadband_rps=float(imu_cfg.get("yaw_rate_deadband_rps", 0.004)),
        )
        tag_localization_fusion = SeerTagRelativeFusion(
            position_correction_gain=float(localization_cfg.get("position_correction_gain", 0.12)),
            yaw_correction_gain=float(localization_cfg.get("yaw_correction_gain", 0.10)),
            stationary_gain_scale=float(localization_cfg.get("stationary_gain_scale", 1.0)),
            max_position_innovation_m=float(localization_cfg.get("max_position_innovation_m", 0.25)),
            max_yaw_innovation_deg=float(localization_cfg.get("max_yaw_innovation_deg", 15.0)),
            min_measurement_confidence=float(localization_cfg.get("min_measurement_confidence", 0.30)),
            stationary_lock_enabled=bool(localization_cfg.get("stationary_lock_enabled", True)),
            stationary_v_threshold_mps=float(localization_cfg.get("stationary_v_threshold_mps", 0.006)),
            stationary_w_threshold_rps=float(localization_cfg.get("stationary_w_threshold_rps", 0.010)),
            stationary_map_step_m=float(localization_cfg.get("stationary_map_step_m", 0.008)),
            stationary_map_step_yaw_deg=float(localization_cfg.get("stationary_map_step_yaw_deg", 0.20)),
            pnp_anchor_window=int(localization_cfg.get("pnp_anchor_window", 9)),
            pnp_anchor_max_position_spread_m=float(localization_cfg.get("pnp_anchor_max_position_spread_m", 0.10)),
            pnp_anchor_max_yaw_spread_deg=float(localization_cfg.get("pnp_anchor_max_yaw_spread_deg", 8.0)),
            pnp_moving_position_gain_scale=float(localization_cfg.get("pnp_moving_position_gain_scale", 0.10)),
            pnp_moving_yaw_gain_scale=float(localization_cfg.get("pnp_moving_yaw_gain_scale", 0.08)),
        )
        world_lock_cfg = dict(localization_cfg.get("world_axis_lock", {})) if isinstance(localization_cfg.get("world_axis_lock", {}), Mapping) else {}
        world_axis_lock_enabled = bool(world_lock_cfg.get("enabled", True))
        world_axis_lock_required_samples = max(3, min(15, int(world_lock_cfg.get("samples", 7))))
        world_axis_lock_max_position_spread_m = max(0.002, float(world_lock_cfg.get("max_position_spread_m", 0.025)))
        world_axis_lock_max_yaw_spread_rad = math.radians(max(0.10, float(world_lock_cfg.get("max_yaw_spread_deg", 1.5))))
        world_axis_lock_max_age_s = max(5.0, float(world_lock_cfg.get("persist_max_age_s", 900.0)))
        world_axis_lock_candidates: deque[tuple[float, float, float]] = deque(maxlen=world_axis_lock_required_samples)
        world_axis_lock_sample_count = 0
        world_axis_lock_file = _world_axis_lock_path(status_path)
        world_axis_locked = False
        world_axis_lock_source = "-"
        world_axis_lock_saved_at: float | None = None
        world_axis_lock_loaded = False
        if world_axis_lock_enabled and manual_stage in {"yaw", "straight"}:
            restored = _load_world_axis_lock(
                world_axis_lock_file,
                robot_ip=str(getattr(vehicle, "robot_ip", "") or ""),
                max_age_s=world_axis_lock_max_age_s,
            )
            if restored is not None:
                anchor_pose, lock_meta = restored
                tag_localization_fusion.restore_anchor_pose(anchor_pose, frozen=True)
                world_axis_locked = True
                world_axis_lock_loaded = True
                world_axis_lock_source = str(lock_meta.get("source", "PERSISTED WORLD AXIS LOCK") or "PERSISTED WORLD AXIS LOCK")
                try:
                    world_axis_lock_sample_count = int(lock_meta.get("sample_count", world_axis_lock_required_samples) or world_axis_lock_required_samples)
                except (TypeError, ValueError):
                    world_axis_lock_sample_count = world_axis_lock_required_samples
                try:
                    world_axis_lock_saved_at = float(lock_meta.get("saved_at", 0.0) or 0.0)
                except (TypeError, ValueError):
                    world_axis_lock_saved_at = None

        min_localization_score = float(localization_cfg.get("min_score", 0.25))
        use_rgbd_for_pose_fusion = bool(fusion_cfg.get("use_rgbd_for_pose_fusion", False))
        params = DockParams(**cfg.get("dock_params", {}))
        mount_cfg = dict(cfg.get("camera_mount", {}))
        mount_xyz = list(mount_cfg.get("xyz_m", [0.0, 0.0, 0.0]))
        mount_rpy = list(mount_cfg.get("rpy_deg", [0.0, 0.0, 0.0]))
        controller = CameraVisualServoController(
            params,
            **cfg.get("visual_servo", {}),
            camera_forward_m=float(mount_xyz[0]),
            camera_left_m=float(mount_xyz[1]),
            camera_yaw_rad=math.radians(float(mount_rpy[2])),
        )
        if manual_stage:
            # Stage-test buttons are deliberately one-shot.  Never let the
            # visual controller initiate reverse/FOV recovery motion.  Safety
            # stops remain active, but a stop ends the stage instead of
            # automatically resuming it.
            controller.recovery_enabled = False
        safety = SafetyGate(params, **cfg.get("safety", {}))
        fov_guard = TagFovGuard(FovGuardParams(**dict(camera_cfg.get("fov_guard", {}))))
        smooth_cfg = dict(cfg.get("live_motion_smoothing", {}))
        smoother = LiveVelocitySmoother(
            max_linear_accel_mps2=float(smooth_cfg.get("max_linear_accel_mps2", 0.15)),
            max_linear_decel_mps2=float(smooth_cfg.get("max_linear_decel_mps2", 0.25)),
            max_angular_accel_rps2=float(smooth_cfg.get("max_angular_accel_rps2", 0.30)),
            max_angular_decel_rps2=float(smooth_cfg.get("max_angular_decel_rps2", 0.50)),
            linear_zero_epsilon_mps=float(smooth_cfg.get("linear_zero_epsilon_mps", 0.003)),
            angular_zero_epsilon_rps=float(smooth_cfg.get("angular_zero_epsilon_rps", 0.008)),
            max_dt_s=float(smooth_cfg.get("max_dt_s", 0.20)),
            phase_interlock=bool(smooth_cfg.get("phase_interlock", True)),
        )
        estimator: Optional[PlanarDockEKF] = None
        estimator_pose_reliable = False
        coarse_axis_history: deque[tuple[float, float, float]] = deque(maxlen=5)
        coarse_axis_min_side_px = float(tracking_cfg.get("min_coarse_axis_marker_side_px", 8.0))
        last_time = time.monotonic()
        last_v = float(getattr(vehicle, "_vx", 0.0) or 0.0)
        last_w = float(getattr(vehicle, "_w", 0.0) or 0.0)
        success_hold_since: Optional[float] = None
        final_success_hold_s = max(0.5, float(cfg.get("visual_servo", {}).get("final_success_hold_s", 3.0)))
        last_visual_valid_mono: Optional[float] = None
        control_visual_grace_s = max(0.0, min(0.35, float(camera_cfg.get("control_visual_grace_s", 0.20))))
        hard_stop_latched = False
        hard_stop_clear_since: Optional[float] = None
        control_recovery_next_probe_mono = 0.0
        control_recovery_error_count = 0
        control_recovery_last_error = ""
        consecutive_motion_ack_timeouts = 0
        last_live_sent_cmd = VelocityCommand(0.0, 0.0)
        next_motion_tx_mono = 0.0
        motion_tx_deferred = False
        blocked_event_count = 0
        blocked_since_mono: Optional[float] = None
        no_visual_stop_sent = False
        wheel_radius_m = float(cfg.get("robot_model", {}).get("wheel_radius_m", 0.075))
        track_width_m = float(cfg.get("robot_model", {}).get("track_width_m", 0.469))
        reduction_ratio = float(cfg.get("robot_model", {}).get("reduction_ratio", 15.0))
        status_timeout_s = float(cfg.get("seer", {}).get("status_timeout_s", 1.5))
        clear_hold_s = float(cfg.get("seer", {}).get("safety_clear_hold_s", 0.60))
        control_recovery_probe_timeout_s = max(0.25, min(1.5, float(cfg.get("seer", {}).get("control_recovery_probe_timeout_s", 0.90))))
        control_recovery_probe_interval_s = max(0.10, min(2.0, float(cfg.get("seer", {}).get("control_recovery_probe_interval_s", 0.25))))
        motion_command_hz = max(2.0, min(8.0, float(cfg.get("seer", {}).get("motion_command_hz", 5.0))))
        motion_tx_interval_s = 1.0 / motion_command_hz
        motion_duration_ms = max(
            int(math.ceil(1000.0 * motion_tx_interval_s * 2.0)),
            int(cfg.get("seer", {}).get("duration_ms", 450)),
        )
        motion_ack_timeout_s = max(0.15, min(1.0, float(cfg.get("seer", {}).get("motion_ack_timeout_s", 0.28))))
        max_consecutive_motion_ack_timeouts = max(
            1, int(cfg.get("seer", {}).get("max_consecutive_motion_ack_timeouts", 2))
        )
        max_control_recovery_errors = max(1, int(cfg.get("seer", {}).get("max_control_recovery_errors", 5)))

        stage_cfg = dict(cfg.get("manual_stage", {})) if isinstance(cfg.get("manual_stage", {}), Mapping) else {}
        manual_control_loss_grace_s = max(0.5, min(15.0, float(stage_cfg.get("control_loss_grace_s", 4.0))))
        last_control_transport_ok_mono = time.monotonic()
        manual_state_confirmed_count = 0
        if manual_stage:
            # 단계 시험도 축/각도 변화에 즉시 반응해야 하므로 CONTROL 명령은
            # SEER 포트의 0.1 s 최소 간격에 맞춰 최대 10 Hz로 유지한다. Wi-Fi에서
            # ACK만 유실된 경우에는 주기를 낮추거나 후퇴/재접근하지 않고, 신선한
            # STATE vx/w가 실제 명령 반영을 확인하는 동안 최신 명령 전송을 계속한다.
            # STATE stale/emergency/blocked는 기존 안전 경로에서 계속 fail-closed 한다.
            motion_command_hz = max(2.0, min(10.0, float(stage_cfg.get("control_motion_hz", 10.0))))
            motion_tx_interval_s = 1.0 / motion_command_hz
            motion_duration_ms = max(
                int(math.ceil(1000.0 * motion_tx_interval_s * 2.5)),
                int(stage_cfg.get("control_duration_ms", 450)),
            )
            motion_ack_timeout_s = max(
                0.15, min(0.80, float(stage_cfg.get("control_ack_timeout_s", 0.28)))
            )
        stage_yaw_speed_rps = math.radians(max(0.1, float(stage_cfg.get("yaw_speed_deg_s", 1.0))))
        stage_yaw_tolerance_rad = math.radians(max(0.05, float(stage_cfg.get("yaw_tolerance_deg", 0.30))))
        stage_yaw_window = max(3, min(15, int(stage_cfg.get("yaw_median_window", 7))))
        stage_yaw_history: deque[float] = deque(maxlen=stage_yaw_window)
        stage_straight_speed_mps = max(0.005, min(params.max_final_linear_mps, float(stage_cfg.get("straight_speed_mps", params.max_final_linear_mps))))
        stage_straight_stop_depth_m = max(0.20, float(stage_cfg.get("straight_stop_tag_depth_m", 0.50)))
        stage_straight_stop_tolerance_m = max(0.0, float(stage_cfg.get("straight_stop_tolerance_m", 0.03)))
        stage_straight_heading_kp = max(0.0, float(stage_cfg.get("straight_heading_kp", 0.80)))
        stage_straight_heading_deadband_rad = math.radians(max(0.0, float(stage_cfg.get("straight_heading_deadband_deg", 0.05))))
        stage_straight_heading_limit_rps = math.radians(max(0.05, float(stage_cfg.get("straight_heading_limit_deg_s", 0.35))))
        # A stage button can be pressed before RealSense/LiDAR/IMU have produced
        # a usable tag-axis pose.  This is not a recovery manoeuvre: keep the
        # robot stationary and give the sensors/tracker a finite acquisition
        # window before declaring the one-shot stage failed.
        stage_initial_pose_timeout_s = max(1.0, min(30.0, float(stage_cfg.get("initial_pose_timeout_s", 12.0))))
        # Once a stage has acquired a valid pose, a tiny RealSense/tag-axis
        # dropout must not terminate the one-shot test.  Keep the existing
        # API2010 watchdog command alive for a short grace window, then stop
        # in place and wait for the SAME pose to return.  This is deliberately
        # not a physical recovery manoeuvre: no reverse, re-approach or FOV
        # recovery is requested.
        stage_visual_command_hold_s = max(
            control_visual_grace_s,
            min(1.0, float(stage_cfg.get("visual_command_hold_s", 0.50))),
        )
        stage_visual_reacquire_timeout_s = max(
            stage_visual_command_hold_s + 0.10,
            min(10.0, float(stage_cfg.get("visual_reacquire_timeout_s", 3.0))),
        )
        stage_pose_wait_started_mono = time.monotonic()
        stage_pose_acquired_once = False
        stage_waiting_for_pose = bool(manual_stage)
        stage_pose_reacquiring = False
        stage_straight_anchor: tuple[float, float, float] | None = None
        stage_straight_heading_error_rad: float | None = None
        stage_straight_cross_track_m: float | None = None
        stage_straight_current_heading_rad: float | None = None
        stage_complete = False
        stage_complete_phase = ""
        stage_complete_message = ""

        while True:
            camera_frame = await asyncio.to_thread(source.read)
            frame = camera_frame.color_bgr
            now = float(camera_frame.timestamp_s)
            if not first_frame_seen:
                first_frame_seen = True
                publish(
                    "starting",
                    "CAMERA_STREAMING",
                    "First RealSense frame received; tracker/control loop is active",
                    frame_width=int(frame.shape[1]),
                    frame_height=int(frame.shape[0]),
                    control_probe_response=control_probe_response,
                )
            camera_window_frames += 1
            mono_now = time.monotonic()
            camera_window_elapsed = mono_now - camera_window_started
            if camera_window_elapsed >= 1.0:
                camera_loop_hz = camera_window_frames / max(camera_window_elapsed, 1e-6)
                camera_window_started = mono_now
                camera_window_frames = 0

            if preview_task is not None and preview_task.done():
                try:
                    preview_result = preview_task.result()
                    preview_seq += 1
                    preview_window_completed += 1
                    preview_last_complete_mono = float(preview_result.get("completed_monotonic", mono_now))
                    preview_last_duration_ms = float(preview_result.get("duration_ms", 0.0))
                except Exception:
                    preview_drop_count += 1
                preview_task = None
            preview_window_elapsed = mono_now - preview_window_started
            if preview_window_elapsed >= 1.0:
                preview_fps_actual = preview_window_completed / max(preview_window_elapsed, 1e-6)
                preview_window_started = mono_now
                preview_window_completed = 0
            if time.monotonic() - start_mono >= runtime_limit:
                final_phase = _phase_name(controller)
                final_message = f"runtime limit {runtime_limit:.1f}s reached"
                break
            dt = min(max(now - last_time, 0.01), 0.25)
            last_time = now
            last_v = float(getattr(vehicle, "_vx", last_v) or 0.0)
            last_w = float(getattr(vehicle, "_w", last_w) or 0.0)
            seer_map_pose, seer_localization_score = _seer_map_pose(vehicle)

            # Laser points are sampled asynchronously on the Adapter-owned STATE
            # connection.  The camera loop never waits for API 1009.
            if laser_task is not None and laser_task.done():
                try:
                    laser_result = laser_task.result()
                    laser_latency_ms = float(laser_result.get("latency_ms", 0.0))
                    laser_diagnostics = list(laser_result.get("diagnostics", []) or [])
                    raw_step_param = laser_result.get("laser_step_param")
                    try:
                        laser_step_param = None if raw_step_param is None else int(raw_step_param)
                    except (TypeError, ValueError):
                        laser_step_param = None
                    if bool(laser_result.get("ok", False)):
                        latest_laser_points = list(laser_result.get("points", []))
                        latest_laser_mono = mono_now
                        laser_last_error = ""
                        laser_source = str(laser_result.get("source", "-") or "-")
                        laser_schema = str(laser_result.get("schema", "-") or "-")
                        laser_attempts = list(laser_result.get("attempts", []) or [])
                        laser_poll_count += 1
                    else:
                        laser_last_error = str(laser_result.get("error", "laser request failed") or "laser request failed")
                        laser_source = str(laser_result.get("source", "-") or "-")
                        laser_schema = str(laser_result.get("schema", "-") or "-")
                        laser_attempts = list(laser_result.get("attempts", []) or [])
                        retry_after = float(laser_result.get("retry_after_s", lidar_failure_retry_s) or lidar_failure_retry_s)
                        laser_next_poll_mono = max(laser_next_poll_mono, mono_now + max(lidar_failure_retry_s, retry_after))
                except Exception as exc:
                    laser_last_error = f"{type(exc).__name__}: {exc}"
                laser_task = None
            if lidar_enabled and laser_task is None and mono_now >= laser_next_poll_mono:
                laser_task = asyncio.create_task(_request_laser_points(vehicle, step=lidar_step))
                laser_next_poll_mono = mono_now + lidar_poll_interval_s

            # IMU attitude (API 1014) is sampled independently from the camera
            # and LiDAR.  Its yaw zero is not assumed to equal the map zero; the
            # fusion uses relative yaw changes and SEER localization as the
            # absolute reference.
            if imu_task is not None and imu_task.done():
                try:
                    imu_result = imu_task.result()
                    imu_latency_ms = float(imu_result.get("latency_ms", 0.0))
                    imu_source = str(imu_result.get("source", "API1014") or "API1014")
                    imu_schema = str(imu_result.get("schema", "-") or "-")
                    if bool(imu_result.get("ok", False)) and isinstance(imu_result.get("attitude"), ImuAttitude):
                        latest_imu_attitude = imu_result["attitude"]
                        latest_imu_mono = mono_now
                        imu_last_error = ""
                        imu_poll_count += 1
                    else:
                        imu_last_error = str(imu_result.get("error", "IMU request failed") or "IMU request failed")
                        imu_next_poll_mono = max(imu_next_poll_mono, mono_now + imu_failure_retry_s)
                except Exception as exc:
                    imu_last_error = f"{type(exc).__name__}: {exc}"
                    imu_next_poll_mono = max(imu_next_poll_mono, mono_now + imu_failure_retry_s)
                imu_task = None
            if imu_enabled and imu_task is None and mono_now >= imu_next_poll_mono:
                imu_task = asyncio.create_task(_request_imu_attitude(vehicle))
                imu_next_poll_mono = mono_now + imu_poll_interval_s

            imu_age_s = None if latest_imu_mono is None else max(0.0, mono_now - latest_imu_mono)
            imu_fresh = bool(
                imu_enabled
                and latest_imu_attitude is not None
                and imu_age_s is not None
                and imu_age_s <= imu_max_age_s
            )
            fusion_map_pose = seer_map_pose
            imu_yaw_rate_rps = last_w
            imu_yaw_fusion_source = "SEER_W_FALLBACK"
            imu_yaw_diag = None
            if seer_map_pose is not None:
                imu_yaw_diag = imu_yaw_fusion.update(
                    seer_map_pose,
                    imu_yaw_rad=(latest_imu_attitude.yaw_rad if imu_fresh and latest_imu_attitude is not None else None),
                    imu_sample_mono=(latest_imu_mono if imu_fresh else None),
                    imu_fresh=imu_fresh,
                )
                fusion_map_pose = (seer_map_pose[0], seer_map_pose[1], imu_yaw_diag.fused_yaw_rad)
                if imu_fresh:
                    imu_yaw_fusion_source = str(imu_yaw_diag.source)
                    # API 1014 provides attitude, not a raw gyro rate.  Its yaw
                    # derivative is therefore blended with SEER's measured w.
                    # As an IMU sample ages between polls, progressively hand
                    # angular-rate authority back to SEER rather than holding a
                    # stale derivative for the whole IMU polling interval.
                    if bool(imu_yaw_diag.used_imu_delta) and imu_age_s is not None:
                        freshness = max(0.0, min(1.0, 1.0 - (imu_age_s / max(imu_max_age_s, 1e-6))))
                        imu_weight = imu_yaw_rate_weight * freshness
                        imu_yaw_rate_rps = (
                            imu_weight * float(imu_yaw_diag.yaw_rate_rps)
                            + (1.0 - imu_weight) * last_w
                        )
                        imu_yaw_fusion_source += "+SEER_W_BLEND"
                    else:
                        # First/re-anchored IMU sample has no valid derivative.
                        imu_yaw_rate_rps = last_w
                        imu_yaw_fusion_source += "+SEER_W_BOOTSTRAP"

            raw_detection = await asyncio.to_thread(detector.detect, frame)
            detection: Optional[TagDetection] = raw_detection
            note = ""
            tag_depth_m: Optional[float] = None
            reprojection_rejected = False
            depth_only_rejected = False
            if detection is not None and detection.reprojection_error_px > float(cfg.get("max_reprojection_error_px", 2.0)):
                note = f"reprojection rejected {detection.reprojection_error_px:.2f}px"
                reprojection_rejected = True
                detection = None
            if detection is not None and depth_enabled:
                depth_ok, tag_depth_m, depth_note = _validate_depth(detection, camera_frame.depth_m, depth_cfg)
                if not depth_ok:
                    # Do not permanently discard a geometrically good RGB tag
                    # here.  A valid LiDAR wall fit later in this frame supplies
                    # the metric distance/axis independently of RealSense depth.
                    note = depth_note
                    depth_only_rejected = True
                    detection = None
            if detection is not None:
                safety.mark_tag(now)
                if not detection.angle_reliable:
                    note = f"axis angle unreliable: {detection.angle_reliability_reason}"

            clearance_m = depth_clearance_in_roi(
                camera_frame.depth_m if depth_enabled else None,
                depth_cfg.get("obstacle_roi", [0.30, 0.70, 0.55, 0.95]),
                percentile=float(depth_cfg.get("obstacle_percentile", 5.0)),
                min_valid_m=float(depth_cfg.get("min_valid_m", 0.12)),
                max_valid_m=float(depth_cfg.get("max_valid_m", 6.0)),
            )
            # Build a metric wall-plane pose from aligned RealSense depth whenever
            # possible.  The fixed AprilTag gives a precise image-space centre;
            # the larger surrounding wall patch gives a much longer baseline for
            # yaw than the 40-mm square alone.
            rgbd_pose: RgbdWallPose | None = None
            if (
                raw_detection is not None
                and depth_enabled
                and bool(tracking_cfg.get("rgbd_wall_pose_enabled", True))
            ):
                rgbd_pose = estimate_rgbd_wall_pose(
                    camera_frame.depth_m,
                    raw_detection.corners_px,
                    source.camera_matrix,
                    t_b_c,
                    min_valid_m=float(depth_cfg.get("min_valid_m", 0.12)),
                    max_valid_m=float(depth_cfg.get("max_valid_m", 6.0)),
                    roi_scale=float(tracking_cfg.get("rgbd_wall_roi_scale", 4.0)),
                    min_roi_half_px=int(tracking_cfg.get("rgbd_wall_min_roi_half_px", 24)),
                    max_roi_half_px=int(tracking_cfg.get("rgbd_wall_max_roi_half_px", 180)),
                    sample_step_px=int(tracking_cfg.get("rgbd_wall_sample_step_px", 3)),
                    depth_band_m=float(tracking_cfg.get("rgbd_wall_depth_band_m", 0.06)),
                    depth_band_ratio=float(tracking_cfg.get("rgbd_wall_depth_band_ratio", 0.04)),
                    min_points=int(tracking_cfg.get("rgbd_wall_min_points", 80)),
                    min_inlier_ratio=float(tracking_cfg.get("rgbd_wall_min_inlier_ratio", 0.45)),
                    max_plane_rms_m=float(tracking_cfg.get("rgbd_wall_max_plane_rms_m", 0.010)),
                    min_horizontal_span_m=float(tracking_cfg.get("rgbd_wall_min_horizontal_span_m", 0.055)),
                )
                if rgbd_pose is not None and detection is not None and not detection.angle_reliable:
                    note = "PnP angle unreliable; RGB-D wall axis valid"

            # Depth-independent wall pose: SEER API 1009 laser points define the
            # wall line while the RGB tag centre defines the exact point on that
            # line.  This remains valid when RealSense depth jumps at close range.
            lidar_pose: LidarTagPose | None = None
            laser_age_s = None if latest_laser_mono is None else max(0.0, mono_now - latest_laser_mono)
            localization_ok = bool(
                seer_map_pose is not None
                and (seer_localization_score is None or seer_localization_score >= min_localization_score)
            )
            if (
                lidar_enabled
                and raw_detection is not None
                and localization_ok
                and latest_laser_points
                and laser_age_s is not None
                and laser_age_s <= lidar_max_age_s
            ):
                lidar_pose = estimate_lidar_tag_pose(
                    latest_laser_points,
                    robot_map_pose=seer_map_pose,
                    corners_px=raw_detection.corners_px,
                    camera_matrix=source.camera_matrix,
                    t_base_camera=t_b_c,
                    min_range_m=float(lidar_cfg.get("min_range_m", 0.12)),
                    max_range_m=float(lidar_cfg.get("max_range_m", 4.0)),
                    sector_half_angle_deg=float(lidar_cfg.get("sector_half_angle_deg", 38.0)),
                    sector_lateral_pad_m=float(lidar_cfg.get("sector_lateral_pad_m", 0.45)),
                    ransac_threshold_m=float(lidar_cfg.get("ransac_threshold_m", 0.018)),
                    ransac_iterations=int(lidar_cfg.get("ransac_iterations", 90)),
                    min_points=int(lidar_cfg.get("min_points", 18)),
                    min_inlier_ratio=float(lidar_cfg.get("min_inlier_ratio", 0.35)),
                    min_wall_span_m=float(lidar_cfg.get("min_wall_span_m", 0.22)),
                    max_wall_rms_m=float(lidar_cfg.get("max_wall_rms_m", 0.025)),
                    min_ray_wall_sin=float(lidar_cfg.get("min_ray_wall_sin", 0.18)),
                )

            if lidar_pose is not None and depth_only_rejected and not reprojection_rejected and raw_detection is not None:
                detection = raw_detection
                safety.mark_tag(now)
                note = (
                    f"{note}; LiDAR wall pose valid -> RealSense tag-depth mismatch ignored for motion pose"
                    if note else
                    "LiDAR wall pose valid -> RealSense tag-depth mismatch ignored for motion pose"
                )

            # Display values come from the best camera measurement even when
            # motion control rejects the frame.  RGB-D wall geometry is preferred
            # over PnP, while PnP remains visible as a diagnostic fallback.
            display_metrics = _camera_display_metrics(
                raw_detection,
                camera_frame.depth_m if depth_enabled else None,
                source=source,
                t_base_camera=t_b_c,
                t_marker_dock=t_m_d,
                cfg=cfg,
                depth_cfg=depth_cfg,
                rgbd_pose=rgbd_pose,
            )
            # Preserve depth-derived pose only as diagnostics.  LiDAR+RGB is the
            # preferred human-facing absolute pose when available.
            display_metrics["rgbd_axis_distance_m"] = (None if rgbd_pose is None else float(rgbd_pose.x_m))
            display_metrics["rgbd_axis_error_m"] = (None if rgbd_pose is None else float(rgbd_pose.y_m))
            display_metrics["rgbd_yaw_error_deg"] = (None if rgbd_pose is None else math.degrees(float(rgbd_pose.yaw_rad)))
            if lidar_pose is not None:
                display_metrics["axis_distance_m"] = float(lidar_pose.x_m)
                display_metrics["axis_error_m"] = float(lidar_pose.y_m)
                display_metrics["yaw_error_deg"] = math.degrees(float(lidar_pose.yaw_rad))
                display_metrics["pose_measurement_source"] = "LIDAR_WALL+RGB_CENTER"
                display_metrics["pose_measurement_confidence"] = float(lidar_pose.confidence)
                display_metrics["pose_position_sigma_m"] = max(0.003, float(lidar_pose.wall_rms_m) * 1.5)
                display_metrics["pose_yaw_sigma_deg"] = float(np.clip(math.degrees(math.atan2(max(lidar_pose.wall_rms_m, 1e-4), max(lidar_pose.wall_span_m, 1e-3))), 0.15, 3.0))
                display_metrics["lidar_wall_distance_m"] = float(lidar_pose.wall_distance_m)
                display_metrics["lidar_wall_rms_mm"] = 1000.0 * float(lidar_pose.wall_rms_m)
                display_metrics["lidar_wall_inlier_ratio"] = float(lidar_pose.inlier_ratio)
                display_metrics["lidar_wall_inliers"] = int(lidar_pose.inlier_count)
                display_metrics["lidar_wall_span_m"] = float(lidar_pose.wall_span_m)
                display_metrics["lidar_tag_ray_range_m"] = float(lidar_pose.tag_intersection_range_m)
            else:
                display_metrics["lidar_wall_distance_m"] = None
                display_metrics["lidar_wall_rms_mm"] = None
                display_metrics["lidar_wall_inlier_ratio"] = None
                display_metrics["lidar_wall_inliers"] = None
                display_metrics["lidar_wall_span_m"] = None
                display_metrics["lidar_tag_ray_range_m"] = None
            display_held = False
            display_age_ms: float | None = None
            if raw_detection is not None:
                # Preserve raw planar geometry for diagnostics, but present a
                # camera-only temporally stabilised pose to humans.  Planar PnP
                # yaw can jump from sub-pixel corner noise even when the AMR is
                # stationary, especially at close range.
                raw_axis_x = display_metrics.get("axis_distance_m")
                raw_axis_y = display_metrics.get("axis_error_m")
                raw_axis_yaw = display_metrics.get("yaw_error_deg")
                display_metrics["axis_distance_raw_m"] = raw_axis_x
                display_metrics["axis_error_raw_m"] = raw_axis_y
                display_metrics["yaw_error_raw_deg"] = raw_axis_yaw
                display_age_ms = 0.0
            elif (
                last_display_metrics is not None
                and last_display_seen_mono is not None
                and mono_now - last_display_seen_mono <= display_hold_s
            ):
                # Do not blink all geometry cells to '-' on one or two detector
                # misses.  The values are explicitly marked HOLD in the WebUI;
                # motion control never uses these held display-only samples.
                display_metrics = dict(last_display_metrics)
                display_held = True
                display_age_ms = 1000.0 * max(0.0, mono_now - last_display_seen_mono)
            measured: Optional[DockState] = None
            measurement_covariance: np.ndarray | None = None
            measurement_source = "-"
            measurement_confidence = 0.0
            coarse_axis_state: Optional[DockState] = None
            coarse_base_distance_m: Optional[float] = None
            pose_detection = raw_detection
            if pose_detection is not None:
                t_b_m = t_b_c @ pose_detection.t_camera_marker
                coarse_base_distance_m = float(np.linalg.norm(t_b_m[:2, 3]))

                # Pose priority deliberately avoids RealSense depth by default:
                # LiDAR wall + RGB tag centre -> RGB PnP -> optional RGB-D fallback.
                coarse_sample: tuple[float, float, float] | None = None
                if lidar_pose is not None:
                    coarse_sample = (float(lidar_pose.x_m), float(lidar_pose.y_m), float(lidar_pose.yaw_rad))
                elif pose_detection.marker_side_px >= coarse_axis_min_side_px:
                    try:
                        coarse_sample = base_pose_from_marker_center_axis(t_b_c, pose_detection.t_camera_marker)
                    except (ValueError, FloatingPointError):
                        coarse_sample = None
                elif use_rgbd_for_pose_fusion and rgbd_pose is not None:
                    coarse_sample = (float(rgbd_pose.x_m), float(rgbd_pose.y_m), float(rgbd_pose.yaw_rad))

                if coarse_sample is not None:
                    coarse_axis_history.append(coarse_sample)
                    if len(coarse_axis_history) >= 3:
                        samples = np.asarray(list(coarse_axis_history), dtype=float)
                        yaw_ref = float(samples[-1, 2])
                        yaw_unwrapped = np.asarray(
                            [yaw_ref + wrap_angle(float(v) - yaw_ref) for v in samples[:, 2]],
                            dtype=float,
                        )
                        coarse_axis_state = DockState(
                            float(np.median(samples[:, 0])),
                            float(np.median(samples[:, 1])),
                            wrap_angle(float(np.median(yaw_unwrapped))),
                            last_v,
                            imu_yaw_rate_rps,
                        )
                else:
                    coarse_axis_history.clear()

                if lidar_pose is not None:
                    measured = DockState(
                        float(lidar_pose.x_m), float(lidar_pose.y_m), float(lidar_pose.yaw_rad), last_v, imu_yaw_rate_rps
                    )
                    pos_sigma = max(0.004, 1.8 * float(lidar_pose.wall_rms_m))
                    yaw_sigma = math.radians(float(np.clip(
                        math.degrees(math.atan2(max(lidar_pose.wall_rms_m, 1e-4), max(lidar_pose.wall_span_m, 1e-3))),
                        0.18,
                        2.5,
                    )))
                    measurement_covariance = np.diag([pos_sigma**2, pos_sigma**2, yaw_sigma**2])
                    measurement_source = "LIDAR_WALL+RGB_CENTER"
                    measurement_confidence = float(lidar_pose.confidence)
                elif pose_detection.angle_reliable:
                    try:
                        x, y, yaw = base_pose_from_marker_center_axis(t_b_c, pose_detection.t_camera_marker)
                        measured = DockState(x, y, yaw, last_v, imu_yaw_rate_rps)
                        side = max(1.0, float(pose_detection.marker_side_px))
                        distance_scale = max(1.0, float(measured.x))
                        pos_sigma = max(0.004, 0.0040 * distance_scale)
                        yaw_sigma_deg = float(np.clip(100.0 / side, 0.45, 7.0))
                        measurement_covariance = np.diag(
                            [pos_sigma**2, pos_sigma**2, math.radians(yaw_sigma_deg) ** 2]
                        )
                        measurement_source = "PNP_RGB_ONLY"
                        measurement_confidence = float(np.clip(side / 90.0, 0.18, 0.78))
                    except (ValueError, FloatingPointError):
                        measured = None
                elif use_rgbd_for_pose_fusion and rgbd_pose is not None:
                    measured = DockState(
                        float(rgbd_pose.x_m), float(rgbd_pose.y_m), float(rgbd_pose.yaw_rad), last_v, imu_yaw_rate_rps
                    )
                    pos_sigma = max(0.008, float(rgbd_pose.position_sigma_m) * 1.8)
                    yaw_sigma = max(math.radians(0.7), float(rgbd_pose.yaw_sigma_rad) * 1.8)
                    measurement_covariance = np.diag([pos_sigma**2, pos_sigma**2, yaw_sigma**2])
                    measurement_source = "RGBD_OPTIONAL_FALLBACK"
                    measurement_confidence = 0.45 * float(rgbd_pose.confidence)

                # Legacy EKF remains as a fallback when SEER localization cannot
                # be trusted/anchored.  It uses SEER vx/w for temporal prediction.
                if measured is not None and measurement_covariance is not None:
                    if estimator is None or not estimator_pose_reliable:
                        estimator = PlanarDockEKF(measured)
                        estimator_pose_reliable = True
                    else:
                        estimator.predict(last_v, imu_yaw_rate_rps, dt)
                        accepted = estimator.update_tag(
                            measured,
                            measurement_covariance,
                            gate_sigma=4.8 if measurement_source == "LIDAR_WALL+RGB_CENTER" else 4.0,
                        )
                        if not accepted:
                            note = f"{measurement_source} EKF innovation rejected"
                elif estimator is not None and estimator_pose_reliable:
                    estimator.predict(last_v, imu_yaw_rate_rps, dt)
            elif estimator is not None and estimator_pose_reliable:
                coarse_axis_history.clear()
                estimator.predict(last_v, imu_yaw_rate_rps, dt)
            else:
                coarse_axis_history.clear()

            # Establish one immutable dock axis in the SEER map frame from a
            # short stationary window of LiDAR-wall + RGB-tag-centre samples.
            # After this lock, camera/LiDAR measurements are diagnostics only:
            # centreline/yaw are propagated by SEER x/y plus relative IMU yaw.
            if (
                world_axis_lock_enabled
                and not world_axis_locked
                and localization_ok
                and fusion_map_pose is not None
                and measured is not None
                and measurement_source == "LIDAR_WALL+RGB_CENTER"
            ):
                candidate_anchor = SeerTagRelativeFusion.anchor_pose_from_measurement(
                    fusion_map_pose, measured
                )
                world_axis_lock_candidates.append(candidate_anchor)
                stable_anchor = _stable_world_axis_anchor(
                    world_axis_lock_candidates,
                    required_samples=world_axis_lock_required_samples,
                    max_position_spread_m=world_axis_lock_max_position_spread_m,
                    max_yaw_spread_rad=world_axis_lock_max_yaw_spread_rad,
                )
                if stable_anchor is not None:
                    tag_localization_fusion.restore_anchor_pose(stable_anchor, frozen=True)
                    world_axis_locked = True
                    world_axis_lock_sample_count = world_axis_lock_required_samples
                    world_axis_lock_source = (
                        f"LIDAR_WALL+RGB_CENTER median({world_axis_lock_required_samples}) + SEER/IMU"
                    )
                    world_axis_lock_saved_at = time.time()
                    _save_world_axis_lock(
                        world_axis_lock_file,
                        anchor_pose=stable_anchor,
                        robot_ip=str(getattr(vehicle, "robot_ip", "") or ""),
                        source=world_axis_lock_source,
                        sample_count=world_axis_lock_required_samples,
                    )
                    note = (
                        f"{note}; WORLD AXIS LOCKED" if note else "WORLD AXIS LOCKED"
                    )

            ekf_fused = estimator.state(last_v, imu_yaw_rate_rps) if estimator is not None and estimator_pose_reliable else None
            localization_fused: DockState | None = None
            localization_diag = None
            if localization_ok and fusion_map_pose is not None:
                localization_fused, localization_diag = tag_localization_fusion.update(
                    fusion_map_pose,
                    measured,
                    measurement_confidence=measurement_confidence,
                    measurement_source=measurement_source,
                    v=last_v,
                    w=imu_yaw_rate_rps,
                )
            fused = localization_fused if localization_fused is not None else ekf_fused

            # The Top View and human-facing table should show the same fused pose
            # that the controller actually reasons about.  Keep raw camera/PnP
            # diagnostics in their own fields, but do not draw the AMR from a
            # noisier one-frame pose when a fused tag-relative estimate exists.
            if fused is not None:
                if display_metrics.get("axis_distance_m") is not None:
                    display_metrics["axis_distance_measurement_m"] = display_metrics.get("axis_distance_m")
                    display_metrics["axis_error_measurement_m"] = display_metrics.get("axis_error_m")
                    display_metrics["yaw_error_measurement_deg"] = display_metrics.get("yaw_error_deg")
                display_metrics["axis_distance_m"] = float(fused.x)
                display_metrics["axis_error_m"] = float(fused.y)
                display_metrics["yaw_error_deg"] = math.degrees(float(fused.yaw))
                if localization_fused is not None and localization_diag is not None:
                    display_metrics["display_pose_source"] = str(localization_diag.source)
                    display_metrics["fusion_innovation_position_m"] = localization_diag.innovation_position_m
                    display_metrics["fusion_innovation_yaw_deg"] = localization_diag.innovation_yaw_deg
                    display_metrics["fusion_correction_accepted"] = bool(localization_diag.accepted_correction)
                elif lidar_pose is not None:
                    display_metrics["display_pose_source"] = "EKF LIDAR+RGB + SEER v + IMU yaw"
                elif measurement_source == "PNP_RGB_ONLY":
                    display_metrics["display_pose_source"] = "EKF RGB PNP + SEER v + IMU yaw"
                elif use_rgbd_for_pose_fusion and rgbd_pose is not None:
                    display_metrics["display_pose_source"] = "EKF RGBD FALLBACK + SEER v + IMU yaw"
                else:
                    display_metrics["display_pose_source"] = "SEER ODOM PREDICT / HOLD"

            # IMPORTANT: apply the human-facing filter *after* the final fused
            # pose is selected.  Older builds filtered the camera pose first and
            # then overwrote it with EKF/SEER/IMU, so Top View still jittered.
            final_display_x = display_metrics.get("axis_distance_m")
            final_display_y = display_metrics.get("axis_error_m")
            final_display_yaw = display_metrics.get("yaw_error_deg")
            if None not in (final_display_x, final_display_y, final_display_yaw):
                display_moving = abs(last_v) > 0.008 or abs(imu_yaw_rate_rps) > 0.012
                stable_x, stable_y, stable_yaw = display_pose_filter.update(
                    float(final_display_x),
                    float(final_display_y),
                    float(final_display_yaw),
                    moving=display_moving,
                )
                display_metrics["axis_distance_m"] = stable_x
                display_metrics["axis_error_m"] = stable_y
                display_metrics["yaw_error_deg"] = stable_yaw
                if raw_detection is not None or fused is not None:
                    last_display_metrics = dict(display_metrics)
                    last_display_seen_mono = mono_now
                    display_age_ms = 0.0
            elif raw_detection is None and fused is None and not display_held:
                display_pose_filter.reset()

            visual: Optional[VisualDockObservation] = None
            current_fov_margin = float("-inf")
            if detection is not None:
                current_fov_margin = image_margin_px(detection.corners_px, frame.shape[1], frame.shape[0])
                center = np.mean(detection.corners_px, axis=0)
                control_distance = fused.x if fused is not None else (coarse_axis_state.x if coarse_axis_state is not None else float(coarse_base_distance_m or detection.t_camera_marker[2, 3]))
                try:
                    target_visual = visual_target_for_base_distance(t_b_c, t_m_d, source.camera_matrix, control_distance)
                    target_du_px = target_visual.du_px
                    target_pitch_rad = target_visual.pitch_rad
                except ValueError:
                    target_du_px = 0.0
                    target_pitch_rad = 0.0
                visual = VisualDockObservation(
                    base_distance_m=control_distance,
                    goal_distance_m=params.goal_standoff_m,
                    du_px=float(center[0] - source.camera_matrix[0, 2]),
                    target_du_px=target_du_px,
                    marker_pitch_rad=math.radians(detection.marker_pitch_deg),
                    target_pitch_rad=target_pitch_rad,
                    fx_px=float(source.camera_matrix[0, 0]),
                    tag_depth_m=float(display_metrics.get("tag_depth_m")) if display_metrics.get("tag_depth_m") is not None else (float(rgbd_pose.tag_center_camera_m[2]) if rgbd_pose is not None else float(detection.t_camera_marker[2, 3])),
                    v=fused.v if fused is not None else last_v,
                    omega=fused.omega if fused is not None else imu_yaw_rate_rps,
                    visible=True,
                    angle_reliable=bool(lidar_pose is not None or rgbd_pose is not None or detection.angle_reliable),
                    fov_margin_normalized=float(np.clip(2.0 * current_fov_margin / max(1.0, 0.5 * min(frame.shape[:2])) - 1.0, -1.0, 1.0)),
                    fused_lateral_m=fused.y if fused is not None else None,
                    fused_yaw_rad=fused.yaw if fused is not None else None,
                    coarse_lateral_m=coarse_axis_state.y if coarse_axis_state is not None else None,
                    coarse_yaw_rad=coarse_axis_state.yaw if coarse_axis_state is not None else None,
                )

            target_cmd = VelocityCommand(0.0, 0.0)
            sent_cmd = VelocityCommand(0.0, 0.0)
            if visual is not None:
                no_visual_stop_sent = False
                last_visual_valid_mono = time.monotonic()
                if manual_stage:
                    stage_pose_acquired_once = True
                    stage_waiting_for_pose = False
                    stage_pose_reacquiring = False
                if world_axis_lock_enabled and not world_axis_locked:
                    # Do not move until the physical tag normal has been
                    # converted to one stable map-frame axis.  This prevents
                    # camera/LiDAR frame-to-frame noise from moving the target
                    # while the AMR is already correcting toward it.
                    requested = VelocityCommand(0.0, 0.0)
                    lock_wait_s = max(0.0, time.monotonic() - stage_pose_wait_started_mono)
                    note = (
                        f"WORLD AXIS acquiring {len(world_axis_lock_candidates)}/"
                        f"{world_axis_lock_required_samples} LiDAR samples; robot held stationary"
                    )
                    if manual_stage and lock_wait_s > stage_initial_pose_timeout_s:
                        raise RuntimeError(
                            f"manual stage could not lock LiDAR world axis within "
                            f"{stage_initial_pose_timeout_s:.1f}s; stopped with automatic recovery disabled"
                        )
                elif manual_stage == "yaw":
                    # Stage 2: rotate in place only.  Do not call the normal
                    # controller because it may decide to re-centre or recover.
                    controller.phase = DockPhase.YAW_ALIGN
                    if visual.fused_yaw_rad is None:
                        requested = VelocityCommand(0.0, 0.0)
                        note = f"{note}; waiting for fused tag-axis yaw" if note else "waiting for fused tag-axis yaw"
                    else:
                        stage_yaw_history.append(float(visual.fused_yaw_rad))
                        yaw_sample = float(np.median(np.asarray(stage_yaw_history, dtype=float)))
                        requested, yaw_done = _manual_yaw_only_command(
                            yaw_sample,
                            speed_rps=stage_yaw_speed_rps,
                            tolerance_rad=stage_yaw_tolerance_rad,
                        )
                        if len(stage_yaw_history) >= 3 and yaw_done:
                            stage_complete = True
                            stage_complete_phase = "STAGE_YAW_DONE"
                            stage_complete_message = (
                                f"yaw-only complete: {math.degrees(yaw_sample):+.3f} deg; "
                                "translation was disabled and no recovery motion was used"
                            )
                        else:
                            note = (
                                f"manual yaw-only {math.degrees(yaw_sample):+.3f} deg -> "
                                f"{math.degrees(requested.omega):+.2f} deg/s"
                            )
                elif manual_stage == "straight":
                    # Stage 3: camera is range/visibility only.  Heading comes
                    # exclusively from the SEER map pose corrected by relative
                    # IMU yaw.  The starting heading is latched once and a very
                    # small w correction counters wheel/floor asymmetry.
                    controller.phase = DockPhase.STRAIGHT_APPROACH
                    if fusion_map_pose is None:
                        requested = VelocityCommand(0.0, 0.0)
                        note = f"{note}; waiting for SEER+IMU heading" if note else "waiting for SEER+IMU heading"
                    else:
                        current_x = float(fusion_map_pose[0])
                        current_y = float(fusion_map_pose[1])
                        current_heading = float(fusion_map_pose[2])
                        stage_straight_current_heading_rad = current_heading
                        if stage_straight_anchor is None:
                            stage_straight_anchor = (current_x, current_y, current_heading)
                        target_heading = float(stage_straight_anchor[2])
                        requested, stage_straight_heading_error_rad, stage_straight_cross_track_m = _manual_straight_heading_hold(
                            stage_straight_anchor,
                            (current_x, current_y, current_heading),
                            speed_mps=stage_straight_speed_mps,
                            kp=stage_straight_heading_kp,
                            deadband_rad=stage_straight_heading_deadband_rad,
                            limit_rps=stage_straight_heading_limit_rps,
                        )
                        stop_depth = stage_straight_stop_depth_m + stage_straight_stop_tolerance_m
                        if visual.tag_depth_m <= stop_depth:
                            requested = VelocityCommand(0.0, 0.0)
                            stage_complete = True
                            stage_complete_phase = "STAGE_STRAIGHT_DONE"
                            stage_complete_message = (
                                f"straight-only complete at tag_depth={visual.tag_depth_m:.3f} m; "
                                f"heading error={math.degrees(stage_straight_heading_error_rad):+.3f} deg, "
                                f"cross-track={stage_straight_cross_track_m*1000.0:+.1f} mm"
                            )
                        else:
                            note = (
                                f"manual straight IMU+SEER hold: heading error "
                                f"{math.degrees(stage_straight_heading_error_rad):+.3f} deg, "
                                f"cross-track {stage_straight_cross_track_m*1000.0:+.1f} mm; "
                                "camera steering OFF"
                            )
                else:
                    requested = controller.command(visual)
                    if manual_stage == "centerline":
                        if getattr(controller, "phase", None) in (DockPhase.RECOVERY_ALIGN, DockPhase.RECOVERY_REVERSE, DockPhase.ABORTED):
                            raise RuntimeError("centerline stage requested recovery/abort; stopped because stage recovery is disabled")
                        if getattr(controller, "phase", None) == DockPhase.YAW_ALIGN:
                            requested = VelocityCommand(0.0, 0.0)
                            stage_complete = True
                            stage_complete_phase = "STAGE_CENTERLINE_DONE"
                            lateral_now = visual.fused_lateral_m
                            stage_complete_message = (
                                "centerline-only complete: rotation centre reached tag normal axis"
                                + ("" if lateral_now is None else f" ({float(lateral_now)*1000.0:+.2f} mm)")
                                + "; yaw alignment was not executed"
                            )
                if detection is not None and manual_stage not in {"yaw", "straight"}:
                    straight_recovery = bool(getattr(controller, "phase", None) == DockPhase.RECOVERY_REVERSE and requested.v < 0.0)
                    if not straight_recovery:
                        fov_result = fov_guard.filter_detection(
                            requested,
                            detection.t_camera_marker,
                            float(cfg["tag_size_m"]),
                            t_b_c,
                            source.camera_matrix,
                            (frame.shape[1], frame.shape[0]),
                            visual.v,
                            visual.omega,
                            params.max_linear_mps,
                            params.max_angular_rps,
                            params.max_linear_accel_mps2,
                            params.max_angular_accel_rps2,
                        )
                        before_fov = requested
                        requested = fov_result.command
                        requested_mag = abs(before_fov.v) / max(params.max_linear_mps, 1e-6) + abs(before_fov.omega) / max(params.max_angular_rps, 1e-6)
                        guarded_mag = abs(requested.v) / max(params.max_linear_mps, 1e-6) + abs(requested.omega) / max(params.max_angular_rps, 1e-6)
                        if bool((not manual_stage) and fov_result.limited and requested_mag > 0.08 and guarded_mag < 0.03 and getattr(controller, "phase", None) not in (DockPhase.RECOVERY_ALIGN, DockPhase.RECOVERY_REVERSE, DockPhase.DOCKED, DockPhase.ABORTED) and hasattr(controller, "request_visibility_recovery")):
                            requested = controller.request_visibility_recovery(visual)
                            note = f"{note}; FOV stall -> reverse recovery" if note else "FOV stall -> reverse recovery"
                        elif fov_result.limited:
                            note = f"{note}; {fov_result.reason}" if note else fov_result.reason
                safety_state = fused or DockState(visual.base_distance_m, 0.0, 0.0, visual.v, visual.omega)
                safe_vehicle, safe_reason = _vehicle_safety(vehicle, stale_after_s=status_timeout_s)
                block_now = bool(getattr(vehicle, "_blocked", False))
                if block_now:
                    if blocked_since_mono is None:
                        blocked_since_mono = time.monotonic()
                        blocked_event_count += 1
                else:
                    blocked_since_mono = None
                safe_command = safety.filter(requested, safety_state, lidar_clearance_m=clearance_m, emergency_stop=(live and not safe_vehicle), now=now)
                target_cmd = safe_command
                tag_timed_out = now - safety.last_tag_time > safety.tag_timeout_s
                obstacle_stop = bool(math.isfinite(clearance_m) and clearance_m < safety.obstacle_stop_m and requested.v > 0.0)
                hard_stop = bool(live and (not safe_vehicle or tag_timed_out or obstacle_stop))
                if hard_stop:
                    entering_hard_stop = not hard_stop_latched
                    hard_stop_latched = True
                    hard_stop_clear_since = None
                    control_recovery_next_probe_mono = 0.0
                    consecutive_motion_ack_timeouts = 0
                    next_motion_tx_mono = 0.0
                    last_live_sent_cmd = VelocityCommand(0.0, 0.0)
                    motion_tx_deferred = False
                    smoother.reset()
                    sent_cmd = VelocityCommand(0.0, 0.0)
                    stop_reason = safe_reason if not safe_vehicle else ('tag timeout' if tag_timed_out else 'depth obstacle')
                    note = f"{note}; HARD STOP {stop_reason}" if note else f"HARD STOP {stop_reason}"
                    # blocked가 유지되는 동안 카메라 주기마다 API 2000을 반복하면
                    # CONTROL 포트가 자체 정지 응답으로 포화될 수 있다. 첫 진입에서
                    # 한 번만 정지하고 이후에는 0속도 latch를 유지한다.
                    if entering_hard_stop:
                        stop_result = await _safe_stop(vehicle)
                        if "failed" in stop_result.lower() or "timeout" in stop_result.lower():
                            control_recovery_last_error = stop_result
                    if manual_stage:
                        raise RuntimeError(
                            f"manual stage stopped by safety ({stop_reason}); automatic recovery is disabled"
                        )
                elif live and hard_stop_latched:
                    safe_vehicle, _ = _vehicle_safety(vehicle, stale_after_s=status_timeout_s)
                    stationary = safe_vehicle and abs(float(getattr(vehicle, "_vx", 0.0) or 0.0)) <= 0.01 and abs(float(getattr(vehicle, "_w", 0.0) or 0.0)) <= 0.02
                    if stationary:
                        hard_stop_clear_since = hard_stop_clear_since or time.monotonic()
                    else:
                        hard_stop_clear_since = None
                    clear_for = 0.0 if hard_stop_clear_since is None else time.monotonic() - hard_stop_clear_since
                    sent_cmd = VelocityCommand(0.0, 0.0)
                    if clear_for >= clear_hold_s:
                        probe_now = time.monotonic()
                        if probe_now >= control_recovery_next_probe_mono:
                            probe_ok, probe_detail, probe_response = await _zero_control_probe(
                                vehicle, timeout_s=control_recovery_probe_timeout_s
                            )
                            control_recovery_next_probe_mono = probe_now + control_recovery_probe_interval_s
                            if probe_ok:
                                hard_stop_latched = False
                                hard_stop_clear_since = None
                                control_recovery_error_count = 0
                                control_recovery_last_error = ""
                                consecutive_motion_ack_timeouts = 0
                                last_live_sent_cmd = VelocityCommand(0.0, 0.0)
                                next_motion_tx_mono = 0.0
                                motion_tx_deferred = False
                                last_motion_response = probe_response
                                last_motion_error = ""
                                smoother.reset()
                                note = f"{note}; safety clear, {probe_detail}" if note else f"safety clear, {probe_detail}"
                            else:
                                control_recovery_error_count += 1
                                control_recovery_last_error = probe_detail
                                last_motion_error = probe_detail
                                note = f"{note}; safety clear but {probe_detail}" if note else f"safety clear but {probe_detail}"
                                if control_recovery_error_count >= max_control_recovery_errors:
                                    raise RuntimeError(
                                        f"CONTROL recovery failed {control_recovery_error_count} times after safety stop: {probe_detail}"
                                    )
                        else:
                            note = f"{note}; safety clear, CONTROL recovery probe waiting" if note else "safety clear, CONTROL recovery probe waiting"
                    else:
                        note = f"{note}; safety clear hold {clear_for:.2f}/{clear_hold_s:.2f}s" if note else f"safety clear hold {clear_for:.2f}/{clear_hold_s:.2f}s"
                if not hard_stop_latched:
                    smoothed_cmd = smoother.apply(safe_command, dt)
                    sent_cmd = smoothed_cmd
                    motion_tx_deferred = False
                    if live:
                        tx_now = time.monotonic()
                        cmd_zero = (
                            abs(smoothed_cmd.v) <= smoother.linear_zero_epsilon_mps
                            and abs(smoothed_cmd.omega) <= smoother.angular_zero_epsilon_rps
                        )
                        last_cmd_zero = (
                            abs(last_live_sent_cmd.v) <= smoother.linear_zero_epsilon_mps
                            and abs(last_live_sent_cmd.omega) <= smoother.angular_zero_epsilon_rps
                        )
                        # 2026-08-27 실물 성공 로그에서 약 10 Hz API2010 전송 중 단발
                        # ACK timeout이 13회 발생해 총 17.9초가 recovery 정지로 소비됐다.
                        # 자동 도킹은 기본 5 Hz, 단계 시험은 실시간성을 위해 10 Hz를
                        # 사용한다. ACK 유실은 아래 STATE 확인으로 흡수하며, 정지 전환은
                        # 스케줄을 기다리지 않고 즉시 보내 overshoot를 막는다.
                        should_send_motion = bool(
                            tx_now >= next_motion_tx_mono or (cmd_zero and not last_cmd_zero)
                        )
                        if should_send_motion:
                            try:
                                last_motion_response = await asyncio.wait_for(
                                    vehicle.control.drive_native(
                                        vx_mps=smoothed_cmd.v,
                                        vy_mps=0.0,
                                        w_rad_s=smoothed_cmd.omega,
                                        duration_ms=motion_duration_ms,
                                    ),
                                    timeout=motion_ack_timeout_s,
                                )
                                last_live_sent_cmd = smoothed_cmd
                                sent_cmd = smoothed_cmd
                                next_motion_tx_mono = time.monotonic() + motion_tx_interval_s
                                consecutive_motion_ack_timeouts = 0
                                last_motion_error = ""
                                control_recovery_error_count = 0
                                control_recovery_last_error = ""
                                last_control_transport_ok_mono = time.monotonic()
                            except TimeoutError as exc:
                                # API2010은 duration dead-man을 포함하고 request()가 timeout
                                # 시 CONTROL 소켓을 폐기한다. 단발 ACK 유실에 API2000을
                                # 추가로 보내면 실제 장비가 매번 '뚝' 멈췄다. 첫 timeout은
                                # 450ms dead-man 안에서 다음 최신 명령으로 소켓만 재개하고,
                                # 연속 timeout일 때만 fail-closed 정지한다.
                                consecutive_motion_ack_timeouts += 1
                                control_recovery_error_count = consecutive_motion_ack_timeouts
                                last_motion_error = f"{type(exc).__name__}: {exc}"
                                control_recovery_last_error = last_motion_error
                                last_live_sent_cmd = smoothed_cmd
                                sent_cmd = smoothed_cmd
                                next_motion_tx_mono = 0.0
                                if manual_stage:
                                    # CONTROL 응답만 잃은 경우 STATE 속도가 명령과 일치하면
                                    # 로봇이 명령을 실제 수신한 것으로 본다. STATE 안전 판정은
                                    # 이 블록보다 앞에서 매 loop 수행되므로 stale/blocked/emergency를
                                    # 숨기지 않는다.
                                    state_confirmed, actual_v, actual_w = _motion_command_observed_in_state(
                                        vehicle, smoothed_cmd
                                    )
                                    if state_confirmed:
                                        last_control_transport_ok_mono = time.monotonic()
                                        manual_state_confirmed_count += 1
                                    transport_gap_s = max(0.0, time.monotonic() - last_control_transport_ok_mono)
                                    next_motion_tx_mono = time.monotonic() + motion_tx_interval_s
                                    if transport_gap_s < manual_control_loss_grace_s:
                                        detail = (
                                            f"CONTROL ACK timeout x{consecutive_motion_ack_timeouts} tolerated; "
                                            f"transport_gap={transport_gap_s:.2f}/{manual_control_loss_grace_s:.2f}s; "
                                            f"STATE confirm={'yes' if state_confirmed else 'no'} "
                                            f"vx={actual_v:.3f} w={actual_w:.4f}"
                                        )
                                        note = f"{note}; {detail}" if note else detail
                                    else:
                                        stop_result = await _safe_stop(vehicle)
                                        raise RuntimeError(
                                            f"manual stage CONTROL transport unavailable for {transport_gap_s:.2f}s; "
                                            f"stopped with no motion recovery: {last_motion_error}; {stop_result}"
                                        )
                                elif consecutive_motion_ack_timeouts < max_consecutive_motion_ack_timeouts:
                                    note = (
                                        f"{note}; CONTROL ACK timeout tolerated "
                                        f"{consecutive_motion_ack_timeouts}/{max_consecutive_motion_ack_timeouts}; "
                                        "dead-man protected"
                                    ) if note else (
                                        f"CONTROL ACK timeout tolerated "
                                        f"{consecutive_motion_ack_timeouts}/{max_consecutive_motion_ack_timeouts}; "
                                        "dead-man protected"
                                    )
                                else:
                                    hard_stop_latched = True
                                    hard_stop_clear_since = None
                                    control_recovery_next_probe_mono = time.monotonic() + control_recovery_probe_interval_s
                                    next_motion_tx_mono = 0.0
                                    last_live_sent_cmd = VelocityCommand(0.0, 0.0)
                                    motion_tx_deferred = False
                                    smoother.reset()
                                    sent_cmd = VelocityCommand(0.0, 0.0)
                                    stop_result = await _safe_stop(vehicle)
                                    note = (
                                        f"{note}; CONTROL ACK timeout x{consecutive_motion_ack_timeouts} "
                                        f"-> recovery latch: {last_motion_error}; {stop_result}"
                                    ) if note else (
                                        f"CONTROL ACK timeout x{consecutive_motion_ack_timeouts} "
                                        f"-> recovery latch: {last_motion_error}; {stop_result}"
                                    )
                            except Exception as exc:
                                last_motion_error = f"{type(exc).__name__}: {exc}"
                                control_recovery_last_error = last_motion_error
                                control_recovery_error_count += 1
                                consecutive_motion_ack_timeouts = 0
                                if manual_stage and isinstance(exc, (ConnectionError, OSError)):
                                    # Wi-Fi 순간 단절은 TimeoutError 외에 socket OSError로도
                                    # 나타난다. 단계 시험에서는 후퇴/재접근은 하지 않되,
                                    # 짧은 transport gap 동안 최신 명령 재전송만 허용한다.
                                    # 명시적 SEER ret_code/프로토콜 오류(RuntimeError)는
                                    # 여기에 포함하지 않고 아래 fail-closed 경로를 탄다.
                                    transport_gap_s = max(0.0, time.monotonic() - last_control_transport_ok_mono)
                                    next_motion_tx_mono = time.monotonic() + motion_tx_interval_s
                                    last_live_sent_cmd = smoothed_cmd
                                    sent_cmd = smoothed_cmd
                                    motion_tx_deferred = False
                                    if transport_gap_s < manual_control_loss_grace_s:
                                        detail = (
                                            f"CONTROL transport {type(exc).__name__} tolerated; "
                                            f"gap={transport_gap_s:.2f}/{manual_control_loss_grace_s:.2f}s"
                                        )
                                        note = f"{note}; {detail}" if note else detail
                                    else:
                                        stop_result = await _safe_stop(vehicle)
                                        smoother.reset()
                                        sent_cmd = VelocityCommand(0.0, 0.0)
                                        last_live_sent_cmd = VelocityCommand(0.0, 0.0)
                                        raise RuntimeError(
                                            f"manual stage CONTROL transport unavailable for {transport_gap_s:.2f}s; "
                                            f"stopped with no motion recovery: {last_motion_error}; {stop_result}"
                                        )
                                else:
                                    # 실제 API 오류나 자동 도킹의 transport 오류는 기존처럼
                                    # 즉시 fail-closed 정지/복구 latch를 사용한다.
                                    hard_stop_latched = True
                                    hard_stop_clear_since = None
                                    control_recovery_next_probe_mono = time.monotonic() + control_recovery_probe_interval_s
                                    next_motion_tx_mono = 0.0
                                    last_live_sent_cmd = VelocityCommand(0.0, 0.0)
                                    motion_tx_deferred = False
                                    smoother.reset()
                                    sent_cmd = VelocityCommand(0.0, 0.0)
                                    stop_result = await _safe_stop(vehicle)
                                    note = f"{note}; CONTROL motion error -> recovery latch: {last_motion_error}; {stop_result}" if note else f"CONTROL motion error -> recovery latch: {last_motion_error}; {stop_result}"
                                    if manual_stage:
                                        raise RuntimeError(
                                            f"manual stage CONTROL motion error; stopped with no recovery: {last_motion_error}"
                                        )
                                    if control_recovery_error_count >= max_control_recovery_errors:
                                        raise RuntimeError(
                                            f"CONTROL motion/recovery failed {control_recovery_error_count} times: {last_motion_error}"
                                        )
                        else:
                            # 카메라/추정기는 계속 고속으로 갱신하되 SEER에는 마지막
                            # 명령을 유지시킨다. 로봇이 실제로 받고 있는 값 기준으로
                            # telemetry를 남겨 시각화와 실물 동작이 어긋나지 않게 한다.
                            sent_cmd = last_live_sent_cmd
                            motion_tx_deferred = True
            else:
                # 실물 로그에서 한두 frame의 depth/PnP mismatch마다 API2000을
                # 보내며 회전이 뚝뚝 끊겼다. 0.20초 이내의 짧은 visual miss는
                # 새 non-zero 명령을 보내지 않고 기존 API2010 duration dead-man이
                # 자연 만료되도록 둔다. 지속 miss만 한 번 정지시킨다.
                visual_gap_s = float("inf") if last_visual_valid_mono is None else max(0.0, time.monotonic() - last_visual_valid_mono)

                # Manual stage startup is special: before the first valid pose
                # exists, ``visual_gap_s`` is infinite.  The previous build
                # interpreted that as an immediate loss and failed on the very
                # first camera frame.  Wait stationary for initial acquisition;
                # once a pose has been acquired at least once, any later loss
                # still follows the strict no-recovery stage policy below.
                if manual_stage and not stage_pose_acquired_once:
                    stage_waiting_for_pose = True
                    acquire_elapsed_s = max(0.0, time.monotonic() - stage_pose_wait_started_mono)
                    smoother.reset()
                    sent_cmd = VelocityCommand(0.0, 0.0)
                    last_live_sent_cmd = VelocityCommand(0.0, 0.0)
                    motion_tx_deferred = False
                    note = (
                        f"waiting for initial tag/axis pose "
                        f"{acquire_elapsed_s:.1f}/{stage_initial_pose_timeout_s:.1f}s; robot held stationary"
                    )
                    if acquire_elapsed_s > stage_initial_pose_timeout_s:
                        if live and not no_visual_stop_sent:
                            await _safe_stop(vehicle)
                            no_visual_stop_sent = True
                        raise RuntimeError(
                            f"manual stage could not acquire a valid tag/axis pose within "
                            f"{stage_initial_pose_timeout_s:.1f}s; stopped with automatic recovery disabled"
                        )
                elif manual_stage and stage_pose_acquired_once:
                    if visual_gap_s <= stage_visual_command_hold_s:
                        # Short camera/tag-axis dropout: do not inject a stop
                        # pulse.  No new motion packet is sent; the last API2010
                        # command simply remains active under its short dead-man
                        # duration while the tracker gets the next good frame.
                        stage_pose_reacquiring = False
                        sent_cmd = last_live_sent_cmd
                        target_cmd = last_live_sent_cmd
                        motion_tx_deferred = True
                        note = (
                            f"manual stage transient pose gap {visual_gap_s:.2f}/"
                            f"{stage_visual_command_hold_s:.2f}s; holding last dead-man command"
                        )
                    else:
                        # Longer loss: stop once and wait stationary for pose
                        # reacquisition.  This is NOT automatic motion recovery.
                        stage_pose_reacquiring = True
                        smoother.reset()
                        sent_cmd = VelocityCommand(0.0, 0.0)
                        target_cmd = VelocityCommand(0.0, 0.0)
                        if live and not no_visual_stop_sent:
                            stop_result = await _safe_stop(vehicle)
                            last_live_sent_cmd = VelocityCommand(0.0, 0.0)
                            next_motion_tx_mono = 0.0
                            consecutive_motion_ack_timeouts = 0
                            motion_tx_deferred = False
                            no_visual_stop_sent = True
                            note = (
                                f"manual stage pose lost {visual_gap_s:.2f}s; stopped in place and "
                                f"waiting for reacquisition ({stop_result})"
                            )
                        else:
                            note = (
                                f"manual stage pose lost {visual_gap_s:.2f}/"
                                f"{stage_visual_reacquire_timeout_s:.2f}s; waiting stationary for reacquisition"
                            )
                        if visual_gap_s > stage_visual_reacquire_timeout_s:
                            raise RuntimeError(
                                f"manual stage could not reacquire valid tag/axis pose within "
                                f"{stage_visual_reacquire_timeout_s:.1f}s; stopped in place with "
                                "automatic motion recovery disabled"
                            )
                elif visual_gap_s > control_visual_grace_s:
                    smoother.reset()
                    if live and not no_visual_stop_sent:
                        await _safe_stop(vehicle)
                        last_live_sent_cmd = VelocityCommand(0.0, 0.0)
                        next_motion_tx_mono = 0.0
                        consecutive_motion_ack_timeouts = 0
                        motion_tx_deferred = False
                        no_visual_stop_sent = True

            phase = _phase_name(controller)
            if world_axis_lock_enabled and not world_axis_locked:
                phase = (
                    f"MANUAL_{manual_stage.upper()}_WORLD_AXIS_LOCK"
                    if manual_stage else "WORLD_AXIS_LOCK"
                )
            elif manual_stage:
                phase = (
                    f"MANUAL_{manual_stage.upper()}_WAIT_POSE"
                    if stage_waiting_for_pose and not stage_pose_acquired_once
                    else (
                        f"MANUAL_{manual_stage.upper()}_REACQUIRE_POSE"
                        if stage_pose_reacquiring
                        else f"MANUAL_{manual_stage.upper()}"
                    )
                )
            wheels = differential_wheel_values(sent_cmd, wheel_radius_m, track_width_m)
            motor_left_rpm = wheels.left_rpm * reduction_ratio
            motor_right_rpm = wheels.right_rpm * reduction_ratio
            control_health = _control_port_health(vehicle)
            status_fields = {
                "manual_stage": manual_stage or "",
                "world_axis_locked": bool(world_axis_locked),
                "world_axis_lock_source": world_axis_lock_source,
                "world_axis_lock_samples": int(world_axis_lock_sample_count if world_axis_locked else len(world_axis_lock_candidates)),
                "world_axis_lock_required_samples": int(world_axis_lock_required_samples),
                "world_axis_lock_loaded": bool(world_axis_lock_loaded),
                "world_axis_lock_age_s": (
                    None if world_axis_lock_saved_at is None
                    else round(max(0.0, time.time() - world_axis_lock_saved_at), 3)
                ),
                "world_axis_corrections_frozen": bool(tag_localization_fusion.corrections_frozen),
                "manual_stage_waiting_for_pose": bool(stage_waiting_for_pose and not stage_pose_acquired_once),
                "manual_stage_pose_acquired_once": bool(stage_pose_acquired_once),
                "manual_stage_pose_wait_elapsed_s": (
                    round(max(0.0, time.monotonic() - stage_pose_wait_started_mono), 3)
                    if manual_stage and not stage_pose_acquired_once else 0.0
                ),
                "manual_stage_pose_acquire_timeout_s": stage_initial_pose_timeout_s if manual_stage else None,
                "manual_stage_pose_reacquiring": bool(stage_pose_reacquiring) if manual_stage else False,
                "manual_stage_visual_gap_s": (
                    None if not manual_stage or last_visual_valid_mono is None
                    else round(max(0.0, time.monotonic() - last_visual_valid_mono), 3)
                ),
                "manual_stage_visual_command_hold_s": stage_visual_command_hold_s if manual_stage else None,
                "manual_stage_visual_reacquire_timeout_s": stage_visual_reacquire_timeout_s if manual_stage else None,
                "tag_visible": raw_detection is not None,
                "tag_control_valid": detection is not None,
                "tag_display_held": bool(display_held),
                "tag_display_age_ms": None if display_age_ms is None else round(float(display_age_ms), 1),
                "axis_pose_locked": fused is not None,
                # Human-facing geometry uses the exact same raw filtered tracker
                # measurements as the camera HUD.  Motion-valid/fused values are
                # retained below under control_* fields.
                "axis_distance_m": None if display_metrics.get("axis_distance_m") is None else round(float(display_metrics["axis_distance_m"]), 6),
                "axis_error_m": None if display_metrics.get("axis_error_m") is None else round(float(display_metrics["axis_error_m"]), 6),
                "yaw_error_deg": None if display_metrics.get("yaw_error_deg") is None else round(float(display_metrics["yaw_error_deg"]), 4),
                "yaw_error_raw_deg": None if display_metrics.get("yaw_error_raw_deg") is None else round(float(display_metrics["yaw_error_raw_deg"]), 4),
                "axis_distance_raw_m": None if display_metrics.get("axis_distance_raw_m") is None else round(float(display_metrics["axis_distance_raw_m"]), 6),
                "axis_error_raw_m": None if display_metrics.get("axis_error_raw_m") is None else round(float(display_metrics["axis_error_raw_m"]), 6),
                "display_pose_filtered": True,
                "pose_measurement_source": str(display_metrics.get("pose_measurement_source", "-") or "-"),
                "display_pose_source": str(display_metrics.get("display_pose_source", display_metrics.get("pose_measurement_source", "-")) or "-"),
                "pose_measurement_confidence": None if display_metrics.get("pose_measurement_confidence") is None else round(float(display_metrics["pose_measurement_confidence"]), 3),
                "wall_plane_rms_mm": None if display_metrics.get("wall_plane_rms_mm") is None else round(float(display_metrics["wall_plane_rms_mm"]), 3),
                "wall_plane_inlier_ratio": None if display_metrics.get("wall_plane_inlier_ratio") is None else round(float(display_metrics["wall_plane_inlier_ratio"]), 3),
                "wall_plane_inliers": display_metrics.get("wall_plane_inliers"),
                "wall_plane_span_m": None if display_metrics.get("wall_plane_span_m") is None else round(float(display_metrics["wall_plane_span_m"]), 4),
                "pose_position_sigma_m": None if display_metrics.get("pose_position_sigma_m") is None else round(float(display_metrics["pose_position_sigma_m"]), 6),
                "pose_yaw_sigma_deg": None if display_metrics.get("pose_yaw_sigma_deg") is None else round(float(display_metrics["pose_yaw_sigma_deg"]), 3),
                "pnp_axis_distance_m": None if display_metrics.get("pnp_axis_distance_m") is None else round(float(display_metrics["pnp_axis_distance_m"]), 6),
                "pnp_axis_error_m": None if display_metrics.get("pnp_axis_error_m") is None else round(float(display_metrics["pnp_axis_error_m"]), 6),
                "pnp_yaw_error_deg": None if display_metrics.get("pnp_yaw_error_deg") is None else round(float(display_metrics["pnp_yaw_error_deg"]), 4),
                "rgbd_axis_distance_m": None if display_metrics.get("rgbd_axis_distance_m") is None else round(float(display_metrics["rgbd_axis_distance_m"]), 6),
                "rgbd_axis_error_m": None if display_metrics.get("rgbd_axis_error_m") is None else round(float(display_metrics["rgbd_axis_error_m"]), 6),
                "rgbd_yaw_error_deg": None if display_metrics.get("rgbd_yaw_error_deg") is None else round(float(display_metrics["rgbd_yaw_error_deg"]), 4),
                "lidar_wall_distance_m": None if display_metrics.get("lidar_wall_distance_m") is None else round(float(display_metrics["lidar_wall_distance_m"]), 6),
                "lidar_wall_rms_mm": None if display_metrics.get("lidar_wall_rms_mm") is None else round(float(display_metrics["lidar_wall_rms_mm"]), 3),
                "lidar_wall_inlier_ratio": None if display_metrics.get("lidar_wall_inlier_ratio") is None else round(float(display_metrics["lidar_wall_inlier_ratio"]), 3),
                "lidar_wall_inliers": display_metrics.get("lidar_wall_inliers"),
                "lidar_wall_span_m": None if display_metrics.get("lidar_wall_span_m") is None else round(float(display_metrics["lidar_wall_span_m"]), 4),
                "lidar_tag_ray_range_m": None if display_metrics.get("lidar_tag_ray_range_m") is None else round(float(display_metrics["lidar_tag_ray_range_m"]), 6),
                "laser_enabled": bool(lidar_enabled),
                "laser_fresh": bool(laser_age_s is not None and laser_age_s <= lidar_max_age_s and latest_laser_points),
                "laser_age_ms": None if laser_age_s is None else round(1000.0 * float(laser_age_s), 1),
                "laser_point_count": int(len(latest_laser_points)),
                "laser_poll_count": int(laser_poll_count),
                "laser_latency_ms": None if laser_latency_ms is None else round(float(laser_latency_ms), 1),
                "laser_source": laser_source,
                "laser_schema": laser_schema,
                "laser_attempts": list(laser_attempts),
                "laser_diagnostics": list(laser_diagnostics),
                "laser_step_param": laser_step_param,
                "laser_last_error": laser_last_error,
                "imu_enabled": bool(imu_enabled),
                "imu_fresh": bool(imu_fresh),
                "imu_age_ms": None if imu_age_s is None else round(1000.0 * float(imu_age_s), 1),
                "imu_poll_count": int(imu_poll_count),
                "imu_latency_ms": None if imu_latency_ms is None else round(float(imu_latency_ms), 1),
                "imu_source": imu_source,
                "imu_schema": imu_schema,
                "imu_last_error": imu_last_error,
                "imu_yaw_deg": None if latest_imu_attitude is None else round(math.degrees(float(latest_imu_attitude.yaw_rad)), 4),
                "imu_roll_deg": None if latest_imu_attitude is None or latest_imu_attitude.roll_rad is None else round(math.degrees(float(latest_imu_attitude.roll_rad)), 4),
                "imu_pitch_deg": None if latest_imu_attitude is None or latest_imu_attitude.pitch_rad is None else round(math.degrees(float(latest_imu_attitude.pitch_rad)), 4),
                "imu_yaw_rate_rps": round(float(imu_yaw_rate_rps), 6),
                "imu_yaw_fusion_source": imu_yaw_fusion_source,
                "imu_map_innovation_deg": None if imu_yaw_diag is None or imu_yaw_diag.map_innovation_rad is None else round(math.degrees(float(imu_yaw_diag.map_innovation_rad)), 4),
                "fusion_map_yaw_deg": None if fusion_map_pose is None else round(math.degrees(float(fusion_map_pose[2])), 4),
                "seer_map_x_m": None if seer_map_pose is None else round(float(seer_map_pose[0]), 6),
                "seer_map_y_m": None if seer_map_pose is None else round(float(seer_map_pose[1]), 6),
                "seer_map_yaw_deg": None if seer_map_pose is None else round(math.degrees(float(seer_map_pose[2])), 4),
                "seer_localization_score": None if seer_localization_score is None else round(float(seer_localization_score), 3),
                "seer_localization_ok": bool(localization_ok),
                "straight_target_heading_deg": None if stage_straight_anchor is None else round(math.degrees(float(stage_straight_anchor[2])), 4),
                "straight_current_heading_deg": None if stage_straight_current_heading_rad is None else round(math.degrees(float(stage_straight_current_heading_rad)), 4),
                "straight_heading_error_deg": None if stage_straight_heading_error_rad is None else round(math.degrees(float(stage_straight_heading_error_rad)), 4),
                "straight_cross_track_m": None if stage_straight_cross_track_m is None else round(float(stage_straight_cross_track_m), 6),
                "straight_heading_source": "SEER map + relative IMU yaw" if manual_stage == "straight" else "-",
                "straight_camera_steering": False if manual_stage == "straight" else None,
                "fusion_innovation_position_m": None if display_metrics.get("fusion_innovation_position_m") is None else round(float(display_metrics["fusion_innovation_position_m"]), 6),
                "fusion_innovation_yaw_deg": None if display_metrics.get("fusion_innovation_yaw_deg") is None else round(float(display_metrics["fusion_innovation_yaw_deg"]), 4),
                "fusion_correction_accepted": bool(display_metrics.get("fusion_correction_accepted", False)),
                "du_error_px": None if display_metrics.get("du_error_px") is None else round(float(display_metrics["du_error_px"]), 3),
                "marker_pitch_error_deg": None if display_metrics.get("marker_pitch_error_deg") is None else round(float(display_metrics["marker_pitch_error_deg"]), 4),
                "tag_depth_m": None if display_metrics.get("tag_depth_m") is None else round(float(display_metrics["tag_depth_m"]), 6),
                "tag_depth_source": str(display_metrics.get("tag_depth_source", "-") or "-"),
                "reprojection_error_px": None if display_metrics.get("reprojection_error_px") is None else round(float(display_metrics["reprojection_error_px"]), 3),
                "marker_side_px": None if display_metrics.get("marker_side_px") is None else round(float(display_metrics["marker_side_px"]), 2),
                "control_axis_distance_m": None if visual is None else round(float(visual.base_distance_m), 6),
                "control_axis_error_m": None if (fused is None and coarse_axis_state is None) else round(float((fused or coarse_axis_state).y), 6),
                "control_yaw_error_deg": None if (fused is None and coarse_axis_state is None) else round(math.degrees(float((fused or coarse_axis_state).yaw)), 4),
                "control_du_error_px": None if visual is None else round(float(visual.du_error_px), 3),
                "control_tag_depth_m": None if tag_depth_m is None else round(float(tag_depth_m), 6),
                "final_depth_target_m": round(float(getattr(controller, "final_tag_depth_m", 0.50)), 3),
                "verification_axis_error_m": None if getattr(controller, "verification_lateral_m", None) is None else round(float(controller.verification_lateral_m), 6),
                "verification_yaw_error_deg": None if getattr(controller, "verification_yaw_rad", None) is None else round(math.degrees(float(controller.verification_yaw_rad)), 4),
                "centerline_verified": bool(getattr(controller, "centerline_verified", False)),
                "centerline_hold_elapsed_s": round(float(getattr(controller, "centerline_hold_elapsed_s", 0.0)), 2),
                "centerline_hold_required_s": round(float(getattr(controller, "centerline_hold_s", 3.0)), 2),
                "yaw_verified": bool(getattr(controller, "yaw_verified", False)),
                "yaw_hold_elapsed_s": round(float(getattr(controller, "yaw_hold_elapsed_s", 0.0)), 2),
                "yaw_hold_required_s": round(float(getattr(controller, "yaw_hold_s", 3.0)), 2),
                "straight_axis_latched": bool(getattr(controller, "straight_axis_latched", False)),
                "final_hold_elapsed_s": round(0.0 if success_hold_since is None else max(0.0, time.monotonic() - success_hold_since), 2),
                "final_hold_required_s": round(float(final_success_hold_s), 2),
                "clearance_m": None if not math.isfinite(clearance_m) else round(float(clearance_m), 6),
                "target_v_mps": round(float(target_cmd.v), 6),
                "target_w_rps": round(float(target_cmd.omega), 6),
                "sent_v_mps": round(float(sent_cmd.v), 6),
                "sent_w_rps": round(float(sent_cmd.omega), 6),
                "motion_transmitted": bool(live),
                "seer_vx_mps": round(float(getattr(vehicle, "_vx", 0.0) or 0.0), 6),
                "seer_w_rps": round(float(getattr(vehicle, "_w", 0.0) or 0.0), 6),
                "blocked": bool(getattr(vehicle, "_blocked", False)),
                "block_reason": str(getattr(vehicle, "_block_reason", "") or ""),
                "blocked_event_count": int(blocked_event_count),
                "blocked_duration_ms": None if blocked_since_mono is None else round(1000.0 * max(0.0, time.monotonic() - blocked_since_mono), 1),
                "hard_stop_latched": bool(hard_stop_latched),
                "motion_command_hz": round(float(motion_command_hz), 2),
                "motion_ack_timeout_streak": int(consecutive_motion_ack_timeouts),
                "motion_tx_deferred": bool(motion_tx_deferred),
                "control_recovery_error_count": int(control_recovery_error_count),
                "control_recovery_last_error": control_recovery_last_error,
                "last_motion_error": last_motion_error,
                "emergency": bool(getattr(vehicle, "_emergency", False)),
                "motor_enabled": bool(getattr(vehicle, "_motor_flag", True)),
                "motor_flag_source": str(getattr(vehicle, "_motor_flag_source", "legacy") or "legacy"),
                "electric_state": getattr(vehicle, "_electric_state", None),
                "motor_left_cmd_rpm": round(float(motor_left_rpm), 2),
                "motor_right_cmd_rpm": round(float(motor_right_rpm), 2),
                "camera_loop_hz": round(float(camera_loop_hz), 2),
                "preview_fps_actual": round(float(preview_fps_actual), 2),
                "preview_seq": int(preview_seq),
                "preview_drop_count": int(preview_drop_count),
                "preview_render_ms": None if preview_last_duration_ms is None else round(float(preview_last_duration_ms), 1),
                "preview_age_ms": None if preview_last_complete_mono is None else round(1000.0 * max(0.0, time.monotonic() - preview_last_complete_mono), 1),
                "note": note,
                "control_probe_response": control_probe_response,
                "last_motion_response": last_motion_response,
                "last_motion_error": last_motion_error,
                "control_connected": control_health.get("connected"),
                "control_reconnect_count": control_health.get("reconnect_count"),
                "control_last_error": control_health.get("last_error", ""),
                "control_latency_ms": None if control_health.get("last_latency_sec") is None else round(1000.0 * float(control_health.get("last_latency_sec")), 1),
                "camera_owner_pid": os.getpid(),
                "session_id": session_id,
                "status_write_error_count": int(status_write_error_count),
                "status_last_write_error": status_last_write_error,
            }
            status_fields.update(recorder.status_fields())
            last_runtime_fields = dict(status_fields)
            recorder.log_telemetry({
                "timestamp_epoch_s": time.time(),
                "status": "running",
                "phase": phase,
                **status_fields,
            })
            if time.time() - last_status_write >= 0.10:
                publish("running", phase, note or "camera docking running", **status_fields)
            if time.time() - last_preview_write >= preview_interval_s:
                # Rendering, depth colormap, resizing, JPEG encoding and the
                # cross-process file publish all happen off the control loop.
                # There is never a queue: when the worker is still busy this
                # frame is dropped, so the browser always receives a recent
                # frame instead of accumulating seconds of latency.
                if preview_task is None:
                    preview_task = asyncio.create_task(
                        asyncio.to_thread(
                            _render_and_write_preview,
                            preview_path,
                            frame.copy(),
                            camera_frame.depth_m if depth_enabled else None,
                            source=source,
                            detector=detector,
                            detection=raw_detection,
                            t_base_camera=t_b_c,
                            t_marker_dock=t_m_d,
                            cfg=cfg,
                            depth_cfg=depth_cfg,
                            clearance_m=clearance_m,
                            control_valid=detection is not None,
                            control_note=note,
                            jpeg_quality=preview_jpeg_quality,
                            display_metrics=dict(display_metrics),
                            telemetry_path=telemetry_path,
                            telemetry={
                                **status_fields,
                                "schema": 1,
                                "session_id": session_id,
                                "started_at": start_wall,
                                "status": "running",
                                "run_mode": run_mode,
                                "phase": phase,
                                "message": note or "camera docking running",
                                "action_id": action_id,
                                "robot_ip": str(getattr(vehicle, "robot_ip", "") or ""),
                                "preview_seq": int(preview_seq + 1),
                            },
                            recorder=recorder,
                        )
                    )
                else:
                    preview_drop_count += 1
                last_preview_write = time.time()

            if manual_stage and stage_complete:
                final_phase = stage_complete_phase or f"STAGE_{manual_stage.upper()}_DONE"
                final_message = stage_complete_message or f"manual stage {manual_stage} complete"
                publish(
                    "finished",
                    final_phase,
                    final_message,
                    **{**status_fields, "terminal_message": final_message},
                )
                return DockingRunResult(True, final_message, final_phase)

            if fused is not None and visual is not None:
                final_depth_target_m = float(getattr(controller, "final_tag_depth_m", 0.50))
                final_depth_tol_m = float(getattr(controller, "final_tag_depth_tolerance_m", 0.03))
                verify_lateral = getattr(controller, "verification_lateral_m", None)
                verify_yaw = getattr(controller, "verification_yaw_rad", None)
                if verify_lateral is None:
                    verify_lateral = float(fused.y)
                if verify_yaw is None:
                    verify_yaw = float(fused.yaw)
                success_now = (
                    bool(getattr(controller, "straight_axis_latched", False))
                    and abs(float(visual.tag_depth_m) - final_depth_target_m) <= final_depth_tol_m
                    and abs(float(verify_lateral)) <= params.lateral_tolerance_m
                    and abs(float(verify_yaw)) <= params.yaw_tolerance_rad
                    and abs(float(getattr(vehicle, "_vx", fused.v) or 0.0)) <= 0.01
                    and abs(float(getattr(vehicle, "_w", fused.omega) or 0.0)) <= 0.02
                )
                if success_now:
                    success_hold_since = success_hold_since or time.monotonic()
                else:
                    success_hold_since = None
            else:
                success_hold_since = None
            success_hold_elapsed_s = 0.0 if success_hold_since is None else max(0.0, time.monotonic() - success_hold_since)
            if status_fields is not None:
                status_fields["final_hold_elapsed_s"] = round(success_hold_elapsed_s, 2)
                status_fields["final_hold_required_s"] = round(final_success_hold_s, 2)
                status_fields["final_depth_target_m"] = round(float(getattr(controller, "final_tag_depth_m", 0.50)), 3)
            if success_hold_elapsed_s >= final_success_hold_s:
                controller.phase = DockPhase.DOCKED
                final_phase = "DOCKED"
                final_message = (
                    f"centerline {getattr(controller, 'centerline_hold_s', 3.0):.1f}s + "
                    f"yaw {getattr(controller, 'yaw_hold_s', 3.0):.1f}s verified; "
                    f"straight-only approach; tag_depth 0.50 m held for {final_success_hold_s:.1f}s"
                )
                publish("finished", final_phase, final_message, **status_fields)
                return DockingRunResult(True, final_message, final_phase)
            if getattr(controller, "phase", None) == DockPhase.ABORTED:
                final_phase = "ABORTED"
                final_message = "docking controller aborted"
                break

        publish(
            "finished" if final_phase == "DOCKED" else "stopped",
            final_phase,
            final_message,
            **{**last_runtime_fields, "terminal_message": final_message},
        )
        return DockingRunResult(final_phase == "DOCKED", final_message, final_phase)
    except asyncio.CancelledError:
        final_phase = "CANCELLED"
        final_message = "도킹 Action이 사용자에 의해 취소되었습니다"
        publish("cancelled", final_phase, final_message, **last_runtime_fields)
        raise
    except Exception as exc:
        if isinstance(exc, CameraOpenError):
            final_phase = "CAMERA_OPEN_FAILED"
            final_message = str(exc)
        else:
            final_phase = "FAILED"
            final_message = f"{type(exc).__name__}: {exc}"
        publish(
            "failed", final_phase, final_message,
            **{**last_runtime_fields, "terminal_message": final_message},
        )
        return DockingRunResult(False, final_message, final_phase)
    finally:
        if laser_task is not None:
            if not laser_task.done():
                laser_task.cancel()
            try:
                await laser_task
            except (asyncio.CancelledError, Exception):
                pass
        if imu_task is not None:
            if not imu_task.done():
                imu_task.cancel()
            try:
                await imu_task
            except (asyncio.CancelledError, Exception):
                pass
        if preview_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(preview_task), timeout=1.0)
            except Exception:
                pass
        if smoother is not None:
            smoother.reset()
        stop_message = ""
        if live:
            stop_message = await _safe_stop(vehicle)
            try:
                current = read_docking_status(status_path)
                current = dict(current)
                current["stop_result"] = stop_message
                current["updated_at"] = time.time()
                _atomic_json(status_path, current)
            except Exception:
                pass
        record_status = (
            "finished" if final_phase == "DOCKED" else
            "cancelled" if final_phase == "CANCELLED" else
            "failed" if final_phase in {"FAILED", "CAMERA_OPEN_FAILED"} else
            "stopped"
        )
        try:
            await asyncio.to_thread(
                recorder.finish,
                status=record_status,
                phase=final_phase,
                message=final_message,
                stop_result=stop_message,
            )
        except Exception:
            pass
        try:
            current = read_docking_status(status_path)
            current = dict(current)
            current.update(recorder.status_fields())
            current["updated_at"] = time.time()
            _atomic_json(status_path, current)
        except Exception:
            pass
        if source is not None:
            try:
                await asyncio.to_thread(source.close)
            except Exception:
                pass
        if camera_lease is not None:
            try:
                camera_lease.release()
            except Exception:
                pass


def runtime_status_path_for(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / "seer-docking-status.json"


def runtime_preview_path_for(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / "seer-docking-preview.jpg"
