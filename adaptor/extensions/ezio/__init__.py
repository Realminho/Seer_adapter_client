"""EZI 입출력과 트레이 광센서를 처리하는 action extension."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Iterable, List

from core.action_registry import (
    ActionParameterSpec,
    ActionResult,
    ActionSpec,
    action_params,
)
from extensions.pio import parse_pio_state
from protocol.vda_2_0_0.vda5050_2_0_0_state import (
    ActionStatus,
    Error,
    ErrorLevel,
    ErrorReference,
    ErrorType,
    Load,
)


MODULE_TITLE = "EZIO"

EZIO_ACTION_TYPES = (
    "ezioReadIn",
    "photoSensorRead",
    "ezioWriteOut",
    "ezioWaitIn",
)


def is_ezio_action(action_type: str) -> bool:
    """action type이 EZIO 기능에 속하는지 확인한다."""
    return action_type in EZIO_ACTION_TYPES


def parse_ezio_index(value: Any) -> int:
    """EZIO 점 번호를 정수로 변환하고 0~15 범위를 검증한다.

    입력·출력 모두 같은 번호 체계다. extensions.hcl의 open_door_pin,
    tray_slot_pin 같은 값이 그대로 들어온다.
    """
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("EZIO point index must be an integer 0-15") from exc
    if index < 0 or index > 15:
        raise ValueError("EZIO point index must be 0-15")
    return index


async def ezio_write_output_action(adapter: Any, action: Any) -> Dict[str, Any]:
    """EZIO 출력 한 점을 쓰고 읽어 실제 출력 상태를 갱신한다."""
    try:
        params = action_params(action)
        index = parse_ezio_index(params.get("index"))
        state = parse_pio_state(params.get("state"))
        if adapter._ezi_io is None:
            raise ValueError("EZI IO is not initialized")
        if state == "on":
            await adapter._ezi_io.turn_on_output(index)
        else:
            await adapter._ezi_io.turn_off_output(index)
        resp = await adapter._ezi_io.get_output()
        if resp and "outputs" in resp:
            adapter._note_ezio(outputs=list(resp["outputs"]))
        return {
            "ok": True,
            "action": "ezioWriteOut",
            "message": f"ezioWriteOut finished: out{index}={state}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "action": "ezioWriteOut",
            "failedReason": type(exc).__name__,
            "message": f"ezioWriteOut failed: {exc}",
        }


async def wait_ezio_input(adapter: Any, action: Any) -> Dict[str, Any]:
    """EZI IO 입력 한 점이 기대 상태가 될 때까지 제한 시간 안에서 기다린다.

    ezioReadIn은 한 번 읽고 끝이라 "문이 열렸는지"를 판정하지 못한다. recipe가
    step 사이에 조건 대기를 끼우려면 기대 상태와 제한 시간을 함께 받아야 한다.
    """
    params = action_params(action)
    index = parse_ezio_index(params.get("index"))
    expected = parse_pio_state(params.get("state"))
    ezi_config = adapter.config.ezi_config
    timeout_sec = max(
        0.0,
        float(params.get("timeoutSec", ezi_config.wait_in_default_timeout_sec)),
    )
    poll_sec = float(ezi_config.wait_in_poll_interval_sec)
    deadline = time.monotonic() + timeout_sec
    while True:
        bits = await read_ezio_input_bits(adapter)
        actual = "on" if bits[index] else "off"
        if actual == expected:
            return {
                "ok": True,
                "action": "ezioWaitIn",
                "message": f"ezioWaitIn finished: in{index}={expected}",
                "inputs": bits_to_indexed_states(bits),
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "ok": False,
                "action": "ezioWaitIn",
                "failedReason": "timeout",
                "message": (
                    f"ezioWaitIn failed: expected in{index}={expected} "
                    f"within {timeout_sec:g}s; last in{index}={actual}"
                ),
                "inputs": bits_to_indexed_states(bits),
            }
        # 남은 예산보다 오래 자면 제한 시간을 넘겨 보고하게 된다.
        await asyncio.sleep(min(poll_sec, remaining))


async def execute_ezio_action(adapter: Any, action: Any) -> Dict[str, Any]:
    """EZIO action을 분기 실행하고 성공·실패 결과 형식을 통일한다."""
    if action.action_type == "ezioWriteOut":
        return await ezio_write_output_action(adapter, action)
    try:
        if action.action_type == "ezioWaitIn":
            return await wait_ezio_input(adapter, action)
        if action.action_type == "ezioReadIn":
            inputs = await read_ezio_input_bits(adapter)
            return {
                "ok": True,
                "action": "ezioReadIn",
                "message": f"ezioReadIn finished: inputs={format_indexed_states(inputs)}",
                "inputs": bits_to_indexed_states(inputs),
            }
        if action.action_type == "photoSensorRead":
            inputs = await read_ezio_input_bits(adapter)
            changed = update_loads_from_photo_sensor_inputs(adapter, inputs)
            if changed:
                adapter.request_state_publish("photo sensor changed")
            sensor_states = photo_sensor_states(adapter, inputs)
            indexed_sensor_states = {
                str(index): state
                for index, state in enumerate(sensor_states.values(), start=1)
            }
            return {
                "ok": True,
                "action": "photoSensorRead",
                "message": (
                    "photoSensorRead finished: "
                    f"photoSensors={format_indexed_states(indexed_sensor_states)}"
                ),
                "inputs": bits_to_indexed_states(inputs),
                "photoSensors": indexed_sensor_states,
                "photoSensorPins": list(adapter.config.ezi_config.tray_slot_pin),
                "loadsChanged": changed,
            }
        raise ValueError(f"Unsupported EZI IO action: {action.action_type}")
    except Exception as exc:
        set_ezio_input_error(adapter, str(exc))
        return {
            "ok": False,
            "action": action.action_type,
            "failedReason": type(exc).__name__,
            "message": f"{action.action_type} failed: {exc}",
        }


async def read_ezio_input_bits(adapter: Any) -> List[int]:
    """EZIO 입력을 한 번 재시도해 읽고 16개의 0/1 값으로 정규화한다."""
    if adapter._ezi_io is None:
        raise ValueError("EZI IO is not initialized")

    last_reason = "EZI IO input response is empty"
    for attempt in range(2):
        response = await adapter._ezi_io.get_input()
        if response and "inputs" in response:
            break

        last_reason = (
            getattr(adapter._ezi_io, "last_error", "")
            or "EZI IO input response is empty"
        )
        if attempt == 0:
            await asyncio.sleep(0.1)
    else:
        raise ValueError(f"EZI IO input response is empty: {last_reason}")

    inputs = list(response["inputs"])
    if len(inputs) < 16:
        raise ValueError(f"EZI IO input response has {len(inputs)} bits; expected 16")
    clear_ezio_input_error(adapter)
    return [1 if int(bit) else 0 for bit in inputs[:16]]


def set_ezio_input_error(adapter: Any, reason: str) -> None:
    """EZIO 입력 오류를 중복 없이 VDA 상태에 기록한다."""
    if adapter.state is None:
        return

    clear_ezio_input_error(adapter)
    adapter.state.errors.append(
        Error(
            error_type=ErrorType.EZI_IO_INPUT_FAILED,
            error_level=ErrorLevel.WARNING,
            error_references=[
                ErrorReference("component", "EZI_IO"),
                ErrorReference("reason", reason),
            ],
            error_description=(
                "Adapter could not read EZI IO input bits; "
                "photo sensor and load state may be stale"
            ),
        )
    )
    adapter.request_state_publish("ezio input failed")


def clear_ezio_input_error(adapter: Any) -> None:
    """기존 EZIO 입력 오류만 상태 목록에서 제거한다."""
    if adapter.state is None:
        return

    adapter.state.errors = [
        error
        for error in adapter.state.errors
        if getattr(error, "error_type", None) != ErrorType.EZI_IO_INPUT_FAILED
    ]


def update_loads_from_photo_sensor_inputs(adapter: Any, inputs: List[int]) -> bool:
    """광센서 입력을 VDA load 목록에 반영하고 변경 여부를 반환한다."""
    loads = [
        Load(
            # The photo sensor only tells us whether the slot is occupied; it
            # cannot identify the lot.  Leave loadId unset unless a real lot
            # identifier is supplied by another source.
            load_id=None,
            load_type="TRAY",
            load_position=f"slot{slot}",
        )
        for slot, pin in enumerate(adapter.config.ezi_config.tray_slot_pin, start=1)
        if pin < len(inputs) and inputs[pin] == 1
    ]
    changed = [load.to_dict() for load in adapter.loads] != [
        load.to_dict() for load in loads
    ]
    adapter.loads = loads
    if adapter.state is not None:
        adapter.state.loads = adapter.loads
    return changed


def photo_sensor_states(adapter: Any, inputs: List[int]) -> Dict[int, str]:
    """설정된 트레이 핀을 기준으로 센서별 on/off 상태를 만든다."""
    states: Dict[int, str] = {}
    for slot, pin in enumerate(adapter.config.ezi_config.tray_slot_pin, start=1):
        states[slot] = "on" if pin < len(inputs) and inputs[pin] == 1 else "off"
    return states


def bits_to_indexed_states(bits: List[int]) -> Dict[str, str]:
    """0부터 시작하는 bit 배열을 1부터 시작하는 on/off 사전으로 바꾼다."""
    return {
        str(index): ("on" if bit else "off")
        for index, bit in enumerate(bits, start=1)
    }


def format_indexed_states(bits_or_states: Any) -> str:
    """센서 상태를 로그에 적합한 안정적인 순서의 문자열로 만든다."""
    if isinstance(bits_or_states, dict):
        states = bits_or_states
    else:
        states = {
            str(index): ("on" if bit else "off")
            for index, bit in enumerate(bits_or_states, start=1)
        }
    return "{" + ",".join(
        f"{index}:{states[str(index)]}"
        for index in range(1, len(states) + 1)
        if str(index) in states
    ) + "}"


def summarize_ezio_result(result: Dict[str, Any]) -> str:
    """EZIO 결과에서 사용자에게 보여 줄 대표 메시지를 선택한다."""
    return str(result.get("message") or result)


async def manage_tray_slot(adapter: Any, interval_sec: float = 1) -> None:
    """주기적으로 광센서를 읽어 load와 연결 상태를 갱신한다."""
    while True:
        if adapter._is_simulator():
            await asyncio.sleep(interval_sec)
            continue

        if adapter._ezi_io is not None:
            try:
                inputs = await read_ezio_input_bits(adapter)
            except Exception as exc:
                set_ezio_input_error(adapter, str(exc))
                adapter._note_ezio(connected=False, error=str(exc))
                print(f"[PHOTO SENSOR READ FAILED] {exc}")
            else:
                adapter._note_ezio(connected=True, inputs=inputs, error="")
                changed = update_loads_from_photo_sensor_inputs(adapter, inputs)
                if changed:
                    adapter.request_state_publish("photo sensor changed")

        await adapter._io_throttle_tick(time.time())
        await asyncio.sleep(interval_sec)


async def handle_ezio_action(ctx: Any) -> ActionResult:
    """EZIO 실행 결과를 표준 ActionResult로 변환한다."""
    result = await execute_ezio_action(ctx.adapter, ctx.action)
    status = ActionStatus.FINISHED if result["ok"] else ActionStatus.FAILED
    description = summarize_ezio_result(result)
    print(f"[EZIO ACTION RESULT] {json.dumps(result, ensure_ascii=False)}")
    return ActionResult(status, description)


_POINT_PARAMETERS = (
    ActionParameterSpec(
        "index", required=True, input_type="number", placeholder="0-15",
        label="IO 포인트 번호",
    ),
    ActionParameterSpec(
        "state", required=True, placeholder="on 또는 off", choices=("on", "off"),
        label="on/off",
    ),
)

_EZIO_PARAMETERS = {
    "ezioWriteOut": _POINT_PARAMETERS,
    "ezioWaitIn": (
        *_POINT_PARAMETERS,
        ActionParameterSpec(
            "timeoutSec", input_type="number",
            placeholder="기본 ezi.wait_in_default_timeout_sec",
            label="제한시간(초)",
        ),
    ),
}


def action_specs() -> Iterable[ActionSpec]:
    """EZIO action별 handler와 실행 속성을 등록한다."""
    return tuple(
        ActionSpec(
            action_type=action_type,
            handler=handle_ezio_action,
            parameters=_EZIO_PARAMETERS.get(action_type, ()),
        )
        for action_type in EZIO_ACTION_TYPES
    )


def start_sensor_service(adapter: Any, *, interval_sec: float = 1) -> asyncio.Task[Any]:
    """현재 event loop에 트레이 센서 감시 task를 생성한다."""
    return asyncio.create_task(manage_tray_slot(adapter, interval_sec=interval_sec))
