"""The adaptor <-> AMR-client contract, as runtime-checkable Protocols.

어댑터 <-> AMR 클라이언트 계약. 런타임 검사 가능한 Protocol 로 정의.

Every member here is marked ``[ADAPTOR-CONTRACT]``: the adaptor calls/reads it
TODAY (currently against ``jibot_client.JIBOT``). This file is the explicit
extraction of what used to be an implicit, duck-typed dependency.
여기 모든 멤버는 ``[ADAPTOR-CONTRACT]``로 표시한다: 어댑터가 *현재* 호출/조회하는
것이다(지금은 ``jibot_client.JIBOT`` 대상). 이 파일은 과거에 암묵적·덕타이핑이던
의존성을 명시적으로 추출한 것이다.

Structural vs nominal / 구조적 vs 명목적
----------------------------------------
These are ``typing.Protocol`` interfaces — satisfied STRUCTURALLY. A client does
NOT import or inherit them to conform; matching method/attribute names are
enough. That is exactly why ``jibot_client.JIBOT`` needs ZERO changes:
이들은 ``typing.Protocol`` 인터페이스 — *구조적*으로 충족된다. 클라이언트는 이를
import 하거나 상속하지 않아도 되고, 메서드/속성 이름만 맞으면 된다. 그래서
``jibot_client.JIBOT``은 변경이 *전혀* 필요 없다:

  - JIBOT already satisfies the public wrappers (``goto_xyz``, ``goto_point``,
    ``stop_motion``, ``move_distance``, ``enable_motor``, ``disable_motor``,
    ``connect``/``connect_socket``/``disconnect``/``reconnect``/``is_connected``/
    ``is_rx_stale``/``seconds_since_last_rx``) and the injectors (``set_bms`` …).
  - JIBOT 은 공개 래퍼와 주입자들을 이미 충족한다.

  - The clean names ``dock`` / ``localize`` / ``drive`` and the state PROPERTIES
    (``x``, ``y`` …) are NOT yet on JIBOT (it has ``um_dock`` / ``um_localize`` /
    ``um_drive`` and the private attrs ``_x`` / ``_y`` …). So JIBOT is a PARTIAL
    structural match today. Full nominal conformance is a future, purely-additive
    shim on jibot-client (``dock = um_dock``; ``@property x: return self._x``) —
    out of scope here, and it changes no runtime behaviour.
  - 깔끔한 이름 ``dock`` / ``localize`` / ``drive``과 상태 PROPERTY(``x``, ``y`` …)는
    아직 JIBOT 에 없다(JIBOT 은 ``um_dock`` / ``um_localize`` / ``um_drive``과 private
    속성 ``_x`` / ``_y`` …를 가짐). 즉 JIBOT 은 오늘 기준 *부분* 구조 일치다. 완전한
    명목 정합은 jibot-client 에 가산적으로 얇은 shim 을 붙이는 후속 작업(이 작업 범위
    밖)이며, 런타임 동작을 바꾸지 않는다.

``seer_client.SeerClient`` (new) is built clean-name-first, so it satisfies these
Protocols directly.
``seer_client.SeerClient``(신규)은 깔끔한 이름 우선으로 만들어, 이 Protocol 들을 바로
충족한다.

Units / 단위
------------
Pose units follow each client's native frame and are documented per client
(JIBOT reports millimetres; SEER reports metres). The adaptor normalizes. The
contract is unit-agnostic.
pose 단위는 각 클라이언트의 네이티브 프레임을 따르며 클라이언트별로 문서화한다(JIBOT
은 mm, SEER 는 m). 정규화는 어댑터가 한다. 계약 자체는 단위 불가지론.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, runtime_checkable


@runtime_checkable
class AmrConnection(Protocol):
    """Link lifecycle + a receive-staleness watchdog.  [ADAPTOR-CONTRACT]

    링크 수명주기 + 수신 staleness 워치독.  [ADAPTOR-CONTRACT]

    The adaptor's reconnection supervisor drives these; the staleness pair lets
    it detect a half-open link (socket alive but no inbound frames).
    어댑터의 재연결 감시 루프가 이를 구동한다. staleness 쌍은 half-open 링크(소켓은
    살아있지만 수신 프레임 없음)를 탐지하게 한다.
    """

    async def connect_socket(self) -> None:
        """Open the transport (TCP socket(s)) and start the receive loop.

        전송(TCP 소켓)을 열고 수신 루프를 시작한다.
        """
        ...

    async def connect(self) -> None:
        """Perform any post-socket login/handshake (JIBOT: UmConnect).

        소켓 연결 후 로그인/핸드셰이크 수행(JIBOT: UmConnect).
        """
        ...

    async def disconnect(self) -> None:
        """Tear down the transport and stop background tasks.

        전송을 정리하고 백그라운드 태스크를 멈춘다.
        """
        ...

    async def reconnect(self) -> None:
        """Re-open the transport and re-run the login without a process restart.

        프로세스 재시작 없이 전송을 다시 열고 로그인을 재실행한다.
        """
        ...

    def is_connected(self) -> bool:
        """True while the receive loop is alive.

        수신 루프가 살아있는 동안 True.
        """
        ...

    def is_rx_stale(self, timeout: float) -> bool:
        """True when a connected link received nothing for ``timeout`` seconds.

        연결된 링크가 ``timeout`` 초 동안 아무 것도 수신하지 못하면 True.
        """
        ...

    def seconds_since_last_rx(self) -> float:
        """Seconds since the last inbound frame (monotonic).

        마지막 수신 프레임 이후 경과 시간(초, monotonic).
        """
        ...


@runtime_checkable
class AmrMotion(Protocol):
    """High-level motion commands the adaptor issues per order/instant-action.

    어댑터가 주문/instant-action 마다 내리는 고수준 모션 명령.  [ADAPTOR-CONTRACT]

    These are deliberately vendor-neutral. Each client maps them to its native
    protocol (JIBOT UmGoto/UmDock/…, SEER gotarget/gopath/motion/…).
    의도적으로 벤더 중립이다. 각 클라이언트가 네이티브 프로토콜로 매핑한다(JIBOT
    UmGoto/UmDock/…, SEER gotarget/gopath/motion/…).
    """

    async def goto_xyz(self, x: float, y: float, z: float, strict: bool = False) -> None:
        """Drive to an absolute pose (x, y, heading) in the robot frame.

        로봇 프레임의 절대 pose(x, y, heading)로 주행.

        ``z`` (the arrival heading) is REQUIRED. There is no "leave the heading
        alone" value: JIBOT drops a pose goto that carries no heading without
        reporting anything, so a caller that does not care must pass the pose
        the robot already has. 도착 heading ``z``는 필수다. "heading을 두라"는
        값은 없으므로, 상관없는 호출자는 로봇의 현재 heading을 넘긴다.
        """
        ...

    async def goto_point(self, point: str, strict: bool = False) -> None:
        """Drive to a named map node / station / landmark.

        이름이 붙은 맵 노드 / 스테이션 / 랜드마크로 주행.
        """
        ...

    async def stop_motion(self) -> None:
        """Stop / cancel the current motion (canonical stop).

        현재 모션을 정지/취소(표준 정지).
        """
        ...

    async def move_distance(self, distance: float, speed: float, **kwargs: Any) -> None:
        """Relative jog: move ``distance`` (signed) at ``speed``.

        상대 조그: ``distance``(부호 포함)만큼 ``speed``로 이동.
        """
        ...

    async def enable_motor(self) -> None:
        """Enable / energize the drive motors.

        구동 모터 활성화/여자.
        """
        ...

    async def disable_motor(self) -> None:
        """Disable / de-energize the drive motors.

        구동 모터 비활성화/소자.
        """
        ...

    # The three below need their capability marker too (SupportsDocking /
    # SupportsRelocation / SupportsManualDrive). They are part of the contract
    # because the adaptor calls them today, but a client that lacks the hardware
    # may stub them and simply NOT inherit the matching marker.
    # 아래 셋은 능력 marker(SupportsDocking / SupportsRelocation /
    # SupportsManualDrive)도 함께 필요하다. 어댑터가 오늘 호출하므로 계약에 포함되지만,
    # 해당 하드웨어가 없는 클라이언트는 스텁 처리하고 해당 marker 를 상속하지 않으면 된다.
    async def dock(self, **params: Any) -> None:
        """Auto-dock / charge-seat manoeuvre (JIBOT: ``um_dock``).

        자동 도킹 / 충전 안착 동작(JIBOT: ``um_dock``).
        """
        ...

    async def localize(
        self,
        target: str,
        goal: Optional[str],
        poseX: Optional[float],
        poseY: Optional[float],
        poseTh: Optional[float],
    ) -> None:
        """Re-localize the robot to a pose/goal (JIBOT: ``um_localize``).

        로봇을 pose/goal 로 재위치(JIBOT: ``um_localize``).
        """
        ...

    async def drive(self, trans: float, rot: float, speed: float, lat: float) -> None:
        """Low-level velocity drive (JIBOT: ``um_drive``).

        저수준 속도 주행(JIBOT: ``um_drive``).
        """
        ...


@runtime_checkable
class AmrStateReader(Protocol):
    """Cached robot state the adaptor reads each publish tick.  [ADAPTOR-CONTRACT]

    어댑터가 매 publish tick 마다 읽는 캐시된 로봇 상태.  [ADAPTOR-CONTRACT]

    Exposed as clean PROPERTIES. NOTE: the *current* adaptor reads JIBOT's private
    attrs (``vehicle._x`` …) directly; a client built clean-first (SeerClient)
    exposes ``vehicle.x`` properties, and a future adaptor refactor / adapter_seer
    reads via these properties. See the module docstring on partial conformance.
    깔끔한 PROPERTY 로 노출. 참고: *현재* 어댑터는 JIBOT 의 private 속성(``vehicle._x``
    …)을 직접 읽는다. 깔끔-우선으로 만든 클라이언트(SeerClient)는 ``vehicle.x`` property
    를 노출하고, 추후 어댑터 리팩터/adapter_seer 가 이 property 로 읽는다. 부분 정합은
    모듈 docstring 참고.
    """

    # Pose in the robot's native frame (see "Units" in the module docstring).
    # 로봇 네이티브 프레임의 pose(모듈 docstring 의 "Units" 참고).
    x: float
    y: float
    th: float
    # Battery state-of-charge (percent) or None until first known reading.
    # 배터리 충전량(%) 또는 첫 유효값 전까지 None.
    battery: Optional[float]
    # Vendor status / mode strings (adaptor maps these onto VDA5050 + AMR_STATE).
    # 벤더 status / mode 문자열(어댑터가 VDA5050 + AMR_STATE 로 매핑).
    status: str
    mode: str
    # Current station / node label, "" when between nodes.
    # 현재 스테이션 / 노드 라벨, 노드 사이일 때 "".
    station: str
    charging: bool
    # Motor enabled flag. NOTE: the adaptor reduces motor_flag==False to EMERGENCY
    # (see project memory "emergency == motor_flag off").
    # 모터 활성 플래그. 참고: 어댑터는 motor_flag==False 를 EMERGENCY 로 환원한다
    # (프로젝트 메모리 "emergency == motor_flag off" 참고).
    motor_flag: bool
    # 0..1 (or vendor scale) localization confidence.
    # 0..1(또는 벤더 스케일) 위치추정 신뢰도.
    localization_score: float


@runtime_checkable
class AmrTelemetryInjection(Protocol):
    """Out-of-band telemetry the adaptor pushes into the client.  [ADAPTOR-CONTRACT]

    어댑터가 클라이언트로 밀어넣는 대역 외 텔레메트리.  [ADAPTOR-CONTRACT]

    Pairs with the ``SupportsTelemetryInjection`` marker. For JIBOT, BMS and
    ``/jrobot_status`` safety come from a ROS listener (not the TCP stream), so
    the adaptor injects them and the client merely caches them for its state
    getters.
    ``SupportsTelemetryInjection`` marker 와 짝을 이룬다. JIBOT 은 BMS 와
    ``/jrobot_status`` 안전 정보가 ROS 리스너(=TCP 스트림 아님)에서 오므로 어댑터가
    주입하고, 클라이언트는 상태 getter 를 위해 캐시만 한다.
    """

    def set_bms(self, voltage: float, current: float) -> None:
        """Cache the latest BMS voltage (V) and signed current (A).

        최신 BMS 전압(V)과 부호 있는 전류(A)를 캐시한다.
        """
        ...

    def clear_bms(self) -> None:
        """Drop cached BMS values (stream stale/down).

        캐시된 BMS 값을 버린다(스트림 stale/down).
        """
        ...

    def set_robot_safety(self, safety: Mapping[str, Any]) -> None:
        """Cache the latest safety/motor fields (system_status, estop, …).

        최신 안전/모터 필드(system_status, estop, …)를 캐시한다.
        """
        ...

    def clear_robot_safety(self) -> None:
        """Drop cached safety fields (stream stale/down).

        캐시된 안전 필드를 버린다(스트림 stale/down).
        """
        ...


@runtime_checkable
class AmrClient(
    AmrConnection,
    AmrMotion,
    AmrStateReader,
    AmrTelemetryInjection,
    Protocol,
):
    """The full vehicle contract = connection + motion + state + injection.

    전체 차량 계약 = 연결 + 모션 + 상태 + 주입.  [ADAPTOR-CONTRACT]

    This is the single type the adaptor should annotate its ``vehicle`` with:
    ``vehicle: AmrClient``. Both ``jibot_client.JIBOT`` (structurally, partially —
    see module docstring) and ``seer_client.SeerClient`` (directly) inhabit it.
    어댑터가 ``vehicle``에 붙일 단일 타입이다: ``vehicle: AmrClient``. ``JIBOT``
    (구조적·부분 — 모듈 docstring 참고)과 ``SeerClient``(직접)가 모두 이 타입에 속한다.

    Capability markers (``SupportsDocking`` …) are NOT folded in here on purpose:
    they are OPTIONAL, so they stay separate and are feature-detected with
    ``isinstance``. ``AmrClient`` is the always-present core.
    능력 marker(``SupportsDocking`` …)는 의도적으로 여기 합치지 않는다: 선택적이므로
    분리해 두고 ``isinstance``로 탐지한다. ``AmrClient``는 항상 존재하는 코어다.
    """
