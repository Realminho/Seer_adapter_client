"""tree-sitter 기반 HCL 편집기 테스트.

손수 만든 정규식 줄 스캐너를 대체한다. 요구는 두 가지다.

1. **바이트 충실도** — 편집한 범위 밖은 한 바이트도 바뀌지 않는다. 주석·정렬 보존이
   후처리 노력이 아니라 구조적 보장이어야 한다.
2. **여러 줄 값 도달** — 여러 줄 맵의 내부 항목과 여러 줄 리스트의 원소를
   개별로 지목할 수 있어야 한다. 줄단위 편집기가 못 하던 부분이다.
"""
import sys
import unittest
from pathlib import Path

ADAPTOR_DIR = Path(__file__).resolve().parent.parent
if str(ADAPTOR_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_DIR))

from core import hcledit  # noqa: E402

SAMPLE = '''# 파일 머리 주석
extension "pio" {
  pio_baudrate = 38400          # 직렬 통신 속도
  media        = 2

  output_signals = {
    elevatorOpen  = 5
    elevatorClose = 4
  }

  output_pins  = [0, 1, 2, 3]

  motion_rules = [
    { from = "a", to = "b", mode = "enter" },
    { from = "b", to = "c", mode = "inside" },
  ]
}

extension "elevator" {
  media = 9
}
'''

RECIPES = '''recipe "openDoor" {
  label       = "문 열기"
  timeout_sec = 60

  step "pioSelect" {
    timeout_sec = 5
    parameters  = { signal = "elevatorOpen", state = "on" }
  }

  cleanup "pioDisconnect" {}
}
'''


class ScanTestCase(unittest.TestCase):
    def test_finds_top_level_scalars(self):
        found = hcledit.scan_values(SAMPLE)
        self.assertIn(("pio", "pio_baudrate"), found)
        self.assertIn(("elevator", "media"), found)

    def test_finds_multiline_map_interior(self):
        """줄단위 편집기가 못 하던 부분이다."""
        found = hcledit.scan_values(SAMPLE)
        self.assertIn(("pio", "output_signals", "elevatorOpen"), found)
        self.assertIn(("pio", "output_signals", "elevatorClose"), found)

    def test_finds_list_elements_by_index(self):
        found = hcledit.scan_values(SAMPLE)
        self.assertIn(("pio", "motion_rules", 0), found)
        self.assertIn(("pio", "motion_rules", 1), found)
        self.assertIn(("pio", "output_pins", 0), found)

    def test_finds_nested_block_values(self):
        found = hcledit.scan_values(RECIPES)
        self.assertIn(("openDoor", "timeout_sec"), found)
        self.assertIn(("openDoor", "pioSelect", "timeout_sec"), found)
        self.assertIn(("openDoor", "pioSelect", "parameters", "signal"), found)

    def test_reports_parsed_values(self):
        found = hcledit.scan_values(SAMPLE)
        self.assertEqual(found[("pio", "pio_baudrate")].value, 38400)
        self.assertEqual(found[("pio", "output_signals", "elevatorOpen")].value, 5)
        self.assertEqual(found[("elevator", "media")].value, 9)

    def test_reports_line_numbers(self):
        found = hcledit.scan_values(SAMPLE)
        self.assertEqual(found[("pio", "pio_baudrate")].line, 3)


class SetValueTestCase(unittest.TestCase):
    def assert_only_changed(self, before, after, expected_lines):
        """편집한 줄 수를 세고, 나머지 줄이 그대로인지 확인한다."""
        import difflib

        diff = [
            line
            for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        ]
        self.assertEqual(len(diff), expected_lines, diff)

    def test_sets_scalar_and_keeps_comment_and_alignment(self):
        out = hcledit.set_value(SAMPLE, ("pio", "pio_baudrate"), 9600)
        self.assertIn("pio_baudrate = 9600          # 직렬 통신 속도", out)
        self.assert_only_changed(SAMPLE, out, 2)

    def test_sets_multiline_map_interior(self):
        out = hcledit.set_value(SAMPLE, ("pio", "output_signals", "elevatorOpen"), 9)
        self.assertIn("elevatorOpen  = 9", out)
        self.assertIn("elevatorClose = 4", out)
        self.assert_only_changed(SAMPLE, out, 2)

    def test_sets_list_element_by_index(self):
        out = hcledit.set_value(SAMPLE, ("pio", "output_pins", 2), 7)
        self.assertIn("[0, 1, 7, 3]", out)
        self.assert_only_changed(SAMPLE, out, 2)

    def test_sets_multiline_list_element(self):
        out = hcledit.set_value(
            SAMPLE, ("pio", "motion_rules", 1), {"from": "x", "to": "y", "mode": "passed"}
        )
        self.assertIn('{ from = "a", to = "b", mode = "enter" }', out)
        self.assertIn('mode = "passed"', out)
        self.assert_only_changed(SAMPLE, out, 2)

    def test_sets_string_with_quotes(self):
        out = hcledit.set_value(RECIPES, ("openDoor", "label"), "닫기")
        self.assertIn('label       = "닫기"', out)
        self.assert_only_changed(RECIPES, out, 2)

    def test_sets_nested_block_value(self):
        out = hcledit.set_value(RECIPES, ("openDoor", "pioSelect", "timeout_sec"), 99)
        self.assertIn("timeout_sec = 99", out)
        self.assertIn("timeout_sec = 60", out)  # recipe 직속은 그대로
        self.assert_only_changed(RECIPES, out, 2)

    def test_sets_inline_map_member(self):
        out = hcledit.set_value(RECIPES, ("openDoor", "pioSelect", "parameters", "state"), "off")
        self.assertIn('{ signal = "elevatorOpen", state = "off" }', out)
        self.assert_only_changed(RECIPES, out, 2)

    def test_boolean_and_float_literals(self):
        out = hcledit.set_value(SAMPLE, ("pio", "media"), True)
        self.assertIn("media        = true", out)
        out2 = hcledit.set_value(SAMPLE, ("pio", "media"), 1.5)
        self.assertIn("media        = 1.5", out2)

    def test_replaces_whole_container(self):
        out = hcledit.set_value(SAMPLE, ("pio", "output_pins"), [9, 8])
        self.assertIn("output_pins  = [9, 8]", out)
        self.assert_only_changed(SAMPLE, out, 2)

    def test_missing_path_raises(self):
        with self.assertRaises(KeyError):
            hcledit.set_value(SAMPLE, ("pio", "nope"), 1)
        with self.assertRaises(KeyError):
            hcledit.set_value(SAMPLE, ("nope", "media"), 1)
        with self.assertRaises(KeyError):
            hcledit.set_value(SAMPLE, ("pio", "output_pins", 99), 1)

    def test_result_is_still_valid_hcl(self):
        import hcl2

        out = hcledit.set_value(SAMPLE, ("pio", "pio_baudrate"), 9600)
        self.assertTrue(hcl2.loads(out))


class RemoveValueTestCase(unittest.TestCase):
    def test_removes_scalar_line(self):
        out = hcledit.remove_value(SAMPLE, ("pio", "media"))
        self.assertNotIn("media        = 2", out)
        self.assertIn("media = 9", out)  # 다른 블록의 동명 키는 유지
        self.assertIn("pio_baudrate = 38400", out)

    def test_removed_result_is_valid_hcl(self):
        import hcl2

        out = hcledit.remove_value(SAMPLE, ("pio", "media"))
        self.assertTrue(hcl2.loads(out))

    def test_missing_path_raises(self):
        with self.assertRaises(KeyError):
            hcledit.remove_value(SAMPLE, ("pio", "nope"))


class RealFileTestCase(unittest.TestCase):
    """실제 배포 파일로 확인한다."""

    def files(self):
        for name in ("extensions.hcl", "recipes.hcl", "robots.hcl"):
            path = ADAPTOR_DIR / "config" / name
            if path.exists():
                yield name, path.read_text(encoding="utf-8")

    def test_parses_without_error(self):
        for name, text in self.files():
            self.assertEqual(hcledit.parse_error_count(text), 0, name)

    def test_scan_finds_values(self):
        for name, text in self.files():
            self.assertGreater(len(hcledit.scan_values(text)), 0, name)

    def test_every_scanned_value_is_writable(self):
        """스캔이 내준 경로는 전부 실제로 써져야 한다.

        여기가 어긋나면 화면엔 입력란이 보이는데 저장이 반드시 실패한다.
        """
        import hcl2

        for name, text in self.files():
            for path, found in hcledit.scan_values(text).items():
                out = hcledit.set_value(text, path, found.value)
                self.assertTrue(hcl2.loads(out), f"{name} {path}")

    def test_rewriting_same_scalar_is_a_no_op(self):
        """같은 낱값을 다시 쓰면 파일이 한 바이트도 안 바뀌어야 한다.

        컨테이너는 제외한다 — 여러 줄로 적힌 맵/리스트를 구조에서 되쓰면
        한 줄 표기가 되므로 원문과 다른 게 정상이다.
        """
        checked = 0
        for name, text in self.files():
            for path, found in hcledit.scan_values(text).items():
                if isinstance(found.value, (dict, list)):
                    continue
                out = hcledit.set_value(text, path, found.value)
                self.assertEqual(out, text, f"{name} {path}")
                checked += 1
        self.assertGreater(checked, 0)

class RawExpressionTestCase(unittest.TestCase):
    """`var.X` 자리표시자는 리터럴이 아니다. 문자열로 취급하면 recipe 가 깨진다."""

    TEXT = (
        'recipe "r" {\n'
        '  step "s" {\n'
        '    parameters = { station = var.pioStationId, n = 1 }\n'
        '    label      = "${var.name}-tail"\n'
        '  }\n'
        '}\n'
    )

    def test_expression_is_not_a_plain_string(self):
        found = hcledit.scan_values(self.TEXT)
        v = found[("r", "s", "parameters", "station")].value
        self.assertIsInstance(v, hcledit.RawExpression)
        self.assertEqual(str(v), "var.pioStationId")

    def test_rewriting_expression_keeps_it_unquoted(self):
        found = hcledit.scan_values(self.TEXT)
        v = found[("r", "s", "parameters", "station")].value
        out = hcledit.set_value(self.TEXT, ("r", "s", "parameters", "station"), v)
        self.assertEqual(out, self.TEXT)
        self.assertIn("station = var.pioStationId", out)

    def test_writing_a_real_string_still_quotes(self):
        out = hcledit.set_value(self.TEXT, ("r", "s", "parameters", "station"), "000010")
        self.assertIn('station = "000010"', out)

    def test_interpolated_string_round_trips(self):
        found = hcledit.scan_values(self.TEXT)
        v = found[("r", "s", "label")].value
        out = hcledit.set_value(self.TEXT, ("r", "s", "label"), v)
        self.assertEqual(out, self.TEXT)


class DuplicateBlockLabelTestCase(unittest.TestCase):
    """같은 라벨 블록이 반복되면 2번째부터 `라벨#N` 으로 구분한다."""

    TEXT = (
        'recipe "r" {\n'
        '  step "w" { state = "on" }\n'
        '  step "w" { state = "off" }\n'
        '}\n'
    )

    def test_both_occurrences_are_addressable(self):
        found = hcledit.scan_values(self.TEXT)
        self.assertEqual(found[("r", "w", "state")].value, "on")
        self.assertEqual(found[("r", "w#1", "state")].value, "off")

    def test_edits_target_the_right_occurrence(self):
        out = hcledit.set_value(self.TEXT, ("r", "w#1", "state"), "hold")
        self.assertIn('step "w" { state = "on" }', out)
        self.assertIn('step "w" { state = "hold" }', out)


if __name__ == "__main__":
    unittest.main()


def test_set_value_creates_missing_nested_blocks():
    """기본값만 쓰는 섹션은 파일에 없다. 그래도 값을 고정할 수 있어야 한다."""
    text = 'pio {\n  station = "A"\n}\n'
    out = hcledit.set_value(text, ("pio", "advanced", "ping_timeout"), 6, create=True)
    assert 'station = "A"' in out  # 기존 줄 보존
    assert hcledit.parse_error_count(out) == 0
    found = hcledit.scan_values(out)
    assert found[("pio", "advanced", "ping_timeout")].value == 6
    assert found[("pio", "station")].value == "A"


def test_set_value_creates_missing_top_level_block():
    text = 'pio {\n  station = "A"\n}\n'
    out = hcledit.set_value(text, ("dock", "timeout"), 30, create=True)
    assert hcledit.parse_error_count(out) == 0
    found = hcledit.scan_values(out)
    assert found[("dock", "timeout")].value == 30
    assert found[("pio", "station")].value == "A"


def test_set_value_refuses_to_invent_indexed_block_label():
    text = "openDoor {\n}\n"
    try:
        hcledit.set_value(text, ("openDoor", "step#3", "state"), 1, create=True)
    except KeyError as exc:
        assert "만들 수 없는" in str(exc)
    else:
        raise AssertionError("중복 라벨 블록을 만들면 안 됨")
