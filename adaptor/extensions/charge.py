"""현재 도킹 위치에서 충전을 재개하는 extension 보조 기능."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional, Tuple

from protocol.vda5050_common import ErrorLevel
from protocol.vda_2_0_0.vda5050_2_0_0_state import (
    Error,
    ErrorReference,
    ErrorType,
)


def should_charge_in_place(adapter: Any, node_id: Optional[str]) -> bool:
    """요청 노드가 현재 도킹 노드와 같아 제자리 충전이 가능한지 판단한다."""
    current = adapter._last_node_id
    if not current:
        return False
    target = node_id or current
    return target == current and adapter._is_dock_motion_node(current)


def release_charge_in_place(adapter: Any) -> None:
    """진행 중인 제자리 충전 릴레이 유지를 안전하게 해제한다."""
    if not adapter._charge_in_place_active:
        return
    adapter._charge_circuit.stop_hold()
    adapter._charge_in_place_active = False
    print("[CHARGE IN PLACE] hold released")


def set_charge_in_place_disabled_error(adapter: Any, reason: str) -> None:
    """제자리 충전 비활성 오류를 중복 없이 VDA 상태에 기록한다."""
    if adapter.state is None:
        return

    adapter.state.errors = [
        error
        for error in adapter.state.errors
        if not (
            getattr(error, "error_type", None) == ErrorType.UNKNOWN_ERROR
            and any(
                getattr(ref, "reference_key", None) == "actionType"
                and getattr(ref, "reference_value", None) == "chargeInPlace"
                for ref in (getattr(error, "error_references", []) or [])
            )
            and any(
                getattr(ref, "reference_key", None) == "reason"
                and getattr(ref, "reference_value", None) == "chargeCircuitDisabled"
                for ref in (getattr(error, "error_references", []) or [])
            )
        )
    ]
    adapter.state.errors.append(
        Error(
            error_type=ErrorType.UNKNOWN_ERROR,
            error_level=ErrorLevel.CRITICAL,
            error_references=[
                ErrorReference("actionType", "chargeInPlace"),
                ErrorReference("reason", "chargeCircuitDisabled"),
                ErrorReference("config", "charge_circuit.enabled=false"),
            ],
            error_description=reason,
        )
    )
    adapter.request_state_publish("charge in place disabled")


async def run_charge_in_place(adapter: Any) -> Tuple[bool, str]:
    """충전 릴레이를 유지하고 제한 시간 안에 충전 시작을 확인한다."""
    if not bool(getattr(adapter.config.charge_circuit, "enabled", False)):
        reason = (
            "chargeInPlace disabled: charge_circuit.enabled=false; "
            "ROS /jcmd charge relay publisher is not configured; "
            "set [charge_circuit].enabled=true in adaptor/config/config.toml "
            "or the deployed AMR config"
        )
        print(f"[CHARGE IN PLACE CRITICAL] {reason}")
        adapter._charge_in_place_active = False
        set_charge_in_place_disabled_error(adapter, reason)
        return False, reason

    adapter._charge_circuit.start_hold()
    adapter._charge_in_place_active = True
    timeout = float(adapter.config.charge_circuit.verify_timeout_sec)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if adapter._is_vehicle_charging():
            return True, "charging in place"
        await asyncio.sleep(0.2)
    if adapter._is_vehicle_charging():
        return True, "charging in place"
    adapter._charge_circuit.stop_hold()
    adapter._charge_in_place_active = False
    return False, "in-place charge: charger not engaged"
