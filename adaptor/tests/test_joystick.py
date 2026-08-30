import struct

import pytest

from utils.joystick import (
    EvdevDpadDrive,
    choose_preferred_device,
    chord_direction,
    chord_slot,
    decode_event,
    mapped_action,
    mapped_chord_action,
    normalize_axis,
    step_speed_percent,
    validate_joystick_actions,
)


def test_micro_evdev_dpad_drive_and_release():
    drive = EvdevDpadDrive(x_code=0, y_code=1, neutral_threshold=1000)

    assert drive.update(1, -32767) == "forward"
    assert drive.update(1, -241) == "manual_stop"
    assert drive.update(0, 32767) == "turn_right"
    assert drive.update(0, 241) == "manual_stop"
    assert drive.update(1, 32767) == "reverse"
    assert drive.update(1, -241) == "manual_stop"
    assert drive.update(0, -32767) == "turn_left"
    assert drive.update(0, 241) == "manual_stop"


def test_micro_evdev_dpad_rejects_diagonal_and_unrelated_codes():
    drive = EvdevDpadDrive()

    assert drive.update(99, 1) is None
    assert drive.update(1, -32767) == "forward"
    assert drive.update(0, 32767) == "manual_stop"
    assert drive.update(0, 0) == "manual_stop"
    assert drive.update(1, 0) == "manual_stop"
    assert drive.update(0, -32767) == "turn_left"


def test_decode_axis_event_and_initial_flag():
    event = decode_event(struct.pack("IhBB", 1234, -16384, 0x82, 1))
    assert event.timestamp_ms == 1234
    assert event.event_type == "axis"
    assert event.number == 1
    assert event.value == -16384
    assert event.initial is True


def test_decode_rejects_partial_record():
    with pytest.raises(ValueError, match="expected 8 bytes"):
        decode_event(b"short")


def test_normalize_axis_applies_deadzone_and_rescales():
    assert normalize_axis(2000, deadzone=0.1) == 0.0
    assert normalize_axis(32767, deadzone=0.1) == 1.0
    assert normalize_axis(-32768, deadzone=0.1) == -1.0


def test_mapping_supports_axis_invert_and_buttons():
    mapping = {
        "deadzone": 0.1,
        "axes": {"1": {"action": "drive", "invert": True}},
        "buttons": {"1": "manual_stop"},
    }
    axis = decode_event(struct.pack("IhBB", 1, -32767, 2, 1))
    button = decode_event(struct.pack("IhBB", 2, 1, 1, 1))
    assert mapped_action(axis, mapping) == {
        "action": "drive", "value": 1.0, "input": "axes", "number": 1
    }
    assert mapped_action(button, mapping) == {
        "action": "manual_stop", "value": 1, "input": "buttons", "number": 1
    }


def test_choose_preferred_device_selects_8bitdo_usb_receiver():
    devices = [
        {"device": "/dev/input/js0", "name": "Bluetooth Pad", "connection": "bluetooth", "vendor": "1234"},
        {"device": "/dev/input/js1", "name": "8BitDo Ultimate 2", "connection": "usb", "vendor": "2dc8"},
        {"device": "/dev/input/js2", "name": "Generic USB Pad", "connection": "usb", "vendor": "9999"},
    ]
    assert choose_preferred_device(devices)["device"] == "/dev/input/js1"


def test_choose_preferred_device_falls_back_to_usb_then_device_name():
    devices = [
        {"device": "/dev/input/js2", "name": "Wireless Pad", "connection": "bluetooth"},
        {"device": "/dev/input/js1", "name": "USB Pad", "connection": "usb"},
    ]
    assert choose_preferred_device(devices)["device"] == "/dev/input/js1"
    assert choose_preferred_device([]) is None


def test_mapping_normalizes_xinput_trigger_as_zero_to_one():
    mapping = {"axes": {"2": {"action": "deadman", "trigger": True, "deadzone": 0.1}}}
    released = decode_event(struct.pack("IhBB", 1, -32767, 2, 2))
    pressed = decode_event(struct.pack("IhBB", 2, 32767, 2, 2))
    assert mapped_action(released, mapping)["value"] == 0.0
    assert mapped_action(pressed, mapping)["value"] == 1.0


def test_dpad_face_button_chords_produce_sixteen_unique_actions():
    mapping = {
        "chords": {
            f"dpad_{direction}+{button}": f"action_{index}"
            for index, (direction, button) in enumerate(
                (direction, button)
                for direction in ("up", "right", "down", "left")
                for button in ("a", "b", "x", "y")
            )
        }
    }
    actions = {
        mapped_chord_action(direction, button, mapping)
        for direction in ("up", "right", "down", "left")
        for button in ("a", "b", "x", "y")
    }
    assert actions == {f"action_{index}" for index in range(16)}
    assert mapped_chord_action("centre", "a", mapping) is None


@pytest.mark.parametrize("scale", [1, 32767])
def test_chord_direction_reads_both_backend_scales(scale):
    # joydev reports the hat as +/-32767, xboxdrv's uinput pad as +/-1, and both
    # sit at exactly 0 when released.
    assert chord_direction(0, -scale) == "up"
    assert chord_direction(scale, 0) == "right"
    assert chord_direction(0, scale) == "down"
    assert chord_direction(-scale, 0) == "left"
    assert chord_direction(0, 0) is None
    # A diagonal is ambiguous: picking either axis would fire the wrong slot.
    assert chord_direction(scale, -scale) is None
    assert chord_direction(-scale, scale) is None


def test_chord_slot_matches_the_documented_slot_order():
    assert [chord_slot("up", face) for face in ("a", "b", "x", "y")] == [1, 2, 3, 4]
    assert [chord_slot("right", face) for face in ("a", "b", "x", "y")] == [5, 6, 7, 8]
    assert [chord_slot("down", face) for face in ("a", "b", "x", "y")] == [9, 10, 11, 12]
    assert [chord_slot("left", face) for face in ("a", "b", "x", "y")] == [13, 14, 15, 16]
    assert chord_slot(None, "a") is None
    assert chord_slot("up", None) is None
    assert chord_slot("centre", "a") is None
    assert chord_slot("up", "z") is None


def test_enabled_joystick_actions_are_validated_against_registry():
    class Registry:
        def has(self, action):
            return action == "knownRecipe"

    action = type("Action", (), {"slot": 1, "action": "knownRecipe", "enabled": True})()
    config = type("Joystick", (), {"enabled": True, "actions": [action]})()
    validate_joystick_actions(config, Registry())
    action.action = "missing"
    with pytest.raises(ValueError, match="slot 1.*missing"):
        validate_joystick_actions(config, Registry())

    config.enabled = False
    validate_joystick_actions(config, Registry())


def test_step_speed_percent_steps_both_ways():
    assert step_speed_percent(60, 1, 20, 20, 100) == 80
    assert step_speed_percent(60, -1, 20, 20, 100) == 40


def test_step_speed_percent_clamps_to_the_configured_range():
    assert step_speed_percent(90, 1, 20, 20, 100) == 100
    assert step_speed_percent(30, -1, 20, 20, 100) == 20


def test_step_speed_percent_at_a_bound_returns_the_current_value():
    # The caller uses "unchanged" to mean "do not re-send a drive command".
    assert step_speed_percent(100, 1, 20, 20, 100) == 100
    assert step_speed_percent(20, -1, 20, 20, 100) == 20


def test_step_speed_percent_ignores_a_zero_direction_or_step():
    assert step_speed_percent(60, 0, 20, 20, 100) == 60
    assert step_speed_percent(60, 1, 0, 20, 100) == 60
