"""Runtime evdev joystick service for safe local AMR manual driving."""

from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
import struct
import time
from typing import Any, Mapping

from utils.joystick import (
    JS_EVENT_SIZE,
    EvdevDpadDrive,
    JoystickEvent,
    chord_direction,
    chord_slot,
    decode_event,
    normalize_axis,
    step_speed_percent,
)


INPUT_EVENT = struct.Struct("llHHi")
EV_KEY = 1
EV_ABS = 3

USB_DEVICES_ROOT = Path("/sys/bus/usb/devices")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def find_micro_event_device(name: str = "Pro Controller") -> Path | None:
    for device in sorted(Path("/dev/input").glob("event*")):
        sys_device = Path("/sys/class/input") / device.name / "device"
        if _read_text(sys_device / "name") != name:
            continue
        if _read_text(sys_device / "id" / "vendor").lower() == "057e" and _read_text(
            sys_device / "id" / "product"
        ).lower() == "2009":
            return device
    return None


def find_ultimate2_joystick(config: Any) -> Path | None:
    for device in sorted(Path("/dev/input").glob("js*")):
        sys_device = Path("/sys/class/input") / device.name / "device"
        name = _read_text(sys_device / "name")
        vendor = _read_text(sys_device / "id" / "vendor").lower()
        product = _read_text(sys_device / "id" / "product").lower()
        if str(config.device_name_contains).lower() not in name.lower():
            continue
        if vendor == str(config.vendor_id).lower() and product == str(config.product_id).lower():
            return device
    return None


def find_xboxdrv_event_device(config: Any) -> Path | None:
    expected_name = str(config.xboxdrv_device_name)
    for device in sorted(Path("/dev/input").glob("event*")):
        sys_device = Path("/sys/class/input") / device.name / "device"
        if _read_text(sys_device / "name") == expected_name:
            return device
    return None


def receiver_present(config: Any, *, root: Path | None = None) -> bool:
    """True while the configured USB receiver id is still enumerated.

    The xboxdrv uinput pad is virtual: it survives the receiver re-enumerating
    (310b awake -> 3109 asleep) and just stops emitting, so a drive held at that
    moment would be re-sent by the heartbeat until the supervisor tears xboxdrv
    down. Watch the real receiver instead. Event silence cannot be the signal —
    a trigger held steady is silent too, and stopping on that would make the
    fallback path unusable.

    Unreadable entries count as absent so an operating guard fails towards a
    stop; ``run`` probes ``root`` once up front so a host without USB sysfs
    disables the guard instead of never being allowed to drive.
    """
    vendor = str(config.vendor_id).lower()
    product = str(config.product_id).lower()
    try:
        devices = sorted((USB_DEVICES_ROOT if root is None else root).iterdir())
    except OSError:
        return False
    for device in devices:
        if _read_text(device / "idVendor").lower() != vendor:
            continue
        if _read_text(device / "idProduct").lower() == product:
            return True
    return False


class JoystickActionDispatcher:
    """Fire configured slot chords as adapter instant actions."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.config = adapter.config.joystick
        self.enabled = bool(getattr(self.config, "actions_enabled", False))
        self.slots = {
            int(getattr(entry, "slot")): entry
            for entry in getattr(self.config, "actions", ())
            if bool(getattr(entry, "enabled", True))
        }

    def fire_slot(self, slot: int) -> None:
        if not self.enabled:
            return
        entry = self.slots.get(slot)
        if entry is None:
            print(f"[JOYSTICK ACTION] slot {slot} is unmapped or disabled")
            return
        action = str(getattr(entry, "action", "")).strip()
        if not action:
            return
        self._submit(action, getattr(entry, "parameters", None), f"slot {slot}")

    def _submit(
        self, action_type: str, parameters: Mapping[str, Any] | None, label: str
    ) -> None:
        try:
            self.adapter.submit_local_instant_action(
                action_type, parameters, source="joystick"
            )
        except Exception as exc:  # noqa: BLE001 - a bad slot must not kill driving
            print(f"[JOYSTICK ACTION] {label} -> {action_type} failed: {exc}")
            return
        print(f"[JOYSTICK ACTION] {label} -> {action_type}")


def _trigger_value(value: int, threshold: float) -> float:
    normalized = max(0.0, min(1.0, (value + 32767) / 65534.0))
    if normalized <= threshold:
        return 0.0
    return (normalized - threshold) / (1.0 - threshold)


def _evdev_trigger_value(value: int, threshold: float) -> float:
    normalized = max(0.0, min(1.0, value / 255.0))
    if normalized <= threshold:
        return 0.0
    return (normalized - threshold) / (1.0 - threshold)


def _play_connect_sound(adapter: Any) -> None:
    """조이스틱이 붙었을 때 연결음을 울린다.

    화면 없는 로봇이라 컨트롤러가 실제로 붙었는지 확인할 방법이 로그뿐이었다.
    SoundPlayer 호출은 블로킹이라 어댑터가 워커 스레드로 넘겨야 하므로 여기서는
    위임만 한다. 훅이 없는 어댑터에서도 주행은 그대로 돼야 하니 없으면 넘어간다.
    """
    play = getattr(adapter, "play_joystick_connect_sound", None)
    if play is None:
        return
    try:
        play()
    except Exception as exc:  # noqa: BLE001 - 알림음 때문에 조이스틱이 죽으면 안 된다
        print(f"[JOYSTICK] connect sound failed: {exc}")


class MicroJoystickService:
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.config = adapter.config.joystick
        self.dpad = EvdevDpadDrive(
            x_code=int(self.config.micro_dpad_x_code),
            y_code=int(self.config.micro_dpad_y_code),
            neutral_threshold=int(self.config.micro_neutral_threshold),
        )
        self._action = "manual_stop"
        self._moving = False
        self._last_drive_at = 0.0
        # 마지막으로 찍은 거부 사유. 같은 사유를 D-pad 이벤트마다 다시 찍지 않기 위함.
        self._refused_reason: str | None = None

    async def _stop(self, reason: str) -> None:
        was_moving = self._moving
        self._moving = False
        self._action = "manual_stop"
        self.adapter._manual_control_active = False
        self.adapter._cancel_manual_drive_watchdog()
        if was_moving and self.adapter._vehicle is not None:
            try:
                await self.adapter._vehicle.um_stop()
            except Exception as exc:
                print(f"[JOYSTICK MICRO] stop failed ({reason}): {exc}")
                return
        if was_moving:
            print(f"[JOYSTICK MICRO] stopped ({reason})")

    async def _drive(self, action: str) -> None:
        reason = self.adapter._manual_blocked_reason()
        if reason is not None or self.adapter._vehicle is None:
            reason = reason or "vehicle unavailable"
            self._log_refusal(reason)
            await self._stop(reason)
            return
        mc = self.adapter.config.manual_control
        trans = float(mc.drive_trans) if action == "forward" else (
            -float(mc.drive_trans) if action == "reverse" else 0.0
        )
        rot = float(mc.drive_rot) if action == "turn_left" else (
            -float(mc.drive_rot) if action == "turn_right" else 0.0
        )
        try:
            await self.adapter._vehicle.um_drive(
                trans, rot, float(mc.drive_speed), float(mc.drive_lat)
            )
        except Exception as exc:
            await self._stop(f"drive failed: {exc}")
            return
        self._moving = True
        self._last_drive_at = time.monotonic()
        self._refused_reason = None
        self.adapter._manual_control_active = True
        self.adapter._arm_manual_drive_watchdog()

    def _log_refusal(self, reason: str) -> None:
        """거부 사유를 시도당 한 줄만 남긴다.

        차단은 정지 상태에서 일어나 _stop 이 아무것도 찍지 않는다. 그래서 조이스틱이
        죽어도 로그가 0줄이라 원인을 못 찾는다 (2026-08-22 .62 실측). 반대로 이벤트마다
        찍으면 journal 이 도배되므로 사유가 바뀔 때만 찍는다.
        """
        if self._refused_reason == reason:
            return
        self._refused_reason = reason
        print(f"[JOYSTICK MICRO] refused: {reason}")

    async def _handle_event(self, event_type: int, code: int, value: int) -> None:
        if event_type == EV_KEY and code == int(self.config.micro_stop_button) and value == 1:
            await self._stop("R button")
            return
        if event_type != EV_ABS:
            return
        action = self.dpad.update(code, value)
        if action is None:
            return
        self._action = action
        if action == "manual_stop":
            # 중립으로 돌아왔으면 다음 시도는 새 사건이므로 사유를 다시 찍게 한다.
            self._refused_reason = None
            await self._stop("D-pad neutral/diagonal")
        else:
            await self._drive(action)

    async def _read_connected(self, device: Path) -> None:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        print(f"[JOYSTICK MICRO] connected: {device}")
        _play_connect_sound(self.adapter)
        try:
            while True:
                try:
                    data = os.read(fd, INPUT_EVENT.size * 32)
                except BlockingIOError:
                    data = b""
                except OSError as exc:
                    if exc.errno in {errno.ENODEV, errno.ENOENT, errno.EIO}:
                        return
                    raise
                for offset in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
                    _sec, _usec, event_type, code, value = INPUT_EVENT.unpack_from(data, offset)
                    await self._handle_event(event_type, code, value)
                heartbeat = float(self.adapter.config.manual_control.heartbeat_ms) / 1000.0
                if self._action != "manual_stop" and time.monotonic() - self._last_drive_at >= heartbeat:
                    await self._drive(self._action)
                await asyncio.sleep(0.01)
        finally:
            os.close(fd)
            await self._stop("device disconnected")
            print(f"[JOYSTICK MICRO] disconnected: {device}")

    async def run(self) -> None:
        retry = max(0.1, float(self.config.micro_reconnect_sec))
        while True:
            device = find_micro_event_device(str(self.config.micro_device_name))
            if device is None:
                await asyncio.sleep(retry)
                continue
            try:
                await self._read_connected(device)
            except PermissionError:
                print(f"[JOYSTICK MICRO] permission denied: {device}; add service user to input group")
                await asyncio.sleep(retry)
            except asyncio.CancelledError:
                await self._stop("service shutdown")
                raise
            except Exception as exc:
                print(f"[JOYSTICK MICRO] input error: {exc}")
                await self._stop("input error")
                await asyncio.sleep(retry)


class Ultimate2JoystickService:
    """Read Ultimate 2 js events and drive with analogue triggers and steering."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.config = adapter.config.joystick
        self.forward = 0.0
        self.reverse = 0.0
        self.steering = 0.0
        self._moving = False
        self._blocked_until_neutral = False
        self._last_drive_at = 0.0
        # 마지막으로 찍은 거부 사유. 트리거를 쥔 동안 같은 줄을 반복하지 않기 위함.
        self._refused_reason: str | None = None
        self.actions = JoystickActionDispatcher(adapter)
        self._speed_percent = int(self.config.speed_start_percent)
        self._dpad_x = 0
        self._dpad_y = 0
        self._js_faces = {
            int(self.config.a_button): "a",
            int(self.config.b_button): "b",
            int(self.config.x_button): "x",
            int(self.config.y_button): "y",
        }
        self._evdev_faces = {
            int(self.config.xboxdrv_a_button_code): "a",
            int(self.config.xboxdrv_b_button_code): "b",
            int(self.config.xboxdrv_x_button_code): "x",
            int(self.config.xboxdrv_y_button_code): "y",
        }

    def _fire_chord(self, face: str) -> None:
        """Fire the slot for a face button pressed while a D-pad direction is held."""
        if not self._neutral():
            # Slots run motors, clamps and charging. Firing one on a robot the
            # operator is still steering is the wrong moment, so require every
            # drive input released — not merely that the wheels stopped, which a
            # latched stop already satisfies with the trigger still held.
            print(f"[JOYSTICK ULTIMATE2] '{face}' slot action ignored; controls not neutral")
            return
        slot = chord_slot(chord_direction(self._dpad_x, self._dpad_y), face)
        if slot is None:
            return
        self.actions.fire_slot(slot)

    async def step_speed(self, direction: int) -> None:
        """Scale manual driving with -/+ and apply it without waiting.

        Not gated by ``actions_enabled``: that switch guards slots that run
        motors, clamps and charging. Changing the scale of a drive the operator
        is already performing carries none of that, and gating it would leave
        every robot with dead -/+ buttons until an unrelated switch is flipped.
        """
        target = step_speed_percent(
            self._speed_percent,
            direction,
            int(self.config.speed_step_percent),
            int(self.config.speed_min_percent),
            int(self.config.speed_max_percent),
        )
        if target == self._speed_percent:
            return
        self._speed_percent = target
        print(f"[JOYSTICK ULTIMATE2] drive speed {target}%")
        if self._moving:
            await self._apply()

    def _neutral(self) -> bool:
        return self.forward == 0.0 and self.reverse == 0.0 and self.steering == 0.0

    async def _stop(self, reason: str, *, latch: bool = False) -> None:
        was_moving = self._moving
        self._moving = False
        if latch:
            self._blocked_until_neutral = True
        self.adapter._manual_control_active = False
        self.adapter._cancel_manual_drive_watchdog()
        if was_moving and self.adapter._vehicle is not None:
            try:
                await self.adapter._vehicle.um_stop()
            except Exception as exc:
                print(f"[JOYSTICK ULTIMATE2] stop failed ({reason}): {exc}")
                return
        if was_moving:
            print(f"[JOYSTICK ULTIMATE2] stopped ({reason})")

    async def _apply(self) -> None:
        if self.forward > 0.0 and self.reverse > 0.0:
            await self._stop("simultaneous triggers", latch=True)
            return
        if self._blocked_until_neutral:
            if self._neutral():
                self._blocked_until_neutral = False
                # 중립으로 돌아왔으면 다음 시도는 새 사건이므로 사유를 다시 찍게 한다.
                self._refused_reason = None
            await self._stop("waiting for neutral")
            return
        if self._neutral():
            self._refused_reason = None
            await self._stop("controls neutral")
            return
        reason = self.adapter._manual_blocked_reason()
        if reason is not None or self.adapter._vehicle is None:
            reason = reason or "vehicle unavailable"
            self._log_refusal(reason)
            await self._stop(reason, latch=True)
            return
        mc = self.adapter.config.manual_control
        scale = self._speed_percent / 100.0
        throttle = self.forward - self.reverse
        trans = float(mc.drive_trans) * scale * throttle
        rot = -float(mc.drive_rot) * scale * self.steering
        # `speed` follows the same demand as trans/rot. Leaving it at the full
        # budget made a half-pulled trigger ask for half the velocity while
        # still handing the robot the whole speed allowance. The larger of the
        # two inputs is what counts: a stick-only spin has no trigger to read,
        # and steering gently mid-drive must not throttle the trigger back down.
        demand = max(abs(throttle), abs(self.steering))
        speed = float(mc.drive_speed) * scale * demand
        try:
            await self.adapter._vehicle.um_drive(
                trans, rot, speed, float(mc.drive_lat)
            )
        except Exception as exc:
            await self._stop(f"drive failed: {exc}", latch=True)
            return
        self._moving = True
        self._last_drive_at = time.monotonic()
        self._refused_reason = None
        self.adapter._manual_control_active = True
        self.adapter._arm_manual_drive_watchdog()

    def _log_refusal(self, reason: str) -> None:
        """거부 사유를 시도당 한 줄만 남긴다.

        차단은 정지 상태에서 일어나 _stop 이 아무것도 찍지 않는다(was_moving False).
        그래서 조이스틱이 죽어도 journal 에 단서가 0줄이었다 (2026-08-22 .62 실측:
        23:14~23:19 무반응 구간에 JOYSTICK 로그 없음). 반대로 heartbeat 마다 찍으면
        도배되므로 사유가 바뀔 때만 찍는다.
        """
        if self._refused_reason == reason:
            return
        self._refused_reason = reason
        print(f"[JOYSTICK ULTIMATE2] refused: {reason}")

    async def _handle_event(self, event: JoystickEvent) -> None:
        if event.initial:
            return
        if event.event_type == "button":
            if event.value != 1:
                return
            if event.number == int(self.config.stop_button):
                await self._stop("R1 button", latch=True)
            elif event.number == int(self.config.speed_up_button):
                await self.step_speed(1)
            elif event.number == int(self.config.speed_down_button):
                await self.step_speed(-1)
            elif event.number in self._js_faces:
                self._fire_chord(self._js_faces[event.number])
            return
        if event.event_type != "axis":
            return
        if event.number == int(self.config.dpad_x_axis):
            self._dpad_x = event.value
            return
        if event.number == int(self.config.dpad_y_axis):
            self._dpad_y = event.value
            return
        if event.number == int(self.config.forward_axis):
            self.forward = _trigger_value(event.value, float(self.config.trigger_threshold))
        elif event.number == int(self.config.reverse_axis):
            self.reverse = _trigger_value(event.value, float(self.config.trigger_threshold))
        elif event.number == int(self.config.steering_axis):
            self.steering = normalize_axis(event.value, float(self.config.steering_deadzone))
        else:
            return
        await self._apply()

    async def _handle_evdev_event(self, event_type: int, code: int, value: int) -> None:
        if event_type == EV_KEY:
            if value != 1:
                return
            if code == int(self.config.xboxdrv_stop_button_code):
                await self._stop("R1 button", latch=True)
            elif code == int(self.config.xboxdrv_speed_up_button_code):
                await self.step_speed(1)
            elif code == int(self.config.xboxdrv_speed_down_button_code):
                await self.step_speed(-1)
            elif code in self._evdev_faces:
                self._fire_chord(self._evdev_faces[code])
            return
        if event_type != EV_ABS:
            return
        if code == int(self.config.xboxdrv_dpad_x_code):
            self._dpad_x = value
            return
        if code == int(self.config.xboxdrv_dpad_y_code):
            self._dpad_y = value
            return
        if code == int(self.config.xboxdrv_forward_axis_code):
            self.forward = _evdev_trigger_value(value, float(self.config.trigger_threshold))
        elif code == int(self.config.xboxdrv_reverse_axis_code):
            self.reverse = _evdev_trigger_value(value, float(self.config.trigger_threshold))
        elif code == int(self.config.xboxdrv_steering_axis_code):
            self.steering = normalize_axis(value, float(self.config.steering_deadzone))
        else:
            return
        await self._apply()

    async def _read_connected(self, device: Path) -> None:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        print(f"[JOYSTICK ULTIMATE2] connected: {device}")
        _play_connect_sound(self.adapter)
        try:
            while True:
                try:
                    data = os.read(fd, JS_EVENT_SIZE * 64)
                except BlockingIOError:
                    data = b""
                except OSError as exc:
                    if exc.errno in {errno.ENODEV, errno.ENOENT, errno.EIO}:
                        return
                    raise
                for offset in range(0, len(data) - JS_EVENT_SIZE + 1, JS_EVENT_SIZE):
                    await self._handle_event(decode_event(data[offset:offset + JS_EVENT_SIZE]))
                heartbeat = float(self.adapter.config.manual_control.heartbeat_ms) / 1000.0
                if self._moving and time.monotonic() - self._last_drive_at >= heartbeat:
                    await self._apply()
                await asyncio.sleep(0.01)
        finally:
            os.close(fd)
            await self._stop("device disconnected", latch=True)
            print(f"[JOYSTICK ULTIMATE2] disconnected: {device}")

    async def _read_evdev_connected(self, device: Path, *, guard: bool = False) -> None:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        print(f"[JOYSTICK ULTIMATE2] connected via xboxdrv evdev: {device}")
        _play_connect_sound(self.adapter)
        try:
            while True:
                try:
                    data = os.read(fd, INPUT_EVENT.size * 64)
                except BlockingIOError:
                    data = b""
                except OSError as exc:
                    if exc.errno in {errno.ENODEV, errno.ENOENT, errno.EIO}:
                        return
                    raise
                for offset in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
                    _sec, _usec, event_type, code, value = INPUT_EVENT.unpack_from(data, offset)
                    await self._handle_evdev_event(event_type, code, value)
                heartbeat = float(self.adapter.config.manual_control.heartbeat_ms) / 1000.0
                if self._moving and time.monotonic() - self._last_drive_at >= heartbeat:
                    if guard and not receiver_present(self.config):
                        await self._stop("receiver gone", latch=True)
                        return
                    await self._apply()
                await asyncio.sleep(0.01)
        finally:
            os.close(fd)
            await self._stop("device disconnected", latch=True)
            print(f"[JOYSTICK ULTIMATE2] disconnected: {device}")

    async def run(self) -> None:
        retry = max(0.1, float(self.config.joystick_reconnect_sec))
        # Probe once, not per drive: a host without USB sysfs must fall back to
        # an unguarded read rather than be stopped on every heartbeat.
        guard = bool(getattr(self.config, "xboxdrv_receiver_guard", True))
        if guard and not USB_DEVICES_ROOT.is_dir():
            print(
                f"[JOYSTICK ULTIMATE2] {USB_DEVICES_ROOT} is unreadable; "
                "running the xboxdrv fallback without the receiver guard"
            )
            guard = False
        while True:
            device = find_ultimate2_joystick(self.config)
            use_evdev = False
            if device is None:
                device = find_xboxdrv_event_device(self.config)
                use_evdev = device is not None
                if device is None:
                    await asyncio.sleep(retry)
                    continue
            try:
                if use_evdev:
                    await self._read_evdev_connected(device, guard=guard)
                else:
                    await self._read_connected(device)
            except PermissionError:
                print(f"[JOYSTICK ULTIMATE2] permission denied: {device}; add service user to input group")
                await asyncio.sleep(retry)
            except asyncio.CancelledError:
                await self._stop("service shutdown", latch=True)
                raise
            except Exception as exc:
                print(f"[JOYSTICK ULTIMATE2] input error: {exc}")
                await self._stop("input error", latch=True)
                await asyncio.sleep(retry)


def start_joystick_service(adapter: Any) -> asyncio.Task[Any] | None:
    config = getattr(adapter.config, "joystick", None)
    if config is None or not bool(getattr(config, "enabled", False)):
        return None
    if bool(getattr(config, "micro_enabled", False)):
        return asyncio.create_task(MicroJoystickService(adapter).run())
    if bool(getattr(config, "ultimate2_enabled", False)):
        return asyncio.create_task(Ultimate2JoystickService(adapter).run())
    return None
