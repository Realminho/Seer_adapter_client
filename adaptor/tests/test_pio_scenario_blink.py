# adaptor/tests/test_pio_scenario_blink.py
"""pioScenario의 blink 단계 — 설비가 열림 요청을 반복 신호로 읽는 배선용.

recipe 문법에는 반복이 없고 step은 직렬이라, 주행과 겹쳐 깜박이려면 반복이 액션
하나 안에 있어야 한다. 그 액션은 반드시 스스로 끝나야 한다 — 끝나지 않으면
뒤따르는 HARD 액션이 이 배경 task를 기다리다 같이 멈춘다.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import PioConfig
from extensions.pio import execute_pio_scenario


class _Ezi:
    """쓴 출력을 그대로 되읽어 주는 EZI IO 대역.

    입력은 `on_after`번째 읽기부터 켜진다 — "지나가는 동안 깜박이다가 설비 신호가
    올라오면 멈춘다"를 시간이 아니라 읽기 횟수로 재현한다.
    """

    def __init__(self, *, input_pin=None, on_after=None):
        self.outputs = [0] * 16
        self.output_calls = []
        self.input_pin = input_pin
        self.on_after = on_after
        self.reads = 0

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
        if self.input_pin is not None and self.on_after is not None:
            if self.reads >= self.on_after:
                bits[self.input_pin] = 1
        self.reads += 1
        return {"inputs": bits}


def _adapter(ezi):
    return SimpleNamespace(
        _ezi_io=ezi,
        _pio_outputs={},
        _note_pio=lambda **kwargs: None,
        _note_ezio=lambda **kwargs: None,
        state=None,
        config=SimpleNamespace(
            pio_config=PioConfig(
                pio_serial_port="/dev/null",
                pio_baudrate=38400,
                media=2,
                port=0,
                vehicle_num="OHT123",
                output_signals={"airShower3lOpen": 1},
            ),
            # 시험을 벽시계에 매달지 않는다. poll 간격 0이면 조건 확인이 곧바로 돈다.
            pio_advanced=SimpleNamespace(
                call_poll_interval_sec=0.0,
                scenario_default_timeout_sec=2.0,
            ),
        ),
    )


def _action(**params):
    return SimpleNamespace(
        action_id="blink-test",
        action_type="pioScenario",
        action_parameters=[
            SimpleNamespace(key=key, value=value) for key, value in params.items()
        ],
    )


def _scenario(adapter, steps, **params):
    return asyncio.run(
        execute_pio_scenario(
            adapter,
            _action(pair="off", disconnect="off", scenario=steps, **params),
        )
    )


class BlinkStepTest(unittest.TestCase):
    def test_blink_stops_as_soon_as_the_facility_input_comes_on(self):
        ezi = _Ezi(input_pin=0, on_after=3)  # PIO in1 = EZI in0
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "timeoutSec": 5,
                }
            ],
        )

        self.assertTrue(result["ok"])
        self.assertIn("stopped by condition", result["steps"][0]["message"])
        # 조건이 서기 전에는 실제로 깜박였어야 한다. 한 번도 안 켜고 끝나면
        # 문을 잡아 두지 못한 채 성공으로 보고하는 것이다.
        self.assertIn((0, "on"), ezi.output_calls)

    def test_the_first_cycle_runs_even_when_the_condition_is_already_true(self):
        """종료 조건이 처음부터 참이면 예전에는 0회 깜박이고 성공으로 끝났다.

        설비는 문 열림 요청을 레벨이 아니라 반복 신호로 읽는다. 한 번도 안 켜고
        끝나면 문을 전혀 잡지 않은 것인데 결과는 FINISHED다 — 진출 구간에서
        "문이 안 열린 채 지나간다"로 나타난다. 첫 사이클은 조건과 무관하게
        온전히 돌아야 한다.
        """
        ezi = _Ezi(input_pin=0, on_after=0)  # in1이 처음부터 on
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "timeoutSec": 5,
                }
            ],
        )

        self.assertTrue(result["ok"])
        self.assertIn((0, "on"), ezi.output_calls)
        self.assertIn("blinked 1 time(s)", result["steps"][0]["message"])

    def test_a_condition_true_before_the_first_cycle_is_reported(self):
        """"처음부터 참"과 "깜박이다 참이 됨"은 다른 사건이라 구분해 남긴다.

        둘 다 stopped by condition으로만 적히면, untilIndex가 엉뚱한 핀을
        가리키고 있다는 사실이 로그에서 지워진다.
        """
        ezi = _Ezi(input_pin=0, on_after=0)
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "timeoutSec": 5,
                }
            ],
        )

        self.assertIn("already true at start", result["steps"][0]["message"])

    def test_a_condition_that_comes_later_is_not_marked_as_true_at_start(self):
        ezi = _Ezi(input_pin=0, on_after=3)
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "timeoutSec": 5,
                }
            ],
        )

        self.assertIn("stopped by condition", result["steps"][0]["message"])
        self.assertNotIn("already true at start", result["steps"][0]["message"])

    def test_min_cycles_holds_the_door_for_longer_than_one_cycle(self):
        """조건 핀을 못 믿는 동안 문을 잡아 둘 유일한 손잡이다.

        실제 값은 통과에 걸리는 시간을 재고 정한다 — 여기서는 손잡이가 도는지만
        본다.
        """
        ezi = _Ezi(input_pin=0, on_after=0)
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "minCycles": 3,
                    "timeoutSec": 5,
                }
            ],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [call for call in ezi.output_calls if call == (0, "on")],
            [(0, "on")] * 3,
        )

    def test_min_cycles_zero_lets_the_condition_stop_it_before_any_pulse(self):
        # 손잡이를 0으로 내리면 예전 동작이다. 문을 잡을 필요가 없고 조건만
        # 확인하면 되는 자리를 위해 남겨 둔다.
        ezi = _Ezi(input_pin=0, on_after=0)
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "minCycles": 0,
                    "timeoutSec": 5,
                }
            ],
        )

        self.assertTrue(result["ok"])
        self.assertNotIn((0, "on"), ezi.output_calls)
        self.assertIn("blinked 0 time(s)", result["steps"][0]["message"])

    def test_blink_always_leaves_the_output_off(self):
        # 조건이 ON 구간에서 서면 출력이 켜진 채로 끝나기 쉽다. 버튼을 누른 채
        # 두면 다음 recipe가 무엇을 눌러도 설비가 구분하지 못한다.
        ezi = _Ezi(input_pin=0, on_after=1)
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "timeoutSec": 5,
                }
            ],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(ezi.output_calls[-1], (0, "off"))
        self.assertEqual(ezi.outputs[0], 0)

    def test_count_bounds_the_blink_when_the_input_never_comes(self):
        # until 핀이 엉뚱한 곳을 가리켜도 액션은 끝나야 한다 — 안 끝나면 뒤의
        # HARD 액션이 영원히 기다린다. 대신 어느 조건으로 끝났는지 남긴다.
        ezi = _Ezi()
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "count": 3,
                    "timeoutSec": 5,
                }
            ],
        )

        self.assertTrue(result["ok"])
        self.assertIn("blinked 3 time(s)", result["steps"][0]["message"])
        self.assertIn("stopped by count", result["steps"][0]["message"])
        self.assertEqual(
            [call for call in ezi.output_calls if call == (0, "on")],
            [(0, "on")] * 3,
        )

    def test_blink_without_a_terminator_is_refused(self):
        adapter = _adapter(_Ezi())

        with self.assertRaises(ValueError) as caught:
            _scenario(
                adapter,
                [{"type": "blink", "index": 1, "onSec": 0, "offSec": 0}],
            )

        self.assertIn("until", str(caught.exception))

    def test_a_condition_that_never_comes_fails_instead_of_passing_quietly(self):
        ezi = _Ezi()
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {
                    "type": "blink", "index": 1,
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "timeoutSec": 0.05,
                }
            ],
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failedReason"], "timeout while blinking")
        self.assertEqual(ezi.outputs[0], 0)

    def test_blink_and_out_accept_a_signal_name(self):
        # 숫자를 recipe에 박으면 배선이 바뀔 때 recipe도 같이 고쳐야 한다.
        ezi = _Ezi(input_pin=0, on_after=1)
        adapter = _adapter(ezi)

        result = _scenario(
            adapter,
            [
                {"type": "out", "signal": "airShower3lOpen", "state": "on"},
                {
                    "type": "blink", "signal": "airShower3lOpen",
                    "onSec": 0, "offSec": 0,
                    "untilIndex": 1, "untilState": "on",
                    "timeoutSec": 5,
                },
            ],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["steps"][0]["message"], "out1=on")


class ScenarioPairingTest(unittest.TestCase):
    def test_pair_off_runs_without_touching_the_serial_link(self):
        # 통과 절차를 recipe 여러 개로 쪼개면 두 번째부터는 이미 붙어 있다.
        # 다시 걸면 SELECT를 올렸다 내리고 BC를 새로 보내느라 깜박임이 끊긴다.
        adapter = _adapter(_Ezi())

        result = _scenario(
            adapter,
            [{"type": "out", "index": 2, "state": "on"}],
        )

        self.assertTrue(result["ok"])
        self.assertIsNone(result["init"])

    def test_disconnect_off_is_honoured_as_a_string(self):
        # WebUI 폼은 "off"라는 문자열을 준다. bool("off")는 True라서, 문자열을
        # 그대로 bool로 쓰면 끄라는 지시가 조용히 무시된다.
        adapter = _adapter(_Ezi())

        result = _scenario(
            adapter,
            [{"type": "out", "index": 2, "state": "off"}],
        )

        # disconnect가 살아 있었다면 직렬 포트를 열려다 여기서 터졌다.
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
