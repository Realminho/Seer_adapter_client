"""One persistent asynchronous connection to a SEER API port."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional, Union

from .protocol import (
    ERROR_RESPONSE_TYPE,
    HEADER_SIZE,
    ApiPort,
    SeerApiError,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    pack_message,
    response_type_for,
    unpack_header,
)


class SeerPortConnection:
    """Serialize request/response traffic for one SEER TCP port."""

    def __init__(
        self,
        host: str,
        port: Union[ApiPort, int],
        *,
        command_timeout: float = 3.0,
        recv_chunk_bytes: int = 1024,
        recorder: Any = None,
        allow_legacy_echo_response: bool = True,
        protocol_version: int = PROTOCOL_VERSION,
        min_request_interval_sec: float = 0.0,
    ) -> None:
        self.host = host
        self.port = port
        self.command_timeout = command_timeout
        # Retained for constructor compatibility; readexactly is used on the wire.
        self.recv_chunk_bytes = recv_chunk_bytes
        self.recorder = recorder
        self.allow_legacy_echo_response = allow_legacy_echo_response
        self.protocol_version = int(protocol_version)
        if self.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ValueError(
                "SEER protocol_version must be 1 (RBK3.4) or 2 (RBK3.5)"
            )
        self.min_request_interval_sec = max(0.0, float(min_request_interval_sec))

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._running = False
        self._last_rx_at = 0.0
        self._last_tx_at = 0.0
        self._connected_at = 0.0
        self._last_latency_sec: Optional[float] = None
        self._last_error = ""
        self._error_count = 0
        self._reconnect_count = 0
        self._generation = 0
        self._req_id = 0
        self._request_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.is_connected():
            return
        self.reader, self.writer = await asyncio.open_connection(self.host, int(self.port))
        self._running = True
        now = time.monotonic()
        self._connected_at = now
        self._last_rx_at = now
        self._last_error = ""
        self._generation += 1
        self._record_event("port_connected", host=self.host, port=int(self.port))

    async def disconnect(self) -> None:
        self._running = False
        writer, self.writer = self.writer, None
        self.reader = None
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._record_event("port_disconnected", host=self.host, port=int(self.port))

    def is_connected(self) -> bool:
        return bool(
            self._running
            and self.reader is not None
            and self.writer is not None
            and not self.writer.is_closing()
        )

    def seconds_since_last_rx(self) -> float:
        if not self._last_rx_at:
            return float("inf")
        return time.monotonic() - self._last_rx_at

    def health_snapshot(self, *, stale_after: Optional[float] = None) -> dict[str, Any]:
        """Return a side-effect-free health snapshot for this logical port."""

        now = time.monotonic()
        last_rx_age = float("inf") if not self._last_rx_at else now - self._last_rx_at
        last_tx_age = float("inf") if not self._last_tx_at else now - self._last_tx_at
        stale = bool(
            stale_after is not None
            and stale_after > 0
            and last_rx_age > float(stale_after)
        )
        return {
            "host": self.host,
            "port": int(self.port),
            "connected": self.is_connected(),
            "generation": self._generation,
            "connected_for_sec": 0.0 if not self._connected_at else now - self._connected_at,
            "last_rx_age_sec": last_rx_age,
            "last_tx_age_sec": last_tx_age,
            "last_latency_sec": self._last_latency_sec,
            "error_count": self._error_count,
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error,
            "stale": stale,
        }

    async def request(
        self,
        msg_typ: int,
        body: Optional[Any] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send a request and validate the correlated response frame."""

        resolved = self.command_timeout if timeout is None else timeout
        async with self._request_lock:
            if not self.is_connected():
                # Each SEER logical port is independent.  Reconnect this port
                # on demand instead of escalating one stale auxiliary socket
                # into a vehicle-wide link loss.  No command is retried after
                # a write/read failure because delivery may already have
                # happened; only a request that has not yet been sent enters
                # through this preflight reconnect.
                await self._recover_connection()
                if not self.is_connected():
                    raise ConnectionError(f"SEER port {int(self.port)} not connected")
            try:
                await self._respect_request_interval()
                return await asyncio.wait_for(
                    self._request_once(int(msg_typ), body), timeout=resolved
                )
            except asyncio.CancelledError:
                # A cancelled read can leave its late response queued. Never
                # let the next caller consume that stale sequence.
                await self.disconnect()
                raise
            except SeerApiError as exc:
                # This is a fully correlated, valid response carrying an API
                # error. The stream remains safe for the next request.
                self._error_count += 1
                self._last_error = str(exc)
                raise
            except Exception as exc:
                self._error_count += 1
                self._last_error = str(exc)
                # Timeout, EOF, malformed JSON or sequence/type mismatch means
                # the request/response boundary is no longer trustworthy.
                # Reopen this one logical port before releasing the lock; do
                # not retry the command because it may have changed equipment.
                await self._recover_connection()
                raise

    async def _respect_request_interval(self) -> None:
        """Respect SEER's recommended per-port request spacing."""

        if self.min_request_interval_sec <= 0.0 or self._last_tx_at <= 0.0:
            return
        remaining = self.min_request_interval_sec - (time.monotonic() - self._last_tx_at)
        if remaining > 0.0:
            await asyncio.sleep(remaining)

    async def _recover_connection(self) -> None:
        """Discard any late frame and reopen a clean port connection."""

        await self.disconnect()
        try:
            await self.connect()
            self._reconnect_count += 1
            self._record_event(
                "port_recovered", host=self.host, port=int(self.port),
                reconnect_count=self._reconnect_count,
            )
        except Exception as exc:
            self._record_event(
                "port_recovery_failed",
                host=self.host,
                port=int(self.port),
                error=str(exc),
            )

    async def _request_once(self, msg_typ: int, body: Optional[Any]) -> Any:
        if self.writer is None or self.reader is None:
            raise ConnectionError(f"SEER port {int(self.port)} not connected")

        sequence = self._next_req_id()
        started_at = time.monotonic()
        request_frame = pack_message(
            sequence, msg_typ, body, protocol_version=self.protocol_version
        )
        self.writer.write(request_frame)
        await self.writer.drain()
        self._last_tx_at = time.monotonic()
        self._record_message("tx", sequence, request_frame, body or {}, msg_typ)

        header = await self.reader.readexactly(HEADER_SIZE)
        msg_len, response_sequence, response_type = unpack_header(header)
        raw_body = await self.reader.readexactly(msg_len) if msg_len else b""
        self._last_rx_at = time.monotonic()
        self._last_latency_sec = self._last_rx_at - started_at
        self._last_error = ""

        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Invalid SEER JSON response on port {int(self.port)}"
            ) from exc

        response_frame = header + raw_body
        self._record_message(
            "rx", response_sequence, response_frame, payload, response_type
        )

        if response_sequence != sequence:
            raise ValueError(
                f"SEER desync on port {int(self.port)}: response sequence "
                f"{response_sequence} != request {sequence}"
            )
        if response_type == ERROR_RESPONSE_TYPE:
            raise SeerApiError(payload, request_type=msg_typ, sequence=sequence)

        allowed_types = {response_type_for(msg_typ)}
        # The repository's existing JiBot-style fake server echoes the request
        # type. Keeping that mode enabled makes the same simulator harness usable.
        if self.allow_legacy_echo_response:
            allowed_types.add(msg_typ)
        if response_type not in allowed_types:
            expected = "/".join(str(item) for item in sorted(allowed_types))
            raise ValueError(
                f"SEER desync on port {int(self.port)}: response type "
                f"{response_type} != expected {expected}"
            )
        if isinstance(payload, dict) and "ret_code" in payload:
            try:
                ret_code = int(payload["ret_code"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid SEER ret_code on port {int(self.port)}: "
                    f"{payload.get('ret_code')!r}"
                ) from exc
            if ret_code != 0:
                raise SeerApiError(payload, request_type=msg_typ, sequence=sequence)
        return payload

    def _next_req_id(self) -> int:
        self._req_id = (self._req_id + 1) & 0xFFFF
        return self._req_id

    def _record_message(
        self, direction: str, sequence: int, raw: bytes, payload: Any, msg_type: int
    ) -> None:
        if self.recorder is None:
            return
        try:
            self.recorder.record_message(
                direction,
                sequence,
                raw.hex(),
                payload,
                command=str(msg_type),
                metadata={"port": int(self.port), "message_type": msg_type},
            )
        except Exception:
            # Recording must never interrupt robot communication.
            pass

    def _record_event(self, event: str, **fields: Any) -> None:
        if self.recorder is None:
            return
        try:
            self.recorder.record_event(event, **fields)
        except Exception:
            pass

