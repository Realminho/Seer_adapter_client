import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


SENSITIVE_KEYS = frozenset({"password", "passwd", "secret", "token", "api_key"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _redact(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                redacted[key] = "***"
            else:
                redacted[key] = _redact(item)
        return redacted

    if isinstance(value, list):
        return [_redact(item) for item in value]

    return value


class JibotRecorder:
    def __init__(self, file_path=None, metadata=None):
        self.session_id = uuid4().hex
        self.file_path = Path(file_path) if file_path else self._default_file_path()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.file_path.open("a", encoding="utf-8", buffering=1)
        self._metadata = dict(metadata or {})
        self.record_event("session_start", metadata=self._metadata)

    @classmethod
    def _default_file_path(cls):
        record_dir = Path(os.getenv("JIBOT_RECORD_DIR", "logs/jibot"))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return record_dir / f"{stamp}-session.jsonl"

    def record_event(self, event, **fields):
        entry = {
            "ts": _utc_now_iso(),
            "session_id": self.session_id,
            "event": event,
        }
        entry.update(fields)
        self._write(entry)

    def record_message(self, direction, sequence, raw, payload, command=None, metadata=None):
        entry = {
            "ts": _utc_now_iso(),
            "session_id": self.session_id,
            "event": "message",
            "direction": direction,
            "sequence": sequence,
            "command": command or self._command_from_payload(payload),
            "payload": _redact(payload),
            "raw": raw,
        }
        if metadata:
            entry["metadata"] = _redact(metadata)
        self._write(entry)

    def close(self):
        if self._file.closed:
            return

        self.record_event("session_end")
        self._file.close()

    def _write(self, entry):
        self._file.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _command_from_payload(payload):
        if isinstance(payload, dict):
            return payload.get("#CMD#")
        return None


def recorder_from_env(metadata=None):
    enabled = os.getenv("JIBOT_RECORD", "").lower() in TRUE_VALUES
    if not enabled:
        return None

    return JibotRecorder(
        file_path=os.getenv("JIBOT_RECORD_FILE") or None,
        metadata=metadata,
    )
