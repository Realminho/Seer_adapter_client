"""Persist the last real JIBOT pose for simulator startup."""

import datetime
import json
from pathlib import Path
from typing import Any, Dict, Optional


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def save_position_snapshot(
    path: Path,
    *,
    x: Any,
    y: Any,
    theta: Any,
    map_id: str,
    serial_number: str,
) -> bool:
    """Write a position snapshot atomically.

    Returns False when the vehicle has not reported a complete numeric pose yet.
    """
    pose_x = _as_float(x)
    pose_y = _as_float(y)
    pose_theta = _as_float(theta)
    if pose_x is None or pose_y is None or pose_theta is None:
        return False

    payload = {
        "serialNumber": serial_number,
        "mapId": map_id,
        "x": pose_x,
        "y": pose_y,
        "theta": pose_theta,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return True


def load_position_snapshot(path: Path) -> Optional[Dict[str, float]]:
    """Load a position snapshot as ``{"x", "y", "theta"}``, or None."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    pose_x = _as_float(payload.get("x"))
    pose_y = _as_float(payload.get("y"))
    pose_theta = _as_float(payload.get("theta", payload.get("th")))
    if pose_x is None or pose_y is None or pose_theta is None:
        return None

    return {"x": pose_x, "y": pose_y, "theta": pose_theta}
