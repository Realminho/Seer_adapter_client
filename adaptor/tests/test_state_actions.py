import asyncio
from types import SimpleNamespace

import pytest

from config.config import StateActionCallConfig, StateActionConfig
from core.action_registry import ActionRegistry, ActionResult, ActionSpec
from core.state_actions import StateActionController
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


def _binding(state="DRIVING", start=None, end=None):
    return StateActionConfig(
        state=state,
        start=StateActionCallConfig(start) if start else None,
        end=StateActionCallConfig(end) if end else None,
    )


def _adapter(specs):
    return SimpleNamespace(_action_registry=ActionRegistry(specs))


def test_runs_start_once_and_end_on_state_transition():
    async def scenario():
        calls = []

        def record(ctx):
            calls.append((ctx.action.action_type, dict(ctx.params)))
            return ActionResult(ActionStatus.FINISHED)

        adapter = _adapter([ActionSpec("on", handler=record), ActionSpec("off", handler=record)])
        binding = _binding(start="on", end="off")
        binding.start.parameters["signal"] = "lamp"
        controller = StateActionController(adapter, [binding])

        controller.observe("IDLE")
        controller.observe("DRIVING")
        controller.observe("DRIVING")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        controller.observe("IDLE")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert calls == [("on", {"signal": "lamp"}), ("off", {})]

    asyncio.run(scenario())


def test_leaving_state_cancels_start_before_end():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()
        calls = []

        async def hold(_ctx):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        def finish(ctx):
            calls.append(ctx.action.action_type)
            return ActionResult(ActionStatus.FINISHED)

        adapter = _adapter([ActionSpec("hold", handler=hold), ActionSpec("off", handler=finish)])
        controller = StateActionController(adapter, [_binding(start="hold", end="off")])

        controller.observe("DRIVING")
        await started.wait()
        controller.observe("IDLE")
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        await asyncio.sleep(0)

        assert calls == ["off"]

    asyncio.run(scenario())


def test_rejects_unregistered_action_at_startup():
    adapter = _adapter([])
    with pytest.raises(ValueError, match="unregistered action 'missing'"):
        StateActionController(adapter, [_binding(start="missing")])
