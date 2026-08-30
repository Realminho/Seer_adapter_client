"""Tests for SeerClient pool lifecycle + active status polling.

SeerClient 풀 수명주기 + 능동 상태 폴링 테스트.

SEER has no free status stream, so ``_poll_once`` actively queries the STATE port
and feeds each response to ``_process_status``. ``connect_socket`` builds one
SeerPortConnection per API port and starts the poll loop; ``disconnect`` tears the
pool down. Tests use a loopback fake SEER server; the TCP target is redirected via
the ``_open_port`` factory seam.
SEER 는 공짜 status 스트림이 없어 ``_poll_once``가 STATE 포트를 능동 질의하고 각 응답을
``_process_status``로 넘긴다. ``connect_socket``은 API 포트마다 SeerPortConnection 을
만들고 폴 루프를 시작; ``disconnect``는 풀을 정리. 루프백 가짜 SEER 서버 사용,
TCP 대상은 ``_open_port`` 팩토리 시ーム으로 리다이렉트.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _src in ("amr-client-contract/src", "seer-client/src"):
    _p = str(REPO_ROOT / _src)
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for seer_fake_server

from seer_client import SeerClient  # noqa: E402
from seer_client.connection import SeerPortConnection  # noqa: E402
from seer_client.protocol import ApiNumber, ApiPort  # noqa: E402
from seer_fake_server import FakeSeerServer  # noqa: E402


class PollOnceTest(unittest.IsolatedAsyncioTestCase):
    async def test_poll_once_updates_state_from_status_responses(self):
        def respond(typ, rid, body):
            if typ == int(ApiNumber.STATUS_ALL1):
                return (rid, typ, {"x": 1.0, "y": 2.0, "angle": 0.5,
                                   "battery_level": 0.5, "charging": True})
            if typ == int(ApiNumber.STATUS_TASK):
                return (rid, typ, {"task_status": 4, "target_id": "LM9"})
            if typ == int(ApiNumber.STATUS_BLOCK):
                return (rid, typ, {"blocked": True, "block_reason": "obstacle"})
            return (rid, typ, {})

        fake = FakeSeerServer(respond)
        port = await fake.start()
        self.addAsyncCleanup(fake.stop)
        c = SeerClient("127.0.0.1")
        conn = SeerPortConnection("127.0.0.1", port)
        await conn.connect()
        self.addAsyncCleanup(conn.disconnect)
        c._ports[ApiPort.STATE] = conn

        await c._poll_once()

        self.assertEqual((c.x, c.y, c.th), (1.0, 2.0, 0.5))
        self.assertAlmostEqual(c.battery, 50.0)
        self.assertTrue(c.charging)
        self.assertEqual(c._task_status, 4)
        self.assertEqual(c._target_id, "LM9")
        self.assertTrue(c._blocked)

    async def test_poll_once_without_state_port_is_noop(self):
        c = SeerClient("127.0.0.1")  # empty pool
        await c._poll_once()  # must not raise
        self.assertEqual((c.x, c.y, c.th), (0.0, 0.0, 0.0))


class PoolLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_socket_builds_pool_then_disconnect_clears(self):
        fake = FakeSeerServer()
        port = await fake.start()
        self.addAsyncCleanup(fake.stop)
        c = SeerClient("127.0.0.1", status_poll_interval_sec=0.01)

        async def open_loopback(port_id):
            conn = SeerPortConnection("127.0.0.1", port)
            await conn.connect()
            return conn

        c._open_port = open_loopback  # redirect TCP target to the loopback fake

        await c.connect_socket()
        self.assertTrue(c.is_connected())
        self.assertEqual(set(c._ports), set(c._port_ids))

        await c.disconnect()
        self.assertFalse(c.is_connected())
        self.assertEqual(dict(c._ports), {})


if __name__ == "__main__":
    unittest.main()
