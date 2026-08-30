"""systemd integration for the WebUi (shared backend).

The pure helpers (:func:`parse_show`, :func:`compute_cpu_percent`,
:func:`format_uptime`, :func:`format_bytes`, :func:`summarize`) are kept free
of subprocess/I/O so they can be unit tested without a running systemd.

:class:`SystemdController` wraps ``systemctl``/``journalctl`` and computes a
CPU% by sampling ``CPUUsageNSec`` across polls. Lifecycle verbs use
``sudo -n`` (non-interactive) so the WebUi never blocks on a password prompt;
the installer (``scripts/setup-adaptor-service.sh``) drops in a scoped sudoers
rule that makes those NOPASSWD.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# systemd reports "unknown/unset" numeric properties as this uint64 sentinel.
_UINT64_MAX = 18446744073709551615

SHOW_PROPERTIES = (
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "MainPID",
    "NRestarts",
    "MemoryCurrent",
    "CPUUsageNSec",
    "ExecMainStartTimestampMonotonic",
)


def parse_show(text: str) -> Dict[str, str]:
    """Parse ``systemctl show`` ``key=value`` lines into a dict."""
    result: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _as_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = value.strip()
    if value == "" or value in ("[not set]", "infinity"):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    if number == _UINT64_MAX:
        return None
    return number


def compute_cpu_percent(
    prev_nsec: Optional[int],
    prev_wall: Optional[float],
    cur_nsec: Optional[int],
    cur_wall: float,
) -> Optional[float]:
    """CPU% from two ``CPUUsageNSec`` samples and their wall-clock times.

    Returns ``None`` when a baseline is missing or the interval is degenerate.
    Clamped to ``[0, 100 * ncpu-ish]`` is intentionally NOT applied: a busy
    multi-threaded process can legitimately exceed 100%.
    """
    if prev_nsec is None or cur_nsec is None or prev_wall is None:
        return None
    wall_delta = cur_wall - prev_wall
    if wall_delta <= 0:
        return None
    cpu_delta_ns = cur_nsec - prev_nsec
    if cpu_delta_ns < 0:
        # Counter reset (service restarted) — no meaningful rate this tick.
        return None
    return (cpu_delta_ns / 1e9) / wall_delta * 100.0


def format_uptime(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "-"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_bytes(num: Optional[int]) -> str:
    if num is None:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


@dataclass
class ServiceMetrics:
    """A point-in-time snapshot of a unit's systemd state."""

    exists: bool = False
    load_state: str = "unknown"
    active_state: str = "unknown"
    sub_state: str = "unknown"
    enabled_state: str = "unknown"
    main_pid: Optional[int] = None
    restarts: Optional[int] = None
    memory_bytes: Optional[int] = None
    cpu_nsec: Optional[int] = None
    uptime_sec: Optional[float] = None
    cpu_percent: Optional[float] = None

    @property
    def is_active(self) -> bool:
        return self.active_state == "active"

    @property
    def is_failed(self) -> bool:
        return self.active_state == "failed" or self.sub_state == "failed"


@dataclass(frozen=True)
class EdgeAgentUnit:
    id: str
    unit: str


def parse_edge_agent_unit(name: str) -> Optional[EdgeAgentUnit]:
    unit = name.strip().split(None, 1)[0]
    if unit == "edge-agent.service":
        return EdgeAgentUnit(id="default", unit=unit)
    prefix = "edge-agent@"
    suffix = ".service"
    if not unit.startswith(prefix) or not unit.endswith(suffix):
        return None
    instance = unit[len(prefix) : -len(suffix)]
    if not instance:
        return None
    return EdgeAgentUnit(id=instance, unit=unit)


def parse_edge_agent_units(text: str) -> List[EdgeAgentUnit]:
    result: List[EdgeAgentUnit] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        parsed = parse_edge_agent_unit(raw_line)
        if parsed is None or parsed.unit in seen:
            continue
        seen.add(parsed.unit)
        result.append(parsed)
    return result


def discover_edge_agent_units(timeout: float = 3.0) -> List[EdgeAgentUnit]:
    if shutil.which("systemctl") is None:
        return []
    commands = (
        [
            "systemctl",
            "list-units",
            "edge-agent@*.service",
            "edge-agent.service",
            "--all",
            "--no-legend",
            "--no-pager",
        ],
        [
            "systemctl",
            "list-unit-files",
            "edge-agent@*.service",
            "edge-agent.service",
            "--no-legend",
            "--no-pager",
        ],
    )
    result: List[EdgeAgentUnit] = []
    seen: set[str] = set()
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            continue
        for unit in parse_edge_agent_units(proc.stdout):
            if unit.unit in seen:
                continue
            seen.add(unit.unit)
            result.append(unit)
    return result


def summarize(
    props: Dict[str, str],
    now_monotonic: float,
    prev_cpu_nsec: Optional[int] = None,
    prev_cpu_wall: Optional[float] = None,
) -> ServiceMetrics:
    """Turn parsed ``systemctl show`` props into a :class:`ServiceMetrics`."""
    load_state = props.get("LoadState", "unknown") or "unknown"
    metrics = ServiceMetrics(
        exists=load_state not in ("not-found", "", "unknown"),
        load_state=load_state,
        active_state=props.get("ActiveState", "unknown") or "unknown",
        sub_state=props.get("SubState", "unknown") or "unknown",
        enabled_state=props.get("UnitFileState", "unknown") or "unknown",
        main_pid=_as_int(props.get("MainPID")) or None,
        restarts=_as_int(props.get("NRestarts")),
        memory_bytes=_as_int(props.get("MemoryCurrent")),
        cpu_nsec=_as_int(props.get("CPUUsageNSec")),
    )
    if metrics.main_pid == 0:
        metrics.main_pid = None

    start_mono_us = _as_int(props.get("ExecMainStartTimestampMonotonic"))
    if metrics.is_active and start_mono_us:
        # Monotonic timestamps are microseconds since boot.
        metrics.uptime_sec = max(0.0, now_monotonic - start_mono_us / 1e6)

    metrics.cpu_percent = compute_cpu_percent(
        prev_cpu_nsec, prev_cpu_wall, metrics.cpu_nsec, time.time()
    )
    return metrics


class SystemdController:
    """Thin wrapper over ``systemctl``/``journalctl`` for one unit."""

    # start/stop/restart enqueue a systemd job that blocks the systemctl client
    # until the unit settles. Heavy units (e.g. urobot's ROS stack) take longer
    # than our subprocess timeout, so the client would be killed and we'd report
    # a false "timed out" even though systemd finishes the job in the background.
    # --no-block returns as soon as the job is enqueued; the WebUi shows the
    # resulting state via its own polling. enable/disable are fast filesystem
    # ops whose synchronous result is useful, so they stay blocking.
    _NONBLOCKING_VERBS = frozenset({"start", "stop", "restart"})

    def __init__(self, unit: str, use_sudo: bool = True, timeout: float = 5.0) -> None:
        self.unit = unit
        self.use_sudo = use_sudo
        self.timeout = timeout
        self._prev_cpu_nsec: Optional[int] = None
        self._prev_cpu_wall: Optional[float] = None
        self.have_systemctl = shutil.which("systemctl") is not None

    # -- read paths (no sudo needed) --------------------------------------
    def poll(self) -> ServiceMetrics:
        if not self.have_systemctl:
            return ServiceMetrics(load_state="not-found")
        args = [
            "systemctl",
            "show",
            self.unit,
            "--no-pager",
            "--property=" + ",".join(SHOW_PROPERTIES),
        ]
        code, out, _ = self._run(args, sudo=False)
        if code != 0 and not out:
            return ServiceMetrics(load_state="not-found")
        props = parse_show(out)
        metrics = summarize(
            props,
            now_monotonic=time.monotonic(),
            prev_cpu_nsec=self._prev_cpu_nsec,
            prev_cpu_wall=self._prev_cpu_wall,
        )
        # Save baseline for the next CPU% sample. Reset on restart so the next
        # tick re-baselines instead of reporting a negative spike.
        if metrics.cpu_nsec is not None:
            if self._prev_cpu_nsec is not None and metrics.cpu_nsec < self._prev_cpu_nsec:
                self._prev_cpu_nsec = None
                self._prev_cpu_wall = None
            else:
                self._prev_cpu_nsec = metrics.cpu_nsec
                self._prev_cpu_wall = time.time()
        return metrics

    def journal_argv(self, lines: int = 300, follow: bool = True) -> List[str]:
        args = [
            "journalctl",
            "-u",
            self.unit,
            "-n",
            str(lines),
            "-o",
            "short-precise",
            "--no-pager",
        ]
        if follow:
            args.append("-f")
        return args

    # -- lifecycle verbs (sudo -n) ----------------------------------------
    def start(self) -> Tuple[bool, str]:
        return self._systemctl("start")

    def stop(self) -> Tuple[bool, str]:
        return self._systemctl("stop")

    def restart(self) -> Tuple[bool, str]:
        return self._systemctl("restart")

    def enable(self) -> Tuple[bool, str]:
        return self._systemctl("enable")

    def disable(self) -> Tuple[bool, str]:
        return self._systemctl("disable")

    def manual_command(self, verb: str) -> str:
        prefix = "sudo " if self.use_sudo else ""
        return f"{prefix}systemctl {verb} {self.unit}"

    def _systemctl(self, verb: str) -> Tuple[bool, str]:
        if not self.have_systemctl:
            return False, "systemctl not found on this host"
        args = ["systemctl", verb]
        if verb in self._NONBLOCKING_VERBS:
            args.append("--no-block")
        args.append(self.unit)
        code, out, err = self._run(args, sudo=self.use_sudo)
        if code == 0:
            return True, f"{verb} {self.unit}: ok"
        message = (err or out or "").strip()
        if "a password is required" in message or "sudo: a terminal" in message:
            message = (
                "needs privileges — run:\n  "
                + self.manual_command(verb)
                + "\n(or re-run scripts/setup-adaptor-service.sh to install the sudoers rule)"
            )
        return False, message or f"{verb} failed (exit {code})"

    def _run(self, args: List[str], sudo: bool) -> Tuple[int, str, str]:
        cmd = (["sudo", "-n"] + args) if sudo else args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {self.timeout}s"
        except FileNotFoundError as exc:
            return 127, "", str(exc)


class HostController:
    """Host-level power controls used by the WebUi."""

    def __init__(self, use_sudo: bool = True, timeout: float = 5.0) -> None:
        self.use_sudo = use_sudo
        self.timeout = timeout
        self.have_systemctl = shutil.which("systemctl") is not None

    def reboot(self) -> Tuple[bool, str]:
        if not self.have_systemctl:
            return False, "systemctl not found on this host"
        code, out, err = self._run(["systemctl", "reboot"], sudo=self.use_sudo)
        if code == 0:
            return True, "reboot requested"
        message = (err or out or "").strip()
        if "a password is required" in message or "sudo: a terminal" in message:
            message = (
                "needs privileges — run:\n  "
                + self.manual_command()
                + "\n(or re-run scripts/setup-adaptor-service.sh to install the polkit rule)"
            )
        return False, message or f"reboot failed (exit {code})"

    def manual_command(self) -> str:
        prefix = "sudo " if self.use_sudo else ""
        return f"{prefix}systemctl reboot"

    def _run(self, args: List[str], sudo: bool) -> Tuple[int, str, str]:
        cmd = (["sudo", "-n"] + args) if sudo else args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {self.timeout}s"
        except FileNotFoundError as exc:
            return 127, "", str(exc)
