"""Command watchdog used before any real robot transport."""

from __future__ import annotations

from dataclasses import dataclass
import math
import time

from .model import DockParams, DockState, VelocityCommand, clamp


@dataclass(slots=True)
class SafetyGate:
    params: DockParams
    tag_timeout_s: float = 0.40
    obstacle_stop_m: float = 0.25
    allow_reverse_recovery: bool = False
    max_reverse_mps: float = 0.07
    last_tag_time: float = 0.0

    def mark_tag(self, now: float | None = None) -> None:
        self.last_tag_time = time.monotonic() if now is None else float(now)

    def filter(
        self,
        command: VelocityCommand,
        state: DockState,
        lidar_clearance_m: float = math.inf,
        emergency_stop: bool = False,
        now: float | None = None,
    ) -> VelocityCommand:
        current = time.monotonic() if now is None else float(now)
        if emergency_stop or current - self.last_tag_time > self.tag_timeout_s:
            return VelocityCommand(0.0, 0.0)
        if lidar_clearance_m < self.obstacle_stop_m and command.v > 0.0:
            return VelocityCommand(0.0, 0.0)
        if command.v < 0.0 and not self.allow_reverse_recovery:
            return VelocityCommand(0.0, 0.0)
        # Speed falls linearly inside the final 0.35 m before goal standoff.
        remaining = max(0.0, state.x - self.params.goal_standoff_m)
        speed_cap = min(self.params.max_linear_mps, 0.02 + 0.35 * remaining)
        return VelocityCommand(
            clamp(command.v, -max(0.0, self.max_reverse_mps), speed_cap),
            clamp(command.omega, -self.params.max_angular_rps, self.params.max_angular_rps),
        )
