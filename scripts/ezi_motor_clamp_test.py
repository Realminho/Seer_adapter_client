#!/usr/bin/env python3
"""Read-mostly diagnostic for the EZI clamp motor.

Answers the question "the adapter said clamp finished but the motor did not
move — why?" by showing the drive's own state and the per-command result
codes the adapter currently throws away (see ``utils/ezi_motor.py`` and
``adapter_jibot._execute_clamp_action``).

By default it ONLY reads (board info, axis status flags, actual position) and
moves nothing. Servo toggling, alarm reset and motion happen only when the
matching flag is passed, so it is safe to run first with no flags.

Example (read-only):
    .venv/bin/python scripts/ezi_motor_clamp_test.py 10.8.8.2

Full bring-up check (reset alarm, enable servo, move, verify it moved):
    .venv/bin/python scripts/ezi_motor_clamp_test.py 10.8.8.2 \
        --alarm-reset --enable-servo --move 16000 --speed 10000
"""
import argparse
import asyncio
import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (SCRIPT_ROOT, SCRIPT_ROOT / "adaptor"):
    if (candidate / "utils" / "ezi_motor.py").exists():
        sys.path.insert(0, str(candidate))
        break

from utils.ezi_motor import EziMotorClient  # noqa: E402


# Flags worth calling out by name; everything else that is set is listed too.
KEY_FLAGS = (
    "FFLAG_SERVOON",
    "FFLAG_ERRORALL",
    "FFLAG_EMGSTOP",
    "FFLAG_ORIGINRETOK",
    "FFLAG_INPOSITION",
    "FFLAG_MOTIONING",
)


def _comm(result):
    """Pull the drive's per-command result code out of a response dict."""
    if result is None:
        return None
    return result.get("communication_status")


async def show_status(motor, label):
    status = await motor.get_axis_status()
    pos = await motor.get_actual_position()

    if status is None:
        print(f"[{label}] axis_status: NO RESPONSE (timeout / comms down)")
    else:
        flags = status["active_flags"]
        named = " ".join(f"{name}={int(flags.get(name, False))}" for name in KEY_FLAGS)
        others = sorted(
            n for n, on in flags.items() if on and n not in KEY_FLAGS
        )
        print(f"[{label}] flags {status['status_flags_hex']} :: {named}")
        if others:
            print(f"[{label}] also set: {', '.join(others)}")

    if pos is None:
        print(f"[{label}] position: NO RESPONSE")
    else:
        print(f"[{label}] position: {pos['position']}")
    return status, pos


async def main():
    parser = argparse.ArgumentParser(
        description="Diagnose the EZI clamp motor (servo/alarm/origin/move).",
    )
    parser.add_argument("ip", help="EZI motor IP (config ezi_config.ezi_motor), e.g. 10.8.8.2")
    parser.add_argument("--port", type=int, default=3002)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--alarm-reset", action="store_true", help="Send FAS_ServoAlarmReset first.")
    parser.add_argument("--enable-servo", action="store_true", help="Servo ON before moving.")
    parser.add_argument("--disable-servo", action="store_true", help="Servo OFF at the end.")
    parser.add_argument("--move", type=int, default=None, metavar="POS",
                        help="Absolute target position (pulses). Omit to only read state.")
    parser.add_argument("--speed", type=int, default=10000,
                        help="Move speed in pps (recommended 10000~20000; default: 10000).")
    parser.add_argument("--settle", type=float, default=2.0,
                        help="Seconds to wait after a move before re-reading (default: 2).")
    args = parser.parse_args()

    motor = EziMotorClient(args.ip, port=args.port, timeout=args.timeout)
    try:
        print(f"udp target={args.ip}:{args.port} timeout={args.timeout}s")

        info = await motor.get_board_info()
        if info is None:
            print("board_info: NO RESPONSE — motor unreachable (wrong IP/port or power off)")
        else:
            print(f"board_info: status={info['status']} description={info['description']!r}")

        await show_status(motor, "before")

        if args.alarm_reset:
            res = await motor.alarm_reset()
            print(f"alarm_reset: comm_status={_comm(res)} ({res})")

        if args.enable_servo:
            res = await motor.servo_enable(True)
            print(f"servo_enable(True): comm_status={_comm(res)} ({res})")
            await asyncio.sleep(0.5)
            await show_status(motor, "after-servo-on")

        if args.move is not None:
            _, pos_before = await show_status(motor, "pre-move")
            res = await motor.move_single_axis_abs_pos(args.move, args.speed)
            comm = _comm(res)
            if res is None:
                print(f"move -> {args.move}: NO RESPONSE (timeout) — command not acknowledged")
            elif comm not in (0, None):
                print(f"move -> {args.move}: REJECTED comm_status={comm} "
                      "(nonzero usually = servo off / alarm / origin not set / in motion)")
            else:
                print(f"move -> {args.move}: accepted comm_status={comm}")
            await asyncio.sleep(args.settle)
            _, pos_after = await show_status(motor, "post-move")

            if pos_before is not None and pos_after is not None:
                delta = pos_after["position"] - pos_before["position"]
                moved = "MOVED" if delta != 0 else "DID NOT MOVE"
                print(f"result: {moved} (delta={delta}, target={args.move})")

        if args.disable_servo:
            res = await motor.servo_enable(False)
            print(f"servo_enable(False): comm_status={_comm(res)} ({res})")
    finally:
        await motor.close()


if __name__ == "__main__":
    asyncio.run(main())
