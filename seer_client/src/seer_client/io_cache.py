"""Normalized SEER I/O snapshots shared by the adapter and WebUI."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Optional


class SeerIOCache:
    """Persist API 1013 data without exposing a live TCP client to the WebUI."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else None
        self.latest: Optional[dict[str, Any]] = None

    @staticmethod
    def _channels(payload: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
        raw = payload.get(name, payload.get(name.lower(), []))
        if not isinstance(raw, list):
            return []
        result = []
        for fallback_id, item in enumerate(raw):
            if isinstance(item, Mapping):
                try:
                    channel_id = int(item.get("id", fallback_id))
                except (TypeError, ValueError):
                    continue
                result.append(
                    {
                        "id": channel_id,
                        "source": str(item.get("source", "normal") or "normal"),
                        "status": bool(item.get("status", False)),
                        "valid": bool(item.get("valid", True)),
                    }
                )
            else:
                result.append(
                    {
                        "id": fallback_id,
                        "source": "normal",
                        "status": bool(item),
                        "valid": True,
                    }
                )
        return sorted(result, key=lambda channel: channel["id"])

    @classmethod
    def normalize(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return the stable representation consumed by the SEER IO page."""

        return {
            "updated_at": time.time(),
            "ret_code": payload.get("ret_code", 0),
            "err_msg": str(payload.get("err_msg", "") or ""),
            "DI": cls._channels(payload, "DI"),
            "DO": cls._channels(payload, "DO"),
        }

    def update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self.normalize(payload)
        self.latest = snapshot
        if self.path is not None:
            self._write(snapshot)
        return snapshot

    def _write(self, snapshot: Mapping[str, Any]) -> None:
        """Atomically write with short retries for Windows reader collisions."""

        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.tmp.{os.getpid()}"
        )
        data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        try:
            for attempt in range(12):
                temporary.write_text(data, encoding="utf-8")
                try:
                    os.replace(temporary, self.path)
                    return
                except PermissionError:
                    if attempt == 11:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def read(path: Path) -> Optional[dict[str, Any]]:
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None
