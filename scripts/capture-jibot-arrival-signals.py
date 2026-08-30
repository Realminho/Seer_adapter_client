#!/usr/bin/env python3
"""Capture JIBOT task/status APIs around a movement.

The goal is to determine whether JIBOT exposes a reliable arrival signal beyond
pose proximity. The script records raw TCP frames via JIBOT_RECORD and prints a
compact JSONL observation stream for:

  UmGetRobotInfo, UmGetCurTask, UmGetTaskInfo, UmGetPath, UmGetLocState

Optionally pass --goal or --pose to start a movement after the pre-samples.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_SRC = REPO_ROOT / "jibot-client" / "src"
ADAPTOR_ROOT = REPO_ROOT / "adaptor"
for path in (CLIENT_SRC, ADAPTOR_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from jibot_client import JIBOT  # noqa: E402


COMMANDS = (
    "UmGetRobotInfo",
    "UmGetCurTask",
    "UmGetTaskInfo",
    "UmGetPath",
    "UmGetLocState",
)


def _load_vehicle_defaults() -> Dict[str, Any]:
    config_path = ADAPTOR_ROOT / "config" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    with config_path.open("rb") as file:
        config = tomllib.load(file)
    return dict(config.get("vehicle", {}))


def _compact_payload(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if payload is None:
        return None
    compact = dict(payload)
    path = compact.get("path")
    if isinstance(path, list) and len(path) > 5:
        compact["path"] = path[:5]
        compact["path_truncated_count"] = len(path) - 5
    return compact


def _print_observation(
    *,
    stage: str,
    command: str,
    payload: Optional[Dict[str, Any]],
    started_at: float,
) -> None:
    entry = {
        "t": round(time.monotonic() - started_at, 3),
        "stage": stage,
        "command": command,
        "payload": _compact_payload(payload),
    }
    print(json.dumps(entry, ensure_ascii=False, separators=(",", ":")), flush=True)


async def _poll_once(
    vehicle: JIBOT,
    *,
    stage: str,
    timeout: float,
    started_at: float,
    commands: Iterable[str] = COMMANDS,
) -> None:
    for command in commands:
        payload = await vehicle.send_command_and_wait(
            command,
            gap=-1,
            timeout=timeout,
            accept_errors=True,
        )
        _print_observation(
            stage=stage,
            command=command,
            payload=payload,
            started_at=started_at,
        )


async def _send_requested_motion(vehicle: JIBOT, args: argparse.Namespace) -> None:
    if args.goal:
        await vehicle.send_command_and_wait(
            "UmGoto",
            gap=-1,
            timeout=args.timeout,
            accept_errors=True,
            target="goal",
            goal=args.goal,
            strict=args.strict,
        )
        return

    if args.pose is not None:
        pose_x, pose_y, pose_th = args.pose
        await vehicle.send_command_and_wait(
            "UmGoto",
            gap=-1,
            timeout=args.timeout,
            accept_errors=True,
            target="pose",
            poseX=pose_x,
            poseY=pose_y,
            poseTh=pose_th,
            strict=args.strict,
        )


async def run(args: argparse.Namespace) -> None:
    if args.record:
        os.environ.setdefault("JIBOT_RECORD", "1")
        if args.record_file:
            os.environ["JIBOT_RECORD_FILE"] = str(args.record_file)
        elif not os.getenv("JIBOT_RECORD_FILE"):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            record_dir = Path(os.getenv("JIBOT_RECORD_DIR", "logs/jibot"))
            os.environ["JIBOT_RECORD_FILE"] = str(
                record_dir / f"{stamp}-arrival-signals.jsonl"
            )

    vehicle = JIBOT(args.host, args.port, charging_status=args.charging_status)
    started_at = time.monotonic()
    try:
        await vehicle.connect_socket()
        await vehicle.connect()

        for index in range(args.pre_samples):
            await _poll_once(
                vehicle,
                stage=f"pre:{index}",
                timeout=args.timeout,
                started_at=started_at,
            )
            await asyncio.sleep(args.interval)

        if args.goal or args.pose is not None:
            await _send_requested_motion(vehicle, args)
            _print_observation(
                stage="motion-command",
                command="UmGoto",
                payload={"goal": args.goal, "pose": args.pose, "strict": args.strict},
                started_at=started_at,
            )

        deadline = time.monotonic() + args.duration
        sample_index = 0
        while time.monotonic() < deadline:
            await _poll_once(
                vehicle,
                stage=f"sample:{sample_index}",
                timeout=args.timeout,
                started_at=started_at,
            )
            sample_index += 1
            await asyncio.sleep(args.interval)
    finally:
        await vehicle.disconnect()

    if args.record:
        print(
            json.dumps(
                {
                    "record_file": os.getenv("JIBOT_RECORD_FILE"),
                    "note": "Inspect this JSONL for raw tx/rx payloads.",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )


def main() -> None:
    defaults = _load_vehicle_defaults()
    parser = argparse.ArgumentParser(
        description="Capture JIBOT arrival-related API signals around a movement."
    )
    parser.add_argument(
        "--host",
        default=defaults.get("vehicle_ip", "127.0.0.1"),
        help="JIBOT host/IP. Defaults to adaptor config vehicle.vehicle_ip.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(defaults.get("vehicle_port", 7273)),
        help="JIBOT TCP port.",
    )
    parser.add_argument(
        "--goal",
        help="Optional named UmGoto goal to start after pre-samples.",
    )
    parser.add_argument(
        "--pose",
        nargs=3,
        type=float,
        metavar=("X", "Y", "TH"),
        help="Optional UmGoto pose to start after pre-samples.",
    )
    parser.add_argument("--strict", action="store_true", help="Pass strict=true to UmGoto.")
    parser.add_argument("--duration", type=float, default=20.0, help="Seconds to sample after motion command or pre-samples.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between samples.")
    parser.add_argument("--timeout", type=float, default=1.0, help="Per-command response timeout.")
    parser.add_argument("--pre-samples", type=int, default=2, help="Samples before optional motion command.")
    parser.add_argument("--charging-status", default="charging", help="Robot status string that means charging.")
    parser.add_argument("--record", action=argparse.BooleanOptionalAction, default=True, help="Enable raw JIBOT_RECORD JSONL capture.")
    parser.add_argument("--record-file", type=Path, help="Explicit raw recording JSONL path.")
    args = parser.parse_args()

    if args.goal and args.pose is not None:
        parser.error("--goal and --pose are mutually exclusive")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
