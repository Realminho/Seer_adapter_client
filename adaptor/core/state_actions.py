"""Run configured extension/recipe actions on workingState transitions."""

from __future__ import annotations

import asyncio
import itertools
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, Optional

from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


def validate_state_actions(bindings, registry: Any) -> None:
    """Reject lifecycle calls that the boot-time registry cannot execute."""
    for binding in bindings:
        for phase in ("start", "end"):
            call = getattr(binding, phase, None)
            if call is not None and not registry.has(call.action):
                raise ValueError(
                    f"state_action '{binding.state}' {phase} references "
                    f"unregistered action '{call.action}'"
                )


class StateActionController:
    """Serialize state lifecycle callbacks without blocking state publishing."""

    def __init__(self, adapter: Any, bindings=()) -> None:
        self._adapter = adapter
        self._bindings = {binding.state: binding for binding in bindings}
        self._observed_state: Optional[str] = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: Optional[asyncio.Task[Any]] = None
        self._start_task: Optional[asyncio.Task[Any]] = None
        self._active_state: Optional[str] = None
        self._ids = itertools.count(1)

        validate_state_actions(self._bindings.values(), adapter._action_registry)

    def observe(self, state: str) -> None:
        """Record one state value; unchanged heartbeat values are ignored."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        if state == self._observed_state:
            return
        self._observed_state = state
        self._queue.put_nowait(state)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
            self._worker.add_done_callback(self._worker_done)

    def _worker_done(self, _task: asyncio.Task[Any]) -> None:
        if not self._queue.empty():
            self._worker = asyncio.create_task(self._run())
            self._worker.add_done_callback(self._worker_done)

    async def _run(self) -> None:
        while not self._queue.empty():
            state = await self._queue.get()
            await self._leave_active_state()
            self._active_state = state
            binding = self._bindings.get(state)
            call = getattr(binding, "start", None) if binding is not None else None
            if call is not None:
                self._start_task = asyncio.create_task(
                    self._execute(state, "start", call)
                )

    async def _leave_active_state(self) -> None:
        state = self._active_state
        if state is None:
            return

        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._start_task
        self._start_task = None

        binding = self._bindings.get(state)
        call = getattr(binding, "end", None) if binding is not None else None
        if call is not None:
            await self._execute(state, "end", call)
        self._active_state = None

    async def _execute(self, state: str, phase: str, call: Any) -> None:
        action = SimpleNamespace(
            action_id=f"__state_action__:{state}:{phase}:{next(self._ids)}",
            action_type=call.action,
            action_parameters=[
                SimpleNamespace(key=key, value=value)
                for key, value in call.parameters.items()
            ],
        )
        try:
            result = await self._adapter._action_registry.execute(
                action, self._adapter
            )
        except asyncio.CancelledError:
            print(f"[STATE ACTION CANCELLED] state={state} phase={phase}")
            raise
        except Exception as exc:
            print(
                f"[STATE ACTION FAILED] state={state} phase={phase} "
                f"action={call.action}: {exc}"
            )
            return
        if result.status != ActionStatus.FINISHED:
            print(
                f"[STATE ACTION FAILED] state={state} phase={phase} "
                f"action={call.action}: {result.description or result.status.value}"
            )
