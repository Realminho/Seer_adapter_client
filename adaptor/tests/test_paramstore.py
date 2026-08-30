"""source → 편집기 라우팅과 opaque key 직렬화 테스트.

EPR 계약의 key 는 WCS 가 파싱하지 않는 opaque 문자열이다. 그 문자열과 편집기 경로
사이의 왕복이 어긋나면 "화면에 보이는 항목을 저장하면 다른 값이 바뀐다" 가 된다.
"""
import sys
import unittest
from pathlib import Path

ADAPTOR_DIR = Path(__file__).resolve().parent.parent
if str(ADAPTOR_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_DIR))

from core import paramstore  # noqa: E402


class KeyRoundTripTestCase(unittest.TestCase):
    CASES = [
        ("config.toml", ("mqtt_broker", "port"), "config.toml:mqtt_broker.port"),
        ("config.toml", ("motion_rules", 0, "mode"), "config.toml:motion_rules[0].mode"),
        ("extensions.hcl", ("pio", "advanced", "t"), "extensions.hcl:pio.advanced.t"),
        ("extensions.hcl", ("elevator", "motion_rules", 2, "mode"),
         "extensions.hcl:elevator.motion_rules[2].mode"),
        ("recipes.hcl", ("openDoor", "pioWriteOut#1", "parameters", "state"),
         "recipes.hcl:openDoor.pioWriteOut#1.parameters.state"),
        ("robots.hcl", ("robot", "vehicle_ip"), "robots.hcl:robot.vehicle_ip"),
    ]

    def test_encodes(self):
        for source, path, key in self.CASES:
            self.assertEqual(paramstore.encode_key(source, path), key)

    def test_decodes(self):
        for source, path, key in self.CASES:
            self.assertEqual(paramstore.decode_key(key), (source, path))

    def test_round_trip(self):
        for source, path, _key in self.CASES:
            key = paramstore.encode_key(source, path)
            self.assertEqual(paramstore.decode_key(key), (source, path))

    def test_index_is_int_not_string(self):
        """문자열로 돌아오면 리스트 원소가 아니라 맵 키로 잡혀 조회가 실패한다."""
        _src, path = paramstore.decode_key("config.toml:a.b[3]")
        self.assertEqual(path, ("a", "b", 3))
        self.assertIsInstance(path[-1], int)

    def test_malformed_key_raises(self):
        for bad in ("nocolon", "src:", ":path", "src:a.b[x]", "src:a.b["):
            with self.assertRaises(ValueError, msg=bad):
                paramstore.decode_key(bad)


class EditorRoutingTestCase(unittest.TestCase):
    def test_toml_source_uses_tomlkit(self):
        from core import tomledit

        self.assertIs(paramstore.editor_for("config.toml"), tomledit)

    def test_hcl_sources_use_tree_sitter(self):
        from core import hcledit

        for source in ("extensions.hcl", "recipes.hcl", "robots.hcl"):
            self.assertIs(paramstore.editor_for(source), hcledit)

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            paramstore.editor_for("nope.yaml")


class RealFileTestCase(unittest.TestCase):
    """실제 배포 파일로 스캔 → key → 되조회 왕복을 확인한다."""

    def test_every_scanned_key_round_trips_and_writes(self):
        checked = 0
        for source in paramstore.SOURCES:
            path = ADAPTOR_DIR / "config" / source
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for value_path, found in paramstore.scan(source, text).items():
                key = paramstore.encode_key(source, value_path)
                decoded_source, decoded_path = paramstore.decode_key(key)
                self.assertEqual((decoded_source, decoded_path), (source, value_path))
                # 그 key 로 실제 쓰기가 되어야 한다
                paramstore.set_value(source, text, decoded_path, found.value)
                checked += 1
        self.assertGreater(checked, 0)

    def test_scan_covers_all_sources(self):
        for source in paramstore.SOURCES:
            path = ADAPTOR_DIR / "config" / source
            if not path.exists():
                continue
            self.assertGreater(len(paramstore.scan(source, path.read_text(encoding="utf-8"))), 0, source)


if __name__ == "__main__":
    unittest.main()
