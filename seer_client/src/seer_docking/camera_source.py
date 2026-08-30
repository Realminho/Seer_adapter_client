"""USB and Intel RealSense camera sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import subprocess
import sys
from typing import Any, Protocol

import numpy as np

from .vision import load_camera_calibration


@dataclass(slots=True)
class CameraFrame:
    color_bgr: np.ndarray
    depth_m: np.ndarray | None
    timestamp_s: float


class CameraSource(Protocol):
    camera_matrix: np.ndarray
    distortion: np.ndarray
    has_depth: bool

    def read(self) -> CameraFrame: ...

    def close(self) -> None: ...


class OpenCVCameraSource:
    """Ordinary USB camera using a saved OpenCV calibration."""

    has_depth = False

    def __init__(
        self,
        index: int,
        calibration_path: str | Path,
        width: int | None = None,
        height: int | None = None,
        fps: int | None = None,
    ) -> None:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover
            raise ImportError("Install camera support with: pip install -e .[vision]") from exc
        self._cv2 = cv2
        self.camera_matrix, self.distortion = load_camera_calibration(calibration_path)
        self._capture = cv2.VideoCapture(int(index))
        if width:
            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        if height:
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        if fps:
            self._capture.set(cv2.CAP_PROP_FPS, int(fps))
        if not self._capture.isOpened():
            raise RuntimeError(f"USB camera index {index} could not be opened")

    def read(self) -> CameraFrame:
        import time

        ok, color = self._capture.read()
        if not ok:
            raise RuntimeError("USB camera frame read failed")
        return CameraFrame(color, None, time.monotonic())

    def close(self) -> None:
        self._capture.release()


class RealSenseCameraSource:
    """RealSense color plus depth aligned into the color pixel coordinates."""

    has_depth = True

    def __init__(
        self,
        serial: str | None = None,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        timeout_ms: int = 1500,
    ) -> None:
        try:
            import pyrealsense2 as rs
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Intel RealSense support is missing. Install with: "
                'pip install -e ".[vision,realsense]"'
            ) from exc
        self._rs = rs
        self._timeout_ms = int(timeout_ms)
        self._pipeline = rs.pipeline()
        config = rs.config()
        if serial:
            config.enable_device(str(serial))
        config.enable_stream(rs.stream.depth, int(width), int(height), rs.format.z16, int(fps))
        config.enable_stream(rs.stream.color, int(width), int(height), rs.format.bgr8, int(fps))
        try:
            self._profile = self._pipeline.start(config)
        except Exception as exc:
            requested = f"{int(width)}x{int(height)}@{int(fps)} RGB-D"
            selected = f" serial={serial}" if serial else ""
            raise RuntimeError(
                "RealSense start failed for "
                f"{requested}{selected}. Driver detail: {type(exc).__name__}: {exc}. "
                "Check camera occupancy, USB 3 connection, serial selection, and supported stream profile."
            ) from exc
        self._align = rs.align(rs.stream.color)
        sensor = self._profile.get_device().first_depth_sensor()
        self.depth_scale_m = float(sensor.get_depth_scale())
        color_profile = self._profile.get_stream(rs.stream.color).as_video_stream_profile()
        intr = color_profile.get_intrinsics()
        self.camera_matrix = np.array(
            [[intr.fx, 0.0, intr.ppx], [0.0, intr.fy, intr.ppy], [0.0, 0.0, 1.0]],
            dtype=float,
        )
        coefficients = list(intr.coeffs)
        self.distortion = np.asarray(coefficients[:5], dtype=float)
        self.serial = self._profile.get_device().get_info(rs.camera_info.serial_number)
        self.product_line = self._profile.get_device().get_info(rs.camera_info.product_line)

    def read(self) -> CameraFrame:
        import time

        frames = self._pipeline.wait_for_frames(self._timeout_ms)
        aligned = self._align.process(frames)
        depth_frame = aligned.get_depth_frame()
        color_frame = aligned.get_color_frame()
        if not depth_frame or not color_frame:
            raise RuntimeError("RealSense returned an incomplete aligned RGB-D frame")
        color = np.asanyarray(color_frame.get_data())
        depth_m = np.asanyarray(depth_frame.get_data()).astype(np.float32) * self.depth_scale_m
        return CameraFrame(color, depth_m, time.monotonic())

    def close(self) -> None:
        self._pipeline.stop()


def probe_realsense_camera(
    camera_cfg: dict[str, Any],
    *,
    timeout_s: float = 6.0,
) -> dict[str, Any]:
    """Open/read/close RealSense in a disposable child process.

    ``pyrealsense2.pipeline.start()`` has no Python-level timeout argument. A
    driver/USB deadlock can therefore leave the Adapter permanently stuck in
    CAMERA_OPEN. The probe owns the device only in a short-lived subprocess;
    if it does not finish in ``timeout_s`` the subprocess is terminated without
    leaking a camera-owning thread into the Adapter process.
    """

    cfg = {
        "serial": str(camera_cfg.get("serial", "") or ""),
        "width": int(camera_cfg.get("width", 640)),
        "height": int(camera_cfg.get("height", 480)),
        "fps": int(camera_cfg.get("fps", 30)),
        "frame_timeout_ms": int(camera_cfg.get("timeout_ms", 1500)),
    }
    script = r"""
import json
import sys
import time

cfg = json.loads(sys.stdin.read())
result = {
    "ok": False,
    "requested": {
        "serial": cfg.get("serial", ""),
        "width": int(cfg.get("width", 640)),
        "height": int(cfg.get("height", 480)),
        "fps": int(cfg.get("fps", 30)),
    },
}
pipeline = None
started = False
try:
    import pyrealsense2 as rs
    ctx = rs.context()
    devices = list(ctx.query_devices())
    found = []
    for dev in devices:
        try:
            serial = dev.get_info(rs.camera_info.serial_number)
        except Exception:
            serial = "?"
        try:
            name = dev.get_info(rs.camera_info.name)
        except Exception:
            name = "RealSense"
        found.append({"serial": serial, "name": name})
    result["devices"] = found
    if not devices:
        result.update(code="NO_DEVICE", message="No Intel RealSense device was detected by pyrealsense2.")
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0)
    requested_serial = str(cfg.get("serial", "") or "")
    if requested_serial and requested_serial not in {str(x.get("serial", "")) for x in found}:
        result.update(
            code="SERIAL_NOT_FOUND",
            message=f"Requested RealSense serial {requested_serial!r} was not detected; detected={found}",
        )
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0)

    pipeline = rs.pipeline()
    config = rs.config()
    if requested_serial:
        config.enable_device(requested_serial)
    width = int(cfg.get("width", 640))
    height = int(cfg.get("height", 480))
    fps = int(cfg.get("fps", 30))
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
    config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    t0 = time.monotonic()
    profile = pipeline.start(config)
    started = True
    open_ms = (time.monotonic() - t0) * 1000.0
    frames = pipeline.wait_for_frames(int(cfg.get("frame_timeout_ms", 1500)))
    depth = frames.get_depth_frame()
    color = frames.get_color_frame()
    if not depth or not color:
        raise RuntimeError("RealSense opened but did not return a complete RGB-D frame")
    dev = profile.get_device()
    try:
        serial = dev.get_info(rs.camera_info.serial_number)
    except Exception:
        serial = "?"
    try:
        product = dev.get_info(rs.camera_info.product_line)
    except Exception:
        product = "?"
    result.update(
        ok=True,
        code="OK",
        message="RealSense open + first RGB-D frame probe succeeded.",
        serial=serial,
        product_line=product,
        open_ms=round(open_ms, 1),
    )
except SystemExit:
    pass
except Exception as exc:
    result.update(
        code="OPEN_FAILED",
        message=f"{type(exc).__name__}: {exc}",
    )
finally:
    if started and pipeline is not None:
        try:
            pipeline.stop()
        except Exception:
            pass
if result.get("code") not in {"NO_DEVICE", "SERIAL_NOT_FOUND"}:
    print(json.dumps(result, ensure_ascii=False))
"""
    timeout_s = max(1.0, float(timeout_s))
    kwargs: dict[str, Any] = {
        "input": json.dumps(cfg, ensure_ascii=False),
        "text": True,
        "capture_output": True,
        "timeout": timeout_s,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        completed = subprocess.run([sys.executable, "-c", script], **kwargs)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "code": "OPEN_TIMEOUT",
            "message": (
                f"RealSense open probe timed out after {timeout_s:.1f}s. "
                "The camera may be occupied by RealSense Viewer/another Python process, "
                "the USB link may be stalled, or the requested RGB-D profile may not start."
            ),
            "requested": cfg,
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": "PROBE_LAUNCH_FAILED",
            "message": f"{type(exc).__name__}: {exc}",
            "requested": cfg,
        }

    stdout = str(completed.stdout or "").strip().splitlines()
    parsed: dict[str, Any] | None = None
    for line in reversed(stdout):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            parsed = value
            break
    if parsed is None:
        stderr = str(completed.stderr or "").strip()
        return {
            "ok": False,
            "code": "PROBE_BAD_OUTPUT",
            "message": stderr or f"RealSense probe exited with code {completed.returncode} without diagnostics.",
            "requested": cfg,
        }
    if completed.returncode not in (0, None) and parsed.get("ok"):
        parsed = dict(parsed)
        parsed["ok"] = False
        parsed["code"] = "PROBE_PROCESS_FAILED"
        parsed["message"] = str(completed.stderr or "").strip() or f"probe exit code {completed.returncode}"
    return parsed



def probe_realsense_camera_quick(camera_cfg: dict[str, Any]) -> dict[str, Any]:
    """Very fast RealSense preflight that never resolves/opens a pipeline.

    The old "quick" probe still called ``config.resolve()``, which can take a
    surprisingly long time on Windows/USB and made PREVIEW feel frozen before
    the Adapter even attempted its real open.  This path only enumerates the
    device and its advertised video profiles.  The Adapter remains the only
    process that actually opens the RGB-D stream.
    """

    import time

    requested = {
        "serial": str(camera_cfg.get("serial", "") or ""),
        "width": int(camera_cfg.get("width", 640)),
        "height": int(camera_cfg.get("height", 480)),
        "fps": int(camera_cfg.get("fps", 30)),
    }
    started = time.monotonic()
    try:
        import pyrealsense2 as rs

        ctx = rs.context()
        devices = list(ctx.query_devices())
        found: list[dict[str, str]] = []
        for dev in devices:
            try:
                serial = str(dev.get_info(rs.camera_info.serial_number))
            except Exception:
                serial = "?"
            try:
                name = str(dev.get_info(rs.camera_info.name))
            except Exception:
                name = "RealSense"
            found.append({"serial": serial, "name": name})

        if not devices:
            return {
                "ok": False,
                "code": "NO_DEVICE",
                "message": "No Intel RealSense device was detected by pyrealsense2.",
                "requested": requested,
                "devices": found,
                "probe_mode": "quick-enumeration",
                "probe_ms": round(1000.0 * (time.monotonic() - started), 1),
            }

        requested_serial = requested["serial"]
        selected = None
        for dev, info in zip(devices, found):
            if not requested_serial or info["serial"] == requested_serial:
                selected = dev
                break
        if selected is None:
            return {
                "ok": False,
                "code": "SERIAL_NOT_FOUND",
                "message": f"Requested RealSense serial {requested_serial!r} was not detected; detected={found}",
                "requested": requested,
                "devices": found,
                "probe_mode": "quick-enumeration",
                "probe_ms": round(1000.0 * (time.monotonic() - started), 1),
            }

        # Profile enumeration is local metadata access and does not claim the
        # USB stream.  It catches obvious unsupported width/height/FPS choices
        # without the expensive pipeline.resolve()/start()/stop() sequence.
        have_depth = False
        have_color = False
        try:
            for sensor in selected.query_sensors():
                for profile in sensor.get_stream_profiles():
                    try:
                        video = profile.as_video_stream_profile()
                        if (
                            int(video.width()) != requested["width"]
                            or int(video.height()) != requested["height"]
                            or int(profile.fps()) != requested["fps"]
                        ):
                            continue
                        stream_type = profile.stream_type()
                        fmt = profile.format()
                        if stream_type == rs.stream.depth and fmt == rs.format.z16:
                            have_depth = True
                        elif stream_type == rs.stream.color and fmt == rs.format.bgr8:
                            have_color = True
                    except Exception:
                        continue
        except Exception:
            # Some librealsense builds/devices do not expose profile metadata
            # reliably before open.  Do not turn that into a false failure; the
            # real Adapter open below remains authoritative.
            have_depth = have_color = True

        if not (have_depth and have_color):
            missing = []
            if not have_depth:
                missing.append("depth z16")
            if not have_color:
                missing.append("color bgr8")
            return {
                "ok": False,
                "code": "PROFILE_UNAVAILABLE",
                "message": (
                    f"Requested {requested['width']}x{requested['height']}@{requested['fps']} "
                    f"RGB-D profile is missing advertised {' + '.join(missing)} stream(s)."
                ),
                "requested": requested,
                "devices": found,
                "probe_mode": "quick-enumeration",
                "probe_ms": round(1000.0 * (time.monotonic() - started), 1),
            }

        elapsed_ms = 1000.0 * (time.monotonic() - started)
        return {
            "ok": True,
            "code": "OK_FAST",
            "message": "RealSense device/profile advertised; Adapter will open the stream once.",
            "requested": requested,
            "devices": found,
            "probe_mode": "quick-enumeration",
            "probe_ms": round(elapsed_ms, 1),
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": "QUICK_PROBE_FAILED",
            "message": f"{type(exc).__name__}: {exc}",
            "requested": requested,
            "probe_mode": "quick-enumeration",
            "probe_ms": round(1000.0 * (time.monotonic() - started), 1),
        }

def create_camera_source(
    camera_cfg: dict[str, Any],
    legacy_calibration: str | None = None,
) -> CameraSource:
    camera_type = str(camera_cfg.get("type", "usb")).lower()
    if camera_type in ("realsense", "intel_realsense", "rgbd"):
        return RealSenseCameraSource(
            serial=camera_cfg.get("serial") or None,
            width=int(camera_cfg.get("width", 640)),
            height=int(camera_cfg.get("height", 480)),
            fps=int(camera_cfg.get("fps", 30)),
            timeout_ms=int(camera_cfg.get("timeout_ms", 1500)),
        )
    if camera_type not in ("usb", "opencv"):
        raise ValueError(
            f"unsupported camera.type={camera_type!r}; use 'realsense' or calibrated 'usb'"
        )
    calibration = camera_cfg.get("calibration") or legacy_calibration
    if not calibration:
        raise ValueError("USB camera mode requires camera.calibration")
    return OpenCVCameraSource(
        index=int(camera_cfg.get("index", 0)),
        calibration_path=calibration,
        width=camera_cfg.get("width"),
        height=camera_cfg.get("height"),
        fps=camera_cfg.get("fps"),
    )


def median_depth_at_pixel(
    depth_m: np.ndarray | None,
    pixel_xy: tuple[float, float] | np.ndarray,
    radius_px: int = 5,
    min_valid_m: float = 0.10,
    max_valid_m: float = 10.0,
) -> float | None:
    """Median valid aligned depth in a square patch around one color pixel."""

    if depth_m is None or depth_m.ndim != 2:
        return None
    u, v = np.asarray(pixel_xy, dtype=float).reshape(2)
    if not np.isfinite([u, v]).all():
        return None
    h, w = depth_m.shape
    cx, cy = int(round(u)), int(round(v))
    radius = max(0, int(radius_px))
    x0, x1 = max(0, cx - radius), min(w, cx + radius + 1)
    y0, y1 = max(0, cy - radius), min(h, cy + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return None
    values = np.asarray(depth_m[y0:y1, x0:x1], dtype=float).reshape(-1)
    valid = values[np.isfinite(values) & (values >= min_valid_m) & (values <= max_valid_m)]
    return float(np.median(valid)) if valid.size else None


def depth_clearance_in_roi(
    depth_m: np.ndarray | None,
    roi_norm: list[float] | tuple[float, float, float, float],
    percentile: float = 5.0,
    min_valid_m: float = 0.10,
    max_valid_m: float = 10.0,
) -> float:
    """Robust near-depth percentile in normalized ``[x0,x1,y0,y1]`` ROI."""

    if depth_m is None or depth_m.ndim != 2:
        return float("inf")
    x0n, x1n, y0n, y1n = [float(v) for v in roi_norm]
    h, w = depth_m.shape
    x0 = int(np.clip(round(x0n * w), 0, w))
    x1 = int(np.clip(round(x1n * w), 0, w))
    y0 = int(np.clip(round(y0n * h), 0, h))
    y1 = int(np.clip(round(y1n * h), 0, h))
    if x0 >= x1 or y0 >= y1:
        raise ValueError("camera.depth.obstacle_roi must be [x0,x1,y0,y1] with increasing bounds")
    values = np.asarray(depth_m[y0:y1, x0:x1], dtype=float).reshape(-1)
    valid = values[np.isfinite(values) & (values >= min_valid_m) & (values <= max_valid_m)]
    if valid.size < 20:
        return float("inf")
    return float(np.percentile(valid, float(np.clip(percentile, 0.0, 100.0))))


def depth_colormap(depth_m: np.ndarray, max_depth_m: float = 4.0) -> np.ndarray:
    """Generate a BGR debug image without changing metric depth data."""

    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise ImportError("OpenCV is required for the depth preview") from exc
    clipped = np.clip(np.asarray(depth_m, dtype=float), 0.0, max_depth_m)
    gray = np.uint8(255.0 * (1.0 - clipped / max_depth_m))
    color = cv2.applyColorMap(gray, cv2.COLORMAP_TURBO)
    color[~np.isfinite(depth_m) | (depth_m <= 0.0)] = 0
    return color
