"""OS-level sound playback for the adapter host.

Thin wrapper around an external player (mplayer by default) routed through
PulseAudio. One playback process at a time; same-track calls are deduped so the
auto-driver can call play() every cycle without restarting audio. No exception
escapes — a missing player binary disables the player; other failures are logged.

Repeat behaviour has two knobs. ``repeat_count`` is how many times a track plays
(0 = until stopped), ``replay_gap_sec`` is the silence between those plays. A
seamless endless loop is handed to the player itself (``-loop 0``); anything with
a gap or a finite count is driven one shot at a time from a helper thread,
because mplayer cannot express either.
"""

import atexit
import logging
import os
import subprocess
import threading
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class SoundPlayer:
    def __init__(
        self,
        *,
        sink: str = "",
        player: str = "mplayer",
        sound_dir: str = "sounds",
        base_dir: Optional[str] = None,
        enabled: bool = True,
        player_wait_timeout_sec: float = 1.0,
        pactl_timeout_sec: float = 2.0,
    ) -> None:
        self._sink = sink or ""
        self._player = player or "mplayer"
        base = base_dir or os.getcwd()
        self.sound_dir = sound_dir if os.path.isabs(sound_dir) else os.path.join(base, sound_dir)
        self._disabled = not enabled
        self._proc: Optional[subprocess.Popen] = None
        self._current: Optional[str] = None
        self._current_loop = False
        self._current_replay_gap = 0.0
        self._current_repeat = 0
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_stop = threading.Event()
        self._lock = threading.RLock()
        self._player_wait_timeout = player_wait_timeout_sec
        self._pactl_timeout = pactl_timeout_sec
        atexit.register(self.stop)

    def _resolve(self, track: str) -> str:
        return track if os.path.isabs(track) else os.path.join(self.sound_dir, track)

    def is_playing(self) -> bool:
        with self._lock:
            loop_alive = self._loop_thread is not None and self._loop_thread.is_alive()
            proc_alive = self._proc is not None and self._proc.poll() is None
            return loop_alive or proc_alive

    def has_track(self, track: str) -> bool:
        """True if the track file exists under sound_dir. Lets the auto-driver
        pick the most specific available sound (per-detail file, else per-state)."""
        return os.path.isfile(self._resolve(track))

    def play(
        self,
        track: str,
        loop: bool = True,
        replay_gap_sec: float = 0.0,
        repeat_count: int = 0,
    ) -> None:
        """Play ``track``, repeating it ``repeat_count`` times (0 = until stopped).

        ``repeat_count`` only means anything while ``loop`` is set; a one-shot
        call already plays exactly once. When the count runs out playback simply
        ends and the player goes idle -- the caller is not told, because the
        auto-driver's own dedupe is what stops the same track being requeued
        while the state that chose it is still true.
        """
        if self._disabled:
            return
        path = self._resolve(track)
        replay_gap = max(0.0, float(replay_gap_sec or 0.0))
        repeat = self._normalize_repeat(repeat_count)
        if not loop:
            repeat = 1
        with self._lock:
            loop_alive = self._loop_thread is not None and self._loop_thread.is_alive()
            proc_alive = self._proc is not None and self._proc.poll() is None
            same_request = (
                self._current == path
                and self._current_loop == loop
                and self._current_replay_gap == replay_gap
                and self._current_repeat == repeat
            )
            if same_request and (loop_alive or proc_alive):
                return
        if not os.path.exists(path):
            logger.warning("sound file not found: %s", path)
            return
        self.stop()
        pulse_output = f"pulse::{self._sink}" if self._sink else "pulse"
        cmd: List[str] = [self._player, "-really-quiet", "-ao", pulse_output]
        # mplayer can only do "forever, seamlessly". A gap between plays or a
        # finite count has to be driven one process at a time from our thread.
        adapter_managed_loop = loop and (replay_gap > 0 or repeat > 1)
        if loop and repeat != 1 and not adapter_managed_loop:
            cmd += ["-loop", "0"]
        cmd.append(path)
        env = dict(os.environ)
        if self._sink:
            env["PULSE_SINK"] = self._sink
        if adapter_managed_loop:
            self._start_adapter_managed_loop(path, cmd, env, replay_gap, repeat)
            return
        try:
            self._proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._current = path
            self._current_loop = loop
            self._current_replay_gap = replay_gap
            self._current_repeat = repeat
        except FileNotFoundError:
            logger.error("sound player binary not found: %s (sound disabled)", self._player)
            self._disabled = True
        except Exception as exc:  # noqa: BLE001 - never propagate into the loop
            logger.error("sound play failed: %s", exc)

    @staticmethod
    def _normalize_repeat(repeat_count: Any) -> int:
        """Play count as a non-negative int; anything unusable means 0 (endless)."""
        try:
            return max(0, int(repeat_count or 0))
        except (TypeError, ValueError):
            return 0

    def _start_adapter_managed_loop(
        self,
        path: str,
        cmd: List[str],
        env: dict,
        replay_gap: float,
        repeat: int = 0,
    ) -> None:
        self._loop_stop = threading.Event()
        stop_event = self._loop_stop
        with self._lock:
            self._current = path
            self._current_loop = True
            self._current_replay_gap = replay_gap
            self._current_repeat = repeat

        def run() -> None:
            plays = 0
            while not stop_event.is_set():
                proc: Optional[subprocess.Popen] = None
                try:
                    proc = subprocess.Popen(
                        cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    with self._lock:
                        self._proc = proc
                    proc.wait()
                except FileNotFoundError:
                    logger.error("sound player binary not found: %s (sound disabled)", self._player)
                    self._disabled = True
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.error("sound play failed: %s", exc)
                    break
                finally:
                    with self._lock:
                        if self._proc is proc:
                            self._proc = None
                plays += 1
                if repeat and plays >= repeat:
                    break
                if stop_event.wait(replay_gap):
                    break
            with self._lock:
                if self._loop_thread is threading.current_thread():
                    self._loop_thread = None
                    if self._proc is None:
                        self._current = None
                        self._current_loop = False
                        self._current_replay_gap = 0.0
                        self._current_repeat = 0

        self._loop_thread = threading.Thread(target=run, name="sound-replay-loop", daemon=True)
        self._loop_thread.start()

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            thread = self._loop_thread
            self._proc = None
            self._current = None
            self._current_loop = False
            self._current_replay_gap = 0.0
            self._current_repeat = 0
            self._loop_thread = None
            self._loop_stop.set()
        if proc is None or proc.poll() is not None:
            proc = None
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=self._player_wait_timeout)
                except Exception:
                    proc.kill()
            except Exception as exc:  # noqa: BLE001
                logger.error("sound stop failed: %s", exc)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._player_wait_timeout)

    def _pactl(self, *args: str) -> None:
        target = self._sink or "@DEFAULT_SINK@"
        try:
            subprocess.run(["pactl", *args[:1], target, *args[1:]], timeout=self._pactl_timeout)
        except Exception as exc:  # noqa: BLE001
            logger.error("pactl %s failed: %s", args, exc)

    def set_volume(self, percent: int) -> None:
        if self._disabled:
            return
        clamped = max(0, min(100, int(percent)))
        self._pactl("set-sink-volume", f"{clamped}%")

    def set_mute(self, muted: bool) -> None:
        if self._disabled:
            return
        self._pactl("set-sink-mute", "1" if muted else "0")

    def shutdown(self) -> None:
        """Stop playback and release. Process-exit is also covered by atexit."""
        self.stop()
