"""Jibot VDA5050 adapter implementation.

Translates ACS MQTT commands into robot actions and publishes VDA5050 state/connection messages.
"""

import asyncio
import atexit
import base64
import functools
import json
import os
import re
import time
import math
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Optional, List, Set, Tuple, Union
from datetime import datetime

CLIENT_SRC = Path(__file__).resolve().parents[1] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

_JIBOT_UM_TEST_ACTION_TYPES = frozenset({"jibotUmGoto"})

from protocol.vda5050_common import AgvPosition, ErrorLevel
from protocol.vda_2_0_0.vda5050_2_0_0_state import State, Load, NodeState, EdgeState, ActionState, BatteryState, SafetyState, OperatingMode, ActionStatus, EStop, Error, ErrorType, ErrorReference, Information, InfoLevel, InfoType, InfoReference
# from protocol.vda_2_0_0.vda5050_2_0_0_visualization import Visualization
from protocol.vda_2_0_0.vda5050_2_0_0_action import Action, ActionParameter, ActionParameterValue, BlockingType
from protocol.vda5050_3_0.messages import (
    ActionState as V3ActionState,
    ActionStatus as V3ActionStatus,
    Connection,
    ConnectionState,
    EmergencyStop,
    Header,
    InstantActions,
    MobileRobotPosition,
    OperatingMode as V3OperatingMode,
    Order,
    PowerSupply,
    SafetyState as V3SafetyState,
    State as V3State,
)
from protocol.vda5050_3_0.transport import Vda5050V3Transport

from utils.charge_circuit import ChargeCircuit, NullChargeCircuit
from utils.jibot_error_catalog import decode_system_error_code
from utils.mqtt_client import MQTTClient
from utils.sound import SoundPlayer
from utils.video import VideoStreamer
from jibot_client import JIBOT
from utils.ezi_io import EZIIOClient
from utils.ezi_motor import EziMotorClient
import utils.helpers as utils
from core import configio, ipc_paths

from config.config import Config, get_config
from core.action_modules import first_party_action_specs
from core.action_registry import ActionResult, build_registry_from_config
from core.coalescing import drivable_run
from core.waypoint_pass import segment_passes_within
from core.state_actions import StateActionController, validate_state_actions
from utils.joystick import validate_joystick_actions
from core.factsheet import INSTANT_ACTION_TYPES, build_factsheet, merge_instant_action_types
from extensions.charge import release_charge_in_place
from extensions.charge import run_charge_in_place
from extensions.charge import should_charge_in_place
from extensions.hardware import execute_order_action as execute_hardware_order_action
from extensions.hardware import is_hardware_action
from extensions.hardware import start_sensor_service as start_hardware_sensor_service
from jibot_params import DEFAULT_BACKUP_ROOT, DEFAULT_SOURCE_DIR, DEFAULT_TARGET_DIR, sync_jibot_params
from amr_map_publish import extract_request_id, get_map_and_publish
from amr_parameter_publish import (
    apply_parameter_changes,
    build_all_items,
    build_apply_result,
    build_parameter_snapshot,
    group_changes_by_source,
    publish_apply_result,
    publish_parameters,
    read_source_text_with_reason,
)
from common_amr_map import to_jibot_snapshot
from jibot_map_writer import write_jibot_map


#: 오더 수명에만 의미가 있는 거절 사유들. 오더가 취소/폐기되면 같이 지운다.
#:
#: cancelOrder 가 order_id 는 비우면서 이 경고들은 state.errors 에 남겨 두는 바람에,
#: FMS 는 취소된 뒤에도 "Current order is still in progress" 를 계속 받았다
#: (2026-08-25 07:02 HN-SH6-TR-002: orderId="" 인데 06:58:59 발생한
#: ORDER_CURRENT_NOT_FINISHED 가 그대로 발행 중).
#:
#: ORDER_JSON_PAYLOAD_INVALID / ACTION_* 는 넣지 않는다. 페이로드 파싱 실패는 특정
#: 오더의 수명에 묶인 상태가 아니라 FMS 쪽 결함 신호라, 지우면 원인을 감춘다.
_ORDER_SCOPED_ERROR_TYPES = (
    ErrorType.ORDER_CURRENT_NOT_FINISHED,
    ErrorType.ORDER_UPDATE_ID_INVALID,
    ErrorType.ORDER_START_NODE_INVALID,
    ErrorType.ORDER_START_SEQUENCE_ID_INVALID,
)


def _validate_hcl_on_disk(path: Any) -> Tuple[bool, str]:
    """저장된 HCL 파일이 여전히 파싱되는지 확인한다.

    부팅 로더(get_config)가 읽지 않는 파일(robots.hcl)에만 쓴다. get_config 가 읽는
    파일은 :func:`_validate_parameter_sources` 로 의미까지 검증해야 한다 — 문법만 보면
    "파싱은 되는데 부팅은 안 되는" 파일이 통과한다.

    :param path: 검사할 HCL 파일 경로
    :returns: (성공 여부, 메시지)
    """
    try:
        import hcl2

        with open(path, "r", encoding="utf-8") as handle:
            hcl2.load(handle)
        return True, "hcl valid"
    except Exception as exc:  # noqa: BLE001 - 로더 오류를 그대로 운영자에게 전달
        return False, f"{type(exc).__name__}: {exc}"


def _validate_parameter_sources(paths: Dict[str, Path]) -> Tuple[bool, str]:
    """저장된 설정 파일들이 **실제 부팅 로더**를 통과하는지 확인한다.

    ``hcl2.load`` 문법 검사로는 부족하다. recipes.hcl 은 중복 recipe 이름·모르는 필드·
    음수 timeout 을, extensions.hcl 은 모르는 extension 블록을 부팅 시점에 거부한다.
    문법만 보면 이런 파일이 통과해서 다음 재시작 때 어댑터가 못 뜬다. 값만 고칠 때는
    드러나지 않았지만 recipe/extension 블록을 통째로 추가·삭제하는 순간 흔한 실패가 된다.

    세 파일을 한꺼번에 검증하는 이유는 부팅이 그렇게 하기 때문이다. recipes.hcl 이 깨진
    상태에서 config.toml 편집만 통과시키면 "적용은 됐는데 재시작하면 죽는" 상태가 남는다.
    경로를 전부 넘기는 것도 중요하다 — 기본 경로로 검증하면 멀티로봇에서 남의 파일을 본다.

    :param paths: source → 파일 경로(``self._parameter_paths``)
    :returns: (성공 여부, 메시지)
    """
    kwargs: Dict[str, Any] = {}
    # 없는 파일 경로를 넘기면 로더가 "파일 없음"으로 실패한다. 기본값 동작에 맡긴다
    for source, keyword in (("extensions.hcl", "extensions_path"), ("recipes.hcl", "recipes_path")):
        path = paths.get(source)
        if path is not None and Path(path).exists():
            kwargs[keyword] = path
    try:
        config = get_config(config_path=paths.get("config.toml"), **kwargs)
        registry = build_registry_from_config(
            getattr(config, "actions", []),
            first_party_specs=first_party_action_specs(config),
        )
        validate_state_actions(getattr(config, "state_actions", ()), registry)
        validate_joystick_actions(getattr(config, "joystick", None), registry)
        return True, "config valid"
    except Exception as exc:  # noqa: BLE001 - 로더 오류를 그대로 운영자에게 전달
        return False, f"{type(exc).__name__}: {exc}"


class _NoParameterChange(Exception):
    """적용할 변경이 하나도 없을 때. write_config 안에서 던져 파일을 건드리지 않게 한다."""


_IO_WRITE_INTERVAL_SEC = 2.5

# VDA5050 requires batteryState.batteryCharge / powerSupply.stateOfCharge to be a
# number, so we cannot send null when the JIBOT link is down and no real reading
# was ever received. Publish this out-of-range sentinel instead of a misleading
# placeholder (which would falsely read as a full battery). The dashboard maps
# any negative SoC back to "unknown". See core/monitor.py.
_BATTERY_CHARGE_UNKNOWN = -1.0


@dataclass
class OrderStep:
    kind: str
    sequence_id: int
    item: Any
    # True for the order's start node when the robot already stands on it: the
    # motion is skipped but the node's actions still run. VDA5050 start nodes
    # carry work that must happen before departure (a BEFORE_LEAVE_NODE clamp,
    # say), and driving to a node the robot occupies would re-trigger its dock.
    actions_only: bool = False


class _NodeMotionStallGuard:
    """노드 주행 중 "아무도 로봇을 몰고 있지 않은 정지"를 감지해 goto 를 되살린다.

    도착 판정(_wait_until_node_position_reached)과 통과 판정
    (_wait_until_node_passed)은 판정 기준만 다를 뿐 복구 정책은 같아야 한다.
    두 벌로 두면 한쪽만 고쳐지고 다른 쪽이 조용히 매달린다 — 통과 판정은
    특히 위험하다. 코너를 크게 도는 로봇은 노드 반경 안에 영영 못 들어올 수
    있는데(spec §3.2 는 코너에서 구간을 끊지 않는다), 그때 FMS 로 나가는
    에러가 없으면 오더가 이유 없이 멈춰 있는 것으로 보인다.

    로그 태그는 두 경로가 같은 것을 쓴다. 같은 사건(노드 주행 중 stall)이라
    태그를 나누면 오히려 grep 으로 한 번에 못 본다.
    """

    def __init__(
        self,
        adapter: "Adapter",
        node: Any,
        target_x: float,
        target_y: float,
        radius: float,
        goto_recoverable: bool,
    ) -> None:
        self._adapter = adapter
        self._node = node
        self._target_x = target_x
        self._target_y = target_y
        self._radius = radius
        self._goto_recoverable = goto_recoverable
        settings = adapter.config.settings
        self._unreached_delay = float(
            getattr(settings, "node_unreached_delay_sec", 1.0)
        )
        self._retry_delay = float(
            getattr(settings, "node_goto_retry_delay_sec", 3.0)
        )
        self._retry_limit = max(
            0, int(getattr(settings, "node_goto_retry_limit", 3))
        )
        self.stalled_since = time.monotonic()
        self.retries_used = 0
        self.unreached_error_reported = False
        self.gave_up = False

    async def observe(self, vx: float, vy: float) -> None:
        """포즈 표본 하나를 먹인다. 목적지에 아직 못 간 표본에만 부른다."""
        adapter = self._adapter
        node = self._node
        # 스톨 시계는 "로봇이 서 있고, 그 주인이 우리 말고 아무도 없을 때"만
        # 돈다. 장애물 브레이크, 조이스틱, stopPause 는 전부 정당하게 여기
        # 머무는 이유이고 우리가 끊을 것이 아니다. _manual_control_active 는
        # 어댑터가 내보낸 조그를 덮는다 — JIBOT 의 폴링된 mode 보다 먼저 움직인다.
        stopped = adapter._is_jibot_stopped()
        stalled = (
            stopped
            and not adapter._is_jibot_obstacle_wait()
            and not adapter._is_jibot_manual_drive()
            and not adapter._manual_control_active
            and not adapter._motion_paused
        )
        if not stalled:
            self.stalled_since = time.monotonic()
            if not stopped:
                # 실제 진행. 이 노드는 복구 예산을 새로 받는다 — 중간에 여러 번
                # 방해받는 긴 경로가 예산을 다 써 버리지 못하게 한다.
                self.retries_used = 0
                self.unreached_error_reported = False
                self.gave_up = False
            return

        station = str(getattr(adapter._vehicle, "_station", "") or "").strip()
        stalled_for = time.monotonic() - self.stalled_since
        if stalled_for >= self._unreached_delay and not self.unreached_error_reported:
            adapter._set_jibot_node_unreached_error(
                node,
                self._target_x,
                self._target_y,
                vx,
                vy,
                self._radius,
                station,
            )
            self.unreached_error_reported = True
            print(
                f"[ORDER NODE UNREACHED] id={node.node_id} "
                f"station={station or '<empty>'} "
                f"status={getattr(adapter._vehicle, '_status', '')} "
                f"pos=({vx:.1f}, {vy:.1f}) "
                f"target=({self._target_x:.1f}, {self._target_y:.1f}) "
                f"radius={self._radius:.1f}"
            )
        if self._goto_recoverable and stalled_for >= self._retry_delay:
            if self.retries_used < self._retry_limit:
                self.retries_used += 1
                print(
                    f"[ORDER NODE GOTO RETRY] id={node.node_id} "
                    f"attempt={self.retries_used}/{self._retry_limit} "
                    f"pos=({vx:.1f}, {vy:.1f})"
                )
                await adapter._resend_node_goto(node)
                self.stalled_since = time.monotonic()
                self.unreached_error_reported = False
            elif not self.gave_up:
                self.gave_up = True
                adapter._set_jibot_node_unreached_error(
                    node,
                    self._target_x,
                    self._target_y,
                    vx,
                    vy,
                    self._radius,
                    station,
                    error_level=ErrorLevel.FATAL,
                    retries=self.retries_used,
                )
                print(
                    f"[ORDER NODE GOTO GAVE UP] id={node.node_id} "
                    f"retries={self.retries_used} "
                    f"pos=({vx:.1f}, {vy:.1f}) "
                    f"target=({self._target_x:.1f}, {self._target_y:.1f})"
                )


class Adapter:
    def __init__(
        self,
        config: Optional[Config] = None,
        config_path: Optional[Union[str, Path]] = None,
        extensions_path: Optional[Union[str, Path]] = None,
        recipes_path: Optional[Union[str, Path]] = None,
        robots_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.publish_state_task: Optional[asyncio.Task[Any]] = None
        self.subscribe_task: Optional[asyncio.Task[Any]] = None
        self.order_worker_task: Optional[asyncio.Task[Any]] = None
        self.load_task: Optional[asyncio.Task[Any]] = None
        self.connection_monitor_task: Optional[asyncio.Task[Any]] = None
        self.laser_publish_task: Optional[asyncio.Task[Any]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.order_queue: asyncio.Queue[OrderStep] = asyncio.Queue()
        self.current_order_step: Optional[OrderStep] = None


        # Use the injected config when running multiple adaptors so this
        # instance's serial_number/broker stay distinct; otherwise load the
        # shared config.toml.
        # 여러 adaptor를 띄울 때 인스턴스별 serial_number/broker가 섞이지 않도록
        # 주입된 config를 쓰고, 없으면 공용 config.toml을 읽는다.
        self.config = config if config is not None else get_config()
        self._config_path = Path(config_path) if config_path is not None else configio.CONFIG_PATH
        # setParameters 멱등성. instant-action 은 재전송될 수 있고, 재적용하면 그 사이
        # 다른 경로로 바뀐 값을 조용히 되돌린다. requestId → 결과 envelope
        self._applied_parameter_requests: Dict[str, Any] = {}
        # EPR 이 쓰는 source → 파일 경로. 멀티로봇에서 기본값을 쓰면 남의 파일을 고치므로
        # 주입값을 우선하고, 없으면 config.toml 옆 파일을 쓴다.
        config_dir = self._config_path.parent
        self._parameter_paths: Dict[str, Path] = {
            "config.toml": self._config_path,
            "extensions.hcl": Path(extensions_path) if extensions_path else config_dir / "extensions.hcl",
            "recipes.hcl": Path(recipes_path) if recipes_path else config_dir / "recipes.hcl",
            # robots.hcl 은 config.toml 의 vehicle/mqtt 값을 기동 시 덮어쓴다.
            # 이 파일을 빼면 그 필드들이 "고쳐도 재시작하면 원복" 상태로 남는다.
            "robots.hcl": Path(robots_path) if robots_path else config_dir / "robots.hcl",
        }
        self._action_registry = build_registry_from_config(
            getattr(self.config, "actions", []),
            first_party_specs=first_party_action_specs(self.config),
        )
        validate_joystick_actions(
            getattr(self.config, "joystick", None), self._action_registry
        )
        # Awaitable completion bridge for synthetic recipe child actions. These
        # IDs never enter VDA5050 instant_action_states.
        self._action_completions: Dict[str, asyncio.Future[Any]] = {}
        self._synthetic_action_ids: Set[str] = set()
        self._action_tasks: Dict[str, asyncio.Task[Any]] = {}
        # Terminal instant action states are retained so the FMS can read the
        # result, but the list has no order boundary to reset it, so cap it.
        self._max_retained_terminal_instant_action_states: int = 50
        # Order actions with NONE/SINGLE may outlive the node/edge worker step.
        # Track their queue runners so SINGLE/HARD exclusivity and cancelOrder
        # can include work started at an earlier action point.
        self._order_background_action_tasks: Set[asyncio.Task[Any]] = set()
        self._order_exclusive_background_action_tasks: Set[asyncio.Task[Any]] = set()
        # 워커가 노드 주행을 시작할 때마다 오른다. 배경에서 도는 조그 회전이
        # "내가 아직 로봇을 잡고 있나"를 판별하는 데 쓴다.
        self._order_node_motion_seq: int = 0
        # 현재 주문에서 이미 실행기로 넘긴 actionId 들.
        # VDA5050 은 actionId 를 주문당 한 번만 주므로, 두 번째 dispatch 는
        # 항상 이미 끝난(또는 도는 중인) 일의 재실행이다.
        self._dispatched_order_action_ids: Set[str] = set()
        # 지금 자기 노드의 액션을 실행 중인 워커 스텝.
        # 노드 도착이 액션 실행 '전에' lastNodeSequenceId 를 올리기 때문에,
        # 이게 없으면 stale 스텝 검사가 액션 실행 중인 스텝을 이미 지나간 것으로
        # 읽고 액션 도중에 취소한다 (_active_order_worker_step_is_obsolete 참고).
        self._order_action_step_in_flight: Optional[OrderStep] = None
        # 지금 코얼레싱 구간을 주행 중인 워커 스텝.
        # 중간 노드를 통과할 때마다 lastNodeSequenceId 가 올라가므로, 이게 없으면
        # stale 스텝 검사가 주행 중인 스텝을 이미 지나간 것으로 읽고 자기 주행을
        # 끊는다 (_active_order_worker_step_is_obsolete 참고).
        self._coalescing_run_step: Optional[OrderStep] = None
        # Recipe child actions are synthetic and intentionally absent from the
        # VDA5050 action-state arrays. Keep the currently executing child per
        # parent action so AMR_STATE and sound can still expose the real step.
        self._active_action_steps: Dict[str, str] = {}
        self._state_action_controller = StateActionController(
            self, getattr(self.config, "state_actions", ())
        )

        ss = self.config.sound_settings
        self._sound = SoundPlayer(
            sink=ss.sink,
            player=ss.player,
            sound_dir=ss.sound_dir,
            base_dir=os.path.dirname(os.path.abspath(__file__)),
            enabled=ss.enabled,
            player_wait_timeout_sec=ss.player_wait_timeout_sec,
            pactl_timeout_sec=ss.pactl_timeout_sec,
        )
        self._sound_test_until = 0.0
        self._sound_started = False
        self._sound_test_seconds = getattr(
            self.config.sound_settings, "sound_test_duration_sec", self._SOUND_TEST_SECONDS
        )
        # Every SoundPlayer entry point blocks: play() calls stop(), which
        # terminates mplayer and waits player_wait_timeout_sec, then joins the
        # replay thread for the same budget (~2s worst case); _pactl() runs a
        # subprocess with pactl_timeout_sec. Called inline they stalled the whole
        # asyncio adapter -- state publishing, order steps, MQTT -- so they run
        # on one worker thread instead. Created on first use so an adapter that
        # never reaches playback (tests, sound disabled) starts no thread.
        self._sound_worker: Optional[ThreadPoolExecutor] = None
        self._sound_request: Optional[Tuple[str, float, bool, int]] = None

        self._acs_broker_connected = False
        # FMS 링크 상실 가드 상태. _mqtt_ever_connected 는 "한 번도 못 붙은 기동"을
        # 끊김으로 오인하지 않기 위한 것이고, _paused_by_mqtt_loss 는 이 가드가 건
        # pause 와 운영자의 startPause 를 구분한다(재연결이 후자를 풀면 안 된다).
        self._mqtt_ever_connected = False
        self._paused_by_mqtt_loss = False
        self._mqtt_disconnect_timer: Optional[asyncio.TimerHandle] = None
        self._mqtt_guard_lock_obj: Optional[asyncio.Lock] = None
        # True once OFFLINE was published on purpose (graceful shutdown), which
        # suppresses the reconnect-time ONLINE re-assert.
        self._connection_offline_intent = False
        self._adapter_started_at = time.time()
        self._mqtt = MQTTClient(
            config=self.config,
            on_connection_change=self._on_acs_broker_change,
        )
        self._vda3 = Vda5050V3Transport(self._mqtt)

        # Last sequence received from cmd/sync; used as base for increment loop
        self._status_sequence: int = 0
        # Track current and last node values for status payload
        self._current_next_node: Optional[str] = None
        self._last_node_value: Optional[str] = None

        self._vehicle: Optional[JIBOT] = None
        self._docking_started_node_ids: Set[str] = set()
        self._charge_circuit: ChargeCircuit = NullChargeCircuit()
        self._charge_in_place_active: bool = False

        # Loading/unloading BUSY state (in-memory, adapter-process lifetime only).
        # BUSY <=> _work_in_progress is not None. While BUSY the adapter rejects
        # orders and motion instant actions. JIBOT cannot self-detect completion,
        # so the work action is held RUNNING until stopLoading/stopUnloading.
        self._work_in_progress: Optional[str] = None  # None | "loading" | "unloading"
        self._work_action_id: Optional[str] = None
        self._manual_drive_watchdog: Optional[asyncio.TimerHandle] = None
        # True while the operator is manually driving/jogging (no active order).
        # Used by _capture_idle_settled to suppress lastNodeId churn during jogs.
        self._manual_control_active: bool = False
        self.joystick_task: Optional[asyncio.Task[Any]] = None
        # One-time guard so an unknown last_node_capture_mode warns once, not every cycle.
        self._last_node_capture_mode_warned: bool = False
        # 미지원 path_control 값을 값별로 한 번만 경고하기 위한 집합.
        self._warned_path_control_values: set[str] = set()

        self._ezi_io: Optional[EZIIOClient] = None

        self._ezi_motor: Optional[EziMotorClient] = None
        self._pio_client: Any = None
        self._pio_client_factory: Any = None

        # EZIO/PIO 진단 스냅샷(로컬 io.json용). 미관측은 None.
        self._ezio_inputs: Optional[List[int]] = None
        self._ezio_inputs_at: Optional[float] = None
        self._ezio_outputs: Optional[List[int]] = None
        self._ezio_outputs_at: Optional[float] = None
        self._ezio_connected: bool = False
        self._ezio_board: str = ""
        self._ezio_error: str = ""
        self._pio_connected: bool = False
        self._pio_inputs: Optional[Dict[str, str]] = None
        self._pio_inputs_at: Optional[float] = None
        # PIO 출력은 장치에서 되읽기 불가(프로토콜에 read-back 없음) → 마지막으로
        # 명령한 출력 상태를 인덱스("1".."8")별로 누적 캐시한다.
        self._pio_outputs: Optional[Dict[str, str]] = None
        self._pio_outputs_at: Optional[float] = None
        self._pio_error: str = ""
        self._io_last_write: float = 0.0

        # Camera/video: web_video_server URL builder + event-snapshot publisher.
        # Pixels go out-of-band; only snapshot JPEGs and stream URLs touch MQTT.
        self._video = VideoStreamer(self.config.video, self._mqtt)
        # Snapshot events active last cycle, for edge-triggered capture.
        self._prev_video_events: Set[str] = set()

        self._jibot_rx_timeout: float = float(self.config.settings.jibot_rx_timeout)

        self.connection: Optional[Connection] = None
        self.header_id: int = 0 # Header ID for connection

        self.state_header_id: int = 0 # Header ID for state

        self.state: Optional[State] = None
        self.order: Optional[Order] = None

        self.error: Optional[Error] = None

        # Wakes the state publish loop immediately on state-changing events
        # (order accept/reject, action status change, node reached, ...) so
        # ACS is not left waiting for the periodic heartbeat.
        self._state_publish_event: asyncio.Event = asyncio.Event()

        # Node the order worker is currently driving to; re-issued on stopPause.
        self._active_goto_node: Optional[Any] = None
        # True between startPause and stopPause instant actions.
        self._motion_paused: bool = False
        # Set while a mode="move" relative move is in flight, so stopPause can
        # resume it with the distance it still owes. Mutually exclusive with
        # _active_goto_node, which covers the fallback goto phase.
        self._active_move_segment: Optional[Any] = None

        self.factsheet_header_id: int = 0  # Header ID for factsheet

        self._last_node_id: str = ""
        self._last_node_sequence_id: int = 0

        self._nearest_node_id: str = ""
        self._nearest_node_sequence_id: int = 0
        self._nearest_node_distance: Optional[float] = None

        self._charge_node: bool = False

        # Resolve synthetic docking-status action id/type from config (Task 5.4).
        # Class constants are the canonical defaults; config overrides for rare
        # deployments where the FMS contract names differ.
        self._docking_status_action_id: str = getattr(
            self.config.internal_actions,
            "docking_status_action_id",
            self.DOCKING_STATUS_ACTION_ID,
        )
        self._docking_status_action_type: str = getattr(
            self.config.internal_actions,
            "docking_status_action_type",
            self.DOCKING_STATUS_ACTION_TYPE,
        )

        # Active map id reported in agv_position. Defaults to config, but a
        # simulator (which has no onboard map) can be updated from an FMS map
        # snapshot instant action.
        self._current_map_id: str = self.config.settings.map_id
        self._fms_map_nodes: Dict[str, Tuple[float, float, float]] = {}

        # Loads are presence-based: publish only occupied slots.
        self.loads: List[Load] = []

        # Cached stop-reason from the latest _apply_jibot_stop_reason() call.
        # None = stale/absent safety data (legacy fallback active).
        self._jibot_stop_reason: Optional[str] = None

        # self.error_reference[ErrorReference] = None


    def connect_mqtt(self) -> None:
        # Background (non-blocking) connect: a broker that is down at boot must
        # not block adapter startup — the local IPC (state.json + control.sock)
        # has to come up regardless of broker availability.
        self._mqtt.connect_background()

    def disconnect_mqtt(self) -> None:
        self._mqtt.disconnect()

    # -------------------------
    # setter method
    # -------------------------
    def set_vehicle(self, vehicle: JIBOT) -> None:
        self._vehicle = vehicle

    def set_charge_circuit(self, charge_circuit: ChargeCircuit) -> None:
        self._charge_circuit = charge_circuit

    def set_ezi_io(self, io: EZIIOClient ) -> None:
        self._ezi_io = io

    def set_ezi_motor(self, motor: EziMotorClient) -> None:
        self._ezi_motor = motor

    def set_pio_client(self, client: Any) -> None:
        self._pio_client = client

    def set_pio_client_factory(self, factory: Any) -> None:
        self._pio_client_factory = factory

    def _action_params(self, action: Any) -> Dict[str, Any]:
        return {
            param.key: param.value
            for param in getattr(action, "action_parameters", []) or []
        }

    @property
    def _map_fetch_lock(self) -> asyncio.Lock:
        """Lazy asyncio.Lock for serializing concurrent UmGetMap requests.

        asyncio.Lock()은 실행 중인 이벤트 루프 없이 __init__에서 생성하면
        DeprecationWarning(3.10+)이 발생하므로 첫 접근 시 지연 생성한다.
        """
        if getattr(self, "_map_fetch_lock_instance", None) is None:
            self._map_fetch_lock_instance = asyncio.Lock()
        return self._map_fetch_lock_instance

    def _run_on_adapter_loop(
        self, coro_factory: Any, *, action_id: Optional[str] = None
    ) -> None:
        """Schedule a coroutine on the adapter event loop, thread-safely.

        coroutine을 adapter event loop에 thread-safe하게 예약한다. MQTT callback은
        adapter asyncio loop 밖의 스레드에서 호출될 수 있다.
        """
        if self._loop is None or not self._loop.is_running():
            print("Adapter event loop is not ready; coroutine skipped.")
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        def schedule() -> None:
            task = asyncio.create_task(coro_factory())
            if action_id is not None:
                previous = self._action_tasks.pop(action_id, None)
                if previous is not None and not previous.done():
                    previous.cancel()
                self._action_tasks[action_id] = task
                task.add_done_callback(
                    lambda done, key=action_id: (
                        self._action_tasks.pop(key, None)
                        if self._action_tasks.get(key) is done
                        else None
                    )
                )

        if running_loop is self._loop:
            schedule()
        else:
            self._loop.call_soon_threadsafe(schedule)

    def _call_on_adapter_loop(self, callback: Any) -> None:
        """Run a synchronous state mutation on the adapter event loop."""
        if self._loop is None or not self._loop.is_running():
            callback()
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            callback()
        else:
            self._loop.call_soon_threadsafe(callback)

    def request_state_publish(self, reason: str = "") -> None:
        """Wake the state publish loop so the next state goes out immediately.

        VDA5050 requires publishing state on every state-changing event, not
        only on the periodic interval. Safe to call from MQTT callback threads.
        """
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._state_publish_event.set)
        else:
            self._state_publish_event.set()
        if reason and self.config.settings.debug_log:
            print(f"[STATE PUBLISH REQUESTED] reason={reason}")

    def _supervise_task(self, task: "asyncio.Task[Any]", name: str) -> "asyncio.Task[Any]":
        """Make a long-lived adapter loop fail loudly instead of silently.

        asyncio swallows a Task exception until the Task is garbage collected,
        so a dead loop leaves the process running and looking healthy: JIBOT
        polling continues, connection stays ONLINE, and the FMS quietly freezes
        on the last state it received. Every loop registered here is a
        ``while True`` that must outlive the process, so any completion is a
        fault -- log it and exit, letting systemd (Restart=on-failure) restart
        us and the MQTT Last Will tell the FMS we are gone.
        """
        task.add_done_callback(functools.partial(self._on_supervised_task_done, name))
        return task

    def _on_supervised_task_done(self, name: str, task: "asyncio.Task[Any]") -> None:
        # Shutdown cancels these tasks; that is not a fault.
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            print(f"[TASK EXITED] name={name}; supervised loop returned unexpectedly")
        else:
            print(f"[TASK DIED] name={name} error={exc!r}")
            traceback.print_exception(type(exc), exc, exc.__traceback__)
        self._terminate_after_task_loss(name)

    def _terminate_after_task_loss(self, name: str) -> None:
        """Exit non-zero so systemd restarts the adapter.

        os._exit skips interpreter teardown on purpose: the loop we depend on is
        already gone, and an orderly unwind could hang here. stdout is flushed
        first so the traceback above actually reaches the journal.
        """
        print(
            f"[ADAPTER EXIT] supervised task {name} is gone; exiting so systemd "
            f"restarts the adapter"
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)

    # -------------------------
    # Run Adapter
    # -------------------------
    async def run_adapter(self):

        # Motion control ( Order accept/update )
        self._loop = asyncio.get_running_loop()

        from utils.joystick_runtime import start_joystick_service
        self.joystick_task = start_joystick_service(self)

        # Advertise vehicle capabilities once at startup (retained).
        self.publish_factsheet()

        # Seed health.json with acs_broker_connected=false until the first MQTT
        # connect callback flips it (WebUi reads this for ACS broker status).
        self._write_health_file()

        # Publish State AMR==>ACS
        self.publish_state_task = self._supervise_task(
            asyncio.create_task(
                self.publish_state(
                    "state",
                    interval_sec=float(self.config.settings.state_publish_delay),
                )
            ),
            "publish_state",
        )

        # Subscribe ( Order and InstantAction )
        self.subscribe_task = self._supervise_task(
            asyncio.create_task(
                self.subscribe_acs_cmd(interval_sec=getattr(self.config.settings, "acs_cmd_subscribe_interval_sec", 1.0))
            ),
            "subscribe_acs_cmd",
        )

        # Local control socket (WebUi -> adapter, broker-independent).
        self.control_server_task = self._supervise_task(
            asyncio.create_task(self._serve_control_socket()), "control_socket"
        )


        # Manage Load ( Tray-slot By IO detection)
        self.load_task = start_hardware_sensor_service(
            self,
            interval_sec=float(self.config.settings.state_publish_delay),
            action_registry=self._action_registry,
        )

        # Supervise the JIBOT link and reconnect on drop/failure.
        self.connection_monitor_task = self._supervise_task(
            asyncio.create_task(
                self.monitor_jibot_connection(
                    interval_sec=float(self.config.settings.jibot_reconnect_delay),
                )
            ),
            "monitor_jibot_connection",
        )

    # Supervise JIBOT connection and reconnect periodically when it is down.
    async def monitor_jibot_connection(self, interval_sec: float = 5.0) -> None:
        """Retry the JIBOT connection every ``interval_sec`` while it is down.

        연결이 끊긴 동안 ``interval_sec``마다 JIBOT 연결을 재시도한다.

        A dropped or failed JIBOT link must not stop the adapter: state keeps
        publishing (with a FATAL JIBOT_CONNECTION_LOST error) and this loop
        keeps reconnecting until the robot is reachable again.
        끊기거나 실패한 JIBOT 링크가 adapter를 멈춰서는 안 된다. state는 계속
        발행되고(FATAL JIBOT_CONNECTION_LOST 오류 포함) 이 루프는 로봇이 다시
        연결될 때까지 재연결을 시도한다.
        """
        print(
            f"[JIBOT RECONNECT] monitor ready; retry interval={interval_sec}s "
            f"rx_timeout={self._jibot_rx_timeout:g}s"
        )
        while True:
            await asyncio.sleep(interval_sec)

            if self._vehicle is None or self._is_jibot_link_healthy():
                continue

            reconnect = getattr(self._vehicle, "reconnect", None)
            if not callable(reconnect):
                continue

            if not self._is_jibot_connected():
                cause = "connection down"
            else:
                cause = (
                    f"no data for >{self._jibot_rx_timeout:g}s "
                    f"(last rx {self._jibot_seconds_since_rx():.1f}s ago)"
                )
            print(f"[JIBOT RECONNECT] {cause}; attempting to reconnect...")
            try:
                await reconnect()
            except Exception as exc:
                print(
                    f"[JIBOT RECONNECT FAILED] {exc}; retry in {interval_sec}s"
                )
                continue

            print("[JIBOT RECONNECT] reconnected to JIBOT.")

    async def publish_state(self, topic_name: str, interval_sec: float = 1):
        print("publish_state is ready!")

       # AgvPosition
        agv_position = AgvPosition(
            x=self._vehicle._x,
            y=self._vehicle._y,
            position_initialized=True,
            theta=self._vehicle._th,
            map_id=self._current_map_id,
            deviation_range=None,
            map_description=None,
            localization_score=self._vehicle._localization_score,
        )

        
        self.state = State(
            header_id=self.state_header_id,
            timestamp=utils.get_timestamp(),
            version=self.config.vehicle.vda_full_version,
            manufacturer=self.config.vehicle.manufacturer,
            serial_number=self.config.vehicle.serial_number,
            driving=False,
            distance_since_last_node=None,
            operating_mode=OperatingMode.AUTOMATIC,
            node_states=[], 
            edge_states=[],
            last_node_id=self._last_node_id,
            order_id=self.order.order_id if self.order else "",
            order_update_id=self.order.order_update_id if self.order else 0,
            last_node_sequence_id= self._last_node_sequence_id,
            action_states=[],
            instant_action_states=[],
            information=[],
            loads=self.loads,
            errors=[],
            battery_state=BatteryState(
                battery_charge=self._get_vehicle_battery_charge(),
                battery_voltage=self._get_vehicle_battery_voltage(),
                battery_health=self._get_vehicle_battery_health(),
                charging=self._vehicle._charging,
                reach=None
            ),
            safety_state=SafetyState(
                e_stop=EStop.NONE,
                field_violation=False
            ),
            paused=None,
            new_base_request=None,
            agv_position=agv_position,
            velocity=None,
            zone_set_id=None
        )


        while True:
            # counter_state += 1
            self.state_header_id += 1

            if self._vehicle is not None:

                vx, vy, vth = self._vehicle._x, self._vehicle._y, self._vehicle._th
                agv_position.x = float(vx) if vx is not None else 0.0
                agv_position.y = float(vy) if vy is not None else 0.0
                agv_position.theta = float(vth) if vth is not None else 0.0
                agv_position.localization_score = float(self._vehicle._localization_score) if self._vehicle._localization_score is not None else 0.0
                agv_position.map_id = self._current_map_id

                if self.config.settings.debug_log:
                    print(
                        f"[POSITION] x={agv_position.x:.3f} y={agv_position.y:.3f} "
                        f"theta={agv_position.theta:.3f}"
                    )
                
                
                self.state.battery_state.battery_charge = self._get_vehicle_battery_charge()
                self.state.battery_state.battery_voltage = self._get_vehicle_battery_voltage()
                self.state.battery_state.battery_health = self._get_vehicle_battery_health()
                self._refresh_status_information()
                self._refresh_jibot_status_errors()


                # charging
                self.state.battery_state.charging = self._vehicle._charging


                # JIBOT brake (obstacle wait) → field_violation; bumper may add to it.
                self.state.safety_state.field_violation = self._is_jibot_obstacle_wait()

                # driving
                self.state.driving = self._derive_driving()

                # safety_state: subdivide motor-off into the real stop reason
                # (e_stop / field_violation / driving / motor-fault errors). Falls
                # back to legacy motor_flag→AUTOACK when /jrobot_status is stale.
                self._apply_jibot_stop_reason()

                self.state.operating_mode = self._derive_operating_mode()

                # Capture an event snapshot when a watched JIBOT condition appears.
                self._schedule_event_snapshots()

            self._update_nearest_node_from_position(
                agv_position.x,
                agv_position.y,
                agv_position.theta,
            )
            self.state.last_node_id = self._last_node_id
            self.state.last_node_sequence_id = self._last_node_sequence_id
            self._refresh_last_node_id_errors()

            # Surface a lost or silent JIBOT link as a FATAL state error. Runs
            # every cycle so it is set/cleared on each link transition; cached
            # position/battery values may be stale meanwhile, so driving is
            # forced False until the robot is streaming again.
            self._refresh_jibot_connection_errors()
            if not self._is_jibot_link_healthy():
                self.state.driving = False

            # Surface/clear the synthetic RUNNING "dock" action mirroring the
            # current docking maneuver so the FMS/eq can show a DOCKING sub-state.
            self._sync_docking_action_state()

            # Adapter-computed working state for the eq (read directly from AMR_STATE).
            self._refresh_amr_state_information()
            self._refresh_jibot_safety_information()
            self._update_sound_for_working_state()

            self.state.header_id = self.state_header_id
            self.state.timestamp = utils.get_timestamp()

            msg = self._build_v3_state_message()
            self._write_state_file(msg)  # local WebUi first — broker 무관 보장
            try:
                self._vda3.publish_state(msg, qos=0)  # ACS
            except Exception as exc:  # noqa: BLE001
                print(f"[ACS STATE PUBLISH FAILED] {exc}")

            # Heartbeat interval, interruptible: request_state_publish() wakes
            # this wait so event-driven states are published immediately.
            try:
                await asyncio.wait_for(
                    self._state_publish_event.wait(), timeout=interval_sec
                )
            except asyncio.TimeoutError:
                pass
            self._state_publish_event.clear()

    def _write_state_file(self, state_msg) -> None:
        """Write the same VDA5050 state dict the adapter publishes, plus a
        wall-clock ``updated_at``, to /run/amr-adaptor/<serial>/state.json
        (tmpfs). Errors are swallowed so a file fault never blocks the loop."""
        try:
            serial = self.config.vehicle.serial_number
            ipc_paths.ensure_runtime_dir(serial)
            data = state_msg.to_dict()
            data["updated_at"] = time.time()
            ipc_paths.atomic_write_json(ipc_paths.state_path(serial), data)
            self._state_file_error_logged = False
        except Exception as exc:  # noqa: BLE001
            # Log once per failure streak (loop runs every ~1s) — reset on success.
            if not getattr(self, "_state_file_error_logged", False):
                print(f"[STATE FILE WRITE FAILED] {exc}")
                self._state_file_error_logged = True

    def _on_acs_broker_change(self, connected: bool) -> None:
        """MQTT connect/disconnect callback — record ACS broker reachability
        for the WebUi (which no longer connects to the broker itself)."""
        self._acs_broker_connected = connected
        self._write_health_file()
        if connected:
            self._mqtt_ever_connected = True
            self._call_on_adapter_loop(self._cancel_mqtt_disconnect_timer)
            self._republish_connection_online()
            if self._paused_by_mqtt_loss:
                self._run_on_adapter_loop(self._resume_after_mqtt_recovery)
        else:
            self._call_on_adapter_loop(self._arm_mqtt_disconnect_timer)

    def _arm_mqtt_disconnect_timer(self) -> None:
        """끊김 유예 타이머를 건다(adapter loop 위에서만 호출).

        한 번도 붙은 적 없는 브로커는 "끊김"이 아니다. connect_background 는
        브로커가 없어도 즉시 돌아오므로, 이 가드가 없으면 브로커보다 먼저 뜬
        로봇이 기동하자마자 스스로 pause 로 들어간다.
        """
        if not self._mqtt_ever_connected:
            return
        if self._mqtt_disconnect_timer is not None:
            return
        if self._loop is None or not self._loop.is_running():
            return
        grace = max(0.0, float(self.config.settings.mqtt_disconnect_stop_grace_sec))
        self._mqtt_disconnect_timer = self._loop.call_later(
            grace, self._on_mqtt_disconnect_grace_elapsed
        )

    def _cancel_mqtt_disconnect_timer(self) -> None:
        timer = self._mqtt_disconnect_timer
        self._mqtt_disconnect_timer = None
        if timer is not None:
            timer.cancel()

    def _on_mqtt_disconnect_grace_elapsed(self) -> None:
        """유예를 넘겨서도 링크가 안 돌아왔다 — 알린 뒤(옵션이면) 세운다."""
        self._mqtt_disconnect_timer = None
        if self._acs_broker_connected:
            return

        # 알림음은 정지 옵션과 독립이다. 세우지 않기로 한 로봇에서도 현장 작업자는
        # FMS 가 끊긴 사실을 알아야 한다.
        self._play_mqtt_disconnect_sound()

        if not self.config.settings.stop_on_mqtt_disconnect:
            return
        if self._vehicle is None:
            return
        if self._motion_paused:
            # 이미 startPause 로 서 있다. 여기서 _paused_by_mqtt_loss 를 세우면
            # 재연결이 운영자의 pause 를 대신 풀어 버린다.
            return

        print(
            "[MQTT LOST] broker unreachable beyond grace; pausing motion "
            f"(grace={self.config.settings.mqtt_disconnect_stop_grace_sec:g}s)"
        )
        self._run_on_adapter_loop(self._pause_for_mqtt_loss)

    def _mqtt_guard_lock(self) -> asyncio.Lock:
        """정지/재개를 직렬화하는 락(첫 사용 시 생성 — __init__ 에는 loop 가 없다).

        _pause_motion 과 _resume_motion 은 각각 로봇과 TCP 를 주고받는 동안 await
        한다(stop_motion, _send_node_goto). 그 사이에 링크 상태가 뒤집히면 두 코루틴이
        서로의 flag 를 밟는다. 락으로 겹치지 않게 하고, 각자 끝에서 링크 상태를
        다시 읽어 수렴시킨다.
        """
        if self._mqtt_guard_lock_obj is None:
            self._mqtt_guard_lock_obj = asyncio.Lock()
        return self._mqtt_guard_lock_obj

    async def _pause_for_mqtt_loss(self) -> None:
        async with self._mqtt_guard_lock():
            if self._acs_broker_connected or self._motion_paused:
                return
            try:
                await self._pause_motion()
            except Exception as exc:
                print(f"[MQTT LOST] pause failed: {exc}")
                return
            self._paused_by_mqtt_loss = True

        # stop_motion 을 기다리는 동안(최대 jibot_command_default_timeout_sec) 링크가
        # 돌아왔을 수 있다. 그때 _on_acs_broker_change 는 _paused_by_mqtt_loss 가 아직
        # False 라 재개를 걸지 않았으므로, 여기서 다시 읽어 직접 재개한다. 이게 없으면
        # 링크가 멀쩡한데 FMS 의 stopPause 전까지 영영 서 있다.
        if self._acs_broker_connected:
            await self._resume_after_mqtt_recovery()

    async def _resume_after_mqtt_recovery(self) -> None:
        async with self._mqtt_guard_lock():
            if not self._paused_by_mqtt_loss:
                return
            try:
                await self._resume_motion()
            except Exception as exc:
                # paused 는 유지된다(_resume_motion 규약). 운영자가 stopPause 로 풀 수 있게
                # 소유권만 놓는다 — 재연결이 또 재개를 시도해도 같은 실패를 반복한다.
                self._paused_by_mqtt_loss = False
                print(f"[MQTT RECOVERED] resume failed: {exc}")
                return
            print("[MQTT RECOVERED] link restored; motion resumed.")

        # 재발행을 기다리는 동안 링크가 또 끊겼을 수 있다. 그 사이 발화한 유예 타이머는
        # _motion_paused=True 를 보고 "이미 서 있다"며 그냥 소진됐으므로, 다시 걸지 않으면
        # 로봇이 브로커 없이 달린다(paho 재연결 max_delay=10s, 플래핑 구간에서 실제로 난다).
        if not self._acs_broker_connected:
            self._arm_mqtt_disconnect_timer()

    def _play_mqtt_disconnect_sound(self) -> None:
        """"FMS 와 연결이 끊겼습니다" 일회성 알림음.

        play_joystick_connect_sound 와 같은 규약: 상태음이 아니라 알림이므로
        _dispatch_sound 가 아니라 _submit_sound 로 바로 던지고 _sound_request 를
        비워 다음 publish 주기에 상태음이 되살아나게 한다. 트랙이 없으면 아무것도
        하지 않는다 — 상태음만 끊고 끝나는 게 제일 나쁘다.
        """
        track = (self.config.settings.mqtt_disconnect_sound or "").strip()
        if not track:
            return
        if not self._sound.has_track(track):
            return
        self._submit_sound(self._sound.play, track, loop=False)
        self._sound_request = None

    def _republish_connection_online(self) -> None:
        """Re-assert connectionState=ONLINE on every successful (re)connect.

        The Last Will is a *retained* OFFLINE, so a dropped link leaves the
        broker holding OFFLINE for this serial. paho reconnects underneath and
        _resubscribe_all() re-arms the downlink, but nothing used to overwrite
        that retained message — ONLINE was published once at startup only, so
        the FMS kept seeing the robot offline for the rest of the process
        lifetime even though the session was healthy.
        LWT는 retain=True OFFLINE이라 링크가 한 번 끊기면 브로커에 OFFLINE이
        남는다. paho가 재접속해도 덮어쓰는 주체가 없어 FMS에는 영구 오프라인으로
        보였다. 접속이 살아날 때마다 ONLINE을 다시 선언한다.

        Called from the paho network thread, so the publish is handed to the
        adapter loop; before run_adapter() sets the loop it runs inline (paho's
        publish() is thread-safe) rather than being dropped.
        """
        if self._connection_offline_intent:
            # Shutdown already published OFFLINE on purpose; do not undo it.
            return
        try:
            self._call_on_adapter_loop(
                lambda: self._publish_connection_state(ConnectionState.ONLINE)
            )
        except Exception as exc:  # noqa: BLE001
            # paho does not suppress callback exceptions: one escaping here kills
            # the network thread and with it the auto-reconnect this very method
            # exists to support. The first connect lands before run_adapter()
            # sets _loop, so the publish runs inline on that thread.
            # paho는 콜백 예외를 삼키지 않는다. 여기서 예외가 나가면 네트워크
            # 스레드가 죽어 자동 재접속 자체가 멈춘다.
            if not getattr(self, "_connection_republish_error_logged", False):
                print(f"[VDA5050 CONNECTION REPUBLISH FAILED] {exc}")
                self._connection_republish_error_logged = True

    def _write_health_file(self) -> None:
        try:
            serial = self.config.vehicle.serial_number
            ipc_paths.ensure_runtime_dir(serial)
            now = time.time()
            ipc_paths.atomic_write_json(
                ipc_paths.health_path(serial),
                {
                    "pid": os.getpid(),
                    "started_at": self._adapter_started_at,
                    "acs_broker_connected": self._acs_broker_connected,
                    "acs_broker_last_change": now,
                    "updated_at": now,
                },
            )
        except Exception as exc:  # noqa: BLE001
            if not getattr(self, "_health_file_error_logged", False):
                print(f"[HEALTH FILE WRITE FAILED] {exc}")
                self._health_file_error_logged = True

    def _note_ezio(self, *, connected=None, inputs=None, outputs=None,
                   board=None, error=None) -> None:
        now = time.time()
        if connected is not None:
            self._ezio_connected = connected
        if inputs is not None:
            self._ezio_inputs = inputs
            self._ezio_inputs_at = now
        if outputs is not None:
            self._ezio_outputs = outputs
            self._ezio_outputs_at = now
        if board is not None:
            self._ezio_board = board
        if error is not None:
            self._ezio_error = error

    def _note_pio(self, *, connected=None, inputs=None, outputs=None, error=None) -> None:
        now = time.time()
        if connected is not None:
            self._pio_connected = connected
        if inputs is not None:
            self._pio_inputs = inputs
            self._pio_inputs_at = now
        if outputs is not None:
            self._pio_outputs = outputs
            self._pio_outputs_at = now
        if error is not None:
            self._pio_error = error

    def _refresh_pio_from_ezio(self) -> None:
        """EZI IO가 이미 읽어 둔 비트를 PIO in/out 표시 값으로 옮긴다.

        PIO 신호선은 직렬이 아니라 EZI IO 레지스터다(pio_write_output 주석 참고).
        그런데 _note_pio는 pio* action이 돌 때만 불려서, action을 실행하지 않는
        동안 WebUI의 PIO 값이 그대로 멈춰 있었다 — EZIO는 manage_tray_slot이
        1초마다 갱신하므로 잘 움직인다.

        타임스탬프는 EZIO 것을 그대로 물려받는다. 여기서 time.time()을 찍으면
        EZI IO 읽기가 끊긴 동안에도 값이 신선해 보여서 stale 표시가 거짓말을 한다.
        """
        if not getattr(self.config.pio_config, "pio_serial_port", ""):
            return
        try:
            from extensions.pio import map_pio_inputs, map_pio_outputs

            # 빈 dict는 덮어쓰지 않는다. strict=False는 짝이 없는 점을 건너뛰므로
            # 핀 맵이 통째로 어긋나면 {}가 나오는데, 그대로 넣으면 방금 명령한
            # 출력 표시가 지워지고 시각까지 새로 찍혀 "막 읽은 빈 값"이 된다.
            inputs = map_pio_inputs(self, self._ezio_inputs or [], strict=False)
            if inputs:
                self._pio_inputs = inputs
                self._pio_inputs_at = self._ezio_inputs_at
            outputs = map_pio_outputs(self, self._ezio_outputs or [], strict=False)
            if outputs:
                self._pio_outputs = outputs
                self._pio_outputs_at = self._ezio_outputs_at
        except Exception as exc:  # noqa: BLE001
            # 이 함수는 tray-slot 루프 안에서 불린다. 여기서 예외가 나가면 EZIO
            # 폴링까지 같이 멈춘다 — 표시 하나 때문에 센서를 잃을 수는 없다.
            if not getattr(self, "_pio_mirror_error_logged", False):
                print(f"[PIO MIRROR FAILED] {exc}")
                self._pio_mirror_error_logged = True

    def _io_snapshot_dict(self) -> Dict[str, Any]:
        ezio_configured = self._ezi_io is not None
        pio_configured = bool(getattr(self.config.pio_config, "pio_serial_port", ""))
        return {
            "updated_at": time.time(),
            "ezio": {
                "configured": ezio_configured,
                "ip": getattr(self.config.ezi_config, "ezi_io", None),
                "port": 3002,
                "connected": self._ezio_connected,
                "board": self._ezio_board,
                "inputs": self._ezio_inputs,
                "inputs_updated_at": self._ezio_inputs_at,
                "outputs": self._ezio_outputs,
                "outputs_updated_at": self._ezio_outputs_at,
                "error": self._ezio_error,
            },
            "pio": {
                "configured": pio_configured,
                "port": getattr(self.config.pio_config, "pio_serial_port", None),
                "baudrate": getattr(self.config.pio_config, "pio_baudrate", None),
                "connected": self._pio_connected,
                "inputs": self._pio_inputs,
                "inputs_updated_at": self._pio_inputs_at,
                "outputs": self._pio_outputs,
                "outputs_updated_at": self._pio_outputs_at,
                "error": self._pio_error,
            },
        }

    def _write_io_file(self) -> None:
        try:
            serial = self.config.vehicle.serial_number
            ipc_paths.ensure_runtime_dir(serial)
            ipc_paths.atomic_write_json(
                ipc_paths.io_path(serial), self._io_snapshot_dict()
            )
            self._io_file_error_logged = False
        except Exception as exc:  # noqa: BLE001
            if not getattr(self, "_io_file_error_logged", False):
                print(f"[IO FILE WRITE FAILED] {exc}")
                self._io_file_error_logged = True

    async def _io_throttle_tick(self, now: float) -> None:
        """~_IO_WRITE_INTERVAL_SEC마다 EZIO 출력/보드 조회 후 io.json 기록.

        입력 read(매 1초)와 분리: get_output은 별도 try/except로 입력 루프를
        방해하지 않는다. board는 최초 1회만 캐시.
        """
        if now - self._io_last_write < _IO_WRITE_INTERVAL_SEC:
            return
        self._io_last_write = now
        if self._ezi_io is not None:
            try:
                resp = await self._ezi_io.get_output()
                if resp and "outputs" in resp:
                    self._note_ezio(outputs=list(resp["outputs"]))
            except Exception as exc:  # noqa: BLE001
                self._note_ezio(error=f"get_output: {exc}")
            if not self._ezio_board:
                try:
                    info = await self._ezi_io.get_board_info()
                    if info and info.get("description"):
                        self._note_ezio(board=info["description"])
                except Exception:  # noqa: BLE001
                    pass
        self._refresh_pio_from_ezio()
        self._write_io_file()

    # -------------------------
    # LOCAL CONTROL (WebUi -> adapter over Unix domain socket)
    # -------------------------
    def _process_control_request(self, req: dict) -> dict:
        """Parse a local WebUi control request and dispatch via the shared
        instant-actions procedure. Returns a delivered-level ack — the
        procedure returns nothing, so per-action accept/reject and results are
        observed via state.instantActionStates, not in this reply."""
        try:
            ia = InstantActions.from_dict(req["instantActions"])
        except Exception as exc:  # noqa: BLE001
            return {"delivered": False, "error": f"invalid instantActions payload: {exc}"}
        meta = req.get("meta", {})
        print(
            f"[CONTROL UDS] source_user={meta.get('source_user')} "
            f"confirmed={meta.get('confirmed')} actions={len(ia.actions)}"
        )
        action_ids = [a.action_id for a in ia.actions]
        self.instant_actions_accept_procedure(ia)
        return {"delivered": True, "action_ids": action_ids}

    async def _handle_control_conn(self, reader, writer):
        try:
            # Read the whole request: the client half-closes (SHUT_WR) after
            # sending, so read() returns the full payload at EOF. A bare
            # read(n) could return just the first chunk on a fragmented send.
            raw = await reader.read()
            req = json.loads(raw.decode("utf-8"))
            resp = self._process_control_request(req)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            resp = {"delivered": False, "error": f"bad request: {exc}"}
        except Exception as exc:  # noqa: BLE001
            resp = {"delivered": False, "error": f"server error: {exc}"}
        try:
            writer.write(json.dumps(resp).encode("utf-8"))
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()

    def _control_socket_retry_delay(self) -> float:
        """Seconds between control-socket rebind attempts while the IPC root is
        not yet writable. Modest default so an operator-applied fix self-heals
        soon; overridable via settings for tests/tuning."""
        return float(getattr(self.config.settings, "control_socket_retry_delay", 10.0))

    async def _bind_control_socket(self, serial):
        """Create the runtime dir and bind the WebUi control socket.

        Returns the asyncio server on success, or ``None`` on a filesystem fault
        (typically /run/amr-adaptor not writable by this adapter). The failure is
        logged loudly — once per failure streak, matching the state/health
        writers — instead of propagating and silently killing the control task,
        which used to leave the WebUi reporting "adapter offline" with nothing in
        the journal to explain it."""
        sock = ipc_paths.control_sock_path(serial)
        try:
            ipc_paths.ensure_runtime_dir(serial)
            try:
                if sock.exists():
                    os.unlink(sock)  # remove a stale socket from a previous run
            except OSError:
                pass
            server = await asyncio.start_unix_server(
                self._handle_control_conn, path=str(sock)
            )
            try:
                os.chmod(sock, 0o660)
            except OSError:
                pass
            print(f"[CONTROL UDS] listening on {sock}")
            self._control_socket_error_logged = False
            return server
        except OSError as exc:
            if not getattr(self, "_control_socket_error_logged", False):
                print(
                    f"[CONTROL UDS FAILED] {exc} — WebUi cannot deliver commands; "
                    f"/run/amr-adaptor not writable by this adapter. Fix with "
                    f"scripts/repair-adaptor-ipc.sh (the socket then rebinds "
                    f"without an adapter restart)."
                )
                self._control_socket_error_logged = True
            return None

    async def _serve_control_socket(self):
        """Serve the WebUi control socket, retrying the bind so an
        operator-fixable fault (IPC root missing/unwritable) self-heals without
        an adapter restart."""
        serial = self.config.vehicle.serial_number
        while True:
            server = await self._bind_control_socket(serial)
            if server is None:
                await asyncio.sleep(self._control_socket_retry_delay())
                continue
            try:
                async with server:
                    await server.serve_forever()
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                print(f"[CONTROL UDS] server error: {exc}; rebinding")
                await asyncio.sleep(self._control_socket_retry_delay())

    def _get_vehicle_battery_charge(self) -> float:
        # A dead link means no current reading: any prior value is stale and no
        # fresh one will arrive. Report the unknown sentinel rather than a
        # misleading last value or full-battery startup placeholder. This is the
        # same condition that raises JIBOT_CONNECTION_LOST, so battery-unknown
        # and the connection error always agree.
        if not self._is_jibot_link_healthy():
            return _BATTERY_CHARGE_UNKNOWN
        if not self._is_vehicle_battery_known():
            # Link is healthy but the first reading hasn't landed yet: a genuine
            # startup transient where the placeholder is honest.
            return self._clamp_percentage(self._get_startup_battery_soc())
        return self._clamp_percentage(getattr(self._vehicle, "_battery", None))

    def _get_startup_battery_soc(self) -> float:
        numeric = self._optional_float(
            getattr(self.config.jibot_client, "startup_battery_soc", 100.0)
        )
        return 100.0 if numeric is None else numeric

    def _is_vehicle_battery_known(self) -> bool:
        if self._vehicle is None:
            return False
        known = getattr(self._vehicle, "_battery_known", None)
        if known is not None:
            return bool(known)
        return getattr(self._vehicle, "_battery", None) is not None

    def _startup_pending_reasons(self) -> List[str]:
        reasons: List[str] = []
        if self._vehicle is None:
            reasons.append("vehicle")
        if (
            bool(getattr(self.config.jibot_client, "require_battery_before_ready", True))
            and not self._is_vehicle_battery_known()
        ):
            reasons.append("battery")
        return reasons

    def _is_startup_initializing(self) -> bool:
        return bool(self._startup_pending_reasons())

    def _get_vehicle_battery_voltage(self) -> Optional[float]:
        return self._optional_float(getattr(self._vehicle, "_battery_voltage", None))

    def _get_vehicle_battery_health(self) -> Optional[float]:
        return self._optional_float(getattr(self._vehicle, "_battery_health", None))

    def _get_vehicle_battery_current(self) -> Optional[float]:
        return self._optional_float(getattr(self._vehicle, "_battery_current", None))

    def _clamp_percentage(self, value: Any) -> float:
        numeric = self._optional_float(value)
        if numeric is None:
            return 0.0
        return max(0.0, min(100.0, numeric))

    def _optional_float(self, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _jibot_text_contains(self, *needles: str) -> bool:
        mode = str(getattr(self._vehicle, "_mode", "") or "").lower()
        status = str(getattr(self._vehicle, "_status", "") or "").lower()
        text = f"{mode} {status}"
        return any(str(needle).lower() in text for needle in needles if needle)

    def _derive_operating_mode(self) -> OperatingMode:
        if self._is_startup_initializing():
            return OperatingMode.STARTUP
        # Motor disabled without a real e-stop (Press ON to Enable) → MANUAL, so
        # the eq reuses its existing operatingMode=MANUAL → AmrState.MANUAL path.
        if getattr(self, "_jibot_stop_reason", None) == "MANUAL":
            return OperatingMode.MANUAL
        # Joystick/teleop driving: the robot is under a human's hand, so the FMS
        # must not read the state as an AGV executing its order autonomously.
        if self._is_jibot_manual_drive():
            return OperatingMode.MANUAL
        return OperatingMode.AUTOMATIC

    def _derive_driving(self) -> bool:
        if self._is_jibot_obstacle_wait():
            return False

        status = str(getattr(self._vehicle, "_status", "") or "").lower()
        driving_status = str(self.config.jibot_status.driving or "").lower()
        motions = [
            str(motion).lower()
            for motion in self.config.jibot_status.motion
            if motion
        ]

        if driving_status and driving_status == status:
            return True
        return any(motion in status for motion in motions)

    def _is_jibot_obstacle_wait(self) -> bool:
        """True when JIBOT reports brake, i.e. stopped and waiting for an object
        in its path to clear. This is a recoverable obstacle wait, not a crash."""
        return self._jibot_text_contains("#brake", " brake")

    def _is_jibot_manual_drive(self) -> bool:
        """True while JIBOT reports a hand-driven mode (joystick/teleop).

        Manual driving discards whatever task was running, so this is both the
        reason a goto disappears mid-order and the reason the adapter must not
        command motion until the operator hands the robot back.
        """
        if self._vehicle is None:
            return False
        mode = str(getattr(self._vehicle, "_mode", "") or "").strip().lower()
        if not mode:
            return False
        return any(
            mode == str(manual_mode).strip().lower()
            for manual_mode in getattr(self.config.jibot_status, "manual_modes", ())
        )

    def _is_jibot_lost(self) -> bool:
        """True when JIBOT reports a localization/path-lost status (status token
        "lost", e.g. "...#lost"). This is a hard fault requiring relocalization,
        distinct from a TCP link loss (JIBOT_CONNECTION_LOST)."""
        return self._jibot_text_contains("#lost", " lost")

    def _is_jibot_motor_disabled(self) -> bool:
        """True when JIBOT's own task status reports the motor disabled ("#disable").

        Arrives over TCP 7273, so this still works when the ROS listener is down
        and the /jrobot_status stop-reason subdivision is unavailable. Says only
        that the motor is off — not why — so callers must not read it as a bumper.
        """
        return self._jibot_text_contains("#disable", " disable")

    def _jibot_safety_if_fresh(self):
        """Return cached /jrobot_status safety dict if fresh, else None.

        None means the subdivision data is unavailable (listener down, sim, or
        stale) and callers must fall back to the legacy motor_flag behaviour.
        """
        v = self._vehicle
        if v is None:
            return None
        safety = getattr(v, "_robot_safety", None)
        last = getattr(v, "_robot_safety_last_update", 0.0)
        if not safety or last <= 0.0:
            return None
        if (time.monotonic() - last) > self.config.bms_ros.stale_after_sec:
            return None
        return safety

    def _derive_jibot_stop_reason(self):
        """Classify the robot stop/disable reason from /jrobot_status fields.

        Returns one of NONE/MANUAL/EMERGENCY/PROTECTIVE_STOP/BUMPER/MOTOR_FAULT,
        or None when safety data is stale/absent. Keys off the firmware
        system_status TEXT first (polarity-safe), then boolean flags. Priority:
        EMERGENCY > PROTECTIVE_STOP > MOTOR_FAULT > BUMPER > MANUAL > NONE.
        """
        safety = self._jibot_safety_if_fresh()
        if safety is None:
            return None
        status = str(safety.get("system_status", "") or "").strip().lower()

        def on(name):
            return str(safety.get(name, "") or "").strip() in ("1", "true", "True")

        if "estop pressed" in status or on("hmi_estop"):
            return "EMERGENCY"
        if "enter estop" in status or on("pc_estop"):
            return "PROTECTIVE_STOP"
        if on("motor_error"):
            return "MOTOR_FAULT"
        if "bumper" in status:
            return "BUMPER"
        motor_enable = str(safety.get("motor_enable", "") or "").strip()
        if "press on to enable" in status or motor_enable in ("0", "false", "False"):
            return "MANUAL"
        return "NONE"

    def _is_jibot_stopped(self) -> bool:
        status = str(getattr(self._vehicle, "_status", "") or "").strip().lower()
        stop_status = str(getattr(self.config.jibot_status, "stop", "") or "").strip().lower()
        if stop_status and status == stop_status:
            return True
        return status in {"stop", "stopped"}

    def _jibot_avoidance_reason(self) -> Optional[str]:
        if self._jibot_text_contains("#slowdown", " slowdown"):
            return "slowdown"
        if self._jibot_text_contains("#watch", " watch"):
            return "watch"
        return None

    def _build_status_information(self) -> List[Information]:
        if self._vehicle is None:
            return []

        localization_score = getattr(self._vehicle, "_localization_score", None)
        cur_task = getattr(self._vehicle, "_cur_task", None) or {}
        cur_task_data = cur_task.get("data", {}) if isinstance(cur_task, dict) else {}
        cur_task_value = (
            cur_task_data.get("value", {}) if isinstance(cur_task_data, dict) else {}
        )
        task_info = getattr(self._vehicle, "_task_info", None) or {}
        path = getattr(self._vehicle, "_path", None) or {}
        path_points = path.get("points", []) if isinstance(path, dict) else []
        path_end = path_points[-1] if isinstance(path_points, list) and path_points else None
        startup_pending = self._startup_pending_reasons()
        battery_known = self._is_vehicle_battery_known()

        # Only values not already carried by a dedicated state field belong
        # here: voltage/current/health are exact duplicates of powerSupply and
        # the pose units are static (advertised once in the factsheet).
        # jibotBattery stays as the raw SOC: powerSupply.stateOfCharge is
        # clamped, so the raw value is needed to diagnose a missing reading.
        references = [
            InfoReference("adapterInitializing", "true" if startup_pending else "false"),
            InfoReference("adapterInitPending", ",".join(startup_pending)),
            InfoReference("jibotMode", str(getattr(self._vehicle, "_mode", "") or "")),
            InfoReference("jibotStatus", str(getattr(self._vehicle, "_status", "") or "")),
            InfoReference("jibotStation", str(getattr(self._vehicle, "_station", "") or "")),
            # Authoritative motor power flag fetched via UmGetMotorState polling
            # (_motor_flag == 0 means motor off). Lets the WebUi show the real
            # motor state instead of inferring it from safetyState.eStop.
            InfoReference(
                "jibotMotorState",
                "stopped" if getattr(self._vehicle, "_motor_flag", None) == 0 else "running",
            ),
            InfoReference(
                "jibotLocalizationScore",
                "" if localization_score is None else str(localization_score),
            ),
            InfoReference("jibotBatteryKnown", "true" if battery_known else "false"),
            InfoReference("jibotBattery", str(getattr(self._vehicle, "_battery", "") or "")),
            InfoReference("jibotBatteryTemperatures", json.dumps(getattr(self._vehicle, "_battery_temperatures", []), ensure_ascii=False)),
            InfoReference("jibotBatteryCells", json.dumps(getattr(self._vehicle, "_battery_cells", None), ensure_ascii=False)),
            InfoReference("jibotCurTaskCommand", str(cur_task_value.get("cmd", "") if isinstance(cur_task_value, dict) else "")),
            InfoReference("jibotCurTaskGoal", str(cur_task_value.get("goal", "") if isinstance(cur_task_value, dict) else "")),
            InfoReference("jibotCurTaskStatus", str(cur_task_data.get("status", "") if isinstance(cur_task_data, dict) else "")),
            InfoReference("jibotTaskInfoStatus", str(task_info.get("status", "") if isinstance(task_info, dict) else "")),
            InfoReference("jibotPathNum", str(path.get("num", 0) if isinstance(path, dict) else 0)),
            InfoReference("jibotPathEnd", "" if path_end is None else json.dumps(path_end, ensure_ascii=False)),
        ]

        return [
            Information(
                info_type="JIBOT_STATUS",
                info_level=InfoLevel.INFO,
                info_references=references,
                info_description="Raw JIBOT status and battery values",
            ),
        ]

    def _refresh_status_information(self) -> None:
        if self.state is None:
            return

        self.state.information = [
            info
            for info in self.state.information
            if getattr(info, "info_type", None) != "JIBOT_STATUS"
        ]
        self.state.information.extend(self._build_status_information())

    def _derive_amr_working_state(self) -> Tuple[str, str]:
        """Compute (workingState, workingStateDetail) from current state signals.

        The adapter owns this mapping so the eq can consume the result directly
        (information[] AMR_STATE) instead of re-deriving it. Priority:
          - workingState: ERROR > BLOCKED > PAUSED > CHARGING > DRIVING > ACTING > IDLE
          - detail:       EMERGENCY > LOST > FAULT >
                          LOADING/UNLOADING/DOCKING > NONE
        Reason group (EMERGENCY/LOST/FAULT) corresponds to workingState=ERROR.
        ACTING means an operation (loading/unloading/docking) is in progress, so
        it always carries a non-NONE detail; a bare active order with no such
        operation and no motion stays IDLE (never ACTING+NONE).
        Must be called after the cycle's driving/eStop/charging/errors are set.
        """
        s = self.state
        if s is None:
            return "IDLE", "NONE"

        safety = getattr(s, "safety_state", None)
        e_stop = getattr(safety, "e_stop", None)
        estopped = e_stop is not None and e_stop != EStop.NONE
        field_violation = bool(getattr(safety, "field_violation", False))
        charging = bool(getattr(getattr(s, "battery_state", None), "charging", False))
        driving = bool(getattr(s, "driving", False))
        paused = bool(getattr(s, "paused", False))
        has_fatal = any(
            getattr(e, "error_level", None) == ErrorLevel.FATAL
            for e in (getattr(s, "errors", None) or [])
        )
        docking = bool(self._docking_started_node_ids)
        work = self._work_in_progress  # "loading" | "unloading" | None

        # detail: reason group (ERROR causes) wins over operation group
        if estopped:
            detail = "EMERGENCY"
        elif self._is_jibot_lost():
            detail = "LOST"
        elif has_fatal:
            detail = "FAULT"
        elif field_violation and not driving:
            detail = "BRAKE"
        elif work == "loading":
            detail = "LOADING"
        elif work == "unloading":
            detail = "UNLOADING"
        elif docking:
            detail = "DOCKING"
        else:
            detail = "NONE"

        # coarse working state
        if has_fatal or estopped:
            working_state = "ERROR"
        elif field_violation and not driving:
            working_state = "BLOCKED"
        elif paused:
            working_state = "PAUSED"
        elif charging:
            working_state = "CHARGING"
        elif driving:
            working_state = "DRIVING"
        elif work is not None or docking:
            working_state = "ACTING"
        else:
            working_state = "IDLE"

        return working_state, detail

    def _active_action_types(self) -> List[str]:
        """Return active parent action types without changing working state.

        Both order and instant actions are included. Preserve state-array order
        and remove duplicates so the singular reference has a deterministic
        primary value while the JSON list retains concurrent actions. WAITING
        order actions are deliberately excluded because they may be many steps
        ahead and have not started yet.
        """
        if self.state is None:
            return []
        active: List[str] = []
        for collection_name in ("action_states", "instant_action_states"):
            for action_state in getattr(self.state, collection_name, None) or []:
                status = getattr(action_state, "action_status", "")
                status_value = status.value if isinstance(status, ActionStatus) else str(status)
                if status_value not in {
                    ActionStatus.INITIALIZING.value,
                    ActionStatus.RUNNING.value,
                    ActionStatus.PAUSED.value,
                    "INITIALIZATION",
                }:
                    continue
                action_type = str(
                    getattr(action_state, "action_type", "") or ""
                ).strip()
                if action_type and action_type not in active:
                    active.append(action_type)
        return active

    def _active_action_step_types(self) -> List[str]:
        """Return current synthetic recipe step types in stable parent order."""
        return list(dict.fromkeys(self._active_action_steps.values()))

    def _set_active_action_step(
        self,
        parent_action_id: str,
        action_type: Optional[str],
    ) -> None:
        """Publish one recipe parent's current synthetic child action."""
        if action_type:
            self._active_action_steps[parent_action_id] = action_type
            reason = f"action step {action_type} started"
        else:
            self._active_action_steps.pop(parent_action_id, None)
            reason = "action step terminal"
        self.request_state_publish(reason)

    def _refresh_amr_state_information(self) -> None:
        """Publish adapter-computed AMR working state as an AMR_STATE info block.

        Recomputed each publish cycle; the eq reads workingState/workingStateDetail
        directly from here. Must run late in the publish loop (after driving/eStop/
        charging/errors and _sync_docking_action_state are updated).
        """
        if self.state is None:
            return

        working_state, detail = self._derive_amr_working_state()
        self._state_action_controller.observe(working_state)
        active_actions = self._active_action_types()
        active_steps = self._active_action_step_types()
        self.state.information = [
            info
            for info in self.state.information
            if getattr(info, "info_type", None) != "AMR_STATE"
        ]
        self.state.information.append(
            Information(
                info_type="AMR_STATE",
                info_level=InfoLevel.INFO,
                info_references=[
                    InfoReference("workingState", working_state),
                    InfoReference("workingStateDetail", detail),
                    InfoReference(
                        "activeActionType",
                        active_actions[0] if active_actions else "",
                    ),
                    InfoReference(
                        "activeActionTypes",
                        json.dumps(active_actions, ensure_ascii=False),
                    ),
                    InfoReference(
                        "activeStepActionType",
                        active_steps[0] if active_steps else "",
                    ),
                    InfoReference(
                        "activeStepActionTypes",
                        json.dumps(active_steps, ensure_ascii=False),
                    ),
                ],
                info_description="Adapter-computed AMR working and active action state",
            )
        )

    def _refresh_jibot_safety_information(self) -> None:
        """Publish raw /jrobot_status safety fields + derived stopReason as a
        JIBOT_SAFETY info block. Always present; empty strings when stale/absent
        so the eq never retains a stale value."""
        if self.state is None:
            return
        safety = self._jibot_safety_if_fresh()
        reason = self._derive_jibot_stop_reason()

        def f(name):
            return "" if safety is None else str(safety.get(name, "") or "")

        references = [
            InfoReference("stopReason", reason or ""),
            InfoReference("systemStatus", f("system_status")),
            InfoReference("motorEnable", f("motor_enable")),
            InfoReference("hmiEstop", f("hmi_estop")),
            InfoReference("bumperEstop", f("bumpe_stop")),
            InfoReference("pcEstop", f("pc_estop")),
            InfoReference("motorError", f("motor_error")),
            InfoReference("pcEnable", f("pc_enable")),
            InfoReference("charge", f("charge")),
        ]
        err_code = f("system_error_code")
        err_name, err_desc = decode_system_error_code(err_code)
        references += [
            InfoReference("systemErrorCode", err_code),
            InfoReference("systemErrorName", err_name),
            InfoReference("systemErrorDescription", err_desc),
            InfoReference("wheelLeftStatus", f("l_status")),
            InfoReference("wheelLeftError", f("l_error")),
            InfoReference("wheelRightStatus", f("r_status")),
            InfoReference("wheelRightError", f("r_error")),
            InfoReference("liftStatus", f("lift_status")),
            InfoReference("rotateStatus", f("rotate_status")),
        ]
        self.state.information = [
            i for i in self.state.information
            if getattr(i, "info_type", None) != "JIBOT_SAFETY"
        ]
        self.state.information.append(
            Information(
                info_type="JIBOT_SAFETY",
                info_level=InfoLevel.INFO,
                info_references=references,
                info_description="JIBOT motor/safety stop reason and raw flags",
            )
        )

    def _resolve_sound_track(self) -> Optional[str]:
        ss = self.config.sound_settings
        if not ss.enabled:
            return None

        safety = self._jibot_safety_if_fresh()
        if safety is not None:
            err_name, _ = decode_system_error_code(safety.get("system_error_code"))
            if err_name:
                candidate = f"{err_name}.wav"
                if self._sound.has_track(candidate):
                    return candidate

        working_state, detail = self._derive_amr_working_state()
        if detail == "BRAKE":
            return "brake.mp3" if self._sound.has_track("brake.mp3") else None
        if detail in {"EMERGENCY", "LOST", "FAULT"}:
            # Stop-reason subtype (bumper.mp3, motor_fault.mp3, ...) wins over the
            # generic detail file so distinct fault causes can sound distinct.
            # Guard both None (safety data stale/absent) and the string "NONE"
            # (no stop reason) -- otherwise this probes nonsense like none.mp3,
            # which also fires for FATAL errors unrelated to the stop reason
            # (JIBOT_CONNECTION_LOST, JIBOT_DOCK_FAILED, ...).
            stop_reason = self._jibot_stop_reason
            if stop_reason and stop_reason != "NONE":
                candidate = f"{stop_reason.lower()}.mp3"
                if self._sound.has_track(candidate):
                    return candidate
            for token in (detail, working_state):
                candidate = f"{token.lower()}.mp3"
                if self._sound.has_track(candidate):
                    return candidate
            return None

        avoidance_reason = self._jibot_avoidance_reason()
        if avoidance_reason is not None:
            candidate = f"{avoidance_reason}.mp3"
            if self._sound.has_track(candidate):
                return candidate

        # An action sound does not replace the AMR working state; it is only a
        # more specific playback convention. During a recipe, the current
        # synthetic child wins over its parent recipe action.
        for action_type in (
            *self._active_action_step_types(),
            *self._active_action_types(),
        ):
            safe_type = re.sub(r"[^A-Za-z0-9_.-]+", "_", action_type).strip("._")
            if not safe_type:
                continue
            candidate = f"action-{safe_type}.mp3"
            if self._sound.has_track(candidate):
                return candidate

        # Play the most specific available file: a workingStateDetail match
        # (e.g. loading.mp3) wins over the coarse workingState (e.g. driving.mp3).
        # Filenames are the lowercased token; no matching file -> silence.
        for token in (detail, working_state):
            if not token or token == "NONE":
                continue
            candidate = f"{token.lower()}.mp3"
            if self._sound.has_track(candidate):
                return candidate
        return None

    def _resolve_sound_replay_gap(self, track: str) -> float:
        ss = self.config.sound_settings
        default_gap = max(0.0, float(getattr(ss, "state_replay_gap_sec", 0.0) or 0.0))
        overrides = getattr(ss, "state_replay_gap_overrides", {}) or {}
        working_state, detail = self._derive_amr_working_state()
        # Same tiers _resolve_sound_track picks from, most specific first. The
        # stop reason must be included: a bumper plays bumper.mp3, so keying the
        # override off detail alone would never match it and the sound would fall
        # back to the continuous default while the robot sits in contact.
        tokens = [
            token
            for token in (self._jibot_stop_reason, detail, working_state)
            if token and token != "NONE"
        ]
        # An override only applies to a track this cascade actually chose;
        # otherwise a state override would leak onto an action-*.mp3 track.
        if not any(track == f"{token.lower()}.mp3" for token in tokens):
            return default_gap
        for token in tokens:
            key = token.lower()
            if key in overrides:
                try:
                    return max(0.0, float(overrides[key]))
                except (TypeError, ValueError):
                    return default_gap
        return default_gap

    def _sound_executor(self) -> ThreadPoolExecutor:
        """One worker thread, so queued playback stays in submission order."""
        if self._sound_worker is None:
            self._sound_worker = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="sound"
            )
            # atexit is LIFO and SoundPlayer registered its own stop() hook back
            # in __init__, so this drains the worker first. The other order would
            # let a queued play() start after stop() and leave mplayer running.
            atexit.register(self._shutdown_sound_worker)
        return self._sound_worker

    def _submit_sound(self, fn, *args, **kwargs) -> None:
        """Run one blocking SoundPlayer call off the adapter event loop.

        Fire-and-forget: SoundPlayer swallows its own failures, so there is no
        result to await and nothing the publish loop could do with one. With no
        adapter loop running (startup, shutdown, tests) there is nothing to
        protect, so the call stays inline and keeps its original ordering.
        """
        if self._loop is None or not self._loop.is_running():
            fn(*args, **kwargs)
            return
        try:
            self._sound_executor().submit(fn, *args, **kwargs)
        except RuntimeError as exc:  # executor already shut down
            print(f"[SOUND] dispatch skipped: {exc}")

    JOYSTICK_CONNECT_TRACK = "joystick-connected.mp3"

    def play_joystick_connect_sound(self) -> None:
        """조이스틱이 붙었을 때 한 번 울리는 알림음.

        화면 없는 로봇이라 컨트롤러가 실제로 붙었는지 확인할 방법이 로그뿐이었다.
        _dispatch_sound 가 아니라 _submit_sound 로 바로 던진다: 연결음은 상태음이
        아니라 일회성 알림이므로 auto driver 의 상태 요청 자리를 차지하면 안 된다.

        대신 _sound_request 를 비운다. SoundPlayer.play() 는 현재 트랙을 먼저
        stop 시키므로 주행 중이면 상태음 루프가 끊기는데, 요청을 그대로 두면
        _update_sound_for_working_state 의 dedupe 가 "이미 그 트랙 요청함"으로
        보고 다시 큐에 넣지 않아 그대로 침묵한다. 비워 두면 다음 publish 주기
        (state_frequency, 보통 1Hz)에 상태음이 되살아난다.

        트랙이 없으면 아무것도 하지 않는다. 파일을 아직 안 넣은 로봇에서
        상태음만 끊고 끝나는 게 제일 나쁜 결과다.
        """
        if not self._sound.has_track(self.JOYSTICK_CONNECT_TRACK):
            return
        self._submit_sound(self._sound.play, self.JOYSTICK_CONNECT_TRACK, loop=False)
        self._sound_request = None

    def _dispatch_sound(
        self, request: Optional[Tuple[str, float, bool, int]], *, force: bool = False
    ) -> None:
        """Queue a play/stop request, skipping one identical to the last.

        SoundPlayer.play() dedupes same-track calls too, but that only happens
        once the worker picks the job up. Without this guard the auto driver
        would enqueue one job per publish cycle while a long state sound loops.
        ``force`` is for an operator command (stopSound), which must reach the
        player even when the adapter believes nothing is playing.
        """
        if request == self._sound_request and not force:
            return
        self._sound_request = request
        if request is None:
            self._submit_sound(self._sound.stop)
            return
        track, replay_gap, loop, repeat = request
        self._submit_sound(
            self._sound.play,
            track,
            loop=loop,
            replay_gap_sec=replay_gap,
            repeat_count=repeat,
        )

    def _drain_sound_worker(self, timeout: float = 5.0) -> None:
        """Block until queued playback reached the player.

        The worker is single-threaded, so a barrier job finishing means every
        earlier submission finished too. Callers are shutdown and tests -- not
        the publish loop, which must never wait on playback.
        """
        worker = self._sound_worker
        if worker is None:
            return
        try:
            worker.submit(lambda: None).result(timeout)
        except Exception as exc:  # noqa: BLE001 - draining must not raise
            print(f"[SOUND] drain failed: {exc}")

    def _shutdown_sound_worker(self) -> None:
        """Drain queued playback before SoundPlayer's atexit hook kills mplayer."""
        self._sound_request = None
        worker, self._sound_worker = self._sound_worker, None
        if worker is not None:
            worker.shutdown(wait=True)

    def _resolve_sound_repeat_count(self, track: str) -> int:
        """How many times ``track`` plays before going quiet. 0 = until it stops
        being the chosen track.

        Keyed on the file name rather than on a state token, so an action sound
        is addressable too -- _resolve_sound_replay_gap deliberately refuses to
        apply a state override to an action-*.mp3, and reusing that keying here
        would leave action sounds with no way to say "play once".
        """
        ss = self.config.sound_settings
        default = self._normalize_repeat_count(
            getattr(ss, "state_repeat_count", 0), 0
        )
        overrides = getattr(ss, "repeat_count_overrides", {}) or {}
        key = track.rsplit(".", 1)[0]
        if key not in overrides:
            return default
        return self._normalize_repeat_count(overrides[key], default)

    @staticmethod
    def _normalize_repeat_count(value: Any, fallback: int) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return fallback

    def _update_sound_for_working_state(self) -> None:
        if not self._sound_started:
            self._sound_started = True
            startup_volume = self.config.sound_settings.startup_volume
            if startup_volume is not None:
                self._submit_sound(self._sound.set_volume, startup_volume)
        if self._sound_test_until > time.monotonic():
            return  # bounded test override active; auto yields
        try:
            track = self._resolve_sound_track()
            if track is None:
                self._dispatch_sound(None)
            else:
                self._dispatch_sound(
                    (
                        track,
                        self._resolve_sound_replay_gap(track),
                        True,
                        self._resolve_sound_repeat_count(track),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - never break the publish loop
            print(f"[SOUND] update failed: {exc}")

    def _is_simulator(self) -> bool:
        """True when the attached vehicle is the in-process JIBOT simulator."""
        return bool(getattr(self._vehicle, "is_simulator", False))

    def _decode_map_snapshot_value(self, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return value

    def _pose_tuple_from_snapshot(self, value: Any) -> Optional[Tuple[float, float, float]]:
        value = self._decode_map_snapshot_value(value)
        if value is None:
            return None

        if isinstance(value, dict):
            pose = value.get("nodePosition") or value.get("pose") or value
            pose = self._decode_map_snapshot_value(pose)
            if isinstance(pose, str):
                parts = pose.split()
                if len(parts) < 2:
                    return None
                try:
                    x = float(parts[0])
                    y = float(parts[1])
                    theta = float(parts[2]) if len(parts) > 2 else 0.0
                    return x, y, theta
                except ValueError:
                    return None
            if not isinstance(pose, dict):
                return self._pose_tuple_from_snapshot(pose)
            try:
                x = float(pose["x"])
                y = float(pose["y"])
                theta = float(pose.get("theta", 0.0) or 0.0)
                return x, y, theta
            except (KeyError, TypeError, ValueError):
                return None

        if isinstance(value, (list, tuple)):
            if len(value) < 2:
                return None
            try:
                x = float(value[0])
                y = float(value[1])
                theta = float(value[2]) if len(value) > 2 else 0.0
                return x, y, theta
            except (TypeError, ValueError):
                return None

        return None

    def _map_nodes_from_snapshot_payload(self, payload: Any) -> Dict[str, Tuple[float, float, float]]:
        payload = self._decode_map_snapshot_value(payload)
        if payload is None:
            return {}

        if isinstance(payload, dict):
            for key in ("nodes", "mapNodes", "map", "information"):
                if key in payload:
                    nested = self._map_nodes_from_snapshot_payload(payload[key])
                    if nested:
                        return nested

            nodes: Dict[str, Tuple[float, float, float]] = {}
            for name, pose in payload.items():
                pose_tuple = self._pose_tuple_from_snapshot(pose)
                if pose_tuple is not None:
                    nodes[str(name)] = pose_tuple
            return nodes

        if not isinstance(payload, list):
            return {}

        nodes = {}
        for item in payload:
            item = self._decode_map_snapshot_value(item)
            if not isinstance(item, dict):
                continue

            if "infoReferences" in item:
                item = {
                    ref.get("key"): ref.get("value")
                    for ref in item.get("infoReferences", [])
                    if isinstance(ref, dict) and ref.get("key")
                }

            node_id = item.get("nodeId") or item.get("name") or item.get("id")
            if not node_id:
                continue
            pose_tuple = self._pose_tuple_from_snapshot(item)
            if pose_tuple is not None:
                nodes[str(node_id)] = pose_tuple
        return nodes

    def _handle_set_map_snapshot_instant_action(self, action: Any) -> None:
        """Store an FMS-provided map snapshot for simulator mode.

        Real JIBOT vehicles own their map data, so this action is accepted only
        for simulator instances. The snapshot stays in memory and is used by
        _map_nodes() for lastNodeId/position matching.
        """
        if not self._is_simulator():
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=(
                    "setMapSnapshot is only supported when the adaptor runs in simulator mode"
                ),
            )
            return

        params = {param.key: param.value for param in action.action_parameters}
        payload = None
        for key in ("nodes", "mapNodes", "map", "information"):
            if key in params:
                payload = params[key]
                break
        if payload is None:
            payload = params

        nodes = self._map_nodes_from_snapshot_payload(payload)
        if not nodes:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=(
                    "setMapSnapshot requires map nodes with nodeId/name/id and x/y coordinates"
                ),
            )
            return

        self._fms_map_nodes = nodes
        if self._vehicle is not None:
            setattr(self._vehicle, "_map_nodes", nodes)

        map_id = params.get("mapId") or params.get("map_id")
        if map_id:
            self._current_map_id = str(map_id)

        self._update_instant_action_status(
            action.action_id,
            ActionStatus.FINISHED,
            result_description=f"Stored FMS map snapshot with {len(nodes)} nodes",
        )
        print(
            f"[FMS MAP SNAPSHOT] nodes={len(nodes)} "
            f"mapId={map_id or self._current_map_id}"
        )

    def _handle_get_map_instant_action(self, action: Any) -> None:
        """getMap instant-action 처리: requestId/mapId 추출 후 비동기 fetch+publish 스케줄.

        getMap VDA5050 instant-action handler: extracts requestId/mapId from
        action_parameters, then schedules an async JIBOT map fetch and MQTT
        publish on the adapter event loop. The action is accepted immediately;
        the publish completes asynchronously.
        """
        import datetime

        if self._vehicle is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        request_id = extract_request_id(
            getattr(action, "action_parameters", []),
            getattr(action, "action_id", ""),
        )
        map_id = self._current_map_id
        for param in getattr(action, "action_parameters", []) or []:
            if getattr(param, "key", None) == "mapId" and getattr(param, "value", None):
                map_id = str(param.value)
                break
        generated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

        vehicle = self._vehicle
        action_id = action.action_id
        print(
            f"[GET MAP] actionId={action_id} "
            f"requestId={request_id} mapId={map_id}"
        )

        async def _run_get_map() -> None:
            try:
                async with self._map_fetch_lock:
                    ok = await get_map_and_publish(
                        vehicle, self._mqtt,
                        map_id=map_id,
                        request_id=request_id,
                        generated_at=generated_at,
                    )
                if ok:
                    self._update_instant_action_status(
                        action_id,
                        ActionStatus.FINISHED,
                        result_description="map published",
                    )
                else:
                    self._update_instant_action_status(
                        action_id,
                        ActionStatus.FAILED,
                        result_description="map fetch/publish failed (stale/timeout/error)",
                    )
            except Exception as exc:  # noqa: BLE001
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FAILED,
                    result_description=f"getMap error: {exc}",
                )

        self._run_on_adapter_loop(lambda: _run_get_map())

    def _handle_get_parameters_instant_action(self, action: Any) -> None:
        """getParameters instant-action 처리: adaptor 설정을 항목 단위 스냅샷으로 publish.

        getMap 과 달리 로봇 소켓 왕복이 없고 파일 I/O 뿐이라 동기로 처리한다
        (_run_on_adapter_loop / _map_fetch_lock 불필요).

        WCS 설비 파라미터 레지스트리 계약의 조회 경로다. 응답은 amr/v3/{serial}/config 로 발행하며
        plugin 이 requestId 로 상관해 @FunctionCall 반환값으로 되돌린다.
        """
        import datetime

        request_id = extract_request_id(
            getattr(action, "action_parameters", []),
            getattr(action, "action_id", ""),
        )
        source = ""
        for param in getattr(action, "action_parameters", []) or []:
            if getattr(param, "key", None) == "source" and getattr(param, "value", None):
                source = str(param.value)
                break
        generated_at = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        action_id = action.action_id
        print(f"[GET CONFIG] actionId={action_id} requestId={request_id} source={source or '(all)'}")

        try:
            # 멀티로봇에서 configio.CONFIG_PATH 기본값을 쓰면 남의 설정을 발행하게 된다.
            # 반드시 이 인스턴스에 주입된 경로를 쓴다.
            items = self._parameter_items()
            # 원문 편집이 열린 source 면 파일 전문도 함께 싣는다. 이게 있어야 WCS 가
            # 블록 추가·삭제를 replaceText 로 보낼 수 있다. 못 실을 때는 사유를 함께
            # 올려서 WCS 화면이 "구조 변경 불가" 넉 자 대신 조치를 보여 줄 수 있게 한다
            source_text, text_block_reason = read_source_text_with_reason(
                self._parameter_paths, source
            )
            snapshot = build_parameter_snapshot(
                equipment_id=str(self.config.vehicle.serial_number),
                source=source,
                request_id=request_id,
                generated_at=generated_at,
                revision=self._parameter_revision(),
                items=items,
                text=source_text,
                text_block_reason=text_block_reason,
            )
            ok = publish_parameters(self._mqtt, snapshot)
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description=f"getParameters error: {exc}",
            )
            return

        if ok:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FINISHED,
                result_description=f"config published ({len(snapshot['items'])} items)",
            )
        else:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description="config publish failed",
            )

    def _parameter_revision(self, config_path: Any = None) -> int:
        """스냅샷에 실리는 파일들의 최신 수정시각(마이크로초)을 반환한다.

        config.toml 만 보면 extensions.hcl 을 고쳤을 때 리비전이 그대로라 낙관적 동시성이
        변경을 놓친다. 그래서 세 파일 중 최대값을 쓴다.

        ns 를 그대로 쓰면 1.78e18 이라 JS 안전 정수(9.007e15)를 넘어 GraphQL Float 왕복에서
        값이 바뀐다. 그러면 baseRevision 비교가 항상 어긋나 낙관적 동시성이 무력화된다.

        :param config_path: 하위호환용. 주면 그 파일만 본다
        :returns: 마이크로초 단위 정수 리비전
        """
        if config_path is not None:
            return int(Path(config_path).stat().st_mtime_ns // 1000)
        stamps = []
        for path in self._parameter_paths.values():
            try:
                stamps.append(int(Path(path).stat().st_mtime_ns // 1000))
            except OSError:
                continue
        return max(stamps) if stamps else 0

    def _parameter_items(self) -> list:
        """네 설정 파일을 합쳐 EquipmentParameterItem 목록을 만든다.

        :returns: config.toml + extensions.hcl + recipes.hcl + robots.hcl 항목
        """
        return build_all_items(self._parameter_paths)

    def _handle_set_parameters_instant_action(self, action: Any) -> None:
        """setParameters instant-action 처리: 변경분을 config.toml 에 적용한다.

        WCS 설비 파라미터 레지스트리(EPR) 계약의 쓰기 경로다. 결과는
        amr/v3/{serial}/parameters-result 로 발행하며 plugin 이 requestId 로 상관해
        @FunctionCall 반환값으로 되돌린다.

        저장은 configio.write_config 로만 한다. 이 파일에는 web UI 저장과 adapter 의
        음량 기억이라는 다른 writer 가 있어서, 직접 write 하면 서로의 수정을 통째로
        되돌린다. write_config 가 읽기부터 쓰기까지를 파일 락 안에 넣고 원자적으로 바꾼다.
        """
        import datetime
        import json as _json

        params = {p.key: p.value for p in getattr(action, "action_parameters", []) or []}
        request_id = extract_request_id(
            getattr(action, "action_parameters", []),
            getattr(action, "action_id", ""),
        )
        source = str(params.get("source") or "")
        action_id = action.action_id
        generated_at = (
            datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        )
        equipment_id = str(self.config.vehicle.serial_number)

        # 같은 요청을 다시 받아도 재적용하지 않는다. instant-action 은 재전송될 수 있고,
        # 재적용되면 그 사이 다른 경로로 바뀐 값을 조용히 되돌린다.
        cached = self._applied_parameter_requests.get(request_id)
        if cached is not None:
            print(f"[SET PARAMETERS] duplicate requestId={request_id} — 캐시 결과 재발행")
            publish_apply_result(self._mqtt, cached)
            self._update_instant_action_status(
                action_id, ActionStatus.FINISHED, result_description="duplicate request (cached)"
            )
            return

        def _reject(reason_key: str, reason: str, revision: int) -> None:
            result = build_apply_result(
                request_id=request_id,
                equipment_id=equipment_id,
                source=source,
                revision=revision,
                applied=[],
                failures=[{"key": reason_key, "reason": reason}],
                generated_at=generated_at,
            )
            self._applied_parameter_requests[request_id] = result
            publish_apply_result(self._mqtt, result)
            self._update_instant_action_status(
                action_id, ActionStatus.FAILED, result_description=reason
            )

        try:
            raw_changes = params.get("changes")
            changes = _json.loads(raw_changes) if isinstance(raw_changes, str) else raw_changes
            if not isinstance(changes, list) or not changes:
                _reject("", "changes 가 비어 있거나 배열이 아님", self._parameter_revision())
                return

            current_revision = self._parameter_revision()
            base_revision = params.get("baseRevision")
            if base_revision not in (None, "") and int(base_revision) != current_revision:
                _reject(
                    "",
                    f"PARAMETER_STALE_REVISION: base={base_revision} current={current_revision} — 다시 조회 후 편집 필요",
                    current_revision,
                )
                return

            snapshot_items = self._parameter_items()
            editable_keys = {i["key"] for i in snapshot_items if i.get("editable")}
            # 파일에 줄이 없다고 신고한 항목만 새로 적어 넣는다. 그 밖의 없는 키는
            # 오타로 보고 거부해야 로더가 모르는 설정이 조용히 쌓이지 않는다.
            creatable_keys = {
                i["key"] for i in snapshot_items if i.get("editable") and not i.get("present")
            }
            applied_all: list = []
            failures_all: list = []

            # source 마다 파일과 편집기가 다르므로 파일별로 따로 쓴다.
            # 한 파일이 실패해도 나머지는 적용하고 PARTIAL 로 보고한다.
            for change_source, source_changes in group_changes_by_source(changes).items():
                path = self._parameter_paths.get(change_source)
                if path is None or not Path(path).exists():
                    failures_all.extend(
                        {"key": str(c.get("key", "")), "reason": f"쓸 수 없는 source: {change_source or '(없음)'}"}
                        for c in source_changes
                    )
                    continue

                outcome: Dict[str, Any] = {}

                def _transform(text: str, _src=change_source, _changes=source_changes, _out=outcome) -> str:
                    new_text, applied, failures = apply_parameter_changes(
                        text, _changes,
                        editable_keys=editable_keys,
                        creatable_keys=creatable_keys,
                        source=_src,
                    )
                    _out["applied"] = applied
                    _out["failures"] = failures
                    if not applied:
                        # 쓸 게 없으면 파일을 건드리지 않는다(mtime 이 바뀌면 리비전만 헛돈다)
                        raise _NoParameterChange()
                    return new_text

                try:
                    configio.write_config(
                        path,
                        _transform,
                        # 부팅 로더로 재검증한다. robots.hcl 은 get_config 가 읽지 않는
                        # fleet 오버레이라 문법 검사만 가능하다.
                        validate=(
                            (lambda _p=path: _validate_hcl_on_disk(_p))
                            if change_source == "robots.hcl"
                            else (lambda: _validate_parameter_sources(self._parameter_paths))
                        ),
                    )
                except _NoParameterChange:
                    pass
                except (ValueError, TimeoutError, OSError) as exc:
                    failures_all.extend(
                        {"key": str(c.get("key", "")), "reason": f"{change_source} 저장 실패: {exc}"}
                        for c in source_changes
                    )
                    continue

                applied_all.extend(outcome.get("applied", []))
                failures_all.extend(outcome.get("failures", []))

            if not applied_all:
                result = build_apply_result(
                    request_id=request_id,
                    equipment_id=equipment_id,
                    source=source,
                    revision=current_revision,
                    applied=[],
                    failures=failures_all or [{"key": "", "reason": "적용할 변경이 없음"}],
                    generated_at=generated_at,
                )
                self._applied_parameter_requests[request_id] = result
                publish_apply_result(self._mqtt, result)
                self._update_instant_action_status(
                    action_id, ActionStatus.FAILED, result_description="no change applied"
                )
                return

            result = build_apply_result(
                request_id=request_id,
                equipment_id=equipment_id,
                source=source,
                revision=self._parameter_revision(),
                applied=applied_all,
                failures=failures_all,
                generated_at=generated_at,
            )
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 운영자에게 결과 envelope 으로 알린다
            _reject("", f"setParameters error: {exc}", self._parameter_revision())
            return

        self._applied_parameter_requests[request_id] = result
        publish_apply_result(self._mqtt, result)
        print(
            f"[SET PARAMETERS] requestId={request_id} status={result['status']} "
            f"applied={len(result['appliedKeys'])} failed={len(result['failures'])}"
        )
        self._update_instant_action_status(
            action_id,
            ActionStatus.FINISHED,
            result_description=f"{result['status']} applied={len(result['appliedKeys'])}",
        )

    def _handle_set_map_instant_action(self, action: Any) -> None:
        """setMap instant-action 처리: uamap payload를 JIBOT raw로 변환 후 저장/파일쓰기.

        실로봇: write_jibot_map으로 파일에 원자쓰기 후 FINISHED 보고.
        시뮬레이터: vehicle._map_raw에 변환된 raw를 저장하고 즉시 FINISHED 보고.
        payload 없음: 즉시 FAILED 보고.

        map_id는 payload.map.mapId 우선, 없으면 self._current_map_id 사용.
        실로봇에서 비동기 작업은 _run_on_adapter_loop으로 스케줄한다.
        """
        import datetime

        params = {
            getattr(p, "key", None): getattr(p, "value", None)
            for p in getattr(action, "action_parameters", []) or []
        }
        raw_payload = params.get("payload")
        action_id = action.action_id

        if raw_payload is None:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description="setMap requires 'payload' (uamap) in actionParameters",
            )
            return

        # payload may arrive as a JSON string (FunctionCallDataType.JSON over MQTT)
        try:
            payload = self._decode_map_snapshot_value(raw_payload)
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description=f"setMap payload decode error (encoding mismatch?): {exc}",
            )
            return
        if not isinstance(payload, dict):
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description=(
                    f"setMap payload must be a dict after decoding, got {type(payload).__name__} "
                    "(encoding mismatch?)"
                ),
            )
            return

        map_id = (
            (payload.get("map") or {}).get("mapId")
            or self._current_map_id
        )
        raw = to_jibot_snapshot(payload)

        print(
            f"[SET MAP] actionId={action_id} "
            f"mapId={map_id} simulator={self._is_simulator()}"
        )

        if self._is_simulator():
            # 시뮬레이터: in-memory 저장으로 read→write→read round-trip 지원
            if self._vehicle is not None:
                self._vehicle._map_raw = raw
            self._update_instant_action_status(
                action_id,
                ActionStatus.FINISHED,
                result_description=f"setMap stored in simulator memory for mapId={map_id}",
            )
            return

        # 실로봇: 비동기 파일쓰기
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S%f")

        async def _run_write_map() -> None:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    functools.partial(write_jibot_map, map_id, raw, timestamp=timestamp),
                )
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FINISHED,
                    result_description=f"setMap written to disk for mapId={map_id}",
                )
            except Exception as exc:  # noqa: BLE001
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FAILED,
                    result_description=f"setMap write error: {exc}",
                )

        self._run_on_adapter_loop(lambda: _run_write_map())

    def _handle_switch_map_action(self, action: Any) -> None:
        params = self._action_params(action)
        map_id = str(params.get("mapId") or "").strip()
        if not map_id:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="switchMap requires non-empty mapId",
            )
            return
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)
        if self._is_simulator():
            self._current_map_id = map_id
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FINISHED,
                result_description=f"current map confirmed: {map_id}",
            )
            return

        async def _run() -> None:
            try:
                await self._switch_map_and_confirm(map_id)
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FINISHED,
                    result_description=f"current map confirmed: {map_id}",
                )
            except Exception as exc:  # noqa: BLE001
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=f"switchMap failed: {exc}",
                )

        self._run_on_adapter_loop(_run, action_id=action.action_id)

    async def _switch_map_and_confirm(self, map_id: str) -> None:
        if self._vehicle is None:
            raise RuntimeError("JIBOT vehicle is not initialized")
        timeout = float(getattr(self.config.settings, "switch_map_timeout_seconds", 30.0))
        if not math.isfinite(timeout) or timeout <= 0:
            timeout = 30.0
        poll_interval = float(getattr(
            self.config.settings, "node_position_poll_interval_sec", 0.2
        ))
        poll_interval = max(0.001, poll_interval)
        await self._vehicle.set_map(map_id)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"current map did not become {map_id} within {timeout}s")
            current = await self._vehicle.get_map_name(timeout=min(remaining, 3.0))
            if current == map_id:
                self._current_map_id = current
                return
            await asyncio.sleep(min(poll_interval, remaining))

    async def _execute_switch_map_order_action(
        self,
        action: Any,
        action_state: ActionState,
    ) -> None:
        map_id = str(self._action_params(action).get("mapId") or "").strip()
        if not map_id:
            action_state.action_status = ActionStatus.FAILED
            action_state.result_description = "switchMap requires non-empty mapId"
            self.request_state_publish("order switchMap failed")
            return
        action_state.action_status = ActionStatus.RUNNING
        self.request_state_publish("order switchMap running")
        try:
            if self._is_simulator():
                self._current_map_id = map_id
            else:
                await self._switch_map_and_confirm(map_id)
            action_state.action_status = ActionStatus.FINISHED
            action_state.result_description = f"current map confirmed: {map_id}"
        except Exception as exc:  # noqa: BLE001
            action_state.action_status = ActionStatus.FAILED
            action_state.result_description = f"switchMap failed: {exc}"
        self.request_state_publish("order switchMap terminal")

    # -------------------------
    # Video: event snapshots + live-stream advertising
    # -------------------------
    def _active_video_events(self) -> Set[str]:
        """Configured snapshot events currently present in the JIBOT mode/status."""
        if not self.config.video.enabled or self._vehicle is None:
            return set()
        return {
            event
            for event in self.config.video.snapshot_events
            if event and self._jibot_text_contains(event)
        }

    def _schedule_event_snapshots(self) -> None:
        """On a rising edge of a watched event, fetch and publish a snapshot.

        Edge-triggered against the previous cycle so a sustained brake/slowdown
        does not refetch every cycle; the streamer also debounces by time. The
        HTTP fetch runs as a background task so state publishing is never blocked.
        """
        if not self.config.video.enabled or self._loop is None:
            return

        active = self._active_video_events()
        new_events = active - self._prev_video_events
        self._prev_video_events = active

        for event in new_events:
            self._loop.create_task(self._video.capture_event_snapshot(event))

    def _is_jibot_connected(self) -> bool:
        """True when the underlying JIBOT TCP connection is alive.

        JIBOT TCP 연결이 살아있을 때 True를 반환한다.

        Vehicles without an explicit connection probe (e.g. the simulator) are
        treated as connected. 명시적 연결 확인이 없는 차량(예: 시뮬레이터)은
        연결된 것으로 간주한다.
        """
        if self._vehicle is None:
            return False
        is_connected = getattr(self._vehicle, "is_connected", None)
        if callable(is_connected):
            return bool(is_connected())
        return True

    def _is_jibot_rx_stale(self) -> bool:
        """True when the socket is up but no JIBOT frame has arrived recently.

        소켓은 살아있지만 최근 JIBOT 프레임이 도착하지 않았을 때 True.
        """
        if self._vehicle is None:
            return False
        is_rx_stale = getattr(self._vehicle, "is_rx_stale", None)
        if callable(is_rx_stale):
            return bool(is_rx_stale(self._jibot_rx_timeout))
        return False

    def _jibot_seconds_since_rx(self) -> float:
        """Seconds since the last JIBOT frame, or 0.0 if not tracked."""
        if self._vehicle is None:
            return 0.0
        seconds_since_last_rx = getattr(self._vehicle, "seconds_since_last_rx", None)
        if callable(seconds_since_last_rx):
            return float(seconds_since_last_rx())
        return 0.0

    def _is_jibot_link_healthy(self) -> bool:
        """True only when the JIBOT link is both connected and actively streaming.

        JIBOT 링크가 연결되어 있고 실제로 데이터를 수신 중일 때만 True.
        """
        return self._is_jibot_connected() and not self._is_jibot_rx_stale()

    def _refresh_jibot_motor_fault_errors(self, active):
        """Set/clear a FATAL JIBOT_MOTOR_FAULT state error each cycle."""
        if self.state is None:
            return
        self.state.errors = [
            e for e in self.state.errors
            if getattr(e, "error_type", None) != ErrorType.JIBOT_MOTOR_FAULT
        ]
        if active:
            self.state.errors.append(
                Error(
                    error_type=ErrorType.JIBOT_MOTOR_FAULT,
                    error_level=ErrorLevel.FATAL,
                    error_references=[ErrorReference("reason", "motorError")],
                    error_description="JIBOT reported a drive motor fault (motor_error)",
                )
            )

    def _refresh_jibot_bumper_errors(self, active):
        """Set/clear a FATAL JIBOT_BUMPER state error each cycle.

        A bumper stop is physical contact, not an ordinary obstacle wait, so it
        is surfaced as FATAL: eq/ACS then reads workingState=ERROR detail=FAULT
        instead of BLOCKED/BRAKE.
        """
        if self.state is None:
            return
        self.state.errors = [
            e for e in self.state.errors
            if getattr(e, "error_type", None) != ErrorType.JIBOT_BUMPER
        ]
        if active:
            self.state.errors.append(
                Error(
                    error_type=ErrorType.JIBOT_BUMPER,
                    error_level=ErrorLevel.FATAL,
                    error_references=[ErrorReference("reason", "bumperTrigger")],
                    error_description="JIBOT reported a bumper contact stop (bumper Trigger!)",
                )
            )

    def _apply_jibot_stop_reason(self):
        """Drive safetyState/operatingMode/errors from the /jrobot_status stop
        reason. Falls back to the legacy motor_flag→AUTOACK e-stop when the
        safety data is stale/absent (reason is None)."""
        if self.state is None:
            return
        reason = self._derive_jibot_stop_reason()
        # Log on transition only: the publish loop runs ~3Hz, so logging every
        # cycle would flood the journal. Before this, a bumper stop left no trace
        # in the journal at all and had to be reconstructed from the robot's own
        # /usr/local/urobot/logs/bot_log recording.
        if reason != self._jibot_stop_reason:
            print(f"[JIBOT STOP REASON] {self._jibot_stop_reason} -> {reason}")
        self._jibot_stop_reason = reason  # cached for operatingMode + info block

        if reason is None:  # legacy fallback (no subdivision data)
            self.state.safety_state.e_stop = (
                EStop.AUTOACK if self._vehicle._motor_flag == 0 else EStop.NONE
            )
            self._refresh_jibot_motor_fault_errors(False)
            self._refresh_jibot_bumper_errors(False)
            return

        if reason == "EMERGENCY":
            self.state.safety_state.e_stop = EStop.MANUAL
        elif reason == "PROTECTIVE_STOP":
            self.state.safety_state.e_stop = EStop.REMOTE
        else:
            self.state.safety_state.e_stop = EStop.NONE

        if reason == "BUMPER":
            self.state.safety_state.field_violation = True
            self.state.driving = False

        self._refresh_jibot_motor_fault_errors(reason == "MOTOR_FAULT")
        self._refresh_jibot_bumper_errors(reason == "BUMPER")

    def _refresh_jibot_connection_errors(self) -> None:
        """Report a FATAL error in the state whenever the JIBOT link is down.

        JIBOT 연결이 끊긴 동안 state에 FATAL 오류를 보고한다.

        The error is purged automatically once the connection is restored, so
        ACS sees the disconnect as a recoverable, self-clearing condition.
        연결이 복구되면 오류가 자동으로 제거되어, ACS는 끊김을 자동 해제되는
        복구 가능한 상태로 인식한다.
        """
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.JIBOT_CONNECTION_LOST
        ]

        if self._is_jibot_link_healthy():
            return

        if not self._is_jibot_connected():
            reason = "disconnected"
            description = "Adapter lost the JIBOT TCP connection; robot is unreachable"
        else:
            reason = "rxTimeout"
            description = (
                "No JIBOT status frame received within "
                f"{self._jibot_rx_timeout:g}s; robot link is silent"
            )

        self.state.errors.append(
            Error(
                error_type=ErrorType.JIBOT_CONNECTION_LOST,
                error_level=ErrorLevel.FATAL,
                error_references=[
                    ErrorReference(
                        "robotIp",
                        str(getattr(self._vehicle, "robot_ip", "") or ""),
                    ),
                    ErrorReference(
                        "robotPort",
                        str(getattr(self._vehicle, "robot_port", "") or ""),
                    ),
                    ErrorReference("reason", reason),
                ],
                error_description=description,
            )
        )

    def _refresh_last_node_id_errors(self) -> None:
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.LAST_NODE_ID_MISSING
        ]

        last_node_id = str(getattr(self.state, "last_node_id", "") or "").strip()
        internal_last_node_id = str(self._last_node_id or "").strip()
        if last_node_id and internal_last_node_id:
            return

        position = getattr(self.state, "agv_position", None)
        last_node_gap = self._distance_to_node_id(
            last_node_id or internal_last_node_id,
            position,
        )
        references = [
            ErrorReference("lastNodeId", last_node_id),
            ErrorReference("adapterLastNodeId", internal_last_node_id),
            ErrorReference(
                "lastNodeSequenceId",
                str(getattr(self.state, "last_node_sequence_id", "") or ""),
            ),
            ErrorReference("orderId", str(getattr(self.state, "order_id", "") or "")),
            ErrorReference("nearestNodeId", str(self._nearest_node_id or "")),
            ErrorReference(
                "nearestNodeGap",
                "" if self._nearest_node_distance is None else f"{self._nearest_node_distance:.1f}",
            ),
            ErrorReference(
                "lastNodeGap",
                "" if last_node_gap is None else f"{last_node_gap:.1f}",
            ),
            ErrorReference("idleLastNodeReach", f"{self._idle_last_node_reach_xy():.1f}"),
            ErrorReference(
                "position",
                ""
                if position is None
                else f"{float(getattr(position, 'x', 0.0)):.1f},{float(getattr(position, 'y', 0.0)):.1f}",
            ),
        ]
        self.state.errors.append(
            Error(
                error_type=ErrorType.LAST_NODE_ID_MISSING,
                error_level=ErrorLevel.CRITICAL,
                error_references=references,
                error_description=(
                    "Adapter cannot publish a valid lastNodeId; current pose is "
                    "not within any known node reach zone yet"
                ),
            )
        )

    def _refresh_jibot_status_errors(self) -> None:
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None)
            not in (
                ErrorType.JIBOT_CONFLICT,
                ErrorType.JIBOT_AVOIDANCE,
                ErrorType.JIBOT_LOCALIZATION_LOST,
                ErrorType.JIBOT_MOTOR_DISABLED,
            )
        ]

        # brake (obstacle wait) is primarily conveyed through safety_state.field_violation
        # + driving=False. We ALSO surface it as a WARNING error so ACS sees a recoverable
        # obstacle wait in errors[] — WARNING (not FATAL/CRITICAL) since the robot resumes
        # automatically once the path clears.
        if self._is_jibot_obstacle_wait():
            self.state.errors.append(
                self._build_jibot_status_error(
                    ErrorType.JIBOT_CONFLICT,
                    "brake",
                    "JIBOT stopped and waiting for an object in its path to clear",
                )
            )

        avoidance_reason = self._jibot_avoidance_reason()
        if avoidance_reason is not None:
            self.state.errors.append(
                self._build_jibot_status_error(
                    ErrorType.JIBOT_AVOIDANCE,
                    avoidance_reason,
                    f"JIBOT reported obstacle avoidance: {avoidance_reason}",
                )
            )

        # lost (localization/path lost) is a hard fault: surface it as a FATAL
        # error so ACS/eq treats the robot as ERROR (eq derives a LOST sub-state
        # from this errorType). Distinct from JIBOT_CONNECTION_LOST (TCP link).
        if self._is_jibot_lost():
            self.state.errors.append(
                self._build_jibot_status_error(
                    ErrorType.JIBOT_LOCALIZATION_LOST,
                    "lost",
                    "JIBOT reported localization/path lost; relocalization required",
                    error_level=ErrorLevel.FATAL,
                )
            )

        # "#disable" means JIBOT itself considers the motor off. Unlike the
        # /jrobot_status subdivision this arrives over TCP 7273, so it survives a
        # dead ROS listener. WARNING (not FATAL): it does not identify the cause,
        # and a routine manual disable raises it too.
        #
        # Suppressed while charging: the firmware status text is literally
        # "charging#disable" for the whole dock cycle (_is_charging_status()
        # widens on that), so an undocked-vs-charging gate here would make this
        # WARNING permanent for hours a day and destroy its value as a bumper
        # backstop. A docked robot is not driving, so nothing is lost.
        #
        # Read self._vehicle._charging (not self.state.battery_state.charging):
        # the latter is assigned further down this cycle's publish loop, so
        # reading it here would lag by one cycle on every dock/undock edge.
        # _charging is refreshed from the same _status text on every
        # UmGetRobotInfo, so the two stay consistent within a cycle.
        if self._is_jibot_motor_disabled() and not self._vehicle._charging:
            self.state.errors.append(
                self._build_jibot_status_error(
                    ErrorType.JIBOT_MOTOR_DISABLED,
                    "disable",
                    "JIBOT reported the drive motor disabled (#disable)",
                )
            )

    def _build_jibot_status_error(
        self,
        error_type: ErrorType,
        reason: str,
        description: str,
        error_level: ErrorLevel = ErrorLevel.WARNING,
    ) -> Error:
        return Error(
            error_type=error_type,
            error_level=error_level,
            error_references=[
                ErrorReference(
                    "jibotMode",
                    str(getattr(self._vehicle, "_mode", "") or ""),
                ),
                ErrorReference(
                    "jibotStatus",
                    str(getattr(self._vehicle, "_status", "") or ""),
                ),
                ErrorReference("jibotReason", reason),
            ],
            error_description=description,
        )

    def _set_jibot_node_unreached_error(
        self,
        node: Any,
        target_x: float,
        target_y: float,
        vx: float,
        vy: float,
        deviation_xy: float,
        station: str,
        error_level: ErrorLevel = ErrorLevel.WARNING,
        retries: Optional[int] = None,
    ) -> None:
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.JIBOT_NODE_UNREACHED
        ]
        distance_xy = math.hypot(vx - target_x, vy - target_y)
        references = [
            ErrorReference("nodeId", str(getattr(node, "node_id", "") or "")),
            ErrorReference("sequenceId", str(getattr(node, "sequence_id", "") or "")),
            ErrorReference("station", station),
            ErrorReference("jibotStatus", str(getattr(self._vehicle, "_status", "") or "")),
            ErrorReference("target", f"{target_x:.1f},{target_y:.1f}"),
            ErrorReference("position", f"{vx:.1f},{vy:.1f}"),
            ErrorReference("distance", f"{distance_xy:.1f}"),
            ErrorReference("reachRadius", f"{deviation_xy:.1f}"),
            ErrorReference("command", "UmGoto"),
        ]
        description = (
            "JIBOT stopped outside the order node pose reach zone after UmGoto. "
            "No usable route may exist for this target, or the target may require "
            "a different motion semantic such as UmDock."
        )
        if retries is not None:
            references.append(ErrorReference("retries", str(retries)))
            description = (
                f"{description} The goto was re-sent {retries} time(s) and the robot "
                "still did not reach the node; the order cannot make progress "
                "without master control."
            )
        self.state.errors.append(
            Error(
                error_type=ErrorType.JIBOT_NODE_UNREACHED,
                error_level=error_level,
                error_references=references,
                error_description=description,
            )
        )
        self.request_state_publish("node unreached")

    def _status_goal_hint(self, status: Any) -> str:
        text = str(status or "").strip()
        if not text:
            return ""
        lower = text.lower()
        if lower.startswith("nrunto "):
            parts = text.split()
            if len(parts) >= 2:
                return parts[1].strip()
        return ""

    def _status_goal_pose(self, status: Any) -> Optional[Tuple[float, float]]:
        """(x, y) of a ``nrunto pose (x y th)`` status, else None.

        A pose goto names no station, so "pose" is not a goal to compare with a
        nodeId; the coordinates are the only honest signal it carries.
        """
        text = str(status or "").strip()
        if self._status_goal_hint(text).lower() != "pose":
            return None
        match = self._POSE_GOAL_RE.search(text)
        if match is None:
            return None
        return float(match.group(1)), float(match.group(2))

    def _cur_task_goal_pose(self, cur_task_value: Any) -> Optional[Tuple[float, float]]:
        """(x, y) of a pose-target ``UmGetCurTask``, else None.

        JIBOT answers a pose goto with ``target="pose"`` and ``goal="none"``,
        so the literal goal name would never match the order node.
        """
        if not isinstance(cur_task_value, dict):
            return None
        if str(cur_task_value.get("target", "") or "").strip().lower() != "pose":
            return None
        x = self._optional_float(cur_task_value.get("x"))
        y = self._optional_float(cur_task_value.get("y"))
        if x is None or y is None:
            return None
        return x, y

    def _collect_jibot_arrival_signal_mismatches(
        self,
        node: Any,
        target_x: float,
        target_y: float,
        deviation_xy: float,
    ) -> Tuple[List[ErrorReference], List[str]]:
        node_id = str(getattr(node, "node_id", "") or "")
        references = [
            ErrorReference("nodeId", node_id),
            ErrorReference("sequenceId", str(getattr(node, "sequence_id", "") or "")),
        ]
        mismatch_labels: List[str] = []

        cur_task = getattr(self._vehicle, "_cur_task", None) or {}
        cur_task_data = cur_task.get("data", {}) if isinstance(cur_task, dict) else {}
        cur_task_value = (
            cur_task_data.get("value", {}) if isinstance(cur_task_data, dict) else {}
        )
        if isinstance(cur_task_value, dict):
            cur_task_pose = self._cur_task_goal_pose(cur_task_value)
            cur_task_goal = str(cur_task_value.get("goal", "") or "").strip()
            if cur_task_pose is not None:
                if not self._pose_in_reach_zone(
                    cur_task_pose[0], cur_task_pose[1], target_x, target_y, deviation_xy
                ):
                    references.append(
                        ErrorReference(
                            "curTaskGoal",
                            f"{cur_task_pose[0]:.1f},{cur_task_pose[1]:.1f}",
                        )
                    )
                    mismatch_labels.append("curTaskGoal")
            elif cur_task_goal and cur_task_goal != node_id:
                references.append(ErrorReference("curTaskGoal", cur_task_goal))
                mismatch_labels.append("curTaskGoal")

        for key, status in (
            ("jibotStatusGoal", getattr(self._vehicle, "_status", "")),
            (
                "curTaskStatusGoal",
                cur_task_data.get("status", "") if isinstance(cur_task_data, dict) else "",
            ),
            (
                "taskInfoStatusGoal",
                (getattr(self._vehicle, "_task_info", None) or {}).get("status", "")
                if isinstance(getattr(self._vehicle, "_task_info", None) or {}, dict)
                else "",
            ),
        ):
            goal_pose = self._status_goal_pose(status)
            if goal_pose is not None:
                if not self._pose_in_reach_zone(
                    goal_pose[0], goal_pose[1], target_x, target_y, deviation_xy
                ):
                    references.append(
                        ErrorReference(key, f"{goal_pose[0]:.1f},{goal_pose[1]:.1f}")
                    )
                    mismatch_labels.append(key)
                continue

            goal_hint = self._status_goal_hint(status)
            if goal_hint and goal_hint != node_id:
                references.append(ErrorReference(key, goal_hint))
                mismatch_labels.append(key)

        path = getattr(self._vehicle, "_path", None) or {}
        path_points = path.get("points", []) if isinstance(path, dict) else []
        if isinstance(path_points, list) and path_points:
            path_end = path_points[-1]
            if isinstance(path_end, dict):
                px = self._optional_float(path_end.get("x"))
                py = self._optional_float(path_end.get("y"))
                if px is not None and py is not None:
                    path_end_distance = math.hypot(px - target_x, py - target_y)
                    if path_end_distance > deviation_xy:
                        references.extend(
                            [
                                ErrorReference("pathEnd", f"{px:.1f},{py:.1f}"),
                                ErrorReference("pathEndDistance", f"{path_end_distance:.1f}"),
                            ]
                        )
                        mismatch_labels.append("pathEnd")

        return references, mismatch_labels

    def _set_jibot_arrival_signal_mismatch_warning(
        self,
        node: Any,
        target_x: float,
        target_y: float,
        vx: float,
        vy: float,
        deviation_xy: float,
    ) -> None:
        if self.state is None:
            return

        self._clear_jibot_arrival_signal_mismatch_warning(publish=False)
        references, mismatch_labels = self._collect_jibot_arrival_signal_mismatches(
            node,
            target_x,
            target_y,
            deviation_xy,
        )
        if not mismatch_labels:
            return

        references.extend(
            [
                ErrorReference("target", f"{target_x:.1f},{target_y:.1f}"),
                ErrorReference("position", f"{vx:.1f},{vy:.1f}"),
                ErrorReference("reachRadius", f"{deviation_xy:.1f}"),
                ErrorReference("jibotStatus", str(getattr(self._vehicle, "_status", "") or "")),
            ]
        )
        self.state.errors.append(
            Error(
                error_type=ErrorType.JIBOT_ARRIVAL_SIGNAL_MISMATCH,
                error_level=ErrorLevel.WARNING,
                error_references=references,
                error_description=(
                    "JIBOT pose reached the order node, but task/path diagnostics "
                    f"did not match: {', '.join(mismatch_labels)}"
                ),
            )
        )
        self.request_state_publish("arrival signal mismatch")

    def _clear_jibot_arrival_signal_mismatch_warning(
        self,
        publish: bool = True,
    ) -> None:
        if self.state is None:
            return

        before = len(self.state.errors)
        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None)
            != ErrorType.JIBOT_ARRIVAL_SIGNAL_MISMATCH
        ]
        if publish and len(self.state.errors) != before:
            self.request_state_publish("arrival signal mismatch cleared")

    def _set_jibot_dock_work_error(self, node: Any, reason: str) -> None:
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if not self._is_jibot_dock_work_error(error)
        ]
        self.state.errors.append(
            Error(
                error_type=ErrorType.UNKNOWN_ERROR,
                error_level=ErrorLevel.WARNING,
                error_references=[
                    ErrorReference("jibotDockWork", "true"),
                    ErrorReference("nodeId", str(getattr(node, "node_id", "") or "")),
                    ErrorReference("sequenceId", str(getattr(node, "sequence_id", "") or "")),
                    ErrorReference("jibotStatus", str(getattr(self._vehicle, "_status", "") or "")),
                    ErrorReference("jibotReason", reason),
                ],
                error_description="JIBOT dock-work charging stop failed",
            )
        )
        self.request_state_publish("dock-work stop charging failed")

    def _record_action_not_found(self, action: Any, *, scope: str) -> None:
        """Keep an unsupported action visible in ``state.errors``.

        Terminal instant-action states are removed after completion, so a
        FAILED status alone can disappear before the next state publication.
        The error remains until the normal clearErrors flow removes it.
        """
        if self.state is None:
            return
        action_id = str(getattr(action, "action_id", "") or "")
        action_type = str(getattr(action, "action_type", "") or "")
        self.state.errors.append(
            Error(
                error_type=ErrorType.ACTION_NOT_FOUND,
                error_level=ErrorLevel.WARNING,
                error_references=[
                    ErrorReference("actionId", action_id),
                    ErrorReference("actionType", action_type),
                    ErrorReference("scope", scope),
                ],
                error_description=(
                    f"Unsupported {scope} action type: {action_type}"
                ),
            )
        )
        self.request_state_publish("action not found")

    def _record_invalid_instant_action(self, action: Any, description: str) -> None:
        if self.state is None:
            return
        self.state.errors.append(
            Error(
                error_type=ErrorType.INVALID_INSTANT_ACTION,
                error_level=ErrorLevel.WARNING,
                error_references=[
                    ErrorReference("actionId", str(action.action_id)),
                    ErrorReference("actionType", str(action.action_type)),
                    ErrorReference(
                        "blockingType",
                        str(getattr(action.blocking_type, "value", action.blocking_type)),
                    ),
                ],
                error_description=description,
            )
        )
        self.request_state_publish("invalid instant action")

    def _is_jibot_dock_work_error(self, error: Any) -> bool:
        if getattr(error, "error_type", None) != ErrorType.UNKNOWN_ERROR:
            return False
        if getattr(error, "error_description", None) == "JIBOT dock-work charging stop failed":
            return True
        for ref in getattr(error, "error_references", []) or []:
            if (
                getattr(ref, "reference_key", None) == "jibotDockWork"
                and getattr(ref, "reference_value", None) == "true"
            ):
                return True
        return False

    def _set_jibot_goto_rejected_error(self, node: Any, reason: str) -> None:
        """Surface a rejected/unacknowledged node goto as a FATAL error.

        Mirrors _set_jibot_node_unreached_error: the prior same-type error is
        filtered out before appending, so repeated rejections do not stack.
        """
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.JIBOT_GOTO_REJECTED
        ]
        self.state.errors.append(
            Error(
                error_type=ErrorType.JIBOT_GOTO_REJECTED,
                error_level=ErrorLevel.FATAL,
                error_references=[
                    ErrorReference("nodeId", str(getattr(node, "node_id", "") or "")),
                    ErrorReference("sequenceId", str(getattr(node, "sequence_id", "") or "")),
                    ErrorReference("command", "UmGoto"),
                    ErrorReference("reason", reason),
                    ErrorReference("jibotMode", str(getattr(self._vehicle, "_mode", "") or "")),
                    ErrorReference("jibotStatus", str(getattr(self._vehicle, "_status", "") or "")),
                ],
                error_description=(
                    "JIBOT rejected or did not acknowledge the order node goto; "
                    "the order is held until the fleet manager re-sends it."
                ),
            )
        )
        self.request_state_publish("goto rejected")

    def _set_jibot_dock_fail_error(self, node: Any, reason: str) -> None:
        """Surface a failed approach-dock (UmDock did not start charging) as FATAL.

        FATAL so the fleet manager holds and re-sends the order, retrying the
        approach->dock. De-duped by type like the other JIBOT error setters.
        """
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.JIBOT_DOCK_FAILED
        ]
        self.state.errors.append(
            Error(
                error_type=ErrorType.JIBOT_DOCK_FAILED,
                error_level=ErrorLevel.FATAL,
                error_references=[
                    ErrorReference("nodeId", str(getattr(node, "node_id", "") or "")),
                    ErrorReference("sequenceId", str(getattr(node, "sequence_id", "") or "")),
                    ErrorReference("command", "UmDock"),
                    ErrorReference("reason", reason),
                    ErrorReference("jibotMode", str(getattr(self._vehicle, "_mode", "") or "")),
                    ErrorReference("jibotStatus", str(getattr(self._vehicle, "_status", "") or "")),
                ],
                error_description=(
                    "JIBOT did not start charging after the approach UmDock; "
                    "the order is held until the fleet manager re-sends it. "
                    "If charging starts after this timeout, increase "
                    "[dock].fail_timeout_sec in config.toml or the matching "
                    "motion_rules fail_timeout_sec override."
                ),
            )
        )
        self.request_state_publish("dock failed")

    def _clear_jibot_goto_rejected_error(self) -> None:
        """Drop a stale JIBOT_GOTO_REJECTED error once a goto is accepted."""
        if self.state is None:
            return

        before = len(self.state.errors)
        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.JIBOT_GOTO_REJECTED
        ]
        if len(self.state.errors) != before:
            self.request_state_publish("goto rejected cleared")

    def _build_v3_state_message(self) -> V3State:
        if self.state is None:
            raise RuntimeError("State is not initialized")

        agv_position = self.state.agv_position
        mobile_robot_position = None
        if agv_position is not None:
            # Pose is published in the robot's raw units (mm), unconverted, so it
            # stays consistent with the map node coordinates used for lastNodeId
            # matching. The unit is advertised via the factsheet coordinateUnits
            # entry rather than scaling here.
            mobile_robot_position = MobileRobotPosition(
                x=agv_position.x,
                y=agv_position.y,
                theta=agv_position.theta,
                map_id=agv_position.map_id,
                localized=agv_position.position_initialized,
                localization_score=agv_position.localization_score,
                deviation_range=agv_position.deviation_range,
            )

        return V3State(
            header=Header(
                header_id=self.state.header_id,
                timestamp=self.state.timestamp,
                version=self.state.version,
                manufacturer=self.state.manufacturer,
                serial_number=self.state.serial_number,
            ),
            order_id=self.state.order_id,
            order_update_id=self.state.order_update_id,
            last_node_id=self.state.last_node_id,
            last_node_sequence_id=self.state.last_node_sequence_id,
            node_states=[
                self._build_v3_node_state_dict(node)
                for node in self.state.node_states
            ],
            edge_states=[
                self._build_v3_edge_state_dict(edge)
                for edge in self.state.edge_states
            ],
            driving=self.state.driving,
            action_states=[
                self._build_v3_action_state(action_state)
                for action_state in self.state.action_states
            ],
            instant_action_states=[
                self._build_v3_action_state(action_state)
                for action_state in self.state.instant_action_states
            ],
            power_supply=PowerSupply(
                state_of_charge=self.state.battery_state.battery_charge,
                charging=self.state.battery_state.charging,
                battery_voltage=self.state.battery_state.battery_voltage,
                battery_current=self._get_vehicle_battery_current(),
                battery_health=self.state.battery_state.battery_health,
                range=self.state.battery_state.reach,
            ),
            operating_mode=self._build_v3_operating_mode(self.state.operating_mode),
            errors=[error.to_dict() for error in self.state.errors],
            information=[info.to_dict() for info in self.state.information],
            loads=self.state.loads,
            safety_state=V3SafetyState(
                active_emergency_stop=self._build_v3_emergency_stop(
                    self.state.safety_state.e_stop
                ),
                field_violation=self.state.safety_state.field_violation,
            ),
            mobile_robot_position=mobile_robot_position,
            paused=self.state.paused,
            new_base_request=self.state.new_base_request,
            distance_since_last_node=self.state.distance_since_last_node,
        )

    def _build_v3_node_state_dict(self, node: Any) -> Dict[str, Any]:
        """Spec NodeState subset for the published state.

        Internal bookkeeping keeps the full order Node objects (the worker
        needs their actions), but the published nodeState must only carry
        nodeId/sequenceId/released plus optional descriptor/position.
        """
        result: Dict[str, Any] = {
            "nodeId": getattr(node, "node_id", ""),
            "sequenceId": getattr(node, "sequence_id", 0),
            "released": bool(getattr(node, "released", False)),
        }
        descriptor = (
            getattr(node, "node_descriptor", None)
            or getattr(node, "node_description", None)
        )
        if descriptor is not None:
            result["nodeDescriptor"] = descriptor
        position = getattr(node, "node_position", None)
        if position is not None:
            result["nodePosition"] = (
                position.to_dict() if hasattr(position, "to_dict") else position
            )
        return result

    def _build_v3_edge_state_dict(self, edge: Any) -> Dict[str, Any]:
        """Spec EdgeState subset for the published state."""
        result: Dict[str, Any] = {
            "edgeId": getattr(edge, "edge_id", ""),
            "sequenceId": getattr(edge, "sequence_id", 0),
            "released": bool(getattr(edge, "released", False)),
        }
        descriptor = (
            getattr(edge, "edge_descriptor", None)
            or getattr(edge, "edge_description", None)
        )
        if descriptor is not None:
            result["edgeDescriptor"] = descriptor
        trajectory = getattr(edge, "trajectory", None)
        if trajectory is not None:
            result["trajectory"] = (
                trajectory.to_dict() if hasattr(trajectory, "to_dict") else trajectory
            )
        return result

    def _build_v3_action_state(self, action_state: ActionState) -> V3ActionState:
        return V3ActionState(
            action_id=action_state.action_id,
            action_status=V3ActionStatus(action_state.action_status.value),
            action_type=action_state.action_type,
            action_descriptor=action_state.action_description,
            action_result=action_state.result_description,
        )

    def _build_v3_operating_mode(self, operating_mode: OperatingMode) -> V3OperatingMode:
        if operating_mode == OperatingMode.TEACHIN:
            return V3OperatingMode.TEACH_IN
        return V3OperatingMode(operating_mode.value)

    def _build_v3_emergency_stop(self, e_stop: EStop) -> EmergencyStop:
        if e_stop == EStop.AUTOACK:
            return EmergencyStop.MANUAL
        if e_stop == EStop.MANUAL:
            return EmergencyStop.MANUAL
        if e_stop == EStop.REMOTE:
            return EmergencyStop.REMOTE
        return EmergencyStop.NONE

    # -------------------------
    # PUBLISH CONNECTION
    # -------------------------
    def _publish_connection_state(
        self,
        connection_state: ConnectionState,
        topic_name: str = "connection",
        retain_msg: bool = True,
    ) -> None:
        """Publish one connection message. Sync so the MQTT connect callback
        can reuse it without an event loop."""
        self.header_id += 1
        self.connection = Connection(
                header=Header(
                    header_id=self.header_id,
                    timestamp=utils.get_timestamp(),
                    version=self.config.vehicle.vda_full_version,
                    manufacturer=self.config.vehicle.manufacturer,
                    serial_number=self.config.vehicle.serial_number,
                ),
                connection_state=connection_state
        )

        # Remember a deliberate OFFLINE so a reconnect racing shutdown does not
        # flip the retained message back to ONLINE. Only OFFLINE latches:
        # CONNECTION_BROKEN/HIBERNATING describe a live session and should still
        # be superseded by ONLINE when the link comes back.
        self._connection_offline_intent = connection_state == ConnectionState.OFFLINE

        self._vda3.publish(topic_name, self.connection, qos=1, retain=retain_msg)
        print(
            f"[VDA5050 CONNECTION PUBLISHED] "
            f"topic={topic_name} state={connection_state.value} qos=1 retain={retain_msg}"
        )

    async def publish_connection(self, topic_name: str, interval_sec: int = 1, retain_msg: bool = True, connection_state: ConnectionState = ConnectionState.CONNECTION_BROKEN):
        self._publish_connection_state(
            connection_state, topic_name=topic_name, retain_msg=retain_msg
        )

    def configure_connection_last_will(self, topic_name: str, connection_state: ConnectionState = ConnectionState.OFFLINE) -> None:
        self.header_id += 1
        self.connection = Connection(
                header=Header(
                    header_id=self.header_id,
                    timestamp=utils.get_timestamp(),
                    version=self.config.vehicle.vda_full_version,
                    manufacturer=self.config.vehicle.manufacturer,
                    serial_number=self.config.vehicle.serial_number,
                ),
                connection_state=connection_state
        )

        self._vda3.set_connection_last_will(self.connection, qos=1, retain=True)

    # -------------------------
    # PUBLISH FACTSHEET
    # -------------------------
    # Instant actions this adapter executes. stopCharging/logReport are
    # intentionally absent: the JIBOT API has no matching command, so they are
    # rejected as FAILED and must not be advertised here.
    # Single source lives in core.factsheet; alias kept so existing usages are unchanged.
    SUPPORTED_INSTANT_ACTIONS = INSTANT_ACTION_TYPES

    # Synthetic instant-action used to surface the docking maneuver as a RUNNING
    # action (status only; NOT an FMS-received instant action, so it is not in
    # SUPPORTED_INSTANT_ACTIONS). The eq maps actionType "dock" -> DOCKING.
    DOCKING_STATUS_ACTION_ID = "__jibot_docking__"
    DOCKING_STATUS_ACTION_TYPE = "dock"
    _SOUND_TEST_SECONDS = 10.0

    # JIBOT reports a pose goto as "nrunto pose (16399 -1897 0)"; the first two
    # numbers in the parentheses are the target x/y in mm.
    _POSE_GOAL_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)")

    def publish_factsheet(self) -> None:
        """Publish the vehicle factsheet (retained) so ACS can discover
        capabilities and the supported action set."""
        self.factsheet_header_id += 1
        self._vda3.publish("factsheet", self._build_factsheet(), qos=1, retain=True)
        print("[VDA5050 FACTSHEET PUBLISHED] topic=factsheet qos=1 retain=True")

    def _build_factsheet(self) -> Dict[str, Any]:
        tray = self.config.ezi_config.tray_slot_pin
        load_positions = [f"slot{i}" for i in range(1, len(tray) + 1)]
        video_streams = None
        if self.config.video.enabled and self.config.video.stream_topics:
            video_streams = self._video.stream_urls(list(self.config.video.stream_topics))
        return build_factsheet(
            self.config, header_id=self.factsheet_header_id,
            timestamp=utils.get_timestamp(), simulation=self._is_simulator(),
            load_positions=load_positions, video_streams=video_streams,
            instant_action_types=merge_instant_action_types(
                self._action_registry.action_types()
            ),
        )

    # -------------------------
    # SUBSCRIBE ORDER & INSTANT ACTIONS
    # -------------------------
    async def subscribe_acs_cmd(self, interval_sec: int = 1):
        print("subscribe ACS Command is ready!")
        
        self._vda3.subscribe_order(self.handle_incoming_acs_cmd, qos=0)
        self._vda3.subscribe_instant_actions(self.handle_incoming_acs_cmd, qos=0)

        while True:
            await asyncio.sleep(interval_sec)


    # -------------------------
    # HANDLE INCOMING ORDER & INSTANT ACTIONS
    # -------------------------   
    def handle_incoming_acs_cmd(self, topic: str, payload: Any) -> None:
        # check JSON payload valid
        data: Optional[Dict[str, Any]] = None
        if isinstance(payload, (Order, InstantActions)):
            data = None
        else:
            try:
                raw_payload = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
                data = json.loads(raw_payload)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.order_reject(
                    True,
                    ErrorLevel.WARNING,
                    ErrorType.ORDER_JSON_PAYLOAD_INVALID,
                    "Failed to decode or parse JSON payload",
                    error_hint="Check if the payload is a valid JSON string and properly encoded in UTF-8",
                )
                return
            if isinstance(data, dict) and "serialNumber" not in data:
                data["serialNumber"] = ""

        if topic.endswith("/order"):
            # Convert payload dict to Order instance before processing
            try:
                order_obj = payload if isinstance(payload, Order) else Order.from_dict(data)
            except Exception as exc:
                # print(f"Failed to parse order payload: {exc}")
                self.order_reject(True, ErrorLevel.WARNING, ErrorType.UNKNOWN_ERROR, f"Failed to parse order payload: {exc}", error_hint="Check if the order payload structure matches the expected format")
                return
            if not self._is_command_for_this_robot(topic, order_obj.serial_number):
                return
            self._print_v3_order_preview(topic, order_obj)
            self._call_on_adapter_loop(lambda: self._handle_v3_order(order_obj))
            return
            

        elif topic.endswith("/instantActions"):
            # Convert payload dict to InstantActions instance before processing
            try:
                instant_actions_obj = payload if isinstance(payload, InstantActions) else InstantActions.from_dict(data)
            except Exception as exc:
                # print(f"Failed to parse instant actions payload: {exc}")
                self.order_reject(False, ErrorLevel.WARNING, ErrorType.UNKNOWN_ERROR, f"Failed to parse instant actions payload: {exc}", error_hint="Check if the instant actions payload structure matches the expected format")
                return
            if not self._is_command_for_this_robot(
                topic,
                instant_actions_obj.header.serial_number,
            ):
                return
            # Validate instant actions payload
            self.instant_actions_accept_procedure(instant_actions_obj)
        
        
        else:
            pass
            # print(f"Received ACS command from unsupported topic '{topic}'")
            return

    def _is_command_for_this_robot(self, topic: str, payload_serial_number: str) -> bool:
        """수신 명령의 MQTT 토픽과 payload serial이 현재 AMR과 같은지 검증함."""
        expected_serial = self.config.vehicle.serial_number
        topic_parts = topic.split("/")
        topic_serial = topic_parts[-2] if len(topic_parts) >= 2 else ""
        payload_serial = str(payload_serial_number or "").strip()
        if topic_serial == expected_serial and (
            not payload_serial or payload_serial == expected_serial
        ):
            return True

        print(
            "[VDA5050 COMMAND REJECT] "
            f"expectedSerial={expected_serial} topic={topic} "
            f"topicSerial={topic_serial} payloadSerial={payload_serial}"
        )
        return False

    def _print_v3_order_preview(self, topic: str, order_request: Order) -> None:
        print("=" * 80)
        print("[VDA5050 v3.0 ORDER RECEIVED]")
        print(f"topic={topic}")
        print(
            f"headerId={order_request.header_id} "
            f"version={order_request.version} "
            f"manufacturer={order_request.manufacturer} "
            f"serialNumber={order_request.serial_number}"
        )
        print(
            f"orderId={order_request.order_id} "
            f"orderUpdateId={order_request.order_update_id} "
            f"nodes={len(order_request.nodes)} "
            f"edges={len(order_request.edges)}"
        )

        for node in order_request.nodes:
            pos = node.node_position
            if pos is None:
                position_text = "position=None"
            else:
                position_text = (
                    f"position=(x={pos.x}, y={pos.y}, theta={pos.theta}, "
                    f"mapId={pos.map_id}, allowedDeviationXY={pos.allowed_deviation_xy}, "
                    f"allowedDeviationTheta={pos.allowed_deviation_theta})"
                )
            print(
                f"  node seq={node.sequence_id} id={node.node_id} "
                f"released={node.released} actions={len(node.actions)} {position_text}"
            )
            for action in node.actions:
                print(
                    f"    action id={action.action_id} "
                    f"type={action.action_type} "
                    f"blockingType={action.blocking_type.value} "
                    f"params={[param.to_dict() for param in action.action_parameters]}"
                )

        for edge in order_request.edges:
            print(
                f"  edge seq={edge.sequence_id} id={edge.edge_id} "
                f"released={edge.released} start={edge.start_node_id} "
                f"end={edge.end_node_id} maximumSpeed={edge.maximum_speed} "
                f"actions={len(edge.actions)}"
            )
            for action in edge.actions:
                print(
                    f"    action id={action.action_id} "
                    f"type={action.action_type} "
                    f"blockingType={action.blocking_type.value} "
                    f"params={[param.to_dict() for param in action.action_parameters]}"
                )

        print("[ORDER QUEUE] order will be processed by sequenceId.")
        print("=" * 80)

    def _handle_v3_order(self, order_request: Order) -> None:
        if self.state is None:
            self.order_reject(
                True,
                ErrorLevel.WARNING,
                ErrorType.UNKNOWN_ERROR,
                "State is not initialized; order cannot be accepted yet",
            )
            print(
                f"[ORDER REJECTED] orderId={order_request.order_id} "
                "state is not initialized"
            )
            return

        if self._work_in_progress is not None:
            self.order_reject(
                True,
                ErrorLevel.WARNING,
                ErrorType.ORDER_CURRENT_NOT_FINISHED,
                f"Robot is busy with {self._work_in_progress} work; "
                "cannot accept order",
                error_hint="Send stopLoading/stopUnloading to finish the work first",
            )
            print(
                f"[ORDER REJECTED] orderId={order_request.order_id} "
                f"reason=busy:{self._work_in_progress}"
            )
            return

        if not order_request.nodes:
            self.order_reject(
                self.state.order_id in ("", order_request.order_id),
                ErrorLevel.WARNING,
                ErrorType.ORDER_START_NODE_INVALID,
                "Order must contain at least one node",
            )
            print(f"[ORDER REJECTED] orderId={order_request.order_id} nodes=0")
            return

        # JIBOT TCP 가 끊긴 상태에서 오더를 받으면 첫 모션 명령에서 writer 가 None 이라
        # 워커가 AttributeError 로 죽고, 오더는 active 인 채 좀비로 남는다
        # (2026-08-25 06:58:59 HN-SH6-TR-002: 리부팅 직후 7273 미개방 상태에서 p39 오더를
        # 수락 → "'NoneType' object has no attribute 'write'" → 뒤이은 4노드 오더가
        # ORDER_CURRENT_NOT_FINISHED 로 거절). 링크가 없으면 받지 않고 FMS 에 되돌린다.
        # 형식 검증(nodes=0) 뒤에 두는 이유: 잘못된 페이로드는 링크 상태와 무관하게
        # 원래 사유로 거절돼야 한다.
        # errorType 은 형제 가드("State is not initialized")와 같은 UNKNOWN_ERROR 를 쓴다.
        # JIBOT_CONNECTION_LOST 를 쓰면 _refresh_jibot_connection_errors 가 매 주기
        # 그 타입을 통째로 걷어내고 자기 것만 다시 넣어서 거절 사유가 지워진다.
        # 링크 끊김 자체는 그 FATAL 오류로 이미 FMS 에 계속 보고되고 있다.
        if not self._is_jibot_connected():
            self.order_reject(
                True,
                ErrorLevel.WARNING,
                ErrorType.UNKNOWN_ERROR,
                "JIBOT link is down; order cannot be accepted",
                error_hint="Re-send the order once the JIBOT link is restored",
            )
            print(
                f"[ORDER REJECTED] orderId={order_request.order_id} "
                "reason=vehicle link down"
            )
            return

        if not self.state.order_id or self._is_v3_order_finished():
            self._accept_v3_order_for_queue(order_request)
            return

        if order_request.order_id != self.state.order_id:
            self.order_reject(
                True,
                ErrorLevel.WARNING,
                ErrorType.ORDER_CURRENT_NOT_FINISHED,
                "Current order is still in progress",
                error_hint="Cancel the current order before sending a different orderId",
            )
            print(
                f"[ORDER REJECTED] orderId={order_request.order_id} "
                f"activeOrderId={self.state.order_id} reason=current order in progress"
            )
            return

        self._update_v3_order_for_queue(order_request)

    def _accept_v3_order_for_queue(self, order_request: Order) -> None:
        self.order = order_request
        # 새 주문은 자기 actionId 를 들고 온다. 이전 주문의 기록이 그걸
        # 막아서는 안 된다.
        self._dispatched_order_action_ids.clear()
        if self.order_worker_task is not None and not self.order_worker_task.done():
            self.order_worker_task.cancel()
        self._clear_order_queue()

        if self.state is not None:
            self.state.order_id = order_request.order_id
            self.state.order_update_id = order_request.order_update_id
            self.state.node_states = list(order_request.nodes)
            self.state.edge_states = list(order_request.edges)
            self.state.action_states = self.build_action_states(order_request)
            self.state.instant_action_states = []
            self.state.errors = []
            self.state.information = []
            self.state.last_node_id = self._last_node_id
            self.state.last_node_sequence_id = self._last_node_sequence_id
            self._remap_last_node_sequence_for_new_order(order_request)
            self._bootstrap_v3_order_from_vehicle_station(order_request)
            self._bootstrap_v3_order_from_current_pose(order_request)
            self._prune_v3_order_state_before_last_node()

        self._rebuild_v3_order_queue_from_state()

        print(
            f"[ORDER QUEUED] orderId={order_request.order_id} "
            f"steps={self.order_queue.qsize()}"
        )
        self.request_state_publish("order accepted")
        self._schedule_v3_order_worker()

    def _update_v3_order_for_queue(self, order_request: Order) -> None:
        if self.state is None:
            return

        if order_request.order_update_id <= self.state.order_update_id:
            self.order_reject(
                False,
                ErrorLevel.WARNING,
                ErrorType.ORDER_UPDATE_ID_INVALID,
                "Order update id must increase",
                error_hint=(
                    f"Current={self.state.order_update_id}, "
                    f"received={order_request.order_update_id}"
                ),
            )
            print(
                f"[ORDER UPDATE REJECTED] orderId={order_request.order_id} "
                f"currentUpdateId={self.state.order_update_id} "
                f"receivedUpdateId={order_request.order_update_id}"
            )
            return

        self.order = order_request
        self.state.order_update_id = order_request.order_update_id
        changed = self._merge_v3_order_state(order_request)
        self._prune_v3_order_state_before_last_node()
        self._rebuild_v3_order_queue_from_state()
        self._merge_v3_action_states(order_request)

        print(
            f"[ORDER UPDATE ACCEPTED] orderId={order_request.order_id} "
            f"orderUpdateId={order_request.order_update_id} "
            f"changed={changed} queued={self.order_queue.qsize()}"
        )
        self.request_state_publish("order update accepted")
        self._schedule_v3_order_worker()

    def _merge_v3_order_state(self, order_request: Order) -> int:
        if self.state is None:
            return 0

        changed = 0
        changed += self._merge_v3_order_items(
            self.state.node_states,
            order_request.nodes,
            "node",
        )
        changed += self._merge_v3_order_items(
            self.state.edge_states,
            order_request.edges,
            "edge",
        )
        self.state.node_states.sort(key=lambda node: node.sequence_id)
        self.state.edge_states.sort(key=lambda edge: edge.sequence_id)
        return changed

    def _bootstrap_v3_order_from_vehicle_station(self, order_request: Order) -> None:
        if self.state is None or self._vehicle is None:
            return

        mode = self._last_node_capture_mode()
        if mode in {"disabled", "proximity"}:
            return

        station = str(getattr(self._vehicle, "_station", "") or "").strip()
        if not station:
            return

        matching_nodes = [
            node for node in order_request.nodes
            if getattr(node, "node_id", None) == station
        ]
        if not matching_nodes:
            return

        station_node = max(matching_nodes, key=lambda node: node.sequence_id)

        if mode == "settled":
            # Pose guard: _station can be set before the robot physically reaches
            # the node coords (see docs/reference/jibot-arrival-and-last-node.md),
            # which makes lastNodeId jump to an unreached node at order accept
            # (Bug #2). Only trust the station when the current pose agrees.
            gap = self._distance_to_node_id(
                station, getattr(self.state, "agv_position", None)
            )
            if gap is None or gap > self._idle_last_node_reach_xy():
                gap_str = "n/a" if gap is None else f"{gap:.1f}mm"
                print(
                    f"[ORDER BOOTSTRAP] station={station} rejected "
                    f"(pose gap={gap_str} > {self._idle_last_node_reach_xy():.1f}mm)"
                )
                return

        self._set_last_node(station_node.node_id, station_node.sequence_id)
        print(
            f"[ORDER BOOTSTRAP] station={station} "
            f"lastNodeId={self._last_node_id} "
            f"lastNodeSequenceId={self._last_node_sequence_id}"
        )

    def _bootstrap_v3_order_from_current_pose(self, order_request: Order) -> None:
        if self.state is None:
            return
        if self._last_node_capture_mode() in {"disabled", "proximity"}:
            return

        position = getattr(self.state, "agv_position", None)
        if position is None:
            return
        try:
            x = float(getattr(position, "x"))
            y = float(getattr(position, "y"))
        except (AttributeError, TypeError, ValueError):
            return

        best: Optional[Tuple[float, Any]] = None
        for node in order_request.nodes:
            xy = self._node_xy(node)
            if xy is None:
                continue
            distance = math.hypot(x - xy[0], y - xy[1])
            if best is None or distance < best[0]:
                best = (distance, node)

        if best is None:
            return

        distance, node = best
        if distance > self._idle_last_node_reach_xy():
            return

        # Bootstrap exists to skip the already-traversed prefix when a re-issued
        # order finds the robot mid-route. The order's start node has no prefix
        # to skip, so never bootstrap-prune it: an order whose first node sits
        # at the robot's current pose must still drive/process that node (and a
        # one-node order must not silently empty on accept). Resume (robot at a
        # later node) is unaffected.
        start_sequence_id = min(n.sequence_id for n in order_request.nodes)
        if node.sequence_id == start_sequence_id:
            return

        self._set_last_node(node.node_id, node.sequence_id)
        print(
            f"[ORDER BOOTSTRAP] pose=({x:.1f},{y:.1f}) "
            f"lastNodeId={self._last_node_id} "
            f"lastNodeSequenceId={self._last_node_sequence_id} "
            f"gap={distance:.1f}mm"
        )

    def _remap_last_node_sequence_for_new_order(self, order_request: Order) -> None:
        if self.state is None or not self._last_node_id:
            return

        matching_nodes = [
            node for node in order_request.nodes
            if getattr(node, "node_id", None) == self._last_node_id
        ]
        if not matching_nodes:
            return

        order_node = min(matching_nodes, key=lambda node: node.sequence_id)
        self._set_last_node(self._last_node_id, order_node.sequence_id)

    def _prune_v3_order_state_before_last_node(self) -> None:
        if self.state is None or not self.state.last_node_id:
            return

        last_sequence_id = int(self.state.last_node_sequence_id or 0)
        last_node_is_in_order = any(
            node.node_id == self.state.last_node_id
            and node.sequence_id == last_sequence_id
            for node in self.state.node_states
        )
        if not last_node_is_in_order:
            return

        self.state.node_states = [
            node for node in self.state.node_states
            if node.sequence_id > last_sequence_id
            or self._is_start_node_with_pending_actions(node)
        ]
        self.state.edge_states = [
            edge for edge in self.state.edge_states
            if edge.sequence_id > last_sequence_id
        ]

    def _is_start_node_with_pending_actions(self, node: Any) -> bool:
        """True for the already-reached start node when it still owes actions.

        The traversed-prefix prune drops nodes the robot has passed, but the
        start node of a new order is where the robot stands *now*, and its
        actions have not run yet. Dropping it silently discarded them — this is
        how a departure order's BEFORE_LEAVE_NODE clamp never fired. The node is
        kept so the queue can run its actions without any motion.
        """
        if self.state is None or not self.state.last_node_id:
            return False
        if (
            getattr(node, "sequence_id", None) != int(self.state.last_node_sequence_id or 0)
            or getattr(node, "node_id", None) != self.state.last_node_id
        ):
            return False
        return any(
            self._order_action_is_pending(action)
            for action in getattr(node, "actions", None) or []
        )

    def _order_action_is_pending(self, action: Any) -> bool:
        """True while an order action has not been dispatched yet.

        `bool(node.actions)` 로는 답할 수 없는 질문이다. 방금 도착한 노드는
        액션이 도는 동안에도 actions 목록을 그대로 들고 있고, 스텝이 clear 될
        때까지 node_states 에 남는다. 그래서 그 창에 orderUpdate 가 들어오면
        노드가 큐에 다시 들어가 이미 실행 중이던 액션이 재실행됐다
        (현장: p36 에서 pioElevatorOpen 이 두 번 발사).

        dispatch 만이 주문 액션을 WAITING 에서 빼내므로(build_action_states 는
        전부 WAITING 으로 시작), WAITING 이거나 — 이번 업데이트가 새로 들고 온
        액션이라 아직 상태가 없거나 — 인 경우가 정확히 "아직 안 한 일"이다.
        """
        action_id = str(getattr(action, "action_id", "") or "")
        if action_id in self._dispatched_order_action_ids:
            return False
        if self.state is None:
            return True
        for action_state in self.state.action_states:
            if action_state.action_id == action_id:
                return action_state.action_status == ActionStatus.WAITING
        return True

    def _merge_v3_order_items(
        self,
        current_items: List[Any],
        requested_items: List[Any],
        kind: str,
    ) -> int:
        current_by_sequence = {
            item.sequence_id: item
            for item in current_items
        }
        changed = 0

        for requested in requested_items:
            existing = current_by_sequence.get(requested.sequence_id)
            item_id = getattr(requested, "node_id", None) or getattr(requested, "edge_id", "")
            if existing is None:
                if (
                    self.state is not None
                    and requested.sequence_id <= self.state.last_node_sequence_id
                ):
                    print(
                        f"[ORDER UPDATE SKIP] kind={kind} "
                        f"seq={requested.sequence_id} id={item_id} "
                        f"lastNodeSequenceId={self.state.last_node_sequence_id}"
                    )
                    continue

                current_items.append(requested)
                current_by_sequence[requested.sequence_id] = requested
                changed += 1
                print(
                    f"[ORDER UPDATE APPEND] kind={kind} "
                    f"seq={requested.sequence_id} id={item_id} released={requested.released}"
                )
                continue

            current_id = getattr(existing, "node_id", None) or getattr(existing, "edge_id", "")
            if current_id != item_id:
                print(
                    f"[ORDER UPDATE IGNORE] kind={kind} seq={requested.sequence_id} "
                    f"existingId={current_id} requestedId={item_id}"
                )
                continue

            if existing.released != requested.released:
                print(
                    f"[ORDER UPDATE RELEASE] kind={kind} seq={requested.sequence_id} "
                    f"id={item_id} released={existing.released}->{requested.released}"
                )
                existing.released = requested.released
                changed += 1

            existing.actions = requested.actions

        return changed

    def _rebuild_v3_order_queue_from_state(self) -> None:
        if self.state is None:
            return

        # Only skip the already-traversed prefix when last_node actually belongs
        # to THIS order. A last_node_sequence_id carried over from a previous,
        # finished order (robot parked) must not skip a new order's nodes: a
        # single-node order (e.g. an FMS charge order) whose only node sits at
        # sequenceId 0 would otherwise be dropped to 0 steps, completing without
        # ever moving. Mirrors the guard in _prune_v3_order_state_before_last_node.
        last_sequence_id = int(self.state.last_node_sequence_id or 0)
        last_node_in_order = any(
            node.node_id == self.state.last_node_id
            and node.sequence_id == last_sequence_id
            for node in self.state.node_states
        )

        self._clear_order_queue(clear_current_step=self.current_order_step is None)
        for step in self._build_v3_order_steps_from_state():
            if (
                self.state.last_node_id
                and last_node_in_order
                and step.sequence_id <= last_sequence_id
            ):
                if (
                    step.kind == "node"
                    and self._is_start_node_with_pending_actions(step.item)
                    and getattr(step.item, "released", False)
                ):
                    # Already standing here: run the node's actions, skip motion.
                    step.actions_only = True
                    self.order_queue.put_nowait(step)
                continue
            if not getattr(step.item, "released", False):
                break
            self.order_queue.put_nowait(step)

        print(
            f"[ORDER QUEUE REBUILT] queued={self.order_queue.qsize()} "
            f"remainingNodes={len(self.state.node_states)} "
            f"remainingEdges={len(self.state.edge_states)}"
        )

    def _build_v3_order_steps_from_state(self) -> List[OrderStep]:
        if self.state is None:
            return []

        steps: List[OrderStep] = []
        for node in self.state.node_states:
            steps.append(OrderStep("node", node.sequence_id, node))
        for edge in self.state.edge_states:
            steps.append(OrderStep("edge", edge.sequence_id, edge))
        return sorted(steps, key=lambda step: step.sequence_id)

    def _merge_v3_action_states(self, order_request: Order) -> None:
        if self.state is None:
            return

        existing_ids = {
            action_state.action_id
            for action_state in self.state.action_states
        }
        for action_state in self.build_action_states(order_request):
            if action_state.action_id not in existing_ids:
                self.state.action_states.append(action_state)
                existing_ids.add(action_state.action_id)

    def _is_v3_order_finished(self) -> bool:
        if self.state is None:
            return True
        return not self.state.node_states and not self.state.edge_states

    def _is_v3_order_active(self) -> bool:
        """True when a v3 order is accepted and not yet finished.

        Mirrors the inline guard used elsewhere (e.g. adapter_jibot.py:4950):
        state.order_id is not cleared on completion, so a finished/parked robot
        must read as inactive.
        """
        if self.state is None:
            return False
        return bool(getattr(self.state, "order_id", "")) and not self._is_v3_order_finished()

    def _clear_order_queue(self, *, clear_current_step: bool = True) -> None:
        while not self.order_queue.empty():
            try:
                self.order_queue.get_nowait()
                self.order_queue.task_done()
            except asyncio.QueueEmpty:
                break
        if clear_current_step:
            self.current_order_step = None
            self._docking_started_node_ids.clear()
            release_charge_in_place(self)

    def _build_v3_order_steps(self, order_request: Order) -> List[OrderStep]:
        steps: List[OrderStep] = []
        for node in order_request.nodes:
            steps.append(OrderStep("node", node.sequence_id, node))
        for edge in order_request.edges:
            steps.append(OrderStep("edge", edge.sequence_id, edge))
        return sorted(steps, key=lambda step: step.sequence_id)

    def _schedule_v3_order_worker(self) -> None:
        if self._loop is None or not self._loop.is_running():
            print("[ORDER QUEUE] Adapter event loop is not ready; worker not started.")
            return

        def _start_task() -> None:
            if (
                self.order_worker_task is not None
                and not self.order_worker_task.done()
            ):
                if self._active_order_worker_step_is_obsolete():
                    obsolete_step = self.current_order_step
                    item = obsolete_step.item if obsolete_step is not None else None
                    item_id = ""
                    if item is not None:
                        item_id = (
                            getattr(item, "node_id", None)
                            or getattr(item, "edge_id", "")
                        )
                    last_sequence_id = (
                        self.state.last_node_sequence_id
                        if self.state is not None
                        else "<none>"
                    )
                    print(
                        f"[ORDER QUEUE] cancelling obsolete worker "
                        f"kind={obsolete_step.kind if obsolete_step else '<none>'} "
                        f"seq={obsolete_step.sequence_id if obsolete_step else '<none>'} "
                        f"id={item_id} "
                        f"lastNodeSequenceId={last_sequence_id}"
                    )
                    old_task = self.order_worker_task
                    old_task.cancel()
                    old_task.add_done_callback(
                        lambda _task: self._loop.call_soon(_start_task)
                    )
                else:
                    print("[ORDER QUEUE] worker already running; schedule skipped.")
                return

            self.order_worker_task = asyncio.create_task(self._process_v3_order_queue())

            def _on_done(task: asyncio.Task) -> None:
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    print(f"[ORDER QUEUE] worker error: {exc}")
                    traceback.print_exc()
                    self._drop_order_after_worker_crash(exc)

            self.order_worker_task.add_done_callback(_on_done)

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            _start_task()
        else:
            self._loop.call_soon_threadsafe(_start_task)

    def _active_order_worker_step_is_obsolete(self) -> bool:
        if self.state is None or self.current_order_step is None:
            return False

        if not self.state.last_node_id:
            return False

        if self._order_action_step_in_flight is self.current_order_step:
            # 주행 구간은 끝났고 자기 노드의 액션을 실행 중인 스텝이다. 노드 도착이
            # 이미 lastNodeSequenceId 를 이 스텝의 sequenceId 로 올려놨기 때문에
            # 아래 비교는 이 스텝을 '지나간 것'으로 읽고, blocking 액션(엘리베이터
            # 열기 등)을 도중에 끊는다. 재시작한 워커는 같은 actionId 를 또
            # dispatch 한다. 아직 주행 중인 스텝은 그대로 취소 대상으로 남긴다 —
            # 이 가드가 원래 노리던 경우다.
            return False

        if self._coalescing_run_step is self.current_order_step:
            # 코얼레싱 구간을 주행 중인 스텝이다. 중간 노드를 통과할 때마다
            # lastNodeSequenceId 가 올라가므로 아래 비교가 이 스텝을 '지나간 것'으로
            # 읽고 자기 주행을 끊는다. 위 _order_action_step_in_flight 예외와 같은 성질이다.
            return False

        return (
            self.current_order_step.sequence_id
            <= self.state.last_node_sequence_id
        )

    async def _process_v3_order_queue(self) -> None:
        try:
            while not self.order_queue.empty():
                step = await self.order_queue.get()
                self.current_order_step = step
                completed = False
                try:
                    completed = await self._process_v3_order_step(step)
                    if completed:
                        self._clear_v3_order_step(step)
                finally:
                    self.order_queue.task_done()
                    self.current_order_step = None

                if not completed:
                    print(
                        f"[ORDER STEP WAIT] kind={step.kind} "
                        f"seq={step.sequence_id}; requeue and stop worker"
                    )
                    await self.order_queue.put(step)
                    return

            print("[ORDER COMPLETE] all queued node/edge steps processed.")
            self._clear_new_base_request(reason="order complete")
        except asyncio.CancelledError:
            print("[ORDER QUEUE] worker cancelled.")
            raise

    async def _process_v3_order_step(self, step: OrderStep) -> bool:
        item = step.item
        released = bool(getattr(item, "released", False))
        item_id = getattr(item, "node_id", None) or getattr(item, "edge_id", "")

        if not released:
            print(
                f"[ORDER STEP BLOCKED] {step.kind} seq={step.sequence_id} "
                f"id={item_id} released=False"
            )
            return False

        print(
            f"[ORDER STEP START] {step.kind} seq={step.sequence_id} "
            f"id={item_id} released=True actions={len(getattr(item, 'actions', []) or [])}"
        )

        if step.actions_only:
            # The robot already stands on this node, so there is nothing to
            # drive; only the node's actions are still owed.
            print(
                f"[ORDER STEP ACTIONS ONLY] node seq={step.sequence_id} "
                f"id={item_id} (robot already at node)"
            )
            return await self._process_v3_step_actions(step)

        if step.kind == "edge":
            # Edge actions run while the edge is traversed; the drive itself
            # happens in the following node step.
            if not await self._process_v3_step_actions(step):
                return False
            return await self._process_v3_edge_step(step)

        # Node actions are triggered when the node is reached (VDA5050), so
        # drive first, then execute the node's actions.
        if not await self._process_v3_node_step(step):
            return False
        return await self._process_v3_step_actions(step)

    async def _process_v3_step_actions(self, step: OrderStep) -> bool:
        item = step.item
        actions = getattr(item, "actions", []) or []
        owner_id = getattr(item, "node_id", None) or getattr(item, "edge_id", "")
        entries = []
        for action in actions:
            action_state = self._find_action_state_for_step(step, action.action_id)
            if action_state is None:
                continue

            if not self._order_action_is_pending(action):
                # 멱등성 최후 방어선: actionId 는 주문당 한 번만 dispatch 한다.
                # 이 스텝을 큐에 다시 넣은 쪽(orderUpdate 큐 재구성이든 워커
                # 재시작이든)이 요구하는 건 새 일이 아니라 재실행이다.
                print(
                    f"[ORDER ACTION SKIP] {step.kind}={owner_id} "
                    f"actionId={action.action_id} type={action.action_type} "
                    f"status={action_state.action_status.value} (already dispatched)"
                )
                continue

            blocking_type = str(
                getattr(action.blocking_type, "value", action.blocking_type)
            )
            print(
                f"[ORDER ACTION] {step.kind}={owner_id} actionId={action.action_id} "
                f"type={action.action_type} blockingType={blocking_type}"
            )
            self._dispatched_order_action_ids.add(str(action.action_id))
            entries.append((action, action_state, owner_id, blocking_type))

        if not entries:
            return True

        # 이 스텝이 주행 구간을 지났다고 표시한다. 액션이 도는 동안 들어온
        # orderUpdate 가 워커를 취소하지 못하게 하기 위함이다.
        self._order_action_step_in_flight = step
        try:
            # VDA5050 v3: SOFT/HARD stop automatic driving.  Arrival is accepted
            # inside a configured reach zone, while JIBOT can still be finishing
            # the previous UmGoto, so awaiting actions alone is insufficient.
            last_driving_blocker = max(
                (
                    index
                    for index, entry in enumerate(entries)
                    if entry[3] in {"SOFT", "HARD"}
                ),
                default=-1,
            )
            if last_driving_blocker >= 0:
                if not await self._stop_for_blocking_order_actions(entries):
                    return False
                await self._execute_order_action_sequence(
                    entries[: last_driving_blocker + 1],
                    predecessors=self._active_order_background_action_tasks(),
                    initial_barriers=self._active_order_exclusive_action_tasks(),
                    release_none_at_end=True,
                )
                self._schedule_order_action_sequence(entries[last_driving_blocker + 1 :])
            else:
                # NONE and SINGLE both allow automatic driving.  Their mutual
                # parallel/exclusive ordering continues independently of motion.
                self._schedule_order_action_sequence(entries)

            # 방금 예약된 startCharging 이 도킹 마커를 볼 수 있게, 주문 워커가
            # 이 노드를 clear 하기 전에 한 틱 양보한다.
            await asyncio.sleep(0)
        finally:
            if self._order_action_step_in_flight is step:
                self._order_action_step_in_flight = None

        return True

    def _active_order_background_action_tasks(self) -> tuple[asyncio.Task[Any], ...]:
        return tuple(
            task
            for task in self._order_background_action_tasks
            if not task.done()
        )

    def _active_order_exclusive_action_tasks(self) -> tuple[asyncio.Task[Any], ...]:
        return tuple(
            task
            for task in self._order_exclusive_background_action_tasks
            if not task.done()
        )

    async def _await_order_action_tasks(
        self,
        tasks: Any,
    ) -> None:
        pending = tuple(task for task in tasks if task is not None and not task.done())
        if not pending:
            return
        results = await asyncio.gather(*pending, return_exceptions=True)
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                print(f"[ORDER ACTION SCHEDULER ERROR] {result}")

    async def _execute_order_action_sequence(
        self,
        entries: Any,
        *,
        predecessors: Any = (),
        initial_barriers: Any = (),
        release_none_at_end: bool = False,
    ) -> None:
        """Execute one ordered action list with VDA5050 parallel semantics."""
        prior = tuple(predecessors)
        barriers = tuple(initial_barriers)
        await self._await_order_action_tasks(barriers)
        prior = tuple(task for task in prior if task not in barriers)
        parallel: List[Tuple[asyncio.Task[Any], str]] = []
        for action, action_state, owner_id, blocking_type in entries:
            if blocking_type in {"NONE", "SOFT"}:
                parallel.append(
                    (
                        asyncio.create_task(
                            self._execute_order_action(action, action_state, owner_id)
                        ),
                        blocking_type,
                    )
                )
                continue

            # SINGLE/HARD are exclusive: all earlier parallel work, including
            # non-blocking work from a previous action point, must be terminal.
            await self._await_order_action_tasks(
                (*prior, *(task for task, _kind in parallel))
            )
            prior = ()
            parallel = []
            await self._execute_order_action(action, action_state, owner_id)

        if release_none_at_end:
            await self._await_order_action_tasks(
                task for task, kind in parallel if kind == "SOFT"
            )
            for task, kind in parallel:
                if kind == "NONE" and not task.done():
                    self._track_order_background_action_task(task)
        else:
            await self._await_order_action_tasks(
                task for task, _kind in parallel
            )

    def _schedule_order_action_sequence(self, entries: Any) -> None:
        entries = tuple(entries)
        if not entries:
            return
        predecessors = self._active_order_background_action_tasks()
        initial_barriers = self._active_order_exclusive_action_tasks()
        task = asyncio.create_task(
            self._execute_order_action_sequence(
                entries,
                predecessors=predecessors,
                initial_barriers=initial_barriers,
            )
        )
        self._track_order_background_action_task(
            task,
            exclusive=any(entry[3] == "SINGLE" for entry in entries),
        )

    def _track_order_background_action_task(
        self,
        task: asyncio.Task[Any],
        *,
        exclusive: bool = False,
    ) -> None:
        self._order_background_action_tasks.add(task)
        if exclusive:
            self._order_exclusive_background_action_tasks.add(task)

        def _done(done: asyncio.Task[Any]) -> None:
            self._order_background_action_tasks.discard(done)
            self._order_exclusive_background_action_tasks.discard(done)
            if not done.cancelled():
                try:
                    done.result()
                except Exception as exc:
                    print(f"[ORDER ACTION SCHEDULER ERROR] {exc}")

        task.add_done_callback(_done)

    async def _stop_for_blocking_order_actions(self, entries: Any) -> bool:
        """Stop residual automatic motion before the first SOFT/HARD action."""
        if self._vehicle is None or not self._has_active_automatic_motion():
            return True
        try:
            print("[ORDER ACTION BLOCK] stopping automatic driving for SOFT/HARD")
            await self._vehicle.stop_motion()
            await self._wait_until_automatic_motion_stopped()
            print("[ORDER ACTION BLOCK] vehicle stands still")
            return True
        except Exception as exc:
            description = f"Could not stop automatic driving before action: {exc}"
            print(f"[ORDER ACTION BLOCK FAILED] {description}")
            for _action, action_state, _owner_id, blocking_type in entries:
                if blocking_type not in {"SOFT", "HARD"}:
                    continue
                action_state.action_status = ActionStatus.FAILED
                action_state.result_description = description
            self.request_state_publish("order action driving stop failed")
            return False

    def _has_active_automatic_motion(self) -> bool:
        """True while JIBOT still owns a goto, including #brake/#watch waits."""
        if self._derive_driving():
            return True
        mode = str(getattr(self._vehicle, "_mode", "") or "").strip().lower()
        if "goto" in mode:
            return True
        cur_task = getattr(self._vehicle, "_cur_task", None) or {}
        data = cur_task.get("data", {}) if isinstance(cur_task, dict) else {}
        value = data.get("value", {}) if isinstance(data, dict) else {}
        return (
            isinstance(value, dict)
            and str(value.get("cmd", "") or "").strip().lower() == "goto"
        )

    async def _wait_until_automatic_motion_stopped(self) -> None:
        poll_sec = float(
            getattr(self.config.settings, "standstill_poll_interval_sec", 0.1)
        )
        timeout_sec = float(
            getattr(self.config.settings, "standstill_timeout_sec", 10.0)
        )

        async def _poll() -> None:
            while self._has_active_automatic_motion():
                await asyncio.sleep(poll_sec)

        if timeout_sec > 0:
            await asyncio.wait_for(_poll(), timeout=timeout_sec)
        else:
            await _poll()

    def _should_settle_at(self, step: Any, *, is_run_end: bool) -> bool:
        """이 노드에서 완전 정지를 기다려야 하는가.

        연속 주행에서 중간 노드는 통과 지점이지 정지 지점이 아니다.
        VDA5050 3.0 §6.1.2 는 정지 의무를 base 의 마지막 노드(decision point)
        하나로만 규정하고, §6.6.2 는 노드 위 정지를 SOFT/HARD 블로킹 액션이
        있을 때의 예외로 기술한다.
        """
        if not self._continuous_path_enabled():
            return True
        return is_run_end

    def _step_has_blocking_action(self, step: Any) -> bool:
        """SOFT/HARD 블로킹 액션이 하나라도 있으면 True.

        _process_v3_step_actions 의 last_driving_blocker 와 같은 기준이다.
        blocking_type 은 enum 일 수도 문자열일 수도 있어 value 를 먼저 본다.
        """
        for action in getattr(step.item, "actions", None) or []:
            raw = getattr(action, "blocking_type", None)
            value = str(getattr(raw, "value", raw) or "").strip().upper()
            if value in {"SOFT", "HARD"}:
                return True
        return False

    def _is_coalescing_breaker(
        self, step: Any, prev_node_id: Optional[str] = None
    ) -> bool:
        """이 스텝을 앞 노드와 합칠 수 없는가 (spec 5.2).

        prev_node_id 는 이 스텝이 도착점인 구간의 **시작 노드**다. dock/move 는
        from->to 방향성 룰이라 시작 노드를 모르면 판정이 어긋난다. 앞날 보기에서
        self._last_node_id 를 그대로 쓰면 한 칸 뒤처진 노드를 보게 되므로
        _is_run_end 가 올바른 값을 넘긴다. None 이면 현행(self._last_node_id) 그대로다.
        """
        item = step.item
        node_id = str(getattr(item, "node_id", "") or "")
        if step.kind != "node":
            return True
        if getattr(step, "actions_only", False):
            return True
        if not getattr(item, "released", False):
            return True
        if self._step_has_blocking_action(step):
            return True
        if self._dock_segment_rule(node_id, prev_node_id) is not None:
            return True
        if self._move_motion_rule(node_id, prev_node_id) is not None:
            return True
        if self._is_dock_work_node(node_id):
            return True
        if self._resolve_node_target(item) is None:
            return True
        return False

    def _is_run_end(self, step: Any) -> bool:
        """이 스텝이 코얼레싱 구간의 마지막인가.

        큐에 남은 스텝들을 순서대로 놓고, 이 스텝에서 시작하는 구간의 끝을 구한다.
        구간의 끝이 곧 "여기서는 실제로 서야 한다"는 뜻이다.

        큐에는 노드와 엣지가 sequenceId 순으로 번갈아 들어 있는데
        (_build_v3_order_steps_from_state), 엣지는 주행 목적지가 아니라 노드 사이를
        잇는 연결일 뿐이다. 엣지를 전부 그대로 술어에 먹이면 _is_coalescing_breaker 의
        `kind != "node"` 조건에 항상 걸려 구간 길이가 늘 1 이 되고, continuous 가
        stop_point 와 완전히 같아진다. 그래서 노드만 골라 구간을 센다.

        **다만 블로킹 액션을 단 엣지는 남긴다** — spec §5.2 가 별도 행으로 규정한
        끊는 조건이다. 엣지 액션은 다음 노드 주행 **전에** 실행되므로, 그 엣지를
        걸러 버리면 구간이 그 너머까지 이어지고 로봇은 노드를 지나친 뒤
        _stop_for_blocking_order_actions 에 걸려 급제동한다. 안전하지 않은 것은
        아니지만(정지 게이트는 그대로 산다) 이 조건이 막으려던 것이 바로
        "노드에서의 통제된 정지"다.

        엣지를 나머지 걸러도 안전한 이유: 큐에는 released 스텝만 들어온다
        (_rebuild_v3_order_queue_from_state 가 미릴리즈에서 break).
        """
        if not self._continuous_path_enabled():
            return True
        # (스텝, 그 스텝이 도착점인 구간의 시작 노드) 쌍으로 넘긴다. dock/move 는
        # from->to 방향성 룰이라, 앞날 보기에서 self._last_node_id 를 그대로 쓰면
        # 한 칸 뒤처진 노드를 시작점으로 보고 매칭에 실패한다.
        pending: List[Tuple[Any, Optional[str]]] = [(step, self._last_node_id)]
        prev_id = str(getattr(step.item, "node_id", "") or "")
        for queued in list(self.order_queue._queue):  # asyncio.Queue 내부 deque
            if queued.kind != "node" and not self._step_has_blocking_action(queued):
                continue
            pending.append((queued, prev_id))
            if queued.kind == "node":
                prev_id = str(getattr(queued.item, "node_id", "") or "")
        last = drivable_run(
            pending,
            0,
            lambda pair: self._is_coalescing_breaker(pair[0], pair[1]),
        )
        return last == 0

    def _vehicle_xy(self) -> Optional[Tuple[float, float]]:
        """차량이 보고하는 현재 (x, y). 좌표가 없으면 None.

        _wait_until_node_position_reached 가 도착을 판정할 때 읽는 것과 같은 소스다
        — 통과 판정이 다른 좌표계를 쓰면 두 판정이 조용히 어긋난다.
        """
        vx = self._optional_float(getattr(self._vehicle, "_x", None))
        vy = self._optional_float(getattr(self._vehicle, "_y", None))
        if vx is None or vy is None:
            return None
        return (vx, vy)

    async def _settle_goto_arrival(self, node: Any) -> None:
        """Hold the node open until JIBOT's goto task has actually finished.

        Arrival is accepted anywhere inside the reach zone, so
        _wait_until_node_position_reached returns while the robot may still be
        driven to the node's exact pose. Completing there publishes a lastNodeId
        the robot has not settled on and lets the next step dispatch on top of a
        live goto. _has_active_automatic_motion is the same standstill test the
        SOFT/HARD action gate uses, so both read "the drive is over" alike.

        A timeout is NOT a node failure: the pose already satisfies the node, so
        a robot that keeps its goto task pinned (stale telemetry, firmware
        quirk) falls back to the arrival-only completion this wait refines
        rather than killing the order worker with an exception.
        """
        try:
            await self._wait_until_automatic_motion_stopped()
        except asyncio.TimeoutError:
            timeout = getattr(self.config.settings, "standstill_timeout_sec", 10.0)
            print(
                f"[ORDER NODE SETTLE TIMEOUT] id={node.node_id} "
                f"still reporting automatic motion after {timeout}s; "
                "completing on arrival"
            )

    _PATH_CONTROL_VALUES = ("stop_point", "continuous")

    def _continuous_path_enabled(self) -> bool:
        """path_control 이 continuous 일 때만 True. 모르는 값은 경고 후 현행 동작.

        문자열 enum 검증은 last_node_capture_mode / nearest_node_mode 선례를 따른다 —
        부팅을 막지 않고 경고 후 안전한 쪽(현행 동작)으로 폴백한다.
        """
        value = str(
            getattr(self.config.settings, "path_control", "stop_point")
        ).strip().lower()
        if value not in self._PATH_CONTROL_VALUES:
            if value not in self._warned_path_control_values:
                self._warned_path_control_values.add(value)
                print(
                    f"[CONFIG WARN] unknown path_control={value!r}; "
                    f"expected one of {self._PATH_CONTROL_VALUES}; using 'stop_point'"
                )
            return False
        return value == "continuous"

    # base 가 짧아지고 있음을 FMS 에 알리는 임계치. 1 이면 "다음이 decision point" 다.
    _NEW_BASE_REQUEST_THRESHOLD = 1

    def _set_new_base_request(self, wanted: bool, *, reason: str) -> None:
        """newBaseRequest 값을 실제로 쓰고, 바뀔 때만 발행한다.

        큐 기반 판정(_set_new_base_request_from_queue)과 오더 종료/취소(무조건
        해제)가 이 가드를 각자 베끼면 한쪽만 고치는 회귀가 난다 — 그래서 값을
        쓰는 지점을 여기 하나로 모은다.
        """
        if self.state is None:
            return
        # getattr 로 읽는 이유: 이 setter 는 노드 경로 전체가 지나는 공용 코드에서
        # 불리는데, 기존 테스트 더블(SimpleNamespace)에 이 필드가 없는 경우가
        # 있다(2026-08-27 확인, EdgeStateReleaseTimingTests). 실제 State 는 항상
        # 이 필드를 갖고 None 으로 초기화되므로 운영 경로 동작은 그대로다.
        if bool(getattr(self.state, "new_base_request", None)) == wanted:
            return
        self.state.new_base_request = wanted
        print(f"[ORDER BASE REQUEST] newBaseRequest={wanted} reason={reason}")
        self.request_state_publish("new base request")

    def _set_new_base_request_from_queue(self, *, remaining_released: int) -> None:
        """base 끝이 가까우면 newBaseRequest 를 올린다.

        VDA5050 3.0 §6.6.3: "If the mobile robot detects that its base is running
        short, it can set the newBaseRequest flag to 'true' to attempt to prevent
        unnecessary braking." 이 신호가 없으면 연속 주행을 만들어도 base 의 마지막
        노드에서는 그대로 정지한다 — §6.1.2 가 decision point 정지를 의무로 두기 때문이다.
        stop_point 에서는 어차피 노드마다 서므로 올리지 않는다.
        """
        wanted = (
            self._continuous_path_enabled()
            and remaining_released <= self._NEW_BASE_REQUEST_THRESHOLD
        )
        self._set_new_base_request(wanted, reason=f"remaining={remaining_released}")

    def _clear_new_base_request(self, *, reason: str) -> None:
        """오더가 더 이상 진행 중이 아니면 newBaseRequest 를 무조건 내린다.

        이 플래그의 의미는 "지금 이 오더의 base 를 늘려 달라"다. 오더가 끝나거나
        취소된 뒤에도 true 로 남으면 로봇이 아무 오더도 없이 대기 중인데 곧 base 가
        바닥난다고 거짓 보고를 하는 셈이다 — §6.6.3 신호의 의미 자체가 깨진다.
        continuous 게이트를 여기 걸면 stop_point 로 바뀐 채 오더가 끝날 때 이전
        오더에서 세운 true 가 영원히 안 내려가므로, 여기서는 무조건 호출한다.
        """
        self._set_new_base_request(False, reason=reason)

    async def _execute_order_action(
        self,
        action: Any,
        action_state: ActionState,
        owner_id: Optional[str] = None,
    ) -> None:
        """Run one order action, then surface a FAILED outcome as an error.

        A FAILED actionState alone is not enough: the order worker keeps going
        and the order reaches ORDER COMPLETE, so a clamp that never opened looks
        like a clean run to the fleet. Every terminal-FAILED path below funnels
        through here, so the error is raised once, wherever the failure came
        from.
        """
        try:
            await self._dispatch_order_action(action, action_state, owner_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Not every dispatch branch catches for itself (a registry/recipe
            # handler can raise straight through). Without this the exception
            # escaped to the scheduler, leaving the action stuck non-terminal.
            action_state.action_status = ActionStatus.FAILED
            action_state.result_description = (
                f"{getattr(action, 'action_type', 'action')} failed: {exc}"
            )
            print(
                f"[ORDER ACTION FAILED] actionId={getattr(action, 'action_id', '')} "
                f"type={getattr(action, 'action_type', '')}: {exc}"
            )

        if action_state.action_status != ActionStatus.FAILED:
            return

        self._set_order_action_failed_error(
            action,
            str(getattr(action_state, "result_description", "") or ""),
            owner_id=owner_id,
        )
        self.request_state_publish("order action failed")

    def _set_order_action_failed_error(
        self,
        action: Any,
        reason: str,
        *,
        owner_id: Optional[str] = None,
    ) -> None:
        """Publish a FATAL error for an order action that ended FAILED.

        FATAL because the physical work the order asked for did not happen —
        letting the robot drive on (leaving a charger with the clamp still shut,
        say) is worse than holding for the fleet to decide. De-duped per
        actionId so a retried action does not pile up copies.
        """
        if self.state is None:
            return

        action_id = str(getattr(action, "action_id", "") or "")
        self.state.errors = [
            error
            for error in self.state.errors
            if not (
                getattr(error, "error_type", None) == ErrorType.ORDER_ACTION_FAILED
                and any(
                    ref.reference_key == "actionId" and ref.reference_value == action_id
                    for ref in getattr(error, "error_references", []) or []
                )
            )
        ]
        self.state.errors.append(
            Error(
                error_type=ErrorType.ORDER_ACTION_FAILED,
                error_level=ErrorLevel.FATAL,
                error_references=[
                    ErrorReference("actionId", action_id),
                    ErrorReference(
                        "actionType", str(getattr(action, "action_type", "") or "")
                    ),
                    ErrorReference("nodeId", str(owner_id or "")),
                    ErrorReference("reason", reason),
                ],
                error_description=f"Order action failed: {reason}",
            )
        )
        print(
            f"[ORDER ACTION ERROR] actionId={action_id} "
            f"type={getattr(action, 'action_type', '')}: {reason}"
        )

    async def _dispatch_order_action(
        self,
        action: Any,
        action_state: ActionState,
        owner_id: Optional[str] = None,
    ) -> None:
        """Execute one node/edge action and track its VDA5050 status.

        Order actions reuse the JIBOT command executor of the instant-action
        path (actionType="jibotCommand" or a bare COMMAND_SPECS name).
        Unsupported action types are reported FAILED instead of being silently
        auto-finished.
        """
        action_state.action_status = ActionStatus.INITIALIZING
        self.request_state_publish("order action initializing")

        if action.action_type == "switchMap":
            await self._execute_switch_map_order_action(action, action_state)
            return

        if action.action_type == "startCharging":
            # startCharging has no JIBOT command name; it maps to UmDock just
            # like the instant-action path (_handle_start_charging_instant_action).
            await self._dock_for_order_action(action, action_state, node_id=owner_id)
            return

        if is_hardware_action(action.action_type):
            await self._execute_hardware_order_action(action, action_state)
            return

        if self._action_registry.has(action.action_type):
            await self._execute_registered_order_action(action, action_state)
            return

        if not self._is_jibot_command_instant_action(action):
            action_state.action_status = ActionStatus.FAILED
            action_state.result_description = (
                f"Unsupported order action type: {action.action_type}"
            )
            self._record_action_not_found(action, scope="order")
            self.request_state_publish("order action unsupported")
            print(
                f"[ORDER ACTION UNSUPPORTED] actionId={action.action_id} "
                f"type={action.action_type}"
            )
            return

        try:
            command, gap, params = self._parse_jibot_command_action(action)
            if self._vehicle is None:
                raise ValueError("JIBOT vehicle is not initialized")
            self._vehicle.build_command(command, gap=gap, **params)

            action_state.action_status = ActionStatus.RUNNING
            self.request_state_publish("order action running")

            response = await self._dispatch_jibot_command(
                command,
                gap=gap,
                params=params,
                timeout=self._parse_jibot_ack_timeout(action, default=getattr(self.config.settings, "jibot_command_default_timeout_sec", 3.0)),
            )
            rejection_reason = self._jibot_command_rejection_reason(command, response)
            if rejection_reason is not None:
                action_state.action_status = ActionStatus.FAILED
                action_state.result_description = f"Rejected: {rejection_reason}"
                print(
                    f"[ORDER ACTION REJECTED] actionId={action.action_id} "
                    f"command={command}: {rejection_reason}"
                )
            else:
                action_state.action_status = ActionStatus.FINISHED
                action_state.result_description = f"{command} sent"
                print(
                    f"[ORDER ACTION DONE] actionId={action.action_id} "
                    f"command={command}"
                )
        except Exception as exc:
            action_state.action_status = ActionStatus.FAILED
            action_state.result_description = f"{action.action_type} failed: {exc}"
            print(
                f"[ORDER ACTION FAILED] actionId={action.action_id} "
                f"type={action.action_type}: {exc}"
            )

        self.request_state_publish("order action terminal")

    async def _execute_registered_order_action(
        self,
        action: Any,
        action_state: ActionState,
    ) -> None:
        action_state.action_status = ActionStatus.RUNNING
        self.request_state_publish("order extension action running")
        # Registry actions (notably recipes) may own motion while executing as
        # part of this order. The owner marker is propagated to recipe children.
        setattr(
            action,
            "_owner_order_id",
            getattr(self.order, "order_id", None),
        )
        result = await self._action_registry.execute(action, self)
        action_state.action_status = result.status
        action_state.result_description = result.description
        self.request_state_publish("order extension action terminal")

    async def _execute_hardware_order_action(
        self,
        action: Any,
        action_state: ActionState,
    ) -> None:
        action_state.action_status = ActionStatus.RUNNING
        self.request_state_publish("order hardware action running")

        try:
            ok, result_description = await execute_hardware_order_action(self, action)
            if not ok:
                action_state.action_status = ActionStatus.FAILED
                action_state.result_description = result_description
                print(
                    f"[ORDER HARDWARE ACTION FAILED] actionId={action.action_id} "
                    f"type={action.action_type}: {action_state.result_description}"
                )
                self.request_state_publish("order hardware action terminal")
                return
        except Exception as exc:
            action_state.action_status = ActionStatus.FAILED
            action_state.result_description = f"{action.action_type} failed: {exc}"
            print(
                f"[ORDER HARDWARE ACTION FAILED] actionId={action.action_id} "
                f"type={action.action_type}: {exc}"
            )
            self.request_state_publish("order hardware action terminal")
            return

        action_state.action_status = ActionStatus.FINISHED
        action_state.result_description = result_description
        print(
            f"[ORDER HARDWARE ACTION DONE] actionId={action.action_id} "
            f"type={action.action_type}: {result_description}"
        )
        self.request_state_publish("order hardware action terminal")

    async def _dock_for_order_action(
        self,
        action: Any,
        action_state: ActionState,
        node_id: Optional[str] = None,
    ) -> None:
        """Order-carried startCharging: dock via UmDock so charging begins.

        Mirrors _handle_start_charging_instant_action but tracks the node/edge
        action_state directly instead of the instant-action registry. The
        charging flag in powerSupply then follows the robot status.
        """
        if self._vehicle is None:
            action_state.action_status = ActionStatus.FAILED
            action_state.result_description = (
                "startCharging failed: JIBOT vehicle is not initialized"
            )
            self.request_state_publish("order action terminal")
            return

        if node_id and node_id in self._docking_started_node_ids:
            action_state.action_status = ActionStatus.FINISHED
            action_state.result_description = (
                "Docking already started by node motion"
            )
            print(
                f"[ORDER ACTION DONE] actionId={action.action_id} "
                f"command=UmDock already started node={node_id}"
            )
            self.request_state_publish("order action terminal")
            return

        action_state.action_status = ActionStatus.RUNNING
        self.request_state_publish("order action running")

        if should_charge_in_place(self, node_id):
            ok, desc = await run_charge_in_place(self)
            action_state.action_status = (
                ActionStatus.FINISHED if ok else ActionStatus.FAILED
            )
            action_state.result_description = desc
            print(
                f"[ORDER ACTION DONE] actionId={action.action_id} "
                f"command=chargeInPlace ok={ok}"
            )
            self.request_state_publish("order action terminal")
            return

        try:
            await self._vehicle.um_dock(**self._dock_approach_params(str(node_id) if node_id else ""))
        except Exception as exc:
            action_state.action_status = ActionStatus.FAILED
            action_state.result_description = f"startCharging failed: {exc}"
            print(
                f"[ORDER ACTION FAILED] actionId={action.action_id} "
                f"type=startCharging: {exc}"
            )
            self.request_state_publish("order action terminal")
            return

        action_state.action_status = ActionStatus.FINISHED
        action_state.result_description = "UmDock sent; charging follows robot status"
        print(
            f"[ORDER ACTION DONE] actionId={action.action_id} "
            f"command=UmDock (startCharging)"
        )
        self.request_state_publish("order action terminal")

    def _finish_step_placeholder_action(self, step: OrderStep) -> None:
        """Finish the placeholder action state of a node/edge without actions."""
        if self.state is None:
            return

        placeholder_id = f"{step.kind}_{step.sequence_id}"
        for action_state in self.state.action_states:
            if action_state.action_id == placeholder_id:
                action_state.action_status = ActionStatus.FINISHED

    async def _process_v3_edge_step(self, step: OrderStep) -> bool:
        edge = step.item
        print(
            f"[ORDER EDGE CLEAR] seq={edge.sequence_id} id={edge.edge_id} "
            f"start={edge.start_node_id} end={edge.end_node_id}"
        )
        self._finish_step_placeholder_action(step)
        return True

    def _dock_motion_rule(self, node_id: str) -> Optional[Any]:
        """The mode="dock" motion rule whose destination is node_id, or None.

        From config.toml `motion_rules`. dock rules match on `to` only
        (any from), so a charger node is detected wherever the robot arrives
        from.
        """
        for rule in getattr(self.config, "motion_rules", None) or []:
            if (
                getattr(rule, "mode", "") == "dock"
                and getattr(rule, "to", None) == node_id
            ):
                return rule
        return None

    def _move_motion_rule(
        self, node_id: str, prev_node_id: Optional[str] = None
    ) -> Optional[Any]:
        """The mode="move" motion rule for the segment ending at node_id, or None.

        Matches `to == node_id` and, when the rule sets `from`, requires it to
        equal the previously reached node (the segment start). So the move runs
        only when traversing the configured from->to segment.

        prev_node_id 는 코얼레싱 앞날 보기 전용이다. 큐의 다음 노드를 볼 때
        self._last_node_id 는 *현재 노드의 앞 노드*이지 그 노드의 앞 노드가
        아니어서, from/to 방향성 룰이 매칭에 실패한다. 호출자가 그 구간의 실제
        시작 노드를 넘길 수 있게 열어 둔다. None 이면 현행 그대로다.
        """
        prev = self._last_node_id if prev_node_id is None else prev_node_id
        for rule in getattr(self.config, "motion_rules", None) or []:
            if getattr(rule, "mode", "") != "move":
                continue
            if getattr(rule, "to", None) != node_id:
                continue
            rule_from = getattr(rule, "from_node", None)
            if rule_from is None or rule_from == prev:
                return rule
        return None

    def _dock_segment_rule(
        self, node_id: str, prev_node_id: Optional[str] = None
    ) -> Optional[Any]:
        """The mode="dock" rule for ARRIVING at node_id, honoring direction.

        Like _dock_motion_rule but, when the rule sets `from`, requires it to
        equal the previously reached node (the segment start). So UmDock fires
        only when traversing the configured from->to (approach->charger) segment
        — never on the reverse (charger->approach) departure, nor on an unrelated
        route that merely passes through the charger. The robot is already at
        `from` (it just reached it), so no approach goto is issued; it docks in
        place. Unspecified directions fall through to a plain goto.

        prev_node_id 의 의미는 _move_motion_rule 과 같다 — 코얼레싱 앞날 보기가
        그 구간의 실제 시작 노드를 넘긴다.
        """
        prev = self._last_node_id if prev_node_id is None else prev_node_id
        for rule in getattr(self.config, "motion_rules", None) or []:
            if getattr(rule, "mode", "") != "dock":
                continue
            if getattr(rule, "to", None) != node_id:
                continue
            rule_from = getattr(rule, "from_node", None)
            if rule_from is None or rule_from == prev:
                return rule
        return None

    def _dock_fail_timeout_sec(self, node_id: str) -> float:
        """Seconds to wait for charging after UmDock before rejecting the dock."""
        rule = self._dock_motion_rule(node_id)
        if rule is not None and getattr(rule, "fail_timeout_sec", None) is not None:
            return float(rule.fail_timeout_sec)
        return float(getattr(self.config.dock, "fail_timeout_sec", 90.0))

    def _dock_approach_params(self, node_id: str) -> Dict[str, Any]:
        """Merged UmDock params for a dock node: global [dock.approach_params]
        overlaid with the matching motion rule's approach_params. None-valued
        keys are dropped so a node with no config yields a bare UmDock."""
        params: Dict[str, Any] = {}
        global_ap = getattr(self.config.dock, "approach_params", None)
        if global_ap is not None:
            params.update(global_ap.as_params())
        rule = self._dock_motion_rule(node_id)
        rule_ap = getattr(rule, "approach_params", None) if rule is not None else None
        if isinstance(rule_ap, dict):
            params.update({k: v for k, v in rule_ap.items() if v is not None})
        return params

    def _is_dock_work_node(self, node_id: str) -> bool:
        return node_id in set(getattr(self.config.dock, "nodes", []) or [])

    def _is_dock_motion_node(self, node_id: str) -> bool:
        """True when node_id is a dock target — a mode="dock" motion_rule's `to`
        (any direction) or a dock.nodes work node.

        NON-directional predicate ("is this a charger/dock node at all"), used by
        in-place-charge detection (_should_charge_in_place) and dock-work
        handling. Whether to UmDock NOW is a separate, DIRECTIONAL decision made
        by _dock_segment_rule (only when arriving from the rule's `from`). Dock
        targets come solely from motion_rules / dock.nodes — never the JIBOT
        map's raw "Dock" category, which also tags approach waypoints (e.g.
        ``*_BEFORE``) and would mis-fire UmDock.
        """
        return (
            self._dock_motion_rule(node_id) is not None
            or self._is_dock_work_node(node_id)
        )

    async def _send_node_motion(self, node: Any) -> Optional[str]:
        """Transmit the motion command that drives the robot to a node.

        Used by the order worker for the initial dispatch to a node, where a
        dock-work node legitimately means UmDock. stopPause and the retry path
        instead call _send_node_goto directly: they may be re-issuing a
        move-segment fallback goto, and _is_dock_work_node is non-directional,
        so routing through here could dock the robot instead of driving it the
        rest of the way. The jibot-client only transmits the command; arrival
        is verified separately by _wait_until_node_position_reached().

        Returns:
            A rejection-reason string if the goto was rejected or unacknowledged
            by the robot, or ``None`` on success. Also returns ``None`` for charge
            routes and simulator mode, where the ack check does not apply.
        """
        if should_charge_in_place(self, node.node_id):
            print(
                f"[ORDER NODE CHARGE IN PLACE] seq={node.sequence_id} "
                f"id={node.node_id}"
            )
            ok, desc = await run_charge_in_place(self)
            if ok:
                self._docking_started_node_ids.add(str(node.node_id))
                return None
            return desc

        stop_reason = await self._ensure_not_charging_before_order_motion(node)
        if stop_reason is not None:
            return stop_reason

        motor_reason = await self._ensure_motor_enabled_before_order_motion(node)
        if motor_reason is not None:
            return motor_reason

        # Bare UmDock here is only for dock-WORK nodes (config [dock].nodes).
        # Charger docking is dispatched by the directional _dock_segment_rule in
        # _process_v3_node_step, so a charger reached in a non-dock direction
        # falls through to a plain goto rather than docking here.
        if self._is_dock_work_node(node.node_id):
            print(
                f"[ORDER NODE DOCK] seq={node.sequence_id} id={node.node_id} "
                "command=UmDock"
            )
            await self._vehicle.um_dock(**self._dock_approach_params(str(node.node_id)))
            self._docking_started_node_ids.add(str(node.node_id))
            return

        return await self._send_node_goto(node)

    # Below this displacement (mm) the bearing to the target is noise, not a
    # travel direction: a few mm of pose jitter swings it through any angle.
    _GOTO_BEARING_MIN_TRAVEL_MM = 10.0

    # _wait_until_node_passed 에서 prev_xy 보간 앵커를 몇 폴링 주기까지 묵혀
    # 두는가. 표본 하나가 스케줄링 지터로 늦는 것은 정상이라 1배는 너무
    # 예민하지만, dropout 이 몇 주기씩 이어지면 다음 표본과의 선분이 로봇이
    # 실제로 가지 않은 구간을 가로질러 통과를 오판할 수 있다(§Finding 3).
    # poll 값에 곱해 쓰므로 새 설정 키를 추가하지 않는다.
    _PASS_WAIT_STALE_GAP_POLLS = 3.0

    def _node_goto_theta_deg(
        self,
        node: Any = None,
        target_xy: Optional[Tuple[float, float]] = None,
        map_theta_deg: Any = None,
    ) -> Optional[float]:
        """UmGoto `poseTh` in degrees for a goto; None only if nothing is known.

        poseTh cannot be left out. urobot SILENTLY DROPS a `target=pose` UmGoto
        that carries no poseTh: no error frame comes back (UmGoto is declared
        `ret:none`, so there is nothing to ack), no route is planned, and the
        robot just stays Stopped. Measured on HN-SH6-TR-002 2026-08-22
        22:46-22:52 — three re-sends of
        `{"target":"pose","poseX":16350.0,"poseY":91.0}` left the pose at
        (16301, -1155) unchanged, while the 48 gotos that carried poseTh in the
        same session all drove normally. So "arrive without turning" has to name
        a heading; it cannot be a missing field.

        Sources, in order:
          1. the order's `nodePosition.theta` — VDA5050 radians, converted to
             JIBOT degrees (common_amr_map._jibot_pose is the same conversion
             the other way). This is the only per-order heading there is.
          2. the bearing from where the robot stands to the target: the heading
             it already has when it rolls up to the node, so it stops there
             rather than turning. Confirmed against telemetry — over 215 forward
             moves on HN-SH6-TR-002, atan2(dy, dx) in degrees matched the
             reported heading to a 2.4 deg median.
             NOT the heading held at dispatch: that one is from BEFORE the
             drive, so asking for it makes the robot swing back on arrival.
          3. `map_theta_deg`, the node's heading on the robot map. Last resort,
             for when there is no pose to take a bearing from: every PathPoint
             on this site's map carries 0.00, and honouring that is exactly what
             turned the robot at every arrival. Still better than dropping
             poseTh, which costs the motion outright.

        Arrival is judged on x/y only (_wait_until_node_position_reached via
        _node_xy), so the heading never affects node completion.
        """
        position = getattr(node, "node_position", None)
        order_theta = getattr(position, "theta", None) if position is not None else None
        if order_theta is not None:
            try:
                return math.degrees(float(order_theta))
            except (TypeError, ValueError):
                pass

        vx = self._optional_float(getattr(self._vehicle, "_x", None))
        vy = self._optional_float(getattr(self._vehicle, "_y", None))
        current = self._optional_float(getattr(self._vehicle, "_th", None))
        if vx is not None and vy is not None and target_xy is not None:
            dx = float(target_xy[0]) - vx
            dy = float(target_xy[1]) - vy
            if math.hypot(dx, dy) >= self._GOTO_BEARING_MIN_TRAVEL_MM:
                return math.degrees(math.atan2(dy, dx))

        # Standing on the node, or no pose to take a bearing from: hold the
        # heading the robot has rather than name a new one to turn to.
        if current is not None:
            return current

        return self._optional_float(map_theta_deg)

    async def _send_node_goto(self, node: Any) -> Optional[str]:
        """Transmit a plain goto to a node; no dock or charge branches.

        Split out of _send_node_motion so the move-segment fallback can ask for
        a goto and get only a goto. _send_node_motion's charge-in-place and
        UmDock branches key off _is_dock_motion_node, which is NON-directional,
        and a mode="move" rule may legitimately target a charger (see the
        MotionRule docstring in config/config.py) — routing the fallback through
        them could dock the robot instead of driving it to the node.

        Returns a rejection reason, or None when the goto was accepted.
        """
        position = getattr(node, "node_position", None)
        if self._is_simulator() and position is not None:
            # The simulator has no onboard map/localization, so it cannot resolve
            # a bare node id to coordinates. Feed it the order's nodePosition and
            # adopt its mapId so reported pose/map follow the commanded route.
            if position.map_id:
                self._current_map_id = position.map_id
            print(
                f"[ORDER NODE GOTO_POSITION:SIM] seq={node.sequence_id} "
                f"id={node.node_id} x={position.x} y={position.y} "
                f"theta={position.theta} mapId={position.map_id}"
            )
            goto_node_position = getattr(self._vehicle, "goto_node_position", None)
            if callable(goto_node_position):
                await goto_node_position(
                    node.node_id, position.x, position.y, position.theta
                )
            else:
                await self._vehicle.goto_xyz(position.x, position.y, position.theta)
            return await self._await_goto_ack(node)

        get_path_point_pose = getattr(self._vehicle, "get_path_point_pose", None)
        path_point_pose = (
            get_path_point_pose(str(node.node_id))
            if callable(get_path_point_pose)
            else None
        )
        if path_point_pose is not None:
            x, y, map_theta = path_point_pose
            theta = self._node_goto_theta_deg(node, (x, y), map_theta)
            print(
                f"[ORDER NODE GOTO_PATH_POINT_POSE] seq={node.sequence_id} "
                f"id={node.node_id} x={x} y={y} theta={theta}"
            )
            await self._vehicle.goto_xyz(x, y, theta)
        else:
            print(f"[ORDER NODE GOTO_POINT] seq={node.sequence_id} id={node.node_id}")
            await self._vehicle.goto_point(node.node_id)
        return await self._await_goto_ack(node)

    async def _await_goto_ack(self, node: Any) -> Optional[str]:
        """Wait for the robot's UmGoto response; return a rejection reason or None.

        The goto itself was already transmitted by the caller. Two cases skip
        the ack check entirely and return None:

        - Simulator mode (``_is_simulator()``): ``SimulatedJIBOT`` inherits a
          real TCP ``wait_for_response`` from ``JIBOT``, but the simulator never
          emits a UmGoto ack frame over that socket. Awaiting it would hang or
          falsely reject the goto, so simulator runs are skipped here rather than
          relying on the ``callable(...)`` guard below (which would NOT skip them).
        - Non-JIBOT vehicles lacking ``wait_for_response``: the ``callable(...)``
          check handles this case and returns None without raising an error.
        """
        if self._is_simulator():
            return None
        wait_for_response = getattr(self._vehicle, "wait_for_response", None)
        if not callable(wait_for_response):
            return None
        response = await wait_for_response(
            command="UmGoto", accept_errors=True, timeout=3.0
        )
        if response is None:
            # urobot's command manifest declares UmGoto as ret:none — it is
            # fire-and-forget and never emits a UmGoto ack frame, so a silent
            # (timeout) response is NOT a rejection. Acceptance shows up only in
            # streamed telemetry (mode=MRosGoto / status="nrunto <node>"), and
            # arrival is verified downstream by _wait_until_node_position_reached().
            # Only an explicit error frame (captured via accept_errors=True) is a
            # real rejection.
            return None
        return self._jibot_command_rejection_reason("UmGoto", response)

    async def _ensure_not_charging_before_order_motion(self, node: Any) -> Optional[str]:
        """Leave charging mode before dispatching order motion, if needed."""
        if not self._is_vehicle_charging():
            return None
        node_id = str(getattr(node, "node_id", ""))
        print(f"[ORDER NODE STOP_CHARGING] id={node_id} before motion")
        ok, desc = await self._run_stop_charging()
        print(f"[ORDER NODE STOP_CHARGING] id={node_id} ok={ok} ({desc})")
        if not ok:
            return desc

        # Let the stop land before the motion does. UmStop and UmSchedulerThis
        # both replace the JIBOT scheduler task, and the firmware applies them
        # in its own time — on 2026-08-20 a move issued 1ms after UmStop reached
        # the robot first, the stop overwrote it, and the segment reported
        # travelled=0.0mm with no error on either side (10:49 and 12:51, both
        # 1_01CH→p39). There is no ack for UmSchedulerThis to wait on, so this
        # is a settle window rather than a handshake.
        # ponytail: fixed settle; if a dropped move still shows up, re-issue the
        # relative move once on travelled≈0 instead of lengthening this.
        settle_sec = float(
            getattr(self.config.dock, "stop_charging_motion_settle_sec", 1.0)
        )
        if settle_sec > 0:
            print(f"[ORDER NODE STOP_CHARGING] id={node_id} settle {settle_sec:g}s before motion")
            await asyncio.sleep(settle_sec)
        return None

    def _is_motor_power_off(self) -> bool:
        """True only when the polled motor flag explicitly reports the motor off.

        ``None`` means UmGetMotorState has not answered yet: unknown, not off.
        The guard must never command a motor it has no reading for, so anything
        that is not a recognisable "off" reads as on — an empty string included,
        since that is a missing value, not a powered-down motor.

        The firmware sends a JSON bool today; ints and strings are accepted too
        because this decides whether to command a motor and a type change in the
        UmGetMotorState payload must not silently turn the guard off.
        """
        flag = getattr(self._vehicle, "_motor_flag", None)
        if flag is None:
            return False
        if isinstance(flag, str):
            return flag.strip().lower() in {"0", "false", "off"}
        return not bool(flag)

    async def _ensure_motor_enabled_before_order_motion(
        self, node: Any
    ) -> Optional[str]:
        """Power the drive motor before dispatching order motion, if needed.

        Same contract as _ensure_not_charging_before_order_motion: ``None`` to
        proceed, a reason string to reject the step.

        Without this the adapter transmits UmGoto/UmDock to a robot whose drive
        motor is off. The firmware accepts the command and the robot buzzes
        without moving; arming the motor by hand afterwards re-dispatches
        nothing, so the order stands still until someone cancels it (observed on
        192.168.101.61 at 2026-08-17 17:02 — UmDock sent at motor=False, motor
        switched on at 17:03:01, robot still Stopped at 17:05:33).

        UmSetMotor is fire-and-forget, so the flag is polled until the robot
        confirms the motor is live rather than assuming the command took.
        """
        if not bool(getattr(self.config.settings, "order_motor_auto_enable", True)):
            return None
        if self._vehicle is None or not self._is_motor_power_off():
            return None

        node_id = str(getattr(node, "node_id", ""))

        # A latched safety stop is a human decision; re-arming it to satisfy an
        # order would undo it silently. Only a plain manual motor-off is ours to
        # recover. Stale or absent /jrobot_status yields None (unknown) and falls
        # through — a firmware that refuses to arm still lands on the timeout
        # below, so the order fails with a reason either way.
        stop_reason = self._derive_jibot_stop_reason()
        if stop_reason in ("EMERGENCY", "PROTECTIVE_STOP", "BUMPER", "MOTOR_FAULT"):
            print(f"[ORDER NODE MOTOR BLOCKED] id={node_id} reason={stop_reason}")
            return (
                f"drive motor is off and latched by {stop_reason}; not "
                f"auto-enabled for '{node_id}'"
            )

        enable_motor = getattr(self._vehicle, "enable_motor", None)
        if not callable(enable_motor):
            return None

        print(f"[ORDER NODE MOTOR ENABLE] id={node_id} before motion")
        try:
            await enable_motor()
        except Exception as exc:  # transport/send failure
            print(f"[ORDER NODE MOTOR ENABLE FAILED] id={node_id}: {exc}")
            return f"enabling the drive motor for '{node_id}' failed: {exc}"

        timeout = float(
            getattr(self.config.settings, "order_motor_enable_timeout_sec", 5.0)
        )
        poll_sec = float(
            getattr(self.config.settings, "node_position_poll_interval_sec", 0.2)
        )
        deadline = time.monotonic() + timeout
        while self._is_motor_power_off():
            if time.monotonic() >= deadline:
                print(
                    f"[ORDER NODE MOTOR ENABLE TIMEOUT] id={node_id} "
                    f"timeout={timeout}s"
                )
                return (
                    f"drive motor did not power on within {timeout}s of "
                    f"UmSetMotor; motion to '{node_id}' not dispatched"
                )
            await asyncio.sleep(poll_sec)

        print(f"[ORDER NODE MOTOR ENABLED] id={node_id}")
        return None

    def _is_vehicle_charging(self) -> bool:
        if self._vehicle is None:
            return False

        if bool(getattr(self._vehicle, "_charging", False)):
            return True

        charging_status = str(
            getattr(self.config.jibot_status, "charging", "") or ""
        ).strip().lower()
        vehicle_status = str(getattr(self._vehicle, "_status", "") or "").strip().lower()
        return bool(charging_status) and (
            vehicle_status == charging_status
            or vehicle_status.startswith(f"{charging_status}#")
        )

    async def _wait_until_docking_complete(
        self,
        node: Any,
        poll_sec: Optional[float] = None,
    ) -> None:
        """Block until JIBOT reports that UmDock reached a charger/dock.

        Completion is keyed on charging state ALONE, by design — do not add a
        standstill/edge-trigger guard here:
        - JIBOT reports a single _status field, so "charging" and "stopped" are
          mutually exclusive; requiring both (charging AND _is_jibot_stopped)
          would deadlock (see _is_jibot_stopped / _is_vehicle_charging).
        - UmDock latches onto the nearest onboard dock, so the FMS approach path
          (the nodes before this one) is what guarantees the right charger; the
          wait only confirms a dock was reached, not which one.
        """
        if poll_sec is None:
            poll_sec = float(getattr(self.config.dock, "dock_wait_poll_interval_sec", 0.2))
        print(f"[ORDER NODE DOCK WAIT] id={node.node_id}")
        while True:
            if self._is_vehicle_charging():
                print(f"[ORDER NODE DOCKED] id={node.node_id}")
                return
            await asyncio.sleep(poll_sec)

    async def _stop_charging_after_dock_work(self, node_id: str) -> None:
        if self._vehicle is None:
            raise RuntimeError("JIBOT vehicle is not initialized")

        stop = getattr(self._vehicle, "um_stop", None)
        if not callable(stop):
            raise RuntimeError(
                "JIBOT API does not provide um_stop for dock-work charging stop"
            )

        # JIBOT ignores a single UmStop when stopping charging; repeat the
        # command with a gap so the robot actually stops (firmware bug).
        repeat = max(1, int(getattr(self.config.dock, "stop_charging_repeat_count", 2)))
        gap_sec = float(getattr(self.config.dock, "stop_charging_repeat_gap_sec", 3.0))
        for attempt in range(repeat):
            if attempt > 0:
                if gap_sec > 0:
                    await asyncio.sleep(gap_sec)
                # The repeat exists for a firmware bug where one UmStop does not
                # take. Once telemetry says charging ended it already took, and
                # sending another only re-stops an idle robot — which is exactly
                # what killed the following move on 2026-08-20.
                if not self._is_vehicle_charging():
                    print(
                        f"[ORDER NODE DOCK STOP_CHARGING] id={node_id} "
                        f"already stopped — skip UmStop ({attempt + 1}/{repeat})"
                    )
                    return
            print(
                f"[ORDER NODE DOCK STOP_CHARGING] id={node_id} command=UmStop "
                f"({attempt + 1}/{repeat})"
            )
            result = stop()
            if asyncio.iscoroutine(result):
                await result

    def _resolve_node_target(self, node: Any) -> Optional[Tuple[float, float, float]]:
        """(x, y, allowedDeviationXY) of a node, or None when unknown.

        Coordinates come from the order's nodePosition or, when missing, from
        the robot map. Without a deviation from the order, the configured
        default reach deviation is used.
        """
        xy = self._node_xy(node)
        if xy is None:
            return None

        deviation = 0.0
        position = getattr(node, "node_position", None)
        if position is not None:
            deviation = self._get_allowed_deviation_xy(
                getattr(position, "allowed_deviation_xy", None)
            )
        if deviation <= 0.0:
            deviation = float(
                getattr(self.config.settings, "default_node_deviation_xy", 10.0)
            )
        return xy[0], xy[1], deviation

    def _pose_in_reach_zone(
        self, vx: float, vy: float, tx: float, ty: float, deviation: float
    ) -> bool:
        """True when (vx, vy) is inside the reach zone around (tx, ty)."""
        zone_shape = str(
            getattr(self.config.settings, "reach_zone_shape", "square")
        ).strip().lower()
        if zone_shape == "circle":
            return math.hypot(vx - tx, vy - ty) <= deviation
        return abs(vx - tx) <= deviation and abs(vy - ty) <= deviation

    async def _wait_until_node_position_reached(
        self,
        node: Any,
        target: Tuple[float, float, float],
        poll_sec: float = None,
    ) -> None:
        """Block until the robot pose enters the node's reach zone.

        The goto command only starts the motion, so node completion must be
        tied to the actual pose. The wait has no timeout by design: a robot
        blocked by an obstacle (brake) legitimately waits here, and cancelOrder
        or a new order cancels the worker task.

        Waiting alone is not enough, though: manual (joystick) driving replaces
        the running JIBOT goto task, so the robot ends up standing still with
        nothing driving it to the node. That stall is detected and the goto is
        re-sent, up to node_goto_retry_limit times, before the node is escalated
        to a FATAL error for the FMS to act on.
        """
        if poll_sec is None:
            poll_sec = getattr(self.config.settings, "node_position_poll_interval_sec", 0.2)
        target_x, target_y, deviation_xy = target

        zone_shape = str(
            getattr(self.config.settings, "reach_zone_shape", "square")
        ).strip().lower()
        if zone_shape not in {"square", "circle"}:
            zone_shape = "square"
        effective_deviation = self._effective_reach_deviation_xy(deviation_xy)

        print(
            f"[ORDER NODE WAIT] id={node.node_id} target=({target_x}, {target_y}) "
            f"zone={zone_shape} radius={effective_deviation}"
        )
        # Re-sending motion is only correct on the plain-goto path, where
        # _active_goto_node is the node the order worker dispatched a goto for.
        # A mode="move" segment also waits here, but it was driven by a relative
        # move, so a goto would be the wrong command to repeat.
        goto_recoverable = self._active_goto_node is node
        # 스톨 복구 정책은 통과 판정(_wait_until_node_passed)과 공유한다.
        guard = _NodeMotionStallGuard(
            self, node, target_x, target_y, effective_deviation, goto_recoverable
        )
        while True:
            vx = self._optional_float(getattr(self._vehicle, "_x", None))
            vy = self._optional_float(getattr(self._vehicle, "_y", None))
            if vx is not None and vy is not None:
                if self._pose_in_reach_zone(
                    vx, vy, target_x, target_y, effective_deviation
                ):
                    self._set_jibot_arrival_signal_mismatch_warning(
                        node,
                        target_x,
                        target_y,
                        vx,
                        vy,
                        effective_deviation,
                    )
                    print(
                        f"[ORDER NODE REACHED] id={node.node_id} "
                        f"pos=({vx:.1f}, {vy:.1f})"
                    )
                    return
                await guard.observe(vx, vy)
            await asyncio.sleep(poll_sec)

    async def _wait_until_node_passed(self, node: Any, target: Any) -> bool:
        """코얼레싱 중간 노드를 '지나갔다'고 볼 때까지 기다린다.

        점 판정("지금 pose 가 반경 안인가")은 못 쓴다. 2026-08-26 192.168.101.50:7274
        실측에서 직선 통과 속도가 1002 mm/s 였고 node_position_poll_interval_sec 이
        0.2 라 표본 간 이동이 약 200mm 다. 도착존 실효값 ±40mm 는 두 표본 사이로
        통째로 지나간다. 그래서 직전 표본과 현재 표본을 잇는 선분으로 판정한다.

        도착 판정과 같은 스톨 복구를 건다. 여기는 오히려 더 필요하다 —
        waypoint_pass_radius_mm 기본값 0.0 이면 반경이 도착존(±40mm)까지 좁아지는데,
        spec §3.2 는 코너에서 구간을 끊지 않으므로 코너를 크게 도는 로봇은 그 반경에
        영영 안 들어올 수 있다.

        스톨 가드가 포기(gave_up)하면 대기를 끝내고 False 를 반환한다. FATAL
        JIBOT_NODE_UNREACHED 는 가드가 이미 발행했으므로 여기서 새 에러 타입을
        만들지 않는다 — 호출자(_process_v3_node_step)가 이 False 를 다른 노드
        스텝 실패들과 같은 방식으로 처리해(스텝 requeue 후 워커 정지) FMS 의
        cancelOrder/새 오더를 기다리게 한다. while True 로 계속 돌면(과거 구현)
        가드가 이미 포기를 선언한 뒤에도 워커가 취소 신호 없이 영원히 매달린다.

        반환값: 정상 통과 True, 스톨 가드 포기 False.
        """
        poll_sec = float(
            getattr(self.config.settings, "node_position_poll_interval_sec", 0.2)
        )
        target_x, target_y, deviation_xy = target
        # waypoint_pass_radius_mm 기본값 0.0 은 "따로 안 준다"는 뜻이라
        # 정지 판정이 쓰는 실효 도착존과 같은 값으로 떨어진다.
        radius = float(
            getattr(self.config.settings, "waypoint_pass_radius_mm", 0.0)
        ) or self._effective_reach_deviation_xy(deviation_xy)
        node_xy = (float(target_x), float(target_y))
        # 폴링 값 자체에서 유도한다(새 설정 키를 만들지 않는다). 이 값보다 긴
        # 공백 뒤에는 prev_xy 를 버려 다음 판정을 점 판정으로 되돌린다 —
        # 안전한 쪽은 통과를 "놓치는" 쪽이다(뒤에서 스톨 가드가 잡는다).
        stale_gap_sec = poll_sec * self._PASS_WAIT_STALE_GAP_POLLS

        print(
            f"[ORDER NODE PASS WAIT] id={node.node_id} target={node_xy} radius={radius}"
        )
        goto_recoverable = self._active_goto_node is node
        guard = _NodeMotionStallGuard(
            self, node, float(target_x), float(target_y), radius, goto_recoverable
        )
        prev_xy: Optional[Tuple[float, float]] = None
        prev_xy_at: Optional[float] = None
        while True:
            pose = self._vehicle_xy()
            if pose is not None:
                now = time.monotonic()
                if (
                    prev_xy is not None
                    and prev_xy_at is not None
                    and (now - prev_xy_at) > stale_gap_sec
                ):
                    # pose dropout 이 길었다 — 직전 앵커와 지금 표본을 잇는
                    # 선분은 로봇이 실제로 지나지 않았을 수도 있는 공백을
                    # 통째로 가로지른다. 보간을 접고 점 판정으로 되돌린다.
                    print(
                        f"[ORDER NODE PASS STALE] id={node.node_id} "
                        f"gap={now - prev_xy_at:.2f}s > {stale_gap_sec:.2f}s "
                        "— 보간 앵커 리셋"
                    )
                    prev_xy = None
                if segment_passes_within(node_xy, prev_xy, pose, radius):
                    print(f"[ORDER NODE PASSED] id={node.node_id} at={pose}")
                    return True
                prev_xy = pose
                prev_xy_at = now
                await guard.observe(pose[0], pose[1])
                if guard.gave_up:
                    print(
                        f"[ORDER NODE PASS GIVE UP] id={node.node_id} "
                        "— 스톨 가드 포기, 스텝을 실패 처리한다"
                    )
                    return False
            await asyncio.sleep(poll_sec)

    async def _wait_for_vehicle_pose(
        self, timeout_sec: float
    ) -> Optional[Tuple[float, float]]:
        """Poll until the vehicle reports a usable (x, y), or give up.

        A relative move's completion is measured from its origin, so a missing
        pose makes the judgment meaningless. Callers fail the step rather than
        dispatch motion they cannot monitor.
        """
        poll_sec = getattr(
            self.config.settings, "node_position_poll_interval_sec", 0.2
        )
        deadline = time.monotonic() + timeout_sec
        while True:
            vx = self._optional_float(getattr(self._vehicle, "_x", None))
            vy = self._optional_float(getattr(self._vehicle, "_y", None))
            if vx is not None and vy is not None:
                return (vx, vy)
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(poll_sec)

    async def _wait_until_move_settled(
        self,
        node: Any,
        target: Tuple[float, float, float],
        distance: float,
        start_pose: Tuple[float, float],
    ) -> Tuple[bool, float]:
        """Block until the relative move has finished, then report how it ended.

        A mode="move" rule's distance is deliberately shorter than the segment,
        so "the move finished" and "the robot arrived" are different questions.
        This answers the first from the commanded distance and the second from
        the pose, and never publishes UNREACHED: finishing short is normal and
        the caller drives the remainder with a goto.

        Returns:
            ``(reached, travelled)`` — ``reached`` is True when the settled pose
            is inside the to-node reach zone, False when the move finished
            outside it. ``travelled`` is the displacement in mm from
            ``start_pose`` at that moment. The caller needs it because a move
            that never left its origin must NOT fall back to a goto: the segment
            exists precisely because the planner cannot route out of where the
            robot is standing (a charger bay), so a goto from there is the route
            the design already ruled out. See _run_move_segment.
        """
        settings = self.config.settings
        poll_sec = getattr(settings, "node_position_poll_interval_sec", 0.2)
        start_timeout = float(getattr(settings, "move_start_timeout_sec", 10.0))
        min_travel = float(getattr(settings, "move_started_min_travel_mm", 50.0))
        travel_ratio = float(getattr(settings, "move_complete_travel_ratio", 0.9))
        stall_timeout = float(getattr(settings, "move_stall_timeout_sec", 5.0))

        target_x, target_y, deviation_xy = target
        effective_deviation = self._effective_reach_deviation_xy(deviation_xy)
        target_travel = abs(float(distance))
        start_x, start_y = start_pose

        print(
            f"[ORDER NODE MOVE WAIT] id={node.node_id} "
            f"target=({target_x}, {target_y}) radius={effective_deviation} "
            f"travel={target_travel}"
        )

        started = False
        start_budget = 0.0     # accrues only while unpaused, with a known pose
        stopped_since = None   # monotonic stamp of the CURRENT continuous stop
        last_tick = time.monotonic()

        while True:
            now = time.monotonic()
            tick = now - last_tick
            last_tick = now

            # Someone else owns the motion; freeze rather than judge. startPause
            # stops the robot on purpose, and manual/teleop driving discards the
            # running move outright — in both cases the robot reads "stopped"
            # while nothing of ours is driving it, so judging would call it a
            # stall and fire the fallback goto. Issuing an autonomous goto with a
            # hand on the joystick is the one outcome to never allow (mirrors
            # _wait_until_node_position_reached's stall guard: never fight the
            # joystick). _manual_control_active covers jogs the adapter itself
            # dispatched, which move the robot before JIBOT's polled _mode
            # catches up.
            if (
                self._motion_paused
                or self._is_jibot_manual_drive()
                or self._manual_control_active
            ):
                stopped_since = None
                await asyncio.sleep(poll_sec)
                continue

            vx = self._optional_float(getattr(self._vehicle, "_x", None))
            vy = self._optional_float(getattr(self._vehicle, "_y", None))
            if vx is None or vy is None:
                # An unknown pose must not advance the stall clock either: a
                # telemetry dropout that outlasts move_stall_timeout_sec would
                # otherwise settle the very first sample after it recovers,
                # judged against a stopped_since timestamp from before the gap.
                stopped_since = None
                await asyncio.sleep(poll_sec)
                continue

            start_budget += tick
            travelled = math.hypot(vx - start_x, vy - start_y)
            full_travel = travelled >= target_travel * travel_ratio
            stopped = self._is_jibot_stopped()
            obstacle = self._is_jibot_obstacle_wait()

            if stopped and not obstacle:
                if stopped_since is None:
                    stopped_since = now
            else:
                stopped_since = None

            reason = None
            if full_travel and stopped and not obstacle:
                # Checked BEFORE the start gate on purpose: a robot that covered
                # its commanded distance and stopped is finished whether or not
                # any sample caught it moving. This is also what makes a zero or
                # sub-threshold distance settle promptly instead of waiting out
                # start_timeout.
                reason = "full-travel"
            elif not started:
                if not stopped or travelled >= min_travel:
                    started = True
                elif start_budget >= start_timeout:
                    reason = "not-started"
            elif stopped_since is not None and (now - stopped_since) >= stall_timeout:
                reason = "stall"

            if reason is not None:
                reached = self._pose_in_reach_zone(
                    vx, vy, target_x, target_y, effective_deviation
                )
                print(
                    f"[ORDER NODE MOVE SETTLED] id={node.node_id} reason={reason} "
                    f"pos=({vx:.1f}, {vy:.1f}) "
                    f"travelled={travelled:.1f}/{target_travel:.1f} "
                    f"reached={reached}"
                )
                return reached, travelled

            await asyncio.sleep(poll_sec)

    async def _resend_node_goto(self, node: Any) -> None:
        """JIBOT 주행 태스크가 사라진 노드에 plain goto 를 재전송한다.

        호출자는 둘이다 — 도착 판정(_wait_until_node_position_reached)의 스톨
        재시도(mode="move" 폴백 goto 도 _active_goto_node 경유로 여기서 함께
        커버된다, _run_move_segment 참고)와, 스톨 복구 정책을 공유하는 통과
        판정(_wait_until_node_passed, path_control="continuous" 의 코얼레싱
        중간 노드 전용)의 스톨 재시도다. 스톨 복구 정책이
        _NodeMotionStallGuard 로 빠지면서 둘 다 이 재시도에 닿게 됐다.

        _send_node_goto 를 쓰고 _send_node_motion 을 쓰지 않는 이유는 두
        호출자 모두에 대해 같다 — 후자의 _is_dock_work_node 분기는 방향성이
        없어서, `to` 가 dock-work 노드인 move 룰의 재시도에서 로봇을 그대로
        도킹시켜 버린다(나머지 구간을 마저 모는 게 아니라). 두 대기 모두 이
        분기를 이미 피해 있다 — _process_v3_node_step 은 dock-work 노드를
        _wait_until_docking_complete 로 보내고, _wait_until_node_position_reached
        와 _wait_until_node_passed 는 둘 다 그 분기 밖(else)에서만 불린다.
        move 룰이 붙은 노드는 _is_coalescing_breaker 가 무조건 구간 끝으로
        고정하므로 _wait_until_node_passed 로는 애초에 오지 않지만, 그 경로가
        존재하지 않는다는 사실 자체가 이 재시도의 안전 논거를 바꾸지 않는다 —
        어느 호출자도 dock 분기를 원한 적이 없다는 결론은 그대로다.

        오더 주행 전 두 전제조건(충전 중지·모터 활성화)은 매 dispatch 와
        똑같이 여기서도 먼저 돈다. _send_node_goto 는 추출된 goto 본체라 둘 다
        갖고 있지 않으므로(_send_node_motion 쪽이 갖고 있다), 이 호출 없이는
        충전기에 앉아 있는 로봇에 그대로 goto 를 쏘게 된다. 오더 도중 충전기에
        서 있는 경우는 가정이 아니다 — mode="move" 룰의 `to` 가 충전기일 수
        있다. 모터 가드는 여기서 더 중요하다 — 이 재시도가 오더 도중 꺼진
        모터를 되살리는 유일한 경로다.

        실패는 raise 하지 않고 로그만 남긴다. 워커는 이 노드에 머문 채이므로,
        다음 스톨에서 재시도가 다시 돌거나 예산이 다하면 에스컬레이션된다.
        """
        try:
            stop_reason = await self._ensure_not_charging_before_order_motion(node)
            if stop_reason is None:
                stop_reason = await self._ensure_motor_enabled_before_order_motion(
                    node
                )
            if stop_reason is not None:
                print(
                    f"[ORDER NODE GOTO RETRY BLOCKED] id={node.node_id} "
                    f"reason={stop_reason}"
                )
                return
            rejection_reason = await self._send_node_goto(node)
        except Exception as exc:  # transport/send failure
            print(f"[ORDER NODE GOTO RETRY FAILED] id={node.node_id}: {exc}")
            return
        if rejection_reason is not None:
            print(
                f"[ORDER NODE GOTO RETRY REJECTED] id={node.node_id} "
                f"reason={rejection_reason}"
            )

    async def _wait_until_dock_charge_or_timeout(self, node: Any) -> bool:
        """True when charging starts, False after fail_timeout_sec.

        The escape the original _wait_until_docking_complete lacks: a failed
        seat (no charge) returns False so the caller rejects the step instead of
        hanging in DOCKING forever.
        """
        timeout = self._dock_fail_timeout_sec(str(getattr(node, "node_id", "")))
        poll_sec = float(
            getattr(self.config.dock, "dock_wait_poll_interval_sec", 0.2)
        )
        # Time spent braked does not count: a brake is JIBOT stopped waiting for
        # something in its path to clear, which is the robot making legitimate
        # progress toward the charger, not a failed seat. Charging it as failure
        # time reports an obstacle on the approach as a broken charger (see the
        # 2026-08-19 11:02 dock: 52 of 82 samples in "nrunto 1_01CH#brake").
        # Mirrors the obstacle exclusion in _wait_until_node_position_reached
        # and _wait_until_move_settled, so all three waits read a brake alike.
        # One continuous brake is still bounded by the same budget: excluding
        # brake time must not turn a permanently blocked approach into an
        # infinite DOCKING hang, which is the exact failure the timeout exists
        # to prevent.
        obstacle_timeout = float(
            getattr(self.config.dock, "obstacle_timeout_sec", 300.0) or 0.0
        )
        budget = 0.0
        braked_since = None
        last_tick = time.monotonic()
        while budget < timeout:
            if self._is_vehicle_charging():
                return True
            now = time.monotonic()
            tick = now - last_tick
            last_tick = now
            if self._is_jibot_obstacle_wait():
                if braked_since is None:
                    braked_since = now
                    print(
                        f"[ORDER NODE DOCK BLOCKED] id={node.node_id} "
                        f"obstacle wait; dock timeout paused at "
                        f"{budget:.1f}/{timeout:.1f}s"
                    )
                elif obstacle_timeout > 0.0 and (now - braked_since) >= obstacle_timeout:
                    print(
                        f"[ORDER NODE DOCK BLOCKED OUT] id={node.node_id} "
                        f"obstacle held for {now - braked_since:.1f}s"
                    )
                    return False
            else:
                if braked_since is not None:
                    print(
                        f"[ORDER NODE DOCK CLEARED] id={node.node_id} "
                        f"obstacle cleared after {now - braked_since:.1f}s"
                    )
                    braked_since = None
                budget += tick
            await asyncio.sleep(poll_sec)
        return False

    def _finalize_v3_node_step(self, step: "OrderStep", node: Any) -> bool:
        """Shared node-completion bookkeeping; returns True (step done)."""
        self._last_node_id = node.node_id
        self._last_node_sequence_id = node.sequence_id
        self._finish_step_placeholder_action(step)
        if self.state is not None:
            self.state.last_node_id = self._last_node_id
            self.state.last_node_sequence_id = self._last_node_sequence_id
            removed_edges = self._clear_edge_states_up_to(int(node.sequence_id))
            if removed_edges:
                # _process_v3_edge_step 의 "[ORDER EDGE CLEAR]" 와 이름이 겹치면
                # grep 으로 두 이벤트를 구분할 수 없어 태그를 분리한다(리뷰 지적).
                print(
                    f"[ORDER EDGE RELEASE] node seq={node.sequence_id} "
                    f"id={node.node_id} removed={removed_edges}"
                )
        self.request_state_publish("node reached")
        remaining = sum(
            1
            for pending in list(self.order_queue._queue)  # asyncio.Queue 내부 deque
            if pending.kind == "node" and getattr(pending.item, "released", False)
        )
        self._set_new_base_request_from_queue(remaining_released=remaining)
        return True

    def _pose_at_map_node(self, node_id: str) -> bool:
        """True when the robot pose is already within node_id's map reach zone.

        Keeps the approach->UmDock maneuver directional: it runs only when the
        robot is ARRIVING at the charger from elsewhere. If the robot is already
        at the charger, the drive back out to the approach waypoint is skipped.
        """
        coords = self._map_nodes().get(node_id)
        if not coords:
            return False
        vx = self._optional_float(getattr(self._vehicle, "_x", None))
        vy = self._optional_float(getattr(self._vehicle, "_y", None))
        if vx is None or vy is None:
            return False
        deviation = self._effective_reach_deviation_xy(
            float(getattr(self.config.settings, "default_node_deviation_xy", 10.0))
        )
        return self._pose_in_reach_zone(vx, vy, coords[0], coords[1], deviation)

    async def _run_dock(self, node: Any) -> Optional[str]:
        """UmDock to seat into the charger from the current pose.

        Reached only via a directional dock-segment rule (_dock_segment_rule),
        which matches when the robot has just arrived from the rule's `from`
        (approach) node — so it is already positioned to seat and NO approach
        goto is issued. Owns per-phase error surfacing; returns a reason string
        on failure (error already published) or None on success.
        """
        node_id = str(node.node_id)

        # Already docked and charging -> the node is satisfied; do not move.
        if self._is_vehicle_charging():
            print(f"[ORDER NODE DOCK SKIP] id={node_id} already charging")
            return None

        # Seating into the charger is motion, so it needs a live motor like any
        # other dispatch. Checked after the already-charging skip above: that
        # branch moves nothing and must stay a no-op.
        motor_reason = await self._ensure_motor_enabled_before_order_motion(node)
        if motor_reason is not None:
            self._set_jibot_dock_fail_error(node, motor_reason)
            return motor_reason

        print(f"[ORDER NODE DOCK] id={node_id} command=UmDock")
        try:
            await self._vehicle.um_dock(**self._dock_approach_params(node_id))
        except Exception as exc:  # transport/send failure
            reason = f"UmDock send failed: {exc}"
            self._set_jibot_dock_fail_error(node, reason)
            return reason
        self._docking_started_node_ids.add(node_id)
        self._sync_docking_action_state()  # surface DOCKING immediately

        # wait for charging, or fail out on timeout (anti-hang).
        if not await self._wait_until_dock_charge_or_timeout(node):
            self._docking_started_node_ids.discard(node_id)
            timeout = self._dock_fail_timeout_sec(node_id)
            reason = f"dock failed: not charging within {timeout}s after UmDock"
            self._set_jibot_dock_fail_error(node, reason)
            return reason

        handover_reason, may_redock = await self._handover_dock_charge_to_relay(node)
        if handover_reason is None:
            return None

        # 인계 실패 상태 그대로 두면 로봇이 충전기 위에 앉아 아무것도 충전하지 않는다
        # (ModeCharge 는 이미 걷혔고 relay hold 도 놓은 뒤다). 이 변경 이전 동작인
        # "ModeCharge 가 충전을 쥐고 있는 상태"로 되돌리는 편이 항상 낫다 —
        # #brake 재접근 위험은 원래 있던 것이고, 방전은 새로 생긴 것이다.
        # 단, 관측이 없어서 실패한 경우(링크 침묵)에는 재도킹하지 않는다. 상태를 못 보는
        # 로봇에 모션 명령을 쏘는 셈이고, 애초에 충전이 안 붙었다는 근거도 없다.
        if may_redock and await self._redock_after_failed_handover(node):
            print(
                f"[ORDER NODE DOCK HANDOVER FALLBACK] id={node_id} "
                "re-docked; charging under ModeCharge again"
            )
            self._set_charge_handover_warning(node, handover_reason)
            return None

        self._docking_started_node_ids.discard(node_id)
        self._set_jibot_dock_fail_error(node, handover_reason)
        return handover_reason

    async def _redock_after_failed_handover(self, node: Any) -> bool:
        """인계 실패 후 UmDock 을 다시 걸어 충전을 되살린다. 성공하면 True.

        되돌리는 대상은 이 변경 이전의 정상 상태(ModeCharge 가 충전을 쥠)다. 실패
        경로에서 아무것도 안 하면 충전기 위에서 방전만 하는, 이전보다 나쁜 상태로
        끝난다.
        """
        node_id = str(getattr(node, "node_id", ""))
        print(f"[ORDER NODE DOCK HANDOVER FALLBACK] id={node_id} command=UmDock")
        try:
            await self._vehicle.um_dock(**self._dock_approach_params(node_id))
        except Exception as exc:  # transport/send failure
            print(
                f"[ORDER NODE DOCK HANDOVER FALLBACK FAILED] id={node_id} "
                f"UmDock send failed: {exc}"
            )
            return False
        return await self._wait_until_dock_charge_or_timeout(node)

    def _set_charge_handover_warning(self, node: Any, reason: str) -> None:
        """인계는 실패했지만 재도킹으로 충전은 살아난 상태를 WARNING 으로 남긴다.

        노드는 성공으로 끝나므로(로봇은 충전 중) FATAL 이 아니다. 다만 이 로봇은
        다시 ModeCharge 에 남아 있어 충전이 끊기면 `#brake` 재접근 루프에 빠질 수
        있으므로, 조용히 넘어가면 안 된다.
        """
        if self.state is None:
            return
        self.state.errors = [
            error
            for error in self.state.errors
            if not (
                getattr(error, "error_type", None) == ErrorType.UNKNOWN_ERROR
                and any(
                    getattr(ref, "reference_key", None) == "actionType"
                    and getattr(ref, "reference_value", None) == "chargeHandover"
                    for ref in (getattr(error, "error_references", []) or [])
                )
            )
        ]
        self.state.errors.append(
            Error(
                error_type=ErrorType.UNKNOWN_ERROR,
                error_level=ErrorLevel.WARNING,
                error_references=[
                    ErrorReference("actionType", "chargeHandover"),
                    ErrorReference("nodeId", str(getattr(node, "node_id", "") or "")),
                    ErrorReference("reason", reason),
                    ErrorReference("fallback", "re-docked under ModeCharge"),
                ],
                error_description=(
                    "Charge was not handed over to the relay hold; the robot is "
                    "charging under JIBOT ModeCharge again. It can fall back into "
                    "the #brake re-approach loop when charging drops. Check the "
                    "ROS /jcmd charge relay (charge_circuit) on this host."
                ),
            )
        )
        self.request_state_publish("charge handover fallback")

    async def _handover_dock_charge_to_relay(
        self, node: Any
    ) -> Tuple[Optional[str], bool]:
        """충전을 relay hold 로 넘기고 UmStop 으로 ModeCharge 를 빠져나온다.

        왜: 도킹이 끝나도 JIBOT 은 ModeCharge 에 남는다. 그 상태에서 충전이 끊기면
        (만충으로 릴레이가 떨어지는 경우 포함) JIBOT 이 스스로 접근 단계로 되돌아가
        `nrunto N` 을 다시 시도하는데, 이미 충전기에 밀착해 있어 전방이 막혀
        `#brake` 로 고착된다. 2026-08-25 HN-SH6-TR-002 실측: 05:04 까지
        `ModeCharge status=charging` 인데 battery_current -1.4A(방전, 배터리 99%),
        05:06:02 에 `nrunto 1#brake` 로 넘어가 06:20:13 까지 74분 연속
        (873표본). 그 동안 어댑터가 보낸 비폴링 명령은 0건이었다 — 어댑터가 아니라
        JIBOT 자신의 재접근 루프다.

        relay hold(/jcmd cmd:3=1 을 계속 assert)는 ModeCharge 없이도 충전을 유지하므로
        (docs/reference/jibot-charging-dock-bms.md), 충전을 그쪽으로 먼저 넘기고 나서
        스케줄러 task 를 비우면 재접근 루프가 생길 자리 자체가 없어진다.

        순서가 중요하다: hold 를 **먼저** 잡고 UmStop 을 보낸다. 반대로 하면 UmStop 의
        OpenChargingCircuit 과 hold 사이에 충전이 비는 구간이 생긴다.

        ``(사유, 재도킹 폴백을 해도 되는가)`` 를 돌려준다. 성공이면 사유가 ``None``.
        실패 시에는 hold 를 놓아 릴레이를 원래대로 되돌린다 — 붙잡은 채 두면 어느 쪽도
        충전을 책임지지 않는 상태가 된다.
        """
        node_id = str(getattr(node, "node_id", ""))
        if not bool(
            getattr(self.config.dock, "handover_charge_to_relay_after_dock", True)
        ):
            return None, False
        # relay 를 쥘 수 없으면 UmStop 은 충전을 끊기만 한다. 인계 자체를 하지 않는다.
        if not bool(getattr(self.config.charge_circuit, "enabled", False)):
            print(
                f"[ORDER NODE DOCK HANDOVER SKIP] id={node_id} "
                "charge_circuit.enabled=false; staying in ModeCharge"
            )
            return None, False

        settle_sec = float(
            getattr(self.config.dock, "handover_hold_settle_sec", 3.0)
        )
        print(
            f"[ORDER NODE DOCK HANDOVER] id={node_id} relay hold "
            f"(+{settle_sec:g}s settle) -> UmStop"
        )
        self._charge_circuit.start_hold()
        self._charge_in_place_active = True
        # start_hold() 는 `rostopic pub` 를 spawn 만 하고 즉시 돌아온다. 등록 전에
        # UmStop 을 보내면 OpenChargingCircuit 이 먼저 이겨 릴레이가 열린 채 남는다.
        if settle_sec > 0:
            await asyncio.sleep(settle_sec)

        since = time.monotonic()
        try:
            await self._send_leave_mode_charge_stops(node_id)
        except Exception as exc:  # transport/send failure
            release_charge_in_place(self)
            reason = f"charge relay handover failed: UmStop send failed: {exc}"
            print(f"[ORDER NODE DOCK HANDOVER FAILED] id={node_id} {reason}")
            # UmStop 이 나가지도 않았으므로 ModeCharge 는 그대로일 수 있다. 재도킹으로
            # 충전 상태를 확정해 두는 편이 낫다.
            return reason, True

        held, saw_fresh = await self._wait_until_charge_relay_holds(since)
        if held:
            print(
                f"[ORDER NODE DOCK HANDOVER DONE] id={node_id} "
                "charging on relay hold; left ModeCharge"
            )
            return None, False

        release_charge_in_place(self)
        timeout = float(
            getattr(self.config.dock, "handover_verify_timeout_sec", 30.0)
        )
        if saw_fresh:
            reason = (
                f"charge relay handover failed: telemetry reported not charging "
                f"within {timeout}s of leaving ModeCharge"
            )
        else:
            reason = (
                f"charge relay handover unverified: no JIBOT telemetry arrived in "
                f"{timeout}s after leaving ModeCharge; charge state unknown"
            )
        print(f"[ORDER NODE DOCK HANDOVER FAILED] id={node_id} {reason}")
        return reason, saw_fresh

    async def _send_leave_mode_charge_stops(self, node_id: str) -> None:
        """ModeCharge 를 걷어내기 위한 UmStop 을 정해진 횟수만큼 그대로 보낸다.

        _stop_charging_after_dock_work 를 재사용하지 않는 이유: 그쪽은 "충전을 멈추려는
        것이고 이미 멈췄으면 더 보내지 말자"는 가드(`already stopped — skip`)를 갖고
        있는데, 여기서는 전제가 뒤집힌다. 릴레이 경쟁에서 hold 가 밀려 충전이 꺼져
        보이면 그 가드가 두 번째 UmStop 을 건너뛰고, 결과적으로 ModeCharge 에 남은 채
        hold 만 싸우는 상태가 된다 — 정확히 피하려는 상태다.
        """
        if self._vehicle is None:
            raise RuntimeError("JIBOT vehicle is not initialized")
        stop = getattr(self._vehicle, "um_stop", None)
        if not callable(stop):
            raise RuntimeError("JIBOT API does not provide um_stop")

        repeat = max(1, int(getattr(self.config.dock, "stop_charging_repeat_count", 2)))
        gap_sec = float(getattr(self.config.dock, "stop_charging_repeat_gap_sec", 3.0))
        for attempt in range(repeat):
            if attempt > 0 and gap_sec > 0:
                await asyncio.sleep(gap_sec)
            print(
                f"[ORDER NODE DOCK HANDOVER] id={node_id} command=UmStop "
                f"({attempt + 1}/{repeat})"
            )
            result = stop()
            if asyncio.iscoroutine(result):
                await result

    def _last_rx_monotonic(self) -> Optional[float]:
        """마지막 JIBOT 프레임이 도착한 monotonic 시각. 알 수 없으면 None.

        `_charging` 은 상태 스냅샷이 들어올 때만 갱신되는 캐시값이라, 벽시계로
        기다린 것만으로는 '새 표본을 봤다'고 말할 수 없다. 실측 스냅샷 주기는 약
        5초다(2026-08-25 UmGetLocState 06:30:10/:16/:21/:26/:31).
        """
        seconds_since = getattr(self._vehicle, "seconds_since_last_rx", None)
        if not callable(seconds_since):
            return None
        try:
            return time.monotonic() - float(seconds_since())
        except (TypeError, ValueError):
            return None

    async def _wait_until_charge_relay_holds(
        self, since: float
    ) -> Tuple[bool, bool]:
        """UmStop 이후 **새로 도착한** 표본만으로 충전 유지 여부를 판정한다.

        캐시된 값으로 판정하면 UmStop 직후 400ms 만에 '유지됨'이라고 답하게 되는데,
        그 값은 릴레이가 열리기 전에 찍힌 표본이다. 그러면 릴레이가 열린 채
        ModeCharge 만 빠져나온 상태를 성공으로 보고하고, SOC 가 떨어질 때까지 아무도
        모른다 — 고치려던 버그보다 나쁘다.

        판정: `since` 이후 도착한 표본이 연속 handover_verify_samples 개 충전을
        보고하면 성공. 창을 다 쓰도록 못 채우면 실패. 연속을 요구하는 이유는
        JModeCharge 이탈 순간의 깜빡임을 실패로 읽지 않기 위해서다.

        `seconds_since_last_rx` 가 없는 vehicle(테스트 더블 등)에서는 폴링 주기를
        표본 주기로 간주한다.

        ``(유지됨, 새 표본을 본 적 있음)`` 을 돌려준다. 둘째 값이 필요한 이유: 링크가
        조용해져서 확인을 못 한 것과, 표본이 실제로 "충전 아님"이라고 말한 것은 다른
        사실이다. 전자에서 재도킹을 걸면 상태를 못 보는 로봇에 모션을 쏘게 된다.
        """
        timeout = float(
            getattr(self.config.dock, "handover_verify_timeout_sec", 30.0)
        )
        required = max(1, int(getattr(self.config.dock, "handover_verify_samples", 2)))
        poll_sec = float(
            getattr(self.config.dock, "charging_start_poll_interval_sec", 0.2)
        )
        deadline = time.monotonic() + timeout
        last_seen: Optional[float] = None
        streak = 0
        saw_fresh = False
        while time.monotonic() < deadline:
            rx_at = self._last_rx_monotonic()
            is_fresh = (
                rx_at is None                      # 알 수 없으면 폴링을 표본으로 본다
                or (rx_at > since and (last_seen is None or rx_at > last_seen))
            )
            if is_fresh:
                saw_fresh = True
                if rx_at is not None:
                    last_seen = rx_at
                if self._is_vehicle_charging():
                    streak += 1
                    if streak >= required:
                        return True, True
                else:
                    streak = 0
            await asyncio.sleep(poll_sec)
        return False, saw_fresh

    def _remaining_move_distance(self, segment: Any) -> float:
        """Signed distance a paused relative move still owes; 0.0 when none.

        Keeps the commanded sign so the resumed move continues in the same
        direction. Without a pose the remainder is unknowable and re-issuing
        the full distance would overshoot, so return 0.0 and let the settle
        wait resolve the segment with its goto fallback.
        """
        vx = self._optional_float(getattr(self._vehicle, "_x", None))
        vy = self._optional_float(getattr(self._vehicle, "_y", None))
        if vx is None or vy is None:
            print("[STOP PAUSE] no pose; not re-issuing the relative move")
            return 0.0
        start_x, start_y = segment.start_pose
        travelled = math.hypot(vx - start_x, vy - start_y)
        remaining = abs(float(segment.distance)) - travelled
        if remaining <= 1.0:   # sub-millimetre remainder is not worth a command
            return 0.0
        return math.copysign(remaining, float(segment.distance))

    async def _run_move_segment(self, node: Any, rule: Any) -> Optional[str]:
        """Traverse a from->to segment with a relative move, then a goto for
        whatever the move did not cover.

        The rule's distance is deliberately shorter than the segment: it only
        has to clear the constrained part (backing out of a charger, say), and
        an ordinary goto finishes the approach. Returns a rejection reason on
        failure, else None.
        """
        distance = getattr(rule, "distance", None)
        if distance is None:
            return f"move rule for node '{node.node_id}' has no distance"
        # Validate the arrival target BEFORE dispatching any motion.
        target = self._resolve_node_target(node)
        if target is None:
            return f"move target node '{node.node_id}' has no coordinates"
        mc = self.config.manual_control
        rule_speed = getattr(rule, "speed", None)
        speed = float(rule_speed) if rule_speed is not None else float(mc.default_move_speed)
        obs_avoid_dist = getattr(rule, "obs_avoid_dist", None)
        side_avoid_dist = getattr(rule, "side_avoid_dist", None)
        flag = getattr(rule, "flag", None)
        io = getattr(rule, "io", None)
        use_io = getattr(rule, "use_io", None)
        note = getattr(rule, "note", None)
        move_kwargs = {
            "obs_avoid_dist": (
                int(obs_avoid_dist) if obs_avoid_dist is not None
                else mc.move_obs_avoid_dist
            ),
            "side_avoid_dist": (
                int(side_avoid_dist) if side_avoid_dist is not None
                else mc.move_side_avoid_dist
            ),
            "flag": int(flag) if flag is not None else 1,
            "io": int(io) if io is not None else 1,
            "use_io": bool(use_io) if use_io is not None else False,
            "note": int(note) if note is not None else 1,
        }
        stop_reason = await self._ensure_not_charging_before_order_motion(node)
        if stop_reason is not None:
            return stop_reason

        motor_reason = await self._ensure_motor_enabled_before_order_motion(node)
        if motor_reason is not None:
            return motor_reason

        # Completion is measured from the origin, so refuse to move blind.
        start_timeout = float(
            getattr(self.config.settings, "move_start_timeout_sec", 10.0)
        )
        start_pose = await self._wait_for_vehicle_pose(start_timeout)
        if start_pose is None:
            return (
                f"move origin pose unavailable within {start_timeout}s; "
                f"relative move to '{node.node_id}' not dispatched"
            )

        print(
            f"[ORDER NODE MOVE] id={node.node_id} distance={distance} speed={speed} "
            f"obs_avoid_dist={move_kwargs['obs_avoid_dist']} "
            f"side_avoid_dist={move_kwargs['side_avoid_dist']}"
        )
        self._active_move_segment = SimpleNamespace(
            node=node,
            start_pose=start_pose,
            distance=float(distance),
            speed=speed,
            kwargs=move_kwargs,
        )
        try:
            try:
                await self._vehicle.move_distance(float(distance), speed, **move_kwargs)
            except Exception as exc:  # transport/send failure
                return f"move send failed: {exc}"
            reached, travelled = await self._wait_until_move_settled(
                node, target, float(distance), start_pose
            )
        finally:
            self._active_move_segment = None

        if reached:
            return None

        # The goto fallback only runs once the robot has demonstrably left its
        # origin. A move that never travelled (motor not enabled, command
        # dropped, stall at ~0mm) leaves the robot fully inside the constrained
        # part of the segment — the charger bay the rule exists to escape — and
        # a goto from there is exactly the route the design says the planner
        # cannot solve. Fail the node step instead of driving.
        # move_started_min_travel_mm is reused deliberately: it already means
        # "has this move demonstrably begun".
        min_travel = float(
            getattr(self.config.settings, "move_started_min_travel_mm", 50.0)
        )
        if travelled < min_travel:
            print(
                f"[ORDER NODE MOVE NOT STARTED] id={node.node_id} "
                f"travelled={travelled:.1f} < {min_travel:.1f} "
                f"distance={distance} — no goto fallback"
            )
            return (
                f"move to '{node.node_id}' did not start: travelled "
                f"{travelled:.1f}mm, below the {min_travel:.1f}mm "
                f"move_started_min_travel_mm threshold; no goto fallback from "
                f"the segment origin"
            )

        # The move ran its course and stopped short. Normal flow, not an error:
        # cover the remainder with a plain goto. _send_node_goto rather than
        # _send_node_motion because the latter branches into UmDock and
        # charge-in-place, and a move rule's `to` may be a charger.
        print(
            f"[ORDER NODE MOVE SHORT] id={node.node_id} "
            f"distance={distance} -> goto"
        )
        self._active_goto_node = node
        try:
            rejection = await self._send_node_goto(node)
            if rejection is not None:
                return rejection
            # 여기는 코얼레싱 게이트를 걸지 않는다. move 룰이 붙은 노드는
            # _is_coalescing_breaker 가 무조건 끊으므로 항상 구간의 끝이고,
            # 게이트를 붙여 봐야 늘 정지 경로로 떨어진다.
            await self._wait_until_node_position_reached(node, target)
            await self._settle_goto_arrival(node)
        finally:
            self._active_goto_node = None
        return None

    async def _process_v3_node_step(self, step: OrderStep) -> bool:
        node = step.item
        self._order_node_motion_seq += 1
        if self._vehicle is None:
            print(
                f"[ORDER NODE BLOCKED] seq={node.sequence_id} id={node.node_id} "
                "vehicle is not attached"
            )
            return False

        if self._dock_segment_rule(node.node_id) is not None:
            reason = await self._run_dock(node)
            if reason is not None:
                print(f"[ORDER NODE DOCK FAILED] id={node.node_id} reason={reason}")
                return False
            self._clear_jibot_goto_rejected_error()
            return self._finalize_v3_node_step(step, node)

        move_rule = self._move_motion_rule(node.node_id)
        if move_rule is not None:
            reason = await self._run_move_segment(node, move_rule)
            if reason is not None:
                self._set_jibot_goto_rejected_error(node, reason)
                print(f"[ORDER NODE MOVE FAILED] id={node.node_id} reason={reason}")
                return False
            self._clear_jibot_goto_rejected_error()
            return self._finalize_v3_node_step(step, node)

        rejection_reason = await self._send_node_motion(node)
        if rejection_reason is not None:
            self._set_jibot_goto_rejected_error(node, rejection_reason)
            print(
                f"[ORDER NODE GOTO REJECTED] id={node.node_id} "
                f"seq={node.sequence_id} command=UmGoto reason={rejection_reason}"
            )
            return False
        self._clear_jibot_goto_rejected_error()

        if self._is_dock_work_node(node.node_id):
            await self._wait_until_docking_complete(node)
            if bool(getattr(self.config.dock, "stop_charging_on_arrival", True)):
                try:
                    await self._stop_charging_after_dock_work(node.node_id)
                except Exception as exc:
                    self._set_jibot_dock_work_error(node, str(exc))
                    print(
                        f"[ORDER NODE DOCK STOP_CHARGING FAILED] "
                        f"id={node.node_id}: {exc}"
                    )
                    return False
        else:
            target = self._resolve_node_target(node)
            if target is None:
                # Without coordinates (no nodePosition and not on the robot map)
                # arrival cannot be verified; fall back to send-and-complete.
                print(
                    f"[ORDER NODE WARN] seq={node.sequence_id} id={node.node_id} "
                    "no coordinates known; completing on send without arrival check"
                )
            else:
                self._active_goto_node = node
                is_run_end = self._is_run_end(step)
                if not is_run_end:
                    # 구간 주행 중임을 표시한다. 중간 노드 통과가
                    # lastNodeSequenceId 를 올려도 워커가 자기 주행을 취소하지
                    # 못하게 하기 위함이다.
                    self._coalescing_run_step = step
                try:
                    if self._should_settle_at(step, is_run_end=is_run_end):
                        await self._wait_until_node_position_reached(node, target)
                        await self._settle_goto_arrival(node)
                    else:
                        # False = 스톨 가드가 포기했다(§Finding 1). 다른 노드
                        # 실패 경로(도크·move)와 같은 신호로 돌려 스텝을 실패
                        # 처리한다 — 워커가 requeue 후 정지하고, cancelOrder나
                        # 새 오더가 올 때까지 기다린다.
                        if not await self._wait_until_node_passed(node, target):
                            print(
                                f"[ORDER NODE PASS FAILED] id={node.node_id} "
                                "— 스톨 가드 포기로 스텝 실패"
                            )
                            return False
                finally:
                    self._active_goto_node = None
                    self._coalescing_run_step = None

        return self._finalize_v3_node_step(step, node)

    def _clear_edge_states_up_to(self, node_sequence_id: int) -> int:
        """노드 하나를 통과했을 때, 그 노드로 이어지던 엣지들의 edgeState 를 지운다.

        VDA5050 3.0 §6.6.2 는 엣지 이탈을 "그 엣지가 이어지는 다음 노드를 통과할 때"
        로 규정한다. 기존 구현은 엣지 스텝이 주행 없이 즉시 completed 되는 탓에
        사실상 엣지 **진입** 시점에 지우고 있었다. FMS 는 edgeStates 로 구간 점유를
        보므로, 로봇이 아직 그 구간 안에 있는데 점유를 놓아 버리는 창이 생긴다.
        노드마다 완전 정지하던 동안에는 창이 짧았을 뿐이고, 연속 주행이 이 창을 넓힌다.
        """
        if self.state is None:
            return 0
        before = len(self.state.edge_states)
        self.state.edge_states = [
            edge for edge in self.state.edge_states
            if edge.sequence_id >= node_sequence_id
        ]
        return before - len(self.state.edge_states)

    def _clear_v3_order_step(self, step: OrderStep) -> None:
        if self.state is None:
            return

        item = step.item
        item_id = getattr(item, "node_id", None) or getattr(item, "edge_id", "")
        before_nodes = len(self.state.node_states)
        before_edges = len(self.state.edge_states)

        if step.kind == "node":
            self.state.node_states = [
                node for node in self.state.node_states
                if node.sequence_id != step.sequence_id
            ]
            self._docking_started_node_ids.discard(str(item_id))
        # 엣지 step 의 edge_states 는 여기서 지우지 않는다. VDA5050 3.0 §6.6.2 는
        # "엣지 이탈 = 다음 노드 통과" 로 규정하므로, _finalize_v3_node_step 에서
        # _clear_edge_states_up_to 로 지운다. 엣지 스텝은 주행 없이 즉시 completed
        # 되는 구조라, 여기서 지우면 사실상 엣지 진입 시점 제거가 되어 버린다.

        after_nodes = len(self.state.node_states)
        after_edges = len(self.state.edge_states)
        cleared = before_nodes != after_nodes or before_edges != after_edges
        order_id = self.order.order_id if self.order is not None else self.state.order_id

        print(
            f"[ORDER STEP CLEAR] orderId={order_id} kind={step.kind} "
            f"seq={step.sequence_id} id={item_id} cleared={cleared} "
            f"nodes={before_nodes}->{after_nodes} "
            f"edges={before_edges}->{after_edges} "
            f"queued={self.order_queue.qsize()}"
        )

    def _find_action_state_for_step(
        self,
        step: OrderStep,
        action_id: str,
    ) -> Optional[ActionState]:
        if self.state is None:
            return None

        # VDA5050 requires actionIds to be unique across the order, so the id
        # the order carried is enough to find its state.
        for action_state in self.state.action_states:
            if action_state.action_id == action_id:
                return action_state
        return None

    def instant_actions_accept_procedure(self, instant_actions_request: InstantActions) -> None:
        """Process incoming instant actions request"""
        print("=" * 80)
        print("[VDA5050 v3.0 INSTANT ACTIONS RECEIVED]")
        print(
            f"headerId={instant_actions_request.header.header_id} "
            f"version={instant_actions_request.header.version} "
            f"serialNumber={instant_actions_request.header.serial_number} "
            f"actions={len(instant_actions_request.actions)}"
        )

        if self.state is not None:
            for action in instant_actions_request.actions:
                self.state.instant_action_states.append(
                    ActionState(
                        action_id=action.action_id,
                        action_status=ActionStatus.WAITING,
                        action_type=action.action_type,
                        action_description=action.action_descriptor,
                    )
                )

        for action in instant_actions_request.actions:
            print(
                f"  instantAction id={action.action_id} "
                f"type={action.action_type} "
                f"blockingType={action.blocking_type.value} "
                f"params={[param.to_dict() for param in action.action_parameters]}"
            )
            blocking_type = str(
                getattr(action.blocking_type, "value", action.blocking_type)
            )
            if blocking_type != "NONE":
                description = (
                    "VDA5050 v3 instant actions require blockingType NONE; "
                    f"received {blocking_type}"
                )
                self._record_invalid_instant_action(action, description)
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=description,
                )
                print(
                    f"[INSTANT ACTION INVALID] id={action.action_id} "
                    f"type={action.action_type} blockingType={blocking_type}"
                )
                continue
            if self._is_jibot_um_test_instant_action(action):
                self._handle_jibot_um_test_instant_action(action)
                continue
            if (
                self._work_in_progress is not None
                and self._is_motion_instant_action(action)
            ):
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=(
                        f"busy with {self._work_in_progress} work; motion blocked"
                    ),
                )
                print(
                    f"[WORK BUSY BLOCK] id={action.action_id} "
                    f"type={action.action_type}"
                )
                continue
            if action.action_type == "cancelOrder":
                self._handle_cancel_order_instant_action(action.action_id)
            elif action.action_type == "manualStop":
                # manualStop must stay OUT of _is_motion_instant_action so it is
                # never work/busy-gated — an operator must always be able to stop.
                self._handle_manual_stop_instant_action(action)
            elif action.action_type == "manualDrive":
                self._handle_manual_drive_instant_action(action)
            elif action.action_type == "manualMove":
                self._handle_manual_move_instant_action(action)
            elif action.action_type == "jibotMotionRule":
                self._handle_motion_rule_instant_action(action)
            elif action.action_type == "enableMotor":
                self._handle_enable_motor_instant_action(action)
            elif action.action_type == "disableMotor":
                self._handle_disable_motor_instant_action(action)
            elif action.action_type == "gotoNearestNode":
                self._handle_goto_nearest_node_instant_action(action)
            elif action.action_type == "stateRequest":
                self._handle_state_request_instant_action(action.action_id)
            elif action.action_type == "factsheetRequest":
                self._handle_factsheet_request_instant_action(action.action_id)
            elif action.action_type == "startPause":
                self._handle_start_pause_instant_action(action.action_id)
            elif action.action_type == "stopPause":
                self._handle_stop_pause_instant_action(action.action_id)
            elif action.action_type == "startCharging":
                self._handle_start_charging_instant_action(action.action_id)
            elif action.action_type == "chargeInPlace":
                self._handle_charge_in_place_instant_action(action.action_id)
            elif action.action_type == "initPosition":
                self._handle_init_position_instant_action(action)
            elif action.action_type == "localize":
                self._handle_localize_instant_action(action)
            elif action.action_type in ("loading", "unloading"):
                self._handle_work_start_instant_action(action, action.action_type)
            elif action.action_type in ("stopLoading", "stopUnloading"):
                self._handle_work_stop_instant_action(action, action.action_type)
            elif action.action_type == "clearInstantActions":
                self._handle_clear_instant_actions_instant_action(action.action_id)
            elif action.action_type == "clearZoneActions":
                self._handle_clear_zone_actions_instant_action(action.action_id)
            elif action.action_type == "clearErrors":
                self._handle_clear_errors_instant_action(action.action_id)
            elif action.action_type == "stopCharging":
                self._handle_stop_charging_instant_action(action.action_id)
            elif action.action_type == "logReport":
                # Standard VDA5050 action with no matching JIBOT command;
                # rejected explicitly and excluded from the factsheet.
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=(
                        f"{action.action_type} is not supported by the JIBOT API"
                    ),
                )
            elif action.action_type == "syncJibotParams":
                self._handle_sync_jibot_params_instant_action(action)
            elif action.action_type == "requestVideo":
                self._handle_request_video_instant_action(action)
            elif action.action_type == "requestLaser":
                self._handle_request_laser_instant_action(action)
            elif action.action_type == "stopLaser":
                self._handle_stop_laser_instant_action(action)
            elif action.action_type == "setMapSnapshot":
                self._handle_set_map_snapshot_instant_action(action)
            elif action.action_type == "getMap":
                self._handle_get_map_instant_action(action)
            elif action.action_type == "getParameters":
                self._handle_get_parameters_instant_action(action)
            elif action.action_type == "setParameters":
                self._handle_set_parameters_instant_action(action)
            elif action.action_type == "setMap":
                self._handle_set_map_instant_action(action)
            elif action.action_type == "switchMap":
                self._handle_switch_map_action(action)
            elif action.action_type == "setSoundVolume":
                self._handle_set_sound_volume_instant_action(action)
            elif action.action_type == "testSound":
                self._handle_test_sound_instant_action(action)
            elif action.action_type == "stopSound":
                self._handle_stop_sound_instant_action(action)
            elif action.action_type == "uploadSound":
                self._handle_upload_sound_instant_action(action)
            elif self._is_jibot_command_instant_action(action):
                self._handle_jibot_command_instant_action(action)
            elif self._action_registry.has(action.action_type):
                self._action_registry.dispatch(action, self)
            else:
                self._record_action_not_found(action, scope="instant")
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=(
                        f"Unsupported instant action type: {action.action_type}"
                    ),
                )
                print(
                    f"[INSTANT ACTION UNSUPPORTED] "
                    f"id={action.action_id} type={action.action_type}"
                )

        self._prune_retained_instant_action_states()
        self.request_state_publish("instant actions handled")
        print("=" * 80)

    def _cancel_manual_drive_watchdog(self) -> None:
        if self._manual_drive_watchdog is not None:
            self._manual_drive_watchdog.cancel()
            self._manual_drive_watchdog = None

    def _order_motion_in_flight(self) -> bool:
        """오더가 아직 로봇을 움직일 수 있는 상태인가.

        `self.order` 존재 여부로 판정하면 안 된다. order 객체를 지우는 곳은
        `_clear_cancelled_order_state()` 하나뿐이고 그건 cancelOrder 에서만
        불리므로, 오더가 정상 완료돼도(`[ORDER COMPLETE]`) 객체는 그대로 남는다.
        그 상태로 수동 주행을 막으면 다음 오더나 cancelOrder 가 올 때까지
        조이스틱이 영구히 죽는다 (2026-08-22 .62 실측: 23:14:37 ORDER COMPLETE
        후 23:19:57 cancelOrder 까지 약 5분간 무반응).

        `_is_v3_order_active()` 는 남은 node/edge 로 완료를 읽으므로 그게 정답
        술어다. 다만 blockingType=NONE 액션(rotateTo)은 스텝이 다 빠진 뒤에도
        배경 태스크로 로봇을 돌리므로 그것까지 같이 본다.
        """
        if self._is_v3_order_active():
            return True
        return bool(self._active_order_background_action_tasks())

    def _manual_blocked_reason(
        self, *, owner_order_id: Optional[str] = None
    ) -> Optional[str]:
        """Return why manual drive/move is blocked, or None if allowed."""
        if not self.config.manual_control.enabled:
            return "manual control disabled"
        if self._work_in_progress is not None:
            return f"busy with {self._work_in_progress} work"
        if (
            self.order is not None
            and getattr(self.order, "order_id", None) != owner_order_id
            and self._order_motion_in_flight()
        ):
            return "order in progress; cancel order first"
        return None

    def _arm_manual_drive_watchdog(self) -> None:
        if self._loop is None or not self._loop.is_running():
            return
        if self._manual_drive_watchdog is not None:
            self._manual_drive_watchdog.cancel()
        timeout = float(self.config.manual_control.watchdog_ms) / 1000.0
        self._manual_drive_watchdog = self._loop.call_later(
            timeout, lambda: self._run_on_adapter_loop(self._manual_drive_timeout)
        )

    async def _manual_drive_timeout(self) -> None:
        self._manual_drive_watchdog = None
        self._manual_control_active = False
        await self._vehicle.um_stop()
        print("[MANUAL DRIVE WATCHDOG] auto-stop (no heartbeat)")

    def _handle_manual_drive_instant_action(self, action: Any) -> None:
        reason = self._manual_blocked_reason(
            owner_order_id=getattr(action, "_owner_order_id", None)
        )
        if reason:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, result_description=reason
            )
            return
        params = {p.key: p.value for p in action.action_parameters}
        mc = self.config.manual_control
        try:
            trans = float(params.get("trans", mc.drive_trans))
            rot = float(params.get("rot", mc.drive_rot))
            speed = float(params.get("speed", mc.drive_speed))
            lat = float(params.get("lat", mc.drive_lat))
        except (ValueError, TypeError):
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="manualDrive requires numeric trans/rot/speed/lat",
            )
            return
        self._update_instant_action_status(
            action.action_id, ActionStatus.FINISHED, result_description="manual drive"
        )

        async def _run() -> None:
            # Raise the active flag only AFTER um_drive succeeds, and never leave
            # it stuck True on failure: a leaked flag would suppress settled idle
            # capture forever, so lastNodeId would stop updating even once the
            # robot settles. Setting it inside _run (not the handler) also means a
            # failed _run_on_adapter_loop schedule never raises the flag at all.
            try:
                await self._vehicle.um_drive(trans, rot, speed, lat)
            except Exception as exc:
                self._manual_control_active = False
                print(f"[MANUAL DRIVE] um_drive failed: {exc}")
                return
            self._manual_control_active = True
            self._arm_manual_drive_watchdog()

        self._run_on_adapter_loop(_run, action_id=action.action_id)

    def _handle_manual_move_instant_action(self, action: Any) -> None:
        reason = self._manual_blocked_reason(
            owner_order_id=getattr(action, "_owner_order_id", None)
        )
        if reason:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, result_description=reason
            )
            return
        params = {p.key: p.value for p in action.action_parameters}
        mc = self.config.manual_control
        try:
            distance = float(params["distance"])
            # speed is optional (falls back to default) only when the key is ABSENT;
            # a present-but-blank/non-numeric value is an error (caller intent was explicit).
            speed_raw = params.get("speed")
            if speed_raw is None:
                speed = float(mc.default_move_speed)
            else:
                speed = float(speed_raw)
            flag = int(params.get("flag", 1))
            io = int(params.get("io", 1))
            obs_avoid_dist = int(params.get("obs_avoid_dist", mc.move_obs_avoid_dist))
            side_avoid_dist = int(params.get("side_avoid_dist", mc.move_side_avoid_dist))
            note = int(params.get("note", 1))
            use_io_raw = str(params.get("use_io", "false")).strip().lower()
            use_io = use_io_raw in ("1", "true", "yes", "on")
            run_mode = str(params.get("run_mode", "scheduler")).strip() or "scheduler"
        except (KeyError, ValueError, TypeError):
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="manualMove requires numeric distance and speed",
            )
            return
        print(
            f"[MANUAL MOVE] distance={distance} speed={speed} run_mode={run_mode} "
            f"flag={flag} io={io} obs_avoid_dist={obs_avoid_dist} "
            f"side_avoid_dist={side_avoid_dist} use_io={use_io} note={note}"
        )
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)
        self._manual_control_active = True

        async def _run() -> None:
            try:
                await self._vehicle.move_distance(
                    distance, speed,
                    obs_avoid_dist=obs_avoid_dist,
                    side_avoid_dist=side_avoid_dist,
                    flag=flag,
                    io=io,
                    use_io=use_io,
                    note=note,
                    run_mode=run_mode,
                )
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FINISHED,
                    result_description=f"move {distance}mm @ {speed}",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"manualMove failed: {exc}",
                )
            finally:
                self._manual_control_active = False

        self._run_on_adapter_loop(_run, action_id=action.action_id)

    def _handle_motion_rule_instant_action(self, action: Any) -> None:
        """WebUI test hook: run the configured dock/move motion rule for a node.

        Picks the motion_rules entry whose `to` matches the `to` param (optionally
        narrowed by `from`) and runs the SAME orchestrator the order worker uses
        (_run_dock for mode="dock", _run_move_segment for "move"), so the dock and
        move-segment flows are testable without an order.
        """
        reason = self._manual_blocked_reason(
            owner_order_id=getattr(action, "_owner_order_id", None)
        )
        if reason:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, result_description=reason
            )
            return
        params = {p.key: p.value for p in action.action_parameters}
        to = str(params.get("to") or params.get("goal") or "").strip()
        if not to:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="jibotMotionRule requires a 'to' node",
            )
            return
        from_node = str(params.get("from") or "").strip() or None
        rule = next(
            (
                r for r in (getattr(self.config, "motion_rules", None) or [])
                if getattr(r, "to", None) == to
                and getattr(r, "mode", "") in ("dock", "move")
                and (
                    from_node is None
                    or getattr(r, "from_node", None) in (None, from_node)
                )
            ),
            None,
        )
        if rule is None:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description=f"no dock/move motion_rule for to={to}",
            )
            return
        from types import SimpleNamespace
        node = SimpleNamespace(node_id=to, sequence_id=0, node_position=None)
        mode = rule.mode
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                if mode == "dock":
                    rej = await self._run_dock(node)
                else:
                    rej = await self._run_move_segment(node, rule)
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"motion rule {mode} error: {exc}",
                )
                return
            if rej is not None:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"{mode} failed: {rej}",
                )
                return
            if mode == "dock":
                # No order owns this test dock; clear DOCKING so it does not linger.
                self._docking_started_node_ids.discard(str(to))
                self._sync_docking_action_state()
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED,
                result_description=f"{mode} to {to} complete",
            )

        self._run_on_adapter_loop(_run, action_id=action.action_id)

    def _handle_manual_stop_instant_action(self, action: Any) -> None:
        self._manual_control_active = False
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                self._cancel_manual_drive_watchdog()
                await self._vehicle.um_stop()
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FINISHED,
                    result_description="manual stop",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=f"manualStop failed: {exc}",
                )

        self._run_on_adapter_loop(_run)

    def _handle_enable_motor_instant_action(self, action: Any) -> None:
        if not self.config.manual_control.enabled:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="manual control disabled",
            )
            return
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                await self._vehicle.enable_motor()
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FINISHED,
                    result_description="motor enabled",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"enableMotor failed: {exc}",
                )

        self._run_on_adapter_loop(_run)

    def _handle_disable_motor_instant_action(self, action: Any) -> None:
        if not self.config.manual_control.enabled:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="manual control disabled",
            )
            return
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                await self._vehicle.disable_motor()
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FINISHED,
                    result_description="motor disabled",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"disableMotor failed: {exc}",
                )

        self._run_on_adapter_loop(_run)

    def _handle_goto_nearest_node_instant_action(self, action: Any) -> None:
        """Drive to the nearest map node and set lastNodeId on arrival.

        Resolves the nearest node from the live pose, issues an UmGoto, then
        polls the pose until it is within the lastNodeId capture threshold
        (_idle_last_node_reach_xy). On arrival the shared _set_last_node writer
        records the node. A bounded timeout stops the robot and fails the action
        (so a rejected/blocked goto cannot hang in RUNNING, and a later proximity
        capture cannot be attributed to this attempt).
        """
        vx = self._optional_float(getattr(self._vehicle, "_x", None))
        vy = self._optional_float(getattr(self._vehicle, "_y", None))
        if vx is None or vy is None:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="not localized",
            )
            return
        if self._work_in_progress is not None:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description=f"busy with {self._work_in_progress} work; motion blocked",
            )
            return
        owner_order_id = getattr(action, "_owner_order_id", None)
        current_order_id = getattr(self.order, "order_id", None)
        if (
            self.order_worker_task is not None
            and not self.order_worker_task.done()
            and (owner_order_id is None or owner_order_id != current_order_id)
        ):
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="order in progress; cancel order first",
            )
            return
        nearest_mode = self._nearest_node_mode()
        nearest = self._find_nearest_node(vx, vy)
        if nearest is None:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description=f"no nearest node for mode {nearest_mode}",
            )
            return
        node_id, sequence_id, distance = nearest
        threshold = self._idle_last_node_reach_xy()
        if distance <= threshold:
            self._set_last_node(node_id, sequence_id)
            self.request_state_publish("goto nearest: already at node")
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED,
                result_description=f"already at {node_id}",
            )
            return

        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)
        timeout = self._goto_nearest_timeout_sec()
        poll = float(
            getattr(self.config.settings, "node_position_poll_interval_sec", 0.2)
        )
        map_pos = self._nearest_mode_map_nodes().get(node_id)

        async def _run() -> None:
            try:
                if nearest_mode == "pathPoint" and map_pos is not None:
                    # Face the way it drives instead of the PathPoint's map
                    # heading: those are all 0.00 here, so sending map_pos[2]
                    # spun the robot on arrival. An instant action carries no
                    # nodePosition, so there is no order heading to prefer.
                    # poseTh still has to go out — see _node_goto_theta_deg.
                    await self._vehicle.goto_xyz(
                        float(map_pos[0]),
                        float(map_pos[1]),
                        self._node_goto_theta_deg(
                            target_xy=(float(map_pos[0]), float(map_pos[1])),
                            map_theta_deg=map_pos[2],
                        ),
                    )
                elif self._is_simulator() and map_pos is not None:
                    await self._vehicle.goto_xyz(
                        float(map_pos[0]), float(map_pos[1]), float(map_pos[2])
                    )
                else:
                    await self._vehicle.goto_point(node_id)

                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    cx = self._optional_float(getattr(self._vehicle, "_x", None))
                    cy = self._optional_float(getattr(self._vehicle, "_y", None))
                    if cx is not None and cy is not None:
                        d = (
                            math.hypot(
                                cx - float(map_pos[0]),
                                cy - float(map_pos[1]),
                            )
                            if map_pos is not None
                            else None
                        )
                        if d is not None and d <= threshold:
                            self._set_last_node(node_id, sequence_id)
                            self.request_state_publish("goto nearest: reached")
                            self._update_instant_action_status(
                                action.action_id, ActionStatus.FINISHED,
                                result_description=f"reached {node_id}",
                            )
                            return
                    await asyncio.sleep(poll)

                await self._vehicle.um_stop()
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"timeout reaching {node_id}",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"gotoNearestNode failed: {exc}",
                )

        self._run_on_adapter_loop(_run, action_id=action.action_id)

    def _is_jibot_um_test_instant_action(self, action: Any) -> bool:
        return (
            str(getattr(action, "action_type", "") or "")
            in _JIBOT_UM_TEST_ACTION_TYPES
        )

    def _is_jibot_command_instant_action(self, action: Any) -> bool:
        """Return True when an instant action maps to a JIBOT command.

        instant action이 JIBOT 명령으로 매핑되는 경우 True를 반환한다.

        Supported forms / 지원 형식:
        - actionType="jibotCommand" with command/cmd/#CMD# actionParameter.
          actionType="jibotCommand"이고 actionParameter에 command/cmd/#CMD#가 있는 형태.
        - actionType set directly to a command in JIBOT.COMMAND_SPECS.
          actionType 자체가 JIBOT.COMMAND_SPECS에 등록된 명령명인 형태.
        Explicitly listed ``jibotUm`` action types are eq/test-only probes and are acknowledged
        by ``_handle_jibot_um_test_instant_action`` instead of being sent to the
        robot command path.
        """
        if self._is_jibot_um_test_instant_action(action):
            return False

        return (
            action.action_type == "jibotCommand"
            or self._command_from_jibot_action_type(action.action_type) is not None
        )

    def _is_motion_instant_action(self, action: Any) -> bool:
        """True for instant actions that can physically move the robot.

        Conservative: any raw JIBOT command (jibotCommand / jibot* alias /
        direct command-name actionType) can issue UmGoto/UmDock, plus the
        standard startCharging (UmDock). Read-only JIBOT commands are blocked
        too while BUSY by design; allow-list later only if a real need appears.
        """
        return (
            action.action_type == "startCharging"
            or action.action_type in ("manualDrive", "manualMove", "localize")
            or self._is_jibot_command_instant_action(action)
            or self._action_registry.is_motion(action.action_type)
        )

    def _command_from_jibot_action_type(self, action_type: str) -> Optional[str]:
        if action_type in JIBOT.COMMAND_SPECS:
            return action_type

        prefix = "jibot"
        if action_type.startswith(prefix):
            command = action_type[len(prefix):]
            if command in JIBOT.COMMAND_SPECS:
                return command

        return None

    def _handle_sync_jibot_params_instant_action(self, action: Any) -> None:
        """Copy JIBOT map/routes params from disk through a VDA5050 instant action."""
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)
        action_params = {
            param.key: param.value
            for param in action.action_parameters
        }

        source_dir = Path(
            action_params.get("source")
            or action_params.get("sourceDir")
            or action_params.get("jibotParamsSource")
            or DEFAULT_SOURCE_DIR
        )
        target_dir = Path(
            action_params.get("dest")
            or action_params.get("destination")
            or action_params.get("targetDir")
            or action_params.get("jibotParamsDest")
            or DEFAULT_TARGET_DIR
        )
        backup_root = Path(
            action_params.get("backupDir")
            or action_params.get("backupRoot")
            or action_params.get("jibotParamsBackupDir")
            or DEFAULT_BACKUP_ROOT
        )

        try:
            result = sync_jibot_params(
                source_dir=source_dir,
                target_dir=target_dir,
                backup_root=backup_root,
            )
        except Exception as exc:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=f"syncJibotParams failed: {exc}",
            )
            print(f"[JIBOT PARAMS SYNC FAILED] actionId={action.action_id}: {exc}")
            return

        backup_text = str(result.backup_dir) if result.backup_dir is not None else "none"
        copied_text = ", ".join(str(path) for path in result.copied)
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.FINISHED,
            result_description=(
                f"syncJibotParams copied [{copied_text}] backup={backup_text}"
            ),
        )
        print(
            f"[JIBOT PARAMS SYNCED] actionId={action.action_id} "
            f"target={result.target_dir} backup={backup_text}"
        )

    def _handle_request_video_instant_action(self, action: Any) -> None:
        """Advertise web_video_server live-stream URLs to the FMS.

        Pixels are not relayed through MQTT: the FMS opens the returned URL
        directly. Optional actionParameters ``topic`` or ``camera`` request a
        single camera; otherwise all configured stream topics are advertised.
        The URLs are returned in the action result; the full set is also
        advertised statically in the factsheet (videoStreams).
        """
        if not self.config.video.enabled:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="Video feature is disabled",
            )
            return

        action_params = {
            param.key: param.value for param in action.action_parameters
        }
        requested = action_params.get("topic") or action_params.get("camera")
        topics = [requested] if requested else list(self.config.video.stream_topics)
        if not topics:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="No stream topics configured",
            )
            return

        stream_urls = self._video.stream_urls(topics)
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.FINISHED,
            result_description=json.dumps(stream_urls, ensure_ascii=False),
        )
        print(
            f"[VIDEO REQUEST] actionId={action.action_id} "
            f"streams={list(stream_urls.values())}"
        )

    def _laser_action_params(self, action: Any) -> Dict[str, Any]:
        return {param.key: param.value for param in action.action_parameters}

    def _handle_request_laser_instant_action(self, action: Any) -> None:
        params = self._laser_action_params(action)
        index = int(params.get("index", 0) or 0)
        interval_ms = int(params.get("intervalMs", params.get("interval", 200)) or 200)
        interval_ms = max(50, interval_ms)
        topic = str(params.get("topic") or "laser")

        if self._vehicle is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        if self._loop is None or not self._loop.is_running():
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="Adapter event loop is not ready",
            )
            return

        if self.laser_publish_task is not None and not self.laser_publish_task.done():
            self.laser_publish_task.cancel()

        self.laser_publish_task = self._loop.create_task(
            self._laser_publish_loop(
                index=index,
                interval_ms=interval_ms,
                topic=topic,
            )
        )
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.FINISHED,
            result_description=f"Laser publishing started topic={topic} intervalMs={interval_ms}",
        )
        print(
            f"[LASER REQUEST] actionId={action.action_id} "
            f"topic={topic} index={index} intervalMs={interval_ms}"
        )

    def _handle_stop_laser_instant_action(self, action: Any) -> None:
        if self.laser_publish_task is not None and not self.laser_publish_task.done():
            self.laser_publish_task.cancel()
        self.laser_publish_task = None
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.FINISHED,
            result_description="Laser publishing stopped",
        )
        print(f"[LASER STOP] actionId={action.action_id}")

    def _handle_jibot_um_test_instant_action(self, action: Any) -> None:
        """Acknowledge a known eq/test-only probe without sending robot commands."""
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.FINISHED,
            result_description=f"{action.action_type} acknowledged as test action",
        )

    async def _laser_publish_loop(self, *, index: int, interval_ms: int, topic: str) -> None:
        interval_sec = interval_ms / 1000.0
        try:
            while True:
                payload = await self._fetch_laser_payload(index, interval_ms)
                if payload is not None:
                    self._mqtt.publish(topic, payload, qos=0, retain=False)
                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[LASER PUBLISH FAILED] topic={topic}: {exc}")

    async def _fetch_laser_payload(self, index: int, interval_ms: int) -> Optional[Dict[str, Any]]:
        if self._vehicle is None:
            return None

        get_laser = getattr(self._vehicle, "get_laser", None)
        if callable(get_laser):
            response = await get_laser(index=index, interval_ms=interval_ms)
        else:
            um_get_laser = getattr(self._vehicle, "um_get_laser", None)
            if not callable(um_get_laser):
                return None
            await um_get_laser(index=index, gap=interval_ms)
            response = getattr(self._vehicle, "_laser_raw", None)

        if not response:
            return None

        data = response.get("data") if isinstance(response, dict) else response
        return {
            "timestamp": utils.get_timestamp(),
            "source": "UmGetLaser",
            "index": index,
            "data": data,
        }

    def _handle_jibot_command_instant_action(self, action: Any) -> None:
        """Validate and schedule a JIBOT command from a VDA5050 instant action.

        VDA5050 instant action에서 JIBOT 명령을 검증하고 event loop에 예약한다.

        UmGoto waits for a robot-side response before it is marked FINISHED.
        If the robot rejects it, or no acknowledgement arrives before timeout,
        the VDA5050 action is reported as FAILED with a rejection reason.
        UmGoto는 로봇 측 응답을 확인한 뒤 FINISHED로 표시한다. 로봇이 거절하거나
        제한 시간 안에 ack가 없으면 거절 사유를 담아 FAILED로 리포트한다.
        """
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        try:
            command, gap, params = self._parse_jibot_command_action(action)
        except ValueError as exc:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=str(exc),
            )
            print(f"[JIBOT COMMAND FAILED] actionId={action.action_id}: {exc}")
            return

        if self._vehicle is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            print(f"[JIBOT COMMAND FAILED] actionId={action.action_id}: vehicle is not initialized")
            return

        if self._loop is None or not self._loop.is_running():
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="Adapter event loop is not ready",
            )
            print(f"[JIBOT COMMAND FAILED] actionId={action.action_id}: event loop is not ready")
            return

        async def _send_jibot_command() -> None:
            try:
                # Reuse the client validator before dispatching through the
                # command-specific wrapper.
                # 명령별 wrapper 호출 전에 client 검증 로직을 재사용한다.
                self._vehicle.build_command(command, gap=gap, **params)
                response = await self._dispatch_jibot_command(
                    command,
                    gap=gap,
                    params=params,
                    timeout=self._parse_jibot_ack_timeout(action, default=getattr(self.config.settings, "jibot_command_default_timeout_sec", 3.0)),
                )
                rejection_reason = self._jibot_command_rejection_reason(
                    command,
                    response,
                )
                if rejection_reason is not None:
                    self._update_instant_action_status(
                        action.action_id,
                        ActionStatus.FAILED,
                        result_description=f"Rejected: {rejection_reason}",
                    )
                    print(
                        f"[JIBOT COMMAND REJECTED] actionId={action.action_id} "
                        f"command={command}: {rejection_reason}"
                    )
                    return
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=f"{command} failed: {exc}",
                )
                print(f"[JIBOT COMMAND FAILED] actionId={action.action_id} command={command}: {exc}")
                return

            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FINISHED,
                result_description=f"{command} sent",
            )
            print(f"[JIBOT COMMAND SENT] actionId={action.action_id} command={command} gap={gap} params={params}")

        # MQTT callbacks may run outside the adapter's asyncio loop, so the
        # coroutine is scheduled thread-safely onto the owning loop.
        # MQTT callback이 adapter asyncio loop 밖에서 실행될 수 있으므로
        # coroutine을 소유 loop에 thread-safe하게 예약한다.
        self._run_on_adapter_loop(_send_jibot_command)

    async def _dispatch_jibot_command(
        self,
        command: str,
        gap: int,
        params: Dict[str, Any],
        timeout: float,
    ) -> Optional[Dict[str, Any]]:
        if command == "UmGoto":
            return await self._vehicle.send_command_and_wait(
                command,
                gap=gap,
                timeout=timeout,
                accept_errors=True,
                **params,
            )

        method_name = JIBOT.COMMAND_METHODS[command]
        method = getattr(self._vehicle, method_name)
        await method(**params, gap=gap)
        return {"#CMD#": command, "state": True}

    def _parse_jibot_ack_timeout(self, action: Any, default: float) -> float:
        for param in action.action_parameters:
            if param.key in ("ackTimeout", "timeout", "responseTimeout"):
                try:
                    timeout = float(param.value)
                    return max(getattr(self.config.settings, "jibot_ack_timeout_min_sec", 0.1), timeout)
                except (TypeError, ValueError):
                    return default
        return default

    def _jibot_command_rejection_reason(
        self,
        command: str,
        response: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if response is None:
            return f"{command} no robot acknowledgement"

        response_command = response.get("#CMD#")
        if response_command == "error":
            return self._format_jibot_error_response(response)

        if response.get("state") is False:
            return self._format_jibot_error_response(response)

        success = response.get("success")
        if success is False:
            return self._format_jibot_error_response(response)

        status = str(response.get("status", response.get("result", ""))).lower()
        if any(token in status for token in ("reject", "fail", "error", "denied")):
            return self._format_jibot_error_response(response)

        return None

    def _format_jibot_error_response(self, response: Dict[str, Any]) -> str:
        parts = []
        for key in ("msg", "message", "title", "suggestion", "reason", "error"):
            value = response.get(key)
            if value not in (None, ""):
                parts.append(f"{key}={value}")

        if parts:
            return "; ".join(parts)

        return json.dumps(response, ensure_ascii=False, separators=(",", ":"))

    def _parse_jibot_command_action(self, action: Any) -> Tuple[str, int, Dict[str, Any]]:
        """Extract command, #GAP#, and command params from an instant action.

        instant action에서 command, #GAP#, 명령 파라미터를 추출한다.

        Parameter precedence / 파라미터 우선순위:
        - For actionType="jibotCommand", command/cmd/#CMD# selects the JIBOT command.
          actionType="jibotCommand"일 때 command/cmd/#CMD#가 JIBOT 명령을 지정한다.
        - gap or #GAP# becomes the JIBOT #GAP# value; default is -1.
          gap 또는 #GAP#가 JIBOT #GAP# 값이며 기본값은 -1이다.
        - params object is expanded first, then top-level actionParameters
          override duplicate keys.
          params 객체를 먼저 펼치고, 중복 key는 상위 actionParameter가 덮어쓴다.

        보완 필요 / Needs confirmation:
        The prior prompt material defined the accepted command names, but not
        all value types. Keep unsupported or uncertain fields explicit in ACS
        payloads until the JIBOT command contract is confirmed.
        이전 prompt 자료는 허용 명령명을 제공했지만 모든 값 타입을 정의하지는 않았다.
        JIBOT 명령 계약이 확정될 때까지 불확실한 필드는 ACS payload에서 명시적으로
        관리해야 한다.
        """
        action_params = {
            param.key: param.value
            for param in action.action_parameters
        }

        command = self._command_from_jibot_action_type(action.action_type)
        if action.action_type == "jibotCommand":
            command = (
                action_params.pop("command", None)
                or action_params.pop("cmd", None)
                or action_params.pop("#CMD#", None)
            )

        if not command:
            raise ValueError("jibotCommand requires command actionParameter")

        if command not in JIBOT.COMMAND_SPECS:
            raise ValueError(f"Unsupported JIBOT command: {command}")

        gap = action_params.pop("gap", action_params.pop("#GAP#", -1))
        try:
            gap = int(gap)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{command} gap must be an integer") from exc

        nested_params = action_params.pop("params", {})
        if nested_params is None:
            nested_params = {}
        if not isinstance(nested_params, dict):
            raise ValueError(f"{command} params actionParameter must be an object")

        command_params = dict(nested_params)
        command_params.update(action_params)
        for timeout_key in ("ackTimeout", "timeout", "responseTimeout"):
            command_params.pop(timeout_key, None)
        return command, gap, command_params

    def _handle_state_request_instant_action(self, action_id: str) -> None:
        """VDA5050 stateRequest: publish the current state immediately."""
        self._update_instant_action_status(
            action_id,
            ActionStatus.FINISHED,
            result_description="State publish triggered",
        )
        self.request_state_publish("stateRequest")
        print(f"[STATE REQUEST] actionId={action_id}")

    def _handle_factsheet_request_instant_action(self, action_id: str) -> None:
        """VDA5050 factsheetRequest: publish the factsheet topic."""
        try:
            self.publish_factsheet()
        except Exception as exc:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description=f"factsheetRequest failed: {exc}",
            )
            print(f"[FACTSHEET REQUEST FAILED] actionId={action_id}: {exc}")
            return

        self._update_instant_action_status(
            action_id,
            ActionStatus.FINISHED,
            result_description="Factsheet published",
        )
        print(f"[FACTSHEET REQUEST] actionId={action_id}")

    async def _pause_motion(self) -> None:
        """모션을 세우고 state 를 paused 로 표시한다.

        startPause instant action 과 FMS 링크 상실 가드가 공유한다. 두 경로가
        각자 flag 를 만지다 한쪽만 action_states 를 PAUSED 로 못 돌리는 어긋남을
        막으려고 한 곳에 모았다.
        """
        await self._vehicle.stop_motion()
        self._motion_paused = True
        if self.state is not None:
            self.state.paused = True
            for action_state in self.state.action_states:
                if action_state.action_status == ActionStatus.RUNNING:
                    action_state.action_status = ActionStatus.PAUSED

    async def _resume_motion(self) -> None:
        """중단된 모션을 다시 쏘고 paused 를 푼다(stopPause 와 링크 복구가 공유).

        재발행이 실패하면 예외를 그대로 올려 보내고 paused 는 유지한다 — 못 움직인
        채로 paused 만 풀리면 FMS 는 달리는 줄 안다.
        """
        # A paused relative move is resumed as a move, not a goto: the
        # rule exists because a goto is unsuitable on this segment.
        # _active_move_segment and _active_goto_node are mutually
        # exclusive (move phase vs. fallback-goto phase).
        segment = self._active_move_segment
        if segment is not None:
            remaining = self._remaining_move_distance(segment)
            if remaining:
                print(f"[RESUME] re-issuing move remaining={remaining:.1f}")
                await self._vehicle.move_distance(
                    remaining, segment.speed, **segment.kwargs
                )
        else:
            node = self._active_goto_node
            if node is not None:
                # stop_motion cancelled the active goto; the worker is
                # still waiting on this node, so transmit the motion again.
                # _send_node_goto rather than _send_node_motion: the
                # latter's _is_dock_work_node branch is non-directional
                # and would fire a bare UmDock if this goto (including
                # a move-segment fallback goto) targets a dock-work
                # node, docking the robot instead of driving it there.
                # The return value (rejection reason) is intentionally
                # ignored here: the worker remains parked on this node
                # and will re-detect a persistent rejection on its next
                # run.
                await self._send_node_goto(node)

        self._motion_paused = False
        self._paused_by_mqtt_loss = False
        if self.state is not None:
            self.state.paused = False
            for action_state in self.state.action_states:
                if action_state.action_status == ActionStatus.PAUSED:
                    action_state.action_status = ActionStatus.RUNNING

    def _handle_start_pause_instant_action(self, action_id: str) -> None:
        """VDA5050 startPause: stop motion and mark the state paused.

        The order worker keeps waiting on the current node; the interrupted
        goto is re-issued by stopPause.
        """
        if self._vehicle is None:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        self._update_instant_action_status(action_id, ActionStatus.RUNNING)

        async def _pause() -> None:
            try:
                await self._pause_motion()
            except Exception as exc:
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FAILED,
                    result_description=f"startPause failed: {exc}",
                )
                print(f"[START PAUSE FAILED] actionId={action_id}: {exc}")
                return

            self._update_instant_action_status(
                action_id,
                ActionStatus.FINISHED,
                result_description="Motion paused",
            )
            print("[START PAUSE] motion stopped; resume with stopPause.")

        self._run_on_adapter_loop(_pause)

    def _handle_stop_pause_instant_action(self, action_id: str) -> None:
        """VDA5050 stopPause: clear paused and re-issue the interrupted goto."""
        if self._vehicle is None:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        self._update_instant_action_status(action_id, ActionStatus.RUNNING)

        async def _resume() -> None:
            try:
                await self._resume_motion()
            except Exception as exc:
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FAILED,
                    result_description=f"stopPause failed: {exc}",
                )
                print(f"[STOP PAUSE FAILED] actionId={action_id}: {exc}")
                return

            self._update_instant_action_status(
                action_id,
                ActionStatus.FINISHED,
                result_description="Motion resumed",
            )
            print("[STOP PAUSE] paused cleared; motion re-issued if active.")

        self._run_on_adapter_loop(_resume)

    def _handle_clear_instant_actions_instant_action(self, action_id: str) -> None:
        """VDA5050 clearInstantActions: clear retained instant-action states now."""
        if self.state is not None:
            self.state.instant_action_states = []
        self.request_state_publish(f"instant action states cleared by {action_id}")

    def _handle_clear_zone_actions_instant_action(self, action_id: str) -> None:
        """VDA5050 clearZoneActions: clear retained zone-action states if present."""
        if self.state is not None and hasattr(self.state, "zone_action_states"):
            self.state.zone_action_states = []
        self._update_instant_action_status(
            action_id,
            ActionStatus.FINISHED,
            result_description="Cleared zone action states",
        )

    def _handle_clear_errors_instant_action(self, action_id: str) -> None:
        """Clear stale state.errors on FMS/operator request (clearErrors action).

        Sets state.errors = []. Active conditions (brake/avoidance/lost/
        connection-lost) are re-added by the per-cycle refresh methods if still
        present, so only stale sticky errors (JIBOT_GOTO_REJECTED,
        JIBOT_NODE_UNREACHED, ORDER_* rejections) are actually cleared.
        Not a motion action, so it is allowed even while BUSY.
        """
        if self.state is not None:
            self.state.errors = []
        # marks the received action FINISHED and triggers a state publish
        self._update_instant_action_status(
            action_id,
            ActionStatus.FINISHED,
            result_description="Cleared state errors",
        )

    def _handle_work_start_instant_action(self, action: Any, work_type: str) -> None:
        """loading/unloading: enter BUSY and hold the action RUNNING.

        JIBOT cannot self-detect completion, so the action is kept RUNNING until
        a matching stopLoading/stopUnloading injects completion. While BUSY the
        adapter rejects orders and motion instant actions.
        """
        if self.state is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="State is not initialized",
            )
            return

        if self._work_in_progress is not None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=(
                    f"work already in progress: {self._work_in_progress}"
                ),
            )
            print(
                f"[WORK START REJECTED] type={work_type} "
                f"active={self._work_in_progress}"
            )
            return

        if self.state.order_id and not self._is_v3_order_finished():
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=(
                    f"cannot start {work_type} while an order is in progress"
                ),
            )
            print(
                f"[WORK START REJECTED] type={work_type} "
                f"reason=order-active orderId={self.state.order_id}"
            )
            return

        self._work_in_progress = work_type
        self._work_action_id = action.action_id
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.RUNNING,
            result_description=f"{work_type} in progress",
        )
        print(
            f"[WORK START] type={work_type} actionId={action.action_id} -> BUSY"
        )

    def _handle_work_stop_instant_action(self, action: Any, stop_type: str) -> None:
        """stopLoading/stopUnloading: inject completion, finish the held work
        action, and clear BUSY."""
        if self.state is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="State is not initialized",
            )
            return

        expected_work = "loading" if stop_type == "stopLoading" else "unloading"
        if self._work_in_progress != expected_work:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=f"no active {expected_work} work to stop",
            )
            print(
                f"[WORK STOP IGNORED] type={stop_type} "
                f"active={self._work_in_progress}"
            )
            return

        # Finish the held work action: terminal status auto-removes it from the
        # published instant_action_states.
        if self._work_action_id is not None:
            self._update_instant_action_status(
                self._work_action_id,
                ActionStatus.FINISHED,
                result_description=f"{expected_work} completed",
            )
        self._work_in_progress = None
        self._work_action_id = None
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.FINISHED,
            result_description=f"{expected_work} stopped",
        )
        print(f"[WORK STOP] type={expected_work} -> IDLE")

    def _handle_start_charging_instant_action(self, action_id: str) -> None:
        """VDA5050 startCharging: dock onto the charger via JIBOT UmDock.

        The charging flag in powerSupply follows the robot status, so the
        action finishes when the dock command is accepted by the robot link.
        """
        if self._vehicle is None:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        self._update_instant_action_status(action_id, ActionStatus.RUNNING)

        if should_charge_in_place(self, None):
            async def _charge() -> None:
                ok, desc = await run_charge_in_place(self)
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FINISHED if ok else ActionStatus.FAILED,
                    result_description=desc,
                )
                print(f"[START CHARGING IN PLACE] actionId={action_id} ok={ok}")
            self._run_on_adapter_loop(_charge)
            return

        async def _dock() -> None:
            try:
                await self._vehicle.um_dock(
                    **self._dock_approach_params(self._last_node_id or "")
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FAILED,
                    result_description=f"startCharging failed: {exc}",
                )
                print(f"[START CHARGING FAILED] actionId={action_id}: {exc}")
                return
            # UmDock is fire-and-forget; confirm the robot actually enters the
            # charging state before reporting success. A charge request that
            # never changes the robot to charging must surface FAILED.
            if await self._wait_until_charging():
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FINISHED,
                    result_description="charging started",
                )
                print(f"[START CHARGING] actionId={action_id} charging started.")
            else:
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FAILED,
                    result_description="startCharging: charging did not start",
                )
                print(
                    f"[START CHARGING FAILED] actionId={action_id} "
                    "charging did not start"
                )

        self._run_on_adapter_loop(_dock)

    def _handle_stop_charging_instant_action(self, action_id: str) -> None:
        """VDA5050 stopCharging: end whichever charge mode is active.

        - In-place charge → release the relay hold (relay opens).
        - Dock charge (JModeCharge) → UmStop, repeated because the firmware
          ignores a single UmStop, then verify telemetry actually clears the
          charging flag within stop_charging_verify_timeout_sec.

        FAILS if the robot is still charging after the verify window so the
        master never sees a false "stopped".
        """
        if self._vehicle is None:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        self._update_instant_action_status(action_id, ActionStatus.RUNNING)

        async def _stop() -> None:
            try:
                ok, desc = await self._run_stop_charging()
            except Exception as exc:
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FAILED,
                    result_description=f"stopCharging failed: {exc}",
                )
                print(f"[STOP CHARGING FAILED] actionId={action_id}: {exc}")
                return
            self._update_instant_action_status(
                action_id,
                ActionStatus.FINISHED if ok else ActionStatus.FAILED,
                result_description=desc,
            )
            print(f"[STOP CHARGING] actionId={action_id} ok={ok} ({desc})")

        self._run_on_adapter_loop(_stop)

    async def _run_stop_charging(self) -> Tuple[bool, str]:
        """Stop the active charge and confirm it ended.

        Returns (ok, description). ok is False only when the robot keeps
        reporting charging past the verify window.
        """
        # In-place charge: drop the relay hold (stop_hold opens the relay).
        if self._charge_in_place_active:
            release_charge_in_place(self)
            return True, "in-place charge stopped"

        # Dock charge (JModeCharge): nothing to stop if not charging.
        if not self._is_vehicle_charging():
            return True, "not charging"

        # JIBOT ignores a single UmStop, so the stop is repeated; then verify
        # the robot actually left the charging state.
        await self._stop_charging_after_dock_work("stopCharging")
        if await self._wait_until_not_charging():
            return True, "charging stopped"
        return False, "stopCharging: robot still charging after UmStop"

    async def _wait_until_not_charging(self, poll_sec: Optional[float] = None) -> bool:
        """Poll telemetry until charging clears, bounded by the verify timeout."""
        if poll_sec is None:
            poll_sec = float(getattr(self.config.dock, "charging_stop_poll_interval_sec", 0.2))
        timeout = float(
            getattr(self.config.dock, "stop_charging_verify_timeout_sec", 10.0)
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._is_vehicle_charging():
                return True
            await asyncio.sleep(poll_sec)
        return not self._is_vehicle_charging()

    async def _wait_until_charging(self, poll_sec: Optional[float] = None) -> bool:
        """Poll telemetry until charging starts, bounded by the verify timeout."""
        if poll_sec is None:
            poll_sec = float(getattr(self.config.dock, "charging_start_poll_interval_sec", 0.2))
        timeout = float(
            getattr(self.config.dock, "start_charging_verify_timeout_sec", 60.0)
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_vehicle_charging():
                return True
            await asyncio.sleep(poll_sec)
        return self._is_vehicle_charging()

    def _handle_charge_in_place_instant_action(self, action_id: str) -> None:
        """VDA5050 chargeInPlace: close the charge relay without moving."""
        if self._vehicle is None:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        self._update_instant_action_status(action_id, ActionStatus.RUNNING)

        async def _charge() -> None:
            ok, desc = await run_charge_in_place(self)
            self._update_instant_action_status(
                action_id,
                ActionStatus.FINISHED if ok else ActionStatus.FAILED,
                result_description=desc,
            )
            print(f"[CHARGE IN PLACE] actionId={action_id} ok={ok} ({desc})")

        self._run_on_adapter_loop(_charge)

    def _handle_init_position_instant_action(self, action: Any) -> None:
        """VDA5050 initPosition: localize the robot via JIBOT UmLocalize.

        Standard parameters x/y/theta (robot pose units) and optional mapId/
        lastNodeId. UmLocalize target/goal semantics are not fully documented;
        target defaults to "pose" like UmGoto and can be overridden via a
        "target" actionParameter (see docs/todo/vda5050-compliance-gaps.md).
        """
        if self._vehicle is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        params = {param.key: param.value for param in action.action_parameters}
        try:
            pose_x = float(params["x"])
            pose_y = float(params["y"])
            pose_theta = float(params["theta"])
        except (KeyError, TypeError, ValueError):
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=(
                    "initPosition requires numeric x, y, theta actionParameters"
                ),
            )
            return

        map_id = params.get("mapId")
        target = params.get("target", "pose")
        goal = params.get("lastNodeId") or params.get("goal")

        async def _localize() -> None:
            try:
                await self._vehicle.um_localize(
                    target=target,
                    goal=goal,
                    poseX=pose_x,
                    poseY=pose_y,
                    poseTh=pose_theta,
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=f"initPosition failed: {exc}",
                )
                print(f"[INIT POSITION FAILED] actionId={action.action_id}: {exc}")
                return

            if map_id:
                self._current_map_id = str(map_id)
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FINISHED,
                result_description="UmLocalize sent",
            )
            print(
                f"[INIT POSITION] actionId={action.action_id} "
                f"pose=({pose_x}, {pose_y}, {pose_theta}) mapId={map_id}"
            )

        self._run_on_adapter_loop(_localize)

    def _handle_localize_instant_action(self, action: Any) -> None:
        """Localize to a map goal or explicit pose using the common client API."""
        if self._vehicle is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        params = {param.key: param.value for param in action.action_parameters}
        goal = str(params.get("goal") or "").strip() or None
        node = str(params.get("node") or "").strip() or None
        target = str(params.get("target") or ("goal" if goal else "pose")).strip().lower()
        if target not in ("goal", "pose", "auto"):
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="localize target must be goal, pose, or auto",
            )
            return

        if node is not None and target != "pose":
            # goal= and node= read different lookup tables; dropping one silently
            # would re-anchor the robot somewhere the operator never asked for.
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=f"localize node requires target=pose, got {target}",
            )
            return

        pose_x = pose_y = pose_theta = None
        if target == "goal":
            if goal is None:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description="localize target=goal requires goal",
                )
                return
        elif target == "pose":
            def _blank(value: Any) -> bool:
                # Missing or empty/whitespace string means "not supplied". A
                # numeric 0 is a real coordinate (map origin), so it is NOT blank.
                return value is None or (
                    isinstance(value, str) and value.strip() == ""
                )

            # A named map node supplies an ABSOLUTE anchor pose, so a recipe can
            # re-anchor without carrying mm coordinates that go stale when the
            # map is re-surveyed. Explicit x/y/theta still win over it.
            node_x = node_y = node_theta = None
            if node is not None:
                # Goal/GoalWithHeading/Dock first: only those carry a real
                # heading. PathPoint is accepted as a POSITION source (the FMS
                # driving graph is PathPoint-based, so p2 is the id it knows) but
                # never as a heading source — every PathPoint on this site's map
                # carries theta 0.00, so a silent 0 would be a lie. Same rule as
                # _send_node_goto (adapter_jibot.py:4964).
                node_pose = self._map_nodes().get(node)
                if node_pose is not None:
                    node_theta = (
                        float(node_pose[2]) if len(node_pose) > 2 else 0.0
                    )
                else:
                    node_pose = self._map_path_points().get(node)
                    if node_pose is not None and _blank(params.get("theta")):
                        self._update_instant_action_status(
                            action.action_id,
                            ActionStatus.FAILED,
                            result_description=(
                                f"localize node '{node}' is a PathPoint, which "
                                "carries no heading — pass theta explicitly"
                            ),
                        )
                        return
                if node_pose is None:
                    self._update_instant_action_status(
                        action.action_id,
                        ActionStatus.FAILED,
                        result_description=(
                            f"localize node '{node}' is not on the robot map"
                        ),
                    )
                    return
                node_x = float(node_pose[0])
                node_y = float(node_pose[1])

            if node_theta is not None and _blank(params.get("theta")):
                pose_theta = node_theta
            else:
                try:
                    pose_theta = float(params["theta"])
                except (KeyError, TypeError, ValueError):
                    self._update_instant_action_status(
                        action.action_id,
                        ActionStatus.FAILED,
                        result_description=(
                            "localize target=pose requires numeric theta"
                        ),
                    )
                    return

            if node_x is not None and _blank(params.get("x")) and _blank(
                params.get("y")
            ):
                pose_x = node_x
                pose_y = node_y
            elif _blank(params.get("x")) and _blank(params.get("y")):
                # No coordinates supplied: re-anchor at the live current position
                # and fix only the heading. This keeps the localizationNf recipes
                # free of hardcoded x/y that would go stale.
                cur_x = getattr(self._vehicle, "_x", None)
                cur_y = getattr(self._vehicle, "_y", None)
                if cur_x is None or cur_y is None:
                    self._update_instant_action_status(
                        action.action_id,
                        ActionStatus.FAILED,
                        result_description=(
                            "localize target=pose needs x, y or a known current pose"
                        ),
                    )
                    return
                pose_x = float(cur_x)
                pose_y = float(cur_y)
            else:
                try:
                    pose_x = float(params["x"])
                    pose_y = float(params["y"])
                except (KeyError, TypeError, ValueError):
                    self._update_instant_action_status(
                        action.action_id,
                        ActionStatus.FAILED,
                        result_description=(
                            "localize target=pose requires numeric x, y"
                        ),
                    )
                    return

        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)
        map_id = str(params.get("mapId") or "").strip() or None

        async def _localize() -> None:
            try:
                localize = getattr(self._vehicle, "localize", None)
                if callable(localize):
                    await localize(target, goal, pose_x, pose_y, pose_theta)
                else:
                    await self._vehicle.um_localize(
                        target=target,
                        goal=goal,
                        poseX=pose_x,
                        poseY=pose_y,
                        poseTh=pose_theta,
                    )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=f"localize failed: {exc}",
                )
                print(f"[LOCALIZE FAILED] actionId={action.action_id}: {exc}")
                return

            if map_id:
                self._current_map_id = map_id
            # Explicit localization is an operator-approved graph re-anchor.
            # A pose/auto request has no trustworthy node, so discard a stale
            # value such as the incorrectly inferred p36.
            self._set_last_node(goal or "", 0)
            self.request_state_publish("localized")
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FINISHED,
                result_description=(
                    f"localized to {goal}" if goal else f"localized by {target}"
                ),
            )
            print(
                f"[LOCALIZE] actionId={action.action_id} target={target} "
                f"goal={goal} node={node} "
                f"pose=({pose_x}, {pose_y}, {pose_theta}) mapId={map_id}"
            )

        self._run_on_adapter_loop(_localize)

    # UmGetRobotInfo 재조회용. gap=0 은 로봇이 응답하지 않는다(실측). 200ms 는
    # scripts/probe-jibot-localize-theta.py 가 실기에서 쓴 값이고, 1초짜리
    # robot_info_loop 가 다음 주기에 원래 주기로 되돌린다.
    _POSE_REFRESH_GAP_MS = 200
    _POSE_REFRESH_TIMEOUT_SEC = 1.5

    async def _refresh_vehicle_pose(self) -> None:
        """명령 직전에 pose 캐시를 다시 읽는다. 실패해도 그냥 진행한다.

        `_vehicle._x/_y` 는 main.py `robot_info_loop` 가 interval_sec=1 로 돌리는
        폴링 결과라 최대 1초 묵어 있다. 그 값을 goto 목표로 쓰면 그 사이 로봇이
        굴러간 만큼 목표가 뒤에 찍히고, 로봇은 거기까지 후진한 뒤 회전한다.
        localize 직후에는 재앵커가 텔레메트리에 반영되기까지 실측 1.3초
        (2026-08-22 HN-SH6-TR-002, scripts/probe-jibot-localize-theta.py) 라
        그 오차가 미터 단위가 된다.

        응답 큐는 건드리지 않는다. UmGoto accept 대기 같은 다른 대기와 공유하는
        자원이라 여기서 프레임을 꺼내면 그쪽이 굶는다. 대신 클라이언트가 스냅샷을
        갱신할 때 찍는 `_status_snapshot_at` 이 움직이는지만 본다. 스탬프가 없는
        구현(시뮬레이터, 테스트 페이크)이면 캐시를 그대로 쓴다.
        """
        vehicle = self._vehicle
        request = getattr(vehicle, "um_get_robot_info", None)
        if vehicle is None or not callable(request):
            return

        before = getattr(vehicle, "_status_snapshot_at", None)
        try:
            await request(gap=self._POSE_REFRESH_GAP_MS)
        except Exception as exc:
            print(f"[POSE REFRESH] UmGetRobotInfo 실패: {exc}")
            return

        if before is None:
            return

        deadline = time.monotonic() + self._POSE_REFRESH_TIMEOUT_SEC
        while getattr(vehicle, "_status_snapshot_at", before) == before:
            if time.monotonic() >= deadline:
                print(
                    "[POSE REFRESH] UmGetRobotInfo 응답이 "
                    f"{self._POSE_REFRESH_TIMEOUT_SEC}s 안에 오지 않음 — "
                    "캐시된 pose 로 진행"
                )
                return
            await asyncio.sleep(0.05)

    @staticmethod
    def _shortest_heading_error_deg(target_deg: float, current_deg: float) -> float:
        """target - current 를 [-180, 180) 으로 감아 짧은 쪽 부호를 준다."""
        return (float(target_deg) - float(current_deg) + 180.0) % 360.0 - 180.0

    def _rotate_to_uses_jog(self) -> bool:
        """제자리 회전을 UmDrive 조그로 할 것인가.

        기본이 조그다. pose goto 는 제자리 회전이 아니다 — urobot 은
        target=pose 를 늘 경로로 풀어서, 목표 자세로 진입하려고 뒤로 물러났다가
        돈다 (2026-08-22 HN-SH6-TR-002 실기; 명령 직전 pose 재조회를 넣어
        좌표 지연을 없앤 뒤에도 후진이 그대로였다).

        UmDrive 가 없는 구현(jibot-simulator)은 자동으로 goto 폴백이다.
        """
        mode = str(getattr(self.config.settings, "rotate_to_mode", "jog")).strip().lower()
        if mode == "goto":
            return False
        return callable(getattr(self._vehicle, "um_drive", None)) and callable(
            getattr(self._vehicle, "um_stop", None)
        )

    async def _rotate_to_with_jog(
        self,
        action: Any,
        theta_deg: float,
        tolerance_deg: float,
        timeout_sec: float,
    ) -> Optional[str]:
        """UmDrive 조그 폐루프로 제자리 회전. 실패 사유, 성공이면 None.

        UmDrive 는 거리 개념이 없는 연속 속도 명령(fire-and-forget)이라 경로
        계획을 타지 않는다. 대신 "멈춰라"를 우리가 책임져야 해서 종료 경로마다
        UmStop 을 보낸다 — 취소로 빠져나갈 때도 마찬가지다.

        heading 은 `_refresh_vehicle_pose()` 로 매 tick 다시 읽는다. 기본 폴링은
        1Hz(main.py robot_info_loop) 라 그대로 쓰면 한 tick 사이에 목표를
        지나친다.

        표본 하나로 도달을 확정하지 않고, 멈춘 뒤 다시 읽어 확인한다. 2026-08-23
        62 실기에서 89deg -> 270deg(정확히 180도) 회전이 172도만 돌고 끝났다:
        회전 중 -91 표본 하나에서 도달로 판정하고 멈췄는데 실제 안착은 -83
        이었다(연속 9표본). 이 텔레메트리는 정수 도 단위이고 이상표본이 섞인다 —
        같은 로그에서 정지 상태의 -83 연속 중에 -90 표본 하나가 끼었고 x 도
        27mm 튀었다. 그래서 (1) 허용치 안 표본을 rotate_to_confirm_samples 번
        연속으로 봐야 도달이고 (2) UmStop 뒤 감속이 끝나기를 기다렸다가 다시
        읽어, 벗어나 있으면 rotate_to_settle_retries 번까지 재보정한다.
        """
        settings = self.config.settings
        confirm_samples = max(
            1, int(float(getattr(settings, "rotate_to_confirm_samples", 2)))
        )
        settle_retries = max(
            0, int(float(getattr(settings, "rotate_to_settle_retries", 2)))
        )
        settle_sec = max(0.0, float(getattr(settings, "rotate_to_settle_sec", 0.6)))
        deadline = time.monotonic() + max(0.0, float(timeout_sec))

        attempt = 0
        while True:
            reason = await self._rotate_to_jog_pass(
                action, theta_deg, tolerance_deg, deadline, confirm_samples
            )
            if reason is not None:
                return reason

            # UmStop 은 감속을 시작시킬 뿐이라, 멈춘 자리는 판정한 자리와 다르다.
            error = await self._rotate_to_settled_error(theta_deg, settle_sec)
            if error is None or abs(error) <= tolerance_deg:
                # heading 을 못 읽는 경우(None)는 방금 연속 표본으로 도달을 확인한
                # 직후라 그 판정을 뒤집지 않는다. 위치 대기가 텔레메트리 공백을
                # 다루는 방식과 같다.
                return None

            attempt += 1
            if attempt > settle_retries or time.monotonic() >= deadline:
                return (
                    f"rotateTo stopped {error:.1f}deg off {theta_deg}deg "
                    f"(+/-{tolerance_deg}) after {attempt} settle retries"
                )
            print(
                f"[ROTATE TO] actionId={action.action_id} "
                f"멈춘 자리가 {error:.1f}deg 어긋남 — 재보정 "
                f"{attempt}/{settle_retries}"
            )

    async def _rotate_to_settled_error(
        self, theta_deg: float, settle_sec: float
    ) -> Optional[float]:
        """감속이 끝나기를 기다렸다가 heading 을 다시 읽어 오차를 낸다.

        heading 을 못 읽으면 None. 자동주행 정지 대기(`_has_active_automatic_motion`)
        는 쓰지 않는다 — 그건 UmGoto 소유 여부라 조그에는 걸리지 않고, 플래그가
        붙어 있으면 standstill_timeout_sec(기본 10초)를 통째로 태운다.
        """
        if settle_sec > 0:
            await asyncio.sleep(settle_sec)
        await self._refresh_vehicle_pose()
        current = self._optional_float(getattr(self._vehicle, "_th", None))
        if current is None:
            return None
        return self._shortest_heading_error_deg(theta_deg, current)

    async def _rotate_to_jog_pass(
        self,
        action: Any,
        theta_deg: float,
        tolerance_deg: float,
        deadline: float,
        confirm_samples: int,
    ) -> Optional[str]:
        """조그 한 차례. 도달하면 None, 아니면 실패 사유. 끝에 UmStop 을 보낸다."""
        settings = self.config.settings
        rot = abs(float(getattr(settings, "rotate_to_rot", 30.0)))
        slow_rot = abs(float(getattr(settings, "rotate_to_slow_rot", 10.0)))
        slow_zone = abs(float(getattr(settings, "rotate_to_slow_zone_deg", 20.0)))
        speed = float(getattr(settings, "rotate_to_speed", 200.0))
        lat = float(getattr(settings, "rotate_to_lat", 0.0))
        sign = -1.0 if float(getattr(settings, "rotate_to_rot_sign", 1)) < 0 else 1.0
        tick = max(0.01, float(getattr(settings, "rotate_to_tick_sec", 0.2)))

        last_seen: Optional[float] = None
        confirmed = 0
        # blockingType=NONE 이면 스텝이 빠진 뒤에도 이 루프가 배경에서 돈다.
        # 그 사이 워커가 다음 노드로 출발하면 조그가 그 UmGoto 와 싸우고,
        # 우리가 끝에 보내는 UmStop 이 남의 주행을 세운다. 그때는 양보한다.
        motion_seq = self._order_node_motion_seq
        superseded = False
        try:
            while True:
                if self._order_node_motion_seq != motion_seq:
                    superseded = True
                    return (
                        "rotateTo superseded by the next node drive; "
                        "send it as SOFT/HARD so driving waits for the turn"
                    )
                await self._refresh_vehicle_pose()
                current = self._optional_float(getattr(self._vehicle, "_th", None))
                if current is not None:
                    last_seen = current
                    error = self._shortest_heading_error_deg(theta_deg, current)
                    if abs(error) <= tolerance_deg:
                        confirmed += 1
                        if confirmed >= confirm_samples:
                            print(
                                f"[ROTATE TO] actionId={action.action_id} "
                                f"heading={current}deg 도달 (목표 {theta_deg}deg, "
                                f"오차 {error:.1f}deg, 연속 {confirmed}표본)"
                            )
                            return None
                        # 확인 표본을 더 받는 동안에는 밀지 않는다. 여기서 더 밀면
                        # 확인하는 사이에 목표를 지나친다.
                        if time.monotonic() >= deadline:
                            return (
                                f"rotateTo timed out before the heading held "
                                f"{theta_deg}deg (+/-{tolerance_deg}) for "
                                f"{confirm_samples} samples; last={current}deg"
                            )
                        await asyncio.sleep(tick)
                        continue
                    confirmed = 0
                else:
                    # heading 을 모르는 동안은 돌리지 않는다. 자세 없이 미는 건
                    # 위치 대기가 텔레메트리 공백을 다루는 방식과 같이, 타임아웃을
                    # 태우는 쪽이 맞다.
                    error = None

                if time.monotonic() >= deadline:
                    if last_seen is None:
                        return "rotateTo needs a known heading (th)"
                    return (
                        f"rotateTo timed out before the heading reached "
                        f"{theta_deg}deg (+/-{tolerance_deg}); last={last_seen}deg"
                    )

                if error is not None:
                    magnitude = rot if abs(error) > slow_zone else slow_rot
                    rot_command = sign * magnitude * (1.0 if error > 0 else -1.0)
                    await self._vehicle.um_drive(0.0, rot_command, speed, lat)

                await asyncio.sleep(tick)
        finally:
            if superseded:
                print(
                    f"[ROTATE TO] actionId={action.action_id} "
                    "다음 노드 주행이 시작되어 회전을 넘긴다 (UmStop 보내지 않음)"
                )
            else:
                try:
                    await self._vehicle.um_stop()
                except Exception as exc:
                    print(f"[ROTATE TO] UmStop 실패: {exc}")

    def _handle_rotate_to_instant_action(self, action: Any) -> None:
        """Turn in place to an absolute heading, without going anywhere.

        JIBOT has no rotate command, so this is a pose goto at the robot's own
        current x/y — the heading is the only field that changes. Holding the
        live position matters: substituting 0 for an unknown pose would drive
        the robot to the map origin instead of turning it, so an unknown pose
        fails instead.

        그 x/y 는 주행이 끝난 뒤 다시 읽는다(_refresh_vehicle_pose). 캐시는 최대
        1초 묵어 있어서, 그대로 쓰면 그 사이 굴러간 만큼 목표가 뒤에 찍히고
        로봇이 회전 전에 후진한다.

        ``thetaDeg`` is degrees, JIBOT's native unit, and the name says so.
        VDA5050 carries headings in radians elsewhere (nodePosition.theta), and
        a bare ``theta`` here would eventually be filled in with one.

        FINISHED is published only once the reported heading is within
        ``toleranceDeg``. A HARD-blocking node action that reports success on
        dispatch lets FMS start the next step while the robot is still turning.
        """
        params = {param.key: param.value for param in action.action_parameters}
        try:
            theta_deg = float(params["thetaDeg"])
        except (KeyError, TypeError, ValueError):
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="rotateTo requires numeric thetaDeg (degrees)",
            )
            return

        settings = self.config.settings
        try:
            tolerance_deg = abs(float(
                params.get("toleranceDeg")
                if params.get("toleranceDeg") not in (None, "")
                else getattr(settings, "rotate_to_tolerance_deg", 3.0)
            ))
            timeout_sec = float(
                params.get("timeoutSec")
                if params.get("timeoutSec") not in (None, "")
                else getattr(settings, "rotate_to_timeout_sec", 30.0)
            )
        except (TypeError, ValueError):
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="rotateTo requires numeric toleranceDeg/timeoutSec",
            )
            return

        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _rotate() -> None:
            # 잡을 자리는 '지금 서 있는 자리'여야 한다. 주행이 끝나기 전에
            # 읽으면 도착 자리가 아니고, 캐시를 그대로 쓰면 최대 1초 묵은
            # 자리다. 둘 다 목표를 뒤에 찍어 로봇이 회전 전에 후진하게 만든다.
            try:
                await self._wait_until_automatic_motion_stopped()
            except asyncio.TimeoutError:
                standstill_timeout = getattr(
                    self.config.settings, "standstill_timeout_sec", 10.0
                )
                print(
                    f"[ROTATE TO] actionId={action.action_id} "
                    f"{standstill_timeout}s 동안 자동주행이 끝나지 않음 — "
                    "현재 pose 로 진행"
                )
            if self._rotate_to_uses_jog():
                print(
                    f"[ROTATE TO] actionId={action.action_id} theta={theta_deg}deg "
                    f"mode=jog tolerance={tolerance_deg} timeout={timeout_sec}"
                )
                reason = await self._rotate_to_with_jog(
                    action, theta_deg, tolerance_deg, timeout_sec
                )
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FINISHED if reason is None else ActionStatus.FAILED,
                    result_description=(
                        f"heading {theta_deg}deg reached" if reason is None else reason
                    ),
                )
                return

            # goto 폴백: 잡을 자리가 필요하니 명령 직전에 다시 읽는다.
            await self._refresh_vehicle_pose()

            cur_x = self._optional_float(getattr(self._vehicle, "_x", None))
            cur_y = self._optional_float(getattr(self._vehicle, "_y", None))
            if cur_x is None or cur_y is None:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description="rotateTo needs a known current pose (x, y)",
                )
                return

            print(
                f"[ROTATE TO] actionId={action.action_id} theta={theta_deg}deg "
                f"hold=({cur_x}, {cur_y}) tolerance={tolerance_deg} "
                f"timeout={timeout_sec}"
            )
            try:
                await self._vehicle.goto_xyz(cur_x, cur_y, theta_deg)
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=f"rotateTo failed: {exc}",
                )
                return

            settled = await self._wait_until_heading_reached(
                theta_deg, tolerance_deg, timeout_sec
            )
            if settled:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FINISHED,
                    result_description=f"heading {theta_deg}deg reached",
                )
            else:
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=(
                        f"rotateTo timed out before the heading reached "
                        f"{theta_deg}deg (+/-{tolerance_deg})"
                    ),
                )

        self._run_on_adapter_loop(_rotate, action_id=action.action_id)

    async def _wait_until_heading_reached(
        self, theta_deg: float, tolerance_deg: float, timeout_sec: float
    ) -> bool:
        """Poll the reported heading until it is within tolerance of the target.

        Returns False on timeout rather than raising: the caller turns that into
        a FAILED action, which is the honest report — a rotation the adapter
        cannot confirm must not be published as done.

        An unknown heading never counts as reached and never ends the wait early;
        it just burns the timeout, the same way the position waits treat a
        telemetry gap.
        """
        settings = self.config.settings
        poll_sec = float(getattr(settings, "node_position_poll_interval_sec", 0.2))
        deadline = time.monotonic() + max(0.0, float(timeout_sec))

        while True:
            current = self._optional_float(getattr(self._vehicle, "_th", None))
            if current is not None:
                # Wrap to (-180, 180] so 359deg vs 1deg reads as 2deg apart.
                delta = (float(theta_deg) - current + 180.0) % 360.0 - 180.0
                if abs(delta) <= tolerance_deg:
                    return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(poll_sec)

    def _handle_cancel_order_instant_action(self, action_id: str) -> None:
        self._update_instant_action_status(action_id, ActionStatus.RUNNING)
        print(f"[CANCEL ORDER] actionId={action_id} requested.")

        cancelled_order_id = (
            getattr(self.order, "order_id", "")
            or (self.state.order_id if self.state is not None else "")
        )
        self._run_on_adapter_loop(
            lambda: self._cancel_order_on_loop(action_id, cancelled_order_id)
        )

    async def _cancel_order_on_loop(
        self,
        action_id: str,
        cancelled_order_id: str = "",
    ) -> None:
        if self.order_worker_task is not None and not self.order_worker_task.done():
            self.order_worker_task.cancel()

        await self._cancel_order_background_actions()

        self._clear_order_queue()

        stop_error: Optional[Exception] = None
        if self._vehicle is not None:
            try:
                await self._vehicle.stop_motion()
                await self._wait_until_vehicle_standstill()
                print("[CANCEL ORDER] stop_motion sent; vehicle stands still.")
            except Exception as exc:
                stop_error = exc
                print(f"[CANCEL ORDER] stop_motion failed: {exc}")

        current_order_id = (
            getattr(self.order, "order_id", "")
            or (self.state.order_id if self.state is not None else "")
        )
        newer_order_active = bool(current_order_id) and (
            current_order_id != cancelled_order_id
        )
        if newer_order_active:
            print(
                "[CANCEL ORDER] preserving newer order "
                f"cancelledOrderId={cancelled_order_id} "
                f"currentOrderId={current_order_id}"
            )
        else:
            # The targeted order is cancelled regardless of whether motion-stop
            # succeeded. Do not clear a newer order accepted while standstill was
            # awaited.
            self._clear_cancelled_order_state()

        # The order is always cancelled here, so report cancelOrder as FINISHED.
        # The VDA5050 action result reflects the ORDER, not whether the robot
        # physically stopped; a motion-stop failure is surfaced in the result
        # description (and via driving/safety telemetry). FAILED is reserved for a
        # rejected cancel, on which the FMS deliberately KEEPS the order/destination
        # — reporting FAILED here would leave the FMS order snapshot stale.
        if newer_order_active:
            result_description = (
                "Previous order cancelled; newer order preserved: "
                f"{current_order_id}"
            )
        elif stop_error is not None:
            result_description = (
                f"Order cancelled; vehicle stop not confirmed: {stop_error}"
            )
        else:
            result_description = "Order cancelled"
        self._update_instant_action_status(
            action_id,
            ActionStatus.FINISHED,
            result_description=result_description,
        )
        print("[CANCEL ORDER] queued order cleared and motion stop requested.")

    async def _cancel_order_background_actions(self) -> None:
        tasks = self._active_order_background_action_tasks()
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._order_background_action_tasks.difference_update(tasks)
        self._order_exclusive_background_action_tasks.difference_update(tasks)

    def _clear_cancelled_order_state(self) -> None:
        self.order = None
        self._dispatched_order_action_ids.clear()
        self._charge_node = False
        self._active_goto_node = None

        if self.state is None:
            return

        self.state.order_id = ""
        self.state.order_update_id = 0
        self.state.node_states = []
        self.state.edge_states = []
        self.state.action_states = []
        # 취소된 오더가 세워 둔 newBaseRequest 가 다음 오더 없이도 계속 true 로
        # 남으면, 아무 오더도 없는 로봇이 "곧 base 가 바닥난다"고 거짓 보고하는
        # 셈이 된다(§6.6.3 신호의 의미 훼손) — 취소/워커 크래시 공용 경로라
        # cancelOrder 와 _drop_order_after_worker_crash 양쪽 다 여기서 걷힌다.
        self._clear_new_base_request(reason="order cleared")
        self.state.information = [
            info
            for info in self.state.information
            if getattr(info, "info_type", None) != "ORDER_INFO"
        ]
        # 오더가 사라졌으니 그 오더에만 의미가 있던 거절 사유도 같이 걷는다. 안 그러면
        # orderId="" 인데 "Current order is still in progress" 가 계속 발행돼 FMS 가
        # 로봇을 영구 미완료로 본다(2026-08-25 07:02 HN-SH6-TR-002).
        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) not in _ORDER_SCOPED_ERROR_TYPES
        ]

    def _drop_order_after_worker_crash(self, exc: BaseException) -> None:
        """오더 워커가 예기치 못한 예외로 죽으면 오더를 확실히 끝낸다.

        예전에는 예외만 찍고 끝냈다. state.order_id 가 그대로 남아 이후 오더가 전부
        ORDER_CURRENT_NOT_FINISHED 로 거절되고, 사람이 cancelOrder 를 보내기 전까지
        로봇이 좀비 오더에 묶였다(2026-08-25 06:58:59 HN-SH6-TR-002: JIBOT 링크가
        끊긴 상태에서 UmGoto 발행 → "'NoneType' object has no attribute 'write'" 로
        워커 사망 → 06:58:59.402 다음 오더 4노드 통째 거절 → 06:59:35 수동 취소).

        수락 시점 링크 검사만으로는 부족하다 — 주행 도중 링크가 끊기면 같은 예외가
        같은 자리에서 난다. 여기가 "워커가 죽으면 오더는 반드시 끝난다" 를 지키는 곳이다.
        """
        if self.state is None:
            return

        failed_order_id = (
            getattr(self.order, "order_id", "")
            or str(getattr(self.state, "order_id", "") or "")
        )
        self._clear_order_queue()
        # order_reject 가 errorReferences 키로 state.order_id 를 쓰므로 비우기 전에 부른다
        self.order_reject(
            True,
            ErrorLevel.FATAL,
            ErrorType.UNKNOWN_ERROR,
            f"Order worker crashed: {exc}",
            error_hint="Re-send the order; the adapter dropped it to avoid a stuck order",
        )
        self._clear_cancelled_order_state()
        print(
            f"[ORDER QUEUE] order dropped after worker crash "
            f"orderId={failed_order_id} error={exc}"
        )
        self.request_state_publish("order worker crashed")

    async def _wait_until_vehicle_standstill(
        self, poll_sec: float = None, timeout_sec: float = None
    ) -> None:
        if poll_sec is None:
            poll_sec = getattr(self.config.settings, "standstill_poll_interval_sec", 0.1)
        if timeout_sec is None:
            timeout_sec = getattr(self.config.settings, "standstill_timeout_sec", 10.0)

        async def _poll() -> None:
            while self._derive_driving():
                await asyncio.sleep(poll_sec)

        if timeout_sec and timeout_sec > 0:
            # Bound the wait: a robot stuck reporting motion must not hang cancel
            # forever. asyncio.TimeoutError propagates to the caller, which still
            # clears order state and reports the cancel as FAILED.
            await asyncio.wait_for(_poll(), timeout=timeout_sec)
        else:
            await _poll()

    def _handle_set_sound_volume_instant_action(self, action: Any) -> None:
        params = {p.key: p.value for p in action.action_parameters}
        try:
            if "volume" in params:
                volume = self._clamp_sound_volume(params["volume"])
                # pactl runs with pactl_timeout_sec, so it goes to the worker
                # like every other blocking SoundPlayer call. _clamp_sound_volume
                # above is what can actually reject the request; _pactl only
                # logs its own failures, so FINISHED never depended on it.
                self._submit_sound(self._sound.set_volume, volume)
                self._persist_sound_startup_volume(volume)
            mute = params.get("mute")
            if mute is not None:
                self._submit_sound(
                    self._sound.set_mute,
                    str(mute).strip().lower() in ("1", "true", "yes", "on"),
                )
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED, "sound volume updated"
            )
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"setSoundVolume failed: {exc}"
            )

    @staticmethod
    def _clamp_sound_volume(value: Any) -> int:
        return max(0, min(100, int(float(value))))

    def _persist_sound_startup_volume(self, volume: int) -> None:
        """음량을 config.toml 에 기억시킨다.

        web UI 저장·WCS 설정 형상관리 적용과 같은 파일을 고치므로 configio.write_config
        로 몰아 읽기-쓰기 사이에 남의 수정이 끼어들어 유실되는 것을 막는다.
        """
        self.config.sound_settings.startup_volume = volume
        literal = configio.toml_literal(volume, "int")
        configio.write_config(
            self._config_path,
            lambda text: configio.set_scalar(
                text, "sound_settings", "startup_volume", literal
            ),
        )

    def _handle_test_sound_instant_action(self, action: Any) -> None:
        params = {p.key: p.value for p in action.action_parameters}
        ss = self.config.sound_settings
        # Default to a track that actually ships under sound_dir. The WebUI "Play"
        # button sends no params; travel_music/work_music are deprecated (their
        # files no longer exist -> silent), so fall back to the convention file
        # driving.mp3. Explicit sound=travel/work still maps to the legacy keys.
        sound = str(params.get("sound", "driving.mp3"))
        track = {"travel": ss.travel_music, "work": ss.work_music}.get(sound, sound)
        try:
            seconds = float(params.get("seconds", self._sound_test_seconds))
        except (TypeError, ValueError):
            seconds = self._sound_test_seconds
        self._sound_test_until = time.monotonic() + seconds
        try:
            # Goes through _dispatch_sound so the auto driver's next
            # _update_sound_for_working_state sees this as the current request
            # and does not immediately re-submit the same track. repeat 0: the
            # test is bounded by _sound_test_until, not by a play count.
            self._dispatch_sound((track, 0.0, True, 0))
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED,
                f"testing {track} for {seconds:.0f}s",
            )
        except Exception as exc:  # noqa: BLE001
            self._sound_test_until = 0.0
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"testSound failed: {exc}"
            )

    def _handle_stop_sound_instant_action(self, action: Any) -> None:
        self._sound_test_until = 0.0
        try:
            # force: an operator pressing Stop must reach the player even when
            # the adapter's last request was already "silence".
            self._dispatch_sound(None, force=True)
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED, "sound stopped"
            )
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"stopSound failed: {exc}"
            )

    def _handle_upload_sound_instant_action(self, action: Any) -> None:
        params = {p.key: p.value for p in action.action_parameters}
        file_name = os.path.basename(str(params.get("fileName", "")).strip())
        if not file_name or not file_name.lower().endswith(".mp3"):
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                "fileName must be a .mp3 basename",
            )
            return
        try:
            raw = base64.b64decode(str(params.get("data", "")), validate=True)
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"invalid base64: {exc}"
            )
            return
        if not raw:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, "uploadSound: empty data"
            )
            return
        try:
            target_dir = self._sound.sound_dir
            os.makedirs(target_dir, exist_ok=True)
            dest = os.path.join(target_dir, file_name)
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, dest)
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED,
                f"stored {file_name} ({len(raw)} bytes)",
            )
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"write failed: {exc}"
            )

    def submit_local_instant_action(
        self,
        action_type: str,
        parameters: Optional[Mapping[str, Any]] = None,
        *,
        source: str = "local",
    ) -> str:
        """Run one locally-originated action through the instant-action path.

        Inputs that are not the FMS (today: the joystick) must not reach the
        handlers by a private side door — the accept procedure is where
        work/order gating, status reporting and the action registry live, so a
        local action gets exactly the same treatment as a published one. The
        synthesized id carries the source so the FMS can tell the two apart in
        instantActionStates.
        """
        self.header_id += 1
        action_id = f"{source}-{action_type}-{self.header_id}"
        request = InstantActions.from_dict({
            "headerId": self.header_id,
            "timestamp": utils.get_timestamp(),
            "version": self.config.vehicle.vda_full_version,
            "manufacturer": self.config.vehicle.manufacturer,
            "serialNumber": self.config.vehicle.serial_number,
            "actions": [{
                "actionType": action_type,
                "actionId": action_id,
                "actionDescriptor": f"{source} {action_type}",
                # NONE is the only blockingType the accept procedure allows for
                # instant actions, and a local trigger has no order to block.
                "blockingType": "NONE",
                "actionParameters": [
                    {"key": str(key), "value": value}
                    for key, value in dict(parameters or {}).items()
                ],
            }],
        })
        self.instant_actions_accept_procedure(request)
        return action_id

    def _update_instant_action_status(
        self,
        action_id: str,
        action_status: ActionStatus,
        result_description: Optional[str] = None,
    ) -> None:
        future = self._action_completions.get(action_id)
        if future is not None:
            if self._is_terminal_action_status(action_status):
                self._action_completions.pop(action_id, None)
                self._synthetic_action_ids.add(action_id)
                if not future.done():
                    future.set_result(
                        ActionResult(action_status, result_description or "")
                    )
            return
        if action_id in self._synthetic_action_ids:
            print(f"[ACTION BRIDGE LATE] {action_id} -> {action_status}")
            return
        if self.state is None:
            return

        for action_state in self.state.instant_action_states:
            if action_state.action_id == action_id:
                action_state.action_status = action_status
                if result_description is not None:
                    action_state.result_description = result_description
                if self._is_terminal_action_status(action_status):
                    self._prune_retained_instant_action_states()
                self.request_state_publish("instant action status")
                return

    def _register_action_completion(self, action_id: str) -> asyncio.Future[Any]:
        if action_id in self._action_completions:
            raise RuntimeError(f"action completion already registered: {action_id}")
        future = asyncio.get_running_loop().create_future()
        self._synthetic_action_ids.discard(action_id)
        self._action_completions[action_id] = future
        return future

    def _retire_action_completion(self, action_id: str) -> None:
        future = self._action_completions.pop(action_id, None)
        if future is not None and not future.done():
            future.cancel()
        self._synthetic_action_ids.add(action_id)

    async def _cancel_action_task(self, action_id: str) -> None:
        task = self._action_tasks.pop(action_id, None)
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _forget_synthetic_actions(self, action_ids: Any) -> None:
        for action_id in action_ids:
            self._action_completions.pop(action_id, None)
            self._synthetic_action_ids.discard(action_id)


    # Manage InstantAction
    # According to Type of Action, Action method will be different 
    def manage_instant_actions(self, instant_actions_request: InstantActions ):
        print(instant_actions_request)

        
    def _all_node_actions_waiting_or_failed(self) -> bool:
        """True if every action on current node_states has ActionState WAITING or FAILED."""
        if self.state is None:
            return False
        
        status_by_action_id = {a.action_id: a.action_status for a in self.state.action_states}
        for node in self.state.node_states:
            actions = getattr(node, "actions", None) or []
            for act in actions:
                aid = getattr(act, "action_id", None)
                if not aid:
                    continue
                st = status_by_action_id.get(aid)
                if st is None or st not in (ActionStatus.FINISHED, ActionStatus.FAILED):
                    return False
        return True


    def _all_node_actions_finished_or_failed(self) -> bool:
        """True if every action on current node_states has ActionState FINISHED or FAILED."""
        if self.state is None:
            return False
        
        # status_by_action_id = {a.action_id: a.action_status for a in self.state.action_states}
        for node in self.state.action_states:
            if node.action_status not in (ActionStatus.FINISHED, ActionStatus.FAILED):
                return False
        return True

    # -------------------------
    # ORDER VALIDATION PROCEDURE
    # -------------------------   
    def cancel_order(self) -> None:
        pass

    # -------------------------
    # NEW ORDER ACCEPT PROCEDURE 
    # -------------------------   
    def _angle_error(self,target: float, current: float) -> float:
        """Smallest absolute angle difference in radians."""
        return abs((target - current + math.pi) % (2 * math.pi) - math.pi)

    def _is_active_action_status(self, action_status: ActionStatus) -> bool:
        status_value = (
            action_status.value
            if isinstance(action_status, ActionStatus)
            else str(action_status)
        )
        return status_value in {
            ActionStatus.INITIALIZING.value,
            ActionStatus.WAITING.value,
            ActionStatus.RUNNING.value,
            ActionStatus.PAUSED.value,
            "INITIALIZATION",  # Compatibility with external/non-standard status naming
        }

    def _is_terminal_action_status(self, action_status: ActionStatus) -> bool:
        status_value = (
            action_status.value
            if isinstance(action_status, ActionStatus)
            else str(action_status)
        )
        return status_value in {ActionStatus.FINISHED.value, ActionStatus.FAILED.value}

    def _prune_retained_instant_action_states(self) -> None:
        """Bound the terminal instantActionStates the adapter keeps reporting.

        Terminal states are retained so the FMS can read a FINISHED/FAILED
        result — dropping one the moment it turns terminal makes it
        unobservable, because request_state_publish() only wakes the publish
        loop and the entry is already gone when the state message is built.
        _accept_v3_order_for_queue() resets the list on every new order, but a
        shift of manual driving may never accept one while the joystick submits
        an instant action per button press, so cap the retained terminal
        history here. Non-terminal states are never dropped.
        """
        if self.state is None:
            return

        limit = self._max_retained_terminal_instant_action_states
        terminal = [
            action_state
            for action_state in self.state.instant_action_states
            if self._is_terminal_action_status(action_state.action_status)
        ]
        if len(terminal) <= limit:
            return

        dropped = {id(action_state) for action_state in terminal[: len(terminal) - limit]}
        self.state.instant_action_states = [
            action_state
            for action_state in self.state.instant_action_states
            if id(action_state) not in dropped
        ]

    def _sync_docking_action_state(self) -> None:
        """Surface the docking maneuver as a RUNNING ``dock`` instant-action state.

        Docking is driven by order node motion (``UmDock``), not by an FMS
        instant action, and is tracked via ``_docking_started_node_ids``. To let
        the FMS/eq observe it, we keep a synthetic RUNNING ``dock`` entry in
        ``instant_action_states`` while a dock maneuver is in progress; the eq
        derives ``workingStateDetail=DOCKING`` from this ``actionType``. Order
        completion is unaffected (``_is_v3_order_finished`` checks node/edge
        states only). Idempotent: safe to call on every publish cycle.
        """
        if self.state is None:
            return

        action_id = self._docking_status_action_id
        docking = bool(self._docking_started_node_ids)
        existing = next(
            (
                action_state
                for action_state in self.state.instant_action_states
                if action_state.action_id == action_id
            ),
            None,
        )
        if docking and existing is None:
            self.state.instant_action_states.append(
                ActionState(
                    action_id=action_id,
                    action_status=ActionStatus.RUNNING,
                    action_type=self._docking_status_action_type,
                    action_description="Docking maneuver in progress",
                )
            )
        elif not docking and existing is not None:
            self.state.instant_action_states = [
                action_state
                for action_state in self.state.instant_action_states
                if action_state.action_id != action_id
            ]

    def _find_node_start_index(self, node_ids: Set[str]) -> Optional[int]:
        if self.state is None or not self.state.node_states or not node_ids:
            return None

        for index, node in enumerate(self.state.node_states):
            if node.node_id in node_ids:
                return index
        return None

    def get_sequence_by_node(self, node_id: str) -> Optional[int]:
        for node in self.state.node_states:
            if node.node_id == node_id:
                return node.sequence_id
        return None

    def _get_allowed_deviation_xy(self, deviation_xy: Any) -> float:
        if deviation_xy is None:
            return 0.0
        if isinstance(deviation_xy, dict):
            return max(
                abs(float(deviation_xy.get("a", 0.0) or 0.0)),
                abs(float(deviation_xy.get("b", 0.0) or 0.0)),
            )
        return abs(float(deviation_xy))

    def _effective_reach_deviation_xy(self, deviation_xy: Any = None) -> float:
        deviation = self._get_allowed_deviation_xy(deviation_xy)
        if deviation <= 0.0:
            deviation = float(
                getattr(self.config.settings, "default_node_deviation_xy", 10.0)
            )
        zone_scale = float(getattr(self.config.settings, "reach_zone_scale", 1.0) or 1.0)
        effective_deviation = abs(float(deviation)) * zone_scale
        if effective_deviation <= 0.0:
            return 0.01
        return effective_deviation

    def _idle_last_node_reach_xy(self) -> float:
        threshold = float(
            getattr(self.config.settings, "idle_last_node_reach_xy", 500.0) or 0.0
        )
        if threshold <= 0.0:
            threshold = self._effective_reach_deviation_xy()
        return threshold

    def _missing_last_node_reach_xy(self) -> float:
        """Gate for the empty-lastNodeId seed; 0 (default) means no gate.

        Deliberately not _idle_last_node_reach_xy(): that one rewrites <=0 into
        the deviation fallback, which would make "no gate" unexpressible here
        and silently narrow a seed that shipped ungated.
        """
        return float(
            getattr(self.config.settings, "missing_last_node_reach_xy", 0.0) or 0.0
        )

    def _last_node_release_xy(self) -> float:
        """캡처해 둔 lastNodeId 를 더 이상 믿을 수 없는 거리.

        0 이면 해제하지 않는다(캡처만 하던 예전 settled 동작).
        기본값은 Settings.last_node_release_xy 에 있다 — 로봇 config.toml 은
        배포 때 덮이지 않으므로 기본값이 곧 현장 동작이다.
        """
        return float(
            getattr(self.config.settings, "last_node_release_xy", 0.0) or 0.0
        )

    def _missing_last_node_seed_gate(self) -> float:
        """빈 lastNodeId seed 의 실효 게이트. 해제 반경으로 캡을 씌운다.

        seed 는 게이트 없이 출하됐다(missing_last_node_reach_xy = 0). 캡이 없으면
        _release_stale_last_node 가 방금 떨군 그 노드를 곧바로 다시 seed 해서,
        pose 갱신마다 lastNodeId 가 노드와 "" 사이를 오간다.
        """
        gate = self._missing_last_node_reach_xy()
        release = self._last_node_release_xy()
        if release <= 0.0 or self._last_node_capture_mode() != "settled":
            return gate
        return release if gate <= 0.0 else min(gate, release)

    def _release_stale_last_node(self, x: float, y: float) -> bool:
        """pose 가 캡처한 노드를 벗어나면 lastNodeId 를 비운다.

        settled 는 idle_last_node_reach_xy 안에서 쓰기만 하고 반납하는 경로가
        없었다. 그래서 수동 주행으로 몇 미터를 나가도 떠난 노드를 계속 publish
        했다(2026-08-23 로봇 62: p36~p37 사이인데 lastNodeId=p2). 판단 근거는
        pose 이므로 조그가 끝나기를 기다리지 않는다.

        v3 오더가 active 면 건너뛴다 — 그때 lastNodeId 는 오더 진척도라서 다음
        노드로 가는 동안 도착했던 노드에 남아 있는 것이 정상이다. settled 가
        아니어도 건너뛴다: proximity 는 어차피 pose 로 매번 덮어쓰고, disabled 는
        pose 가 lastNodeId 를 쓰지 않는다는 뜻이다.
        비웠으면 True 를 반환한다.
        """
        release = self._last_node_release_xy()
        if release <= 0.0:
            return False
        if self._last_node_capture_mode() != "settled":
            return False
        last_node_id = str(self._last_node_id or "").strip()
        if not last_node_id:
            return False
        if self._is_v3_order_active():
            return False
        gap = self._distance_to_node_id(last_node_id, SimpleNamespace(x=x, y=y))
        if gap is None or gap <= release:
            return False
        print(
            f"[LAST NODE RELEASE] lastNodeId={last_node_id} "
            f"gap={gap:.1f} > release={release:.1f}"
        )
        self._set_last_node("", 0)
        return True

    def _goto_nearest_timeout_sec(self) -> float:
        value = float(
            getattr(self.config.settings, "goto_nearest_timeout_sec", 60.0) or 0.0
        )
        return value if value > 0.0 else 60.0

    def _last_node_capture_mode(self) -> str:
        """Validated lastNodeId capture mode. Read every use (config-swappable
        via restart). Unknown values fall back to 'settled' with a one-time
        warning so a config typo never silently changes behavior."""
        valid = ("proximity", "disabled", "settled")
        mode = str(
            getattr(self.config.settings, "last_node_capture_mode", "settled") or ""
        ).strip().lower()
        if mode in valid:
            return mode
        if not self._last_node_capture_mode_warned:
            print(
                f"[CONFIG] unknown last_node_capture_mode={mode!r}; "
                f"falling back to 'settled'"
            )
            self._last_node_capture_mode_warned = True
        return "settled"

    def _set_last_node(self, node_id: str, sequence_id: int) -> None:
        """Single writer for lastNodeId. Always updates the private fields and
        mirrors them onto self.state when present. Replaces the scattered inline
        (private + state) write pairs so capture/bootstrap paths stay consistent."""
        self._last_node_id = node_id
        self._last_node_sequence_id = sequence_id
        if self.state is not None:
            self.state.last_node_id = node_id
            self.state.last_node_sequence_id = sequence_id

    def _all_order_nodes(self) -> List[Any]:
        """Every node of the active order, including ones already passed.

        Completed nodes are dropped from ``state.node_states`` as the order
        queue clears each step, so that list only holds the remaining route.
        ``self.order`` keeps the original full node list, which is what we
        need to map a position back onto an already-passed node.
        """
        if self.order is not None and self.order.nodes:
            return self.order.nodes
        if self.state is not None and self.state.node_states:
            return self.state.node_states
        return []

    def _map_nodes(self) -> Dict[str, Any]:
        """Node positions fetched from the robot map: {name: (x, y, theta)}."""
        if self._is_simulator() and self._fms_map_nodes:
            return self._fms_map_nodes
        if self._vehicle is None:
            return {}
        return getattr(self._vehicle, "_map_nodes", None) or {}

    def _map_path_points(self) -> Dict[str, Any]:
        """PathPoint positions fetched from the robot map."""
        if self._is_simulator():
            # Simulator/FMS snapshots expose one flat node map without JIBOT
            # object categories. With PathPoint as the default driving graph,
            # treat those snapshot nodes as PathPoint candidates.
            if self._fms_map_nodes:
                return self._fms_map_nodes
            if self._vehicle is not None:
                return getattr(self._vehicle, "_map_nodes", None) or {}
        if self._vehicle is None:
            return {}
        return getattr(self._vehicle, "_map_path_points", None) or {}

    def _nearest_node_mode(self) -> str:
        """Return the normalized nearest-node candidate mode."""
        configured = str(
            getattr(self.config.settings, "nearest_node_mode", "pathPoint") or ""
        ).strip().lower()
        if configured in ("headinggoal", "heading_goal", "heading-goal"):
            return "headingGoal"
        return "pathPoint"

    def _nearest_mode_map_nodes(self) -> Dict[str, Any]:
        if self._nearest_node_mode() == "pathPoint":
            path_points = self._map_path_points()
            if path_points:
                return path_points
            # Compatibility for legacy maps/snapshots that do not expose
            # PathPoint objects at all. PathPoint remains preferred whenever
            # at least one is available.
            return self._map_nodes()
        return self._map_nodes()

    def _node_xy(self, node: Any) -> Optional[Tuple[float, float]]:
        """Resolve a node's (x, y), preferring its order position.

        Orders may carry node ids without a nodePosition, so fall back to the
        coordinates the robot map reports for that node id. PathPoint is
        checked first because it is the default FMS driving-node graph; named
        Goal/GoalWithHeading/Dock objects remain valid semantic destinations.
        """
        pos = getattr(node, "node_position", None)
        if pos is not None and pos.x is not None and pos.y is not None:
            return float(pos.x), float(pos.y)

        node_id = getattr(node, "node_id", None)
        map_pos = self._map_path_points().get(node_id)
        if map_pos is None:
            map_pos = self._map_nodes().get(node_id)
        if map_pos is not None:
            return float(map_pos[0]), float(map_pos[1])
        return None

    def _find_nearest_node(
        self,
        x: float,
        y: float,
    ) -> Optional[Tuple[str, int, float]]:
        """Return the node closest to (x, y) plus its distance.

        Candidates follow settings.nearest_node_mode. In headingGoal mode they
        are addressable nodes known from the robot map; in pathPoint mode they
        are JIBOT PathPoint poses only. If the chosen map
        node is also present in the active order/state, its order sequenceId is
        reported; map-only nodes use sequenceId 0. When no map node is
        available in headingGoal mode, fall back to order nodePosition
        coordinates. Returns
        (node_id, sequence_id, distance) or None when nothing can be matched.
        """
        # (node_id, sequence_id, nx, ny)
        candidates: List[Tuple[str, int, float, float]] = []
        sequence_by_node = {
            getattr(node, "node_id", ""): int(getattr(node, "sequence_id", 0) or 0)
            for node in self._all_order_nodes()
            if getattr(node, "node_id", "")
        }

        nearest_mode = self._nearest_node_mode()
        for name, pose in self._nearest_mode_map_nodes().items():
            node_id = str(name)
            candidates.append(
                (
                    node_id,
                    sequence_by_node.get(node_id, 0),
                    float(pose[0]),
                    float(pose[1]),
                )
            )

        if not candidates and nearest_mode == "headingGoal":
            for node in self._all_order_nodes():
                xy = self._node_xy(node)
                if xy is not None:
                    candidates.append((node.node_id, node.sequence_id, xy[0], xy[1]))

        best: Optional[Tuple[float, str, int]] = None
        for node_id, sequence_id, nx, ny in candidates:
            distance_xy = math.hypot(x - nx, y - ny)
            if best is None or distance_xy < best[0]:
                best = (distance_xy, node_id, sequence_id)

        if best is None:
            return None
        return best[1], best[2], best[0]

    def _distance_to_node_id(self, node_id: str, position: Any) -> Optional[float]:
        if not node_id or position is None:
            return None
        try:
            x = float(getattr(position, "x"))
            y = float(getattr(position, "y"))
        except (AttributeError, TypeError, ValueError):
            return None

        # nearestNodeId candidates follow nearest_node_mode, but an existing
        # lastNodeId may be either a driving PathPoint or a semantic
        # Goal/GoalWithHeading/Dock. Resolve both kinds for diagnostics and
        # order-resume guards regardless of the active candidate mode.
        for map_nodes in (
            self._nearest_mode_map_nodes(),
            self._map_path_points(),
            self._map_nodes(),
        ):
            map_pos = map_nodes.get(node_id)
            if map_pos is not None:
                return math.hypot(x - float(map_pos[0]), y - float(map_pos[1]))

        for node in self._all_order_nodes():
            if getattr(node, "node_id", None) != node_id:
                continue
            xy = self._node_xy(node)
            if xy is not None:
                return math.hypot(x - xy[0], y - xy[1])
        return None

    def _clear_nearest_node_information(self) -> None:
        """Drop any existing NEAREST_NODE info entry from the state."""
        if self.state is None:
            return
        self.state.information = [
            info
            for info in self.state.information
            if getattr(info, "info_type", None) != "NEAREST_NODE"
        ]

    def _refresh_nearest_node_information(self) -> None:
        """Republish the NEAREST_NODE telemetry from the current nearest-node
        state (``_nearest_node_id`` / ``_nearest_node_distance``).

        The ``_nearest_node_*`` fields are the single source of truth; this
        entry only mirrors them. It is visualization/debug telemetry only
        (VDA5050 forbids using state.information for fleet-control logic). The
        gap is reported in the robot's raw pose units; the unit is advertised
        once in the factsheet (coordinateUnits.position).

        When a lastNodeId is published we ALSO surface the gap between the
        current pose and that node (``lastNodeId`` / ``lastNodeGap``). lastNodeId
        is the last *reached* node, not a live position, so this gap is the only
        way to tell whether the robot is actually parked at the node it claims.
        Unlike the same diagnostic in the LAST_NODE_ID_MISSING error, this one
        is always on (that error is suppressed precisely when lastNodeId is
        healthy), mirroring the always-on nearest gap.
        """
        self._clear_nearest_node_information()
        if (
            self.state is None
            or not self._nearest_node_id
            or self._nearest_node_distance is None
        ):
            return
        references = [
            InfoReference("nearestNodeId", self._nearest_node_id),
            InfoReference("gap", f"{self._nearest_node_distance:.1f}"),
        ]
        last_node_id = str(self._last_node_id or "").strip()
        if last_node_id:
            last_node_gap = self._distance_to_node_id(
                last_node_id, getattr(self.state, "agv_position", None)
            )
            references.append(InfoReference("lastNodeId", last_node_id))
            references.append(
                InfoReference(
                    "lastNodeGap",
                    "" if last_node_gap is None else f"{last_node_gap:.1f}",
                )
            )
        self.state.information.append(
            Information(
                info_type="NEAREST_NODE",
                info_level=InfoLevel.INFO,
                info_references=references,
                info_description=(
                    "Distance between the robot and the nearest map node, plus "
                    "the gap to lastNodeId when present, in factsheet "
                    "coordinateUnits.position (telemetry only)"
                ),
            )
        )

    def _capture_idle_last_node(
        self, node_id: str, sequence_id: int, distance: float
    ) -> None:
        """Dispatch pose-based lastNodeId capture on the configured mode.

        ``proximity`` is intentionally order-agnostic: the closest map node is
        always treated as the current node. ``settled`` keeps the safer
        idle/stopped/distance gates, and ``disabled`` never captures from pose.
        Called from _update_nearest_node_from_position after the nearest map
        node and its distance are known.
        """
        mode = self._last_node_capture_mode()
        if mode == "disabled":
            self._capture_idle_disabled(node_id, sequence_id, distance)
        elif mode == "proximity":
            self._capture_idle_proximity(node_id, sequence_id, distance)
        else:
            self._capture_idle_settled(node_id, sequence_id, distance)

    def _capture_idle_proximity(
        self, node_id: str, sequence_id: int, distance: float
    ) -> None:
        """Always capture the nearest map node, regardless of distance or
        active order state."""
        self._set_last_node(node_id, sequence_id)

    def _capture_idle_disabled(
        self, node_id: str, sequence_id: int, distance: float
    ) -> None:
        """No-op: lastNodeId is never advanced by idle capture in this mode.
        Explicit (vs. an inline branch) so the policy is unit-testable."""
        return

    def _capture_idle_settled(
        self, node_id: str, sequence_id: int, distance: float
    ) -> None:
        """Proximity capture, but only when the robot is actually idle, at rest,
        and not under manual control -- prevents lastNodeId churn while the
        operator jogs (Bug #1). The stopped-gate covers steady jogging (driving
        status); the manual flag additionally suppresses capture during brief
        jog pauses."""
        if self._is_v3_order_active():
            return
        if self._manual_control_active:
            return
        if not self._is_jibot_stopped():
            return
        if distance > self._idle_last_node_reach_xy():
            return
        self._set_last_node(node_id, sequence_id)

    def _update_nearest_node_from_position(
        self,
        x: float,
        y: float,
        theta: Optional[float] = None,
    ) -> None:
        debug = bool(getattr(self.config.settings, "debug_log", False))
        matched_node = self._find_nearest_node(x, y)
        if matched_node is None:
            # No map node and no order nodePosition to match against: clear the
            # nearest-node telemetry so stale values are never published.
            self._nearest_node_id = ""
            self._nearest_node_sequence_id = 0
            self._nearest_node_distance = None
            self._clear_nearest_node_information()
            if debug:
                print(
                    f"[NEAREST NODE] no match for pos=({x:.1f}, {y:.1f}); "
                    f"orderNodes={len(self._all_order_nodes())} "
                    f"mapNodes={len(self._map_nodes())}"
                )
            return

        node_id, sequence_id, distance = matched_node
        self._nearest_node_id = node_id
        self._nearest_node_sequence_id = sequence_id
        self._nearest_node_distance = distance

        # 현재 pose 가 더는 뒷받침하지 못하는 lastNodeId 를 먼저 떨군다(수동 조그,
        # 밀림 등 로봇을 노드 밖으로 옮긴 모든 경우). 아래 seed/capture 가 무엇을
        # 쓸지 정하기 전에 실행돼야 한다.
        self._release_stale_last_node(x, y)

        state_last_node_id = (
            str(getattr(self.state, "last_node_id", "") or "").strip()
            if self.state is not None
            else ""
        )
        if (
            bool(
                getattr(
                    self.config.settings,
                    "use_nearest_node_as_last_node_when_missing",
                    False,
                )
            )
            and not str(self._last_node_id or "").strip()
            and not state_last_node_id
        ):
            gate = self._missing_last_node_seed_gate()
            if gate > 0.0 and distance > gate:
                # Log the refusal: a silently empty lastNodeId is reported as
                # LAST_NODE_ID_MISSING later, with no hint that a gate caused it.
                print(
                    f"[LAST NODE FALLBACK SKIP] nearestNodeId={node_id} "
                    f"gap={distance:.1f} > reach={gate:.1f}"
                )
            else:
                self._set_last_node(node_id, sequence_id)
                print(
                    f"[LAST NODE FALLBACK] nearestNodeId={node_id} "
                    f"sequenceId={sequence_id} gap={distance:.1f}"
                )

        self._refresh_nearest_node_information()

        # Pose update: advance lastNodeId from the nearest map node per the
        # configured last_node_capture_mode (see _capture_idle_last_node).
        # In proximity mode this is an unconditional nearest-node mirror;
        # settled mode keeps the idle/stopped/distance gates.
        self._capture_idle_last_node(node_id, sequence_id, distance)

        if debug:
            print(
                f"[NEAREST NODE] pos=({x:.1f}, {y:.1f}) -> nearest={node_id} "
                f"seq={sequence_id} gap={distance:.1f}mm"
            )


    # Node Released False=>True True=>False updating according to requested sequence_id
    def build_action_states(self, order_request: Order) -> List[ActionState]:
        action_states: List[ActionState] = []

        combined = []

        # Collect nodes
        for node in order_request.nodes:
            combined.append(("node", node.sequence_id, node))

        # Collect edges
        for edge in order_request.edges:
            combined.append(("edge", edge.sequence_id, edge))

        # Sort by sequence_id
        combined.sort(key=lambda x: x[1])

        # Build action states
        for source_type, sequence_id, obj in combined:
            actions = getattr(obj, "actions", []) or []
            for action in actions:
                action_states.append(
                    ActionState(
                        # The FMS matches on the actionId it sent; a
                        # "{kind}_{seq}_" prefix makes every entry unmatchable.
                        action_id=action.action_id,
                        action_status=ActionStatus.WAITING,
                        action_type=action.action_type,
                        action_description=action.action_descriptor,
                    )
                )

        return action_states
    

    # -------------------------
    # ORDER REJECT PROCEDURE 
    # ------------------------- 
    def order_reject(
        self,
        is_new_order: bool,
        error_level: ErrorLevel,
        error_type: ErrorType,
        description: str = "",
        error_hint: Optional[str] = None,
    ) -> None:
        """Reject an order and append an Error object to state.errors."""
        if self.state is None:
            return

        # Combine description and optional hint into a single description field
        full_description = description
        if error_hint:
            full_description = f"{description} Hint: {error_hint}"

        error_ref = ErrorReference(
            reference_key=self.state.order_id or "N/A",
            reference_value=description or "Order rejected",
        )
        error_ref_datetime = ErrorReference(
            reference_key="timestamp",
            reference_value=datetime.now().isoformat(),
        )

        error_obj = Error(
            error_type=error_type,
            error_level=error_level,
            error_references=[error_ref, error_ref_datetime],
            error_description=full_description or None,
        )

        self.state.errors.append(error_obj)

        if is_new_order:
            self.add_state_info(
                InfoType.ORDER_NEW_REJECTED,
                description=full_description or None,
                info_level= InfoLevel.DEBUG,
                info_reference_key= "orderId",
                info_reference_value = self.order.order_id if self.order else "",
            )
        else:
            self.add_state_info(
                InfoType.ORDER_UPDATE_REJECTED,
                description=full_description or None,
                info_level= InfoLevel.DEBUG,
                info_reference_key= "orderId",
                info_reference_value = self.order.order_id if self.order else "",
            )

        self.request_state_publish("order rejected")


    # -------------------------
    # ADD INFO TO STATE PROCEDURE
    # ------------------------- 
    def add_state_info(
        self,
        info_type: InfoType,
        description: str = "",
        info_level: InfoLevel = InfoLevel.INFO,
        info_reference_key: Optional[str] = None,
        info_reference_value: Optional[str] = None,
    ) -> None:
        """Append an info object to state.infos."""
        if self.state is None:
            return

        full_description = description


        info_ref = InfoReference(
            reference_key=info_reference_key,
            reference_value=info_reference_value,
        )
        
        info_ref_datetime = InfoReference(
            reference_key="timestamp",
            reference_value=datetime.now().isoformat(),
        )

        info_obj = Information(
            info_type=info_type,
            info_level=info_level,
            info_description=full_description or None,
            info_references=[info_ref, info_ref_datetime],
        )

        self.state.information.append(info_obj)
