"""Tests for tui.configio comment-preserving scalar edits + validation."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core import configio


SAMPLE = (
    "[mqtt_broker]\n"
    'host = "192.168.3.108"              # MQTT broker address\n'
    "port = 11883                         # MQTT broker port\n"
    "\n"
    "[settings]\n"
    "debug_log = false                    # verbose logging\n"
    "port = 7                             # unrelated key named port\n"
)


class RewriteScalarTest(unittest.TestCase):
    def test_changes_value_and_keeps_comment(self):
        out = configio.rewrite_scalar(SAMPLE, "mqtt_broker", "host", '"10.0.0.5"')
        self.assertIn('host = "10.0.0.5"', out)
        self.assertIn("# MQTT broker address", out)
        # other lines untouched
        self.assertIn("port = 11883", out)

    def test_preserves_comment_column(self):
        out = configio.rewrite_scalar(SAMPLE, "mqtt_broker", "port", "12000")
        line = [ln for ln in out.splitlines() if ln.startswith("port = 12000")][0]
        # comment still aligned at the original column
        self.assertEqual(line.index("#"), SAMPLE.splitlines()[2].index("#"))

    def test_section_isolation(self):
        # 'port' exists in both [mqtt_broker] and [settings]; only target changes.
        out = configio.rewrite_scalar(SAMPLE, "settings", "port", "9")
        self.assertIn("port = 11883", out)  # mqtt_broker.port unchanged
        self.assertIn("port = 9", out)

    def test_no_comment_line(self):
        text = "[a]\nkey = 1\n"
        out = configio.rewrite_scalar(text, "a", "key", "2")
        self.assertEqual(out, "[a]\nkey = 2\n")

    def test_bool_literal(self):
        out = configio.rewrite_scalar(SAMPLE, "settings", "debug_log", "true")
        self.assertIn("debug_log = true", out)
        self.assertIn("# verbose logging", out)

    def test_missing_key_raises(self):
        with self.assertRaises(KeyError):
            configio.rewrite_scalar(SAMPLE, "mqtt_broker", "nope", "1")
        with self.assertRaises(KeyError):
            configio.rewrite_scalar(SAMPLE, "missing_section", "host", "1")

    def test_set_scalar_appends_missing_key_to_existing_section(self):
        text = "[sound_settings]   # sound\nenabled = true\n\n[settings]\ndebug_log = false\n"
        out = configio.set_scalar(text, "sound_settings", "startup_volume", "42")
        self.assertIn("[sound_settings]   # sound\n", out)
        self.assertIn("enabled = true\nstartup_volume = 42\n\n[settings]", out)

    def test_set_scalar_rewrites_existing_key(self):
        out = configio.set_scalar(SAMPLE, "mqtt_broker", "port", "12000")
        self.assertIn("port = 12000", out)
        self.assertIn("# MQTT broker port", out)

    def test_commented_section_header(self):
        # The repo uses commented section headers as a documentation convention.
        text = "[settings]   # tuning knobs\ndebug_log = false   # verbose\n"
        out = configio.rewrite_scalar(text, "settings", "debug_log", "true")
        self.assertIn("debug_log = true", out)
        self.assertIn("# verbose", out)

    def test_array_of_tables_isolation(self):
        # A key with the same name inside [[...]] must not be edited when we
        # target a real [section].
        text = (
            "[settings]\n"
            "state_publish_delay = 5\n"
            "\n"
            "[[air_shower_pio.wait_for_trigger.rules]]\n"
            "state_publish_delay = 99\n"
        )
        out = configio.rewrite_scalar(text, "settings", "state_publish_delay", "8")
        self.assertIn("state_publish_delay = 8", out)
        self.assertIn("state_publish_delay = 99", out)  # array-of-tables untouched

    def test_escaped_quote_keeps_comment(self):
        # Basic string with an embedded escaped quote (odd raw-quote count).
        text = '[a]\nkey = "x\\"y"   # keep me\nother = 1\n'
        out = configio.rewrite_scalar(text, "a", "key", '"z"')
        self.assertIn("key = \"z\"", out)
        self.assertIn("# keep me", out)
        self.assertIn("other = 1", out)


class LiteralCoerceTest(unittest.TestCase):
    def test_toml_literal(self):
        self.assertEqual(configio.toml_literal("a/b", "str"), '"a/b"')
        self.assertEqual(configio.toml_literal(True, "bool"), "true")
        self.assertEqual(configio.toml_literal(False, "bool"), "false")
        self.assertEqual(configio.toml_literal(42, "int"), "42")

    def test_string_escaping(self):
        self.assertEqual(configio.toml_literal('a"b', "str"), '"a\\"b"')

    def test_coerce(self):
        self.assertEqual(configio.coerce("11883", "int"), 11883)
        self.assertIs(configio.coerce("yes", "bool"), True)
        self.assertIs(configio.coerce("off", "bool"), False)
        self.assertEqual(configio.coerce("5", "num"), 5)
        self.assertEqual(configio.coerce("5.5", "num"), 5.5)
        self.assertEqual(configio.coerce("  host  ", "str"), "host")

    def test_coerce_bad_bool(self):
        with self.assertRaises(ValueError):
            configio.coerce("maybe", "bool")


class ConfigLocationTest(unittest.TestCase):
    def test_scalar_locations_include_path_line_and_key_path(self):
        text = (
            "[mqtt_broker]\n"
            'host = "127.0.0.1"\n'
            "port = 11883\n"
            "\n"
            "[dock.approach_params]\n"
            "detect_charging_signal = true\n"
        )
        locs = configio.scan_scalar_locations(text, "adaptor/config/config.toml")

        host = locs[("mqtt_broker", "host")]
        self.assertEqual(host.path, "adaptor/config/config.toml")
        self.assertEqual(host.line, 2)
        self.assertEqual(host.key_path, "[mqtt_broker].host")

        nested = locs[("dock.approach_params", "detect_charging_signal")]
        self.assertEqual(nested.line, 6)
        self.assertEqual(nested.key_path, "[dock.approach_params].detect_charging_signal")

    def test_location_scanner_ignores_comments_and_array_of_tables(self):
        text = (
            "[settings]\n"
            "# port = 1\n"
            "debug_log = false # keep\n"
            "\n"
            "[[adapter.instances]]\n"
            'name = "line1"\n'
        )
        locs = configio.scan_scalar_locations(text, "/tmp/config.toml")

        self.assertIn(("settings", "debug_log"), locs)
        self.assertNotIn(("settings", "port"), locs)
        self.assertNotIn(("adapter.instances", "name"), locs)


class FactsheetFieldsTest(unittest.TestCase):
    def test_rewrite_factsheet_scalar(self):
        from core import configio
        text = '[factsheet]\nseries_name = "JIBOT"  # series\n'
        out = configio.rewrite_scalar(text, "factsheet", "series_name", '"X100"')
        self.assertIn('series_name = "X100"', out)
        self.assertIn("# series", out)  # comment preserved


class ValidateTest(unittest.TestCase):
    def test_on_disk_config_is_valid(self):
        ok, message = configio.validate_on_disk()
        self.assertTrue(ok, message)

    def test_validate_on_disk_uses_given_path(self):
        import tempfile, os
        from core import configio
        fd, p = tempfile.mkstemp(suffix=".toml"); os.close(fd)
        open(p, "w").write("mqtt_broker = 123\n")  # structurally invalid
        ok, msg = configio.validate_on_disk(p)
        self.assertFalse(ok)
        ok2, _ = configio.validate_on_disk(configio.CONFIG_PATH)
        self.assertTrue(ok2)


class IterConfigSectionsTest(unittest.TestCase):
    def test_enumerates_scalars_lists_and_nested(self):
        raw = {
            "mqtt_broker": {"host": "127.0.0.1", "port": 11883},
            "settings": {"debug_log": False, "speed": 0.05},
            "video": {"enabled": True, "stream_topics": ["/a", "/b"]},
            "dock": {"nodes": [], "approach_params": {"detect_charging_signal": True}},
            "motion_rules": [{"to": "X", "mode": "dock"}],
        }
        secs = dict((s, (sc, ro)) for s, sc, ro in configio.iter_config_sections(raw))
        # scalars carry (key, kind, value); kind inferred from the value type
        mqtt_scalars = [tuple(row) for row in secs["mqtt_broker"][0]]
        settings_scalars = [tuple(row) for row in secs["settings"][0]]
        self.assertIn(("host", "str", "127.0.0.1"), mqtt_scalars)
        self.assertIn(("port", "int", 11883), mqtt_scalars)
        self.assertIn(("debug_log", "bool", False), settings_scalars)
        self.assertIn(("speed", "num", 0.05), settings_scalars)
        # lists are read-only, not editable scalars
        self.assertIn(("enabled", "bool", True), [tuple(row) for row in secs["video"][0]])
        self.assertEqual([tuple(row) for row in secs["video"][1]], [("stream_topics", ["/a", "/b"])])
        # nested table emitted as its own dotted-name section
        self.assertIn("dock.approach_params", secs)
        self.assertIn(
            ("detect_charging_signal", "bool", True),
            [tuple(row) for row in secs["dock.approach_params"][0]],
        )
        # top-level array -> read-only entry
        self.assertEqual(secs["motion_rules"][0], [])
        self.assertTrue(secs["motion_rules"][1])  # has a read-only "(array)" row

    def test_real_config_exposes_new_module5_knobs(self):
        """The whole point: knobs that used to be hidden now show up."""
        secs = {s: sc for s, sc, _ro in configio.iter_config_sections(configio.load_raw())}
        settings_keys = {k for k, _kind, _v in secs.get("settings", [])}
        self.assertIn("jibot_command_default_timeout_sec", settings_keys)
        dock_keys = {k for k, _kind, _v in secs.get("dock", [])}
        self.assertIn("charging_start_poll_interval_sec", dock_keys)
        self.assertIn("fail_timeout_sec", dock_keys)
        self.assertIn("pio_advanced", secs)  # whole new section is present

    def test_field_description_is_non_empty_for_every_scalar(self):
        for section, scalars, _readonly in configio.iter_config_sections(configio.load_raw()):
            for key, _kind, _value in scalars:
                self.assertTrue(configio.field_description(section, key).strip())

    def test_dock_fail_timeout_description_mentions_umdock(self):
        desc = configio.field_description("dock", "fail_timeout_sec")
        self.assertIn("UmDock", desc)
        self.assertIn("JIBOT_DOCK_FAILED", desc)

    def test_iter_sections_attaches_locations_and_badges(self):
        raw = {
            "mqtt_broker": {"host": "127.0.0.1", "port": 11883},
            "settings": {"jibot_rx_timeout": 10.0},
            "dock": {"nodes": [], "approach_params": {"detect_charging_signal": True}},
            "jibot_client": {"user": "test"},
        }
        text = (
            "[mqtt_broker]\n"
            'host = "127.0.0.1"\n'
            "port = 11883\n"
            "[settings]\n"
            "jibot_rx_timeout = 10.0\n"
            "[dock]\n"
            "nodes = []\n"
            "[dock.approach_params]\n"
            "detect_charging_signal = true\n"
            "[jibot_client]\n"
            'user = "test"\n'
        )
        locs = configio.scan_scalar_locations(text, "config/config.toml")
        secs = dict((s, (sc, ro)) for s, sc, ro in configio.iter_config_sections(raw, locations=locs))

        host = secs["mqtt_broker"][0][0]
        self.assertEqual(host.location.line, 2)
        self.assertIn("robot override", host.badges)

        timeout = secs["settings"][0][0]
        self.assertIn("advanced", timeout.badges)

        overlay = secs["dock.approach_params"][0][0]
        self.assertIn("jibot overlay", overlay.badges)

        user = secs["jibot_client"][0][0]
        self.assertIn("jibot overlay", user.badges)

        readonly = secs["dock"][1][0]
        self.assertEqual(readonly.key, "nodes")
        self.assertIn("read-only", readonly.badges)

    def test_legacy_tuple_unpacking_still_works_for_scalar_rows(self):
        raw = {"mqtt_broker": {"host": "127.0.0.1"}}
        section, scalars, readonly = configio.iter_config_sections(raw)[0]
        key, kind, value = scalars[0]
        self.assertEqual((section, key, kind, value, readonly), ("mqtt_broker", "host", "str", "127.0.0.1", []))


if __name__ == "__main__":
    unittest.main()


class ExtensionSectionsInConfigViewTest(unittest.TestCase):
    """extensions.hcl로 옮긴 설정도 Config 화면에 보여야 한다.

    분리 직후엔 5개 섹션이 통째로 사라져 운영자가 PIO 포트, EZI 클램프 위치,
    설비 핀을 확인할 방법이 없었다. 값은 노출하되 편집은 막는다 —
    rewrite_scalar가 TOML 한 줄을 고치는 방식이라 HCL에는 쓸 수 없다.
    """

    def test_extension_sections_are_visible(self):
        names = [s for s, _, _ in configio.iter_config_sections(configio.load_raw())]
        for section in configio.EXTENSION_SECTIONS:
            self.assertIn(section, names)

    def test_extension_sections_are_read_only(self):
        for section, scalars, readonly in configio.iter_config_sections(
            configio.load_raw()
        ):
            if section not in configio.EXTENSION_SECTIONS:
                continue
            with self.subTest(section=section):
                self.assertEqual(scalars, [], "extension fields must not be editable")
                self.assertTrue(readonly, "extension fields must still be shown")
                for row in readonly:
                    self.assertIn("extensions.hcl", row.badges)

    def test_config_view_survives_a_broken_extensions_file(self):
        """Config 화면은 부팅 실패를 진단하는 창구다. 같이 죽으면 안 된다."""
        with TemporaryDirectory() as tmp:
            bad = Path(tmp) / "extensions.hcl"
            bad.write_text('extension "pio" {\n  pio_serial_port =\n}\n', encoding="utf-8")
            raw = configio.load_raw(extensions_path=bad)
        self.assertIn("settings", raw)
        for section in configio.EXTENSION_SECTIONS:
            self.assertNotIn(section, raw)

