import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adaptor"))

from utils.ezi_io import EZIIOClient, UDPClientProtocol


def ezi_response(frame_type=0xC0):
    data = b"\x00" * 8
    return bytes([0xAA, 4 + len(data), 1, 0x00, frame_type, 0x00]) + data


class DelayedResponseTransport:
    def __init__(self, protocol, delay=0.01):
        self.protocol = protocol
        self.delay = delay
        self.sent = 0

    def sendto(self, _frame):
        self.sent += 1
        loop = asyncio.get_running_loop()
        loop.call_later(self.delay, self.protocol.datagram_received, ezi_response(), None)

    def close(self):
        pass


class TimeoutTransport:
    def __init__(self):
        self.closed = False

    def sendto(self, _frame):
        pass

    def close(self):
        self.closed = True


def test_ezi_io_client_serializes_concurrent_requests():
    async def scenario():
        client = EZIIOClient("127.0.0.1", timeout=0.05)
        client.protocol = UDPClientProtocol()
        client.transport = DelayedResponseTransport(client.protocol)

        first, second = await asyncio.gather(
            client._send(0xC0),
            client._send(0xC0),
        )

        assert first is not None
        assert second is not None
        assert client.transport.sent == 2

    asyncio.run(scenario())


def test_ezi_io_client_resets_transport_after_timeout():
    async def scenario():
        client = EZIIOClient("127.0.0.1", timeout=0.01)
        timed_out_transport = TimeoutTransport()
        client.protocol = UDPClientProtocol()
        client.transport = timed_out_transport

        first = await client._send(0xC0)

        assert first is None
        assert timed_out_transport.closed is True
        assert client.protocol is None
        assert client.transport is None
        assert "timeout" in client.last_error

    asyncio.run(scenario())
