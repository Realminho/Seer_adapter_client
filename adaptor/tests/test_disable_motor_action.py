import asyncio
import unittest

from protocol.vda5050_3_0.messages import InstantActions
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from tests._stop_reason_harness import make_adapter, start_state


def _disable_motor_ia(action_id):
    return InstantActions.from_dict({
        "headerId": 1,
        "timestamp": "2026-06-25T00:00:00.000Z",
        "version": "3.0.0",
        "manufacturer": "jibot",
        "serialNumber": "HN-TEST-001",
        "actions": [{
            "actionType": "disableMotor",
            "actionId": action_id,
            "blockingType": "NONE",
            "actionParameters": [],
        }],
    })


def _dispatch(enabled):
    async def scenario():
        adapter = make_adapter()
        adapter.config.manual_control.enabled = enabled
        calls = []

        async def fake_disable():
            calls.append(True)

        adapter._vehicle.disable_motor = fake_disable
        statuses = []
        orig = adapter._update_instant_action_status

        def capture(action_id, status, **kw):
            statuses.append(status)
            return orig(action_id, status, **kw)

        adapter._update_instant_action_status = capture
        task = await start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(_disable_motor_ia("d1"))
            await asyncio.sleep(0.1)
            return calls, statuses
        finally:
            task.cancel()
    return asyncio.run(scenario())


class DisableMotorTest(unittest.TestCase):
    def test_disable_motor_calls_vehicle_and_finishes(self):
        calls, statuses = _dispatch(enabled=True)
        self.assertEqual(calls, [True])
        self.assertIn(ActionStatus.FINISHED, statuses)

    def test_disable_motor_gated_off_fails(self):
        calls, statuses = _dispatch(enabled=False)
        self.assertEqual(calls, [])
        self.assertIn(ActionStatus.FAILED, statuses)


if __name__ == "__main__":
    unittest.main()
