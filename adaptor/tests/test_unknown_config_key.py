"""모르는 설정 키 하나가 부팅 자체를 막으면 안 된다.

현장(2026-08-22)에서 extensions.hcl의 extension "pio"에 input_signals 한 줄이
있었고, PioConfig(**...)가 라벨 없는 TypeError로 죽었다. TypeError는
ExtensionsError가 아니라서 config-error 모드(살아서 MQTT로 보고)로 가지 못하고
systemd 크래시 루프가 됐다 — journalctl을 열 수 있는 사람만 원인을 아는 상태다.

옛 키를 하나씩 손으로 열거하는 방식(pio_port, station_id, channel...)은 다음에
나올 모르는 키를 영원히 못 잡는다. 섹션 dict를 dataclass로 만드는 자리에서
필드 이름을 검증해 ExtensionsError로 올려야 한다.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from config.config import get_config, get_config_with_fallback
from config.errors import ConfigError


def _extensions_with_pio_line(tmp: str, line: str) -> Path:
    text = open(ADAPTER_ROOT / "config/extensions.hcl", encoding="utf-8").read()
    text = text.replace('extension "pio" {', 'extension "pio" {\n' + line)
    path = Path(tmp) / "extensions.hcl"
    path.write_text(text, encoding="utf-8")
    return path


class TestUnknownExtensionKeyIsLabelled(unittest.TestCase):
    def test_unknown_pio_key_raises_extensions_error_not_typeerror(self):
        """모르는 키는 ConfigError여야 config-error 모드로 갈 수 있다."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _extensions_with_pio_line(tmp, "  input_signals = { go = 1 }")
            with self.assertRaises(ConfigError) as caught:
                get_config(extensions_path=path)
        self.assertIn("input_signals", str(caught.exception))

    def test_unknown_pio_key_message_names_the_block_and_known_keys(self):
        """무엇을 고쳐야 하는지 말해줘야 한다 — 블록 이름과 쓸 수 있는 키."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _extensions_with_pio_line(tmp, "  input_signals = { go = 1 }")
            with self.assertRaises(ConfigError) as caught:
                get_config(extensions_path=path)
        message = str(caught.exception)
        self.assertIn("pio", message)
        self.assertIn("output_signals", message)


class TestMissingRequiredKeyIsLabelled(unittest.TestCase):
    """줄을 지운 경우도 같은 자리에서 라벨 없는 TypeError로 죽는다."""

    def test_missing_required_pio_key_names_the_block_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = open(ADAPTER_ROOT / "config/extensions.hcl", encoding="utf-8").read()
            self.assertIn("media", text)
            text = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("media")
            )
            path = Path(tmp) / "extensions.hcl"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                get_config(extensions_path=path)
            expected_path = str(path)
        message = str(caught.exception)
        self.assertIn("media", message)
        # 빠진 키는 어느 파일에도 없다. 그렇다고 config.toml을 가리키면 운영자는
        # 멀쩡한 파일을 뒤진다 — 그 섹션을 실제로 갖고 있는 파일을 대야 한다.
        self.assertEqual(caught.exception.path, expected_path)
        self.assertIn('extension "pio"', message)


class TestElevatorBlockIsValidatedToo(unittest.TestCase):
    """elevator 블록은 키를 하나씩 손으로 꺼내 쓴다 — 빠지면 KeyError, 남으면
    조용히 무시다. 둘 다 같은 종류의 사고이므로 같은 검증을 받아야 한다."""

    def _load_with_elevator_text(self, replace, with_):
        with tempfile.TemporaryDirectory() as tmp:
            text = open(ADAPTER_ROOT / "config/extensions.hcl", encoding="utf-8").read()
            self.assertIn(replace, text)
            text = text.replace(replace, with_, 1)
            path = Path(tmp) / "extensions.hcl"
            path.write_text(text, encoding="utf-8")
            return get_config(extensions_path=path)

    def test_missing_elevator_key_names_the_block_and_key(self):
        with self.assertRaises(ConfigError) as caught:
            self._load_with_elevator_text("  open_door_pin = 3", "")
        message = str(caught.exception)
        self.assertIn("open_door_pin", message)
        self.assertIn("elevator", message)

    def test_unknown_elevator_key_is_not_silently_ignored(self):
        with self.assertRaises(ConfigError) as caught:
            self._load_with_elevator_text(
                "  channel = 250", "  channel = 250\n  open_door_pins = 3"
            )
        message = str(caught.exception)
        self.assertIn("open_door_pins", message)
        self.assertIn("elevator", message)


class TestUnknownKeyDoesNotStopBoot(unittest.TestCase):
    """부팅이 멈추면 안 된다: 살아남아 config-error 모드로 보고해야 한다."""

    def test_base_config_survives_an_unknown_extension_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _extensions_with_pio_line(tmp, "  input_signals = { go = 1 }")
            config, error = get_config_with_fallback(extensions_path=path)
        self.assertIsInstance(error, ConfigError)
        self.assertTrue(config.mqtt_broker.host)

    def test_per_robot_config_survives_an_unknown_extension_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _extensions_with_pio_line(tmp, "  input_signals = { go = 1 }")
            robot_toml = Path(tmp) / "robot.toml"
            robot_toml.write_text(
                open(ADAPTER_ROOT / "config/config.toml", encoding="utf-8").read(),
                encoding="utf-8",
            )
            config, error = get_config_with_fallback(
                config_path=robot_toml, extensions_path=path
            )
        self.assertIsInstance(error, ConfigError)
        self.assertTrue(config.mqtt_broker.host)


if __name__ == "__main__":
    unittest.main()
