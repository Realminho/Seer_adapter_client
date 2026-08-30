"""SEER Robokit TCP frame definitions and API numbers."""

from __future__ import annotations

import json
import struct
from enum import IntEnum
from typing import Any, Optional, Tuple

SYNC_BYTE = 0x5A
PROTOCOL_VERSION = 1
SUPPORTED_PROTOCOL_VERSIONS = (1, 2)
HEADER_FMT = "!BBHLH6s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
RESPONSE_OFFSET = 10000
ERROR_RESPONSE_TYPE = 60000
_RESERVED = b"\x00" * 6


class ApiPort(IntEnum):
    """TCP ports assigned to the SEER API groups."""

    ROBOD = 19200
    STATE = 19204
    CONTROL = 19205
    TASK = 19206
    CONFIG = 19207
    KERNEL = 19208
    OTHER = 19210


class ApiNumber(IntEnum):
    """Request message types used by this client."""

    STATUS_INFO = 1000
    STATUS_RUN = 1002
    STATUS_MODE = 1003
    STATUS_LOC = 1004
    STATUS_SPEED = 1005
    STATUS_BLOCK = 1006
    STATUS_BATTERY = 1007
    STATUS_LASER = 1009
    STATUS_AREA = 1011
    STATUS_EMERGENCY = 1012
    STATUS_IO = 1013
    STATUS_IMU = 1014
    STATUS_ENCODER = 1018
    STATUS_TASK = 1020
    STATUS_JACK = 1027
    STATUS_ALARM = 1050
    STATUS_ALL1 = 1100
    STATUS_ALL2 = 1101
    STATUS_ALL3 = 1102
    STATUS_MAP = 1300
    STATUS_PARAMS = 1400
    STATUS_MODEL = 1500

    CONTROL_STOP = 2000
    CONTROL_RELOC = 2002
    CONTROL_MOTION = 2010
    CONTROL_LOADMAP = 2022

    CONFIG_DOWNLOAD_MAP = 4011

    TASK_PAUSE = 3001
    TASK_RESUME = 3002
    TASK_CANCEL = 3003
    TASK_FREE_GOTO = 3050
    TASK_GOTARGET = 3051
    TASK_TRANSLATE = 3055
    TASK_TURN = 3056
    TASK_GOTARGET_LIST = 3066

    OTHER_SETDO = 6001
    OTHER_SOFT_EMERGENCY = 6004
    OTHER_SET_MOTOR = 6201


class SeerApiError(RuntimeError):
    """API-level failure returned by the robot.

    SEER may report a failure either with response type 60000 or with a normal
    response type whose JSON body contains a non-zero ``ret_code``.
    """

    def __init__(self, payload: Any, *, request_type: int, sequence: int) -> None:
        self.payload = payload
        self.request_type = request_type
        self.sequence = sequence
        super().__init__(
            f"SEER API {request_type} failed (sequence={sequence}): {payload!r}"
        )


def response_type_for(request_type: int) -> int:
    """Return the normal response type for a request type."""

    return int(request_type) + RESPONSE_OFFSET


def pack_message(
    req_id: int,
    msg_typ: int,
    body: Optional[Any] = None,
    *,
    protocol_version: int = PROTOCOL_VERSION,
) -> bytes:
    """Serialize a 16-byte SEER header and optional compact UTF-8 JSON body."""

    if not 0 <= int(req_id) <= 0xFFFF:
        raise ValueError(f"SEER sequence must be uint16, got {req_id!r}")
    if not 0 <= int(msg_typ) <= 0xFFFF:
        raise ValueError(f"SEER message type must be uint16, got {msg_typ!r}")
    if int(protocol_version) not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ValueError(
            "SEER protocol_version must be 1 (RBK3.4) or 2 (RBK3.5), "
            f"got {protocol_version!r}"
        )

    body_bytes = (
        b""
        if not body
        else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return struct.pack(
        HEADER_FMT,
        SYNC_BYTE,
        int(protocol_version),
        int(req_id),
        len(body_bytes),
        int(msg_typ),
        _RESERVED,
    ) + body_bytes


def unpack_header(
    data: bytes, *, accepted_versions: Tuple[int, ...] = SUPPORTED_PROTOCOL_VERSIONS
) -> Tuple[int, int, int]:
    """Parse a SEER header into ``(body_length, sequence, message_type)``."""

    if len(data) < HEADER_SIZE:
        raise ValueError(f"SEER header needs {HEADER_SIZE} bytes, got {len(data)}")
    sync, version, req_id, msg_len, msg_typ, _ = struct.unpack(
        HEADER_FMT, data[:HEADER_SIZE]
    )
    if sync != SYNC_BYTE:
        raise ValueError(
            f"SEER frame desync: sync byte 0x{sync:02X} != 0x{SYNC_BYTE:02X}"
        )
    if version not in accepted_versions:
        expected = ", ".join(str(item) for item in accepted_versions)
        raise ValueError(
            f"Unsupported SEER protocol version {version}; expected one of {expected}"
        )
    return msg_len, req_id, msg_typ
