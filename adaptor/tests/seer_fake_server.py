"""Shared in-process loopback "fake SEER" asyncio server for client tests.

클라이언트 테스트용 공유 인프로세스 루프백 "가짜 SEER" asyncio 서버.

No ``test_`` prefix so pytest does not collect it. Speaks the real 16-byte-header
+ JSON-body frame, captures requests, and replies via a ``respond`` callback.
``test_`` 접두사가 없어 pytest 가 수집하지 않는다. 실제 16바이트 헤더 + JSON 바디
프레임을 말하고, 요청을 기록하며, ``respond`` 콜백으로 응답한다.
"""

import asyncio

from seer_client.protocol import HEADER_SIZE, pack_message, unpack_header


def echo(msg_typ, req_id, body):
    """Default responder: echo the request id+type with an empty body.

    기본 응답자: 요청 id+type 을 그대로, 빈 바디로 응답.
    """
    return (req_id, msg_typ, {})


class FakeSeerServer:
    """``respond(msg_typ, req_id, body) -> (resp_req_id, resp_typ, resp_body)``.

    ``respond`` 가 되돌려 보낼 ``(resp_req_id, resp_typ, resp_body)`` 를 반환한다.
    """

    def __init__(self, respond=None):
        self._respond = respond or echo
        self.requests = []  # captured (msg_typ, req_id, body) / 수신 요청 기록
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
                import json

                body = json.loads(raw) if raw else {}
                self.requests.append((msg_typ, req_id, body))
                r_id, r_typ, r_body = self._respond(msg_typ, req_id, body)
                writer.write(pack_message(r_id, r_typ, r_body))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass  # client hung up / 클라이언트 연결 종료

    async def stop(self):
        self._server.close()
        # Python 3.12 asyncio.Server.wait_closed() can hang with no active
        # connections (gh-104344); close() already shuts the listening socket, so
        # guard the await with a timeout to keep teardown deadlock-free.
        # Py3.12 wait_closed()는 연결 없을 때 무한대기 가능(gh-104344) → timeout 가드.
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=0.1)
        except asyncio.TimeoutError:
            pass
