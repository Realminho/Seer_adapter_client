import asyncio
from types import SimpleNamespace

from core.action_bridge import action_specs
from core.action_registry import ActionRegistry, ActionResult
from adapter_jibot import Adapter
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


def _action(kind, action_id="parent:step1:try1"):
    return SimpleNamespace(action_id=action_id, action_type=kind, action_parameters=[])


class _Vehicle:
    def __init__(self):
        self.stops = 0

    async def um_stop(self):
        self.stops += 1


class _Adapter:
    def __init__(self):
        self._action_completions = {}
        self._synthetic_action_ids = set()
        self._vehicle = _Vehicle()
        self._manual_control_active = False
        self.localized = 0

    def _cancel_manual_drive_watchdog(self):
        pass

    def _register_action_completion(self, action_id):
        future = asyncio.get_running_loop().create_future()
        self._action_completions[action_id] = future
        return future

    def _retire_action_completion(self, action_id):
        future = self._action_completions.pop(action_id, None)
        if future is not None and not future.done():
            future.cancel()
        self._synthetic_action_ids.add(action_id)

    def _handle_manual_move_instant_action(self, action):
        self._action_completions[action.action_id].set_result(
            ActionResult(ActionStatus.FINISHED, "done")
        )

    def _handle_localize_instant_action(self, action):
        self.localized += 1
        self._action_completions[action.action_id].set_result(
            ActionResult(ActionStatus.FINISHED, "localized")
        )


def test_localize_is_composable_and_bridges_to_instant_handler():
    async def run():
        adapter = _Adapter()
        registry = ActionRegistry(action_specs())
        result = await registry.execute(_action("localize"), adapter)
        return adapter, result

    adapter, result = asyncio.run(run())
    assert result.status == ActionStatus.FINISHED
    assert adapter.localized == 1
    assert adapter._action_completions == {}


def test_bridge_waits_for_private_completion_without_state_entry():
    async def run():
        adapter = _Adapter()
        registry = ActionRegistry(action_specs())
        result = await registry.execute(_action("manualMove"), adapter)
        return adapter, result

    adapter, result = asyncio.run(run())
    assert result.status == ActionStatus.FINISHED
    assert adapter._action_completions == {}
    assert "parent:step1:try1" in adapter._synthetic_action_ids


def test_motion_timeout_calls_um_stop():
    async def run():
        adapter = _Adapter()

        def never(_action):
            pass

        adapter._handle_manual_move_instant_action = never
        registry = ActionRegistry(action_specs())
        result = await registry.execute(
            _action("manualMove"), adapter, timeout_sec=0.01
        )
        return adapter, result

    adapter, result = asyncio.run(run())
    assert result.status == ActionStatus.FAILED
    assert "timeout" in result.description
    assert adapter._vehicle.stops == 1
    assert adapter._action_completions == {}


class _FakeTask:
    """set 에 들어가야 하므로 해시 가능해야 한다. SimpleNamespace 는 __eq__ 때문에 불가."""

    def __init__(self, done: bool):
        self._done = done

    def done(self) -> bool:
        return self._done


def _manual_gate_adapter(order_id="order-1", *, nodes=(), edges=(), background=()):
    """수동 주행 게이트만 보는 최소 어댑터.

    `self.order` 는 cancelOrder 로만 지워지므로(_clear_cancelled_order_state) 오더
    완료 여부는 state 의 node/edge 잔량으로 읽어야 한다. 그래서 게이트 테스트는
    order 객체와 state 를 따로 세팅한다.
    """
    adapter = Adapter.__new__(Adapter)
    adapter.config = SimpleNamespace(manual_control=SimpleNamespace(enabled=True))
    adapter._work_in_progress = None
    adapter.order = SimpleNamespace(order_id=order_id)
    adapter.state = SimpleNamespace(
        order_id=order_id, node_states=list(nodes), edge_states=list(edges)
    )
    adapter._order_background_action_tasks = set(background)
    adapter._order_exclusive_background_action_tasks = set()
    return adapter


def test_motion_owner_gate_allows_only_matching_order():
    adapter = _manual_gate_adapter(nodes=[SimpleNamespace(sequence_id=1)])

    assert adapter._manual_blocked_reason(owner_order_id="order-1") is None
    assert "order in progress" in adapter._manual_blocked_reason(
        owner_order_id="order-2"
    )
    assert "order in progress" in adapter._manual_blocked_reason(owner_order_id=None)


def test_manual_gate_reopens_once_the_order_ran_out_of_steps():
    """ORDER COMPLETE 직후 상태: order 객체는 남아 있지만 실행할 스텝은 없다.

    2026-08-22 .62 실측 회귀: 23:14:37 ORDER COMPLETE 뒤 23:19:57 cancelOrder 까지
    약 5분간 조이스틱이 무반응이었다. 완료된 오더는 수동 주행을 막지 않아야 한다.
    """
    adapter = _manual_gate_adapter()

    assert adapter._manual_blocked_reason() is None


def test_manual_gate_still_blocks_while_an_order_action_runs_in_background():
    """blockingType=NONE 액션(rotateTo)은 스텝이 다 빠진 뒤에도 로봇을 돌린다.

    그 사이 조이스틱을 열어 주면 자율 회전과 조작이 서로 싸운다.
    """
    adapter = _manual_gate_adapter(background=(_FakeTask(done=False),))

    assert "order in progress" in adapter._manual_blocked_reason()


def test_manual_gate_ignores_order_actions_that_already_finished():
    adapter = _manual_gate_adapter(background=(_FakeTask(done=True),))

    assert adapter._manual_blocked_reason() is None


def test_synthetic_completion_never_enters_vda_state_and_late_result_is_ignored(
    capsys,
):
    async def run():
        adapter = Adapter.__new__(Adapter)
        adapter._action_completions = {}
        adapter._synthetic_action_ids = set()
        adapter.state = SimpleNamespace(instant_action_states=[])
        future = adapter._register_action_completion("recipe:step1:try1")
        adapter._update_instant_action_status(
            "recipe:step1:try1", ActionStatus.FINISHED, "done"
        )
        result = await future
        adapter._update_instant_action_status(
            "recipe:step1:try1", ActionStatus.FINISHED, "late"
        )
        return adapter, result

    adapter, result = asyncio.run(run())
    assert result == ActionResult(ActionStatus.FINISHED, "done")
    assert adapter.state.instant_action_states == []
    assert "[ACTION BRIDGE LATE]" in capsys.readouterr().out


def test_registered_order_action_propagates_owner_order_id():
    seen = []

    class Registry:
        async def execute(self, action, _adapter):
            seen.append(action._owner_order_id)
            return ActionResult(ActionStatus.FINISHED, "ok")

    fake = SimpleNamespace(
        order=SimpleNamespace(order_id="order-7"),
        _action_registry=Registry(),
        request_state_publish=lambda _reason: None,
    )
    action = SimpleNamespace(action_id="r1", action_type="recipe")
    state = SimpleNamespace(action_status=None, result_description=None)
    asyncio.run(Adapter._execute_registered_order_action(fake, action, state))
    assert seen == ["order-7"]
    assert state.action_status == ActionStatus.FINISHED


def _sound_adapter(*, has_track=True, request=("driving.mp3", 0.0, True, 0)):
    calls = []
    adapter = Adapter.__new__(Adapter)
    adapter._sound = SimpleNamespace(
        has_track=lambda track: has_track,
        play=lambda *a, **k: calls.append((a, k)),
    )
    adapter._submit_sound = lambda fn, *a, **k: fn(*a, **k)
    adapter._sound_request = request
    return adapter, calls


def test_joystick_connect_sound_plays_once_and_reopens_the_state_sound():
    """연결음은 한 번만 울리고, 상태음 루프는 다음 publish 주기에 되살아나야 한다.

    SoundPlayer.play() 는 현재 트랙을 먼저 stop 시킨다. _sound_request 를 비우지
    않으면 _update_sound_for_working_state 의 dedupe 가 같은 요청이라 판단해
    상태음(driving.mp3 등)을 다시 큐에 넣지 않고 그대로 침묵한다.
    """
    adapter, calls = _sound_adapter()

    adapter.play_joystick_connect_sound()

    assert calls == [(("joystick-connected.mp3",), {"loop": False})]
    assert adapter._sound_request is None


def test_joystick_connect_sound_is_skipped_when_the_track_is_missing():
    """파일을 아직 안 넣은 로봇에서 상태음 루프를 끊어 먹으면 안 된다."""
    adapter, calls = _sound_adapter(has_track=False)

    adapter.play_joystick_connect_sound()

    assert calls == []
    assert adapter._sound_request == ("driving.mp3", 0.0, True, 0)
