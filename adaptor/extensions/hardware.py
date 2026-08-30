"""클램프·PIO·EZIO 하드웨어 action extension을 묶는 진입점."""

from __future__ import annotations

from typing import Any, Tuple

from extensions.clamp import action_specs as clamp_action_specs
from extensions.clamp import execute_clamp_action
from extensions.clamp import is_clamp_action
from extensions.ezio import action_specs as ezio_action_specs
from extensions.ezio import execute_ezio_action
from extensions.ezio import is_ezio_action
from extensions.ezio import start_sensor_service as start_ezio_sensor_service
from extensions.ezio import summarize_ezio_result
from extensions.pio import action_specs as pio_action_specs
from extensions.pio import execute_pio_action
from extensions.pio import is_pio_action
from extensions.pio import summarize_pio_result


def action_specs() -> tuple[Any, ...]:
    """각 하드웨어 모듈의 action 명세를 하나의 튜플로 합친다."""
    return (
        *clamp_action_specs(),
        *pio_action_specs(),
        *ezio_action_specs(),
    )


def is_hardware_action(action_type: str) -> bool:
    """action type이 지원하는 하드웨어 action인지 확인한다."""
    return (
        is_clamp_action(action_type)
        or is_pio_action(action_type)
        or is_ezio_action(action_type)
    )


def start_sensor_service(
    adapter: Any,
    *,
    interval_sec: float = 1,
    action_registry: Any = None,
) -> Any:
    """photoSensorRead가 활성화된 경우에만 EZIO 센서 작업을 시작한다."""
    if action_registry is not None and not action_registry.has("photoSensorRead"):
        print(
            "[HARDWARE SENSOR SERVICE SKIP] "
            "photoSensorRead action is disabled"
        )
        return None
    return start_ezio_sensor_service(adapter, interval_sec=interval_sec)


async def execute_order_action(adapter: Any, action: Any) -> Tuple[bool, str]:
    """action 종류에 맞는 하드웨어 실행기로 전달하고 결과를 표준화한다."""
    if is_clamp_action(action.action_type):
        return True, await execute_clamp_action(adapter, action)

    if is_pio_action(action.action_type):
        pio_result = await execute_pio_action(adapter, action)
        return bool(pio_result["ok"]), summarize_pio_result(pio_result)

    if is_ezio_action(action.action_type):
        ezio_result = await execute_ezio_action(adapter, action)
        return bool(ezio_result["ok"]), summarize_ezio_result(ezio_result)

    raise ValueError(f"Unsupported hardware action: {action.action_type}")
