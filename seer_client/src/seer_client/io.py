"""SEER digital I/O and named motor control operations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from .io_cache import SeerIOCache

if TYPE_CHECKING:
    from .client import SeerClient


class SeerIOService:
    """Handle OTHER-port writes and STATE-port digital-input parsing."""

    def __init__(self, client: "SeerClient", motor_names: Sequence[str] = ()) -> None:
        self.client = client
        self.motor_names = tuple(str(name) for name in motor_names if str(name))
        cache_path = str(os.getenv("SEER_IO_CACHE_PATH", "")).strip()
        self.cache = SeerIOCache(Path(cache_path) if cache_path else None)

    async def query(self) -> Mapping[str, Any]:
        payload = await self.client.send_command("query_io")
        if not isinstance(payload, Mapping):
            return {}
        self.cache.update(payload)
        return payload

    async def set_do(self, do_id: int, status: bool) -> Any:
        return await self.client.send_command(
            "set_do", id=int(do_id), status=bool(status)
        )

    async def set_soft_emergency(self, status: bool) -> Any:
        """Set SEER software emergency output through OTHER API 6004."""

        enabled = bool(status)
        result = await self.client.send_command(
            "set_soft_emergency", status=enabled
        )
        self.client._soft_emergency = enabled
        self.client._emergency = bool(
            enabled
            or self.client._physical_emergency
            or self.client._driver_emergency
        )
        self.client.status_service._refresh_safety_cache()
        return result

    async def read_di(self, di_id: int) -> Optional[bool]:
        payload = await self.query()
        values = payload.get("DI", payload.get("di", payload.get("dis")))
        if not isinstance(values, list):
            return None

        target = int(di_id)
        # Current firmware: [{"id": 1, "source": ..., "status": true,
        # "valid": true}, ...]. Older integrations used a plain bool list.
        if values and isinstance(values[0], Mapping):
            for item in values:
                try:
                    matches = int(item.get("id")) == target
                except (TypeError, ValueError):
                    matches = False
                if not matches:
                    continue
                if item.get("valid") is False:
                    return None
                return bool(item.get("status"))
            return None
        if 0 <= target < len(values):
            return bool(values[target])
        return None

    async def set_motor(self, motor_name: str, enable: bool) -> Any:
        name = str(motor_name)
        if not name:
            raise ValueError("SEER motor_name must not be empty")
        result = await self.client.send_command(
            "set_motor", motor_name=name, enable=bool(enable)
        )
        self.client._motor_flag = bool(enable)
        return result

    async def set_all_motors(self, enable: bool) -> None:
        if not self.motor_names:
            raise RuntimeError(
                "SEER motor control requires exact vendor motor_names; pass "
                "motor_names=(...) when constructing SeerClient"
            )
        for motor_name in self.motor_names:
            await self.set_motor(motor_name, enable)
        self.client._motor_flag = bool(enable)
