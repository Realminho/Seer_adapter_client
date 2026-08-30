"""config/hcl.py — HCL 파싱과 라벨 블록 정규화."""

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config import hcl


def _write(tmp: str, name: str, body: str) -> Path:
    path = Path(tmp) / name
    path.write_text(textwrap.dedent(body).strip(), encoding="utf-8")
    return path


class LoadHclTest(unittest.TestCase):
    def test_strips_quotes_and_block_markers(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "sample.hcl",
                """
                # 주석은 결과에 남지 않는다
                robot "ROBOT-A" {
                  vehicle_ip = "10.0.0.11"
                  vehicle_port = 7273
                  simulator = true
                  extra_args = ["--x"]
                }
                """,
            )
            data = hcl.load_hcl(path)

        self.assertNotIn("__comments__", data)
        entry = data["robot"][0]["ROBOT-A"]
        self.assertNotIn("__is_block__", entry)
        self.assertEqual(entry["vehicle_ip"], "10.0.0.11")
        self.assertEqual(entry["vehicle_port"], 7273)
        self.assertIs(entry["simulator"], True)
        self.assertEqual(entry["extra_args"], ["--x"])

    def test_preserves_nested_block_order(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "sample.hcl",
                """
                recipe "r" {
                  step "first" {}
                  step "second" {}
                  step "third" {}
                }
                """,
            )
            data = hcl.load_hcl(path)

        steps = data["recipe"][0]["r"]["step"]
        self.assertEqual([next(iter(s)) for s in steps], ["first", "second", "third"])

    def test_keeps_var_interpolation_unevaluated(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "sample.hcl",
                """
                recipe "r" {
                  step "s" {
                    parameters = { index = var.doorPin, name = "dock-${var.n}" }
                  }
                }
                """,
            )
            data = hcl.load_hcl(path)

        params = data["recipe"][0]["r"]["step"][0]["s"]["parameters"]
        self.assertEqual(params["index"], "${var.doorPin}")
        self.assertEqual(params["name"], "dock-${var.n}")

    def test_missing_file_raises_hcl_error(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(hcl.HclError) as ctx:
                hcl.load_hcl(Path(tmp) / "nope.hcl")
        self.assertIn("nope.hcl", str(ctx.exception))

    def test_syntax_error_mentions_path(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "bad.hcl", 'robot "A" {\n  vehicle_ip =\n}')
            with self.assertRaises(hcl.HclError) as ctx:
                hcl.load_hcl(path)
        self.assertIn("bad.hcl", str(ctx.exception))

    def test_block_with_no_label_raises(self):
        """`robot { ... }` (missing quotes) must not silently become a body."""
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "nolabel.hcl", 'robot {\n  vehicle_ip = "10.0.0.1"\n}')
            with self.assertRaises(hcl.HclError) as ctx:
                hcl.load_hcl(path)
        self.assertIn("nolabel.hcl", str(ctx.exception))

    def test_block_with_extra_label_raises(self):
        """`robot "A" "B" { ... }` must not silently drop the real body."""
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "extralabel.hcl", 'robot "A" "B" {\n  vehicle_ip = "1"\n}')
            with self.assertRaises(hcl.HclError) as ctx:
                hcl.load_hcl(path)
        self.assertIn("extralabel.hcl", str(ctx.exception))


class BlocksTest(unittest.TestCase):
    def test_returns_label_body_pairs_in_order(self):
        data = {"robot": [{"A": {"x": 1}}, {"B": {"y": 2}}]}
        self.assertEqual(hcl.blocks(data, "robot"), [("A", {"x": 1}), ("B", {"y": 2})])

    def test_missing_kind_returns_empty(self):
        self.assertEqual(hcl.blocks({}, "robot"), [])

    def test_unlabelled_block_raises(self):
        with self.assertRaises(hcl.HclError):
            hcl.blocks({"robot": [{}]}, "robot")

    def test_non_dict_body_raises(self):
        """A body that isn't a dict (e.g. a scalar) must not become {}."""
        with self.assertRaises(hcl.HclError):
            hcl.blocks({"robot": [{"vehicle_ip": "10.0.0.1"}]}, "robot")

    def test_attribute_list_of_single_key_objects_is_not_a_block(self):
        """평범한 속성 값이 라벨 검증에 걸리면 안 된다.

        블록에만 __is_block__ 마커가 붙는다. 마커 없는 리스트 항목은
        `{label: {label: body}}`와 모양이 같아도 블록이 아니다.
        """
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "sample.hcl",
                """
                recipe "r" {
                  foo = [{ x = { y = 1 } }]
                  bar = [{ type = "in", index = 4 }]
                  deep = [{ a = { b = { c = 1 } } }]
                }
                """,
            )
            data = hcl.load_hcl(path)

        body = data["recipe"][0]["r"]
        self.assertEqual(body["foo"], [{"x": {"y": 1}}])
        self.assertEqual(body["bar"], [{"type": "in", "index": 4}])
        self.assertEqual(body["deep"], [{"a": {"b": {"c": 1}}}])

    def test_param_pattern_matches_whole_string_only(self):
        self.assertTrue(hcl.PARAM_PATTERN.fullmatch("${var.doorPin}"))
        self.assertIsNone(hcl.PARAM_PATTERN.fullmatch("dock-${var.n}"))


if __name__ == "__main__":
    unittest.main()
