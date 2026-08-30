#!/usr/bin/env python3
"""Discover Linux joysticks and print raw/mapped input values without driving an AMR."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import time


SCRIPT_ROOT = Path(__file__).resolve().parents[1]


def find_adaptor_root(root: Path) -> Path:
    """Support both source checkout and flattened AMR deployment layouts."""
    candidates = (root / "adaptor", root)
    for candidate in candidates:
        if (candidate / "utils" / "joystick.py").is_file():
            return candidate
    expected = " or ".join(str(path / "utils" / "joystick.py") for path in candidates)
    raise RuntimeError(f"joystick module not found; expected {expected}")


ADAPTOR_ROOT = find_adaptor_root(SCRIPT_ROOT)
sys.path.insert(0, str(ADAPTOR_ROOT))

from utils.joystick import (  # noqa: E402
    JS_EVENT_SIZE,
    choose_preferred_device,
    decode_event,
    mapped_action,
)


BUS_NAMES = {0x0003: "usb", 0x0005: "bluetooth"}
INPUT_EVENT_STRUCT = struct.Struct("llHHi")
EVENT_TYPE_NAMES = {0: "EV_SYN", 1: "EV_KEY", 2: "EV_REL", 3: "EV_ABS", 4: "EV_MSC"}
EVENT_ROLE_NAMES = {
    "controller": "ultimate 2 wireless controller",
    "keyboard": "receiver keyboard",
    "mouse": "receiver mouse",
    "micro": "pro controller",
}
LEARN_BUTTONS = ("R1", "+", "-", "A", "B", "X", "Y")
LEARN_AXES = (
    ("D-pad left", "dpad_x_axis"),
    ("D-pad up", "dpad_y_axis"),
    ("R2", "forward_axis"),
    ("L2", "reverse_axis"),
    ("left stick left", "steering_axis"),
)


def read_text(path: Path, default: str = "unknown") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default


def joystick_info(device: Path) -> dict[str, str]:
    sys_device = Path("/sys/class/input") / device.name / "device"
    bus_raw = read_text(sys_device / "id" / "bustype", "0")
    try:
        bus = BUS_NAMES.get(int(bus_raw, 16), f"bus-0x{bus_raw}")
    except ValueError:
        bus = "unknown"
    return {
        "device": str(device),
        "name": read_text(sys_device / "name"),
        "connection": bus,
        "vendor": read_text(sys_device / "id" / "vendor"),
        "product": read_text(sys_device / "id" / "product"),
    }


def print_devices() -> list[Path]:
    devices = sorted(Path("/dev/input").glob("js*"))
    bluetooth = sorted(Path("/sys/class/bluetooth").glob("hci*"))
    print("Bluetooth adapter:", ", ".join(item.name for item in bluetooth) or "not detected")
    # Reading every USB device's sysfs attributes can block indefinitely when
    # a device/driver is wedged. lsusb gives us the same discovery signal and
    # the timeout keeps --list usable even with unhealthy unrelated hardware.
    usb_8bitdo: list[str] = []
    usb_check_note = ""
    try:
        result = subprocess.run(
            ["lsusb", "-d", "2dc8:"],
            capture_output=True,
            check=False,
            text=True,
            timeout=3.0,
        )
        usb_8bitdo = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except FileNotFoundError:
        usb_check_note = "lsusb not installed"
    except subprocess.TimeoutExpired:
        usb_check_note = "lsusb timed out"

    # udev's database remains useful when a USB device makes lsusb/sysfs stall.
    if not usb_8bitdo:
        try:
            result = subprocess.run(
                ["udevadm", "info", "--export-db"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3.0,
            )
            for record in result.stdout.split("\n\n"):
                fields = dict(
                    line[3:].split("=", 1)
                    for line in record.splitlines()
                    if line.startswith("E: ") and "=" in line
                )
                if fields.get("ID_VENDOR_ID", "").lower() != "2dc8":
                    continue
                model = fields.get("ID_MODEL", "8BitDo").replace("_", " ")
                product = fields.get("ID_MODEL_ID", "unknown")
                usb_8bitdo.append(f"{model} id=2dc8:{product} (udev)")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    print(
        "8BitDo USB:",
        "; ".join(dict.fromkeys(usb_8bitdo))
        or (f"not detected ({usb_check_note})" if usb_check_note else "not detected"),
    )
    if any("2dc8:3109" in item.lower() for item in usb_8bitdo):
        print(
            "  3109 receiver mode detected; if the controller is paired but no js device "
            "appears, check USB enumeration/driver binding"
        )
    if not devices:
        print("Joystick: not detected under /dev/input/js*")
    for device in devices:
        info = joystick_info(device)
        print(
            f"Joystick: {info['device']} name={info['name']!r} "
            f"connection={info['connection']} usb_id={info['vendor']}:{info['product']}"
        )
    event_devices = []
    for event_device in sorted(Path("/dev/input").glob("event*")):
        name = read_text(Path("/sys/class/input") / event_device.name / "device" / "name")
        if "8bitdo" in name.lower() or (
            name == "Pro Controller" and read_text(
                Path("/sys/class/input") / event_device.name / "device" / "id" / "vendor", ""
            ).lower() == "057e"
        ):
            event_devices.append(f"{event_device} name={name!r}")
    print("8BitDo event devices:")
    for description in event_devices:
        print(f"  {description}")
    if not event_devices:
        print("  not detected")
    return devices


def _kernel_config() -> tuple[dict[str, str], str]:
    release = os.uname().release
    candidates = (Path("/proc/config.gz"), Path(f"/boot/config-{release}"))
    for path in candidates:
        try:
            if path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
                    lines = stream.readlines()
            else:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        values: dict[str, str] = {}
        for line in lines:
            if line.startswith("CONFIG_") and "=" in line:
                key, value = line.strip().split("=", 1)
                values[key] = value
            elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
                values[line[2:].split(" ", 1)[0]] = "not set"
        return values, str(path)
    return {}, "not available"


def _module_info(name: str) -> tuple[bool, str]:
    loaded = (Path("/sys/module") / name).exists()
    try:
        result = subprocess.run(
            ["modinfo", "-n", name], capture_output=True, text=True, timeout=2.0
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return loaded, "modinfo unavailable"
    path = result.stdout.strip() if result.returncode == 0 else "module file not found"
    return loaded, path


def check_drivers() -> bool:
    release = os.uname().release
    config, config_source = _kernel_config()
    print(f"Kernel: {release}")
    print(f"Kernel config: {config_source}")
    build_dir = Path("/lib/modules") / release / "build"
    print(f"Kernel build headers: {'yes' if build_dir.exists() else 'no'}")

    keys = ("CONFIG_INPUT_EVDEV", "CONFIG_INPUT_JOYSTICK", "CONFIG_INPUT_JOYDEV", "CONFIG_JOYSTICK_XPAD", "CONFIG_INPUT_UINPUT")
    for key in keys:
        print(f"  {key}={config.get(key, 'unknown')}")

    for module in ("xpad", "joydev", "uinput"):
        loaded, location = _module_info(module)
        print(f"Module {module}: loaded={'yes' if loaded else 'no'}; {location}")

    joystick_nodes = sorted(Path("/dev/input").glob("js*"))
    controller_events = []
    for event in sorted(Path("/dev/input").glob("event*")):
        name = read_text(Path("/sys/class/input") / event.name / "device" / "name", "")
        if "ultimate 2 wireless controller" in name.lower():
            controller_events.append(event)
    print("Ultimate 2 js nodes:", ", ".join(map(str, joystick_nodes)) or "none")
    print("Ultimate 2 controller events:", ", ".join(map(str, controller_events)) or "none")

    try:
        tree = subprocess.run(
            ["lsusb", "-t"], capture_output=True, text=True, timeout=3.0
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        tree = ""
    unbound_vendor_interface = any(
        "Class=Vendor Specific Class, Driver=," in line
        for line in tree.splitlines()
    )
    if unbound_vendor_interface:
        print("USB XInput interface: unbound vendor-specific interface detected")

    ready = bool(joystick_nodes or controller_events)
    if ready:
        print("Ultimate 2 runtime: READY")
    else:
        print("Ultimate 2 runtime: NOT READY")
        print("Reason: no Linux joystick/controller input node is available.")
        if config.get("CONFIG_JOYSTICK_XPAD") in {"not set", "unknown"}:
            print("Action: install/build xpad for this exact kernel, or use another supported input path.")
        if config.get("CONFIG_INPUT_JOYDEV") in {"not set", "unknown"}:
            print("Action: enable joydev for the current js-based runtime.")
    return ready


def monitor_event_device(device: Path, timeout: float | None) -> None:
    name = read_text(Path("/sys/class/input") / device.name / "device" / "name")
    print(f"Reading {device} ({name}); press controls, Ctrl-C to stop")
    with device.open("rb", buffering=0) as stream:
        while True:
            if timeout is not None and not select.select([stream], [], [], timeout)[0]:
                print(f"No input for {timeout:g}s; finished")
                return
            data = stream.read(INPUT_EVENT_STRUCT.size)
            if len(data) != INPUT_EVENT_STRUCT.size:
                raise OSError(f"input device disconnected while reading {device}")
            _seconds, _microseconds, event_type, code, value = INPUT_EVENT_STRUCT.unpack(data)
            if event_type == 0:
                continue
            type_name = EVENT_TYPE_NAMES.get(event_type, f"EV_{event_type}")
            state = " pressed" if event_type == 1 and value == 1 else ""
            print(f"{type_name} code={code} value={value}{state}", flush=True)


def find_event_device(role: str) -> Path | None:
    expected = EVENT_ROLE_NAMES[role]
    for device in sorted(Path("/dev/input").glob("event*")):
        name = read_text(Path("/sys/class/input") / device.name / "device" / "name", "")
        is_ultimate = "8bitdo" in name.lower() and expected in name.lower()
        is_micro = role == "micro" and name.lower() == expected
        if is_ultimate or is_micro:
            return device
    return None


def auto_select_device(devices: list[Path]) -> Path | None:
    selected = choose_preferred_device([joystick_info(device) for device in devices])
    return Path(selected["device"]) if selected else None


def load_mapping(path: Path | None) -> dict:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("mapping root must be a JSON object")
    return value


def monitor(
    device: Path,
    mapping: dict,
    timeout: float | None,
    only: str | None = None,
) -> None:
    info = joystick_info(device)
    print(f"Reading {device} ({info['name']}, {info['connection']}); Ctrl-C to stop")
    with device.open("rb", buffering=0) as stream:
        while True:
            if timeout is not None and not select.select([stream], [], [], timeout)[0]:
                print(f"No input for {timeout:g}s; finished")
                return
            data = stream.read(JS_EVENT_SIZE)
            if len(data) != JS_EVENT_SIZE:
                raise OSError(f"joystick disconnected while reading {device}")
            event = decode_event(data)
            if event.initial or (only is not None and event.event_type != only):
                continue
            logical = mapped_action(event, mapping)
            suffix = f" mapped={json.dumps(logical, ensure_ascii=False)}" if logical else ""
            print(
                f"{event.event_type}[{event.number}] value={event.value} "
                f"time={event.timestamp_ms}ms{suffix}",
                flush=True,
            )


def _next_event(stream, timeout: float):
    if not select.select([stream], [], [], timeout)[0]:
        return None
    data = stream.read(JS_EVENT_SIZE)
    if len(data) != JS_EVENT_SIZE:
        raise OSError("joystick disconnected during learning")
    return decode_event(data)


def _capture_button(stream, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = _next_event(stream, min(0.1, deadline - time.monotonic()))
        if event is None:
            continue
        if not event.initial and event.event_type == "button" and event.value == 1:
            return event.number
    return None


def _axis_baseline(stream) -> dict[int, int]:
    baseline: dict[int, int] = {}
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        event = _next_event(stream, min(0.03, deadline - time.monotonic()))
        if event is None:
            continue
        if event.event_type == "axis":
            baseline[event.number] = event.value
    return baseline


def _capture_axis(stream, timeout: float) -> tuple[int, int] | None:
    baseline = _axis_baseline(stream)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = _next_event(stream, min(0.1, deadline - time.monotonic()))
        if event is None or event.initial or event.event_type != "axis":
            continue
        origin = baseline.get(event.number, 0)
        if abs(event.value - origin) >= 16000:
            return event.number, 1 if event.value > origin else -1
    return None


def learn_controls(device: Path, timeout: float) -> None:
    print("\nUltimate 2 input learning")
    print("각 안내가 나오면 해당 키만 한 번 누르세요. q + Enter로 중단합니다.")
    buttons: dict[str, int] = {}
    axes: dict[str, tuple[int, int]] = {}
    with device.open("rb", buffering=0) as stream:
        _axis_baseline(stream)
        for label in LEARN_BUTTONS:
            answer = input(f"\n[{label}] 준비되면 Enter, 건너뛰려면 s: ").strip().lower()
            if answer == "q":
                break
            if answer == "s":
                continue
            print(f"  지금 {label}을 누르세요...", flush=True)
            number = _capture_button(stream, timeout)
            if number is None:
                print("  감지 실패 (연결 또는 독립 버튼 노출 여부 확인)")
            else:
                buttons[label] = number
                print(f"  감지: {label} = button[{number}]")

        for label, field in LEARN_AXES:
            answer = input(
                f"\n[{label}] 중립에서 Enter, 건너뛰려면 s: "
            ).strip().lower()
            if answer == "q":
                break
            if answer == "s":
                continue
            print(f"  지금 {label}을 끝까지 조작하세요...", flush=True)
            result = _capture_axis(stream, timeout)
            if result is None:
                print("  감지 실패")
            else:
                axes[field] = result
                print(f"  감지: {label} = axis[{result[0]}] direction={result[1]:+d}")

    print("\n=== 측정 결과 ===")
    for label in LEARN_BUTTONS:
        if label in buttons:
            print(f"{label:16} button[{buttons[label]}]")
    for label, field in LEARN_AXES:
        if field in axes:
            number, direction = axes[field]
            print(f"{label:16} axis[{number}] direction={direction:+d}")

    print("\n=== extensions.hcl에 복사 ===")
    fields = {
        "stop_button": buttons.get("R1"),
        "speed_up_button": buttons.get("+"),
        "speed_down_button": buttons.get("-"),
        "a_button": buttons.get("A"),
        "b_button": buttons.get("B"),
        "x_button": buttons.get("X"),
        "y_button": buttons.get("Y"),
    }
    print('joystick "ultimate2" {')
    print("  enabled = true")
    for field, value in fields.items():
        if value is not None:
            print(f"  {field} = {value}")
    for field, (number, _direction) in axes.items():
        print(f"  {field} = {number}")
    print("}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list Bluetooth adapters and joysticks")
    parser.add_argument(
        "--driver-check", action="store_true",
        help="check kernel input configuration, modules, binding, and device readiness",
    )
    parser.add_argument("--device", type=Path, help="device to monitor, for example /dev/input/js0")
    parser.add_argument(
        "--event-device", type=Path,
        help="raw Linux event device to monitor, for example /dev/input/event16",
    )
    parser.add_argument(
        "--event-role", choices=tuple(EVENT_ROLE_NAMES),
        help="auto-select an 8BitDo raw event device by role (survives eventN renumbering)",
    )
    parser.add_argument("--mapping", type=Path, help="JSON file mapping axes/buttons to logical actions")
    parser.add_argument("--timeout", type=float, help="exit after this many seconds without input")
    parser.add_argument(
        "--only", choices=("button", "axis"), help="print only one event type"
    )
    parser.add_argument(
        "--learn", action="store_true", help="interactively identify Ultimate 2 controls"
    )
    parser.add_argument(
        "--learn-timeout", type=float, default=10.0,
        help="seconds to wait for each control in --learn mode (default: 10)",
    )
    args = parser.parse_args()

    if args.driver_check:
        return 0 if check_drivers() else 2

    if args.event_device is not None and args.event_role is not None:
        parser.error("--event-device and --event-role cannot be used together")
    if args.event_device is not None or args.event_role is not None:
        event_device = args.event_device
        if args.event_role is not None:
            event_device = find_event_device(args.event_role)
            if event_device is None:
                print(
                    f"No connected 8BitDo {args.event_role} event device found. "
                    "Wake/reconnect the controller and retry.",
                    file=sys.stderr,
                )
                return 2
            print(f"Auto-selected {args.event_role}: {event_device}")
        try:
            assert event_device is not None
            monitor_event_device(event_device, args.timeout)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nStopped")
        return 0

    devices = print_devices() if args.list or args.device is None else []
    if args.list and args.device is None:
        return 0
    device = args.device or auto_select_device(devices)
    if device is None:
        print("No joystick found. Connect/pair it, then retry with --list.", file=sys.stderr)
        return 2
    if args.device is None:
        print(f"Auto-selected joystick: {device}")
    try:
        if args.learn:
            learn_controls(device, args.learn_timeout)
        else:
            monitor(device, load_mapping(args.mapping), args.timeout, args.only)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
