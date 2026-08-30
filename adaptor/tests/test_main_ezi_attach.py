# adaptor/tests/test_main_ezi_attach.py
"""main.py의 EZI 모듈 부착/종료 정리 배선 회귀 테스트.

ezi_io/ezi_motor는 로봇별 주소라 시뮬레이터나 하드웨어 없는 개발 장비에서는
비어 있는 게 정상이다. 그 상태에서 소켓을 열면 getaddrinfo가 gaierror를 던지고
main()의 넓은 except가 이를 받아 adaptor 전체가 종료된다.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import utils.ezi_io
import utils.ezi_motor
from adapter_jibot import Adapter
from config.config import EziConfig
from utils.charge_circuit import FakeChargeCircuit


class _FakeEziIO:
    instances = []

    def __init__(self, ip, *args, **kwargs):
        self.ip = ip
        self.connected = False
        _FakeEziIO.instances.append(self)

    async def connect(self):
        self.connected = True

    async def get_board_info(self):
        return {"description": f"fake io {self.ip}"}


class _FakeEziMotor:
    instances = []

    def __init__(self, ip, *args, **kwargs):
        self.ip = ip
        _FakeEziMotor.instances.append(self)

    async def get_board_info(self):
        return {"description": f"fake motor {self.ip}"}


class AttachEziClientsTest(unittest.TestCase):
    def setUp(self):
        _FakeEziIO.instances = []
        _FakeEziMotor.instances = []
        self.adapter = Adapter(config=None)

    def _attach(self, ezi_config):
        return asyncio.run(main.attach_ezi_clients(self.adapter, ezi_config))

    def test_blank_addresses_skip_both_modules(self):
        # robots.hcl에서 ezi_io/ezi_motor를 주석 처리한 --simulator 실행 그대로.
        with patch.object(utils.ezi_io, "EZIIOClient", _FakeEziIO), \
             patch.object(utils.ezi_motor, "EziMotorClient", _FakeEziMotor):
            motor = self._attach(EziConfig())

        self.assertIsNone(motor)
        self.assertEqual(_FakeEziIO.instances, [])
        self.assertEqual(_FakeEziMotor.instances, [])
        self.assertIsNone(self.adapter._ezi_io)
        self.assertIsNone(self.adapter._ezi_motor)

    def test_configured_addresses_attach_both_modules(self):
        config = EziConfig(ezi_io="10.8.8.87", ezi_motor="10.8.8.2")
        with patch.object(utils.ezi_io, "EZIIOClient", _FakeEziIO), \
             patch.object(utils.ezi_motor, "EziMotorClient", _FakeEziMotor):
            motor = self._attach(config)

        self.assertIs(self.adapter._ezi_io, _FakeEziIO.instances[0])
        self.assertEqual(self.adapter._ezi_io.ip, "10.8.8.87")
        self.assertTrue(self.adapter._ezi_io.connected)
        self.assertIs(self.adapter._ezi_motor, _FakeEziMotor.instances[0])
        self.assertEqual(self.adapter._ezi_motor.ip, "10.8.8.2")
        self.assertIs(motor, self.adapter._ezi_motor)

    def test_unresolvable_address_does_not_kill_the_adaptor(self):
        # 주소 오타/DNS 실패는 그 모듈만 포기한다. adaptor는 MQTT로 상태를
        # 계속 보고해야 하므로 프로세스를 내려서는 안 된다.
        class _Boom:
            def __init__(self, ip, *args, **kwargs):
                raise OSError("[Errno -2] Name or service not known")

        config = EziConfig(ezi_io="nope.invalid", ezi_motor="10.8.8.2")
        with patch.object(utils.ezi_io, "EZIIOClient", _Boom), \
             patch.object(utils.ezi_motor, "EziMotorClient", _FakeEziMotor):
            motor = self._attach(config)

        self.assertIsNone(self.adapter._ezi_io)
        # 한 모듈이 실패해도 나머지는 정상 부착된다.
        self.assertIs(self.adapter._ezi_motor, _FakeEziMotor.instances[0])
        self.assertIs(motor, self.adapter._ezi_motor)


class ReleaseChargeHoldTest(unittest.TestCase):
    def test_shutdown_releases_the_charge_hold(self):
        # 제자리 충전 해제는 Adapter 메서드에서 extensions.charge 함수로 옮겨졌다.
        # 종료 경로가 옛 메서드를 부르면 릴레이 유지가 그대로 남는다.
        adapter = Adapter(config=None)
        circuit = FakeChargeCircuit()
        adapter.set_charge_circuit(circuit)
        circuit.start_hold()
        adapter._charge_in_place_active = True

        main.release_charge_hold(adapter)

        self.assertFalse(adapter._charge_in_place_active)
        self.assertFalse(circuit.is_holding)
        self.assertEqual(circuit.stop_calls, 1)

    def test_release_failure_is_logged_not_raised(self):
        class _Broken:
            _charge_in_place_active = True

            @property
            def _charge_circuit(self):
                raise RuntimeError("circuit gone")

        main.release_charge_hold(_Broken())


if __name__ == "__main__":
    unittest.main()
