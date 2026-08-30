"""Safe block-style editor for SEER ``recipes.hcl``.

The repository Adapter remains untouched.  This module deliberately exposes a
small allow-list of SEER TCP/IP actions instead of accepting arbitrary Python,
shell commands, or HCL expressions from the browser.
"""

from __future__ import annotations

import html
import base64
import json
import math
import os
import re
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from .block_program import VARIABLE_MARKER, encode_program


_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_VARIABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_BEGIN = "# SEER_BLOCK_RECIPE_BEGIN "
_END = "# SEER_BLOCK_RECIPE_END "
_DEFINITION = "# SEER_BLOCK_DEFINITION "
_VARIABLE_VALUE = re.compile(r"^\$\{var\.([A-Za-z][A-Za-z0-9_]{0,63})\}$")
_DI_CHANNELS = tuple(str(index) for index in range(24))
_DO_CHANNELS = tuple(str(index) for index in range(16))


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    kind: str = "text"
    default: Any = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Tuple[str, ...] = ()
    variable: bool = True
    note: str = ""


@dataclass(frozen=True)
class BlockSpec:
    kind: str
    label: str
    action_type: str
    color: str
    motion: bool
    api: str
    fields: Tuple[FieldSpec, ...]


BLOCK_SPECS: Tuple[BlockSpec, ...] = (
    BlockSpec(
        "coordinate",
        "좌표 이동",
        "seerCoordinateNav",
        "#1677ff",
        True,
        "API 3051 freeGo",
        (
            FieldSpec("x", "X (m)", "number", 0.0, -100000.0, 100000.0),
            FieldSpec("y", "Y (m)", "number", 0.0, -100000.0, 100000.0),
            FieldSpec("theta_deg", "방향각 (deg)", "number", 0.0, -36000.0, 36000.0),
        ),
    ),
    BlockSpec(
        "translate",
        "직선 거리 이동",
        "seerTranslate",
        "#08a88a",
        True,
        "API 3055",
        (
            FieldSpec("distance_m", "거리 (m, 후진은 음수)", "number", 0.1, -1000.0, 1000.0),
            FieldSpec("linear_speed_mps", "선속도 (m/s)", "number", 0.05, 0.01, 0.5),
            FieldSpec("lateral", "이동 축", "choice", "forward", choices=("forward", "lateral")),
        ),
    ),
    BlockSpec(
        "rotate",
        "제자리 회전",
        "seerTurn",
        "#8c5ce6",
        True,
        "API 3056",
        (
            FieldSpec("angle_deg", "회전각 (deg, 우회전은 음수)", "number", 90.0, -36000.0, 36000.0),
            FieldSpec("angular_speed_deg_s", "각속도 (deg/s)", "number", 5.0, 1.0, 60.0),
        ),
    ),
    BlockSpec(
        "path_nav",
        "Path Nav",
        "seerPathNav",
        "#db8b10",
        True,
        "API 3051 / 3066",
        (
            FieldSpec("id", "목적 포인트", "text", "LM1"),
            FieldSpec("source_id", "출발 포인트 (비우면 현재 위치 자동)", "text", ""),
            FieldSpec(
                "route_points",
                "지정 경로",
                "route",
                "",
                note="미니맵에서 실제 연결 경로 선택 가능. 비우면 SEER가 경로 계획",
            ),
            FieldSpec(
                "task_id",
                "Task ID 접두어",
                "text",
                "",
                note="비워도 자동 생성. 입력해도 실행마다 고유 ID를 덧붙임",
            ),
            FieldSpec(
                "waypoint_delay_sec",
                "포인트 간 대기 (초)",
                "number",
                0.0,
                0.0,
                3600.0,
                note=(
                    "기본 0초: 중간 포인트 도착 즉시 다음 포인트로 이동하고 전체 "
                    "호환용 값. 연속 주행에서는 대기 없이 전체 경로를 API 3066 한 번으로 전송"
                ),
            ),
        ),
    ),
    BlockSpec(
        "set_do",
        "신호 보내기",
        "seerSetDO",
        "#d9534f",
        False,
        "API 6001",
        (
            FieldSpec("id", "보낼 DO 신호", "integer", 0, 0, 15, choices=_DO_CHANNELS),
            FieldSpec("status", "출력", "choice", "off", choices=("on", "off")),
        ),
    ),
    BlockSpec(
        "pulse_do",
        "DO 펄스",
        "",
        "#e24a68",
        False,
        "API 6001 조합",
        (
            FieldSpec("id", "보낼 DO 신호", "integer", 0, 0, 15, choices=_DO_CHANNELS),
            FieldSpec("seconds", "유지시간 (초)", "number", 0.5, 0.01, 3600.0),
            FieldSpec("first_status", "먼저 출력", "choice", "on", choices=("on", "off")),
            FieldSpec("final_status", "마지막 출력", "choice", "off", choices=("on", "off")),
        ),
    ),
    BlockSpec(
        "wait_di",
        "DI 신호 기다리기",
        "",
        "#c93c76",
        False,
        "API 1013 · ON/OFF/상승/하강",
        (
            FieldSpec("channel", "기다릴 DI 신호", "integer", 0, 0, 23, choices=_DI_CHANNELS),
            FieldSpec(
                "mode",
                "기다릴 신호",
                "choice",
                "on",
                choices=("on", "off", "rising", "falling"),
                note="rising: OFF→ON · falling: ON→OFF",
            ),
            FieldSpec("timeout_sec", "제한시간 (초)", "number", 30.0, 0.1, 3600.0),
            FieldSpec("poll_interval_sec", "확인 주기 (초)", "number", 0.1, 0.05, 60.0),
        ),
    ),
    BlockSpec(
        "on_di",
        "DI 신호를 받으면",
        "",
        "#b93678",
        False,
        "IO 흐름 · 신호 뒤 내부 블록 실행",
        (
            FieldSpec("channel", "받을 DI 신호", "integer", 0, 0, 23, choices=_DI_CHANNELS),
            FieldSpec(
                "mode",
                "기다릴 신호",
                "choice",
                "rising",
                choices=("on", "off", "rising", "falling"),
            ),
            FieldSpec("timeout_sec", "제한시간 (초)", "number", 30.0, 0.1, 3600.0),
            FieldSpec("poll_interval_sec", "확인 주기 (초)", "number", 0.1, 0.05, 60.0),
        ),
    ),
    BlockSpec(
        "send_do_wait_di",
        "신호 보내고 기다리기",
        "",
        "#cf3f70",
        False,
        "API 6001 전송 후 API 1013 응답 대기",
        (
            FieldSpec("do_id", "보낼 DO 신호", "integer", 0, 0, 15, choices=_DO_CHANNELS),
            FieldSpec("do_status", "보낼 값", "choice", "on", choices=("on", "off")),
            FieldSpec("di_channel", "응답 DI 신호", "integer", 0, 0, 23, choices=_DI_CHANNELS),
            FieldSpec(
                "di_mode",
                "기다릴 응답",
                "choice",
                "rising",
                choices=("on", "off", "rising", "falling"),
            ),
            FieldSpec("timeout_sec", "제한시간 (초)", "number", 30.0, 0.1, 3600.0),
            FieldSpec("poll_interval_sec", "확인 주기 (초)", "number", 0.1, 0.05, 60.0),
            FieldSpec(
                "final_do_status",
                "응답/실패 뒤 DO",
                "choice",
                "off",
                choices=("keep", "on", "off"),
                note="timeout 또는 취소 때도 keep이 아니면 지정 상태로 복구",
            ),
        ),
    ),
    BlockSpec(
        "switch_map",
        "지도 전환",
        "",
        "#287fbd",
        False,
        "API 2022 loadMap",
        (FieldSpec("map_name", "지도 이름", "text", "map1"),),
    ),
    BlockSpec(
        "relocate",
        "위치 초기화",
        "",
        "#446bc1",
        False,
        "API 2002 reloc",
        (
            FieldSpec("mode", "방식", "choice", "auto", choices=("auto", "manual")),
            FieldSpec("x", "X (m, 수동)", "number", 0.0, -100000.0, 100000.0),
            FieldSpec("y", "Y (m, 수동)", "number", 0.0, -100000.0, 100000.0),
            FieldSpec("theta_deg", "방향각 (deg, 수동)", "number", 0.0, -36000.0, 36000.0),
        ),
    ),
    BlockSpec(
        "set_motor",
        "모터 전원",
        "",
        "#317b73",
        False,
        "API 6002 motor",
        (
            FieldSpec("target", "대상", "choice", "all", choices=("all", "named")),
            FieldSpec(
                "motor_name",
                "모터 이름 (named)",
                "text",
                "",
                note="named일 때 SEER 설정의 정확한 모터 이름",
            ),
            FieldSpec("status", "전원", "choice", "on", choices=("on", "off")),
        ),
    ),
    BlockSpec(
        "jack_load",
        "Jack 올리기",
        "seerJackLoad",
        "#7a5af8",
        True,
        "TASK 3051 · jackDoMotor.py / JackLoad",
        (),
    ),
    BlockSpec(
        "jack_unload",
        "Jack 내리기",
        "seerJackUnload",
        "#5b46c9",
        True,
        "TASK 3051 · jackDoMotor.py / JackUnLoadAndResetShelf",
        (),
    ),
    BlockSpec(
        "camera_dock",
        "카메라 도킹",
        "seerCameraDock",
        "#0f766e",
        True,
        "RealSense + AprilTag · 회전중심→마커 법선축 정렬 후 도킹",
        (
            FieldSpec(
                "max_runtime_s",
                "최대 실행시간 (초)",
                "number",
                300.0,
                1.0,
                1800.0,
            ),
        ),
    ),
    BlockSpec(
        "wait",
        "대기",
        "seerWait",
        "#64748b",
        False,
        "로컬 Recipe 단계",
        (FieldSpec("seconds", "대기시간 (초)", "number", 1.0, 0.0, 3600.0),),
    ),
)


_CONDITION_FIELDS: Tuple[FieldSpec, ...] = (
    FieldSpec(
        "source",
        "상태",
        "choice",
        "di",
        choices=(
            "di",
            "emergency",
            "blocked",
            "battery",
            "current_point",
            "charging",
            "motor",
            "localization",
            "variable",
        ),
    ),
    FieldSpec("channel", "DI 신호", "integer", 0, 0, 23, choices=_DI_CHANNELS),
    FieldSpec(
        "variable_name",
        "비교할 변수 이름",
        "variable_ref",
        "value",
        variable=False,
    ),
    FieldSpec(
        "operator",
        "비교",
        "choice",
        "==",
        choices=("==", "!=", ">", ">=", "<", "<="),
    ),
    FieldSpec(
        "expected",
        "비교값",
        "text",
        "1",
        note=(
            "DI/비상/막힘/충전/모터: 1 또는 0 · 배터리: % · "
            "위치신뢰도: 원시값(보통 0~1) · 현재 포인트: LM1 · 변수: 저장된 값"
        ),
    ),
)


CONTROL_SPECS: Tuple[BlockSpec, ...] = (
    BlockSpec(
        "repeat",
        "반복하기",
        "",
        "#f09b2d",
        False,
        "흐름 · 지정 횟수 반복",
        (FieldSpec("count", "반복 횟수", "integer", 2, 0, 100),),
    ),
    BlockSpec(
        "forever",
        "계속 반복하기",
        "",
        "#ed9727",
        False,
        "흐름 · 중단/취소까지 반복 · 안전상 최대 1000 실행",
        (),
    ),
    BlockSpec(
        "repeat_until",
        "조건이 될 때까지 반복하기",
        "",
        "#ed8f24",
        False,
        "흐름 · 조건이 참이면 종료",
        _CONDITION_FIELDS
        + (FieldSpec("max_count", "최대 반복 횟수", "integer", 100, 1, 100),),
    ),
    BlockSpec(
        "if",
        "만일 ~이라면",
        "",
        "#f2c230",
        False,
        "판단 · 조건이 참일 때 실행",
        _CONDITION_FIELDS,
    ),
    BlockSpec(
        "if_else",
        "만일 ~이라면 / 아니면",
        "",
        "#e3ad20",
        False,
        "판단 · 참/거짓 분기",
        _CONDITION_FIELDS,
    ),
    BlockSpec(
        "wait_until",
        "조건까지 기다리기",
        "",
        "#dfb51f",
        False,
        "흐름 · 상태를 주기적으로 확인",
        _CONDITION_FIELDS
        + (
            FieldSpec("timeout_sec", "제한시간 (초)", "number", 30.0, 0.1, 3600.0),
            FieldSpec("poll_interval_sec", "확인 주기 (초)", "number", 0.2, 0.05, 60.0),
        ),
    ),
    BlockSpec(
        "break_loop",
        "현재 반복 중단",
        "",
        "#ef7d27",
        False,
        "흐름 · 가장 가까운 반복 종료",
        (),
    ),
    BlockSpec(
        "continue_loop",
        "이번 반복 건너뛰기",
        "",
        "#e66d25",
        False,
        "흐름 · 가장 가까운 반복의 다음 회차로 이동",
        (),
    ),
    BlockSpec(
        "stop_program",
        "이 코드 멈추기",
        "",
        "#d95c3f",
        False,
        "흐름 · 정상 완료로 종료",
        (),
    ),
    BlockSpec(
        "restart_program",
        "처음부터 다시 실행하기",
        "",
        "#c34a43",
        False,
        "흐름 · 현재 변수값을 유지하고 Recipe 처음으로 이동",
        (),
    ),
    BlockSpec(
        "judge",
        "판단 결과 정하기",
        "",
        "#4f9f55",
        False,
        "판단 · 비교 결과를 1/0 변수에 저장",
        (
            FieldSpec("result_name", "결과 변수", "text", "condition", variable=False),
            FieldSpec("left", "왼쪽 값", "operand", "0", note="값 또는 $변수명"),
            FieldSpec(
                "operator",
                "판단",
                "choice",
                "==",
                choices=("==", "!=", ">", ">=", "<", "<="),
            ),
            FieldSpec("right", "오른쪽 값", "operand", "0", note="값 또는 $변수명"),
        ),
    ),
    BlockSpec(
        "logic",
        "논리 결과 정하기",
        "",
        "#438f51",
        False,
        "판단 · AND/OR/XOR/NOT 결과를 1/0 변수에 저장",
        (
            FieldSpec("result_name", "결과 변수", "text", "logic_result", variable=False),
            FieldSpec("left", "왼쪽 값", "operand", "1", note="1/0, true/false 또는 $변수명"),
            FieldSpec(
                "operator",
                "논리 연산",
                "choice",
                "and",
                choices=("and", "or", "xor", "not"),
            ),
            FieldSpec("right", "오른쪽 값", "operand", "1", note="NOT에서는 사용하지 않음"),
        ),
    ),
    BlockSpec(
        "calculate",
        "계산 결과 정하기",
        "",
        "#2c9d8f",
        False,
        "계산 · 결과를 변수에 저장",
        (
            FieldSpec("result_name", "결과 변수", "text", "result", variable=False),
            FieldSpec("left", "왼쪽 값", "operand", "0", note="숫자 또는 $변수명"),
            FieldSpec(
                "operator",
                "계산",
                "choice",
                "add",
                choices=("add", "subtract", "multiply", "divide", "modulo", "power", "min", "max"),
            ),
            FieldSpec("right", "오른쪽 값", "operand", "0", note="숫자 또는 $변수명"),
        ),
    ),
    BlockSpec(
        "read_state",
        "AMR 상태를 변수에 저장",
        "",
        "#8a58bd",
        False,
        "자료 · 실시간 상태/DI를 읽어 저장",
        (
            FieldSpec(
                "source",
                "읽을 상태",
                "choice",
                "di",
                choices=(
                    "di",
                    "emergency",
                    "blocked",
                    "battery",
                    "current_point",
                    "charging",
                    "motor",
                    "localization",
                ),
            ),
            FieldSpec("channel", "DI 신호", "integer", 0, 0, 23, choices=_DI_CHANNELS),
            FieldSpec("result_name", "저장할 변수", "text", "state", variable=False),
        ),
    ),
    BlockSpec(
        "set_variable",
        "변수 값 정하기",
        "",
        "#b65cc8",
        False,
        "자료 · 실행 중 변수 저장",
        (
            FieldSpec("name", "변수 이름", "text", "value", variable=False),
            FieldSpec("value", "값", "text", "0"),
        ),
    ),
    BlockSpec(
        "change_variable",
        "변수에 더하기",
        "",
        "#9c55c7",
        False,
        "자료 · 숫자 변수 증감",
        (
            FieldSpec("name", "변수 이름", "variable_ref", "value", variable=False),
            FieldSpec("amount", "더할 값", "number", 1.0, -1000000.0, 1000000.0),
        ),
    ),
    BlockSpec(
        "delete_variable",
        "변수 삭제하기",
        "",
        "#8750ad",
        False,
        "자료 · 실행 중 변수 제거",
        (FieldSpec("name", "삭제할 변수", "variable_ref", "value", variable=False),),
    ),
    BlockSpec(
        "log",
        "로그 남기기",
        "",
        "#7657c4",
        False,
        "자료 · seer_client 로그 출력",
        (FieldSpec("message", "내용", "text", "SEER Block"),),
    ),
    BlockSpec(
        "define_function",
        "함수 정의하기",
        "",
        "#5967c5",
        False,
        "함수 · 반복 사용할 블록 묶음 정의",
        (FieldSpec("function_name", "함수 이름", "text", "myFunction", variable=False),),
    ),
    BlockSpec(
        "call_function",
        "함수 실행하기",
        "",
        "#4c59b0",
        False,
        "함수 · 정의된 블록 묶음 호출",
        (FieldSpec("function_name", "실행할 함수", "function_ref", "myFunction", variable=False),),
    ),
)


_BY_KIND = {spec.kind: spec for spec in (*BLOCK_SPECS, *CONTROL_SPECS)}
_CONTAINER_KINDS = frozenset(
    {"repeat", "forever", "repeat_until", "if", "if_else", "on_di", "define_function"}
)
_PROGRAM_ONLY_KINDS = frozenset(
    {
        "pulse_do",
        "wait_di",
        "send_do_wait_di",
        "switch_map",
        "relocate",
        "set_motor",
        "wait_until",
        "break_loop",
        "continue_loop",
        "stop_program",
        "restart_program",
        "judge",
        "logic",
        "calculate",
        "read_state",
        "set_variable",
        "change_variable",
        "delete_variable",
        "log",
        "call_function",
    }
)
_PROGRAM_KINDS = _CONTAINER_KINDS | _PROGRAM_ONLY_KINDS
_MOVEMENT_KINDS = frozenset({"coordinate", "translate", "rotate", "path_nav"})
_FLOW_KINDS = frozenset(
    {
        "wait",
        "repeat",
        "forever",
        "repeat_until",
        "wait_until",
        "break_loop",
        "continue_loop",
        "stop_program",
        "restart_program",
    }
)
_IO_KINDS = frozenset({"set_do", "pulse_do", "wait_di", "on_di", "send_do_wait_di"})
_SYSTEM_KINDS = frozenset({"switch_map", "relocate", "set_motor", "jack_load", "jack_unload", "camera_dock"})
_LOGIC_KINDS = frozenset({"if", "if_else", "judge", "logic"})
_MATH_KINDS = frozenset({"calculate"})
_FUNCTION_KINDS = frozenset({"define_function", "call_function"})


BUILDER_EXAMPLES: Tuple[Dict[str, Any], ...] = (
    {
        "key": "simulator_round_trip",
        "title": "시뮬레이터 지정 경로 왕복",
        "description": "위쪽 경로로 이동하고 회전한 뒤 아래쪽 경로로 복귀합니다.",
        "tags": ("Path Nav", "대기", "회전"),
        "note": "SEER 기본 시뮬레이터의 SIM_START/SIM_GOAL 포인트용입니다.",
        "definition": {
            "name": "seerExampleRoundTrip",
            "label": "SEER Example - Simulator Round Trip",
            "blocks": [
                {
                    "kind": "path_nav",
                    "layout": {"x": 8, "y": 42},
                    "values": {
                        "id": "SIM_GOAL",
                        "source_id": "SIM_START",
                        "route_points": "SIM_START, SIM_UPPER, SIM_GOAL",
                        "task_id": "example-outbound",
                        "waypoint_delay_sec": 0,
                    },
                    "variables": {},
                    "delay_sec": 0,
                },
                {
                    "kind": "wait",
                    "layout": {"x": 8, "y": 84, "connected": True},
                    "values": {"seconds": 0.2},
                    "variables": {},
                    "delay_sec": 0,
                },
                {
                    "kind": "rotate",
                    "layout": {"x": 8, "y": 126, "connected": True},
                    "values": {"angle_deg": 180, "angular_speed_deg_s": 5},
                    "variables": {},
                    "delay_sec": 0,
                },
                {
                    "kind": "path_nav",
                    "layout": {"x": 8, "y": 168, "connected": True},
                    "values": {
                        "id": "SIM_START",
                        "source_id": "SIM_GOAL",
                        "route_points": "SIM_GOAL, SIM_LOWER, SIM_START",
                        "task_id": "example-return",
                        "waypoint_delay_sec": 0,
                    },
                    "variables": {},
                    "delay_sec": 0,
                },
            ],
        },
    },
    {
        "key": "facility_handshake",
        "title": "설비 신호 확인 후 이동",
        "description": "DO 요청을 보내고 DI 완료 신호를 기다린 뒤 지정 포인트로 이동합니다.",
        "tags": ("DO", "DI", "Path Nav"),
        "note": "실제 장비의 DO/DI 번호와 LM1 목적지를 반드시 확인해 바꾸세요.",
        "definition": {
            "name": "seerExampleFacilityHandshake",
            "label": "SEER Example - Facility Handshake",
            "blocks": [
                {
                    "kind": "send_do_wait_di",
                    "layout": {"x": 8, "y": 42},
                    "values": {
                        "do_id": 0,
                        "do_status": "on",
                        "di_channel": 0,
                        "di_mode": "rising",
                        "timeout_sec": 30,
                        "poll_interval_sec": 0.1,
                        "final_do_status": "off",
                    },
                    "variables": {},
                    "delay_sec": 0,
                },
                {
                    "kind": "path_nav",
                    "layout": {"x": 8, "y": 84, "connected": True},
                    "values": {
                        "id": "LM1",
                        "source_id": "SELF_POSITION",
                        "route_points": "",
                        "task_id": "facility-move",
                        "waypoint_delay_sec": 0,
                    },
                    "variables": {},
                    "delay_sec": 0,
                },
                {
                    "kind": "pulse_do",
                    "layout": {"x": 8, "y": 126, "connected": True},
                    "values": {
                        "id": 1,
                        "seconds": 0.5,
                        "first_status": "on",
                        "final_status": "off",
                    },
                    "variables": {},
                    "delay_sec": 0,
                },
            ],
        },
    },
    {
        "key": "battery_guarded_move",
        "title": "배터리 조건 분기 이동",
        "description": "배터리가 30% 이상일 때만 이동하고, 부족하면 로그를 남깁니다.",
        "tags": ("조건문", "배터리", "안전"),
        "note": "목적지 LM1은 실제 지도 포인트로 변경하세요.",
        "definition": {
            "name": "seerExampleBatteryGuard",
            "label": "SEER Example - Battery Guard",
            "blocks": [
                {
                    "kind": "if_else",
                    "layout": {"x": 8, "y": 42},
                    "values": {
                        "source": "battery",
                        "channel": 0,
                        "variable_name": "value",
                        "operator": ">=",
                        "expected": "30",
                    },
                    "variables": {},
                    "delay_sec": 0,
                    "children": [
                        {
                            "kind": "path_nav",
                            "values": {
                                "id": "LM1",
                                "source_id": "SELF_POSITION",
                                "route_points": "",
                                "task_id": "battery-guard",
                                "waypoint_delay_sec": 0,
                            },
                            "variables": {},
                            "delay_sec": 0,
                        }
                    ],
                    "else_children": [
                        {
                            "kind": "log",
                            "values": {"message": "배터리 부족으로 이동을 시작하지 않습니다."},
                            "variables": {},
                            "delay_sec": 0,
                        }
                    ],
                }
            ],
        },
    },
    {
        "key": "counter_until_four",
        "title": "result가 4가 될 때까지 반복",
        "description": "result를 0으로 시작하고 반복문 안에서 1씩 더해 result = 4가 되면 자동 종료합니다.",
        "tags": ("변수", "반복문", "판단"),
        "note": "엔트리처럼 변수 정하기 → 조건 반복 → 변수에 더하기 순서로 조립하는 기본 예제입니다.",
        "definition": {
            "name": "seerExampleCounterUntilFour",
            "label": "SEER Example - Counter Until Four",
            "blocks": [
                {
                    "kind": "set_variable",
                    "layout": {"x": 8, "y": 42},
                    "values": {"name": "result", "value": "0"},
                    "variables": {},
                    "delay_sec": 0,
                },
                {
                    "kind": "repeat_until",
                    "layout": {"x": 8, "y": 84, "connected": True},
                    "values": {
                        "source": "variable",
                        "channel": "0",
                        "variable_name": "result",
                        "operator": "==",
                        "expected": "4",
                        "max_count": "10",
                    },
                    "variables": {},
                    "delay_sec": 0,
                    "children": [
                        {
                            "kind": "change_variable",
                            "values": {"name": "result", "amount": "1"},
                            "variables": {},
                            "delay_sec": 0,
                        }
                    ],
                },
            ],
        },
    },
    {
        "key": "function_repeat",
        "title": "함수 정의 후 반복 실행",
        "description": "이동 동작을 DemoMotion 함수로 정의하고 전체 흐름에서 두 번 호출합니다.",
        "tags": ("함수", "반복문", "변수"),
        "note": "반복 횟수는 실행 변수 repeat_count로 노출되어 Actions에서 바꿀 수 있습니다.",
        "definition": {
            "name": "seerExampleFunctionLoop",
            "label": "SEER Example - Function Loop",
            "blocks": [
                {
                    "kind": "define_function",
                    "layout": {"x": 8, "y": 42},
                    "values": {"function_name": "DemoMotion"},
                    "variables": {},
                    "delay_sec": 0,
                    "children": [
                        {
                            "kind": "translate",
                            "values": {
                                "distance_m": 0.1,
                                "linear_speed_mps": 0.05,
                                "lateral": "forward",
                            },
                            "variables": {},
                            "delay_sec": 0,
                        },
                        {
                            "kind": "rotate",
                            "values": {"angle_deg": 90, "angular_speed_deg_s": 5},
                            "variables": {},
                            "delay_sec": 0,
                        },
                    ],
                },
                {
                    "kind": "repeat",
                    "layout": {"x": 8, "y": 42},
                    "values": {"count": 2},
                    "variables": {"count": "repeat_count"},
                    "delay_sec": 0,
                    "children": [
                        {
                            "kind": "call_function",
                            "values": {"function_name": "DemoMotion"},
                            "variables": {},
                            "delay_sec": 0,
                        },
                        {
                            "kind": "wait",
                            "values": {"seconds": 0.2},
                            "variables": {},
                            "delay_sec": 0,
                        },
                    ],
                },
            ],
        },
    },
)


def _spec_category(spec: BlockSpec) -> str:
    if spec.kind in _MOVEMENT_KINDS:
        return "movement"
    if spec.kind in _FLOW_KINDS:
        return "flow"
    if spec.kind in _IO_KINDS:
        return "io"
    if spec.kind in _LOGIC_KINDS:
        return "logic"
    if spec.kind in _MATH_KINDS:
        return "math"
    if spec.kind in _FUNCTION_KINDS:
        return "function"
    if spec.kind in _SYSTEM_KINDS:
        return "system"
    return "data"


class RecipeBuilderError(ValueError):
    """The submitted block program is unsafe or malformed."""


def _number(value: Any, field: FieldSpec) -> Any:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise RecipeBuilderError(f"{field.label}: 숫자를 입력하세요") from exc
    if not math.isfinite(numeric):
        raise RecipeBuilderError(f"{field.label}: 유한한 숫자만 사용할 수 있습니다")
    if field.minimum is not None and numeric < field.minimum:
        raise RecipeBuilderError(f"{field.label}: 최솟값은 {field.minimum:g}입니다")
    if field.maximum is not None and numeric > field.maximum:
        raise RecipeBuilderError(f"{field.label}: 최댓값은 {field.maximum:g}입니다")
    if field.kind == "integer":
        if not numeric.is_integer():
            raise RecipeBuilderError(f"{field.label}: 정수만 사용할 수 있습니다")
        return int(numeric)
    return numeric


def _fixed_value(value: Any, field: FieldSpec) -> Any:
    if value in (None, ""):
        value = field.default
    if field.kind in ("number", "integer"):
        numeric = _number(value, field)
        if field.choices and str(numeric) not in field.choices:
            raise RecipeBuilderError(
                f"{field.label}: {', '.join(field.choices)} 중 하나를 선택하세요"
            )
        return numeric
    text = str(value).strip()
    if len(text) > 1024:
        raise RecipeBuilderError(f"{field.label}: 입력이 너무 깁니다")
    if field.kind == "choice":
        if text not in field.choices:
            raise RecipeBuilderError(
                f"{field.label}: {', '.join(field.choices)} 중 하나를 선택하세요"
            )
        return text
    if field.kind == "route":
        if not text:
            return ""
        points = [part.strip() for part in text.replace("→", ",").split(",")]
        points = [point for point in points if point]
        if len(points) < 2:
            raise RecipeBuilderError("지정 경로는 포인트를 두 개 이상 입력하세요")
        if len(points) > 32 or any(len(point) > 128 for point in points):
            raise RecipeBuilderError("지정 경로는 최대 32개, 포인트명은 최대 128자입니다")
        if any(points[index] == points[index - 1] for index in range(1, len(points))):
            raise RecipeBuilderError("지정 경로에 연속 중복 포인트가 있습니다")
        # Keep a designated route as an HCL/JSON list.  Encoding the list as a
        # quoted JSON string leaves the quote escapes intact in python-hcl2
        # (``[\\\"LM1\\\",...]``), so the SEER action cannot parse it.
        return points
    return text


def _step_delay(value: Any, index: int) -> float:
    """Validate the optional pause after one successful Recipe step."""

    if value in (None, ""):
        return 0.0
    try:
        delay = float(value)
    except (TypeError, ValueError) as exc:
        raise RecipeBuilderError(
            f"{index}번 블록 완료 후 대기시간은 숫자로 입력하세요"
        ) from exc
    if not math.isfinite(delay) or not 0.0 <= delay <= 3600.0:
        raise RecipeBuilderError(
            f"{index}번 블록 완료 후 대기시간은 0..3600초여야 합니다"
        )
    return delay


def _requires_program(nodes: Any) -> bool:
    if not isinstance(nodes, list):
        return False
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        if isinstance(node.get("layout"), Mapping):
            return True
        if str(node.get("kind", "")) in _PROGRAM_KINDS:
            return True
        if _requires_program(node.get("children")) or _requires_program(
            node.get("else_children")
        ):
            return True
    return False


def _program_value(fixed: Any, variable_name: str) -> Any:
    if not variable_name:
        return fixed
    return {VARIABLE_MARKER: variable_name, "default": fixed}


def _parse_structured_definition(
    data: Mapping[str, Any],
    *,
    name: str,
    label: str,
) -> Dict[str, Any]:
    variable_names = set()
    function_names = set()
    called_functions = set()
    node_count = 0

    def parse_nodes(raw_nodes: Any, path: str, depth: int):
        nonlocal node_count
        if not isinstance(raw_nodes, list):
            raise RecipeBuilderError(f"{path} 블록 목록 형식이 잘못되었습니다")
        if depth > 8:
            raise RecipeBuilderError("블록 중첩은 최대 8단계입니다")
        normalized = []
        program = []
        motion = False
        for position, raw_block in enumerate(raw_nodes, start=1):
            node_count += 1
            if node_count > 100:
                raise RecipeBuilderError("Recipe 하나에는 중첩 포함 블록을 최대 100개 저장할 수 있습니다")
            if not isinstance(raw_block, Mapping):
                raise RecipeBuilderError(f"{path} {position}번 블록 형식이 잘못되었습니다")
            kind = str(raw_block.get("kind", ""))
            spec = _BY_KIND.get(kind)
            if spec is None:
                raise RecipeBuilderError(f"지원하지 않는 블록입니다: {kind or '(없음)'}")
            raw_values = raw_block.get("values", {})
            raw_variables = raw_block.get("variables", {})
            if not isinstance(raw_values, Mapping) or not isinstance(raw_variables, Mapping):
                raise RecipeBuilderError(f"{path} {position}번 블록의 입력 형식이 잘못되었습니다")
            values: Dict[str, Any] = {}
            variables: Dict[str, str] = {}
            executable: Dict[str, Any] = {}
            for field in spec.fields:
                fixed = _fixed_value(raw_values.get(field.name), field)
                variable_name = str(raw_variables.get(field.name, "") or "").strip()
                if variable_name:
                    if not field.variable:
                        raise RecipeBuilderError(f"{field.label}은 변수로 만들 수 없습니다")
                    if not _VARIABLE.fullmatch(variable_name):
                        raise RecipeBuilderError(
                            f"{field.label} 변수명은 영문으로 시작하는 영문/숫자/_만 가능합니다"
                        )
                    variable_names.add(variable_name)
                    variables[field.name] = variable_name
                values[field.name] = _ui_value(field, fixed)
                executable[field.name] = _program_value(fixed, variable_name)
            delay_sec = _step_delay(raw_block.get("delay_sec"), node_count)
            normalized_node: Dict[str, Any] = {
                "kind": kind,
                "values": values,
                "variables": variables,
                "delay_sec": delay_sec,
            }
            raw_layout = raw_block.get("layout")
            if raw_layout is not None:
                if not isinstance(raw_layout, Mapping):
                    raise RecipeBuilderError(f"{path} {position}번 블록의 배치 정보가 잘못되었습니다")
                try:
                    layout_x = float(raw_layout.get("x", 0.0))
                    layout_y = float(raw_layout.get("y", 0.0))
                except (TypeError, ValueError) as exc:
                    raise RecipeBuilderError(
                        f"{path} {position}번 블록의 배치 좌표는 숫자여야 합니다"
                    ) from exc
                if not all(math.isfinite(value) and 0.0 <= value <= 10000.0 for value in (layout_x, layout_y)):
                    raise RecipeBuilderError(
                        f"{path} {position}번 블록의 배치 좌표는 0..10000이어야 합니다"
                    )
                layout_connected = raw_layout.get("connected", False)
                if not isinstance(layout_connected, bool):
                    raise RecipeBuilderError(
                        f"{path} {position}번 블록의 연결 정보가 잘못되었습니다"
                    )
                normalized_node["layout"] = {"x": layout_x, "y": layout_y}
                if layout_connected:
                    normalized_node["layout"]["connected"] = True
            target_field = {
                "set_variable": "name",
                "change_variable": "name",
                "delete_variable": "name",
                "judge": "result_name",
                "logic": "result_name",
                "calculate": "result_name",
                "read_state": "result_name",
            }.get(kind)
            if target_field:
                variable_name = str(executable[target_field]).strip()
                if not _VARIABLE.fullmatch(variable_name):
                    raise RecipeBuilderError(
                        f"{spec.label} 변수명은 영문으로 시작하는 영문/숫자/_만 가능합니다"
                    )
                executable[target_field] = variable_name
            if kind == "define_function":
                if depth != 0:
                    raise RecipeBuilderError("함수 정의 블록은 최상위 조립 공간에 두세요")
                function_name = str(executable["function_name"]).strip()
                if not _VARIABLE.fullmatch(function_name):
                    raise RecipeBuilderError("함수 이름은 영문으로 시작하는 영문/숫자/_만 가능합니다")
                if function_name in function_names:
                    raise RecipeBuilderError(f"함수 이름이 중복되었습니다: {function_name}")
                function_names.add(function_name)
                executable["function_name"] = function_name
            elif kind == "call_function":
                function_name = str(executable["function_name"]).strip()
                if not _VARIABLE.fullmatch(function_name):
                    raise RecipeBuilderError("실행할 함수 이름 형식이 잘못되었습니다")
                called_functions.add(function_name)
                executable["function_name"] = function_name
            if kind in _CONTAINER_KINDS:
                children, child_program, child_motion = parse_nodes(
                    raw_block.get("children", []), f"{path} {position}번 내부", depth + 1
                )
                else_children = []
                else_program = []
                else_motion = False
                if kind == "if_else":
                    else_children, else_program, else_motion = parse_nodes(
                        raw_block.get("else_children", []),
                        f"{path} {position}번 아니면 내부",
                        depth + 1,
                    )
                normalized_node["children"] = children
                if kind == "if_else":
                    normalized_node["else_children"] = else_children
                if kind == "repeat":
                    program_node = {
                        "kind": "repeat",
                        "count": executable["count"],
                        "children": child_program,
                        "delay_sec": delay_sec,
                    }
                elif kind == "forever":
                    if not child_program:
                        raise RecipeBuilderError("계속 반복하기 블록 안에 실행할 블록을 넣으세요")
                    program_node = {
                        "kind": "forever",
                        "children": child_program,
                        "delay_sec": delay_sec,
                    }
                elif kind == "repeat_until":
                    program_node = {
                        "kind": "repeat_until",
                        "condition": {
                            "source": executable["source"],
                            "channel": executable["channel"],
                            "variable_name": executable["variable_name"],
                            "operator": executable["operator"],
                            "expected": executable["expected"],
                        },
                        "max_count": executable["max_count"],
                        "children": child_program,
                        "delay_sec": delay_sec,
                    }
                elif kind == "on_di":
                    program_node = {
                        "kind": "on_di",
                        "parameters": executable,
                        "children": child_program,
                        "delay_sec": delay_sec,
                    }
                elif kind == "define_function":
                    program_node = {
                        "kind": "define_function",
                        "parameters": executable,
                        "children": child_program,
                        "delay_sec": 0.0,
                    }
                else:
                    program_node = {
                        "kind": kind,
                        "condition": {
                            "source": executable["source"],
                            "channel": executable["channel"],
                            "variable_name": executable["variable_name"],
                            "operator": executable["operator"],
                            "expected": executable["expected"],
                        },
                        "children": child_program,
                        "else_children": else_program,
                        "delay_sec": delay_sec,
                    }
                motion = motion or child_motion or else_motion
            elif kind == "wait_until":
                program_node = {
                    "kind": "wait_until",
                    "condition": {
                        "source": executable["source"],
                        "channel": executable["channel"],
                        "variable_name": executable["variable_name"],
                        "operator": executable["operator"],
                        "expected": executable["expected"],
                    },
                    "timeout_sec": executable["timeout_sec"],
                    "poll_interval_sec": executable["poll_interval_sec"],
                    "delay_sec": delay_sec,
                }
            elif kind in _PROGRAM_ONLY_KINDS:
                program_node = {
                    "kind": kind,
                    "parameters": executable,
                    "delay_sec": delay_sec,
                }
            else:
                program_node = {
                    "kind": kind,
                    "action_type": spec.action_type,
                    "parameters": executable,
                    "delay_sec": delay_sec,
                }
                motion = motion or spec.motion
            normalized.append(normalized_node)
            program.append(program_node)
        return normalized, program, motion

    normalized, program, motion = parse_nodes(data.get("blocks"), "최상위", 0)
    if not normalized:
        raise RecipeBuilderError("블록을 하나 이상 추가하세요")
    missing_functions = sorted(called_functions - function_names)
    if missing_functions:
        raise RecipeBuilderError(
            "정의되지 않은 함수를 실행할 수 없습니다: " + ", ".join(missing_functions)
        )
    definition = {"name": name, "label": label, "blocks": normalized}
    return {
        "name": name,
        "label": label,
        "blocks": (),
        "motion": motion,
        "variables": tuple(sorted(variable_names)),
        "structured": True,
        "definition": definition,
        "program": tuple(program),
    }


def _parse_definition(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RecipeBuilderError("블록 데이터를 읽을 수 없습니다") from exc
    if not isinstance(data, Mapping):
        raise RecipeBuilderError("블록 데이터는 object여야 합니다")
    name = str(data.get("name", "")).strip()
    if not _NAME.fullmatch(name):
        raise RecipeBuilderError("Recipe 이름은 영문으로 시작하는 영문/숫자/_ 1~64자여야 합니다")
    label = str(data.get("label", "")).strip() or name
    if len(label) > 120:
        raise RecipeBuilderError("표시 이름은 최대 120자입니다")
    raw_blocks = data.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise RecipeBuilderError("블록을 하나 이상 추가하세요")
    if _requires_program(raw_blocks):
        return _parse_structured_definition(data, name=name, label=label)
    if len(raw_blocks) > 50:
        raise RecipeBuilderError("Recipe 하나에는 블록을 최대 50개까지 저장할 수 있습니다")

    blocks = []
    variable_names = set()
    for index, raw_block in enumerate(raw_blocks, start=1):
        if not isinstance(raw_block, Mapping):
            raise RecipeBuilderError(f"{index}번 블록 형식이 잘못되었습니다")
        kind = str(raw_block.get("kind", ""))
        spec = _BY_KIND.get(kind)
        if spec is None:
            raise RecipeBuilderError(f"지원하지 않는 블록입니다: {kind or '(없음)'}")
        raw_values = raw_block.get("values", {})
        raw_variables = raw_block.get("variables", {})
        if not isinstance(raw_values, Mapping) or not isinstance(raw_variables, Mapping):
            raise RecipeBuilderError(f"{index}번 블록의 입력 형식이 잘못되었습니다")
        parameters = {}
        for field in spec.fields:
            fixed = _fixed_value(raw_values.get(field.name), field)
            variable_name = str(raw_variables.get(field.name, "") or "").strip()
            if variable_name:
                if not field.variable:
                    raise RecipeBuilderError(f"{field.label}은 변수로 만들 수 없습니다")
                if not _VARIABLE.fullmatch(variable_name):
                    raise RecipeBuilderError(
                        f"{field.label} 변수명은 영문으로 시작하는 영문/숫자/_만 가능합니다"
                    )
                variable_names.add(variable_name)
                parameters[field.name] = f"${{var.{variable_name}}}"
                parameters[f"default_{field.name}"] = fixed
            else:
                parameters[field.name] = fixed
        blocks.append((spec, parameters, _step_delay(raw_block.get("delay_sec"), index)))
    return {
        "name": name,
        "label": label,
        "blocks": tuple(blocks),
        "motion": any(spec.motion for spec, _params, _delay in blocks),
        "variables": tuple(sorted(variable_names)),
        "structured": False,
    }


def _hcl_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".12g")
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(str(value), ensure_ascii=False)


def _ui_value(field: FieldSpec, value: Any) -> Any:
    """Convert a normalized HCL parameter back into an editor field value."""

    if field.kind == "route" and value:
        points = value
        if isinstance(value, str):
            try:
                points = json.loads(value)
            except json.JSONDecodeError:
                try:
                    points = json.loads(value.replace('\\\"', '"'))
                except json.JSONDecodeError:
                    return value
        if isinstance(points, (list, tuple)) and all(
            isinstance(point, str) for point in points
        ):
            return ", ".join(points)
    return value


def _definition_from_recipe(recipe: Mapping[str, Any]) -> Dict[str, Any]:
    if recipe.get("structured"):
        return dict(recipe["definition"])
    blocks = []
    for spec, parameters, delay_sec in recipe["blocks"]:
        values: Dict[str, Any] = {}
        variables: Dict[str, str] = {}
        for field in spec.fields:
            value = parameters[field.name]
            match = _VARIABLE_VALUE.fullmatch(value) if isinstance(value, str) else None
            if match:
                variables[field.name] = match.group(1)
                value = parameters.get(f"default_{field.name}", field.default)
            values[field.name] = _ui_value(field, value)
        blocks.append(
            {
                "kind": spec.kind,
                "values": values,
                "variables": variables,
                "delay_sec": delay_sec,
            }
        )
    return {
        "name": recipe["name"],
        "label": recipe["label"],
        "blocks": blocks,
    }


def _encode_definition(recipe: Mapping[str, Any]) -> str:
    raw = json.dumps(
        _definition_from_recipe(recipe),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def build_recipe_hcl(raw_definition: str) -> Tuple[str, Dict[str, Any]]:
    """Validate browser JSON and return one marker-wrapped Recipe block."""

    recipe = _parse_definition(raw_definition)
    name = recipe["name"]
    lines = [
        f"{_BEGIN}{name}",
        f"{_DEFINITION}{_encode_definition(recipe)}",
        f'recipe "{name}" {{',
        "  enabled = true",
        f"  motion = {'true' if recipe['motion'] else 'false'}",
        f"  label = {_hcl_literal(recipe['label'])}",
        "  timeout_sec = 0",
        "  cleanup_timeout_sec = 5",
    ]
    if recipe.get("structured"):
        encoded_program = encode_program(recipe["program"])
        # declared_variables is not used by the nested executor.  Its safe HCL
        # placeholders let the unchanged Adapter/WebUI discover inputs, while
        # bridge.py replaces this Recipe's linear handler with the structured
        # handler that applies stored defaults when a caller omits a value.
        declared = [f"${{var.{name}}}" for name in recipe["variables"]]
        lines.extend(
            (
                "",
                '  step "seerBlockProgram" {',
                "    parameters = {",
                f"      program_b64 = {_hcl_literal(encoded_program)}",
                f"      declared_variables = {_hcl_literal(declared)}",
                "    }",
                "    timeout_sec = 0",
                "    delay_sec = 0",
                "  }",
            )
        )
    else:
        for spec, parameters, delay_sec in recipe["blocks"]:
            lines.extend(("", f'  step "{spec.action_type}" {{', "    parameters = {"))
            for key, value in parameters.items():
                lines.append(f"      {key} = {_hcl_literal(value)}")
            lines.extend(
                (
                    "    }",
                    "    timeout_sec = 0",
                    f"    delay_sec = {_hcl_literal(delay_sec)}",
                    "  }",
                )
            )
    lines.extend(("}", f"{_END}{name}", ""))
    return "\n".join(lines), recipe


def _managed_span(text: str, name: str) -> Optional[Tuple[int, int]]:
    begin = f"{_BEGIN}{name}"
    end = f"{_END}{name}"
    start = text.find(begin)
    if start < 0:
        return None
    finish_marker = text.find(end, start + len(begin))
    if finish_marker < 0:
        raise RecipeBuilderError(f"기존 {name} 블록의 종료 마커가 없습니다")
    finish = finish_marker + len(end)
    while finish < len(text) and text[finish] in "\r\n":
        finish += 1
    return start, finish


def managed_recipe_names(text: str) -> Tuple[str, ...]:
    names = []
    for line in text.splitlines():
        if not line.startswith(_BEGIN):
            continue
        name = line[len(_BEGIN) :].strip()
        if _NAME.fullmatch(name) and name not in names:
            names.append(name)
    return tuple(names)


def _parse_hcl_literal(raw: str) -> Any:
    value = raw.strip()
    if value in ("true", "false"):
        return value == "true"
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        try:
            return float(value) if any(char in value.lower() for char in (".", "e")) else int(value)
        except ValueError as exc:
            raise RecipeBuilderError("기존 블록 Recipe의 파라미터를 읽을 수 없습니다") from exc


def _legacy_definition(block: str, name: str) -> Dict[str, Any]:
    """Recover definitions written before embedded builder metadata existed."""

    label_match = re.search(r"^\s*label\s*=\s*(.+)$", block, re.M)
    label = _parse_hcl_literal(label_match.group(1)) if label_match else name
    action_to_spec = {spec.action_type: spec for spec in BLOCK_SPECS if spec.action_type}
    lines = block.splitlines()
    blocks = []
    index = 0
    while index < len(lines):
        step_match = re.match(r'^\s*step\s+"([^"]+)"\s*\{\s*$', lines[index])
        if not step_match:
            index += 1
            continue
        spec = action_to_spec.get(step_match.group(1))
        if spec is None:
            raise RecipeBuilderError(f"{name}에 편집기가 지원하지 않는 단계가 있습니다")
        index += 1
        while index < len(lines) and not re.match(r"^\s*parameters\s*=\s*\{\s*$", lines[index]):
            index += 1
        if index >= len(lines):
            raise RecipeBuilderError(f"{name}의 parameters 블록을 읽을 수 없습니다")
        index += 1
        parameters: Dict[str, Any] = {}
        while index < len(lines) and lines[index].strip() != "}":
            parameter = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.+)$", lines[index])
            if parameter:
                parameters[parameter.group(1)] = _parse_hcl_literal(parameter.group(2))
            index += 1
        delay_sec = 0.0
        probe = index + 1
        while probe < len(lines) and lines[probe].strip() != "}":
            delay_match = re.match(r"^\s*delay_sec\s*=\s*(.+)$", lines[probe])
            if delay_match:
                parsed_delay = _parse_hcl_literal(delay_match.group(1))
                delay_sec = _step_delay(parsed_delay, len(blocks) + 1)
            probe += 1
        values: Dict[str, Any] = {}
        variables: Dict[str, str] = {}
        for field in spec.fields:
            value = parameters.get(field.name, field.default)
            match = _VARIABLE_VALUE.fullmatch(value) if isinstance(value, str) else None
            if match:
                variables[field.name] = match.group(1)
                value = parameters.get(f"default_{field.name}", field.default)
            values[field.name] = _ui_value(field, value)
        blocks.append(
            {
                "kind": spec.kind,
                "values": values,
                "variables": variables,
                "delay_sec": delay_sec,
            }
        )
        index = probe + 1
    if not blocks:
        raise RecipeBuilderError(f"{name}에 편집 가능한 블록이 없습니다")
    return {"name": name, "label": str(label), "blocks": blocks}


def managed_recipe_definition(text: str, name: str) -> Dict[str, Any]:
    """Return one validated builder definition, never a hand-written Recipe."""

    if not _NAME.fullmatch(name):
        raise RecipeBuilderError("잘못된 Recipe 이름입니다")
    span = _managed_span(text, name)
    if span is None:
        raise RecipeBuilderError(f"{name}은 Block Builder 관리 Recipe가 아닙니다")
    block = text[span[0] : span[1]]
    encoded = next(
        (
            line[len(_DEFINITION) :].strip()
            for line in block.splitlines()
            if line.startswith(_DEFINITION)
        ),
        "",
    )
    if encoded:
        try:
            decoded = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
            definition = json.loads(decoded)
            parsed = _parse_definition(json.dumps(definition, ensure_ascii=False))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise RecipeBuilderError(f"{name}의 저장된 블록 정보를 읽을 수 없습니다") from exc
        if parsed["name"] != name:
            raise RecipeBuilderError(f"{name}의 블록 정보 이름이 일치하지 않습니다")
        return _definition_from_recipe(parsed)
    return _legacy_definition(block, name)


def merge_recipe(
    text: str,
    raw_definition: str,
    *,
    original_name: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    block, recipe = build_recipe_hcl(raw_definition)
    if original_name is not None:
        original_name = str(original_name).strip()
        if not _NAME.fullmatch(original_name):
            raise RecipeBuilderError("변경 전 Recipe 이름이 잘못되었습니다")
        original_span = _managed_span(text, original_name)
        if original_span is None:
            raise RecipeBuilderError(
                f"{original_name}은 이름을 변경할 수 있는 Block Builder 관리 Recipe가 아닙니다"
            )
        if recipe["name"] != original_name:
            if _managed_span(text, recipe["name"]) is not None:
                raise RecipeBuilderError(
                    f"{recipe['name']} 이름의 Block Builder Recipe가 이미 있습니다"
                )
            pattern = re.compile(
                r'^\s*recipe\s+"' + re.escape(recipe["name"]) + r'"\s*\{', re.M
            )
            if pattern.search(text):
                raise RecipeBuilderError(
                    f"{recipe['name']}은 원문 HCL에 이미 있습니다. 다른 이름을 사용하세요"
                )
        return text[: original_span[0]] + block + text[original_span[1] :], recipe

    span = _managed_span(text, recipe["name"])
    if span is not None:
        merged = text[: span[0]] + block + text[span[1] :]
    else:
        # Refuse to shadow a hand-written Recipe with the same name.
        pattern = re.compile(r'^\s*recipe\s+"' + re.escape(recipe["name"]) + r'"\s*\{', re.M)
        if pattern.search(text):
            raise RecipeBuilderError(
                f"{recipe['name']}은 원문 HCL에 이미 있습니다. Block Builder가 만든 Recipe만 덮어쓸 수 있습니다"
            )
        merged = text.rstrip() + "\n\n" + block
    return merged, recipe


def _atomic_validated_write(
    target: Path,
    content: str,
    *,
    validate: Optional[Callable[[Path], Tuple[bool, str]]] = None,
) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix="seer-recipes-",
            suffix=".hcl",
            dir=target.parent,
            delete=False,
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        if validate is not None:
            ok, message = validate(temporary)
            if not ok:
                raise RecipeBuilderError(f"Adapter 검증 실패: {message}")
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def save_recipe(
    target: Path,
    raw_definition: str,
    *,
    original_name: Optional[str] = None,
    validate: Optional[Callable[[Path], Tuple[bool, str]]] = None,
) -> Dict[str, Any]:
    """Atomically create, update, or rename a managed block after validation."""

    target = Path(target)
    original = target.read_text(encoding="utf-8")
    merged, recipe = merge_recipe(
        original,
        raw_definition,
        original_name=original_name,
    )
    _atomic_validated_write(target, merged, validate=validate)
    return recipe


def delete_recipe(
    target: Path,
    name: str,
    *,
    validate: Optional[Callable[[Path], Tuple[bool, str]]] = None,
) -> str:
    """Delete only a marker-managed Recipe and validate the remaining HCL."""

    if not _NAME.fullmatch(name):
        raise RecipeBuilderError("잘못된 Recipe 이름입니다")
    target = Path(target)
    original = target.read_text(encoding="utf-8")
    span = _managed_span(original, name)
    if span is None:
        raise RecipeBuilderError(f"{name}은 Block Builder 관리 Recipe가 아닙니다")
    merged = (original[: span[0]].rstrip() + "\n\n" + original[span[1] :].lstrip()).rstrip() + "\n"
    _atomic_validated_write(target, merged, validate=validate)
    return name


def _schema_json() -> str:
    value = []
    for spec in (*BLOCK_SPECS, *CONTROL_SPECS):
        category = _spec_category(spec)
        value.append(
            {
                "kind": spec.kind,
                "label": spec.label,
                "api": spec.api,
                "color": spec.color,
                "motion": spec.motion,
                "category": category,
                "container": spec.kind in _CONTAINER_KINDS,
                "has_else": spec.kind == "if_else",
                "fields": [
                    {
                        "name": field.name,
                        "label": field.label,
                        "kind": field.kind,
                        "default": field.default,
                        "min": field.minimum,
                        "max": field.maximum,
                        "choices": field.choices,
                        "variable": field.variable,
                        "note": field.note,
                    }
                    for field in spec.fields
                ],
            }
        )
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


_STYLE = r"""
<style id="seer-block-builder-style">
.seer-builder-layout{display:grid;grid-template-columns:minmax(210px,.65fr) minmax(0,2fr);gap:16px;align-items:start}
.seer-builder-panel{background:#0d1b26;border:1px solid #294357;border-radius:14px;padding:16px}
.seer-builder-panel h2,.seer-managed-item strong{color:#f3f8fc}.seer-builder-panel h2{letter-spacing:.01em}
.seer-block-palette{display:grid;gap:9px;position:sticky;top:12px}.seer-palette-button{text-align:left;border-left:7px solid var(--block-color)!important}
.seer-block-workspace{display:grid;gap:12px;min-height:180px}.seer-block-empty{border:2px dashed #35556d;border-radius:12px;padding:35px;text-align:center;color:#9db2c2}
.seer-program-block{border:1px solid #35556d;border-left:9px solid var(--block-color);border-radius:12px;background:#102432;overflow:hidden}
.seer-program-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;background:rgba(255,255,255,.035)}
.seer-program-head strong{font-size:1rem}.seer-block-actions{display:flex;gap:5px}.seer-block-actions button{padding:5px 9px}
.seer-block-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:12px}
.seer-builder-field{display:grid;gap:5px}.seer-builder-field>span{font-size:.82rem;color:#b8cad6;font-weight:700}
.seer-builder-field input,.seer-builder-field select{width:100%;box-sizing:border-box}
.seer-variable-row{display:grid;grid-template-columns:auto minmax(0,1fr);gap:7px;align-items:center;font-size:.78rem;color:#9db2c2}
.seer-variable-row input[type=checkbox]{width:auto}.seer-variable-name:disabled{opacity:.45}
.seer-api-note{font-size:.78rem;color:#9db2c2}.seer-builder-warning{padding:10px 12px;border:1px solid #8d6c19;background:#2b2715;border-radius:10px;color:#ffe4a3}
.seer-step-delay{grid-column:1/-1;padding:9px 10px;border:1px solid #38566d;border-radius:9px;background:#0b1d29}.seer-step-delay input{max-width:180px}.seer-step-delay .seer-api-note{display:block}
.seer-route-tool{grid-column:1/-1;display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:10px;border:1px dashed #557086;border-radius:10px;background:#0a1822}.seer-route-tool .btn{border-color:#d9a72e;color:#3b2a00!important;font-weight:800}.seer-route-tool .btn:hover,.seer-route-tool .btn:focus{color:#241900!important}
.seer-route-picker{display:none;position:fixed;inset:0;z-index:80;background:rgba(2,8,14,.78);padding:24px;overflow:auto}.seer-route-picker.is-open{display:grid;place-items:center}.seer-route-picker-modal{width:min(1040px,100%);max-height:calc(100vh - 48px);overflow:auto;background:#0d1b26;border:1px solid #41617a;border-radius:16px;padding:16px;box-shadow:0 24px 80px rgba(0,0,0,.55)}
.seer-route-picker-head,.seer-route-picker-buttons{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.seer-route-picker-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(280px,.75fr);gap:14px;margin-top:12px}.seer-route-picker-controls{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-bottom:9px}.seer-route-picker-controls label{display:grid;gap:4px;color:#c8d7e2;font-size:.82rem;font-weight:700}
.seer-route-map-toolbar{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:0 0 7px}.seer-route-map-toolbar .btn{min-width:38px;padding:6px 10px}.seer-route-map-zoom{min-width:58px;text-align:center;color:#d8e9f5;font-weight:700;font-variant-numeric:tabular-nums}.seer-route-label-size,.seer-route-layer-order{display:flex;align-items:center;gap:5px;white-space:nowrap;color:#d8e9f5;font-size:.8rem;font-weight:700}.seer-route-label-size input{width:82px;accent-color:#3ce6a4}.seer-route-label-size output{min-width:38px;font-variant-numeric:tabular-nums}.seer-route-layer-order select{padding:4px 6px;border:1px solid #41617a;border-radius:6px;background:#07131d;color:#eaf6ff}.seer-route-map-hint{margin-left:auto;color:#9db2c2;font-size:.78rem}
.seer-route-picker-svg{display:block;width:100%;height:auto;max-height:440px;background:#07131d;border:1px solid #294357;border-radius:12px;cursor:grab;touch-action:none;user-select:none}.seer-route-picker-svg.is-panning{cursor:grabbing}.seer-route-picker-svg path{fill:none;stroke:#246f82;stroke-width:3;vector-effect:non-scaling-stroke;stroke-linecap:round;stroke-linejoin:round}.seer-route-picker-svg path.is-selected{stroke:#ffd43b;stroke-width:7;filter:drop-shadow(0 0 4px #ffd43b)}.seer-route-picker-svg circle{fill:#ffd166;stroke:#111;stroke-width:1.5;cursor:pointer;vector-effect:non-scaling-stroke}.seer-route-picker-svg text{fill:#eef8ff;font:600 15px sans-serif;paint-order:stroke;stroke:#07131d;stroke-width:4;pointer-events:none}.seer-route-picker-svg [data-seer-route-point-label]{fill:#07131d;stroke:none;font-weight:800}.seer-route-picker-svg .seer-route-robot{fill:#3ce6a4;stroke:#eafff6;stroke-width:2;pointer-events:none}
.seer-route-options{display:grid;gap:8px;max-height:360px;overflow:auto}.seer-route-option{display:grid;grid-template-columns:auto 1fr;gap:8px;align-items:start;padding:9px;border:1px solid #35556d;border-radius:9px;background:#102432;color:#eaf6ff}.seer-route-option input{margin-top:3px}.seer-route-option small{display:block;color:#9db2c2;margin-top:3px}.seer-route-picker-status{color:#ffd98a;min-height:1.4em}.seer-builder-map-selector{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:10px 0}.seer-builder-selected-amr{margin:8px 0 12px;padding:9px 12px;border:1px solid #b9d9ea;border-radius:10px;background:#f4fbff;color:#16384d}.seer-builder-selected-amr span{font-weight:800;color:#0b3558}.seer-builder-map-selector .is-current{border-color:#31b9d2;background:#d8f3f8!important;color:#07354a!important;-webkit-text-fill-color:#07354a!important;text-shadow:none!important;box-shadow:inset 0 0 0 1px rgba(49,185,210,.24)}.seer-builder-map-selector .is-current:hover,.seer-builder-map-selector .is-current:focus{background:#c8edf4!important;color:#052b3c!important;-webkit-text-fill-color:#052b3c!important}
.seer-builder-footer{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:14px}.seer-managed-list{display:grid;gap:8px}.seer-managed-item{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 11px;border-radius:10px;background:#163149;border:1px solid #315a78}.seer-managed-actions{display:flex;gap:6px;flex-wrap:wrap}.seer-delete-modal{display:none;position:fixed;inset:0;z-index:120;place-items:center;padding:20px;background:rgba(2,8,14,.54)}.seer-delete-modal.is-open{display:grid}.seer-delete-dialog{width:min(360px,calc(100vw - 36px));padding:18px;border:1px solid #79505a;border-radius:14px;background:#17232d;box-shadow:0 18px 60px rgba(0,0,0,.55);color:#eaf4fa}.seer-delete-dialog h3{margin:0 0 10px;color:#fff}.seer-delete-dialog p{margin:0 0 8px;line-height:1.5}.seer-delete-note{font-size:.76rem;color:#aebfca}.seer-delete-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}
@media(max-width:800px){.seer-builder-layout,.seer-route-picker-grid{grid-template-columns:1fr}.seer-block-palette{position:static}.seer-block-fields{grid-template-columns:1fr}.seer-route-picker{padding:8px}.seer-route-picker-modal{max-height:calc(100vh - 16px)}}
</style>
"""


_SCRIPT = r"""
<script>
(function(){
  if(window.__seerBlockBuilderInstalled)return;window.__seerBlockBuilderInstalled=true;
  var schemas=JSON.parse(document.getElementById('seer-block-schemas').textContent), byKind={};
  var routeMapNode=document.getElementById('seer-route-map'),routeMap=routeMapNode?JSON.parse(routeMapNode.textContent):{},pickerBlock=null,routeChoices=[];
  var routeView={scale:1,x:0,y:0,pointScale:1,labelScale:1,routeLayerOrder:'above',dragging:false,moved:false,pointerId:null,startX:0,startY:0,originX:0,originY:0,suppressClickUntil:0};
  schemas.forEach(function(s){byKind[s.kind]=s;});
  function esc(value){return String(value).replace(/[&<>\"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c];});}
  function routeMapReady(){return routeMap&&Array.isArray(routeMap.points)&&routeMap.points.length>1&&Array.isArray(routeMap.edges)&&routeMap.edges.length>0;}
  function mapHasPoint(name){return routeMapReady()&&routeMap.points.some(function(point){return point.name===name;});}
  function routeInput(block,name){return block&&block.querySelector('[data-value="'+name+'"]');}
  function routeMapSize(){return {width:Number(routeMap.width||760),height:Number(routeMap.height||420)};}
  function routeLabelStorageKey(){return 'seer-route-map-label:'+String(routeMap.map_name||'default');}
  function routePointStorageKey(){return 'seer-route-map-point:'+String(routeMap.map_name||'default');}
  function routeLayerStorageKey(){return 'seer-route-layer-order:'+String(routeMap.map_name||'default');}
  function restoreRouteDisplayScale(){
    try{
      var savedPoint=parseFloat(localStorage.getItem(routePointStorageKey())||'');
      if(Number.isFinite(savedPoint))routeView.pointScale=Math.max(.35,Math.min(2,savedPoint));
      var savedLabel=parseFloat(localStorage.getItem(routeLabelStorageKey())||'');
      if(Number.isFinite(savedLabel))routeView.labelScale=Math.max(.25,Math.min(1.5,savedLabel));
      routeView.routeLayerOrder=localStorage.getItem(routeLayerStorageKey())==='below'?'below':'above';
    }catch(e){}
  }
  function clampRouteView(){
    var size=routeMapSize(),scale=routeView.scale;
    if(scale>=1){routeView.x=Math.max(size.width*(1-scale),Math.min(0,routeView.x));routeView.y=Math.max(size.height*(1-scale),Math.min(0,routeView.y));}
    else{routeView.x=size.width*(1-scale)/2;routeView.y=size.height*(1-scale)/2;}
  }
  function applyRouteLayerOrder(){
    var base=document.getElementById('seer-route-base-paths'),highlight=document.getElementById('seer-route-highlight-paths');
    if(base&&highlight&&base.parentNode===highlight.parentNode){
      if(routeView.routeLayerOrder==='below'){if(highlight.nextElementSibling!==base)base.parentNode.insertBefore(highlight,base);}
      else{if(base.nextElementSibling!==highlight)base.parentNode.insertBefore(highlight,base.nextSibling);}
    }
    var select=document.getElementById('seer-route-layer-order');if(select)select.value=routeView.routeLayerOrder;
  }
  function renderRouteView(){
    clampRouteView();var content=document.getElementById('seer-route-map-content'),label=document.getElementById('seer-route-map-zoom');
    if(content)content.setAttribute('transform','translate('+routeView.x.toFixed(3)+' '+routeView.y.toFixed(3)+') scale('+routeView.scale.toFixed(4)+')');
    if(label)label.textContent=Math.round(routeView.scale*100)+'%';
    applyRouteLayerOrder();
    var inverseScale=1/routeView.scale;
    document.querySelectorAll('#seer-route-picker-map [data-seer-route-fixed-marker]').forEach(function(marker){
      var px=parseFloat(marker.dataset.seerRouteX||'0'),py=parseFloat(marker.dataset.seerRouteY||'0');
      marker.setAttribute('transform','translate('+px.toFixed(2)+' '+py.toFixed(2)+') scale('+inverseScale.toFixed(5)+')');
    });
    document.querySelectorAll('#seer-route-picker-map [data-seer-route-point-marker]').forEach(function(pointMarker){var base=parseFloat(pointMarker.dataset.seerRoutePointBaseRadius||'16');pointMarker.setAttribute('r',(base*routeView.pointScale).toFixed(2));});
    document.querySelectorAll('#seer-route-picker-map [data-seer-route-point-label]').forEach(function(pointLabel){var base=parseFloat(pointLabel.dataset.seerRouteLabelBase||'11');pointLabel.style.fontSize=(base*routeView.labelScale).toFixed(2)+'px';});
    var pointSize=document.getElementById('seer-route-point-size'),pointSizeValue=document.getElementById('seer-route-point-size-value');
    var labelSize=document.getElementById('seer-route-point-label-size'),labelSizeValue=document.getElementById('seer-route-point-label-size-value');
    if(pointSize)pointSize.value=String(Math.round(routeView.pointScale*100));
    if(pointSizeValue)pointSizeValue.textContent=Math.round(routeView.pointScale*100)+'%';
    if(labelSize)labelSize.value=String(Math.round(routeView.labelScale*100));
    if(labelSizeValue)labelSizeValue.textContent=Math.round(routeView.labelScale*100)+'%';
    try{
      localStorage.setItem(routePointStorageKey(),String(routeView.pointScale));
      localStorage.setItem(routeLabelStorageKey(),String(routeView.labelScale));
      localStorage.setItem(routeLayerStorageKey(),routeView.routeLayerOrder);
    }catch(e){}
  }
  function fitRouteMap(){routeView.scale=1;routeView.x=0;routeView.y=0;renderRouteView();}
  function zoomRouteMap(factor,clientX,clientY){
    var svg=document.getElementById('seer-route-picker-map');if(!svg)return;
    var rect=svg.getBoundingClientRect(),size=routeMapSize(),oldScale=routeView.scale,next=Math.max(.5,Math.min(6,oldScale*factor));if(Math.abs(next-oldScale)<.0001)return;
    var ux=(clientX==null?rect.left+rect.width/2:clientX)-rect.left,uy=(clientY==null?rect.top+rect.height/2:clientY)-rect.top;
    ux*=size.width/Math.max(1,rect.width);uy*=size.height/Math.max(1,rect.height);
    var worldX=(ux-routeView.x)/oldScale,worldY=(uy-routeView.y)/oldScale;
    routeView.scale=next;routeView.x=ux-worldX*next;routeView.y=uy-worldY*next;renderRouteView();
  }
  function setupRouteMapNavigation(svg){
    if(!svg||svg.dataset.navigationReady)return;svg.dataset.navigationReady='1';
    svg.addEventListener('wheel',function(event){event.preventDefault();zoomRouteMap(event.deltaY<0?1.2:1/1.2,event.clientX,event.clientY);},{passive:false});
    svg.addEventListener('pointerdown',function(event){if(event.button!==0)return;routeView.dragging=true;routeView.moved=false;routeView.pointerId=event.pointerId;routeView.startX=event.clientX;routeView.startY=event.clientY;routeView.originX=routeView.x;routeView.originY=routeView.y;svg.classList.add('is-panning');svg.setPointerCapture(event.pointerId);});
    svg.addEventListener('pointermove',function(event){if(!routeView.dragging||event.pointerId!==routeView.pointerId)return;var rect=svg.getBoundingClientRect(),size=routeMapSize(),dx=(event.clientX-routeView.startX)*size.width/Math.max(1,rect.width),dy=(event.clientY-routeView.startY)*size.height/Math.max(1,rect.height);if(Math.abs(dx)+Math.abs(dy)>3)routeView.moved=true;routeView.x=routeView.originX+dx;routeView.y=routeView.originY+dy;renderRouteView();});
    function endPan(event){if(!routeView.dragging||event.pointerId!==routeView.pointerId)return;if(routeView.moved)routeView.suppressClickUntil=Date.now()+250;routeView.dragging=false;routeView.pointerId=null;svg.classList.remove('is-panning');if(svg.hasPointerCapture&&svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);}
    svg.addEventListener('pointerup',endPan);svg.addEventListener('pointercancel',endPan);
  }
  function findRoutes(source,target){
    if(!routeMapReady()||!source||!target||source===target)return [];
    var graph={};routeMap.edges.forEach(function(edge){(graph[edge.source]||(graph[edge.source]=[])).push(edge);});
    Object.keys(graph).forEach(function(key){graph[key].sort(function(a,b){return a.distance_m-b.distance_m||a.target.localeCompare(b.target);});});
    var queue=[{points:[source],distance:0}],results=[],expanded=0;
    while(queue.length&&results.length<5&&expanded<10000){
      queue.sort(function(a,b){return a.distance-b.distance||a.points.join('\u0000').localeCompare(b.points.join('\u0000'));});
      var item=queue.shift(),node=item.points[item.points.length-1];expanded++;
      if(node===target){results.push(item);continue;}
      if(item.points.length>=32)continue;
      (graph[node]||[]).forEach(function(edge){if(item.points.indexOf(edge.target)>=0)return;queue.push({points:item.points.concat([edge.target]),distance:item.distance+Number(edge.distance_m||0)});});
    }
    return results;
  }
  function previewRoute(){
    var dialog=document.getElementById('seer-route-picker'),chosen=dialog&&dialog.querySelector('input[name="seer_route_choice"]:checked'),route=chosen&&routeChoices[Number(chosen.value)];
    var layer=document.getElementById('seer-route-highlight-paths');if(!layer)return;layer.innerHTML='';if(!route)return;
    var combined='';
    for(var i=0;i+1<route.points.length;i++){
      var source=route.points[i],target=route.points[i+1],matched=null;
      document.querySelectorAll('#seer-route-base-paths [data-route-source]').forEach(function(path){
        if(!matched&&path.dataset.routeSource===source&&path.dataset.routeTarget===target)matched=path;
      });
      if(!matched)continue;
      var pathData=String(matched.getAttribute('d')||'');if(!pathData)continue;
      if(!combined)combined=pathData;
      else{var command=pathData.search(/\s[QC]\s/);combined+=' '+(command>=0?pathData.slice(command+1):pathData);}
    }
    if(combined){
      var overlay=document.createElementNS('http://www.w3.org/2000/svg','path');
      overlay.setAttribute('d',combined);overlay.setAttribute('class','is-selected');layer.appendChild(overlay);
    }
  }
  function refreshRouteChoices(){
    var source=document.getElementById('seer-route-source'),target=document.getElementById('seer-route-target'),list=document.getElementById('seer-route-options'),status=document.getElementById('seer-route-picker-status');
    if(!source||!target||!list)return;routeChoices=findRoutes(source.value,target.value);
    if(!routeChoices.length){list.innerHTML='<p class="muted">직접 연결된 유효 지정 경로가 없습니다.</p>';if(status)status.textContent='출발점과 목적지 또는 경로 방향을 확인하세요.';previewRoute();return;}
    list.innerHTML=routeChoices.map(function(route,index){return '<label class="seer-route-option"><input type="radio" name="seer_route_choice" value="'+index+'"'+(index===0?' checked':'')+'><span><strong>경로 '+(index+1)+' · '+route.distance.toFixed(2)+' m</strong><small>'+esc(route.points.join(' → '))+'</small></span></label>';}).join('');
    if(status)status.textContent=routeChoices.length+'개 경로 후보 · API 3066 한 번으로 전송';previewRoute();
  }
  function setupRoutePicker(){
    if(!routeMapReady())return;
    var options=routeMap.points.map(function(point){return '<option value="'+esc(point.name)+'">'+esc(point.name)+'</option>';}).join('');
    var source=document.getElementById('seer-route-source'),target=document.getElementById('seer-route-target'),svg=document.getElementById('seer-route-picker-map');
    if(source)source.innerHTML=options;if(target)target.innerHTML=options;if(!svg)return;
    var paths=routeMap.edges.map(function(edge){return '<path data-route-source="'+esc(edge.source)+'" data-route-target="'+esc(edge.target)+'" d="'+esc(edge.path)+'"><title>'+esc(edge.source+' → '+edge.target+' · '+Number(edge.distance_m).toFixed(2)+' m')+'</title></path>';}).join('');
    var points=routeMap.points.map(function(point){var px=Number(point.x).toFixed(2),py=Number(point.y).toFixed(2);return '<g data-route-point="'+esc(point.name)+'" data-seer-route-fixed-marker data-seer-route-x="'+px+'" data-seer-route-y="'+py+'"><circle cx="0" cy="0" r="16" data-seer-route-point-marker data-seer-route-point-base-radius="16"><title>'+esc(point.name)+'</title></circle><text data-seer-route-point-label data-seer-route-label-base="11" x="0" y="0" text-anchor="middle" dominant-baseline="central">'+esc(point.name)+'</text></g>';}).join('');
    var robot=routeMap.pose?(function(){var px=Number(routeMap.pose.x).toFixed(2),py=Number(routeMap.pose.y).toFixed(2);return '<g data-seer-route-fixed-marker data-seer-route-x="'+px+'" data-seer-route-y="'+py+'"><circle class="seer-route-robot" cx="0" cy="0" r="8"><title>현재 AMR</title></circle></g>';}()):'';
    svg.innerHTML='<g id="seer-route-map-content"><rect width="100%" height="100%" fill="#07131d"></rect><g id="seer-route-base-paths">'+paths+'</g><g id="seer-route-highlight-paths" pointer-events="none"></g>'+points+robot+'</g>';setupRouteMapNavigation(svg);fitRouteMap();
  }
  function openRoutePicker(block){
    if(!routeMapReady())return;pickerBlock=block;
    var source=document.getElementById('seer-route-source'),target=document.getElementById('seer-route-target'),sourceValue=String((routeInput(block,'source_id')||{}).value||''),targetValue=String((routeInput(block,'id')||{}).value||'');
    if(sourceValue==='SELF_POSITION'||!mapHasPoint(sourceValue))sourceValue=routeMap.current_point||routeMap.points[0].name;
    if(!mapHasPoint(targetValue)||targetValue===sourceValue){var other=routeMap.points.find(function(point){return point.name!==sourceValue;});targetValue=other?other.name:sourceValue;}
    source.value=sourceValue;target.value=targetValue;document.getElementById('seer-route-picker').classList.add('is-open');fitRouteMap();refreshRouteChoices();
  }
  function closeRoutePicker(){var dialog=document.getElementById('seer-route-picker');if(dialog)dialog.classList.remove('is-open');pickerBlock=null;}
  function applyRouteChoice(){
    var dialog=document.getElementById('seer-route-picker'),chosen=dialog&&dialog.querySelector('input[name="seer_route_choice"]:checked'),route=chosen&&routeChoices[Number(chosen.value)];if(!pickerBlock||!route)return;
    routeInput(pickerBlock,'source_id').value=route.points[0];routeInput(pickerBlock,'id').value=route.points[route.points.length-1];routeInput(pickerBlock,'route_points').value=route.points.join(', ');pickerBlock.dispatchEvent(new Event('input',{bubbles:true}));closeRoutePicker();
  }
  function fieldHtml(field,index){
    var id='seer-b'+index+'-'+field.name, control='';
    if(field.kind==='choice'){
      control='<select data-value="'+esc(field.name)+'">'+field.choices.map(function(v){return '<option value="'+esc(v)+'"'+(String(v)===String(field.default)?' selected':'')+'>'+esc(v)+'</option>';}).join('')+'</select>';
    }else{
      var type=(field.kind==='number'||field.kind==='integer')?'number':'text';
      var attrs=type==='number'?' step="'+(field.kind==='integer'?'1':'any')+'"'+(field.min!=null?' min="'+field.min+'"':'')+(field.max!=null?' max="'+field.max+'"':''):'';
      control='<input type="'+type+'" data-value="'+esc(field.name)+'" value="'+esc(field.default==null?'':field.default)+'"'+attrs+'>';
    }
    var variable=field.variable?'<label class="seer-variable-row"><input type="checkbox" data-variable-toggle="'+esc(field.name)+'"> 변수로 사용 <input class="seer-variable-name" data-variable="'+esc(field.name)+'" value="" placeholder="예: '+esc(field.name)+'" disabled></label>':'';
    return '<label class="seer-builder-field" data-builder-field="'+esc(field.name)+'"><span>'+esc(field.label)+'</span>'+control+variable+(field.note?'<small class="seer-api-note">'+esc(field.note)+'</small>':'')+'</label>';
  }
  function add(kind,initial){
    var schema=byKind[kind], workspace=document.getElementById('seer-block-workspace');if(!schema||!workspace)return;
    var block=document.createElement('section');block.className='seer-program-block';block.dataset.kind=kind;block.style.setProperty('--block-color',schema.color);
    var index=Date.now()+Math.floor(Math.random()*10000);
    var routeTool=kind==='path_nav'?(routeMapReady()?'<div class="seer-route-tool"><button type="button" class="btn" data-open-route-picker>🗺 지도에서 지정 경로 선택</button><small class="seer-api-note">'+esc(routeMap.map_name||'SEER map')+' · 유효한 방향성 경로와 이동거리 표시</small></div>':'<div class="seer-route-tool"><small class="seer-api-note">지도 cache를 기다리는 중입니다. WebUI에서 지도가 표시된 뒤 새로고침하세요.</small></div>'):'';
    var delayValue=initial&&initial.delay_sec!=null?initial.delay_sec:0;
    var delayNote=kind==='path_nav'?'기본 0초 · 목적지 도착 확인 즉시 다음 블록 실행. 주행시간과 속도에는 영향을 주지 않습니다.':'기본 0초 · 동작 완료 즉시 다음 블록 실행.';
    var delayControl='<label class="seer-builder-field seer-step-delay"><span>이 블록 완료 후 추가 대기 (초)</span><input type="number" data-step-delay min="0" max="3600" step="0.1" value="'+esc(delayValue)+'"><small class="seer-api-note">'+delayNote+'</small></label>';
    block.innerHTML='<div class="seer-program-head"><div><strong>'+esc(schema.label)+'</strong><div class="seer-api-note">'+esc(schema.api)+(schema.motion?' · 이동 블록':'')+'</div></div><div class="seer-block-actions"><button type="button" class="btn" data-move="up" title="위로">↑</button><button type="button" class="btn" data-move="down" title="아래로">↓</button><button type="button" class="btn danger" data-remove title="삭제">×</button></div></div><div class="seer-block-fields">'+schema.fields.map(function(f){return fieldHtml(f,index);}).join('')+delayControl+routeTool+'</div>';
    workspace.appendChild(block);
    if(initial){
      Object.keys(initial.values||{}).forEach(function(name){var input=block.querySelector('[data-value="'+name+'"]');if(input)input.value=initial.values[name];});
      Object.keys(initial.variables||{}).forEach(function(name){var toggle=block.querySelector('[data-variable-toggle="'+name+'"]'),input=block.querySelector('[data-variable="'+name+'"]');if(toggle&&input){toggle.checked=true;input.disabled=false;input.value=initial.variables[name];}});
    }
    syncEmpty();
  }
  function syncEmpty(){var w=document.getElementById('seer-block-workspace'),e=document.getElementById('seer-block-empty');if(e)e.style.display=w.querySelector('.seer-program-block')?'none':'block';}
  document.addEventListener('click',function(event){
    var addButton=event.target.closest&&event.target.closest('[data-add-block]');if(addButton){add(addButton.dataset.addBlock);return;}
    var opener=event.target.closest&&event.target.closest('[data-open-route-picker]');if(opener){openRoutePicker(opener.closest('.seer-program-block'));return;}
    if(event.target.closest&&event.target.closest('[data-route-picker-close]')){closeRoutePicker();return;}
    if(event.target.closest&&event.target.closest('[data-route-picker-apply]')){applyRouteChoice();return;}
    var zoomButton=event.target.closest&&event.target.closest('[data-route-map-zoom]');if(zoomButton){zoomRouteMap(Number(zoomButton.dataset.routeMapZoom||1));return;}
    if(event.target.closest&&event.target.closest('[data-route-map-fit]')){fitRouteMap();return;}
    var mapPoint=event.target.closest&&event.target.closest('[data-route-point]');if(mapPoint){if(Date.now()<routeView.suppressClickUntil)return;var target=document.getElementById('seer-route-target');if(target){target.value=mapPoint.dataset.routePoint;refreshRouteChoices();}return;}
    var block=event.target.closest&&event.target.closest('.seer-program-block');if(!block)return;
    if(event.target.closest('[data-remove]')){block.remove();syncEmpty();return;}
    var mover=event.target.closest('[data-move]');if(!mover)return;
    if(mover.dataset.move==='up'&&block.previousElementSibling&&block.previousElementSibling.classList.contains('seer-program-block'))block.parentNode.insertBefore(block,block.previousElementSibling);
    if(mover.dataset.move==='down'&&block.nextElementSibling)block.parentNode.insertBefore(block.nextElementSibling,block);
  });
  document.addEventListener('change',function(event){
    if(event.target.matches&&event.target.matches('#seer-route-layer-order')){routeView.routeLayerOrder=event.target.value==='below'?'below':'above';renderRouteView();return;}
    if(event.target.matches&&event.target.matches('#seer-route-source,#seer-route-target')){refreshRouteChoices();return;}
    if(event.target.matches&&event.target.matches('input[name="seer_route_choice"]')){previewRoute();return;}
    var toggle=event.target.closest&&event.target.closest('[data-variable-toggle]');if(!toggle)return;
    var row=toggle.closest('.seer-variable-row'),name=row&&row.querySelector('[data-variable]');if(name){name.disabled=!toggle.checked;if(toggle.checked&&!name.value)name.value=toggle.dataset.variableToggle;}
  });
  document.addEventListener('input',function(event){
    if(!event.target.matches||!event.target.matches('#seer-route-point-size,#seer-route-point-label-size'))return;
    var percent=parseFloat(event.target.value);if(!Number.isFinite(percent))return;
    if(event.target.matches('#seer-route-point-size'))routeView.pointScale=Math.max(.35,Math.min(2,percent/100));
    else routeView.labelScale=Math.max(.25,Math.min(1.5,percent/100));
    renderRouteView();
  });
  var form=document.getElementById('seer-block-form');if(form)form.addEventListener('submit',function(event){
    var blocks=[];document.querySelectorAll('#seer-block-workspace .seer-program-block').forEach(function(block){
      var values={},variables={};block.querySelectorAll('[data-value]').forEach(function(input){values[input.dataset.value]=input.value;});
      block.querySelectorAll('[data-variable]').forEach(function(input){var toggle=block.querySelector('[data-variable-toggle="'+input.dataset.variable+'"]');if(toggle&&toggle.checked)variables[input.dataset.variable]=input.value;});
      var delay=block.querySelector('[data-step-delay]');blocks.push({kind:block.dataset.kind,values:values,variables:variables,delay_sec:delay?delay.value:'0'});
    });
    if(!blocks.length){event.preventDefault();window.alert('블록을 하나 이상 추가하세요.');return;}
    document.getElementById('seer-block-definition').value=JSON.stringify({name:form.elements.recipe_name.value,label:form.elements.recipe_label.value,blocks:blocks});
  });
  var initialNode=document.getElementById('seer-block-initial');if(initialNode){var initial=JSON.parse(initialNode.textContent);(initial.blocks||[]).forEach(function(block){add(block.kind,block);});}
  restoreRouteDisplayScale();
  setupRoutePicker();
  syncEmpty();
})();
</script>
"""


_ENTRY_STYLE = r"""
<style id="seer-entry-builder-style">
#seer-block-form,#seer-block-form .command-fields,#seer-block-form .mini-field,.seer-entry-shell,.seer-entry-pack,.seer-entry-stage{min-width:0;max-width:100%;box-sizing:border-box}#seer-block-form .command-fields{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}#seer-block-form .command-fields input{width:100%;max-width:100%;box-sizing:border-box}
.seer-entry-shell{display:grid;width:100%;grid-template-columns:68px minmax(170px,220px) minmax(0,1fr);gap:0;min-height:560px;border:1px solid #35556d;border-radius:14px;overflow:hidden;background:#081722}
.seer-entry-categories{display:flex;min-width:0;flex-direction:column;gap:4px;padding:8px 5px;background:#102a3b;border-right:1px solid #35556d}.seer-entry-category{min-height:42px;padding:5px 3px;border:0;border-radius:9px;background:transparent;color:#d8e8f2;font-size:.72rem;font-weight:800;cursor:pointer}.seer-entry-category.is-active{background:#f4f8fb;color:#153044;box-shadow:0 2px 6px #0004}.seer-entry-category span{display:block;font-size:1rem;line-height:1;margin-bottom:2px}
.seer-entry-pack{padding:9px;background:#10202c;color:#e8f7ff;border-right:1px solid #35556d;overflow:hidden}.seer-entry-pack h2{color:#e8f7ff!important;margin:0 0 2px;font-size:1rem}.seer-entry-pack-note{color:#9eb6c6;font-size:.68rem;margin-bottom:7px}.seer-entry-palette{display:grid;gap:5px}.seer-entry-palette[hidden]{display:none!important}.seer-entry-palette [data-entry-add-block]{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:5px;min-height:36px;text-align:left;border:0!important;border-radius:7px 10px 10px 7px;padding:5px 7px 5px 13px;background:var(--block-color)!important;color:#fff!important;box-shadow:0 2px 0 color-mix(in srgb,var(--block-color),#000 28%);cursor:grab}.seer-entry-palette [data-entry-add-block]::before{content:"";position:absolute;left:-1px;top:9px;width:6px;height:14px;background:#10202c;border-radius:0 6px 6px 0}.seer-entry-palette strong{min-width:0;font-size:.78rem;line-height:1.15}.seer-entry-palette small{max-width:78px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#fff;font-size:.62rem;opacity:.78}
.seer-custom-function-palette{display:grid;gap:5px;margin-top:5px;padding-top:6px;border-top:1px solid #35556d}.seer-custom-function-palette:empty{display:none}
.seer-entry-stage{min-width:0;overflow:hidden;padding:9px;background-color:#10202c;background-image:radial-gradient(#365163 1px,transparent 1px);background-size:20px 20px}.seer-entry-toolbar{display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap;margin-bottom:7px;padding:6px 8px;border-radius:8px;background:#0b1822dd}.seer-entry-toolbar strong{font-size:.88rem}.seer-entry-toolbar .seer-api-note{font-size:.67rem}.seer-entry-zoom{display:flex;align-items:center;gap:3px}.seer-entry-zoom .btn{min-width:28px;padding:3px 6px}.seer-entry-zoom output{min-width:42px;text-align:center;color:#e9f6ff;font-size:.72rem;font-weight:800}
.seer-entry-toolbar-actions,.seer-view-switch{display:flex;align-items:center;gap:4px;flex-wrap:wrap}.seer-view-switch .btn.is-active{border-color:#48d9ef;background:#17475a;color:#fff}.seer-view-switch .btn{font-size:.68rem;padding:4px 7px}
.seer-entry-viewport{overflow:auto;min-width:0;min-height:450px;max-height:72vh;padding:10px;border:1px solid #294357;border-radius:9px;background:#07131db8}.seer-block-workspace{display:grid;grid-template-columns:max-content max-content;gap:12px;width:max-content;min-width:100%;transform-origin:top left}.seer-top-lane{position:relative;width:var(--seer-lane-width,480px);min-width:360px;max-width:none;min-height:520px;height:520px;border:1px solid #35556d;border-radius:10px;background:#09182388;overflow:visible}.seer-top-lane.is-drag-over{outline:2px solid #66e0ff;outline-offset:1px}.seer-top-lane.is-insert-target{box-shadow:inset 0 0 0 2px #45d7ef}.seer-lane-head{position:absolute;z-index:2;top:0;left:0;right:0;display:flex;justify-content:space-between;gap:8px;padding:7px 9px;border-bottom:1px solid #35556d;border-radius:9px 9px 0 0;background:#102b3bdd;color:#e8f7ff;font-size:.75rem;pointer-events:none}.seer-lane-head small{color:#9eb6c6}.seer-lane-empty{position:absolute;top:54px;left:12px;right:12px;padding:18px;text-align:center;color:#86a1b3;border:1px dashed #35556d;border-radius:8px;pointer-events:none}.seer-top-lane>.seer-program-block{position:absolute!important;width:max-content;min-width:360px;max-width:none;z-index:1}.seer-top-lane>.seer-program-block:hover,.seer-top-lane>.seer-program-block.is-expanded{z-index:3}.seer-top-lane>.seer-program-block.is-snapped-after::after{content:"";position:absolute;left:24px;top:-5px;width:24px;height:5px;border-radius:3px;background:#57dff3;box-shadow:0 0 6px #57dff399}.seer-top-lane>.seer-program-block.is-snap-candidate{outline:3px solid #57dff3;outline-offset:3px}.seer-block-dropzone{position:relative;display:grid;min-width:0;align-content:start;gap:5px;min-height:42px;margin:4px 7px 6px 20px;padding:5px;border:1px dashed rgba(255,255,255,.52);border-radius:7px;background:rgba(2,12,19,.24)}.seer-block-dropzone>.seer-program-block{position:relative!important;left:auto!important;top:auto!important;width:max-content!important;min-width:calc(100% - 10px);max-width:none!important;margin-left:10px}.seer-block-dropzone>.seer-program-block::before{display:none}.seer-block-dropzone>.seer-program-block>.seer-program-head{padding-left:12px}.seer-block-dropzone>.seer-block-placeholder{margin-left:10px}.seer-block-dropzone.is-drag-over{outline:2px solid #66e0ff;outline-offset:1px}.seer-block-dropzone.is-insert-target{box-shadow:inset 0 0 0 2px #45d7ef}.seer-block-workspace[data-view="flow"]{grid-template-columns:max-content}.seer-block-workspace[data-view="flow"] [data-lane="functions"],.seer-block-workspace[data-view="functions"] [data-lane="flow"]{display:none}.seer-block-workspace[data-view="functions"]{grid-template-columns:max-content}.seer-branch-label{display:block;margin:5px 7px 1px 20px;color:#fff;font-size:.68rem;font-weight:900}.seer-sequence-badge{display:inline-block;min-width:24px;width:24px;height:22px;padding:0 5px;border:0;border-radius:999px;background:#07131d;color:#fff;font-size:.62rem;font-weight:900;text-align:center;cursor:pointer;appearance:none;-webkit-appearance:none}#seer-function-lane .seer-sequence-badge{cursor:default;opacity:1}
.seer-program-block{position:relative;min-width:0;max-width:none;border:0!important;border-left:0!important;border-radius:7px 11px 11px 7px;background:var(--block-color)!important;color:#fff;overflow:visible;box-shadow:0 2px 0 color-mix(in srgb,var(--block-color),#000 26%)}.seer-program-block::before{content:"";position:absolute;left:-1px;top:10px;width:7px;height:15px;background:#07131d;border-radius:0 7px 7px 0}.seer-program-block .seer-program-head{display:flex;width:max-content;min-width:100%;min-height:34px;box-sizing:border-box;align-items:center;justify-content:space-between;gap:5px;padding:4px 5px 4px 10px;background:rgba(0,0,0,.12);cursor:grab;border-radius:7px 11px 11px 7px}.seer-program-block.is-expanded>.seer-program-head{border-radius:7px 11px 0 0}.seer-block-title{display:flex;min-width:max-content;align-items:center;gap:7px}.seer-block-title strong{flex:0 0 auto;font-size:.8rem;line-height:1.1}.seer-block-summary{min-width:max-content;overflow:visible;text-overflow:clip;white-space:nowrap;font-size:.66rem;color:#eef8ff;opacity:.82}.seer-block-dropzone .seer-sequence-badge{display:none}.seer-program-block .seer-program-head .seer-api-note{display:none}.seer-block-actions{display:flex;flex:0 0 auto;gap:2px}.seer-block-actions .btn{display:grid;width:25px;height:25px;min-width:25px;place-items:center;padding:0;font-size:.68rem;line-height:1}.seer-block-actions [data-run-from-block]{display:none;width:auto;min-width:38px;padding:0 6px;font-size:.58rem;font-weight:900}.seer-top-lane#seer-flow-lane>.seer-program-block>.seer-program-head .seer-block-actions>[data-run-from-block]{display:grid}.seer-block-actions [data-toggle-config]{width:30px}.seer-program-block .seer-program-head strong,.seer-program-block .seer-api-note,.seer-program-block .seer-builder-field>span,.seer-program-block .seer-variable-row{color:#fff}.seer-program-block .seer-api-note{font-size:.66rem;opacity:.82}.seer-program-block .seer-block-title{flex:0 0 auto;overflow:visible}.seer-program-block .seer-block-summary{display:inline-block;flex:0 0 auto;min-width:max-content;max-width:none;margin-left:2px;overflow:visible;text-overflow:clip;white-space:nowrap;font-size:.64rem;font-weight:600;color:#eef8ff;opacity:.82;vertical-align:middle}.seer-program-block .seer-block-summary:not(:empty)::before{content:"· ";opacity:.72}.seer-program-block>.seer-block-fields{grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:6px;padding:7px;background:rgba(5,18,27,.28)}.seer-program-block:not(.is-expanded)>.seer-block-fields{display:none}.seer-program-block .seer-builder-field{gap:2px}.seer-program-block .seer-builder-field>span{font-size:.68rem}.seer-program-block .seer-builder-field input,.seer-program-block .seer-builder-field select{min-height:29px;padding:3px 6px;font-size:.75rem}.seer-program-block .seer-variable-row{grid-template-columns:auto auto minmax(0,1fr);gap:4px;font-size:.65rem}.seer-program-block .seer-variable-row input{min-height:24px}.seer-control-block>.seer-block-fields{border-bottom:1px solid rgba(255,255,255,.25)}.seer-control-block{padding-bottom:4px}.seer-step-delay,.seer-route-tool{padding:6px!important}.seer-block-placeholder{min-height:26px;padding:5px;text-align:center;color:#bcd0dc;font-size:.67rem;border:1px dashed #45677d;border-radius:6px}.seer-block-empty{padding:18px;font-size:.8rem}
.seer-program-block .seer-builder-field[hidden]{display:none!important}
/* Entry-style inline sentences: the everyday values live directly on the block face. */
.seer-block-title{flex:0 1 auto}.seer-entry-sentence{display:flex;min-width:0;align-items:center;gap:5px;flex-wrap:wrap;font-size:.76rem;font-weight:900;line-height:1.2}.seer-entry-sentence .seer-entry-word{white-space:nowrap}.seer-entry-inline-field{display:inline-flex;align-items:center;gap:3px;min-width:0}.seer-entry-inline-field[hidden]{display:none!important}.seer-entry-inline-field input,.seer-entry-inline-field select{box-sizing:border-box;height:25px;min-height:25px!important;width:var(--seer-entry-control-width,auto);min-width:0;max-width:none;padding:1px 7px!important;border:1px solid rgba(0,0,0,.22)!important;border-radius:999px!important;background:#fff!important;color:#173044!important;font-size:.69rem!important;font-weight:800!important;box-shadow:inset 0 1px 2px #0002;field-sizing:content}.seer-entry-inline-field input{width:var(--seer-entry-control-width,48px)}.seer-entry-inline-field input[data-value="name"],.seer-entry-inline-field input[data-value="result_name"],.seer-entry-inline-field input[data-value="variable_name"]{min-width:32px}.seer-entry-inline-field input[data-value="left"],.seer-entry-inline-field input[data-value="right"],.seer-entry-inline-field input[data-value="expected"]{min-width:32px}.seer-entry-inline-field input[type="number"]{min-width:32px}.seer-entry-inline-field select[data-value="operator"]{min-width:34px;max-width:none}.seer-entry-inline-field select[data-value="source"]{min-width:48px;max-width:none}.seer-entry-inline-field select[data-value="channel"]{min-width:44px;max-width:none}.seer-entry-boolean{display:inline-flex;align-items:center;gap:3px;min-width:0;margin-left:2px;padding:3px 10px 3px 14px;background:rgba(5,32,26,.24);clip-path:polygon(12px 0,calc(100% - 8px) 0,100% 50%,calc(100% - 8px) 100%,12px 100%,0 50%)}.seer-entry-boolean .seer-entry-inline-field input,.seer-entry-boolean .seer-entry-inline-field select{border-radius:5px!important}.seer-program-block[data-kind="repeat_until"]>.seer-program-head,.seer-program-block[data-kind="if"]>.seer-program-head,.seer-program-block[data-kind="if_else"]>.seer-program-head{align-items:flex-start}.seer-program-block[data-kind="repeat"],.seer-program-block[data-kind="forever"],.seer-program-block[data-kind="repeat_until"],.seer-program-block[data-kind="wait_until"],.seer-program-block[data-kind="if"],.seer-program-block[data-kind="if_else"]{border-radius:14px 16px 14px 10px}.seer-program-block[data-kind="repeat"]>.seer-program-head,.seer-program-block[data-kind="forever"]>.seer-program-head,.seer-program-block[data-kind="repeat_until"]>.seer-program-head,.seer-program-block[data-kind="wait_until"]>.seer-program-head,.seer-program-block[data-kind="if"]>.seer-program-head,.seer-program-block[data-kind="if_else"]>.seer-program-head{padding:6px 6px 6px 12px;border-radius:14px 16px 12px 12px;background:linear-gradient(180deg,rgba(255,255,255,.14),rgba(255,255,255,.04));box-shadow:inset 0 1px 0 rgba(255,255,255,.14)}.seer-program-block[data-kind="repeat"].is-expanded>.seer-program-head,.seer-program-block[data-kind="forever"].is-expanded>.seer-program-head,.seer-program-block[data-kind="repeat_until"].is-expanded>.seer-program-head,.seer-program-block[data-kind="wait_until"].is-expanded>.seer-program-head,.seer-program-block[data-kind="if"].is-expanded>.seer-program-head,.seer-program-block[data-kind="if_else"].is-expanded>.seer-program-head{border-radius:14px 16px 0 0}.seer-program-block[data-kind="repeat"] .seer-block-actions .btn,.seer-program-block[data-kind="forever"] .seer-block-actions .btn,.seer-program-block[data-kind="repeat_until"] .seer-block-actions .btn,.seer-program-block[data-kind="wait_until"] .seer-block-actions .btn,.seer-program-block[data-kind="if"] .seer-block-actions .btn,.seer-program-block[data-kind="if_else"] .seer-block-actions .btn{border-radius:10px}.seer-program-block[data-kind="repeat"] .seer-entry-sentence,.seer-program-block[data-kind="forever"] .seer-entry-sentence,.seer-program-block[data-kind="repeat_until"] .seer-entry-sentence,.seer-program-block[data-kind="wait_until"] .seer-entry-sentence,.seer-program-block[data-kind="if"] .seer-entry-sentence,.seer-program-block[data-kind="if_else"] .seer-entry-sentence{padding:1px 0 1px 3px}.seer-program-block[data-kind="repeat_until"] .seer-entry-sentence,.seer-program-block[data-kind="wait_until"] .seer-entry-sentence,.seer-program-block[data-kind="if"] .seer-entry-sentence,.seer-program-block[data-kind="if_else"] .seer-entry-sentence{flex-wrap:nowrap;gap:4px;white-space:nowrap}.seer-program-block[data-kind="repeat_until"] .seer-entry-boolean,.seer-program-block[data-kind="wait_until"] .seer-entry-boolean,.seer-program-block[data-kind="if"] .seer-entry-boolean,.seer-program-block[data-kind="if_else"] .seer-entry-boolean{flex:0 1 auto;flex-wrap:nowrap;gap:2px;padding-right:8px}.seer-program-block[data-kind="repeat_until"] .seer-block-title,.seer-program-block[data-kind="wait_until"] .seer-block-title,.seer-program-block[data-kind="if"] .seer-block-title,.seer-program-block[data-kind="if_else"] .seer-block-title{overflow:visible}.seer-top-lane>.seer-program-block[data-kind="repeat"],.seer-top-lane>.seer-program-block[data-kind="forever"],.seer-top-lane>.seer-program-block[data-kind="repeat_until"],.seer-top-lane>.seer-program-block[data-kind="wait_until"],.seer-top-lane>.seer-program-block[data-kind="if"],.seer-top-lane>.seer-program-block[data-kind="if_else"],.seer-top-lane>.seer-program-block[data-kind="define_function"]{max-width:none}.seer-program-block[data-kind="repeat_until"] .seer-entry-sentence,.seer-program-block[data-kind="wait_until"] .seer-entry-sentence,.seer-program-block[data-kind="if"] .seer-entry-sentence,.seer-program-block[data-kind="if_else"] .seer-entry-sentence{font-size:.72rem}.seer-program-block[data-kind="repeat_until"] .seer-block-actions .btn,.seer-program-block[data-kind="wait_until"] .seer-block-actions .btn,.seer-program-block[data-kind="if"] .seer-block-actions .btn,.seer-program-block[data-kind="if_else"] .seer-block-actions .btn{width:23px;height:25px;min-width:23px}.seer-program-block[data-kind="repeat_until"] .seer-block-actions [data-toggle-config],.seer-program-block[data-kind="wait_until"] .seer-block-actions [data-toggle-config],.seer-program-block[data-kind="if"] .seer-block-actions [data-toggle-config],.seer-program-block[data-kind="if_else"] .seer-block-actions [data-toggle-config]{width:26px;min-width:26px}.seer-control-block>.seer-block-dropzone{margin-left:18px;margin-right:8px;padding:8px 8px 8px 16px;border-width:2px;border-color:rgba(255,255,255,.56);border-left:0;border-radius:0 10px 10px 10px;background:linear-gradient(90deg,rgba(208,223,238,.30) 0 12px,rgba(4,19,28,.30) 12px 100%)}.seer-control-block>.seer-block-dropzone::before{content:"";position:absolute;left:4px;top:8px;bottom:8px;width:4px;border-radius:999px;background:rgba(7,19,29,.68)}.seer-control-block>.seer-branch-label{margin-left:18px}.seer-entry-advanced-bindings{display:grid;grid-column:1/-1;gap:4px;padding:5px 7px;border:1px dashed rgba(255,255,255,.28);border-radius:7px;background:rgba(0,0,0,.10)}.seer-entry-advanced-bindings>small{font-size:.63rem;color:#fff;opacity:.76}.seer-entry-advanced-bindings .seer-variable-row{grid-template-columns:auto auto minmax(0,1fr)}.seer-program-block[data-entry-inline="true"] .seer-block-summary{display:none}.seer-program-block[data-entry-inline="true"]>.seer-program-head{min-height:40px}.seer-program-block[data-entry-inline="true"]>.seer-block-fields:empty{display:none!important}.seer-program-block[data-kind="set_variable"]>.seer-program-head,.seer-program-block[data-kind="change_variable"]>.seer-program-head{background:rgba(0,0,0,.06)}
.seer-runtime-monitor{display:grid;gap:7px;margin:0 0 8px;padding:9px 10px;border:1px solid #35556d;border-radius:10px;background:#0b1b27}.seer-runtime-monitor.is-running{border-color:#4dcfe3;box-shadow:inset 0 0 0 1px rgba(77,207,227,.18)}.seer-runtime-monitor.is-failed{border-color:#ff6577}.seer-runtime-head{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.seer-runtime-title{display:flex;align-items:center;gap:7px;min-width:0}.seer-runtime-dot{width:9px;height:9px;border-radius:999px;background:#687d8b;box-shadow:0 0 0 3px rgba(104,125,139,.13)}.seer-runtime-monitor.is-running .seer-runtime-dot{background:#55e6b0;box-shadow:0 0 9px #55e6b0}.seer-runtime-monitor.is-finished .seer-runtime-dot{background:#6dd5ff}.seer-runtime-monitor.is-failed .seer-runtime-dot{background:#ff6577;box-shadow:0 0 9px #ff6577}.seer-runtime-title strong{color:#f4fbff;font-size:.8rem}.seer-runtime-recipe{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#9db6c6;font-size:.68rem}.seer-runtime-follow{display:flex;align-items:center;gap:4px;color:#bdd0dc;font-size:.67rem;font-weight:700;white-space:nowrap}.seer-runtime-grid{display:grid;grid-template-columns:minmax(130px,.7fr) minmax(180px,1.3fr) minmax(190px,1.3fr);gap:7px}.seer-runtime-card{min-width:0;padding:7px 8px;border-radius:8px;background:#07141e;border:1px solid #294357}.seer-runtime-card b{display:block;margin-bottom:3px;color:#8ea8b9;font-size:.61rem;text-transform:uppercase;letter-spacing:.03em}.seer-runtime-card span,.seer-runtime-card div{color:#e5f4fc;font-size:.7rem;line-height:1.35}.seer-runtime-vars{display:flex;gap:4px;flex-wrap:wrap}.seer-runtime-var{padding:2px 6px;border-radius:999px;background:#21394a;color:#e9f8ff!important;font-variant-numeric:tabular-nums}.seer-runtime-condition.is-true{color:#65e6ae!important}.seer-runtime-condition.is-false{color:#ffd071!important}.seer-runtime-empty{color:#7892a3!important}.seer-runtime-chip{display:none;flex:0 0 auto;padding:2px 6px;border-radius:999px;background:#0a1a24;color:#dff7ff;font-size:.58rem;font-weight:900;white-space:nowrap;box-shadow:0 1px 2px #0005}.seer-program-block.is-runtime-current{z-index:6!important;outline:3px solid #6ef0c0;outline-offset:3px;box-shadow:0 0 0 2px rgba(110,240,192,.22),0 0 18px rgba(110,240,192,.58),0 2px 0 color-mix(in srgb,var(--block-color),#000 26%)}.seer-program-block.is-runtime-current>.seer-program-head .seer-runtime-chip{display:inline-flex;background:#0b5c47;color:#effff9}.seer-program-block.is-runtime-parent{outline:2px solid #58cbea;outline-offset:2px}.seer-program-block.is-runtime-parent>.seer-program-head .seer-runtime-chip{display:inline-flex;background:#15556a;color:#eaffff}.seer-program-block.is-runtime-complete:not(.is-runtime-current):not(.is-runtime-parent)>.seer-program-head .seer-runtime-chip{display:inline-flex;background:#1d5a49;color:#eafff5}.seer-program-block.is-runtime-error{outline:3px solid #ff6577!important;box-shadow:0 0 16px rgba(255,101,119,.55)!important}.seer-program-block.is-runtime-error>.seer-program-head .seer-runtime-chip{display:inline-flex;background:#8d2434;color:#fff}.seer-program-block[data-runtime-count]:not([data-runtime-count="1"])>.seer-program-head .seer-runtime-chip::after{content:" ×" attr(data-runtime-count);opacity:.82}@media(max-width:900px){.seer-runtime-grid{grid-template-columns:1fr}.seer-runtime-head{align-items:flex-start}}
/* Runtime controls and stable execution overlays. */
.seer-runtime-vars{align-items:flex-start}.seer-runtime-var{max-width:100%;overflow-wrap:anywhere;white-space:normal}.seer-runtime-controls{display:flex;align-items:center;gap:5px;flex-wrap:wrap}.seer-runtime-controls .btn{min-width:66px;padding:5px 9px;font-size:.7rem;font-weight:900}.seer-runtime-controls [data-builder-run]{border-color:#3fd69d;background:#123d32;color:#eafff7}.seer-runtime-controls [data-builder-pause],.seer-runtime-controls [data-builder-resume]{border-color:#e5b54c}.seer-runtime-controls [data-builder-cancel]{border-color:#e56672;color:#ffdfe3}.seer-runtime-controls [data-builder-reset]{border-color:#d7a94a;color:#ffe5a6}.seer-runtime-control-note{min-width:0;color:#8fa8b8;font-size:.65rem}.seer-runtime-control-note.is-dirty{color:#ffd071}.seer-runtime-control-note.is-ok{color:#65e6ae}
.seer-program-head{position:relative}.seer-program-head>.seer-block-title{position:relative;padding-right:62px}.seer-runtime-chip{display:inline-flex!important;visibility:hidden;position:absolute;right:0;top:50%;width:56px;min-width:56px;max-width:56px;height:20px;align-items:center;justify-content:center;transform:translateY(-50%);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;box-sizing:border-box}.seer-program-block.is-runtime-current>.seer-program-head .seer-runtime-chip,.seer-program-block.is-runtime-parent>.seer-program-head .seer-runtime-chip,.seer-program-block.is-runtime-complete:not(.is-runtime-current):not(.is-runtime-parent)>.seer-program-head .seer-runtime-chip,.seer-program-block.is-runtime-error>.seer-program-head .seer-runtime-chip{visibility:visible}.seer-program-block[data-runtime-count]:not([data-runtime-count="1"])>.seer-program-head .seer-runtime-chip::after{content:""}.seer-program-block.is-runtime-current{outline-width:4px;outline-offset:3px;box-shadow:0 0 0 2px rgba(110,240,192,.28),0 0 22px rgba(110,240,192,.64),0 2px 0 color-mix(in srgb,var(--block-color),#000 26%)}.seer-program-block.is-runtime-parent{outline-width:3px;outline-offset:2px}.seer-program-block.is-runtime-error{outline-width:4px!important}
/* Inline controls use the selected/typed text width. No oversized minimums, no collisions. */
.seer-entry-sentence{column-gap:4px;row-gap:4px;max-width:100%}.seer-entry-boolean{flex:0 0 auto;width:max-content;min-width:max-content;max-width:none;flex-wrap:nowrap;column-gap:4px;row-gap:0;box-sizing:border-box;padding-right:18px;overflow:visible}.seer-entry-inline-field{flex:0 0 auto;min-width:0;max-width:none}.seer-entry-inline-field input,.seer-entry-inline-field select{field-sizing:content;width:var(--seer-entry-control-width,auto)!important;min-width:0!important;max-width:none!important;box-sizing:border-box;padding-left:6px!important;padding-right:6px!important;overflow:visible;text-overflow:clip}.seer-program-block[data-kind="repeat_until"] .seer-entry-sentence,.seer-program-block[data-kind="wait_until"] .seer-entry-sentence,.seer-program-block[data-kind="if"] .seer-entry-sentence,.seer-program-block[data-kind="if_else"] .seer-entry-sentence{flex-wrap:nowrap;white-space:nowrap;overflow:visible}.seer-program-block[data-kind="repeat_until"] .seer-entry-boolean,.seer-program-block[data-kind="wait_until"] .seer-entry-boolean,.seer-program-block[data-kind="if"] .seer-entry-boolean,.seer-program-block[data-kind="if_else"] .seer-entry-boolean{width:max-content;min-width:max-content;max-width:none}.seer-program-block>.seer-block-fields{grid-template-columns:repeat(auto-fit,minmax(min(155px,100%),1fr))}.seer-program-block .seer-builder-field{min-width:0}.seer-program-block .seer-builder-field input,.seer-program-block .seer-builder-field select{min-width:0;max-width:100%}.seer-top-lane>.seer-program-block{max-width:none}
/* While a Recipe is running, the Builder is view-only. Monitoring/navigation controls stay usable. */
#seer-block-form.is-runtime-locked .seer-entry-pack,#seer-block-form.is-runtime-locked .seer-program-block,#seer-block-form.is-runtime-locked .command-fields{cursor:not-allowed}#seer-block-form.is-runtime-locked .seer-entry-pack [data-entry-add-block],#seer-block-form.is-runtime-locked .seer-program-block input,#seer-block-form.is-runtime-locked .seer-program-block select,#seer-block-form.is-runtime-locked .seer-program-block button,#seer-block-form.is-runtime-locked .command-fields input,#seer-block-form.is-runtime-locked .seer-builder-footer button[type="submit"]{pointer-events:none;filter:saturate(.72);opacity:.72}#seer-block-form.is-runtime-locked .seer-program-head{cursor:not-allowed}.seer-runtime-lock-note{display:none;color:#ffd071;font-size:.66rem;font-weight:800}#seer-block-form.is-runtime-locked .seer-runtime-lock-note{display:inline}
.seer-drag-visual{position:fixed!important;z-index:10000!important;margin:0!important;pointer-events:none!important;opacity:1!important;filter:none!important;transform:none!important;box-shadow:0 12px 30px #0008!important}.seer-drag-visual[data-entry-add-block]{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:5px;min-height:36px;padding:5px 8px 5px 13px;border:0;border-radius:7px 10px 10px 7px;background:var(--block-color)!important;color:#fff!important}.seer-drag-visual.seer-drag-group-visual{overflow:visible!important;background:transparent!important;box-shadow:none!important}.seer-drag-group-visual>.seer-drag-group-item{position:absolute!important;margin:0!important;opacity:1!important;filter:none!important;transform:none!important;box-shadow:0 12px 30px #0008!important}.seer-drag-group-visual>.seer-program-block.is-snapped-after::after{content:"";position:absolute;left:24px;top:-5px;width:24px;height:5px;border-radius:3px;background:#57dff3;box-shadow:0 0 6px #57dff399}.seer-drag-visual *{pointer-events:none!important}.seer-program-block.is-drag-source{opacity:1!important;filter:none!important}
.seer-entry-help{padding:8px 10px;margin:10px 0;border-left:4px solid #28b7d4;border-radius:8px;background:#123247;color:#dff7ff;font-size:.82rem}.seer-entry-help strong{color:#fff}
.seer-builder-learning{display:grid;gap:9px;margin:12px 0}.seer-builder-learning-tools{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border:1px solid #35556d;border-radius:11px;background:#0c1d29}.seer-builder-learning-tools strong{display:block;color:#f2f8fc;font-size:.91rem}.seer-builder-learning-tools small{display:block;margin-top:2px;color:#afc7d6;font-size:.69rem}.seer-manual-launch{flex:0 0 auto;border-color:#34b9d3!important;background:#16495d!important;color:#effcff!important;font-weight:900}.seer-learning-panel{overflow:hidden;border:1px solid #35556d;border-radius:11px;background:#0c1d29}.seer-learning-panel>summary{display:flex;align-items:center;gap:9px;padding:11px 13px;color:#f2f8fc;font-size:.95rem;font-weight:900;cursor:pointer;list-style:none;background:#143047}.seer-learning-panel>summary::-webkit-details-marker{display:none}.seer-learning-panel>summary::after{content:"펼치기";margin-left:auto;padding:2px 7px;border-radius:999px;background:#071722;color:#a9c7d8;font-size:.64rem}.seer-learning-panel[open]>summary::after{content:"접기"}.seer-learning-panel>summary small{color:#afc7d6;font-size:.69rem;font-weight:600}.seer-learning-body{padding:12px}.seer-manual-modal{display:none;position:fixed;z-index:120;inset:0;place-items:center;padding:20px;background:rgba(2,8,14,.78);backdrop-filter:blur(3px)}.seer-manual-modal.is-open{display:grid}.seer-manual-dialog{display:flex;width:min(940px,calc(100vw - 40px));max-height:min(820px,calc(100vh - 40px));flex-direction:column;overflow:hidden;border:1px solid #41617a;border-radius:16px;background:#0d1b26;box-shadow:0 24px 80px rgba(0,0,0,.6)}.seer-manual-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;border-bottom:1px solid #35556d;background:#143047}.seer-manual-head h2{margin:0;color:#f3f9fd;font-size:1.08rem}.seer-manual-head p{margin:3px 0 0;color:#b7ccda;font-size:.7rem}.seer-manual-close{min-width:38px!important;font-size:1rem!important}.seer-manual-layout{display:grid;grid-template-columns:175px minmax(0,1fr);min-height:0;overflow:hidden}.seer-manual-nav{display:grid;align-content:start;gap:4px;padding:10px;border-right:1px solid #294357;background:#091722;overflow:auto}.seer-manual-nav a{padding:7px 8px;border-radius:7px;color:#bcd3e1;font-size:.7rem;text-decoration:none}.seer-manual-nav a:hover,.seer-manual-nav a:focus{background:#17384b;color:#fff}.seer-manual-content{display:grid;gap:11px;padding:13px;overflow:auto;scroll-behavior:smooth}.seer-manual-intro{padding:10px 12px;border-left:4px solid #2fc5df;border-radius:8px;background:#102d3e;color:#dff8ff;font-size:.76rem;line-height:1.55}.seer-manual-chapter{scroll-margin-top:8px;padding:12px;border:1px solid #294a60;border-radius:11px;background:#102737}.seer-manual-chapter h3{margin:0 0 5px;color:#f1f8fc;font-size:.9rem}.seer-manual-chapter p,.seer-manual-chapter li{color:#bfd3df;font-size:.72rem;line-height:1.55}.seer-manual-chapter p{margin:4px 0}.seer-manual-chapter ol,.seer-manual-chapter ul{margin:7px 0 0;padding-left:19px}.seer-manual-figure{display:grid;gap:7px;margin:8px 0;padding:10px;border:1px solid #35566d;border-radius:9px;background:#071722}.seer-manual-caption{color:#8fabbc;font-size:.64rem;text-align:center}.seer-manual-screen{display:grid;grid-template-columns:68px 150px minmax(200px,1fr);min-height:105px;overflow:hidden;border:1px solid #426178;border-radius:8px}.seer-manual-screen>div{display:grid;place-items:center;padding:8px;text-align:center;font-size:.68rem;font-weight:900}.seer-manual-screen .categories{background:#102a3b;color:#d9eaf4}.seer-manual-screen .pack{background:#10202c;color:#e8f7ff;border-left:1px solid #35556d;border-right:1px solid #35556d}.seer-manual-screen .stage{background-color:#10202c;background-image:radial-gradient(#365163 1px,transparent 1px);background-size:14px 14px;color:#dff6ff}.seer-manual-block-row{display:flex;align-items:center;justify-content:center;gap:9px;flex-wrap:wrap}.seer-manual-arrow{color:#66dff2;font-size:1.15rem;font-weight:900}.seer-manual-mini-block{position:relative;min-width:150px;padding:8px 11px 8px 16px;border-radius:7px 11px 11px 7px;background:var(--manual-block,#c47b00);color:#fff;font-size:.7rem;font-weight:900;box-shadow:0 3px 0 color-mix(in srgb,var(--manual-block,#c47b00),#000 28%)}.seer-manual-mini-block::before{content:"";position:absolute;left:0;top:8px;width:7px;height:15px;border-radius:0 7px 7px 0;background:#071722}.seer-manual-mini-block small{display:block;margin-top:3px;color:#fff;font-size:.6rem;font-weight:600;opacity:.8}.seer-manual-stack{display:grid;gap:4px;min-width:min(320px,100%)}.seer-manual-stack .seer-manual-mini-block+ .seer-manual-mini-block::after{content:"";position:absolute;left:25px;top:-4px;width:24px;height:4px;border-radius:3px;background:#57dff3}.seer-manual-control{padding:8px;border-radius:9px;background:#d98b22;color:#fff;font-size:.7rem;font-weight:900}.seer-manual-drop{margin:7px 0 0 14px;padding:7px;border:1px dashed #eaf8ff;border-radius:7px;background:#07172255}.seer-manual-form{display:grid;grid-template-columns:repeat(2,minmax(120px,1fr));gap:6px}.seer-manual-field{padding:7px;border-radius:7px;background:#fff;color:#173044;font-size:.66rem}.seer-manual-field b{display:block;margin-bottom:3px}.seer-manual-toggle{display:inline-block;padding:2px 6px;border-radius:999px;background:#1f9d67;color:#fff;font-weight:900}.seer-manual-signal{display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap}.seer-manual-signal span{padding:7px 9px;border-radius:999px;background:#173b4e;color:#eaf8ff;font-size:.67rem;font-weight:800}.seer-manual-signal .io{background:#0f766e}.seer-manual-signal .wait{background:#6a4caf}.seer-manual-map{display:block;width:100%;height:150px;border:1px solid #294a60;border-radius:8px;background:#07131d}.seer-manual-map path{fill:none;stroke:#2db9cf;stroke-width:7}.seer-manual-map path.selected{stroke:#ffd43b;stroke-width:9}.seer-manual-map circle{fill:#ffd166;stroke:#17212a;stroke-width:2}.seer-manual-map text{fill:#edf8ff;font:700 13px sans-serif}.seer-manual-pipeline{display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap}.seer-manual-pipeline span{padding:8px 10px;border:1px solid #315a78;border-radius:8px;background:#163149;color:#eaf8ff;font-size:.68rem;font-weight:900}.seer-manual-safety{padding:10px 12px;border-left:4px solid #e5a62b;border-radius:8px;background:#2a2415;color:#ffe0a1;font-size:.73rem;line-height:1.55}.seer-example-intro{margin:0 0 9px;color:#b9cfdd;font-size:.76rem}.seer-example-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px}.seer-example-card{display:flex;min-width:0;flex-direction:column;gap:6px;padding:11px;border:1px solid #34566d;border-radius:10px;background:#102737}.seer-example-card h3{margin:0;color:#f1f8fc;font-size:.88rem}.seer-example-card p{margin:0;color:#b7ccda;font-size:.72rem;line-height:1.45}.seer-example-tags{display:flex;gap:4px;flex-wrap:wrap}.seer-example-tag{padding:2px 6px;border-radius:999px;background:#1c5369;color:#dff8ff;font-size:.61rem;font-weight:800}.seer-example-note{color:#ffd991!important}.seer-example-card .btn{align-self:flex-start;margin-top:auto}.seer-example-status{min-height:1.4em;margin:9px 0 0;color:#69e4b0;font-size:.76rem;font-weight:800}
body.seer-manual-open{overflow:hidden}
@media(max-width:1080px){.seer-entry-shell{grid-template-columns:58px minmax(150px,185px) minmax(0,1fr)}.seer-entry-category{font-size:.66rem}.seer-entry-palette small{display:none}.seer-entry-palette [data-entry-add-block]{grid-template-columns:1fr}}
@media(max-width:760px){.seer-entry-shell{grid-template-columns:minmax(0,1fr);grid-template-rows:auto auto minmax(440px,auto)}.seer-entry-categories{grid-row:1;flex-direction:row;overflow-x:auto;border-right:0;border-bottom:1px solid #35556d}.seer-entry-category{flex:1 0 68px;min-height:34px}.seer-entry-category span{display:inline;margin:0 4px 0 0}.seer-entry-pack{grid-row:2;border-right:0;border-bottom:1px solid #35556d}.seer-entry-palette{grid-template-columns:repeat(auto-fit,minmax(145px,1fr))}.seer-entry-palette small{display:block}.seer-entry-stage{grid-row:3}.seer-entry-viewport{max-height:none;min-height:390px}.seer-builder-learning-tools{align-items:flex-start;flex-direction:column}.seer-manual-modal{padding:8px}.seer-manual-dialog{width:calc(100vw - 16px);max-height:calc(100vh - 16px)}.seer-manual-layout{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}.seer-manual-nav{display:flex;overflow-x:auto;border-right:0;border-bottom:1px solid #294357}.seer-manual-nav a{flex:0 0 auto}.seer-manual-screen{grid-template-columns:52px 100px minmax(150px,1fr)}.seer-manual-form{grid-template-columns:1fr}}
@media(max-width:1000px){.seer-block-workspace{grid-template-columns:minmax(0,1fr)}}
@media(max-width:480px){.seer-entry-palette{grid-template-columns:1fr}.seer-program-block>.seer-block-fields{grid-template-columns:1fr}.seer-block-actions [data-move]{display:none}.seer-program-block[data-kind="repeat_until"] .seer-entry-sentence,.seer-program-block[data-kind="wait_until"] .seer-entry-sentence,.seer-program-block[data-kind="if"] .seer-entry-sentence,.seer-program-block[data-kind="if_else"] .seer-entry-sentence{flex-wrap:wrap;white-space:normal}}
</style>
"""


_ENTRY_SCRIPT = r"""
<script>
(function(){
  if(window.__seerEntryBuilderInstalled)return;window.__seerEntryBuilderInstalled=true;
  var schemaNode=document.getElementById('seer-block-schemas'),schemas=schemaNode?JSON.parse(schemaNode.textContent):[],byKind={};schemas.forEach(function(s){byKind[s.kind]=s;});
  var examplesNode=document.getElementById('seer-builder-examples'),examples=[],exampleByKey={};try{examples=examplesNode?JSON.parse(examplesNode.textContent):[];}catch(error){console.error(error);}examples.forEach(function(example){exampleByKey[example.key]=example;});
  var root=document.getElementById('seer-block-workspace'),flowLane=document.getElementById('seer-flow-lane'),functionLane=document.getElementById('seer-function-lane'),activeZone=flowLane,dragKind='',dragFunction='',dragBlock=null,dragGroup=[],dragVisual=null,dragVisualScale=1,dragOffsetX=0,dragOffsetY=0,blockCounter=0,workspaceZoom=100,SNAP_GAP=2,SNAP_DISTANCE=42;
  var manualModal=document.getElementById('seer-builder-manual-modal'),manualReturnFocus=null;
  var deleteModal=document.getElementById('seer-delete-modal'),deleteModalReturnFocus=null;
  function setDeleteModalOpen(open,name){if(!deleteModal)return;var input=deleteModal.querySelector('[data-delete-recipe-input]'),label=deleteModal.querySelector('[data-delete-recipe-name]');if(name!=null){if(input)input.value=String(name);if(label)label.textContent=String(name);}deleteModal.classList.toggle('is-open',!!open);deleteModal.setAttribute('aria-hidden',open?'false':'true');if(open){deleteModalReturnFocus=document.activeElement;var no=deleteModal.querySelector('[data-close-delete-modal]');if(no)window.setTimeout(function(){no.focus();},0);}else if(deleteModalReturnFocus&&deleteModalReturnFocus.focus){deleteModalReturnFocus.focus();deleteModalReturnFocus=null;}}
  var runtimeConfigNode=document.getElementById('seer-runtime-config'),runtimeConfig={};try{runtimeConfig=runtimeConfigNode?JSON.parse(runtimeConfigNode.textContent):{};}catch(_error){runtimeConfig={};}
  var runtimeLastTrace='',runtimeTimer=null,runtimeLocked=false,runtimeStatus='idle',runtimePausedRequested=false,saveBusy=false;
  var builderDirty=!Boolean(runtimeConfig.saved);
  function assignRuntimeTraceIds(){
    var functionBlocks=directBlocks(functionLane),flowBlocks=directBlocks(flowLane),rootIndex=0;
    function walk(zone,prefix){directBlocks(zone).forEach(function(block,index){var traceId=prefix+'.'+(index+1);block.dataset.runtimeTraceId=traceId;var children=block.querySelector(':scope > [data-child-zone="children"]'),otherwise=block.querySelector(':scope > [data-child-zone="else_children"]');if(children)walk(children,traceId+'.c');if(otherwise)walk(otherwise,traceId+'.e');});}
    functionBlocks.forEach(function(block){rootIndex++;var traceId='r.'+rootIndex;block.dataset.runtimeTraceId=traceId;var children=block.querySelector(':scope > [data-child-zone="children"]'),otherwise=block.querySelector(':scope > [data-child-zone="else_children"]');if(children)walk(children,traceId+'.c');if(otherwise)walk(otherwise,traceId+'.e');});
    flowBlocks.forEach(function(block){rootIndex++;var traceId='r.'+rootIndex;block.dataset.runtimeTraceId=traceId;var children=block.querySelector(':scope > [data-child-zone="children"]'),otherwise=block.querySelector(':scope > [data-child-zone="else_children"]');if(children)walk(children,traceId+'.c');if(otherwise)walk(otherwise,traceId+'.e');});
  }
  function runtimeBlock(traceId){if(!traceId)return null;return document.querySelector('.seer-program-block[data-runtime-trace-id="'+String(traceId).replace(/"/g,'')+'"]');}
  function runtimePhaseLabel(phase){var labels={starting:'시작 준비',running:'실행 중',action:'동작 실행',loop:'반복 실행',condition:'조건 판단',waiting_condition:'조건 대기',waiting_di:'DI 대기',delay:'다음 블록 대기',complete:'완료',finished:'전체 완료',stopped:'중지됨',failed:'오류',cancelled:'취소됨',restarting:'처음부터 재시작',stale:'실행 상태 종료'};return labels[String(phase||'')]||String(phase||'실행 중');}
  function runtimeValue(value){if(value===null)return 'null';if(value===true)return 'TRUE';if(value===false)return 'FALSE';if(typeof value==='object'){try{return JSON.stringify(value);}catch(_error){return String(value);}}return String(value);}
  function clearRuntimeBlockMarks(){document.querySelectorAll('.seer-program-block').forEach(function(block){block.classList.remove('is-runtime-current','is-runtime-parent','is-runtime-complete','is-runtime-error');block.removeAttribute('data-runtime-count');var chip=block.querySelector(':scope > .seer-program-head [data-runtime-chip]');if(chip)chip.textContent='';});}
  function setRuntimeChip(block,text){if(!block)return;var chip=block.querySelector(':scope > .seer-program-head [data-runtime-chip]');if(chip){chip.textContent=text||'';chip.title=text||'';}}
  function setRuntimeControlNote(text,kind){var note=document.getElementById('seer-runtime-control-note');if(!note)return;note.textContent=text||'';note.classList.remove('is-dirty','is-ok');if(kind)note.classList.add(kind);}
  function updateRuntimeControlButtons(){
    var run=document.querySelector('[data-builder-run]'),pause=document.querySelector('[data-builder-pause]'),resume=document.querySelector('[data-builder-resume]'),cancel=document.querySelector('[data-builder-cancel]'),reset=document.querySelector('[data-builder-reset]'),running=runtimeStatus==='running';
    if(run)run.disabled=running||runtimeLocked||builderDirty||saveBusy||!runtimeConfig.robot;
    if(pause)pause.disabled=!running||runtimePausedRequested;
    if(resume)resume.disabled=!running||!runtimePausedRequested;
    if(cancel)cancel.disabled=!running;
    if(reset)reset.disabled=running||!runtimeConfig.robot;
    if(builderDirty&&!running)setRuntimeControlNote('변경사항을 저장하면 실행할 수 있습니다.','is-dirty');
    else if(!running&&!saveBusy)setRuntimeControlNote('저장된 Recipe를 Builder에서 바로 실행할 수 있습니다.','');
  }
  function setBuilderDirty(dirty){builderDirty=!!dirty;updateRuntimeControlButtons();}
  function setBuilderLocked(locked){
    locked=!!locked;if(runtimeLocked===locked)return;runtimeLocked=locked;var builderForm=document.getElementById('seer-block-form');if(builderForm)builderForm.classList.toggle('is-runtime-locked',locked);
    document.querySelectorAll('#seer-block-form .command-fields input,#seer-block-form .seer-program-block input,#seer-block-form .seer-program-block select,#seer-block-form .seer-program-block button,#seer-block-form .seer-entry-pack [data-entry-add-block],#seer-block-form .seer-builder-footer button[type="submit"]').forEach(function(control){
      if(locked){if(!control.disabled){control.dataset.runtimeWasEnabled='1';control.disabled=true;}}else if(control.dataset.runtimeWasEnabled==='1'){control.disabled=false;delete control.dataset.runtimeWasEnabled;}
    });
    document.querySelectorAll('#seer-block-form .seer-program-head,#seer-block-form .seer-entry-pack [data-entry-add-block]').forEach(function(node){if(locked){node.dataset.runtimeDraggable=node.getAttribute('draggable')||'';node.setAttribute('draggable','false');}else if(node.dataset.runtimeDraggable!=null){if(node.dataset.runtimeDraggable)node.setAttribute('draggable',node.dataset.runtimeDraggable);else node.removeAttribute('draggable');delete node.dataset.runtimeDraggable;}});
    if(locked){var routeDialog=document.getElementById('seer-route-picker');if(routeDialog)routeDialog.classList.remove('is-open');}
    updateRuntimeControlButtons();
  }
  async function postBuilderAction(actionType,startTraceId){
    if(!runtimeConfig.robot)throw new Error('실행할 SEER AMR을 선택하세요.');var csrfNode=document.querySelector('#seer-block-form [name="csrf_token"]'),body=new URLSearchParams();body.set('csrf_token',csrfNode?csrfNode.value:'');body.set('robot',runtimeConfig.robot);body.set('action_type',actionType);body.set('confirm','on');if(startTraceId)body.set('start_trace_id',String(startTraceId));
    var response=await fetch('/recipe-builder/action',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString(),cache:'no-store'}),payload={};try{payload=await response.json();}catch(_error){}if(!response.ok||!payload.ok)throw new Error(payload.error||('요청 실패 ('+response.status+')'));return payload.message||'선택한 AMR에 명령을 전달했습니다.';
  }
  async function handleRuntimeControl(kind,button,startTraceId){
    if(button&&button.disabled)return;try{if(button)button.disabled=true;
      if(kind==='run'||kind==='run_from'){if(builderDirty)throw new Error('먼저 블록코딩을 저장하세요.');var recipe=form&&form.elements.recipe_name?String(form.elements.recipe_name.value||'').trim():'';if(!recipe)throw new Error('Recipe 이름이 없습니다.');var fromTrace=kind==='run_from'?String(startTraceId||''):'';setRuntimeControlNote(fromTrace?('선택한 블록('+fromTrace+')부터 실행 명령 전달 중…'):'실행 명령 전달 중…','');var message=await postBuilderAction(recipe,fromTrace);runtimePausedRequested=false;runtimeStatus='running';setBuilderLocked(true);setRuntimeControlNote(message+(fromTrace?' · '+fromTrace+'부터 실행':''),'is-ok');}
      else if(kind==='pause'){setRuntimeControlNote('Pause 요청 중…','');var pauseMessage=await postBuilderAction('startPause');runtimePausedRequested=true;setRuntimeControlNote(pauseMessage,'is-ok');}
      else if(kind==='resume'){setRuntimeControlNote('Resume 요청 중…','');var resumeMessage=await postBuilderAction('stopPause');runtimePausedRequested=false;setRuntimeControlNote(resumeMessage,'is-ok');}
      else if(kind==='cancel'){setRuntimeControlNote('현재 Action 취소 요청 중…','');var cancelMessage=await postBuilderAction('seerCancelActiveAction');runtimePausedRequested=false;setRuntimeControlNote(cancelMessage,'is-ok');}
      else if(kind==='reset'){setRuntimeControlNote('Action 오류/기록 초기화 중…','');var resetMessage=await postBuilderAction('seerResetActionErrors');runtimePausedRequested=false;runtimeStatus='idle';setBuilderLocked(false);clearRuntimeBlockMarks();setRuntimeControlNote(resetMessage,'is-ok');}
    }catch(error){setRuntimeControlNote(error&&error.message?error.message:String(error),'is-dirty');}finally{updateRuntimeControlButtons();}
  }
  function renderRuntimeState(state){
    assignRuntimeTraceIds();clearRuntimeBlockMarks();
    var monitor=document.getElementById('seer-runtime-monitor'),statusNode=document.getElementById('seer-runtime-status'),recipeNode=document.getElementById('seer-runtime-recipe'),currentNode=document.getElementById('seer-runtime-current'),varsNode=document.getElementById('seer-runtime-vars'),conditionNode=document.getElementById('seer-runtime-condition');if(!monitor)return;
    monitor.classList.remove('is-running','is-finished','is-failed');var status=String(state&&state.status||'idle'),runtimeRecipe=String(state&&state.recipe_name||''),formRecipe=form&&form.elements.recipe_name?String(form.elements.recipe_name.value||''):'';
    runtimeStatus=status;runtimePausedRequested=status==='running'&&!!(state&&state.paused);setBuilderLocked(status==='running');updateRuntimeControlButtons();
    if(status==='running')monitor.classList.add('is-running');else if(status==='failed'||status==='cancelled')monitor.classList.add('is-failed');else if(status==='finished'||status==='stopped')monitor.classList.add('is-finished');
    statusNode.textContent=status==='idle'?'실행 대기':(status==='running'?(runtimePausedRequested?'일시정지':runtimePhaseLabel(state.phase)):(status==='finished'?'실행 완료':(status==='stopped'?'중지됨':(status==='cancelled'?'취소됨':'실행 오류'))));recipeNode.textContent=runtimeRecipe?runtimeRecipe:'';var runtimeMessage=String(state&&state.message||'');if(state&&state.stop_after_current&&status==='running')setRuntimeControlNote(String(state.stop_after_current_reason||'통신이 복구되었습니다. 현재 블록 완료 후 정지합니다.'),'is-dirty');else if(status==='stopped'&&runtimeMessage)setRuntimeControlNote(runtimeMessage,'is-ok');
    if(status==='idle'){currentNode.innerHTML='<span class="seer-runtime-empty">실행 중인 블록이 없습니다.</span>';varsNode.innerHTML='<span class="seer-runtime-empty">변수 없음</span>';conditionNode.className='seer-runtime-condition';conditionNode.innerHTML='<span class="seer-runtime-empty">조건 평가 없음</span>';runtimeLastTrace='';return;}
    if(runtimeRecipe&&formRecipe&&runtimeRecipe!==formRecipe){currentNode.textContent='다른 Recipe 실행 중 · '+runtimeRecipe;varsNode.innerHTML='<span class="seer-runtime-empty">해당 Recipe를 편집하면 블록 위치까지 표시됩니다.</span>';conditionNode.className='seer-runtime-condition';conditionNode.innerHTML='<span class="seer-runtime-empty">현재 편집 Recipe와 실행 Recipe가 다릅니다.</span>';return;}
    var counts=state.block_counts||{};Object.keys(counts).forEach(function(traceId){var block=runtimeBlock(traceId);if(block)block.dataset.runtimeCount=String(counts[traceId]);});
    (state.completed_trace_ids||[]).forEach(function(traceId){var block=runtimeBlock(traceId);if(block){block.classList.add('is-runtime-complete');setRuntimeChip(block,'✓ 완료');}});
    (state.active_parent_trace_ids||[]).forEach(function(traceId){var block=runtimeBlock(traceId);if(block){block.classList.add('is-runtime-parent');setRuntimeChip(block,'↻ 진행 중');}});
    (state.loops||[]).forEach(function(loop){var block=runtimeBlock(loop.trace_id);if(block){block.classList.add('is-runtime-parent');var total=loop.total==null?'∞':loop.total;setRuntimeChip(block,'↻ '+String(loop.iteration||0)+' / '+String(total));}});
    var trace=String(state.current_trace_id||''),currentBlock=runtimeBlock(trace);if(currentBlock){currentBlock.classList.add('is-runtime-current');setRuntimeChip(currentBlock,'▶ '+runtimePhaseLabel(state.phase));if(status==='failed'||status==='cancelled')currentBlock.classList.add('is-runtime-error');}
    var kind=String(state.current_kind||'');currentNode.textContent=trace?(runtimePhaseLabel(state.phase)+' · '+kind+' · '+trace):(status==='finished'?'모든 블록 실행 완료':runtimePhaseLabel(state.phase));
    var variables=state.variables||{},keys=Object.keys(variables).sort();varsNode.innerHTML=keys.length?keys.map(function(key){return '<span class="seer-runtime-var">'+esc(key)+' = '+esc(runtimeValue(variables[key]))+'</span>';}).join(''):'<span class="seer-runtime-empty">변수 없음</span>';
    var condition=state.condition;if(condition&&typeof condition==='object'){var source=condition.source==='variable'?(condition.variable_name||'변수'):(condition.source==='di'?'DI'+String(condition.channel==null?'':condition.channel):condition.source),result=!!condition.result;conditionNode.className='seer-runtime-condition '+(result?'is-true':'is-false');conditionNode.textContent=String(source)+' : '+runtimeValue(condition.actual)+' '+String(condition.operator||'=')+' '+runtimeValue(condition.expected)+' → '+(result?'TRUE':'FALSE');}else{conditionNode.className='seer-runtime-condition';conditionNode.innerHTML='<span class="seer-runtime-empty">조건 평가 없음</span>';}
    var follow=document.getElementById('seer-runtime-follow');if(currentBlock&&follow&&follow.checked&&trace&&trace!==runtimeLastTrace){runtimeLastTrace=trace;window.setTimeout(function(){try{currentBlock.scrollIntoView({behavior:'smooth',block:'center',inline:'center'});}catch(_error){}},20);}
  }
  async function pollRuntime(){try{var params=new URLSearchParams();if(runtimeConfig.robot)params.set('robot',runtimeConfig.robot);params.set('_t',String(Date.now()));var response=await fetch('/recipe-builder/runtime?'+params.toString(),{cache:'no-store'});if(response.ok)renderRuntimeState(await response.json());}catch(_error){}finally{runtimeTimer=window.setTimeout(pollRuntime,350);}}
  function startRuntimeMonitor(){if(runtimeTimer)return;assignRuntimeTraceIds();pollRuntime();}
  function setBuilderManualOpen(open){if(!manualModal)return;manualModal.classList.toggle('is-open',open);manualModal.setAttribute('aria-hidden',open?'false':'true');document.body.classList.toggle('seer-manual-open',open);if(open){manualReturnFocus=document.activeElement;var content=manualModal.querySelector('.seer-manual-content');if(content)content.scrollTop=0;var close=manualModal.querySelector('[data-close-builder-manual]');if(close)close.focus();}else if(manualReturnFocus&&manualReturnFocus.focus){manualReturnFocus.focus();}}
  function esc(value){return String(value==null?'':value).replace(/[&<>\"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c];});}
  function entryControlText(control){if(!control)return '';if(control.tagName==='SELECT'){var option=control.options&&control.selectedIndex>=0?control.options[control.selectedIndex]:null;return String(option?option.textContent:control.value||'');}return String(control.value||control.placeholder||'');}
  var entryMeasureCanvas=null;
  function entryTextWidth(control,text){try{entryMeasureCanvas=entryMeasureCanvas||document.createElement('canvas');var ctx=entryMeasureCanvas.getContext('2d'),style=window.getComputedStyle(control);if(ctx){ctx.font=[style.fontStyle,style.fontVariant,style.fontWeight,style.fontSize,style.fontFamily].filter(Boolean).join(' ');return ctx.measureText(String(text||'')).width;}}catch(_error){}return Array.from(String(text||'')).reduce(function(total,char){return total+(/[^\x00-\xff]/.test(char)?12:7);},0);}
  function sizeEntryControl(control){if(!control||['INPUT','SELECT'].indexOf(control.tagName)<0||!control.closest('.seer-entry-inline-field'))return;var text=entryControlText(control),style=window.getComputedStyle(control),isSelect=control.tagName==='SELECT',isNumber=control.tagName==='INPUT'&&control.type==='number',hasList=control.tagName==='INPUT'&&control.hasAttribute('list'),paddingLeft=parseFloat(style.paddingLeft)||0,paddingRight=parseFloat(style.paddingRight)||0,borderLeft=parseFloat(style.borderLeftWidth)||0,borderRight=parseFloat(style.borderRightWidth)||0,chromeExtra=isSelect?24:(hasList?24:(isNumber?18:2)),textWidth=entryTextWidth(control,text),px=Math.ceil(textWidth+paddingLeft+paddingRight+borderLeft+borderRight+chromeExtra+3),minPx=isSelect?36:(isNumber?38:30);px=Math.max(minPx,px);control.style.setProperty('--seer-entry-control-width',px+'px');}
  function sizeConditionCapsule(capsule){if(!capsule)return;capsule.style.width='max-content';capsule.style.minWidth='max-content';capsule.style.maxWidth='none';}
  function sizeConditionCapsules(rootNode){var scope=rootNode||document;if(scope.classList&&scope.classList.contains('seer-entry-boolean'))sizeConditionCapsule(scope);scope.querySelectorAll&&scope.querySelectorAll('.seer-entry-boolean').forEach(sizeConditionCapsule);}
  function sizeAllEntryInputs(rootNode){var scope=rootNode||document;scope.querySelectorAll('.seer-entry-inline-field input,.seer-entry-inline-field select').forEach(sizeEntryControl);sizeConditionCapsules(scope);}
  function directBlocks(zone){return Array.prototype.filter.call(zone.children,function(node){return node.classList&&node.classList.contains('seer-program-block');});}
  function isTopLane(zone){return !!(zone&&zone.classList&&zone.classList.contains('seer-top-lane'));}
  function laneForKind(kind){return kind==='define_function'?functionLane:flowLane;}
  function blockBaseWidth(block){var kind=block&&block.dataset&&block.dataset.kind;return ['repeat','forever','repeat_until','wait_until','if','if_else','define_function'].indexOf(kind)>=0?490:360;}
  function naturalFlexChildrenWidth(node){
    if(!node)return 0;
    var style=window.getComputedStyle(node),pad=(parseFloat(style.paddingLeft)||0)+(parseFloat(style.paddingRight)||0),gap=parseFloat(style.columnGap||style.gap)||0,children=Array.prototype.filter.call(node.children,function(child){var cs=window.getComputedStyle(child);return cs.display!=='none'&&cs.visibility!=='hidden'&&cs.position!=='absolute';}),total=pad;
    children.forEach(function(child,index){var width;if(child.classList&&(child.classList.contains('seer-entry-sentence')||child.classList.contains('seer-entry-boolean')||child.classList.contains('seer-block-title')))width=naturalFlexChildrenWidth(child);else width=child.getBoundingClientRect().width||child.offsetWidth||0;total+=width;if(index<children.length-1)total+=gap;});
    return Math.ceil(total);
  }
  function naturalBlockWidth(block){
    if(!block)return 360;
    var head=block.querySelector(':scope > .seer-program-head'),title=head&&head.querySelector(':scope > .seer-block-title'),actions=head&&head.querySelector(':scope > .seer-block-actions'),base=blockBaseWidth(block),needed=base;
    if(head&&title){var hs=window.getComputedStyle(head),gap=parseFloat(hs.columnGap||hs.gap)||5,pad=(parseFloat(hs.paddingLeft)||0)+(parseFloat(hs.paddingRight)||0),actionWidth=actions?actions.getBoundingClientRect().width:0,titleWidth=naturalFlexChildrenWidth(title);needed=Math.max(needed,Math.ceil(titleWidth+actionWidth+gap+pad+8));}
    block.querySelectorAll(':scope > .seer-block-dropzone > .seer-program-block').forEach(function(child){needed=Math.max(needed,naturalBlockWidth(child)+58);});
    return Math.max(base,needed);
  }
  function syncAdaptiveBlockWidths(){
    if(!root||!flowLane||!functionLane)return;
    var viewport=root.closest('.seer-entry-viewport'),view=root.dataset.view||'both',visible=[];if(view!=='functions')visible.push(flowLane);if(view!=='flow')visible.push(functionLane);var available=viewport?Math.max(360,viewport.clientWidth-4):760,gap=12,baseLane=visible.length===1?available:Math.max(360,Math.floor((available-gap)/2));
    [flowLane,functionLane].forEach(function(lane){var laneNeeded=baseLane;directBlocks(lane).forEach(function(block){var width=naturalBlockWidth(block);block.style.width=Math.ceil(width)+'px';var x=Number(block.dataset.layoutX||8);laneNeeded=Math.max(laneNeeded,Math.ceil(x+width+16));});lane.style.setProperty('--seer-lane-width',Math.ceil(laneNeeded)+'px');});
    /* Block width changes can change a connected block's rendered height (for example,
       an Entry-style sentence can unwrap after its natural width is measured). Re-snap
       the saved top-level chain only after those final widths are applied. */
    refreshSnapMarkers();ensureLaneHeight(flowLane);ensureLaneHeight(functionLane);
  }
  function syncEmpty(){[flowLane,functionLane].forEach(function(lane){var empty=lane&&lane.querySelector(':scope > [data-lane-empty]');if(empty)empty.style.display=directBlocks(lane).length?'none':'block';});}
  function ensureLaneHeight(lane){if(!lane)return;var bottom=0;directBlocks(lane).forEach(function(block){bottom=Math.max(bottom,Number(block.dataset.layoutY||42)+block.offsetHeight);});lane.style.height=Math.max(520,Math.ceil(bottom+28))+'px';}
  function refreshSnapMarkers(){var blocks=directBlocks(flowLane);blocks.forEach(function(block,index){block.classList.remove('is-snapped-after');if(index===0){block.dataset.connected='false';return;}if(block.dataset.connected!=='true')return;var previous=blocks[index-1],expectedX=Number(previous.dataset.layoutX||8),expectedY=Number(previous.dataset.layoutY||42)+previous.offsetHeight+SNAP_GAP;setTopCoordinates(block,expectedX,expectedY);block.classList.add('is-snapped-after');});}
  function refreshSequenceNumbers(){var flowBlocks=directBlocks(flowLane),options=flowBlocks.map(function(_block,index){var number=index+1;return '<option value="'+number+'">'+number+'</option>';}).join('');flowBlocks.forEach(function(block,index){var badge=block.querySelector(':scope > .seer-program-head [data-sequence]');if(badge){badge.disabled=false;badge.innerHTML=options;badge.value=String(index+1);badge.title='실행 번호 변경';badge.setAttribute('aria-label','실행 순서 '+(index+1));}});directBlocks(functionLane).forEach(function(block){var badge=block.querySelector(':scope > .seer-program-head [data-sequence]');if(badge){badge.innerHTML='<option>ƒ</option>';badge.disabled=true;badge.title='함수 정의';badge.setAttribute('aria-label','함수 정의');}});refreshSnapMarkers();ensureLaneHeight(flowLane);ensureLaneHeight(functionLane);}
  function placeTopBlock(lane,block,x,y,preserveOrder){
    if(!lane||!block)return;if(block.parentElement!==lane){lane.appendChild(block);}else if(!preserveOrder){lane.appendChild(block);}block.style.position='absolute';var maxX=Math.max(8,lane.clientWidth-block.offsetWidth-8),safeX=Math.max(8,Math.min(Number.isFinite(x)?x:8,maxX)),safeY=Math.max(42,Math.min(Number.isFinite(y)?y:42,10000));block.dataset.layoutX=String(Math.round(safeX*10)/10);block.dataset.layoutY=String(Math.round(safeY*10)/10);block.style.left=block.dataset.layoutX+'px';block.style.top=block.dataset.layoutY+'px';refreshSequenceNumbers();syncEmpty();
  }
  function setTopCoordinates(block,x,y){block.dataset.layoutX=String(Math.round(x*10)/10);block.dataset.layoutY=String(Math.round(y*10)/10);block.style.left=block.dataset.layoutX+'px';block.style.top=block.dataset.layoutY+'px';}
  function connectedFollowers(block){var followers=[],next=block&&block.nextElementSibling;while(next&&next.classList&&next.classList.contains('seer-program-block')&&next.dataset.connected==='true'){followers.push(next);next=next.nextElementSibling;}return followers;}
  function connectAfter(block,target){var next=target.nextElementSibling;flowLane.insertBefore(block,next);block.dataset.connected='true';setTopCoordinates(block,Number(target.dataset.layoutX||8),Number(target.dataset.layoutY||42)+target.offsetHeight+SNAP_GAP);}
  function connectBefore(block,target){flowLane.insertBefore(block,target);block.dataset.connected=target.dataset.connected==='true'?'true':'false';target.dataset.connected='true';setTopCoordinates(block,Number(target.dataset.layoutX||8),Math.max(42,Number(target.dataset.layoutY||42)-block.offsetHeight-SNAP_GAP));}
  function findFlowSnap(block,x,y,excluded){var best=null,blockHeight=block?block.offsetHeight:34,skip=excluded||[];directBlocks(flowLane).forEach(function(target){if(target===block||skip.indexOf(target)>=0)return;var tx=Number(target.dataset.layoutX||8),ty=Number(target.dataset.layoutY||42);if(Math.abs(x-tx)>72)return;var below=ty+target.offsetHeight+SNAP_GAP,above=ty-blockHeight-SNAP_GAP,belowDistance=Math.abs(y-below),aboveDistance=Math.abs(y-above),next=target.nextElementSibling,hasConnectedNext=!!(next&&next.classList&&next.classList.contains('seer-program-block')&&next.dataset.connected==='true');if(!hasConnectedNext&&belowDistance<=SNAP_DISTANCE&&(!best||belowDistance<best.distance))best={target:target,side:'after',distance:belowDistance};if(target.dataset.connected!=='true'&&above>=42&&aboveDistance<=SNAP_DISTANCE&&(!best||aboveDistance<best.distance))best={target:target,side:'before',distance:aboveDistance};});return best;}
  function snapFlowBlock(block,x,y,excluded){var best=findFlowSnap(block,x,y,excluded);if(!best)return false;if(best.side==='after')connectAfter(block,best.target);else connectBefore(block,best.target);return true;}
  function moveFollowersAfter(block,followers,offsets){var anchor=block;followers.forEach(function(follower,index){flowLane.insertBefore(follower,anchor.nextElementSibling);follower.dataset.connected='true';setTopCoordinates(follower,Number(block.dataset.layoutX||8)+offsets[index].x,Number(block.dataset.layoutY||42)+offsets[index].y);anchor=follower;});}
  function removeDragVisual(){if(dragVisual&&dragVisual.parentNode)dragVisual.parentNode.removeChild(dragVisual);dragVisual=null;document.querySelectorAll('.is-drag-source').forEach(function(node){node.classList.remove('is-drag-source');});}
  function prepareDragClone(source){var clone=source.cloneNode(true);clone.removeAttribute('id');clone.classList.remove('is-drag-source','is-snap-candidate');clone.querySelectorAll('[id]').forEach(function(node){node.removeAttribute('id');});clone.querySelectorAll('input,select,button').forEach(function(node){node.tabIndex=-1;});return clone;}
  function showDragVisual(source,event,scale,group){removeDragVisual();if(!source)return;var members=[source].concat((group||[]).map(function(item){return item.block;}).filter(function(block){return block&&block!==source;})),rects=members.map(function(block){return block.getBoundingClientRect();}),left=Math.min.apply(null,rects.map(function(rect){return rect.left;})),top=Math.min.apply(null,rects.map(function(rect){return rect.top;})),right=Math.max.apply(null,rects.map(function(rect){return rect.right;})),bottom=Math.max.apply(null,rects.map(function(rect){return rect.bottom;}));dragVisualScale=scale||1;if(members.length===1){dragVisual=prepareDragClone(source);dragVisual.classList.add('seer-drag-visual');dragVisual.style.width=rects[0].width+'px';dragVisual.style.height=rects[0].height+'px';}else{dragVisual=document.createElement('div');dragVisual.className='seer-drag-visual seer-drag-group-visual';dragVisual.style.width=(right-left)+'px';dragVisual.style.height=(bottom-top)+'px';members.forEach(function(member,index){var rect=rects[index],clone=prepareDragClone(member);clone.classList.add('seer-drag-group-item');clone.style.left=(rect.left-left)+'px';clone.style.top=(rect.top-top)+'px';clone.style.width=rect.width+'px';clone.style.height=rect.height+'px';dragVisual.appendChild(clone);});}dragVisual.style.left=left+'px';dragVisual.style.top=top+'px';document.body.appendChild(dragVisual);members.forEach(function(member){member.classList.add('is-drag-source');});try{var transparent=document.createElement('canvas');transparent.width=1;transparent.height=1;event.dataTransfer.setDragImage(transparent,0,0);}catch(_error){}}
  function moveDragVisual(event){if(!dragVisual||!event.clientX&&!event.clientY)return;dragVisual.style.left=(event.clientX-dragOffsetX*dragVisualScale)+'px';dragVisual.style.top=(event.clientY-dragOffsetY*dragVisualScale)+'px';}
  function autoPlaceTop(lane,block,layout){var count=directBlocks(lane).length,x=layout&&Number(layout.x),y=layout&&Number(layout.y),previous=lane===flowLane?directBlocks(flowLane).slice(-1)[0]:null;if(!Number.isFinite(x))x=8+(count%3)*22;if(!Number.isFinite(y))y=42+count*58;placeTopBlock(lane,block,x,y,true);if(lane===flowLane&&layout&&layout.connected&&previous&&previous!==block){connectAfter(block,previous);refreshSequenceNumbers();}}
  function setWorkspaceView(view){if(['both','flow','functions'].indexOf(view)<0)view='both';root.dataset.view=view;document.querySelectorAll('[data-entry-view]').forEach(function(button){button.classList.toggle('is-active',button.dataset.entryView===view);});try{localStorage.setItem('seerBlockBuilderView',view);}catch(_error){}window.requestAnimationFrame(syncAdaptiveBlockWidths);}
  function choiceLabel(fieldName,value){
    var maps={source:{di:'DI',emergency:'비상정지',blocked:'주행 막힘',battery:'배터리',current_point:'현재 포인트',charging:'충전 중',motor:'모터',localization:'위치 신뢰도',variable:'변수'},operator:{'==':'=','!=':'≠','>':'>','>=':'≥','<':'<','<=':'≤',add:'+',subtract:'−',multiply:'×',divide:'÷',modulo:'나머지',power:'제곱',min:'최솟값',max:'최댓값',and:'그리고',or:'또는',xor:'둘 중 하나',not:'아니다'}};
    return maps[fieldName]&&maps[fieldName][String(value)]!=null?maps[fieldName][String(value)]:String(value);
  }
  function fieldControlHtml(field,inline){
    var choices=field.choices||[],control='';
    if(field.kind==='choice'||choices.length){control='<select data-value="'+esc(field.name)+'">'+choices.map(function(v){var prefix=(field.name.indexOf('channel')>=0?'DI':(field.name==='id'?'DO':''));return '<option value="'+esc(v)+'"'+(String(v)===String(field.default)?' selected':'')+'>'+esc(prefix+choiceLabel(field.name,v))+'</option>';}).join('')+'</select>';}
    else{var type=(field.kind==='number'||field.kind==='integer')?'number':'text',list=field.kind==='variable_ref'?' list="seer-variable-options"':(field.kind==='function_ref'?' list="seer-function-options"':(field.kind==='operand'?' list="seer-operand-options"':''));if(inline&&['name','result_name','variable_name'].indexOf(field.name)>=0)list=' list="seer-variable-options"';var attrs=type==='number'?' step="'+(field.kind==='integer'?'1':'any')+'"'+(field.min!=null?' min="'+field.min+'"':'')+(field.max!=null?' max="'+field.max+'"':''):list;control='<input type="'+type+'" data-value="'+esc(field.name)+'" value="'+esc(field.default)+'"'+attrs+'>';}
    return control;
  }
  function variableBindingHtml(field){if(!field.variable)return '';return '<label class="seer-variable-row"><input type="checkbox" data-variable-toggle="'+esc(field.name)+'"> Actions 실행값 <input class="seer-variable-name" data-variable="'+esc(field.name)+'" placeholder="예: '+esc(field.name)+'" disabled></label>';}
  function fieldHtml(field,index){
    return '<label class="seer-builder-field" data-builder-field="'+esc(field.name)+'"><span>'+esc(field.label)+'</span>'+fieldControlHtml(field,false)+variableBindingHtml(field)+(field.note?'<small class="seer-api-note">'+esc(field.note)+'</small>':'')+'</label>';
  }
  function schemaField(schema,name){return (schema.fields||[]).find(function(field){return field.name===name;});}
  function inlineField(schema,name,extraClass){var field=schemaField(schema,name);return field?'<span class="seer-entry-inline-field '+(extraClass||'')+'" data-builder-field="'+esc(name)+'">'+fieldControlHtml(field,true)+'</span>':'';}
  function conditionSentence(schema,ending){return '<span class="seer-entry-boolean">'+inlineField(schema,'source')+inlineField(schema,'channel')+inlineField(schema,'variable_name')+inlineField(schema,'operator')+inlineField(schema,'expected')+'</span><span class="seer-entry-word">'+ending+'</span>';}
  function inlineFieldNames(kind){
    var names={repeat:['count'],repeat_until:['source','channel','variable_name','operator','expected'],if:['source','channel','variable_name','operator','expected'],if_else:['source','channel','variable_name','operator','expected'],wait_until:['source','channel','variable_name','operator','expected'],set_variable:['name','value'],change_variable:['name','amount'],calculate:['result_name','left','operator','right'],judge:['result_name','left','operator','right'],logic:['result_name','left','operator','right']};
    return names[kind]||[];
  }
  function inlineSentence(kind,schema){
    if(kind==='repeat')return inlineField(schema,'count')+'<span class="seer-entry-word">번 반복하기</span>';
    if(kind==='repeat_until')return conditionSentence(schema,'이 될 때까지 반복하기');
    if(kind==='if')return '<span class="seer-entry-word">만일</span>'+conditionSentence(schema,'이라면');
    if(kind==='if_else')return '<span class="seer-entry-word">만일</span>'+conditionSentence(schema,'이라면 / 아니면');
    if(kind==='wait_until')return conditionSentence(schema,'이 될 때까지 기다리기');
    if(kind==='set_variable')return inlineField(schema,'name')+'<span class="seer-entry-word">을(를)</span>'+inlineField(schema,'value')+'<span class="seer-entry-word">으로 정하기</span>';
    if(kind==='change_variable')return inlineField(schema,'name')+'<span class="seer-entry-word">에</span>'+inlineField(schema,'amount')+'<span class="seer-entry-word">만큼 더하기</span>';
    if(kind==='calculate')return inlineField(schema,'result_name')+'<span class="seer-entry-word">을(를)</span>'+inlineField(schema,'left')+inlineField(schema,'operator')+inlineField(schema,'right')+'<span class="seer-entry-word">계산값으로 정하기</span>';
    if(kind==='judge')return inlineField(schema,'result_name')+'<span class="seer-entry-word">을(를)</span><span class="seer-entry-boolean">'+inlineField(schema,'left')+inlineField(schema,'operator')+inlineField(schema,'right')+'</span><span class="seer-entry-word">판단값으로 정하기</span>';
    if(kind==='logic')return inlineField(schema,'result_name')+'<span class="seer-entry-word">을(를)</span><span class="seer-entry-boolean">'+inlineField(schema,'left')+inlineField(schema,'operator')+inlineField(schema,'right')+'</span><span class="seer-entry-word">논리값으로 정하기</span>';
    return '';
  }
  function advancedBindingsHtml(schema,names){var rows=(schema.fields||[]).filter(function(field){return names.indexOf(field.name)>=0&&field.variable;}).map(variableBindingHtml).join('');return rows?'<div class="seer-entry-advanced-bindings"><small>고급: 이 값을 Actions 실행창에서 바꾸려면 체크하세요.</small>'+rows+'</div>':'';}
  function blockValue(block,name){var input=block.querySelector(':scope > .seer-program-head [data-value="'+name+'"], :scope > .seer-block-fields [data-value="'+name+'"]');return input?input.value:'';}
  function blockSummary(block){
    var kind=block.dataset.kind,schema=byKind[kind]||{},v=function(name){return blockValue(block,name);};
    if(kind==='coordinate')return 'x '+v('x')+' · y '+v('y')+' · '+v('theta_deg')+'°';
    if(kind==='translate')return v('distance_m')+'m · '+v('linear_speed_mps')+'m/s · '+v('lateral');
    if(kind==='rotate')return v('angle_deg')+'° · '+v('angular_speed_deg_s')+'°/s';
    if(kind==='path_nav'){var base=(v('source_id')||'SELF_POSITION')+' → '+v('id'),route=String(v('route_points')||'').trim(),wait=Number(v('waypoint_delay_sec')||0),parts=[base];if(route){var points=route.split(',').map(function(item){return item.trim();}).filter(Boolean);parts.push(points.length+'포인트 경로');}else parts.push('경로 자동');if(wait>0)parts.push('포인트 대기 '+wait+'s');return parts.join(' · ');}
    if(kind==='set_do')return 'DO'+v('id')+' '+String(v('status')).toUpperCase();
    if(kind==='pulse_do')return 'DO'+v('id')+' '+v('seconds')+'s';
    if(kind==='send_do_wait_di')return 'DO'+v('do_id')+' → DI'+v('di_channel')+' '+v('di_mode');
    if(kind==='wait_di'||kind==='on_di')return 'DI'+v('channel')+' '+v('mode');
    if(kind==='wait')return v('seconds')+'s';
    if(kind==='repeat')return v('count')+'회';
    if(kind==='forever')return '중단 또는 취소까지';
    if(kind==='repeat_until'||kind==='if'||kind==='if_else'||kind==='wait_until')return v('source')+(v('source')==='di'?v('channel'):(v('source')==='variable'?' '+v('variable_name'):''))+' '+v('operator')+' '+v('expected');
    if(kind==='switch_map')return v('map_name');
    if(kind==='relocate')return v('mode')+(v('mode')==='manual'?' · '+v('x')+', '+v('y')+', '+v('theta_deg')+'°':'');
    if(kind==='set_motor')return v('target')+(v('target')==='named'?' '+v('motor_name'):'')+' '+String(v('status')).toUpperCase();
    if(kind==='set_variable')return v('name')+' = '+v('value');
    if(kind==='change_variable')return v('name')+' + '+v('amount');
    if(kind==='delete_variable')return v('name');
    if(kind==='judge'||kind==='logic')return v('result_name')+' ← '+v('left')+' '+v('operator')+' '+v('right');
    if(kind==='calculate')return v('result_name')+' ← '+v('left')+' '+v('operator')+' '+v('right');
    if(kind==='read_state')return v('result_name')+' ← '+v('source')+(v('source')==='di'?v('channel'):'');
    if(kind==='define_function')return v('function_name');
    if(kind==='call_function')return v('function_name');
    if(kind==='log')return v('message');
    return (schema.api||'').replace(/^.*?·\s*/, '');
  }
  function updateBlockSummary(block){var summary=block&&block.querySelector(':scope > .seer-program-head [data-block-summary]');if(summary)summary.textContent=blockSummary(block);}
  function updateConditionalFields(block){var source=blockValue(block,'source');if(!source)return;var channel=block.querySelector('[data-builder-field="channel"]'),variableName=block.querySelector('[data-builder-field="variable_name"]');if(channel)channel.hidden=source!=='di';if(variableName)variableName.hidden=source!=='variable';var capsule=block.querySelector(':scope > .seer-program-head .seer-entry-boolean');if(capsule)sizeConditionCapsule(capsule);}
  function refreshReferenceLists(){
    var variables=[],functions=[];function addUnique(list,value){value=String(value||'').trim();if(/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(value)&&list.indexOf(value)<0)list.push(value);}
    document.querySelectorAll('.seer-program-block').forEach(function(block){var kind=block.dataset.kind;if(['set_variable','change_variable','judge','logic','calculate','read_state'].indexOf(kind)>=0)addUnique(variables,blockValue(block,kind==='set_variable'||kind==='change_variable'?'name':'result_name'));if(kind==='define_function')addUnique(functions,blockValue(block,'function_name'));block.querySelectorAll(':scope > .seer-block-fields [data-variable]').forEach(function(input){var toggle=block.querySelector(':scope > .seer-block-fields [data-variable-toggle="'+input.dataset.variable+'"]');if(toggle&&toggle.checked)addUnique(variables,input.value);});});
    variables.sort();functions.sort();var variableList=document.getElementById('seer-variable-options'),operandList=document.getElementById('seer-operand-options'),functionList=document.getElementById('seer-function-options'),customPalette=document.getElementById('seer-custom-function-palette');if(variableList)variableList.innerHTML=variables.map(function(v){return '<option value="'+esc(v)+'"></option>';}).join('');if(operandList)operandList.innerHTML=variables.map(function(v){return '<option value="$'+esc(v)+'"></option>';}).join('');if(functionList)functionList.innerHTML=functions.map(function(v){return '<option value="'+esc(v)+'"></option>';}).join('');if(customPalette)customPalette.innerHTML=functions.map(function(v){return '<button type="button" draggable="true" style="--block-color:#4c59b0" data-entry-add-block="call_function" data-entry-function="'+esc(v)+'"><strong>'+esc(v)+' 함수 실행</strong><small>사용자 함수</small></button>';}).join('');
  }
  function dropzone(label,branch){return '<span class="seer-branch-label">'+label+'</span><div class="seer-block-dropzone" data-child-zone="'+branch+'"><div class="seer-block-placeholder">여기에 블록을 끌어 놓으세요</div></div>';}
  function updatePlaceholders(){document.querySelectorAll('.seer-block-dropzone').forEach(function(zone){var p=zone.querySelector(':scope > .seer-block-placeholder');if(p)p.style.display=directBlocks(zone).length?'none':'block';});syncEmpty();refreshSequenceNumbers();window.requestAnimationFrame(syncAdaptiveBlockWidths);}
  function makeBlock(kind,initial){
    var schema=byKind[kind];if(!schema)return null;var block=document.createElement('section');blockCounter++;block.className='seer-program-block'+(schema.container?' seer-control-block':'');block.dataset.kind=kind;block.dataset.blockId='seer-entry-'+blockCounter;block.style.setProperty('--block-color',schema.color);
    var index=Date.now()+blockCounter,routeTool=kind==='path_nav'?'<div class="seer-route-tool"><button type="button" class="btn" data-open-route-picker>🗺 지도에서 지정 경로 선택</button><small class="seer-api-note">유효한 방향 경로와 이동거리를 지도에서 선택합니다.</small></div>':'',delayValue=initial&&initial.delay_sec!=null?initial.delay_sec:0;
    var inlineNames=inlineFieldNames(kind),sentence=inlineSentence(kind,schema);if(sentence)block.dataset.entryInline='true';
    var detailFields=(schema.fields||[]).filter(function(field){return inlineNames.indexOf(field.name)<0;}).map(function(f){return fieldHtml(f,index);}).join('')+advancedBindingsHtml(schema,inlineNames);
    var delay=(['break_loop','continue_loop','stop_program','restart_program','define_function'].indexOf(kind)>=0)?'':'<label class="seer-builder-field seer-step-delay"><span>이 블록 완료 후 대기 (초)</span><input type="number" data-step-delay min="0" max="3600" step="0.1" value="'+esc(delayValue)+'"><small class="seer-api-note">기본 0초 · 완료 즉시 다음 블록 실행</small></label>';
    var configButton=(detailFields||delay||routeTool)?'<button type="button" class="btn" data-toggle-config title="고급 설정 펼치기" aria-expanded="false">⚙</button>':'';
    var childLabel=(kind==='repeat'||kind==='forever'||kind==='repeat_until')?'반복할 블록':(kind==='on_di'?'신호를 받은 뒤':(kind==='define_function'?'함수 내용':'참일 때'));
    var titleBody='<span class="seer-runtime-chip" data-runtime-chip></span>'+(sentence?'<span class="seer-entry-sentence">'+sentence+'</span>':'<strong>'+esc(schema.label)+'</strong><small class="seer-block-summary" data-block-summary></small>');
    block.innerHTML='<div class="seer-program-head" draggable="true" title="'+esc(schema.api)+'"><div class="seer-block-title"><select class="seer-sequence-badge" data-sequence title="실행 번호 변경" aria-label="실행 순서"></select>'+titleBody+'</div><div class="seer-block-actions">'+configButton+'<button type="button" class="btn" data-run-from-block title="이 블록부터 마지막까지 실행">여기▶</button><button type="button" class="btn" data-move="up" title="실행 순서 위로">↑</button><button type="button" class="btn" data-move="down" title="실행 순서 아래로">↓</button><button type="button" class="btn danger" data-remove title="삭제">×</button></div></div><div class="seer-block-fields">'+detailFields+delay+routeTool+'</div>'+(schema.container?dropzone(childLabel,'children')+(schema.has_else?dropzone('아니면','else_children'):''):'');
    if(initial){Object.keys(initial.values||{}).forEach(function(name){var input=block.querySelector('[data-value="'+name+'"]');if(input)input.value=initial.values[name];});Object.keys(initial.variables||{}).forEach(function(name){var toggle=block.querySelector('[data-variable-toggle="'+name+'"]'),input=block.querySelector('[data-variable="'+name+'"]');if(toggle&&input){toggle.checked=true;input.disabled=false;input.value=initial.variables[name];}});}
    if(schema.container&&initial){appendNodes(block.querySelector('[data-child-zone="children"]'),initial.children||[]);if(schema.has_else)appendNodes(block.querySelector('[data-child-zone="else_children"]'),initial.else_children||[]);}
    updateBlockSummary(block);updateConditionalFields(block);sizeAllEntryInputs(block);window.setTimeout(refreshReferenceLists,0);
    return block;
  }
  function appendNodes(zone,nodes){(nodes||[]).forEach(function(node){var block=makeBlock(node.kind,node);if(!block)return;if(isTopLane(zone))autoPlaceTop(laneForKind(node.kind),block,node.layout);else zone.appendChild(block);});updatePlaceholders();}
  function loadBuilderExample(key){var example=exampleByKey[key],definition=example&&example.definition;if(!definition||runtimeLocked)return;var existing=directBlocks(flowLane).length+directBlocks(functionLane).length;if(existing&&!window.confirm('현재 조립 중인 블록을 지우고 이 예제를 불러오시겠습니까? 저장하지 않은 내용은 사라집니다.'))return;directBlocks(flowLane).concat(directBlocks(functionLane)).forEach(function(block){block.remove();});var original=form&&form.querySelector('[name="original_recipe_name"]');if(original)original.remove();if(form&&form.elements.recipe_name)form.elements.recipe_name.value=definition.name||'seerExampleAction';if(form&&form.elements.recipe_label)form.elements.recipe_label.value=definition.label||'SEER Example Action';var submit=form&&form.querySelector('.seer-builder-footer button[type="submit"]');if(submit)submit.textContent='Recipe 저장 및 적용';(definition.blocks||[]).forEach(function(node){var block=makeBlock(node.kind,node);if(block)autoPlaceTop(laneForKind(node.kind),block,node.layout);});activeZone=flowLane;setWorkspaceView('both');updatePlaceholders();refreshReferenceLists();setBuilderDirty(true);var status=document.getElementById('seer-example-status');if(status)status.textContent='“'+(example.title||definition.name)+'” 예제를 불러왔습니다. 값과 Recipe 이름을 확인한 뒤 저장하세요.';var shell=document.querySelector('.seer-entry-shell');if(shell&&shell.scrollIntoView)shell.scrollIntoView({behavior:'smooth',block:'start'});}
  function insertNestedBlock(zone,block,clientY){block.style.position='';block.style.left='';block.style.top='';delete block.dataset.layoutX;delete block.dataset.layoutY;var before=directBlocks(zone).find(function(item){var r=item.getBoundingClientRect();return clientY<r.top+r.height/2;});if(before)zone.insertBefore(block,before);else zone.appendChild(block);updatePlaceholders();}
  function paletteInitial(button){var name=button&&button.dataset.entryFunction;return name?{values:{function_name:name},variables:{},delay_sec:0}:null;}
  function addFromPalette(button){if(runtimeLocked)return;var kind=button.dataset.entryAddBlock,block=makeBlock(kind,paletteInitial(button));if(!block)return;var zone=activeZone&&document.contains(activeZone)?activeZone:laneForKind(kind);if(isTopLane(zone)){var lane=laneForKind(kind);autoPlaceTop(lane,block,null);activeZone=lane;}else if(kind==='define_function'){autoPlaceTop(functionLane,block,null);activeZone=functionLane;}else{insertNestedBlock(zone,block,Number.MAX_SAFE_INTEGER);}updatePlaceholders();setBuilderDirty(true);}
  document.addEventListener('click',function(event){
    var recipeDelete=event.target.closest&&event.target.closest('[data-delete-recipe]');if(recipeDelete){event.preventDefault();setDeleteModalOpen(true,recipeDelete.dataset.deleteRecipe||'');return;}
    var deleteClose=event.target.closest&&event.target.closest('[data-close-delete-modal]');if(deleteClose){event.preventDefault();setDeleteModalOpen(false);return;}
    if(event.target===deleteModal){setDeleteModalOpen(false);return;}
    var manualOpen=event.target.closest&&event.target.closest('[data-open-builder-manual]');if(manualOpen){setBuilderManualOpen(true);return;}
    if(event.target===manualModal){setBuilderManualOpen(false);return;}
    var manualClose=event.target.closest&&event.target.closest('[data-close-builder-manual]');if(manualClose){setBuilderManualOpen(false);return;}
    var manualLink=event.target.closest&&event.target.closest('[data-builder-manual-link]');if(manualLink){event.preventDefault();var chapter=document.querySelector(manualLink.getAttribute('href'));if(chapter)chapter.scrollIntoView({behavior:'smooth',block:'start'});return;}
    var runFrom=event.target.closest&&event.target.closest('[data-run-from-block]');if(runFrom){event.preventDefault();if(runtimeLocked)return;assignRuntimeTraceIds();var runBlock=runFrom.closest('.seer-program-block');if(!runBlock||runBlock.parentElement!==flowLane)return;handleRuntimeControl('run_from',runFrom,runBlock.dataset.runtimeTraceId||'');return;}
    var runtimeControl=event.target.closest&&event.target.closest('[data-builder-control]');if(runtimeControl){handleRuntimeControl(runtimeControl.dataset.builderControl,runtimeControl,'');return;}
    if(event.target.closest&&event.target.closest('[data-route-picker-apply]')){if(!runtimeLocked)setBuilderDirty(true);return;}
    if(event.target.closest&&event.target.closest('[data-remove],[data-move]')){if(runtimeLocked){event.preventDefault();return;}setBuilderDirty(true);window.setTimeout(function(){updatePlaceholders();refreshReferenceLists();},0);return;}
    var exampleButton=event.target.closest&&event.target.closest('[data-load-builder-example]');if(exampleButton){loadBuilderExample(exampleButton.dataset.loadBuilderExample);return;}
    var view=event.target.closest&&event.target.closest('[data-entry-view]');if(view){setWorkspaceView(view.dataset.entryView);return;}
    var sequence=event.target.closest&&event.target.closest('[data-sequence]');if(sequence){if(runtimeLocked)event.preventDefault();return;}
    var toggleConfig=event.target.closest&&event.target.closest('.seer-block-actions [data-toggle-config]');if(toggleConfig){var configBlock=toggleConfig.closest('.seer-program-block');if(configBlock){var followers=configBlock.parentElement===flowLane?connectedFollowers(configBlock):[],oldHeight=configBlock.offsetHeight;configBlock.classList.toggle('is-expanded');var delta=configBlock.offsetHeight-oldHeight;if(delta)followers.forEach(function(follower){setTopCoordinates(follower,Number(follower.dataset.layoutX||8),Number(follower.dataset.layoutY||42)+delta);});var button=configBlock.querySelector(':scope > .seer-program-head [data-toggle-config].btn');if(button){var expanded=configBlock.classList.contains('is-expanded');button.setAttribute('aria-expanded',expanded?'true':'false');button.title=expanded?'설정 접기':'설정 펼치기';}refreshSequenceNumbers();}return;}
    var selectedZone=event.target.closest&&event.target.closest('.seer-block-dropzone,.seer-top-lane');if(selectedZone&&(event.target===selectedZone||(event.target.closest&&event.target.closest('.seer-block-placeholder,.seer-lane-empty')))){activeZone=selectedZone;document.querySelectorAll('.is-insert-target').forEach(function(z){z.classList.remove('is-insert-target');});activeZone.classList.add('is-insert-target');return;}
    var category=event.target.closest&&event.target.closest('[data-entry-category]');if(category){document.querySelectorAll('[data-entry-category]').forEach(function(b){b.classList.toggle('is-active',b===category);});document.querySelectorAll('[data-entry-palette-category]').forEach(function(p){p.hidden=p.dataset.entryPaletteCategory!==category.dataset.entryCategory;});return;}
    var add=event.target.closest&&event.target.closest('[data-entry-add-block]');if(add){addFromPalette(add);return;}
    var zoom=event.target.closest&&event.target.closest('[data-entry-zoom]');if(zoom){workspaceZoom=Math.max(60,Math.min(200,workspaceZoom+Number(zoom.dataset.entryZoom)));root.style.zoom=workspaceZoom+'%';var out=document.getElementById('seer-entry-zoom-value');if(out)out.value=workspaceZoom+'%';return;}
    if(event.target.closest&&event.target.closest('[data-entry-fit]')){workspaceZoom=100;root.style.zoom='100%';var out=document.getElementById('seer-entry-zoom-value');if(out)out.value='100%';}
  });
  document.addEventListener('keydown',function(event){if(event.key==='Escape'&&deleteModal&&deleteModal.classList.contains('is-open')){event.preventDefault();setDeleteModalOpen(false);return;}if(event.key==='Escape'&&manualModal&&manualModal.classList.contains('is-open'))setBuilderManualOpen(false);});
  document.addEventListener('dragstart',function(event){var palette=event.target.closest&&event.target.closest('[data-entry-add-block]');if(palette){dragKind=palette.dataset.entryAddBlock;dragFunction=palette.dataset.entryFunction||'';dragBlock=null;dragGroup=[];var paletteRect=palette.getBoundingClientRect();dragOffsetX=event.clientX-paletteRect.left;dragOffsetY=event.clientY-paletteRect.top;showDragVisual(palette,event,1);event.dataTransfer.effectAllowed='copy';event.dataTransfer.setData('text/plain','new:'+dragKind);return;}if(event.target.closest&&event.target.closest('.seer-entry-inline-field input,.seer-entry-inline-field select,.seer-block-actions button')){event.preventDefault();return;}var head=event.target.closest&&event.target.closest('.seer-program-head');if(head){dragBlock=head.closest('.seer-program-block');dragKind='';dragFunction='';var rect=dragBlock.getBoundingClientRect(),scale=workspaceZoom/100,baseX=Number(dragBlock.dataset.layoutX||0),baseY=Number(dragBlock.dataset.layoutY||0);dragGroup=dragBlock.parentElement===flowLane?connectedFollowers(dragBlock).map(function(follower){return {block:follower,x:Number(follower.dataset.layoutX||0)-baseX,y:Number(follower.dataset.layoutY||0)-baseY};}):[];dragOffsetX=(event.clientX-rect.left)/scale;dragOffsetY=(event.clientY-rect.top)/scale;showDragVisual(dragBlock,event,scale,dragGroup);event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain','move:'+dragBlock.dataset.blockId);}});
  document.addEventListener('drag',moveDragVisual);
  document.addEventListener('dragend',function(){dragKind='';dragFunction='';dragBlock=null;dragGroup=[];removeDragVisual();document.querySelectorAll('.is-drag-over,.is-snap-candidate').forEach(function(n){n.classList.remove('is-drag-over','is-snap-candidate');});});
  document.addEventListener('dragover',function(event){var zone=event.target.closest&&event.target.closest('.seer-block-dropzone,.seer-top-lane');if(!zone||dragBlock&&dragBlock.contains(zone))return;event.preventDefault();zone.classList.add('is-drag-over');event.dataTransfer.dropEffect=dragBlock?'move':'copy';document.querySelectorAll('.is-snap-candidate').forEach(function(n){n.classList.remove('is-snap-candidate');});if(zone===flowLane&&(dragKind!=='define_function')&&(!dragBlock||dragBlock.dataset.kind!=='define_function')){var rect=flowLane.getBoundingClientRect(),scale=workspaceZoom/100,x=(event.clientX-rect.left)/scale-dragOffsetX,y=(event.clientY-rect.top)/scale-dragOffsetY,best=findFlowSnap(dragBlock,x,y,dragGroup.map(function(item){return item.block;}));if(best)best.target.classList.add('is-snap-candidate');}});
  document.addEventListener('dragleave',function(event){var zone=event.target.closest&&event.target.closest('.seer-block-dropzone,.seer-top-lane');if(zone&&!zone.contains(event.relatedTarget))zone.classList.remove('is-drag-over');});
  document.addEventListener('drop',function(event){var zone=event.target.closest&&event.target.closest('.seer-block-dropzone,.seer-top-lane');if(!zone)return;event.preventDefault();zone.classList.remove('is-drag-over');document.querySelectorAll('.is-snap-candidate').forEach(function(n){n.classList.remove('is-snap-candidate');});var initial=dragFunction?{values:{function_name:dragFunction},variables:{},delay_sec:0}:null,block=dragBlock||makeBlock(dragKind,initial),group=dragGroup.slice();if(block&&!block.contains(zone)){if(isTopLane(zone)||block.dataset.kind==='define_function'){var lane=laneForKind(block.dataset.kind),rect=lane.getBoundingClientRect(),scale=workspaceZoom/100,wasSameLane=block.parentElement===lane&&isTopLane(block.parentElement),x=(event.clientX-rect.left)/scale-dragOffsetX,y=(event.clientY-rect.top)/scale-dragOffsetY;block.dataset.connected='false';placeTopBlock(lane,block,x,y,wasSameLane);if(lane===flowLane)snapFlowBlock(block,Number(block.dataset.layoutX||8),Number(block.dataset.layoutY||42),group.map(function(item){return item.block;}));if(lane===flowLane&&group.length)moveFollowersAfter(block,group.map(function(item){return item.block;}),group);zone=lane;}else{group.forEach(function(item){item.block.dataset.connected='false';});insertNestedBlock(zone,block,event.clientY);}activeZone=zone;document.querySelectorAll('.is-insert-target').forEach(function(z){z.classList.remove('is-insert-target');});zone.classList.add('is-insert-target');}dragKind='';dragFunction='';dragBlock=null;dragGroup=[];removeDragVisual();updatePlaceholders();refreshReferenceLists();setBuilderDirty(true);});
  document.addEventListener('input',function(event){if(runtimeLocked)return;sizeEntryControl(event.target);var block=event.target.closest&&event.target.closest('.seer-program-block');if(block){updateBlockSummary(block);updateConditionalFields(block);refreshReferenceLists();window.requestAnimationFrame(syncAdaptiveBlockWidths);setBuilderDirty(true);return;}if(event.target.closest&&event.target.closest('#seer-block-form .command-fields'))setBuilderDirty(true);});
  document.addEventListener('change',function(event){if(event.target&&event.target.id==='seer-runtime-follow')return;if(runtimeLocked){event.preventDefault();return;}sizeEntryControl(event.target);var sequence=event.target.closest&&event.target.closest('[data-sequence]');if(sequence){var orderBlock=sequence.closest('.seer-program-block'),blocks=directBlocks(flowLane),current=blocks.indexOf(orderBlock)+1,wanted=Number(sequence.value);if(orderBlock&&orderBlock.parentElement===flowLane&&Number.isInteger(wanted)&&wanted>=1&&wanted<=blocks.length&&wanted!==current){orderBlock.dataset.connected='false';var remaining=blocks.filter(function(item){return item!==orderBlock;}),before=remaining[wanted-1]||null;flowLane.insertBefore(orderBlock,before);refreshSequenceNumbers();setBuilderDirty(true);}window.requestAnimationFrame(syncAdaptiveBlockWidths);return;}var block=event.target.closest&&event.target.closest('.seer-program-block');if(block){updateBlockSummary(block);updateConditionalFields(block);setBuilderDirty(true);}var toggle=event.target.closest&&event.target.closest('[data-variable-toggle]');if(toggle){var row=toggle.closest('.seer-variable-row'),input=row&&row.querySelector('[data-variable]');if(input){input.disabled=!toggle.checked;if(toggle.checked&&!input.value)input.value=toggle.dataset.variableToggle;}}if(event.target.closest&&event.target.closest('#seer-block-form .command-fields'))setBuilderDirty(true);refreshReferenceLists();window.requestAnimationFrame(syncAdaptiveBlockWidths);});
  function serializeZone(zone){return directBlocks(zone).map(function(block){var values={},variables={};block.querySelectorAll(':scope > .seer-program-head [data-value], :scope > .seer-block-fields [data-value]').forEach(function(input){values[input.dataset.value]=input.value;});block.querySelectorAll(':scope > .seer-block-fields [data-variable]').forEach(function(input){var toggle=block.querySelector(':scope > .seer-block-fields [data-variable-toggle="'+input.dataset.variable+'"]');if(toggle&&toggle.checked)variables[input.dataset.variable]=input.value;});var delay=block.querySelector(':scope > .seer-block-fields [data-step-delay]'),node={kind:block.dataset.kind,values:values,variables:variables,delay_sec:delay?delay.value:'0'},children=block.querySelector(':scope > [data-child-zone="children"]'),otherwise=block.querySelector(':scope > [data-child-zone="else_children"]');if(isTopLane(zone)){node.layout={x:Number(block.dataset.layoutX||0),y:Number(block.dataset.layoutY||42)};if(zone===flowLane&&block.dataset.connected==='true')node.layout.connected=true;}if(children)node.children=serializeZone(children);if(otherwise)node.else_children=serializeZone(otherwise);return node;});}
  function builderDefinition(){var blocks=serializeZone(functionLane).concat(serializeZone(flowLane));if(!blocks.length)throw new Error('블록을 하나 이상 추가하세요.');return {name:form.elements.recipe_name.value,label:form.elements.recipe_label.value,blocks:blocks};}
  function prepareBuilderNativeSubmit(event){
    if(saveBusy||runtimeLocked){event.preventDefault();return;}
    try{
      var definition=builderDefinition(),definitionNode=document.getElementById('seer-block-definition');
      definitionNode.value=JSON.stringify(definition);
      saveBusy=true;
      updateRuntimeControlButtons();
      setRuntimeControlNote('블록코딩 저장 및 Adapter 적용 중…','');
      var submit=form.querySelector('.seer-builder-footer button[type="submit"]');
      if(submit){submit.disabled=true;submit.setAttribute('aria-busy','true');submit.textContent='저장 및 적용 중…';}
      // Do not prevent the normal POST.  A full redirect/reload after save is
      // intentional: it refreshes the per-AMR managed Recipe list, edit name,
      // delete links, runtime config, and Adapter-applied action registry from
      // the same recipes.hcl snapshot.  The old AJAX path left those pieces
      // stale in the browser after a successful save.
    }catch(error){
      event.preventDefault();
      saveBusy=false;
      updateRuntimeControlButtons();
      setRuntimeControlNote(error&&error.message?error.message:String(error),'is-dirty');
    }
  }
  var form=document.getElementById('seer-block-form');if(form)form.addEventListener('submit',prepareBuilderNativeSubmit);
  var initialNode=document.getElementById('seer-entry-initial');if(initialNode){try{var initial=JSON.parse(initialNode.textContent);(initial.blocks||[]).forEach(function(node){var block=makeBlock(node.kind,node);if(block)autoPlaceTop(laneForKind(node.kind),block,node.layout);});}catch(error){console.error(error);}}
  var initialView='both';try{initialView=localStorage.getItem('seerBlockBuilderView')||'both';}catch(_error){}setWorkspaceView(initialView);sizeAllEntryInputs(root);updatePlaceholders();refreshReferenceLists();window.requestAnimationFrame(syncAdaptiveBlockWidths);window.addEventListener('resize',function(){window.requestAnimationFrame(syncAdaptiveBlockWidths);});updateRuntimeControlButtons();startRuntimeMonitor();
})();
</script>
"""


def render_builder_page(
    render: Any,
    csrf: str,
    q: Mapping[str, Any],
    recipes_path: Path,
    *,
    map_cache_path: Optional[Path] = None,
    map_snapshot: Any = None,
    map_position_unit: str = "mm",
    map_robot_key: str = "",
    map_robot_options: Sequence[Tuple[str, str]] = (),
) -> str:
    text = ""
    load_error = ""
    try:
        text = Path(recipes_path).read_text(encoding="utf-8")
        managed = managed_recipe_names(text)
    except OSError as exc:
        managed = ()
        load_error = f"recipes.hcl을 읽을 수 없습니다: {exc}"
    edit_name = str((q or {}).get("edit", "") or "").strip()
    delete_name = str((q or {}).get("delete", "") or "").strip()
    definition: Dict[str, Any] = {
        # A new Recipe must look new.  Using seerCustomAction here made a
        # successfully deleted Recipe appear to come back because the blank
        # editor immediately showed the same name again.
        "name": "",
        "label": "",
        "blocks": [],
    }
    if edit_name:
        try:
            definition = managed_recipe_definition(text, edit_name)
        except RecipeBuilderError as exc:
            load_error = str(exc)
            edit_name = ""
    if delete_name and delete_name not in managed:
        load_error = f"{delete_name}은 Block Builder 관리 Recipe가 아닙니다"
        delete_name = ""
    route_map: Dict[str, Any] = {}
    if map_cache_path is not None:
        from .map_view import recipe_route_picker_payload

        route_map = recipe_route_picker_payload(
            Path(map_cache_path),
            map_snapshot,
            position_unit=map_position_unit,
        )
    categories = (
        ("movement", "↗", "이동"),
        ("flow", "↻", "흐름"),
        ("io", "◉", "IO"),
        ("logic", "◇", "판단"),
        ("math", "±", "계산"),
        ("data", "◆", "자료"),
        ("function", "ƒ", "함수"),
        ("system", "⚙", "장비"),
    )

    palette = "".join(
        f'<div class="seer-entry-palette" data-entry-palette-category="{category}"'
        f'{"" if index == 0 else " hidden"}>'
        + "".join(
            f'<button type="button" draggable="true" style="--block-color:{spec.color}" '
            f'data-entry-add-block="{html.escape(spec.kind, quote=True)}">'
            f'<strong>{html.escape(spec.label)}</strong><small>{html.escape(spec.api)}</small></button>'
            for spec in (*BLOCK_SPECS, *CONTROL_SPECS)
            if _spec_category(spec) == category
        )
        + (
            '<div id="seer-custom-function-palette" class="seer-custom-function-palette" '
            'aria-live="polite"></div>'
            if category == "function"
            else ""
        )
        + "</div>"
        for index, (category, _icon, _label) in enumerate(categories)
    )
    category_rail = "".join(
        f'<button type="button" class="seer-entry-category'
        f'{" is-active" if index == 0 else ""}" data-entry-category="{category}">'
        f'<span>{icon}</span>{label}</button>'
        for index, (category, icon, label) in enumerate(categories)
    )
    def _builder_query(**values: str) -> str:
        params = {key: value for key, value in values.items() if value}
        if map_robot_key:
            params["robot"] = map_robot_key
        return urllib.parse.urlencode(params)

    chips = "".join(
        '<div class="seer-managed-item"><strong>'
        f'{html.escape(name)}</strong><span class="seer-managed-actions">'
        f'<a class="btn" href="/recipe-builder?{_builder_query(edit=name)}">편집</a>'
        f'<a class="btn danger" data-delete-recipe="{html.escape(name, quote=True)}" href="/recipe-builder?{_builder_query(delete=name)}">삭제</a>'
        '</span></div>'
        for name in managed
    ) or '<span class="muted">이 AMR에는 아직 Block Builder Recipe가 없습니다.</span>'
    delete_modal_open = bool(delete_name)
    delete_modal = (
        '<div id="seer-delete-modal" class="seer-delete-modal'
        + (' is-open' if delete_modal_open else '')
        + '" role="dialog" aria-modal="true" aria-hidden="'
        + ('false' if delete_modal_open else 'true')
        + '" aria-labelledby="seer-delete-modal-title">'
        '<div class="seer-delete-dialog">'
        '<h3 id="seer-delete-modal-title">Recipe 삭제</h3>'
        '<p><strong data-delete-recipe-name>'
        + html.escape(delete_name)
        + '</strong> Recipe를 삭제하시겠습니까?</p>'
        '<p class="seer-delete-note">예를 누르면 저장된 블록코딩을 삭제하고 선택한 AMR의 Adapter에 바로 적용합니다.</p>'
        '<form method="post" action="/recipe-builder" data-seer-builder-delete-form>'
        f'<input type="hidden" name="csrf_token" value="{html.escape(csrf, quote=True)}">'
        '<input type="hidden" name="operation" value="delete">'
        f'<input type="hidden" name="recipe_name" data-delete-recipe-input value="{html.escape(delete_name, quote=True)}">'
        f'<input type="hidden" name="robot" value="{html.escape(map_robot_key, quote=True)}">'
        '<div class="seer-delete-actions"><button type="submit" class="btn danger">예, 삭제</button>'
        '<button type="button" class="btn" data-close-delete-modal>아니오</button></div>'
        '</form></div></div>'
    )
    editing = bool(edit_name)
    initial_json = json.dumps(definition, ensure_ascii=False).replace("</", "<\\/")
    route_map_json = json.dumps(route_map, ensure_ascii=False).replace("</", "<\\/")
    examples_json = json.dumps(BUILDER_EXAMPLES, ensure_ascii=False).replace("</", "<\\/")
    example_cards = "".join(
        '<article class="seer-example-card">'
        f'<h3>{html.escape(str(example["title"]))}</h3>'
        f'<p>{html.escape(str(example["description"]))}</p>'
        '<div class="seer-example-tags">'
        + "".join(
            f'<span class="seer-example-tag">{html.escape(str(tag))}</span>'
            for tag in example["tags"]
        )
        + f'<span class="seer-example-tag">{len(example["definition"]["blocks"])}개 최상위 블록</span>'
        '</div>'
        f'<p class="seer-example-note">확인: {html.escape(str(example["note"]))}</p>'
        f'<button type="button" class="btn" data-load-builder-example="{html.escape(str(example["key"]), quote=True)}">'
        '예제로 불러오기</button></article>'
        for example in BUILDER_EXAMPLES
    )
    learning_panel = (
        '<section class="seer-builder-learning" aria-label="Block Builder 도움말과 예제">'
        '<div class="seer-builder-learning-tools"><div><strong>📘 그림으로 보는 Block Builder 매뉴얼</strong>'
        '<small>블록 추가부터 변수·조건·함수·Path Nav·실행까지 전용 창에서 확인합니다.</small></div>'
        '<button type="button" class="btn seer-manual-launch" data-open-builder-manual>'
        '매뉴얼 열기</button></div>'
        '<details class="seer-learning-panel"><summary>🧩 예제 블록코딩 '
        '<small>불러온 뒤 값과 순서를 자유롭게 편집</small></summary><div class="seer-learning-body">'
        '<p class="seer-example-intro">예제를 불러오면 현재 조립소를 교체하지만 자동 저장·자동 실행은 '
        '하지 않습니다. 실제 장비에서는 노란 확인 문구의 포인트와 IO 번호를 먼저 수정하세요.</p>'
        f'<div class="seer-example-grid">{example_cards}</div>'
        '<p id="seer-example-status" class="seer-example-status" aria-live="polite"></p>'
        '</div></details>'
        '<div id="seer-builder-manual-modal" class="seer-manual-modal" role="dialog" '
        'aria-modal="true" aria-hidden="true" aria-labelledby="seer-builder-manual-title">'
        '<div class="seer-manual-dialog" '
        'data-manual-dialog><header class="seer-manual-head"><div>'
        '<h2 id="seer-builder-manual-title">Block Builder 사용 매뉴얼</h2>'
        '<p>그림의 블록 모양과 색상은 실제 Block Builder의 구성 방식을 나타냅니다.</p></div>'
        '<button type="button" class="btn seer-manual-close" data-close-builder-manual '
        'aria-label="매뉴얼 닫기">✕</button></header>'
        '<div class="seer-manual-layout"><nav class="seer-manual-nav" aria-label="매뉴얼 목차">'
        '<a href="#seer-manual-1" data-builder-manual-link>1. 화면 구성</a>'
        '<a href="#seer-manual-2" data-builder-manual-link>2. 블록 추가·연결</a>'
        '<a href="#seer-manual-3" data-builder-manual-link>3. 설정·변수</a>'
        '<a href="#seer-manual-4" data-builder-manual-link>4. 반복·조건</a>'
        '<a href="#seer-manual-5" data-builder-manual-link>5. 함수</a>'
        '<a href="#seer-manual-6" data-builder-manual-link>6. 신호·IO</a>'
        '<a href="#seer-manual-7" data-builder-manual-link>7. Path Nav</a>'
        '<a href="#seer-manual-8" data-builder-manual-link>8. 저장·실행·모니터링</a></nav>'
        '<main class="seer-manual-content">'
        '<div class="seer-manual-intro"><strong>기본 작업 순서</strong><br>'
        'Recipe 이름 입력 → 꾸러미에서 블록 추가 → 블록 표면에서 값·조건 입력 → 필요할 때 ⚙ 고급 설정 → 저장 및 적용 → '
        'Actions에서 실행 → Logs에서 결과 확인 순서로 사용합니다.</div>'
        '<article id="seer-manual-1" class="seer-manual-chapter"><h3>1. 화면 구성 이해하기</h3>'
        '<div class="seer-manual-figure"><div class="seer-manual-screen">'
        '<div class="categories">① 카테고리<br>이동·흐름·IO</div>'
        '<div class="pack">② 블록 꾸러미<br>사용할 블록 선택</div>'
        '<div class="stage">③ 블록 조립소<br>전체 흐름 / 함수 정의</div></div>'
        '<div class="seer-manual-caption">왼쪽에서 종류를 고르고, 가운데 블록을 오른쪽 조립소에 놓습니다.</div></div>'
        '<ol><li><strong>카테고리</strong>: 이동, 흐름, IO, 판단, 계산, 자료, 함수, 장비 블록을 전환합니다.</li>'
        '<li><strong>블록 꾸러미</strong>: 선택한 카테고리에서 실제 사용할 명령을 고릅니다.</li>'
        '<li><strong>블록 조립소</strong>: 전체 실행 흐름과 함수 정의를 분리해서 배치합니다.</li></ol></article>'
        '<article id="seer-manual-2" class="seer-manual-chapter"><h3>2. 블록 추가와 실행 순서 연결하기</h3>'
        '<div class="seer-manual-figure"><div class="seer-manual-block-row">'
        '<div class="seer-manual-mini-block" style="--manual-block:#c47b00">Path Nav<small>LM1 → LM4</small></div>'
        '<span class="seer-manual-arrow">끌기 →</span><div class="seer-manual-stack">'
        '<div class="seer-manual-mini-block" style="--manual-block:#c47b00">① Path Nav<small>LM1 → LM4</small></div>'
        '<div class="seer-manual-mini-block" style="--manual-block:#6d7f99">② 기다리기<small>0.2초</small></div></div></div>'
        '<div class="seer-manual-caption">블록을 가까이 놓으면 청록색 연결 표시가 생기고 위에서 아래로 실행됩니다.</div></div>'
        '<ul><li>꾸러미의 블록을 <strong>누르면</strong> 현재 선택 영역에 추가되고, <strong>끌면</strong> 원하는 위치에 놓을 수 있습니다.</li>'
        '<li>최상위 블록은 자유 배치되며 서로 가까이 놓으면 연결됩니다. 첫 블록을 움직이면 연결된 묶음도 함께 이동합니다.</li>'
        '<li>블록 왼쪽 번호를 바꾸거나 ↑/↓ 버튼을 사용해 실행 순서를 조정합니다. 반복·조건의 점선 안에는 하위 블록을 넣습니다.</li></ul></article>'
        '<article id="seer-manual-3" class="seer-manual-chapter"><h3>3. 상세 설정, 기본값과 실행 변수 사용하기</h3>'
        '<div class="seer-manual-figure"><div class="seer-manual-mini-block" '
        'style="--manual-block:#7a4ec7;margin:auto">⚙ 직선 이동 설정<small>설정 버튼 또는 제목으로 펼치기</small></div>'
        '<div class="seer-manual-form"><div class="seer-manual-field"><b>이동 거리</b>0.1 m</div>'
        '<div class="seer-manual-field"><b>선속도</b>0.05 m/s</div>'
        '<div class="seer-manual-field"><b>실행 변수</b><span class="seer-manual-toggle">사용</span> distance</div>'
        '<div class="seer-manual-field"><b>단계 후 대기</b>0 초</div></div></div>'
        '<ul><li>입력하지 않은 항목은 블록에 표시된 기본값을 사용합니다. 실제 장비 한계 안의 값인지 확인하세요.</li>'
        '<li><strong>변수로 사용</strong>을 켜고 변수명을 지정하면 Actions 실행창에서 실행할 때마다 값을 바꿀 수 있습니다.</li>'
        '<li>단계 후 대기는 해당 블록이 성공한 뒤 다음 블록을 시작하기 전 기다리는 시간입니다. 즉시 이어가려면 0초로 둡니다.</li></ul></article>'
        '<article id="seer-manual-4" class="seer-manual-chapter"><h3>4. 반복문과 조건문 구성하기</h3>'
        '<div class="seer-manual-figure"><div class="seer-manual-block-row">'
        '<div class="seer-manual-mini-block" style="--manual-block:#b65cc8">result 을 0 으로 정하기</div>'
        '<span class="seer-manual-arrow">→</span><div class="seer-manual-control">result = 4 가 될 때까지 반복하기'
        '<div class="seer-manual-drop"><span style="color:#fff">result 에 1 만큼 더하기</span></div></div></div>'
        '<div class="seer-manual-caption">result는 0 → 1 → 2 → 3 → 4가 되고, 조건이 참이 되는 순간 반복이 끝납니다.</div></div>'
        '<ul><li>변수·계산·판단·반복 조건의 핵심 입력칸은 블록 표면에 바로 보입니다. 설정창을 열지 않고 값을 바꿀 수 있습니다. 반복·조건뿐 아니라 변수·계산·판단 등 블록 표면의 모든 입력칸은 현재 입력된 글자 또는 선택된 항목의 실제 픽셀 폭에 맞춰 각각 자동으로 줄고 늘어납니다. 변수명처럼 글자가 길어지면 해당 칸이 즉시 넓어지고 짧아지면 다시 줄어듭니다. 자동완성 목록이 붙은 입력칸과 드롭다운 화살표가 차지하는 폭까지 포함해 계산합니다. 조건을 감싸는 육각형 영역도 안쪽 입력칸들의 총 너비를 그대로 따라 자동으로 확장·축소됩니다. 조건식 전체를 한 줄로 표시하면서, 조건식 전체가 현재 블록 너비보다 길어지면 바깥 블록 자체도 함께 넓어지고 실행 흐름/함수 영역도 그 블록 폭에 맞춰 확장되므로 옆 영역과 겹치지 않습니다. 긴 값은 가로 스크롤로 확인할 수 있고, 값을 다시 짧게 만들거나 변수명을 지우면 입력칸·육각형·블록·작업 영역이 즉시 함께 줄어듭니다. 블록 폭 계산은 현재 블록의 기존 폭이 아니라 실제 보이는 입력칸과 문구의 자연 폭만 사용하므로 삭제할수록 오히려 넓어지는 현상이 생기지 않습니다.</li>'
        '<li><strong>변수 정하기 → 조건 반복 → 변수에 더하기</strong>를 조립하면 엔트리의 카운터 반복과 같은 흐름을 만들 수 있습니다.</li>'
        '<li>반복 중단은 가장 가까운 반복문을 끝내며, 이번 반복 건너뛰기는 다음 회차로 넘어갑니다. 계속 반복에는 반드시 정지 조건이나 timeout을 설계하세요.</li><li>실행 모니터에서는 반복이 새 회차로 넘어갈 때 반복문 내부와 호출 함수 내부의 완료 표시가 초기화됩니다. 따라서 초록색 완료 표시는 항상 <strong>현재 반복 회차</strong> 기준으로 읽으면 됩니다.</li></ul></article>'
        '<article id="seer-manual-5" class="seer-manual-chapter"><h3>5. 함수를 정의하고 전체 흐름에서 호출하기</h3>'
        '<div class="seer-manual-figure"><div class="seer-manual-block-row">'
        '<div class="seer-manual-control" style="background:#4c59b0">함수 정의하기 DemoMotion'
        '<div class="seer-manual-drop">직선 이동<br>→ 제자리 회전</div></div>'
        '<span class="seer-manual-arrow">이름 저장 →</span>'
        '<div class="seer-manual-mini-block" style="--manual-block:#4c59b0">DemoMotion 함수 실행</div></div></div>'
        '<ul><li><strong>함수 정의</strong> 영역에 함수 정의하기 블록을 놓고 내부 동작을 구성합니다.</li>'
        '<li>정의한 이름은 왼쪽 함수 꾸러미에 나타납니다. 전체 실행 흐름에 함수 실행 블록을 놓아 호출합니다.</li>'
        '<li>함수 정의 블록은 그 자체로 실행되지 않습니다. 같은 이름의 함수 실행 블록을 만났을 때만 내부 동작이 실행됩니다.</li><li>반복문에서 같은 함수를 다시 호출하면 함수 내부의 이전 완료 표시를 지우고 이번 호출의 진행 상태를 새로 표시합니다.</li></ul></article>'
        '<article id="seer-manual-6" class="seer-manual-chapter"><h3>6. 신호와 DI·DO로 설비 연동하기</h3>'
        '<div class="seer-manual-figure"><div class="seer-manual-signal">'
        '<span>신호 보내기: LOAD_READY</span><b class="seer-manual-arrow">→</b>'
        '<span class="wait">신호 받을 때까지 대기</span><b class="seer-manual-arrow">→</b>'
        '<span class="io">DO0 ON</span><b class="seer-manual-arrow">→</b>'
        '<span class="wait">DI0 rising 대기</span></div></div>'
        '<ul><li>신호 블록의 목록에서 신호 이름을 선택합니다. 보내고 기다리기는 송신 후 지정 신호를 받을 때까지 다음 단계로 가지 않습니다.</li>'
        '<li>DO는 외부 장치에 출력하고 DI는 외부 장치의 입력 상태를 읽습니다. rising은 0→1, falling은 1→0 변화를 의미합니다.</li>'
        '<li>통신 단절이나 센서 미동작에 대비해 DI/신호 대기에는 timeout과 실패 시 동작을 설정하세요. 실제 IO 번호는 장비 배선표와 대조해야 합니다.</li></ul></article>'
        '<article id="seer-manual-7" class="seer-manual-chapter"><h3>7. Path Nav 경로를 지도에서 선택하기</h3>'
        '<div class="seer-manual-figure"><svg class="seer-manual-map" viewBox="0 0 520 150" '
        'role="img" aria-label="LM1에서 LM4까지 선택한 Path Nav 경로 그림">'
        '<path d="M55 105 C130 20 215 25 275 75 S400 130 465 45"/>'
        '<path class="selected" d="M55 105 C130 20 215 25 275 75 S400 130 465 45"/>'
        '<circle cx="55" cy="105" r="10"/><circle cx="275" cy="75" r="10"/><circle cx="465" cy="45" r="10"/>'
        '<text x="34" y="135">LM1</text><text x="253" y="105">LM5</text><text x="443" y="27">LM4</text></svg>'
        '<div class="seer-manual-caption">노란 선이 선택 경로입니다. 출발지·목적지와 후보별 거리를 확인한 뒤 적용합니다.</div></div>'
        '<ul><li>Path Nav 블록의 ⚙에서 <strong>지정 경로 선택</strong>을 열고 출발지, 목적지, 후보 경로를 선택합니다.</li>'
        '<li>포인트 간 대기 0초이면 선택한 전체 포인트 배열을 한 번의 Path Nav 명령으로 전달합니다. 값이 있으면 각 포인트에서 그 시간만큼 기다립니다.</li>'
        '<li>지정 경로를 비우면 SEER가 경로를 자동 계획합니다. 그래프 경로가 없어도 목적지 좌표가 있으면 Free Nav를 선택할 수 있습니다.</li></ul></article>'
        '<article id="seer-manual-8" class="seer-manual-chapter"><h3>8. 저장·실행·Pause·Resume·Cancel과 진행 상태 보기</h3>'
        '<div class="seer-manual-figure"><div class="seer-manual-pipeline">'
        '<span>Recipe 저장 및 적용</span><b class="seer-manual-arrow">→</b><span>▶ 실행</span>'
        '<b class="seer-manual-arrow">→</b><span>실행 블록 강조</span><b class="seer-manual-arrow">→</b>'
        '<span>변수·조건 확인</span></div></div>'
        '<ol><li>Recipe 이름은 영문으로 시작하고 영문·숫자·밑줄만 사용합니다. 화면 표시 이름은 Actions에도 보입니다.</li>'
        '<li><strong>Recipe 저장 및 적용</strong>은 화면을 새로 불러오지 않고 현재 블록 배치와 편집 상태를 그대로 유지한 채 저장합니다. 저장된 Recipe만 ▶ 실행할 수 있습니다.</li>'
        '<li>Builder 위쪽의 <strong>▶ 실행 / Pause / Resume / Cancel</strong> 버튼으로 현재 선택된 SEER AMR의 Recipe 실행을 제어합니다. 실행 중에는 블록, 변수값, 순서, Recipe 이름을 수정할 수 없도록 편집이 잠깁니다.</li>'
        '<li>실행 중인 블록은 굵은 초록 윤곽선, 반복·함수 부모는 보조 윤곽선으로 표시됩니다. 실시간 변수와 조건 판정은 모니터 카드에서 확인합니다. 실행 따라가기를 켜면 현재 블록으로 자동 스크롤합니다.</li>'
        '<li>반복문은 새 회차가 시작될 때 내부 블록과 호출 함수 내부의 완료 상태를 초기화합니다. 이전 회차의 완료 표시가 다음 회차에 남지 않습니다.</li>'
        '<li>Pause/Resume은 SEER 네이티브 일시정지/재개를 사용합니다. Action 취소는 현재 Block Builder/Jack 같은 standalone Action과 SEER TASK를 취소하고, 오류 리셋은 남은 Action 실패 기록과 sticky 오류를 초기화합니다.</li>'
        '<li>아래 관리 목록에서 편집·이름 변경·삭제할 수 있습니다.</li></ol></article>'
        '<div class="seer-manual-safety"><strong>⚠ 실물 장비 적용 전 확인</strong><br>'
        '시뮬레이터에서 먼저 검증하고 실제 포인트명, 지도명, DO/DI 번호, 속도, 모터 설정을 장비와 '
        '대조하세요. 예제나 매뉴얼 버튼은 자동 저장·실행하지 않습니다. 비상정지 해제 후 중단된 '
        '이동은 자동 재개하지 말고 상태를 확인한 뒤 새 명령으로 시작하세요.</div>'
        '</main></div></div></div></section>'
    )
    edit_notice = (
        f'<span class="pill accent">{html.escape(edit_name)} 편집 중</span> '
        '<span class="muted">Recipe 이름과 화면 표시 이름을 모두 변경할 수 있습니다.</span> '
        f'<a class="btn" href="/recipe-builder?{_builder_query()}">새 Recipe</a>'
        if editing
        else ""
    )
    error_notice = f'<p class="error-notice">{html.escape(load_error)}</p>' if load_error else ""
    submit_label = "Recipe 변경 저장 및 적용" if editing else "Recipe 저장 및 적용"
    map_selector = ""
    if len(map_robot_options) > 1:
        links = []
        for key, label in map_robot_options:
            # Switching AMRs clears edit= because Recipes are isolated per AMR.
            params = {"robot": key}
            current = " is-current" if key == map_robot_key else ""
            links.append(
                f'<a class="btn{current}" data-seer-amr-label-key="{html.escape(key, quote=True)}" '
                f'href="/recipe-builder?{urllib.parse.urlencode(params)}">{html.escape(label)}</a>'
            )
        map_selector = (
            '<nav class="seer-builder-map-selector" aria-label="Recipe 실행 AMR 선택">'
            '<strong>Recipe / 실행 AMR</strong>' + "".join(links) + "</nav>"
        )
    selected_robot_label = next(
        (label for key, label in map_robot_options if key == map_robot_key),
        map_robot_key or "선택 안 됨",
    )
    selected_robot_note = (
        '<div class="seer-builder-selected-amr"><strong>현재 Recipe / 실행 대상 AMR</strong> '
        f'<span data-seer-amr-label-key="{html.escape(map_robot_key, quote=True)}">'
        f'{html.escape(selected_robot_label)}</span></div>'
        if map_robot_key else
        '<div class="seer-builder-selected-amr"><strong>현재 Recipe / 실행 대상 AMR</strong> 선택 안 됨</div>'
    )
    nickname_sync_script = """
<script id="seer-builder-amr-label-sync">
(function(){
  async function sync(){
    try{
      var response=await fetch('/seer/amr-labels?_t='+Date.now(),{cache:'no-store'});
      if(!response.ok)return;
      var payload=await response.json(),labels=(payload&&payload.labels)||{};
      document.querySelectorAll('[data-seer-amr-label-key]').forEach(function(node){
        var key=node.getAttribute('data-seer-amr-label-key');
        if(key&&labels[key])node.textContent=labels[key];
      });
    }catch(_error){}
  }
  sync();window.setInterval(sync,1000);
})();
</script>
"""

    runtime_config_json = json.dumps(
        {
            "robot": map_robot_key,
            "recipe_name": str(definition.get("name", "")),
            "saved": bool(editing),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    route_picker = (
        '<div id="seer-route-picker" class="seer-route-picker" role="dialog" '
        'aria-modal="true" aria-label="지정 경로 선택"><div class="seer-route-picker-modal">'
        '<div class="seer-route-picker-head"><div><h2 style="margin:0;color:#f3f8fc">'
        '지정 경로 선택</h2><small class="muted">지도 경로의 방향을 기준으로 최대 5개 후보를 계산합니다.</small>'
        '</div><button type="button" class="btn" data-route-picker-close>닫기</button></div>'
        '<div class="seer-route-picker-grid"><div><div class="seer-route-picker-controls">'
        '<label>출발 포인트<select id="seer-route-source"></select></label>'
        '<label>목적 포인트<select id="seer-route-target"></select></label></div>'
        '<div class="seer-route-map-toolbar" aria-label="지정 경로 지도 확대 축소">'
        '<button type="button" class="btn" data-route-map-zoom="0.833333" aria-label="지도 축소">−</button>'
        '<span id="seer-route-map-zoom" class="seer-route-map-zoom" aria-live="polite">100%</span>'
        '<button type="button" class="btn" data-route-map-zoom="1.2" aria-label="지도 확대">＋</button>'
        '<button type="button" class="btn" data-route-map-fit>맞춤</button>'
        '<label class="seer-route-label-size" title="LM 포인트 원 크기">LM 원 '
        '<input id="seer-route-point-size" type="range" min="35" max="200" step="5" value="100" '
        'aria-label="Path Nav 지도 LM 포인트 원 크기">'
        '<output id="seer-route-point-size-value">100%</output></label>'
        '<label class="seer-route-label-size" title="LM 포인트 이름 글자 크기">LM 글자 '
        '<input id="seer-route-point-label-size" type="range" min="25" max="150" step="5" value="100" '
        'aria-label="Path Nav 지도 LM 포인트 이름 글자 크기">'
        '<output id="seer-route-point-label-size-value">100%</output></label>'
        '<label class="seer-route-layer-order" title="파란 기본 경로와 노란 선택 경로의 겹침 순서">경로 겹침 '
        '<select id="seer-route-layer-order" aria-label="노란 경로 표시 순서">'
        '<option value="above">노란선 위</option><option value="below">노란선 아래</option>'
        '</select></label>'
        '<span class="seer-route-map-hint">휠 확대·축소 · 드래그 이동</span></div>'
        f'<svg id="seer-route-picker-map" class="seer-route-picker-svg" viewBox="0 0 '
        f'{float(route_map.get("width", 760)):.0f} {float(route_map.get("height", 420)):.0f}" '
        'role="img" aria-label="SEER 지정 경로 미니맵"></svg>'
        '<p class="muted">지도 포인트를 누르면 목적지가 바뀝니다. 노란색이 선택한 경로입니다.</p>'
        '</div><div><h3>가능한 경로</h3><div id="seer-route-options" class="seer-route-options"></div>'
        '<p id="seer-route-picker-status" class="seer-route-picker-status" aria-live="polite"></p>'
        '<div class="seer-route-picker-buttons"><button type="button" class="btn" '
        'data-route-picker-close>취소</button><button type="button" class="btn primary" '
        'data-route-picker-apply>선택 경로 적용</button></div></div></div></div></div>'
    )
    body = (
        f'{render._flash(dict(q or {}))}{_STYLE}{_ENTRY_STYLE}{error_notice}{delete_modal}'
        '<div><h1>SEER Block Action Builder</h1>'
        '<p class="subtitle">엔트리형 카테고리·블록 꾸러미·조립 공간에서 VDA5050 Recipe를 만듭니다. '
        f'블록을 끌어 조립하고 각 값을 고정값 또는 실행 변수로 저장할 수 있습니다.</p>{edit_notice}</div>{map_selector}{selected_robot_note}'
        '<div class="seer-entry-help"><strong>사용법</strong> 왼쪽 카테고리에서 블록을 고른 뒤 '
        '가운데 꾸러미에서 오른쪽 조립 공간으로 끌어 놓으세요. 반복/조건 블록 안에도 블록을 '
        '중첩할 수 있습니다. 변수·계산·판단·반복 조건은 엔트리처럼 블록 표면에서 바로 입력하고, '
        '⚙은 고급 설정만 펼칩니다. DI 신호 '
        'ON/OFF와 상승·하강 신호를 기다린 뒤 다음 동작을 실행할 수 있습니다.</div>'
        '<div class="seer-builder-warning">좌표 이동과 Path Nav는 SEER API 3051/3066에 '
        '속도 인자가 없어서 장비의 Roboshop 이동 프로파일을 사용합니다. 직선 이동(API 3055)의 '
        '선속도와 회전(API 3056)의 각속도는 실제 TCP 요청에 적용됩니다. 지도 전환·위치 초기화·'
        '모터 전원 블록은 장비 상태에 직접 영향을 주므로 실제 설정값을 확인한 뒤 사용하세요.</div>'
        f'{learning_panel}'
        '<form id="seer-block-form" method="post" action="/recipe-builder" data-seer-builder-save-form>'
        f'<input type="hidden" name="csrf_token" value="{html.escape(csrf, quote=True)}">'
        '<input type="hidden" name="operation" value="save">'
        f'<input type="hidden" name="robot" value="{html.escape(map_robot_key, quote=True)}">'
        + (
            f'<input type="hidden" name="original_recipe_name" '
            f'value="{html.escape(edit_name, quote=True)}">'
            if editing
            else ""
        )
        +
        '<input type="hidden" id="seer-block-definition" name="definition" value="">'
        '<datalist id="seer-variable-options"></datalist>'
        '<datalist id="seer-operand-options"></datalist>'
        '<datalist id="seer-function-options"></datalist>'
        '<div class="command-fields" style="margin:14px 0">'
        '<label class="mini-field">Recipe 이름 <input name="recipe_name" required '
        f'pattern="[A-Za-z][A-Za-z0-9_]{{0,63}}" value="{html.escape(str(definition["name"]), quote=True)}" placeholder="예: myRecipe" maxlength="64"></label>'
        '<label class="mini-field">화면 표시 이름 <input name="recipe_label" '
        f'value="{html.escape(str(definition["label"]), quote=True)}" maxlength="120"></label></div>'
        '<div class="seer-entry-shell"><nav class="seer-entry-categories" aria-label="블록 카테고리">'
        f'{category_rail}</nav><aside class="seer-entry-pack"><h2>블록 꾸러미</h2>'
        f'<div class="seer-entry-pack-note">누르거나 끌어서 추가</div>{palette}</aside>'
        '<section class="seer-entry-stage"><div class="seer-entry-toolbar"><div><strong>블록 조립소</strong>'
        '<div class="seer-api-note">자유 배치 · 가까이 놓으면 자석 연결 · 번호 클릭 또는 ↑/↓로 실행 순서 변경</div></div>'
        '<div class="seer-entry-toolbar-actions"><div class="seer-view-switch" aria-label="작업공간 보기">'
        '<button type="button" class="btn is-active" data-entry-view="both">둘 다 보기</button>'
        '<button type="button" class="btn" data-entry-view="flow">전체 흐름만</button>'
        '<button type="button" class="btn" data-entry-view="functions">함수만</button></div>'
        '<div class="seer-entry-zoom"><button type="button" class="btn" data-entry-zoom="-10">−</button>'
        '<output id="seer-entry-zoom-value">100%</output><button type="button" class="btn" data-entry-zoom="10">＋</button>'
        '<button type="button" class="btn" data-entry-fit>100%</button></div></div></div>'
        '<section id="seer-runtime-monitor" class="seer-runtime-monitor" aria-live="polite">'
        '<div class="seer-runtime-head"><div class="seer-runtime-title"><span class="seer-runtime-dot"></span>'
        '<strong id="seer-runtime-status">실행 대기</strong><span id="seer-runtime-recipe" class="seer-runtime-recipe"></span></div>'
        '<div class="seer-runtime-controls" aria-label="Builder 실행 제어">'
        f'<button type="button" class="btn" data-builder-control="run" data-builder-run{"" if editing and map_robot_key else " disabled"}>▶ 실행</button>'
        '<button type="button" class="btn" data-builder-control="pause" data-builder-pause disabled>Ⅱ Pause</button>'
        '<button type="button" class="btn" data-builder-control="resume" data-builder-resume disabled>▶ Resume</button>'
        '<button type="button" class="btn" data-builder-control="cancel" data-builder-cancel disabled>■ Action 취소</button>'
        f'<button type="button" class="btn" data-builder-control="reset" data-builder-reset{"" if map_robot_key else " disabled"}>↺ 오류 리셋</button></div>'
        '<label class="seer-runtime-follow"><input id="seer-runtime-follow" type="checkbox" checked> 실행 따라가기</label></div>'
        '<div id="seer-runtime-control-note" class="seer-runtime-control-note">저장된 Recipe를 Builder에서 바로 실행할 수 있습니다.</div>'
        '<div class="seer-runtime-grid"><div class="seer-runtime-card"><b>현재 블록</b><span id="seer-runtime-current">실행 중인 블록이 없습니다.</span></div>'
        '<div class="seer-runtime-card"><b>실시간 변수</b><div id="seer-runtime-vars" class="seer-runtime-vars"><span class="seer-runtime-empty">변수 없음</span></div></div>'
        '<div class="seer-runtime-card"><b>조건 / 판단</b><div id="seer-runtime-condition" class="seer-runtime-condition"><span class="seer-runtime-empty">조건 평가 없음</span></div></div></div></section>'
        '<div class="seer-entry-viewport"><div id="seer-block-workspace" class="seer-block-workspace">'
        '<section id="seer-flow-lane" class="seer-top-lane is-insert-target" data-lane="flow">'
        '<div class="seer-lane-head"><strong>전체 실행 흐름</strong><small>연결 또는 번호 순서로 실행</small></div>'
        '<div class="seer-lane-empty" data-lane-empty="flow">원하는 위치에 블록을 놓으세요.</div></section>'
        '<section id="seer-function-lane" class="seer-top-lane" data-lane="functions">'
        '<div class="seer-lane-head"><strong>함수 정의</strong><small>호출할 때만 실행</small></div>'
        '<div class="seer-lane-empty" data-lane-empty="functions">함수 정의 블록을 자유롭게 배치하세요.</div>'
        '</section></div></div>'
        '<div class="seer-builder-footer"><button type="submit" class="btn primary">'
        f'{submit_label}</button><a class="btn" href="/source/recipes.hcl">HCL 원문 보기</a>'
        '<span class="muted">저장 후 연결된 SEER Adapter가 자동 재시작됩니다.</span>'
        '<span class="seer-runtime-lock-note">실행 중에는 블록 편집과 저장이 잠깁니다.</span></div>'
        f'</section></div></form>{route_picker}<section class="seer-builder-panel" style="margin-top:16px">'
        f'<h2>Block Builder 관리 Recipe</h2><div class="seer-managed-list">{chips}</div></section>'
        f'<script type="application/json" id="seer-block-schemas">{_schema_json()}</script>'
        '<script type="application/json" id="seer-block-initial">{"blocks":[]}</script>'
        f'<script type="application/json" id="seer-entry-initial">{initial_json}</script>'
        f'<script type="application/json" id="seer-builder-examples">{examples_json}</script>'
        f'<script type="application/json" id="seer-route-map">{route_map_json}</script>'
        f'<script type="application/json" id="seer-runtime-config">{runtime_config_json}</script>'
        f'{nickname_sync_script}{_SCRIPT}{_ENTRY_SCRIPT}'
    )
    return render.page(
        "SEER Block Action Builder",
        body,
        current="/recipe-builder",
        brand_title="SEER Block Builder",
    )


def redirect_result(handler: Any, *, ok: str = "", error: str = "") -> None:
    query = urllib.parse.urlencode({"msg": ok} if ok else {"err": error})
    handler._redirect(f"/recipe-builder?{query}")
