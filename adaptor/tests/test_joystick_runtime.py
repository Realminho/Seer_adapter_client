import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from utils import joystick_runtime
from utils.joystick import JoystickEvent
from utils.joystick_runtime import (
    EV_ABS,
    EV_KEY,
    INPUT_EVENT,
    MicroJoystickService,
    Ultimate2JoystickService,
    receiver_present,
)


def _adapter(*, actions=(), actions_enabled=False):
    joystick = SimpleNamespace(
        micro_dpad_x_code=0,
        micro_dpad_y_code=1,
        micro_neutral_threshold=1000,
        micro_stop_button=311,
        micro_reconnect_sec=1.0,
        vendor_id="2dc8",
        product_id="310b",
        forward_axis=5,
        reverse_axis=2,
        steering_axis=0,
        stop_button=5,
        speed_down_button=6,
        speed_up_button=7,
        dpad_x_axis=6,
        dpad_y_axis=7,
        a_button=1,
        b_button=0,
        x_button=3,
        y_button=2,
        speed_step_percent=20,
        speed_min_percent=20,
        speed_max_percent=100,
        speed_start_percent=100,
        trigger_threshold=0.1,
        steering_deadzone=0.15,
        joystick_reconnect_sec=1.0,
        xboxdrv_device_name="Xbox Gamepad (userspace driver)",
        xboxdrv_forward_axis_code=9,
        xboxdrv_reverse_axis_code=10,
        xboxdrv_steering_axis_code=0,
        xboxdrv_stop_button_code=311,
        xboxdrv_dpad_x_code=16,
        xboxdrv_dpad_y_code=17,
        xboxdrv_a_button_code=304,
        xboxdrv_b_button_code=305,
        xboxdrv_x_button_code=307,
        xboxdrv_y_button_code=308,
        xboxdrv_speed_down_button_code=314,
        xboxdrv_speed_up_button_code=315,
        xboxdrv_receiver_guard=True,
        actions_enabled=actions_enabled,
        actions=list(actions),
    )
    manual = SimpleNamespace(
        drive_trans=200,
        drive_rot=30,
        drive_speed=100,
        drive_lat=0,
        heartbeat_ms=300,
    )
    adapter = SimpleNamespace(
        config=SimpleNamespace(joystick=joystick, manual_control=manual),
        _vehicle=SimpleNamespace(um_drive=AsyncMock(), um_stop=AsyncMock()),
        _manual_control_active=False,
        _manual_blocked_reason=Mock(return_value=None),
        _cancel_manual_drive_watchdog=Mock(),
        _arm_manual_drive_watchdog=Mock(),
        submit_local_instant_action=Mock(return_value="joystick-x-1"),
        play_joystick_connect_sound=Mock(),
    )
    return adapter


def _slot(slot, action, **parameters):
    return SimpleNamespace(
        slot=slot, action=action, target="action",
        parameters=parameters, enabled=True,
    )


def test_micro_dpad_drives_and_neutral_stops():
    async def scenario():
        adapter = _adapter()
        service = MicroJoystickService(adapter)

        await service._handle_event(EV_ABS, 1, -32767)
        adapter._vehicle.um_drive.assert_awaited_once_with(200.0, 0.0, 100.0, 0.0)
        assert adapter._manual_control_active is True

        await service._handle_event(EV_ABS, 1, -241)
        adapter._vehicle.um_stop.assert_awaited_once()
        assert adapter._manual_control_active is False

    asyncio.run(scenario())


def test_micro_r_button_stops_active_drive():
    async def scenario():
        adapter = _adapter()
        service = MicroJoystickService(adapter)
        await service._handle_event(EV_ABS, 0, 32767)
        await service._handle_event(EV_KEY, 311, 1)

        adapter._vehicle.um_drive.assert_awaited_once_with(0.0, -30.0, 100.0, 0.0)
        adapter._vehicle.um_stop.assert_awaited_once()

    asyncio.run(scenario())


def test_micro_drive_is_blocked_during_order():
    async def scenario():
        adapter = _adapter()
        adapter._manual_blocked_reason.return_value = "order in progress"
        service = MicroJoystickService(adapter)

        await service._handle_event(EV_ABS, 1, 32767)
        adapter._vehicle.um_drive.assert_not_awaited()

    asyncio.run(scenario())


def _js_event(event_type, number, value):
    return JoystickEvent(0, value, event_type, number)


def test_ultimate2_trigger_drive_steering_and_release_stop():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 5, 32767))
        adapter._vehicle.um_drive.assert_awaited_with(200.0, 0.0, 100.0, 0.0)
        await service._handle_event(_js_event("axis", 0, 32767))
        adapter._vehicle.um_drive.assert_awaited_with(200.0, -30.0, 100.0, 0.0)
        await service._handle_event(_js_event("axis", 5, -32767))
        adapter._vehicle.um_drive.assert_awaited_with(0.0, -30.0, 100.0, 0.0)
        await service._handle_event(_js_event("axis", 0, 0))
        adapter._vehicle.um_stop.assert_awaited_once()

    asyncio.run(scenario())


def test_ultimate2_partial_trigger_scales_the_umdrive_speed():
    """A half-pulled R2 must ask for half the speed, not the full configured one."""
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 5, 3277))

        trans, rot, speed, lat = adapter._vehicle.um_drive.await_args.args
        assert round(trans, 2) == 100.0
        assert round(speed, 2) == 50.0
        assert (rot, lat) == (0.0, 0.0)

    asyncio.run(scenario())


def test_ultimate2_partial_steering_alone_scales_the_umdrive_speed():
    """Rotation carries no trigger, so the stick alone has to set the speed."""
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 0, 18841))

        adapter._vehicle.um_drive.assert_awaited_with(0.0, -15.0, 50.0, 0.0)

    asyncio.run(scenario())


def test_ultimate2_speed_follows_the_larger_of_trigger_and_steering():
    """Steering gently while driving must not drag the speed below the trigger."""
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 5, 3277))
        await service._handle_event(_js_event("axis", 0, 11878))

        trans, rot, speed, lat = adapter._vehicle.um_drive.await_args.args
        assert round(trans, 2) == 100.0
        assert rot == -7.5
        assert round(speed, 2) == 50.0
        assert lat == 0.0

    asyncio.run(scenario())


def test_ultimate2_r1_and_simultaneous_triggers_latch_until_neutral():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 5, 32767))
        await service._handle_event(_js_event("button", 5, 1))
        assert service._blocked_until_neutral is True
        adapter._vehicle.um_stop.assert_awaited_once()

        await service._handle_event(_js_event("axis", 2, 32767))
        assert adapter._vehicle.um_drive.await_count == 1
        await service._handle_event(_js_event("axis", 5, -32767))
        await service._handle_event(_js_event("axis", 2, -32767))
        assert service._blocked_until_neutral is False

        await service._handle_event(_js_event("axis", 5, 32767))
        await service._handle_event(_js_event("axis", 2, 32767))
        assert service._blocked_until_neutral is True

    asyncio.run(scenario())


def test_ultimate2_xboxdrv_evdev_trigger_steering_and_stop():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_evdev_event(EV_ABS, 9, 255)
        adapter._vehicle.um_drive.assert_awaited_with(200.0, 0.0, 100.0, 0.0)
        await service._handle_evdev_event(EV_ABS, 0, 32767)
        adapter._vehicle.um_drive.assert_awaited_with(200.0, -30.0, 100.0, 0.0)
        await service._handle_evdev_event(EV_KEY, 311, 1)
        assert service._blocked_until_neutral is True
        adapter._vehicle.um_stop.assert_awaited_once()

    asyncio.run(scenario())


def _usb_tree(root: Path, *devices):
    root.mkdir(parents=True, exist_ok=True)
    for index, (vendor, product) in enumerate(devices):
        device = root / f"1-{index}"
        device.mkdir()
        (device / "idVendor").write_text(f"{vendor}\n")
        (device / "idProduct").write_text(f"{product}\n")
    return root


def test_receiver_present_tracks_the_awake_product_id(tmp_path):
    config = _adapter().config.joystick
    awake = _usb_tree(tmp_path / "awake", ("1d6b", "0002"), ("2dc8", "310b"))
    asleep = _usb_tree(tmp_path / "asleep", ("1d6b", "0002"), ("2dc8", "3109"))

    assert receiver_present(config, root=awake) is True
    # The receiver is still on the bus, but as the id nothing can be read from.
    assert receiver_present(config, root=asleep) is False
    # An unreadable root counts as absent so an armed guard fails to a stop.
    assert receiver_present(config, root=tmp_path / "missing") is False


def _evdev_bytes(*events):
    return b"".join(INPUT_EVENT.pack(0, 0, *event) for event in events)


def test_evdev_heartbeat_stops_when_the_receiver_disappears(tmp_path, monkeypatch):
    """The xboxdrv pad goes quiet instead of dying, so the drive must not persist."""
    device = tmp_path / "event9"
    # A full-throttle press and then nothing more: the file hits EOF exactly the
    # way the virtual pad stops emitting once its receiver re-enumerates.
    device.write_bytes(_evdev_bytes((EV_ABS, 9, 255)))
    monkeypatch.setattr(
        joystick_runtime, "USB_DEVICES_ROOT",
        _usb_tree(tmp_path / "usb", ("2dc8", "3109")),
    )

    async def scenario():
        adapter = _adapter()
        adapter.config.manual_control.heartbeat_ms = 0
        service = Ultimate2JoystickService(adapter)
        await asyncio.wait_for(
            service._read_evdev_connected(device, guard=True), timeout=2
        )

        adapter._vehicle.um_drive.assert_awaited_once_with(200.0, 0.0, 100.0, 0.0)
        adapter._vehicle.um_stop.assert_awaited_once()
        assert service._moving is False
        assert service._blocked_until_neutral is True
        assert adapter._manual_control_active is False
        # Still latched: forward stays stale non-zero until the operator releases.
        await service._apply()
        assert adapter._vehicle.um_drive.await_count == 1

    asyncio.run(scenario())


def test_evdev_heartbeat_keeps_driving_while_the_receiver_is_there(tmp_path, monkeypatch):
    """A held trigger emits nothing, so presence -- not silence -- is the signal."""
    device = tmp_path / "event9"
    device.write_bytes(_evdev_bytes((EV_ABS, 9, 255)))
    monkeypatch.setattr(
        joystick_runtime, "USB_DEVICES_ROOT",
        _usb_tree(tmp_path / "usb", ("2dc8", "310b")),
    )

    async def scenario():
        adapter = _adapter()
        adapter.config.manual_control.heartbeat_ms = 0
        service = Ultimate2JoystickService(adapter)
        reader = asyncio.create_task(
            service._read_evdev_connected(device, guard=True)
        )
        await asyncio.sleep(0.1)
        try:
            assert service._moving is True
            assert adapter._vehicle.um_drive.await_count > 1
            adapter._vehicle.um_stop.assert_not_awaited()
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    asyncio.run(scenario())


def test_ultimate2_chord_fires_the_configured_slot():
    async def scenario():
        adapter = _adapter(
            actions=[_slot(6, "unclamp"), _slot(13, "startCharging", mode="fast")],
            actions_enabled=True,
        )
        service = Ultimate2JoystickService(adapter)

        # Right (dpad x = +32767) + B (js button 0) is slot 6.
        await service._handle_event(_js_event("axis", 6, 32767))
        await service._handle_event(_js_event("button", 0, 1))
        adapter.submit_local_instant_action.assert_called_once_with(
            "unclamp", {}, source="joystick"
        )

        # Left + A is slot 13, and its configured parameters ride along.
        await service._handle_event(_js_event("axis", 6, -32767))
        await service._handle_event(_js_event("button", 1, 1))
        adapter.submit_local_instant_action.assert_called_with(
            "startCharging", {"mode": "fast"}, source="joystick"
        )

    asyncio.run(scenario())


def test_ultimate2_chord_is_refused_while_a_latched_trigger_is_still_held():
    async def scenario():
        adapter = _adapter(actions=[_slot(1, "enableMotor")], actions_enabled=True)
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 7, -32767))
        await service._handle_event(_js_event("axis", 5, 32767))
        await service._handle_event(_js_event("button", 5, 1))
        # R1 latched the drive, so the wheels stopped -- but the trigger is
        # still pulled, which is not a moment to fire a slot action.
        assert service._moving is False
        assert service._blocked_until_neutral is True
        await service._handle_event(_js_event("button", 1, 1))
        adapter.submit_local_instant_action.assert_not_called()

        await service._handle_event(_js_event("axis", 5, -32767))
        await service._handle_event(_js_event("button", 1, 1))
        adapter.submit_local_instant_action.assert_called_once_with(
            "enableMotor", {}, source="joystick"
        )

    asyncio.run(scenario())


def test_ultimate2_chord_needs_a_direction_and_is_refused_while_driving():
    async def scenario():
        adapter = _adapter(actions=[_slot(1, "enableMotor")], actions_enabled=True)
        service = Ultimate2JoystickService(adapter)

        # A face button with the D-pad neutral maps to no slot at all.
        await service._handle_event(_js_event("button", 1, 1))
        adapter.submit_local_instant_action.assert_not_called()

        # A diagonal is ambiguous, so it maps to no slot either.
        await service._handle_event(_js_event("axis", 6, 32767))
        await service._handle_event(_js_event("axis", 7, -32767))
        await service._handle_event(_js_event("button", 1, 1))
        adapter.submit_local_instant_action.assert_not_called()

        # Up + A is slot 1, but not while the robot is under manual drive.
        await service._handle_event(_js_event("axis", 6, 0))
        await service._handle_event(_js_event("axis", 5, 32767))
        assert service._moving is True
        await service._handle_event(_js_event("button", 1, 1))
        adapter.submit_local_instant_action.assert_not_called()

        # Release the trigger and the same chord goes through.
        await service._handle_event(_js_event("axis", 5, -32767))
        await service._handle_event(_js_event("button", 1, 1))
        adapter.submit_local_instant_action.assert_called_once_with(
            "enableMotor", {}, source="joystick"
        )

    asyncio.run(scenario())


def test_ultimate2_slot_actions_stay_off_until_enabled():
    async def scenario():
        adapter = _adapter(actions=[_slot(1, "enableMotor")])
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 7, -32767))
        await service._handle_event(_js_event("button", 1, 1))
        adapter.submit_local_instant_action.assert_not_called()

    asyncio.run(scenario())


def test_ultimate2_speed_buttons_scale_the_drive():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("button", 6, 1))  # - : 100 -> 80
        assert service._speed_percent == 80
        await service._handle_event(_js_event("axis", 5, 32767))
        adapter._vehicle.um_drive.assert_awaited_with(160.0, 0.0, 80.0, 0.0)

        # Pressing + mid-drive re-sends immediately; the heartbeat is not the
        # thing that applies it.
        await service._handle_event(_js_event("button", 7, 1))  # + : 80 -> 100
        adapter._vehicle.um_drive.assert_awaited_with(200.0, 0.0, 100.0, 0.0)

    asyncio.run(scenario())


def test_ultimate2_speed_clamps_and_sends_nothing_at_the_bound():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 5, 32767))
        adapter._vehicle.um_drive.assert_awaited_once_with(200.0, 0.0, 100.0, 0.0)

        # Already at speed_max_percent: no change, so no second um_drive.
        await service._handle_event(_js_event("button", 7, 1))
        assert service._speed_percent == 100
        assert adapter._vehicle.um_drive.await_count == 1

        for _ in range(5):
            await service._handle_event(_js_event("button", 6, 1))
        assert service._speed_percent == 20
        adapter._vehicle.um_drive.assert_awaited_with(40.0, 0.0, 20.0, 0.0)

    asyncio.run(scenario())


def test_ultimate2_speed_buttons_work_while_slot_actions_are_off():
    async def scenario():
        adapter = _adapter(actions=[_slot(1, "enableMotor")], actions_enabled=False)
        service = Ultimate2JoystickService(adapter)

        # actions_enabled guards slots that run motors and clamps. Scaling a
        # drive the operator is already performing is not that.
        await service._handle_event(_js_event("button", 6, 1))
        assert service._speed_percent == 80
        adapter.submit_local_instant_action.assert_not_called()

    asyncio.run(scenario())


def test_ultimate2_evdev_speed_buttons_use_the_xboxdrv_codes():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_evdev_event(EV_KEY, 314, 1)
        assert service._speed_percent == 80
        await service._handle_evdev_event(EV_ABS, 9, 255)
        adapter._vehicle.um_drive.assert_awaited_with(160.0, 0.0, 80.0, 0.0)

    asyncio.run(scenario())


def test_ultimate2_evdev_speed_button_mid_drive_resends_immediately():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_evdev_event(EV_ABS, 9, 255)
        adapter._vehicle.um_drive.assert_awaited_with(200.0, 0.0, 100.0, 0.0)

        # Pressing - mid-drive re-sends immediately; the heartbeat is not the
        # thing that applies it. Proven on the joydev path by
        # test_ultimate2_speed_buttons_scale_the_drive above -- this is the
        # same behaviour on the xboxdrv evdev path.
        await service._handle_evdev_event(EV_KEY, 314, 1)  # - : 100 -> 80
        assert service._speed_percent == 80
        adapter._vehicle.um_drive.assert_awaited_with(160.0, 0.0, 80.0, 0.0)

    asyncio.run(scenario())


def test_ultimate2_evdev_chord_uses_the_xboxdrv_codes():
    async def scenario():
        adapter = _adapter(actions=[_slot(11, "clamp")], actions_enabled=True)
        service = Ultimate2JoystickService(adapter)

        # Down (ABS_HAT0Y = +1) + X (BTN_X) is slot 11.
        await service._handle_evdev_event(EV_ABS, 17, 1)
        await service._handle_evdev_event(EV_KEY, 307, 1)
        adapter.submit_local_instant_action.assert_called_once_with(
            "clamp", {}, source="joystick"
        )

    asyncio.run(scenario())


def test_ultimate2_button_release_fires_nothing():
    async def scenario():
        adapter = _adapter(actions=[_slot(1, "enableMotor")], actions_enabled=True)
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 7, -32767))
        await service._handle_event(_js_event("button", 1, 0))
        adapter.submit_local_instant_action.assert_not_called()

        await service._handle_evdev_event(EV_ABS, 17, -1)
        await service._handle_evdev_event(EV_KEY, 304, 0)
        adapter.submit_local_instant_action.assert_not_called()

    asyncio.run(scenario())


def test_ultimate2_refusal_is_logged_once_per_attempt(capsys):
    """차단은 정지 상태에서 일어나므로 _stop 이 아무것도 찍지 않는다.

    2026-08-22 .62 에서 조이스틱이 5분간 죽어 있었는데 로그가 0줄이라 원인을
    로그만으로는 못 찾았다. 시도마다 한 줄은 남되, heartbeat 마다 도배하지는 않는다.
    """
    async def scenario():
        adapter = _adapter()
        adapter._manual_blocked_reason.return_value = "order in progress; cancel order first"
        service = Ultimate2JoystickService(adapter)

        await service._handle_evdev_event(EV_ABS, 9, 255)   # 첫 시도 -> 사유 1줄
        await service._handle_evdev_event(EV_ABS, 9, 254)   # 계속 당김 -> 도배 금지
        await service._handle_evdev_event(EV_ABS, 9, 0)     # 중립 복귀 -> 래치 해제
        await service._handle_evdev_event(EV_ABS, 9, 255)   # 다시 시도 -> 사유 1줄 더

        adapter._vehicle.um_drive.assert_not_awaited()

    asyncio.run(scenario())

    out = capsys.readouterr().out
    assert out.count("[JOYSTICK ULTIMATE2] refused: order in progress") == 2


def test_ultimate2_refusal_log_clears_once_driving_is_allowed(capsys):
    async def scenario():
        adapter = _adapter()
        adapter._manual_blocked_reason.return_value = "order in progress; cancel order first"
        service = Ultimate2JoystickService(adapter)
        await service._handle_evdev_event(EV_ABS, 9, 255)

        adapter._manual_blocked_reason.return_value = None
        await service._handle_evdev_event(EV_ABS, 9, 0)
        await service._handle_evdev_event(EV_ABS, 9, 255)
        adapter._vehicle.um_drive.assert_awaited_once()

        adapter._manual_blocked_reason.return_value = "order in progress; cancel order first"
        await service._handle_evdev_event(EV_ABS, 9, 254)

    asyncio.run(scenario())

    # 주행이 한 번 통과했으면 다음 차단은 다시 새 사건이므로 또 찍혀야 한다.
    out = capsys.readouterr().out
    assert out.count("[JOYSTICK ULTIMATE2] refused: order in progress") == 2


def test_micro_refusal_is_logged_once_per_attempt(capsys):
    async def scenario():
        adapter = _adapter()
        adapter._manual_blocked_reason.return_value = "busy with loading work"
        service = MicroJoystickService(adapter)

        await service._handle_event(EV_ABS, 1, -32767)
        await service._handle_event(EV_ABS, 1, -32000)
        adapter._vehicle.um_drive.assert_not_awaited()

    asyncio.run(scenario())

    assert capsys.readouterr().out.count("[JOYSTICK MICRO] refused: busy with loading work") == 1


def test_evdev_connect_plays_the_connect_sound(tmp_path, monkeypatch):
    """조이스틱이 붙었는지 화면 없이 알 수 있어야 한다."""
    device = tmp_path / "event9"
    device.write_bytes(b"")
    monkeypatch.setattr(
        joystick_runtime, "USB_DEVICES_ROOT",
        _usb_tree(tmp_path / "usb", ("2dc8", "310b")),
    )

    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)
        reader = asyncio.create_task(
            service._read_evdev_connected(device, guard=True)
        )
        await asyncio.sleep(0.05)
        try:
            adapter.play_joystick_connect_sound.assert_called_once_with()
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    asyncio.run(scenario())
