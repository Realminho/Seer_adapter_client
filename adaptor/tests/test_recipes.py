import asyncio
from types import SimpleNamespace

from config.config import RecipeConfig, RecipeStep
from core.action_registry import ActionRegistry, ActionResult, ActionSpec
from extensions.recipes import action_specs
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


def _action(kind="recipe", **params):
    return SimpleNamespace(
        action_id="parent",
        action_type=kind,
        action_parameters=[SimpleNamespace(key=k, value=v) for k, v in params.items()],
    )


def _registry(recipe, handlers):
    extensions = tuple(ActionSpec(name, handler=fn) for name, fn in handlers.items())
    registry = ActionRegistry((*extensions, *action_specs([recipe], extensions)))
    return registry, SimpleNamespace(_action_registry=registry)


def _run_timed(registry, adapter, kind):
    """recipe를 돌리고 걸린 시간을 함께 돌려준다(delay 예산 검증용)."""

    async def run():
        loop = asyncio.get_running_loop()
        at = loop.time()
        result = await registry.execute(_action(kind), adapter)
        return result, loop.time() - at

    return asyncio.run(run())


def test_recipe_substitution_sequence_and_cleanup():
    calls = []

    async def record(ctx):
        calls.append((ctx.action.action_type, ctx.params))
        return ActionResult(ActionStatus.FINISHED)

    recipe = RecipeConfig(
        "airShowerEnter",
        steps=[RecipeStep("write", {"index": "${var.pin}", "tag": "door-${var.pin}"})],
        cleanup=[RecipeStep("disconnect")],
    )
    registry, adapter = _registry(recipe, {"write": record, "disconnect": record})
    result = asyncio.run(registry.execute(_action("airShowerEnter", pin=3), adapter))
    assert result.status == ActionStatus.FINISHED
    assert calls == [("write", {"index": 3, "tag": "door-3"}), ("disconnect", {})]


def test_recipe_exposes_current_step_until_cleanup_finishes():
    observed = []

    async def observe(ctx):
        observed.append(dict(ctx.adapter._active_action_steps))
        return ActionResult(ActionStatus.FINISHED)

    recipe = RecipeConfig(
        "trip",
        steps=[RecipeStep("body")],
        cleanup=[RecipeStep("cleanup")],
    )
    registry, adapter = _registry(recipe, {"body": observe, "cleanup": observe})

    result = asyncio.run(registry.execute(_action("trip"), adapter))

    assert result.status == ActionStatus.FINISHED
    assert observed == [
        {"parent": "body"},
        {"parent": "cleanup"},
    ]
    assert adapter._active_action_steps == {}


def test_cleanup_runs_after_body_failure_and_does_not_replace_primary_error():
    async def fail_body(_ctx):
        return ActionResult(ActionStatus.FAILED, "body detail")

    async def fail_cleanup(_ctx):
        return ActionResult(ActionStatus.FAILED, "cleanup detail")

    recipe = RecipeConfig(
        "trip",
        steps=[RecipeStep("body")],
        cleanup=[RecipeStep("cleanup")],
    )
    registry, adapter = _registry(
        recipe, {"body": fail_body, "cleanup": fail_cleanup}
    )
    result = asyncio.run(registry.execute(_action("trip"), adapter))
    assert result.status == ActionStatus.FAILED
    assert "body detail" in result.description
    assert "cleanup cleanup failed: cleanup detail" in result.description


def test_retry_uses_unique_attempt_ids():
    ids = []

    async def flaky(ctx):
        ids.append(ctx.action.action_id)
        status = ActionStatus.FINISHED if len(ids) == 2 else ActionStatus.FAILED
        return ActionResult(status, "retry")

    recipe = RecipeConfig("trip", steps=[RecipeStep("flaky", retry=1)])
    registry, adapter = _registry(recipe, {"flaky": flaky})
    result = asyncio.run(registry.execute(_action("trip"), adapter))
    assert result.status == ActionStatus.FINISHED
    assert ids == ["parent:step1:try1", "parent:step1:try2"]


def test_body_timeout_still_runs_cleanup_with_separate_budget():
    cleaned = []

    async def slow(_ctx):
        await asyncio.sleep(1)
        return ActionResult(ActionStatus.FINISHED)

    async def cleanup(_ctx):
        cleaned.append(True)
        return ActionResult(ActionStatus.FINISHED)

    recipe = RecipeConfig(
        "trip",
        steps=[RecipeStep("slow")],
        cleanup=[RecipeStep("cleanup")],
        timeout_sec=0.01,
        cleanup_timeout_sec=0.1,
    )
    registry, adapter = _registry(recipe, {"slow": slow, "cleanup": cleanup})
    result = asyncio.run(registry.execute(_action("trip"), adapter))
    assert result.status == ActionStatus.FAILED
    assert "timeout" in result.description
    assert cleaned == [True]


def test_step_delay_sec_waits_before_the_next_step():
    """momentary 출력을 켠 뒤 끄기까지 pulse 폭을 보장한다.

    delay가 없으면 on -> off 사이 간격은 통신 왕복 시간이 전부다. 설비가 그보다
    긴 유지 시간을 요구하면 버튼을 눌렀다고 인식하지 못한다.
    """
    stamps = []

    async def record(ctx):
        stamps.append(asyncio.get_running_loop().time())
        return ActionResult(ActionStatus.FINISHED)

    recipe = RecipeConfig(
        "pulse",
        steps=[RecipeStep("on", delay_sec=0.05), RecipeStep("off")],
    )
    registry, adapter = _registry(recipe, {"on": record, "off": record})
    result = asyncio.run(registry.execute(_action("pulse"), adapter))
    assert result.status == ActionStatus.FINISHED
    assert stamps[1] - stamps[0] >= 0.05


def test_step_delay_sec_is_skipped_when_the_step_fails():
    """실패하면 recipe는 멈춘다. cleanup을 늦추면서까지 쉴 이유가 없다."""
    stamps = []

    async def fail(_ctx):
        stamps.append(asyncio.get_running_loop().time())
        return ActionResult(ActionStatus.FAILED, "body detail")

    async def cleanup(_ctx):
        stamps.append(asyncio.get_running_loop().time())
        return ActionResult(ActionStatus.FINISHED)

    recipe = RecipeConfig(
        "pulse",
        steps=[RecipeStep("body", delay_sec=5)],
        cleanup=[RecipeStep("cleanup")],
    )
    registry, adapter = _registry(recipe, {"body": fail, "cleanup": cleanup})
    result = asyncio.run(registry.execute(_action("pulse"), adapter))
    assert result.status == ActionStatus.FAILED
    assert stamps[1] - stamps[0] < 1


def test_step_delay_sec_cannot_outlive_the_recipe_budget():
    """delay도 본문 예산 안에서만 쉰다. 넘치면 그 자리에서 timeout이다."""
    calls = []

    async def record(ctx):
        calls.append(ctx.action.action_type)
        return ActionResult(ActionStatus.FINISHED)

    recipe = RecipeConfig(
        "pulse",
        steps=[RecipeStep("on", delay_sec=5), RecipeStep("off")],
        timeout_sec=0.05,
    )
    registry, adapter = _registry(recipe, {"on": record, "off": record})
    result, elapsed = _run_timed(registry, adapter, "pulse")
    assert result.status == ActionStatus.FAILED
    assert "timeout" in result.description
    assert calls == ["on"]
    assert elapsed < 1


def test_cleanup_delay_sec_cannot_outlive_the_cleanup_budget():
    """cleanup도 pulse를 낼 수 있어야 하지만 자기 예산을 넘겨선 안 된다."""

    async def cleanup(_ctx):
        return ActionResult(ActionStatus.FINISHED)

    recipe = RecipeConfig(
        "pulse",
        steps=[RecipeStep("body")],
        cleanup=[RecipeStep("cleanup", delay_sec=5)],
        cleanup_timeout_sec=0.05,
    )
    registry, adapter = _registry(recipe, {"body": cleanup, "cleanup": cleanup})
    result, elapsed = _run_timed(registry, adapter, "pulse")
    assert result.status == ActionStatus.FINISHED
    assert elapsed < 1


def test_recipe_spec_has_no_registry_timeout_and_nested_reference_fails():
    recipe = RecipeConfig("trip", steps=[RecipeStep("body")], timeout_sec=1)
    extension = ActionSpec("body", handler=lambda _: ActionResult(ActionStatus.FINISHED))
    assert action_specs([recipe], [extension])[0].timeout_sec == 0
    nested = RecipeConfig("outer", steps=[RecipeStep("trip")])
    try:
        action_specs([recipe, nested], [extension])
    except ValueError as exc:
        assert "cannot reference recipe" in str(exc)
    else:
        raise AssertionError("nested recipe was accepted")
