"""Bridge status-updating built-in actions into awaitable registry actions."""

from __future__ import annotations

from core.action_registry import ActionResult, ActionSpec, InlineActionContext
from core.motion_primitives import (
    run_goto_nearest_node,
    run_jibot_motion_rule,
    run_localize,
    run_manual_move,
    run_manual_stop,
    run_rotate_to,
    run_switch_map,
    stop_motion,
)
_MOTION = {"manualMove", "jibotMotionRule", "gotoNearestNode", "rotateTo"}


def _handler(primitive):
    async def handle(ctx: InlineActionContext) -> ActionResult:
        return await primitive(ctx.adapter, ctx.action)

    return handle


async def _cancel_motion(ctx: InlineActionContext) -> None:
    await stop_motion(ctx.adapter, ctx.action.action_id)


async def _manual_stop(ctx: InlineActionContext) -> ActionResult:
    return await run_manual_stop(ctx.adapter)


async def _switch_map(ctx: InlineActionContext) -> ActionResult:
    map_id = str(ctx.params.get("mapId") or "").strip()
    return await run_switch_map(ctx.adapter, map_id)


def action_specs() -> tuple[ActionSpec, ...]:
    bridged = tuple(
        ActionSpec(
            action_type=action_type,
            handler=_handler(primitive),
            cancel_handler=_cancel_motion if action_type in _MOTION else None,
            timeout_sec=0.0,
            motion=action_type in _MOTION,
            label=action_type,
        )
        for action_type, primitive in (
            ("manualMove", run_manual_move),
            ("jibotMotionRule", run_jibot_motion_rule),
            ("gotoNearestNode", run_goto_nearest_node),
            # Turn in place to an absolute heading. Built-in rather than a site
            # recipe because the arrival heading is a fleet-wide need: the map
            # cannot express a per-order heading, and every site would otherwise
            # copy the same recipe into recipes.hcl.
            ("rotateTo", run_rotate_to),
        )
    )
    return bridged + (
        ActionSpec(
            "manualStop",
            handler=_manual_stop,
            cancel_handler=_cancel_motion,
            motion=False,
            label="manualStop",
        ),
        ActionSpec(
            "switchMap",
            handler=_switch_map,
            motion=False,
            label="switchMap",
        ),
        # Re-anchor via the contract localize() (JIBOT UmLocalize / SEER reloc).
        # motion=True to match the built-in localize classification; no cancel
        # handler because a re-anchor is a fast command, not a drive to stop.
        ActionSpec(
            "localize",
            handler=_handler(run_localize),
            motion=True,
            label="localize",
        ),
    )
