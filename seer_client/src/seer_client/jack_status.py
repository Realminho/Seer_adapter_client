"""Small sidecar cache for SEER Jack operator state.

The physical controller exposes a definitive lower-limit DI (DI2) but no
usable upper-limit DI on the current vehicle.  Jack completion is therefore
verified from TASK status while an operation is running and published here so
WebUI can render one stateful toggle control without changing the shared
Adapter sources.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Optional

SCHEMA = 1
VALID_PHASES = frozenset({
    "unknown",
    "moving_up",
    "up",
    "moving_down",
    "down",
    "failed",
})


def cache_path_from_env() -> Optional[Path]:
    raw = str(os.getenv("SEER_JACK_STATUS_CACHE_PATH", "") or "").strip()
    return Path(raw) if raw else None


def write_jack_status(
    path: Optional[Path],
    *,
    phase: str,
    operation: str = "",
    task_status: Any = None,
    operation_status: Any = None,
    message: str = "",
) -> None:
    if path is None:
        return
    normalized = str(phase or "unknown").strip().lower()
    if normalized not in VALID_PHASES:
        normalized = "unknown"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "updated_at": time.time(),
        "phase": normalized,
        "operation": str(operation or ""),
        "task_status": task_status,
        "operation_status": operation_status,
        "message": str(message or ""),
    }
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        encoding="utf-8",
    )
    os.replace(str(temporary), str(target))


def read_jack_status(path: Optional[Path]) -> Optional[Mapping[str, Any]]:
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
        return None
    phase = str(payload.get("phase", "unknown") or "unknown").strip().lower()
    if phase not in VALID_PHASES:
        return None
    return payload
