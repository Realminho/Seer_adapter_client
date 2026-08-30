# adaptor/tests/test_io_simulator.py
"""하드웨어 없는 simulator 실행용 가짜 EZI IO / PIO 직렬 보드.

실장비가 없으면 pio*/ezio* action은 "EZI IO is not initialized"로 첫 step에서
끝난다. 이 가짜 보드는 설비 규칙(SELECT를 올린 채 BC가 오면 pairing, 출력은
같은 비트로 되읽힘)을 그대로 흉내내 recipe를 끝까지 돌린다.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import utils.ezi_io
import utils.ezi_motor
from adapter_jibot import Adapter
from config.config import EziConfig, get_config
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from utils.io_simulator import SimulatedEZIIO, SimulatedPIOMaster, make_simulated_io


def _action(action_type, **params):
    return SimpleNamespace(
        action_id=f"sim-{action_type}",
        action_type=action_type,
        action_parameters=[
            SimpleNamespace(key=key, value=value) for key, value in params.items()
        ],
    )


class SimulatedEziIoOutputTest(unittest.TestCase):
    def test_output_write_reads_back_on_the_same_pin(self):
        ezi, _pio = make_simulated_io(EziConfig())

        async def scenario():
            await ezi.turn_on_output(4)
            resp = await ezi.get_output()
            self.assertEqual(resp["outputs"][4], 1)
            self.assertEqual(await ezi.get_output_pin(4), 1)
            # 이웃을 건드리면 pioWriteOut의 read-back 검증이 무의미해진다.
            self.assertEqual(resp["outputs"][3], 0)
            self.assertEqual(resp["outputs"][5], 0)

            await ezi.turn_off_output(4)
            self.assertEqual((await ezi.get_output())["outputs"][4], 0)

        asyncio.run(scenario())

    def test_set_output_uses_the_board_bit_convention(self):
        # FAS_SetOutput/FAS_GetOutput 모두 output n을 비트 (16+n)에 둔다
        # (utils/ezi_io.output_bit). 가짜도 같은 규약이어야 실기와 안 갈린다.
        ezi, _pio = make_simulated_io(EziConfig())

        async def scenario():
            await ezi.set_output(set_mask=1 << (16 + 7))
            resp = await ezi.get_output()
            self.assertEqual(resp["outputs"][7], 1)
            self.assertEqual(resp["output_raw"], 1 << (16 + 7))

            await ezi.clear_all_outputs()
            self.assertEqual((await ezi.get_output())["outputs"], [0] * 16)

        asyncio.run(scenario())

    def test_board_info_is_reported_so_startup_logs_it(self):
        ezi, _pio = make_simulated_io(EziConfig())
        info = asyncio.run(ezi.get_board_info())
        self.assertIn("simulated", info["description"].lower())


class SimulatedPairingTest(unittest.TestCase):
    """설비 pairing 규칙: SELECT가 올라가 있는 동안 BC가 오면 GO가 올라온다."""

    def test_bc_while_select_is_on_raises_go(self):
        cfg = EziConfig()
        ezi, pio = make_simulated_io(cfg)
        pio.connect()

        async def scenario():
            self.assertEqual(await ezi.get_input_pin(cfg.go), 0)
            await ezi.turn_on_output(cfg.select)
            pio.send_bc(2, "123456", 250, 0, "OHT123")
            await ezi.turn_off_output(cfg.select)
            self.assertEqual(await ezi.get_input_pin(cfg.go), 1)

        asyncio.run(scenario())

    def test_bc_without_select_does_not_pair(self):
        cfg = EziConfig()
        ezi, pio = make_simulated_io(cfg)
        pio.connect()

        async def scenario():
            pio.send_bc(2, "123456", 250, 0, "OHT123")
            self.assertEqual(await ezi.get_input_pin(cfg.go), 0)

        asyncio.run(scenario())

    def test_bc_on_a_closed_port_does_not_pair(self):
        # 실기에서도 포트가 닫혀 있으면 응답이 0바이트다. 여기서 pairing이
        # 성립하면 "포트도 안 열고 통과"하는 가짜가 된다.
        cfg = EziConfig()
        ezi, pio = make_simulated_io(cfg)

        async def scenario():
            await ezi.turn_on_output(cfg.select)
            self.assertEqual(pio.send_bc(2, "123456", 250, 0, "OHT123"), "")
            await ezi.turn_off_output(cfg.select)
            self.assertEqual(await ezi.get_input_pin(cfg.go), 0)

        asyncio.run(scenario())

    def test_select_toggle_without_bc_unpairs(self):
        cfg = EziConfig()
        ezi, pio = make_simulated_io(cfg)
        pio.connect()

        async def scenario():
            await ezi.turn_on_output(cfg.select)
            pio.send_bc(2, "123456", 250, 0, "OHT123")
            await ezi.turn_off_output(cfg.select)
            self.assertEqual(await ezi.get_input_pin(cfg.go), 1)

            # pio_unpair와 같은 절차: BC 없이 SELECT만 토글한다.
            await ezi.turn_on_output(cfg.select)
            await ezi.turn_off_output(cfg.select)
            self.assertEqual(await ezi.get_input_pin(cfg.go), 0)

        asyncio.run(scenario())

    def test_outputs_echo_into_inputs_except_the_go_pin(self):
        # elevator.py는 floor pin을 출력으로 걸고 같은 번호의 입력으로 확인한다.
        # GO 입력만은 echo가 아니라 pairing 상태를 따라야 한다.
        cfg = EziConfig()
        ezi, _pio = make_simulated_io(cfg)

        async def scenario():
            await ezi.turn_on_output(1)
            self.assertEqual(await ezi.get_input_pin(1), 1)
            await ezi.turn_on_output(cfg.select)
            self.assertEqual(await ezi.get_input_pin(cfg.go), 0)

        asyncio.run(scenario())


class SimulatedPioMasterTest(unittest.TestCase):
    def test_connect_opens_a_port_the_extensions_can_probe(self):
        _ezi, pio = make_simulated_io(EziConfig())
        self.assertFalse(bool(getattr(pio.ser, "is_open", False)))
        pio.connect()
        self.assertTrue(pio.ser.is_open)
        pio.close()
        self.assertFalse(bool(getattr(pio.ser, "is_open", False)))

    def test_send_bc_answers_with_a_framed_reply(self):
        _ezi, pio = make_simulated_io(EziConfig())
        pio.connect()
        reply = pio.send_bc(2, "123456", 250, 0, "OHT123")
        self.assertTrue(reply.startswith("<") and reply.endswith(">"))
        self.assertIn("BC=OK", reply)


class SimulatedIoRunsShippedPioRecipeTest(unittest.TestCase):
    def _run_open_1f(self, car_upper: bool):
        """pioElevatorOpen1f를 시뮬레이터로 돌리고 (결과, ezi)를 돌려준다.

        시뮬레이터에는 엘리베이터 모델이 없으므로 recipe가 읽는 두 입력을 직접
        세운다: in2 = 카가 상층에 있는지(갈래를 고르는 조건), in1 = 문 열림 확인
        (없으면 recipe가 120초를 기다리다 실패한다).
        """
        config = get_config()
        adapter = Adapter(config=config)
        ezi, pio = make_simulated_io(config.ezi_config)
        inputs = config.pio_config.input_pins
        ezi.inputs[inputs[0]] = 1                      # in1 = 문 열림
        ezi.inputs[inputs[1]] = 1 if car_upper else 0  # in2 = 카가 상층
        adapter.set_ezi_io(ezi)
        adapter.set_pio_client_factory(lambda: pio)

        result = asyncio.run(
            adapter._action_registry.execute(_action("pioElevatorOpen1f"), adapter)
        )
        return result, ezi, config

    def test_pio_elevator_open_completes_against_the_simulated_boards(self):
        """(a)의 목적: 하드웨어 없이 pioElevatorOpen1f가 끝까지 FINISHED로 간다.

        카가 이미 1층이면 문만 연다 — 문 열기 출력만 눌리고 층 호출은 나가지 않는다.
        """
        result, ezi, config = self._run_open_1f(car_upper=False)

        self.assertEqual(result.status, ActionStatus.FINISHED, result.description)
        door = config.elevator_config.open_door_pin
        self.assertEqual(
            [state for pin, state in ezi.output_history if pin == door],
            ["on", "off", "off"],
        )
        # 호출 출력은 cleanup의 off 한 번뿐이어야 한다. on이 섞이면 이미 서 있는
        # 층으로 카를 다시 부른 것이다.
        call_pin = config.pio_config.output_pins[0]   # out1 = elevator1f_1f
        self.assertEqual(
            [state for pin, state in ezi.output_history if pin == call_pin],
            ["off"],
        )

    def test_pio_elevator_open_calls_the_car_down_when_the_sensor_says_upper(self):
        """in2가 켜져 있으면 문 버튼이 아니라 층 호출을 누른다."""
        result, ezi, config = self._run_open_1f(car_upper=True)

        self.assertEqual(result.status, ActionStatus.FINISHED, result.description)
        call_pin = config.pio_config.output_pins[0]   # out1 = elevator1f_1f
        self.assertEqual(
            [state for pin, state in ezi.output_history if pin == call_pin],
            ["on", "off", "off"],
        )
        door = config.elevator_config.open_door_pin
        self.assertEqual(
            [state for pin, state in ezi.output_history if pin == door],
            ["off"],
        )


class AttachSimulatedIoTest(unittest.TestCase):
    def test_simulator_without_an_address_attaches_the_simulated_boards(self):
        adapter = Adapter(config=None)
        asyncio.run(main.attach_ezi_clients(adapter, EziConfig(), simulator=True))

        self.assertIsInstance(adapter._ezi_io, SimulatedEZIIO)
        self.assertIsNotNone(adapter._pio_client_factory)
        self.assertIsInstance(adapter._pio_client_factory(), SimulatedPIOMaster)

    def test_a_configured_address_still_wins_under_the_simulator(self):
        class _RealIO:
            def __init__(self, ip, *args, **kwargs):
                self.ip = ip

            async def connect(self):
                pass

            async def get_board_info(self):
                return None

        adapter = Adapter(config=None)
        with patch.object(utils.ezi_io, "EZIIOClient", _RealIO):
            asyncio.run(
                main.attach_ezi_clients(
                    adapter, EziConfig(ezi_io="10.8.8.87"), simulator=True
                )
            )

        self.assertIsInstance(adapter._ezi_io, _RealIO)
        self.assertIsNone(adapter._pio_client_factory)

    def test_real_run_without_an_address_still_skips(self):
        adapter = Adapter(config=None)
        asyncio.run(main.attach_ezi_clients(adapter, EziConfig(), simulator=False))

        self.assertIsNone(adapter._ezi_io)
        self.assertIsNone(adapter._pio_client_factory)


if __name__ == "__main__":
    unittest.main()
