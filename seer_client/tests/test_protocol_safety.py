from __future__ import annotations

import asyncio
import math
import struct
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from seer_client.client import SeerClient  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.connection import SeerPortConnection  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.navigation import SeerNavigationService  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.protocol import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    HEADER_FMT,
    HEADER_SIZE,
    ApiNumber,
    ApiPort,
    SeerApiError,
    pack_message,
    response_type_for,
    unpack_header,
)


class _NavigationClient:
    adapter_position_unit = "m"
    adapter_orientation_unit = "rad"

    def __init__(self) -> None:
        self.calls = []
        self._status = "Stopped"
        self._pending_station = ""
        self._station = ""

    @staticmethod
    def position_to_native(value: float) -> float:
        return float(value)

    @staticmethod
    def angle_to_native(value: float) -> float:
        return float(value)

    async def send_command(self, command: str, **body):
        self.calls.append((command, body))
        return {"ret_code": 0}


class SeerProtocolSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_protocol_version_two_is_serialized_and_accepted(self):
        frame = pack_message(
            7,
            ApiNumber.STATUS_LOC,
            {"sample": True},
            protocol_version=2,
        )
        _, version, *_ = struct.unpack(HEADER_FMT, frame[:HEADER_SIZE])
        self.assertEqual(version, 2)
        length, sequence, message_type = unpack_header(frame[:HEADER_SIZE])
        self.assertGreater(length, 0)
        self.assertEqual(sequence, 7)
        self.assertEqual(message_type, int(ApiNumber.STATUS_LOC))

    async def test_nonzero_ret_code_in_normal_response_raises_api_error(self):
        async def handle(reader, writer):
            header = await reader.readexactly(HEADER_SIZE)
            length, sequence, message_type = unpack_header(header)
            if length:
                await reader.readexactly(length)
            writer.write(
                pack_message(
                    sequence,
                    response_type_for(message_type),
                    {"ret_code": 40051, "err_msg": "map not found"},
                )
            )
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        connection = SeerPortConnection(
            "127.0.0.1", port, allow_legacy_echo_response=False
        )
        await connection.connect()
        try:
            with self.assertRaises(SeerApiError) as raised:
                await connection.request(ApiNumber.STATUS_MAP, {})
            self.assertEqual(raised.exception.payload["ret_code"], 40051)
            self.assertTrue(connection.is_connected())
        finally:
            await connection.disconnect()
            server.close()
            await server.wait_closed()

    async def test_negative_translation_uses_absolute_distance_and_signed_speed(self):
        client = _NavigationClient()
        navigation = SeerNavigationService(client)
        await navigation.translate(-1.25, 0.2)
        command, body = client.calls[-1]
        self.assertEqual(command, "translate")
        self.assertEqual(body["dist"], 1.25)
        self.assertEqual(body["vx"], -0.2)

    async def test_clockwise_turn_uses_absolute_angle_and_negative_vw(self):
        client = _NavigationClient()
        navigation = SeerNavigationService(client)
        await navigation.turn(-math.pi / 2.0, 0.4)
        command, body = client.calls[-1]
        self.assertEqual(command, "turn")
        self.assertAlmostEqual(body["angle"], math.pi / 2.0)
        self.assertAlmostEqual(body["vw"], -0.4)

    async def test_status_poll_uses_one_filtered_request_and_updates_safety_io(self):
        class FakeConnection:
            def __init__(self):
                self.calls = []

            async def request(self, api, body):
                self.calls.append((api, body))
                return {
                    "ret_code": 0,
                    "x": 1.0,
                    "y": 2.0,
                    "angle": 0.25,
                    "task_status": 2,
                    "electric": False,
                    "DI": [{"id": 0, "status": True, "valid": True}],
                    "DO": [{"id": 1, "status": True, "valid": True}],
                }

        client = SeerClient("127.0.0.1", ports=())
        connection = FakeConnection()
        client._ports[ApiPort.STATE] = connection
        await client.status_service.poll_once()

        self.assertEqual(len(connection.calls), 1)
        api, body = connection.calls[0]
        self.assertEqual(api, ApiNumber.STATUS_ALL1)
        self.assertFalse(body["return_laser"])
        self.assertFalse(body["return_beams3D"])
        self.assertIn("task_status", body["keys"])
        self.assertFalse(client._motor_flag)
        self.assertEqual(client._status, "Driving")
        self.assertTrue(client.io.cache.latest["DI"][0]["status"])
        self.assertTrue(client.io.cache.latest["DO"][0]["status"])


if __name__ == "__main__":
    unittest.main()
