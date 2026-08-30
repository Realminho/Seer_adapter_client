"""localize target=pose without x/y re-anchors at the robot's current position.

The localization recipes ship only a theta and let the handler fill x/y from the
live pose, so "재위치 at where I am now, only fix the heading" needs no hardcoded
coordinates that would go stale.
"""

import asyncio
from types import SimpleNamespace

from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from tests._stop_reason_harness import make_adapter, start_state


def _run_localize(params, *, cur_x=1.5, cur_y=2.5):
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._x = cur_x
        adapter._vehicle._y = cur_y
        adapter._vehicle.localize_calls = []
        statuses = {}
        original = adapter._update_instant_action_status

        def capture(action_id, status, *args, **kw):
            statuses[action_id] = status
            return original(action_id, status, *args, **kw)

        adapter._update_instant_action_status = capture
        task = await start_state(adapter)
        try:
            action = SimpleNamespace(
                action_id="loc-1",
                action_type="localize",
                action_parameters=[
                    SimpleNamespace(key=k, value=v) for k, v in params.items()
                ],
            )
            adapter._handle_localize_instant_action(action)
            await asyncio.sleep(0.1)
            return adapter, statuses
        finally:
            task.cancel()

    return asyncio.run(scenario())


def test_localize_pose_without_xy_uses_current_vehicle_position():
    adapter, statuses = _run_localize({"target": "pose", "theta": -90})

    assert adapter._vehicle.localize_calls == [("pose", None, 1.5, 2.5, -90.0)]
    assert statuses["loc-1"] is ActionStatus.FINISHED


def test_localize_pose_with_explicit_xy_is_unchanged():
    adapter, statuses = _run_localize({"target": "pose", "x": 5, "y": 6, "theta": 0})

    assert adapter._vehicle.localize_calls == [("pose", None, 5.0, 6.0, 0.0)]
    assert statuses["loc-1"] is ActionStatus.FINISHED


def test_localize_pose_without_theta_fails_and_does_not_localize():
    adapter, statuses = _run_localize({"target": "pose"})

    assert adapter._vehicle.localize_calls == []
    assert statuses["loc-1"] is ActionStatus.FAILED


def test_localize_pose_explicit_zero_targets_origin_not_current():
    # x=0,y=0 is a real destination (map origin), not "unspecified" — it must not
    # be swallowed into the current-pose fallback.
    adapter, statuses = _run_localize({"target": "pose", "x": 0, "y": 0, "theta": 15})

    assert adapter._vehicle.localize_calls == [("pose", None, 0.0, 0.0, 15.0)]
    assert statuses["loc-1"] is ActionStatus.FINISHED


def test_localize_recipe_path_through_registry_uses_current_pose():
    # The real composition: registry.execute -> run_localize -> _run_status_action
    # (completion future) -> real _handle_localize_instant_action (RUNNING ->
    # _run_on_adapter_loop -> terminal status) -> future resolves. Proves the
    # feature works, not just that its parts do.
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._x = 1.5
        adapter._vehicle._y = 2.5
        adapter._vehicle.localize_calls = []
        task = await start_state(adapter)
        try:
            action = SimpleNamespace(
                action_id="rec:step1:try1",
                action_type="localize",
                action_parameters=[
                    SimpleNamespace(key="target", value="pose"),
                    SimpleNamespace(key="theta", value=-90),
                ],
            )
            result = await adapter._action_registry.execute(action, adapter)
            return adapter, result
        finally:
            task.cancel()

    adapter, result = asyncio.run(scenario())

    assert result.status is ActionStatus.FINISHED
    assert adapter._vehicle.localize_calls == [("pose", None, 1.5, 2.5, -90.0)]
