from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from seer_client.connection import SeerPortConnection  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.protocol import HEADER_SIZE, ApiNumber, pack_message, unpack_header  # pyright: ignore[reportMissingImports]  # noqa: E402


class SeerConnectionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_discards_late_sequence_before_next_request(self):
        request_count = 0

        async def handle(reader, writer):
            nonlocal request_count
            try:
                while True:
                    header = await reader.readexactly(HEADER_SIZE)
                    length, sequence, message_type = unpack_header(header)
                    raw = await reader.readexactly(length) if length else b""
                    if raw:
                        json.loads(raw.decode("utf-8"))
                    request_count += 1
                    if request_count == 1:
                        await asyncio.sleep(0.08)
                    writer.write(
                        pack_message(
                            sequence,
                            message_type,
                            {"request_count": request_count},
                        )
                    )
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError):
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        connection = SeerPortConnection(
            "127.0.0.1", port, command_timeout=0.02
        )
        await connection.connect()
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await connection.request(ApiNumber.STATUS_LOC, {})
            self.assertTrue(connection.is_connected())
            result = await connection.request(
                ApiNumber.STATUS_LOC, {}, timeout=0.5
            )
            self.assertEqual(result["request_count"], 2)
        finally:
            await connection.disconnect()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
