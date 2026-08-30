"""Tests for SeerPortConnection: one SEER API port's socket + request/response.

SeerPortConnection 테스트: SEER API 포트 하나의 소켓 + 요청/응답.

Real socket I/O against an in-process loopback "fake SEER" asyncio server (no
mocks): the fake speaks the same 16-byte-header + JSON-body frame, so these tests
exercise connect / request (pack→send→recv header→recv body→json) / disconnect
over an actual TCP connection.
인프로세스 루프백 "가짜 SEER" asyncio 서버를 상대로 실제 소켓 I/O(모킹 없음). 가짜
서버가 동일한 16바이트 헤더 + JSON 바디 프레임을 말하므로, 이 테스트들은 실제 TCP
연결에서 connect / request / disconnect 를 실행한다.
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _src in ("amr-client-contract/src", "seer-client/src"):
    _p = str(REPO_ROOT / _src)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from seer_client.connection import SeerPortConnection  # noqa: E402
from seer_client.protocol import (  # noqa: E402
    HEADER_SIZE,
    ApiNumber,
    pack_message,
    unpack_header,
)


def _echo(msg_typ, req_id, body):
    """Default responder: echo the request id+type with an empty body.

    기본 응답자: 요청 id+type 을 그대로, 빈 바디로 응답.
    """
    return (req_id, msg_typ, {})


class FakeSeerServer:
    """Minimal loopback SEER server. ``respond(msg_typ, req_id, body)`` returns
    the ``(resp_req_id, resp_typ, resp_body)`` to send back.

    최소 루프백 SEER 서버. ``respond`` 가 되돌려 보낼 ``(resp_req_id, resp_typ,
    resp_body)`` 를 반환한다.
    """

    def __init__(self, respond=None):
        self._respond = respond or _echo
        self.requests = []  # captured (msg_typ, req_id, body) / 수신한 요청 기록
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            while True:
                header = await reader.readexactly(HEADER_SIZE)
                msg_len, req_id, msg_typ = unpack_header(header)
                raw = await reader.readexactly(msg_len) if msg_len else b""
                body = json.loads(raw) if raw else {}
                self.requests.append((msg_typ, req_id, body))
                r_id, r_typ, r_body = self._respond(msg_typ, req_id, body)
                writer.write(pack_message(r_id, r_typ, r_body))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass  # client hung up / 클라이언트 연결 종료

    async def stop(self):
        self._server.close()
        # NOTE: Python 3.12's asyncio.Server.wait_closed() can hang forever when
        # close() is called with no active connections (gh-104344). close()
        # already shuts the listening socket; guard the await with a timeout so
        # test teardown can't deadlock.
        # 참고: Python 3.12의 asyncio.Server.wait_closed()는 활성 연결이 없을 때
        # close() 후 무한대기할 수 있음(gh-104344). close()로 리스닝 소켓은 이미 닫히니,
        # teardown 데드락 방지로 await 에 timeout 가드.
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=0.1)
        except asyncio.TimeoutError:
            pass


class SeerPortConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def _server(self, respond=None):
        fake = FakeSeerServer(respond)
        port = await fake.start()
        self.addAsyncCleanup(fake.stop)
        return fake, port

    async def _conn(self, port):
        conn = SeerPortConnection("127.0.0.1", port)
        await conn.connect()
        self.addAsyncCleanup(conn.disconnect)
        return conn

    async def test_connect_then_disconnect_toggles_is_connected(self):
        _, port = await self._server()
        conn = SeerPortConnection("127.0.0.1", port)
        self.assertFalse(conn.is_connected())
        await conn.connect()
        self.assertTrue(conn.is_connected())
        await conn.disconnect()
        self.assertFalse(conn.is_connected())

    async def test_request_round_trips_response_body(self):
        fake, port = await self._server(
            lambda typ, rid, body: (rid, typ, {"x": 1.0, "y": 2.0, "angle": 0.5})
        )
        conn = await self._conn(port)
        result = await conn.request(ApiNumber.STATUS_LOC, {})
        self.assertEqual(result, {"x": 1.0, "y": 2.0, "angle": 0.5})
        # The server actually received our STATUS_LOC (1004) request.
        self.assertEqual(fake.requests[0][0], 1004)

    async def test_request_transmits_the_body(self):
        fake, port = await self._server(lambda typ, rid, body: (rid, typ, {"ok": True}))
        conn = await self._conn(port)
        await conn.request(ApiNumber.TASK_GOTARGET, {"id": "LM5"})
        self.assertEqual(fake.requests[0][2], {"id": "LM5"})

    async def test_header_only_response_returns_empty_dict(self):
        # echo responder sends an empty body -> header-only frame.
        _, port = await self._server()
        conn = await self._conn(port)
        result = await conn.request(ApiNumber.STATUS_INFO, {})
        self.assertEqual(result, {})

    async def test_request_refreshes_rx_clock(self):
        _, port = await self._server()
        conn = await self._conn(port)
        await conn.request(ApiNumber.STATUS_BATTERY, {})
        self.assertLess(conn.seconds_since_last_rx(), 1.0)

    async def test_response_type_mismatch_raises_desync(self):
        # Server replies with a WRONG msg_typ -> correlation fails.
        _, port = await self._server(lambda typ, rid, body: (rid, 9999, {"x": 0}))
        conn = await self._conn(port)
        with self.assertRaises(ValueError):
            await conn.request(ApiNumber.STATUS_LOC, {})

    async def test_next_req_id_increments_and_wraps_uint16(self):
        _, port = await self._server()
        conn = SeerPortConnection("127.0.0.1", port)
        first = conn._next_req_id()
        second = conn._next_req_id()
        self.assertEqual(second, first + 1)
        conn._req_id = 0xFFFF
        self.assertEqual(conn._next_req_id(), 0)


if __name__ == "__main__":
    unittest.main()
