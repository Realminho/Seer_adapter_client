"""localize target=pose with a `node` parameter takes its pose from the map.

The localization recipes must not carry hardcoded mm coordinates that go stale
when the map is re-surveyed. Naming a map node makes the anchor absolute and
map-sourced: the handler resolves (x, y, theta) from the robot's own map.
"""

import asyncio
from types import SimpleNamespace

from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from tests._stop_reason_harness import make_adapter, start_state


def _run_localize(params, *, map_nodes=None, path_points=None, cur_x=1.5, cur_y=2.5):
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._x = cur_x
        adapter._vehicle._y = cur_y
        adapter._vehicle._map_nodes = dict(map_nodes or {})
        adapter._vehicle._map_path_points = dict(path_points or {})
        adapter._vehicle.localize_calls = []
        statuses = {}
        original = adapter._update_instant_action_status

        def capture(action_id, status, *args, **kw):
            # Keep the description too: a FAILED assertion is worthless if the
            # handler failed for an unrelated reason (e.g. "requires theta").
            description = kw.get("result_description")
            if description is None and args:
                description = args[0]
            statuses[action_id] = (status, description or "")
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


# The elevator car node on this site's map, with the Goal heading it carries.
ELEVATOR_MAP = {"2_01": (16377.0, 1666.0, 0.0), "3_01": (16350.0, 3223.0, -90.0)}


def test_localize_node_uses_the_map_node_pose():
    adapter, statuses = _run_localize({"node": "3_01"}, map_nodes=ELEVATOR_MAP)

    assert adapter._vehicle.localize_calls == [
        ("pose", None, 16350.0, 3223.0, -90.0)
    ]
    assert statuses["loc-1"][0] is ActionStatus.FINISHED


def test_localize_unknown_node_fails_and_does_not_localize():
    adapter, statuses = _run_localize({"node": "nope"}, map_nodes=ELEVATOR_MAP)

    status, description = statuses["loc-1"]
    assert adapter._vehicle.localize_calls == []
    assert status is ActionStatus.FAILED
    assert "nope" in description


def test_localize_path_point_without_an_explicit_theta_is_rejected():
    # Every PathPoint on this site's map carries theta 0.00, so a PathPoint may
    # never supply a heading — a silent 0 would be a lie. Same rule as
    # _send_node_goto (adapter_jibot.py:4964).
    adapter, statuses = _run_localize(
        {"node": "p40"},
        map_nodes=ELEVATOR_MAP,
        path_points={"p40": (13793.0, -1324.0, 0.0)},
    )

    status, description = statuses["loc-1"]
    assert adapter._vehicle.localize_calls == []
    assert status is ActionStatus.FAILED
    assert "p40" in description


def test_localize_node_with_target_goal_is_rejected():
    # goal= and node= name different lookup tables. Silently dropping one would
    # re-anchor the robot somewhere the operator did not ask for.
    adapter, statuses = _run_localize(
        {"target": "goal", "goal": "3_01", "node": "2_01"}, map_nodes=ELEVATOR_MAP
    )

    status, description = statuses["loc-1"]
    assert adapter._vehicle.localize_calls == []
    assert status is ActionStatus.FAILED
    assert "node" in description


def test_localize_node_theta_is_overridden_by_an_explicit_theta():
    # The elevator car node carries theta 0.00 on the map, but the true heading
    # depends on the direction of travel (up = +90, down = -90). The node fixes
    # the position; the recipe still states the heading.
    adapter, statuses = _run_localize(
        {"node": "2_01", "theta": -90}, map_nodes=ELEVATOR_MAP
    )

    assert adapter._vehicle.localize_calls == [
        ("pose", None, 16377.0, 1666.0, -90.0)
    ]
    assert statuses["loc-1"][0] is ActionStatus.FINISHED


def test_localize_node_position_is_overridden_by_explicit_xy():
    adapter, statuses = _run_localize(
        {"node": "2_01", "x": 5, "y": 6, "theta": 15}, map_nodes=ELEVATOR_MAP
    )

    assert adapter._vehicle.localize_calls == [("pose", None, 5.0, 6.0, 15.0)]
    assert statuses["loc-1"][0] is ActionStatus.FINISHED


def test_localize_node_ignores_the_stale_current_position():
    # The whole reason for `node`: the current pose is only an ESTIMATE, and after
    # an elevator ride it is exactly what cannot be trusted.
    adapter, statuses = _run_localize(
        {"node": "2_01"}, map_nodes=ELEVATOR_MAP, cur_x=999.0, cur_y=888.0
    )

    assert adapter._vehicle.localize_calls == [
        ("pose", None, 16377.0, 1666.0, 0.0)
    ]
    assert statuses["loc-1"][0] is ActionStatus.FINISHED


def test_localize_node_through_the_recipe_registry_path():
    # The real composition: registry.execute -> run_localize -> _run_status_action
    # -> _handle_localize_instant_action -> terminal status resolves the future.
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = dict(ELEVATOR_MAP)
        adapter._vehicle.localize_calls = []
        task = await start_state(adapter)
        try:
            action = SimpleNamespace(
                action_id="rec:step1:try1",
                action_type="localize",
                action_parameters=[
                    SimpleNamespace(key="node", value="2_01"),
                    SimpleNamespace(key="theta", value=-90),
                ],
            )
            result = await adapter._action_registry.execute(action, adapter)
            return adapter, result
        finally:
            task.cancel()

    adapter, result = asyncio.run(scenario())

    assert result.status is ActionStatus.FINISHED
    assert adapter._vehicle.localize_calls == [
        ("pose", None, 16377.0, 1666.0, -90.0)
    ]


def test_localize_path_point_supplies_position_when_theta_is_explicit():
    # The FMS driving graph is PathPoint-based (nearest_node_mode defaults to
    # "pathPoint"), so p2 — not the co-located Goal 2_01 — is the id it knows.
    # A PathPoint may anchor the POSITION; the heading still has to be stated.
    adapter, statuses = _run_localize(
        {"node": "p2", "theta": -90},
        map_nodes=ELEVATOR_MAP,
        path_points={"p2": (16377.0, 1666.0, 0.0)},
    )

    assert adapter._vehicle.localize_calls == [
        ("pose", None, 16377.0, 1666.0, -90.0)
    ]
    assert statuses["loc-1"][0] is ActionStatus.FINISHED
