import asyncio
import math

from protocol.vda5050_common import ErrorLevel
from protocol.vda5050_3_0.messages import InstantActions
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from tests._stop_reason_harness import make_adapter as _make_adapter, start_state


def make_adapter():
    adapter = _make_adapter()
    adapter.config.settings.nearest_node_mode = "headingGoal"
    # These tests exercise gotoNearestNode action completion, not the separate
    # missing-lastNode fallback policy.
    adapter.config.settings.use_nearest_node_as_last_node_when_missing = False
    # Nor the pose-based capture policy. config.toml ships "proximity", which
    # mirrors the nearest map node into lastNodeId on every pose update at any
    # distance -- so without this pin an assertion about lastNodeId here is
    # really an assertion about that policy, and the handler could stop writing
    # lastNodeId entirely without a single test noticing. Pinned off, a
    # lastNodeId in these tests can only have come from the handler.
    adapter.config.settings.last_node_capture_mode = "disabled"
    return adapter


def _goto_nearest_ia(action_id):
    return InstantActions.from_dict({
        "headerId": 1,
        "timestamp": "2026-06-26T00:00:00.000Z",
        "version": "3.0.0",
        "manufacturer": "jibot",
        "serialNumber": "HN-TEST-001",
        "actions": [{
            "actionType": "gotoNearestNode",
            "actionId": action_id,
            "blockingType": "NONE",
            "actionParameters": [],
        }],
    })


def _capture_statuses(adapter):
    """Record every instant-action status as it is set.

    Terminal statuses are cleared from state.instant_action_states the moment
    they are set, so reading that list after the fact misses them. Wrapping
    _update_instant_action_status captures the status at set-time (mirrors
    test_disable_motor_action.py).
    """
    statuses = []
    orig = adapter._update_instant_action_status

    def capture(action_id, status, **kw):
        statuses.append(status)
        return orig(action_id, status, **kw)

    adapter._update_instant_action_status = capture
    return statuses


def test_goto_then_arrival_sets_last_node():
    async def scenario():
        adapter = make_adapter()
        adapter.config.settings.node_position_poll_interval_sec = 0.01
        adapter.config.settings.goto_nearest_timeout_sec = 2.0
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            issued = list(adapter._vehicle.goto_targets)
            adapter._vehicle.arrive_at(1000.0, 0.0)
            await asyncio.sleep(0.1)
            return issued, adapter.state.last_node_id, statuses
        finally:
            task.cancel()
    issued, last_node, statuses = asyncio.run(scenario())
    assert issued == ["N3"]
    assert last_node == "N3"
    assert ActionStatus.RUNNING in statuses
    assert ActionStatus.FINISHED in statuses
    assert statuses.index(ActionStatus.RUNNING) < statuses.index(ActionStatus.FINISHED)


def test_already_at_node_skips_motion():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {"N3": (50.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return list(adapter._vehicle.goto_targets), adapter.state.last_node_id, statuses
        finally:
            task.cancel()
    issued, last_node, statuses = asyncio.run(scenario())
    assert issued == []
    assert last_node == "N3"
    assert ActionStatus.FINISHED in statuses


def test_path_point_mode_selects_path_point_and_uses_pose_motion():
    async def scenario():
        adapter = make_adapter()
        adapter.config.settings.nearest_node_mode = "pathPoint"
        adapter.config.settings.node_position_poll_interval_sec = 0.01
        adapter.config.settings.goto_nearest_timeout_sec = 2.0
        adapter._vehicle._map_nodes = {"G1": (10.0, 0.0, 0.0)}
        adapter._vehicle._map_path_points = {"p40": (1000.0, 50.0, 90.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g-path"))
            await asyncio.sleep(0.05)
            issued = list(adapter._vehicle.goto_targets)
            adapter._vehicle.arrive_at(1000.0, 50.0)
            await asyncio.sleep(0.1)
            return (
                issued,
                adapter.state.last_node_id,
                adapter._nearest_node_id,
                statuses,
            )
        finally:
            task.cancel()

    issued, last_node, nearest_node, statuses = asyncio.run(scenario())
    # The PathPoint's map heading (90.0) is deliberately not sent — every
    # PathPoint on the real map carries 0.00, so passing it as poseTh turned the
    # robot on arrival. poseTh cannot be dropped either (urobot silently ignores
    # a pose goto without it), so the bearing from (0,0) to the node goes out and
    # the robot stops facing the way it drove. Adapter._node_goto_theta_deg.
    assert len(issued) == 1
    gx, gy, gtheta = issued[0]
    assert (gx, gy) == (1000.0, 50.0)
    assert abs(gtheta - math.degrees(math.atan2(50.0, 1000.0))) < 1e-9
    assert last_node == "p40"
    assert nearest_node == "p40"
    assert ActionStatus.RUNNING in statuses
    assert ActionStatus.FINISHED in statuses


def test_simulator_uses_flat_snapshot_nodes_as_path_points_by_default():
    adapter = _make_adapter()
    adapter.config.settings.nearest_node_mode = "pathPoint"
    adapter._vehicle.is_simulator = True
    adapter._vehicle._map_nodes = {"p40": (1000.0, 50.0, 90.0)}

    assert adapter._find_nearest_node(0.0, 0.0) == (
        "p40",
        0,
        (1000.0 ** 2 + 50.0 ** 2) ** 0.5,
    )


def test_timeout_stops_robot_and_fails():
    async def scenario():
        adapter = make_adapter()
        adapter.config.settings.node_position_poll_interval_sec = 0.01
        adapter.config.settings.goto_nearest_timeout_sec = 0.1
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.3)
            return adapter._vehicle.um_stop_calls, adapter.state.last_node_id, statuses
        finally:
            task.cancel()
    stops, last_node, statuses = asyncio.run(scenario())
    assert stops >= 1
    assert last_node == ""
    assert ActionStatus.FAILED in statuses


def test_timeout_does_not_credit_the_unreached_node_under_settled_capture():
    """The deployed policy must not hand the FMS a node the recovery never
    reached.

    gotoNearestNode is the manual recovery for a missing lastNodeId, so a
    failed attempt writing the target anyway would re-open the exact hole the
    missing_last_node_reach_xy seed gate closes: an unreached node published as
    the robot's position, with the CRITICAL that was holding orders cleared.
    """
    async def scenario():
        adapter = make_adapter()
        adapter.config.settings.last_node_capture_mode = "settled"   # deployed
        adapter.config.settings.idle_last_node_reach_xy = 150.0      # deployed
        adapter.config.settings.node_position_poll_interval_sec = 0.01
        adapter.config.settings.goto_nearest_timeout_sec = 0.1
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0                                    # 1000mm short
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g-settled"))
            await asyncio.sleep(0.3)
            return adapter.state.last_node_id, statuses
        finally:
            task.cancel()

    last_node, statuses = asyncio.run(scenario())
    assert ActionStatus.FAILED in statuses
    assert last_node == ""


def test_goto_nearest_node_still_runs_while_a_critical_error_is_active():
    """The recovery must survive the error it exists to clear.

    A missing lastNodeId publishes LAST_NODE_ID_MISSING at CRITICAL, and the
    FMS holds orders at CRITICAL — gotoNearestNode is how an operator drives
    the robot back onto a node so orders can resume. Gating instant actions on
    error level (planned) must therefore never take this one with it, or the
    robot is stuck in a state only this action can clear.
    """
    async def scenario():
        adapter = make_adapter()
        adapter.config.settings.nearest_node_mode = "pathPoint"
        adapter.config.settings.node_position_poll_interval_sec = 0.01
        adapter.config.settings.goto_nearest_timeout_sec = 2.0
        adapter._vehicle._map_path_points = {"p40": (1000.0, 50.0, 90.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0                 # far from p40, lastNodeId empty
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            critical_before = [
                e for e in adapter.state.errors
                if getattr(getattr(e, "error_type", None), "value", None)
                == "LAST_NODE_ID_MISSING"
            ]
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g-critical"))
            await asyncio.sleep(0.05)
            adapter._vehicle.arrive_at(1000.0, 50.0)
            await asyncio.sleep(0.1)
            return critical_before, adapter.state.last_node_id, statuses
        finally:
            task.cancel()

    critical_before, last_node, statuses = asyncio.run(scenario())
    assert critical_before, "fixture did not produce the CRITICAL it means to test"
    assert critical_before[0].error_level == ErrorLevel.CRITICAL
    assert ActionStatus.FAILED not in statuses
    assert ActionStatus.FINISHED in statuses
    assert last_node == "p40"


def test_not_localized_fails():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = None
        adapter._vehicle._y = None
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return list(adapter._vehicle.goto_targets), statuses
        finally:
            task.cancel()
    issued, statuses = asyncio.run(scenario())
    assert issued == []
    assert ActionStatus.FAILED in statuses


def test_active_order_fails():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        blocker = asyncio.create_task(asyncio.sleep(5))
        adapter.order_worker_task = blocker
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return list(adapter._vehicle.goto_targets), statuses
        finally:
            blocker.cancel()
            task.cancel()
    issued, statuses = asyncio.run(scenario())
    assert issued == []
    assert ActionStatus.FAILED in statuses


def test_no_nodes_fails():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return statuses
        finally:
            task.cancel()
    statuses = asyncio.run(scenario())
    assert ActionStatus.FAILED in statuses


def test_work_in_progress_blocks():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        adapter._work_in_progress = "loading"
        task = await start_state(adapter)
        statuses = _capture_statuses(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return list(adapter._vehicle.goto_targets), statuses
        finally:
            task.cancel()
    issued, statuses = asyncio.run(scenario())
    assert issued == []
    assert ActionStatus.FAILED in statuses
