"""config/extensions.py — extensions.hcl을 config 섹션 dict로 읽는다."""

import io
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from config import extensions


SAMPLE = textwrap.dedent(
    """
    extension "pio" {
      pio_serial_port     = "/dev/ttyUSB0"
      pio_baudrate = 38400
      media        = 2
      station_id   = "123456"
      channel      = 250
      port         = 0
      vehicle_num  = "OHT123"

      advanced = {
        init_default_timeout_sec = 2.0
        call_poll_interval_sec   = 0.05
      }
    }

    extension "ezi" {
      tray_slot_pin = [8, 9]
      select        = 15
      go            = 15
      motor_speed   = 20000
    }

    extension "airshower" {
      pio_station_id = "123456"
      channel        = 250
      failure        = 7
      occupied       = 2
      fun_working    = 3
      door_pin       = [0, 1]
    }

    extension "elevator" {
      channel        = 250
      open_door_pin  = 4
      close_door_pin = 3

      motion_rules = [
        { from = "1_05", to = "2_01", mode = "enter", floor_pin = 0, pio_station_id = "000010" },
      ]
    }

    module "extensions.pio" { enabled = false }
    module "custom_actions.thing" {}

    action "customDoorOpen" {
      runner      = "subprocess"
      command     = ["python", "x.py"]
      timeout_sec = 10
    }

    action "pioReadIn" { enabled = false }

    state_action "DRIVING" {
      start = {
        action = "customDoorOpen"
        parameters = { signal = "warningLamp" }
      }
      end = { action = "pioReadIn" }
    }

    joystick "ultimate2" {
      enabled             = true
      vendor_id           = "2dc8"
      product_id          = "3109"
      forward_axis        = 5
      reverse_axis        = 2
      steering_axis       = 0
      speed_step_percent  = 20
      speed_min_percent   = 20
      speed_max_percent   = 100
      speed_start_percent = 100
      heartbeat_timeout_ms = 400
    }

    joystick_action "1" {
      target     = "extension"
      action     = "enableMotor"
      parameters = {}
    }

    joystick_action "16" {
      target     = "recipe"
      action     = "pioElevatorOpen"
      parameters = { floor = 1 }
    }
    """
).strip()


LEGACY_JOYSTICK = textwrap.dedent(
    """
    extension "pio" {}
    extension "ezi" {}
    extension "airshower" {}
    extension "elevator" {}

    joystick "ultimate2" {
      enabled                         = true
      volume_up_button                = 9
      volume_down_button              = 8
      volume_step                     = 5
      xboxdrv_volume_up_button_code   = 400
      xboxdrv_volume_down_button_code = 401
    }
    """
).strip()


def _write(tmp: str, body: str) -> Path:
    path = Path(tmp) / "extensions.hcl"
    path.write_text(body, encoding="utf-8")
    return path


class LoadExtensionsTest(unittest.TestCase):
    def _load(self, body=SAMPLE):
        with TemporaryDirectory() as tmp:
            return extensions.load_extensions(_write(tmp, body))

    def test_pio_section_matches_config_toml_shape(self):
        data = self._load()
        self.assertEqual(data["pio"]["pio_serial_port"], "/dev/ttyUSB0")
        self.assertEqual(data["pio"]["pio_baudrate"], 38400)
        self.assertEqual(data["pio"]["port"], 0)
        # advanced 블록은 별도 섹션이 된다 (config.toml의 [pio_advanced]).
        self.assertNotIn("advanced", data["pio"])
        self.assertEqual(data["pio_advanced"]["call_poll_interval_sec"], 0.05)

    def test_ezi_and_airshower_sections(self):
        data = self._load()
        self.assertEqual(data["ezi"]["tray_slot_pin"], [8, 9])
        self.assertEqual(data["air_shower_pio"]["door_pin"], [0, 1])

    def test_elevator_motion_rules_become_a_list(self):
        data = self._load()
        rules = data["elevator_pio"]["elevator_motion_rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["from"], "1_05")
        self.assertEqual(rules[0]["pio_station_id"], "000010")
        self.assertNotIn("motion_rules", data["elevator_pio"])

    def test_facility_blocks_own_their_station_and_channel(self):
        data = self._load()
        air = data["air_shower_pio"]
        self.assertEqual(air["pio_station_id"], "123456")
        self.assertEqual(air["channel"], 250)
        # 엘리베이터는 시스템 하나에 층 station이 여럿이라 channel은 블록에 하나다.
        self.assertEqual(data["elevator_pio"]["channel"], 250)

    def test_modules_become_action_module_entries(self):
        data = self._load()
        self.assertEqual(
            data["action_modules"],
            [
                {"module": "extensions.pio", "enabled": False},
                {"module": "custom_actions.thing", "enabled": True},
            ],
        )

    def test_actions_become_action_plugin_entries(self):
        data = self._load()
        self.assertEqual(
            data["actions"],
            [
                {
                    "action_type": "customDoorOpen",
                    "runner": "subprocess",
                    "command": ["python", "x.py"],
                    "timeout_sec": 10,
                    "enabled": True,
                },
                {"action_type": "pioReadIn", "enabled": False},
            ],
        )

    def test_state_actions_use_working_state_names(self):
        data = self._load()
        self.assertEqual(
            data["state_actions"],
            [
                {
                    "state": "DRIVING",
                    "start": {
                        "action": "customDoorOpen",
                        "parameters": {"signal": "warningLamp"},
                    },
                    "end": {"action": "pioReadIn", "parameters": {}},
                }
            ],
        )

    def test_state_action_validation(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE + '\nstate_action "FLYING" { start = { action = "x" } }\n')
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE + '\nstate_action "driving" { start = { action = "x" } }\n')
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE + '\nstate_action "IDLE" {}\n')
        with self.assertRaises(extensions.ExtensionsError):
            self._load(
                SAMPLE
                + '\nstate_action "IDLE" { start = { action = "x", nope = 1 } }\n'
            )

    def test_joystick_profile_and_action_slots(self):
        data = self._load()
        self.assertEqual(data["joystick"]["profile"], "ultimate2")
        self.assertEqual(data["joystick"]["vendor_id"], "2dc8")
        self.assertEqual(
            data["joystick"]["actions"],
            [
                {
                    "slot": 1, "target": "extension", "action": "enableMotor",
                    "parameters": {}, "enabled": True,
                },
                {
                    "slot": 16, "target": "recipe", "action": "pioElevatorOpen",
                    "parameters": {"floor": 1}, "enabled": True,
                },
            ],
        )

    def test_shipped_joystick_slots_are_contiguous_from_one(self):
        """이 현장은 1~12만 쓰고 Left 행(13~16)은 비워 둔다.

        번호가 곧 D-pad+버튼 조합이라, 중간이 비면 그 아래 슬롯이 통째로 다른
        조합으로 밀려 엉뚱한 action이 나간다. 빈 자리는 끝에만 허용한다.
        """
        data = extensions.load_extensions("config/extensions.hcl")
        slots = [entry["slot"] for entry in data["joystick"]["actions"]]
        self.assertEqual(slots, list(range(1, len(slots) + 1)))
        self.assertEqual(len(slots), 12)

    def test_joystick_action_validation(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE + '\njoystick_action "17" { action = "x" }\n')
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE + '\njoystick_action "2" { target = "nope", action = "x" }\n')
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE + '\njoystick_action "2" { parameters = {} }\n')

    def test_joystick_speed_range_keys_parse(self):
        data = self._load()
        self.assertEqual(data["joystick"]["speed_step_percent"], 20)
        self.assertEqual(data["joystick"]["speed_min_percent"], 20)
        self.assertEqual(data["joystick"]["speed_max_percent"], 100)
        self.assertEqual(data["joystick"]["speed_start_percent"], 100)

    def test_legacy_volume_keys_map_onto_the_speed_buttons(self):
        # Already-deployed robots still say volume_*; an unknown field would
        # stop them booting, so the old names stay accepted as aliases.
        with TemporaryDirectory() as tmp:
            path = _write(tmp, LEGACY_JOYSTICK)
            out = io.StringIO()
            with redirect_stdout(out):
                data = extensions.load_extensions(path)
        self.assertEqual(data["joystick"]["speed_up_button"], 9)
        self.assertEqual(data["joystick"]["speed_down_button"], 8)
        self.assertEqual(data["joystick"]["xboxdrv_speed_up_button_code"], 400)
        self.assertEqual(data["joystick"]["xboxdrv_speed_down_button_code"], 401)
        self.assertNotIn("volume_up_button", data["joystick"])
        self.assertNotIn("volume_step", data["joystick"])

        # The print is the entire migration signal for an operator reading
        # journalctl on a robot still running legacy keys -- it must name both
        # the old and the new key, not just "deprecated".
        printed = out.getvalue()
        self.assertIn(f"[EXTENSIONS] {path}: 'volume_up_button' is deprecated", printed)
        self.assertIn("rename it to 'speed_up_button'", printed)
        self.assertIn(f"[EXTENSIONS] {path}: 'volume_down_button' is deprecated", printed)
        self.assertIn("rename it to 'speed_down_button'", printed)
        self.assertIn(f"[EXTENSIONS] {path}: 'volume_step' no longer does anything", printed)

    def test_new_speed_button_key_wins_over_the_legacy_name(self):
        body = LEGACY_JOYSTICK.replace(
            "volume_up_button                = 9",
            "volume_up_button                = 9\n  speed_up_button                 = 3",
        )
        self.assertEqual(self._load(body)["joystick"]["speed_up_button"], 3)

    def test_joystick_speed_range_validation(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE.replace("speed_min_percent   = 20", "speed_min_percent   = 120"))
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE.replace("speed_min_percent   = 20", "speed_min_percent   = 0"))
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE.replace("speed_start_percent = 100", "speed_start_percent = 10"))
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE.replace("speed_step_percent  = 20", "speed_step_percent  = 0"))
        # speed_start_percent above speed_max_percent is rejected too, not only below
        # speed_min_percent -- both ends of the same range check.
        with self.assertRaises(extensions.ExtensionsError):
            self._load(
                SAMPLE.replace("speed_max_percent   = 100", "speed_max_percent   = 50")
            )

    def test_joystick_speed_max_percent_ceiling(self):
        # 100% is the configured manual_control magnitude (design spec); a
        # higher ceiling lets speed_start_percent boot the robot already
        # scaled past its tuned magnitudes with no button press.
        with self.assertRaisesRegex(extensions.ExtensionsError, "speed_max_percent"):
            self._load(
                SAMPLE.replace("speed_max_percent   = 100", "speed_max_percent   = 500").replace(
                    "speed_start_percent = 100", "speed_start_percent = 500"
                )
            )

    def test_joystick_speed_field_non_numeric_names_the_file_and_field(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp, SAMPLE.replace("speed_step_percent  = 20", 'speed_step_percent  = "abc"')
            )
            with self.assertRaisesRegex(
                extensions.ExtensionsError, "speed_step_percent"
            ) as ctx:
                extensions.load_extensions(path)
            self.assertIn(str(path), str(ctx.exception))

    def test_only_one_joystick_driver_may_be_enabled(self):
        both = SAMPLE.replace(
            "enabled             = true",
            "enabled             = true\n      ultimate2_enabled   = true\n      micro_enabled       = true",
            1,
        )
        with self.assertRaisesRegex(extensions.ExtensionsError, "only one"):
            self._load(both)

    def test_unknown_extension_name_rejected(self):
        with self.assertRaises(extensions.ExtensionsError) as ctx:
            self._load('extension "nope" {}\n')
        self.assertIn("nope", str(ctx.exception))

    def test_missing_file_raises(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(extensions.ExtensionsError):
                extensions.load_extensions(Path(tmp) / "missing.hcl")

    def test_syntax_error_raises(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load('extension "pio" {\n  pio_serial_port =\n}\n')

    def test_duplicate_extension_rejected(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load('extension "pio" {}\n\nextension "pio" {}\n')

    def test_missing_required_extension_rejected(self):
        with self.assertRaises(extensions.ExtensionsError) as ctx:
            self._load('extension "pio" {}\n')
        self.assertIn("missing required extension", str(ctx.exception))

    def test_duplicate_module_and_action_labels_rejected(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load(
                SAMPLE
                + '\nmodule "extensions.pio" {}\n'
            )
        with self.assertRaises(extensions.ExtensionsError):
            self._load(
                SAMPLE
                + '\naction "pioReadIn" {}\n'
            )

    def test_enabled_requires_boolean(self):
        with self.assertRaises(extensions.ExtensionsError) as ctx:
            self._load(SAMPLE + '\nmodule "x" { enabled = "false" }\n')
        self.assertIn("enabled must be true or false", str(ctx.exception))

    def test_shipped_files_load(self):
        for name in ("config/extensions.hcl", "config/extensions.hcl.example"):
            with self.subTest(name=name):
                self.assertTrue(extensions.load_extensions(name))


if __name__ == "__main__":
    unittest.main()
