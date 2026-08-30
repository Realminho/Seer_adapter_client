"""Offline tests for the v3 order worker and standard instant actions.

No MQTT broker or robot is needed: the MQTT client publishes into the void
(paho buffers/no-conn) and the vehicle is a fake that moves on demand.
"""

import asyncio
import json
import math
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest import mock
from unittest.mock import patch

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_ROOT.parent
for p in (ADAPTER_ROOT, REPO_ROOT / "jibot-simulator", REPO_ROOT / "jibot-client" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from adapter_jibot import Adapter, OrderStep
from config.config import DockApproachParams, MotionRule, Settings
from extensions.clamp import execute_clamp_action
from extensions.ezio import execute_ezio_action, manage_tray_slot, read_ezio_input_bits
from extensions.pio import execute_pio_action
from jibot_position_store import load_position_snapshot
from protocol.vda_2_0_0.vda5050_2_0_0_state import (
    ActionState,
    ActionStatus,
    EStop,
    Error,
    ErrorLevel,
    ErrorReference,
    ErrorType,
    OperatingMode,
)
from utils.charge_circuit import FakeChargeCircuit
from utils.ezi_motor import EziMotorClient
from protocol.vda5050_3_0.messages import InstantActions, Node, NodePosition, Order


def _work_instant_actions(action_type: str, action_id: str) -> InstantActions:
    """Build a one-action InstantActions request for loading/unloading tests."""
    return InstantActions.from_dict(
        {
            "headerId": 1,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "actions": [
                {
                    "actionType": action_type,
                    "actionId": action_id,
                    "blockingType": "NONE",
                    "actionParameters": [],
                }
            ],
        }
    )


def _one_node_order(order_id: str) -> Order:
    """Minimal released one-node order for accept/reject tests."""
    return Order.from_dict(
        {
            "headerId": 1,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "orderId": order_id,
            "orderUpdateId": 0,
            "nodes": [
                {
                    "nodeId": "N1",
                    "sequenceId": 0,
                    "released": True,
                    "nodePosition": {
                        "x": 0.0, "y": 0.0, "theta": 0.0, "mapId": "lab2m",
                    },
                    "actions": [],
                }
            ],
            "edges": [],
        }
    )


class _FakeSound:
    def __init__(self):
        self.played = []
        self.looped = []
        self.repeats = []
        self.stops = 0
        self.volumes = []
        self.mutes = []
        self.sound_dir = "/tmp/snd"
        self.tracks = set()  # filenames that "exist" for has_track()

    def has_track(self, track):
        return track in self.tracks

    def play(self, track, loop=True, replay_gap_sec=0.0, repeat_count=0):
        self.played.append((track, replay_gap_sec))
        self.looped.append((track, loop))
        self.repeats.append((track, repeat_count))

    def stop(self):
        self.stops += 1

    def set_volume(self, percent):
        self.volumes.append(percent)

    def set_mute(self, muted):
        self.mutes.append(muted)

    def is_playing(self):
        return bool(self.played)


class FakeVehicle:
    """Minimal JIBOT stand-in: stores commands, moves only when told to."""

    def __init__(self) -> None:
        self._x = 0.0
        self._y = 0.0
        self._th = 0.0
        self._battery = 80.0
        self._localization_score = 1.0
        self._charging = False
        self._motor_flag = 1
        self._mode = "auto"
        self._status = "Stopped"
        self._map_nodes: Dict[str, Any] = {}
        self._map_node_categories: Dict[str, str] = {}
        self._map_path_points: Dict[str, Any] = {}
        self.robot_ip = "127.0.0.1"
        self.robot_port = 7273
        self.goto_targets = []
        self.route_calls = []
        self.goto_response: Optional[Dict[str, Any]] = {"#CMD#": "UmGoto", "result": "accepted"}
        self.last_wait_for_response_kwargs = None
        self.commands = []
        self.localize_calls = []
        self.stop_motion_calls = 0
        self.stop_charge_calls = 0
        self.dock_calls = 0
        self.last_dock_params: Dict[str, Any] = {}
        self.laser_calls = []
        self._laser_raw = None
        self._pending_target: Optional[str] = None
        self.is_simulator = False
        self.um_stop_calls = 0
        # 충전 릴레이가 어댑터의 hold 로 붙잡혀 있는지. hold 중에는 UmStop 이
        # ModeCharge 만 걷어내고 충전은 남는다 — 이것이 dock 후 인계의 전제다
        # (docs/reference/jibot-charging-dock-bms.md: 릴레이는 assert 를 유지하는
        # 동안만 닫혀 있고, HMI 그린버튼도 ModeCharge 없이 충전을 유지한다).
        self.charge_relay_held = False
        self.drive_calls = []
        self.move_distance_calls = []
        self.enable_motor_calls = 0

    def is_connected(self) -> bool:
        return True

    def is_rx_stale(self, timeout: float) -> bool:
        return False

    def seconds_since_last_rx(self) -> float:
        return 0.0

    def build_command(self, command: str, gap: int = -1, **params: Any) -> Dict[str, Any]:
        return {"#CMD#": command, "#GAP#": gap, **params}

    async def goto_point(self, point: str, strict: bool = False) -> None:
        self.goto_targets.append(point)
        self._pending_target = point

    async def goto_xyz(self, x: float, y: float, z: float, strict: bool = False) -> None:
        self.goto_targets.append((x, y, z))

    def get_path_point_pose(self, name: str):
        return self._map_path_points.get(name)

    async def call_routes(self, name, key, id=None) -> None:
        self.route_calls.append((name, key, id))

    async def wait_for_response(self, command=None, timeout=3.0, accept_errors=False):
        self.last_wait_for_response_kwargs = {
            "command": command,
            "timeout": timeout,
            "accept_errors": accept_errors,
        }
        return self.goto_response

    async def um_set_volume(self, volume: int, gap: int = -1) -> None:
        self.commands.append(("UmSetVolume", volume))

    async def um_localize(self, target, goal, poseX, poseY, poseTh, gap=-1) -> None:
        self.localize_calls.append((target, goal, poseX, poseY, poseTh))

    async def um_dock(self, gap: int = -1, **params) -> None:
        self.dock_calls += 1
        self.last_dock_params = params

    async def um_stop(self, gap: int = -1) -> None:
        self.um_stop_calls += 1
        self.stop_charge_calls += 1
        if self.charge_relay_held:
            # hold 가 릴레이를 다시 닫으므로 충전은 유지된다. ModeCharge 만 빠진다.
            self._status = "charging"
            return
        self._charging = False
        self._status = "Stopped"

    async def get_laser(self, index: int = 0, interval_ms: int = 200, timeout: float = 1.0) -> Dict[str, Any]:
        self.laser_calls.append((index, interval_ms, timeout))
        self._laser_raw = {
            "#CMD#": "UmGetLaser",
            "data": [{"num": 2, "points": "1 2 3 4"}],
        }
        return self._laser_raw

    async def stop_motion(self) -> None:
        self.stop_motion_calls += 1

    async def um_drive(self, trans, rot, speed, lat, gap: int = -1) -> None:
        self.drive_calls.append((trans, rot, speed, lat))

    async def move_distance(self, distance, speed, **kwargs) -> None:
        self.move_distance_calls.append((distance, speed, kwargs))

    async def enable_motor(self) -> None:
        self.enable_motor_calls += 1

    def arrive_at(self, x: float, y: float) -> None:
        self._x = x
        self._y = y

    # 실차는 주행 중 mode="MRosGoto" / status="nrunto ..." 를 보고한다
    # (2026-08-26 192.168.101.50:7274 실측). fake 가 늘 "Stopped" 라서
    # _has_active_automatic_motion 이 항상 False 였고, 그 탓에
    # _settle_goto_arrival 이 전 테스트에서 no-op 이었다.
    def start_driving(self, goal: str = "test") -> None:
        self._mode = "MRosGoto"
        self._status = f"nrunto {goal}"

    def stop_driving(self) -> None:
        self._mode = "auto"
        self._status = "Stopped"

    def is_driving(self) -> bool:
        return "goto" in str(self._mode).lower()


class _VehicleLinkedChargeCircuit(FakeChargeCircuit):
    """hold 상태를 FakeVehicle 로 전달하는 테스트용 charge circuit.

    실차에서 relay hold 는 /jcmd cmd:3=1 을 계속 publish 해 릴레이를 닫아 두므로,
    UmStop 으로 ModeCharge 를 빠져나와도 충전이 남는다. 이 연결이 없으면 fake 는
    "UmStop = 충전 종료" 만 알고 있어 dock 후 인계가 항상 실패로 보인다.
    """

    def __init__(self, vehicle) -> None:
        super().__init__()
        self._vehicle = vehicle

    def start_hold(self) -> None:
        super().start_hold()
        self._vehicle.charge_relay_held = True

    def stop_hold(self) -> None:
        super().stop_hold()
        self._vehicle.charge_relay_held = False
        self._vehicle._charging = False


class PollingVehicle(FakeVehicle):
    def __init__(self, *, is_simulator: bool) -> None:
        super().__init__()
        self.is_simulator = is_simulator
        self.map_calls = 0
        self.robot_info_calls = 0

    async def get_robot_info(self, interval_ms: float) -> None:
        self.robot_info_calls += 1
        self._x = 12.5
        self._y = 34.0
        self._th = 90.0

    async def get_motor_state(self, interval_ms: float) -> None:
        return None

    async def get_localization_info(self, interval_ms: float) -> None:
        return None

    async def get_map(self) -> Dict[str, Any]:
        self.map_calls += 1
        if self.is_simulator:
            raise AssertionError("simulator must not request map from jibot-client")
        self._map_nodes = {"N1": (0.0, 0.0, 0.0)}
        return self._map_nodes


class BlockingStopVehicle(FakeVehicle):
    def __init__(self) -> None:
        super().__init__()
        self._status = "Driving"
        self.stop_started = asyncio.Event()
        self.stop_release = asyncio.Event()

    async def stop_motion(self) -> None:
        self.stop_motion_calls += 1
        self.stop_started.set()
        await self.stop_release.wait()
        self._status = "Stopped"


class FakeClampMotor:
    def __init__(
        self,
        move_comm_status=0,
        move_returns_none=False,
        servo_on=False,
        axis_status_script=None,
        axis_status_none=False,
        actual_position=None,
        actual_position_none=False,
        position_none_times=0,
        servo_off_comm_status=0,
        teach_hangs=False,
    ) -> None:
        self.calls = []
        # Model the EZI drive's per-command result: 0 = accepted, nonzero =
        # rejected (servo off / alarm / ...), None return = comms timeout.
        self._move_comm_status = move_comm_status
        self._move_returns_none = move_returns_none
        self._servo_off_comm_status = servo_off_comm_status
        # 축 상태 대본. 한 번에 하나씩 소비하고 소진되면 마지막 상태를 유지한다.
        # 항목이 None이면 그 폴링은 무응답(UDP 유실)으로 본다.
        # 대본 자체가 None이면 servo_enable()/goto_*() 호출이 곧바로 반영되고 이동은
        # 즉시 끝난 것으로 본다.
        self._axis_status_script = list(axis_status_script or [])
        self._axis_status_none = axis_status_none
        self._servo_on = servo_on
        # 실제 엔코더 위치. 값을 지정하면 그 자리에 고정되고(=명령을 무시하는 드라이브),
        # 지정하지 않으면 마지막 이동 목표로 즉시 도착한 것으로 본다.
        self._actual_position = 0 if actual_position is None else int(actual_position)
        self._tracks_moves = actual_position is None
        self._actual_position_none = actual_position_none
        # 앞의 몇 번만 무응답(UDP 유실)으로 돌려주고 그 뒤로는 정상 응답한다.
        self._position_none_times = int(position_none_times)
        # 대본이 없을 때의 리미트/원점 도달 플래그. goto_*()가 세운다.
        self._limit_minus = False
        self._limit_plus = False
        self._origin_ret_ok = False
        self._teach_hangs = teach_hangs

    async def alarm_reset(self):
        self.calls.append(("alarm_reset",))
        return {"communication_status": 0}

    async def servo_enable(self, enable=True):
        self.calls.append(("servo_enable", enable))
        if not self._axis_status_script:
            self._servo_on = bool(enable)
        if not enable:
            return {"communication_status": self._servo_off_comm_status}
        return {"communication_status": 0}

    async def get_axis_status(self):
        self.calls.append(("get_axis_status",))
        if self._axis_status_none:
            return None
        if self._axis_status_script:
            entry = (
                self._axis_status_script.pop(0)
                if len(self._axis_status_script) > 1
                else self._axis_status_script[0]
            )
        else:
            entry = {
                "servo_on": self._servo_on,
                "motioning": False,
                "limit_minus": self._limit_minus,
                "limit_plus": self._limit_plus,
                "origin_ret_ok": self._origin_ret_ok,
            }
        if entry is None:
            return None
        flags = {name: False for name in EziMotorClient.AXIS_FLAGS}
        flags["FFLAG_SERVOON"] = bool(entry.get("servo_on"))
        flags["FFLAG_MOTIONING"] = bool(entry.get("motioning"))
        # origin 복귀(원점 탐색) 판정용. 대본에 없으면 기존 대본은 그대로 False로 동작한다.
        flags["FFLAG_ORIGINRETURNING"] = bool(entry.get("origin_returning"))
        flags["FFLAG_ORIGINRETOK"] = bool(entry.get("origin_ret_ok"))
        # 리미트 도달/오류 플래그. 역시 대본에 없으면 False라 기존 대본은 그대로 돈다.
        flags["FFLAG_HWNEGALMT"] = bool(entry.get("limit_minus"))
        flags["FFLAG_HWPOSILMT"] = bool(entry.get("limit_plus"))
        flags["FFLAG_ERRORALL"] = bool(entry.get("error_all"))
        flags["FFLAG_EMGSTOP"] = bool(entry.get("emg_stop"))
        return {"communication_status": 0, "active_flags": flags}

    async def get_actual_position(self):
        self.calls.append(("get_actual_position",))
        if self._actual_position_none:
            return None
        if self._position_none_times > 0:
            self._position_none_times -= 1
            return None
        return {"communication_status": 0, "position": self._actual_position}

    async def move_single_axis_abs_pos(self, position, speed):
        self.calls.append(("move_single_axis_abs_pos", position, speed))
        if self._move_returns_none:
            return None
        if self._tracks_moves and self._move_comm_status == 0:
            self._actual_position = int(position)
        return {"communication_status": self._move_comm_status}

    async def move_stop(self):
        self.calls.append(("move_stop",))
        return {"ok": True}

    async def initialized_open_close_encoder_position(self, origin_encoder_offset=1000):
        self.calls.append(("initialized_open_close_encoder_position", origin_encoder_offset))
        if self._teach_hangs:
            # ezi_motor의 원점 센서 대기 루프처럼 타임아웃 없이 매달린다.
            await asyncio.Event().wait()
        return {"close_gripper": origin_encoder_offset, "open_gripper": -origin_encoder_offset}

    async def goto_limit_minus(self, speed=10000):
        self.calls.append(("goto_limit_minus", speed))
        if not self._axis_status_script:
            self._limit_minus = True
        return {"communication_status": 0}

    async def goto_limit_plus(self, speed=10000):
        self.calls.append(("goto_limit_plus", speed))
        if not self._axis_status_script:
            self._limit_plus = True
        return {"communication_status": 0}

    async def goto_origin(self):
        self.calls.append(("goto_origin",))
        if not self._axis_status_script:
            self._origin_ret_ok = True
        return {"communication_status": 0}


class FakePioClient:
    def __init__(
        self,
        input_frames=None,
        silent=False,
        connect_error=None,
        # 정상 보드는 PIOMaster.make_frame과 같은 <payload+checksum> 프레임으로
        # 답한다. 5C는 "BC=OK"의 실제 checksum_hex 값이다.
        bc_response="<BC=OK5C>",
    ) -> None:
        self.connected = False
        self.closed = False
        self.connect_calls = 0
        self.ser = None
        self.bc_response = bc_response
        self.bc_calls = []
        self.raw_calls = []
        self.monitor_calls = []
        self.input_frames = list(input_frames or ["IN=00000000"])
        # silent: 포트는 열리지만 반대편 보드가 응답하지 않는 상태 (케이블 분리 등)
        self.silent = silent
        self.connect_error = connect_error

    def connect(self):
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True
        self.connect_calls += 1
        self.ser = SimpleNamespace(is_open=True)

    def send_bc(self, media, station_id, channel, port, oht_num, wait_sec=2.0):
        self.bc_calls.append((media, station_id, channel, port, oht_num, wait_sec))
        return self.bc_response

    def send_raw(self, data, wait_sec=2.0):
        self.raw_calls.append((data, wait_sec))
        return "" if self.silent else "OK"

    def monitor_data(self, channel, wait_sec=2.0):
        self.monitor_calls.append((channel, wait_sec))
        if self.silent:
            return ""
        if len(self.input_frames) > 1:
            return self.input_frames.pop(0)
        return self.input_frames[0]

    def close(self):
        self.closed = True
        self.connected = False
        self.ser = None


class FakeEziIo:
    def __init__(self, input_frames=None) -> None:
        self.input_frames = list(input_frames or [[0] * 16])
        self.calls = 0

    async def get_input(self):
        self.calls += 1
        if len(self.input_frames) > 1:
            inputs = self.input_frames.pop(0)
        else:
            inputs = self.input_frames[0]
        return {
            "comm_status": 0,
            "input_raw": 0,
            "latch_raw": 0,
            "inputs": inputs,
            "latches": [0] * 16,
        }


class FakePioFacility:
    """SELECT 출력·GO 입력과 pairing 상태를 함께 흉내내는 EZI-IO 가짜.

    실제 설비 규칙을 그대로 따른다 — SELECT가 올라가 있는 동안 BC가 오면
    pairing 성립, BC 없이 SELECT가 내려가면 해제. 그래서 pair/unpair를 헷갈리면
    테스트가 잡아낸다.

    never_pairs: BC를 받아도 GO를 안 올림.
    stays_paired: unpair 절차를 밟아도 GO를 안 내림.
    """

    def __init__(self, pio, *, paired=False, never_pairs=False, stays_paired=False,
                 pair_after=1) -> None:
        self.pio = pio
        self.outputs = {}
        self.output_calls = []
        self.reset_masks = []
        self.paired = paired
        self.never_pairs = never_pairs
        self.stays_paired = stays_paired
        self.pair_after = pair_after      # 몇 번째 BC에 GO를 올려주는지
        self.bc_attempts = 0
        self.inputs = [0] * 16            # EZI IO digital input 16비트
        self._bc_at_select = 0

    async def turn_on_output(self, n):
        self.outputs[n] = 1
        self.output_calls.append((n, "on"))
        self._bc_at_select = len(self.pio.bc_calls)

    async def turn_off_output(self, n):
        if self.outputs.get(n) == 1:
            if len(self.pio.bc_calls) > self._bc_at_select:
                self.bc_attempts += 1
                self.paired = (
                    not self.never_pairs and self.bc_attempts >= self.pair_after
                )
            elif not self.stays_paired:
                self.paired = False
        self.outputs[n] = 0
        self.output_calls.append((n, "off"))

    async def set_output(self, set_mask=0, reset_mask=0):
        self.reset_masks.append(reset_mask)
        return 0

    async def get_output(self):
        # 쓰기/읽기 비트 맵이 대칭이어야 한다 (utils/ezi_io.output_bit 참고)
        return {"outputs": [self.outputs.get(n, 0) for n in range(16)]}

    async def get_input(self):
        bits = list(self.inputs)
        bits[15] = 1 if self.paired else 0      # GO는 pairing 상태를 따른다
        return {"inputs": bits}

    async def get_input_pin(self, pin):
        if pin == 15:
            return 1 if self.paired else 0
        return self.inputs[pin]


class EmptyEziIo:
    async def get_input(self):
        return None


class RaisingEziIo:
    def __init__(self) -> None:
        self.calls = 0

    async def get_input(self):
        self.calls += 1
        raise AssertionError("simulator mode must not read EZI IO inputs")


class FlakyEziIo:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def get_input(self):
        self.calls += 1
        response = self.responses.pop(0)
        if response is None:
            return None
        return {
            "comm_status": 0,
            "input_raw": 0,
            "latch_raw": 0,
            "inputs": response,
            "latches": [0] * 16,
        }


def make_order(order_id: str = "order-1") -> Order:
    return Order.from_dict(
        {
            "headerId": 1,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "orderId": order_id,
            "orderUpdateId": 0,
            "nodes": [
                {
                    "nodeId": "N1",
                    "sequenceId": 0,
                    "released": True,
                    "nodePosition": {
                        "x": 0.0,
                        "y": 0.0,
                        "theta": 0.0,
                        "mapId": "lab2m",
                        "allowedDeviationXY": 5.0,
                    },
                    "actions": [],
                },
                {
                    "nodeId": "N2",
                    "sequenceId": 2,
                    "released": True,
                    "nodePosition": {
                        "x": 1000.0,
                        "y": 500.0,
                        "theta": 0.0,
                        "mapId": "lab2m",
                        "allowedDeviationXY": 5.0,
                    },
                    "actions": [
                        {
                            "actionType": "UmSetVolume",
                            "actionId": "a-volume",
                            "blockingType": "HARD",
                            "actionParameters": [{"key": "volume", "value": 3}],
                        },
                        {
                            "actionType": "totallyUnknownAction",
                            "actionId": "a-unknown",
                            "blockingType": "HARD",
                            "actionParameters": [],
                        },
                    ],
                },
            ],
            "edges": [
                {
                    "edgeId": "E1",
                    "sequenceId": 1,
                    "released": True,
                    "startNodeId": "N1",
                    "endNodeId": "N2",
                    "actions": [],
                }
            ],
        }
    )


def make_cancel_order_action(action_id: str = "ia-cancel") -> InstantActions:
    return InstantActions.from_dict(
        {
            "headerId": 20,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "actions": [
                {
                    "actionType": "cancelOrder",
                    "actionId": action_id,
                    "blockingType": "NONE",
                    "actionParameters": [],
                }
            ],
        }
    )


def make_laser_action(
    action_type: str,
    action_id: str,
    *,
    interval_ms: int = 10,
    topic: str = "laser/test",
) -> InstantActions:
    return InstantActions.from_dict(
        {
            "headerId": 22,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "actions": [
                {
                    "actionType": action_type,
                    "actionId": action_id,
                    "blockingType": "NONE",
                    "actionParameters": [
                        {"key": "index", "value": 0},
                        {"key": "intervalMs", "value": interval_ms},
                        {"key": "topic", "value": topic},
                    ],
                }
            ],
        }
    )


def make_instant_action(
    action_type: str,
    action_id: str = "ia-hw",
    params: Optional[Dict[str, Any]] = None,
) -> InstantActions:
    return InstantActions.from_dict(
        {
            "headerId": 44,
            "timestamp": "2026-06-19T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "actions": [
                {
                    "actionType": action_type,
                    "actionId": action_id,
                    "blockingType": "NONE",
                    "actionParameters": [
                        {"key": key, "value": value}
                        for key, value in (params or {}).items()
                    ],
                }
            ],
        }
    )


# pioInit/pioPing은 이제 stationId/channel을 실행마다 받는다 — extension "pio"의
# station_id/channel 폴백이 설비 블록으로 옮겨가면서 사라졌다. 이 값 자체는
# 여기 테스트들의 관심사가 아니므로 station "000010"/channel 250을 그냥 쓴다.
_PIO_BC_PARAMS = {"stationId": "000010", "channel": 250}


async def _await_terminal_instant_status(spy, action_id, timeout=4.0):
    """Poll a wrapped _update_instant_action_status spy until ``action_id``
    reaches a terminal (FINISHED/FAILED) status, or the timeout elapses.

    Condition-based wait — robust to event-loop load and test ordering, unlike a
    fixed sleep which can assert before the scheduled handler coroutine runs.
    """
    terminal = (ActionStatus.FINISHED, ActionStatus.FAILED)
    for _ in range(int(timeout / 0.02)):
        if any(
            c.args[0] == action_id and c.args[1] in terminal
            for c in spy.call_args_list
        ):
            return
        await asyncio.sleep(0.02)


def _instant_action_statuses(adapter):
    return {
        state.action_id: state.action_status
        for state in adapter.state.instant_action_states
    }


async def _settle_instant_actions(adapter, *action_ids, timeout=2.0):
    """Wait until every named instant action reports a terminal status.

    Terminal states are retained so the FMS can read the result, so "done" is
    a FINISHED/FAILED entry — not an empty instantActionStates list.
    """
    terminal = (ActionStatus.FINISHED, ActionStatus.FAILED)
    for _ in range(int(timeout / 0.01)):
        statuses = _instant_action_statuses(adapter)
        if all(statuses.get(action_id) in terminal for action_id in action_ids):
            return statuses
        await asyncio.sleep(0.01)
    return _instant_action_statuses(adapter)


class AdapterV3OrderTest(unittest.TestCase):
    def _make_adapter(self) -> Adapter:
        adapter = Adapter()
        vehicle = FakeVehicle()
        adapter.set_vehicle(vehicle)
        adapter.set_charge_circuit(_VehicleLinkedChargeCircuit(vehicle))
        # dock 후 인계의 실차 대기값(hold settle 3s, 확인창 30s)은 테스트에서 의미가
        # 없다. 타이밍 자체는 test_dock_charge_handover.py 가 따로 본다.
        adapter.config.dock.handover_hold_settle_sec = 0.0
        adapter.config.dock.handover_verify_timeout_sec = 2.0
        return adapter

    async def _start_state(self, adapter: Adapter) -> asyncio.Task:
        adapter._loop = asyncio.get_running_loop()
        task = asyncio.create_task(adapter.publish_state("state", interval_sec=0.05))
        for _ in range(100):
            if adapter.state is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(adapter.state)
        return task

    def _blocking_probe_adapter(self, action_types):
        adapter = self._make_adapter()
        states = {
            action_id: ActionState(
                action_id=action_id,
                action_status=ActionStatus.WAITING,
                action_type="probe",
            )
            for action_id, _blocking_type in action_types
        }
        adapter.state = SimpleNamespace(action_states=list(states.values()))
        adapter._find_action_state_for_step = (
            lambda _step, action_id: states[action_id]
        )
        actions = [
            SimpleNamespace(
                action_id=action_id,
                action_type="probe",
                blocking_type=blocking_type,
                action_parameters=[],
            )
            for action_id, blocking_type in action_types
        ]
        return adapter, OrderStep(
            "edge",
            1,
            SimpleNamespace(edge_id="E1", actions=actions),
        )

    def _stop_driving_on_arrival(self, adapter, vehicle) -> None:
        """Model JIBOT ending its goto task the moment the robot arrives.

        A node step is complete only once the goto task has cleared, so a
        fixture that leaves mode/curTask pinned to a running goto models a robot
        that never finishes its approach. Hooked on the arrival wait rather than
        a timer so the arrival-signal diagnostics still see the running task.
        """
        original = adapter._wait_until_node_position_reached

        async def _wait(node, target, *args, **kwargs):
            await original(node, target, *args, **kwargs)
            vehicle._status = "Stopped"
            vehicle._mode = "auto"
            vehicle._cur_task = {}

        adapter._wait_until_node_position_reached = _wait

    def test_hard_waits_for_prior_none_action_before_starting(self) -> None:
        async def scenario() -> None:
            adapter, step = self._blocking_probe_adapter(
                [("none", "NONE"), ("hard", "HARD")]
            )
            release_none = asyncio.Event()
            none_started = asyncio.Event()
            events = []

            async def execute(action, _state, _owner):
                events.append(f"start:{action.action_id}")
                if action.action_id == "none":
                    none_started.set()
                    await release_none.wait()
                events.append(f"end:{action.action_id}")

            adapter._execute_order_action = execute
            task = asyncio.create_task(adapter._process_v3_step_actions(step))
            await asyncio.wait_for(none_started.wait(), timeout=1)
            self.assertNotIn("start:hard", events)

            release_none.set()
            self.assertTrue(await task)
            self.assertEqual(
                events,
                ["start:none", "end:none", "start:hard", "end:hard"],
            )

        asyncio.run(scenario())

    def test_soft_actions_run_in_parallel_and_block_worker(self) -> None:
        async def scenario() -> None:
            adapter, step = self._blocking_probe_adapter(
                [("soft-1", "SOFT"), ("soft-2", "SOFT")]
            )
            release = asyncio.Event()
            both_started = asyncio.Event()
            started = set()

            async def execute(action, _state, _owner):
                started.add(action.action_id)
                if len(started) == 2:
                    both_started.set()
                await release.wait()

            adapter._execute_order_action = execute
            task = asyncio.create_task(adapter._process_v3_step_actions(step))
            await asyncio.wait_for(both_started.wait(), timeout=1)
            self.assertFalse(task.done())

            release.set()
            self.assertTrue(await task)

        asyncio.run(scenario())

    def test_soft_completion_releases_driving_while_none_continues(self) -> None:
        async def scenario() -> None:
            adapter, step = self._blocking_probe_adapter(
                [("none", "NONE"), ("soft", "SOFT")]
            )
            release_none = asyncio.Event()
            release_soft = asyncio.Event()
            started = set()

            async def execute(action, _state, _owner):
                started.add(action.action_id)
                if action.action_id == "none":
                    await release_none.wait()
                else:
                    await release_soft.wait()

            adapter._execute_order_action = execute
            worker = asyncio.create_task(adapter._process_v3_step_actions(step))
            for _ in range(100):
                if started == {"none", "soft"}:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(started, {"none", "soft"})

            release_soft.set()
            self.assertTrue(await asyncio.wait_for(worker, timeout=1))
            self.assertTrue(adapter._active_order_background_action_tasks())

            release_none.set()
            await adapter._await_order_action_tasks(
                adapter._active_order_background_action_tasks()
            )

        asyncio.run(scenario())

    def test_single_is_exclusive_but_does_not_block_driving(self) -> None:
        async def scenario() -> None:
            adapter, step = self._blocking_probe_adapter(
                [("single", "SINGLE"), ("none", "NONE")]
            )
            release_single = asyncio.Event()
            single_started = asyncio.Event()
            events = []

            async def execute(action, _state, _owner):
                events.append(f"start:{action.action_id}")
                if action.action_id == "single":
                    single_started.set()
                    await release_single.wait()
                events.append(f"end:{action.action_id}")

            adapter._execute_order_action = execute
            self.assertTrue(await adapter._process_v3_step_actions(step))
            await asyncio.wait_for(single_started.wait(), timeout=1)
            self.assertNotIn("start:none", events)

            release_single.set()
            await adapter._await_order_action_tasks(
                adapter._active_order_background_action_tasks()
            )
            self.assertEqual(
                events,
                ["start:single", "end:single", "start:none", "end:none"],
            )

        asyncio.run(scenario())

    def test_hard_stops_residual_node_motion_before_action(self) -> None:
        async def scenario() -> None:
            adapter, step = self._blocking_probe_adapter([("hard", "HARD")])
            adapter._vehicle._mode = "MRosGoto"
            adapter._vehicle._status = "nrunto pose (1 2 0)#brake"
            self.assertFalse(adapter._derive_driving())
            adapter._wait_until_automatic_motion_stopped = mock.AsyncMock()
            adapter._execute_order_action = mock.AsyncMock()

            self.assertTrue(await adapter._process_v3_step_actions(step))

            self.assertEqual(adapter._vehicle.stop_motion_calls, 1)
            adapter._wait_until_automatic_motion_stopped.assert_awaited_once()
            adapter._execute_order_action.assert_awaited_once()

        asyncio.run(scenario())

    def test_hard_at_next_action_point_waits_for_background_none(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            release_none = asyncio.Event()
            none_started = asyncio.Event()
            events = []
            states = {
                name: ActionState(
                    action_id=name,
                    action_status=ActionStatus.WAITING,
                    action_type="probe",
                )
                for name in ("none", "hard")
            }
            adapter.state = SimpleNamespace(action_states=list(states.values()))
            adapter._find_action_state_for_step = (
                lambda _step, action_id: states[action_id]
            )
    
            async def execute(action, _state, _owner):
                events.append(f"start:{action.action_id}")
                if action.action_id == "none":
                    none_started.set()
                    await release_none.wait()
                events.append(f"end:{action.action_id}")

            adapter._execute_order_action = execute
            none_step = OrderStep(
                "edge",
                1,
                SimpleNamespace(
                    edge_id="E1",
                    actions=[
                        SimpleNamespace(
                            action_id="none",
                            action_type="probe",
                            blocking_type="NONE",
                            action_parameters=[],
                        )
                    ],
                ),
            )
            hard_step = OrderStep(
                "node",
                2,
                SimpleNamespace(
                    node_id="N2",
                    actions=[
                        SimpleNamespace(
                            action_id="hard",
                            action_type="probe",
                            blocking_type="HARD",
                            action_parameters=[],
                        )
                    ],
                ),
            )

            self.assertTrue(await adapter._process_v3_step_actions(none_step))
            await asyncio.wait_for(none_started.wait(), timeout=1)
            hard_task = asyncio.create_task(
                adapter._process_v3_step_actions(hard_step)
            )
            await asyncio.sleep(0)
            self.assertNotIn("start:hard", events)

            release_none.set()
            self.assertTrue(await hard_task)
            self.assertEqual(
                events,
                ["start:none", "end:none", "start:hard", "end:hard"],
            )

        asyncio.run(scenario())

    def test_next_action_point_waits_for_background_single(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            release_single = asyncio.Event()
            single_started = asyncio.Event()
            events = []
            states = {
                name: ActionState(
                    action_id=name,
                    action_status=ActionStatus.WAITING,
                    action_type="probe",
                )
                for name in ("single", "next-none")
            }
            adapter.state = SimpleNamespace(action_states=list(states.values()))
            adapter._find_action_state_for_step = (
                lambda _step, action_id: states[action_id]
            )
    
            async def execute(action, _state, _owner):
                events.append(f"start:{action.action_id}")
                if action.action_id == "single":
                    single_started.set()
                    await release_single.wait()
                events.append(f"end:{action.action_id}")

            adapter._execute_order_action = execute
            single_step = OrderStep(
                "edge",
                1,
                SimpleNamespace(
                    edge_id="E1",
                    actions=[
                        SimpleNamespace(
                            action_id="single",
                            action_type="probe",
                            blocking_type="SINGLE",
                            action_parameters=[],
                        )
                    ],
                ),
            )
            next_step = OrderStep(
                "node",
                2,
                SimpleNamespace(
                    node_id="N2",
                    actions=[
                        SimpleNamespace(
                            action_id="next-none",
                            action_type="probe",
                            blocking_type="NONE",
                            action_parameters=[],
                        )
                    ],
                ),
            )

            self.assertTrue(await adapter._process_v3_step_actions(single_step))
            await asyncio.wait_for(single_started.wait(), timeout=1)
            self.assertTrue(await adapter._process_v3_step_actions(next_step))
            await asyncio.sleep(0)
            self.assertNotIn("start:next-none", events)

            release_single.set()
            await adapter._await_order_action_tasks(
                adapter._active_order_background_action_tasks()
            )
            self.assertEqual(
                events,
                [
                    "start:single",
                    "end:single",
                    "start:next-none",
                    "end:next-none",
                ],
            )

        asyncio.run(scenario())

    def test_cancel_order_cancels_background_none_action(self) -> None:
        async def scenario() -> None:
            adapter, step = self._blocking_probe_adapter([("none", "NONE")])
            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def execute(_action, _state, _owner):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            adapter._execute_order_action = execute
            self.assertTrue(await adapter._process_v3_step_actions(step))
            await asyncio.wait_for(started.wait(), timeout=1)

            await adapter._cancel_order_background_actions()

            self.assertTrue(cancelled.is_set())
            self.assertEqual(adapter._active_order_background_action_tasks(), ())

        asyncio.run(scenario())

    def test_node_state_dict_is_spec_subset(self) -> None:
        adapter = self._make_adapter()
        order = make_order()
        node_dict = adapter._build_v3_node_state_dict(order.nodes[1])

        self.assertEqual(
            set(node_dict.keys()), {"nodeId", "sequenceId", "released", "nodePosition"}
        )
        self.assertNotIn("actions", node_dict)

        edge_dict = adapter._build_v3_edge_state_dict(order.edges[0])
        self.assertEqual(set(edge_dict.keys()), {"edgeId", "sequenceId", "released"})

    def test_motor_stop_flag_surfaces_as_v3_emergency_stop(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._motor_flag = 0
            state_task = await self._start_state(adapter)
            try:
                await asyncio.sleep(0.05)
                payload = adapter._build_v3_state_message().to_dict()
                self.assertEqual(
                    payload["safetyState"]["activeEmergencyStop"],
                    "MANUAL",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_motor_state_surfaces_in_information(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                await asyncio.sleep(0.05)

                def motor_ref():
                    payload = adapter._build_v3_state_message().to_dict()
                    refs = {
                        r["referenceKey"]: r["referenceValue"]
                        for info in payload.get("information", [])
                        for r in info.get("infoReferences", [])
                    }
                    return refs.get("jibotMotorState")

                vehicle._motor_flag = 0
                adapter._refresh_status_information()
                self.assertEqual(motor_ref(), "stopped")

                vehicle._motor_flag = True
                adapter._refresh_status_information()
                self.assertEqual(motor_ref(), "running")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_startup_mode_uses_fallback_battery_until_first_battery_reading(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.jibot_client.startup_battery_soc = 77.0
            adapter.config.jibot_client.require_battery_before_ready = True
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._battery = None
            vehicle._battery_known = False

            state_task = await self._start_state(adapter)
            try:
                await asyncio.sleep(0.05)
                payload = adapter._build_v3_state_message().to_dict()
                refs = {
                    r["referenceKey"]: r["referenceValue"]
                    for info in payload.get("information", [])
                    for r in info.get("infoReferences", [])
                }

                self.assertEqual(payload["operatingMode"], "STARTUP")
                self.assertEqual(payload["powerSupply"]["stateOfCharge"], 77.0)
                self.assertEqual(refs["adapterInitializing"], "true")
                self.assertEqual(refs["adapterInitPending"], "battery")
                self.assertEqual(refs["jibotBatteryKnown"], "false")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_disconnected_link_reports_unknown_battery_sentinel(self) -> None:
        # When the JIBOT link is down and no real battery reading was ever
        # received, the adapter must NOT leak the startup placeholder (which
        # would falsely read as a full battery). VDA5050 requires a numeric
        # stateOfCharge, so it reports the -1 "unknown" sentinel instead.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.jibot_client.startup_battery_soc = 100.0
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._battery = None
            vehicle._battery_known = False
            vehicle.is_connected = lambda: False  # link is down

            state_task = await self._start_state(adapter)
            try:
                await asyncio.sleep(0.05)
                payload = adapter._build_v3_state_message().to_dict()
                self.assertEqual(payload["powerSupply"]["stateOfCharge"], -1)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_link_drop_after_known_battery_reports_unknown_sentinel(self) -> None:
        # Even after a real reading was received, a dropped link means the value
        # is stale and no fresh one will arrive. Report the unknown sentinel
        # rather than a misleading last value — aligned with JIBOT_CONNECTION_LOST.
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._battery = 80.0
            vehicle._battery_known = True
            vehicle.is_connected = lambda: False  # link dropped after a reading

            state_task = await self._start_state(adapter)
            try:
                await asyncio.sleep(0.05)
                payload = adapter._build_v3_state_message().to_dict()
                self.assertEqual(payload["powerSupply"]["stateOfCharge"], -1)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_first_battery_reading_clears_startup_mode(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.jibot_client.startup_battery_soc = 77.0
            adapter.config.jibot_client.require_battery_before_ready = True
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._battery = 12.0
            vehicle._battery_known = True

            state_task = await self._start_state(adapter)
            try:
                await asyncio.sleep(0.05)
                payload = adapter._build_v3_state_message().to_dict()
                refs = {
                    r["referenceKey"]: r["referenceValue"]
                    for info in payload.get("information", [])
                    for r in info.get("infoReferences", [])
                }

                self.assertEqual(payload["operatingMode"], "AUTOMATIC")
                self.assertEqual(payload["powerSupply"]["stateOfCharge"], 12.0)
                self.assertEqual(refs.get("adapterInitializing"), "false")
                self.assertEqual(refs.get("adapterInitPending"), "")
                self.assertEqual(refs.get("jibotBatteryKnown"), "true")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_jibot_task_and_path_diagnostics_surface_in_information(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle = adapter._vehicle
            vehicle._station = "F1_60"
            vehicle._cur_task = {
                "#CMD#": "UmGetCurTask",
                "data": {
                    "status": "nrunto F1_60",
                    "value": {"cmd": "goto", "goal": "F1_60", "target": "goal"},
                },
            }
            vehicle._task_info = {
                "#CMD#": "UmGetTaskInfo",
                "status": "nrunto F1_60",
            }
            vehicle._path = {
                "#CMD#": "UmGetPath",
                "num": 3,
                "points": [{"x": 13003, "y": 4672}, {"x": 12837, "y": 4782}],
            }
            state_task = await self._start_state(adapter)
            try:
                adapter._refresh_status_information()
                refs = {
                    ref.reference_key: ref.reference_value
                    for info in adapter.state.information
                    if info.info_type == "JIBOT_STATUS"
                    for ref in info.info_references
                }

                self.assertEqual(refs["jibotStation"], "F1_60")
                self.assertEqual(refs["jibotCurTaskCommand"], "goto")
                self.assertEqual(refs["jibotCurTaskGoal"], "F1_60")
                self.assertEqual(refs["jibotCurTaskStatus"], "nrunto F1_60")
                self.assertEqual(refs["jibotTaskInfoStatus"], "nrunto F1_60")
                self.assertEqual(refs["jibotPathNum"], "3")
                self.assertEqual(refs["jibotPathEnd"], '{"x": 12837, "y": 4782}')
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_missing_last_node_id_surfaces_critical_error_until_recovered(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                for _ in range(100):
                    if any(
                        getattr(getattr(error, "error_type", None), "value", None)
                        == "LAST_NODE_ID_MISSING"
                        for error in adapter.state.errors
                    ):
                        break
                    await asyncio.sleep(0.01)

                missing_errors = [
                    error
                    for error in adapter.state.errors
                    if getattr(getattr(error, "error_type", None), "value", None)
                    == "LAST_NODE_ID_MISSING"
                ]
                self.assertEqual(len(missing_errors), 1)
                self.assertEqual(missing_errors[0].error_level, ErrorLevel.CRITICAL)

                adapter._last_node_id = "N1"
                adapter._last_node_sequence_id = 0
                for _ in range(100):
                    if not any(
                        getattr(getattr(error, "error_type", None), "value", None)
                        == "LAST_NODE_ID_MISSING"
                        for error in adapter.state.errors
                    ):
                        break
                    await asyncio.sleep(0.01)

                self.assertFalse(
                    any(
                        getattr(getattr(error, "error_type", None), "value", None)
                        == "LAST_NODE_ID_MISSING"
                        for error in adapter.state.errors
                    )
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_missing_last_node_id_critical_error_includes_last_node_gap(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "N1": (3.0, 4.0, 0.0),
                "N2": (6.0, 8.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.agv_position.x = 3.0
                adapter.state.agv_position.y = 4.0
                adapter.state.last_node_id = "N2"
                adapter._last_node_id = ""
                adapter._nearest_node_id = "N1"
                adapter._nearest_node_distance = 0.0

                adapter._refresh_last_node_id_errors()

                missing_error = next(
                    error
                    for error in adapter.state.errors
                    if getattr(getattr(error, "error_type", None), "value", None)
                    == "LAST_NODE_ID_MISSING"
                )
                refs = {
                    ref.reference_key: ref.reference_value
                    for ref in missing_error.error_references
                }
                self.assertEqual(refs["nearestNodeGap"], "0.0")
                self.assertEqual(refs["lastNodeGap"], "5.0")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_accepting_new_order_preserves_existing_last_node_until_arrival(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter._vehicle._station = ""
                adapter._last_node_id = "N1"
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = "N1"
                adapter.state.last_node_sequence_id = 0

                adapter._accept_v3_order_for_queue(make_order("preserve-last-node"))

                self.assertEqual(adapter._last_node_id, "N1")
                self.assertEqual(adapter._last_node_sequence_id, 0)
                self.assertEqual(adapter.state.last_node_id, "N1")
                self.assertEqual(adapter.state.last_node_sequence_id, 0)
                self.assertNotIn("N1", [node.node_id for node in adapter.state.node_states])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_accepting_new_order_bootstraps_last_node_from_current_pose(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            # Asserts the lastNodeId = order-progress contract, i.e. the "settled"
            # capture mode. config.toml's deployment default is now "proximity"
            # (order-agnostic nearest), so opt in explicitly to keep this test
            # independent of the deployment default.
            adapter.config.settings.last_node_capture_mode = "settled"
            vehicle._station = ""
            vehicle._x = 14040.0
            vehicle._y = 3750.0
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_80_S2IC": (14159.0, 3615.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None

                # Stale idle/no-order seed from a prior pose.
                adapter._last_node_id = "F2_80_S2IC"
                adapter._last_node_sequence_id = 2
                adapter.state.last_node_id = "F2_80_S2IC"
                adapter.state.last_node_sequence_id = 2

                order = Order.from_dict(
                    {
                        "headerId": 62,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "orderId": "pose-bootstrap-order",
                        "orderUpdateId": 1,
                        "nodes": [
                            {"nodeId": "F1_60", "sequenceId": 0, "released": True, "actions": []},
                            {"nodeId": "F2_80_S2IC", "sequenceId": 2, "released": True, "actions": []},
                            {"nodeId": "F2_90_S2CH", "sequenceId": 4, "released": True, "actions": []},
                        ],
                        "edges": [
                            {
                                "edgeId": "e_F1_60_F2_80_S2IC",
                                "sequenceId": 1,
                                "released": True,
                                "startNodeId": "F1_60",
                                "endNodeId": "F2_80_S2IC",
                                "actions": [],
                            },
                            {
                                "edgeId": "e_F2_80_S2IC_F2_90_S2CH",
                                "sequenceId": 3,
                                "released": True,
                                "startNodeId": "F2_80_S2IC",
                                "endNodeId": "F2_90_S2CH",
                                "actions": [],
                            },
                        ],
                    }
                )

                adapter._accept_v3_order_for_queue(order)

                self.assertEqual(adapter._last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_sequence_id, 4)
                self.assertEqual(adapter.state.last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter.state.last_node_sequence_id, 4)
                self.assertEqual(adapter.state.node_states, [])
                self.assertEqual(adapter.state.edge_states, [])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_write_state_file_dumps_v3_state_with_updated_at(self) -> None:
        from pathlib import Path

        from core import ipc_paths

        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                with tempfile.TemporaryDirectory() as d:
                    target = Path(d) / "state.json"
                    with patch.object(ipc_paths, "state_path", return_value=target), \
                         patch.object(ipc_paths, "ensure_runtime_dir", return_value=Path(d)):
                        adapter._write_state_file(adapter._build_v3_state_message())
                    data = json.loads(target.read_text())
                self.assertIn("safetyState", data)
                self.assertIn("activeEmergencyStop", data["safetyState"])
                self.assertIsInstance(data["updated_at"], float)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def _capture_connection_publishes(self, adapter) -> list:
        """Record (topic, connectionState) for every _vda3.publish call."""
        published: list = []
        original = adapter._vda3.publish

        def _spy(topic, message, *args, **kwargs):
            published.append(
                (topic, getattr(message, "connection_state", None))
            )
            return None

        adapter._vda3.publish = _spy
        self.addCleanup(lambda: setattr(adapter._vda3, "publish", original))
        return published

    def test_broker_change_hook_is_wired_into_the_mqtt_client(self) -> None:
        """Without this the republish below is dead code — nothing calls it."""
        adapter = self._make_adapter()

        # Bound methods are rebuilt per attribute access, so compare by value.
        self.assertEqual(
            adapter._mqtt._on_connection_change, adapter._on_acs_broker_change
        )

    def test_broker_reconnect_republishes_connection_online(self) -> None:
        """A retained OFFLINE Last Will must be overwritten on every reconnect.

        Without this the FMS keeps seeing the robot offline after any link drop,
        because ONLINE used to be published once at startup only.
        """
        from pathlib import Path

        from core import ipc_paths
        from protocol.vda5050_3_0.messages import ConnectionState

        adapter = self._make_adapter()
        published = self._capture_connection_publishes(adapter)

        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "health.json"
            with patch.object(ipc_paths, "health_path", return_value=target), \
                 patch.object(ipc_paths, "ensure_runtime_dir", return_value=Path(d)):
                adapter._on_acs_broker_change(True)   # first connect
                adapter._on_acs_broker_change(False)  # link drops
                adapter._on_acs_broker_change(True)   # paho reconnects

        self.assertEqual(
            published,
            [
                ("connection", ConnectionState.ONLINE),
                ("connection", ConnectionState.ONLINE),
            ],
        )

    def test_broker_reconnect_after_deliberate_offline_stays_offline(self) -> None:
        """Graceful shutdown publishes OFFLINE; a late reconnect must not undo it."""
        from pathlib import Path

        from core import ipc_paths
        from protocol.vda5050_3_0.messages import ConnectionState

        adapter = self._make_adapter()

        async def scenario() -> None:
            await adapter.publish_connection(
                topic_name="connection", connection_state=ConnectionState.OFFLINE
            )

        asyncio.run(scenario())

        published = self._capture_connection_publishes(adapter)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "health.json"
            with patch.object(ipc_paths, "health_path", return_value=target), \
                 patch.object(ipc_paths, "ensure_runtime_dir", return_value=Path(d)):
                adapter._on_acs_broker_change(True)

        self.assertEqual(published, [])

    def test_on_acs_broker_change_writes_health_file(self) -> None:
        from pathlib import Path

        from core import ipc_paths

        adapter = self._make_adapter()
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "health.json"
            with patch.object(ipc_paths, "health_path", return_value=target), \
                 patch.object(ipc_paths, "ensure_runtime_dir", return_value=Path(d)):
                adapter._on_acs_broker_change(True)
                data_true = json.loads(target.read_text())
                adapter._on_acs_broker_change(False)
                data_false = json.loads(target.read_text())
        self.assertTrue(data_true["acs_broker_connected"])
        self.assertFalse(data_false["acs_broker_connected"])
        self.assertIsInstance(data_true["updated_at"], float)

    def test_process_control_request_dispatches_and_reports_delivered(self) -> None:
        adapter = self._make_adapter()
        with patch.object(adapter, "instant_actions_accept_procedure") as proc:
            resp = adapter._process_control_request(
                {
                    "instantActions": {
                        "headerId": 1, "timestamp": "t", "version": "3.0.0",
                        "manufacturer": "jibot", "serialNumber": "S1",
                        "actions": [
                            {"actionId": "a1", "actionType": "stateRequest",
                             "blockingType": "NONE", "actionParameters": []}
                        ],
                    },
                    "meta": {"source_user": "op", "confirmed": True, "created_at": 1.0},
                }
            )
        self.assertTrue(resp["delivered"])
        self.assertEqual(resp["action_ids"], ["a1"])
        proc.assert_called_once()

    def test_process_control_request_rejects_bad_payload(self) -> None:
        adapter = self._make_adapter()
        resp = adapter._process_control_request({})
        self.assertFalse(resp["delivered"])
        self.assertIn("error", resp)

    def test_clear_instant_actions_from_callback_runs_immediately(self) -> None:
        adapter = self._make_adapter()
        adapter.config.vehicle.serial_number = "HN-SH6-TR-001"
        adapter.state = type(
            "State",
            (),
            {
                "instant_action_states": [
                    ActionState(
                        action_id="old-1",
                        action_status=ActionStatus.RUNNING,
                        action_type="testSound",
                    )
                ],
                "errors": [],
                "information": [],
            },
        )()

        with patch.object(adapter, "_call_on_adapter_loop") as call_on_loop:
            adapter.handle_incoming_acs_cmd(
                "amr/v3/HN-SH6-TR-001/instantActions",
                json.dumps(
                    {
                        "headerId": 9,
                        "timestamp": "2026-06-22T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "actions": [
                            {
                                "actionType": "clearInstantActions",
                                "actionId": "clear-1",
                                "blockingType": "NONE",
                                "actionParameters": [],
                            }
                        ],
                    }
                ),
            )

        call_on_loop.assert_not_called()
        self.assertEqual(adapter.state.instant_action_states, [])

    def test_rejects_instant_actions_for_different_robot_serial(self) -> None:
        adapter = self._make_adapter()
        adapter.config.vehicle.serial_number = "HN-SH6-TR-001"

        with patch.object(adapter, "instant_actions_accept_procedure") as proc:
            adapter.handle_incoming_acs_cmd(
                "amr/v3/HN-SH6-TR-002/instantActions",
                json.dumps(
                    {
                        "headerId": 10,
                        "timestamp": "2026-07-29T10:21:18.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-002",
                        "actions": [
                            {
                                "actionType": "cancelOrder",
                                "actionId": "cross-cancel-1",
                                "blockingType": "NONE",
                                "actionParameters": [],
                            }
                        ],
                    }
                ),
            )

        proc.assert_not_called()

    def test_rejects_order_when_payload_serial_differs_from_topic_robot(self) -> None:
        adapter = self._make_adapter()
        adapter.config.vehicle.serial_number = "HN-SH6-TR-001"
        payload = _one_node_order("cross-order-1").to_dict()
        payload["serialNumber"] = "HN-SH6-TR-002"

        with patch.object(adapter, "_call_on_adapter_loop") as call_on_loop:
            adapter.handle_incoming_acs_cmd(
                "amr/v3/HN-SH6-TR-001/order",
                json.dumps(payload),
            )

        call_on_loop.assert_not_called()

    def test_accepts_instant_actions_with_empty_payload_serial_on_own_topic(self) -> None:
        adapter = self._make_adapter()
        adapter.config.vehicle.serial_number = "HN-SH6-TR-001"
        payload = {
            "headerId": 11,
            "timestamp": "2026-07-29T10:21:18.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "",
            "actions": [
                {
                    "actionType": "stateRequest",
                    "actionId": "empty-serial-1",
                    "blockingType": "NONE",
                    "actionParameters": [],
                }
            ],
        }

        with patch.object(adapter, "instant_actions_accept_procedure") as proc:
            adapter.handle_incoming_acs_cmd(
                "amr/v3/HN-SH6-TR-001/instantActions",
                json.dumps(payload),
            )

        proc.assert_called_once()

    def test_accepts_instant_actions_without_payload_serial_on_own_topic(self) -> None:
        adapter = self._make_adapter()
        adapter.config.vehicle.serial_number = "HN-SH6-TR-001"
        payload = {
            "headerId": 12,
            "timestamp": "2026-07-29T10:21:18.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "actions": [
                {
                    "actionType": "stateRequest",
                    "actionId": "missing-serial-1",
                    "blockingType": "NONE",
                    "actionParameters": [],
                }
            ],
        }

        with patch.object(adapter, "instant_actions_accept_procedure") as proc:
            adapter.handle_incoming_acs_cmd(
                "amr/v3/HN-SH6-TR-001/instantActions",
                json.dumps(payload),
            )

        proc.assert_called_once()

    def test_uds_server_and_sender_integration(self) -> None:
        from pathlib import Path

        from core import ipc_paths
        from web.senders import UdsSender

        async def scenario():
            with tempfile.TemporaryDirectory() as d:
                sock = Path(d) / "control.sock"
                adapter = self._make_adapter()
                with patch.object(ipc_paths, "control_sock_path", return_value=sock), \
                     patch.object(ipc_paths, "ensure_runtime_dir", return_value=Path(d)), \
                     patch.object(adapter, "instant_actions_accept_procedure") as proc:
                    server_task = asyncio.create_task(adapter._serve_control_socket())
                    await asyncio.sleep(0.1)  # let the server bind
                    payload = {
                        "headerId": 1, "timestamp": "t", "version": "3.0.0",
                        "manufacturer": "jibot", "serialNumber": "S1",
                        "actions": [{"actionId": "a1", "actionType": "stateRequest",
                                     "blockingType": "NONE", "actionParameters": []}],
                    }
                    delivered, _ = await asyncio.get_running_loop().run_in_executor(
                        None, UdsSender(str(sock)).send, payload, {"source_user": "op"}
                    )
                    server_task.cancel()
                    try:
                        await server_task
                    except asyncio.CancelledError:
                        pass
                    return delivered, proc.call_count

        delivered, calls = asyncio.run(scenario())
        self.assertTrue(delivered)
        self.assertEqual(calls, 1)

    def test_bind_control_socket_logs_loudly_and_returns_none_on_permission_error(self) -> None:
        """A non-writable /run/amr-adaptor must not silently kill the control task.

        Regression: _serve_control_socket had no try/except, so a PermissionError
        from ensure_runtime_dir killed the task with no log — the WebUi just saw
        "adapter offline". The bind step must catch it, log an actionable line,
        and signal a retry (return None) instead of propagating.
        """
        import io
        from contextlib import redirect_stdout

        from core import ipc_paths

        async def scenario():
            adapter = self._make_adapter()
            buf = io.StringIO()
            with patch.object(
                ipc_paths, "ensure_runtime_dir",
                side_effect=PermissionError(
                    "[Errno 13] Permission denied: '/run/amr-adaptor'"
                ),
            ), redirect_stdout(buf):
                server = await adapter._bind_control_socket("S1")
            return server, buf.getvalue()

        server, logged = asyncio.run(scenario())
        self.assertIsNone(server)
        self.assertIn("CONTROL UDS FAILED", logged)
        self.assertIn("repair-adaptor-ipc.sh", logged)

    def test_bind_control_socket_failure_is_logged_once_per_streak(self) -> None:
        """Mirror the state/health throttle: log the failure once, not every retry."""
        import io
        from contextlib import redirect_stdout

        from core import ipc_paths

        async def scenario():
            adapter = self._make_adapter()
            buf = io.StringIO()
            with patch.object(
                ipc_paths, "ensure_runtime_dir",
                side_effect=PermissionError("denied"),
            ), redirect_stdout(buf):
                await adapter._bind_control_socket("S1")
                await adapter._bind_control_socket("S1")
            return buf.getvalue()

        logged = asyncio.run(scenario())
        self.assertEqual(logged.count("CONTROL UDS FAILED"), 1)

    def test_serve_control_socket_self_heals_after_initial_failure(self) -> None:
        """If the IPC root becomes writable later (e.g. operator runs the repair
        script without restarting), the retry loop binds the socket on its own —
        no adapter restart required."""
        from pathlib import Path

        from core import ipc_paths

        async def scenario():
            with tempfile.TemporaryDirectory() as d:
                sock = Path(d) / "control.sock"
                adapter = self._make_adapter()
                # First attempt fails (perm denied); subsequent attempts succeed.
                calls = {"n": 0}

                def ensure(serial):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise PermissionError("denied")
                    return Path(d)

                with patch.object(ipc_paths, "control_sock_path", return_value=sock), \
                     patch.object(ipc_paths, "ensure_runtime_dir", side_effect=ensure), \
                     patch.object(adapter, "_control_socket_retry_delay", return_value=0.01):
                    task = asyncio.create_task(adapter._serve_control_socket())
                    healed = False
                    for _ in range(200):
                        if sock.exists():
                            healed = True
                            break
                        await asyncio.sleep(0.01)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    return healed, calls["n"]

        healed, attempts = asyncio.run(scenario())
        self.assertTrue(healed)
        self.assertGreaterEqual(attempts, 2)

    def test_action_states_exclude_nodes_and_edges_without_actions(self) -> None:
        adapter = self._make_adapter()
        order = Order.from_dict(
            {
                "headerId": 70,
                "timestamp": "2026-06-10T00:00:00.000Z",
                "version": "3.0.0",
                "manufacturer": "jibot",
                "serialNumber": "HN-SH6-TR-001",
                "orderId": "no-action-order",
                "orderUpdateId": 0,
                "nodes": [
                    {"nodeId": "N1", "sequenceId": 0, "released": True, "actions": []},
                    {"nodeId": "N2", "sequenceId": 2, "released": True, "actions": []},
                ],
                "edges": [
                    {
                        "edgeId": "E1",
                        "sequenceId": 1,
                        "released": True,
                        "startNodeId": "N1",
                        "endNodeId": "N2",
                        "actions": [],
                    }
                ],
            }
        )

        self.assertEqual(adapter.build_action_states(order), [])

    def test_action_state_uses_order_action_type_not_node_id(self) -> None:
        adapter = self._make_adapter()
        order = make_order()

        action_states = adapter.build_action_states(order)

        self.assertTrue(action_states)
        self.assertEqual(action_states[0].action_type, "UmSetVolume")
        self.assertNotEqual(action_states[0].action_type, "N2")

    def test_status_information_includes_jibot_localization_score(self) -> None:
        adapter = self._make_adapter()
        adapter._vehicle._localization_score = 350

        info = adapter._build_status_information()[0].to_dict()

        references = {
            ref["referenceKey"]: ref["referenceValue"]
            for ref in info["infoReferences"]
        }
        self.assertEqual(references["jibotLocalizationScore"], "350")

    def test_status_information_preserves_zero_jibot_localization_score(self) -> None:
        adapter = self._make_adapter()
        adapter._vehicle._localization_score = 0.0

        info = adapter._build_status_information()[0].to_dict()

        references = {
            ref["referenceKey"]: ref["referenceValue"]
            for ref in info["infoReferences"]
        }
        self.assertEqual(references["jibotLocalizationScore"], "0.0")

    def test_factsheet_contents(self) -> None:
        adapter = self._make_adapter()
        factsheet = adapter._build_factsheet()

        for key in (
            "typeSpecification",
            "physicalParameters",
            "protocolLimits",
            "protocolFeatures",
            "loadSpecification",
        ):
            self.assertIn(key, factsheet)

        # Static facts moved out of state.information.
        self.assertEqual(
            factsheet["coordinateUnits"], {"position": "mm", "orientation": "deg"}
        )
        self.assertIs(factsheet["simulation"], False)
        if adapter.config.video.enabled:
            self.assertEqual(
                set(factsheet["videoStreams"].keys()),
                set(adapter.config.video.stream_topics),
            )

        advertised = {
            entry["actionType"]
            for entry in factsheet["protocolFeatures"]["agvActions"]
        }
        for action_type in (
            "cancelOrder",
            "stateRequest",
            "factsheetRequest",
            "startPause",
            "stopPause",
            "startCharging",
            "initPosition",
            "clamp",
            "unclamp",
            "clampTeach",
            "clampOn",
            "clampOff",
            "clampStop",
            "pioInit",
            "pioReadIn",
            "pioWriteOut",
            "pioDisconnect",
            "pioScenario",
            "ezioReadIn",
            "photoSensorRead",
        ):
            self.assertIn(action_type, advertised)
        self.assertNotIn("stopCharging", advertised)
        self.assertNotIn("logReport", advertised)

    def test_clamp_instant_action_moves_to_position(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.01
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.005
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    make_instant_action(
                        "clamp",
                        params={"position": 1234, "speed": 55},
                    )
                )
                await asyncio.sleep(0.05)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

            self.assertIn(("move_single_axis_abs_pos", 1234, 55), motor.calls)
            self.assertEqual(
                _instant_action_statuses(adapter),
                {"ia-hw": ActionStatus.FINISHED},
            )

        asyncio.run(scenario())

    def test_clamp_instant_action_runs_through_registry_extension(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.01
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.005
            adapter.state = type(
                "State",
                (),
                {"instant_action_states": [], "errors": [], "information": []},
            )()
            adapter._loop = asyncio.get_running_loop()
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            self.assertTrue(adapter._action_registry.has("clamp"))
            adapter._handle_clamp_instant_action = lambda action: self.fail(
                "Clamp instant action should be dispatched by the registry extension"
            )
            statuses = []
            original_update = adapter._update_instant_action_status

            def capture_status(action_id, status, result_description=None):
                statuses.append((action_id, status, result_description))
                original_update(action_id, status, result_description)

            adapter._update_instant_action_status = capture_status

            adapter.instant_actions_accept_procedure(
                make_instant_action(
                    "clamp",
                    "clamp-1",
                    params={"position": 1234, "speed": 55},
                )
            )

            for _ in range(100):
                if any(status == ActionStatus.FINISHED for _, status, _ in statuses):
                    break
                await asyncio.sleep(0.01)

            self.assertIn(("clamp-1", ActionStatus.RUNNING, None), statuses)
            self.assertTrue(
                any(
                    action_id == "clamp-1"
                    and status == ActionStatus.FINISHED
                    and "clamp finished" in str(description)
                    for action_id, status, description in statuses
                )
            )
            self.assertIn(("move_single_axis_abs_pos", 1234, 55), motor.calls)

        asyncio.run(scenario())

    def test_clamp_action_enables_servo_before_moving(self) -> None:
        # Real-robot bug: a bare clamp move left the servo OFF, so the EZI drive
        # ignored MoveSingleAxisAbsPos and the motor never moved (while the
        # adapter still reported "clamp finished"). The clamp path must energise
        # the servo before issuing the move.
        async def scenario() -> None:
            adapter = self._make_adapter()
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.01
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.005
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            action = make_instant_action(
                "clamp",
                params={"position": 1234, "speed": 55},
            ).actions[0]

            await execute_clamp_action(adapter, action)

            types = [call[0] for call in motor.calls]
            self.assertIn("servo_enable", types)
            self.assertIn("move_single_axis_abs_pos", types)
            self.assertIn(("servo_enable", True), motor.calls)
            # Servo must be enabled BEFORE the move, or the drive drops it.
            self.assertLess(
                types.index("servo_enable"),
                types.index("move_single_axis_abs_pos"),
            )

        asyncio.run(scenario())

    def test_clamp_waits_for_servo_flag_before_moving(self) -> None:
        # 드라이브가 servo ON을 보고하기 전에 도착한 move는 조용히 버려진다.
        # servo_enable ACK만 믿지 말고 FFLAG_SERVOON이 설 때까지 기다려야 한다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            # Task 4에서 이동 완료 대기가 붙어도 테스트가 느려지지 않게 미리 줄여 둔다.
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": False, "motioning": False},
                    {"servo_on": False, "motioning": False},
                    {"servo_on": True, "motioning": False},
                ]
            )
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            types = [call[0] for call in motor.calls]
            # servo_enable 뒤에 상태 폴링이 있고, 그 뒤에야 move가 나간다.
            self.assertLess(types.index("servo_enable"), types.index("move_single_axis_abs_pos"))
            polls_before_move = [
                index
                for index, name in enumerate(types)
                if name == "get_axis_status"
                and index > types.index("servo_enable")
                and index < types.index("move_single_axis_abs_pos")
            ]
            self.assertGreaterEqual(len(polls_before_move), 2)

        asyncio.run(scenario())

    def test_clamp_skips_servo_enable_when_already_on(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
            motor = FakeClampMotor(servo_on=True)
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertNotIn(("servo_enable", True), motor.calls)
            self.assertIn(
                ("move_single_axis_abs_pos", 8500, adapter.config.ezi_config.motor_speed),
                motor.calls,
            )

        asyncio.run(scenario())

    def test_clamp_resets_the_alarm_before_enabling_the_servo(self) -> None:
        # 이제 모든 액션이 servo를 다시 켜야 하는데, 알람이 걸린 드라이브는 servo ON을
        # 거부한다. 그러면 FFLAG_SERVOON이 영영 서지 않아 모든 클램프 액션이 실패하고
        # clampOn조차 같은 거부를 만나 손으로도 못 푼다. 벤더 예제대로 alarm_reset을
        # 먼저 보낸다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            types = [call[0] for call in motor.calls]
            self.assertIn("alarm_reset", types)
            self.assertLess(
                types.index("alarm_reset"),
                motor.calls.index(("servo_enable", True)),
            )

        asyncio.run(scenario())

    def test_clamp_does_not_reset_the_alarm_when_the_servo_is_already_on(self) -> None:
        # servo가 이미 켜져 있으면 아무것도 건드리지 않는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(servo_on=True)
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertNotIn("alarm_reset", [call[0] for call in motor.calls])

        asyncio.run(scenario())

    def test_clamp_fails_without_moving_when_servo_never_turns_on(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.clamp_servo_on_timeout_sec = 0.2
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(axis_status_script=[{"servo_on": False, "motioning": False}])
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            types = [call[0] for call in motor.calls]
            self.assertNotIn("move_single_axis_abs_pos", types)

        asyncio.run(scenario())

    def test_clamp_fails_when_axis_status_is_unreachable(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            motor = FakeClampMotor(axis_status_none=True)
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertNotIn(
                "move_single_axis_abs_pos", [call[0] for call in motor.calls]
            )

        asyncio.run(scenario())

    def test_clamp_tolerates_transient_status_read_loss_during_the_move(self) -> None:
        # 30초 이동을 0.1초 주기로 폴링하면 재시도 없는 UDP 단발 교환이 ~300번이다.
        # 손실률 0.1%면 긴 이동의 약 26%가 한 번의 유실로 실패한다 — 이동 중에 모터
        # 전원을 끊으면서. 연속 3번까지는 견딘다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True},
                    None,  # 유실
                    None,  # 유실
                    None,  # 유실
                    {"servo_on": True, "motioning": False},
                ],
            )
            adapter.set_ezi_motor(motor)

            description = await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertIn("clampMoveTo finished", description)

        asyncio.run(scenario())

    def test_clamp_tolerates_a_transient_position_read_loss(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(
                position_none_times=2,
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True},
                    {"servo_on": True, "motioning": False},
                ],
            )
            adapter.set_ezi_motor(motor)

            description = await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertIn("clampMoveTo finished", description)

        asyncio.run(scenario())

    def test_clamp_fails_after_four_consecutive_status_read_losses(self) -> None:
        # 통신이 정말 끊긴 것과 한두 번의 유실은 구분해야 한다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_timeout_sec = 5.0
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True},
                    None,  # 이후로 계속 무응답
                ],
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertIn("no response", str(caught.exception))
            # 완료 타임아웃(5초)까지 버틴 것이 아니라 연속 유실로 끊었다.
            self.assertNotIn("move_stop", [call[0] for call in motor.calls])
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_servo_wait_does_not_tolerate_a_dropped_poll(self) -> None:
        # 이동 전 servo 확인과 move ACK는 단발 명령이라 유실을 봐주지 않는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": False},
                    None,
                    {"servo_on": True},
                ],
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertNotIn(
                "move_single_axis_abs_pos", [call[0] for call in motor.calls]
            )

        asyncio.run(scenario())

    def test_fake_clamp_motor_axis_status_script_holds_last_entry(self) -> None:
        # 테스트 더블 계약: 대본을 앞에서부터 소비하고, 소진되면 마지막 상태를 유지한다.
        async def scenario() -> None:
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": False, "motioning": False},
                    {"servo_on": True, "motioning": True},
                    {"servo_on": True, "motioning": False},
                ]
            )

            seen = [
                (
                    (await motor.get_axis_status())["active_flags"]["FFLAG_SERVOON"],
                    (await motor.get_axis_status())["active_flags"]["FFLAG_MOTIONING"],
                )
            ]
            self.assertEqual(seen[0], (False, True))

            last = await motor.get_axis_status()
            self.assertEqual(last["active_flags"]["FFLAG_SERVOON"], True)
            self.assertEqual(last["active_flags"]["FFLAG_MOTIONING"], False)

            again = await motor.get_axis_status()
            self.assertEqual(again["active_flags"]["FFLAG_MOTIONING"], False)
            self.assertEqual(again["communication_status"], 0)

        asyncio.run(scenario())

    def test_fake_clamp_motor_tracks_servo_enable_without_script(self) -> None:
        async def scenario() -> None:
            motor = FakeClampMotor()
            status = await motor.get_axis_status()
            self.assertFalse(status["active_flags"]["FFLAG_SERVOON"])

            await motor.servo_enable(True)
            status = await motor.get_axis_status()
            self.assertTrue(status["active_flags"]["FFLAG_SERVOON"])
            self.assertFalse(status["active_flags"]["FFLAG_MOTIONING"])

        asyncio.run(scenario())

    def test_clamp_servo_policy_manual_does_not_toggle_servo_for_move(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "manual"
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action(
                    "clampMoveTo",
                    params={"position": "8500"},
                ).actions[0],
            )

            self.assertNotIn(("servo_enable", True), motor.calls)
            self.assertNotIn(("servo_enable", False), motor.calls)
            types = [call[0] for call in motor.calls]
            self.assertNotIn("get_axis_status", types)
            self.assertNotIn("alarm_reset", types)
            self.assertIn(
                (
                    "move_single_axis_abs_pos",
                    8500,
                    adapter.config.ezi_config.motor_speed,
                ),
                motor.calls,
            )

        asyncio.run(scenario())

    def test_clamp_servo_policy_auto_off_disables_servo_after_move(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.01
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.005
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action(
                    "clampMoveTo",
                    params={"position": "8500"},
                ).actions[0],
            )

            self.assertIn(("servo_enable", True), motor.calls)
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_servo_policy_auto_off_disables_servo_after_failed_move(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            motor = FakeClampMotor(move_comm_status=1)
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action(
                        "clampMoveTo",
                        params={"position": "8500"},
                    ).actions[0],
                )

            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_servo_off_failure_does_not_mask_the_real_move_error(self) -> None:
        # finally에서 servo OFF가 실패하면 원래 오류를 덮어써서 운영자가 엉뚱한
        # 서브시스템을 진단하게 된다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            motor = FakeClampMotor(move_comm_status=1, servo_off_comm_status=1)
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            message = str(caught.exception)
            self.assertIn("clampMoveTo move", message)
            self.assertNotIn("servo_disable", message)
            # 원래 오류를 살리되 servo OFF 시도 자체는 그대로 나간다.
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_servo_off_failure_is_reported_when_the_motion_succeeded(self) -> None:
        # 덮을 오류가 없으면 servo OFF 실패는 그대로 올라와야 한다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
            motor = FakeClampMotor(servo_off_comm_status=1)
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertIn("servo_disable", str(caught.exception))

        asyncio.run(scenario())

    def test_clamp_disables_servo_only_after_motion_completes(self) -> None:
        # auto_on_auto_off가 move ACK 직후 servo를 끊으면 이동이 중간에 죽는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": False, "motioning": False},   # 최초 확인
                    {"servo_on": True, "motioning": False},    # servo ON 확인
                    {"servo_on": True, "motioning": True},     # 이동 시작
                    {"servo_on": True, "motioning": True},     # 이동 중
                    {"servo_on": True, "motioning": False},    # 이동 완료
                ]
            )
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clamp", params={"position": "1234"}).actions[0],
            )

            types = [call[0] for call in motor.calls]
            move_at = types.index("move_single_axis_abs_pos")
            off_at = motor.calls.index(("servo_enable", False))
            # move 이후 servo OFF 이전에 상태 폴링이 있어야 한다 = 완료를 기다렸다.
            polls_between = [
                index
                for index, name in enumerate(types)
                if name == "get_axis_status" and move_at < index < off_at
            ]
            self.assertGreaterEqual(len(polls_between), 2)
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_fails_when_the_drive_aborts_the_move(self) -> None:
        # 현장 실패: 어긋난 팔레트에 물려 과부하 알람으로 드라이브가 이동을 중단하면
        # MOTIONING은 내려가지만 목표에는 못 갔다. "clamp finished"로 보고하면 FMS가
        # 클램프되지 않은 짐을 싣고 출발한다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_timeout_sec = 5.0
            motor = FakeClampMotor(
                actual_position=0,  # 목표 8500 근처에도 못 갔다
                axis_status_script=[
                    {"servo_on": True},                                       # servo 확인
                    {"servo_on": True, "motioning": True},                    # 이동 시작
                    {"servo_on": True, "motioning": False, "error_all": True},  # 알람으로 중단
                ],
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertIn("8500", str(caught.exception))
            # 완료 타임아웃이 아니라 즉시 실패다.
            self.assertNotIn("move_stop", [call[0] for call in motor.calls])
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_fails_when_motion_ends_short_of_the_target(self) -> None:
        # 알람 없이 조용히 멈춘 경우도 마찬가지다. MOTIONING이 내려간 것만으로는
        # 도착의 증거가 되지 않는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(
                actual_position=3000,
                axis_status_script=[
                    {"servo_on": True},                     # servo 확인
                    {"servo_on": True, "motioning": True},  # 이동 시작
                    {"servo_on": True, "motioning": False},  # 목표 못 미치고 정지
                ],
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_accepts_arrival_within_the_position_tolerance(self) -> None:
        # 허용 오차는 "도착"과 "아예 안 움직임"을 가르기 위한 것이지 정밀도 채점이 아니다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_position_tolerance = 500
            motor = FakeClampMotor(
                actual_position=8100,  # 목표 8500에서 400 counts
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True},
                    {"servo_on": True, "motioning": False},
                ],
            )
            adapter.set_ezi_motor(motor)

            description = await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertIn("clampMoveTo finished", description)

        asyncio.run(scenario())

    def test_clamp_min_succeeds_when_the_limit_flag_sets_with_error_all(self) -> None:
        # clampMin/clampMax는 하드웨어 리미트를 치는 것이 목적이라, 리미트 플래그와 함께
        # ERRORALL이 서는 것이 정상이다. 여기서 실패로 보면 멀쩡한 액션이 깨진다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.min_position = None  # goto_limit_minus 경로
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True},
                    {
                        "servo_on": True,
                        "motioning": False,
                        "limit_minus": True,
                        "error_all": True,
                    },
                ],
            )
            adapter.set_ezi_motor(motor)

            description = await execute_clamp_action(
                adapter,
                make_instant_action("clampMin").actions[0],
            )

            self.assertIn("clampMin finished", description)
            self.assertIn("goto_limit_minus", [call[0] for call in motor.calls])
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_max_succeeds_on_the_positive_limit_flag(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.max_position = None  # goto_limit_plus 경로
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True},
                    {"servo_on": True, "motioning": False, "limit_plus": True},
                ],
            )
            adapter.set_ezi_motor(motor)

            description = await execute_clamp_action(
                adapter,
                make_instant_action("clampMax").actions[0],
            )

            self.assertIn("clampMax finished", description)

        asyncio.run(scenario())

    def test_clamp_max_fails_when_error_all_sets_without_the_limit_flag(self) -> None:
        # 리미트에 닿지 않은 채 뜬 알람은 진짜 실패다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_timeout_sec = 5.0
            adapter.config.ezi_config.max_position = None
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True},
                    {"servo_on": True, "motioning": True, "error_all": True},
                ],
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMax").actions[0],
                )

            # 완료 타임아웃(5초)까지 기다린 것이 아니라 알람을 보고 즉시 끊었다.
            self.assertNotIn("move_stop", [call[0] for call in motor.calls])
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_fails_immediately_on_emergency_stop(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_timeout_sec = 5.0
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True, "emg_stop": True},
                ],
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertIn("emergency", str(caught.exception).lower())
            self.assertNotIn("move_stop", [call[0] for call in motor.calls])

        asyncio.run(scenario())

    def test_clamp_min_fails_when_motion_ends_without_the_limit_flag(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.min_position = None
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "motioning": True},
                    {"servo_on": True, "motioning": False},  # 리미트 플래그가 없다
                ],
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMin").actions[0],
                )

        asyncio.run(scenario())

    def test_clamp_passes_when_motion_never_starts_and_it_is_already_there(self) -> None:
        # 이미 목표 위치면 MOTIONING이 한 번도 서지 않는다. 실패가 아니다 —
        # 다만 "이미 목표"라고 가정하지 않고 실제 위치로 확인한 뒤에 통과시킨다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            motor = FakeClampMotor(servo_on=True, actual_position=8500)
            adapter.set_ezi_motor(motor)

            description = await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertIn("clampMoveTo finished", description)
            self.assertIn("get_actual_position", [call[0] for call in motor.calls])
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_fails_when_the_drive_ignores_the_move_command(self) -> None:
        # 현장 관측: 드라이브가 1.5초 뒤에 움직이기 시작하는데 시작 대기(1.0초)가 끝났다고
        # 성공으로 통과시키면, 모터가 움직이기 0.5초 전에 servo를 끊는다. 시작 신호가
        # 없으면 "이미 목표"인지 실제 위치로 확인하고, 아니면 실패로 올린다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            motor = FakeClampMotor(servo_on=True, actual_position=0)
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertIn("ignored", str(caught.exception).lower())
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_min_fails_when_the_drive_ignores_the_limit_command(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            adapter.config.ezi_config.min_position = None
            # 리미트 플래그가 서지 않은 채 이동도 시작하지 않는다.
            motor = FakeClampMotor(axis_status_script=[{"servo_on": True}])
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMin").actions[0],
                )

        asyncio.run(scenario())

    def test_clamp_stops_and_fails_when_motion_never_ends(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            adapter.config.ezi_config.clamp_motion_timeout_sec = 0.1
            motor = FakeClampMotor(
                axis_status_script=[{"servo_on": True, "motioning": True}]
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            types = [call[0] for call in motor.calls]
            self.assertIn("move_stop", types)
            # 실패해도 servo는 반드시 내려간다.
            self.assertEqual(motor.calls[-1], ("servo_enable", False))
            self.assertLess(types.index("move_stop"), len(types) - 1)

        asyncio.run(scenario())

    def test_clamp_keep_on_policy_leaves_servo_energised(self) -> None:
        # auto_on_keep_on은 이동 완료를 기다리되 servo를 끄지 않는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertIn(("servo_enable", True), motor.calls)
            self.assertNotIn(("servo_enable", False), motor.calls)

        asyncio.run(scenario())

    def test_clamp_home_waits_for_origin_return_completion(self) -> None:
        # FASTECH 원점 복귀는 탐색→후퇴→Z펄스의 다단계 시퀀스라 MOTIONING이 아니라
        # ORIGINRETURNING/ORIGINRETOK로 완료를 판정해야 한다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.home_position = None  # goto_origin 경로를 타게 한다
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": False},                                             # 최초 확인
                    {"servo_on": True},                                              # servo ON 확인
                    {"servo_on": True, "origin_returning": True},                    # 원점 복귀 시작
                    {"servo_on": True, "origin_returning": True},                    # 복귀 중
                    {"servo_on": True, "origin_returning": False, "origin_ret_ok": True},  # 복귀 완료
                ]
            )
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampHome").actions[0],
            )

            types = [call[0] for call in motor.calls]
            origin_at = types.index("goto_origin")
            off_at = motor.calls.index(("servo_enable", False))
            # goto_origin 이후 servo OFF 이전에 상태 폴링이 있어야 한다 = 완료를 기다렸다.
            polls_between = [
                index
                for index, name in enumerate(types)
                if name == "get_axis_status" and origin_at < index < off_at
            ]
            self.assertGreaterEqual(len(polls_between), 2)
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_home_fails_when_origin_return_ends_without_ok(self) -> None:
        # ORIGINRETURNING이 내려가도 ORIGINRETOK가 안 서면 원점 복귀가 실패로 끝난 것이다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.home_position = None
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": True},
                    {"servo_on": True, "origin_returning": True},
                    {"servo_on": True, "origin_returning": False, "origin_ret_ok": False},
                ]
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampHome").actions[0],
                )

            # 실패해도 servo는 반드시 내려간다.
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_home_passes_when_origin_return_never_starts_but_origin_is_ok(self) -> None:
        # 이미 원점이면 ORIGINRETURNING이 한 번도 서지 않는다. 실패가 아니다 —
        # 다만 ORIGINRETOK로 실제 원점 복귀 성공을 확인한 뒤에 통과시킨다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            adapter.config.ezi_config.home_position = None
            motor = FakeClampMotor(
                axis_status_script=[{"servo_on": True, "origin_ret_ok": True}]
            )
            adapter.set_ezi_motor(motor)

            description = await execute_clamp_action(
                adapter,
                make_instant_action("clampHome").actions[0],
            )

            self.assertIn("clampHome finished", description)
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_home_fails_when_the_drive_ignores_the_origin_command(self) -> None:
        # ORIGINRETURNING도 안 서고 ORIGINRETOK도 없으면 명령이 씹힌 것이다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            adapter.config.ezi_config.home_position = None
            motor = FakeClampMotor(axis_status_script=[{"servo_on": True}])
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampHome").actions[0],
                )

            self.assertIn("ignored", str(caught.exception).lower())
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_home_stops_and_fails_when_origin_return_never_ends(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            adapter.config.ezi_config.clamp_motion_timeout_sec = 0.1
            adapter.config.ezi_config.home_position = None
            motor = FakeClampMotor(
                axis_status_script=[{"servo_on": True, "origin_returning": True}]
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampHome").actions[0],
                )

            # 원점 복귀 타임아웃 메시지는 원점 복귀를 가리켜야 한다.
            self.assertIn("origin return", str(caught.exception))
            types = [call[0] for call in motor.calls]
            self.assertIn("move_stop", types)
            # 실패해도 servo는 반드시 내려간다.
            self.assertEqual(motor.calls[-1], ("servo_enable", False))
            self.assertLess(types.index("move_stop"), len(types) - 1)

        asyncio.run(scenario())

    def test_clamp_teach_runs_inside_the_servo_cycle(self) -> None:
        # teach는 원점 탐색으로 모터를 실제로 움직인다. servo가 꺼져 있으면 무반응이다.
        # 자체 폴링 루프를 갖고 있으므로 이동 완료 대기는 걸지 않는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampTeach").actions[0],
            )

            types = [call[0] for call in motor.calls]
            self.assertLess(
                types.index("servo_enable"),
                types.index("initialized_open_close_encoder_position"),
            )
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_teach_times_out_instead_of_holding_the_servo_forever(self) -> None:
        # ezi_motor의 initialized_open_close_encoder_position()은 원점 센서가 켜질
        # 때까지 타임아웃 없이 돈다(is_origin_sensor_on()은 통신이 끊겨도 예외 없이
        # False를 준다). teach가 servo를 켜게 된 뒤로는 이 무한 대기가 모터에 전류를
        # 물린 채 order 큐까지 붙잡는다. 클램프 쪽에서 시간을 잘라야 한다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.clamp_motion_timeout_sec = 0.05
            motor = FakeClampMotor(teach_hangs=True)
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampTeach").actions[0],
                )

            types = [call[0] for call in motor.calls]
            self.assertIn("move_stop", types)
            # 매달린 뒤에도 servo는 반드시 내려간다.
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_teach_leaves_servo_alone_when_manual(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "manual"
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampTeach").actions[0],
            )

            types = [call[0] for call in motor.calls]
            self.assertNotIn("servo_enable", types)
            self.assertNotIn("get_axis_status", types)
            self.assertIn("initialized_open_close_encoder_position", types)

        asyncio.run(scenario())

    def test_clamp_unclamp_use_separate_position_config(self) -> None:
        # clamp and unclamp must target independently-configured absolute
        # positions (not just ±origin_encoder_offset).
        async def scenario() -> None:
            adapter = self._make_adapter()
            ezi = adapter.config.ezi_config
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            ezi.clamp_motion_start_timeout_sec = 0.01
            ezi.ezi_motor_poll_interval_sec = 0.005
            ezi.clamp_position = 5000
            ezi.unclamp_position = -7000
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            await execute_clamp_action(adapter, make_instant_action("clamp").actions[0])
            await execute_clamp_action(adapter, make_instant_action("unclamp").actions[0])
            speed = ezi.motor_speed
            self.assertIn(("move_single_axis_abs_pos", 5000, speed), motor.calls)
            self.assertIn(("move_single_axis_abs_pos", -7000, speed), motor.calls)

        asyncio.run(scenario())

    def test_unconfigured_clamp_target_is_rejected_instead_of_guessed(self) -> None:
        """No configured target must fail loudly, never fall back to a guess.

        The old ±origin_encoder_offset fallback sent 62's unclamp to -16000,
        past the minus hardware limit the axis was already sitting on, so the
        drive ignored every move. A target nobody configured is not a target.
        """
        async def scenario() -> None:
            for action_type, expected_key in (
                ("clamp", "clamp_position"),
                ("unclamp", "unclamp_position"),
            ):
                with self.subTest(action_type=action_type):
                    adapter = self._make_adapter()
                    ezi = adapter.config.ezi_config
                    ezi.clamp_position = ezi.unclamp_position = None
                    ezi.clamp_offset = ezi.unclamp_offset = None
                    motor = FakeClampMotor()
                    adapter.set_ezi_motor(motor)

                    with self.assertRaises(ValueError) as caught:
                        await execute_clamp_action(
                            adapter, make_instant_action(action_type).actions[0]
                        )

                    message = str(caught.exception)
                    self.assertIn(expected_key, message)
                    self.assertIn("extensions.hcl", message)
                    # Fail before energising the axis or commanding anything.
                    self.assertEqual(motor.calls, [])

        asyncio.run(scenario())

    def test_configured_offset_still_resolves_without_an_absolute_position(self) -> None:
        """Declaring *_offset is enough; only the silent fallback is gone."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            ezi = adapter.config.ezi_config
            ezi.clamp_motion_start_timeout_sec = 0.01
            ezi.ezi_motor_poll_interval_sec = 0.005
            ezi.clamp_position = ezi.unclamp_position = None
            ezi.origin_encoder = 1000
            ezi.clamp_offset = 2000
            ezi.unclamp_offset = -500
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(adapter, make_instant_action("clamp").actions[0])
            await execute_clamp_action(adapter, make_instant_action("unclamp").actions[0])

            speed = ezi.motor_speed
            self.assertIn(("move_single_axis_abs_pos", 3000, speed), motor.calls)
            self.assertIn(("move_single_axis_abs_pos", 500, speed), motor.calls)

        asyncio.run(scenario())

    def test_clamp_offset_is_relative_to_origin_encoder(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            ezi = adapter.config.ezi_config
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            ezi.clamp_motion_start_timeout_sec = 0.01
            ezi.ezi_motor_poll_interval_sec = 0.005
            ezi.clamp_position = None
            ezi.origin_encoder = 1000
            ezi.clamp_offset = -500
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            await execute_clamp_action(adapter, make_instant_action("clamp").actions[0])
            self.assertIn(("move_single_axis_abs_pos", 500, ezi.motor_speed), motor.calls)

        asyncio.run(scenario())

    def test_min_max_home_use_config_position_when_set(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            ezi = adapter.config.ezi_config
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            ezi.clamp_motion_start_timeout_sec = 0.01
            ezi.ezi_motor_poll_interval_sec = 0.005
            ezi.min_position, ezi.max_position, ezi.home_position = 100, 200, 300
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            for at in ("clampMin", "clampMax", "clampHome"):
                await execute_clamp_action(adapter, make_instant_action(at).actions[0])
            speed = ezi.motor_speed
            self.assertIn(("move_single_axis_abs_pos", 100, speed), motor.calls)
            self.assertIn(("move_single_axis_abs_pos", 200, speed), motor.calls)
            self.assertIn(("move_single_axis_abs_pos", 300, speed), motor.calls)

        asyncio.run(scenario())

    def test_min_max_home_use_hardware_limits_when_unset(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            ezi = adapter.config.ezi_config
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            ezi.clamp_motion_start_timeout_sec = 0.01
            ezi.ezi_motor_poll_interval_sec = 0.005
            ezi.min_position = ezi.max_position = ezi.home_position = None
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            for at in ("clampMin", "clampMax", "clampHome"):
                await execute_clamp_action(adapter, make_instant_action(at).actions[0])
            types = [c[0] for c in motor.calls]
            self.assertIn("goto_limit_minus", types)   # clampMin
            self.assertIn("goto_limit_plus", types)    # clampMax
            self.assertIn("goto_origin", types)        # clampHome
            # any motion still energises the servo first
            self.assertIn(("servo_enable", True), motor.calls)

        asyncio.run(scenario())

    def test_clamp_move_to_uses_entered_encoder_position(self) -> None:
        # WebUI sends a manually-entered absolute encoder value as the `position`
        # param (a string); clampMoveTo must servo-enable then move there.
        async def scenario() -> None:
            adapter = self._make_adapter()
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.01
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.005
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0]
            )
            speed = adapter.config.ezi_config.motor_speed
            self.assertIn(("move_single_axis_abs_pos", 8500, speed), motor.calls)
            self.assertIn(("servo_enable", True), motor.calls)

        asyncio.run(scenario())

    def test_clamp_move_to_requires_a_position(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            with self.assertRaises(ValueError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo").actions[0]
                )

        asyncio.run(scenario())

    def test_order_clamp_action_fails_when_drive_rejects_move(self) -> None:
        # Defect 2: the EZI drive can reject a move (nonzero
        # communication_status); the adapter must report FAILED, not pretend the
        # clamp finished.
        async def scenario() -> None:
            adapter = self._make_adapter()
            motor = FakeClampMotor(move_comm_status=1)  # 1 = drive rejected
            adapter.set_ezi_motor(motor)
            action = make_instant_action(
                "clamp",
                action_id="order-clamp",
                params={"position": 4321, "speed": 66},
            ).actions[0]
            action_state = ActionState(
                action_id="order-clamp",
                action_status=ActionStatus.WAITING,
                action_type="clamp",
            )

            await adapter._execute_order_action(action, action_state)

            self.assertEqual(action_state.action_status, ActionStatus.FAILED)
            self.assertIn(("move_single_axis_abs_pos", 4321, 66), motor.calls)

        asyncio.run(scenario())

    def test_order_clamp_action_fails_when_motor_times_out(self) -> None:
        # A comms timeout returns None from the motor client; that must also be
        # surfaced as FAILED rather than reported finished.
        async def scenario() -> None:
            adapter = self._make_adapter()
            motor = FakeClampMotor(move_returns_none=True)
            adapter.set_ezi_motor(motor)
            action = make_instant_action(
                "clamp",
                action_id="order-clamp",
                params={"position": 4321, "speed": 66},
            ).actions[0]
            action_state = ActionState(
                action_id="order-clamp",
                action_status=ActionStatus.WAITING,
                action_type="clamp",
            )

            await adapter._execute_order_action(action, action_state)

            self.assertEqual(action_state.action_status, ActionStatus.FAILED)

        asyncio.run(scenario())

    def test_pio_scenario_executes_out_in_delay_steps(self) -> None:
        async def scenario() -> None:
            # scenario는 pio_init으로 시작하므로 SELECT/GO를 줄 설비가 필요하고,
            # in 단계가 볼 EZI IO 입력(PIO in3 = digital input 2)도 세워 둔다.
            bits = [0] * 16
            bits[2] = 1
            adapter, pio, ezi = self._paired_setup(ezio_inputs=bits)

            action = make_instant_action(
                "pioScenario",
                params={
                    "media": 1,
                    # station/channel은 이제 한 쌍이어야 하는 실제 BC 주소다 —
                    # 이 테스트의 관심사는 scenario step 실행이지 주소 자체가
                    # 아니므로 shipped config의 유효한 조합(_PIO_BC_PARAMS)을 쓴다.
                    **_PIO_BC_PARAMS,
                    "port": 3,
                    "ohtNumber": "OHT-7",
                    "scenario": [
                        {"type": "out", "index": 1, "state": "on"},
                        {"type": "in", "index": 3, "state": "on", "timeoutSec": 0.1},
                        {"type": "delay", "sec": 0},
                        {"type": "out", "index": 1, "state": "off"},
                    ],
                },
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(pio.raw_calls, [])   # 출력은 EZI IO로 나간다
            pin = adapter.config.pio_config.output_pins[0]
            self.assertIn((pin, "on"), ezi.output_calls)
            self.assertEqual(result["steps"][-1]["ok"], True)
            self.assertTrue(pio.closed)

        asyncio.run(scenario())

    def test_pio_scenario_if_runs_the_then_branch(self) -> None:
        """조건 입력이 켜져 있으면 then 갈래만 돈다."""

        async def scenario() -> None:
            bits = [0] * 16
            bits[1] = 1                       # PIO in2 = 조건 참
            adapter, _pio, ezi = self._paired_setup(ezio_inputs=bits)
            action = make_instant_action(
                "pioScenario",
                params={
                    "media": 1,
                    **_PIO_BC_PARAMS,
                    "port": 3,
                    "ohtNumber": "OHT-7",
                    "scenario": [
                        {
                            "type": "if",
                            "index": 2,
                            "state": "on",
                            "then": [{"type": "out", "index": 1, "state": "on"}],
                            "otherwise": [{"type": "out", "index": 4, "state": "on"}],
                        },
                    ],
                },
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["steps"][0]["branch"], "then")
            then_pin = adapter.config.pio_config.output_pins[0]
            other_pin = adapter.config.pio_config.output_pins[3]
            self.assertIn((then_pin, "on"), ezi.output_calls)
            # 안 고른 갈래는 한 번도 나가면 안 된다 — 나가면 엘리베이터가 두 요청을
            # 동시에 받는다.
            self.assertNotIn((other_pin, "on"), ezi.output_calls)

        asyncio.run(scenario())

    def test_pio_scenario_if_runs_the_otherwise_branch(self) -> None:
        """조건이 거짓이면 otherwise 갈래가 돈다(else는 HCL 예약어라 이름이 다르다)."""

        async def scenario() -> None:
            adapter, _pio, ezi = self._paired_setup()   # 입력 전부 off
            action = make_instant_action(
                "pioScenario",
                params={
                    "media": 1,
                    **_PIO_BC_PARAMS,
                    "port": 3,
                    "ohtNumber": "OHT-7",
                    "scenario": [
                        {
                            "type": "if",
                            "index": 2,
                            "state": "on",
                            "then": [{"type": "out", "index": 1, "state": "on"}],
                            "otherwise": [{"type": "out", "index": 4, "state": "on"}],
                        },
                        {"type": "delay", "sec": 0},
                    ],
                },
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["steps"][0]["branch"], "otherwise")
            self.assertIn(
                (adapter.config.pio_config.output_pins[3], "on"), ezi.output_calls
            )
            self.assertNotIn(
                (adapter.config.pio_config.output_pins[0], "on"), ezi.output_calls
            )
            # 갈래가 끝나면 뒤에 남은 공통 단계가 이어서 돈다.
            self.assertEqual(result["steps"][-1]["type"], "delay")

        asyncio.run(scenario())

    def test_pio_scenario_if_fails_when_the_condition_cannot_be_read(self) -> None:
        """조건 입력을 못 읽으면 실패다. off로 넘겨짚으면 엉뚱한 갈래를 탄다."""

        async def scenario() -> None:
            adapter, _pio, _ezi = self._paired_setup()
            adapter.config.pio_config.input_pins = [0]   # in2는 읽히지 않는다
            action = make_instant_action(
                "pioScenario",
                params={
                    "media": 1,
                    **_PIO_BC_PARAMS,
                    "port": 3,
                    "ohtNumber": "OHT-7",
                    "scenario": [
                        {
                            "type": "if",
                            "index": 2,
                            "state": "on",
                            "then": [{"type": "out", "index": 1, "state": "on"}],
                            "otherwise": [{"type": "out", "index": 4, "state": "on"}],
                        },
                    ],
                },
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("in2", result["message"])

        asyncio.run(scenario())

    def test_pio_scenario_if_rejects_nested_if(self) -> None:
        async def scenario() -> None:
            bits = [0] * 16
            bits[1] = 1
            adapter, _pio, _ezi = self._paired_setup(ezio_inputs=bits)
            action = make_instant_action(
                "pioScenario",
                params={
                    "media": 1,
                    **_PIO_BC_PARAMS,
                    "port": 3,
                    "ohtNumber": "OHT-7",
                    "scenario": [
                        {
                            "type": "if",
                            "index": 2,
                            "state": "on",
                            "then": [{"type": "if", "index": 3, "state": "on"}],
                        },
                    ],
                },
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("nested if", result["message"])

        asyncio.run(scenario())

    def test_pio_scenario_reports_input_timeout_step(self) -> None:
        async def scenario() -> None:
            adapter, _pio, _ezi = self._paired_setup()   # 입력이 전부 off -> 대기 타임아웃
            action = make_instant_action(
                "pioScenario",
                params={
                    "media": 1,
                    **_PIO_BC_PARAMS,
                    "port": 3,
                    "ohtNumber": "OHT-7",
                    "timeoutSec": 0.01,
                    "scenario": [
                        {"type": "out", "index": 1, "state": "on"},
                        {"type": "in", "index": 3, "state": "on", "timeoutSec": 0.01},
                    ],
                },
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertEqual(result["failedStep"], 2)
            self.assertIn("expected in3=on", result["message"])

        asyncio.run(scenario())

    def test_pio_read_in_reads_the_mapped_ezi_io_inputs(self) -> None:
        """입력도 직렬이 아니라 EZI IO에서 온다. in 1~8 → digital input 0~7."""

        async def scenario() -> None:
            bits = [1, 0, 1, 0, 1, 0, 1, 0] + [0] * 8
            adapter, pio, _ezi = self._paired_setup(ezio_inputs=bits)
            action = make_instant_action("pioReadIn").actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(
                result["inputs"],
                {
                    "1": "on", "2": "off", "3": "on", "4": "off",
                    "5": "on", "6": "off", "7": "on", "8": "off",
                },
            )
            self.assertEqual(pio.monitor_calls, [])   # 직렬로 묻지 않는다
            self.assertEqual(pio.connect_calls, 0)

        asyncio.run(scenario())

    def test_pio_read_in_follows_the_configured_input_pins(self) -> None:
        async def scenario() -> None:
            bits = [0] * 16
            bits[9] = 1
            adapter, _pio, _ezi = self._paired_setup(ezio_inputs=bits)
            adapter.config.pio_config.input_pins = [9, 8]
            action = make_instant_action("pioReadIn").actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["inputs"], {"1": "on", "2": "off"})

        asyncio.run(scenario())

    def test_pio_read_in_requires_ezi_io(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.set_pio_client(FakePioClient())
            adapter._ezi_io = None
            action = make_instant_action("pioReadIn").actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("EZI IO is not initialized", result["message"])

        asyncio.run(scenario())

    def test_pio_scenario_waits_on_an_ezi_io_input(self) -> None:
        """scenario의 in 단계도 같은 경로를 탄다."""

        async def scenario() -> None:
            bits = [0] * 16
            bits[2] = 1                       # PIO in3 = EZI IO in2
            adapter, _pio, _ezi = self._paired_setup(ezio_inputs=bits)
            action = make_instant_action(
                "pioScenario",
                params={
                    **_PIO_BC_PARAMS,
                    "scenario": [
                        {"type": "in", "index": 3, "state": "on", "timeoutSec": 0.1},
                    ],
                },
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["inputs"]["3"], "on")

        asyncio.run(scenario())

    def test_pio_write_out_drives_the_mapped_ezi_io_pin(self) -> None:
        """설비로 나가는 신호선은 직렬이 아니라 EZI IO다.

        예전에는 `OUT=n:v`를 직렬로 보냈는데 이건 보드 명령이 아니라서
        (최초 구현 utils/cls_pio.py의 명령은 BC=/C=/D= 뿐) 실제 출력이
        움직이지 않았다.
        """

        async def scenario() -> None:
            adapter, pio, ezi = self._paired_setup()
            action = make_instant_action(
                "pioWriteOut", params={"index": 1, "state": "on"}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            pin = adapter.config.pio_config.output_pins[0]
            self.assertEqual(result["ok"], True)
            self.assertEqual(result["pin"], pin)
            self.assertEqual(ezi.output_calls, [(pin, "on")])
            self.assertEqual(ezi.outputs[pin], 1)
            self.assertEqual(pio.raw_calls, [])        # 직렬로는 아무것도 안 보낸다
            self.assertEqual(pio.connect_calls, 0)     # 포트를 열 이유도 없다
            self.assertEqual(adapter._pio_outputs, {"1": "on"})

        asyncio.run(scenario())

    def test_pio_write_out_maps_each_index_through_output_pins(self) -> None:
        async def scenario() -> None:
            adapter, _pio, ezi = self._paired_setup()
            # output_pin_map이 있으면 그쪽이 이긴다 (positional 폴백은
            # tests/test_pio_output_mapping.py가 따로 지킨다).
            adapter.config.pio_config.output_pin_map = {1: 10, 2: 11, 3: 12}
            action = make_instant_action(
                "pioWriteOut", params={"index": 3, "state": "on"}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["pin"], 12)
            self.assertEqual(ezi.output_calls, [(12, "on")])

        asyncio.run(scenario())

    def test_pio_write_out_rejects_an_index_with_no_mapped_pin(self) -> None:
        async def scenario() -> None:
            adapter, _pio, ezi = self._paired_setup()
            adapter.config.pio_config.output_pin_map = {1: 0, 2: 1}
            action = make_instant_action(
                "pioWriteOut", params={"index": 5, "state": "on"}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("no EZI IO pin", result["message"])
            self.assertEqual(ezi.output_calls, [])

        asyncio.run(scenario())

    def test_pio_write_out_reads_back_and_reports_a_mismatch(self) -> None:
        """쓰기 성공만 믿으면 비트 맵이 어긋나도 모른다 (ezi_io.output_bit 사례)."""

        async def scenario() -> None:
            adapter, _pio, ezi = self._paired_setup()
            ezi.turn_on_output = mock.AsyncMock()      # 쓴 척만 하고 실제로 안 바뀜
            action = make_instant_action(
                "pioWriteOut", params={"index": 1, "state": "on"}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("read back off", result["message"])

        asyncio.run(scenario())

    def test_pio_write_out_requires_ezi_io(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.set_pio_client(FakePioClient())
            adapter._ezi_io = None
            action = make_instant_action(
                "pioWriteOut", params={"index": 1, "state": "on"}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("EZI IO is not initialized", result["message"])

        asyncio.run(scenario())

    def _paired_setup(self, **kwargs):
        """pioInit/pioPing이 쓰는 PIO+설비 한 쌍을 만든다.

        pair_confirmation: 기본값은 config의 "bc_reply"(BC 응답만으로 연결 성공
        판정)를 그대로 쓴다. GO 재시도 동작을 검증하는 테스트는 "go"를 명시해야
        한다 — 고정하지 않으면 bc_reply 분기로 들어가 BC 1회에 성공해 버려서
        pair_after/never_pairs가 아무 효과도 내지 못한다.
        """
        adapter = self._make_adapter()
        if "pair_confirmation" in kwargs:
            adapter.config.pio_advanced.pair_confirmation = kwargs["pair_confirmation"]
        pio = FakePioClient(**{k: v for k, v in kwargs.items() if k in
                               ("bc_response", "input_frames", "silent", "connect_error")})
        ezi = FakePioFacility(
            pio,
            paired=kwargs.get("paired", False),
            never_pairs=kwargs.get("never_pairs", False),
            stays_paired=kwargs.get("stays_paired", False),
            pair_after=kwargs.get("pair_after", 1),
        )
        if "ezio_inputs" in kwargs:
            ezi.inputs = list(kwargs["ezio_inputs"])
        adapter.set_pio_client(pio)
        adapter.set_ezi_io(ezi)
        return adapter, pio, ezi

    def test_pio_init_retries_the_bc_until_the_facility_raises_go(self) -> None:
        """설비는 첫 BC에 GO를 올려주지 않는다. workflow처럼 될 때까지 다시 보낸다.

        (elevator.py:341-347 handle_pairing) 한 번만 보내고 수동으로 기다리면
        실기에서 "GO stayed off"로 끝난다 — 18:14 pioInit 실패가 그 경우였다.
        """

        async def scenario() -> None:
            adapter, pio, ezi = self._paired_setup(
                pair_after=3, pair_confirmation="go"
            )
            action = make_instant_action(
                "pioInit",
                params={**_PIO_BC_PARAMS, "timeoutSec": 0.01, "pairTimeoutSec": 5.0},
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["bcAttempts"], 3)
            self.assertEqual(len(pio.bc_calls), 3)
            # 재시도마다 SELECT를 다시 걸어야 보드가 BC를 받는다
            select = adapter.config.ezi_config.select
            self.assertEqual(
                ezi.output_calls, [(select, "on"), (select, "off")] * 3
            )
            self.assertTrue(ezi.paired)

        asyncio.run(scenario())

    def test_pio_init_rejects_line_noise_that_is_not_a_bc_frame(self) -> None:
        """엉뚱한 시리얼 포트의 잡음을 BC 응답으로 오인하면 안 된다.

        amr2(192.168.101.62)에서 pio_serial_port가 PIO 변환기가 아닌 FTDI
        포트를 가리켰는데, pyserial이 비배타 open이라 포트는 열리고 잡음만
        돌아왔다. 응답 유무만 보던 검사는 이걸 성공으로 보고했다.
        """

        async def scenario() -> None:
            noise = "?\x15U\x03k"
            adapter, _pio, _ezi = self._paired_setup(bc_response=noise)
            action = make_instant_action(
                "pioInit", params={**_PIO_BC_PARAMS, "timeoutSec": 0.1}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            # 원문을 그대로 보여야 현장에서 "포트가 틀렸다"를 판단할 수 있다.
            self.assertIn(repr(noise), result["message"])
            self.assertFalse(adapter._pio_connected)

        asyncio.run(scenario())

    def test_pio_init_accepts_a_framed_reply_surrounded_by_noise(self) -> None:
        """검사가 과하게 조이면 정상 링크가 죽는다. 실제 읽기는 프레임 앞뒤로
        종단 문자와 잡음이 섞여 들어오므로 프레임이 '포함'되면 통과해야 한다."""

        async def scenario() -> None:
            adapter, _pio, _ezi = self._paired_setup(bc_response="\x00<BC=OK5C>\n")
            action = make_instant_action(
                "pioInit", params={**_PIO_BC_PARAMS, "timeoutSec": 0.2}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["bcResponse"], "\x00<BC=OK5C>\n")
            self.assertTrue(adapter._pio_connected)

        asyncio.run(scenario())

    def test_pio_frame_accepts_the_boards_bracket_reply(self) -> None:
        """실기 응답은 [payload+cs] 형태다. 요청의 <...>만 찾으면 정상 링크를 놓친다."""
        import extensions.pio as pio_ext

        reply = "[BC=2:569A-123456:250:0:OHT1236B]"
        self.assertEqual(pio_ext.find_pio_frame(reply), reply)
        # 체크섬이 틀리면 구분자가 맞아도 거부한다
        self.assertIsNone(pio_ext.find_pio_frame("[BC=2:569A-123456:250:0:OHT12300]"))
        self.assertIsNone(pio_ext.find_pio_frame("[junk]"))

    def test_pio_checksum_matches_the_transport_implementation(self) -> None:
        """extensions 쪽 체크섬이 PIOMaster와 어긋나면 정상 응답을 잡음으로 본다."""
        import sys
        from unittest.mock import MagicMock

        sys.modules.setdefault("serial", MagicMock())
        import extensions.pio as pio_ext
        from utils.pio import PIOMaster

        for payload in ("BC=OK", "BC=2:569A-123456:250:0:OHT123", "D=250", ""):
            self.assertEqual(
                pio_ext.pio_checksum_hex(payload), PIOMaster.checksum_hex(payload)
            )

    def test_pio_init_gates_the_bc_with_select_and_stays_paired(self) -> None:
        """init은 연결 action이다 — pairing을 걸고 그대로 둔다."""

        async def scenario() -> None:
            adapter, pio, ezi = self._paired_setup()
            action = make_instant_action(
                "pioInit", params={**_PIO_BC_PARAMS, "timeoutSec": 0.2}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            select = adapter.config.ezi_config.select
            self.assertEqual(ezi.output_calls, [(select, "on"), (select, "off")])
            self.assertEqual(len(pio.bc_calls), 1)
            self.assertTrue(ezi.paired)          # 물린 채로 끝난다
            self.assertFalse(pio.closed)         # 포트도 열어 둔다
            self.assertTrue(adapter._pio_connected)

        asyncio.run(scenario())

    def test_pio_init_fails_when_the_facility_never_raises_go(self) -> None:
        async def scenario() -> None:
            adapter, _pio, _ezi = self._paired_setup(
                never_pairs=True, pair_confirmation="go"
            )
            action = make_instant_action(
                "pioInit",
                params={**_PIO_BC_PARAMS, "timeoutSec": 0.05, "pairTimeoutSec": 0.05},
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("did not pair", result["message"])
            self.assertFalse(adapter._pio_connected)

        asyncio.run(scenario())

    def test_pio_init_says_ezi_io_is_needed_to_gate_the_board(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.set_pio_client(FakePioClient())
            adapter._ezi_io = None
            action = make_instant_action("pioInit", params=_PIO_BC_PARAMS).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("EZI IO is not initialized", result["message"])
            self.assertIn("SELECT", result["message"])

        asyncio.run(scenario())

    def test_pio_ping_pairs_sweeps_outputs_then_unpairs(self) -> None:
        """ping은 연결 → 출력 1~8 on/off → 해제까지 하고 흔적을 남기지 않는다."""

        async def scenario() -> None:
            adapter, pio, ezi = self._paired_setup()
            action = make_instant_action(
                "pioPing", params={**_PIO_BC_PARAMS, "timeoutSec": 0.2}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            select = adapter.config.ezi_config.select
            # SELECT는 pairing에 한 번, unpair에 한 번 — 두 쌍
            self.assertEqual(
                [c for c in ezi.output_calls if c[0] == select],
                [(select, "on"), (select, "off"), (select, "on"), (select, "off")],
            )
            self.assertEqual(len(pio.bc_calls), 1)      # unpair는 BC를 안 보낸다
            # 출력 8점을 순서대로 on→off. 직렬이 아니라 EZI IO로 나간다.
            self.assertEqual(pio.raw_calls, [])
            pins = adapter.config.pio_config.output_pins
            self.assertEqual(
                [c for c in ezi.output_calls if c[0] != select],
                [(pin, st) for pin in pins for st in ("on", "off")],
            )
            self.assertEqual(len(result["outputs"]), 8)
            self.assertEqual(result["outputs"][0]["index"], 1)
            # 끝나면 원래대로: 해제되고 포트도 닫힘
            self.assertFalse(ezi.paired)
            self.assertTrue(pio.closed)
            self.assertFalse(adapter._pio_connected)

        asyncio.run(scenario())

    def test_pio_ping_leaves_a_port_it_did_not_open(self) -> None:
        """pioInit으로 연결해 둔 상태에서 ping을 눌러도 남의 포트를 닫지 않는다."""

        async def scenario() -> None:
            adapter, pio, _ezi = self._paired_setup()
            pio.connect()                       # 이미 열려 있는 상태
            action = make_instant_action(
                "pioPing", params={**_PIO_BC_PARAMS, "timeoutSec": 0.2}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertFalse(pio.closed)

        asyncio.run(scenario())

    def test_pio_ping_unpairs_even_when_an_output_write_raises(self) -> None:
        async def scenario() -> None:
            adapter, pio, ezi = self._paired_setup()
            ezi.turn_on_output = mock.Mock(side_effect=RuntimeError("io module down"))
            action = make_instant_action(
                "pioPing", params={**_PIO_BC_PARAMS, "timeoutSec": 0.2}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertFalse(ezi.paired)        # 물린 채로 두지 않는다
            self.assertEqual(ezi.outputs[adapter.config.ezi_config.select], 0)

        asyncio.run(scenario())

    def test_pio_ping_fails_when_the_facility_never_raises_go(self) -> None:
        async def scenario() -> None:
            adapter, pio, ezi = self._paired_setup(
                never_pairs=True, pair_confirmation="go"
            )
            adapter._note_pio(connected=True)   # 이전에 켜져 있던 램프
            action = make_instant_action(
                "pioPing",
                params={**_PIO_BC_PARAMS, "timeoutSec": 0.05, "pairTimeoutSec": 0.05},
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertEqual(result["failedReason"], "not paired")
            self.assertIn("stayed off", result["message"])
            self.assertFalse(adapter._pio_connected)
            self.assertEqual(pio.raw_calls, [])          # pairing 전엔 출력을 안 건드린다
            self.assertEqual(ezi.outputs[adapter.config.ezi_config.select], 0)

        asyncio.run(scenario())

    def test_pio_ping_fails_when_the_facility_will_not_release(self) -> None:
        async def scenario() -> None:
            adapter, _pio, _ezi = self._paired_setup(stays_paired=True)
            action = make_instant_action(
                "pioPing",
                params={**_PIO_BC_PARAMS, "timeoutSec": 0.05, "pairTimeoutSec": 0.05},
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertEqual(result["failedReason"], "still paired")
            self.assertEqual(len(result["outputs"]), 8)   # 점검은 끝까지 돌았다

        asyncio.run(scenario())

    def test_pio_ping_says_ezi_io_is_needed_to_gate_the_board(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.set_pio_client(FakePioClient())
            adapter._ezi_io = None
            action = make_instant_action("pioPing", params=_PIO_BC_PARAMS).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("EZI IO is not initialized", result["message"])
            self.assertIn("SELECT", result["message"])

        asyncio.run(scenario())

    def test_pio_ping_reports_a_port_that_cannot_be_opened(self) -> None:
        async def scenario() -> None:
            adapter, _pio, _ezi = self._paired_setup(
                connect_error=OSError("Permission denied")
            )
            action = make_instant_action("pioPing", params=_PIO_BC_PARAMS).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertEqual(result["opened"], False)
            self.assertEqual(result["failedReason"], "OSError")
            self.assertIn("cannot open", result["message"])
            self.assertIn(adapter.config.pio_config.pio_serial_port, result["message"])
            self.assertFalse(adapter._pio_connected)

        asyncio.run(scenario())

    def test_pio_ping_requires_the_bc_target_now_that_the_fallback_is_moved(self) -> None:
        """station_id/channel 폴백은 설비 블록으로 옮겨갔다. 비워 두면 조용히
        extension "pio"의 예시값을 부르는 대신 바로 실패해야 한다."""

        async def scenario() -> None:
            adapter, pio, _ezi = self._paired_setup()
            action = make_instant_action("pioPing", params={"timeoutSec": 0.2}).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("stationId", result["message"])
            self.assertEqual(pio.bc_calls, [])   # 잘못된 상대로는 BC를 보내지 않는다

        asyncio.run(scenario())

    def test_pio_ping_rejects_a_station_paired_with_a_channel_no_facility_uses(self) -> None:
        """station과 channel은 한 주소의 두 절반이다. WebUI의 stationId는 모든
        설비의 station을 모은 select이고 channel은 자유 숫자 입력이라, 실재하는
        station에 그 설비가 쓰지 않는 channel을 붙여 보낼 수 있다 — 존재하지
        않는 주소로 BC를 쏘는 셈이다. station 하나만 알고 있다고 통과시키면 안
        된다."""

        async def scenario() -> None:
            adapter, pio, _ezi = self._paired_setup()
            # "000010"은 shipped extensions.hcl의 실제 elevator station이지만,
            # 그 station의 channel은 999가 아니라 elevator_config.channel(250)이다.
            action = make_instant_action(
                "pioPing",
                params={"stationId": "000010", "channel": 999, "timeoutSec": 0.2},
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertIn("000010", result["message"])
            self.assertIn("999", result["message"])
            self.assertEqual(pio.bc_calls, [])   # 잘못된 상대로는 BC를 보내지 않는다

        asyncio.run(scenario())

    def test_pio_disconnect_unpairs_the_facility_before_closing_the_port(self) -> None:
        """포트만 닫으면 설비는 계속 물려 있다. SELECT 토글로 실제 해제한다."""

        async def scenario() -> None:
            adapter, pio, ezi = self._paired_setup(paired=True)
            action = make_instant_action(
                "pioDisconnect", params={"timeoutSec": 0.2}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["unpaired"], True)
            select = adapter.config.ezi_config.select
            self.assertEqual(ezi.output_calls, [(select, "on"), (select, "off")])
            # unpair는 BC를 보내지 않는다 — 그게 pairing과의 차이다
            self.assertEqual(pio.bc_calls, [])
            self.assertTrue(pio.closed)
            self.assertFalse(adapter._pio_connected)
            # 기본값은 출력 전체 정리를 하지 않는다
            self.assertEqual(ezi.reset_masks, [])

        asyncio.run(scenario())

    def test_pio_disconnect_fails_when_go_stays_on(self) -> None:
        async def scenario() -> None:
            adapter, _pio, _ezi = self._paired_setup(paired=True, stays_paired=True)
            action = make_instant_action(
                "pioDisconnect", params={"timeoutSec": 0.05}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertEqual(result["unpaired"], False)
            self.assertEqual(result["failedReason"], "still paired")
            self.assertIn("stayed on", result["message"])

        asyncio.run(scenario())

    def test_pio_disconnect_clears_outputs_only_when_asked(self) -> None:
        async def scenario() -> None:
            adapter, _pio, ezi = self._paired_setup(paired=True)
            action = make_instant_action(
                "pioDisconnect", params={"clearOutputs": "on", "timeoutSec": 0.2}
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(ezi.reset_masks, [0xFFFF << 16])

        asyncio.run(scenario())

    def test_pio_disconnect_does_not_pretend_to_unpair_without_ezi_io(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            pio = FakePioClient()
            adapter.set_pio_client(pio)
            adapter._ezi_io = None
            action = make_instant_action("pioDisconnect").actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], False)
            self.assertEqual(result["unpaired"], False)
            self.assertIn("EZI IO is not initialized", result["message"])
            self.assertTrue(pio.closed)  # 포트는 그래도 닫는다

        asyncio.run(scenario())

    def test_pio_scenario_cleanup_still_only_closes_the_port(self) -> None:
        """scenario 뒷정리는 포트만 놓는다 — 설비 해제까지 하면 과하다."""

        async def scenario() -> None:
            adapter, pio, ezi = self._paired_setup(input_frames=["IN=00100000"])
            action = make_instant_action(
                "pioScenario",
                params={
                    "media": 1, **_PIO_BC_PARAMS, "port": 3,
                    "ohtNumber": "OHT-7",
                    "scenario": [{"type": "out", "index": 1, "state": "on"}],
                },
            ).actions[0]

            result = await execute_pio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertTrue(pio.closed)

        asyncio.run(scenario())

    def test_photo_sensor_read_updates_state_loads_from_ezio(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            inputs = [0] * 16
            inputs[8] = 1
            inputs[10] = 1
            adapter.set_ezi_io(FakeEziIo([inputs]))
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    make_instant_action("photoSensorRead")
                )
                for _ in range(100):
                    if len(adapter.loads) == 2:
                        break
                    await asyncio.sleep(0.01)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

            self.assertEqual([load.load_position for load in adapter.state.loads], ["slot1", "slot3"])
            self.assertEqual([load.load_type for load in adapter.state.loads], ["TRAY", "TRAY"])
            self.assertEqual([load.load_id for load in adapter.state.loads], [None, None])
            self.assertNotIn("loadId", adapter.state.loads[0].to_dict())

        asyncio.run(scenario())

    def test_photo_sensor_read_clears_loads_when_all_sensors_are_off(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.set_ezi_io(FakeEziIo([[0] * 16]))
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    make_instant_action("photoSensorRead")
                )
                for _ in range(100):
                    if adapter.state.loads == []:
                        break
                    await asyncio.sleep(0.01)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

            self.assertEqual(adapter.state.loads, [])

        asyncio.run(scenario())

    def test_photo_sensor_read_result_reports_six_sensor_values(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            inputs = [0] * 16
            inputs[8] = 1
            inputs[10] = 1
            adapter.set_ezi_io(FakeEziIo([inputs]))
            action = make_instant_action("photoSensorRead").actions[0]

            result = await execute_ezio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(
                result["photoSensors"],
                {
                    "1": "on",
                    "2": "off",
                    "3": "on",
                    "4": "off",
                    "5": "off",
                    "6": "off",
                },
            )
            self.assertIn("photoSensors={1:on,2:off,3:on", result["message"])

        asyncio.run(scenario())

    def test_ezio_read_in_returns_all_inputs(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            inputs = [1, 0, 1, 0, 0, 1, 0, 1, 1, 0, 0, 0, 1, 0, 1, 0]
            adapter.set_ezi_io(FakeEziIo([inputs]))
            action = make_instant_action("ezioReadIn").actions[0]

            result = await execute_ezio_action(adapter, action)

            self.assertEqual(result["ok"], True)
            self.assertEqual(result["inputs"]["1"], "on")
            self.assertEqual(result["inputs"]["2"], "off")
            self.assertEqual(result["inputs"]["16"], "off")

        asyncio.run(scenario())

    def test_ezio_input_read_retries_once_after_empty_response(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            inputs = [0] * 16
            inputs[8] = 1
            ezi_io = FlakyEziIo([None, inputs])
            adapter.set_ezi_io(ezi_io)

            result = await read_ezio_input_bits(adapter)

            self.assertEqual(result, inputs)
            self.assertEqual(ezi_io.calls, 2)

        asyncio.run(scenario())

    def test_manage_tray_slot_requests_state_publish_when_sensor_values_change(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            first = [0] * 16
            second = [0] * 16
            second[8] = 1
            adapter.set_ezi_io(FakeEziIo([first, second]))
            reasons = []
            adapter.request_state_publish = lambda reason="": reasons.append(reason)

            task = asyncio.create_task(manage_tray_slot(adapter, interval_sec=0.01))
            try:
                for _ in range(100):
                    if any(reason == "photo sensor changed" for reason in reasons):
                        break
                    await asyncio.sleep(0.01)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertIn("photo sensor changed", reasons)
            self.assertEqual([load.load_position for load in adapter.loads], ["slot1"])
            self.assertEqual(adapter.loads[0].load_type, "TRAY")
            self.assertNotIn("loadId", adapter.loads[0].to_dict())

        asyncio.run(scenario())

    def test_manage_tray_slot_skips_ezio_input_reads_in_simulator_mode(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle.is_simulator = True
            ezi_io = RaisingEziIo()
            adapter.set_ezi_io(ezi_io)

            task = asyncio.create_task(manage_tray_slot(adapter, interval_sec=0.01))
            try:
                await asyncio.sleep(0.03)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertEqual(ezi_io.calls, 0)

        asyncio.run(scenario())

    def test_manage_tray_slot_reports_ezio_input_failure_in_state_errors(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.set_ezi_io(EmptyEziIo())
            state_task = await self._start_state(adapter)
            task = asyncio.create_task(manage_tray_slot(adapter, interval_sec=0.01))
            try:
                for _ in range(100):
                    if any(
                        error.error_type == ErrorType.EZI_IO_INPUT_FAILED
                        for error in adapter.state.errors
                    ):
                        break
                    await asyncio.sleep(0.01)
            finally:
                task.cancel()
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertTrue(
                any(
                    error.error_type == ErrorType.EZI_IO_INPUT_FAILED
                    for error in adapter.state.errors
                )
            )

        asyncio.run(scenario())

    def test_order_clamp_action_uses_shared_hardware_handler(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            # Task 4에서 붙은 이동 완료 대기가 기본 타임아웃(1.0s)만큼 걸리지 않게 줄인다.
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.01
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.005
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)
            action = make_instant_action(
                "clamp",
                action_id="order-clamp",
                params={"position": 4321, "speed": 66},
            ).actions[0]
            action_state = ActionState(
                action_id="order-clamp",
                action_status=ActionStatus.WAITING,
                action_type="clamp",
            )

            await adapter._execute_order_action(action, action_state)

            self.assertIn(("move_single_axis_abs_pos", 4321, 66), motor.calls)
            self.assertEqual(action_state.action_status, ActionStatus.FINISHED)
            self.assertIn("clamp finished", action_state.result_description)

        asyncio.run(scenario())

    def test_request_laser_publishes_until_stop_laser(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            published = []

            def capture_publish(topic, payload, qos=0, retain=False, use_prefix=True, vehicle_id=None):
                published.append((topic, payload, qos, retain))

            adapter._mqtt.publish = capture_publish
            state_task = await self._start_state(adapter)

            try:
                adapter.instant_actions_accept_procedure(
                    make_laser_action("requestLaser", "laser-start")
                )
                for _ in range(100):
                    laser_published = [
                        entry for entry in published if entry[0] == "laser/test"
                    ]
                    if len(laser_published) >= 2:
                        break
                    await asyncio.sleep(0.01)

                laser_published = [
                    entry for entry in published if entry[0] == "laser/test"
                ]
                self.assertGreaterEqual(len(laser_published), 2)
                self.assertGreaterEqual(len(vehicle.laser_calls), 2)
                self.assertEqual(laser_published[-1][1]["source"], "UmGetLaser")
                self.assertEqual(
                    laser_published[-1][1]["data"],
                    [{"num": 2, "points": "1 2 3 4"}],
                )

                adapter.instant_actions_accept_procedure(
                    make_laser_action("stopLaser", "laser-stop")
                )
                stopped_count = len(
                    [entry for entry in published if entry[0] == "laser/test"]
                )
                await asyncio.sleep(0.05)
                self.assertEqual(
                    len([entry for entry in published if entry[0] == "laser/test"]),
                    stopped_count,
                )
            finally:
                state_task.cancel()
                laser_task = getattr(adapter, "laser_publish_task", None)
                if laser_task is not None:
                    laser_task.cancel()

        asyncio.run(scenario())

    def test_order_waits_for_arrival_and_executes_actions(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            adapter._handle_v3_order(make_order())
            self.assertIsNotNone(adapter.order_worker_task)

            # N1 is at (0, 0) = start pose, so it completes immediately.
            # N2 needs actual movement: the worker must NOT finish it until
            # the fake vehicle arrives.
            await asyncio.sleep(0.5)
            self.assertEqual(adapter.state.last_node_id, "N1")
            self.assertFalse(adapter.order_worker_task.done())
            self.assertIn("N2", vehicle.goto_targets)

            vehicle.arrive_at(1000.0, 500.0)
            await asyncio.wait_for(adapter.order_worker_task, timeout=5.0)

            self.assertEqual(adapter._last_node_id, "N2")
            self.assertEqual(adapter._last_node_sequence_id, 2)
            self.assertEqual(adapter.state.node_states, [])
            self.assertEqual(adapter.state.edge_states, [])

            # Supported JIBOT command executed; unknown action FAILED. The
            # terminal states stay in state until an order replaces them, and
            # they carry the actionId the order sent.
            self.assertIn(("UmSetVolume", 3), vehicle.commands)
            statuses = {
                action_state.action_id: action_state.action_status
                for action_state in adapter.state.action_states
            }
            self.assertEqual(statuses["a-volume"], ActionStatus.FINISHED)
            self.assertEqual(statuses["a-unknown"], ActionStatus.FAILED)

            state_task.cancel()

        asyncio.run(scenario())

    def test_send_node_motion_uses_pose_for_path_point(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points["p40"] = (14000.0, -2600.0, 180.0)
            node = Node("p40", 2, True, [])

            reason = await adapter._send_node_motion(node)

            self.assertIsNone(reason)
            # x/y come from the map, the heading does not: with no order theta
            # poseTh is the bearing from the robot (0,0) to the node, so it
            # arrives already facing that way (Adapter._node_goto_theta_deg).
            self.assertEqual(len(vehicle.goto_targets), 1)
            gx, gy, gtheta = vehicle.goto_targets[0]
            self.assertEqual((gx, gy), (14000.0, -2600.0))
            self.assertAlmostEqual(gtheta, math.degrees(math.atan2(-2600.0, 14000.0)))

        asyncio.run(scenario())

    def test_path_point_goto_uses_order_theta_converted_to_degrees(self) -> None:
        """nodePosition.theta (VDA radians) becomes UmGoto poseTh (JIBOT degrees).

        This is the only way the FMS can ask for an arrival heading: the robot
        map's own PathPoint heading is not a per-order value.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points["p40"] = (14000.0, -2600.0, 0.0)
            node = Node.from_dict({
                "nodeId": "p40", "sequenceId": 2, "released": True,
                "nodePosition": {
                    "x": 14000.0, "y": -2600.0,
                    "theta": math.pi / 2,          # VDA5050 radians
                    "mapId": "lab2m",
                },
                "actions": [],
            })

            reason = await adapter._send_node_goto(node)

            self.assertIsNone(reason)
            self.assertEqual(len(vehicle.goto_targets), 1)
            x, y, theta = vehicle.goto_targets[0]
            self.assertEqual((x, y), (14000.0, -2600.0))
            self.assertAlmostEqual(theta, 90.0)

        asyncio.run(scenario())

    def test_path_point_goto_heads_the_travel_direction_when_the_order_has_none(self) -> None:
        """No nodePosition.theta => poseTh is the bearing towards the target.

        That is the heading the robot already has when it rolls up to the node,
        so it stops there instead of turning. Neither of the alternatives works:
        the map's PathPoint heading is 0.00 everywhere here (it turned the robot
        at every arrival), and the heading held at dispatch is the one BEFORE
        the drive, so the robot would swing back to it on arrival.

        poseTh cannot simply be dropped either: urobot silently ignores a
        target=pose UmGoto with no poseTh (HN-SH6-TR-002, 2026-08-22
        22:46-22:52 -- three re-sends, no error frame, pose unchanged).
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points["p40"] = (0.0, 5000.0, 180.0)
            vehicle._x, vehicle._y = 0.0, 0.0        # straight up (+y) to p40
            vehicle._th = 33.0                       # heading before the drive
            node = Node("p40", 2, True, [])          # no nodePosition at all

            reason = await adapter._send_node_goto(node)

            self.assertIsNone(reason)
            self.assertEqual(vehicle.goto_targets, [(0.0, 5000.0, 90.0)])

        asyncio.run(scenario())

    def test_path_point_goto_keeps_its_heading_when_already_on_the_node(self) -> None:
        """Standing on the target => no travel, so no travel direction.

        The bearing off a near-zero displacement is noise; holding the current
        heading is the only answer that cannot spin the robot in place.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points["p40"] = (100.0, 200.0, 180.0)
            vehicle._x, vehicle._y = 100.0, 200.0
            vehicle._th = 33.0
            node = Node("p40", 2, True, [])

            reason = await adapter._send_node_goto(node)

            self.assertIsNone(reason)
            self.assertEqual(vehicle.goto_targets, [(100.0, 200.0, 33.0)])

        asyncio.run(scenario())

    def test_path_point_goto_falls_back_to_the_map_heading_without_telemetry(self) -> None:
        """No pose and no heading => send the map heading, not nothing.

        A goto with no poseTh does not move the robot at all, so losing the
        heading must never cost the motion. Turning on arrival is the lesser
        failure.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points["p40"] = (14000.0, -2600.0, 180.0)
            vehicle._x, vehicle._y, vehicle._th = None, None, None
            node = Node("p40", 2, True, [])

            reason = await adapter._send_node_goto(node)

            self.assertIsNone(reason)
            self.assertEqual(vehicle.goto_targets, [(14000.0, -2600.0, 180.0)])

        asyncio.run(scenario())

    def test_path_point_without_node_position_waits_for_pose_arrival(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.settings.node_position_poll_interval_sec = 0.01
            # This test starts intentionally far from its only order node; keep
            # it focused on arrival verification rather than startup anchoring.
            adapter.config.settings.use_nearest_node_as_last_node_when_missing = False
            adapter.config.settings.last_node_capture_mode = "disabled"
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points["p40"] = (14000.0, -2600.0, 180.0)
            state_task = await self._start_state(adapter)
            order = Order.from_dict(
                {
                    "headerId": 40,
                    "timestamp": "2026-07-28T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "path-point-without-position",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "p40",
                            "sequenceId": 0,
                            "released": True,
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            for _ in range(100):
                if vehicle.goto_targets:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(len(vehicle.goto_targets), 1)
            gx, gy, gtheta = vehicle.goto_targets[0]
            self.assertEqual((gx, gy), (14000.0, -2600.0))
            self.assertAlmostEqual(gtheta, math.degrees(math.atan2(-2600.0, 14000.0)))
            self.assertFalse(adapter.order_worker_task.done())
            self.assertNotEqual(adapter.state.last_node_id, "p40")

            vehicle.arrive_at(14000.0, -2600.0)
            await asyncio.wait_for(adapter.order_worker_task, timeout=2.0)
            self.assertEqual(adapter.state.last_node_id, "p40")

            state_task.cancel()

        asyncio.run(scenario())

    def test_order_does_not_complete_from_station_without_pose_reach(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 41,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "station-arrival",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 5000.0,
                                "y": 5000.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            for _ in range(100):
                if vehicle.goto_targets:
                    break
                await asyncio.sleep(0.01)

            vehicle._station = "N1"
            vehicle._status = "Stopped"
            await asyncio.sleep(0.2)

            self.assertNotEqual(adapter.state.last_node_id, "N1")
            self.assertFalse(adapter.order_worker_task.done())
            self.assertEqual([node.node_id for node in adapter.state.node_states], ["N1"])

            state_task.cancel()
            adapter.order_worker_task.cancel()

        asyncio.run(scenario())

    def test_order_reports_unreached_when_vehicle_stops_outside_target(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 42,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "unreached-order",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 5000.0,
                                "y": 5000.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            for _ in range(100):
                if vehicle.goto_targets:
                    break
                await asyncio.sleep(0.01)

            vehicle._x = 3900.0
            vehicle._y = 4860.0
            vehicle._station = ""
            vehicle._status = "Stopped"

            for _ in range(200):
                if any(error.error_type == ErrorType.JIBOT_NODE_UNREACHED for error in adapter.state.errors):
                    break
                await asyncio.sleep(0.01)

            self.assertTrue(
                any(error.error_type == ErrorType.JIBOT_NODE_UNREACHED for error in adapter.state.errors)
            )
            unreached = next(
                error
                for error in adapter.state.errors
                if error.error_type == ErrorType.JIBOT_NODE_UNREACHED
            )
            refs = {
                ref.reference_key: ref.reference_value
                for ref in unreached.error_references
            }
            self.assertEqual(refs["command"], "UmGoto")
            self.assertIn("pose reach zone", unreached.error_description)
            self.assertIn("UmDock", unreached.error_description)
            self.assertEqual(adapter.state.node_states[0].node_id, "N1")
            self.assertFalse(adapter.order_worker_task.done())

            adapter.order_worker_task.cancel()
            state_task.cancel()

        asyncio.run(scenario())

    def _stopped_short_order(self, order_id: str) -> Order:
        """One released node at (5000, 5000) the robot will stop short of."""
        return Order.from_dict(
            {
                "headerId": 42,
                "timestamp": "2026-06-10T00:00:00.000Z",
                "version": "3.0.0",
                "manufacturer": "jibot",
                "serialNumber": "HN-SH6-TR-001",
                "orderId": order_id,
                "orderUpdateId": 0,
                "nodes": [
                    {
                        "nodeId": "N1",
                        "sequenceId": 0,
                        "released": True,
                        "nodePosition": {
                            "x": 5000.0,
                            "y": 5000.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                            "allowedDeviationXY": 5.0,
                        },
                        "actions": [],
                    }
                ],
                "edges": [],
            }
        )

    async def _start_order_and_await_first_goto(
        self, adapter: Adapter, order_id: str, retry_limit: Optional[int] = None
    ) -> None:
        adapter.config.settings.node_position_poll_interval_sec = 0.01
        adapter.config.settings.node_unreached_delay_sec = 0.02
        adapter.config.settings.node_goto_retry_delay_sec = 0.05
        if retry_limit is not None:
            # The wait reads the limit once on entry, so it must be set up front.
            adapter.config.settings.node_goto_retry_limit = retry_limit
        adapter._handle_v3_order(self._stopped_short_order(order_id))
        for _ in range(200):
            if adapter._vehicle.goto_targets:
                return
            await asyncio.sleep(0.01)
        self.fail("initial goto was never dispatched")

    def test_goto_recovery_settings_have_documented_defaults(self) -> None:
        adapter = self._make_adapter()
        self.assertEqual(adapter.config.settings.node_goto_retry_limit, 3)
        self.assertEqual(adapter.config.settings.node_goto_retry_delay_sec, 3.0)
        self.assertEqual(adapter.config.jibot_status.manual_modes, ["ModeDrive"])

    def test_operating_mode_is_manual_while_operator_hand_drives(self) -> None:
        adapter = self._make_adapter()
        adapter._vehicle._mode = "ModeDrive"
        self.assertEqual(adapter._derive_operating_mode(), OperatingMode.MANUAL)

    def test_manual_drive_during_order_reissues_the_lost_goto(self) -> None:
        """Hand-driving cancels JIBOT's goto; the worker must send it again.

        Manual driving replaces the running goto task with the drive mode, so the
        node is never reached and the worker used to poll forever.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                await self._start_order_and_await_first_goto(adapter, "manual-drive-order")

                # Operator drove the robot and let go: JIBOT dropped the goto and
                # sits stopped short of the node.
                vehicle._x = 3900.0
                vehicle._y = 4860.0
                vehicle._mode = "Stop"
                vehicle._status = "Stopped"

                for _ in range(300):
                    if len(vehicle.goto_targets) >= 2:
                        break
                    await asyncio.sleep(0.01)

                self.assertGreaterEqual(len(vehicle.goto_targets), 2)
                self.assertEqual(vehicle.goto_targets[1], "N1")
                self.assertFalse(adapter.order_worker_task.done())
            finally:
                adapter.order_worker_task.cancel()
                state_task.cancel()

        asyncio.run(scenario())

    def test_no_goto_reissue_while_the_operator_is_still_driving(self) -> None:
        """Never fight the joystick: no re-issue while JIBOT reports drive mode."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                await self._start_order_and_await_first_goto(adapter, "hands-on-order")

                vehicle._x = 3900.0
                vehicle._y = 4860.0
                vehicle._mode = "ModeDrive"
                vehicle._status = "Stopped"

                await asyncio.sleep(0.4)

                self.assertEqual(len(vehicle.goto_targets), 1)
                self.assertFalse(adapter.order_worker_task.done())
            finally:
                adapter.order_worker_task.cancel()
                state_task.cancel()

        asyncio.run(scenario())

    def test_no_goto_reissue_while_the_adapter_itself_is_jogging(self) -> None:
        """A jog/manualMove owns the motion too, even before JIBOT reports drive mode."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                await self._start_order_and_await_first_goto(adapter, "jogging-order")

                vehicle._x = 3900.0
                vehicle._y = 4860.0
                vehicle._status = "Stopped"
                adapter._manual_control_active = True

                await asyncio.sleep(0.4)

                self.assertEqual(len(vehicle.goto_targets), 1)
                self.assertFalse(adapter.order_worker_task.done())
            finally:
                adapter.order_worker_task.cancel()
                state_task.cancel()

        asyncio.run(scenario())

    def test_pause_during_a_plain_goto_publishes_no_unreached_error(self) -> None:
        """startPause stops the robot on purpose, so the arrival wait must not
        read the standstill as a stall. Without the _motion_paused term in that
        guard the plain-goto path publishes JIBOT_NODE_UNREACHED one second into
        every pause — a spurious FATAL error for a perfectly normal state."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                await self._start_order_and_await_first_goto(adapter, "paused-goto-order")

                # startPause: the robot stops, still short of N1 at (5000, 5000).
                adapter._motion_paused = True
                vehicle._x = 3900.0
                vehicle._y = 4860.0
                vehicle._mode = "Stop"
                vehicle._status = "Stopped"

                await asyncio.sleep(0.4)   # far past node_unreached_delay_sec (0.02)

                # Key off the error TYPE, never the description: that text reads
                # "...outside the order node pose reach zone..." and contains no
                # "unreach" substring to match on.
                self.assertEqual(
                    [e for e in adapter.state.errors
                     if e.error_type == ErrorType.JIBOT_NODE_UNREACHED],
                    [],
                )
                # and the pause is not a stall, so nothing is re-dispatched either
                self.assertEqual(len(vehicle.goto_targets), 1)
                self.assertFalse(adapter.order_worker_task.done())
            finally:
                adapter.order_worker_task.cancel()
                state_task.cancel()

        asyncio.run(scenario())

    def test_goto_reissue_gives_up_with_a_fatal_error_after_the_retry_limit(self) -> None:
        """A target the robot cannot reach escalates instead of retrying forever."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                await self._start_order_and_await_first_goto(
                    adapter, "unreachable-order", retry_limit=2
                )

                vehicle._x = 3900.0
                vehicle._y = 4860.0
                vehicle._mode = "Stop"
                vehicle._status = "Stopped"

                for _ in range(400):
                    if any(
                        error.error_type == ErrorType.JIBOT_NODE_UNREACHED
                        and error.error_level == ErrorLevel.FATAL
                        for error in adapter.state.errors
                    ):
                        break
                    await asyncio.sleep(0.01)

                fatal = [
                    error
                    for error in adapter.state.errors
                    if error.error_type == ErrorType.JIBOT_NODE_UNREACHED
                    and error.error_level == ErrorLevel.FATAL
                ]
                self.assertEqual(len(fatal), 1)
                refs = {ref.reference_key: ref.reference_value for ref in fatal[0].error_references}
                self.assertEqual(refs["retries"], "2")

                # Retries stop at the limit: the initial goto plus two re-issues.
                await asyncio.sleep(0.2)
                self.assertEqual(len(vehicle.goto_targets), 3)
            finally:
                adapter.order_worker_task.cancel()
                state_task.cancel()

        asyncio.run(scenario())

    def test_goto_retry_budget_resets_once_the_robot_drives_again(self) -> None:
        """Each stall gets a fresh budget, so a long route cannot exhaust it."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                await self._start_order_and_await_first_goto(
                    adapter, "resume-order", retry_limit=1
                )

                vehicle._x = 3900.0
                vehicle._y = 4860.0
                vehicle._mode = "Stop"
                vehicle._status = "Stopped"
                for _ in range(300):
                    if len(vehicle.goto_targets) >= 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(vehicle.goto_targets), 2)

                # Robot picks the route back up, then stalls a second time.
                vehicle._status = "Driving"
                await asyncio.sleep(0.1)
                vehicle._status = "Stopped"

                for _ in range(300):
                    if len(vehicle.goto_targets) >= 3:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(vehicle.goto_targets), 3)
            finally:
                adapter.order_worker_task.cancel()
                state_task.cancel()

        asyncio.run(scenario())

    def test_order_completes_pose_reach_but_warns_on_jibot_signal_mismatch(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            vehicle._status = "nrunto OTHER"
            vehicle._cur_task = {
                "#CMD#": "UmGetCurTask",
                "data": {
                    "status": "nrunto OTHER",
                    "value": {"cmd": "goto", "goal": "OTHER", "target": "goal"},
                },
            }
            vehicle._task_info = {
                "#CMD#": "UmGetTaskInfo",
                "status": "nrunto OTHER",
            }
            vehicle._path = {
                "#CMD#": "UmGetPath",
                "num": 2,
                "points": [{"x": 0.0, "y": 0.0}, {"x": 1000.0, "y": 500.0}],
            }
            self._stop_driving_on_arrival(adapter, vehicle)

            order = Order.from_dict(
                {
                    "headerId": 43,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "signal-mismatch-order",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 0.0,
                                "y": 0.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            await asyncio.wait_for(adapter.order_worker_task, timeout=5.0)

            self.assertEqual(adapter.state.last_node_id, "N1")
            self.assertEqual(adapter.state.node_states, [])
            mismatch_errors = [
                error
                for error in adapter.state.errors
                if getattr(getattr(error, "error_type", None), "value", None)
                == "JIBOT_ARRIVAL_SIGNAL_MISMATCH"
            ]
            self.assertEqual(len(mismatch_errors), 1)
            self.assertEqual(mismatch_errors[0].error_level, ErrorLevel.WARNING)
            refs = {
                ref.reference_key: ref.reference_value
                for ref in mismatch_errors[0].error_references
            }
            self.assertEqual(refs["nodeId"], "N1")
            self.assertEqual(refs["curTaskGoal"], "OTHER")
            self.assertEqual(refs["pathEndDistance"], "1118.0")

            state_task.cancel()

        asyncio.run(scenario())

    async def _run_pose_goto_arrival(self, adapter, order_id: str):
        """Complete a one-node order while JIBOT reports a pose target.

        A pose goto (the move-segment fallback) makes JIBOT answer with
        goal="none" and status="nrunto pose (x y th)" instead of a station
        name, so the arrival diagnostics must be read as coordinates.
        """
        order = Order.from_dict(
            {
                "headerId": 44,
                "timestamp": "2026-06-10T00:00:00.000Z",
                "version": "3.0.0",
                "manufacturer": "jibot",
                "serialNumber": "HN-SH6-TR-001",
                "orderId": order_id,
                "orderUpdateId": 0,
                "nodes": [
                    {
                        "nodeId": "N1",
                        "sequenceId": 0,
                        "released": True,
                        "nodePosition": {
                            "x": 0.0,
                            "y": 0.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                            "allowedDeviationXY": 5.0,
                        },
                        "actions": [],
                    }
                ],
                "edges": [],
            }
        )

        self._stop_driving_on_arrival(adapter, adapter._vehicle)
        adapter._handle_v3_order(order)
        await asyncio.wait_for(adapter.order_worker_task, timeout=5.0)

        self.assertEqual(adapter.state.node_states, [])
        return [
            error
            for error in adapter.state.errors
            if getattr(getattr(error, "error_type", None), "value", None)
            == "JIBOT_ARRIVAL_SIGNAL_MISMATCH"
        ]

    def test_pose_goto_arrival_does_not_warn_when_the_pose_is_the_node(self) -> None:
        """goal="none" plus "nrunto pose (0 0 0)" is agreement, not a mismatch."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            vehicle._status = "nrunto pose (0 0 0)#slowdown"
            vehicle._cur_task = {
                "#CMD#": "UmGetCurTask",
                "data": {
                    "status": "nrunto pose (0 0 0)",
                    "value": {
                        "cmd": "goto",
                        "goal": "none",
                        "target": "pose",
                        "x": 0,
                        "y": 0,
                        "th": 0,
                    },
                },
            }
            vehicle._task_info = {
                "#CMD#": "UmGetTaskInfo",
                "status": "nrunto pose (0 0 0)",
            }
            vehicle._path = {"#CMD#": "UmGetPath", "num": 0}

            mismatch_errors = await self._run_pose_goto_arrival(
                adapter, "pose-goto-match-order"
            )
            self.assertEqual(mismatch_errors, [])

            state_task.cancel()

        asyncio.run(scenario())

    def test_pose_goto_arrival_warns_when_the_pose_is_another_place(self) -> None:
        """A pose target far from the completed node is still a real mismatch."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            vehicle._status = "nrunto pose (5000 5000 0)"
            vehicle._cur_task = {
                "#CMD#": "UmGetCurTask",
                "data": {
                    "status": "nrunto pose (5000 5000 0)",
                    "value": {
                        "cmd": "goto",
                        "goal": "none",
                        "target": "pose",
                        "x": 5000,
                        "y": 5000,
                        "th": 0,
                    },
                },
            }
            vehicle._task_info = {
                "#CMD#": "UmGetTaskInfo",
                "status": "nrunto pose (5000 5000 0)",
            }
            vehicle._path = {"#CMD#": "UmGetPath", "num": 0}

            mismatch_errors = await self._run_pose_goto_arrival(
                adapter, "pose-goto-elsewhere-order"
            )
            self.assertEqual(len(mismatch_errors), 1)
            refs = {
                ref.reference_key: ref.reference_value
                for ref in mismatch_errors[0].error_references
            }
            self.assertEqual(refs["curTaskGoal"], "5000.0,5000.0")
            self.assertEqual(refs["jibotStatusGoal"], "5000.0,5000.0")

            state_task.cancel()

        asyncio.run(scenario())

    def test_robot_info_loop_fetches_map_for_real_vehicle_only(self) -> None:
        from main import robot_info_loop

        async def run_one_cycle(
            vehicle: PollingVehicle, position_store_path: Optional[Path] = None
        ) -> None:
            task = asyncio.create_task(
                robot_info_loop(
                    vehicle,
                    interval_sec=0.01,
                    position_store_path=position_store_path,
                    position_map_id="lab2m",
                    position_serial_number="HN-SH6-TR-001",
                )
            )
            for _ in range(100):
                if vehicle.robot_info_calls:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        real_vehicle = PollingVehicle(is_simulator=False)
        asyncio.run(run_one_cycle(real_vehicle))
        self.assertEqual(real_vehicle.map_calls, 1)
        self.assertEqual(real_vehicle._map_nodes, {"N1": (0.0, 0.0, 0.0)})

        simulator = PollingVehicle(is_simulator=True)
        asyncio.run(run_one_cycle(simulator))
        self.assertEqual(simulator.map_calls, 0)

    def test_robot_info_loop_fetches_map_only_once_on_startup(self) -> None:
        from main import robot_info_loop

        async def run_two_cycles(vehicle: PollingVehicle) -> None:
            task = asyncio.create_task(
                robot_info_loop(
                    vehicle,
                    interval_sec=0.01,
                    position_map_id="lab2m",
                    position_serial_number="HN-SH6-TR-001",
                )
            )
            for _ in range(100):
                if vehicle.robot_info_calls >= 2:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        vehicle = PollingVehicle(is_simulator=False)
        asyncio.run(run_two_cycles(vehicle))

        self.assertGreaterEqual(vehicle.robot_info_calls, 2)
        self.assertEqual(vehicle.map_calls, 1)

    def test_robot_info_loop_persists_real_vehicle_position_for_simulator(self) -> None:
        from main import robot_info_loop

        async def run_one_cycle(vehicle: PollingVehicle, path: Path) -> None:
            task = asyncio.create_task(
                robot_info_loop(
                    vehicle,
                    interval_sec=0.01,
                    position_store_path=path,
                    position_map_id="lab2m",
                    position_serial_number="HN-SH6-TR-001",
                )
            )
            for _ in range(100):
                if path.exists():
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "position.json"
            asyncio.run(run_one_cycle(PollingVehicle(is_simulator=False), path))

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["serialNumber"], "HN-SH6-TR-001")
            self.assertEqual(payload["mapId"], "lab2m")
            self.assertEqual(payload["x"], 12.5)
            self.assertEqual(payload["y"], 34.0)
            self.assertEqual(payload["theta"], 90.0)
            self.assertIn("updatedAt", payload)

            snapshot = load_position_snapshot(path)
            self.assertEqual(snapshot, {"x": 12.5, "y": 34.0, "theta": 90.0})

    def test_simulator_starts_from_persisted_position(self) -> None:
        from cls_jibot_simulator import SimulatedJIBOT

        vehicle = SimulatedJIBOT(
            config=Adapter().config,
            initial_position={"x": 111.0, "y": 222.0, "theta": 33.0},
        )

        self.assertEqual(vehicle._x, 111.0)
        self.assertEqual(vehicle._y, 222.0)
        self.assertEqual(vehicle._th, 33.0)

    def test_simulator_speed_uses_vda_position_units(self) -> None:
        from cls_jibot_simulator import SimulatedJIBOT

        async def scenario() -> None:
            vehicle = SimulatedJIBOT(config=Adapter().config)
            await vehicle.connect_socket()
            try:
                await vehicle.goto_node_position("N2", 1000.0, 0.0, 0.0)
                await asyncio.sleep(0.35)
            finally:
                await vehicle.disconnect()

            self.assertGreaterEqual(vehicle._x, 10.0)
            self.assertLess(vehicle._x, 1000.0)

        asyncio.run(scenario())

    def test_order_worker_completes_steps_with_simulator_vehicle(self) -> None:
        from cls_jibot_simulator import SimulatedJIBOT

        async def scenario() -> None:
            adapter = Adapter()
            vehicle = SimulatedJIBOT(config=adapter.config)
            adapter.set_vehicle(vehicle)
            await vehicle.connect_socket()
            state_task = await self._start_state(adapter)
            try:
                order = Order.from_dict(
                    {
                        "headerId": 11,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "orderId": "sim-order-1",
                        "orderUpdateId": 0,
                        "nodes": [
                            {
                                "nodeId": "S1",
                                "sequenceId": 0,
                                "released": True,
                                "nodePosition": {
                                    "x": 0.0,
                                    "y": 0.0,
                                    "theta": 0.0,
                                    "mapId": "lab2m",
                                    "allowedDeviationXY": 1.0,
                                },
                                "actions": [],
                            },
                            {
                                "nodeId": "S2",
                                "sequenceId": 2,
                                "released": True,
                                "nodePosition": {
                                    "x": 80.0,
                                    "y": 0.0,
                                    "theta": 0.0,
                                    "mapId": "lab2m",
                                    "allowedDeviationXY": 1.0,
                                },
                                "actions": [],
                            },
                        ],
                        "edges": [
                            {
                                "edgeId": "S1-S2",
                                "sequenceId": 1,
                                "released": True,
                                "startNodeId": "S1",
                                "endNodeId": "S2",
                                "actions": [],
                            }
                        ],
                    }
                )

                adapter._handle_v3_order(order)
                self.assertEqual(adapter.order_queue.qsize(), 3)
                await asyncio.wait_for(adapter.order_worker_task, timeout=5.0)

                self.assertEqual(adapter.state.last_node_id, "S2")
                self.assertEqual(adapter.state.node_states, [])
                self.assertEqual(adapter.state.edge_states, [])
                self.assertEqual(vehicle._target_node, "S2")
                self.assertGreaterEqual(vehicle._x, 60.0)
            finally:
                state_task.cancel()
                await vehicle.disconnect()

        asyncio.run(scenario())

    def test_cancel_order_waits_for_general_vehicle_stop_before_finishing(self) -> None:
        async def scenario() -> None:
            adapter = Adapter()
            vehicle = BlockingStopVehicle()
            adapter.set_vehicle(vehicle)
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(make_order())
                await asyncio.sleep(0.2)

                adapter.instant_actions_accept_procedure(make_cancel_order_action())
                await asyncio.wait_for(vehicle.stop_started.wait(), timeout=1.0)
                await asyncio.sleep(0.1)

                cancel_state = next(
                    state
                    for state in adapter.state.instant_action_states
                    if state.action_id == "ia-cancel"
                )
                self.assertEqual(cancel_state.action_status, ActionStatus.RUNNING)
                self.assertEqual(vehicle.stop_motion_calls, 1)

                vehicle.stop_release.set()
                statuses = await _settle_instant_actions(adapter, "ia-cancel")

                self.assertEqual(statuses["ia-cancel"], ActionStatus.FINISHED)
                self.assertEqual(adapter.state.node_states, [])
                self.assertEqual(adapter.state.edge_states, [])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_custom_registry_instant_action_finishes(self) -> None:
        from core.action_registry import ActionRegistry, ActionResult, ActionSpec

        adapter = self._make_adapter()
        adapter.state = type(
            "State",
            (),
            {"instant_action_states": [], "errors": [], "information": []},
        )()
        registry = ActionRegistry()
        registry.register(
            ActionSpec(
                action_type="customPing",
                handler=lambda ctx: ActionResult(ActionStatus.FINISHED, "pong"),
            )
        )
        adapter._action_registry = registry

        statuses = []
        original_update = adapter._update_instant_action_status

        def capture_status(action_id, status, result_description=None):
            statuses.append((action_id, status, result_description))
            original_update(action_id, status, result_description)

        adapter._update_instant_action_status = capture_status
        adapter.instant_actions_accept_procedure(make_instant_action("customPing", "custom-1"))

        self.assertIn(("custom-1", ActionStatus.FINISHED, "pong"), statuses)

    def test_custom_registry_motion_action_blocked_while_busy(self) -> None:
        from core.action_registry import ActionRegistry, ActionResult, ActionSpec

        adapter = self._make_adapter()
        adapter.state = type(
            "State",
            (),
            {"instant_action_states": [], "errors": [], "information": []},
        )()
        registry = ActionRegistry()
        registry.register(
            ActionSpec(
                action_type="customMove",
                motion=True,
                handler=lambda ctx: ActionResult(ActionStatus.FINISHED, "moved"),
            )
        )
        adapter._action_registry = registry
        adapter._work_in_progress = "loading"

        statuses = []
        original_update = adapter._update_instant_action_status

        def capture_status(action_id, status, result_description=None):
            statuses.append((action_id, status, result_description))
            original_update(action_id, status, result_description)

        adapter._update_instant_action_status = capture_status
        adapter.instant_actions_accept_procedure(make_instant_action("customMove", "custom-2"))

        self.assertTrue(any(status == ActionStatus.FAILED for _, status, _ in statuses))
        self.assertTrue(any("busy with loading" in str(desc) for _, _, desc in statuses))

    def test_pio_instant_action_runs_through_registry_extension(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.state = type(
                "State",
                (),
                {"instant_action_states": [], "errors": [], "information": []},
            )()
            adapter._loop = asyncio.get_running_loop()
            pio = FakePioClient()
            adapter.set_pio_client(pio)
            # pioReadIn은 EZI IO 입력을 읽는다
            adapter.set_ezi_io(FakePioFacility(pio))
            self.assertTrue(adapter._action_registry.has("pioReadIn"))
            adapter._handle_pio_instant_action = lambda action: self.fail(
                "PIO instant action should be dispatched by the registry extension"
            )
            statuses = []
            original_update = adapter._update_instant_action_status

            def capture_status(action_id, status, result_description=None):
                statuses.append((action_id, status, result_description))
                original_update(action_id, status, result_description)

            adapter._update_instant_action_status = capture_status

            adapter.instant_actions_accept_procedure(
                make_instant_action(
                    "pioReadIn",
                    "pio-1",
                    params={"channel": 2, "timeoutSec": 0.1},
                )
            )

            for _ in range(100):
                if any(status == ActionStatus.FINISHED for _, status, _ in statuses):
                    break
                await asyncio.sleep(0.01)

            self.assertIn(("pio-1", ActionStatus.RUNNING, None), statuses)
            self.assertTrue(
                any(
                    action_id == "pio-1"
                    and status == ActionStatus.FINISHED
                    and "pioReadIn finished" in str(description)
                    for action_id, status, description in statuses
                )
            )

        asyncio.run(scenario())

    def test_ezio_instant_action_runs_through_registry_extension(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.state = type(
                "State",
                (),
                {"instant_action_states": [], "errors": [], "information": []},
            )()
            adapter._loop = asyncio.get_running_loop()
            inputs = [0] * 16
            inputs[8] = 1
            adapter.set_ezi_io(FakeEziIo([inputs]))
            self.assertTrue(adapter._action_registry.has("photoSensorRead"))
            adapter._handle_ezio_instant_action = lambda action: self.fail(
                "EZI IO instant action should be dispatched by the registry extension"
            )
            statuses = []
            original_update = adapter._update_instant_action_status

            def capture_status(action_id, status, result_description=None):
                statuses.append((action_id, status, result_description))
                original_update(action_id, status, result_description)

            adapter._update_instant_action_status = capture_status

            adapter.instant_actions_accept_procedure(
                make_instant_action("photoSensorRead", "ezio-1")
            )

            for _ in range(100):
                if any(status == ActionStatus.FINISHED for _, status, _ in statuses):
                    break
                await asyncio.sleep(0.01)

            self.assertIn(("ezio-1", ActionStatus.RUNNING, None), statuses)
            self.assertTrue(
                any(
                    action_id == "ezio-1"
                    and status == ActionStatus.FINISHED
                    and "photoSensorRead finished" in str(description)
                    for action_id, status, description in statuses
                )
            )

        asyncio.run(scenario())

    def test_cancel_order_clears_active_order_state_columns(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(make_order())
                await asyncio.sleep(0.2)

                self.assertEqual(adapter.state.order_id, "order-1")
                self.assertEqual(adapter.state.order_update_id, 0)
                self.assertTrue(adapter.state.node_states)
                self.assertTrue(adapter.state.action_states)

                adapter.instant_actions_accept_procedure(make_cancel_order_action())
                for _ in range(100):
                    if (
                        adapter.state.order_id == ""
                        and adapter.state.node_states == []
                        and adapter.state.edge_states == []
                    ):
                        break
                    await asyncio.sleep(0.01)

                self.assertEqual(adapter.state.order_id, "")
                self.assertEqual(adapter.state.order_update_id, 0)
                self.assertEqual(adapter.state.node_states, [])
                self.assertEqual(adapter.state.edge_states, [])
                self.assertEqual(adapter.state.action_states, [])
                self.assertIsNone(adapter.order)
                self.assertIsNone(adapter.current_order_step)
                self.assertEqual(adapter.order_queue.qsize(), 0)
                self.assertEqual(vehicle.stop_motion_calls, 1)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_stale_cancel_does_not_clear_newer_order_accepted_while_stopping(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            standstill_entered = asyncio.Event()
            allow_standstill = asyncio.Event()

            async def delayed_standstill() -> None:
                standstill_entered.set()
                await allow_standstill.wait()

            try:
                with patch.object(
                    adapter,
                    "_wait_until_vehicle_standstill",
                    side_effect=delayed_standstill,
                ):
                    cancel_task = asyncio.create_task(
                        adapter._cancel_order_on_loop("stale-cancel", "")
                    )
                    await standstill_entered.wait()

                    newer_order = _one_node_order("new-order")
                    adapter._accept_v3_order_for_queue(newer_order)
                    allow_standstill.set()
                    await cancel_task

                self.assertIs(adapter.order, newer_order)
                self.assertEqual(adapter.state.order_id, "new-order")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_cancel_order_clears_state_even_when_stop_motion_fails(self) -> None:
        # Real-robot bug: when stop_motion raised, _cancel_order_on_loop returned
        # early WITHOUT clearing order state, so the adapter kept publishing the
        # order's nodeStates and the FMS kept deriving a destination (dstNodeId).
        # The order is cancelled regardless of whether motion-stop succeeded, so
        # derived order state must always be cleared.
        class FailingStopVehicle(FakeVehicle):
            async def stop_motion(self) -> None:
                self.stop_motion_calls += 1
                raise RuntimeError("stop_motion boom")

        async def scenario() -> None:
            adapter = Adapter()
            vehicle = FailingStopVehicle()
            adapter.set_vehicle(vehicle)
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(make_order())
                await asyncio.sleep(0.2)
                self.assertTrue(adapter.state.node_states)

                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(make_cancel_order_action())
                    await _await_terminal_instant_status(spy, "ia-cancel")

                self.assertEqual(vehicle.stop_motion_calls, 1)
                self.assertEqual(adapter.state.order_id, "")
                self.assertEqual(adapter.state.node_states, [])
                self.assertEqual(adapter.state.edge_states, [])
                self.assertIsNone(adapter.order)
                # The order IS cancelled, so cancelOrder must report FINISHED so the
                # FMS resets its order-derived state. FAILED is reserved for a
                # rejected cancel, on which the FMS deliberately KEEPS the order.
                # The motion-stop failure is surfaced in the result description.
                finished = [
                    c
                    for c in spy.call_args_list
                    if c.args[0] == "ia-cancel" and c.args[1] == ActionStatus.FINISHED
                ]
                self.assertTrue(
                    finished,
                    "cancelOrder must FINISH (order cancelled) even when motion-stop fails",
                )
                self.assertTrue(
                    any(
                        "stop not confirmed"
                        in (c.kwargs.get("result_description") or "")
                        for c in finished
                    ),
                    "stop failure must be surfaced in the result description",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_cancel_order_times_out_waiting_for_standstill_and_clears_state(self) -> None:
        # Real-robot bug: _wait_until_vehicle_standstill looped with no timeout, so
        # when telemetry kept reporting "Driving" after stop_motion the cancel hung
        # forever in RUNNING and order state (nodeStates) was never cleared, leaving
        # the FMS-derived dstNodeId stuck until a full system reset. A bounded wait
        # must give up, still clear order state, and FINISH (order cancelled).
        class NeverStopsVehicle(FakeVehicle):
            def __init__(self) -> None:
                super().__init__()
                self._status = "Driving"

            async def stop_motion(self) -> None:
                self.stop_motion_calls += 1
                # status stays "Driving": the robot never reports standstill.

        async def scenario() -> None:
            adapter = Adapter()
            vehicle = NeverStopsVehicle()
            adapter.set_vehicle(vehicle)
            adapter.config.settings.standstill_timeout_sec = 0.2
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(make_order())
                await asyncio.sleep(0.2)
                self.assertTrue(adapter.state.node_states)

                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(make_cancel_order_action())
                    await _await_terminal_instant_status(spy, "ia-cancel", timeout=4.0)

                self.assertEqual(adapter.state.node_states, [])
                self.assertEqual(adapter.state.order_id, "")
                finished = [
                    c
                    for c in spy.call_args_list
                    if c.args[0] == "ia-cancel" and c.args[1] == ActionStatus.FINISHED
                ]
                self.assertTrue(
                    finished,
                    "cancelOrder must FINISH (not hang/FAIL) when standstill times out",
                )
                self.assertTrue(
                    any(
                        "stop not confirmed"
                        in (c.kwargs.get("result_description") or "")
                        for c in finished
                    ),
                    "standstill timeout must be surfaced in the result description",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_cancel_order_clears_queue_on_adapter_loop_from_mqtt_thread(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            loop_thread_id = threading.get_ident()
            clear_thread_ids = []
            original_clear_order_queue = adapter._clear_order_queue

            def clear_order_queue_spy(*args, **kwargs) -> None:
                clear_thread_ids.append(threading.get_ident())
                original_clear_order_queue(*args, **kwargs)

            adapter._clear_order_queue = clear_order_queue_spy

            try:
                adapter._handle_v3_order(make_order())
                await asyncio.sleep(0.2)
                self.assertFalse(adapter.order_worker_task.done())
                self.assertIn("N2", vehicle.goto_targets)

                callback_thread = threading.Thread(
                    target=lambda: adapter.instant_actions_accept_procedure(
                        make_cancel_order_action()
                    )
                )
                callback_thread.start()
                callback_thread.join(timeout=1.0)
                self.assertFalse(callback_thread.is_alive())

                statuses = await _settle_instant_actions(adapter, "ia-cancel")

                self.assertEqual(statuses["ia-cancel"], ActionStatus.FINISHED)
                self.assertEqual(adapter.state.node_states, [])
                self.assertEqual(adapter.state.edge_states, [])
                self.assertTrue(clear_thread_ids)
                self.assertTrue(
                    all(thread_id == loop_thread_id for thread_id in clear_thread_ids)
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_order_callback_mutates_queue_on_adapter_loop_from_mqtt_thread(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.vehicle.serial_number = "HN-SH6-TR-001"
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            loop_thread_id = threading.get_ident()
            rebuild_thread_ids = []
            original_rebuild_order_queue = adapter._rebuild_v3_order_queue_from_state

            def rebuild_order_queue_spy() -> None:
                rebuild_thread_ids.append(threading.get_ident())
                original_rebuild_order_queue()

            adapter._rebuild_v3_order_queue_from_state = rebuild_order_queue_spy

            try:
                callback_thread = threading.Thread(
                    target=lambda: adapter.handle_incoming_acs_cmd(
                        "amr/v3/HN-SH6-TR-001/order",
                        make_order(),
                    )
                )
                callback_thread.start()
                callback_thread.join(timeout=1.0)
                self.assertFalse(callback_thread.is_alive())

                for _ in range(100):
                    if rebuild_thread_ids and "N2" in vehicle.goto_targets:
                        break
                    await asyncio.sleep(0.01)

                self.assertTrue(rebuild_thread_ids)
                self.assertTrue(
                    all(thread_id == loop_thread_id for thread_id in rebuild_thread_ids)
                )
                self.assertIn("N2", vehicle.goto_targets)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_new_order_bootstraps_from_current_vehicle_station(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._station = "N3"
            vehicle._x = 1000.0
            vehicle._y = 0.0
            vehicle._map_nodes = {
                "N1": (0.0, 0.0, 0.0),
                "N2": (10.0, 0.0, 0.0),
                "N3": (1000.0, 0.0, 0.0),
                "N4": (1100.0, 0.0, 0.0),
            }
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 30,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "resume-order",
                    "orderUpdateId": 2,
                    "nodes": [
                        {"nodeId": "N1", "sequenceId": 0, "released": True, "actions": []},
                        {"nodeId": "N2", "sequenceId": 2, "released": True, "actions": []},
                        {"nodeId": "N3", "sequenceId": 4, "released": False, "actions": []},
                        {"nodeId": "N4", "sequenceId": 6, "released": False, "actions": []},
                    ],
                    "edges": [
                        {
                            "edgeId": "E1",
                            "sequenceId": 1,
                            "released": True,
                            "startNodeId": "N1",
                            "endNodeId": "N2",
                            "actions": [],
                        },
                        {
                            "edgeId": "E2",
                            "sequenceId": 3,
                            "released": False,
                            "startNodeId": "N2",
                            "endNodeId": "N3",
                            "actions": [],
                        },
                        {
                            "edgeId": "E3",
                            "sequenceId": 5,
                            "released": False,
                            "startNodeId": "N3",
                            "endNodeId": "N4",
                            "actions": [],
                        },
                    ],
                }
            )

            adapter._handle_v3_order(order)
            await asyncio.sleep(0.1)

            self.assertEqual(adapter.state.last_node_id, "N3")
            self.assertEqual(adapter.state.last_node_sequence_id, 4)
            self.assertEqual(vehicle.goto_targets, [])
            self.assertEqual([node.node_id for node in adapter.state.node_states], ["N4"])
            self.assertEqual([edge.edge_id for edge in adapter.state.edge_states], ["E3"])
            self.assertEqual(adapter.order_queue.qsize(), 0)

            update = Order.from_dict(
                {
                    "headerId": 31,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "resume-order",
                    "orderUpdateId": 3,
                    "nodes": [
                        {"nodeId": "N1", "sequenceId": 0, "released": True, "actions": []},
                        {"nodeId": "N2", "sequenceId": 2, "released": True, "actions": []},
                        {"nodeId": "N3", "sequenceId": 4, "released": True, "actions": []},
                        {"nodeId": "N4", "sequenceId": 6, "released": True, "actions": []},
                    ],
                    "edges": [
                        {
                            "edgeId": "E1",
                            "sequenceId": 1,
                            "released": True,
                            "startNodeId": "N1",
                            "endNodeId": "N2",
                            "actions": [],
                        },
                        {
                            "edgeId": "E2",
                            "sequenceId": 3,
                            "released": True,
                            "startNodeId": "N2",
                            "endNodeId": "N3",
                            "actions": [],
                        },
                        {
                            "edgeId": "E3",
                            "sequenceId": 5,
                            "released": True,
                            "startNodeId": "N3",
                            "endNodeId": "N4",
                            "actions": [],
                        },
                    ],
                }
            )

            adapter._handle_v3_order(update)
            for _ in range(100):
                if vehicle.goto_targets:
                    break
                await asyncio.sleep(0.01)
            vehicle.arrive_at(1100.0, 0.0)
            await asyncio.wait_for(adapter.order_worker_task, timeout=1.0)
            for _ in range(100):
                if adapter.state.last_node_id == "N4":
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(vehicle.goto_targets, ["N4"])
            self.assertEqual(adapter.state.last_node_id, "N4")
            self.assertEqual(adapter.state.last_node_sequence_id, 6)

            state_task.cancel()

        asyncio.run(scenario())

    def _elevator_order(self, update_id: int, p2_released: bool) -> Order:
        """p10 -> p36(HARD 액션) -> p2. p2 쪽 절반은 horizon 또는 base 로 준다."""
        return Order.from_dict(
            {
                "headerId": 100 + update_id,
                "timestamp": "2026-08-22T00:00:00.000Z",
                "version": "3.0.0",
                "manufacturer": "jibot",
                "serialNumber": "HN-SH6-TR-001",
                "orderId": "elevator-order",
                "orderUpdateId": update_id,
                "nodes": [
                    {
                        "nodeId": "p10", "sequenceId": 0, "released": True,
                        "nodePosition": {
                            "x": 0.0, "y": 0.0, "theta": 0.0,
                            "mapId": "lab2m", "allowedDeviationXY": 5.0,
                        },
                        "actions": [],
                    },
                    {
                        "nodeId": "p36", "sequenceId": 2, "released": True,
                        "nodePosition": {
                            "x": 1000.0, "y": 0.0, "theta": 0.0,
                            "mapId": "lab2m", "allowedDeviationXY": 5.0,
                        },
                        "actions": [
                            {
                                "actionType": "pioElevatorOpen",
                                "actionId": "a-elevator",
                                "blockingType": "HARD",
                                "actionParameters": [],
                            }
                        ],
                    },
                    {
                        "nodeId": "p2", "sequenceId": 4, "released": p2_released,
                        "nodePosition": {
                            "x": 2000.0, "y": 0.0, "theta": 0.0,
                            "mapId": "lab2m", "allowedDeviationXY": 5.0,
                        },
                        "actions": [],
                    },
                ],
                "edges": [
                    {"edgeId": "E1", "sequenceId": 1, "released": True,
                     "startNodeId": "p10", "endNodeId": "p36", "actions": []},
                    {"edgeId": "E2", "sequenceId": 3, "released": p2_released,
                     "startNodeId": "p36", "endNodeId": "p2", "actions": []},
                ],
            }
        )

    async def _run_elevator_order(self, adapter, updates: int):
        """p36 에 도착시킨 뒤, 액션이 도는 동안 orderUpdate 를 `updates` 번 밀어넣는다.

        _execute_order_action 에 넘어간 actionId 를 호출 순서대로 돌려준다.
        """
        vehicle: FakeVehicle = adapter._vehicle
        calls: List[str] = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def execute(action, _action_state, _owner_id=None):
            calls.append(action.action_id)
            started.set()
            await release.wait()

        adapter._execute_order_action = execute

        adapter._handle_v3_order(self._elevator_order(0, p2_released=updates == 0))
        for _ in range(200):
            if "p36" in vehicle.goto_targets:
                break
            await asyncio.sleep(0.01)
        vehicle.arrive_at(1000.0, 0.0)
        await asyncio.wait_for(started.wait(), timeout=5.0)

        for index in range(updates):
            adapter._handle_v3_order(self._elevator_order(index + 1, p2_released=True))
            await asyncio.sleep(0.2)

        release.set()
        await asyncio.sleep(0.3)
        return calls

    def test_node_action_runs_once_without_an_order_update(self) -> None:
        """아래 중복 실행 회귀 테스트의 대조군."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                calls = await self._run_elevator_order(adapter, updates=0)
                self.assertEqual(calls, ["a-elevator"])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_order_update_does_not_re_execute_a_running_node_action(self) -> None:
        """orderUpdate 가 방금 도착한 노드의 액션을 재실행해서는 안 된다.

        현장 증상은 p36 에서 p2 로 떠나기 전에 pioElevatorOpen 이 두 번 발사된
        것이었다. 두 번 온 게 아니다. p36 도착이 액션 실행 '전에'
        lastNodeSequenceId 를 올리기 때문에, "node reached" 뒤에 따라오는
        horizon 릴리즈 업데이트가 p36 을 actions_only 스텝으로 큐에 다시 넣고
        워커를 액션 도중에 취소해, 같은 actionId 가 한 번 더 실행됐다.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                calls = await self._run_elevator_order(adapter, updates=1)
                self.assertEqual(calls, ["a-elevator"])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_repeated_order_updates_do_not_stack_node_action_replays(self) -> None:
        """업데이트가 한 번 더 올 때마다 같은 액션의 재실행이 한 번씩 더 붙었다."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                calls = await self._run_elevator_order(adapter, updates=2)
                self.assertEqual(calls, ["a-elevator"])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_step_actions_skip_an_action_that_already_left_waiting(self) -> None:
        """멱등성 최후 방어선: 큐에 다시 들어온 스텝은 아무것도 다시 dispatch 하지 않는다."""
        async def scenario() -> None:
            adapter, step = self._blocking_probe_adapter([("hard", "HARD")])
            calls = []

            async def execute(action, _state, _owner):
                calls.append(action.action_id)

            adapter._execute_order_action = execute

            self.assertTrue(await adapter._process_v3_step_actions(step))
            self.assertEqual(calls, ["hard"])

            # 첫 실행이 terminal 로 끝냈다. 같은 스텝을 또 도는 건 새 일이 아니라
            # 재실행이다.
            adapter.state.action_states[0].action_status = ActionStatus.FINISHED
            self.assertTrue(await adapter._process_v3_step_actions(step))
            self.assertEqual(calls, ["hard"])

        asyncio.run(scenario())

    def test_order_update_restarts_worker_when_active_step_is_already_past(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "N1": (0.0, 0.0, 0.0),
                "N2": (1000.0, 0.0, 0.0),
                "N3": (2000.0, 0.0, 0.0),
            }
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 50,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "stale-worker-order",
                    "orderUpdateId": 1,
                    "nodes": [
                        {"nodeId": "N1", "sequenceId": 0, "released": True, "actions": []},
                        {"nodeId": "N2", "sequenceId": 2, "released": True, "actions": []},
                        {"nodeId": "N3", "sequenceId": 4, "released": False, "actions": []},
                    ],
                    "edges": [
                        {
                            "edgeId": "E1",
                            "sequenceId": 1,
                            "released": True,
                            "startNodeId": "N1",
                            "endNodeId": "N2",
                            "actions": [],
                        },
                        {
                            "edgeId": "E2",
                            "sequenceId": 3,
                            "released": False,
                            "startNodeId": "N2",
                            "endNodeId": "N3",
                            "actions": [],
                        },
                    ],
                }
            )

            try:
                adapter._handle_v3_order(order)
                for _ in range(100):
                    if adapter.current_order_step is not None:
                        break
                    await asyncio.sleep(0.01)

                self.assertEqual(adapter.current_order_step.sequence_id, 2)
                self.assertFalse(adapter.order_worker_task.done())

                adapter.state.last_node_id = "N2"
                adapter.state.last_node_sequence_id = 2
                adapter._last_node_id = "N2"
                adapter._last_node_sequence_id = 2

                update = Order.from_dict(
                    {
                        "headerId": 51,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "orderId": "stale-worker-order",
                        "orderUpdateId": 2,
                        "nodes": [
                            {"nodeId": "N1", "sequenceId": 0, "released": True, "actions": []},
                            {"nodeId": "N2", "sequenceId": 2, "released": True, "actions": []},
                            {"nodeId": "N3", "sequenceId": 4, "released": True, "actions": []},
                        ],
                        "edges": [
                            {
                                "edgeId": "E1",
                                "sequenceId": 1,
                                "released": True,
                                "startNodeId": "N1",
                                "endNodeId": "N2",
                                "actions": [],
                            },
                            {
                                "edgeId": "E2",
                                "sequenceId": 3,
                                "released": True,
                                "startNodeId": "N2",
                                "endNodeId": "N3",
                                "actions": [],
                            },
                        ],
                    }
                )

                adapter._handle_v3_order(update)
                for _ in range(100):
                    if "N3" in vehicle.goto_targets:
                        break
                    await asyncio.sleep(0.01)

                self.assertIn("N3", vehicle.goto_targets)
            finally:
                state_task.cancel()
                if adapter.order_worker_task is not None:
                    adapter.order_worker_task.cancel()

        asyncio.run(scenario())

    def test_new_order_sequence_ids_do_not_inherit_previous_order_progress(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._station = "F1_60"
            vehicle._map_nodes = {
                "F1_60": (12975.0, 4626.0, 0.0),
                "F2_80_S2IC": (14159.0, 3615.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)

            try:
                adapter.state.last_node_id = "F1_60"
                adapter.state.last_node_sequence_id = 6
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 6

                order = Order.from_dict(
                    {
                        "headerId": 60,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "orderId": "new-order-after-seq-six",
                        "orderUpdateId": 0,
                        "nodes": [
                            {"nodeId": "F1_60", "sequenceId": 0, "released": True, "actions": []},
                            {"nodeId": "F2_80_S2IC", "sequenceId": 2, "released": True, "actions": []},
                            {"nodeId": "F2_90_S2CH", "sequenceId": 4, "released": False, "actions": []},
                        ],
                        "edges": [
                            {
                                "edgeId": "e_F1_60_F2_80_S2IC",
                                "sequenceId": 1,
                                "released": True,
                                "startNodeId": "F1_60",
                                "endNodeId": "F2_80_S2IC",
                                "actions": [],
                            },
                            {
                                "edgeId": "e_F2_80_S2IC_F2_90_S2CH",
                                "sequenceId": 3,
                                "released": False,
                                "startNodeId": "F2_80_S2IC",
                                "endNodeId": "F2_90_S2CH",
                                "actions": [],
                            },
                        ],
                    }
                )

                adapter._handle_v3_order(order)
                for _ in range(100):
                    if "F2_80_S2IC" in vehicle.goto_targets:
                        break
                    await asyncio.sleep(0.01)

                self.assertEqual(adapter.state.last_node_id, "F1_60")
                self.assertEqual(adapter.state.last_node_sequence_id, 0)
                self.assertIn("F2_80_S2IC", vehicle.goto_targets)
            finally:
                state_task.cancel()
                if adapter.order_worker_task is not None:
                    adapter.order_worker_task.cancel()

        asyncio.run(scenario())

    def test_single_node_order_not_in_prior_route_is_not_skipped(self) -> None:
        """A new single-node order whose only node is NOT the parked last node
        must still be queued and driven.

        Regression: after the robot parks at the end of a prior order
        (last_node_id set, last_node_sequence_id > 0), an FMS charge order that
        carries a single destination node at sequenceId 0 (and no edges) was
        dropped to 0 steps — _rebuild_v3_order_queue_from_state skipped the node
        because seq 0 <= the stale last_node_sequence_id from the finished order,
        even though that last node is not part of the new order. The robot then
        sat still while the master believed the order completed.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._station = "F1_20_S1IC"
            vehicle._x = 6133.0
            vehicle._y = 6054.0
            vehicle._map_nodes = {
                "F1_20_S1IC": (6133.0, 6054.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)

            try:
                # Robot parked at F1_20_S1IC, carried over from a finished order
                # whose last released node was sequenceId 6.
                adapter.state.last_node_id = "F1_20_S1IC"
                adapter.state.last_node_sequence_id = 6
                adapter._last_node_id = "F1_20_S1IC"
                adapter._last_node_sequence_id = 6

                order = Order.from_dict(
                    {
                        "headerId": 0,
                        "timestamp": "2026-06-27T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "orderId": "single-node-charge",
                        "orderUpdateId": 0,
                        "nodes": [
                            {
                                "nodeId": "F2_90_S2CH",
                                "sequenceId": 0,
                                "released": True,
                                "actions": [],
                            },
                        ],
                        "edges": [],
                    }
                )

                adapter._handle_v3_order(order)
                for _ in range(100):
                    if "F2_90_S2CH" in vehicle.goto_targets:
                        break
                    await asyncio.sleep(0.01)

                self.assertIn(
                    "F2_90_S2CH",
                    vehicle.goto_targets,
                    "single-node order must drive to its node, not silently "
                    "complete with 0 steps",
                )
            finally:
                state_task.cancel()
                if adapter.order_worker_task is not None:
                    adapter.order_worker_task.cancel()

        asyncio.run(scenario())

    def test_active_order_state_loop_updates_last_node_from_nearest_position(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._x = 14117.0
            vehicle._y = 3725.0
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_80_S2IC": (14500.0, 3725.0, 0.0),
                "F2_90_S2CH": (14350.0, 3725.0, 0.0),
            }
            # This test is ABOUT proximity mode, so pin it: config.toml now
            # ships "settled", which suppresses capture during an active order
            # and is exactly the behaviour asserted against below.
            adapter.config.settings.last_node_capture_mode = "proximity"
            state_task = await self._start_state(adapter)

            try:
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = "F1_60"
                adapter.state.last_node_sequence_id = 0

                order = Order.from_dict(
                    {
                        "headerId": 61,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "orderId": "nearest-future-node-order",
                        "orderUpdateId": 2,
                        "nodes": [
                            {"nodeId": "F1_60", "sequenceId": 0, "released": True, "actions": []},
                            {"nodeId": "F2_80_S2IC", "sequenceId": 2, "released": True, "actions": []},
                            {"nodeId": "F2_90_S2CH", "sequenceId": 4, "released": True, "actions": []},
                        ],
                        "edges": [
                            {
                                "edgeId": "e_F1_60_F2_80_S2IC",
                                "sequenceId": 1,
                                "released": True,
                                "startNodeId": "F1_60",
                                "endNodeId": "F2_80_S2IC",
                                "actions": [],
                            },
                            {
                                "edgeId": "e_F2_80_S2IC_F2_90_S2CH",
                                "sequenceId": 3,
                                "released": True,
                                "startNodeId": "F2_80_S2IC",
                                "endNodeId": "F2_90_S2CH",
                                "actions": [],
                            },
                        ],
                    }
                )

                adapter._handle_v3_order(order)
                for _ in range(100):
                    if vehicle.goto_targets:
                        break
                    await asyncio.sleep(0.01)

                await asyncio.sleep(0.1)

                # In proximity mode the publish loop mirrors nearestNodeId to
                # lastNodeId even during an active order and outside the reach
                # threshold.
                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._nearest_node_sequence_id, 4)
                self.assertEqual(adapter.state.last_node_id, "F2_90_S2CH")
                self.assertEqual(vehicle.goto_targets, ["F2_80_S2IC"])
            finally:
                state_task.cancel()
                if adapter.order_worker_task is not None:
                    adapter.order_worker_task.cancel()

        asyncio.run(scenario())

    def test_last_node_uses_nearest_map_node_outside_active_order(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
                "SIDE_NODE": (13000.0, 4700.0, 0.0),
            }
            state_task = await self._start_state(adapter)

            try:
                adapter.state.order_id = "active-order"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter.state.edge_states = [object()]

                # "outside active order" = the matched map node (SIDE_NODE) is
                # outside the ACTIVE order's node set; lastNodeId must be unaffected.
                adapter._update_nearest_node_from_position(13001.0, 4699.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "SIDE_NODE")
                self.assertEqual(adapter._nearest_node_sequence_id, 0)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_active_order_last_node_follows_nearest_xy_position(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            # "settled" contract (lastNodeId = order progress); config.toml's
            # deployment default is now "proximity", so set it explicitly here.
            adapter.config.settings.last_node_capture_mode = "settled"
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_80_S2IC": (14159.0, 3615.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)

            try:
                adapter.state.order_id = "nearest-xy-order"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_80_S2IC", 2, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter.state.edge_states = [object()]
                adapter.state.last_node_id = "F1_60"
                adapter.state.last_node_sequence_id = 0
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0

                adapter._update_nearest_node_from_position(14117.0, 3725.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._nearest_node_sequence_id, 4)
                # lastNodeId stays at the reached node, not the nearest one.
                self.assertEqual(adapter._last_node_id, "F1_60")
                self.assertTrue(
                    any(
                        getattr(info, "info_type", None) == "NEAREST_NODE"
                        for info in adapter.state.information
                    ),
                    "updater must publish a NEAREST_NODE info entry on a match",
                )
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_last_node_can_move_to_lower_sequence_when_xy_is_nearest(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            # "settled" contract (lastNodeId = order progress); config.toml's
            # deployment default is now "proximity", so set it explicitly here.
            adapter.config.settings.last_node_capture_mode = "settled"
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_80_S2IC": (14159.0, 3615.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)

            try:
                adapter.state.order_id = "lower-seq-order"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_80_S2IC", 2, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter.state.last_node_id = "F2_90_S2CH"
                adapter.state.last_node_sequence_id = 4
                adapter._last_node_id = "F2_90_S2CH"
                adapter._last_node_sequence_id = 4

                adapter._update_nearest_node_from_position(12960.0, 4600.0, 0.0)

                # The NEAREST variable may take a lower sequence; lastNodeId is
                # monotonic and must NOT regress during an active order.
                self.assertEqual(adapter._nearest_node_id, "F1_60")
                self.assertEqual(adapter._nearest_node_sequence_id, 0)
                self.assertEqual(adapter._last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_sequence_id, 4)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_refresh_nearest_node_information_uses_nearest_keys(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._nearest_node_id = "S2"
                adapter._nearest_node_distance = 12.3
                adapter._refresh_nearest_node_information()
                entry = next(
                    info for info in adapter.state.information
                    if getattr(info, "info_type", None) == "NEAREST_NODE"
                )
                refs = {r.reference_key: r.reference_value for r in entry.info_references}
                self.assertEqual(refs["nearestNodeId"], "S2")
                self.assertEqual(refs["gap"], "12.3")
                self.assertFalse(
                    any(getattr(i, "info_type", None) == "LAST_NODE_GAP"
                        for i in adapter.state.information)
                )

                # gap rounds (not truncates) to one decimal place.
                adapter._nearest_node_distance = 12.36
                adapter._refresh_nearest_node_information()
                entry = next(
                    info for info in adapter.state.information
                    if getattr(info, "info_type", None) == "NEAREST_NODE"
                )
                refs = {r.reference_key: r.reference_value for r in entry.info_references}
                self.assertEqual(refs["gap"], "12.4")

                adapter._clear_nearest_node_information()
                self.assertFalse(
                    any(getattr(i, "info_type", None) == "NEAREST_NODE"
                        for i in adapter.state.information)
                )
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_nearest_node_information_also_carries_last_node_gap(self) -> None:
        # Healthy case: lastNodeId is present (so the LAST_NODE_ID_MISSING
        # warning is suppressed). The NEAREST_NODE telemetry must STILL expose
        # the distance from the current pose to lastNodeId, mirroring the
        # always-on nearest gap, so ACS/monitor can see whether the robot is
        # actually parked at the node lastNodeId claims.
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "N1": (3.0, 4.0, 0.0),
                "F2_90_S2CH": (6.0, 8.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.agv_position.x = 3.0
                adapter.state.agv_position.y = 4.0
                adapter._nearest_node_id = "N1"
                adapter._nearest_node_distance = 0.0
                adapter._last_node_id = "F2_90_S2CH"
                adapter.state.last_node_id = "F2_90_S2CH"

                adapter._refresh_nearest_node_information()

                entry = next(
                    info for info in adapter.state.information
                    if getattr(info, "info_type", None) == "NEAREST_NODE"
                )
                refs = {r.reference_key: r.reference_value for r in entry.info_references}
                # nearest references are unchanged
                self.assertEqual(refs["nearestNodeId"], "N1")
                self.assertEqual(refs["gap"], "0.0")
                # lastNode gap is now exposed alongside, even though lastNodeId
                # is present (distance from (3,4) to (6,8) == 5.0)
                self.assertEqual(refs["lastNodeId"], "F2_90_S2CH")
                self.assertEqual(refs["lastNodeGap"], "5.0")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_settled_does_not_touch_last_node_during_active_order(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0
                adapter.config.settings.last_node_capture_mode = "settled"

                # Robot drifts nearest to the later node while the order is active.
                adapter._update_nearest_node_from_position(14040.0, 3750.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._nearest_node_sequence_id, 4)
                # lastNodeId must NOT be dragged forward by position.
                self.assertEqual(adapter._last_node_id, "F1_60")
                self.assertEqual(adapter._last_node_sequence_id, 0)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_proximity_updates_last_node_during_active_order(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = "F1_60"
                adapter.state.last_node_sequence_id = 0
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_capture_mode = "proximity"

                adapter._update_nearest_node_from_position(14040.0, 3750.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._nearest_node_sequence_id, 4)
                self.assertEqual(adapter._last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_sequence_id, 4)
                self.assertEqual(adapter.state.last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter.state.last_node_sequence_id, 4)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_proximity_updates_pass_through_node_while_driving(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._status = "Driving"
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
                "F2_100": (14500.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                    Node("F2_100", 6, True, []),
                ]
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_capture_mode = "proximity"

                adapter._update_nearest_node_from_position(14040.0, 3750.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_sequence_id, 4)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_proximity_updates_destination_while_driving(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._status = "Driving"
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_capture_mode = "proximity"

                adapter._update_nearest_node_from_position(14040.0, 3750.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_sequence_id, 4)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_idle_bootstrap_seeds_last_node(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "A": (0.0, 0.0, 0.0),
                "B": (1200.0, 0.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""   # no active order
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                # Pin the idle-recovery threshold so the test validates the
                # seed/keep logic independent of the deployed config value.
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "proximity"

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "B")  # idle bootstrap
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_missing_last_node_can_be_seeded_from_nearest_without_reach_gate(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points = {"p40": (1000.0, 0.0, 0.0)}
            adapter.config.settings.nearest_node_mode = "pathPoint"
            adapter.config.settings.last_node_capture_mode = "disabled"
            adapter.config.settings.idle_last_node_reach_xy = 10.0
            adapter.config.settings.use_nearest_node_as_last_node_when_missing = True
            state_task = await self._start_state(adapter)
            try:
                adapter._update_nearest_node_from_position(0.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "p40")
                self.assertEqual(adapter._nearest_node_distance, 1000.0)
                self.assertEqual(adapter._last_node_id, "p40")
                self.assertEqual(adapter.state.last_node_id, "p40")
                self.assertEqual(adapter.state.last_node_sequence_id, 0)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_missing_last_node_seed_is_refused_beyond_its_own_reach_gate(self) -> None:
        """A far nearest node must not become lastNodeId once a gate is set.

        Without this an off-map or wrong-floor boot seeds a groundless
        lastNodeId that FMS then trusts as the robot's position.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points = {"p40": (1000.0, 0.0, 0.0)}
            adapter.config.settings.nearest_node_mode = "pathPoint"
            adapter.config.settings.last_node_capture_mode = "disabled"
            adapter.config.settings.use_nearest_node_as_last_node_when_missing = True
            adapter.config.settings.missing_last_node_reach_xy = 500.0
            state_task = await self._start_state(adapter)
            try:
                adapter._update_nearest_node_from_position(0.0, 0.0, 0.0)

                # Telemetry still reports the nearest node; only the seed is gated.
                self.assertEqual(adapter._nearest_node_id, "p40")
                self.assertEqual(adapter._nearest_node_distance, 1000.0)
                self.assertEqual(adapter._last_node_id, "")
                self.assertEqual(adapter.state.last_node_id, "")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_missing_last_node_seed_is_allowed_inside_its_own_reach_gate(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points = {"p40": (400.0, 0.0, 0.0)}
            adapter.config.settings.nearest_node_mode = "pathPoint"
            adapter.config.settings.last_node_capture_mode = "disabled"
            adapter.config.settings.use_nearest_node_as_last_node_when_missing = True
            adapter.config.settings.missing_last_node_reach_xy = 500.0
            state_task = await self._start_state(adapter)
            try:
                adapter._update_nearest_node_from_position(0.0, 0.0, 0.0)

                self.assertEqual(adapter._last_node_id, "p40")
                self.assertEqual(adapter.state.last_node_id, "p40")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_missing_last_node_gate_is_independent_of_idle_reach_xy(self) -> None:
        """The gate is its own setting: idle_last_node_reach_xy must not apply.

        idle_last_node_reach_xy treats <=0 as 'fall back to the deviation', so
        sharing it would make 'no gate' unexpressible for this seed.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points = {"p40": (1000.0, 0.0, 0.0)}
            adapter.config.settings.nearest_node_mode = "pathPoint"
            adapter.config.settings.last_node_capture_mode = "disabled"
            adapter.config.settings.use_nearest_node_as_last_node_when_missing = True
            adapter.config.settings.idle_last_node_reach_xy = 10.0
            adapter.config.settings.missing_last_node_reach_xy = 0.0
            state_task = await self._start_state(adapter)
            try:
                adapter._update_nearest_node_from_position(0.0, 0.0, 0.0)

                self.assertEqual(adapter._last_node_id, "p40")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_nearest_last_node_fallback_does_not_overwrite_existing_last_node(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_path_points = {"p40": (1000.0, 0.0, 0.0)}
            adapter.config.settings.nearest_node_mode = "pathPoint"
            adapter.config.settings.last_node_capture_mode = "disabled"
            adapter.config.settings.use_nearest_node_as_last_node_when_missing = True
            state_task = await self._start_state(adapter)
            try:
                adapter._set_last_node("p39", 4)
                adapter._update_nearest_node_from_position(1000.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "p40")
                self.assertEqual(adapter._last_node_id, "p39")
                self.assertEqual(adapter.state.last_node_id, "p39")
                self.assertEqual(adapter.state.last_node_sequence_id, 4)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_idle_updates_last_node_within_reach_zone(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "A": (0.0, 0.0, 0.0),
                "B": (1200.0, 0.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = "A"
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = "A"
                adapter.state.last_node_sequence_id = 0
                # Pin the idle-recovery threshold (deploy-config independent).
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "proximity"

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "B")
                self.assertEqual(adapter.state.last_node_id, "B")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_idle_uses_configured_last_node_threshold(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "A": (0.0, 0.0, 0.0),
                "B": (1000.0, 0.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                # Configured threshold (400) > gap (300) => seeds; this is the
                # value the test exercises, independent of the deployed config.
                adapter.config.settings.idle_last_node_reach_xy = 400.0
                adapter.config.settings.last_node_capture_mode = "proximity"

                adapter._update_nearest_node_from_position(700.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "B")
                self.assertEqual(adapter.state.last_node_id, "B")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_proximity_updates_last_node_outside_reach_zone(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "A": (0.0, 0.0, 0.0),
                "B": (1200.0, 0.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = "A"
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = "A"
                adapter.state.last_node_sequence_id = 0
                # proximity ignores the threshold (500) even when gap is 550.
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "proximity"

                adapter._update_nearest_node_from_position(650.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "B")
                self.assertEqual(adapter.state.last_node_id, "B")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_idle_capture_disabled_does_not_seed(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0), "B": (1200.0, 0.0, 0.0)}
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "disabled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = False

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")   # telemetry still updates
                self.assertEqual(adapter._last_node_id, "")       # but lastNodeId untouched
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_idle_capture_settled_seeds_when_stopped_and_manual_inactive(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0), "B": (1200.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter._manual_control_active = False
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = False

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "B")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_idle_capture_settled_skips_during_manual_control(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0), "B": (1200.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter._manual_control_active = True   # Bug #1 condition
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = False

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "")   # not polluted
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_idle_capture_settled_skips_while_moving(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0), "B": (1200.0, 0.0, 0.0)}
            adapter._vehicle._status = "Driving"   # not stopped
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter._manual_control_active = False
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "")   # not captured mid-motion
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_settled_releases_stale_last_node_when_pose_leaves_release_radius(self) -> None:
        """settled 는 캡처만 하고 해제하지 않았다. 그래서 수동 조그로 몇 미터를
        나가도 떠난 노드를 계속 publish 했다."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._set_last_node("A", 2)
                adapter._manual_control_active = False
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_release_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = False

                adapter._update_nearest_node_from_position(2500.0, 0.0, 0.0)

                self.assertEqual(adapter._last_node_id, "")
                self.assertEqual(adapter._last_node_sequence_id, 0)
                self.assertEqual(adapter.state.last_node_id, "")
                self.assertEqual(adapter.state.last_node_sequence_id, 0)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_settled_keeps_last_node_between_capture_reach_and_release_radius(self) -> None:
        """히스테리시스: 캡처는 idle_last_node_reach_xy 이하, 해제는
        last_node_release_xy 초과에서만. 그 사이 pose 는 lastNodeId 를 흔들지 않는다."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._set_last_node("A", 2)
                adapter._manual_control_active = False
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_release_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = False

                adapter._update_nearest_node_from_position(300.0, 0.0, 0.0)

                self.assertEqual(adapter._last_node_id, "A")
                self.assertEqual(adapter._last_node_sequence_id, 2)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_settled_release_fires_while_the_manual_jog_is_still_running(self) -> None:
        """pose 가 노드를 벗어난 순간 그 주장은 이미 거짓이다. 조그가 끝나기를
        기다리면 그동안 틀린 lastNodeId 를 계속 publish 하게 된다."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0)}
            adapter._vehicle._status = "Driving"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._set_last_node("A", 2)
                adapter._manual_control_active = True
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_release_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = False

                adapter._update_nearest_node_from_position(2500.0, 0.0, 0.0)

                self.assertEqual(adapter._last_node_id, "")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_settled_release_does_not_fire_during_an_active_order(self) -> None:
        """오더 중 lastNodeId 는 오더 진척도다. 다음 노드로 가는 동안 도착했던
        노드에 남아 있는 것이 정상이다."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter._set_last_node("F1_60", 0)
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_release_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"

                adapter._update_nearest_node_from_position(14040.0, 3750.0, 0.0)

                self.assertEqual(adapter._last_node_id, "F1_60")
                self.assertEqual(adapter._last_node_sequence_id, 0)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_settled_release_is_off_when_last_node_release_xy_is_zero(self) -> None:
        """0 이면 예전 동작 그대로다. 해제를 원치 않는 로봇은 이 값을 0 으로 두면
        업그레이드 후에도 lastNodeId 가 비워지지 않는다."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._set_last_node("A", 2)
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_release_xy = 0.0
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = False

                adapter._update_nearest_node_from_position(2500.0, 0.0, 0.0)

                self.assertEqual(adapter._last_node_id, "A")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_idle_capture_disabled_never_releases_last_node(self) -> None:
        """disabled 는 pose 가 lastNodeId 를 쓰지 않는다는 뜻이다. pose 기반 해제도
        쓰기이므로, FMS 가 쥔 값은 건드리지 않는다."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._set_last_node("A", 2)
                adapter.config.settings.last_node_release_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "disabled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = False

                adapter._update_nearest_node_from_position(2500.0, 0.0, 0.0)

                self.assertEqual(adapter._last_node_id, "A")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_missing_last_node_seed_cannot_refill_outside_the_release_radius(self) -> None:
        """seed 는 게이트 없이 출하됐다(missing_last_node_reach_xy = 0). 캡이 없으면
        해제가 방금 떨군 노드를 그대로 다시 seed 해서, pose 갱신마다 lastNodeId 가
        흔들린다."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._set_last_node("A", 2)
                adapter.config.settings.idle_last_node_reach_xy = 100.0
                adapter.config.settings.last_node_release_xy = 500.0
                adapter.config.settings.missing_last_node_reach_xy = 0.0
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter.config.settings.use_nearest_node_as_last_node_when_missing = True

                adapter._update_nearest_node_from_position(2500.0, 0.0, 0.0)
                adapter._update_nearest_node_from_position(2500.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "A")
                self.assertEqual(adapter._last_node_id, "")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_no_match_clears_fields_and_information(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {}   # nothing to match
            state_task = await self._start_state(adapter)
            try:
                adapter.order = None
                adapter.state.order_id = ""
                adapter.state.node_states = []
                adapter._nearest_node_id = "STALE"
                adapter._nearest_node_sequence_id = 7
                adapter._nearest_node_distance = 3.0
                adapter._refresh_nearest_node_information()

                adapter._update_nearest_node_from_position(5.0, 5.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "")
                self.assertEqual(adapter._nearest_node_sequence_id, 0)
                self.assertIsNone(adapter._nearest_node_distance)
                self.assertFalse(
                    any(getattr(i, "info_type", None) == "NEAREST_NODE"
                        for i in adapter.state.information)
                )
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_finished_order_parks_like_idle(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {"PARK": (0.0, 0.0, 0.0)}
            state_task = await self._start_state(adapter)
            try:
                # order_id retained but the order is finished (no node/edge
                # states) -> _is_v3_order_active() is False -> idle proximity
                # capture resumes and follows the robot to where it parks.
                adapter.order = None
                adapter.state.order_id = "done-order"
                adapter.state.node_states = []
                adapter.state.edge_states = []
                adapter._last_node_id = "OLD"
                adapter._last_node_sequence_id = 5
                adapter.state.last_node_id = "OLD"
                adapter.state.last_node_sequence_id = 5

                adapter._update_nearest_node_from_position(30.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "PARK")
                self.assertEqual(adapter._last_node_id, "PARK")
                self.assertEqual(adapter.state.last_node_id, "PARK")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_active_order_does_not_seed_map_only_node(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            # "settled" contract (lastNodeId = order progress); config.toml's
            # deployment default is now "proximity", so set it explicitly here.
            adapter.config.settings.last_node_capture_mode = "settled"
            adapter.config.settings.use_nearest_node_as_last_node_when_missing = False
            vehicle._map_nodes = {
                "N0": (0.0, 0.0, 0.0),
                "SIDE": (50.0, 0.0, 0.0),   # not part of the order
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.order = None
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("N0", 0, True, []),
                    Node("N2", 2, True, []),
                ]
                adapter._last_node_id = ""        # nothing reached yet
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = ""
                adapter.state.last_node_sequence_id = 0

                # Robot sits on the non-order SIDE node during the active order.
                adapter._update_nearest_node_from_position(50.0, 0.0, 0.0)

                # Capture must NOT fire during an active order: no map-only node
                # is latched as lastNodeId.
                self.assertEqual(adapter._nearest_node_id, "SIDE")
                self.assertEqual(adapter._last_node_id, "")
                self.assertEqual(adapter.state.last_node_id, "")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_cancel_order_waits_for_simulator_stop_before_finishing(self) -> None:
        from cls_jibot_simulator import SimulatedJIBOT

        async def scenario() -> None:
            adapter = Adapter()
            vehicle = SimulatedJIBOT(config=adapter.config)
            adapter.set_vehicle(vehicle)
            await vehicle.connect_socket()
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(
                    Order.from_dict(
                        {
                            "headerId": 21,
                            "timestamp": "2026-06-10T00:00:00.000Z",
                            "version": "3.0.0",
                            "manufacturer": "jibot",
                            "serialNumber": "HN-SH6-TR-001",
                            "orderId": "sim-cancel-order",
                            "orderUpdateId": 0,
                            "nodes": [
                                {
                                    "nodeId": "S1",
                                    "sequenceId": 0,
                                    "released": True,
                                    "nodePosition": {
                                        "x": 0.0,
                                        "y": 0.0,
                                        "theta": 0.0,
                                        "mapId": "lab2m",
                                        "allowedDeviationXY": 1.0,
                                    },
                                    "actions": [],
                                },
                                {
                                    "nodeId": "S2",
                                    "sequenceId": 2,
                                    "released": True,
                                    "nodePosition": {
                                        "x": 5000.0,
                                        "y": 0.0,
                                        "theta": 0.0,
                                        "mapId": "lab2m",
                                        "allowedDeviationXY": 1.0,
                                    },
                                    "actions": [],
                                },
                            ],
                            "edges": [
                                {
                                    "edgeId": "S1-S2",
                                    "sequenceId": 1,
                                    "released": True,
                                    "startNodeId": "S1",
                                    "endNodeId": "S2",
                                    "actions": [],
                                }
                            ],
                        }
                    )
                )
                await asyncio.sleep(0.3)

                adapter.instant_actions_accept_procedure(make_cancel_order_action())
                statuses = await _settle_instant_actions(adapter, "ia-cancel")

                self.assertEqual(statuses["ia-cancel"], ActionStatus.FINISHED)
                self.assertEqual(adapter.state.node_states, [])
                self.assertEqual(adapter.state.edge_states, [])
                self.assertEqual(vehicle._status, adapter.config.jibot_status.stop)
                self.assertIsNone(vehicle._motion_task)
            finally:
                state_task.cancel()
                await vehicle.disconnect()

        asyncio.run(scenario())

    def test_simulator_accepts_fms_map_snapshot_instant_action(self) -> None:
        adapter = self._make_adapter()
        adapter._vehicle.is_simulator = True
        adapter.state = type(
            "StateStub",
            (),
            {
                "order_id": "",
                "order_update_id": 0,
                "node_states": [],
                "edge_states": [],
                "action_states": [],
                "instant_action_states": [],
                "errors": [],
                "information": [],
            },
        )()

        instant_actions = InstantActions.from_dict(
            {
                "headerId": 10,
                "timestamp": "2026-06-10T00:00:00.000Z",
                "version": "3.0.0",
                "manufacturer": "jibot",
                "serialNumber": "HN-SH6-TR-001",
                "actions": [
                    {
                        "actionType": "setMapSnapshot",
                        "actionId": "ia-map",
                        "blockingType": "NONE",
                        "actionParameters": [
                            {"key": "mapId", "value": "lab2m"},
                            {
                                "key": "nodes",
                                "value": [
                                    {"nodeId": "N1", "x": 0.0, "y": 0.0, "theta": 0.0},
                                    {
                                        "nodeId": "N2",
                                        "nodePosition": {
                                            "x": 1000.0,
                                            "y": 500.0,
                                            "theta": 0.0,
                                        },
                                    },
                                ],
                            },
                        ],
                    }
                ],
            }
        )
        adapter.instant_actions_accept_procedure(instant_actions)

        self.assertEqual(
            adapter._map_nodes(),
            {
                "N1": (0.0, 0.0, 0.0),
                "N2": (1000.0, 500.0, 0.0),
            },
        )
        self.assertEqual(adapter._current_map_id, "lab2m")
        self.assertEqual(
            _instant_action_statuses(adapter), {"ia-map": ActionStatus.FINISHED}
        )

    def test_jibot_um_series_test_action_runs_immediately_without_robot_queue(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                instant_actions = InstantActions.from_dict(
                    {
                        "headerId": 12,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "actions": [
                            {
                                "actionType": "jibotUmGoto",
                                "actionId": "ia-goto",
                                "blockingType": "NONE",
                                "actionParameters": [
                                    {"key": "target", "value": "pose"},
                                    {"key": "poseX", "value": 100.0},
                                    {"key": "poseY", "value": 0.0},
                                    {"key": "poseTh", "value": 0.0},
                                    {"key": "ackTimeout", "value": 0.2},
                                ],
                            }
                        ],
                    }
                )

                with patch.object(adapter, "_run_on_adapter_loop") as run_on_loop:
                    adapter.instant_actions_accept_procedure(instant_actions)

                run_on_loop.assert_not_called()
                self.assertEqual(
                    _instant_action_statuses(adapter),
                    {"ia-goto": ActionStatus.FINISHED},
                )
                self.assertEqual(vehicle.goto_targets, [])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_unregistered_instant_action_fails_and_records_error(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                unknown = make_instant_action(
                    "pioElevatorMove",
                    action_id="ia-unknown",
                )
                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as update:
                    adapter.instant_actions_accept_procedure(unknown)

                self.assertTrue(
                    any(
                        call.args[0] == "ia-unknown"
                        and call.args[1] == ActionStatus.FAILED
                        and "pioElevatorMove" in call.kwargs["result_description"]
                        for call in update.call_args_list
                    )
                )
                error = adapter.state.errors[-1]
                self.assertEqual(error.error_type, ErrorType.ACTION_NOT_FOUND)
                self.assertEqual(error.error_level, ErrorLevel.WARNING)
                self.assertEqual(
                    {
                        ref.reference_key: ref.reference_value
                        for ref in error.error_references
                    },
                    {
                        "actionId": "ia-unknown",
                        "actionType": "pioElevatorMove",
                        "scope": "instant",
                    },
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_unknown_jibot_um_prefix_is_not_accepted_as_test_action(self) -> None:
        adapter = self._make_adapter()
        action = make_instant_action("jibotUmMissing").actions[0]

        self.assertFalse(adapter._is_jibot_um_test_instant_action(action))

    def test_instant_action_rejects_non_none_blocking_type(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                invalid = InstantActions.from_dict(
                    {
                        "headerId": 45,
                        "timestamp": "2026-07-31T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "actions": [
                            {
                                "actionType": "stateRequest",
                                "actionId": "ia-invalid-blocking",
                                "blockingType": "HARD",
                                "actionParameters": [],
                            }
                        ],
                    }
                )
                with patch.object(
                    adapter,
                    "_handle_state_request_instant_action",
                ) as handler:
                    adapter.instant_actions_accept_procedure(invalid)

                handler.assert_not_called()
                self.assertEqual(
                    adapter.state.errors[-1].error_type,
                    ErrorType.INVALID_INSTANT_ACTION,
                )
                self.assertIn(
                    "require blockingType NONE",
                    adapter.state.errors[-1].error_description,
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_standard_instant_actions(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            instant_actions = InstantActions.from_dict(
                {
                    "headerId": 2,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "actions": [
                        {
                            "actionType": "stateRequest",
                            "actionId": "ia-state",
                            "blockingType": "NONE",
                            "actionParameters": [],
                        },
                        {
                            "actionType": "stopCharging",
                            "actionId": "ia-stopcharge",
                            "blockingType": "NONE",
                            "actionParameters": [],
                        },
                        {
                            "actionType": "initPosition",
                            "actionId": "ia-initpos",
                            "blockingType": "NONE",
                            "actionParameters": [
                                {"key": "x", "value": 100.0},
                                {"key": "y", "value": 200.0},
                                {"key": "theta", "value": 0.5},
                                {"key": "mapId", "value": "lab3m"},
                            ],
                        },
                        {
                            "actionType": "startPause",
                            "actionId": "ia-pause",
                            "blockingType": "NONE",
                            "actionParameters": [],
                        },
                        {
                            "actionType": "startCharging",
                            "actionId": "ia-charge",
                            "blockingType": "NONE",
                            "actionParameters": [],
                        },
                    ],
                }
            )
            # startCharging now verifies the robot actually starts charging
            # before finishing. Simulate the dock engaging shortly after UmDock;
            # the delay lets stopCharging resolve as "not charging" first so it
            # does not race the charge flag.
            async def _charge_after_dock() -> None:
                await asyncio.sleep(0.05)
                vehicle._charging = True

            asyncio.create_task(_charge_after_dock())
            adapter.instant_actions_accept_procedure(instant_actions)
            # Wait for all scheduled instant-action coroutines to terminate
            # (startCharging blocks until charging is verified).
            statuses = await _settle_instant_actions(
                adapter,
                "ia-state",
                "ia-stopcharge",
                "ia-initpos",
                "ia-pause",
                "ia-charge",
                timeout=4.0,
            )

            self.assertEqual(
                statuses,
                {
                    "ia-state": ActionStatus.FINISHED,
                    "ia-stopcharge": ActionStatus.FINISHED,
                    "ia-initpos": ActionStatus.FINISHED,
                    "ia-pause": ActionStatus.FINISHED,
                    "ia-charge": ActionStatus.FINISHED,
                },
            )

            self.assertEqual(
                vehicle.localize_calls, [("pose", None, 100.0, 200.0, 0.5)]
            )
            self.assertEqual(adapter._current_map_id, "lab3m")
            self.assertEqual(vehicle.stop_motion_calls, 1)
            self.assertEqual(vehicle.dock_calls, 1)
            self.assertIs(adapter.state.paused, True)
            self.assertTrue(adapter._motion_paused)

            stop_pause = InstantActions.from_dict(
                {
                    "headerId": 3,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "actions": [
                        {
                            "actionType": "stopPause",
                            "actionId": "ia-resume",
                            "blockingType": "NONE",
                            "actionParameters": [],
                        }
                    ],
                }
            )
            adapter.instant_actions_accept_procedure(stop_pause)
            await asyncio.sleep(0.3)

            self.assertIs(adapter.state.paused, False)
            self.assertFalse(adapter._motion_paused)

            state_task.cancel()

        asyncio.run(scenario())

    def test_localize_goal_reanchors_incorrect_last_node(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            adapter._set_last_node("p36", 0)

            instant_actions = InstantActions.from_dict(
                {
                    "headerId": 3,
                    "timestamp": "2026-07-30T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "actions": [
                        {
                            "actionType": "localize",
                            "actionId": "ia-localize-p2",
                            "blockingType": "NONE",
                            "actionParameters": [
                                {"key": "target", "value": "goal"},
                                {"key": "goal", "value": "p2"},
                            ],
                        }
                    ],
                }
            )

            try:
                adapter.instant_actions_accept_procedure(instant_actions)
                for _ in range(100):
                    if not adapter.state.instant_action_states:
                        break
                    await asyncio.sleep(0.01)

                self.assertEqual(
                    vehicle.localize_calls,
                    [("goal", "p2", None, None, None)],
                )
                self.assertEqual(adapter._last_node_id, "p2")
                self.assertEqual(adapter.state.last_node_id, "p2")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_order_start_charging_action_docks(self) -> None:
        """A startCharging action carried on an order node docks via UmDock."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()

            order = Order.from_dict(
                {
                    "headerId": 9,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "order-charge",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "F2_90_S2CH",
                            "sequenceId": 0,
                            "released": True,
                            "actions": [
                                {
                                    "actionType": "startCharging",
                                    "actionId": "F2_90_S2CH-order-0-startCharging",
                                    "blockingType": "SOFT",
                                    "actionParameters": [],
                                }
                            ],
                        }
                    ],
                    "edges": [],
                }
            )
            action = order.nodes[0].actions[0]
            action_state = ActionState(
                action_id=action.action_id,
                action_status=ActionStatus.WAITING,
                action_type="startCharging",
            )

            await adapter._execute_order_action(action, action_state)

            self.assertEqual(vehicle.dock_calls, 1)
            self.assertEqual(action_state.action_status, ActionStatus.FINISHED)

        asyncio.run(scenario())

    def test_dock_node_forwards_configured_approach_params(self) -> None:
        """Global dock.approach_params are forwarded to UmDock at a charge node."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [MotionRule(to="CHARGE_C", mode="dock")]
                adapter.config.dock.approach_params = DockApproachParams(
                    detect_charging_signal=True
                )

                node = Node.from_dict(
                    {
                        "nodeId": "CHARGE_C",
                        "sequenceId": 2,
                        "released": True,
                        "nodePosition": {
                            "x": 100.0,
                            "y": 200.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                        },
                        "actions": [],
                    }
                )

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertEqual(vehicle.dock_calls, 1)
                self.assertEqual(
                    vehicle.last_dock_params, {"detect_charging_signal": True}
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_dock_node_forwards_false_approach_param(self) -> None:
        """False values in dock.approach_params are forwarded, not silently dropped."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [MotionRule(to="CHARGE_C", mode="dock")]
                adapter.config.dock.approach_params = DockApproachParams(
                    detect_charging_signal=False
                )

                node = Node.from_dict(
                    {
                        "nodeId": "CHARGE_C",
                        "sequenceId": 2,
                        "released": True,
                        "nodePosition": {
                            "x": 100.0,
                            "y": 200.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                        },
                        "actions": [],
                    }
                )

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertEqual(vehicle.dock_calls, 1)
                self.assertEqual(
                    vehicle.last_dock_params, {"detect_charging_signal": False}
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_dock_node_no_approach_params_sends_bare_um_dock(self) -> None:
        """With no approach_params configured, UmDock is called bare (empty kwargs)."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [MotionRule(to="CHARGE_C", mode="dock")]
                # No approach_params set — default DockApproachParams() has all None fields.

                node = Node.from_dict(
                    {
                        "nodeId": "CHARGE_C",
                        "sequenceId": 2,
                        "released": True,
                        "nodePosition": {
                            "x": 100.0,
                            "y": 200.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                        },
                        "actions": [],
                    }
                )

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertEqual(vehicle.dock_calls, 1)
                self.assertEqual(vehicle.last_dock_params, {})
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_charge_node_motion_uses_um_dock_not_route_or_goto(self) -> None:
        """Charge node motion is robot docking, not route-file execution."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [MotionRule(to="CHARGE_C", mode="dock")]
                adapter.config.charge.routes = ["LEGACY_ROUTE"]

                node = Node.from_dict(
                    {
                        "nodeId": "CHARGE_C",
                        "sequenceId": 2,
                        "released": True,
                        "nodePosition": {
                            "x": 100.0,
                            "y": 200.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                        },
                        "actions": [],
                    }
                )

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.dock_calls, 1)
                self.assertEqual(vehicle.goto_targets, [])
                self.assertEqual(vehicle.route_calls, [])
                self.assertEqual(adapter.state.last_node_id, "CHARGE_C")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_hana_1_01ch_order_uses_um_dock_not_umgoto(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                self.assertIsNotNone(adapter._dock_segment_rule("1_01CH"))
                node = Node.from_dict(
                    {
                        "nodeId": "1_01CH",
                        "sequenceId": 2,
                        "released": True,
                        "nodePosition": {
                            "x": 10186.0,
                            "y": -2533.0,
                            "theta": 0.0,
                            "mapId": "hana",
                        },
                        "actions": [],
                    }
                )

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.dock_calls, 1)
                self.assertEqual(vehicle.goto_targets, [])
                self.assertEqual(adapter.state.last_node_id, "1_01CH")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_dock_work_node_stops_charging_after_docking(self) -> None:
        """Dock-work nodes dock the robot, then stop charging for transfer work."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.dock.nodes = ["CV_DOCK_C"]
                adapter.config.dock.stop_charging_on_arrival = True
                # Keep the test fast; the repeat gap is covered separately.
                adapter.config.dock.stop_charging_repeat_gap_sec = 0

                node = Node.from_dict(
                    {
                        "nodeId": "CV_DOCK_C",
                        "sequenceId": 2,
                        "released": True,
                        "nodePosition": {
                            "x": 100.0,
                            "y": 200.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                        },
                        "actions": [],
                    }
                )

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.dock_calls, 1)
                # One UmStop is enough here: the fixture leaves charging on the
                # first send, so the repeat is skipped.
                self.assertEqual(vehicle.stop_charge_calls, 1)
                self.assertFalse(vehicle._charging)
                self.assertEqual(adapter.state.last_node_id, "CV_DOCK_C")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_stop_charging_after_dock_work_repeats_um_stop_while_still_charging(self) -> None:
        """JIBOT sometimes ignores a single UmStop, so a stop that did not take is repeated.

        Work around the firmware bug by repeating UmStop up to the configured
        number of times (default 2) with the configured gap between sends
        (default 3.0s). No gap is inserted after the final send.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle

            events: List[Any] = []

            async def stubborn_um_stop(gap: int = -1) -> None:
                # The firmware bug this repeat exists for: UmStop lands but the
                # robot keeps charging, so the next send must still go out.
                events.append(("um_stop", gap))
                vehicle.stop_charge_calls += 1

            async def fake_sleep(delay: float) -> None:
                events.append(("sleep", delay))

            vehicle._charging = True
            vehicle.um_stop = stubborn_um_stop
            with patch.object(asyncio, "sleep", fake_sleep):
                await adapter._stop_charging_after_dock_work("CV_DOCK_C")

            self.assertEqual(vehicle.stop_charge_calls, 2)
            self.assertEqual(
                events,
                [("um_stop", -1), ("sleep", 3.0), ("um_stop", -1)],
            )

        asyncio.run(scenario())

    def test_stop_charging_after_dock_work_skips_repeat_once_charging_cleared(self) -> None:
        """A stop that took must not be followed by a second UmStop.

        Regression (2026-08-20, 1_01CH→p39): the repeat fired unconditionally,
        so a robot that had already left ModeCharge got a second UmStop, and the
        departure move dispatched 1ms later lost the race against it — the robot
        stayed in the charger and the segment reported travelled=0.0mm with no
        error on either side.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle

            events: List[Any] = []
            original_um_stop = vehicle.um_stop

            async def recording_um_stop(gap: int = -1) -> None:
                events.append(("um_stop", gap))
                await original_um_stop(gap)  # fixture clears _charging

            async def fake_sleep(delay: float) -> None:
                events.append(("sleep", delay))

            vehicle._charging = True
            vehicle.um_stop = recording_um_stop
            with patch.object(asyncio, "sleep", fake_sleep):
                await adapter._stop_charging_after_dock_work("CV_DOCK_C")

            self.assertEqual(vehicle.stop_charge_calls, 1)
            self.assertEqual(events, [("um_stop", -1), ("sleep", 3.0)])

        asyncio.run(scenario())

    def test_stop_charging_before_motion_settles_before_returning(self) -> None:
        """Order motion waits out a settle window after the charge stop.

        UmStop and UmSchedulerThis both replace the JIBOT scheduler task and
        there is no ack to wait on, so a move dispatched in the same instant as
        the stop can be overwritten by it. The settle is the only thing keeping
        the two apart.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter.config.dock.stop_charging_repeat_gap_sec = 0
            adapter.config.dock.stop_charging_motion_settle_sec = 0.75
            vehicle._charging = True

            slept: List[float] = []

            async def fake_sleep(delay: float) -> None:
                slept.append(delay)

            with patch.object(asyncio, "sleep", fake_sleep):
                reason = await adapter._ensure_not_charging_before_order_motion(
                    SimpleNamespace(node_id="p39")
                )

            self.assertIsNone(reason)
            self.assertIn(0.75, slept)

        asyncio.run(scenario())

    def test_stop_charging_before_motion_skips_settle_when_not_charging(self) -> None:
        """A robot that was never charging pays no settle cost."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._charging = False
            adapter.config.dock.stop_charging_motion_settle_sec = 0.75

            slept: List[float] = []

            async def fake_sleep(delay: float) -> None:
                slept.append(delay)

            with patch.object(asyncio, "sleep", fake_sleep):
                reason = await adapter._ensure_not_charging_before_order_motion(
                    SimpleNamespace(node_id="p39")
                )

            self.assertIsNone(reason)
            self.assertEqual(slept, [])

        asyncio.run(scenario())

    def test_stop_charging_instant_action_stops_dock_charging(self) -> None:
        """stopCharging on a dock-charging robot sends UmStop and finishes.

        Regression: stopCharging previously only released an in-place hold and
        FAILED for dock (JModeCharge) charging, so the robot kept charging.
        The action must send UmStop (repeated for the firmware bug) and verify
        the robot actually stopped charging before reporting FINISHED.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                # Robot is dock-charging (JModeCharge); no in-place hold held.
                vehicle._charging = True
                adapter.config.dock.stop_charging_repeat_gap_sec = 0

                stop = InstantActions.from_dict(
                    {
                        "headerId": 1,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "actions": [
                            {
                                "actionType": "stopCharging",
                                "actionId": "ia-stop",
                                "blockingType": "NONE",
                                "actionParameters": [],
                            }
                        ],
                    }
                )
                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(stop)
                    await _await_terminal_instant_status(spy, "ia-stop")

                # One UmStop: the fixture stops charging on it, so the repeat
                # (which only exists for a stop that did not take) is skipped.
                self.assertEqual(vehicle.stop_charge_calls, 1)
                self.assertFalse(vehicle._charging)
                finished = [
                    c
                    for c in spy.call_args_list
                    if c.args[0] == "ia-stop"
                    and c.args[1] == ActionStatus.FINISHED
                ]
                self.assertTrue(
                    finished,
                    "stopCharging must FINISH after the robot stops charging",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_stop_charging_instant_action_fails_if_still_charging(self) -> None:
        """stopCharging FAILS when the robot keeps charging past the timeout.

        Issue 2 (applied to stop): after UmStop the action verifies charging
        actually ended within a timeout; if telemetry still reports charging it
        must surface FAILED rather than a false FINISHED.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                vehicle._charging = True
                adapter.config.dock.stop_charging_repeat_gap_sec = 0
                adapter.config.dock.stop_charging_verify_timeout_sec = 0.1

                # UmStop the firmware ignores: charging never clears.
                async def stubborn_um_stop(gap: int = -1) -> None:
                    vehicle.stop_charge_calls += 1

                vehicle.um_stop = stubborn_um_stop

                stop = InstantActions.from_dict(
                    {
                        "headerId": 1,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "actions": [
                            {
                                "actionType": "stopCharging",
                                "actionId": "ia-stop",
                                "blockingType": "NONE",
                                "actionParameters": [],
                            }
                        ],
                    }
                )
                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(stop)
                    await _await_terminal_instant_status(spy, "ia-stop")

                # UmStop must have been attempted before the verify failed.
                self.assertEqual(vehicle.stop_charge_calls, 2)
                self.assertTrue(vehicle._charging)  # still charging
                failed = [
                    c
                    for c in spy.call_args_list
                    if c.args[0] == "ia-stop"
                    and c.args[1] == ActionStatus.FAILED
                ]
                self.assertTrue(
                    failed,
                    "stopCharging must FAIL when the robot is still charging "
                    "after the verify timeout",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_start_charging_instant_action_verifies_charging_started(self) -> None:
        """startCharging FINISHES only after the robot actually starts charging.

        Regression (Issue 2): the dock path previously reported FINISHED right
        after UmDock was sent, without confirming charging began. It must poll
        telemetry and finish with a 'charging started' result.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()
            state_task = await self._start_state(adapter)
            try:
                # Dock path (not in-place): no last node set. The robot starts
                # charging a moment after UmDock (the dock approach settles).
                async def charge_after_dock() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True

                asyncio.create_task(charge_after_dock())

                charge = InstantActions.from_dict(
                    {
                        "headerId": 1,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "actions": [
                            {
                                "actionType": "startCharging",
                                "actionId": "ia-charge",
                                "blockingType": "NONE",
                                "actionParameters": [],
                            }
                        ],
                    }
                )
                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(charge)
                    await _await_terminal_instant_status(spy, "ia-charge")

                self.assertEqual(vehicle.dock_calls, 1)
                finished = [
                    c
                    for c in spy.call_args_list
                    if c.args[0] == "ia-charge"
                    and c.args[1] == ActionStatus.FINISHED
                    and c.kwargs.get("result_description") == "charging started"
                ]
                self.assertTrue(
                    finished,
                    "startCharging must FINISH with 'charging started' once the "
                    "robot is charging",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_start_charging_instant_action_fails_if_not_charging(self) -> None:
        """startCharging FAILS when charging never starts within the timeout.

        Issue 2: a charge request that does not change the robot into the
        charging state must surface an error rather than a false FINISHED.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.dock.start_charging_verify_timeout_sec = 0.1
                # Robot never starts charging after UmDock.

                charge = InstantActions.from_dict(
                    {
                        "headerId": 1,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "actions": [
                            {
                                "actionType": "startCharging",
                                "actionId": "ia-charge",
                                "blockingType": "NONE",
                                "actionParameters": [],
                            }
                        ],
                    }
                )
                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(charge)
                    await _await_terminal_instant_status(spy, "ia-charge")

                self.assertEqual(vehicle.dock_calls, 1)  # UmDock was still sent
                self.assertFalse(vehicle._charging)
                failed = [
                    c
                    for c in spy.call_args_list
                    if c.args[0] == "ia-charge"
                    and c.args[1] == ActionStatus.FAILED
                ]
                self.assertTrue(
                    failed,
                    "startCharging must FAIL when charging does not start within "
                    "the verify timeout",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_jibot_map_dock_node_without_config_uses_goto_not_dock(self) -> None:
        """A node that is only a JIBOT-map "Dock" object must use UmGoto, not UmDock.

        Dock dispatch is config-driven: the raw map category alone must not
        select docking. Otherwise an approach/exit waypoint that the map labels
        "Dock" (e.g. ``*_BEFORE``) gets a bare UmDock when an order merely routes
        through it, which makes the firmware run its in-place dock maneuver and
        brake. Regression for the F2_90_S2CH_BEFORE stuck-at-brake bug.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                # Map labels MAP_DOCK as "Dock", but it is not in charge.nodes /
                # dock.nodes and is not a motion-rule target -> plain goto.
                vehicle._map_nodes = {"MAP_DOCK": (100.0, 200.0, 0.0)}
                vehicle._map_node_categories = {"MAP_DOCK": "Dock"}

                node = Node.from_dict(
                    {
                        "nodeId": "MAP_DOCK",
                        "sequenceId": 2,
                        "released": True,
                        "actions": [],
                    }
                )

                async def finish() -> None:
                    await asyncio.sleep(0.05)
                    vehicle.arrive_at(100.0, 200.0)  # goto completion (arrival)
                    vehicle._charging = True  # dock completion, if old path runs
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.dock_calls, 0)
                self.assertEqual(vehicle.goto_targets, ["MAP_DOCK"])
                self.assertEqual(vehicle.route_calls, [])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_charge_nodes_without_motion_rule_does_not_dock(self) -> None:
        """charge.nodes alone must NOT select UmDock.

        Dock dispatch is driven by mode="dock" motion_rules (single source of
        truth). A node listed only in the legacy charge.nodes, with no matching
        motion rule, is a plain goto target — not a UmDock.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.charge.nodes = ["CFG_ONLY"]
                adapter.config.motion_rules = []  # no dock rule for CFG_ONLY
                vehicle._map_nodes = {"CFG_ONLY": (100.0, 200.0, 0.0)}

                node = Node.from_dict(
                    {
                        "nodeId": "CFG_ONLY",
                        "sequenceId": 2,
                        "released": True,
                        "actions": [],
                    }
                )

                async def finish() -> None:
                    await asyncio.sleep(0.05)
                    vehicle.arrive_at(100.0, 200.0)  # goto completion (arrival)
                    vehicle._charging = True  # dock completion, if old path runs
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.dock_calls, 0)
                self.assertEqual(vehicle.goto_targets, ["CFG_ONLY"])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_dock_work_error_preserves_unrelated_unknown_errors(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                unrelated = Error(
                    error_type=ErrorType.UNKNOWN_ERROR,
                    error_level=ErrorLevel.WARNING,
                    error_references=[ErrorReference("source", "parse")],
                    error_description="Unrelated unknown error",
                )
                old_dock_error = Error(
                    error_type=ErrorType.UNKNOWN_ERROR,
                    error_level=ErrorLevel.WARNING,
                    error_references=[ErrorReference("jibotDockWork", "true")],
                    error_description="JIBOT dock-work charging stop failed",
                )
                adapter.state.errors = [unrelated, old_dock_error]
                node = Node.from_dict(
                    {
                        "nodeId": "CV_DOCK_C",
                        "sequenceId": 2,
                        "released": True,
                        "actions": [],
                    }
                )

                adapter._set_jibot_dock_work_error(node, "boom")

                descriptions = [error.error_description for error in adapter.state.errors]
                self.assertIn("Unrelated unknown error", descriptions)
                self.assertEqual(
                    descriptions.count("JIBOT dock-work charging stop failed"),
                    1,
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_start_charging_none_action_does_not_redock_after_node_clear(self) -> None:
        """NONE startCharging can run after node clear without a second UmDock."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [MotionRule(to="CHARGE_C", mode="dock")]

                order = Order.from_dict(
                    {
                        "headerId": 1,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "orderId": "order-charge",
                        "orderUpdateId": 0,
                        "nodes": [
                            {
                                "nodeId": "CHARGE_C",
                                "sequenceId": 2,
                                "released": True,
                                "nodePosition": {
                                    "x": 100.0,
                                    "y": 200.0,
                                    "theta": 0.0,
                                    "mapId": "lab2m",
                                },
                                "actions": [
                                    {
                                        "actionType": "startCharging",
                                        "actionId": "a-charge",
                                        "blockingType": "NONE",
                                        "actionParameters": [],
                                    }
                                ],
                            }
                        ],
                        "edges": [],
                    }
                )
                adapter.order = order
                adapter.state.node_states = list(order.nodes)
                adapter.state.edge_states = []
                adapter.state.action_states = adapter.build_action_states(order)
                action_state = adapter.state.action_states[0]

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                step = OrderStep("node", order.nodes[0].sequence_id, order.nodes[0])
                ok = await adapter._process_v3_order_step(step)
                self.assertTrue(ok)
                adapter._clear_v3_order_step(step)
                await asyncio.sleep(0.05)

                self.assertEqual(vehicle.dock_calls, 1)
                self.assertEqual(action_state.action_status, ActionStatus.FINISHED)
                self.assertIn(
                    action_state.result_description,
                    {"already docking/charging", "charging in place"},
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_start_charging_action_does_not_redock_when_node_motion_already_docked(self) -> None:
        """startCharging action does not duplicate node-level UmDock."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()
            adapter._docking_started_node_ids.add("CHARGE_C")

            order = Order.from_dict(
                {
                    "headerId": 1,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "order-charge",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "CHARGE_C",
                            "sequenceId": 2,
                            "released": True,
                            "actions": [
                                {
                                    "actionType": "startCharging",
                                    "actionId": "a-charge",
                                    "blockingType": "SOFT",
                                    "actionParameters": [],
                                }
                            ],
                        }
                    ],
                    "edges": [],
                }
            )
            action = order.nodes[0].actions[0]
            action_state = ActionState(
                action_id="a-charge",
                action_status=ActionStatus.WAITING,
                action_type="startCharging",
            )

            await adapter._dock_for_order_action(
                action,
                action_state,
                node_id="CHARGE_C",
            )

            self.assertEqual(vehicle.dock_calls, 0)
            self.assertEqual(action_state.action_status, ActionStatus.FINISHED)
            self.assertIn("already", action_state.result_description)

        asyncio.run(scenario())

    def test_docking_maneuver_surfaces_as_running_dock_action(self) -> None:
        """A docking maneuver appears as a RUNNING 'dock' instant-action state.

        The eq derives workingStateDetail=DOCKING from actionType='dock'.
        Order completion is unaffected because it depends on node/edge states.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                # No docking in progress -> no synthetic dock action.
                await asyncio.sleep(0.08)
                self.assertFalse(
                    any(
                        s.action_type == "dock"
                        for s in adapter.state.instant_action_states
                    )
                )

                # Docking starts (node-motion UmDock tracked via this set).
                adapter._docking_started_node_ids.add("DOCK_A")
                await asyncio.sleep(0.08)
                dock_states = [
                    s
                    for s in adapter.state.instant_action_states
                    if s.action_type == "dock"
                ]
                self.assertEqual(len(dock_states), 1)
                self.assertEqual(dock_states[0].action_status, ActionStatus.RUNNING)

                # Docking completes -> dock action removed.
                adapter._docking_started_node_ids.clear()
                await asyncio.sleep(0.08)
                self.assertFalse(
                    any(
                        s.action_type == "dock"
                        for s in adapter.state.instant_action_states
                    )
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_clear_errors_instant_action_clears_sticky_errors(self) -> None:
        """clearErrors clears sticky errors (e.g. JIBOT_GOTO_REJECTED) on request."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                await asyncio.sleep(0.05)
                adapter.state.errors.append(
                    Error(
                        error_type=ErrorType.JIBOT_GOTO_REJECTED,
                        error_level=ErrorLevel.FATAL,
                        error_references=[],
                        error_description="test",
                    )
                )
                self.assertTrue(
                    any(e.error_type == ErrorType.JIBOT_GOTO_REJECTED for e in adapter.state.errors)
                )

                adapter._handle_clear_errors_instant_action("clear-1")
                await asyncio.sleep(0.05)

                self.assertFalse(
                    any(e.error_type == ErrorType.JIBOT_GOTO_REJECTED for e in adapter.state.errors)
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_jibot_lost_status_surfaces_as_fatal_localization_error(self) -> None:
        """A JIBOT 'lost' status token surfaces as a FATAL JIBOT_LOCALIZATION_LOST error.

        The eq maps this errorType to a LOST sub-state (workingState=ERROR).
        Distinct from JIBOT_CONNECTION_LOST (TCP link). Recomputed each cycle.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                # Healthy status -> no localization-lost error.
                vehicle._status = "Stopped"
                await asyncio.sleep(0.08)
                self.assertFalse(
                    any(
                        e.error_type == ErrorType.JIBOT_LOCALIZATION_LOST
                        for e in adapter.state.errors
                    )
                )

                # Robot reports a lost status token (e.g. "...#lost").
                vehicle._status = "F1_40#lost"
                await asyncio.sleep(0.08)
                lost_errors = [
                    e
                    for e in adapter.state.errors
                    if e.error_type == ErrorType.JIBOT_LOCALIZATION_LOST
                ]
                self.assertEqual(len(lost_errors), 1)
                self.assertEqual(lost_errors[0].error_level, ErrorLevel.FATAL)

                # Status recovers -> error cleared on the next cycle.
                vehicle._status = "Stopped"
                await asyncio.sleep(0.08)
                self.assertFalse(
                    any(
                        e.error_type == ErrorType.JIBOT_LOCALIZATION_LOST
                        for e in adapter.state.errors
                    )
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_amr_state_information_published(self) -> None:
        """Adapter publishes computed workingState/workingStateDetail in AMR_STATE info."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            def amr_state():
                for info in adapter.state.information:
                    if getattr(info, "info_type", None) == "AMR_STATE":
                        return {r.reference_key: r.reference_value for r in info.info_references}
                return {}

            try:
                vehicle._status = "Stopped"
                await asyncio.sleep(0.08)
                self.assertEqual(amr_state().get("workingState"), "IDLE")
                self.assertEqual(amr_state().get("workingStateDetail"), "NONE")

                vehicle._status = "F1_40#lost"
                await asyncio.sleep(0.08)
                self.assertEqual(amr_state().get("workingState"), "ERROR")
                self.assertEqual(amr_state().get("workingStateDetail"), "LOST")

                vehicle._status = "Stopped"
                adapter._docking_started_node_ids.add("DOCK_A")
                await asyncio.sleep(0.08)
                self.assertEqual(amr_state().get("workingStateDetail"), "DOCKING")
                self.assertEqual(amr_state().get("workingState"), "ACTING")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_brake_status_maps_to_blocked_brake_working_state(self) -> None:
        from types import SimpleNamespace

        adapter = self._make_adapter()
        vehicle: FakeVehicle = adapter._vehicle
        vehicle._status = "nrunto F2_90_S2CH_BEFORE#brake"
        adapter.state = SimpleNamespace(
            safety_state=SimpleNamespace(e_stop=EStop.NONE, field_violation=True),
            battery_state=SimpleNamespace(charging=False),
            driving=False,
            paused=False,
            errors=[],
        )

        self.assertTrue(adapter._is_jibot_obstacle_wait())
        self.assertEqual(adapter._derive_amr_working_state(), ("BLOCKED", "BRAKE"))

    def test_active_action_is_reported_without_changing_working_state(self) -> None:
        adapter = self._make_adapter()
        adapter.state = SimpleNamespace(
            safety_state=SimpleNamespace(e_stop=EStop.NONE, field_violation=False),
            battery_state=SimpleNamespace(charging=False),
            driving=False,
            paused=False,
            errors=[],
            information=[],
            action_states=[
                ActionState(
                    action_id="future",
                    action_status=ActionStatus.WAITING,
                    action_type="futureAction",
                ),
                ActionState(
                    action_id="recipe-1",
                    action_status=ActionStatus.RUNNING,
                    action_type="pioElevatorOpen",
                )
            ],
            instant_action_states=[
                ActionState(
                    action_id="extension-1",
                    action_status=ActionStatus.RUNNING,
                    action_type="requestLaser",
                )
            ],
        )
        adapter._active_action_steps["recipe-1"] = "pioWriteOut"

        self.assertEqual(
            adapter._derive_amr_working_state(),
            ("IDLE", "NONE"),
        )
        adapter._refresh_amr_state_information()
        amr_info = next(
            info for info in adapter.state.information if info.info_type == "AMR_STATE"
        )
        refs = {
            ref.reference_key: ref.reference_value
            for ref in amr_info.info_references
        }
        self.assertEqual(refs["activeActionType"], "pioElevatorOpen")
        self.assertEqual(
            json.loads(refs["activeActionTypes"]),
            ["pioElevatorOpen", "requestLaser"],
        )
        self.assertEqual(refs["activeStepActionType"], "pioWriteOut")
        self.assertEqual(
            json.loads(refs["activeStepActionTypes"]),
            ["pioWriteOut"],
        )

    def test_active_order_without_operation_is_idle_not_acting(self) -> None:
        """A bare active order (no loading/unloading/docking, not driving) is IDLE.

        ACTING must always carry a non-NONE workingStateDetail. An order_id that
        is alive but not currently progressing any operation must NOT surface as
        ACTING+NONE; it stays IDLE until a real operation (work/docking) or
        motion begins.
        """

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            def amr_state():
                for info in adapter.state.information:
                    if getattr(info, "info_type", None) == "AMR_STATE":
                        return {r.reference_key: r.reference_value for r in info.info_references}
                return {}

            try:
                vehicle._status = "Stopped"
                adapter.state.order_id = "active-but-idle"
                adapter._work_in_progress = None
                adapter._docking_started_node_ids.clear()
                await asyncio.sleep(0.08)
                self.assertEqual(amr_state().get("workingState"), "IDLE")
                self.assertEqual(amr_state().get("workingStateDetail"), "NONE")
            finally:
                state_task.cancel()

        asyncio.run(scenario())


    def test_publish_loop_does_not_overwrite_last_node_during_active_order(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            # "settled" contract (lastNodeId = order progress); config.toml's
            # deployment default is now "proximity", so set it explicitly here.
            adapter.config.settings.last_node_capture_mode = "settled"
            vehicle._x = 14040.0
            vehicle._y = 3750.0
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = "F1_60"
                adapter.state.last_node_sequence_id = 0

                # Let several publish cycles run.
                await asyncio.sleep(0.2)

                self.assertEqual(adapter.state.last_node_id, "F1_60")
                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_coordinate_less_node_advances_last_node_on_completion(self) -> None:
        import io
        from contextlib import redirect_stdout

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 80,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "coord-less-order",
                    "orderUpdateId": 0,
                    "nodes": [
                        {"nodeId": "NX", "sequenceId": 0, "released": True, "actions": []}
                    ],
                    "edges": [],
                }
            )

            try:
                adapter._handle_v3_order(order)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    await asyncio.wait_for(adapter.order_worker_task, timeout=2.0)
                self.assertEqual(adapter._last_node_id, "NX")
                self.assertEqual(adapter._last_node_sequence_id, 0)
                # Best-effort advance on a coordinate-less node must be logged,
                # not silent.
                self.assertIn("[ORDER NODE WARN]", buf.getvalue())
            finally:
                state_task.cancel()
                if adapter.order_worker_task is not None and not adapter.order_worker_task.done():
                    adapter.order_worker_task.cancel()

        asyncio.run(scenario())

    def test_order_node_goto_rejected_sets_fatal_error_and_holds(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._mode = "Stop"
            vehicle._status = "Stopped"
            vehicle.goto_response = {"#CMD#": "error", "msg": "robot in stop mode"}
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 50,
                    "timestamp": "2026-06-20T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "goto-reject",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 5000.0,
                                "y": 5000.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            await asyncio.wait_for(adapter.order_worker_task, timeout=1.0)

            rejected = [
                e
                for e in adapter.state.errors
                if e.error_type == ErrorType.JIBOT_GOTO_REJECTED
            ]
            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0].error_level, ErrorLevel.FATAL)
            refs = {
                r.reference_key: r.reference_value
                for r in rejected[0].error_references
            }
            self.assertEqual(refs["nodeId"], "N1")
            self.assertEqual(refs["command"], "UmGoto")
            self.assertEqual(refs["jibotMode"], "Stop")
            self.assertIn("robot in stop mode", refs["reason"])
            # Soft hold: the node is NOT cleared.
            self.assertTrue(adapter.state.node_states)
            # Pin that _await_goto_ack calls wait_for_response with accept_errors=True
            # so explicit robot rejections are not mislabelled as "no acknowledgement".
            self.assertIsNotNone(vehicle.last_wait_for_response_kwargs)
            self.assertEqual(vehicle.last_wait_for_response_kwargs["command"], "UmGoto")
            self.assertIs(vehicle.last_wait_for_response_kwargs["accept_errors"], True)

            state_task.cancel()

        asyncio.run(scenario())

    def test_order_node_goto_no_ack_is_not_rejection(self) -> None:
        """A silent UmGoto (None) is acceptance, not a rejection.

        urobot's own command manifest declares UmGoto as ret:none — it is
        fire-and-forget and never emits a UmGoto ack frame, so wait_for_response
        times out to None on every real goto. The robot signals acceptance only
        through streamed telemetry (mode=MRosGoto / status="nrunto <node>"), and
        arrival is verified downstream. Treating the missing ack as a rejection
        was a false-positive FATAL hold; this guards against the regression.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle.goto_response = None  # fire-and-forget: urobot stays silent
            vehicle._mode = "MRosGoto"
            vehicle._status = "nrunto N1"
            state_task = await self._start_state(adapter)
            self._stop_driving_on_arrival(adapter, vehicle)

            order = Order.from_dict(
                {
                    "headerId": 51,
                    "timestamp": "2026-06-20T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "goto-noack",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 5000.0,
                                "y": 5000.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            for _ in range(100):
                if vehicle.goto_targets:
                    break
                await asyncio.sleep(0.01)
            # Robot reaches the node target; arrival (pose inside the reach
            # zone) is what completes the step.
            vehicle.arrive_at(5000.0, 5000.0)
            vehicle._station = "N1"
            vehicle._status = "Stopped"
            await asyncio.wait_for(adapter.order_worker_task, timeout=1.0)

            # The missing UmGoto ack must NOT surface a rejection error.
            self.assertEqual(
                [
                    e
                    for e in adapter.state.errors
                    if e.error_type == ErrorType.JIBOT_GOTO_REJECTED
                ],
                [],
            )
            # The goto was still dispatched and the node completed normally.
            self.assertIn("N1", vehicle.goto_targets)
            self.assertEqual(adapter.state.last_node_id, "N1")

            state_task.cancel()

        asyncio.run(scenario())

    def test_order_node_goto_accepted_clears_stale_reject_and_completes(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            # Pre-seed a stale rejection error from a previous attempt.
            adapter.state.errors.append(
                Error(
                    error_type=ErrorType.JIBOT_GOTO_REJECTED,
                    error_level=ErrorLevel.FATAL,
                    error_references=[],
                    error_description="stale",
                )
            )

            order = Order.from_dict(
                {
                    "headerId": 52,
                    "timestamp": "2026-06-20T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "goto-accept",
                    "orderUpdateId": 0,
                    "nodes": [
                        {
                            "nodeId": "N1",
                            "sequenceId": 0,
                            "released": True,
                            "nodePosition": {
                                "x": 5000.0,
                                "y": 5000.0,
                                "theta": 0.0,
                                "mapId": "lab2m",
                                "allowedDeviationXY": 5.0,
                            },
                            "actions": [],
                        }
                    ],
                    "edges": [],
                }
            )

            adapter._handle_v3_order(order)
            for _ in range(100):
                if vehicle.goto_targets:
                    break
                await asyncio.sleep(0.01)
            # Report arrival via pose reach so the accepted goto completes.
            # Station-only arrival no longer completes a node (pose-reach is
            # required since the order-goto-rejection-diagnostic change), so the
            # pose must enter the node's reach zone.
            vehicle._status = "Stopped"
            vehicle.arrive_at(5000.0, 5000.0)
            await asyncio.wait_for(adapter.order_worker_task, timeout=1.0)

            self.assertEqual(adapter.state.last_node_id, "N1")
            self.assertEqual(
                [
                    e
                    for e in adapter.state.errors
                    if e.error_type == ErrorType.JIBOT_GOTO_REJECTED
                ],
                [],
            )

            state_task.cancel()

        asyncio.run(scenario())

    def test_dispatch_umgoto_requests_error_frames(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            captured: Dict[str, Any] = {}

            async def fake_send_command_and_wait(
                command, gap=-1, timeout=3.0, accept_errors=False, **params
            ):
                captured["command"] = command
                captured["accept_errors"] = accept_errors
                return {"#CMD#": "error", "msg": "denied"}

            vehicle.send_command_and_wait = fake_send_command_and_wait

            response = await adapter._dispatch_jibot_command(
                "UmGoto",
                gap=-1,
                params={"target": "goal", "goal": "N1"},
                timeout=3.0,
            )
            reason = adapter._jibot_command_rejection_reason("UmGoto", response)

            self.assertTrue(captured["accept_errors"])
            self.assertIsNotNone(reason)
            self.assertIn("denied", reason)

        asyncio.run(scenario())

    def test_loading_instant_action_enters_busy_and_stays_running(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("loading", "work-1")
                )
                self.assertEqual(adapter._work_in_progress, "loading")
                self.assertEqual(adapter._work_action_id, "work-1")
                matches = [
                    s for s in adapter.state.instant_action_states
                    if s.action_id == "work-1"
                ]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].action_status, ActionStatus.RUNNING)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_unloading_instant_action_enters_busy_and_stays_running(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("unloading", "work-2")
                )
                self.assertEqual(adapter._work_in_progress, "unloading")
                matches = [
                    s for s in adapter.state.instant_action_states
                    if s.action_id == "work-2"
                ]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].action_status, ActionStatus.RUNNING)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_order_rejected_while_busy(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._work_in_progress = "loading"
                adapter._work_action_id = "work-1"

                adapter._handle_v3_order(_one_node_order("order-A"))

                # Order was NOT accepted (order_id unchanged) ...
                self.assertNotEqual(adapter.state.order_id, "order-A")
                # ... and a busy rejection error was recorded.
                self.assertTrue(
                    any(
                        "busy" in (e.error_description or "").lower()
                        for e in adapter.state.errors
                    )
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_stop_loading_clears_busy_and_removes_work_action(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("loading", "work-1")
                )
                self.assertEqual(adapter._work_in_progress, "loading")

                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("stopLoading", "stop-1")
                )

                self.assertIsNone(adapter._work_in_progress)
                self.assertIsNone(adapter._work_action_id)
                # Both end FINISHED and stay reportable until an order replaces
                # them — the FMS has to be able to read that the work ended.
                self.assertEqual(
                    _instant_action_statuses(adapter),
                    {
                        "work-1": ActionStatus.FINISHED,
                        "stop-1": ActionStatus.FINISHED,
                    },
                )

                # Orders are accepted again.
                adapter._handle_v3_order(_one_node_order("order-A"))
                self.assertEqual(adapter.state.order_id, "order-A")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_stop_unloading_type_mismatch_fails_and_keeps_busy(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("loading", "work-1")
                )
                # stopUnloading does not match active "loading" work.
                with patch.object(
                    adapter, "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(
                        _work_instant_actions("stopUnloading", "stop-x")
                    )
                self.assertEqual(adapter._work_in_progress, "loading")  # still BUSY
                spy.assert_any_call(
                    "stop-x", ActionStatus.FAILED,
                    result_description="no active unloading work to stop",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_stop_loading_with_no_active_work_does_not_enter_busy(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                with patch.object(
                    adapter, "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(
                        _work_instant_actions("stopLoading", "stop-1")
                    )
                self.assertIsNone(adapter._work_in_progress)
                spy.assert_any_call(
                    "stop-1", ActionStatus.FAILED,
                    result_description="no active loading work to stop",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_loading_rejected_while_already_busy(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("loading", "work-1")
                )
                # Second loading while already BUSY must not replace the active work.
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("loading", "work-2")
                )
                self.assertEqual(adapter._work_action_id, "work-1")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_loading_rejected_while_order_active(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                # Simulate an in-progress order: node_states non-empty.
                adapter._handle_v3_order(_one_node_order("order-A"))
                self.assertEqual(adapter.state.order_id, "order-A")
                self.assertFalse(adapter._is_v3_order_finished())

                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("loading", "work-1")
                )
                self.assertIsNone(adapter._work_in_progress)  # not BUSY
            finally:
                state_task.cancel()

        asyncio.run(scenario())


    def test_start_charging_blocked_while_busy(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("loading", "work-1")
                )
                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(
                        _work_instant_actions("startCharging", "charge-1")
                    )
                # UmDock must NOT have been issued; BUSY persists.
                self.assertEqual(vehicle.dock_calls, 0)
                self.assertEqual(adapter._work_in_progress, "loading")
                spy.assert_any_call(
                    "charge-1",
                    ActionStatus.FAILED,
                    result_description="busy with loading work; motion blocked",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_jibot_command_motion_blocked_while_busy(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("loading", "work-1")
                )
                goto = InstantActions.from_dict(
                    {
                        "headerId": 1,
                        "timestamp": "2026-06-10T00:00:00.000Z",
                        "version": "3.0.0",
                        "manufacturer": "jibot",
                        "serialNumber": "HN-SH6-TR-001",
                        "actions": [
                            {
                                "actionType": "jibotCommand",
                                "actionId": "goto-1",
                                "blockingType": "NONE",
                                "actionParameters": [
                                    {"key": "command", "value": "UmGoto"}
                                ],
                            }
                        ],
                    }
                )
                with patch.object(
                    adapter,
                    "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(goto)
                # No motion was issued; BUSY persists.
                self.assertEqual(vehicle.goto_targets, [])
                self.assertEqual(vehicle.dock_calls, 0)
                self.assertEqual(adapter._work_in_progress, "loading")
                spy.assert_any_call(
                    "goto-1",
                    ActionStatus.FAILED,
                    result_description="busy with loading work; motion blocked",
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_unloading_then_stop_unloading_happy_path(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("unloading", "work-u")
                )
                self.assertEqual(adapter._work_in_progress, "unloading")

                adapter.instant_actions_accept_procedure(
                    _work_instant_actions("stopUnloading", "stop-u")
                )
                self.assertIsNone(adapter._work_in_progress)
                self.assertIsNone(adapter._work_action_id)

                # Both end FINISHED and stay reportable until an order replaces
                # them — the FMS has to be able to read that the work ended.
                self.assertEqual(
                    _instant_action_statuses(adapter),
                    {
                        "work-u": ActionStatus.FINISHED,
                        "stop-u": ActionStatus.FINISHED,
                    },
                )

                # Orders accepted again after BUSY cleared.
                adapter._handle_v3_order(_one_node_order("order-A"))
                self.assertEqual(adapter.state.order_id, "order-A")
            finally:
                state_task.cancel()

        asyncio.run(scenario())


    def test_resolve_sound_track_maps_working_state(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake

        def resolve(ws, detail):
            with mock.patch.object(
                adapter, "_derive_amr_working_state", return_value=(ws, detail)
            ):
                return adapter._resolve_sound_track()

        # workingStateDetail file wins over the workingState file
        fake.tracks = {"loading.mp3", "acting.mp3", "driving.mp3"}
        self.assertEqual(resolve("ACTING", "LOADING"), "loading.mp3")
        # detail file absent -> fall back to the workingState file
        fake.tracks = {"acting.mp3"}
        self.assertEqual(resolve("ACTING", "LOADING"), "acting.mp3")
        # DOCKING detail wins over DRIVING state when docking.mp3 exists
        fake.tracks = {"docking.mp3", "driving.mp3"}
        self.assertEqual(resolve("DRIVING", "DOCKING"), "docking.mp3")
        # docking.mp3 absent -> driving.mp3 (state fallback)
        fake.tracks = {"driving.mp3"}
        self.assertEqual(resolve("DRIVING", "DOCKING"), "driving.mp3")
        # detail NONE -> state file
        fake.tracks = {"driving.mp3"}
        self.assertEqual(resolve("DRIVING", "NONE"), "driving.mp3")
        # any state with a matching file plays (generalized convention)
        fake.tracks = {"charging.mp3"}
        self.assertEqual(resolve("CHARGING", "NONE"), "charging.mp3")
        # no matching file -> silence
        fake.tracks = {"charging.mp3"}
        self.assertIsNone(resolve("DRIVING", "NONE"))

    def test_resolve_sound_track_prefers_recipe_step_then_parent_action(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake
        adapter.state = SimpleNamespace(
            action_states=[],
            instant_action_states=[
                ActionState(
                    action_id="recipe-1",
                    action_status=ActionStatus.RUNNING,
                    action_type="pioElevatorOpen",
                )
            ],
        )
        adapter._active_action_steps["recipe-1"] = "pioWriteOut"
        fake.tracks = {
            "action-pioWriteOut.mp3",
            "action-pioElevatorOpen.mp3",
            "idle.mp3",
        }

        self.assertEqual(
            adapter._resolve_sound_track(),
            "action-pioWriteOut.mp3",
        )

        adapter._active_action_steps.clear()
        self.assertEqual(
            adapter._resolve_sound_track(),
            "action-pioElevatorOpen.mp3",
        )

        fake.tracks.add("fault.mp3")
        with mock.patch.object(
            adapter,
            "_derive_amr_working_state",
            return_value=("ERROR", "FAULT"),
        ):
            self.assertEqual(adapter._resolve_sound_track(), "fault.mp3")

    def test_resolve_sound_track_uses_only_brake_file_for_brake(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake

        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("BLOCKED", "BRAKE")
        ):
            fake.tracks = {"brake.mp3", "excuseme.mp3", "blocked.mp3"}
            self.assertEqual(adapter._resolve_sound_track(), "brake.mp3")

            fake.tracks = {"excuseme.mp3", "blocked.mp3"}
            self.assertIsNone(adapter._resolve_sound_track())

    def test_update_sound_loops_brake_until_state_clears(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        fake.tracks = {"brake.mp3"}
        adapter._sound = fake

        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("BLOCKED", "BRAKE")
        ):
            adapter._update_sound_for_working_state()

        self.assertEqual(fake.played, [("brake.mp3", 0.0)])
        self.assertEqual(fake.looped, [("brake.mp3", True)])

        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("IDLE", "NONE")
        ):
            adapter._update_sound_for_working_state()

        self.assertEqual(fake.stops, 1)

    def test_resolve_sound_track_prefers_slowdown_or_watch_file_when_present(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake

        fake.tracks = {"slowdown.mp3", "watch.mp3", "driving.mp3"}
        adapter._vehicle._status = "nrunto F1_10#slowdown"
        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
        ):
            self.assertEqual(adapter._resolve_sound_track(), "slowdown.mp3")

        adapter._vehicle._status = "nrunto F1_10#watch"
        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
        ):
            self.assertEqual(adapter._resolve_sound_track(), "watch.mp3")

    def test_resolve_sound_track_prefers_jibot_error_code_wav(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        fake.tracks = {"ERROR0200.wav", "driving.mp3"}
        adapter._sound = fake
        adapter._vehicle._robot_safety = {"system_error_code": "200"}
        adapter._vehicle._robot_safety_last_update = time.monotonic()

        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
        ):
            self.assertEqual(adapter._resolve_sound_track(), "ERROR0200.wav")

    def test_resolve_sound_track_falls_back_when_error_wav_missing_or_zero(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        fake.tracks = {"driving.mp3"}
        adapter._sound = fake

        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
        ):
            adapter._vehicle._robot_safety = {"system_error_code": "200"}
            adapter._vehicle._robot_safety_last_update = time.monotonic()
            self.assertEqual(adapter._resolve_sound_track(), "driving.mp3")

            adapter._vehicle._robot_safety = {"system_error_code": "0"}
            adapter._vehicle._robot_safety_last_update = time.monotonic()
            self.assertEqual(adapter._resolve_sound_track(), "driving.mp3")

    def test_resolve_sound_track_none_when_disabled(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        fake.tracks = {"driving.mp3"}  # would match if enabled
        adapter._sound = fake
        adapter.config.sound_settings.enabled = False
        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
        ):
            self.assertIsNone(adapter._resolve_sound_track())

    def test_update_sound_plays_and_stops(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake
        with mock.patch.object(adapter, "_resolve_sound_track", return_value="travel.mp3"):
            adapter._update_sound_for_working_state()
        self.assertEqual(fake.played, [("travel.mp3", 0.0)])
        with mock.patch.object(adapter, "_resolve_sound_track", return_value=None):
            adapter._update_sound_for_working_state()
        self.assertEqual(fake.stops, 1)

    def test_update_sound_uses_common_replay_gap(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake
        adapter.config.sound_settings.state_replay_gap_sec = 1.25
        adapter.config.sound_settings.state_replay_gap_overrides = {}
        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
        ):
            fake.tracks = {"driving.mp3"}
            adapter._update_sound_for_working_state()
        self.assertEqual(fake.played, [("driving.mp3", 1.25)])

    def test_update_sound_detail_replay_gap_override_wins(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake
        adapter.config.sound_settings.state_replay_gap_sec = 1.25
        adapter.config.sound_settings.state_replay_gap_overrides = {
            "acting": 0.5,
            "loading": 2.0,
        }
        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("ACTING", "LOADING")
        ):
            fake.tracks = {"loading.mp3", "acting.mp3"}
            adapter._update_sound_for_working_state()
        self.assertEqual(fake.played, [("loading.mp3", 2.0)])

    def test_update_sound_yields_to_active_test_override(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake
        adapter._sound_test_until = time.monotonic() + 60
        with mock.patch.object(adapter, "_resolve_sound_track", return_value="travel.mp3"):
            adapter._update_sound_for_working_state()
        self.assertEqual(fake.played, [])  # auto suppressed during test

    # ------------------------------------------------------------------
    # Task 4: setSoundVolume / testSound / stopSound instant actions
    # ------------------------------------------------------------------

    def _instant(self, action_type, params=None):
        """Build a single Action object for instant-action handler tests."""
        return make_instant_action(action_type, action_id=f"a-{action_type}",
                                   params=params or {}).actions[0]

    def test_set_sound_volume_action(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            with tempfile.TemporaryDirectory() as td:
                adapter._config_path = Path(td) / "config.toml"
                adapter._config_path.write_text(
                    "[sound_settings]\nstartup_volume = 70\n",
                    encoding="utf-8",
                )
                adapter._sound = _FakeSound()
                adapter._sound_started = True  # prevent startup volume call from polluting volumes
                state_task = await self._start_state(adapter)
                try:
                    action_state = ActionState(
                        action_id="a-setSoundVolume", action_status=ActionStatus.WAITING,
                        action_type="setSoundVolume",
                    )
                    adapter.state.instant_action_states.append(action_state)
                    adapter._handle_set_sound_volume_instant_action(
                        self._instant("setSoundVolume", {"volume": 42, "mute": "false"})
                    )
                    # Blocking SoundPlayer calls run on the sound worker thread
                    # while an adapter loop is up, so wait for them to land.
                    adapter._drain_sound_worker()
                    self.assertEqual(adapter._sound.volumes, [42])
                    self.assertEqual(adapter._sound.mutes, [False])
                    # action_state ref is valid even after _clear_terminal removes it from the list
                    self.assertEqual(action_state.action_status, ActionStatus.FINISHED)
                finally:
                    state_task.cancel()

        asyncio.run(scenario())

    def test_set_sound_volume_action_persists_startup_volume(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.toml"
            config_path.write_text(
                "[sound_settings]\n"
                "startup_volume = 70                   # OS sink volume\n",
                encoding="utf-8",
            )
            adapter = self._make_adapter()
            adapter._config_path = config_path
            adapter._sound = _FakeSound()

            action_state = ActionState(
                action_id="a-setSoundVolume",
                action_status=ActionStatus.WAITING,
                action_type="setSoundVolume",
            )
            adapter.state = mock.Mock()
            adapter.state.instant_action_states = [action_state]

            adapter._handle_set_sound_volume_instant_action(
                self._instant("setSoundVolume", {"volume": 42})
            )

            self.assertEqual(adapter._sound.volumes, [42])
            self.assertEqual(adapter.config.sound_settings.startup_volume, 42)
            saved = config_path.read_text(encoding="utf-8")
            self.assertIn("startup_volume = 42", saved)
            self.assertIn("# OS sink volume", saved)
            self.assertEqual(action_state.action_status, ActionStatus.FINISHED)

    def test_test_sound_action_sets_override_and_plays(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._sound = _FakeSound()
            state_task = await self._start_state(adapter)
            try:
                adapter.state.instant_action_states.append(
                    ActionState(action_id="a-testSound", action_status=ActionStatus.WAITING,
                                action_type="testSound")
                )
                before = time.monotonic()
                adapter._handle_test_sound_instant_action(
                    self._instant("testSound", {"sound": "work"})
                )
                adapter._drain_sound_worker()
                self.assertEqual(adapter._sound.played, [(adapter.config.sound_settings.work_music, 0.0)])
                self.assertGreater(adapter._sound_test_until, before)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_test_sound_default_plays_driving_mp3(self) -> None:
        # The WebUI "Play" button sends testSound with NO params. The default
        # must be an mp3 that actually ships in sounds/ (driving.mp3), not the
        # deprecated travel_music (travel.mp3, which no longer exists) -> silence.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._sound = _FakeSound()
            state_task = await self._start_state(adapter)
            try:
                adapter.state.instant_action_states.append(
                    ActionState(action_id="a-testSound", action_status=ActionStatus.WAITING,
                                action_type="testSound")
                )
                adapter._handle_test_sound_instant_action(self._instant("testSound"))
                adapter._drain_sound_worker()
                self.assertEqual(adapter._sound.played, [("driving.mp3", 0.0)])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_stop_sound_action_clears_override(self) -> None:
        # No state loop needed: handler is sync and _update_instant_action_status
        # gracefully handles state=None.
        adapter = self._make_adapter()
        adapter._sound = _FakeSound()
        adapter._sound_test_until = time.monotonic() + 60
        adapter._handle_stop_sound_instant_action(self._instant("stopSound"))
        self.assertEqual(adapter._sound.stops, 1)
        self.assertEqual(adapter._sound_test_until, 0.0)

    def test_sound_playback_does_not_block_the_adapter_loop(self) -> None:
        """A slow player must not stall state publishing / order execution.

        SoundPlayer.play() terminates mplayer and waits player_wait_timeout_sec,
        then joins the replay thread for the same budget -- about two seconds of
        blocking subprocess work per track change. Run inline it froze the whole
        asyncio adapter, which is why playback is handed to a worker thread.
        """

        class _SlowSound(_FakeSound):
            def play(self, track, loop=True, replay_gap_sec=0.0, repeat_count=0):
                time.sleep(0.5)
                super().play(
                    track,
                    loop=loop,
                    replay_gap_sec=replay_gap_sec,
                    repeat_count=repeat_count,
                )

        async def scenario() -> None:
            adapter = self._make_adapter()
            fake = _SlowSound()
            fake.tracks = {"driving.mp3"}
            adapter._sound = fake
            adapter._loop = asyncio.get_running_loop()

            with mock.patch.object(
                adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
            ):
                started = time.monotonic()
                adapter._update_sound_for_working_state()
                elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.2)
            adapter._drain_sound_worker()
            self.assertEqual(fake.played, [("driving.mp3", 0.0)])

        asyncio.run(scenario())

    def test_auto_sound_does_not_requeue_an_unchanged_track(self) -> None:
        """The auto driver runs every publish cycle; only changes reach the player.

        SoundPlayer.play() dedupes too, but only once the worker picks the job
        up -- without the adapter-side guard the queue would grow one entry per
        cycle for as long as a state sound loops.
        """
        adapter = self._make_adapter()
        fake = _FakeSound()
        fake.tracks = {"driving.mp3"}
        adapter._sound = fake

        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
        ):
            for _ in range(3):
                adapter._update_sound_for_working_state()

        self.assertEqual(fake.played, [("driving.mp3", 0.0)])

    def test_repeat_count_defaults_to_endless_for_every_track(self) -> None:
        adapter = self._make_adapter()
        self.assertEqual(adapter._resolve_sound_repeat_count("driving.mp3"), 0)
        self.assertEqual(adapter._resolve_sound_repeat_count("action-clamp.mp3"), 0)

    def test_repeat_count_override_is_keyed_on_the_file_name(self) -> None:
        """Action sounds need their own key: the gap overrides refuse to match one."""
        adapter = self._make_adapter()
        adapter.config.sound_settings.state_repeat_count = 3
        adapter.config.sound_settings.repeat_count_overrides = {"action-clamp": 1}

        self.assertEqual(adapter._resolve_sound_repeat_count("action-clamp.mp3"), 1)
        self.assertEqual(adapter._resolve_sound_repeat_count("driving.mp3"), 3)

    def test_unusable_repeat_count_override_falls_back_to_the_default(self) -> None:
        adapter = self._make_adapter()
        adapter.config.sound_settings.state_repeat_count = 2
        adapter.config.sound_settings.repeat_count_overrides = {"driving": "twice"}
        self.assertEqual(adapter._resolve_sound_repeat_count("driving.mp3"), 2)

    def test_auto_sound_passes_the_resolved_repeat_count_to_the_player(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        fake.tracks = {"action-clamp.mp3"}
        adapter._sound = fake
        adapter.config.sound_settings.repeat_count_overrides = {"action-clamp": 1}

        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("ACTING", "NONE")
        ), mock.patch.object(adapter, "_active_action_types", return_value=["clamp"]):
            adapter._update_sound_for_working_state()

        self.assertEqual(fake.repeats, [("action-clamp.mp3", 1)])

    def test_stop_sound_action_reaches_the_player_when_already_silent(self) -> None:
        """stopSound is an operator command, so the dedupe must not swallow it."""
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake

        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("IDLE", "NONE")
        ):
            adapter._update_sound_for_working_state()  # resolves to silence
        adapter._handle_stop_sound_instant_action(self._instant("stopSound"))

        self.assertGreaterEqual(fake.stops, 1)

    def _make_adapter_with_state(self):
        """Return adapter with a minimal state stub for instant-action handler tests."""
        adapter = self._make_adapter()
        adapter.state = type(
            "StateStub", (),
            {"instant_action_states": [], "action_states": [], "errors": [], "information": []}
        )()
        return adapter

    def test_upload_sound_writes_file(self) -> None:
        import base64, os, tempfile
        adapter = self._make_adapter_with_state()
        adapter._sound = _FakeSound()
        with tempfile.TemporaryDirectory() as d:
            adapter._sound.sound_dir = d
            action_state = ActionState(action_id="a-uploadSound", action_status=ActionStatus.WAITING,
                                       action_type="uploadSound")
            adapter.state.instant_action_states.append(action_state)
            data = base64.b64encode(b"ID3-fake-mp3-bytes").decode()
            adapter._handle_upload_sound_instant_action(
                self._instant("uploadSound", {"fileName": "travel.mp3", "data": data})
            )
            with open(os.path.join(d, "travel.mp3"), "rb") as f:
                self.assertEqual(f.read(), b"ID3-fake-mp3-bytes")
            self.assertEqual(action_state.action_status, ActionStatus.FINISHED)

    def test_upload_sound_rejects_non_mp3(self) -> None:
        import base64, tempfile
        adapter = self._make_adapter_with_state()
        adapter._sound = _FakeSound()
        with tempfile.TemporaryDirectory() as d:
            adapter._sound.sound_dir = d
            action_state = ActionState(action_id="a-uploadSound", action_status=ActionStatus.WAITING,
                                       action_type="uploadSound")
            adapter.state.instant_action_states.append(action_state)
            adapter._handle_upload_sound_instant_action(
                self._instant("uploadSound", {"fileName": "evil.sh", "data": base64.b64encode(b"x").decode()})
            )
            self.assertEqual(action_state.action_status, ActionStatus.FAILED)

    def test_upload_sound_strips_path_traversal(self) -> None:
        import base64, os, tempfile
        adapter = self._make_adapter_with_state()
        adapter._sound = _FakeSound()
        with tempfile.TemporaryDirectory() as d:
            adapter._sound.sound_dir = d
            adapter.state.instant_action_states.append(
                ActionState(action_id="a-uploadSound", action_status=ActionStatus.WAITING,
                            action_type="uploadSound")
            )
            adapter._handle_upload_sound_instant_action(
                self._instant("uploadSound",
                              {"fileName": "../../etc/x.mp3", "data": base64.b64encode(b"x").decode()})
            )
            self.assertTrue(os.path.exists(os.path.join(d, "x.mp3")))

    def test_upload_sound_rejects_bad_base64(self) -> None:
        import tempfile
        adapter = self._make_adapter_with_state()
        adapter._sound = _FakeSound()
        with tempfile.TemporaryDirectory() as d:
            adapter._sound.sound_dir = d
            action_state = ActionState(action_id="a-uploadSound", action_status=ActionStatus.WAITING,
                                       action_type="uploadSound")
            adapter.state.instant_action_states.append(action_state)
            adapter._handle_upload_sound_instant_action(
                self._instant("uploadSound", {"fileName": "t.mp3", "data": "!!!not-base64!!!"})
            )
            self.assertEqual(action_state.action_status, ActionStatus.FAILED)

    def test_factsheet_advertises_sound_actions(self) -> None:
        adapter = self._make_adapter()
        fs = adapter._build_factsheet()
        types = {a["actionType"] for a in fs["protocolFeatures"]["agvActions"]}
        self.assertIn("setSoundVolume", types)
        self.assertIn("testSound", types)
        self.assertIn("stopSound", types)
        self.assertIn("uploadSound", types)

    def test_factsheet_advertises_manual_actions(self) -> None:
        adapter = self._make_adapter()
        factsheet = adapter._build_factsheet()
        types = {a["actionType"] for a in factsheet["protocolFeatures"]["agvActions"]}
        for t in ("manualDrive", "manualMove", "manualStop", "enableMotor"):
            self.assertIn(t, types)

    def test_set_jibot_dock_fail_error_is_fatal_and_deduped(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                from types import SimpleNamespace
                node = SimpleNamespace(node_id="F2_90_S2CH", sequence_id=4)

                adapter._set_jibot_dock_fail_error(node, "not charging within 30.0s")
                adapter._set_jibot_dock_fail_error(node, "still not charging")

                dock_errors = [
                    e for e in adapter.state.errors
                    if e.error_type == ErrorType.JIBOT_DOCK_FAILED
                ]
                self.assertEqual(len(dock_errors), 1)  # de-duped
                self.assertEqual(dock_errors[0].error_level, ErrorLevel.FATAL)
                refs = {r.reference_key: r.reference_value
                        for r in dock_errors[0].error_references}
                self.assertEqual(refs["nodeId"], "F2_90_S2CH")
                self.assertEqual(refs["command"], "UmDock")
                self.assertEqual(refs["reason"], "still not charging")
                self.assertIn("[dock].fail_timeout_sec", dock_errors[0].error_description)
                self.assertIn("motion_rules", dock_errors[0].error_description)
            finally:
                state_task.cancel()

        asyncio.run(scenario())


    def test_dock_segment_rule_directional_lookup(self) -> None:
        adapter = self._make_adapter()
        adapter.config.motion_rules = [
            MotionRule(to="F2_90_S2CH", mode="dock", from_node="F2_90_S2CH_BEFORE")
        ]
        adapter._last_node_id = "F2_90_S2CH_BEFORE"          # arriving from approach
        self.assertIsNotNone(adapter._dock_segment_rule("F2_90_S2CH"))
        adapter._last_node_id = "SOMEWHERE_ELSE"             # wrong direction
        self.assertIsNone(adapter._dock_segment_rule("F2_90_S2CH"))
        self.assertIsNone(adapter._dock_segment_rule("F1_60"))  # not a dock target

    def test_pose_in_reach_zone_square_and_circle(self) -> None:
        adapter = self._make_adapter()
        adapter.config.settings.reach_zone_shape = "square"
        self.assertTrue(adapter._pose_in_reach_zone(105, 95, 100, 100, 10))
        self.assertFalse(adapter._pose_in_reach_zone(120, 100, 100, 100, 10))
        adapter.config.settings.reach_zone_shape = "circle"
        self.assertTrue(adapter._pose_in_reach_zone(100, 109, 100, 100, 10))
        self.assertFalse(adapter._pose_in_reach_zone(108, 108, 100, 100, 10))

    def test_charging_disable_status_counts_as_charging(self) -> None:
        adapter = self._make_adapter()
        vehicle: FakeVehicle = adapter._vehicle
        vehicle._charging = False
        vehicle._status = "charging#disable"

        self.assertTrue(adapter._is_vehicle_charging())

    def test_wait_until_dock_charge_or_timeout(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            await self._start_state(adapter)
            from types import SimpleNamespace
            node = SimpleNamespace(node_id="F2_90_S2CH", sequence_id=2)

            adapter.config.motion_rules = [
                MotionRule(to="F2_90_S2CH", mode="dock", fail_timeout_sec=0.2)
            ]
            # never charges -> timeout -> False
            self.assertFalse(await adapter._wait_until_dock_charge_or_timeout(node))

            # charges -> True
            vehicle._charging = True
            adapter.config.motion_rules[0].fail_timeout_sec = 1.0
            self.assertTrue(await adapter._wait_until_dock_charge_or_timeout(node))

        asyncio.run(scenario())

    def test_dock_timeout_does_not_run_while_the_robot_waits_on_an_obstacle(self) -> None:
        """The 2026-08-19 11:02 failure: UmDock to 1_01CH spent 52 of 82 polled
        samples in "nrunto 1_01CH#brake" and was failed at the 200s mark.

        A brake is JIBOT stopped waiting for something in its path to clear —
        the same condition _wait_until_node_position_reached and
        _wait_until_move_settled both exclude from their stall judgment. The
        dock wait is the one place that charged it as failure time, so an
        obstacle on the charger approach reads as a broken charger.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            await self._start_state(adapter)
            node = SimpleNamespace(node_id="1_01CH", sequence_id=2)
            adapter.config.motion_rules = [
                MotionRule(to="1_01CH", mode="dock", fail_timeout_sec=0.3)
            ]

            vehicle._status = "nrunto 1_01CH#brake"
            self.assertTrue(adapter._is_jibot_obstacle_wait())

            task = asyncio.create_task(
                adapter._wait_until_dock_charge_or_timeout(node)
            )
            await asyncio.sleep(0.9)          # 3x the fail timeout, all braked
            self.assertFalse(
                task.done(),
                "dock was failed while the robot was waiting out an obstacle",
            )

            vehicle._status = "nrunto 1_01CH"  # obstacle cleared
            vehicle._charging = True
            self.assertTrue(await asyncio.wait_for(task, timeout=2.0))

        asyncio.run(scenario())

    def test_dock_fails_when_the_obstacle_never_clears(self) -> None:
        """Not counting brake time must not turn the dock into an infinite hang.

        The timeout exists so a failed seat rejects the step instead of sitting
        in DOCKING forever. A path that is permanently blocked is still a
        failure — it just is not "the charger is broken" — so the same budget
        bounds one continuous brake.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            await self._start_state(adapter)
            node = SimpleNamespace(node_id="1_01CH", sequence_id=2)
            adapter.config.motion_rules = [
                MotionRule(to="1_01CH", mode="dock", fail_timeout_sec=0.3)
            ]
            adapter.config.dock.obstacle_timeout_sec = 0.3

            vehicle._status = "nrunto 1_01CH#brake"   # never clears
            self.assertFalse(
                await asyncio.wait_for(
                    adapter._wait_until_dock_charge_or_timeout(node),
                    timeout=5.0,
                )
            )

        asyncio.run(scenario())

    # ---------------------------------------------------------------------------
    # approach-then-dock integration tests (Task 5)
    # ---------------------------------------------------------------------------

    def _approach_node(self, node_id="F2_90_S2CH", seq=2):
        from protocol.vda5050_3_0.messages import Node
        return Node.from_dict({
            "nodeId": node_id, "sequenceId": seq, "released": True,
            "nodePosition": {"x": 100.0, "y": 200.0, "theta": 0.0, "mapId": "lab2m"},
            "actions": [],
        })

    def _move_node(self, to="B", x=300.0, y=400.0, seq=3):
        from protocol.vda5050_3_0.messages import Node
        return Node.from_dict({
            "nodeId": to, "sequenceId": seq, "released": True,
            "nodePosition": {"x": x, "y": y, "theta": 0.0, "mapId": "lab2m"},
            "actions": [],
        })

    def test_move_motion_rule_matches_segment(self) -> None:
        adapter = self._make_adapter()
        adapter.config.motion_rules = [
            MotionRule(to="B", mode="move", from_node="A", distance=2500)
        ]
        adapter._last_node_id = "A"
        self.assertIsNotNone(adapter._move_motion_rule("B"))      # from + to match
        adapter._last_node_id = "X"
        self.assertIsNone(adapter._move_motion_rule("B"))         # from mismatch
        adapter._last_node_id = "A"
        self.assertIsNone(adapter._move_motion_rule("C"))         # to mismatch

    def test_move_segment_dispatches_move_and_completes_on_arrival(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="B", mode="move", from_node="A",
                               distance=2500, speed=200,
                               obs_avoid_dist=1200, side_avoid_dist=80,
                               flag=2, io=3, use_io=True, note=4)
                ]
                adapter.config.settings.node_position_poll_interval_sec = 0.01
                # Without this the settle wait burns the full 10s default before
                # judging; the fixture below never reaches that branch, but a
                # regression must fail fast rather than stall the suite.
                adapter.config.settings.move_start_timeout_sec = 0.05
                adapter._last_node_id = "A"            # came from segment start
                node = self._move_node(to="B", x=300.0, y=400.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(300.0, -2100.0)      # 2500mm short of the to-node

                original_move_distance = vehicle.move_distance

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    # The move must actually travel: a relative move that never
                    # left its origin is a failure now, not an arrival.
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(300.0, 400.0)    # full commanded travel, in zone

                vehicle.move_distance = moving_move_distance

                ok = await asyncio.wait_for(
                    adapter._process_v3_node_step(
                        OrderStep("node", node.sequence_id, node)
                    ),
                    timeout=5.0,
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.goto_targets, [])         # no goto
                self.assertEqual(vehicle.dock_calls, 0)            # no dock
                self.assertEqual(len(vehicle.move_distance_calls), 1)
                dist, spd, kwargs = vehicle.move_distance_calls[0]
                self.assertEqual(dist, 2500.0)
                self.assertEqual(spd, 200.0)
                self.assertEqual(kwargs["obs_avoid_dist"], 1200)
                self.assertEqual(kwargs["side_avoid_dist"], 80)
                self.assertEqual(kwargs["flag"], 2)
                self.assertEqual(kwargs["io"], 3)
                self.assertTrue(kwargs["use_io"])
                self.assertEqual(kwargs["note"], 4)
                self.assertEqual(adapter.state.last_node_id, "B")  # FINISHED on arrival
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_stops_charging_before_departure(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="F2_90_S2CHB", mode="move",
                               from_node="F2_90_S2CH", distance=-1200, speed=150)
                ]
                adapter.config.dock.stop_charging_repeat_gap_sec = 0
                adapter.config.dock.stop_charging_motion_settle_sec = 0
                adapter.config.settings.node_position_poll_interval_sec = 0.01
                # As above: the fixture settles on full travel, but pin the
                # start timeout small so a regression fails instead of stalling.
                adapter.config.settings.move_start_timeout_sec = 0.05
                adapter._last_node_id = "F2_90_S2CH"
                vehicle._charging = True
                vehicle._status = "charging"
                vehicle.arrive_at(15782.0, 3388.0)   # in the charger, 1200mm short
                node = self._move_node(to="F2_90_S2CHB", x=15782.0, y=4588.0)

                events: List[str] = []
                original_um_stop = vehicle.um_stop
                original_move_distance = vehicle.move_distance

                async def recording_um_stop(gap: int = -1) -> None:
                    events.append("stopCharging")
                    await original_um_stop(gap)

                async def recording_move_distance(distance, speed, **kwargs) -> None:
                    events.append("move")
                    await original_move_distance(distance, speed, **kwargs)
                    # Back out of the charger for the commanded 1200mm; a move
                    # that never travels no longer counts as an arrival.
                    vehicle.arrive_at(15782.0, 4588.0)

                vehicle.um_stop = recording_um_stop
                vehicle.move_distance = recording_move_distance

                ok = await asyncio.wait_for(
                    adapter._process_v3_node_step(
                        OrderStep("node", node.sequence_id, node)
                    ),
                    timeout=5.0,
                )

                self.assertTrue(ok)
                # Exactly one stop, then the move. A second UmStop here is what
                # swallowed the departure move on the real robot (2026-08-20).
                self.assertEqual(events, ["stopCharging", "move"])
                self.assertFalse(vehicle._charging)
                self.assertEqual(len(vehicle.move_distance_calls), 1)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_does_not_dispatch_if_stop_charging_fails(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="F2_90_S2CHB", mode="move",
                               from_node="F2_90_S2CH", distance=-1200, speed=150)
                ]
                adapter.config.dock.stop_charging_repeat_gap_sec = 0
                adapter.config.dock.stop_charging_verify_timeout_sec = 0.1
                adapter.config.dock.charging_stop_poll_interval_sec = 0.01
                adapter._last_node_id = "F2_90_S2CH"
                vehicle._charging = True
                vehicle._status = "charging"
                vehicle.arrive_at(15782.0, 4588.0)
                node = self._move_node(to="F2_90_S2CHB", x=15782.0, y=4588.0)

                async def stubborn_um_stop(gap: int = -1) -> None:
                    vehicle.stop_charge_calls += 1

                vehicle.um_stop = stubborn_um_stop

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertEqual(vehicle.stop_charge_calls, 2)
                self.assertEqual(vehicle.move_distance_calls, [])
                self.assertTrue(any(e.error_type == ErrorType.JIBOT_GOTO_REJECTED
                                    for e in adapter.state.errors))
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_missing_distance_rejects(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="B", mode="move", from_node="A")   # no distance
                ]
                adapter._last_node_id = "A"
                node = self._move_node(to="B", x=300.0, y=400.0)

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertEqual(vehicle.move_distance_calls, [])   # nothing dispatched
                self.assertTrue(any(e.error_type == ErrorType.JIBOT_GOTO_REJECTED
                                    for e in adapter.state.errors))
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_short_of_node_falls_back_to_goto(self) -> None:
        """The 2026-08-07 HN-SH6-TR-002 failure: order 20260807-2-1 ran its
        commanded 2000mm from the charger and stopped 765mm short of p39, then
        hung — p40, p37 and p38 were never attempted."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0      # -> 200.0 radius
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance
                original_goto_xyz = vehicle.goto_xyz

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(12681.0, -2473.0)   # full travel, still short

                async def arriving_goto_xyz(x, y, z, strict: bool = False) -> None:
                    await original_goto_xyz(x, y, z, strict=strict)
                    vehicle.arrive_at(x, y)

                vehicle.move_distance = moving_move_distance
                vehicle.goto_xyz = arriving_goto_xyz

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(len(vehicle.move_distance_calls), 1)
                self.assertEqual(vehicle.goto_targets, [(13446.0, -2505.0, 0.0)])
                self.assertEqual(vehicle.dock_calls, 0)
                self.assertEqual(adapter.state.last_node_id, "p39")
                self.assertIsNone(adapter._active_move_segment)
                # Finishing short is normal flow, so the settle wait must not
                # have reported the node unreached on the way through.
                self.assertEqual(
                    [e for e in adapter.state.errors
                     if getattr(e, "error_type", None) == ErrorType.JIBOT_NODE_UNREACHED],
                    [],
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_fallback_goto_sets_active_goto_node_during_wait(self) -> None:
        """_active_goto_node gates _wait_until_node_position_reached's stall
        retry/FATAL-escalation path (goto_recoverable = _active_goto_node is
        node). Without it a stalled fallback goto gets no retry coverage and
        hangs the order again — the exact failure this task fixes."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance
                original_goto_xyz = vehicle.goto_xyz
                original_wait = adapter._wait_until_node_position_reached
                captured: Dict[str, Any] = {}

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(12681.0, -2473.0)

                async def arriving_goto_xyz(x, y, z, strict: bool = False) -> None:
                    await original_goto_xyz(x, y, z, strict=strict)
                    vehicle.arrive_at(x, y)

                async def capturing_wait(node_arg, target, poll_sec=None):
                    # The invariant under test: _active_goto_node must already
                    # be `node` at the moment the wait (and its retry logic)
                    # starts, not just set-then-forgotten before dispatch.
                    captured["active_goto_node"] = adapter._active_goto_node
                    await original_wait(node_arg, target, poll_sec)

                vehicle.move_distance = moving_move_distance
                vehicle.goto_xyz = arriving_goto_xyz
                adapter._wait_until_node_position_reached = capturing_wait

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertIs(captured.get("active_goto_node"), node)
                self.assertIsNone(adapter._active_goto_node)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_active_segment_carries_resume_state_while_in_flight(self) -> None:
        """Task 6 resumes a paused move from _active_move_segment's node,
        start_pose, distance, speed and kwargs. Pin the shape (captured while
        the move is actually in flight, not after) so a rename regresses
        loudly instead of silently breaking stopPause resume."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="B", mode="move", from_node="A",
                               distance=2500, speed=200,
                               obs_avoid_dist=1200, side_avoid_dist=80,
                               flag=2, io=3, use_io=True, note=4)
                ]
                adapter._last_node_id = "A"
                vehicle._status = "Stopped"
                vehicle.arrive_at(-2200.0, 400.0)
                node = self._move_node(to="B", x=300.0, y=400.0)

                original_move_distance = vehicle.move_distance
                captured: Dict[str, Any] = {}

                async def capturing_move_distance(distance, speed, **kwargs) -> None:
                    seg = adapter._active_move_segment
                    captured["node"] = seg.node
                    captured["start_pose"] = seg.start_pose
                    captured["distance"] = seg.distance
                    captured["speed"] = seg.speed
                    captured["kwargs"] = seg.kwargs
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(300.0, 400.0)   # full commanded travel, in zone

                vehicle.move_distance = capturing_move_distance

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertIs(captured["node"], node)
                self.assertEqual(captured["start_pose"], (-2200.0, 400.0))
                self.assertEqual(captured["distance"], 2500.0)
                self.assertEqual(captured["speed"], 200.0)
                self.assertEqual(captured["kwargs"]["obs_avoid_dist"], 1200)
                self.assertEqual(captured["kwargs"]["side_avoid_dist"], 80)
                self.assertEqual(captured["kwargs"]["flag"], 2)
                self.assertEqual(captured["kwargs"]["io"], 3)
                self.assertTrue(captured["kwargs"]["use_io"])
                self.assertEqual(captured["kwargs"]["note"], 4)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_fallback_retry_on_dock_node_does_not_dock(self) -> None:
        """A mode="move" rule's `to` may be a charger/dock-work node. When the
        fallback goto stalls and is retried by _wait_until_node_position_reached
        (_resend_node_goto), the retry must stay on the plain-goto path —
        _send_node_motion's unguarded _is_dock_work_node branch would fire a
        bare UmDock instead of driving to the node."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0
                s.node_unreached_delay_sec = 0.02
                s.node_goto_retry_delay_sec = 0.02
                adapter.config.dock.nodes = ["p39"]   # p39 is a dock-work node
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance
                original_goto_xyz = vehicle.goto_xyz
                goto_calls = {"n": 0}

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(12681.0, -2473.0)   # full travel, still short

                async def stalling_then_arriving_goto_xyz(
                    x, y, z, strict: bool = False
                ) -> None:
                    await original_goto_xyz(x, y, z, strict=strict)
                    goto_calls["n"] += 1
                    if goto_calls["n"] >= 2:
                        # The retry (2nd goto) is the one that actually arrives.
                        vehicle.arrive_at(x, y)

                vehicle.move_distance = moving_move_distance
                vehicle.goto_xyz = stalling_then_arriving_goto_xyz

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertGreaterEqual(goto_calls["n"], 2)   # a retry did happen
                self.assertEqual(vehicle.dock_calls, 0)       # never UmDock
                self.assertEqual(adapter.state.last_node_id, "p39")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_inside_zone_sends_no_goto(self) -> None:
        """The production success path: the relative move runs its full
        commanded distance and lands inside the to-node reach zone, so no
        fallback goto is needed."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.settings.node_position_poll_interval_sec = 0.01
                adapter.config.motion_rules = [
                    MotionRule(to="B", mode="move", from_node="A", distance=2500)
                ]
                adapter._last_node_id = "A"
                vehicle._status = "Stopped"
                vehicle.arrive_at(-2200.0, 400.0)   # 2500mm short of B
                node = self._move_node(to="B", x=300.0, y=400.0)

                original_move_distance = vehicle.move_distance

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(300.0, 400.0)   # full commanded travel, in zone

                vehicle.move_distance = moving_move_distance

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(len(vehicle.move_distance_calls), 1)
                self.assertEqual(vehicle.goto_targets, [])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_fails_when_no_pose_before_dispatch(self) -> None:
        """Distance-based completion needs an origin. Never dispatch a relative
        move that cannot be monitored — the segment often starts in a charger."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.settings.node_position_poll_interval_sec = 0.01
                adapter.config.settings.move_start_timeout_sec = 0.05
                adapter.config.motion_rules = [
                    MotionRule(to="B", mode="move", from_node="A", distance=2500)
                ]
                adapter._last_node_id = "A"
                vehicle._x = None
                vehicle._y = None
                node = self._move_node(to="B", x=300.0, y=400.0)

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertEqual(vehicle.move_distance_calls, [])
                self.assertEqual(vehicle.goto_targets, [])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_fallback_goto_rejection_fails_the_step(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                vehicle.goto_response = {"#CMD#": "UmGoto", "result": "error",
                                         "msg": "path blocked"}
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(12681.0, -2473.0)

                vehicle.move_distance = moving_move_distance

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertIsNone(adapter._active_move_segment)
                self.assertIsNone(adapter._active_goto_node)
                # A goto must actually have been attempted and rejected — an
                # implementation that failed before ever reaching the fallback
                # (e.g. bailed out after the short move for some other reason)
                # must not pass this test.
                self.assertEqual(vehicle.goto_targets, [(13446.0, -2505.0, 0.0)])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_without_travel_fails_and_sends_no_goto(self) -> None:
        """No travel means no goto fallback.

        A move that never started (motor not enabled, command dropped, stall at
        ~0mm) leaves the robot fully inside the charger bay the rule exists to
        back out of. A goto from there is precisely the route the design says
        the planner cannot solve — issuing one would put the adapter back to
        driving into a constrained segment — so the node step fails instead.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.move_start_timeout_sec = 0.05
                s.move_stall_timeout_sec = 0.05
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)   # in the charger, never moves
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                # A regression sends the fallback goto and then waits forever for
                # an arrival that never comes, so bound the step instead of
                # hanging the suite on it.
                try:
                    ok = await asyncio.wait_for(
                        adapter._process_v3_node_step(
                            OrderStep("node", node.sequence_id, node)
                        ),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    self.fail(
                        "the node step never returned: the no-travel case fell "
                        f"through to a goto ({vehicle.goto_targets}) and is now "
                        "waiting for an arrival that cannot happen"
                    )

                self.assertFalse(ok)
                self.assertEqual(vehicle.goto_targets, [])          # no goto at all
                self.assertEqual(vehicle.dock_calls, 0)
                self.assertEqual(len(vehicle.move_distance_calls), 1)
                self.assertIsNone(adapter._active_move_segment)
                self.assertIsNone(adapter._active_goto_node)
                reasons = [
                    ref.reference_value
                    for e in adapter.state.errors
                    if e.error_type == ErrorType.JIBOT_GOTO_REJECTED
                    for ref in e.error_references
                    if ref.reference_key == "reason"
                ]
                self.assertEqual(len(reasons), 1)
                self.assertIn("0.0mm", reasons[0])                  # travelled
                self.assertIn("50.0mm", reasons[0])                 # the threshold
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_just_past_the_travel_threshold_falls_back(self) -> None:
        """The other side of the no-travel rule: 60mm clears
        move_started_min_travel_mm (50mm), so the robot HAS demonstrably left
        its origin and the goto fallback proceeds exactly as before."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.move_start_timeout_sec = 0.05
                s.move_stall_timeout_sec = 0.05
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance
                original_goto_xyz = vehicle.goto_xyz

                async def barely_moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(10710.0, -2522.0)   # 60mm > the 50mm threshold

                async def arriving_goto_xyz(x, y, z, strict: bool = False) -> None:
                    await original_goto_xyz(x, y, z, strict=strict)
                    vehicle.arrive_at(x, y)

                vehicle.move_distance = barely_moving_move_distance
                vehicle.goto_xyz = arriving_goto_xyz

                ok = await asyncio.wait_for(
                    adapter._process_v3_node_step(
                        OrderStep("node", node.sequence_id, node)
                    ),
                    timeout=5.0,
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.goto_targets, [(13446.0, -2505.0, 0.0)])
                self.assertEqual(adapter.state.last_node_id, "p39")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    # ---------------------------------------------------------------------------
    # resume a paused move segment (Task 6)
    # ---------------------------------------------------------------------------

    def test_remaining_move_distance_keeps_the_original_sign(self) -> None:
        adapter = self._make_adapter()
        adapter._vehicle.arrive_at(11850.0, -2522.0)      # 1200 of a 2000mm move
        segment = SimpleNamespace(start_pose=(10650.0, -2522.0), distance=-2000.0)
        self.assertAlmostEqual(adapter._remaining_move_distance(segment), -800.0, places=3)

    def test_remaining_move_distance_is_zero_once_covered(self) -> None:
        adapter = self._make_adapter()
        adapter._vehicle.arrive_at(12681.0, -2473.0)      # travelled 2031 of 2000
        segment = SimpleNamespace(start_pose=(10650.0, -2522.0), distance=-2000.0)
        self.assertEqual(adapter._remaining_move_distance(segment), 0.0)

    def test_remaining_move_distance_is_zero_without_a_pose(self) -> None:
        """Re-issuing the full distance blind would overshoot; returning 0 lets
        the settle wait fall back to a goto instead."""
        adapter = self._make_adapter()
        adapter._vehicle._x = None
        adapter._vehicle._y = None
        segment = SimpleNamespace(start_pose=(10650.0, -2522.0), distance=-2000.0)
        self.assertEqual(adapter._remaining_move_distance(segment), 0.0)

    def test_stop_pause_reissues_the_remaining_move(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()
            adapter.state = SimpleNamespace(paused=True, action_states=[])
            adapter._update_instant_action_status = lambda *a, **k: None
            adapter._motion_paused = True
            vehicle.arrive_at(11850.0, -2522.0)           # 1200mm covered
            adapter._active_move_segment = SimpleNamespace(
                node=self._move_node(to="p39"),
                start_pose=(10650.0, -2522.0),
                distance=-2000.0,
                speed=150.0,
                kwargs={"obs_avoid_dist": 1000, "side_avoid_dist": 50,
                        "flag": 1, "io": 1, "use_io": False, "note": 1},
            )

            adapter._handle_stop_pause_instant_action("a1")
            await asyncio.sleep(0.1)

            self.assertEqual(len(vehicle.move_distance_calls), 1)
            dist, spd, kwargs = vehicle.move_distance_calls[0]
            self.assertAlmostEqual(dist, -800.0, places=3)
            self.assertEqual(spd, 150.0)
            self.assertEqual(kwargs["obs_avoid_dist"], 1000)
            self.assertEqual(vehicle.goto_targets, [])    # not converted to a goto
            self.assertFalse(adapter._motion_paused)

        asyncio.run(scenario())

    def test_stop_pause_reissues_nothing_once_the_move_is_covered(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()
            adapter.state = SimpleNamespace(paused=True, action_states=[])
            adapter._update_instant_action_status = lambda *a, **k: None
            adapter._motion_paused = True
            vehicle.arrive_at(12681.0, -2473.0)           # already past 2000mm
            adapter._active_move_segment = SimpleNamespace(
                node=self._move_node(to="p39"),
                start_pose=(10650.0, -2522.0),
                distance=-2000.0,
                speed=150.0,
                kwargs={},
            )

            adapter._handle_stop_pause_instant_action("a1")
            await asyncio.sleep(0.1)

            self.assertEqual(vehicle.move_distance_calls, [])
            self.assertEqual(vehicle.goto_targets, [])
            self.assertFalse(adapter._motion_paused)

        asyncio.run(scenario())

    def test_stop_pause_resumes_a_fallback_goto_on_a_dock_node_without_docking(self) -> None:
        """Mirrors test_move_segment_fallback_retry_on_dock_node_does_not_dock,
        but for the stopPause resume path: a stopPause landing while a
        move-segment fallback goto is in flight toward a `to` node listed in
        [dock].nodes must re-issue a goto via _send_node_goto, not fall through
        _send_node_motion's non-directional dock branch and fire a bare
        UmDock."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()
            adapter.state = SimpleNamespace(paused=True, action_states=[])
            adapter._update_instant_action_status = lambda *a, **k: None
            adapter._motion_paused = True
            adapter.config.dock.nodes = ["p39"]        # p39 is a dock-work node
            vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
            node = self._move_node(to="p39", x=13446.0, y=-2505.0)
            # Fallback-goto phase: no active move segment, only the goto node.
            adapter._active_move_segment = None
            adapter._active_goto_node = node

            adapter._handle_stop_pause_instant_action("a1")
            await asyncio.sleep(0.1)

            self.assertEqual(vehicle.goto_targets, [(13446.0, -2505.0, 0.0)])
            self.assertEqual(vehicle.dock_calls, 0)
            self.assertFalse(adapter._motion_paused)

        asyncio.run(scenario())

    def test_start_pause_during_a_move_issues_no_goto(self) -> None:
        """Without the pause guard the stall branch fires and the fallback goto
        goes out while the order is paused."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.move_stall_timeout_sec = 0.05
                s.move_start_timeout_sec = 0.05
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance

                async def pausing_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(11850.0, -2522.0)   # partway, then paused
                    adapter._motion_paused = True

                vehicle.move_distance = pausing_move_distance

                step = asyncio.create_task(adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                ))
                await asyncio.sleep(0.3)                  # past both timeouts

                self.assertFalse(step.done())
                self.assertEqual(vehicle.goto_targets, [])
                step.cancel()
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_settle_settings_have_documented_defaults(self) -> None:
        adapter = self._make_adapter()
        settings = adapter.config.settings
        self.assertEqual(settings.move_start_timeout_sec, 10.0)
        self.assertEqual(settings.move_started_min_travel_mm, 50.0)
        self.assertEqual(settings.move_complete_travel_ratio, 0.9)
        self.assertEqual(settings.move_stall_timeout_sec, 5.0)

    def test_approach_dock_already_charging_completes_without_moving(self) -> None:
        """Already docked + charging at the charger: no drive-out, no UmDock."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="F2_90_S2CH", mode="dock", approach="F2_90_S2CH_BEFORE")
                ]
                vehicle._map_nodes = {
                    "F2_90_S2CH": (500.0, 500.0, 0.0),
                    "F2_90_S2CH_BEFORE": (100.0, 200.0, 0.0),
                }
                vehicle.arrive_at(500.0, 500.0)   # already at the charger
                vehicle._charging = True           # already charging
                node = self._approach_node()

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.goto_targets, [])  # did not drive out to _BEFORE
                self.assertEqual(vehicle.dock_calls, 0)     # already charging -> no UmDock
                self.assertEqual(adapter.state.last_node_id, "F2_90_S2CH")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_approach_dock_at_charger_not_charging_docks_in_place(self) -> None:
        """At the charger but not charging: UmDock in place, no drive-out to _BEFORE."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="F2_90_S2CH", mode="dock", approach="F2_90_S2CH_BEFORE")
                ]
                vehicle._map_nodes = {
                    "F2_90_S2CH": (500.0, 500.0, 0.0),
                    "F2_90_S2CH_BEFORE": (100.0, 200.0, 0.0),
                }
                vehicle.arrive_at(500.0, 500.0)   # already at the charger
                vehicle._charging = False          # but not charging yet
                node = self._approach_node()

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.goto_targets, [])  # no drive-out to _BEFORE
                self.assertEqual(vehicle.dock_calls, 1)     # docked in place
                self.assertEqual(adapter.state.last_node_id, "F2_90_S2CH")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_dock_segment_docks_from_approach_without_goto(self) -> None:
        """Arriving from the rule's `from` (approach) node UmDocks in place — NO
        approach goto is issued (ACS already routed the robot to the approach)."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="F2_90_S2CH", mode="dock",
                               from_node="F2_90_S2CH_BEFORE")
                ]
                adapter._last_node_id = "F2_90_S2CH_BEFORE"  # arrived from approach
                node = self._approach_node()

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.goto_targets, [])  # no approach goto
                self.assertEqual(vehicle.dock_calls, 1)     # UmDock in place
                self.assertEqual(adapter.state.last_node_id, "F2_90_S2CH")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_dock_segment_skips_dock_in_reverse_direction(self) -> None:
        """The reverse/unspecified direction (not arriving from `from`) is a plain
        goto, never a UmDock. Regression for the departure-from-charger bug where
        a `*_BEFORE` route node was UmDock'd instead of driven through."""

        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="F2_90_S2CH", mode="dock",
                               from_node="F2_90_S2CH_BEFORE")
                ]
                adapter._last_node_id = "SOMEWHERE_ELSE"  # NOT from the approach
                node = self._approach_node()              # nodePosition (100, 200)

                async def arrive() -> None:
                    await asyncio.sleep(0.05)
                    vehicle.arrive_at(100.0, 200.0)
                    vehicle._charging = True  # would complete a dock, if one fired
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(arrive())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.dock_calls, 0)                 # no UmDock
                self.assertEqual(vehicle.goto_targets, ["F2_90_S2CH"])  # plain goto
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_approach_dock_fails_when_never_charging(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.motion_rules = [
                    MotionRule(to="F2_90_S2CH", mode="dock",
                               approach="F2_90_S2CH_BEFORE", fail_timeout_sec=0.2)
                ]
                vehicle._map_nodes = {"F2_90_S2CH_BEFORE": (100.0, 200.0, 0.0)}
                vehicle.arrive_at(100.0, 200.0)
                node = self._approach_node()

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertEqual(vehicle.dock_calls, 1)
                self.assertNotIn("F2_90_S2CH", adapter._docking_started_node_ids)
                self.assertTrue(any(e.error_type == ErrorType.JIBOT_DOCK_FAILED
                                    for e in adapter.state.errors))
            finally:
                state_task.cancel()

        asyncio.run(scenario())


    def test_settings_defaults_capture_mode_and_threshold(self) -> None:
        from dataclasses import fields
        defaults = {f.name: f.default for f in fields(Settings)}
        self.assertEqual(defaults["last_node_capture_mode"], "settled")
        # NOTE: threshold default intentionally NOT changed by this feature (see
        # spec: radius is decoupled; deployed config.toml overrides it anyway).
        self.assertEqual(defaults["idle_last_node_reach_xy"], 500.0)

    def test_capture_mode_accessor_normalizes_and_validates(self) -> None:
        adapter = self._make_adapter()
        adapter.config.settings.last_node_capture_mode = "PROXIMITY"
        self.assertEqual(adapter._last_node_capture_mode(), "proximity")
        adapter.config.settings.last_node_capture_mode = "  SETTLED  "
        self.assertEqual(adapter._last_node_capture_mode(), "settled")
        adapter.config.settings.last_node_capture_mode = "disabled"
        self.assertEqual(adapter._last_node_capture_mode(), "disabled")

    def test_capture_mode_accessor_falls_back_to_settled_once(self) -> None:
        adapter = self._make_adapter()
        adapter.config.settings.last_node_capture_mode = "bogus"
        self.assertFalse(adapter._last_node_capture_mode_warned)
        self.assertEqual(adapter._last_node_capture_mode(), "settled")
        self.assertTrue(adapter._last_node_capture_mode_warned)
        # Second call still returns settled, does not re-warn (idempotent flag).
        self.assertEqual(adapter._last_node_capture_mode(), "settled")

    def test_set_last_node_writes_private_fields_and_state_mirror(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._set_last_node("F1_70", 6)
                self.assertEqual(adapter._last_node_id, "F1_70")
                self.assertEqual(adapter._last_node_sequence_id, 6)
                self.assertEqual(adapter.state.last_node_id, "F1_70")
                self.assertEqual(adapter.state.last_node_sequence_id, 6)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_set_last_node_without_state_sets_private_fields_only(self) -> None:
        adapter = self._make_adapter()
        self.assertIsNone(adapter.state)
        adapter._set_last_node("F1_70", 6)
        self.assertEqual(adapter._last_node_id, "F1_70")
        self.assertEqual(adapter._last_node_sequence_id, 6)

    def _bug2_order(self) -> "Order":
        # Charger order: robot physically at F2_90_S2CH, _station mis-reports
        # F2_80_S2IC (an order node at seq 4) ~2674 mm away.
        return Order.from_dict({
            "headerId": 70,
            "timestamp": "2026-06-23T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "orderId": "bug2-order",
            "orderUpdateId": 1,
            "nodes": [
                {"nodeId": "F2_80_S2IC", "sequenceId": 4, "released": True, "actions": []},
                {"nodeId": "F1_70", "sequenceId": 6, "released": True, "actions": []},
            ],
            "edges": [
                {"edgeId": "e_F2_80_S2IC_F1_70", "sequenceId": 5, "released": True,
                 "startNodeId": "F2_80_S2IC", "endNodeId": "F1_70", "actions": []},
            ],
        })

    def _setup_bug2_adapter(self, adapter: "Adapter") -> None:
        v = adapter._vehicle
        v._station = "F2_80_S2IC"          # mis-reported station (an order node)
        v._x = 14038.0                      # robot is at the charger F2_90_S2CH
        v._y = 3752.0
        v._map_nodes = {
            "F2_80_S2IC": (14159.0, 3615.0, 0.0),   # ~2674 mm from the charger pose
            "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            "F1_70": (12180.0, 3760.0, 0.0),
        }

    def test_bootstrap_proximity_ignores_station_when_pose_disagrees(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            self._setup_bug2_adapter(adapter)
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter.config.settings.last_node_capture_mode = "proximity"
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = ""
                adapter.state.last_node_sequence_id = 0
                adapter._accept_v3_order_for_queue(self._bug2_order())
                # Proximity mode is pose-based; accept-time station bootstrap
                # must not drag lastNodeId to a station that the pose disagrees with.
                self.assertNotEqual(adapter._last_node_id, "F2_80_S2IC")
                self.assertEqual(adapter._last_node_id, "")
                self.assertEqual(adapter._last_node_sequence_id, 0)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_bootstrap_settled_rejects_station_when_pose_disagrees(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            self._setup_bug2_adapter(adapter)
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = ""
                adapter.state.last_node_sequence_id = 0
                adapter._accept_v3_order_for_queue(self._bug2_order())
                # Bug #2 fixed: station bootstrap rejected (pose ~2674 mm away),
                # lastNodeId NOT advanced to the unreached node.
                self.assertNotEqual(adapter._last_node_id, "F2_80_S2IC")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_bootstrap_settled_accepts_station_when_pose_agrees(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            self._setup_bug2_adapter(adapter)
            # Move the robot onto the station node so the pose agrees.
            adapter._vehicle._x = 14159.0
            adapter._vehicle._y = 3615.0
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter._accept_v3_order_for_queue(self._bug2_order())
                # Legit resume still works: pose within reach => station seeded.
                self.assertEqual(adapter._last_node_id, "F2_80_S2IC")
                self.assertEqual(adapter._last_node_sequence_id, 4)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_bootstrap_disabled_runs_neither_bootstrap(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            self._setup_bug2_adapter(adapter)
            # Put the robot right on the station so BOTH station- and pose-bootstrap
            # WOULD seed under settled/proximity; disabled must run neither.
            adapter._vehicle._x = 14159.0
            adapter._vehicle._y = 3615.0
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter.config.settings.last_node_capture_mode = "disabled"
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = ""
                adapter.state.last_node_sequence_id = 0
                adapter._accept_v3_order_for_queue(self._bug2_order())
                self.assertEqual(adapter._last_node_id, "")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_send_node_goto_skips_dock_and_charge_branches(self) -> None:
        """A move rule may target a charger (config.py:385 documents exactly
        that), and _is_dock_motion_node is non-directional, so the fallback
        must not reach _send_node_motion's dock / charge-in-place branches."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter.config.dock.nodes = ["CH"]          # makes CH a dock-work node
            adapter._last_node_id = "CH"                # arms should_charge_in_place
            vehicle._map_path_points["CH"] = (100.0, 200.0, 0.0)
            node = self._move_node(to="CH", x=100.0, y=200.0)

            reason = await adapter._send_node_goto(node)

            self.assertIsNone(reason)
            self.assertEqual(vehicle.goto_targets, [(100.0, 200.0, 0.0)])
            self.assertEqual(vehicle.dock_calls, 0)

        asyncio.run(scenario())

    def test_send_node_motion_still_docks_dock_work_nodes(self) -> None:
        """Guard the extraction: existing callers keep the dock branch."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter.config.dock.nodes = ["CH"]
            adapter._last_node_id = "SOMEWHERE_ELSE"    # disarm charge-in-place
            node = self._move_node(to="CH", x=100.0, y=200.0)

            await adapter._send_node_motion(node)

            self.assertEqual(vehicle.dock_calls, 1)
            self.assertEqual(vehicle.goto_targets, [])

        asyncio.run(scenario())

    # ---------------------------------------------------------------------------
    # order action failures must be visible, and start-node actions must run
    # ---------------------------------------------------------------------------

    def _node_with_actions(self, node_id, sequence_id, actions):
        from protocol.vda5050_3_0.messages import Node
        return Node.from_dict({
            "nodeId": node_id, "sequenceId": sequence_id, "released": True,
            "nodePosition": {"x": 100.0, "y": 200.0, "theta": 0.0, "mapId": "lab2m"},
            "actions": [
                {
                    "actionId": action_id, "actionType": action_type,
                    "blockingType": "HARD", "actionParameters": [],
                }
                for action_id, action_type in actions
            ],
        })

    def _register_action_states(self, adapter, nodes):
        class _Req:
            pass
        _Req.nodes = list(nodes)
        _Req.edges = []
        adapter.state.action_states = adapter.build_action_states(_Req)

    def test_failed_order_action_publishes_an_error(self) -> None:
        """A FAILED order action must also raise a VDA5050 error.

        On robot 62 unclamp failed 10/10 with the EZI drive ignoring the move,
        yet only the actionState went FAILED - no error - so the order ran on to
        ORDER COMPLETE with the clamp still shut.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
                adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
                adapter.config.ezi_config.unclamp_position = -16000
                # A drive that ACKs the move but never moves: 62's symptom.
                adapter.set_ezi_motor(FakeClampMotor(servo_on=True, actual_position=0))

                node = self._node_with_actions("1_01CH", 4, [("unclamp-1", "unclamp")])
                self._register_action_states(adapter, [node])

                await adapter._process_v3_step_actions(OrderStep("node", 4, node))

                action_state = next(
                    st for st in adapter.state.action_states
                    if st.action_id == "unclamp-1"
                )
                self.assertEqual(action_state.action_status, ActionStatus.FAILED)
                failures = [
                    error for error in adapter.state.errors
                    if error.error_type == ErrorType.ORDER_ACTION_FAILED
                ]
                self.assertEqual(
                    len(failures), 1,
                    "a failed order action must surface an error to the fleet",
                )
                references = {
                    ref.reference_key: ref.reference_value
                    for ref in failures[0].error_references
                }
                self.assertEqual(references.get("actionType"), "unclamp")
                self.assertEqual(references.get("actionId"), "unclamp-1")
                self.assertIn("ignored the command", references.get("reason", ""))
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_ignored_move_reports_target_and_actual_position(self) -> None:
        """The 'drive ignored the command' error must name target and actual.

        62's axis sits on the minus hardware limit at 0 while unclamp commands
        -16000; the old message said only 'not at the target', so the mismatch
        was invisible without probing the drive by hand.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
            # 62's deployed situation: a target past the minus limit the axis
            # already rests on, so the drive drops the move.
            adapter.config.ezi_config.unclamp_position = -16000
            adapter.set_ezi_motor(FakeClampMotor(servo_on=True, actual_position=0))

            with self.assertRaises(RuntimeError) as caught:
                await execute_clamp_action(
                    adapter, make_instant_action("unclamp").actions[0]
                )

            message = str(caught.exception)
            self.assertIn("target=-16000", message)
            self.assertIn("actual=0", message)

        asyncio.run(scenario())

    def test_order_action_raising_is_reported_instead_of_escaping(self) -> None:
        """A handler that raises must end FAILED with an error, not vanish.

        _execute_registered_order_action has no try/except, so an exception used
        to escape to the scheduler: the actionState stayed RUNNING forever and
        no error reached the fleet.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                class _RaisingRegistry:
                    def has(self, action_type):
                        return True

                    async def execute(self, action, adapter):
                        raise RuntimeError("recipe blew up")

                adapter._action_registry = _RaisingRegistry()

                node = self._node_with_actions("p39", 6, [("recipe-1", "someRecipe")])
                self._register_action_states(adapter, [node])

                await adapter._process_v3_step_actions(OrderStep("node", 6, node))

                action_state = next(
                    st for st in adapter.state.action_states
                    if st.action_id == "recipe-1"
                )
                self.assertEqual(action_state.action_status, ActionStatus.FAILED)
                self.assertIn("recipe blew up", action_state.result_description)
                self.assertTrue(any(
                    error.error_type == ErrorType.ORDER_ACTION_FAILED
                    for error in adapter.state.errors
                ))
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_start_node_actions_run_when_the_robot_is_already_there(self) -> None:
        """The already-reached start node is not driven to, but its actions run.

        62's departure order carries clamp(trigger=BEFORE_LEAVE_NODE) on node
        sequenceId=0 (1_01CH, where the robot already sits). The traversed-prefix
        prune dropped that node, so the clamp was never dispatched and the robot
        left the charger unclamped.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                # 1_01CH is a dock node: re-running its motion would UmDock again.
                adapter.config.motion_rules = [
                    MotionRule(to="1_01CH", mode="dock", fail_timeout_sec=0.2)
                ]
                adapter._last_node_id = "1_01CH"
                adapter._last_node_sequence_id = 0

                dispatched = []

                async def spy(action, action_state, owner_id=None):
                    dispatched.append(action.action_type)
                    action_state.action_status = ActionStatus.FINISHED

                adapter._execute_order_action = spy

                start = self._node_with_actions(
                    "1_01CH", 0, [("clamp-1", "clamp")]
                )
                onward = self._node_with_actions("p39", 2, [])
                order = Order.from_dict({
                    "headerId": 1, "timestamp": "2026-08-12T09:19:31.000Z",
                    "version": "3.0.0", "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-002",
                    "orderId": "order-depart-1", "orderUpdateId": 0,
                    "nodes": [start.to_dict(), onward.to_dict()],
                    "edges": [],
                })

                adapter._handle_v3_order(order)
                for _ in range(200):
                    if "clamp" in dispatched:
                        break
                    await asyncio.sleep(0.01)

                self.assertIn(
                    "clamp", dispatched,
                    "the start node's BEFORE_LEAVE_NODE action must still run",
                )
                # ...but the robot must not be sent to a node it already occupies.
                self.assertEqual(vehicle.dock_calls, 0)
            finally:
                state_task.cancel()

        asyncio.run(scenario())


class ManualControlInstantActionTest(AdapterV3OrderTest):
    """Tests for manualStop and enableMotor instant action handlers."""

    def _manual_action(self, action_type, action_id="m1", params=None):
        return InstantActions.from_dict({
            "headerId": 1,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "actions": [{
                "actionType": action_type,
                "actionId": action_id,
                "blockingType": "NONE",
                "actionParameters": [
                    {"key": k, "value": v} for k, v in (params or {}).items()
                ],
            }],
        })

    def test_manual_stop_calls_um_stop_even_while_busy(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._work_in_progress = "loading"  # busy
                adapter.instant_actions_accept_procedure(self._manual_action("manualStop"))
                await asyncio.sleep(0.02)
                self.assertGreaterEqual(adapter._vehicle.um_stop_calls, 1)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_enable_motor_calls_vehicle(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.instant_actions_accept_procedure(self._manual_action("enableMotor"))
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.enable_motor_calls, 1)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_drive_sends_um_drive_with_lat(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.config.manual_control.drive_lat = 0
                adapter.order = None
                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualDrive", params={"trans": 150, "rot": 0, "speed": 150})
                )
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.drive_calls, [(150.0, 0.0, 150.0, 0.0)])
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_drive_watchdog_auto_stops(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.config.manual_control.watchdog_ms = 30
                adapter.order = None
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive"))
                await asyncio.sleep(0.01)
                self.assertTrue(adapter._vehicle.drive_calls)
                self.assertEqual(adapter._vehicle.um_stop_calls, 0)
                self.assertTrue(adapter._manual_control_active)  # set during jog
                await asyncio.sleep(0.06)  # > watchdog_ms
                self.assertGreaterEqual(adapter._vehicle.um_stop_calls, 1)
                self.assertFalse(adapter._manual_control_active)  # deadman cleared it
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_drive_rejected_while_order_active(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                order = _one_node_order("o1")  # active order
                adapter.order = order
                # 진행 중이라는 근거는 order 객체가 아니라 아직 남은 node/edge 다.
                adapter.state.order_id = order.order_id
                adapter.state.node_states = list(order.nodes)
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive"))
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.drive_calls, [])
                # after cancelOrder clears self.order, manual is allowed
                adapter.order = None
                adapter.state.node_states = []
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive", action_id="m2"))
                await asyncio.sleep(0.02)
                self.assertTrue(adapter._vehicle.drive_calls)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_drive_allowed_once_the_order_ran_out_of_steps(self) -> None:
        """ORDER COMPLETE 뒤에도 self.order 는 남는다 (cancelOrder 만 지움).

        2026-08-22 .62 회귀: 그 상태로 수동 주행을 막아 23:14:37 오더 완료부터
        23:19:57 cancelOrder 까지 조이스틱이 죽어 있었다.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                order = _one_node_order("o1")
                adapter.order = order          # 완료돼도 지워지지 않고 남는다
                adapter.state.order_id = order.order_id
                adapter.state.node_states = []  # 스텝은 전부 소진
                adapter.state.edge_states = []
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive"))
                await asyncio.sleep(0.02)
                self.assertTrue(adapter._vehicle.drive_calls)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_drive_rejected_when_disabled(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = False
                adapter.order = None
                adapter.instant_actions_accept_procedure(self._manual_action("manualDrive"))
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.drive_calls, [])
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_move_calls_move_distance(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualMove", params={"distance": -2500, "speed": 200})
                )
                await asyncio.sleep(0.02)
                self.assertEqual(len(adapter._vehicle.move_distance_calls), 1)
                distance, speed, kwargs = adapter._vehicle.move_distance_calls[0]
                self.assertEqual(distance, -2500.0)
                self.assertEqual(speed, 200.0)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_move_passes_route_step_options(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                adapter.instant_actions_accept_procedure(
                    self._manual_action(
                        "manualMove",
                        params={
                            "distance": -600,
                            "speed": 150,
                            "flag": 2,
                            "io": 3,
                            "obs_avoid_dist": 900,
                            "side_avoid_dist": 40,
                            "use_io": "true",
                            "note": 7,
                            "run_mode": "set_routes",
                        },
                    )
                )
                await asyncio.sleep(0.02)
                _, _, kwargs = adapter._vehicle.move_distance_calls[0]
                self.assertEqual(kwargs["flag"], 2)
                self.assertEqual(kwargs["io"], 3)
                self.assertEqual(kwargs["obs_avoid_dist"], 900)
                self.assertEqual(kwargs["side_avoid_dist"], 40)
                self.assertEqual(kwargs["use_io"], True)
                self.assertEqual(kwargs["note"], 7)
                self.assertEqual(kwargs["run_mode"], "set_routes")
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_move_requires_distance(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                adapter.instant_actions_accept_procedure(self._manual_action("manualMove"))
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.move_distance_calls, [])
                # Action must be FAILED, not left WAITING
                matches = [
                    s for s in adapter.state.instant_action_states
                    if s.action_id == "m1"
                ]
                # If auto-cleared (terminal), that's also acceptable (FAILED was set then cleared)
                # But dispatch must not raise and action must not be RUNNING/WAITING
                for s in matches:
                    self.assertEqual(s.action_status, ActionStatus.FAILED)
            finally:
                state_task.cancel()
        asyncio.run(scenario())

    def test_manual_move_blank_speed_fails(self) -> None:
        """A blank/non-numeric speed present in params must FAIL the action,
        not silently fall back to default, and must NOT raise out of dispatch."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                # Simulate what the WebUI form sends when speed field is blank
                ia = self._manual_action("manualMove", action_id="m-blank-speed",
                                         params={"distance": "-1000", "speed": ""})
                # Must not raise
                adapter.instant_actions_accept_procedure(ia)
                await asyncio.sleep(0.02)
                # move_distance must NOT have been called
                self.assertEqual(adapter._vehicle.move_distance_calls, [])
                # Action must be FAILED (may already be cleared from instant_action_states
                # since terminal states are pruned, but the spy below confirms FAILED was set)
            finally:
                state_task.cancel()

        # Re-run with a spy to confirm FAILED is explicitly set and dispatch doesn't raise
        async def scenario_with_spy() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                ia = self._manual_action("manualMove", action_id="m-blank-speed2",
                                         params={"distance": "-1000", "speed": ""})
                from unittest.mock import patch as _patch
                with _patch.object(
                    adapter, "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    adapter.instant_actions_accept_procedure(ia)
                await asyncio.sleep(0.02)
                self.assertEqual(adapter._vehicle.move_distance_calls, [])
                self.assertTrue(
                    any(
                        c.args[0] == "m-blank-speed2" and c.args[1] == ActionStatus.FAILED
                        for c in spy.call_args_list
                    ),
                    "Expected FAILED status for blank speed; calls: " + str(spy.call_args_list),
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())
        asyncio.run(scenario_with_spy())

    def test_manual_drive_non_numeric_param_fails(self) -> None:
        """A non-numeric manualDrive param must FAIL the action and must NOT raise
        out of the dispatch loop (later actions in the same batch still execute)."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                ia = self._manual_action("manualDrive", action_id="m-bad-drive",
                                         params={"trans": "fast"})
                from unittest.mock import patch as _patch
                with _patch.object(
                    adapter, "_update_instant_action_status",
                    wraps=adapter._update_instant_action_status,
                ) as spy:
                    # Must not raise
                    adapter.instant_actions_accept_procedure(ia)
                await asyncio.sleep(0.02)
                # um_drive must NOT have been called
                self.assertEqual(adapter._vehicle.drive_calls, [])
                self.assertTrue(
                    any(
                        c.args[0] == "m-bad-drive" and c.args[1] == ActionStatus.FAILED
                        for c in spy.call_args_list
                    ),
                    "Expected FAILED status for non-numeric param; calls: " + str(spy.call_args_list),
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_manual_drive_sets_manual_control_active(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                self.assertFalse(adapter._manual_control_active)
                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualDrive",
                                        params={"trans": 100, "rot": 0, "speed": 100})
                )
                await asyncio.sleep(0.02)
                self.assertTrue(adapter._manual_control_active)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_manual_stop_clears_manual_control_active(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._manual_control_active = True
                adapter.instant_actions_accept_procedure(self._manual_action("manualStop"))
                await asyncio.sleep(0.02)
                self.assertFalse(adapter._manual_control_active)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_manual_move_clears_manual_control_active_when_done(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualMove",
                                        params={"distance": 500, "speed": 100})
                )
                await asyncio.sleep(0.05)
                # FakeVehicle.move_distance returns immediately, so the run's
                # finally-block has cleared the flag by now.
                self.assertFalse(adapter._manual_control_active)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_manual_drive_does_not_leak_flag_when_um_drive_fails(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None

                async def _boom(*args, **kwargs):
                    raise RuntimeError("um_drive boom")
                adapter._vehicle.um_drive = _boom

                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualDrive",
                                        params={"trans": 100, "rot": 0, "speed": 100})
                )
                await asyncio.sleep(0.02)
                # A failed manualDrive must NOT leave _manual_control_active stuck
                # True -- that would suppress settled idle capture forever, so
                # lastNodeId would stop updating even after the robot settles.
                self.assertFalse(adapter._manual_control_active)
            finally:
                state_task.cancel()

        asyncio.run(scenario())


class MoveSettleWaitTest(unittest.TestCase):
    """Completion detection for mode="move" segments.

    Move completion is distance-based; node arrival stays pose-based. These
    two questions have different answers whenever the commanded distance is
    shorter than the segment, which is the intended way to configure the rule.
    """

    def _adapter(self) -> Adapter:
        adapter = Adapter()
        adapter.set_vehicle(FakeVehicle())
        s = adapter.config.settings
        s.node_position_poll_interval_sec = 0.01
        s.move_start_timeout_sec = 0.2
        s.move_stall_timeout_sec = 0.2
        s.move_started_min_travel_mm = 50.0
        s.move_complete_travel_ratio = 0.9
        s.reach_zone_shape = "square"
        s.reach_zone_scale = 20.0
        s.default_node_deviation_xy = 10.0     # -> 200.0 effective radius
        return adapter

    def _node(self, node_id="p39"):
        return SimpleNamespace(node_id=node_id, sequence_id=2)

    def test_full_travel_outside_zone_returns_false(self) -> None:
        """The 2026-08-07 HN-SH6-TR-002 case: commanded 2000mm, travelled
        2031mm, and still 765mm short of p39. Used to hang forever."""
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(12681.0, -2473.0)
            reached, travelled = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            )
            self.assertFalse(reached)
            self.assertAlmostEqual(travelled, 2031.6, places=1)
        asyncio.run(scenario())

    def test_full_travel_inside_zone_returns_true(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(13400.0, -2505.0)          # 46mm from target, inside 200
            reached, _travelled = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2800.0, (10650.0, -2522.0)
            )
            self.assertTrue(reached)
        asyncio.run(scenario())

    def test_stopped_but_not_yet_moving_does_not_settle_early(self) -> None:
        """Regression for the premature firing seen on the robot at
        2026-08-05 23:50:55 and 2026-08-06 15:09:51, both exactly 1.0s after
        the move was sent with the robot still stationary."""
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10650.0, -2522.0)          # has not moved at all
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.1)               # short of both timeouts (each 0.2s)
            self.assertFalse(wait.done())          # must not settle on the first stopped sample
            wait.cancel()
        asyncio.run(scenario())

    def test_never_starting_returns_false_after_start_timeout(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10650.0, -2522.0)
            began = time.monotonic()
            reached, travelled = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            )
            elapsed = time.monotonic() - began
            self.assertFalse(reached)
            self.assertEqual(travelled, 0.0)       # never left the origin
            # Confirms it actually waited out move_start_timeout_sec (0.2), not
            # merely returned False from whichever branch fired.
            self.assertGreaterEqual(elapsed, adapter.config.settings.move_start_timeout_sec)
        asyncio.run(scenario())

    def test_displacement_alone_satisfies_the_start_gate(self) -> None:
        """A move short or fast enough that no sample catches it moving.

        move_start_timeout_sec and move_stall_timeout_sec are pulled far apart
        here (0.5 vs 0.05) so the settle path is unambiguous from timing alone:
        the start gate accepts displacement on the very first sample even
        though the robot reads "Stopped" throughout, so this must settle via
        the stall branch in roughly one stall_timeout, not wait out the much
        longer start_timeout.
        """
        async def scenario() -> None:
            adapter = self._adapter()
            adapter.config.settings.move_start_timeout_sec = 0.5
            adapter.config.settings.move_stall_timeout_sec = 0.05
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10750.0, -2522.0)          # 100mm > move_started_min_travel_mm
            began = time.monotonic()
            reached, travelled = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            )
            elapsed = time.monotonic() - began
            self.assertFalse(reached)              # settled via stall, not start timeout
            self.assertAlmostEqual(travelled, 100.0, places=3)
            self.assertLess(elapsed, 0.3)          # well under move_start_timeout_sec (0.5)
        asyncio.run(scenario())

    def test_short_travel_waits_for_the_stall_timeout(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "moving"
            v.arrive_at(11000.0, -2522.0)          # 350mm of a 2000mm move
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.05)
            v._status = "Stopped"                  # stall begins now
            await asyncio.sleep(0.05)
            self.assertFalse(wait.done())          # stall timeout not yet elapsed
            await asyncio.sleep(0.3)
            self.assertTrue(wait.done())
            reached, _travelled = await wait
            self.assertFalse(reached)
        asyncio.run(scenario())

    def test_resumed_motion_restarts_the_stall_timer(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "moving"
            v.arrive_at(11000.0, -2522.0)
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            v._status = "Stopped"
            await asyncio.sleep(0.15)              # most of the stall timeout
            v._status = "moving"                   # clears the stopped-since marker
            await asyncio.sleep(0.1)
            self.assertFalse(wait.done())          # timer restarted, not accumulated
            wait.cancel()
        asyncio.run(scenario())

    def test_obstacle_wait_blocks_the_stall_branch(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            # _is_jibot_stopped() compares _status by exact equality, so
            # "Stopped #brake" would NOT read as stopped -- it must be the
            # plain stop status, with the brake token carried on _mode, to
            # reach the only combination where the obstacle guard is live:
            # stopped=True and obstacle=True together.
            v._status = "Stopped"
            v._mode = "auto #brake"                # stopped AND obstacle-waiting
            v.arrive_at(11000.0, -2522.0)
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.4)               # past both timeouts
            self.assertFalse(wait.done())
            wait.cancel()
        asyncio.run(scenario())

    def test_zero_distance_settles_on_the_first_stopped_sample(self) -> None:
        """target_travel is 0, so full travel holds immediately. Without the
        full-travel branch preceding the start gate this would wait out
        move_start_timeout_sec instead."""
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(13446.0, -2505.0)
            started = time.monotonic()
            reached, _travelled = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), 0.0, (13446.0, -2505.0)
            )
            self.assertTrue(reached)
            self.assertLess(time.monotonic() - started, 0.15)   # not the 0.2 start timeout
        asyncio.run(scenario())

    def test_distance_below_start_threshold_settles_on_full_travel(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10680.0, -2522.0)          # travelled 30 of a 30mm move
            started = time.monotonic()
            reached, _travelled = await adapter._wait_until_move_settled(
                self._node(), (10680.0, -2522.0, 10.0), -30.0, (10650.0, -2522.0)
            )
            self.assertTrue(reached)
            self.assertLess(time.monotonic() - started, 0.15)
        asyncio.run(scenario())

    def test_paused_move_never_settles(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            adapter._motion_paused = True
            v._status = "Stopped"
            v.arrive_at(12681.0, -2473.0)          # full travel, would settle unpaused
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.4)               # past both timeouts
            self.assertFalse(wait.done())
            wait.cancel()
        asyncio.run(scenario())

    def test_operator_manual_drive_never_settles(self) -> None:
        """Never fight the joystick.

        An operator taking JIBOT's manual/teleop control switches _mode to a
        manual mode and discards the running relative move. With the stick held
        neutral the robot reads "Stopped", so the settle loop would see
        stopped-and-not-obstacle, arm the stall timer, and hand _run_move_segment
        a fallback goto to dispatch while a human is driving. The freeze must be
        the same one pause uses, so the pose here is deliberately at FULL travel:
        every other branch would settle immediately.
        """
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._mode = "ModeDrive"                  # config.jibot_status.manual_modes
            v._status = "Stopped"
            v.arrive_at(12681.0, -2473.0)          # full travel, would settle unguarded
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.4)               # past both timeouts (0.2 each)
            self.assertFalse(wait.done())
            wait.cancel()
        asyncio.run(scenario())

    def test_adapter_jog_never_settles(self) -> None:
        """A jog/manualMove the adapter itself dispatched owns the motion too,
        and moves the robot before JIBOT's polled _mode catches up — so
        _manual_control_active has to freeze the loop on its own, without any
        manual mode being reported."""
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(12681.0, -2473.0)          # full travel, would settle unguarded
            adapter._manual_control_active = True
            self.assertFalse(adapter._is_jibot_manual_drive())   # only the jog flag
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.4)               # past both timeouts (0.2 each)
            self.assertFalse(wait.done())
            wait.cancel()
        asyncio.run(scenario())

    def test_missing_pose_samples_are_skipped(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v._x = None
            v._y = None
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.4)
            self.assertFalse(wait.done())          # no pose, no judgment
            v.arrive_at(12681.0, -2473.0)          # telemetry returns
            await asyncio.sleep(0.1)
            self.assertTrue(wait.done())
            reached, _travelled = await wait
            self.assertFalse(reached)
        asyncio.run(scenario())

    def test_pose_gap_does_not_advance_the_stall_clock(self) -> None:
        """A telemetry dropout must not count toward the stall timer.

        stopped_since is set to a real timestamp BEFORE the pose disappears
        here (unlike test_missing_pose_samples_are_skipped, where it starts
        None). A dropout that outlives move_stall_timeout_sec (0.2) must not
        settle on the very first sample after recovery, judged against a
        stale pre-gap stopped_since.
        """
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "moving"
            v.arrive_at(10750.0, -2522.0)          # 100mm, clears the start gate
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.02)              # started flips true while moving
            v._status = "Stopped"                  # stopped_since begins now, with a known pose
            await asyncio.sleep(0.03)              # real stopped time, still short of stall_timeout
            v._x = None
            v._y = None                            # pose drops out
            await asyncio.sleep(0.25)              # gap outlives move_stall_timeout_sec (0.2)
            self.assertFalse(wait.done())          # no pose, no judgment -- still waiting
            v.arrive_at(10750.0, -2522.0)          # telemetry recovers, position unchanged
            await asyncio.sleep(0.03)              # well under stall_timeout from THIS sample
            self.assertFalse(wait.done())          # must not settle on the first recovered sample
            wait.cancel()
        asyncio.run(scenario())

    def test_wait_for_vehicle_pose_returns_none_on_timeout(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            adapter._vehicle._x = None
            adapter._vehicle._y = None
            self.assertIsNone(await adapter._wait_for_vehicle_pose(0.05))
        asyncio.run(scenario())

    def test_wait_for_vehicle_pose_returns_first_valid_reading(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._x = None
            v._y = None

            async def arrive_soon() -> None:
                await asyncio.sleep(0.03)
                v.arrive_at(10650.0, -2522.0)

            asyncio.create_task(arrive_soon())
            self.assertEqual(await adapter._wait_for_vehicle_pose(1.0), (10650.0, -2522.0))
        asyncio.run(scenario())


class GotoArrivalStandstillTest(unittest.TestCase):
    """A goto node is complete only once JIBOT has stopped driving.

    Arrival is accepted anywhere inside the reach zone, so the pose can satisfy
    the node while the UmGoto task is still running the robot to the node's
    exact pose. Completing there hands the FMS a node it has not finished.
    """

    def _make_adapter(self) -> Adapter:
        adapter = Adapter()
        adapter.set_vehicle(FakeVehicle())
        s = adapter.config.settings
        s.node_position_poll_interval_sec = 0.01
        s.standstill_poll_interval_sec = 0.01
        s.standstill_timeout_sec = 5.0
        return adapter

    async def _start_state(self, adapter: Adapter) -> asyncio.Task:
        adapter._loop = asyncio.get_running_loop()
        task = asyncio.create_task(adapter.publish_state("state", interval_sec=0.05))
        for _ in range(100):
            if adapter.state is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(adapter.state)
        return task

    def _goto_node(self, node_id="p39", x=300.0, y=400.0, seq=3):
        from protocol.vda5050_3_0.messages import Node
        return Node.from_dict({
            "nodeId": node_id, "sequenceId": seq, "released": True,
            "nodePosition": {"x": x, "y": y, "theta": 0.0, "mapId": "lab2m"},
            "actions": [],
        })

    def _move_node(self, to="p39", x=13446.0, y=-2505.0, seq=3):
        return self._goto_node(node_id=to, x=x, y=y, seq=seq)

    def test_goto_node_waits_for_the_goto_task_to_clear_after_arrival(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                node = self._goto_node()
                # In the reach zone from the first poll, but JIBOT still owns
                # the goto task that is finishing the approach.
                vehicle.arrive_at(300.0, 400.0)
                vehicle._status = "Stopped"
                vehicle._mode = "MRosGoto"

                task = asyncio.create_task(
                    adapter._process_v3_node_step(
                        OrderStep("node", node.sequence_id, node)
                    )
                )
                await asyncio.sleep(0.1)
                self.assertFalse(
                    task.done(),
                    "node completed while JIBOT still owned the goto task",
                )
                self.assertNotEqual(adapter.state.last_node_id, "p39")

                vehicle._mode = "auto"          # goto task finished
                self.assertTrue(await asyncio.wait_for(task, timeout=5.0))
                self.assertEqual(adapter.state.last_node_id, "p39")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_goto_node_completes_when_the_standstill_wait_times_out(self) -> None:
        """A robot that never stops reporting its goto must not lose the order.

        The wait is a refinement of arrival, not a new failure mode: the pose is
        already in the reach zone, so a JIBOT that keeps its goto task pinned
        (stale telemetry, firmware quirk) has to fall back to today's
        arrival-only completion instead of killing the order worker.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.settings.standstill_timeout_sec = 0.1
                node = self._goto_node()
                vehicle.arrive_at(300.0, 400.0)
                vehicle._status = "Stopped"
                vehicle._mode = "MRosGoto"      # never clears

                ok = await asyncio.wait_for(
                    adapter._process_v3_node_step(
                        OrderStep("node", node.sequence_id, node)
                    ),
                    timeout=5.0,
                )

                self.assertTrue(ok)
                self.assertEqual(adapter.state.last_node_id, "p39")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_fallback_goto_waits_for_the_goto_task_to_clear(self) -> None:
        """The fallback goto is a goto, so it settles like one.

        A mode="move" segment that stops short finishes with a plain goto, and
        that goto reaches its zone the same way — the segment path must not be
        the one place a node completes on a live goto task.
        """
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0      # -> 200.0 radius
                s.move_start_timeout_sec = 0.05
                s.move_stall_timeout_sec = 0.05
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                node = self._move_node()

                original_move_distance = vehicle.move_distance
                original_goto_xyz = vehicle.goto_xyz

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(12681.0, -2473.0)   # full travel, still short

                async def arriving_goto_xyz(x, y, z, strict: bool = False) -> None:
                    await original_goto_xyz(x, y, z, strict=strict)
                    vehicle._mode = "MRosGoto"            # JIBOT owns the goto
                    vehicle.arrive_at(x, y)

                vehicle.move_distance = moving_move_distance
                vehicle.goto_xyz = arriving_goto_xyz

                task = asyncio.create_task(
                    adapter._process_v3_node_step(
                        OrderStep("node", node.sequence_id, node)
                    )
                )
                for _ in range(200):
                    if vehicle.goto_targets:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(vehicle.goto_targets, [(13446.0, -2505.0, 0.0)])

                await asyncio.sleep(0.1)
                self.assertFalse(
                    task.done(),
                    "fallback goto completed while JIBOT still owned the task",
                )

                vehicle._mode = "auto"
                self.assertTrue(await asyncio.wait_for(task, timeout=5.0))
                self.assertEqual(adapter.state.last_node_id, "p39")
            finally:
                state_task.cancel()

        asyncio.run(scenario())


class ContinuousPathHarnessTests(unittest.TestCase):
    def test_fake_vehicle_can_report_active_automatic_motion(self):
        vehicle = FakeVehicle()
        self.assertFalse(vehicle.is_driving())

        vehicle.start_driving()
        self.assertTrue(vehicle.is_driving())
        # _has_active_automatic_motion 은 _mode 에 "goto" 가 들어 있으면 True 다.
        self.assertIn("goto", vehicle._mode.lower())

        vehicle.stop_driving()
        self.assertFalse(vehicle.is_driving())
        self.assertEqual(vehicle._status, "Stopped")


class EdgeStateReleaseTimingTests(unittest.TestCase):
    def _make_adapter(self) -> Adapter:
        adapter = Adapter()
        vehicle = FakeVehicle()
        adapter.set_vehicle(vehicle)
        adapter.set_charge_circuit(_VehicleLinkedChargeCircuit(vehicle))
        return adapter

    def test_edge_state_survives_until_next_node_is_traversed(self):
        adapter = self._make_adapter()
        adapter.state = SimpleNamespace(
            edge_states=[
                SimpleNamespace(edge_id="E1", sequence_id=1, released=True),
                SimpleNamespace(edge_id="E2", sequence_id=3, released=True),
            ]
        )

        # 엣지 진입만으로는 지워지지 않는다
        removed = adapter._clear_edge_states_up_to(1)
        self.assertEqual(removed, 0)
        self.assertEqual([e.edge_id for e in adapter.state.edge_states], ["E1", "E2"])

        # 그 엣지가 이어지는 노드(seq=2)를 통과하면 E1 만 지워진다
        removed = adapter._clear_edge_states_up_to(2)
        self.assertEqual(removed, 1)
        self.assertEqual([e.edge_id for e in adapter.state.edge_states], ["E2"])

    def test_finalize_node_step_releases_the_edge_leading_into_it(self):
        # _clear_edge_states_up_to 를 직접 부르는 것만으로는 실제 배선
        # (_finalize_v3_node_step 이 그 함수를 부르는지)을 못 지킨다. 이 테스트가
        # 없으면 누가 그 호출을 지워도(혹은 삭제된 else 분기를 되살려도) 위 유닛
        # 테스트만으로는 안 걸린다.
        adapter = self._make_adapter()
        adapter.state = SimpleNamespace(
            edge_states=[SimpleNamespace(edge_id="E1", sequence_id=1, released=True)],
            action_states=[],
        )
        node = SimpleNamespace(node_id="N2", sequence_id=2)
        step = OrderStep("node", 2, node)

        self.assertTrue(adapter._finalize_v3_node_step(step, node))

        self.assertEqual(adapter.state.edge_states, [])
        self.assertEqual(adapter.state.last_node_sequence_id, 2)

    def test_clear_v3_order_step_leaves_edge_states_untouched_for_edge_kind(self):
        # 삭제된 건 "엣지 kind 분기가 edge_states 를 지우는 코드"이지, edge_states
        # 자체가 아니다. 이 분기를 되살리는 회귀를 이 테스트가 직접 잡는다.
        adapter = self._make_adapter()
        edge = SimpleNamespace(edge_id="E1", sequence_id=1, released=True)
        adapter.state = SimpleNamespace(
            order_id="o1",
            node_states=[],
            edge_states=[edge],
            action_states=[],
        )
        step = OrderStep("edge", 1, edge)

        adapter._clear_v3_order_step(step)

        self.assertEqual(adapter.state.edge_states, [edge])


class ContinuousPathSettingsTests(unittest.TestCase):
    def _make_adapter(self) -> Adapter:
        adapter = Adapter()
        vehicle = FakeVehicle()
        adapter.set_vehicle(vehicle)
        adapter.set_charge_circuit(_VehicleLinkedChargeCircuit(vehicle))
        return adapter

    def test_defaults_keep_current_behaviour(self):
        adapter = self._make_adapter()
        self.assertEqual(adapter.config.settings.path_control, "stop_point")
        self.assertEqual(adapter.config.settings.waypoint_pass_radius_mm, 0.0)
        self.assertFalse(adapter._continuous_path_enabled())

    def test_continuous_enables_the_gate(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        self.assertTrue(adapter._continuous_path_enabled())

    def test_unknown_value_warns_and_falls_back(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "blend"   # 오타/미지원 값
        self.assertFalse(adapter._continuous_path_enabled())


def _cp_node(node_id="N1", seq=2, actions=None, released=True, xy=(1000.0, 2000.0)):
    # 좌표를 기본으로 준다. nodePosition 이 없으면 _resolve_node_target 이 None 을
    # 돌려주고 _is_coalescing_breaker 가 무조건 True 가 되어, "합쳐진다"는 쪽을
    # 한 번도 검증하지 못한 채 전부 초록으로 보인다.
    position = (
        None
        if xy is None
        else SimpleNamespace(x=xy[0], y=xy[1], theta=0.0, allowed_deviation_xy=50.0)
    )
    return OrderStep(
        "node",
        seq,
        SimpleNamespace(
            node_id=node_id,
            sequence_id=seq,
            released=released,
            actions=actions or [],
            node_position=position,
        ),
    )


def _cp_edge(seq=3, released=True):
    return OrderStep(
        "edge",
        seq,
        SimpleNamespace(
            edge_id=f"E{seq}",
            sequence_id=seq,
            released=released,
            actions=[],
        ),
    )


def _cp_action(blocking_type):
    return SimpleNamespace(
        action_id=f"a-{blocking_type}",
        action_type="probe",
        blocking_type=blocking_type,
        action_parameters=[],
    )


class ContinuousPathNodeStepTests(unittest.TestCase):
    def _make_adapter(self) -> Adapter:
        adapter = Adapter()
        vehicle = FakeVehicle()
        adapter.set_vehicle(vehicle)
        adapter.set_charge_circuit(_VehicleLinkedChargeCircuit(vehicle))
        return adapter

    def test_intermediate_node_does_not_settle(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        step = _cp_node()
        self.assertFalse(adapter._should_settle_at(step, is_run_end=False))
        self.assertTrue(adapter._should_settle_at(step, is_run_end=True))

    def test_stop_point_always_settles(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "stop_point"
        step = _cp_node()
        self.assertTrue(adapter._should_settle_at(step, is_run_end=False))

    def test_blocking_action_breaks_the_run(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        self.assertFalse(adapter._step_has_blocking_action(_cp_node()))
        self.assertFalse(
            adapter._step_has_blocking_action(_cp_node(actions=[_cp_action("NONE")]))
        )
        self.assertTrue(
            adapter._step_has_blocking_action(_cp_node(actions=[_cp_action("SOFT")]))
        )
        self.assertTrue(
            adapter._step_has_blocking_action(_cp_node(actions=[_cp_action("HARD")]))
        )

    def test_unreleased_and_actions_only_break_the_run(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        self.assertTrue(adapter._is_coalescing_breaker(_cp_node(released=False)))
        only = _cp_node()
        only.actions_only = True
        self.assertTrue(adapter._is_coalescing_breaker(only))

    def test_plain_released_node_is_not_a_breaker(self):
        # 끊지 않는 경우가 하나도 없으면 코얼레싱은 영원히 no-op 인데도 위의
        # True 단정들은 전부 통과한다. 반대쪽을 명시적으로 고정한다.
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        self.assertFalse(adapter._is_coalescing_breaker(_cp_node()))
        # 좌표를 모르면 통과 판정 자체가 불가능하므로 끊는다.
        self.assertTrue(adapter._is_coalescing_breaker(_cp_node(xy=None)))
        # 엣지 스텝은 노드가 아니라 끊는다(구간은 노드의 연속이다).
        self.assertTrue(adapter._is_coalescing_breaker(_cp_edge()))

    def test_is_run_end_is_false_for_an_intermediate_node(self):
        # 큐는 노드와 엣지가 번갈아 들어온다. 엣지를 그대로 술어에 먹이면 항상
        # 첫 칸에서 끊겨 continuous 가 stop_point 와 똑같아진다.
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter.order_queue.put_nowait(_cp_edge(3))
        adapter.order_queue.put_nowait(_cp_node("N4", 4))
        self.assertFalse(adapter._is_run_end(_cp_node("N2", 2)))

    def test_is_run_end_is_true_when_the_next_node_breaks(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter.order_queue.put_nowait(_cp_edge(3))
        adapter.order_queue.put_nowait(
            _cp_node("N4", 4, actions=[_cp_action("HARD")])
        )
        self.assertTrue(adapter._is_run_end(_cp_node("N2", 2)))

    def test_is_run_end_is_true_for_the_last_queued_node(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        self.assertTrue(adapter._is_run_end(_cp_node("N2", 2)))

    def test_stop_point_makes_every_node_a_run_end(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "stop_point"
        adapter.order_queue.put_nowait(_cp_edge(3))
        adapter.order_queue.put_nowait(_cp_node("N4", 4))
        self.assertTrue(adapter._is_run_end(_cp_node("N2", 2)))

    def test_motion_seq_and_finalize_run_per_node_even_when_coalesced(self):
        """중간 노드도 노드마다 seq 가 오르고 finalize 가 돈다 (spec §5.3, §6).

        _finalize_v3_node_step 을 스텁으로 갈아끼우고 직접 부르면 프로덕션 코드를
        한 줄도 안 지난다. 실제로 _process_v3_node_step 을 두 번 돌린다.
        """
        result: Dict[str, Any] = {}

        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.settings.path_control = "continuous"
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:

                async def _accept_goto(_node):
                    return None

                adapter._send_node_motion = _accept_goto

                finalized: List[str] = []
                original_finalize = adapter._finalize_v3_node_step

                def _spy_finalize(step, node):
                    finalized.append(node.node_id)
                    return original_finalize(step, node)

                adapter._finalize_v3_node_step = _spy_finalize

                before = adapter._order_node_motion_seq
                published: List[int] = []
                for node_id, seq, xy in (("N2", 2, (100.0, 200.0)), ("N4", 4, (300.0, 400.0))):
                    node = Node.from_dict(
                        {
                            "nodeId": node_id,
                            "sequenceId": seq,
                            "released": True,
                            "nodePosition": {
                                "x": xy[0], "y": xy[1], "theta": 0.0, "mapId": "lab2m",
                            },
                            "actions": [],
                        }
                    )
                    # 뒤에 released 노드가 남아 있으니 둘 다 구간의 중간이다.
                    adapter.order_queue.put_nowait(_cp_node("N9", seq + 4))
                    vehicle.arrive_at(*xy)
                    vehicle.start_driving(node_id)
                    self.assertTrue(
                        await asyncio.wait_for(
                            adapter._process_v3_node_step(
                                OrderStep("node", seq, node)
                            ),
                            5.0,
                        )
                    )
                    published.append(adapter.state.last_node_sequence_id)
                    adapter.order_queue.get_nowait()

                result["seq_delta"] = adapter._order_node_motion_seq - before
                result["finalized"] = finalized
                result["published"] = published
                result["last_node_id"] = adapter.state.last_node_id
            finally:
                state_task.cancel()

        asyncio.run(scenario())
        # goto 를 안 세웠어도 seq 는 노드마다 오르고(rotateTo supersede 신호),
        # lastNodeId 발행도 노드마다 나간다(FMS 점유 관제).
        self.assertEqual(result["seq_delta"], 2)
        self.assertEqual(result["finalized"], ["N2", "N4"])
        self.assertEqual(result["published"], [2, 4])
        self.assertEqual(result["last_node_id"], "N4")

    def test_coalescing_run_is_not_obsolete_while_driving(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        step = _cp_node(node_id="N4", seq=8)
        adapter.current_order_step = step
        adapter.state = SimpleNamespace(
            last_node_id="N2", last_node_sequence_id=8, node_states=[], edge_states=[]
        )
        adapter._order_action_step_in_flight = None

        # 구간 주행 중이 아니면 기존 판정 그대로 취소 대상이다
        adapter._coalescing_run_step = None
        self.assertTrue(adapter._active_order_worker_step_is_obsolete())

        # 구간 주행 중이면 취소되지 않는다
        adapter._coalescing_run_step = step
        self.assertFalse(adapter._active_order_worker_step_is_obsolete())

    async def _start_state(self, adapter: Adapter) -> asyncio.Task:
        adapter._loop = asyncio.get_running_loop()
        task = asyncio.create_task(adapter.publish_state("state", interval_sec=0.05))
        for _ in range(100):
            if adapter.state is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(adapter.state)
        return task

    def _drive_intermediate_node(self, path_control: str) -> Dict[str, Any]:
        """중간 노드 N2 를 실제로 주행시키고 결과를 모은다.

        술어 단위 테스트만으로는 "중간 노드에서 정지를 기다리지 않는다"가 호출부에서
        실제로 성립하는지 알 수 없다. goto 가 아직 살아 있는 상태(start_driving)에서
        _process_v3_node_step 을 끝까지 돌려 확인한다.
        """
        result: Dict[str, Any] = {}

        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.settings.path_control = path_control
            # stop_point 쪽은 주행이 끝나지 않으므로 정지 대기가 타임아웃까지 간다.
            # 기본 10s 를 기다릴 이유가 없다 — 여기서 보는 건 "불렸는가" 뿐이다.
            adapter.config.settings.standstill_timeout_sec = 0.2
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                node = Node.from_dict(
                    {
                        "nodeId": "N2",
                        "sequenceId": 2,
                        "released": True,
                        "nodePosition": {
                            "x": 100.0,
                            "y": 200.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                        },
                        "actions": [],
                    }
                )
                step = OrderStep("node", 2, node)
                # 구간이 이어진다: 뒤에 released 노드가 하나 더 남아 있다.
                adapter.order_queue.put_nowait(_cp_edge(3))
                adapter.order_queue.put_nowait(_cp_node("N4", 4))

                async def _accept_goto(_node):
                    return None

                adapter._send_node_motion = _accept_goto

                settled: List[str] = []
                original_settle = adapter._settle_goto_arrival

                async def _spy_settle(settled_node):
                    settled.append(settled_node.node_id)
                    await original_settle(settled_node)

                adapter._settle_goto_arrival = _spy_settle

                vehicle.arrive_at(100.0, 200.0)   # 노드 위를 지나는 중
                vehicle.start_driving("N4")       # 앞선 goto 는 아직 살아 있다

                result["ok"] = await asyncio.wait_for(
                    adapter._process_v3_node_step(step), 5.0
                )
                result["settled"] = settled
                result["last_seq"] = adapter.state.last_node_sequence_id
                result["still_driving"] = adapter._has_active_automatic_motion()
            finally:
                state_task.cancel()

        asyncio.run(scenario())
        return result

    def test_continuous_completes_the_node_while_still_driving(self):
        result = self._drive_intermediate_node("continuous")
        self.assertTrue(result["ok"])
        self.assertEqual(result["settled"], [])      # 정지 대기를 아예 안 걸었다
        self.assertEqual(result["last_seq"], 2)      # 통과 보고는 그대로 나간다
        self.assertTrue(result["still_driving"])     # 여전히 주행 중인데 완료됐다

    def test_stop_point_still_settles_at_the_same_node(self):
        # 기본값에서 호출부 동작이 한 톨도 바뀌지 않았음을 게이트가 아니라
        # _process_v3_node_step 자리에서 고정한다.
        result = self._drive_intermediate_node("stop_point")
        self.assertTrue(result["ok"])
        self.assertEqual(result["settled"], ["N2"])
        self.assertEqual(result["last_seq"], 2)

    def test_edge_with_a_blocking_action_ends_the_run(self):
        """spec §5.2 "엣지에 SOFT/HARD 블로킹 액션" 행을 지킨다.

        엣지를 전부 걸러 버리면 구간이 그 너머까지 이어지고, 로봇은 N2 를 지나친
        뒤에야 _stop_for_blocking_order_actions 에 걸려 급제동한다. 이 조건이
        막으려는 것이 바로 "노드에서의 통제된 정지"다.
        """
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        blocking_edge = _cp_edge(3)
        blocking_edge.item.actions = [_cp_action("HARD")]
        adapter.order_queue.put_nowait(blocking_edge)
        adapter.order_queue.put_nowait(_cp_node("N4", 4))
        self.assertTrue(adapter._is_run_end(_cp_node("N2", 2)))

    def test_edge_without_a_blocking_action_does_not_end_the_run(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        plain_edge = _cp_edge(3)
        plain_edge.item.actions = [_cp_action("NONE")]
        adapter.order_queue.put_nowait(plain_edge)
        adapter.order_queue.put_nowait(_cp_node("N4", 4))
        self.assertFalse(adapter._is_run_end(_cp_node("N2", 2)))

    def test_directional_dock_rule_on_the_next_node_ends_the_run(self):
        """앞날 보기는 그 구간의 실제 시작 노드로 방향성 룰을 봐야 한다.

        self._last_node_id 를 그대로 쓰면 N2 가 아니라 N0 를 시작점으로 보고
        from="N2" 룰이 매칭에 실패해, 구간이 충전기까지 정지 없이 이어진다.
        """
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter.config.motion_rules = [
            MotionRule(to="N4", from_node="N2", mode="dock")
        ]
        adapter._last_node_id = "N0"          # 현재 노드 N2 의 앞 노드
        adapter.order_queue.put_nowait(_cp_edge(3))
        adapter.order_queue.put_nowait(_cp_node("N4", 4))
        self.assertTrue(adapter._is_run_end(_cp_node("N2", 2)))

    def test_directional_move_rule_on_the_next_node_ends_the_run(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter.config.motion_rules = [
            MotionRule(to="N4", from_node="N2", mode="move", distance=1000)
        ]
        adapter._last_node_id = "N0"
        adapter.order_queue.put_nowait(_cp_edge(3))
        adapter.order_queue.put_nowait(_cp_node("N4", 4))
        self.assertTrue(adapter._is_run_end(_cp_node("N2", 2)))

    def test_directional_rule_for_another_segment_does_not_end_the_run(self):
        # 같은 목적지라도 다른 구간에서 오는 룰이면 끊지 않는다 — 방향성을 잃고
        # 무조건 끊어 버리는 회귀를 막는다.
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter.config.motion_rules = [
            MotionRule(to="N4", from_node="N7", mode="dock")
        ]
        adapter._last_node_id = "N0"
        adapter.order_queue.put_nowait(_cp_edge(3))
        adapter.order_queue.put_nowait(_cp_node("N4", 4))
        self.assertFalse(adapter._is_run_end(_cp_node("N2", 2)))

    def test_pass_wait_uses_segment_interpolation_between_samples(self):
        """두 표본 모두 반경 밖인데 그 사이 선분이 노드를 지나는 경우 (spec §5.1).

        1002 mm/s x 0.2s = 표본 간 약 200mm 라 점 판정으로는 통째로 건너뛴다.
        기존 테스트는 로봇을 노드 위에 세워 둬서 첫 표본(prev_xy=None)의 점 판정으로
        끝났고, 보간 경로를 한 번도 안 지났다.
        """
        result: Dict[str, Any] = {}

        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.settings.path_control = "continuous"
            adapter.config.settings.node_position_poll_interval_sec = 0.01
            adapter.config.settings.waypoint_pass_radius_mm = 50.0
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                node = SimpleNamespace(node_id="N2", sequence_id=2)
                vehicle.start_driving("N2")
                vehicle.arrive_at(100.0, -100.0)   # 노드에서 300mm 앞, 반경 밖

                async def _fly_past() -> None:
                    await asyncio.sleep(0.05)
                    vehicle.arrive_at(100.0, 500.0)   # 노드에서 300mm 뒤, 반경 밖

                flyer = asyncio.create_task(_fly_past())
                # 두 표본 다 반경(50) 밖이지만 (100,-100)-(100,500) 선분은
                # 노드 (100,200) 을 정확히 지난다.
                await asyncio.wait_for(
                    adapter._wait_until_node_passed(node, (100.0, 200.0, 2.0)), 5.0
                )
                await flyer
                result["passed"] = True
                result["never_inside"] = min(
                    abs(-100.0 - 200.0), abs(500.0 - 200.0)
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())
        self.assertTrue(result["passed"])
        self.assertGreater(result["never_inside"], 50.0)   # 어떤 표본도 반경 안이 아니었다

    def test_pass_wait_recovers_and_escalates_a_stalled_robot(self):
        """통과 대기도 도착 대기와 같은 스톨 복구/에스컬레이션을 건다.

        waypoint_pass_radius_mm 기본값 0.0 이면 반경이 도착존까지 좁아지는데,
        spec §3.2 는 코너에서 구간을 끊지 않는다. 코너를 크게 도는 로봇은 그 반경에
        영영 안 들어올 수 있고, 복구가 없으면 FMS 에 에러 하나 없이 매달린다.

        가드가 재시도 예산을 다 쓰고 포기(gave_up)하면 대기는 거기서 끝나고
        False 를 반환해야 한다(§Finding 1) — asyncio.wait_for 로 감싸서, 과거
        구현처럼 while True 로 계속 돌면 여기서 타임아웃으로 드러난다.
        """
        result: Dict[str, Any] = {}

        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.settings.path_control = "continuous"
            adapter.config.settings.node_position_poll_interval_sec = 0.01
            adapter.config.settings.node_unreached_delay_sec = 0.0
            adapter.config.settings.node_goto_retry_delay_sec = 0.0
            adapter.config.settings.node_goto_retry_limit = 2
            adapter.config.settings.waypoint_pass_radius_mm = 50.0
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                node = SimpleNamespace(node_id="N2", sequence_id=2)
                vehicle.arrive_at(9000.0, 9000.0)   # 노드에서 멀리
                vehicle.stop_driving()              # 아무도 안 몰고 서 있다
                adapter._active_goto_node = node

                result["passed"] = await asyncio.wait_for(
                    adapter._wait_until_node_passed(node, (100.0, 200.0, 2.0)),
                    5.0,
                )
                fatal = [
                    error for error in adapter.state.errors
                    if getattr(error, "error_type", None)
                    == ErrorType.JIBOT_NODE_UNREACHED
                    and error.error_level == ErrorLevel.FATAL
                ]
                result["retries"] = len(vehicle.goto_targets)
                result["fatal"] = bool(fatal)
            finally:
                adapter._active_goto_node = None
                state_task.cancel()

        asyncio.run(scenario())
        self.assertEqual(result["retries"], 2)          # node_goto_retry_limit
        self.assertTrue(result["fatal"])                # FMS 가 볼 FATAL 이 나갔다
        self.assertFalse(result["passed"])              # 포기 후 대기는 끝나고 False

    def test_pass_wait_failure_ends_the_node_step_instead_of_hanging(self):
        """스톨 가드 포기가 워커 전체 경로(_process_v3_node_step)에서도 끝나는지.

        위 테스트는 _wait_until_node_passed 단독을 고정한다. 실제 호출부인
        _process_v3_node_step 이 그 False 를 받아 스텝을 실패 처리하는지(정지가
        아니라)는 별도로 지켜야 한다 — 안 그러면 반환값과 호출부 배선이
        따로 놀아도 이 테스트만으로는 못 잡는다. asyncio.wait_for 가 실제
        판별 수단이다: 배선이 빠져 다시 while True 로 도는 회귀라면 여기서
        타임아웃이 난다.
        """
        result: Dict[str, Any] = {}

        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.settings.path_control = "continuous"
            adapter.config.settings.node_position_poll_interval_sec = 0.01
            adapter.config.settings.node_unreached_delay_sec = 0.0
            adapter.config.settings.node_goto_retry_delay_sec = 0.0
            adapter.config.settings.node_goto_retry_limit = 1
            adapter.config.settings.waypoint_pass_radius_mm = 50.0
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                node = Node.from_dict(
                    {
                        "nodeId": "N2",
                        "sequenceId": 2,
                        "released": True,
                        "nodePosition": {
                            "x": 100.0,
                            "y": 200.0,
                            "theta": 0.0,
                            "mapId": "lab2m",
                        },
                        "actions": [],
                    }
                )
                step = OrderStep("node", 2, node)
                # 구간이 이어진다: N2 는 중간 노드라 정지 대기가 아니라
                # 통과 대기(_wait_until_node_passed) 경로로 들어간다.
                adapter.order_queue.put_nowait(_cp_edge(3))
                adapter.order_queue.put_nowait(_cp_node("N4", 4))

                async def _accept_goto(_node):
                    return None

                adapter._send_node_motion = _accept_goto

                vehicle.arrive_at(9000.0, 9000.0)   # 노드에서 멀리, 반경 밖
                vehicle.stop_driving()              # 아무도 안 몰고 서 있다

                result["ok"] = await asyncio.wait_for(
                    adapter._process_v3_node_step(step), 5.0
                )
                result["fatal"] = any(
                    getattr(error, "error_type", None)
                    == ErrorType.JIBOT_NODE_UNREACHED
                    and error.error_level == ErrorLevel.FATAL
                    for error in adapter.state.errors
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())
        self.assertFalse(result["ok"])     # 스텝은 실패로 끝난다(매달리지 않는다)
        self.assertTrue(result["fatal"])   # FMS 가 볼 FATAL 은 그대로 나간다

    def test_pass_wait_drops_stale_prev_xy_after_a_long_dropout(self):
        """pose dropout 이 길면 prev_xy 를 버려야 한다(§Finding 3).

        노드는 (100, 200). dropout 전 표본 (100, -1000)과 dropout 후 표본
        (100, 1400)은 둘 다 반경(50) 밖이지만, 그 사이를 잇는 선분은 노드를
        정확히 지난다. dropout 이 stale 기준(poll_sec *
        _PASS_WAIT_STALE_GAP_POLLS)을 넘기면 prev_xy 가 리셋되어 재출현
        표본은 점 판정만 서야 하고, 통과로 오판하면 안 된다 — 로봇이 실제로
        그 구간을 지났는지 알 수 없으니 안전한 쪽은 "놓치는" 쪽이다(뒤에서
        스톨 가드가 잡는다).
        """
        result: Dict[str, Any] = {}

        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.settings.path_control = "continuous"
            poll_sec = 0.01
            adapter.config.settings.node_position_poll_interval_sec = poll_sec
            adapter.config.settings.waypoint_pass_radius_mm = 50.0
            stale_gap_sec = poll_sec * Adapter._PASS_WAIT_STALE_GAP_POLLS
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                node = SimpleNamespace(node_id="N2", sequence_id=2)
                # 스톨 시계가 돌지 않게 "주행 중"으로 둔다 — 이 테스트가 보는
                # 것은 보간 앵커지 스톨 가드의 포기가 아니다.
                vehicle.start_driving("N2")
                vehicle.arrive_at(100.0, -1000.0)   # 반경 밖, dropout 전 표본

                async def _drop_and_reappear() -> None:
                    await asyncio.sleep(stale_gap_sec * 2)   # 확실히 stale 기준을 넘긴다
                    vehicle._x = None
                    vehicle._y = None
                    await asyncio.sleep(stale_gap_sec * 2)   # dropout 지속
                    vehicle.arrive_at(100.0, 1400.0)         # 반경 밖, dropout 후 표본
                    result["reappeared"] = True

                dropper = asyncio.create_task(_drop_and_reappear())
                try:
                    # 여유를 넉넉히 둔다 — 재출현(위 4*stale_gap_sec) 이후에도
                    # 판정이 몇 폴링 주기 더 돌 시간을 남겨야, 타임아웃이
                    # "재출현 표본을 아직 못 봄"이 아니라 "봤는데도 통과로
                    # 안 잡힘"을 뜻하게 된다.
                    await asyncio.wait_for(
                        adapter._wait_until_node_passed(
                            node, (100.0, 200.0, 2.0)
                        ),
                        stale_gap_sec * 4 + poll_sec * 20,
                    )
                    result["falsely_passed"] = True
                except asyncio.TimeoutError:
                    result["falsely_passed"] = False
                finally:
                    dropper.cancel()
            finally:
                state_task.cancel()

        asyncio.run(scenario())
        self.assertTrue(result.get("reappeared"))   # 재출현 표본이 실제로 판정을 탔다
        self.assertFalse(result["falsely_passed"])


class NewBaseRequestTests(unittest.TestCase):
    """VDA5050 3.0 §6.6.3 newBaseRequest — Task 6 (CP-3)."""

    def _make_adapter(self) -> Adapter:
        adapter = Adapter()
        vehicle = FakeVehicle()
        adapter.set_vehicle(vehicle)
        adapter.set_charge_circuit(_VehicleLinkedChargeCircuit(vehicle))
        # 실제 State 는 publish_state 가 처음 돌 때만 생기므로(§__init__ 은 None),
        # 여기서는 게이트가 읽고 쓰는 필드만 갖춘 최소 더블을 직접 준다.
        adapter.state = SimpleNamespace(new_base_request=None)
        return adapter

    def test_request_is_raised_when_base_is_running_short(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        # 큐에 released 노드가 하나만 남았다 = 곧 decision point 다
        adapter._set_new_base_request_from_queue(remaining_released=1)
        self.assertTrue(adapter.state.new_base_request)

    def test_request_is_cleared_when_base_is_long(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter._set_new_base_request_from_queue(remaining_released=5)
        self.assertFalse(adapter.state.new_base_request)

    def test_stop_point_never_requests(self):
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "stop_point"
        adapter._set_new_base_request_from_queue(remaining_released=1)
        self.assertFalse(adapter.state.new_base_request)

    def test_finalize_node_step_wires_the_request_from_the_real_queue(self):
        # 위 세 테스트는 _set_new_base_request_from_queue 단독만 고정한다.
        # _finalize_v3_node_step 이 실제로 그 함수를 부르는지, 그리고 remaining
        # 계수가 order_queue 의 released 노드 개수와 맞는지는 별도로 지켜야
        # 한다 — 안 그러면 배선이 빠져도(혹은 카운트가 엣지까지 세도) 안 걸린다.
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter.state = SimpleNamespace(
            edge_states=[], action_states=[], new_base_request=None
        )
        node = SimpleNamespace(node_id="N2", sequence_id=2)
        step = OrderStep("node", 2, node)
        # 큐에 released 노드 하나(N3)와 엣지 하나만 남겨, 엣지는 세지 않고
        # 노드만 세면 remaining=1 이 되어 decision point 임계치에 걸리게 한다.
        adapter.order_queue.put_nowait(_cp_edge(seq=3))
        adapter.order_queue.put_nowait(_cp_node("N3", seq=4))

        self.assertTrue(adapter._finalize_v3_node_step(step, node))

        self.assertTrue(adapter.state.new_base_request)

    def test_order_complete_clears_stale_new_base_request(self):
        # 오더 마지막 노드에서 true 로 올라간 뒤 그대로 남으면, 오더 없이 대기 중인
        # 로봇이 "곧 base 가 바닥난다"고 거짓 보고하는 셈이 된다(§6.6.3 신호 훼손).
        # _process_v3_order_queue 를 스텁 없이 그대로 돌려 확인한다 — 큐가 이미
        # 비어 있으면 while 루프 없이 바로 "[ORDER COMPLETE]" 분기로 간다.
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter.state = SimpleNamespace(new_base_request=True)

        asyncio.run(adapter._process_v3_order_queue())

        self.assertFalse(adapter.state.new_base_request)

    def test_cancel_clears_stale_new_base_request(self):
        # 취소 경로(_clear_cancelled_order_state 는 cancelOrder 와
        # _drop_order_after_worker_crash 공용)도 같은 이유로 무조건 내려야 한다.
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "continuous"
        adapter.state = SimpleNamespace(
            new_base_request=True,
            order_id="o1",
            order_update_id=1,
            node_states=[],
            edge_states=[],
            action_states=[],
            information=[],
            errors=[],
        )

        adapter._clear_cancelled_order_state()

        self.assertFalse(adapter.state.new_base_request)

    def test_clear_fires_even_under_stop_point(self):
        # 게이트를 걸면 continuous 에서 stop_point 로 설정이 바뀐 채 오더가 끝날 때
        # 이전 오더의 true 가 영원히 안 내려간다. clear 자체는 path_control 과
        # 무관하게 돌아야 한다.
        adapter = self._make_adapter()
        adapter.config.settings.path_control = "stop_point"
        adapter.state = SimpleNamespace(new_base_request=True)

        adapter._clear_new_base_request(reason="test")

        self.assertFalse(adapter.state.new_base_request)


if __name__ == "__main__":
    unittest.main()
