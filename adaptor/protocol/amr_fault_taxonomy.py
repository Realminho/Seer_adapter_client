"""Vendor-neutral AMR fault vocabulary published on the VDA5050 wire.

FMS 로 나가는 벤더 중립 AMR 오류 어휘.

Why this exists
---------------
VDA5050 leaves ``error.errorType`` as a free string, so every vendor invents its
own. This adaptor grew a JIBOT-flavoured set (``JIBOT_LOCALIZATION_LOST``,
``JIBOT_MOTOR_FAULT`` …) and the FMS started branching on those raw strings.
That couples fleet control to one robot brand: the day a SEER or Hexplorer robot
reports the same physical condition under a different name, every FMS gate that
learned the JIBOT spelling goes silent — a failure that shows up as "nothing
happened", not as an error.

VDA5050 는 ``error.errorType`` 을 자유 문자열로 두기 때문에 벤더마다 어휘가 다르다.
이 어댑터는 JIBOT 계열 이름(``JIBOT_LOCALIZATION_LOST`` 등)을 써 왔고 FMS 가 그
원문 문자열로 분기하게 되었다. 그러면 교통 제어가 특정 기종에 묶인다 — 다른 벤더가
같은 물리 상황을 다른 이름으로 보고하는 순간, JIBOT 철자를 학습한 FMS 게이트가 전부
무음이 된다. 이 실패는 오류가 아니라 "아무 일도 안 일어남"으로 나타나 발견이 늦다.

The contract
------------
Adapters keep raising their own vendor-specific ``ErrorType`` members — that is
where the diagnosis lives. Normalization happens once, at the wire boundary
(:meth:`Error.to_dict`), so:

- ``errorType`` carries the **common** code. FMS branches on this and only this.
- ``errorReferences`` gains ``vendorErrorType`` (and ``vendorName``) holding the
  original, so operators and logs keep the precise vendor diagnosis.

어댑터는 계속 자기 벤더 ``ErrorType`` 을 raise 한다(진단 정보는 거기 있다).
정규화는 wire 경계(:meth:`Error.to_dict`) 한 곳에서만 일어난다:

- ``errorType`` 에는 **공통 코드**가 실린다. FMS 는 이것만 보고 분기한다.
- ``errorReferences`` 에 ``vendorErrorType``/``vendorName`` 이 추가되어 원문이
  보존된다. 운영자 진단과 로그는 정밀도를 잃지 않는다.

Adding a vendor
---------------
A new vendor adapter declares its own ``ErrorType`` members and adds one row per
member to :data:`VENDOR_ERROR_ALIASES`. Nothing else changes: the wire, the FMS
gates and the operator messages all keep working.
새 벤더는 자기 ``ErrorType`` 을 선언하고 :data:`VENDOR_ERROR_ALIASES` 에 한 줄씩
추가하면 된다. wire·FMS 게이트·운영자 메시지는 그대로 동작한다.

This module imports stdlib only — no project-internal imports — so any vendor
client can depend on it without creating a cycle.
stdlib 외 프로젝트 내부 import 를 두지 않는다. 어떤 벤더 클라이언트도 순환 없이
의존할 수 있게 하기 위함이다.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Tuple

#: ``errorReferences`` key holding the original vendor ``errorType``.
#: 원본 벤더 ``errorType`` 을 담는 ``errorReferences`` 키.
VENDOR_ERROR_TYPE_REFERENCE_KEY = "vendorErrorType"

#: ``errorReferences`` key holding the vendor/robot family name.
#: 벤더(기종 계열) 이름을 담는 ``errorReferences`` 키.
VENDOR_NAME_REFERENCE_KEY = "vendorName"


class CommonErrorType(str, Enum):
    """Vendor-neutral error vocabulary. FMS branches on these values only.

    벤더 중립 오류 어휘. FMS 는 이 값들로만 분기한다.

    Names describe the *condition*, never the robot brand, so a second vendor
    reporting the same physical situation maps onto the same member.
    이름은 로봇 기종이 아니라 *상황*을 기술한다. 다른 벤더가 같은 물리 상황을
    보고하면 같은 멤버로 매핑된다.
    """

    # ── Order / action protocol (already vendor-neutral in VDA5050) ──────────
    ORDER_JSON_PAYLOAD_INVALID = "ORDER_JSON_PAYLOAD_INVALID"
    ORDER_CURRENT_NOT_FINISHED = "ORDER_CURRENT_NOT_FINISHED"
    ORDER_UPDATE_ID_INVALID = "ORDER_UPDATE_ID_INVALID"
    ORDER_START_NODE_INVALID = "ORDER_START_NODE_INVALID"
    ORDER_START_SEQUENCE_ID_INVALID = "ORDER_START_SEQUENCE_ID_INVALID"
    ACTION_JSON_PAYLOAD_INVALID = "ACTION_JSON_PAYLOAD_INVALID"
    ACTION_NOT_FOUND = "ACTION_NOT_FOUND"
    INVALID_INSTANT_ACTION = "INVALID_INSTANT_ACTION"
    ORDER_ACTION_FAILED = "ORDER_ACTION_FAILED"

    # ── Motion blocked by the environment ───────────────────────────────────
    #: Robot is stopped, waiting for an object in its path to clear.
    #: Self-recovering: it resumes on its own once the path is free.
    #: 경로 위 물체가 치워지기를 기다리며 정지. 경로가 뚫리면 스스로 재개함.
    PATH_BLOCKED = "PATH_BLOCKED"
    #: Robot is still moving but degraded (slowdown / watching) around an
    #: obstacle. Not a stop.
    #: 장애물 주변에서 감속/주시하며 주행 중. 정지가 아님.
    OBSTACLE_AVOIDANCE = "OBSTACLE_AVOIDANCE"
    #: Physical contact stop (bumper / contact sensor).
    #: 물리 접촉 정지(범퍼/접촉 센서).
    BUMPER_TRIGGERED = "BUMPER_TRIGGERED"

    # ── Navigation / localization ───────────────────────────────────────────
    #: Localization or path tracking lost; relocalization required.
    #: 측위/경로 추종 상실. 재측위 필요.
    LOCALIZATION_LOST = "LOCALIZATION_LOST"
    #: Robot stopped before reaching the commanded node.
    #: 지시된 노드에 도달하기 전에 정지함.
    NODE_NOT_REACHED = "NODE_NOT_REACHED"
    #: Robot rejected or never acknowledged a motion command.
    #: 이동 명령을 거부했거나 응답하지 않음.
    MOTION_COMMAND_REJECTED = "MOTION_COMMAND_REJECTED"
    #: Vehicle diagnostics disagree with the node the order believes was reached.
    #: 차량 진단과 order 가 판단한 도달 노드가 불일치함.
    ARRIVAL_SIGNAL_MISMATCH = "ARRIVAL_SIGNAL_MISMATCH"
    #: Adaptor cannot currently publish a trustworthy ``lastNodeId``.
    #: 신뢰할 수 있는 ``lastNodeId`` 를 발행할 수 없음.
    LAST_NODE_ID_MISSING = "LAST_NODE_ID_MISSING"

    # ── Drive / power ───────────────────────────────────────────────────────
    #: Drive motor reported a fault.
    #: 구동 모터 결함 보고.
    DRIVE_MOTOR_FAULT = "DRIVE_MOTOR_FAULT"
    #: Drive motor is switched off (manual disable or firmware state).
    #: 구동 모터가 꺼져 있음(수동 해제 또는 펌웨어 상태).
    DRIVE_MOTOR_DISABLED = "DRIVE_MOTOR_DISABLED"
    #: Docking did not lead to charging within the timeout.
    #: 도킹 후 제한 시간 안에 충전이 시작되지 않음.
    DOCK_FAILED = "DOCK_FAILED"

    # ── Adaptor / integration ───────────────────────────────────────────────
    #: Adaptor lost its link to the robot controller.
    #: 어댑터가 로봇 컨트롤러와의 링크를 잃음.
    VEHICLE_LINK_LOST = "VEHICLE_LINK_LOST"
    #: Adaptor could not read peripheral IO.
    #: 주변 IO 를 읽지 못함.
    IO_READ_FAILED = "IO_READ_FAILED"
    #: Adaptor could not load its configuration; running degraded.
    #: 어댑터가 설정을 로드하지 못해 degraded 상태로 동작 중.
    CONFIG_LOAD_FAILED = "CONFIG_LOAD_FAILED"

    UNKNOWN_ERROR = "UNKNOWN_ERROR"


#: Vendor ``errorType`` → :class:`CommonErrorType`.
#:
#: Keys are matched case-insensitively. Only rows whose key differs from the
#: common value are vendor aliases; identity rows are omitted because
#: :func:`resolve_common_error_type` falls back to "already common" when a raw
#: value is itself a :class:`CommonErrorType` member.
#:
#: 벤더 ``errorType`` → :class:`CommonErrorType`. 키는 대소문자 무시로 매칭한다.
#: 공통값과 철자가 같은 항목은 넣지 않는다(:func:`resolve_common_error_type` 가
#: "이미 공통 어휘"로 처리함).
VENDOR_ERROR_ALIASES: Dict[str, CommonErrorType] = {
    # ── JIBOT ───────────────────────────────────────────────────────────────
    "JIBOT_CONFLICT": CommonErrorType.PATH_BLOCKED,
    "JIBOT_AVOIDANCE": CommonErrorType.OBSTACLE_AVOIDANCE,
    "JIBOT_BUMPER": CommonErrorType.BUMPER_TRIGGERED,
    "JIBOT_LOCALIZATION_LOST": CommonErrorType.LOCALIZATION_LOST,
    "JIBOT_NODE_UNREACHED": CommonErrorType.NODE_NOT_REACHED,
    "JIBOT_GOTO_REJECTED": CommonErrorType.MOTION_COMMAND_REJECTED,
    "JIBOT_ARRIVAL_SIGNAL_MISMATCH": CommonErrorType.ARRIVAL_SIGNAL_MISMATCH,
    "JIBOT_MOTOR_FAULT": CommonErrorType.DRIVE_MOTOR_FAULT,
    "JIBOT_MOTOR_DISABLED": CommonErrorType.DRIVE_MOTOR_DISABLED,
    "JIBOT_DOCK_FAILED": CommonErrorType.DOCK_FAILED,
    "JIBOT_CONNECTION_LOST": CommonErrorType.VEHICLE_LINK_LOST,
    # ── Peripherals shared across vendors ───────────────────────────────────
    "EZI_IO_INPUT_FAILED": CommonErrorType.IO_READ_FAILED,
}

#: Vendor family reported alongside the original code, keyed by alias prefix.
#: 원문 코드와 함께 보고할 벤더 계열. alias prefix 로 판별한다.
_VENDOR_NAME_PREFIXES = (
    ("JIBOT_", "JIBOT"),
    ("EZI_", "EZI"),
)

_ALIASES_UPPER: Dict[str, CommonErrorType] = {
    key.upper(): value for key, value in VENDOR_ERROR_ALIASES.items()
}
_COMMON_VALUES = {member.value for member in CommonErrorType}


def resolve_common_error_type(raw_error_type: object) -> Tuple[str, Optional[str]]:
    """Translate a raw ``errorType`` into the common vocabulary.

    원시 ``errorType`` 을 공통 어휘로 번역한다.

    :param raw_error_type: Value as raised by an adapter. May be an ``Enum``
        member, a plain string, or anything with a usable ``str()``.
        어댑터가 raise 한 값. ``Enum`` 멤버, 문자열 등.
    :returns: ``(common_error_type, vendor_error_type)``. ``vendor_error_type``
        is ``None`` when the raw value was already common — callers then add no
        ``vendorErrorType`` reference. Unknown values are passed through
        unchanged (never silently dropped) so a new vendor code stays visible in
        logs until it is mapped.
        ``(공통 코드, 벤더 원문)``. 원문이 이미 공통 어휘였다면 벤더 원문은
        ``None`` 이고 호출자는 참조를 추가하지 않는다. 매핑되지 않은 값은 그대로
        통과시킨다(조용히 버리지 않음) — 새 벤더 코드가 로그에 드러나야 매핑을
        추가할 수 있기 때문이다.
    """
    raw = getattr(raw_error_type, "value", raw_error_type)
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return CommonErrorType.UNKNOWN_ERROR.value, None

    if text in _COMMON_VALUES:
        return text, None

    mapped = _ALIASES_UPPER.get(text.upper())
    if mapped is not None:
        return mapped.value, text

    # Unmapped vendor code: publish it as-is so it is diagnosable, and still
    # report it as the vendor original so consumers can tell it apart from a
    # common code.
    # 미매핑 벤더 코드: 진단 가능하도록 원문 그대로 발행하되 벤더 원문으로도
    # 표시해 공통 코드와 구분되게 한다.
    return text, text


def resolve_vendor_name(vendor_error_type: Optional[str]) -> Optional[str]:
    """Best-effort vendor family for a vendor ``errorType``.

    벤더 ``errorType`` 에서 벤더 계열 이름을 추정한다.

    :param vendor_error_type: Original vendor code, or ``None``.
        벤더 원문 코드 또는 ``None``.
    :returns: Vendor family name, or ``None`` when it cannot be inferred.
        벤더 계열 이름. 추정 불가면 ``None``.
    """
    if not vendor_error_type:
        return None
    upper = vendor_error_type.upper()
    for prefix, name in _VENDOR_NAME_PREFIXES:
        if upper.startswith(prefix):
            return name
    return None


if __name__ == "__main__":  # pragma: no cover - runnable self-check
    # ponytail: assert-based self-check instead of a test framework; the mapping
    # is a pure table so this is the smallest thing that fails if it breaks.
    assert resolve_common_error_type("JIBOT_LOCALIZATION_LOST") == (
        "LOCALIZATION_LOST",
        "JIBOT_LOCALIZATION_LOST",
    )
    assert resolve_common_error_type("jibot_motor_fault") == (
        "DRIVE_MOTOR_FAULT",
        "jibot_motor_fault",
    )
    # Already-common values are passed through with no vendor original.
    assert resolve_common_error_type("ORDER_ACTION_FAILED") == ("ORDER_ACTION_FAILED", None)
    assert resolve_common_error_type(CommonErrorType.PATH_BLOCKED) == ("PATH_BLOCKED", None)
    # Unknown vendor codes survive instead of being dropped.
    assert resolve_common_error_type("SEER_SOMETHING_NEW") == (
        "SEER_SOMETHING_NEW",
        "SEER_SOMETHING_NEW",
    )
    assert resolve_common_error_type(None) == ("UNKNOWN_ERROR", None)
    assert resolve_common_error_type("") == ("UNKNOWN_ERROR", None)
    assert resolve_vendor_name("JIBOT_BUMPER") == "JIBOT"
    assert resolve_vendor_name("EZI_IO_INPUT_FAILED") == "EZI"
    assert resolve_vendor_name("SEER_SOMETHING_NEW") is None
    assert resolve_vendor_name(None) is None
    # Every alias must point at a real member and must not shadow a common value.
    for alias, common in VENDOR_ERROR_ALIASES.items():
        assert isinstance(common, CommonErrorType), alias
        assert alias.upper() not in _COMMON_VALUES, alias
    print("amr_fault_taxonomy self-check OK")
