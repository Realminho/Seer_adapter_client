"""Awaitable primitives shared by recipe/order registry wrappers."""

from __future__ import annotations

from typing import Any

from core.action_registry import ActionResult
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


async def _run_status_action(adapter: Any, action: Any, method_name: str) -> ActionResult:
    future = adapter._register_action_completion(action.action_id)
    try:
        getattr(adapter, method_name)(action)
        return await future
    finally:
        adapter._retire_action_completion(action.action_id)


async def run_manual_move(adapter: Any, action: Any) -> ActionResult:
    return await _run_status_action(
        adapter, action, "_handle_manual_move_instant_action"
    )


async def run_jibot_motion_rule(adapter: Any, action: Any) -> ActionResult:
    return await _run_status_action(
        adapter, action, "_handle_motion_rule_instant_action"
    )


async def run_goto_nearest_node(adapter: Any, action: Any) -> ActionResult:
    return await _run_status_action(
        adapter, action, "_handle_goto_nearest_node_instant_action"
    )


async def run_localize(adapter: Any, action: Any) -> ActionResult:
    return await _run_status_action(
        adapter, action, "_handle_localize_instant_action"
    )


async def run_rotate_to(adapter: Any, action: Any) -> ActionResult:
    return await _run_status_action(
        adapter, action, "_handle_rotate_to_instant_action"
    )


async def run_manual_stop(adapter: Any) -> ActionResult:
    adapter._manual_control_active = False
    adapter._cancel_manual_drive_watchdog()
    if adapter._vehicle is None:
        return ActionResult(ActionStatus.FAILED, "JIBOT vehicle is not initialized")
    await adapter._vehicle.um_stop()
    return ActionResult(ActionStatus.FINISHED, "manual stop")


async def run_switch_map(adapter: Any, map_id: str) -> ActionResult:
    if not map_id:
        return ActionResult(
            ActionStatus.FAILED, "switchMap requires non-empty mapId"
        )
    try:
        if adapter._is_simulator():
            adapter._current_map_id = map_id
        else:
            await adapter._switch_map_and_confirm(map_id)
        return ActionResult(
            ActionStatus.FINISHED, f"current map confirmed: {map_id}"
        )
    except Exception as exc:
        return ActionResult(ActionStatus.FAILED, f"switchMap failed: {exc}")


async def stop_motion(adapter: Any, action_id: str) -> None:
    adapter._retire_action_completion(action_id)
    cancel_task = getattr(adapter, "_cancel_action_task", None)
    if callable(cancel_task):
        await cancel_task(action_id)
    adapter._manual_control_active = False
    adapter._cancel_manual_drive_watchdog()
    if adapter._vehicle is not None:
        await adapter._vehicle.um_stop()
