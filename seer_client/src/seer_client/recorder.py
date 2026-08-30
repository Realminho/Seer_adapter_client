"""Optional append-only JSONL recorder for SEER traffic."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

SENSITIVE_KEYS = frozenset({"password", "passwd", "secret", "token", "api_key"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
SEER_CLIENT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECORD_DIR = SEER_CLIENT_ROOT / "runtime" / "records"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if str(key).lower() in SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class SeerRecorder:
    """Write lifecycle events and protocol messages in JiBot-compatible JSONL."""

    def __init__(
        self, file_path: Optional[str] = None, metadata: Optional[dict] = None
    ) -> None:
        self.session_id = uuid4().hex
        self.file_path = Path(file_path) if file_path else self._default_file_path()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.file_path.open("a", encoding="utf-8", buffering=1)
        self._metadata = dict(metadata or {})
        self.record_event("session_start", metadata=_redact(self._metadata))

    @classmethod
    def _default_file_path(cls) -> Path:
        record_dir = Path(os.getenv("SEER_RECORD_DIR", str(DEFAULT_RECORD_DIR)))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return record_dir / f"{stamp}-session.jsonl"

    def record_message(
        self,
        direction: str,
        sequence: int,
        raw: Any,
        payload: Any,
        **fields: Any,
    ) -> None:
        entry = {
            "ts": _utc_now_iso(),
            "session_id": self.session_id,
            "event": "message",
            "direction": direction,
            "sequence": sequence,
            "payload": _redact(payload),
            "raw": raw,
        }
        entry.update(_redact(fields))
        self._write(entry)

    def record_event(self, event: str, **fields: Any) -> None:
        entry = {
            "ts": _utc_now_iso(),
            "session_id": self.session_id,
            "event": event,
        }
        entry.update(_redact(fields))
        self._write(entry)

    def close(self) -> None:
        if self._file.closed:
            return
        self.record_event("session_end")
        self._file.close()

    def _write(self, entry: dict) -> None:
        self._file.write(
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        )


def recorder_from_env(metadata: Optional[dict] = None) -> Optional[SeerRecorder]:
    """Create a recorder only when ``SEER_RECORD`` is enabled."""

    if os.getenv("SEER_RECORD", "").lower() not in TRUE_VALUES:
        return None
    return SeerRecorder(
        file_path=os.getenv("SEER_RECORD_FILE") or None,
        metadata=metadata,
    )

