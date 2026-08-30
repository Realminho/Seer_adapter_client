"""Small cross-process runtime mailbox helpers for Windows/Linux.

The SEER WebUI and the per-robot Adapter run in separate processes.  Camera
preview/status files are therefore shared across processes.  On Windows an
``os.replace`` can intermittently fail when the WebUI happens to have the
current file open.  A tiny side-car lock coordinates readers and writers so
status/preview updates cannot collide and silently freeze.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterator, Mapping


class RuntimeMailboxBusy(TimeoutError):
    """Raised when a runtime mailbox lock cannot be acquired quickly enough."""


@contextmanager
def runtime_file_lock(target: Path, *, timeout_s: float = 0.25) -> Iterator[None]:
    """Acquire a short cross-process exclusive lock associated with ``target``."""

    target = Path(target)
    lock_path = target.with_name(target.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+b")
    locked = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + max(0.001, float(timeout_s))
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeMailboxBusy(f"runtime mailbox busy: {target}")
                    time.sleep(0.002)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeMailboxBusy(f"runtime mailbox busy: {target}")
                    time.sleep(0.002)
        yield
    finally:
        if locked:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        stream.close()


def atomic_write_bytes(path: Path, data: bytes, *, timeout_s: float = 0.25) -> None:
    """Write bytes atomically while coordinating with cross-process readers."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
    )
    temp.write_bytes(data)
    try:
        with runtime_file_lock(path, timeout_s=timeout_s):
            last_error: OSError | None = None
            for _ in range(5):
                try:
                    os.replace(str(temp), str(path))
                    return
                except OSError as exc:
                    last_error = exc
                    time.sleep(0.003)
            if last_error is not None:
                raise last_error
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def read_bytes_locked(path: Path, *, timeout_s: float = 0.08) -> bytes:
    path = Path(path)
    with runtime_file_lock(path, timeout_s=timeout_s):
        return path.read_bytes()




def read_bytes_snapshot(path: Path, *, retries: int = 3, retry_delay_s: float = 0.001) -> bytes:
    """Read an atomically-published mailbox without taking the side-car lock.

    Writers publish through a temp file + ``os.replace``, so readers always see
    either the previous complete payload or the next complete payload.  Keeping
    WebUI readers off the exclusive side-car lock is important on Windows: a
    4 Hz browser poll must never be able to starve the 10 Hz Adapter heartbeat.
    A tiny retry loop only covers transient Windows file-handle races.
    """

    path = Path(path)
    attempts = max(1, int(retries))
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            return path.read_bytes()
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(max(0.0, float(retry_delay_s)))
    assert last_error is not None
    raise last_error


def read_json_snapshot(path: Path, *, retries: int = 3, retry_delay_s: float = 0.001) -> dict[str, Any]:
    """Lock-free reader for JSON mailboxes published atomically."""

    raw = read_bytes_snapshot(path, retries=retries, retry_delay_s=retry_delay_s)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime mailbox JSON must be an object")
    return payload

def remove_file_locked(path: Path, *, timeout_s: float = 0.25) -> None:
    """Remove a mailbox payload while coordinating with readers/writers."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with runtime_file_lock(path, timeout_s=timeout_s):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, payload: Mapping[str, Any], *, timeout_s: float = 0.25) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    atomic_write_bytes(path, data, timeout_s=timeout_s)


def read_json_locked(path: Path, *, timeout_s: float = 0.08) -> dict[str, Any]:
    raw = read_bytes_locked(path, timeout_s=timeout_s)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime mailbox JSON must be an object")
    return payload
