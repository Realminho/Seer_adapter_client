import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_ROOT.parent
for path in (ADAPTER_ROOT, REPO_ROOT / "jibot-simulator", REPO_ROOT / "jibot-client" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from adapter_jibot import Adapter
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionState, ActionStatus
from protocol.vda5050_3_0.messages import InstantActions
from test_set_map_instant_action import FakeVehicle, StateStub


def make_action(map_id=None):
    parameters = [] if map_id is None else [{"key": "mapId", "value": map_id}]
    return InstantActions.from_dict({
        "headerId": 1, "timestamp": "2026-07-15T00:00:00Z", "version": "3.0.0",
        "manufacturer": "jibot", "serialNumber": "TEST",
        "actions": [{
            "actionType": "switchMap", "actionId": "switch-1", "blockingType": "NONE",
            "actionParameters": parameters,
        }],
    })


def make_adapter(simulator=True):
    adapter = Adapter()
    adapter.set_vehicle(FakeVehicle(is_simulator=simulator))
    adapter.state = StateStub()
    return adapter


def capture(adapter):
    statuses = []
    original = adapter._update_instant_action_status

    def update(action_id, status, **kwargs):
        statuses.append(status)
        return original(action_id, status, **kwargs)

    adapter._update_instant_action_status = update
    return statuses


def test_switch_map_requires_map_id():
    adapter = make_adapter()
    statuses = capture(adapter)
    adapter.instant_actions_accept_procedure(make_action())
    assert ActionStatus.FAILED in statuses


def test_switch_map_updates_simulator_map_id():
    adapter = make_adapter()
    statuses = capture(adapter)
    adapter.instant_actions_accept_procedure(make_action("FAB_2F"))
    assert adapter._current_map_id == "FAB_2F"
    assert ActionStatus.FINISHED in statuses


def test_switch_map_calls_jibot_and_finishes_after_confirmation():
    async def scenario():
        adapter = make_adapter(simulator=False)
        adapter._loop = asyncio.get_running_loop()
        adapter._vehicle.set_map = AsyncMock()
        adapter._vehicle.get_map_name = AsyncMock(return_value="FAB_2F")
        statuses = capture(adapter)
        adapter.instant_actions_accept_procedure(make_action("FAB_2F"))
        await asyncio.sleep(0.02)
        adapter._vehicle.set_map.assert_awaited_once_with("FAB_2F")
        assert ActionStatus.FINISHED in statuses

    asyncio.run(scenario())


def test_switch_map_fails_when_current_map_never_matches():
    async def scenario():
        adapter = make_adapter(simulator=False)
        adapter._loop = asyncio.get_running_loop()
        adapter.config.settings.switch_map_timeout_seconds = 0.01
        adapter.config.settings.node_position_poll_interval_sec = 0.001
        adapter._vehicle.set_map = AsyncMock()
        adapter._vehicle.get_map_name = AsyncMock(return_value="OLD_MAP")
        statuses = capture(adapter)
        adapter.instant_actions_accept_procedure(make_action("FAB_2F"))
        await asyncio.sleep(0.03)
        assert ActionStatus.FAILED in statuses

    asyncio.run(scenario())


def test_switch_map_node_action_uses_same_handler():
    async def scenario():
        adapter = make_adapter(simulator=True)
        action = make_action("FAB_3F").actions[0]
        state = ActionState(
            action_id="switch-1",
            action_status=ActionStatus.WAITING,
            action_type="switchMap",
        )
        await adapter._execute_order_action(action, state, owner_id="MAP-GATE")
        assert state.action_status == ActionStatus.FINISHED
        assert adapter._current_map_id == "FAB_3F"

    asyncio.run(scenario())
