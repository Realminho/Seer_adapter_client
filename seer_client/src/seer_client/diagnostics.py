"""Fail-fast validation for SEER runtime settings."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from .drive_limits import ManualDriveLimits
from .protocol import ApiPort, SUPPORTED_PROTOCOL_VERSIONS


class SeerConfigurationError(ValueError):
    """A configuration is unsafe or cannot be interpreted unambiguously."""


@dataclass(frozen=True)
class StartupDiagnostics:
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return True


def _finite_positive(name: str, value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise SeerConfigurationError(f"{name} must be a positive finite number")
    return numeric


def validate_runtime_settings(
    *,
    port_map: Mapping[ApiPort, int],
    protocol_version: int,
    command_timeout: float,
    recv_chunk_bytes: int,
    status_poll_interval_sec: float,
    min_request_interval_sec: float,
    navigation_timeout_sec: float = 300.0,
    motor_names: Sequence[str] = (),
    manual_drive_limits: Optional[ManualDriveLimits] = None,
) -> StartupDiagnostics:
    """Validate values before a SEER TCP connection is attempted."""

    if int(protocol_version) not in SUPPORTED_PROTOCOL_VERSIONS:
        raise SeerConfigurationError(
            "SEER_PROTOCOL_VERSION must be 1 (RBK3.4) or 2 (RBK3.5)"
        )
    _finite_positive("SEER_COMMAND_TIMEOUT_SEC", command_timeout)
    _finite_positive("SEER_STATUS_POLL_INTERVAL_SEC", status_poll_interval_sec)
    _finite_positive("SEER_NAVIGATION_TIMEOUT_SEC", navigation_timeout_sec)
    if not math.isfinite(float(min_request_interval_sec)) or float(min_request_interval_sec) < 0:
        raise SeerConfigurationError(
            "SEER_MIN_REQUEST_INTERVAL_SEC must be a non-negative finite number"
        )
    if int(recv_chunk_bytes) <= 0:
        raise SeerConfigurationError("SEER_RECV_BUFFER_BYTES must be greater than zero")

    normalized_ports: list[int] = []
    for logical_port in (
        ApiPort.STATE,
        ApiPort.CONTROL,
        ApiPort.TASK,
        ApiPort.CONFIG,
        ApiPort.OTHER,
    ):
        value = int(port_map.get(logical_port, int(logical_port)))
        if not 1 <= value <= 65535:
            raise SeerConfigurationError(
                f"{logical_port.name} TCP port must be between 1 and 65535"
            )
        normalized_ports.append(value)
    if len(set(normalized_ports)) != len(normalized_ports):
        raise SeerConfigurationError(
            "SEER STATE/CONTROL/TASK/CONFIG/OTHER TCP ports must be distinct"
        )

    cleaned_names = [str(item).strip() for item in motor_names]
    if any(not item for item in cleaned_names):
        raise SeerConfigurationError("SEER_MOTOR_NAMES must not contain empty names")
    if len(set(cleaned_names)) != len(cleaned_names):
        raise SeerConfigurationError("SEER_MOTOR_NAMES must not contain duplicates")

    if manual_drive_limits is not None:
        try:
            manual_drive_limits.validated()
        except ValueError as exc:
            raise SeerConfigurationError(str(exc)) from exc

    warnings: list[str] = []
    if 0 < float(min_request_interval_sec) > float(status_poll_interval_sec):
        warnings.append(
            "SEER_STATUS_POLL_INTERVAL_SEC is shorter than the per-port request "
            "interval; effective polling will be rate-limited"
        )
    if not cleaned_names:
        warnings.append(
            "SEER_MOTOR_NAMES is empty; enableMotor/disableMotor cannot control named motors"
        )
    return StartupDiagnostics(tuple(warnings))
