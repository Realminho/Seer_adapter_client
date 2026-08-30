"""Filesystem IPC paths shared by the adapter and the WebUi.

The adapter writes state/health under /run/amr-adaptor/<serial>/ (tmpfs) and
listens on a control socket there; the WebUi reads those files and connects to
the socket. /run/amr-adaptor itself is created by tmpfiles.d; each adapter
creates and owns only its own <serial>/ subdirectory.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

RUNTIME_ROOT = Path("/run/amr-adaptor")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_serial(serial: str) -> str:
    """Sanitize ``serial`` into a single safe path component.

    serial comes from config (and feeds the MQTT topic prefix), but defend the
    runtime path regardless: chars outside ``[A-Za-z0-9._-]`` (incl. ``/``)
    collapse to ``_``; ``""``/``.``/``..`` are rejected so the path cannot
    escape RUNTIME_ROOT.
    """
    s = _UNSAFE.sub("_", str(serial))
    if s in ("", ".", ".."):
        raise ValueError(f"unsafe serial for runtime path: {serial!r}")
    return s


def runtime_dir(serial: str) -> Path:
    return RUNTIME_ROOT / safe_serial(serial)


def state_path(serial: str) -> Path:
    return runtime_dir(serial) / "state.json"


def health_path(serial: str) -> Path:
    return runtime_dir(serial) / "health.json"


def io_path(serial: str) -> Path:
    return runtime_dir(serial) / "io.json"


def control_sock_path(serial: str) -> Path:
    return runtime_dir(serial) / "control.sock"


def ensure_runtime_dir(serial: str) -> Path:
    d = runtime_dir(serial)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o750)
    return d


def atomic_write_json(path: Path, obj: dict) -> None:
    """Write JSON to ``path`` atomically (tmp file + rename on same dir)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
