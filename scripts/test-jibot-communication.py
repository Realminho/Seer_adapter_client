#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(REPO_ROOT / "adaptor"),
    str(REPO_ROOT / "jibot-client" / "src"),
    str(REPO_ROOT / "jibot-simulator"),
]

from config.config import get_config
from jibot_client import JIBOT
from cls_jibot_simulator import SimulatedJIBOT


def make_frame(payload):
    json_data = json.dumps(payload, separators=(",", ":"))
    return f"$#{len(json_data)}##{json_data}$~".encode("utf-8")


def parse_frame(raw):
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    if "$~" not in text:
        return None
    prefix, _ = text.split("$~", 1)
    if "##" not in prefix:
        return None
    _, payload = prefix.split("##", 1)
    return json.loads(payload)


async def read_frame(reader):
    return await reader.readuntil(b"$~")


async def fake_jibot_handler(reader, writer):
    try:
        while True:
            raw = await read_frame(reader)
            payload = parse_frame(raw)
            if not payload:
                continue

            command = payload.get("#CMD#")
            if command == "UmGetRobotInfo":
                writer.write(
                    make_frame(
                        {
                            "#CMD#": "UmGetRobotInfo",
                            "mode": "auto",
                            "status": "charging",
                            "battery": 77,
                            "station": "TEST",
                            "x": 1.25,
                            "y": 2.5,
                            "th": 0.75,
                        }
                    )
                )
                await writer.drain()
            elif command == "UmGetMotorState":
                writer.write(make_frame({"#CMD#": "UmGetMotorState", "flag": 1}))
                await writer.drain()
            elif command == "UmGetLocState":
                writer.write(make_frame({"#CMD#": "UmGetLocState", "score": 0.98}))
                await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for expected state")


async def test_real_client_against_fake_server():
    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        record_file = Path(tmpdir) / "fake-server-session.jsonl"
        os.environ["JIBOT_RECORD"] = "1"
        os.environ["JIBOT_RECORD_FILE"] = str(record_file)

        server = await asyncio.start_server(fake_jibot_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        vehicle = JIBOT("127.0.0.1", port, charging_status="charging")
        try:
            await vehicle.connect_socket()
            await vehicle.connect()
            await vehicle.get_robot_info(100)
            await vehicle.get_motor_state(100)
            await vehicle.get_localization_info(100)
            await wait_for(lambda: vehicle._battery == 77 and vehicle._motor_flag == 1)
        finally:
            await vehicle.disconnect()
            server.close()
            await server.wait_closed()
            os.environ.pop("JIBOT_RECORD", None)
            os.environ.pop("JIBOT_RECORD_FILE", None)

        records = record_file.read_text(encoding="utf-8")
        assert '"direction":"tx"' in records
        assert '"direction":"rx"' in records
        assert '"command":"UmGetRobotInfo"' in records
        assert vehicle._charging is True
        assert vehicle._x == 1.25
        print("PASS real-client fake-server communication")


async def test_simulator_communication():
    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        record_file = Path(tmpdir) / "simulator-session.jsonl"
        os.environ["JIBOT_RECORD"] = "1"
        os.environ["JIBOT_RECORD_FILE"] = str(record_file)

        config = get_config()
        vehicle = SimulatedJIBOT("simulator", 7273, config=config)
        try:
            await vehicle.connect_socket()
            await vehicle.connect()
            await vehicle.get_robot_info(1000)
            await vehicle.get_localization_info(1000)
            await vehicle.um_goto("pose", None, 0.1, 0.0, 0.0, False)
            await wait_for(lambda: vehicle._status == config.jibot_status.stop, timeout=5.0)
            await vehicle.stop_motion()
        finally:
            await vehicle.disconnect()
            os.environ.pop("JIBOT_RECORD", None)
            os.environ.pop("JIBOT_RECORD_FILE", None)

        records = record_file.read_text(encoding="utf-8")
        assert '"command":"UmGoto"' in records
        assert '"command":"UmStop"' in records
        assert vehicle._x == 0.1
        print("PASS simulator communication")


async def test_real_robot(host, port):
    record_dir = Path(os.getenv("JIBOT_RECORD_DIR", "logs/jibot"))
    os.environ["JIBOT_RECORD"] = "1"
    vehicle = JIBOT(host, port, charging_status="charging")
    try:
        await vehicle.connect_socket()
        await vehicle.connect()
        await vehicle.get_robot_info(100)
        await vehicle.get_motor_state(100)
        await vehicle.get_localization_info(100)
        await asyncio.sleep(0.5)
    finally:
        await vehicle.disconnect()
        os.environ.pop("JIBOT_RECORD", None)

    print(f"PASS real robot communication host={host} port={port} records_dir={record_dir}")


async def main():
    parser = argparse.ArgumentParser(description="Test JIBOT client and simulator communication.")
    parser.add_argument("--real-host", help="Real JIBOT AMR host/IP to test against.")
    parser.add_argument("--real-port", type=int, default=7273)
    args = parser.parse_args()

    await test_real_client_against_fake_server()
    await test_simulator_communication()

    if args.real_host:
        await test_real_robot(args.real_host, args.real_port)
    else:
        print("SKIP real robot communication; pass --real-host to run it.")


if __name__ == "__main__":
    asyncio.run(main())
