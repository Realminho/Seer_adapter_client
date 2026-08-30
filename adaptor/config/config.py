try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from config.errors import ConfigError
from config.extensions import (
    DEFAULT_EXTENSIONS_PATH,
    DEFAULT_SPEED_MAX_PERCENT,
    DEFAULT_SPEED_MIN_PERCENT,
    DEFAULT_SPEED_START_PERCENT,
    DEFAULT_SPEED_STEP_PERCENT,
    ExtensionsError,
    load_extensions,
)
from config.fleet import DEFAULT_FLEET_PATH
from config.recipes import RecipesError, load_recipes

@dataclass
class MqttBrokerConfig:
    host: str
    port: int
    vda_interface: str

@dataclass
class VehicleConfig:
    manufacturer: str = "jibot"
    serial_number: str = ""
    vda_version: str = "v3"
    vda_full_version: str = "3.0.0"
    vehicle_ip: str = ""
    vehicle_port: int = 7273

@dataclass
class EziConfig:
    ezi_io: str = ""
    tray_slot_pin: List[int] = field(default_factory=lambda: [8, 9, 10, 11, 12, 13])
    select: int = 15
    go: int = 15
    ezi_motor: str = ""
    do_motor_test: int = 0
    motor_speed: int = 20000
    close_motor_before_motion: bool = True
    origin_encoder_offset: int = 16000
    action_key: List[str] = field(default_factory=list)
    # servo 정책 기본값은 "동작이 끝나면 항상 OFF"다. EZI 드라이브는 servo가 실제로
    # 여자되기 전에 도착한 move를 조용히 버리므로, auto_on_* 정책은 이동 전
    # FFLAG_SERVOON과 이동 완료(대부분 FFLAG_MOTIONING, clampHome의 원점 복귀는
    # FFLAG_ORIGINRETURNING/FFLAG_ORIGINRETOK)를 확인하며 진행한다(extensions/clamp
    # 참고). manual은 이 확인을 모두 건너뛰므로 servo ON·이동 완료 확인은 운영자
    # 책임이다.
    clamp_servo_policy: str = "auto_on_auto_off"
    clamp_servo_on_timeout_sec: float = 3.0        # servo ON 플래그 대기 한계
    # 이동 시작 대기 한계. 초과하면 "이미 목표 위치"인지 확인하고, 아니면 실패다.
    clamp_motion_start_timeout_sec: float = 1.0
    clamp_motion_timeout_sec: float = 30.0         # 이동 완료 대기 한계
    # 절대 위치 이동의 도착 판정 허용 오차(엔코더 counts). ±16000 스트로크 기준으로
    # 일부러 넉넉하게 잡았다 — 정밀도를 채점하는 값이 아니라 "도착"과 "아예 안 움직임"을
    # 가르는 값이다.
    clamp_position_tolerance: int = 500
    # Optional clamp/unclamp target tuning. All default None/0 so existing
    # config.toml files load unchanged. Resolution order per action (first that
    # is set wins): instant-action `position` param > absolute *_position >
    # origin_encoder + *_offset > legacy ±origin_encoder_offset.
    origin_encoder: int = 0            # homed origin reference for *_offset targets
    clamp_position: Optional[int] = None      # absolute close target
    unclamp_position: Optional[int] = None    # absolute open target
    clamp_offset: Optional[int] = None        # close = origin_encoder + clamp_offset
    unclamp_offset: Optional[int] = None      # open  = origin_encoder + unclamp_offset
    # min/max/home: absolute target if set, else hardware -limit / +limit / origin.
    min_position: Optional[int] = None
    max_position: Optional[int] = None
    home_position: Optional[int] = None
    motor_test_delay_sec: float = 2.0  # Delay (s) between steps in the motor-test sequence
    # Poll interval (seconds) for EziMotorClient status-polling loops.
    ezi_motor_poll_interval_sec: float = 0.1
    # ezioWaitIn: how long to wait for the expected input when the action omits
    # timeoutSec, and how often to re-read the input register while waiting.
    wait_in_default_timeout_sec: float = 5.0
    wait_in_poll_interval_sec: float = 0.1

@dataclass
class PioConfig:
    pio_serial_port: str
    pio_baudrate: int
    media: int
    port: int
    vehicle_num: str
    # PIO out 1..8이 실제로 걸리는 EZI IO digital output 번호. 설비로 나가는
    # 신호선은 직렬이 아니라 EZI IO가 구동한다 (utils/elevator.py, airshower.py가
    # 문/층 핀을 ezi_io.turn_on_output으로 친다). 기본은 out1->pin0 .. out8->pin7.
    #
    # output_pins는 위치로 짝을 추론한다(리스트 i번째 = out i+1). 한 칸 밀려도
    # 아무도 못 잡으므로 output_pin_map으로 짝을 그대로 적는 편을 권한다. 둘 다
    # 있으면 output_pin_map이 이긴다. 기존 배포 config를 깨지 않으려고 output_pins도
    # 계속 받는다.
    output_pins: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6, 7])
    # {PIO out 번호: EZI IO 출력 핀}. HCL 맵 키는 문자열로 들어오므로 __post_init__이
    # int로 정규화한다.
    output_pin_map: Dict[int, int] = field(default_factory=dict)
    # {설비 신호 이름: PIO out 번호}. recipe/action이 숫자 대신 이름으로 부르라고
    # 두는 층이다 — 핀이 바뀌어도 recipes.hcl은 손대지 않는다.
    output_signals: Dict[str, int] = field(default_factory=dict)
    # PIO in 1..8이 걸리는 EZI IO digital input 번호. 같은 핀 번호가 입력·출력
    # 레지스터에 각각 있고 방향만 다르다 (elevator.py는 floor_pin을 출력으로 걸고
    # 입력으로 확인한다). 8~13은 트레이 센서, 15는 GO라 0~7만 남는다.
    input_pins: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6, 7])
    # {설비 신호 이름: PIO in 번호}. output_signals의 입력판이다. 두 맵을 하나로
    # 합치면 방향이 섞인다 — 같은 번호가 입력·출력 레지스터에 각각 있고 뜻이
    # 다르다(out2 = 4L 문 열기 요청, in2 = 4L 문 열림 확인).
    input_signals: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # HCL 맵 키는 항상 문자열이다: output_pin_map = { 5 = 4 } -> {"5": 4}.
        # 여기서 int로 못 박아 두지 않으면 pio_output_pin이 int 키로 조회해 늘
        # 빗나가고, 조용히 output_pins 폴백으로 떨어진다.
        self.output_pin_map = _int_keyed_pin_map(
            self.output_pin_map, label="output_pin_map"
        )
        self.output_signals = _signal_map(self.output_signals, label="output_signals")
        self.input_signals = _signal_map(self.input_signals, label="input_signals")


def _int_keyed_pin_map(raw: Any, *, label: str) -> Dict[int, int]:
    """{out 번호: EZI IO 핀}을 int 키/값으로 정규화하고 범위를 검증한다."""
    if not raw:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f'extension "pio": {label} must be an object')
    normalized: Dict[int, int] = {}
    for key, value in raw.items():
        try:
            index = int(key)
            pin = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f'extension "pio": {label} 은 정수 -> 정수 맵이어야 한다 '
                f"({key!r} = {value!r})"
            ) from exc
        if not 1 <= index <= 8:
            raise ValueError(
                f'extension "pio": {label}의 PIO out 번호는 1~8이다 (got {index})'
            )
        if not 0 <= pin <= 15:
            raise ValueError(
                f'extension "pio": {label}의 EZI IO 핀은 0~15다 (got {pin})'
            )
        normalized[index] = pin
    duplicates = len(normalized) - len(set(normalized.values()))
    if duplicates:
        raise ValueError(
            f'extension "pio": {label}에 같은 EZI IO 핀이 두 out에 물려 있다; '
            "1:1이어야 한 점을 켤 때 다른 점이 따라 켜지지 않는다"
        )
    return normalized


def _signal_map(raw: Any, *, label: str = "output_signals") -> Dict[str, int]:
    """{설비 신호 이름: PIO 점 번호}를 정규화하고 범위를 검증한다.

    출력(output_signals)과 입력(input_signals)이 같은 모양이라 한 함수를 쓰되,
    오류 문구에는 어느 맵인지 남긴다 — 두 맵을 헷갈리면 엉뚱한 쪽을 고친다.
    """
    point = "out" if label == "output_signals" else "in"
    if not raw:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f'extension "pio": {label} must be an object')
    normalized: Dict[str, int] = {}
    for name, value in raw.items():
        key = str(name).strip()
        if not key:
            raise ValueError(f'extension "pio": {label}의 이름이 비어 있다')
        try:
            index = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f'extension "pio": {label}["{key}"]는 PIO {point} 번호(1~8)여야 '
                f"한다 (got {value!r})"
            ) from exc
        if not 1 <= index <= 8:
            raise ValueError(
                f'extension "pio": {label}["{key}"]의 PIO {point} 번호는 1~8이다 '
                f"(got {index})"
            )
        normalized[key] = index
    return normalized


@dataclass
class Settings:
    speed: float
    map_id: str
    state_publish_delay: float
    # Legacy simulator/config keys. They are no longer used by the JIBOT
    # adapter, but deployed config.toml files are intentionally preserved
    # during upgrades, so keep accepting them for backward compatibility.
    action_time: float = 1.0
    robot_count: int = 1
    state_frequency: int = 1
    visualization_frequency: int = 1
    map_refresh_sec: float = 300.0
    # Shape used for pose-in-node checks.
    # 위치가 노드 안에 들어왔는지 판단할 때 사용할 영역 모양.
    reach_zone_shape: str = "square"
    # Scale factor applied to VDA/order allowedDeviationXY.
    # VDA/order의 allowedDeviationXY를 JIBOT map unit으로 환산할 때 곱하는 값.
    reach_zone_scale: float = 20.0
    # Active-order node arrival fallback. Used only when an order node has no
    # allowedDeviationXY; multiplied by reach_zone_scale like order-provided
    # deviations.
    # active order의 nodePosition.allowedDeviationXY가 없을 때만 사용하는
    # 도착 판정 fallback 값. order 제공 deviation과 동일하게 reach_zone_scale이 곱해진다.
    default_node_deviation_xy: float = 10.0
    # Idle / non-active-order lastNodeId threshold in raw map units. When no
    # order is ACTIVE (no order_id, or the order is finished -- order_id is kept
    # after completion), the adapter recovers/updates lastNodeId from the nearest
    # map node only if the current pose is within this distance. This covers
    # idle, free-roam, and post-completion parking. Active-order node completion
    # does NOT use this value (the order worker owns that).
    # order가 active가 아닐 때(order_id가 없거나, 완료되어 order_id만 남은 경우)
    # 현재 pose로 lastNodeId를 복구/갱신하기 위한 raw map unit 기준 임계치.
    # idle/자유주행/완료 후 주차를 커버한다. active order node 완료 판정에는
    # 사용하지 않는다(worker가 담당).
    idle_last_node_reach_xy: float = 500.0
    # lastNodeId capture policy. One of "proximity" | "disabled" | "settled".
    #   proximity: order-agnostic nearest-node capture; the closest map node is
    #              always mirrored to lastNodeId, regardless of distance.
    #   disabled:  no idle capture, no accept-time bootstrap; lastNodeId advances
    #              only via order reset/remap + worker arrival.
    #   settled:   idle capture only when stopped AND manual inactive AND within
    #              threshold; station bootstrap only when the pose agrees.
    # Unknown values fall back to "settled" with a one-time warning.
    # lastNodeId 갱신 정책. 알 수 없는 값은 경고 후 "settled"로 폴백.
    last_node_capture_mode: str = "settled"
    # When no lastNodeId exists yet, seed it from the current nearestNodeId.
    # This is independent of the idle reach threshold and only fills an empty
    # value; an existing lastNodeId is never overwritten by this fallback.
    use_nearest_node_as_last_node_when_missing: bool = False
    # Distance gate for that seed, in raw map units. Without it an off-map or
    # wrong-floor boot seeds a groundless lastNodeId that FMS trusts as the
    # robot's position.
    # NOTE the convention differs from idle_last_node_reach_xy above, which
    # treats <=0 as "fall back to the deviation": here 0 means NO gate, so the
    # seed keeps working at any distance. 0 is the default because this seed
    # shipped ungated and deployments already enable it.
    # 이 seed에만 적용되는 raw map unit 거리 제한. 0이면 제한 없음(기존 동작).
    # idle_last_node_reach_xy와 <=0 규약이 다르다는 점에 주의.
    missing_last_node_reach_xy: float = 0.0
    # settled 모드에서 pose가 lastNodeId로부터 이만큼(raw map unit) 벗어나면
    # lastNodeId를 비운다. settled는 idle_last_node_reach_xy 안에서 캡처만 하고
    # 반납하는 경로가 없어서, 수동 주행으로 노드를 벗어나면 떠난 노드를 계속
    # publish 했다. 0이면 해제하지 않는다.
    # idle_last_node_reach_xy(캡처)와의 간격이 히스테리시스 구간이므로 충분히 크게 둘 것.
    #
    # 기본값이 0이 아닌 이유: update-jibot-adapter-over-ssh.sh 는
    # --config-toml-mode keep 이 기본이라 로봇 config.toml 을 덮지 않는다. 즉
    # 새 키는 어떤 로봇에도 자동으로 도달하지 못하고, 기본값이 곧 현장 동작이다.
    # 2026-08-23 로봇 62 실측: 코드는 배포됐는데 키가 없어 기본값 0.0이 먹었고
    # p36~p37 사이(최근접 노드 494mm)에서 lastNodeId가 p2로 고착했다.
    # 500은 그 현장 그래프의 노드 간격(p36-p37 988mm)의 절반이다.
    last_node_release_xy: float = 500.0
    # Candidate map-object mode for nearestNodeId / gotoNearestNode.
    # PathPoint is the default FMS driving graph because it owns the JIBOT
    # vertex/cost topology. "headingGoal" remains available for deployments
    # that expose only addressable Goal/GoalWithHeading/Dock objects.
    nearest_node_mode: str = "pathPoint"
    simulator_node_travel_sec: float = 0.0  # Simulator-only fixed node travel duration; <=0 uses speed
    jibot_reconnect_delay: float = 5.0  # Seconds between JIBOT reconnection attempts
    jibot_rx_timeout: float = 10.0  # Reconnect if no JIBOT frame is received within this many seconds
    # FMS(MQTT) 링크가 끊긴 채로 주행을 계속하면 교통정리를 못 하는 FMS 를 두고
    # 로봇만 달린다. grace 를 넘겨 끊겨 있으면 startPause 와 같은 일시정지를 걸고,
    # 재연결되면 stopPause 와 같은 경로로 스스로 재개한다.
    # 기본 True: 배포 시 config.toml 을 덮지 않으므로(--config-toml-mode keep)
    # 기본값이 곧 현장 동작이다. 안전 쪽을 기본으로 둔다.
    stop_on_mqtt_disconnect: bool = True
    # 끊김 판정 유예(초). paho 의 자동 재연결로 덮이는 순간 단절에 로봇이 서는 것을 막는다.
    mqtt_disconnect_stop_grace_sec: float = 3.0
    # 링크 상실 알림음 파일명. 빈 문자열이면 재생하지 않는다. stop_on_mqtt_disconnect
    # 와 독립이다 — 세우지 않기로 한 로봇에서도 끊김 자체는 알려야 한다.
    mqtt_disconnect_sound: str = "disconnect-with-mw.mp3"
    debug_log: bool = False  # Verbose per-cycle position/lastNode console logging
    # Cur-task / task-info / path are diagnostic-only (WebUI status + arrival
    # mismatch references), not on the order path, so they poll on their own
    # slower cadence instead of every robot_info_loop cycle. <=0 polls every cycle.
    task_info_poll_sec: float = 5.0
    # Polling and timeout knobs (Task 5.1).
    # subscribe_acs_cmd loop sleep interval (seconds).
    acs_cmd_subscribe_interval_sec: float = 1.0
    # _wait_until_node_position_reached poll interval (seconds).
    node_position_poll_interval_sec: float = 0.2
    # rotateTo 제자리 회전 노브.
    # "jog" 는 UmDrive(연속 속도) 폐루프. "goto" 는 예전 pose goto 폴백으로,
    # UmDrive 가 없는 구현(시뮬레이터)과 되돌릴 필요를 위해 남겨 둔다.
    # pose goto 는 제자리 회전이 아니다: urobot 이 목표 자세로 진입하려고 뒤로
    # 물러났다가 돈다 (2026-08-22 HN-SH6-TR-002 실기, pose 재조회 후에도 재현).
    rotate_to_mode: str = "jog"
    # 도착 판정 허용 오차(도). 로봇이 th 를 정수로 보고하므로 분해능은 ±1도.
    rotate_to_tolerance_deg: float = 3.0
    rotate_to_timeout_sec: float = 30.0
    # UmDrive rot 성분. 좌+/우−. 부호 규약이 현장과 반대로 나오면 코드가 아니라
    # rotate_to_rot_sign 을 -1 로 두어 뒤집는다.
    rotate_to_rot: float = 30.0
    rotate_to_slow_rot: float = 10.0
    # 남은 각이 이 안으로 들어오면 slow_rot 으로 감속한다.
    rotate_to_slow_zone_deg: float = 20.0
    rotate_to_speed: float = 200.0
    rotate_to_lat: float = 0.0
    rotate_to_rot_sign: int = 1
    # 폐루프 주기(초). heading 은 이 주기로만 갱신되므로, 너무 크면 지나친다.
    rotate_to_tick_sec: float = 0.2
    # 허용치 안 표본을 몇 개 연속으로 봐야 도달로 볼 것인가. 이 텔레메트리에는
    # 이상표본이 섞인다 — 2026-08-23 62 정지 상태에서 -83 연속 중 -90 표본 하나가
    # 끼었고 같은 시각 x 도 27mm 튀었다. 1 로 두면 회전 중 목표를 스친 표본
    # 하나에 걸려 조기에 멈춘다.
    rotate_to_confirm_samples: int = 2
    # UmStop 뒤 감속이 끝나기를 기다리는 시간(초). 이 뒤에 heading 을 다시 읽어
    # 안착 자리를 확인한다.
    rotate_to_settle_sec: float = 0.6
    # 안착 자리가 허용치를 벗어났을 때 다시 돌려볼 횟수. 0 이면 재보정하지 않고
    # 어긋난 채로 FAILED 다. 전체 timeout 안에서만 재시도한다.
    rotate_to_settle_retries: int = 2
    # _wait_until_vehicle_standstill poll interval (seconds).
    standstill_poll_interval_sec: float = 0.1
    # _wait_until_vehicle_standstill give-up timeout (seconds). Bounds the cancel
    # path so a robot that keeps reporting motion after stop_motion can't hang
    # cancelOrder forever (which left the FMS-derived dstNodeId stuck until a full
    # system reset). <=0 waits indefinitely.
    standstill_timeout_sec: float = 10.0
    # Main adapter loop sleep in the exception handler (seconds).
    adapter_loop_sleep_sec: float = 1.0
    # Default timeout passed to _dispatch_jibot_command (seconds).
    jibot_command_default_timeout_sec: float = 3.0
    # WARNING: affects command deadline floor — minimum clamp for jibot ack timeout (seconds).
    jibot_ack_timeout_min_sec: float = 0.1
    # WARNING: affects arrival/error timing — seconds robot must be stopped before UNREACHED is reported.
    node_unreached_delay_sec: float = 1.0
    # mode="move" segments only. Completion of a relative move is distance-based
    # (the commanded distance is deliberately shorter than the segment), so it
    # needs its own knobs rather than reusing node_unreached_delay_sec.
    # Give-up window for a move that never starts, and for the origin pose that
    # distance-based completion needs before dispatch.
    move_start_timeout_sec: float = 10.0
    # Displacement that confirms the move started, for a move too short or fast
    # for any telemetry sample to catch it in motion.
    move_started_min_travel_mm: float = 50.0
    # Fraction of the commanded distance counting as full travel.
    move_complete_travel_ratio: float = 0.9
    # WARNING: affects fallback timing — how long a stop short of the commanded
    # distance must persist before the move counts as ended early.
    move_stall_timeout_sec: float = 5.0
    # gotoNearestNode arrival-polling timeout (seconds). When the robot cannot
    # reach its nearest node within this window the action stops the robot and
    # fails. <=0 falls back to 60s (no unbounded RUNNING).
    goto_nearest_timeout_sec: float = 60.0
    # Manual (joystick) driving cancels the JIBOT goto task, leaving the order
    # worker waiting on a node nothing is driving to any more. These bound the
    # automatic recovery: how long the robot must sit still with nobody else
    # owning its motion before the goto is sent again, and how many times.
    # 0 retries restores the old behaviour (report and wait for the FMS).
    node_goto_retry_delay_sec: float = 3.0
    node_goto_retry_limit: int = 3
    # A robot whose drive motor is off accepts UmGoto/UmDock and then buzzes in
    # place, and arming the motor by hand afterwards re-dispatches nothing — the
    # order just stands still. Order motion therefore powers the motor first and
    # polls UmSetMotor's effect (the command is fire-and-forget) for this long
    # before giving up and failing the node with a reason. false restores the
    # old dispatch-regardless behaviour.
    order_motor_auto_enable: bool = True
    order_motor_enable_timeout_sec: float = 5.0
    switch_map_timeout_seconds: float = 30.0
    # 연속 경로 주행(Continuous Path). "stop_point" 는 노드마다 완전 정지를 기다리는
    # 현행 동작, "continuous" 는 연속 released 구간을 goto 하나로 합치고 중간 노드는
    # 통과 판정만 한다. 배포가 --config-toml-mode keep 기본이라 새 키는 현장 로봇
    # config.toml 에 도달하지 않는다. 즉 이 기본값이 곧 현장 동작이다.
    path_control: str = "stop_point"
    # 통과 간주 반경(mm). 0 이면 기존 도착존(allowedDeviationXY x reach_zone_scale)을 쓴다.
    # 2026-08-26 192.168.101.50:7274 실측에서 직선 통과 속도가 1002 mm/s 였고
    # node_position_poll_interval_sec=0.2 이므로 샘플 간 이동이 약 200mm 다.
    # 도착존 실효값 ±40mm 로는 통과를 놓친다.
    waypoint_pass_radius_mm: float = 0.0

@dataclass
class JibotStatus:
    charging: str
    driving: str
    stop: str
    motion: List[str]
    # JIBOT `mode` values that mean a human is driving the robot by hand. While
    # one of these is reported the adapter must not command motion of its own.
    manual_modes: List[str] = field(default_factory=lambda: ["ModeDrive"])

@dataclass
class SoundSettings:
    enabled: bool = True
    sink: str = ""
    sound_dir: str = "sounds"
    player: str = "mplayer"
    startup_volume: Optional[int] = None
    # Deprecated/unused: state sound files are matched by convention from the
    # robot working state. Error-code wav files are matched separately from
    # system_error_code (see sounds/README.md). Kept optional so existing
    # configs that still set these keys keep loading.
    travel_music: str = "travel.mp3"
    work_music: str = "work.mp3"
    route_prefiX: List[str] = field(default_factory=list)
    sound_test_duration_sec: float = 10.0  # Default testSound duration (s) when action omits "seconds"
    # Timeout (seconds) passed to proc.wait() after proc.terminate() in SoundPlayer.stop().
    player_wait_timeout_sec: float = 1.0
    # Timeout (seconds) passed to subprocess.run() in SoundPlayer._pactl().
    pactl_timeout_sec: float = 2.0
    # Gap inserted after a state sound finishes before replaying it. 0 keeps the
    # previous continuous player-level loop behavior.
    state_replay_gap_sec: float = 0.0
    # Optional per workingState/workingStateDetail replay gaps. Keys are
    # lowercased tokens such as "driving", "loading", or "docking".
    state_replay_gap_overrides: Dict[str, float] = field(default_factory=dict)
    # How many times a chosen track plays. 0 repeats until the state or action
    # that selected it goes away, which is the historical behaviour.
    state_repeat_count: int = 0
    # Optional per-track play counts, overriding state_repeat_count. Keys are the
    # file name without its extension -- "driving", "brake", "action-clamp" -- so
    # action sounds can be counted too. Unlike the gap overrides these are NOT
    # lowercased, because actionType names are camelCase.
    repeat_count_overrides: Dict[str, int] = field(default_factory=dict)

@dataclass
class ManualControlSettings:
    enabled: bool = True
    drive_trans: float = 200
    drive_rot: float = 30
    drive_speed: float = 200
    drive_lat: float = 0
    heartbeat_ms: int = 300
    watchdog_ms: int = 800
    step_distance_mm: int = 500
    default_move_speed: float = 100
    move_obs_avoid_dist: int = 1000
    move_side_avoid_dist: int = 50

@dataclass
class ChargeConfig:
    """이전 설정과의 객체 호환용이며 현재 docking 판단에는 쓰지 않는다."""

    nodes: List[str] = field(default_factory=list)
    routes: List[str] = field(default_factory=list)

@dataclass
class DockApproachParams:
    """Optional UmDock approach params forwarded to the JIBOT firmware
    (urobot JModeCharge::Start). Unset (None) fields are omitted so a bare
    UmDock keeps the default dock approach. JIBOT hardware-characteristic
    values belong in jibot-config.toml [dock.approach_params]."""
    goal: Optional[str] = None
    detect_charging_signal: Optional[bool] = None
    dock_move_additional_dist: Optional[float] = None
    dock_rotate_additional_angle: Optional[float] = None
    need_turn_around: Optional[bool] = None
    need_heading: Optional[bool] = None
    use_avoid_area: Optional[bool] = None
    disable_motor_secs: Optional[float] = None
    clearance_back_min: Optional[float] = None
    clearance_front_min: Optional[float] = None

    def as_params(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

@dataclass
class DockConfig:
    nodes: List[str] = field(default_factory=list)
    stop_charging_on_arrival: bool = True
    # Default timeout for dock motion rules that do not define their own
    # fail_timeout_sec. Operators can tune this from WebUI /config.
    fail_timeout_sec: float = 90.0
    # Max seconds of ONE continuous obstacle wait (#brake) on the charger
    # approach before the dock fails anyway. Brake time is excluded from
    # fail_timeout_sec because a robot waiting out an obstacle is making
    # legitimate progress, so this is what still bounds a permanently blocked
    # path instead of hanging in DOCKING forever. Keep it above
    # fail_timeout_sec so the seat timeout stays the primary signal.
    # <=0 means unbounded (brake can hold the dock open indefinitely).
    # 충전기 접근 경로가 계속 막혀 있을 때만 걸리는 한도. 0 이하면 무제한.
    # 주의: 0 이하로 두면 인계 실패 후 재도킹 폴백의 대기도 무한이 된다.
    # 그 폴백은 충전기에 앉은 채 UmDock 을 다시 거는 경로라, `#brake` 가 안 풀리면
    # 오더가 영영 끝나지 않는다 — 이번 세션이 고치려던 바로 그 고착이다. 양수로 둘 것.
    obstacle_timeout_sec: float = 300.0
    # 도킹으로 충전이 붙은 뒤 충전을 charge_circuit relay hold 로 넘기고 UmStop 으로
    # ModeCharge 를 빠져나올지. ModeCharge 를 유지한 채 두면 충전이 끊긴 순간 JIBOT 이
    # 스스로 접근 단계(`nrunto N`)로 되돌아가는데, 이미 충전기에 밀착해 있어 전방이
    # 막혀 `#brake` 로 고착된다. 2026-08-25 HN-SH6-TR-002 05:06:02~06:20:13 74분 연속
    # 관측(`ModeCharge status=nrunto 1#brake` 873표본, 그 사이 어댑터 비폴링 TX 0건).
    # relay hold(/jcmd cmd:3=1 지속 assert)는 ModeCharge 없이도 충전을 유지하므로,
    # 충전을 그쪽으로 넘기면 재접근 루프 자체가 사라진다.
    # charge_circuit.enabled=false 면 relay 를 쥘 수 없으므로 이 값과 무관하게 건너뛴다.
    handover_charge_to_relay_after_dock: bool = True
    # start_hold() 는 `rostopic pub` 자식 프로세스를 띄우기만 하고 바로 돌아온다.
    # ROS 마스터 등록과 첫 publish 까지는 실제 시간이 걸리므로, 이만큼 기다렸다가
    # UmStop 을 보낸다. 안 기다리면 JModeCharge::OpenChargingCircuit 이 먼저 이겨
    # 릴레이가 열린 채로 ModeCharge 만 빠지는 최악의 상태가 된다.
    handover_hold_settle_sec: float = 3.0
    # 인계 확인 창. charge_circuit.verify_timeout_sec(5s) 을 쓰면 안 된다 —
    # JIBOT 상태 스냅샷이 약 5초 주기로 들어오므로 그 창은 새 표본을 한 번도
    # 못 볼 수 있다(2026-08-25 실측 UmGetLocState 06:30:10/:16/:21/:26/:31).
    # 이 창 안에서 'UmStop 이후 새로 도착한' 표본이 연속으로 충전을 보고해야 성공이다.
    handover_verify_timeout_sec: float = 30.0
    # 인계 확인에 필요한 '새 표본' 연속 개수. 1이면 릴레이 재폐로 순간의 깜빡임을
    # 실패로 읽고, 너무 크면 도킹이 표본 주기만큼 늘어진다.
    handover_verify_samples: int = 2
    # JIBOT ignores a single UmStop when stopping charging, so the dock-work
    # stop is repeated this many times with the gap (seconds) below between
    # sends. Defaults work around the firmware bug; set count to 1 once fixed.
    stop_charging_repeat_count: int = 2
    stop_charging_repeat_gap_sec: float = 3.0
    # UmStop and UmSchedulerThis both replace the JIBOT scheduler task, and a
    # move issued in the same instant as the stop loses the race — the robot
    # stays put and the segment reports travelled=0.0mm. Wait this long after
    # the charge stop before dispatching order motion. 0 disables.
    stop_charging_motion_settle_sec: float = 1.0
    # After UmStop is sent, poll robot telemetry up to this long for charging to
    # actually clear before a stopCharging instant action reports FINISHED. If
    # the robot is still charging past this window, stopCharging FAILS.
    stop_charging_verify_timeout_sec: float = 10.0
    # After UmDock is sent, poll robot telemetry up to this long for charging to
    # actually start before a startCharging instant action reports FINISHED.
    # Generous by default to cover the dock approach; no charging past this
    # window => startCharging FAILS. Configurable.
    start_charging_verify_timeout_sec: float = 60.0
    # Polling intervals for dock and charging waits (seconds).
    dock_wait_poll_interval_sec: float = 0.2
    dock_approach_poll_interval_sec: float = 0.2
    charging_start_poll_interval_sec: float = 0.2
    charging_stop_poll_interval_sec: float = 0.2
    # How long the robot must be stopped before the dock-approach wait reports
    # unreached; affects dock-approach arrival/error timing.
    # WARNING: lowering this may cause spurious unreached errors on slow robots.
    dock_approach_unreached_delay_sec: float = 1.0
    approach_params: DockApproachParams = field(default_factory=DockApproachParams)

@dataclass
class MotionRule:
    # One node-arrival / segment motion-mode override. Lives in
    # config.toml `motion_rules` (a list of inline tables, one per line):
    #   { to = "F2_90_S2CH", mode = "dock", approach = "F2_90_S2CH_BEFORE", fail_timeout_sec = 90.0 }
    #   { from = "F1_60", to = "F2_90_S2CH", mode = "move", distance = 2500, speed = 200 }
    # Match: `to` (destination node, required) + optional `from_node` (TOML key
    # `from`); when from_node is set, only that segment matches.
    #   mode="dock": go to `approach` waypoint, then UmDock; reject if not
    #                charging within this rule's `fail_timeout_sec`, or the
    #                shared [dock].fail_timeout_sec when unset.
    #   mode="move": relative-distance move over the from->to segment. The
    #                distance is typically shorter than the segment (only the
    #                constrained part needs it); if that lands the pose in the
    #                to-node reach zone the step completes immediately,
    #                otherwise a plain goto drives the remainder to the
    #                to-node and completion follows that arrival instead.
    to: str
    mode: str = "goto"
    from_node: Optional[str] = None       # TOML key "from" (from is a Python keyword)
    approach: Optional[str] = None         # mode="dock"
    fail_timeout_sec: Optional[float] = None  # mode="dock" override; None => [dock].fail_timeout_sec
    distance: Optional[float] = None       # mode="move" (mm, signed: +fwd / -back)
    speed: Optional[float] = None          # mode="move" (default: manual_control.default_move_speed)
    flag: Optional[int] = None             # mode="move" route-step override
    io: Optional[int] = None               # mode="move" route-step override
    obs_avoid_dist: Optional[int] = None   # mode="move" front/back obstacle avoid distance
    side_avoid_dist: Optional[int] = None  # mode="move" side obstacle avoid distance
    use_io: Optional[bool] = None          # mode="move" route-step override
    note: Optional[int] = None             # mode="move" route-step override
    approach_params: Optional[Dict[str, Any]] = None   # mode="dock" override


def _motion_rule_file_keys() -> List[str]:
    """MotionRule 필드를 **파일에 적는 이름**으로 돌려준다.

    dataclass 필드는 from_node지만 운영자가 파일에 쓰는 키는 from이다. 필드
    이름을 그대로 안내하면 파일에 없는 키를 찾아 헤매게 된다.
    """
    return sorted(
        "from" if spec.name == "from_node" else spec.name
        for spec in fields(MotionRule)
    )


def _toml_inline(data: Mapping[str, Any]) -> str:
    """오류 메시지에 운영자가 파일에서 보는 모양 그대로 한 줄을 되돌려준다."""
    def _value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            return f'"{value}"'
        if isinstance(value, Mapping):
            return _toml_inline(value)
        return str(value)

    return "{ " + ", ".join(f"{k} = {_value(v)}" for k, v in data.items()) + " }"


def _motion_rule_error(
    source: Optional[Union[str, Path]],
    index: Optional[int],
    data: Mapping[str, Any],
    *,
    trouble: str,
    advice: str,
) -> ConfigError:
    """어느 파일 몇 번째 motion_rules 줄인지 짚은 ConfigError를 만든다.

    motion_rules는 섹션이 아니라 **목록**이라 _section의 라벨(``[이름]``)로는
    어느 줄인지 알 수 없다. 줄 번호 대신 몇 번째 항목인지와 그 줄의 내용을
    함께 실어야 운영자가 여러 줄 중 하나를 바로 찾는다.
    """
    where = "motion_rules" if index is None else f"motion_rules {index}번째 항목"
    return ConfigError(
        f"{source}: {where}에 {trouble}: {_toml_inline(data)}. "
        f"쓸 수 있는 키: {', '.join(_motion_rule_file_keys())}. {advice}",
        path=source,
    )


def _motion_rule_from_dict(
    data: Mapping[str, Any],
    *,
    source: Optional[Union[str, Path]] = None,
    index: Optional[int] = None,
    lenient: bool = False,
) -> Optional["MotionRule"]:
    """Build a MotionRule from a TOML inline table, mapping `from` -> `from_node`
    (the readable TOML key stays `from`; `from` is a Python keyword).

    ``MotionRule(**data)``를 그냥 부르면 to 한 줄이 빠졌을 때 라벨 없는
    "MotionRule.__init__() missing 1 required positional argument: 'to'"가 되고,
    ConfigError가 아니라서 get_config_with_fallback의 보고 경로도 못 탄다 —
    systemd가 같은 자리에서 계속 되살리는 크래시 루프다 (2026-08-23 20:44 현장).
    _section이 섹션에 하는 검증을 목록 항목에도 똑같이 한다.

    ``lenient``는 **보고 전용** config를 만들 때만 켠다: 못 만드는 항목은 None을
    돌려 호출부가 건너뛴다. 이 config로는 주행하지 않으므로(run_config_error_mode)
    룰이 빠져도 되고, 여기서 또 죽으면 보고할 방법이 사라진다.
    """
    if not isinstance(data, Mapping):
        if lenient:
            return None
        raise _motion_rule_error(
            source, index, {},
            trouble=f"표가 아닌 값이 있습니다({data!r})",
            advice='{ to = "<도착 노드>", mode = "dock" } 꼴로 적으세요.',
        )

    values = dict(data)
    allowed = set(_motion_rule_file_keys())
    unknown = sorted(set(values) - allowed)
    if unknown:
        if not lenient:
            raise _motion_rule_error(
                source, index, data,
                trouble=f"모르는 키가 있습니다({', '.join(unknown)})",
                advice="오타라면 고치고, 더 이상 쓰지 않는 줄이라면 지우세요.",
            )
        values = {key: value for key, value in values.items() if key in allowed}

    if "from" in values:
        values["from_node"] = values.pop("from")

    try:
        return MotionRule(**values)
    except TypeError as exc:
        if lenient:
            return None
        missing = sorted(
            spec.name
            for spec in fields(MotionRule)
            if spec.name not in values
            and spec.default is MISSING
            and spec.default_factory is MISSING
        )
        if not missing:
            raise
        raise _motion_rule_error(
            source, index, data,
            trouble=f"반드시 있어야 할 키가 없습니다({', '.join(missing)})",
            advice='그 줄에 to = "<도착 노드>"를 추가하세요.',
        ) from exc


def _motion_rules_from_config(
    raw: Any,
    source: Optional[Union[str, Path]],
    lenient: bool = False,
) -> List["MotionRule"]:
    """config.toml의 motion_rules 목록을 MotionRule 목록으로 만든다."""
    if not isinstance(raw, list):
        if lenient:
            return []
        raise ConfigError(
            f"{source}: motion_rules는 인라인 표({{ ... }}) 줄의 목록이어야 "
            f"합니다 (지금 값: {raw!r}).",
            path=source,
        )
    rules = []
    for index, rule in enumerate(raw, start=1):
        built = _motion_rule_from_dict(
            rule, source=source, index=index, lenient=lenient
        )
        if built is not None:
            rules.append(built)
    return rules

@dataclass
class ChargeCircuitConfig:
    # In-place charge holds ROS /jcmd cmd:3 (CloseChargingCircuit). Opt-in: only
    # enable on real JIBOT hosts with the ROS env + rostopic available.
    enabled: bool = False
    jcmd_topic: str = "/jcmd"
    publish_rate_hz: float = 2.0
    ros_setup: str = "/usr/local/urobot/jarvis/setup.bash"
    ros_master_uri: str = "http://localhost:11311"
    verify_timeout_sec: float = 5.0
    # Timeout (seconds) passed to proc.wait() after proc.terminate() for the
    # hold-subprocess teardown.
    terminate_timeout_sec: float = 3.0
    # Timeout (seconds) passed to the relay-OFF one-shot command subprocess.
    # `rostopic pub -1` latches the message for ~3s and only gets there after
    # sourcing the ROS env and registering a node; 5s was not enough on a busy
    # JIBOT board, where the publish landed 1.5s past the deadline.
    off_timeout_sec: float = 15.0

@dataclass
class BmsRosConfig:
    # Read-only ROS listener: streams /jrobot_status (jarvis_msgs/RobotStatus)
    # for the BMS voltage/current that UmGetBatteryInfo over 7273 stubs out.
    # Safe to default on (read-only); auto-disabled under --simulator.
    enabled: bool = True
    topic: str = "/jrobot_status"
    ros_setup: str = "/usr/local/urobot/jarvis/setup.bash"
    ros_master_uri: str = "http://localhost:11311"
    stale_after_sec: float = 30.0
    restart_backoff_sec: float = 3.0

@dataclass
class AirShowerPioConfig:
    # 상대 설비를 고르는 BC 주소다. extension "pio"가 아니라 이 설비가 소유한다 —
    # 에어샤워와 엘리베이터는 서로 다른 station에 붙는다.
    pio_station_id: str
    channel: int
    failure: int
    occupied: int
    fun_working: int
    door_pin: List[int]
    timeout_paring_requesting: int
    timeout_close_requesting: int
    timeout_open_requesting: int
    timeout_vacancy_waiting: int
    timeout_airflow_waiting: int
    # Poll cadence (seconds) for the state-machine while-loops in ASWorkflow.
    poll_interval_sec: float = 0.2

@dataclass
class ElevatorMotionRule:
    from_: str
    to: str
    mode: str
    floor_pin: int
    pio_station_id: str

@dataclass
class ElevatorPioConfig:
    # 이 엘리베이터 시스템이 듣는 무선 채널이다. station은 층마다 다르지만
    # (motion_rules의 pio_station_id) 채널은 설비 하나에 하나다.
    channel: int
    open_door_pin: int
    close_door_pin: int
    solid_on_second: int
    elevating_timing_second: int
    door_open_close_timing_second: int
    timeout_paring_requesting: int
    timeout_floor_requesting: int
    elevator_motion_rules: List[ElevatorMotionRule]  

@dataclass
class HexplorerConfig:
    robot_command_topic: str = "/robot_cmd"
    robot_state_topic: str = "/robot_state"
    velocity_command_topic: str = "/vel_cmd"
    camera_info_topic: str = "/realsense_camera_node/sn408122070053/camera_info"
    position_field: str = "pos_body"
    orientation_field: str = "ori_body"
    mode_field: str = "temp"
    mode_index: int = 10
    command_target_state_field: str = "target_state"
    orientation_quaternion_order: str = "wxyz"
    default_battery_soc: float = 100.0
    stand_down_state: int = 1
    stand_up_state: int = 2
    walk_mode_state: int = 4
    map_source_dir: str = "/home/robot/Documents"
    map_target_dir: str = "./hexplorer/maps"
    map_backup_dir: str = "./backups/hexplorer-maps"

@dataclass
class VideoConfig:
    enabled: bool = False
    # Local base used by the adapter to pull snapshots (same host as the adapter).
    web_video_server_url: str = "http://127.0.0.1:9001"
    # Base advertised to the FMS for live streams; must be reachable from the FMS.
    # Falls back to web_video_server_url when empty.
    web_video_server_public_url: str = ""
    stream_type: str = "mjpeg"
    stream_topics: List[str] = field(default_factory=list)
    snapshot_events: List[str] = field(default_factory=list)
    snapshot_topic: str = ""
    snapshot_quality: int = 40
    snapshot_mqtt_topic: str = "event_snapshot"
    snapshot_debounce_sec: float = 5.0
    snapshot_http_timeout_sec: float = 3.0

@dataclass
class JibotClientConfig:
    # Credentials and connection settings for JIBOT (urobot 7273 protocol).
    # WARNING: user/password are stored in plaintext. Do NOT commit real
    # credentials — override in a gitignored TOML (e.g. jibot-config.toml) or
    # pass via the [jibot_client] section in a per-instance config file.
    user: str = "test"
    password: str = "test"
    device_type: str = "pc"
    command_timeout_sec: float = 3.0
    recv_buffer_bytes: int = 32768
    status_log_interval_sec: float = 5.0
    battery_log_interval_sec: float = 30.0
    startup_battery_soc: float = 100.0
    require_battery_before_ready: bool = True


@dataclass
class FactsheetConfig:
    series_name: str = "JIBOT"
    agv_kinematic: str = "DIFF"
    agv_class: str = "CARRIER"
    localization_types: List[str] = field(default_factory=lambda: ["NATURAL"])
    navigation_types: List[str] = field(default_factory=lambda: ["AUTONOMOUS"])
    coordinate_unit_position: str = "mm"
    coordinate_unit_orientation: str = "deg"
    speed_min: float = 0.0
    min_order_interval_sec: float = 1.0

@dataclass
class WebUiConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 9000
    credentials_path: str = "config/web-credentials.toml"
    http_request_timeout_sec: float = 10.0
    shutdown_timeout_sec: float = 2.0
    log_read_timeout_sec: float = 5.0
    state_stale_threshold_sec: float = 15.0
    max_post_body_bytes: int = 65536
    page_default_refresh_sec: int = 5
    test_output_poll_sec: int = 2
    password_min_length: int = 12
    password_forbidden_tokens: List[str] = field(
        default_factory=lambda: ["set-me", "changeme", "password", "admin"]
    )
    camera_default_port: int = 9001

@dataclass
class InternalActionsConfig:
    # WARNING: FMS contract — do NOT change on existing deployments. The FMS
    # learns these synthetic action id/type for docking status; changing them
    # breaks integration.
    docking_status_action_id: str = "__jibot_docking__"
    docking_status_action_type: str = "dock"

@dataclass
class AdapterInstance:
    name: str
    vendor: str = "jibot"
    config: Optional[str] = None

@dataclass
class AdapterConfig:
    vendor: str = "jibot"
    instances: List[AdapterInstance] = field(default_factory=list)

@dataclass
class ActionPluginConfig:
    action_type: str
    enabled: bool = True
    runner: str = "inline"
    module: Optional[str] = None
    command: List[str] = field(default_factory=list)
    timeout_sec: float = 0.0
    motion: bool = False
    snapshot_fields: List[str] = field(default_factory=list)

@dataclass
class ActionModuleConfig:
    module: str
    enabled: bool = True


@dataclass
class StateActionCallConfig:
    action: str
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StateActionConfig:
    state: str
    start: Optional[StateActionCallConfig] = None
    end: Optional[StateActionCallConfig] = None

@dataclass
class RecipeStep:
    extension: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    timeout_sec: float = 0.0
    retry: int = 0
    retry_delay_sec: float = 0.0
    # Wait after this step succeeds, before the next one starts. Momentary
    # outputs need it: without a delay the on->off gap is just the round trip.
    # Clamped by whatever budget the step ran under, and skipped on failure.
    delay_sec: float = 0.0

@dataclass
class RecipeConfig:
    action_type: str
    steps: List[RecipeStep] = field(default_factory=list)
    cleanup: List[RecipeStep] = field(default_factory=list)
    enabled: bool = True
    timeout_sec: float = 0.0
    cleanup_timeout_sec: float = 5.0
    motion: bool = False
    label: str = ""

@dataclass
class PioAdvancedConfig:
    """Advanced PIO timing knobs. All defaults match the previous hard-coded literals."""
    init_default_timeout_sec: float = 2.0       # _pio_init fallback when action omits timeoutSec
    read_default_timeout_sec: float = 2.0       # _pio_read_inputs fallback when action omits timeoutSec
    write_output_timeout_sec: float = 2.0       # _pio_write_output default wait_sec
    scenario_default_timeout_sec: float = 2.0   # _execute_pio_scenario step fallback when action omits timeoutSec
    call_poll_interval_sec: float = 0.05        # _wait_pio_input poll cadence
    # Pairing knobs. The facility rarely raises GO on the first BC, so pairing
    # retries until pair_timeout_sec is spent -- the same shape as
    # airshower/elevator handle_pairing (timeout_paring_requesting = 0.5 min).
    pair_timeout_sec: float = 30.0              # total budget for BC retries until GO rises
    select_settle_sec: float = 0.2              # settle delay around SELECT on/off
    # after_go: SELECT를 유지한 채 BC를 재전송하고 GO가 올라온 뒤 해제.
    # after_bc: 예전 동작처럼 BC 응답 직후 SELECT를 해제하고 GO를 확인.
    select_off_timing: str = "after_bc"
    select_off_delay_sec: float = 0.0            # 해제 조건 성립 후 SELECT OFF까지 유지
    # bc_reply: 유효한 BC 응답을 연결 성공으로 판정(GO는 후행 상태 신호).
    # go: GO 입력 ON까지 확인해야 연결 성공.
    pair_confirmation: str = "bc_reply"
    ping_default_timeout_sec: float = 5.0       # pioPing fallback when action omits timeoutSec
    ping_output_hold_sec: float = 0.2           # pioPing hold between out1-8 on/off writes
    unpair_default_timeout_sec: float = 5.0     # pioDisconnect GO-off wait
    # PIOMaster (utils/pio.py) util-side knobs (Task 5.6b)
    socket_timeout_sec: float = 0.2             # serial.Serial(timeout=...) read timeout
    connect_delay_sec: float = 0.5              # time.sleep after serial connect
    read_frame_poll_sec: float = 0.05           # sleep cadence inside read_frames loop
    read_frames_wait_sec: float = 2.0           # default wait_sec for read_frames
    send_wait_sec: float = 2.0                  # default wait_sec for send_and_read / send_* helpers


@dataclass
class Config:
    mqtt_broker: MqttBrokerConfig
    vehicle: VehicleConfig
    settings: Settings
    jibot_status: JibotStatus
    ezi_config: EziConfig
    sound_settings: SoundSettings
    manual_control: ManualControlSettings
    charge: ChargeConfig
    dock: DockConfig
    motion_rules: List[MotionRule]
    charge_circuit: ChargeCircuitConfig
    bms_ros: BmsRosConfig
    pio_config: PioConfig
    air_shower_config: AirShowerPioConfig
    elevator_config: ElevatorPioConfig
    hexplorer: HexplorerConfig
    video: VideoConfig
    adapter: AdapterConfig
    jibot_client: JibotClientConfig = field(default_factory=JibotClientConfig)
    web_ui: WebUiConfig = field(default_factory=WebUiConfig)
    factsheet: FactsheetConfig = field(default_factory=FactsheetConfig)
    internal_actions: InternalActionsConfig = field(default_factory=InternalActionsConfig)
    pio_advanced: PioAdvancedConfig = field(default_factory=PioAdvancedConfig)
    actions: List[ActionPluginConfig] = field(default_factory=list)
    action_modules: List[ActionModuleConfig] = field(default_factory=list)
    recipes: List[RecipeConfig] = field(default_factory=list)
    state_actions: List[StateActionConfig] = field(default_factory=list)
    joystick: "JoystickConfig" = field(default_factory=lambda: JoystickConfig())


@dataclass
class JoystickActionConfig:
    slot: int
    action: str
    target: str = "action"
    parameters: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class JoystickConfig:
    profile: str = "ultimate2"
    enabled: bool = False
    vendor_id: str = "2dc8"
    # The receiver re-enumerates by controller state: 310b awake, 3109 asleep.
    # 3109 is the id nothing can be read from, so it must not be the default.
    product_id: str = "310b"
    device_name_contains: str = "8BitDo"
    forward_axis: int = 5
    reverse_axis: int = 2
    steering_axis: int = 0
    stop_button: int = 5
    speed_down_button: int = 6
    speed_up_button: int = 7
    dpad_x_axis: int = 6
    dpad_y_axis: int = 7
    a_button: int = 0
    b_button: int = 1
    x_button: int = 2
    y_button: int = 3
    trigger_threshold: float = 0.1
    # -/+ scale the manual drive. 100 % is the configured manual_control
    # magnitude; the floor is never 0, which would read as a dead controller.
    speed_step_percent: int = DEFAULT_SPEED_STEP_PERCENT
    speed_min_percent: int = DEFAULT_SPEED_MIN_PERCENT
    speed_max_percent: int = DEFAULT_SPEED_MAX_PERCENT
    speed_start_percent: int = DEFAULT_SPEED_START_PERCENT
    heartbeat_timeout_ms: int = 400
    ultimate2_enabled: bool = False
    steering_deadzone: float = 0.15
    joystick_reconnect_sec: float = 1.0
    xboxdrv_device_name: str = "Xbox Gamepad (userspace driver)"
    xboxdrv_forward_axis_code: int = 9
    xboxdrv_reverse_axis_code: int = 10
    xboxdrv_steering_axis_code: int = 0
    xboxdrv_stop_button_code: int = 311
    xboxdrv_dpad_x_code: int = 16
    xboxdrv_dpad_y_code: int = 17
    xboxdrv_a_button_code: int = 304
    xboxdrv_b_button_code: int = 305
    xboxdrv_x_button_code: int = 307
    xboxdrv_y_button_code: int = 308
    xboxdrv_speed_down_button_code: int = 314
    xboxdrv_speed_up_button_code: int = 315
    # The xboxdrv uinput pad is virtual: it outlives the receiver it bridges and
    # simply goes quiet, so a held drive would be re-sent by the heartbeat until
    # the supervisor tears xboxdrv down. Re-check the real USB receiver instead.
    # Silence cannot be the signal — a steadily held trigger is silent too.
    xboxdrv_receiver_guard: bool = True
    # Master switch for the D-pad+ABXY slot actions only.
    # Off by default: these fire real motor/clamp/charge actions, so enabling
    # them has to be a deliberate per-robot decision, not an upgrade side effect.
    actions_enabled: bool = False
    micro_enabled: bool = False
    micro_device_name: str = "Pro Controller"
    micro_dpad_x_code: int = 0
    micro_dpad_y_code: int = 1
    micro_neutral_threshold: int = 1000
    micro_stop_button: int = 311
    micro_reconnect_sec: float = 1.0
    actions: List[JoystickActionConfig] = field(default_factory=list)


_CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"
#: config_path 없이 부를 때 로더가 실제로 읽는 파일. 보고 경로(main.config_error_path)가
#: 같은 이름을 쓸 수 있도록 공개한다 — configPath가 None으로 나가면 운영자에게
#: 단서가 아예 남지 않는다(extensions/recipes의 DEFAULT_*_PATH와 같은 역할).
DEFAULT_CONFIG_PATH = _CONFIG_PATH
_JIBOT_CONFIG_PATH = Path(__file__).resolve().parent / "jibot-config.toml"

#: 이 로봇의 extensions.hcl/recipes.hcl이 깨졌을 때 **보고용** config를 만들려고
#: 대신 읽는 출하 파일. 운행에는 쓰이지 않는다 — get_config_with_fallback이
#: 브로커와 serialNumber를 확보해 config-error 모드로 넘기는 용도뿐이다.
#: 로봇 파일을 그대로 재시도하면 같은 이유로 또 실패하므로 example을 쓴다.
_REPORTING_EXTENSIONS_PATH = Path(__file__).resolve().parent / "extensions.hcl.example"
_REPORTING_RECIPES_PATH = Path(__file__).resolve().parent / "recipes.hcl.example"

#: extensions.hcl만 소유하는 PIO 계열 섹션. config.toml에 남아 있으면 부팅을
#: 멈춘다. [ezi]는 PIO가 아니라 여기 없다 — 같은 규칙을 걸려면 별도 결정이 필요하다.
_PIO_SECTIONS = ("pio", "pio_advanced", "air_shower_pio", "elevator_pio")

# station_id/channel은 상대 설비를 고르는 값이라 설비 블록이 소유한다(2026-08-15).
# 로더가 미지의 키를 거부하므로 그냥 두면 PioConfig가 TypeError로 죽어 무엇을
# 고쳐야 하는지 알 수 없다.
#
# channel의 owner는 station_id와 다르다: station_id는 motion_rules 항목마다
# 다르지만(층마다 다른 설비), channel은 엘리베이터 시스템 하나에 하나라
# extension "elevator" 블록 레벨에 있다 — motion_rules에는 channel 필드 자체가
# 없다. 여기를 "motion_rules의 channel"로 잘못 적으면 운영자가 여섯 개 규칙에
# channel을 적고, ElevatorMotionRule은 명시된 키로만 조립되므로 조용히
# 버려지고, elevator_raw["channel"]이 라벨 없는 KeyError로 죽는다 — 이 guard가
# 막으려는 바로 그 크래시로 이어진다.
_MOVED_PIO_KEYS = {
    "station_id": 'extension "airshower"의 pio_station_id, '
                  'extension "elevator" motion_rules의 pio_station_id',
    "channel": 'extension "airshower"의 channel, '
               'extension "elevator"의 channel',
}


def _adapter_from_dict(adapter_dict: Mapping[str, Any]) -> AdapterConfig:
    """Build AdapterConfig from the raw ``[adapter]`` TOML table.

    vendor 기본값은 "jibot"이고, [[adapter.instances]]는 인스턴스 목록이 된다.
    """
    instances = [
        AdapterInstance(**inst) for inst in adapter_dict.get("instances", [])
    ]
    return AdapterConfig(
        vendor=adapter_dict.get("vendor", "jibot"),
        instances=instances,
    )


def _deep_merge(base: Dict[str, Any], overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``overrides`` into ``base`` (returns ``base``).

    ``overrides``를 ``base``에 재귀적으로 덮어쓴다. 같은 키가 둘 다 dict면
    하위까지 병합하고, 그 외에는 override 값으로 교체한다. CLI에서 넘어온
    인스턴스별 설정(serial_number, vehicle_ip, ezi_io ...)을 적용할 때 쓴다.
    """
    for key, value in overrides.items():
        if value is None:
            continue
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base



#: 섹션 이름 -> 파일에 실제로 적혀 있는 자리 이름. 메시지가 운영자가 여는 파일의
#: 표기를 그대로 써야 그 줄을 찾을 수 있다. extensions.hcl은 extension 블록,
#: config.toml/robots.hcl은 TOML/HCL 섹션 표기다.
_EXTENSION_SECTION_LABELS = {
    "pio": 'extension "pio"',
    "pio_advanced": 'extension "pio"의 advanced',
    "ezi": 'extension "ezi"',
    "air_shower_pio": 'extension "airshower"',
    "elevator_pio": 'extension "elevator"',
}


def _section(cls, values, name, origins, lenient=False):
    """섹션 dict를 dataclass로 만들되, 모르는 키는 라벨 붙은 ConfigError로 올린다.

    dataclass에 그대로 ``**``를 하면 모르는 키 하나가 라벨 없는 TypeError가 된다.
    TypeError는 ConfigError가 아니라서 get_config_with_fallback의 보고용 경로를
    타지 못하고, 같은 자리에서 계속 죽는 systemd 크래시 루프가 된다 (2026-08-22
    현장: extension "pio"의 input_signals 한 줄로 부팅 불가). 옛 키를 하나씩 손으로
    열거하는 방식(pio_port, station_id, channel...)은 다음에 나올 모르는 키를
    영원히 못 잡으므로, 여기서 필드 이름 자체를 검증한다.

    ``origins``는 ``(파일 경로, HCL 블록 표기를 쓰는가, 그 파일이 준 섹션 dict들)``
    목록이다. 병합된 뒤에는 키가 어느 파일에서 왔는지 알 수 없으므로 병합 전
    스냅샷을 받아 "고쳐야 할 파일"을 짚는다 — 깨진 것이 extensions.hcl인데
    config.toml을 가리키면 운영자는 멀쩡한 파일을 뒤지게 된다.
    """
    known = {spec.name for spec in fields(cls)}
    unknown = sorted(set(values) - known)
    if unknown and lenient:
        # 보고 전용 config를 만드는 중이다. 이 config로는 vehicle/motor/IO를
        # 시작하지 않으므로(run_config_error_mode) 모르는 키는 버리고 브로커까지만
        # 닿으면 된다 — 여기서 또 죽으면 보고할 방법이 사라진다.
        values = {key: value for key, value in values.items() if key in known}
        unknown = []
    if unknown:
        raise _section_error(
            cls, name, unknown, origins,
            trouble=f"모르는 키가 있습니다: {', '.join(unknown)}",
            advice="오타라면 고치고, 더 이상 쓰지 않는 줄이라면 지우세요.",
        )

    try:
        return cls(**values)
    except TypeError as exc:
        # 필수 키가 빠진 경우. 그대로 두면 "__init__() missing 1 required
        # positional argument: 'media'" 같은 라벨 없는 TypeError가 되어 어느
        # 파일 어느 블록인지 알 수 없고, ConfigError가 아니라서 보고 경로도
        # 타지 못한다.
        required = sorted(
            spec.name
            for spec in fields(cls)
            if spec.name not in values
            and spec.default is MISSING
            and spec.default_factory is MISSING
        )
        if not required:
            raise
        raise _section_error(
            cls, name, required, origins,
            trouble=f"반드시 있어야 할 키가 없습니다: {', '.join(required)}",
            advice="해당 줄을 추가하세요.",
        ) from exc


def _section_error(cls, name, keys, origins, *, trouble, advice):
    """어느 파일 어느 블록을 고쳐야 하는지 짚은 ConfigError를 만든다."""
    known = sorted(spec.name for spec in fields(cls))
    source, hcl_style = _origin_of(name, keys, origins)
    if hcl_style:
        where = _EXTENSION_SECTION_LABELS.get(name, f"[{name}]")
    else:
        where = f"[{name}]"
    return ConfigError(
        f"{source}: {where}에 {trouble}. "
        f"쓸 수 있는 키: {', '.join(known)}. {advice}",
        path=source,
    )


def _origin_of(name, keys, origins):
    """문제가 된 키를 실제로 준 파일을 찾는다.

    빠진 키는 어느 파일에도 없으므로 못 찾는다 — 그때는 목록의 첫 파일을 댄다.
    경로가 비어 나가면 운영자에게 단서가 아예 없다. 마지막에 병합되는 파일이
    이기므로 뒤에서부터 본다.
    """
    for source, hcl_style, sections in reversed(origins):
        section = sections.get(name) or {}
        if any(key in section for key in keys):
            return str(source), hcl_style
    # 빠진 키는 어느 파일에도 없다. 그 섹션의 나머지 줄을 갖고 있는 파일이
    # 그 섹션의 주인이므로, 운영자가 열 파일은 그쪽이다.
    for source, hcl_style, sections in reversed(origins):
        if sections.get(name):
            return str(source), hcl_style
    source, hcl_style, _ = origins[0]
    return str(source), hcl_style


def get_config(
    config_path: Optional[Union[str, Path]] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    extensions_path: Optional[Union[str, Path]] = None,
    recipes_path: Optional[Union[str, Path]] = None,
    _lenient: bool = False,
) -> Config:
    """Load configuration from a TOML file, applying optional overrides.

    config.toml에서 설정을 읽고 필요하면 일부 값을 덮어쓴다. adaptor를 여러 개
    띄울 때 인스턴스마다 다른 TOML(``config_path``)을 쓰거나, 같은 TOML에
    serial_number/vehicle_ip/ezi_io 등만 다르게(``overrides``) 줄 수 있다.

    Args:
        config_path: TOML 경로. None이면 이 모듈 옆 config.toml.
        overrides: TOML 섹션 구조와 같은 중첩 dict.
            예) ``{"vehicle": {"serial_number": "...", "vehicle_ip": "..."}}``
        extensions_path: extensions.hcl 경로. None이면 config/extensions.hcl 기본값.
        recipes_path: recipes.hcl 경로. None이면 선택적인 기본 파일을 사용한다.
        _lenient: 모르는 키를 오류 대신 무시한다. get_config_with_fallback이
            **보고 전용** config를 만들 때만 쓴다 — 이 config로는 vehicle/motor/IO를
            시작하지 않으므로(main.run_config_error_mode) 값이 아니라 브로커까지
            닿는 것만 중요하다. 운행 경로에서는 절대 켜지 않는다.
    """
    path = Path(config_path) if config_path is not None else _CONFIG_PATH
    with open(path, "rb") as files:
        config_dict = tomllib.load(files)

    # JIBOT hardware-quirk overlay (shared across instances). Absent => defaults.
    if _JIBOT_CONFIG_PATH.exists():
        with open(_JIBOT_CONFIG_PATH, "rb") as jibot_files:
            _deep_merge(config_dict, tomllib.load(jibot_files))

    # extensions.hcl은 [pio]/[ezi]/[air_shower_pio]/[elevator_pio] 등 extension
    # 전용 설정의 단일 출처다. override(로봇별 ezi_io 등)보다 먼저 병합해야
    # 로봇별 값이 마지막에 이겨서 살아남는다. 파일이 없거나 잘못되면
    # ExtensionsError가 그대로 올라가 부팅이 멈춘다(무증상 기본값 방지).
    # 병합하면 어느 파일이 어느 키를 줬는지 사라진다. _section이 "고쳐야 할
    # 파일"을 짚을 수 있도록 병합 직전 스냅샷을 남긴다.
    _toml_sections = {
        key: dict(value)
        for key, value in config_dict.items()
        if isinstance(value, Mapping)
    }
    extensions_data = load_extensions(extensions_path)
    # 병합 전에 봐야 어느 파일이 무엇을 갖고 있는지 짚어줄 수 있다. PIO 계열
    # 섹션은 2b0ce69에서 extensions.hcl로 옮겨졌지만, 배포가 config.toml을
    # 보존하므로(CONFIG_TOML_MODE=keep) 그 이전 로봇에는 옛 섹션이 남아 있다.
    # 남아 있어도 조용히 병합되면 운영자가 extensions.hcl을 고쳐도 값이 안 바뀌는
    # 것처럼 보이므로, 부팅을 멈추고 지우라고 말한다.
    _stale = [name for name in _PIO_SECTIONS if config_dict.get(name)]
    if _stale:
        raise ExtensionsError(
            f"config.toml: [{'], ['.join(_stale)}] 섹션은 extensions.hcl로 "
            "옮겨졌습니다. config.toml에서 지우세요 — 값은 "
            'extensions.hcl의 extension "pio" 등이 갖습니다.'
        )
    _deep_merge(config_dict, extensions_data)

    if overrides:
        _deep_merge(config_dict, overrides)

    # 병합 순서와 같다: 뒤가 이긴다. _origin_of가 뒤에서부터 훑는다.
    _origins = [
        (path, False, _toml_sections),
        (extensions_path or DEFAULT_EXTENSIONS_PATH, True, extensions_data),
        (DEFAULT_FLEET_PATH, True, dict(overrides or {})),
    ]

    mqtt_broker = _section(MqttBrokerConfig, config_dict["mqtt_broker"], "mqtt_broker", _origins, _lenient)
    vehicle = _section(VehicleConfig, config_dict["vehicle"], "vehicle", _origins, _lenient)
    ezi_config = _section(EziConfig, config_dict["ezi"], "ezi", _origins, _lenient)
    settings = _section(Settings, config_dict["settings"], "settings", _origins, _lenient)
    jibot_status = _section(JibotStatus, config_dict["jibot_status"], "jibot_status", _origins, _lenient)
    sound_settings = _section(SoundSettings, config_dict["sound_settings"], "sound_settings", _origins, _lenient)
    manual_control = _section(ManualControlSettings, config_dict.get("manual_control", {}), "manual_control", _origins, _lenient)
    charge_settings = _section(ChargeConfig, config_dict.get("charge", {}), "charge", _origins, _lenient)
    dock_raw = dict(config_dict.get("dock", {}))
    approach_raw = dock_raw.pop("approach_params", {})
    dock_config = _section(DockConfig, dock_raw, "dock", _origins, _lenient)
    dock_config.approach_params = _section(
        DockApproachParams, approach_raw, "dock.approach_params", _origins, _lenient
    )
    # motion_rules는 config.toml만 갖는 최상위 목록이다(extensions.hcl/robots.hcl은
    # 섹션 안으로만 들어온다). 그래서 고쳐야 할 파일은 항상 이 TOML이다.
    motion_rules = _motion_rules_from_config(
        config_dict.get("motion_rules", []), path, _lenient
    )
    charge_circuit = _section(ChargeCircuitConfig, config_dict.get("charge_circuit", {}), "charge_circuit", _origins, _lenient)
    bms_ros = _section(BmsRosConfig, config_dict.get("bms_ros", {}), "bms_ros", _origins, _lenient)
    # pio_port -> pio_serial_port 개명(2026-07-29). 옛 키는 받지 않는다. 그대로
    # 두면 PioConfig가 TypeError로 죽어 무엇을 고쳐야 하는지 알 수 없다.
    # config.toml 쪽은 위 섹션 검사가 먼저 걸러내므로 여기 남는 건 extensions.hcl뿐.
    if "pio_port" in config_dict["pio"]:
        raise ExtensionsError(
            'extensions.hcl: extension "pio"의 pio_port는 pio_serial_port로 '
            "이름이 바뀌었습니다 (BC 프레임의 port 필드와 구분하기 위함). "
            "해당 줄을 pio_serial_port로 고치세요."
        )
    # 192.168.101.61처럼 station_id/channel 두 줄이 모두 남은 로봇은, 하나씩
    # 알려주면 운영자가 고치고 재시작한 뒤에야 다음 줄을 만난다. 한 번에 모아
    # 알려준다.
    _stale_moved_keys = [
        _key for _key in _MOVED_PIO_KEYS if _key in config_dict["pio"]
    ]
    if _stale_moved_keys:
        _offenders = "; ".join(
            f'{_key}는 {_MOVED_PIO_KEYS[_key]}로' for _key in _stale_moved_keys
        )
        raise ExtensionsError(
            f'extensions.hcl: extension "pio"의 {_offenders} 옮겨졌습니다. '
            "해당 줄을 지우고 설비 블록에 적으세요."
        )

    # 반대 방향 반쪽 마이그레이션: 옛 줄은 지웠지만 설비 블록에 새 키를 아직
    # 채우지 않은 경우. 그대로 두면 AirShowerPioConfig/ElevatorPioConfig가
    # 라벨 없는 TypeError/KeyError로 죽어 무엇을 고쳐야 하는지 알 수 없다
    # (실제로 확인됨: airshower에 pio_station_id/channel이 없으면
    # AirShowerPioConfig(**...)가 TypeError, elevator에 channel이 없으면
    # elevator_raw["channel"]이 KeyError). 기본값은 주지 않는다 — 조용히
    # 채우면 엉뚱한 설비로 주소를 잡는다.
    _air_shower_raw_for_check = config_dict.get("air_shower_pio", {})
    _elevator_raw_for_check = config_dict.get("elevator_pio", {})
    _missing_facility_lines = []
    if "pio_station_id" not in _air_shower_raw_for_check:
        _missing_facility_lines.append(
            'extension "airshower"에 pio_station_id = "<이 설비의 station id>"'
        )
    if "channel" not in _air_shower_raw_for_check:
        _missing_facility_lines.append(
            'extension "airshower"에 channel = <이 설비의 channel>'
        )
    if "channel" not in _elevator_raw_for_check:
        _missing_facility_lines.append(
            'extension "elevator"에 channel = <이 설비의 channel>'
        )
    if _missing_facility_lines:
        raise ExtensionsError(
            "extensions.hcl: BC 상대 주소를 이루는 키가 설비 블록에 없습니다 — "
            + "; ".join(_missing_facility_lines)
            + " 줄을 추가하세요."
        )

    pio_config = _section(PioConfig, config_dict["pio"], "pio", _origins, _lenient)
    air_shower_config = _section(AirShowerPioConfig, config_dict["air_shower_pio"], "air_shower_pio", _origins, _lenient)

    elevator_raw = config_dict["elevator_pio"]
    # 이름을 motion_rules로 두면 위에서 만든 dock/move 룰을 덮어써서
    # Config.motion_rules에 엘리베이터 룰이 들어간다. 그러면 _dock_segment_rule이
    # dock 룰을 못 찾아 UmDock이 발화하지 않는다.
    elevator_motion_rules = [
        ElevatorMotionRule(
            from_=rule["from"],
            to=rule["to"],
            mode=rule["mode"],
            floor_pin=rule["floor_pin"],
            pio_station_id=rule["pio_station_id"],
        )
        for rule in elevator_raw.get("elevator_motion_rules", [])
    ]
    # 키를 하나씩 꺼내 쓰면 빠진 줄은 라벨 없는 KeyError가 되고, 남은 옛 줄은
    # 조용히 무시된다 — 둘 다 다른 섹션과 같은 검증을 받아야 한다.
    elevator_values = dict(elevator_raw)
    elevator_values["elevator_motion_rules"] = elevator_motion_rules
    elevator_config = _section(
        ElevatorPioConfig, elevator_values, "elevator_pio", _origins, _lenient
    )

    hexplorer = _section(HexplorerConfig, config_dict.get("hexplorer", {}), "hexplorer", _origins, _lenient)
    video = _section(VideoConfig, config_dict.get("video", {}), "video", _origins, _lenient)
    adapter = _adapter_from_dict(config_dict.get("adapter", {}))
    jibot_client = _section(JibotClientConfig, config_dict.get("jibot_client", {}), "jibot_client", _origins, _lenient)
    web_ui = _section(WebUiConfig, config_dict.get("web_ui", {}), "web_ui", _origins, _lenient)
    factsheet = _section(FactsheetConfig, config_dict.get("factsheet", {}), "factsheet", _origins, _lenient)
    internal_actions = _section(InternalActionsConfig, config_dict.get("internal_actions", {}), "internal_actions", _origins, _lenient)
    pio_advanced = _section(PioAdvancedConfig, config_dict.get("pio_advanced", {}), "pio_advanced", _origins, _lenient)
    actions = [
        ActionPluginConfig(**action)
        for action in config_dict.get("actions", [])
    ]
    action_modules = [
        ActionModuleConfig(**module)
        for module in config_dict.get("action_modules", [])
    ]
    state_actions = [
        StateActionConfig(
            state=entry["state"],
            start=(
                StateActionCallConfig(**entry["start"])
                if entry.get("start") is not None
                else None
            ),
            end=(
                StateActionCallConfig(**entry["end"])
                if entry.get("end") is not None
                else None
            ),
        )
        for entry in config_dict.get("state_actions", [])
    ]
    joystick_raw = dict(config_dict.get("joystick", {}))
    joystick_actions = [
        JoystickActionConfig(**entry)
        for entry in joystick_raw.pop("actions", [])
    ]
    joystick = _section(JoystickConfig, joystick_raw, "joystick", _origins, _lenient)
    joystick.actions = joystick_actions
    recipes = [
        RecipeConfig(
            **{
                **recipe,
                "steps": [RecipeStep(**step) for step in recipe["steps"]],
                "cleanup": [RecipeStep(**step) for step in recipe["cleanup"]],
            }
        )
        for recipe in load_recipes(recipes_path)
    ]

    return Config(
        mqtt_broker=mqtt_broker,
        vehicle=vehicle,
        settings=settings,
        jibot_status=jibot_status,
        ezi_config=ezi_config,
        sound_settings=sound_settings,
        manual_control=manual_control,
        charge=charge_settings,
        dock=dock_config,
        motion_rules=motion_rules,
        charge_circuit=charge_circuit,
        bms_ros=bms_ros,
        pio_config=pio_config,
        air_shower_config=air_shower_config,
        elevator_config= elevator_config,
        hexplorer=hexplorer,
        video=video,
        adapter=adapter,
        jibot_client=jibot_client,
        web_ui=web_ui,
        factsheet=factsheet,
        internal_actions=internal_actions,
        pio_advanced=pio_advanced,
        actions=actions,
        action_modules=action_modules,
        recipes=recipes,
        state_actions=state_actions,
        joystick=joystick,
    )


def get_config_with_fallback(
    config_path: Optional[Union[str, Path]] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    extensions_path: Optional[Union[str, Path]] = None,
    recipes_path: Optional[Union[str, Path]] = None,
) -> Tuple[Config, Optional[BaseException]]:
    """Load config, falling back to base config.toml when a per-instance file fails.

    Returns ``(config, error)``:

    * Normal load succeeds -> ``(config, None)``.
    * 로드가 실패하면 -> 출하 기본 extensions/recipes와 base ``config.toml``로
      한 단계씩 물러나며 같은 ``overrides``로 다시 읽어, 이 로봇의
      serialNumber/broker만 살아 있는 **보고용** config를 만들고
      ``(reporting_config, primary_error)``를 돌려준다. 호출부는 이것으로
      config-error 모드(FATAL CONFIG_LOAD_FAILED 발행)를 돌린다.
    * 어떤 단계로도 보고용 config를 만들지 못하면 -> 예외가 그대로 올라가
      호출부가 non-zero로 빠지고 systemd가 재시작한다.

    실패 이유의 **타입은 가리지 않는다**. 예전에는 ConfigError만 보고용 config를
    만들었고 그 밖의 예외(예: motion_rules 한 줄이 만든 TypeError)는 로봇별
    config.toml만 base로 바꿔 한 번 재시도했다 — 깨진 것이 extensions.hcl /
    recipes.hcl / base config.toml이면 같은 자리에서 또 죽어 부팅이 멈췄다
    (2026-08-23 20:44 현장: "missing 1 required positional argument: 'to'").
    검증을 새로 넣을 때마다 ConfigError로 감싸는 것을 잊으면 다시 그 상태가 되므로,
    타입이 아니라 "보고할 수 있느냐"로 가른다.
    """
    try:
        return (
            get_config(
                config_path=config_path,
                overrides=overrides,
                extensions_path=extensions_path,
                recipes_path=recipes_path,
            ),
            None,
        )
    except Exception as primary_exc:
        # 설정을 못 읽었다 (extensions.hcl / recipes.hcl / config.toml의 모르는
        # 키·빠진 키, 파일 없음, TOML/HCL 문법 오류 등). 로봇은 운행하면 안
        # 되지만, 여기서 죽어 버리면 systemd가 되살리고 같은 자리에서 또 죽는다 —
        # journalctl을 열 수 있는 사람만 원인을 아는 크래시 루프다. 브로커와
        # serialNumber만 있으면 무엇이 왜 멈췄는지 MQTT로 말할 수 있으므로, 말할
        # 수 있게 될 때까지 한 단계씩 물러나며 **보고용** config를 만든다.
        #
        # 대체 설정으로 무언가 구동될 걱정은 없다: 호출부는 이 error를 보고
        # config-error 모드로 들어가고 그 모드는 vehicle/motor/IO를 시작하지
        # 않는다. "잘못된 설정으로는 부팅 정지"는 그대로고, 정지 사실을 말할 수
        # 있게 될 뿐이다.
        for _attempt_config_path, _attempt_lenient in (
            # 1) 깨진 게 extensions.hcl/recipes.hcl이면 출하 기본 파일로 해결된다.
            (config_path, False),
            # 2) 깨진 게 로봇별 config.toml이면 base config.toml로 해결된다.
            (None, False),
            # 3) base config.toml에까지 모르는 키가 있으면(운영자가 직접 고치는
            #    파일이다) 그 키를 무시하고서라도 브로커까지는 닿는다.
            (config_path, True),
            (None, True),
        ):
            try:
                reporting = get_config(
                    config_path=_attempt_config_path,
                    overrides=overrides,
                    extensions_path=_REPORTING_EXTENSIONS_PATH,
                    recipes_path=_REPORTING_RECIPES_PATH,
                    _lenient=_attempt_lenient,
                )
            except Exception:
                continue
            return reporting, primary_exc
        # 어떤 단계로도 보고용 config를 못 만들면 보고할 브로커도 못 찾는다.
        # 원래 오류를 그대로 올려 호출부가 non-zero로 빠지게 둔다 — 운영자가
        # 고쳐야 할 것은 여전히 자기 설정 파일이고, 그 파일 이름은 메시지에 있다.
        raise primary_exc from None
