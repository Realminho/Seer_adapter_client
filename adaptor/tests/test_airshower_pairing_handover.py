# adaptor/tests/test_airshower_pairing_handover.py
"""에어샤워 통과에서 pairing이 누구 손에서 누구 손으로 넘어가는지.

한 방향은 네 개가 한 벌이고 링크를 이렇게 넘긴다:
    OpenIn   pair = true       BC를 걸어 링크를 만든다
    CloseIn  pair = false      그 링크를 빌려 쓴다
    OpenOut  pair = false      그 링크를 빌려 쓴다
    CloseOut disconnect = true SELECT 토글 unpair + 포트 close로 놓는다

넷이 다 걸려야 성립한다는 것이 이 설계의 약한 고리다. 진출 절반만 걸리거나 오더가
중간에 깨지면 OpenOut이 링크 없이 출력만 내보내는데, 출력은 EZI IO로 그대로 나가서
recipe는 FINISHED로 끝난다 — 실패로 보이지 않는다(2026-08-20 현장 사례).

여기서는 그 체인을 **배포 recipe의 실제 파라미터로** 순서대로 돌린다. 손으로 적은
파라미터로 시험하면 config가 바뀌어도 통과해 버린다.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import get_config
from extensions.pio import execute_pio_scenario, pio_disconnect_action

# checksum: sum(b"BC=OK") & 0xff == 0x5c. 유효한 PIO 프레임이라야 pairing이 선다.
_BC_REPLY = "[BC=OK5C]"


class _Ezi:
    """출력을 되읽어 주고 입력은 지정한 핀만 켜 두는 EZI IO 대역."""

    def __init__(self, on_input_pins=()):
        self.outputs = [0] * 16
        self.output_calls = []
        self.on_input_pins = set(on_input_pins)

    async def turn_on_output(self, pin):
        self.outputs[pin] = 1
        self.output_calls.append((pin, "on"))

    async def turn_off_output(self, pin):
        self.outputs[pin] = 0
        self.output_calls.append((pin, "off"))

    async def get_output(self):
        return {"outputs": list(self.outputs)}

    async def get_input(self):
        bits = [0] * 16
        for pin in self.on_input_pins:
            bits[pin] = 1
        return {"inputs": bits}

    async def get_input_pin(self, pin):
        return 1 if pin in self.on_input_pins else 0


class _Serial:
    def __init__(self):
        self.is_open = True


class _PioClient:
    """직렬 링크 대역. 포트가 닫혔는지만 관찰하면 된다."""

    def __init__(self):
        self.ser = _Serial()

    def connect(self):
        self.ser.is_open = True

    def close(self):
        self.ser.is_open = False


def _adapter(on_input_pins=()):
    """배포 config를 그대로 쓰고 하드웨어만 대역으로 바꾼 adapter."""
    config = get_config()
    return SimpleNamespace(
        _ezi_io=_Ezi(on_input_pins),
        _pio_client=_PioClient(),
        _pio_client_factory=None,
        _pio_outputs={},
        _note_pio=lambda **kwargs: None,
        _note_ezio=lambda **kwargs: None,
        state=None,
        config=config,
    )


def _scenario_params(action_type):
    """배포 recipe에서 pioScenario step의 파라미터를 그대로 꺼낸다."""
    for recipe in get_config().recipes:
        if recipe.action_type != action_type:
            continue
        for step in recipe.steps:
            if step.extension == "pioScenario":
                return dict(step.parameters)
    raise AssertionError(f"{action_type}에 pioScenario step이 없다")


def _action(params):
    return SimpleNamespace(
        action_id="handover-test",
        action_type="pioScenario",
        action_parameters=[
            SimpleNamespace(key=key, value=value) for key, value in params.items()
        ],
    )


def _run_scenario(adapter, params):
    with mock.patch(
        "extensions.pio.call_pio", new=mock.AsyncMock(return_value=_BC_REPLY)
    ):
        return asyncio.run(execute_pio_scenario(adapter, _action(params)))


class PairingHandoverTest(unittest.TestCase):
    # PIO in1 = EZI in0, in2 = EZI in1. 문 열림 확인이 통과하도록 둘 다 켜 둔다 —
    # 여기서 보려는 것은 문 확인이 아니라 링크다.
    DOORS_CONFIRMED = (0, 1)
    SELECT_PIN = get_config().ezi_config.select

    def _stage(self, adapter, direction, stage):
        return _run_scenario(
            adapter, _scenario_params(f"airShower{direction}{stage}")
        )

    def _select_toggles(self, adapter):
        return [
            call for call in adapter._ezi_io.output_calls
            if call[0] == self.SELECT_PIN
        ]

    def test_open_in_opens_the_link_and_the_middle_stages_borrow_it(self):
        """링크는 OpenIn 하나가 만들고 나머지는 빌려 쓴다.

        중간에 다시 걸면 SELECT를 올렸다 내리고 BC를 새로 보내는 데 0.7~2.7초가
        든다. 그 사이에 문 요청이 끊긴다.
        """
        adapter = _adapter(self.DOORS_CONFIRMED)

        opened = self._stage(adapter, "3l-4l", "OpenIn")
        self.assertIsNotNone(opened["init"], "OpenIn이 링크를 걸지 않았다")

        for stage in ("CloseIn", "OpenOut"):
            result = self._stage(adapter, "3l-4l", stage)
            self.assertTrue(result["ok"], result)
            self.assertIsNone(result["init"], f"{stage}가 링크를 다시 걸었다")
            self.assertTrue(
                adapter._pio_client.ser.is_open, f"{stage} 뒤에 포트가 닫혔다"
            )

    def test_close_out_releases_the_link(self):
        """마지막 단계가 unpair까지 한다. 포트만 닫으면 설비는 계속 물려 있다."""
        adapter = _adapter(self.DOORS_CONFIRMED)

        self._stage(adapter, "3l-4l", "OpenIn")
        before = len(self._select_toggles(adapter))
        self._stage(adapter, "3l-4l", "CloseOut")

        self.assertEqual(
            self._select_toggles(adapter)[before:],
            [(self.SELECT_PIN, "on"), (self.SELECT_PIN, "off")],
            "CloseOut이 SELECT를 토글해 unpair하지 않았다",
        )
        self.assertFalse(adapter._pio_client.ser.is_open, "포트가 열린 채 남았다")

    def test_the_exit_half_alone_runs_without_any_link(self):
        """OpenIn 없이 OpenOut만 걸리면 어떻게 되는지 — 이 고장의 성질.

        출력은 나가고 recipe는 FINISHED다. 실패로 보이지 않으니 로그만 봐서는
        문이 왜 안 열렸는지 알 수 없다.
        """
        adapter = _adapter(self.DOORS_CONFIRMED)

        result = self._stage(adapter, "3l-4l", "OpenOut")

        self.assertTrue(result["ok"], "링크가 없어도 실패조차 하지 않는다")
        self.assertIsNone(result["init"])
        self.assertEqual(self._select_toggles(adapter), [], "BC를 건 적이 없다")

    def test_the_reverse_direction_hands_the_link_over_the_same_way(self):
        adapter = _adapter(self.DOORS_CONFIRMED)

        opened = self._stage(adapter, "4l-3l", "OpenIn")
        self.assertIsNotNone(opened["init"])
        self.assertIsNone(self._stage(adapter, "4l-3l", "OpenOut")["init"])
        self._stage(adapter, "4l-3l", "CloseOut")
        self.assertFalse(adapter._pio_client.ser.is_open)


if __name__ == "__main__":
    unittest.main()
