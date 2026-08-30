"""Shared enums for the AMR client contract.

AMR 클라이언트 계약에서 공용으로 쓰는 enum 모음.

Kept intentionally minimal: only vocabulary that the adaptor and *every* client
agree on belongs here. Vendor-specific enums (SEER API numbers, JIBOT #CMD#
names) stay inside their own client package, NOT here.
의도적으로 최소만 둔다: 어댑터와 *모든* 클라이언트가 공유하는 어휘만 여기에 둔다.
벤더 고유 enum(SEER API 번호, JIBOT #CMD# 이름)은 각 클라이언트 패키지에 두고
여기에는 두지 않는다.
"""

from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    """Optional capabilities a client may advertise via a marker interface.

    클라이언트가 marker interface 로 광고할 수 있는 선택적 능력.

    The string values mirror the marker class names in ``markers.py`` so a
    capability can be referenced from config/registry without importing the
    marker classes. ``isinstance(client, SupportsDocking)`` is the runtime check;
    this enum is the by-name reference.
    문자열 값은 ``markers.py``의 marker 클래스명과 1:1 대응한다. 그래서 marker
    클래스를 import 하지 않고도 config/registry 에서 능력을 이름으로 참조할 수 있다.
    런타임 판별은 ``isinstance(client, SupportsDocking)``, 이 enum 은 이름 참조용.
    """

    SIMULATION = "SupportsSimulation"
    DOCKING = "SupportsDocking"
    MANUAL_DRIVE = "SupportsManualDrive"
    RELOCATION = "SupportsRelocation"
    MAP_NODES = "SupportsMapNodes"
    TELEMETRY_INJECTION = "SupportsTelemetryInjection"


class ConnectionState(str, Enum):
    """Coarse link state the adaptor cares about for VDA5050 connection msgs.

    어댑터가 VDA5050 connection 메시지 판단에 쓰는 거친 링크 상태.

    A client reports this via the ``AmrConnection`` contract methods
    (``is_connected`` / ``is_rx_stale``); this enum is the normalized vocabulary
    the adaptor maps those booleans onto.
    클라이언트는 ``AmrConnection`` 계약 메서드(``is_connected`` / ``is_rx_stale``)로
    상태를 알리고, 어댑터는 그 불리언을 이 enum 어휘로 정규화한다.
    """

    ONLINE = "ONLINE"
    # Socket up but no inbound frame within the staleness timeout (half-open).
    # 소켓은 살아있지만 staleness timeout 안에 수신 프레임이 없음(half-open).
    STALE = "STALE"
    OFFLINE = "OFFLINE"
