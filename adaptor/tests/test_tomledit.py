"""tomlkit 기반 TOML 편집기 테스트.

`core/hcledit.py` 와 같은 경로 기반 인터페이스를 제공해, 상위 계층이 파일 형식을
분기하지 않게 한다. 손수 만든 `configio.rewrite_scalar` 계열을 대체한다.
"""
import sys
import unittest
from pathlib import Path

ADAPTOR_DIR = Path(__file__).resolve().parent.parent
if str(ADAPTOR_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_DIR))

from core import tomledit  # noqa: E402

SAMPLE = '''# 파일 머리 주석
[mqtt_broker]
host = "192.168.3.108"              # MQTT broker address
port = 11883                        # MQTT broker port

[video]
stream_topics = [
    "/camera/cam2/image_raw",
    "/camera/cam3/image_raw",
]
snapshot_events = ["brake", "slowdown"]

[dock]
timeout_s = 30
enabled   = true
'''

ROOT_ARRAY = '''# 규칙 설명 주석
motion_rules = [
  { to = "1_01CH", mode = "dock" },
  { from = "A", to = "B", mode = "move" },
]

[dock]
timeout_s = 30
'''


class ScanTestCase(unittest.TestCase):
    def test_finds_table_scalars(self):
        found = tomledit.scan_values(SAMPLE)
        self.assertIn(("mqtt_broker", "port"), found)
        self.assertIn(("dock", "enabled"), found)

    def test_finds_list_elements_by_index(self):
        found = tomledit.scan_values(SAMPLE)
        self.assertIn(("video", "stream_topics", 0), found)
        self.assertIn(("video", "stream_topics", 1), found)

    def test_finds_root_array_of_tables(self):
        """줄단위 편집기가 `(array)` 라는 가짜 키로 뭉개던 부분이다."""
        found = tomledit.scan_values(ROOT_ARRAY)
        self.assertIn(("motion_rules", 0, "mode"), found)
        self.assertIn(("motion_rules", 1, "from"), found)

    def test_reports_values(self):
        found = tomledit.scan_values(SAMPLE)
        self.assertEqual(found[("mqtt_broker", "port")].value, 11883)
        self.assertEqual(found[("dock", "enabled")].value, True)
        self.assertEqual(found[("video", "stream_topics", 0)].value, "/camera/cam2/image_raw")


class SetValueTestCase(unittest.TestCase):
    def changed_lines(self, before, after):
        import difflib

        return [
            l
            for l in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
            if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))
        ]

    def test_sets_scalar_keeping_comment_and_alignment(self):
        out = tomledit.set_value(SAMPLE, ("mqtt_broker", "port"), 11884)
        self.assertIn("port = 11884                        # MQTT broker port", out)
        self.assertEqual(len(self.changed_lines(SAMPLE, out)), 2)

    def test_sets_multiline_list_element(self):
        """여러 줄 리스트 원소 — 손수 만든 편집기가 못 하던 것."""
        out = tomledit.set_value(SAMPLE, ("video", "stream_topics", 1), "/camera/cam9/image_raw")
        self.assertIn("/camera/cam9/image_raw", out)
        self.assertIn("/camera/cam2/image_raw", out)
        self.assertEqual(len(self.changed_lines(SAMPLE, out)), 2)

    def test_sets_root_array_member(self):
        out = tomledit.set_value(ROOT_ARRAY, ("motion_rules", 0, "mode"), "move")
        self.assertIn('{ to = "1_01CH", mode = "move" }', out)
        self.assertIn("# 규칙 설명 주석", out)
        self.assertEqual(len(self.changed_lines(ROOT_ARRAY, out)), 2)

    def test_sets_inline_list_whole(self):
        out = tomledit.set_value(SAMPLE, ("video", "snapshot_events"), ["a"])
        self.assertIn('snapshot_events = ["a"]', out)

    def test_missing_path_raises(self):
        with self.assertRaises(KeyError):
            tomledit.set_value(SAMPLE, ("dock", "nope"), 1)
        with self.assertRaises(KeyError):
            tomledit.set_value(SAMPLE, ("nope", "timeout_s"), 1)
        with self.assertRaises(KeyError):
            tomledit.set_value(SAMPLE, ("video", "stream_topics", 99), "x")

    def test_result_is_valid_toml(self):
        import tomllib

        out = tomledit.set_value(SAMPLE, ("mqtt_broker", "port"), 11884)
        self.assertEqual(tomllib.loads(out)["mqtt_broker"]["port"], 11884)


class InsertValueTestCase(unittest.TestCase):
    def test_inserts_missing_key_into_existing_table(self):
        """파일에 줄이 없는 항목(dataclass 기본값)을 실제로 적어 넣는다."""
        import tomllib

        out = tomledit.set_value(SAMPLE, ("dock", "fail_timeout_sec"), 45, create=True)
        self.assertEqual(tomllib.loads(out)["dock"]["fail_timeout_sec"], 45)
        self.assertIn("timeout_s = 30", out)

    def test_insert_without_create_raises(self):
        with self.assertRaises(KeyError):
            tomledit.set_value(SAMPLE, ("dock", "fail_timeout_sec"), 45)

    def test_insert_creates_missing_table(self):
        """기본값만 쓰는 섹션은 파일에 없다. create 는 그 테이블부터 만든다."""
        import tomllib

        out = tomledit.set_value(SAMPLE, ("nope", "x"), 1, create=True)
        self.assertEqual(tomllib.loads(out)["nope"]["x"], 1)
        self.assertIn("timeout_s = 30", out)


class RemoveValueTestCase(unittest.TestCase):
    def test_removes_key(self):
        import tomllib

        out = tomledit.remove_value(SAMPLE, ("dock", "enabled"))
        self.assertNotIn("enabled", tomllib.loads(out)["dock"])
        self.assertEqual(tomllib.loads(out)["dock"]["timeout_s"], 30)

    def test_removing_list_element_raises(self):
        """인덱스가 밀려 다른 요청이 엉뚱한 원소를 가리키게 된다."""
        with self.assertRaises(ValueError):
            tomledit.remove_value(SAMPLE, ("video", "stream_topics", 0))

    def test_missing_path_raises(self):
        with self.assertRaises(KeyError):
            tomledit.remove_value(SAMPLE, ("dock", "nope"))


class RealFileTestCase(unittest.TestCase):
    def setUp(self):
        self.text = (ADAPTOR_DIR / "config" / "config.toml").read_text(encoding="utf-8")

    def test_no_op_round_trip_is_byte_identical(self):
        self.assertEqual(tomledit.dumps(tomledit.parse(self.text)), self.text)

    def test_every_scanned_scalar_rewrites_to_itself(self):
        checked = 0
        for path, found in tomledit.scan_values(self.text).items():
            if isinstance(found.value, (dict, list)):
                continue
            self.assertEqual(tomledit.set_value(self.text, path, found.value), self.text, path)
            checked += 1
        self.assertGreater(checked, 0)

    def test_every_scanned_value_stays_valid_toml(self):
        import tomllib

        for path, found in tomledit.scan_values(self.text).items():
            out = tomledit.set_value(self.text, path, found.value)
            tomllib.loads(out)


if __name__ == "__main__":
    unittest.main()


def test_set_value_creates_missing_table():
    """`[pio_advanced]` 가 없는 config.toml 에서도 기본값을 고정할 수 있어야 한다."""
    text = '[adapter]\nvendor = "jibot"\n'
    out = tomledit.set_value(text, ("pio_advanced", "ping_default_timeout_sec"), 6, create=True)
    assert 'vendor = "jibot"' in out
    doc = tomledit.parse(out)
    assert doc["pio_advanced"]["ping_default_timeout_sec"] == 6
    assert doc["adapter"]["vendor"] == "jibot"


def test_set_value_does_not_create_table_without_create_flag():
    text = '[adapter]\nvendor = "jibot"\n'
    try:
        tomledit.set_value(text, ("pio_advanced", "x"), 1)
    except KeyError:
        pass
    else:
        raise AssertionError("create 없이 테이블을 만들면 안 됨")


def test_set_value_never_creates_array_elements():
    text = "motion_rules = [{ to = \"a\" }]\n"
    try:
        tomledit.set_value(text, ("motion_rules", 5, "to"), "b", create=True)
    except KeyError:
        pass
    else:
        raise AssertionError("배열 원소를 만들면 안 됨")
