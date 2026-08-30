"""SEER RBK NetProtocol TCP client with conservative live-motion locks.

The 16-byte header and request/response message-number relationship in this
module follow the Robokit TCP/IP API document supplied with this project.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import socket
import struct
import threading
import time
from typing import Any

from .model import VelocityCommand


PORTS = {
    "state": 19204,
    "control": 19205,
    "navigation": 19206,
    "config": 19207,
    "peripheral": 19210,
}
HEADER = struct.Struct("!BBHIH6s")
SUPPORTED_VERSIONS = (0x01, 0x02)


class RbkApiError(RuntimeError):
    """The controller returned a non-zero ``ret_code``."""

    def __init__(self, api: int, response: dict[str, Any]) -> None:
        self.api = int(api)
        self.response = response
        super().__init__(
            f"RBK API {api} failed: ret_code={response.get('ret_code')} "
            f"err_msg={response.get('err_msg', response.get('message', ''))!r}"
        )


def port_for_api(api: int) -> int:
    if 1000 <= api <= 1999:
        return PORTS["state"]
    if 2000 <= api <= 2999:
        return PORTS["control"]
    if 3000 <= api <= 3999:
        return PORTS["navigation"]
    if 4000 <= api <= 5999:
        return PORTS["config"]
    if 6000 <= api <= 6998:
        return PORTS["peripheral"]
    raise ValueError(f"unsupported RBK API number: {api}")


def expected_response_api(api: int) -> int:
    response_api = int(api) + 10000
    if response_api > 0xFFFF:
        raise ValueError(f"RBK response API does not fit uint16: {response_api}")
    return response_api


def build_frame(
    api: int,
    body: dict[str, Any] | None = None,
    number: int = 0,
    version: int = 0x01,
) -> bytes:
    if int(version) not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported RBK protocol version: {version}")
    payload = b"" if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    # The ordinary TCP request reserves the last six header bytes as all-zero.
    return HEADER.pack(0x5A, int(version), number & 0xFFFF, len(payload), api, b"\x00" * 6) + payload


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise ConnectionError("RBK connection closed before full response")
        chunks.extend(part)
    return bytes(chunks)


class RbkClient:
    def __init__(
        self,
        host: str,
        timeout_s: float = 1.0,
        protocol_version: int = 0x01,
        min_request_interval_s: float = 0.10,
        persistent: bool = False,
    ) -> None:
        self.host = host
        self.timeout_s = float(timeout_s)
        self.protocol_version = int(protocol_version)
        if self.protocol_version not in SUPPORTED_VERSIONS:
            raise ValueError(f"unsupported RBK protocol version: {protocol_version}")
        self.min_request_interval_s = max(0.0, float(min_request_interval_s))
        self.persistent = bool(persistent)
        self._number = 0
        self._lock = threading.Lock()
        self._last_request_by_port: dict[int, float] = {}
        self._sockets_by_port: dict[int, socket.socket] = {}

    def _close_socket_locked(self, port: int) -> None:
        sock = self._sockets_by_port.pop(int(port), None)
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def close(self) -> None:
        """Close any persistent RBK TCP sessions held by this client."""
        with self._lock:
            for port in list(self._sockets_by_port):
                self._close_socket_locked(port)

    def _connected_socket_locked(self, port: int) -> socket.socket:
        sock = self._sockets_by_port.get(int(port))
        if sock is not None:
            return sock
        sock = socket.create_connection((self.host, int(port)), timeout=self.timeout_s)
        sock.settimeout(self.timeout_s)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self._sockets_by_port[int(port)] = sock
        return sock

    def request_payload(self, api: int, body: dict[str, Any] | None = None) -> bytes:
        """Return the response data area without assuming it contains JSON.

        Most RBK APIs return a JSON object, while some status APIs use binary
        payloads. Keeping framing and response validation in one place avoids
        subtly different TCP implementations.
        """

        with self._lock:
            self._number = (self._number + 1) & 0xFFFF
            port = port_for_api(api)
            since_last = time.monotonic() - self._last_request_by_port.get(port, -1e9)
            if since_last < self.min_request_interval_s:
                time.sleep(self.min_request_interval_s - since_last)
            frame = build_frame(api, body, self._number, self.protocol_version)
            self._last_request_by_port[port] = time.monotonic()

            # RoboKit documents port 19205 (control) as a *single-connection*
            # service and keeps established TCP sessions alive.  Opening a new
            # socket for every 2010/2000 request can therefore race the server's
            # cleanup of the preceding connection and cause connect() timeouts.
            # LIVE clients use one persistent request/response session per port.
            if self.persistent:
                sock = self._connected_socket_locked(port)
                try:
                    sock.sendall(frame)
                    raw_header = _recv_exact(sock, HEADER.size)
                    magic, version, number, length, response_api, _ = HEADER.unpack(raw_header)
                    if magic != 0x5A or version != self.protocol_version:
                        raise ValueError("invalid RBK response header")
                    if response_api != expected_response_api(api) or number != self._number:
                        raise ValueError(
                            "RBK response does not match request: "
                            f"serial={number}/{self._number}, type={response_api}/{expected_response_api(api)}"
                        )
                    return _recv_exact(sock, length) if length else b""
                except Exception:
                    # A broken/half-closed persistent session must never be
                    # reused. The next request may establish a fresh session.
                    self._close_socket_locked(port)
                    raise

            with socket.create_connection((self.host, port), timeout=self.timeout_s) as sock:
                sock.settimeout(self.timeout_s)
                sock.sendall(frame)
                raw_header = _recv_exact(sock, HEADER.size)
                magic, version, number, length, response_api, _ = HEADER.unpack(raw_header)
                if magic != 0x5A or version != self.protocol_version:
                    raise ValueError("invalid RBK response header")
                if response_api != expected_response_api(api) or number != self._number:
                    raise ValueError(
                        "RBK response does not match request: "
                        f"serial={number}/{self._number}, type={response_api}/{expected_response_api(api)}"
                    )
                return _recv_exact(sock, length) if length else b""

    def request(self, api: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.request_payload(api, body) or b"{}"
        response = json.loads(payload.decode("utf-8"))
        if not isinstance(response, dict):
            raise ValueError(f"RBK API {api} response is not a JSON object")
        if "ret_code" in response and int(response["ret_code"]) != 0:
            raise RbkApiError(api, response)
        return response


@dataclass(slots=True)
class LiveVelocitySmoother:
    """Rate-limit SEER velocity targets before API 2010 transmission.

    The visual controller is allowed to change its desired velocity immediately,
    while the physical command sent to the AMR follows bounded acceleration and
    deceleration.  Sign reversals pass through zero first.  When a phase asks for
    an in-place turn or a straight leg, the other axis is brought near zero before
    the new motion is released.  Emergency stops bypass this class entirely.
    """

    max_linear_accel_mps2: float = 0.15
    max_linear_decel_mps2: float = 0.25
    max_angular_accel_rps2: float = 0.30
    max_angular_decel_rps2: float = 0.50
    linear_zero_epsilon_mps: float = 0.003
    angular_zero_epsilon_rps: float = 0.008
    max_dt_s: float = 0.20
    phase_interlock: bool = True
    current_v: float = 0.0
    current_omega: float = 0.0

    @staticmethod
    def _bounded_step(
        target: float,
        current: float,
        accel_rate: float,
        decel_rate: float,
        dt: float,
        zero_epsilon: float,
    ) -> float:
        target = float(target)
        current = float(current)
        if not math.isfinite(target) or not math.isfinite(current):
            raise ValueError("velocity smoother inputs must be finite")

        # Never jump directly through zero.  Forward/reverse and CW/CCW
        # reversals first decelerate to a true stop, then accelerate away.
        effective_target = target
        if current * target < 0.0 and abs(current) > zero_epsilon:
            effective_target = 0.0

        slowing = (
            abs(effective_target) < abs(current)
            or effective_target == 0.0
            or current * effective_target < 0.0
        )
        rate = max(0.0, float(decel_rate if slowing else accel_rate))
        max_delta = rate * max(0.0, float(dt))
        delta = max(-max_delta, min(max_delta, effective_target - current))
        value = current + delta

        # Snap tiny residuals only when the requested axis is zero; this avoids
        # an endless millimetre-per-second crawl before a deliberate phase turn.
        if effective_target == 0.0 and abs(value) <= zero_epsilon:
            value = 0.0
        return float(value)

    def reset(self, v: float = 0.0, omega: float = 0.0) -> None:
        self.current_v = float(v)
        self.current_omega = float(omega)

    @property
    def stationary(self) -> bool:
        return (
            abs(self.current_v) <= self.linear_zero_epsilon_mps
            and abs(self.current_omega) <= self.angular_zero_epsilon_rps
        )

    def apply(self, target: VelocityCommand, dt: float) -> VelocityCommand:
        if not math.isfinite(target.v) or not math.isfinite(target.omega):
            raise ValueError("velocity smoother target must be finite")
        dt = min(max(float(dt), 0.0), max(0.0, float(self.max_dt_s)))

        target_v = float(target.v)
        target_w = float(target.omega)

        if self.phase_interlock:
            # In-place turn request: finish linear deceleration first, then turn.
            if (
                abs(target_v) <= self.linear_zero_epsilon_mps
                and abs(target_w) > self.angular_zero_epsilon_rps
                and abs(self.current_v) > self.linear_zero_epsilon_mps
            ):
                target_w = 0.0

            # Straight request: finish angular deceleration first, then translate.
            if (
                abs(target_w) <= self.angular_zero_epsilon_rps
                and abs(target_v) > self.linear_zero_epsilon_mps
                and abs(self.current_omega) > self.angular_zero_epsilon_rps
            ):
                target_v = 0.0

        next_v = self._bounded_step(
            target_v,
            self.current_v,
            self.max_linear_accel_mps2,
            self.max_linear_decel_mps2,
            dt,
            self.linear_zero_epsilon_mps,
        )
        next_w = self._bounded_step(
            target_w,
            self.current_omega,
            self.max_angular_accel_rps2,
            self.max_angular_decel_rps2,
            dt,
            self.angular_zero_epsilon_rps,
        )
        self.current_v = next_v
        self.current_omega = next_w
        return VelocityCommand(next_v, next_w)


@dataclass(slots=True)
class SeerMotionBridge:
    """API 2010 open-loop command bridge; disabled unless explicitly armed."""

    client: RbkClient
    armed: bool = False
    motion_api: int = 2010
    stop_api: int = 2000
    linear_field: str = "vx"
    lateral_field: str = "vy"
    angular_field: str = "w"
    duration_ms: int = 250

    def send(self, command: VelocityCommand) -> dict[str, Any]:
        if not math.isfinite(command.v) or not math.isfinite(command.omega):
            raise ValueError("motion command must be finite")
        body = {
            self.linear_field: float(command.v),
            self.lateral_field: 0.0,
            self.angular_field: float(command.omega),
            "duration": max(1, int(self.duration_ms)),
        }
        if not self.armed:
            return {"dry_run": True, "api": self.motion_api, "body": body}
        return self.client.request(self.motion_api, body)

    def stop(self) -> dict[str, Any]:
        if not self.armed:
            return {"dry_run": True, "stopped": True}
        return self.client.request(self.stop_api, None)

    def safe_stop(
        self,
        *,
        retries: int = 1,
        retry_delay_s: float = 0.05,
        fallback_duration_ms: int = 100,
    ) -> dict[str, Any]:
        """Best-effort fail-closed stop that never raises to the control loop.

        API 2000 is attempted first.  If the SEER control socket cannot accept
        that request, fall back to an API 2010 zero-velocity command with a
        short duration.  The caller must still keep all future non-zero motion
        latched off until status confirms the robot is safe/stationary.
        """
        if not self.armed:
            return {"dry_run": True, "stopped": True, "method": "dry-run"}

        errors: list[str] = []
        for attempt in range(1, max(1, int(retries)) + 1):
            try:
                response = self.client.request(self.stop_api, None)
                return {"stopped": True, "method": "api2000", "response": response, "attempt": attempt}
            except Exception as exc:
                errors.append(f"API {self.stop_api} attempt {attempt}: {type(exc).__name__}: {exc}")
                if attempt < max(1, int(retries)):
                    time.sleep(max(0.0, float(retry_delay_s)))

        zero_body = {
            self.linear_field: 0.0,
            self.lateral_field: 0.0,
            self.angular_field: 0.0,
            "duration": max(1, min(int(self.duration_ms), int(fallback_duration_ms))),
        }
        try:
            response = self.client.request(self.motion_api, zero_body)
            return {
                "stopped": True,
                "method": "api2010-zero-fallback",
                "response": response,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(f"API {self.motion_api} zero fallback: {type(exc).__name__}: {exc}")
            return {
                "stopped": False,
                "method": "watchdog-only",
                "errors": errors,
                "watchdog_duration_ms": int(self.duration_ms),
            }
