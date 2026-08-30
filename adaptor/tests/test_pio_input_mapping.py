# adaptor/tests/test_pio_input_mapping.py
"""PIO 입력 신호 이름 -> in 번호 매핑, 그리고 pioScenario "in" 단계의 signal.

출력은 이름 -> output_signals -> output_pin_map -> EZI IO 핀으로 풀리는데,
입력에는 그 첫 칸이 없었다. recipes.hcl의 문 열림 확인 단계는
`{ type = "in", signal = "doorSensor3l" }`로 적혀 있었지만 실행 쪽은 `index`만
읽어서, 문 pulse를 내보낸 뒤 마지막 확인 단계에서 늘 ValueError로 죽었다.

입력 이름을 output_signals에 얹어 두면 방향이 섞인다 — 같은 번호가 입력·출력
레지스터에 각각 있고 뜻이 다르다(out2 = 4L 문 열기 요청, in2 = 4L 문 열림 확인).
그래서 input_signals를 따로 둔다.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import PioConfig
from extensions.pio import (
    execute_pio_scenario,
    known_input_signals,
    pio_input_signal_index,
    resolve_pio_input_index,
)


def _pio(**kwargs):
    base = dict(
        pio_serial_port="/dev/ttyUSB0",
        pio_baudrate=38400,
        media=2,
        port=0,
        vehicle_num="OHT123",
    )
    base.update(kwargs)
    return PioConfig(**base)


def _adapter(pio_config):
    return SimpleNamespace(config=SimpleNamespace(pio_config=pio_config))


class InputSignalMapTest(unittest.TestCase):
    def test_hcl_string_values_are_normalized_to_ints(self):
        # HCL 맵 값은 문자열로 들어올 수 있다. int로 못 박지 않으면 in 번호
        # 비교가 늘 빗나간다.
        config = _pio(input_signals={"doorSensor3l": "1"})
        self.assertEqual(config.input_signals, {"doorSensor3l": 1})

    def test_out_of_range_input_index_is_rejected_at_config_load(self):
        with self.assertRaises(ValueError):
            _pio(input_signals={"doorSensor3l": 9})

    def test_the_error_names_input_signals_not_output_signals(self):
        # 두 맵을 헷갈리면 엉뚱한 쪽을 고치게 된다.
        with self.assertRaises(ValueError) as ctx:
            _pio(input_signals={"doorSensor3l": 9})
        self.assertIn("input_signals", str(ctx.exception))

    def test_signal_index_resolves(self):
        adapter = _adapter(_pio(input_signals={"doorSensor4l": 2}))
        self.assertEqual(pio_input_signal_index(adapter, "doorSensor4l"), 2)

    def test_unknown_signal_lists_the_declared_input_names(self):
        adapter = _adapter(
            _pio(input_signals={"doorSensor3l": 1, "doorSensor4l": 2})
        )
        with self.assertRaises(ValueError) as ctx:
            pio_input_signal_index(adapter, "doorSensor9l")
        message = str(ctx.exception)
        self.assertIn("input_signals", message)
        self.assertIn("doorSensor3l", message)
        self.assertIn("doorSensor4l", message)

    def test_an_output_name_does_not_resolve_as_an_input(self):
        # 방향이 섞이면 out2를 눌러 놓고 in2를 읽었다고 믿게 된다.
        # input_signals를 비워 두면 "맵이 비어서" 실패해도 통과해 버린다 —
        # 채워 두어야 조회가 정말 입력 맵만 보는지 검사한다.
        adapter = _adapter(
            _pio(
                output_signals={"airShower4lOpen": 2},
                input_signals={"doorSensor4l": 2},
            )
        )
        with self.assertRaises(ValueError):
            pio_input_signal_index(adapter, "airShower4lOpen")

    def test_known_input_signals_are_sorted_for_the_webui(self):
        config = _pio(input_signals={"doorSensor4l": 2, "doorSensor3l": 1})
        self.assertEqual(
            known_input_signals(SimpleNamespace(pio_config=config)),
            ("doorSensor3l", "doorSensor4l"),
        )


class ResolveInputIndexTest(unittest.TestCase):
    def setUp(self):
        self.adapter = _adapter(
            _pio(input_signals={"doorSensor3l": 1, "doorSensor4l": 2})
        )

    def test_signal_parameter_resolves(self):
        self.assertEqual(
            resolve_pio_input_index(self.adapter, {"signal": "doorSensor4l"}), 2
        )

    def test_index_parameter_still_works(self):
        self.assertEqual(resolve_pio_input_index(self.adapter, {"index": 3}), 3)

    def test_both_at_once_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_pio_input_index(
                self.adapter, {"signal": "doorSensor4l", "index": 3}
            )

    def test_neither_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_pio_input_index(self.adapter, {"state": "on"})

    def test_blank_values_count_as_absent(self):
        # WebUI는 비운 칸을 ""로 보낸다.
        self.assertEqual(
            resolve_pio_input_index(
                self.adapter, {"signal": "doorSensor4l", "index": ""}
            ),
            2,
        )
        self.assertEqual(
            resolve_pio_input_index(self.adapter, {"signal": "", "index": 3}), 3
        )


class _Ezi:
    """지정한 입력 핀만 켜 두고 출력은 되읽어 주는 EZI IO 대역."""

    def __init__(self, on_input_pins=()):
        self.outputs = [0] * 16
        self.on_input_pins = set(on_input_pins)

    async def turn_on_output(self, pin):
        self.outputs[pin] = 1

    async def turn_off_output(self, pin):
        self.outputs[pin] = 0

    async def get_output(self):
        return {"outputs": list(self.outputs)}

    async def get_input(self):
        bits = [0] * 16
        for pin in self.on_input_pins:
            bits[pin] = 1
        return {"inputs": bits}


def _scenario_adapter(ezi, pio_config):
    return SimpleNamespace(
        _ezi_io=ezi,
        _pio_outputs={},
        _note_pio=lambda **kwargs: None,
        _note_ezio=lambda **kwargs: None,
        state=None,
        config=SimpleNamespace(
            pio_config=pio_config,
            pio_advanced=SimpleNamespace(
                call_poll_interval_sec=0.0,
                scenario_default_timeout_sec=2.0,
                read_default_timeout_sec=2.0,
            ),
        ),
    )


def _run(adapter, steps):
    action = SimpleNamespace(
        action_id="in-signal-test",
        action_type="pioScenario",
        action_parameters=[
            SimpleNamespace(key="pair", value="off"),
            SimpleNamespace(key="disconnect", value="off"),
            SimpleNamespace(key="scenario", value=steps),
        ],
    )
    return asyncio.run(execute_pio_scenario(adapter, action))


class ScenarioInStepTest(unittest.TestCase):
    """recipes.hcl의 문 열림 확인 단계가 실제로 도는지."""

    def _adapter(self, on_input_pins=()):
        return _scenario_adapter(
            _Ezi(on_input_pins),
            _pio(
                output_signals={"airShower3lOpen": 1},
                input_signals={"doorSensor3l": 1, "doorSensor4l": 2},
            ),
        )

    def test_in_step_accepts_a_signal_name(self):
        # PIO in1 = EZI in0. 켜져 있으므로 확인이 통과해야 한다.
        result = _run(
            self._adapter(on_input_pins=[0]),
            [{"type": "in", "signal": "doorSensor3l", "state": "on",
              "timeoutSec": 0.2}],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["steps"][0]["message"], "in1=on")

    def test_the_door_open_check_fails_by_timeout_not_by_valueerror(self):
        # 신호가 안 오면 "timeout waiting input"이어야 한다. index를 못 읽어
        # 죽는 것과 설비가 응답을 안 하는 것은 다른 사건이다.
        result = _run(
            self._adapter(on_input_pins=[]),
            [{"type": "in", "signal": "doorSensor3l", "state": "on",
              "timeoutSec": 0.05}],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["failedReason"], "timeout waiting input")

    def test_a_full_open_recipe_body_runs_to_the_end(self):
        # recipes.hcl의 airShower3l-4lOpenIn 본문 모양 그대로.
        result = _run(
            self._adapter(on_input_pins=[0]),
            [
                {"type": "out", "signal": "airShower3lOpen", "state": "on"},
                {"type": "delay", "sec": 0},
                {"type": "out", "signal": "airShower3lOpen", "state": "off"},
                {"type": "in", "signal": "doorSensor3l", "state": "on",
                 "timeoutSec": 0.2},
            ],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["steps"]), 4)


class ShippedConfigTest(unittest.TestCase):
    def test_door_sensors_are_declared_as_inputs_not_outputs(self):
        """doorSensor는 설비 -> 로봇 신호다. 출력 맵에 있으면 방향이 섞인다."""
        from config.config import get_config

        pio = get_config().pio_config
        for name in ("doorSensor3l", "doorSensor4l"):
            self.assertIn(name, pio.input_signals)
            self.assertNotIn(name, pio.output_signals)
        self.assertEqual(pio.input_signals["doorSensor3l"], 1)
        self.assertEqual(pio.input_signals["doorSensor4l"], 2)

    def test_every_shipped_scenario_in_step_resolves_to_a_declared_input(self):
        """recipes.hcl의 `type = "in"` 단계가 실행 전에 풀리는지 여기서 잡는다.

        안 풀리면 문 pulse를 내보낸 **뒤** 마지막 확인 단계에서 죽는다 — 문은
        열리고 액션만 FAILED라, 사람 눈에는 "되는데 안 되는" 형태로 보인다.

        배포 recipe에 blink 단계는 없다(설비 센서에 유지력이 있어 걷어냈다).
        blink 자체의 동작은 tests/test_pio_scenario_blink.py가 지킨다.
        """
        from config.config import get_config

        config = get_config()
        adapter = _adapter(config.pio_config)
        checked = []
        for recipe in config.recipes:
            for step in recipe.steps:
                scenario = step.parameters.get("scenario")
                if not isinstance(scenario, list):
                    continue
                for entry in scenario:
                    if not isinstance(entry, dict) or entry.get("type") != "in":
                        continue
                    checked.append((recipe.action_type, entry.get("signal")))
                    resolve_pio_input_index(adapter, entry)
        # 문 열림 확인 단계가 통째로 사라지면 위 루프가 조용히 0바퀴 돈다.
        self.assertTrue(checked, "no pioScenario in-step found in shipped recipes")


if __name__ == "__main__":
    unittest.main()
