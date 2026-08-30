"""Small platform shims required by the unmodified shared Adapter."""

from __future__ import annotations

import os
import sys
import types
from typing import Any


def install_fcntl_compat() -> None:
    """Expose the ``flock`` subset used by Adapter config I/O on Windows.

    The current shared Adapter imports :mod:`fcntl` unconditionally.  Linux
    already provides the real module.  On Windows, ``msvcrt.locking`` gives us
    an equivalent one-byte, cross-process lock for the Adapter's dedicated
    ``*.lock`` files without changing anything under ``adaptor/``.
    """

    if os.name != "nt" or "fcntl" in sys.modules:
        return

    import msvcrt

    module = types.ModuleType("fcntl")
    module.LOCK_SH = 1
    module.LOCK_EX = 2
    module.LOCK_NB = 4
    module.LOCK_UN = 8

    def flock(file_object: Any, operation: int) -> None:
        descriptor = file_object.fileno()
        file_object.seek(0, os.SEEK_END)
        if file_object.tell() == 0:
            file_object.write("\0")
            file_object.flush()
        file_object.seek(0)
        if operation & module.LOCK_UN:
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
        mode = msvcrt.LK_NBLCK if operation & module.LOCK_NB else msvcrt.LK_LOCK
        # msvcrt has no shared-lock primitive.  Treat LOCK_SH as exclusive;
        # Adapter only requests LOCK_EX today and this is the safe fallback.
        msvcrt.locking(descriptor, mode, 1)

    module.flock = flock
    sys.modules["fcntl"] = module

