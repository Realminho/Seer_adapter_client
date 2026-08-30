"""Run tests/diagnostics and tail logs as background subprocesses.

Both the Tests view and the Logs view need to stream a child process's output
into a scrollback buffer without blocking the curses loop. :class:`StreamProcess`
does that: it spawns the process, a daemon thread pumps merged stdout/stderr
into a bounded deque, and the UI thread reads a snapshot each frame.

Each pump thread owns the exact ``Popen`` it was started with (passed in as an
argument) and only writes back state when that process is still the current one,
so a rapid stop()+start() (e.g. switching log units) can never make an old pump
clobber the new process's state. ``stop()`` joins the previous pump before the
caller rebinds, guaranteeing at most one live pump.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections import deque
from typing import Deque, List, Optional, Sequence


class StreamProcess:
    """A subprocess whose output is captured line-by-line into a ring buffer."""

    def __init__(self, maxlines: int = 2000) -> None:
        self._lock = threading.Lock()
        self._lines: Deque[str] = deque(maxlen=maxlines)
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self.label = ""
        self.returncode: Optional[int] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, argv: Sequence[str], cwd: Optional[str] = None, label: str = "") -> None:
        self.stop()  # kills + joins any previous process/pump before rebinding
        with self._lock:
            self._lines.clear()
            self.returncode = None
            self.label = label or " ".join(argv)
            self._lines.append(f"$ {' '.join(argv)}")
        env = dict(os.environ)
        # Unbuffered child output so lines arrive promptly in the buffer.
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            proc = subprocess.Popen(
                list(argv),
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,
            )
        except (FileNotFoundError, OSError) as exc:
            with self._lock:
                self._lines.append(f"[failed to launch] {exc}")
                self.returncode = 127
            return
        with self._lock:
            self._proc = proc
            self._running = True
        self._thread = threading.Thread(target=self._pump, args=(proc,), daemon=True)
        self._thread.start()

    def _pump(self, proc: subprocess.Popen) -> None:
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    with self._lock:
                        # Only record output while this proc is still current.
                        if self._proc is proc:
                            self._lines.append(line.rstrip("\n"))
        except Exception:  # pragma: no cover - defensive
            pass
        finally:
            if proc.stdout is not None:
                try:
                    proc.stdout.close()  # don't leak the pipe fd across runs
                except Exception:
                    pass
        code = proc.wait()
        with self._lock:
            if self._proc is proc:
                self.returncode = code
                self._lines.append(f"[exit {code}]")
                self._running = False

    def stop(self) -> None:
        proc = self._proc
        thread = self._thread
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    proc.wait(timeout=2)  # reap so it cannot linger as a zombie
                except subprocess.TimeoutExpired:
                    pass
        # Join the previous pump so it finishes before the caller rebinds _proc.
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._lock:
            self._running = False

    def lines(self) -> List[str]:
        with self._lock:
            return list(self._lines)

    def line_count(self) -> int:
        with self._lock:
            return len(self._lines)
