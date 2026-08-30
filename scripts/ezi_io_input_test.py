#!/usr/bin/env python3
import argparse
import asyncio
import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (SCRIPT_ROOT, SCRIPT_ROOT / "adaptor"):
    if (candidate / "utils" / "ezi_io.py").exists():
        sys.path.insert(0, str(candidate))
        break

from utils.ezi_io import EZIIOClient  # noqa: E402


DEFAULT_SENSOR_PINS = [8, 9, 10, 11, 12, 13]


def format_bits(bits):
    return " ".join(f"{index + 1}:{bit}" for index, bit in enumerate(bits))


def parse_pins(value):
    pins = []
    for raw_pin in value.split(","):
        raw_pin = raw_pin.strip()
        if not raw_pin:
            continue
        pin = int(raw_pin)
        if pin < 0:
            raise argparse.ArgumentTypeError("pins must be zero or greater")
        pins.append(pin)
    if not pins:
        raise argparse.ArgumentTypeError("at least one pin is required")
    return pins


def format_sensor_states(bits, pins):
    states = []
    for slot, pin in enumerate(pins, start=1):
        state = "on" if pin < len(bits) and int(bits[pin]) == 1 else "off"
        states.append(f"slot{slot}={state}")
    return " ".join(states)


async def main():
    parser = argparse.ArgumentParser(description="Test EZI IO input reads.")
    parser.add_argument("ip", help="EZI IO module IP address, e.g. 10.8.8.87")
    parser.add_argument("--port", type=int, default=3002)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--pins",
        type=parse_pins,
        default=DEFAULT_SENSOR_PINS,
        help="Comma-separated zero-based sensor input pins. Default: 8,9,10,11,12,13",
    )
    args = parser.parse_args()

    client = EZIIOClient(args.ip, port=args.port, timeout=args.timeout)
    try:
        await client.connect()
        print(f"connected udp target={args.ip}:{args.port} timeout={args.timeout}s")

        info = await client.get_board_info()
        if info is None:
            print("board_info: no response")
        else:
            print(f"board_info: status={info['status']} description={info['description']!r}")

        for attempt in range(1, args.count + 1):
            response = await client.get_input()
            if response is None:
                print(f"input[{attempt}]: no response or invalid payload")
            else:
                print(
                    f"input[{attempt}]: status={response['comm_status']} "
                    f"raw=0x{response['input_raw']:08x} "
                    f"bits={format_bits(response['inputs'])}"
                )
                print(
                    f"sensors[{attempt}]: pins={','.join(str(pin) for pin in args.pins)} "
                    f"{format_sensor_states(response['inputs'], args.pins)}"
                )
            if attempt < args.count:
                await asyncio.sleep(args.interval)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
