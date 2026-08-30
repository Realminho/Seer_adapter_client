"""SEER control-port motion, relocation, and map operations."""

from __future__ import annotations

import re

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import SeerClient


class SeerControlService:
    """Build control-port requests independently of client lifecycle code."""

    def __init__(self, client: "SeerClient") -> None:
        self.client = client

    async def stop(self) -> Any:
        result = await self.client.send_command("stop")
        # 2000 stops open-loop velocity; 3003 also cancels an active path task.
        await self.client.navigation.cancel()
        self.client._vx = self.client._vy = self.client._w = 0.0
        self.client._status = "Stopped"
        return result

    async def drive(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        w: float = 0.0,
        *,
        steer: Any = None,
        duration_ms: Any = None,
    ) -> Any:
        body = {
            "vx": self.client.position_to_native(vx),
            "vy": self.client.position_to_native(vy),
            "w": self.client.angle_to_native(w),
        }
        if steer is not None:
            body["steer"] = steer
        if duration_ms is not None:
            body["duration"] = int(duration_ms)
        result = await self.client.send_command("motion", **body)
        self.client._vx, self.client._vy, self.client._w = (
            body["vx"],
            body["vy"],
            body["w"],
        )
        self.client._status = "Driving" if any((vx, vy, w)) else "Stopped"
        return result

    async def drive_native(
        self,
        *,
        vx_mps: float = 0.0,
        vy_mps: float = 0.0,
        w_rad_s: float = 0.0,
        duration_ms: Any = None,
    ) -> Any:
        """Send API-2010 values already expressed in SEER's native units."""

        body = {
            "vx": float(vx_mps),
            "vy": float(vy_mps),
            "w": float(w_rad_s),
        }
        if duration_ms is not None:
            duration = int(duration_ms)
            if not 0 <= duration <= 5000:
                raise ValueError("SEER open-loop duration must be in 0..5000 ms")
            body["duration"] = duration
        result = await self.client.send_command("motion", **body)
        self.client._vx, self.client._vy, self.client._w = (
            body["vx"],
            body["vy"],
            body["w"],
        )
        self.client._status = (
            "Driving" if any((vx_mps, vy_mps, w_rad_s)) else "Stopped"
        )
        return result

    async def relocate(
        self,
        *,
        auto: bool,
        x: float,
        y: float,
        angle: float,
        length: Any = None,
        home: Any = None,
    ) -> Any:
        body = {
            "isAuto": bool(auto),
            "x": self.client.position_to_native(x),
            "y": self.client.position_to_native(y),
            "angle": self.client.angle_to_native(angle),
        }
        if length is not None:
            body["length"] = length
        if home is not None:
            body["home"] = home
        return await self.client.send_command("reloc", **body)

    async def switch_map(self, map_name: str) -> Any:
        name = str(map_name).strip()
        if not name:
            raise ValueError("SEER map name must not be empty")
        if re.fullmatch(r"[0-9A-Za-z_-]+", name) is None:
            raise ValueError(
                "SEER map name may contain only letters, numbers, underscore and hyphen"
            )
        result = await self.client.send_command("load_map", map_name=name)
        self.client._current_map = name
        return result
