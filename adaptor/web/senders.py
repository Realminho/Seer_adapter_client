"""Command-send seam for the Control view.

The WebUi builds a VDA5050 instantActions payload and hands it to a sender.
Phase 1 uses :class:`MqttSender` (publishes to the broker); Phase 2 swaps in a
UDS sender (local Unix socket) without touching ``server.py``. ``send`` returns
``(delivered, message)`` where ``delivered`` means the command was accepted for
processing (per-action outcome is observed via state.instantActionStates).
"""

from __future__ import annotations

import json
import socket
from typing import Tuple


class MqttSender:
    def __init__(self, monitor) -> None:
        self._monitor = monitor

    def send(self, payload: dict, meta: dict) -> Tuple[bool, str]:
        ok = self._monitor.publish_json("instantActions", payload)
        return (ok, "delivered" if ok else "publish failed")


class UdsSender:
    """Send a command to the adapter's local control socket (fail-closed).

    Connects to /run/amr-adaptor/<serial>/control.sock, writes one JSON request
    ({instantActions, meta}) and reads the delivered-level reply. If the adapter
    is down the connect fails and the command is reported as not delivered — it
    is never queued to fire later.
    """

    def __init__(self, sock_path, timeout: float = 2.0) -> None:
        self._path = str(sock_path)
        self._timeout = timeout

    def send(self, payload: dict, meta: dict) -> Tuple[bool, str]:
        req = json.dumps({"instantActions": payload, "meta": meta}).encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self._timeout)
                s.connect(self._path)
                s.sendall(req)
                s.shutdown(socket.SHUT_WR)
                chunks = []
                while True:
                    b = s.recv(4096)
                    if not b:
                        break
                    chunks.append(b)
            data = json.loads(b"".join(chunks).decode("utf-8"))
            delivered = bool(data.get("delivered"))
            return delivered, data.get("error") or ("delivered" if delivered else "rejected")
        except (FileNotFoundError, ConnectionRefusedError):
            return False, "adapter offline — not delivered"
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"command failed: {exc}"
