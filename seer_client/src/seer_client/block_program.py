"""Interpreter for nested SEER Block Builder programs.

The unchanged Adapter Recipe engine is intentionally linear.  Builder
programs containing Entry-style repeat/condition blocks are therefore stored
as one ``seerBlockProgram`` step and interpreted here using only allow-listed
SEER actions and read-only live-state predicates.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

PROGRAM_SCHEMA = 1
RUNTIME_SCHEMA = 1
VARIABLE_MARKER = "__seer_variable__"
MAX_PROGRAM_BYTES = 512_000
MAX_NESTING = 8
MAX_REPEAT = 100
MAX_EXECUTIONS = 1_000
MAX_RESTARTS = 100
MAX_CALL_DEPTH = 8
ALLOWED_ACTIONS = frozenset(
    {
        "seerCoordinateNav",
        "seerTranslate",
        "seerTurn",
        "seerPathNav",
        "seerSetDO",
        "seerJackLoad",
        "seerJackUnload",
        "seerCameraDock",
        "seerWait",
    }
)
CONDITION_SOURCES = frozenset(
    {
        "di",
        "emergency",
        "blocked",
        "battery",
        "current_point",
        "charging",
        "motor",
        "localization",
    }
)
COMPARATORS = frozenset({"==", "!=", ">", ">=", "<", "<="})
VARIABLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class BlockProgramError(ValueError):
    """A stored or submitted block program is malformed."""


class _BreakLoop(Exception):
    pass


class _ContinueLoop(Exception):
    pass


class _StopProgram(Exception):
    pass


class _RestartProgram(Exception):
    pass


def _result(status_name: str, description: str):
    """Import Adapter result types only when the bridge actually executes.

    ``seer_client`` is also imported by protocol-only compatibility tests that
    intentionally put only ``seer_client/src`` on ``PYTHONPATH``. Keeping the
    original Adapter dependency lazy preserves that standalone client mode.
    """

    from core.action_registry import ActionResult
    from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus

    return ActionResult(getattr(ActionStatus, status_name), description)


def encode_program(nodes: Sequence[Mapping[str, Any]]) -> str:
    raw = json.dumps(
        {"schema": PROGRAM_SCHEMA, "nodes": list(nodes)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > MAX_PROGRAM_BYTES:
        raise BlockProgramError("SEER block program is too large")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_program(encoded: str) -> Tuple[Mapping[str, Any], ...]:
    try:
        raw = base64.urlsafe_b64decode(str(encoded).encode("ascii"))
        if len(raw) > MAX_PROGRAM_BYTES:
            raise BlockProgramError("SEER block program is too large")
        value = json.loads(raw.decode("utf-8"))
    except BlockProgramError:
        raise
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise BlockProgramError("invalid SEER block program") from exc
    if not isinstance(value, Mapping) or value.get("schema") != PROGRAM_SCHEMA:
        raise BlockProgramError("unsupported SEER block program schema")
    nodes = value.get("nodes")
    if not isinstance(nodes, list):
        raise BlockProgramError("SEER block program nodes must be a list")
    return tuple(nodes)


def program_variables(encoded: str) -> Tuple[str, ...]:
    """Return variable names in stable first-use order."""

    found = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            name = value.get(VARIABLE_MARKER)
            if isinstance(name, str) and name and name not in found:
                found.append(name)
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(decode_program(encoded))
    return tuple(found)


def _resolve(value: Any, params: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping) and VARIABLE_MARKER in value:
        name = str(value.get(VARIABLE_MARKER, ""))
        supplied = params.get(name)
        if supplied is None or (isinstance(supplied, str) and not supplied.strip()):
            supplied = value.get("default")
        return supplied
    if isinstance(value, Mapping):
        return {key: _resolve(item, params) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, params) for item in value]
    return value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "active", "blocked"}


def _comparable(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    try:
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower()


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    left = _comparable(actual)
    right = _comparable(expected)
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    try:
        if operator == ">":
            return left > right
        if operator == ">=":
            return left >= right
        if operator == "<":
            return left < right
        if operator == "<=":
            return left <= right
    except TypeError:
        return False
    raise BlockProgramError(f"unsupported condition operator: {operator}")


async def _condition_value(vehicle: Any, condition: Mapping[str, Any]) -> Any:
    source = str(condition.get("source", "")).strip().lower()
    if source not in CONDITION_SOURCES:
        raise BlockProgramError(f"unsupported condition source: {source}")
    if source == "di":
        channel = int(condition.get("channel", 0))
        return await _read_di(vehicle, channel)
    if source == "battery":
        getter = getattr(vehicle, "get_battery_info", None)
        payload = await getter() if callable(getter) else {}
        value = getattr(vehicle, "_battery", None)
        if value is None and isinstance(payload, Mapping):
            value = payload.get("battery_level", payload.get("battery"))
            if value is not None and 0.0 <= float(value) <= 1.0:
                value = float(value) * 100.0
        if value is None:
            raise BlockProgramError("SEER battery state is unavailable")
        return value
    if source == "current_point":
        getter = getattr(vehicle, "get_localization_info", None)
        if callable(getter):
            await getter()
        return str(getattr(vehicle, "_station", "") or "")
    if source == "charging":
        getter = getattr(vehicle, "get_battery_info", None)
        if callable(getter):
            await getter()
        return bool(getattr(vehicle, "charging", getattr(vehicle, "_charging", False)))
    if source == "motor":
        getter = getattr(vehicle, "get_motor_state", None)
        value = await getter() if callable(getter) else None
        if value is None:
            value = getattr(vehicle, "motor_flag", getattr(vehicle, "_motor_flag", None))
        if value is None:
            raise BlockProgramError("SEER motor state is unavailable")
        return bool(value)
    if source == "localization":
        getter = getattr(vehicle, "get_localization_info", None)
        if callable(getter):
            await getter()
        value = getattr(
            vehicle,
            "localization_score",
            getattr(vehicle, "_localization_score", None),
        )
        if value is None:
            raise BlockProgramError("SEER localization score is unavailable")
        return float(value)
    if source == "blocked":
        getter = getattr(vehicle, "get_blocked", None)
        payload = await getter() if callable(getter) else {}
        if isinstance(payload, Mapping) and "blocked" in payload:
            return bool(payload["blocked"])
        return bool(getattr(vehicle, "_blocked", False))
    getter = getattr(vehicle, "get_emergency_state", None)
    payload = await getter() if callable(getter) else {}
    if isinstance(payload, Mapping):
        return any(bool(payload.get(key)) for key in ("emergency", "driver_emc", "soft_emc"))
    return bool(getattr(vehicle, "_emergency", False))


async def _read_di(vehicle: Any, channel: int) -> bool:
    reader = getattr(vehicle, "read_di", None)
    if not callable(reader):
        io_service = getattr(vehicle, "io", None)
        reader = getattr(io_service, "read_di", None)
    if not callable(reader):
        raise BlockProgramError("SEER DI state is unavailable")
    value = await reader(int(channel))
    if value is None:
        raise BlockProgramError(f"SEER DI{channel} state is unavailable")
    return bool(value)


class BlockProgramExecutor:
    def __init__(
        self,
        context: Any,
        nodes: Sequence[Mapping[str, Any]],
        recipe_name: str = "",
        *,
        start_trace_id: str = "",
    ) -> None:
        self.context = context
        self.nodes = tuple(nodes)
        self.executions = 0
        self.generated_ids = set()
        self.variables = dict(getattr(context, "params", {}) or {})
        self.functions: Dict[str, Tuple[Tuple[Mapping[str, Any], ...], str]] = {}
        self.call_stack = []
        self.call_trace_stack = []
        self.loop_stack = []
        self.completed_trace_ids = set()
        self.block_counts: Dict[str, int] = {}
        self.current_trace_id = ""
        self.current_kind = ""
        self.phase = "starting"
        self.condition_state: Optional[Dict[str, Any]] = None
        self.message = ""
        self.started_at = time.time()
        self.recipe_name = str(recipe_name or getattr(context.action, "action_type", "") or "").strip()
        self.start_trace_id = str(start_trace_id or "").strip()
        self.start_root_index = self._parse_start_root_index(self.start_trace_id)
        self.stop_after_current = False
        self.stop_after_current_reason = ""
        self._block_reconnect_baselines: Dict[str, int] = {}
        self._runtime_heartbeat_task: Optional[asyncio.Task] = None
        runtime_path = str(os.getenv("SEER_BLOCK_RUNTIME_PATH", "") or "").strip()
        self.runtime_path = Path(runtime_path) if runtime_path else None
        for root_index, node in enumerate(self.nodes, start=1):
            if not isinstance(node, Mapping) or str(node.get("kind", "")) != "define_function":
                continue
            parameters = node.get("parameters", {})
            children = node.get("children", ())
            if not isinstance(parameters, Mapping) or not isinstance(children, list):
                raise BlockProgramError("function definition is malformed")
            name = str(parameters.get("function_name", "")).strip()
            if not VARIABLE_NAME.fullmatch(name):
                raise BlockProgramError("invalid function name")
            if name in self.functions:
                raise BlockProgramError(f"duplicate function definition: {name}")
            self.functions[name] = (tuple(children), f"r.{root_index}.c")
        self._publish_runtime("running")


    @staticmethod
    def _parse_start_root_index(trace_id: str) -> int:
        value = str(trace_id or "").strip()
        if not value:
            return 0
        match = re.fullmatch(r"r\.(\d+)", value)
        if match is None:
            raise BlockProgramError("특정 블록 시작은 최상위 실행 블록만 선택할 수 있습니다")
        index = int(match.group(1))
        if index <= 0:
            raise BlockProgramError("invalid SEER block start index")
        return index

    def _critical_reconnect_count(self) -> int:
        vehicle = getattr(self.context.adapter, "_vehicle", None)
        reader = getattr(vehicle, "critical_reconnect_count", None)
        if callable(reader):
            try:
                return max(0, int(reader()))
            except Exception:
                return 0
        return 0

    def _mark_link_loss_if_needed(self, trace_id: str) -> None:
        baseline = self._block_reconnect_baselines.pop(str(trace_id), None)
        if baseline is None:
            return
        current = self._critical_reconnect_count()
        if current <= baseline:
            return
        self.stop_after_current = True
        self.stop_after_current_reason = (
            "통신이 실행 중 복구되어 현재 블록 완료 후 Recipe를 정지합니다."
        )
        self.message = self.stop_after_current_reason

    async def _runtime_heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.5)
                if self.phase in {"finished", "stopped", "failed", "cancelled"}:
                    return
                self._publish_runtime("running")
        except asyncio.CancelledError:
            raise


    @staticmethod
    def _json_value(value: Any) -> Any:
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return str(value)

    def _runtime_payload(self, status: str, *, error: str = "") -> Dict[str, Any]:
        variables = {str(key): self._json_value(value) for key, value in self.variables.items()}
        parent_ids = [
            str(item.get("trace_id", "")) for item in self.loop_stack if item.get("trace_id")
        ] + [str(item) for item in self.call_trace_stack if item]
        return {
            "schema": RUNTIME_SCHEMA,
            "recipe_name": self.recipe_name,
            "action_id": str(getattr(self.context.action, "action_id", "") or ""),
            "status": status,
            "phase": self.phase,
            "current_trace_id": self.current_trace_id,
            "current_kind": self.current_kind,
            "active_parent_trace_ids": parent_ids,
            "completed_trace_ids": sorted(self.completed_trace_ids),
            "block_counts": dict(self.block_counts),
            "variables": variables,
            "condition": self.condition_state,
            "loops": list(self.loop_stack),
            "call_stack": list(self.call_stack),
            "executions": self.executions,
            "message": self.message,
            "error": str(error or ""),
            "start_trace_id": self.start_trace_id,
            "stop_after_current": bool(self.stop_after_current),
            "stop_after_current_reason": self.stop_after_current_reason,
            "started_at": self.started_at,
            "updated_at": time.time(),
        }

    def _publish_runtime(self, status: str = "running", *, error: str = "") -> None:
        if self.runtime_path is None:
            return
        payload = self._runtime_payload(status, error=error)
        try:
            self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.runtime_path.with_name(self.runtime_path.name + ".tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
                encoding="utf-8",
            )
            os.replace(temp_path, self.runtime_path)
        except OSError:
            pass

    def _enter_block(self, trace_id: str, kind: str, phase: str = "running") -> None:
        self.current_trace_id = str(trace_id)
        self.current_kind = str(kind)
        self.phase = str(phase)
        self._block_reconnect_baselines[self.current_trace_id] = self._critical_reconnect_count()
        self.block_counts[self.current_trace_id] = self.block_counts.get(self.current_trace_id, 0) + 1
        self._publish_runtime()

    def _focus_block(self, trace_id: str, kind: str, phase: str) -> None:
        self.current_trace_id = str(trace_id)
        self.current_kind = str(kind)
        self.phase = str(phase)
        self._publish_runtime()

    def _complete_block(self, trace_id: str, kind: str) -> None:
        self._mark_link_loss_if_needed(str(trace_id))
        self.completed_trace_ids.add(str(trace_id))
        self.current_trace_id = str(trace_id)
        self.current_kind = str(kind)
        self.phase = "complete"
        self._publish_runtime()

    def _clear_completed_prefix(self, prefix: str) -> None:
        prefix = str(prefix or "").strip()
        if not prefix:
            return
        marker = prefix + "."
        self.completed_trace_ids = {
            trace_id
            for trace_id in self.completed_trace_ids
            if trace_id != prefix and not trace_id.startswith(marker)
        }
        if isinstance(self.condition_state, Mapping):
            condition_trace = str(self.condition_state.get("trace_id", "") or "")
            if condition_trace == prefix or condition_trace.startswith(marker):
                self.condition_state = None

    def _reset_loop_iteration_runtime(self, trace_id: str) -> None:
        # A completion badge inside a loop describes the *current* iteration,
        # not every prior pass. Clear the loop body and callable function bodies
        # before the next pass so the Builder never shows stale "completed"
        # blocks from iteration N while iteration N+1 is running.
        self._clear_completed_prefix(f"{trace_id}.c")
        for _children, function_prefix in self.functions.values():
            self._clear_completed_prefix(function_prefix)

    def _set_active(self, action_type: Optional[str]) -> None:
        setter = getattr(self.context.adapter, "_set_active_action_step", None)
        if callable(setter):
            setter(self.context.action.action_id, action_type)

    async def run(self):
        try:
            stopped = False
            restarts = 0
            self.phase = "running"
            self._publish_runtime("running")
            self._runtime_heartbeat_task = asyncio.create_task(
                self._runtime_heartbeat(), name="seer-block-runtime-heartbeat"
            )
            while True:
                try:
                    await self._run_nodes(self.nodes, depth=0, loop_depth=0, trace_prefix="r")
                    break
                except _RestartProgram:
                    restarts += 1
                    self.completed_trace_ids.clear()
                    self.loop_stack.clear()
                    self.call_trace_stack.clear()
                    self.phase = "restarting"
                    self._publish_runtime("running")
                    if restarts > MAX_RESTARTS:
                        raise BlockProgramError("SEER block restart limit exceeded")
                except _StopProgram:
                    stopped = True
                    break
            self.current_trace_id = ""
            self.current_kind = ""
            self.phase = "stopped" if stopped else "finished"
            self.message = (
                (self.stop_after_current_reason + " ")
                if stopped and self.stop_after_current_reason
                else ""
            ) + (
                f"SEER block program {'stopped' if stopped else 'finished'}: "
                f"{self.executions} block/action executions, {restarts} restarts"
            )
            self._publish_runtime("stopped" if stopped else "finished")
            return _result("FINISHED", self.message)
        except asyncio.CancelledError:
            self.phase = "cancelled"
            self.message = "SEER block program cancelled"
            self._publish_runtime("cancelled")
            raise
        except Exception as exc:
            self.phase = "failed"
            self.message = str(exc)
            self._publish_runtime("failed", error=str(exc))
            raise
        finally:
            heartbeat, self._runtime_heartbeat_task = self._runtime_heartbeat_task, None
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
            self._set_active(None)
            forget = getattr(self.context.adapter, "_forget_synthetic_actions", None)
            if callable(forget):
                forget(self.generated_ids)

    async def _condition_matches(
        self, raw_condition: Any, *, trace_id: str = "", phase: str = "condition"
    ) -> bool:
        condition = _resolve(raw_condition, self.variables)
        if not isinstance(condition, Mapping):
            raise BlockProgramError("condition must be an object")
        operator = str(condition.get("operator", "=="))
        if operator not in COMPARATORS:
            raise BlockProgramError(f"unsupported condition operator: {operator}")
        source = str(condition.get("source", "")).strip().lower()
        expected = self._runtime_value(condition.get("expected", True))
        if source == "variable":
            name = str(condition.get("variable_name", "")).strip()
            if not VARIABLE_NAME.fullmatch(name):
                raise BlockProgramError("invalid condition variable name")
            if name not in self.variables:
                raise BlockProgramError(f"condition variable is not defined: {name}")
            actual = self.variables[name]
        else:
            vehicle = getattr(self.context.adapter, "_vehicle", None)
            if vehicle is None:
                raise BlockProgramError("SEER vehicle is not connected")
            actual = await _condition_value(vehicle, condition)
        matched = _compare(actual, operator, expected)
        self.condition_state = {
            "trace_id": str(trace_id or self.current_trace_id),
            "source": source,
            "variable_name": str(condition.get("variable_name", "") or ""),
            "channel": condition.get("channel"),
            "operator": operator,
            "expected": self._json_value(expected),
            "actual": self._json_value(actual),
            "result": bool(matched),
        }
        self.phase = phase
        self._publish_runtime()
        return matched

    def _runtime_value(self, value: Any) -> Any:
        value = _resolve(value, self.variables)
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("$"):
            name = text[1:]
            if not VARIABLE_NAME.fullmatch(name):
                raise BlockProgramError(f"invalid runtime variable reference: {text}")
            if name not in self.variables:
                raise BlockProgramError(f"runtime variable is not defined: {name}")
            return self.variables[name]
        lowered = text.lower()
        if lowered in {"true", "on", "yes"}:
            return True
        if lowered in {"false", "off", "no"}:
            return False
        try:
            numeric = float(text)
            if math.isfinite(numeric):
                return int(numeric) if numeric.is_integer() else numeric
        except ValueError:
            pass
        return value

    @staticmethod
    def _target_name(parameters: Mapping[str, Any], key: str = "name") -> str:
        name = str(parameters.get(key, "")).strip()
        if not VARIABLE_NAME.fullmatch(name):
            raise BlockProgramError("invalid variable name")
        return name

    def _format_message(self, value: Any) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            return str(self.variables.get(name, f"${{{name}}}"))

        return re.sub(r"\$\{([A-Za-z][A-Za-z0-9_]{0,63})\}", replace, str(value))[:1024]

    async def _run_nodes(
        self,
        nodes: Sequence[Any],
        *,
        depth: int,
        loop_depth: int,
        trace_prefix: str,
    ) -> None:
        if depth > MAX_NESTING:
            raise BlockProgramError("SEER block nesting is too deep")
        for node_index, node in enumerate(nodes, start=1):
            if self.stop_after_current:
                raise _StopProgram()
            if depth == 0 and self.start_root_index and node_index < self.start_root_index:
                continue
            if not isinstance(node, Mapping):
                raise BlockProgramError("SEER block node must be an object")
            kind = str(node.get("kind", ""))
            trace_id = f"{trace_prefix}.{node_index}"
            if kind == "define_function":
                continue
            self._enter_block(trace_id, kind)
            if kind == "repeat":
                count = int(_resolve(node.get("count", 1), self.variables))
                if not 0 <= count <= MAX_REPEAT:
                    raise BlockProgramError(f"repeat count must be 0..{MAX_REPEAT}")
                children = node.get("children", ())
                if not isinstance(children, list):
                    raise BlockProgramError("repeat children must be a list")
                loop_state = {"trace_id": trace_id, "kind": kind, "iteration": 0, "total": count}
                self.loop_stack.append(loop_state)
                try:
                    for iteration in range(count):
                        loop_state["iteration"] = iteration + 1
                        self._reset_loop_iteration_runtime(trace_id)
                        self._focus_block(trace_id, kind, "loop")
                        try:
                            await self._run_nodes(
                                children, depth=depth + 1, loop_depth=loop_depth + 1, trace_prefix=f"{trace_id}.c"
                            )
                        except _BreakLoop:
                            break
                        except _ContinueLoop:
                            continue
                finally:
                    self.loop_stack.pop()
                await self._delay(node, trace_id=trace_id, kind=kind)
                self._complete_block(trace_id, kind)
                continue
            if kind == "forever":
                children = node.get("children", ())
                if not isinstance(children, list) or not children:
                    raise BlockProgramError("forever children must be a non-empty list")
                loop_state = {"trace_id": trace_id, "kind": kind, "iteration": 0, "total": None}
                self.loop_stack.append(loop_state)
                try:
                    while True:
                        self._bump_execution()
                        loop_state["iteration"] = int(loop_state["iteration"]) + 1
                        self._reset_loop_iteration_runtime(trace_id)
                        self._focus_block(trace_id, kind, "loop")
                        try:
                            await self._run_nodes(
                                children, depth=depth + 1, loop_depth=loop_depth + 1, trace_prefix=f"{trace_id}.c"
                            )
                        except _BreakLoop:
                            break
                        except _ContinueLoop:
                            continue
                finally:
                    self.loop_stack.pop()
                await self._delay(node, trace_id=trace_id, kind=kind)
                self._complete_block(trace_id, kind)
                continue
            if kind == "repeat_until":
                maximum = int(_resolve(node.get("max_count", MAX_REPEAT), self.variables))
                if not 1 <= maximum <= MAX_REPEAT:
                    raise BlockProgramError(f"repeat-until max count must be 1..{MAX_REPEAT}")
                children = node.get("children", ())
                if not isinstance(children, list):
                    raise BlockProgramError("repeat-until children must be a list")
                loop_state = {"trace_id": trace_id, "kind": kind, "iteration": 0, "total": maximum}
                self.loop_stack.append(loop_state)
                try:
                    for iteration in range(maximum):
                        loop_state["iteration"] = iteration + 1
                        self._focus_block(trace_id, kind, "condition")
                        if await self._condition_matches(node.get("condition", {}), trace_id=trace_id):
                            break
                        self._reset_loop_iteration_runtime(trace_id)
                        try:
                            await self._run_nodes(
                                children, depth=depth + 1, loop_depth=loop_depth + 1, trace_prefix=f"{trace_id}.c"
                            )
                        except _BreakLoop:
                            break
                        except _ContinueLoop:
                            continue
                    else:
                        self._focus_block(trace_id, kind, "condition")
                        if not await self._condition_matches(node.get("condition", {}), trace_id=trace_id):
                            raise BlockProgramError(
                                f"repeat-until condition was not met within {maximum} iterations"
                            )
                finally:
                    self.loop_stack.pop()
                await self._delay(node, trace_id=trace_id, kind=kind)
                self._complete_block(trace_id, kind)
                continue
            if kind in {"if", "if_else"}:
                self._focus_block(trace_id, kind, "condition")
                matched = await self._condition_matches(node.get("condition", {}), trace_id=trace_id)
                use_else = not matched
                branch = node.get("else_children" if use_else else "children", ())
                if not isinstance(branch, list):
                    raise BlockProgramError("condition branch must be a list")
                await self._run_nodes(
                    branch, depth=depth + 1, loop_depth=loop_depth, trace_prefix=f"{trace_id}.{'e' if use_else else 'c'}"
                )
                await self._delay(node, trace_id=trace_id, kind=kind)
                self._complete_block(trace_id, kind)
                continue
            if kind == "on_di":
                parameters = _resolve(node.get("parameters", {}), self.variables)
                if not isinstance(parameters, Mapping):
                    raise BlockProgramError("DI signal parameters must be an object")
                await self._wait_for_di(parameters, trace_id=trace_id, kind=kind)
                children = node.get("children", ())
                if not isinstance(children, list):
                    raise BlockProgramError("DI signal children must be a list")
                await self._run_nodes(
                    children, depth=depth + 1, loop_depth=loop_depth, trace_prefix=f"{trace_id}.c"
                )
                await self._delay(node, trace_id=trace_id, kind=kind)
                self._complete_block(trace_id, kind)
                continue
            if kind == "call_function":
                parameters = _resolve(node.get("parameters", {}), self.variables)
                if not isinstance(parameters, Mapping):
                    raise BlockProgramError("function call parameters must be an object")
                name = str(parameters.get("function_name", "")).strip()
                function_entry = self.functions.get(name)
                if function_entry is None:
                    raise BlockProgramError(f"function is not defined: {name}")
                children, function_prefix = function_entry
                if name in self.call_stack:
                    raise BlockProgramError(f"recursive function call is not allowed: {name}")
                if len(self.call_stack) >= MAX_CALL_DEPTH:
                    raise BlockProgramError("function call nesting is too deep")
                self._bump_execution()
                self._clear_completed_prefix(function_prefix)
                self.call_stack.append(name)
                self.call_trace_stack.append(trace_id)
                try:
                    await self._run_nodes(
                        children, depth=depth + 1, loop_depth=0, trace_prefix=function_prefix
                    )
                finally:
                    self.call_trace_stack.pop()
                    self.call_stack.pop()
                await self._delay(node, trace_id=trace_id, kind=kind)
                self._complete_block(trace_id, kind)
                continue
            if kind == "wait_until":
                timeout = float(_resolve(node.get("timeout_sec", 30.0), self.variables))
                interval = float(_resolve(node.get("poll_interval_sec", 0.2), self.variables))
                if not math.isfinite(timeout) or not 0.1 <= timeout <= 3600.0:
                    raise BlockProgramError("wait-until timeout must be 0.1..3600 seconds")
                if not math.isfinite(interval) or not 0.05 <= interval <= 60.0:
                    raise BlockProgramError("wait-until interval must be 0.05..60 seconds")
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                self._focus_block(trace_id, kind, "waiting_condition")
                while not await self._condition_matches(
                    node.get("condition", {}), trace_id=trace_id, phase="waiting_condition"
                ):
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise BlockProgramError("wait-until condition timed out")
                    await asyncio.sleep(min(interval, remaining))
                await self._delay(node, trace_id=trace_id, kind=kind)
                self._complete_block(trace_id, kind)
                continue
            if kind == "break_loop":
                if loop_depth <= 0:
                    raise BlockProgramError("break-loop block must be inside a repeat block")
                self._complete_block(trace_id, kind)
                raise _BreakLoop()
            if kind == "continue_loop":
                if loop_depth <= 0:
                    raise BlockProgramError("continue-loop block must be inside a repeat block")
                self._complete_block(trace_id, kind)
                raise _ContinueLoop()
            if kind == "stop_program":
                self._complete_block(trace_id, kind)
                raise _StopProgram()
            if kind == "restart_program":
                self._bump_execution()
                self._complete_block(trace_id, kind)
                raise _RestartProgram()
            if kind in {
                "set_variable", "change_variable", "delete_variable", "log", "pulse_do",
                "wait_di", "send_do_wait_di", "switch_map", "relocate", "set_motor",
                "judge", "logic", "calculate", "read_state",
            }:
                await self._run_program_primitive(node, node_index, trace_id)
                self._complete_block(trace_id, kind)
                continue
            await self._run_action(node, node_index, trace_id)
            self._complete_block(trace_id, kind)
        if self.stop_after_current:
            raise _StopProgram()

    async def _run_program_primitive(
        self, node: Mapping[str, Any], node_index: int, trace_id: str
    ) -> None:
        self._bump_execution()
        kind = str(node.get("kind", ""))
        parameters = _resolve(node.get("parameters", {}), self.variables)
        if not isinstance(parameters, Mapping):
            raise BlockProgramError("block parameters must be an object")
        if kind == "set_variable":
            name = self._target_name(parameters)
            self.variables[name] = self._runtime_value(parameters.get("value", ""))
        elif kind == "change_variable":
            name = self._target_name(parameters)
            try:
                current = float(self.variables.get(name, 0) or 0)
                amount = float(self._runtime_value(parameters.get("amount", 0)) or 0)
            except (TypeError, ValueError) as exc:
                raise BlockProgramError(f"variable {name} must be numeric") from exc
            changed = current + amount
            if not math.isfinite(changed):
                raise BlockProgramError(f"variable {name} result is not finite")
            self.variables[name] = int(changed) if changed.is_integer() else changed
        elif kind == "delete_variable":
            self.variables.pop(self._target_name(parameters), None)
        elif kind == "log":
            message = self._format_message(parameters.get("message", ""))
            print(f"[SEER BLOCK LOG] {message}")
        elif kind == "pulse_do":
            try:
                seconds = float(parameters.get("seconds", 0.5))
            except (TypeError, ValueError) as exc:
                raise BlockProgramError("DO pulse duration must be numeric") from exc
            if not math.isfinite(seconds) or not 0.01 <= seconds <= 3600.0:
                raise BlockProgramError("DO pulse duration must be 0.01..3600 seconds")
            channel = parameters.get("id", 0)
            await self._execute_action(
                "seerSetDO",
                {"id": channel, "status": parameters.get("first_status", "on")},
                node_index,
                trace_id,
            )
            await asyncio.sleep(seconds)
            await self._execute_action(
                "seerSetDO",
                {"id": channel, "status": parameters.get("final_status", "off")},
                node_index,
                trace_id,
            )
        elif kind == "wait_di":
            await self._wait_for_di(parameters, trace_id=trace_id, kind=kind)
        elif kind == "send_do_wait_di":
            final_status = str(parameters.get("final_do_status", "off") or "off").lower()
            if final_status not in {"keep", "on", "off"}:
                raise BlockProgramError("final DO status must be keep/on/off")
            await self._execute_action(
                "seerSetDO",
                {"id": parameters.get("do_id", 0), "status": parameters.get("do_status", "on")},
                node_index,
                trace_id,
            )
            try:
                await self._wait_for_di(
                    {
                        "channel": parameters.get("di_channel", 0),
                        "mode": parameters.get("di_mode", "rising"),
                        "timeout_sec": parameters.get("timeout_sec", 30.0),
                        "poll_interval_sec": parameters.get("poll_interval_sec", 0.1),
                    },
                    trace_id=trace_id,
                    kind=kind,
                )
            finally:
                if final_status != "keep":
                    await self._execute_action(
                        "seerSetDO",
                        {"id": parameters.get("do_id", 0), "status": final_status},
                        node_index,
                        trace_id,
                    )
        elif kind == "judge":
            name = self._target_name(parameters, "result_name")
            operator = str(parameters.get("operator", "=="))
            if operator not in COMPARATORS:
                raise BlockProgramError(f"unsupported judgment operator: {operator}")
            left = self._runtime_value(parameters.get("left", 0))
            right = self._runtime_value(parameters.get("right", 0))
            matched = _compare(left, operator, right)
            self.variables[name] = int(matched)
            self.condition_state = {
                "trace_id": trace_id,
                "source": "judgment",
                "variable_name": name,
                "operator": operator,
                "actual": self._json_value(left),
                "expected": self._json_value(right),
                "result": bool(matched),
            }
        elif kind == "logic":
            name = self._target_name(parameters, "result_name")
            operator = str(parameters.get("operator", "and") or "and").lower()
            left = _as_bool(self._runtime_value(parameters.get("left", False)))
            right = _as_bool(self._runtime_value(parameters.get("right", False)))
            if operator == "and":
                result = left and right
            elif operator == "or":
                result = left or right
            elif operator == "xor":
                result = left != right
            elif operator == "not":
                result = not left
            else:
                raise BlockProgramError(f"unsupported logic operator: {operator}")
            self.variables[name] = int(result)
            self.condition_state = {
                "trace_id": trace_id,
                "source": "logic",
                "variable_name": name,
                "operator": operator,
                "actual": int(left),
                "expected": int(right),
                "result": bool(result),
            }
        elif kind == "calculate":
            name = self._target_name(parameters, "result_name")
            try:
                left = float(self._runtime_value(parameters.get("left", 0)))
                right = float(self._runtime_value(parameters.get("right", 0)))
            except (TypeError, ValueError) as exc:
                raise BlockProgramError("calculation operands must be numeric") from exc
            operator = str(parameters.get("operator", "add") or "add").lower()
            try:
                result = {
                    "add": lambda: left + right,
                    "subtract": lambda: left - right,
                    "multiply": lambda: left * right,
                    "divide": lambda: left / right,
                    "modulo": lambda: left % right,
                    "power": lambda: left**right,
                    "min": lambda: min(left, right),
                    "max": lambda: max(left, right),
                }[operator]()
            except KeyError as exc:
                raise BlockProgramError(f"unsupported calculation operator: {operator}") from exc
            except (ZeroDivisionError, OverflowError, ValueError) as exc:
                raise BlockProgramError(f"calculation failed: {exc}") from exc
            if not math.isfinite(result):
                raise BlockProgramError("calculation result is not finite")
            self.variables[name] = int(result) if result.is_integer() else result
        elif kind == "read_state":
            name = self._target_name(parameters, "result_name")
            source = str(parameters.get("source", "di") or "di").lower()
            vehicle = getattr(self.context.adapter, "_vehicle", None)
            if vehicle is None:
                raise BlockProgramError("SEER vehicle is not connected")
            self.variables[name] = await _condition_value(
                vehicle,
                {"source": source, "channel": parameters.get("channel", 0)},
            )
        else:
            vehicle = getattr(self.context.adapter, "_vehicle", None)
            if vehicle is None:
                raise BlockProgramError("SEER vehicle is not connected")
            if kind == "switch_map":
                method = getattr(vehicle, "map_switch", None)
                if not callable(method):
                    raise BlockProgramError("SEER map switching is unavailable")
                await method(str(parameters.get("map_name", "") or "").strip())
            elif kind == "relocate":
                method = getattr(vehicle, "relocation", None)
                if not callable(method):
                    raise BlockProgramError("SEER relocation is unavailable")
                mode = str(parameters.get("mode", "auto") or "auto").lower()
                if mode not in {"auto", "manual"}:
                    raise BlockProgramError("relocation mode must be auto or manual")
                try:
                    x_m = float(parameters.get("x", 0.0))
                    y_m = float(parameters.get("y", 0.0))
                    theta_deg = float(parameters.get("theta_deg", 0.0))
                except (TypeError, ValueError) as exc:
                    raise BlockProgramError("relocation pose must be numeric") from exc
                if not all(math.isfinite(value) for value in (x_m, y_m, theta_deg)):
                    raise BlockProgramError("relocation pose must be finite")
                position_from_native = getattr(vehicle, "position_from_native", float)
                angle_from_native = getattr(vehicle, "angle_from_native", float)
                await method(
                    auto=mode == "auto",
                    x=position_from_native(x_m),
                    y=position_from_native(y_m),
                    angle=angle_from_native(math.radians(theta_deg)),
                )
            elif kind == "set_motor":
                target = str(parameters.get("target", "all") or "all").lower()
                status = str(parameters.get("status", "on") or "on").lower()
                if target not in {"all", "named"} or status not in {"on", "off"}:
                    raise BlockProgramError("invalid motor target or status")
                enabled = status == "on"
                if enabled and await _condition_value(
                    vehicle, {"source": "emergency"}
                ):
                    raise BlockProgramError("cannot enable motors during emergency stop")
                if target == "all":
                    method = getattr(vehicle, "enable_motor" if enabled else "disable_motor", None)
                    if not callable(method):
                        raise BlockProgramError("SEER all-motor control is unavailable")
                    await method()
                else:
                    name = str(parameters.get("motor_name", "") or "").strip()
                    method = getattr(vehicle, "set_motor", None)
                    if not name or not callable(method):
                        raise BlockProgramError("named motor control requires a motor name")
                    await method(name, enabled)
            else:
                raise BlockProgramError(f"unsupported program primitive: {kind}")
        self._publish_runtime()
        await self._delay(node, trace_id=trace_id, kind=kind)

    async def _wait_for_di(
        self, parameters: Mapping[str, Any], *, trace_id: str = "", kind: str = "wait_di"
    ) -> None:
        vehicle = getattr(self.context.adapter, "_vehicle", None)
        if vehicle is None:
            raise BlockProgramError("SEER vehicle is not connected")
        try:
            channel = int(parameters.get("channel", 0))
            timeout = float(parameters.get("timeout_sec", 30.0))
            interval = float(parameters.get("poll_interval_sec", 0.1))
        except (TypeError, ValueError) as exc:
            raise BlockProgramError("DI wait parameters must be numeric") from exc
        mode = str(parameters.get("mode", "on") or "on").lower()
        if mode not in {"on", "off", "rising", "falling"}:
            raise BlockProgramError("DI wait mode must be on/off/rising/falling")
        if not 0 <= channel <= 65535:
            raise BlockProgramError("DI channel is out of range")
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 3600.0:
            raise BlockProgramError("DI wait timeout must be 0.1..3600 seconds")
        if not math.isfinite(interval) or not 0.05 <= interval <= 60.0:
            raise BlockProgramError("DI wait interval must be 0.05..60 seconds")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        previous = await _read_di(vehicle, channel)
        self.condition_state = {
            "trace_id": str(trace_id), "source": "di", "channel": channel,
            "operator": "==", "expected": mode, "actual": bool(previous), "result": False,
        }
        if trace_id:
            self._focus_block(trace_id, kind, "waiting_di")
        if mode == "on" and previous:
            self.condition_state["result"] = True
            self._publish_runtime()
            return
        if mode == "off" and not previous:
            self.condition_state["result"] = True
            self._publish_runtime()
            return
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise BlockProgramError(f"DI{channel} {mode} signal timed out")
            await asyncio.sleep(min(interval, remaining))
            current = await _read_di(vehicle, channel)
            self.condition_state = {
                "trace_id": str(trace_id), "source": "di", "channel": channel,
                "operator": "==", "expected": mode, "actual": bool(current), "result": False,
            }
            self.phase = "waiting_di"
            self._publish_runtime()
            if mode == "on" and current:
                self.condition_state["result"] = True
                self._publish_runtime()
                return
            if mode == "off" and not current:
                self.condition_state["result"] = True
                self._publish_runtime()
                return
            if mode == "rising" and not previous and current:
                self.condition_state["result"] = True
                self._publish_runtime()
                return
            if mode == "falling" and previous and not current:
                self.condition_state["result"] = True
                self._publish_runtime()
                return
            previous = current

    async def _run_action(
        self, node: Mapping[str, Any], node_index: int, trace_id: str
    ) -> None:
        action_type = str(node.get("action_type", ""))
        if action_type not in ALLOWED_ACTIONS:
            raise BlockProgramError(f"unsupported SEER block action: {action_type}")
        parameters = _resolve(node.get("parameters", {}), self.variables)
        if not isinstance(parameters, Mapping):
            raise BlockProgramError("SEER block parameters must be an object")
        await self._execute_action(action_type, parameters, node_index, trace_id)
        await self._delay(node, trace_id=trace_id, kind=str(node.get("kind", "")))

    async def _execute_action(
        self, action_type: str, parameters: Mapping[str, Any], node_index: int, trace_id: str = ""
    ) -> None:
        if action_type not in ALLOWED_ACTIONS:
            raise BlockProgramError(f"unsupported SEER block action: {action_type}")
        self._bump_execution()

        action_id = f"{self.context.action.action_id}:block{self.executions}"

        child = SimpleNamespace(
            action_id=action_id,
            action_type=action_type,
            _owner_order_id=getattr(self.context.action, "_owner_order_id", None),
            action_parameters=[
                SimpleNamespace(key=key, value=value) for key, value in parameters.items()
            ],
        )
        self.generated_ids.add(action_id)
        if trace_id:
            self._focus_block(trace_id, self.current_kind or action_type, "action")
        self._set_active(action_type)
        try:
            result = await self.context.adapter._action_registry.execute(
                child, self.context.adapter
            )
        finally:
            self._set_active(None)
        status_value = getattr(result.status, "value", str(result.status))
        if str(status_value).upper() != "FINISHED":
            raise BlockProgramError(
                f"block {node_index} ({action_type}) failed: "
                f"{result.description or status_value}"
            )

    def _bump_execution(self) -> None:
        self.executions += 1
        if self.executions > MAX_EXECUTIONS:
            raise BlockProgramError("SEER block execution limit exceeded")

    async def _delay(
        self, node: Mapping[str, Any], *, trace_id: str = "", kind: str = ""
    ) -> None:
        value = _resolve(node.get("delay_sec", 0.0), self.variables)
        delay = float(value or 0.0)
        if not math.isfinite(delay) or not 0.0 <= delay <= 3600.0:
            raise BlockProgramError("block delay must be 0..3600 seconds")
        if delay:
            if trace_id:
                self._focus_block(trace_id, kind or self.current_kind, "delay")
            await asyncio.sleep(delay)


def make_program_handler(encoded: Optional[str] = None, recipe_name: str = ""):
    """Build an Adapter inline handler for a fixed or parameterized program."""

    async def handle(context: Any) -> Any:
        program_b64 = encoded or str(context.params.get("program_b64", "") or "")
        start_trace_id = str(context.params.get("_seer_start_trace_id", "") or "").strip()
        try:
            nodes = decode_program(program_b64)
            return await BlockProgramExecutor(
                context, nodes, recipe_name=recipe_name, start_trace_id=start_trace_id
            ).run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _result("FAILED", str(exc))

    return handle
