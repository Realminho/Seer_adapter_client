# adaptor/tests/test_pio_scenario_disconnect.py
"""pioScenario의 자동 연결/해제가 대칭인지.

pair는 기본 true라 scenario가 알아서 pairing을 건다. 그러면 끝낼 때도 알아서
풀어야 한다 — 포트만 닫으면 설비는 계속 물려 있고 GO 입력도 올라와 있다
(pio_unpair의 docstring이 같은 이야기를 한다). 끊은 척하지 않는다.

disconnect = false는 그대로 남는다. 에어샤워 통과처럼 recipe 여러 개가 한 pairing을
이어 쓰는 절차가 여기에 기대고 있다 — 중간에 풀면 다음 recipe가 허공에 출력한다.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import get_config
from extensions.pio import execute_pio_scenario

_BC_REPLY = "[BC=OK5C]"  # checksum: sum(b"BC=OK") & 0xff == 0x5c


class _Ezi:
    def __init__(self, fail_on_pin=None):
        self.outputs = [0] * 16
        self.output_calls = []
        self.fail_on_pin = fail_on_pin

    async def turn_on_output(self, pin):
        if pin == self.fail_on_pin:
            raise RuntimeError(f"EZI out{pin} write failed")
        self.outputs[pin] = 1
        self.output_calls.append((pin, "on"))

    async def turn_off_output(self, pin):
        if pin == self.fail_on_pin:
            raise RuntimeError(f"EZI out{pin} write failed")
        self.outputs[pin] = 0
        self.output_calls.append((pin, "off"))

    async def get_output(self):
        return {"outputs": list(self.outputs)}

    async def get_input(self):
        return {"inputs": [0] * 16}

    async def get_input_pin(self, _pin):
        return 0


class _Serial:
    def __init__(self):
        self.is_open = True


class _PioClient:
    def __init__(self):
        self.ser = _Serial()

    def connect(self):
        self.ser.is_open = True

    def close(self):
        self.ser.is_open = False


def _adapter(fail_on_pin=None):
    return SimpleNamespace(
        _ezi_io=_Ezi(fail_on_pin),
        _pio_client=_PioClient(),
        _pio_client_factory=None,
        _pio_outputs={},
        _note_pio=lambda **kwargs: None,
        _note_ezio=lambda **kwargs: None,
        state=None,
        config=get_config(),
    )


def _run(adapter, **params):
    body = {
        "stationId": "000030",
        "channel": 250,
        "scenario": [{"type": "out", "signal": "airShower3lOpen", "state": "off"}],
    }
    body.update(params)
    action = SimpleNamespace(
        action_id="disconnect-test",
        action_type="pioScenario",
        action_parameters=[
            SimpleNamespace(key=key, value=value) for key, value in body.items()
        ],
    )
    with mock.patch(
        "extensions.pio.call_pio", new=mock.AsyncMock(return_value=_BC_REPLY)
    ):
        return asyncio.run(execute_pio_scenario(adapter, action))


SELECT_PIN = get_config().ezi_config.select


def _select_toggles(ezi):
    return [call for call in ezi.output_calls if call[0] == SELECT_PIN]


class ScenarioDisconnectTest(unittest.TestCase):
    def test_the_default_disconnect_unpairs_the_facility(self):
        """스스로 pairing을 걸었으면 끝낼 때 스스로 푼다.

        포트만 닫으면 설비는 계속 물려 있다. 다음에 다른 설비와 pairing하려 해도
        이쪽이 안 놓고 있으면 GO가 계속 올라와 있다.
        """
        adapter = _adapter()

        result = _run(adapter)

        self.assertTrue(result["ok"], result)
        # pairing(SELECT on->off)에 이어 unpair(SELECT on->off)가 한 번 더 있어야 한다.
        self.assertEqual(
            _select_toggles(adapter._ezi_io),
            [(SELECT_PIN, "on"), (SELECT_PIN, "off")] * 2,
        )
        self.assertFalse(adapter._pio_client.ser.is_open, "포트가 닫히지 않았다")

    def test_disconnect_false_leaves_the_link_alone(self):
        """여러 recipe가 한 pairing을 이어 쓰는 절차는 여기에 기대고 있다."""
        adapter = _adapter()

        result = _run(adapter, disconnect=False)

        self.assertTrue(result["ok"], result)
        # pairing 한 번뿐. 뒤따르는 unpair 토글이 없어야 한다.
        self.assertEqual(
            _select_toggles(adapter._ezi_io),
            [(SELECT_PIN, "on"), (SELECT_PIN, "off")],
        )
        self.assertTrue(adapter._pio_client.ser.is_open, "포트를 닫으면 안 된다")

    def test_a_failing_unpair_does_not_swallow_the_scenario_result(self):
        """해제는 finally에서 돈다. 거기서 터지면 본문 결과가 통째로 가려진다.

        해제가 실패해도 scenario 자신의 성공/실패는 그대로 돌려주고, 포트는
        그래도 닫는다.
        """
        adapter = _adapter()
        # pairing까지는 성공시키고 그 뒤 unpair 쓰기에서만 터지게 한다.
        original = adapter._ezi_io.turn_on_output
        calls = {"n": 0}

        async def _fail_second_select(pin):
            if pin == SELECT_PIN:
                calls["n"] += 1
                if calls["n"] >= 2:
                    raise RuntimeError("EZI write failed")
            await original(pin)

        adapter._ezi_io.turn_on_output = _fail_second_select

        result = _run(adapter)

        self.assertTrue(result["ok"], "본문은 성공했는데 결과가 사라졌다")
        self.assertFalse(
            adapter._pio_client.ser.is_open, "unpair가 실패해도 포트는 닫아야 한다"
        )


if __name__ == "__main__":
    unittest.main()
