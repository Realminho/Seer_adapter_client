"""Local process discovery for adaptor instances launched outside systemd."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ProcessMetrics:
    pid: int
    ppid: int
    cpu_percent: float
    memory_bytes: int
    uptime_sec: Optional[float]
    args: str
    mqtt_host: Optional[str] = None
    mqtt_port: Optional[int] = None


def parse_elapsed_seconds(value: str) -> Optional[float]:
    text = value.strip()
    if not text or text == "-":
        return None
    days = 0
    if "-" in text:
        day_text, _, text = text.partition("-")
        try:
            days = int(day_text)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        seconds = nums[0]
    elif len(nums) == 2:
        minutes, seconds = nums
        seconds = minutes * 60 + seconds
    elif len(nums) == 3:
        hours, minutes, seconds = nums
        seconds = hours * 3600 + minutes * 60 + seconds
    else:
        return None
    return float(days * 86400 + seconds)


def _is_python_executable(arg: str) -> bool:
    name = Path(arg).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    suffix = name.removeprefix("python")
    return name == "python" or (
        name.startswith("python") and bool(suffix) and suffix.replace(".", "").isdigit()
    )


def _main_py_index(argv: List[str]) -> Optional[int]:
    if not argv:
        return None
    if Path(argv[0]).name == "main.py":
        return 0
    if not _is_python_executable(argv[0]):
        return None
    for index, arg in enumerate(argv[1:], start=1):
        if arg in ("-c", "-m"):
            return None
        if arg.startswith("-"):
            continue
        return index if Path(arg).name == "main.py" else None
    return None


def extract_serial(argv: List[str]) -> Optional[str]:
    script_index = _main_py_index(argv)
    if script_index is None:
        return None
    for index, arg in enumerate(argv[script_index + 1 :], start=script_index + 1):
        if arg in ("--id", "--serial-number", "--robot") and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--id="):
            return arg.partition("=")[2]
        if arg.startswith("--serial-number="):
            return arg.partition("=")[2]
        if arg.startswith("--robot="):
            return arg.partition("=")[2]
    return None


def _option_value(argv: List[str], *names: str) -> Optional[str]:
    for index, arg in enumerate(argv):
        if arg in names and index + 1 < len(argv):
            return argv[index + 1]
        for name in names:
            prefix = f"{name}="
            if arg.startswith(prefix):
                return arg.partition("=")[2]
    return None


def extract_mqtt_host(argv: List[str]) -> Optional[str]:
    return _option_value(argv, "--mqtt-host")


def extract_mqtt_port(argv: List[str]) -> Optional[int]:
    value = _option_value(argv, "--mqtt-port")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_ps(text: str) -> Dict[str, ProcessMetrics]:
    result: Dict[str, ProcessMetrics] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("PID "):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid_text, ppid_text, cpu_text, rss_text, elapsed_text, args = parts
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
            cpu = float(cpu_text)
            rss_kib = int(rss_text)
        except ValueError:
            continue
        try:
            argv = shlex.split(args)
        except ValueError:
            argv = args.split()
        serial = extract_serial(argv)
        if serial is None:
            continue
        result[serial] = ProcessMetrics(
            pid=pid,
            ppid=ppid,
            cpu_percent=cpu,
            memory_bytes=rss_kib * 1024,
            uptime_sec=parse_elapsed_seconds(elapsed_text),
            args=args,
            mqtt_host=extract_mqtt_host(argv),
            mqtt_port=extract_mqtt_port(argv),
        )
    return result


class ProcessScanner:
    def __init__(self, timeout: float = 3.0) -> None:
        self.timeout = timeout

    def poll(self) -> Dict[str, ProcessMetrics]:
        try:
            proc = subprocess.run(
                ["ps", "-eo", "pid,ppid,pcpu,rss,etime,args"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        if proc.returncode != 0:
            return {}
        return parse_ps(proc.stdout)
