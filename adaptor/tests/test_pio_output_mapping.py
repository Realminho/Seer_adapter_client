# adaptor/tests/test_pio_output_mapping.py
"""PIO out 번호 <-> EZI IO 핀, 그리고 설비 신호 이름 -> out 번호 매핑.

recipes.hcl은 config를 읽지 못하므로 숫자를 적어 두면 배선이 바뀔 때마다
recipe도 같이 고쳐야 한다. signal 이름으로 부르면 풀이가 전부 extensions.hcl에서
끝난다: 이름 -> output_signals -> output_pin_map -> EZI IO 핀.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import PioConfig
from extensions.pio import (
    known_output_signals,
    pio_output_pin,
    pio_signal_index,
    resolve_pio_output_index,
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


class OutputPinMapTest(unittest.TestCase):
    def test_hcl_string_keys_are_normalized_to_ints(self):
        # HCL 맵 키는 항상 문자열이다: output_pin_map = { 5 = 4 } -> {"5": 4}.
        # 정규화가 없으면 int 키 조회가 늘 빗나가 조용히 output_pins로 떨어진다.
        config = _pio(output_pin_map={"5": 4, "4": 3})
        self.assertEqual(config.output_pin_map, {5: 4, 4: 3})
        self.assertEqual(pio_output_pin(_adapter(config), 5), 4)

    def test_the_map_wins_over_the_positional_list(self):
        config = _pio(
            output_pins=[0, 1, 2, 3, 4, 5, 6, 7],
            output_pin_map={"5": 11},
        )
        self.assertEqual(pio_output_pin(_adapter(config), 5), 11)

    def test_the_positional_list_still_works_without_a_map(self):
        # 배포된 기존 config를 깨지 않는다.
        config = _pio(output_pins=[0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(pio_output_pin(_adapter(config), 5), 4)

    def test_an_out_missing_from_the_map_names_what_is_declared(self):
        config = _pio(output_pin_map={"1": 0, "2": 1})
        with self.assertRaises(ValueError) as caught:
            pio_output_pin(_adapter(config), 5)
        self.assertIn("output_pin_map", str(caught.exception))
        self.assertIn("1, 2", str(caught.exception))

    def test_two_outs_on_one_pin_are_rejected(self):
        # 1:1이 아니면 한 점을 켤 때 다른 점이 따라 켜진다.
        with self.assertRaises(ValueError) as caught:
            _pio(output_pin_map={"1": 4, "2": 4})
        self.assertIn("1:1", str(caught.exception))

    def test_out_of_range_values_are_rejected_at_config_load(self):
        with self.assertRaises(ValueError):
            _pio(output_pin_map={"9": 0})      # PIO out은 1~8
        with self.assertRaises(ValueError):
            _pio(output_pin_map={"1": 16})     # EZI IO 핀은 0~15


class OutputSignalTest(unittest.TestCase):
    def test_signal_resolves_through_to_the_ezi_io_pin(self):
        config = _pio(
            output_pin_map={"5": 4, "4": 3},
            output_signals={"elevatorOpen": 5, "elevatorClose": 4},
        )
        adapter = _adapter(config)
        self.assertEqual(pio_signal_index(adapter, "elevatorOpen"), 5)
        self.assertEqual(pio_output_pin(adapter, 5), 4)

    def test_elevator_open_number_is_configurable(self):
        config = _pio(
            output_pin_map={str(index): index - 1 for index in range(1, 9)},
            output_signals={"elevatorOpen": 5},
        )
        adapter = _adapter(config)
        index = pio_signal_index(adapter, "elevatorOpen")
        self.assertEqual(index, 5)
        self.assertEqual(pio_output_pin(adapter, index), 4)

    def test_unknown_signal_lists_the_declared_names(self):
        config = _pio(output_signals={"elevatorOpen": 5, "elevatorClose": 4})
        with self.assertRaises(ValueError) as caught:
            pio_signal_index(_adapter(config), "elevatorOpn")
        message = str(caught.exception)
        self.assertIn("elevatorOpn", message)
        self.assertIn("elevatorClose, elevatorOpen", message)

    def test_out_of_range_signal_index_is_rejected_at_config_load(self):
        with self.assertRaises(ValueError):
            _pio(output_signals={"elevatorOpen": 9})

    def test_known_output_signals_are_sorted_for_the_webui(self):
        config = _pio(output_signals={"elevatorOpen": 5, "airShowerEntry": 1})
        self.assertEqual(
            known_output_signals(SimpleNamespace(pio_config=config)),
            ("airShowerEntry", "elevatorOpen"),
        )


class ResolveOutputIndexTest(unittest.TestCase):
    def setUp(self):
        self.adapter = _adapter(
            _pio(
                output_pin_map={"4": 3, "3": 2},
                output_signals={"elevatorOpen": 4},
            )
        )

    def test_signal_parameter_resolves(self):
        self.assertEqual(
            resolve_pio_output_index(self.adapter, {"signal": "elevatorOpen"}), 4
        )

    def test_index_parameter_still_works(self):
        # WebUI에서 out 번호를 직접 눌러 보는 경로는 그대로 둔다.
        self.assertEqual(resolve_pio_output_index(self.adapter, {"index": 4}), 4)

    def test_both_at_once_is_rejected(self):
        # 조용히 하나를 고르면 다른 점을 치고도 성공으로 보인다.
        with self.assertRaises(ValueError):
            resolve_pio_output_index(
                self.adapter, {"signal": "elevatorOpen", "index": 2}
            )

    def test_neither_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_pio_output_index(self.adapter, {"state": "on"})

    def test_blank_values_count_as_absent(self):
        # WebUI는 비운 칸을 ""로 보낸다. signal을 쓰면 index 칸은 비어 온다.
        self.assertEqual(
            resolve_pio_output_index(
                self.adapter, {"signal": "elevatorOpen", "index": ""}
            ),
            4,
        )
        self.assertEqual(
            resolve_pio_output_index(self.adapter, {"signal": "", "index": 4}), 4
        )


class ShippedConfigTest(unittest.TestCase):
    def test_shipped_elevator_signals_use_the_one_based_positional_list(self):
        """배포 config의 신호 값은 PIO out 번호(1-based)지 EZI IO 핀이 아니다.

        둘은 한 칸 차이라 섞여도 값이 그럴듯해 보인다. 이 현장의 output_pins는
        위치 리스트(i번째 = out i+1)이므로 out N은 EZI IO N-1이어야 한다.

        문 신호는 층마다 이름이 갈라져 있다(elevator1fOpen/elevator2fOpen). 같은
        네 가닥이 어느 station에 붙었느냐에 따라 다른 뜻이 되기 때문이다 —
        1층은 out1/out2가 층 호출이고 out3/out4가 문, 상층은 그 반대다.
        """
        from config.config import get_config

        config = get_config()
        pio = config.pio_config

        elevator_signals = sorted(
            name for name in pio.output_signals if name.startswith("elevator")
        )
        # 이름이 통째로 바뀌면(예전 elevatorOpen 하나에서 층별로) 아래 루프가
        # 조용히 0바퀴 돌 수 있다. 층당 4가닥 x 2층은 있어야 한다.
        self.assertGreaterEqual(len(elevator_signals), 8, elevator_signals)

        for signal in elevator_signals:
            index = pio.output_signals[signal]
            self.assertTrue(1 <= index <= 8, f"{signal}={index} is not a PIO out")
            self.assertEqual(pio.output_pins[index - 1], index - 1)


if __name__ == "__main__":
    unittest.main()
