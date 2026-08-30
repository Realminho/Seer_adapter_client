"""Locally-originated actions (today: the joystick) reach the same handlers.

An input that is not the FMS must not get a private side door into the action
handlers: the accept procedure is where work/order gating, status reporting and
the action registry live, so a local trigger has to go through it too.
"""

import asyncio

from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from tests._stop_reason_harness import make_adapter, start_state


def _run(action_type, parameters=None, *, manual_enabled=True):
    async def scenario():
        adapter = make_adapter()
        adapter.config.manual_control.enabled = manual_enabled
        calls = []
        statuses = {}

        async def fake_disable():
            calls.append(True)

        adapter._vehicle.disable_motor = fake_disable
        original = adapter._update_instant_action_status

        def capture(action_id, status, *args, **kw):
            statuses[action_id] = status
            return original(action_id, status, *args, **kw)

        adapter._update_instant_action_status = capture
        task = await start_state(adapter)
        try:
            action_id = adapter.submit_local_instant_action(
                action_type, parameters, source="joystick"
            )
            await asyncio.sleep(0.1)
            return adapter, action_id, calls, statuses
        finally:
            task.cancel()

    return asyncio.run(scenario())


def test_local_instant_action_reaches_the_handler():
    _adapter, action_id, calls, statuses = _run("disableMotor")

    assert calls == [True]
    assert statuses[action_id] is ActionStatus.FINISHED


def test_local_instant_action_id_names_its_source():
    # The FMS sees these in instantActionStates alongside its own, so the id has
    # to say where they came from.
    _adapter, action_id, _calls, _statuses = _run("disableMotor")

    assert action_id.startswith("joystick-disableMotor-")


def test_local_instant_action_obeys_the_same_gating():
    _adapter, action_id, calls, statuses = _run("disableMotor", manual_enabled=False)

    assert calls == []
    assert statuses[action_id] is ActionStatus.FAILED


def test_local_instant_action_carries_parameters():
    # manualDrive reads trans/rot/speed/lat off the parameters, and unlike
    # setSoundVolume it does not persist anything to config.toml.
    adapter, action_id, _calls, statuses = _run(
        "manualDrive", {"trans": 12, "rot": 3, "speed": 40, "lat": 0}
    )

    assert statuses[action_id] is ActionStatus.FINISHED
    assert adapter._vehicle.drive_calls[-1] == (12.0, 3.0, 40.0, 0.0)


def test_unknown_local_action_fails_instead_of_raising():
    _adapter, action_id, _calls, statuses = _run("noSuchActionType")

    assert statuses[action_id] is ActionStatus.FAILED
