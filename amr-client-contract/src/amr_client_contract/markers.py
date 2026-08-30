"""Marker interfaces ("mark interfaces") for optional AMR client capabilities.

선택적 AMR 클라이언트 능력을 표시하는 marker interface("mark interface") 모음.

A *marker interface* is an empty interface a class inherits to DECLARE that it
supports something. Unlike the Protocols in ``contract.py`` (which describe a
method surface and are satisfied *structurally*), markers are satisfied
*nominally* — a class must explicitly inherit the marker. The adaptor then
feature-detects with ``isinstance(vehicle, SupportsDocking)``.
*marker interface*는 클래스가 "이 기능을 지원한다"고 선언하려고 상속하는 빈
인터페이스다. ``contract.py``의 Protocol(메서드 표면을 기술, *구조적*으로 충족)과
달리, marker 는 *명목적*으로 충족한다 — 클래스가 marker 를 명시적으로 상속해야 한다.
어댑터는 ``isinstance(vehicle, SupportsDocking)``으로 능력을 탐지한다.

Why nominal (explicit inherit) for capabilities?
능력에 명목적(명시 상속)을 쓰는 이유?
A client might *happen* to have a ``dock`` method that means something else.
Requiring an explicit ``class SeerClient(SupportsDocking)`` makes the capability
a deliberate, greppable declaration rather than an accidental name collision.
어떤 클라이언트가 *우연히* 다른 의미의 ``dock`` 메서드를 가질 수도 있다. 명시적인
``class SeerClient(SupportsDocking)``을 요구하면, 능력이 우연한 이름 충돌이 아니라
의도적이고 grep 가능한 선언이 된다.

Markers carry NO methods by design — the actual methods live in the Protocols in
``contract.py``. A marker only answers the yes/no question "does this client
support X?".
marker 는 설계상 메서드를 갖지 않는다 — 실제 메서드는 ``contract.py``의 Protocol 에
있다. marker 는 "이 클라이언트가 X 를 지원하나?"라는 예/아니오 질문에만 답한다.
"""

from __future__ import annotations

import abc


class AmrCapability(abc.ABC):
    """Base for every capability marker. Empty by design (pure marker).

    모든 능력 marker 의 베이스. 설계상 비어있음(순수 marker).

    Inheriting this base lets the adaptor treat "any capability marker" uniformly
    if it ever needs to (e.g. enumerate supported capabilities of a vehicle).
    이 베이스를 상속하면 어댑터가 필요할 때 "임의의 능력 marker"를 일관되게 다룰 수
    있다(예: 차량이 지원하는 능력 열거).
    """


class SupportsSimulation(AmrCapability):
    """Client is (or can be) an in-process simulator, not a real robot link.

    클라이언트가 실로봇 링크가 아니라 인프로세스 시뮬레이터(이거나 될 수 있음).

    Mirrors JIBOT's ``is_simulator`` attribute, which the adaptor reads to adapt
    behaviour (e.g. drive to order-supplied node positions, advertise sim mode in
    ``state.information``). A class with this marker MUST expose ``is_simulator``.
    JIBOT 의 ``is_simulator`` 속성과 대응한다(어댑터가 읽어서 동작을 바꿈: 주문이 준
    노드 좌표로 주행, ``state.information``에 시뮬레이션 모드 표시). 이 marker 를 단
    클래스는 ``is_simulator`` 속성을 노출해야 한다.
    """


class SupportsDocking(AmrCapability):
    """Client supports an auto-dock / charge-seat manoeuvre (``dock``).

    클라이언트가 자동 도킹 / 충전 안착 동작(``dock``)을 지원한다.

    JIBOT: ``um_dock`` (JModeCharge reflector back-up). SEER: dock task.
    """


class SupportsManualDrive(AmrCapability):
    """Client supports low-level velocity jog (``drive``) and relative moves.

    클라이언트가 저수준 속도 조그(``drive``)와 상대 이동을 지원한다.

    JIBOT: ``um_drive`` / ``move_distance``. SEER: motion control (2010).
    """


class SupportsRelocation(AmrCapability):
    """Client supports re-localizing the robot to a pose (``localize``).

    클라이언트가 로봇을 특정 pose 로 재위치(``localize``)하는 것을 지원한다.

    JIBOT: ``um_localize``. SEER: relocation (2002).
    """


class SupportsMapNodes(AmrCapability):
    """Client exposes the active map's addressable nodes (name -> pose).

    클라이언트가 활성 맵의 주소화 가능한 노드(name -> pose)를 노출한다.

    JIBOT: parses ``UmGetMap`` 'Objs' into ``_map_nodes`` / ``_map_raw``. SEER:
    map info (1300) + landmark list. Lets the adaptor map a pose to a node id.
    JIBOT 은 ``UmGetMap``의 'Objs'를 ``_map_nodes`` / ``_map_raw``로 파싱. SEER 는
    map info(1300) + 랜드마크 목록. 어댑터가 pose 를 node id 로 매핑할 수 있게 한다.
    """


class SupportsTelemetryInjection(AmrCapability):
    """Client accepts out-of-band telemetry pushed in by the adaptor.

    클라이언트가 어댑터가 밀어넣는 대역 외(out-of-band) 텔레메트리를 받는다.

    JIBOT: BMS voltage/current and ``/jrobot_status`` safety fields come from a
    ROS listener, not the 7273 TCP stream, so the adaptor injects them via
    ``set_bms`` / ``set_robot_safety``. A client without a separate ROS feed may
    not need this marker.
    JIBOT 은 BMS 전압/전류와 ``/jrobot_status`` 안전 필드를 7273 TCP 스트림이 아니라
    ROS 리스너에서 받으므로 어댑터가 ``set_bms`` / ``set_robot_safety``로 주입한다.
    별도 ROS 피드가 없는 클라이언트는 이 marker 가 필요 없을 수 있다.
    """
