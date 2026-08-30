"""Tests for SeerClient command dispatch + motion command mapping.

SeerClient 명령 디스패치 + 모션 명령 매핑 테스트.

``validate_command_params`` and ``send_command`` route a logical command through
COMMAND_CATALOG to the right port; the motion methods are thin wrappers that build
the SEER body and call ``send_command``. Tests run against a loopback fake SEER
server (real sockets) with the port pool populated directly.
``validate_command_params``/``send_command``은 논리 명령을 COMMAND_CATALOG 로 올바른
포트에 라우팅하고, 모션 메서드는 SEER 바디를 만들어 ``send_command``를 호출하는 얇은
래퍼다. 테스트는 포트 풀을 직접 채운 채 루프백 가짜 SEER 서버(실제 소켓)로 실행한다.
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


class ValidateCommandParamsTest(unittest.TestCase):
    def setUp(self):
        self.c = SeerClient("127.0.0.1")

    def test_unsupported_command_raises(self):
        with self.assertRaises(ValueError):
            self.c.validate_command_params("no_such_command", {})

    def test_missing_required_param_raises(self):
        # goto_station requires "id".
        with self.assertRaises(ValueError):
            self.c.validate_command_params("goto_station", {})

    def test_unknown_param_raises(self):
        with self.assertRaises(ValueError):
            self.c.validate_command_params("goto_station", {"id": "LM5", "bogus": 1})

    def test_valid_params_pass(self):
        # required + a declared optional ("angle") is accepted.
        self.c.validate_command_params("goto_station", {"id": "LM5", "angle": 1.2})


class SendCommandTest(unittest.IsolatedAsyncioTestCase):
    async def _client_on(self, respond=None):
        fake = FakeSeerServer(respond)
        port = await fake.start()
        self.addAsyncCleanup(fake.stop)
        c = SeerClient("127.0.0.1")
        # Populate the pool: every API port -> a connection to the one fake.
        for p in c._port_ids:
            conn = SeerPortConnection("127.0.0.1", port)
            await conn.connect()
            self.addAsyncCleanup(conn.disconnect)
            c._ports[p] = conn
        c._running = True
        return c, fake

    async def test_send_command_routes_to_catalog_port_and_returns_response(self):
        c, fake = await self._client_on(
            lambda typ, rid, body: (rid, typ, {"current_map": "floor2"})
        )
        result = await c.send_command("query_map")
        self.assertEqual(result, {"current_map": "floor2"})
        # query_map -> STATE port, API 1300.
        self.assertEqual(fake.requests[0][0], int(ApiNumber.STATUS_MAP))

    async def test_send_command_transmits_body(self):
        c, fake = await self._client_on()
        await c.send_command("goto_station", id="LM5", angle=1.5)
        self.assertEqual(fake.requests[0][2], {"id": "LM5", "angle": 1.5})

    async def test_send_command_unsupported_raises(self):
        c, _ = await self._client_on()
        with self.assertRaises(ValueError):
            await c.send_command("no_such_command")


class MotionMappingTest(unittest.IsolatedAsyncioTestCase):
    async def _client_on(self, respond=None):
        fake = FakeSeerServer(respond)
        port = await fake.start()
        self.addAsyncCleanup(fake.stop)
        c = SeerClient("127.0.0.1")
        for p in c._port_ids:
            conn = SeerPortConnection("127.0.0.1", port)
            await conn.connect()
            self.addAsyncCleanup(conn.disconnect)
            c._ports[p] = conn
        c._running = True
        return c, fake

    async def test_goto_point_sends_gotarget_with_landmark_id(self):
        c, fake = await self._client_on()
        await c.goto_point("LM5")
        typ, _rid, body = fake.requests[0]
        self.assertEqual(typ, int(ApiNumber.TASK_GOTARGET))  # 3051 on TASK port
        self.assertEqual(body, {"id": "LM5"})

    async def test_goto_xyz_sends_gopath_script_body(self):
        c, fake = await self._client_on()
        await c.goto_xyz(1.0, 2.0, 0.5)
        typ, _rid, body = fake.requests[0]
        self.assertEqual(typ, int(ApiNumber.TASK_GOTARGET))
        self.assertEqual(body["script_name"], "syspy/goPath.py")
        self.assertEqual(
            body["script_args"],
            {"x": 1.0, "y": 2.0, "theta": 0.5, "coordinate": "world", "backMode": 0},
        )

    async def test_drive_sends_motion_velocities(self):
        c, fake = await self._client_on()
        await c.drive(trans=0.5, rot=0.2, speed=0.0, lat=-0.1)
        typ, _rid, body = fake.requests[0]
        self.assertEqual(typ, int(ApiNumber.CONTROL_MOTION))  # 2010 on CONTROL port
        self.assertEqual(body["vx"], 0.5)
        self.assertEqual(body["vy"], -0.1)
        self.assertEqual(body["w"], 0.2)

    async def test_localize_auto_sends_isauto_reloc_with_zeros(self):
        c, fake = await self._client_on()
        await c.localize("auto", None, None, None, None)
        typ, _rid, body = fake.requests[0]
        self.assertEqual(typ, int(ApiNumber.CONTROL_RELOC))  # 2002 on CONTROL port
        self.assertTrue(body["isAuto"])
        self.assertEqual((body["x"], body["y"], body["angle"]), (0, 0, 0))

    async def test_localize_manual_sends_pose_reloc(self):
        c, fake = await self._client_on()
        await c.localize("pose", None, 3.0, 4.0, 1.57)
        typ, _rid, body = fake.requests[0]
        self.assertEqual(typ, int(ApiNumber.CONTROL_RELOC))
        self.assertFalse(body["isAuto"])
        self.assertEqual((body["x"], body["y"], body["angle"]), (3.0, 4.0, 1.57))

    async def test_set_do_sends_setdo_on_other_port(self):
        c, fake = await self._client_on()
        await c.set_do(15, True)
        typ, _rid, body = fake.requests[0]
        self.assertEqual(typ, int(ApiNumber.OTHER_SETDO))  # 6001 on OTHER port
        self.assertEqual(body, {"id": 15, "status": True})


if __name__ == "__main__":
    unittest.main()
