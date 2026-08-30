"""Pure runtime status formatting for the WebUi (shared backend)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.processes import ProcessMetrics
from core.systemd import ServiceMetrics

_SYSTEMD_AUTHORITATIVE_STATES = ("active", "activating", "deactivating", "reloading")


@dataclass(frozen=True)
class RuntimeStatus:
    source: str
    state_text: str
    pid: Optional[int]
    cpu_percent: Optional[float]
    memory_bytes: Optional[int]
    uptime_sec: Optional[float]
    restarts: Optional[int]


def compose_runtime_status(
    service: ServiceMetrics,
    process: Optional[ProcessMetrics],
) -> RuntimeStatus:
    if service.exists and service.active_state in _SYSTEMD_AUTHORITATIVE_STATES:
        return RuntimeStatus(
            source="systemd",
            state_text=service.active_state,
            pid=service.main_pid,
            cpu_percent=service.cpu_percent,
            memory_bytes=service.memory_bytes,
            uptime_sec=service.uptime_sec,
            restarts=service.restarts,
        )
    if process is not None:
        return RuntimeStatus(
            source="process",
            state_text="manual process",
            pid=process.pid,
            cpu_percent=process.cpu_percent,
            memory_bytes=process.memory_bytes,
            uptime_sec=process.uptime_sec,
            restarts=None,
        )
    if service.exists:
        return RuntimeStatus(
            source="systemd",
            state_text=service.active_state,
            pid=service.main_pid,
            cpu_percent=service.cpu_percent,
            memory_bytes=service.memory_bytes,
            uptime_sec=service.uptime_sec,
            restarts=service.restarts,
        )
    return RuntimeStatus(
        source="none",
        state_text="not installed",
        pid=None,
        cpu_percent=None,
        memory_bytes=None,
        uptime_sec=None,
        restarts=None,
    )
