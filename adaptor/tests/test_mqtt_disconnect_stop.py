"""FMS(MQTT) 링크가 끊겼을 때 로봇을 세우고 알림음을 울리는 가드.

브로커가 사라져도 로봇은 마지막으로 받은 order 를 계속 밀고 나간다. FMS 는
교통정리를 못 하는 상태인데 로봇만 달리는 구간이 생긴다. 이 테스트들은
그 구간을 유예시간(grace) 이후 startPause 와 같은 일시정지로 덮는 것을 고정한다.

- 짧은 재연결(순간 끊김)에는 서지 않는다 — grace 안에 돌아오면 타이머를 접는다.
- 기동 직후 브로커 미가용은 "끊김"이 아니다 — 한 번도 붙은 적 없으면 발화 안 한다.
- 재연결하면 stopPause 와 같은 경로로 스스로 재개한다. 단 FMS 가 startPause 로
  걸어 둔 pause 는 건드리지 않는다.
"""

import asyncio

from adapter_jibot import Adapter
from tests._stop_reason_harness import start_state
from tests.test_adapter_jibot_v3_order import FakeVehicle


def make_adapter(vehicle: FakeVehicle) -> Adapter:
    adapter = Adapter()
    adapter.set_vehicle(vehicle)
    adapter.config.settings.stop_on_mqtt_disconnect = True
    adapter.config.settings.mqtt_disconnect_stop_grace_sec = 0.05
    # 실기의 health 파일 쓰기와 connectionState 재발행은 이 테스트의 관심사가
    # 아니고 브로커도 없다. 콜백 진입점을 그대로 쓰기 위해 여기서만 무력화한다.
    adapter._write_health_file = lambda *a, **k: None
    adapter._republish_connection_online = lambda *a, **k: None
    adapter._play_mqtt_disconnect_sound = lambda: sounds.append("disconnect")
    sounds = adapter._mqtt_disconnect_sounds = []
    return adapter


async def _settle(seconds: float = 0.2) -> None:
    await asyncio.sleep(seconds)


def test_disconnect_beyond_grace_pauses_motion():
    async def scenario():
        vehicle = FakeVehicle()
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            adapter._on_acs_broker_change(True)
            adapter._on_acs_broker_change(False)
            await _settle()
            assert vehicle.stop_motion_calls == 1, vehicle.stop_motion_calls
            assert adapter._motion_paused is True
            assert adapter.state.paused is True
            assert adapter._paused_by_mqtt_loss is True
            assert adapter._mqtt_disconnect_sounds == ["disconnect"]
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_reconnect_within_grace_does_not_pause():
    """순간 끊김은 세우지 않는다. 알림음도 울리지 않는다."""

    async def scenario():
        vehicle = FakeVehicle()
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            adapter._on_acs_broker_change(True)
            adapter._on_acs_broker_change(False)
            await asyncio.sleep(0.01)
            adapter._on_acs_broker_change(True)
            await _settle()
            assert vehicle.stop_motion_calls == 0
            assert adapter._motion_paused is False
            assert adapter._mqtt_disconnect_sounds == []
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_never_connected_broker_does_not_pause():
    """기동 시 브로커가 아직 없는 것은 링크 상실이 아니다."""

    async def scenario():
        vehicle = FakeVehicle()
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            adapter._on_acs_broker_change(False)
            await _settle()
            assert vehicle.stop_motion_calls == 0
            assert adapter._motion_paused is False
            assert adapter._mqtt_disconnect_sounds == []
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_option_off_still_plays_sound_but_does_not_pause():
    """정지는 옵션이지만 알림음은 링크 상실 자체의 알림이라 독립이다."""

    async def scenario():
        vehicle = FakeVehicle()
        adapter = make_adapter(vehicle)
        adapter.config.settings.stop_on_mqtt_disconnect = False
        task = await start_state(adapter)
        try:
            adapter._on_acs_broker_change(True)
            adapter._on_acs_broker_change(False)
            await _settle()
            assert vehicle.stop_motion_calls == 0
            assert adapter._motion_paused is False
            assert adapter._mqtt_disconnect_sounds == ["disconnect"]
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_reconnect_reissues_the_interrupted_goto():
    """재연결은 stopPause 와 같은 경로로 중단된 goto 를 다시 쏜다."""

    async def scenario():
        vehicle = FakeVehicle()
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            adapter._on_acs_broker_change(True)
            adapter._active_goto_node = _node("N1")
            adapter._on_acs_broker_change(False)
            await _settle()
            assert adapter._motion_paused is True
            sent_before = len(vehicle.goto_targets)

            adapter._on_acs_broker_change(True)
            await _settle()
            assert len(vehicle.goto_targets) > sent_before, vehicle.goto_targets
            assert adapter._motion_paused is False
            assert adapter.state.paused is False
            assert adapter._paused_by_mqtt_loss is False
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_reconnect_does_not_clear_an_operator_pause():
    """FMS 가 startPause 로 세워 둔 것은 재연결이 풀면 안 된다."""

    async def scenario():
        vehicle = FakeVehicle()
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            adapter._on_acs_broker_change(True)
            adapter._motion_paused = True
            adapter.state.paused = True
            adapter._on_acs_broker_change(False)
            await _settle()
            # 이미 멈춰 있으니 stop_motion 을 다시 부르지 않는다.
            assert vehicle.stop_motion_calls == 0
            assert adapter._paused_by_mqtt_loss is False

            adapter._on_acs_broker_change(True)
            await _settle()
            assert adapter._motion_paused is True
            assert adapter.state.paused is True
        finally:
            task.cancel()

    asyncio.run(scenario())


def _node(node_id: str):
    from protocol.vda5050_3_0.messages import Node

    return Node.from_dict({
        "nodeId": node_id,
        "sequenceId": 2,
        "released": True,
        "nodePosition": {"x": 0.0, "y": 0.0, "theta": 0.0, "mapId": "lab2m"},
        "actions": [],
    })


class GatedVehicle(FakeVehicle):
    """stop_motion / goto_point 이 이벤트가 열릴 때까지 await 에 머무는 로봇.

    실기의 두 명령은 TCP 왕복이라 최대 jibot_command_default_timeout_sec 동안
    await 한다. 그 창 안에서 링크 상태가 뒤집히는 경우를 재현한다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stop_gate = asyncio.Event()
        self.goto_gate = asyncio.Event()
        self.stop_entered = asyncio.Event()
        self.goto_entered = asyncio.Event()

    async def stop_motion(self) -> None:
        self.stop_entered.set()
        await self.stop_gate.wait()
        await super().stop_motion()

    async def goto_point(self, point: str, strict: bool = False) -> None:
        self.goto_entered.set()
        await self.goto_gate.wait()
        await super().goto_point(point, strict)


def test_reconnect_during_an_in_flight_pause_still_resumes():
    """stop_motion 을 기다리는 동안 링크가 돌아오면 스스로 재개한다.

    이 창에서는 _paused_by_mqtt_loss 가 아직 False 라 _on_acs_broker_change 의
    재개 분기가 안 걸린다. 그대로 두면 링크가 멀쩡한데 로봇만 영영 서 있다.
    """

    async def scenario():
        vehicle = GatedVehicle()
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            adapter._on_acs_broker_change(True)
            adapter._active_goto_node = _node("N1")
            adapter._on_acs_broker_change(False)
            await asyncio.wait_for(vehicle.stop_entered.wait(), 1.0)

            # stop_motion 이 아직 안 끝난 사이에 링크 복구.
            adapter._on_acs_broker_change(True)
            vehicle.stop_gate.set()
            vehicle.goto_gate.set()
            await _settle()

            assert adapter._motion_paused is False, "링크가 돌아왔는데 서 있다"
            assert adapter._paused_by_mqtt_loss is False
            assert vehicle.goto_targets, vehicle.goto_targets
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_disconnect_during_an_in_flight_resume_pauses_again():
    """goto 재발행을 기다리는 동안 또 끊기면 다시 세운다.

    그 사이 발화한 유예 타이머는 _motion_paused=True 를 보고 소진되므로,
    재개 끝에서 다시 걸지 않으면 로봇이 브로커 없이 달린다.
    """

    async def scenario():
        vehicle = GatedVehicle()
        adapter = make_adapter(vehicle)
        task = await start_state(adapter)
        try:
            adapter._on_acs_broker_change(True)
            adapter._active_goto_node = _node("N1")
            adapter._on_acs_broker_change(False)
            await asyncio.wait_for(vehicle.stop_entered.wait(), 1.0)
            vehicle.stop_gate.set()
            await _settle()
            assert adapter._paused_by_mqtt_loss is True

            adapter._on_acs_broker_change(True)
            await asyncio.wait_for(vehicle.goto_entered.wait(), 1.0)
            # 재발행이 아직 안 끝난 사이에 다시 끊김.
            adapter._on_acs_broker_change(False)
            await _settle()
            vehicle.goto_gate.set()
            await _settle(0.3)

            assert adapter._motion_paused is True, "브로커 없이 달리고 있다"
            assert adapter._paused_by_mqtt_loss is True
        finally:
            task.cancel()

    asyncio.run(scenario())
