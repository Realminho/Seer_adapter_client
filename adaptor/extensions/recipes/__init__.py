"""단계를 직렬 실행하고 실패 시 중단하되 cleanup은 제한 시간 안에 항상 실행한다."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from config.hcl import PARAM_PATTERN
from core.action_registry import ActionResult, ActionSpec, InlineActionContext
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus

MODULE_TITLE = "Recipes"


def _resolve(value: Any, parent: Mapping[str, Any]) -> Any:
    """recipe 값 안의 `$파라미터`를 부모 action 파라미터로 재귀 치환한다."""
    if isinstance(value, str):
        match = PARAM_PATTERN.fullmatch(value)
        if match:
            name = match.group(1)
            if name not in parent:
                raise ValueError(f"missing recipe parameter: {name}")
            return parent[name]

        def replace(match):
            """문자열 일부에 포함된 placeholder를 문자열 값으로 치환한다."""

            name = match.group(1)
            if name not in parent:
                raise ValueError(f"missing recipe parameter: {name}")
            return str(parent[name])

        return PARAM_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_resolve(item, parent) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, parent) for key, item in value.items()}
    return value


def _child(parent: Any, section: str, number: int, attempt: int, step: Any, params):
    """각 recipe 단계 실행에 사용할 고유한 합성 자식 action을 만든다."""
    return SimpleNamespace(
        action_id=f"{parent.action_id}:{section}{number}:try{attempt}",
        action_type=step.extension,
        _owner_order_id=getattr(parent, "_owner_order_id", None),
        action_parameters=[
            SimpleNamespace(key=key, value=value) for key, value in params.items()
        ],
    )


async def _hold(delay: float, deadline: float) -> None:
    """남은 예산 안에서만 쉰다. deadline이 지났으면 아예 쉬지 않는다."""
    if delay <= 0:
        return
    if deadline:
        delay = min(delay, max(0.0, deadline - time.monotonic()))
    if delay > 0:
        await asyncio.sleep(delay)


def _set_active_step(adapter: Any, parent_action_id: str, action_type: str | None) -> None:
    """Expose a synthetic recipe child without adding a VDA5050 action state."""
    setter = getattr(adapter, "_set_active_action_step", None)
    if callable(setter):
        setter(parent_action_id, action_type)
        return

    active = getattr(adapter, "_active_action_steps", None)
    if active is None:
        active = {}
        setattr(adapter, "_active_action_steps", active)
    if action_type:
        active[parent_action_id] = action_type
    else:
        active.pop(parent_action_id, None)
    publish = getattr(adapter, "request_state_publish", None)
    if callable(publish):
        publish(
            f"action step {action_type} started"
            if action_type
            else "action step terminal"
        )


async def _run_step(
    ctx, step, section, number, deadline, default_timeout, generated_ids
):
    """한 단계를 timeout·retry 정책에 따라 실행하고 마지막 결과를 반환한다."""
    last = None
    for attempt in range(1, step.retry + 2):
        remaining = max(0.0, deadline - time.monotonic()) if deadline else 0.0
        timeout = (
            step.timeout_sec
            or default_timeout
            or ctx.adapter._action_registry.timeout_sec(step.extension)
        )
        if deadline:
            timeout = min(timeout, remaining) if timeout else remaining
        if deadline and remaining <= 0:
            return ActionResult(ActionStatus.FAILED, "recipe timeout")
        params = _resolve(dict(step.parameters), ctx.params)
        action = _child(ctx.action, section, number, attempt, step, params)
        generated_ids.add(action.action_id)
        _set_active_step(ctx.adapter, ctx.action.action_id, step.extension)
        try:
            last = await ctx.adapter._action_registry.execute(
                action,
                ctx.adapter,
                timeout_sec=timeout if timeout > 0 else None,
            )
        finally:
            _set_active_step(ctx.adapter, ctx.action.action_id, None)
        if last.status == ActionStatus.FINISHED:
            # 성공한 단계 뒤에만 쉰다. 실패하면 recipe가 멈추므로 cleanup을
            # 늦추면서까지 기다릴 이유가 없다.
            await _hold(step.delay_sec, deadline)
            return last
        if attempt <= step.retry:
            await _hold(step.retry_delay_sec, deadline)
    return last


def _handler(recipe):
    """recipe 설정을 캡처한 action handler를 생성한다."""

    async def handle(ctx: InlineActionContext) -> ActionResult:
        """본문을 순서대로 실행하고 성공 여부와 무관하게 cleanup을 수행한다."""
        deadline = time.monotonic() + recipe.timeout_sec if recipe.timeout_sec else 0.0
        body_error = None
        cancellation = None
        cleanup_errors = []
        generated_ids = set()
        try:
            for number, step in enumerate(recipe.steps, 1):
                result = await _run_step(
                    ctx, step, "step", number, deadline, 0.0, generated_ids
                )
                if result.status != ActionStatus.FINISHED:
                    body_error = (
                        f"{recipe.action_type} failed at step {number} "
                        f"({step.extension}): "
                        f"{result.description or result.status.value}"
                    )
                    break
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception as exc:
            body_error = f"{recipe.action_type} failed: {exc}"
        finally:
            # 부모 task가 취소됐더라도 cleanup 각각은 자체 제한 시간 동안 보호해 실행한다.
            for number, step in enumerate(recipe.cleanup, 1):
                timeout = step.timeout_sec or recipe.cleanup_timeout_sec
                cleanup_deadline = time.monotonic() + timeout if timeout else 0.0
                try:
                    result = await asyncio.shield(
                        _run_step(
                            ctx, step, "cleanup", number, cleanup_deadline, timeout
                            , generated_ids
                        )
                    )
                except Exception as exc:
                    result = ActionResult(ActionStatus.FAILED, str(exc))
                if result.status != ActionStatus.FINISHED:
                    cleanup_errors.append(
                        f"cleanup {step.extension} failed: "
                        f"{result.description or result.status.value}"
                    )

        try:
            if cancellation is not None:
                raise cancellation

            if body_error:
                detail = body_error
                if cleanup_errors:
                    detail += "; " + "; ".join(cleanup_errors)
                return ActionResult(ActionStatus.FAILED, detail)
            if cleanup_errors:
                return ActionResult(
                    ActionStatus.FAILED,
                    f"{recipe.action_type} body ok but " + "; ".join(cleanup_errors),
                )
            return ActionResult(
                ActionStatus.FINISHED,
                f"{recipe.action_type} finished: {len(recipe.steps)} steps",
            )
        finally:
            forget = getattr(ctx.adapter, "_forget_synthetic_actions", None)
            if callable(forget):
                forget(generated_ids)

    return handle


def action_specs(
    recipes: Iterable[Any],
    extension_specs=(),
    configured_action_types=(),
) -> tuple[ActionSpec, ...]:
    """recipe 참조와 이름 충돌을 검증한 뒤 활성 action 명세를 만든다."""
    recipes = tuple(recipes)
    enabled = tuple(recipe for recipe in recipes if recipe.enabled)
    recipe_names = {recipe.action_type for recipe in recipes}
    available = {spec.action_type for spec in extension_specs if spec.enabled}
    available.update(configured_action_types)
    for recipe in recipes:
        if recipe.action_type in available:
            raise ValueError(
                f"recipe '{recipe.action_type}' conflicts with registered action"
            )
        for step in (*recipe.steps, *recipe.cleanup):
            if step.extension in recipe_names:
                raise ValueError(
                    f"recipe '{recipe.action_type}' cannot reference recipe "
                    f"'{step.extension}'"
                )
            if step.extension not in available:
                raise ValueError(
                    f"recipe '{recipe.action_type}' references unavailable "
                    f"extension '{step.extension}'"
                )
    return tuple(
        ActionSpec(
            action_type=recipe.action_type,
            handler=_handler(recipe),
            timeout_sec=0.0,
            motion=recipe.motion,
            label=recipe.label or recipe.action_type,
        )
        for recipe in enabled
    )
