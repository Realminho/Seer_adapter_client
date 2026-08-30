"""actionStates the FMS can actually consume.

The adapter reports action progress by mutating ``self.state`` and calling
``request_state_publish()``, which only *sets an Event*: the state message is
built later, by the publish loop, from whatever ``self.state`` holds at that
moment. So an entry removed in the same synchronous block never reaches the
wire — the FMS sees ``WAITING -> RUNNING -> gone`` and can never match a
terminal status to the actionId it sent.

These tests assert on the published payload, not on ``adapter.state``, because
only the payload is what the FMS gets to see.
"""

import asyncio

from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionState, ActionStatus
from tests._stop_reason_harness import make_adapter
from tests.test_adapter_jibot_v3_order import make_order


async def _start_capturing_state(adapter, published: list):
    """Run the real publish loop, recording every state message it emits."""
    adapter._loop = asyncio.get_running_loop()
    adapter._vda3.publish_state = lambda msg, qos=0: published.append(msg.to_dict())
    adapter._write_state_file = lambda msg: None
    task = asyncio.create_task(adapter.publish_state("state", interval_sec=0.02))
    for _ in range(200):
        if adapter.state is not None:
            break
        await asyncio.sleep(0.01)
    assert adapter.state is not None
    return task


def _statuses(published, key, action_id=None):
    return [
        entry["actionStatus"]
        for msg in published
        for entry in msg.get(key, [])
        if action_id is None or entry.get("actionId") == action_id
    ]


def _run_order_to_completion(published: list):
    """Drive make_order() (two HARD actions on the last node) to the end."""

    async def scenario():
        adapter = make_adapter()
        vehicle = adapter._vehicle
        task = await _start_capturing_state(adapter, published)
        try:
            adapter._handle_v3_order(make_order())
            await asyncio.sleep(0.5)
            vehicle.arrive_at(1000.0, 500.0)
            await asyncio.wait_for(adapter.order_worker_task, timeout=5.0)
            await asyncio.sleep(0.2)
            return adapter
        finally:
            task.cancel()

    return asyncio.run(scenario())


def test_finished_order_action_reaches_the_wire():
    # a-volume is blockingType HARD on the order's last node — the exclusive,
    # inline execution path, which is where the clear used to win the race.
    published = []
    _run_order_to_completion(published)

    assert "FINISHED" in _statuses(published, "actionStates", "a-volume")


def test_failed_order_action_reaches_the_wire():
    published = []
    _run_order_to_completion(published)

    assert "FAILED" in _statuses(published, "actionStates", "a-unknown")


def test_order_action_states_survive_order_completion():
    # VDA5050: the action states of the current order stay in state until an
    # order replaces them, so a late-subscribing FMS can still read the result.
    published = []
    adapter = _run_order_to_completion(published)

    statuses = {
        state.action_id: state.action_status for state in adapter.state.action_states
    }
    assert statuses["a-volume"] is ActionStatus.FINISHED
    assert statuses["a-unknown"] is ActionStatus.FAILED


def test_published_order_action_id_is_the_id_the_order_carried():
    # The FMS matches on the actionId it sent; a "{kind}_{seq}_" prefix makes
    # every actionStates entry unmatchable.
    published = []
    _run_order_to_completion(published)

    published_ids = {
        entry["actionId"] for msg in published for entry in msg.get("actionStates", [])
    }
    assert published_ids == {"a-volume", "a-unknown"}


def test_a_new_order_replaces_the_previous_action_states():
    async def scenario():
        published = []
        adapter = make_adapter()
        task = await _start_capturing_state(adapter, published)
        try:
            adapter._handle_v3_order(make_order())
            await asyncio.sleep(0.2)
            adapter._handle_v3_order(make_order("order-2"))
            await asyncio.sleep(0.2)
            return [state.action_status for state in adapter.state.action_states]
        finally:
            task.cancel()

    assert asyncio.run(scenario()) == [ActionStatus.WAITING, ActionStatus.WAITING]


def test_finished_instant_action_reaches_the_wire():
    async def scenario():
        published = []
        adapter = make_adapter()
        adapter.config.manual_control.enabled = True

        async def disable_motor():
            return None

        adapter._vehicle.disable_motor = disable_motor
        task = await _start_capturing_state(adapter, published)
        try:
            action_id = adapter.submit_local_instant_action(
                "disableMotor", None, source="joystick"
            )
            await asyncio.sleep(0.2)
            return _statuses(published, "instantActionStates", action_id)
        finally:
            task.cancel()

    assert "FINISHED" in asyncio.run(scenario())


def test_terminal_instant_action_states_are_bounded():
    # Retaining terminal states must not let instantActionStates grow without
    # bound: the joystick submits one instant action per button press, and a
    # shift of manual driving may never accept an order to reset them.
    async def scenario():
        adapter = make_adapter()
        task = await _start_capturing_state(adapter, [])
        try:
            limit = adapter._max_retained_terminal_instant_action_states
            for index in range(limit + 10):
                adapter.state.instant_action_states.append(
                    ActionState(
                        action_id=f"ia-{index}",
                        action_status=ActionStatus.FINISHED,
                        action_type="probe",
                    )
                )
                adapter._prune_retained_instant_action_states()
            return limit, [
                state.action_id for state in adapter.state.instant_action_states
            ]
        finally:
            task.cancel()

    limit, retained = asyncio.run(scenario())
    assert len(retained) == limit
    assert retained[-1] == f"ia-{limit + 9}"
