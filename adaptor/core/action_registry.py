"""Hybrid action registry for VDA5050 instant-action plugins."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


_ADAPTER_ROOT = Path(__file__).resolve().parents[1]
_TERMINAL_STATUSES = {ActionStatus.FINISHED, ActionStatus.FAILED}
_SENSITIVE_SNAPSHOT_ROOTS = {"config.mqtt_broker"}
_SENSITIVE_SNAPSHOT_PARTS = {
    "access_key",
    "api_key",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}


def _terminal_status(value: Any) -> ActionStatus:
    status = value if isinstance(value, ActionStatus) else ActionStatus(str(value))
    if status not in _TERMINAL_STATUSES:
        raise ValueError("ActionResult status must be terminal: FINISHED or FAILED")
    return status


@dataclass(frozen=True)
class ActionResult:
    status: ActionStatus
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _terminal_status(self.status))


@dataclass(frozen=True)
class ActionParameterSpec:
    """One operator-editable action parameter exposed by the WebUI."""

    name: str
    required: bool = False
    input_type: str = "text"
    placeholder: str = ""
    value_type: str = "string"
    #: 값이 정해진 목록뿐이면 여기 담는다. WebUI가 자유 입력 대신 select를
    #: 그려서 오타로 액션이 실패하는 일을 없앤다.
    choices: tuple = ()
    #: 사람이 읽는 짧은 설명. 칸 이름만으로는 무엇을 넣는지 알 수 없는 것이
    #: 많아서(media? port?) WebUI가 이름 옆에 같이 보여 준다. 비면 이름만 나온다.
    label: str = ""
    #: choices가 "설정이 아는 값" 힌트일 뿐일 때 True. WebUI가 select 대신
    #: datalist를 단 입력으로 그려서 목록에 없는 값도 넣을 수 있다. 값이 정말로
    #: 그 목록뿐인 파라미터(on/off, 설정을 되짚어 푸는 station)는 False로 둔다 —
    #: 그런 칸의 자유 입력은 실행 시점에야 드러나는 오타일 뿐이다.
    editable: bool = False


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    enabled: bool = True
    runner: str = "inline"
    handler: Optional[Callable[[Any], Any]] = None
    cancel_handler: Optional[Callable[[Any], Any]] = None
    module: Optional[str] = None
    command: List[str] = field(default_factory=list)
    timeout_sec: float = 0.0
    motion: bool = False
    snapshot_fields: List[str] = field(default_factory=list)
    label: str = ""
    parameters: Tuple[ActionParameterSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InlineActionContext:
    action: Any
    adapter: Any
    params: Dict[str, Any]

    def report(self, description: str = "") -> None:
        self.adapter._update_instant_action_status(
            self.action.action_id,
            ActionStatus.RUNNING,
            description or None,
        )


def _param_value(value: Any) -> Any:
    return getattr(value, "value", value)


def action_params(action: Any) -> Dict[str, Any]:
    return {
        param.key: _param_value(param.value)
        for param in getattr(action, "action_parameters", []) or []
    }


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default) if obj is not None else default


def _is_simulator(adapter: Any) -> bool:
    try:
        checker = getattr(adapter, "_is_simulator", None)
        if callable(checker):
            return bool(checker())
    except Exception:
        return False
    vehicle = getattr(adapter, "_vehicle", None)
    return bool(getattr(vehicle, "is_simulator", False))


def _pose_snapshot(adapter: Any) -> Optional[Dict[str, Any]]:
    state = getattr(adapter, "state", None)
    position = _safe_getattr(state, "agv_position")
    if position is None:
        return None
    return {
        "x": _safe_getattr(position, "x"),
        "y": _safe_getattr(position, "y"),
        "theta": _safe_getattr(position, "theta"),
        "map_id": _safe_getattr(position, "map_id"),
    }


def _snapshot_value(adapter: Any, dotted_name: str) -> Any:
    current = adapter
    for part in dotted_name.split("."):
        current = _safe_getattr(current, part)
    return current


def _is_denied_snapshot_field(dotted_name: str) -> bool:
    field_name = dotted_name.strip()
    if not field_name:
        return True
    for root in _SENSITIVE_SNAPSHOT_ROOTS:
        if field_name == root or field_name.startswith(f"{root}."):
            return True
    parts = {part.lower() for part in field_name.split(".")}
    return bool(parts & _SENSITIVE_SNAPSHOT_PARTS)


def build_subprocess_payload(
    action: Any,
    adapter: Any,
    snapshot_fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    state = getattr(adapter, "state", None)
    config = getattr(adapter, "config", None)
    vehicle = getattr(config, "vehicle", None)
    snapshot: Dict[str, Any] = {
        "serial_number": _safe_getattr(vehicle, "serial_number"),
        "simulation": _is_simulator(adapter),
        "current_map_id": _safe_getattr(adapter, "_current_map_id"),
        "last_node_id": _safe_getattr(adapter, "_last_node_id"),
        "last_node_sequence_id": _safe_getattr(adapter, "_last_node_sequence_id"),
        "pose": _pose_snapshot(adapter),
        "order_id": _safe_getattr(state, "order_id"),
        "paused": _safe_getattr(state, "paused", False),
        "work_in_progress": _safe_getattr(adapter, "_work_in_progress"),
    }
    for field_name in snapshot_fields or []:
        if _is_denied_snapshot_field(field_name):
            print(f"[ACTION SNAPSHOT SKIP] denied field: {field_name}")
            continue
        snapshot[field_name] = _snapshot_value(adapter, field_name)
    return {
        "action_id": action.action_id,
        "action_type": action.action_type,
        "params": action_params(action),
        "snapshot": snapshot,
    }


class InlineRunner:
    def run(self, spec: ActionSpec, action: Any, adapter: Any) -> Any:
        if spec.handler is None:
            return ActionResult(ActionStatus.FAILED, "inline action has no handler")
        return spec.handler(
            InlineActionContext(
                action=action,
                adapter=adapter,
                params=action_params(action),
            )
        )


class SubprocessRunner:
    async def run(self, spec: ActionSpec, action: Any, adapter: Any) -> ActionResult:
        if not spec.command:
            return ActionResult(ActionStatus.FAILED, "subprocess action has no command")

        payload = json.dumps(
            build_subprocess_payload(action, adapter, spec.snapshot_fields)
        ).encode("utf-8")
        proc = await asyncio.create_subprocess_exec(
            *spec.command,
            cwd=str(_ADAPTER_ROOT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload),
                timeout=spec.timeout_sec if spec.timeout_sec > 0 else None,
            )
        except asyncio.TimeoutError:
            proc.kill()
            with suppress(Exception):
                await proc.wait()
            return ActionResult(ActionStatus.FAILED, "subprocess timeout")

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if stderr_text:
            print(f"[ACTION SUBPROCESS STDERR] {action.action_type}: {stderr_text}")
        if proc.returncode != 0:
            return ActionResult(
                ActionStatus.FAILED,
                f"subprocess exited with code {proc.returncode}",
            )

        try:
            data = json.loads(stdout.decode("utf-8").strip())
            return ActionResult(data.get("status"), data.get("description", ""))
        except Exception as exc:
            return ActionResult(ActionStatus.FAILED, f"invalid terminal result: {exc}")


class ActionRegistry:
    def __init__(self, specs: Optional[Iterable[ActionSpec]] = None) -> None:
        self._specs: Dict[str, ActionSpec] = {}
        self._inline = InlineRunner()
        self._subprocess = SubprocessRunner()
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ActionSpec) -> None:
        if not spec.enabled:
            return
        if spec.action_type in self._specs:
            raise ValueError(f"duplicate action_type: {spec.action_type}")
        self._specs[spec.action_type] = spec

    def has(self, action_type: str) -> bool:
        return action_type in self._specs

    def action_types(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def is_motion(self, action_type: str) -> bool:
        spec = self._specs.get(action_type)
        return bool(spec and spec.motion)

    def timeout_sec(self, action_type: str) -> float:
        """Return a registered action's default timeout, or zero when unbounded."""
        spec = self._specs.get(action_type)
        return float(spec.timeout_sec) if spec is not None else 0.0

    def dispatch(self, action: Any, adapter: Any) -> bool:
        spec = self._specs.get(action.action_type)
        if spec is None:
            return False
        if spec.runner == "subprocess":
            self._schedule(action, adapter, self._subprocess.run(spec, action, adapter))
            return True
        try:
            result = self._inline.run(spec, action, adapter)
        except Exception as exc:
            self._finish(action, adapter, ActionResult(ActionStatus.FAILED, str(exc)))
            return True

        if inspect.isawaitable(result):
            self._schedule(action, adapter, result, timeout_sec=spec.timeout_sec)
        else:
            if result is None:
                result = ActionResult(ActionStatus.FINISHED, "")
            self._finish(action, adapter, result)
        return True

    async def execute(
        self,
        action: Any,
        adapter: Any,
        *,
        timeout_sec: Optional[float] = None,
    ) -> ActionResult:
        """Execute a registered action inline and return its terminal result.

        This path does not update a VDA5050 action state. It is used by order
        actions and configured extension groups that own the parent state.
        """
        spec = self._specs.get(action.action_type)
        if spec is None:
            return ActionResult(
                ActionStatus.FAILED,
                f"unregistered extension action: {action.action_type}",
            )
        if spec.runner == "subprocess":
            awaitable = self._subprocess.run(spec, action, adapter)
        else:
            try:
                result = self._inline.run(spec, action, adapter)
            except Exception as exc:
                return ActionResult(ActionStatus.FAILED, str(exc))
            if not inspect.isawaitable(result):
                if result is None:
                    return ActionResult(ActionStatus.FINISHED, "")
                if isinstance(result, ActionResult):
                    return result
                return ActionResult(
                    ActionStatus.FAILED,
                    f"handler must return ActionResult, got {type(result).__name__}",
                )
            awaitable = result
        async def cancel_action() -> Optional[Exception]:
            if spec.cancel_handler is None:
                return None
            try:
                cancelled = spec.cancel_handler(
                    InlineActionContext(
                        action=action,
                        adapter=adapter,
                        params=action_params(action),
                    )
                )
                if inspect.isawaitable(cancelled):
                    await cancelled
            except Exception as exc:
                return exc
            return None

        try:
            effective_timeout = spec.timeout_sec if timeout_sec is None else timeout_sec
            result = (
                await asyncio.wait_for(awaitable, timeout=effective_timeout)
                if effective_timeout > 0
                else await awaitable
            )
        except asyncio.TimeoutError:
            cancel_error = await cancel_action()
            if cancel_error is not None:
                return ActionResult(
                    ActionStatus.FAILED,
                    f"action timeout; cancellation failed: {cancel_error}",
                )
            return ActionResult(ActionStatus.FAILED, "action timeout")
        except asyncio.CancelledError:
            await cancel_action()
            raise
        except Exception as exc:
            return ActionResult(ActionStatus.FAILED, str(exc))
        if result is None:
            return ActionResult(ActionStatus.FINISHED, "")
        if not isinstance(result, ActionResult):
            return ActionResult(
                ActionStatus.FAILED,
                f"handler must return ActionResult, got {type(result).__name__}",
            )
        return result

    def _schedule(
        self,
        action: Any,
        adapter: Any,
        awaitable: Any,
        *,
        timeout_sec: float = 0.0,
    ) -> None:
        if not self._adapter_loop_ready(adapter):
            self._dispose_awaitable(awaitable)
            self._finish(
                action,
                adapter,
                ActionResult(ActionStatus.FAILED, "adapter event loop is not running"),
            )
            return

        adapter._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                if timeout_sec > 0:
                    result = await asyncio.wait_for(awaitable, timeout=timeout_sec)
                else:
                    result = await awaitable
                if result is None:
                    result = ActionResult(ActionStatus.FINISHED, "")
                self._finish(action, adapter, result)
            except asyncio.TimeoutError:
                self._finish(
                    action,
                    adapter,
                    ActionResult(ActionStatus.FAILED, "action timeout"),
                )
            except Exception as exc:
                self._finish(
                    action,
                    adapter,
                    ActionResult(ActionStatus.FAILED, str(exc)),
                )

        adapter._run_on_adapter_loop(lambda: _run())

    @staticmethod
    def _adapter_loop_ready(adapter: Any) -> bool:
        if not hasattr(adapter, "_loop"):
            return True
        loop = getattr(adapter, "_loop", None)
        return bool(loop is not None and loop.is_running())

    @staticmethod
    def _dispose_awaitable(awaitable: Any) -> None:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        elif isinstance(awaitable, asyncio.Future):
            awaitable.cancel()

    def _finish(self, action: Any, adapter: Any, result: ActionResult) -> None:
        if not isinstance(result, ActionResult):
            result = ActionResult(
                ActionStatus.FAILED,
                f"handler must return ActionResult, got {type(result).__name__}",
            )
        adapter._update_instant_action_status(
            action.action_id,
            result.status,
            result.description,
        )


def _load_inline_handler(module_name: Optional[str]) -> Optional[Callable[[Any], Any]]:
    if not module_name:
        return None
    module = importlib.import_module(module_name)
    register = getattr(module, "register", None)
    if callable(register):
        registered = register()
        if isinstance(registered, ActionSpec):
            return registered.handler
        if callable(registered):
            return registered
    handle = getattr(module, "handle", None)
    return handle if callable(handle) else None


def _builtin_instant_action_types() -> set[str]:
    try:
        from core.registry import _JIBOT_INSTANT_ACTIONS
    except Exception:
        return set()
    return {action.action_type for action in _JIBOT_INSTANT_ACTIONS}


def build_registry_from_config(
    actions: Iterable[Any],
    first_party_specs: Optional[Iterable[ActionSpec]] = None,
) -> ActionRegistry:
    actions = tuple(actions or ())
    first_party_specs = tuple(first_party_specs or ())
    disabled_action_types = {
        action.action_type for action in actions if not bool(getattr(action, "enabled", True))
    }
    registry = ActionRegistry(
        spec for spec in first_party_specs
        if spec.action_type not in disabled_action_types
    )
    reserved_action_types = _builtin_instant_action_types() | {
        spec.action_type for spec in first_party_specs
    }
    for action in actions:
        action_type = action.action_type
        if not bool(getattr(action, "enabled", True)):
            print(f"[ACTION REGISTRY SKIP] {action_type}: disabled")
            continue
        if action_type in reserved_action_types:
            print(
                f"[ACTION REGISTRY SKIP] {action_type}: "
                "configured action shadows a built-in or first-party action"
            )
            continue
        runner = getattr(action, "runner", "inline")
        handler = None
        if runner == "inline":
            try:
                handler = _load_inline_handler(getattr(action, "module", None))
            except Exception as exc:
                print(f"[ACTION REGISTRY SKIP] {action_type}: {exc}")
                continue
        try:
            registry.register(
                ActionSpec(
                    action_type=action_type,
                    enabled=bool(getattr(action, "enabled", True)),
                    runner=runner,
                    handler=handler,
                    module=getattr(action, "module", None),
                    command=list(getattr(action, "command", []) or []),
                    timeout_sec=float(getattr(action, "timeout_sec", 0.0) or 0.0),
                    motion=bool(getattr(action, "motion", False)),
                    snapshot_fields=list(getattr(action, "snapshot_fields", []) or []),
                )
            )
        except Exception as exc:
            print(f"[ACTION REGISTRY SKIP] {action_type}: {exc}")
    return registry
