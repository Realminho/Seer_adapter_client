import unittest
import tomllib
from pathlib import Path

from config.config import ManualControlSettings


class TestJibotClientConfigDefaults(unittest.TestCase):
    def test_jibot_client_config_defaults(self):
        from config.config import get_config
        jc = get_config().jibot_client
        self.assertEqual(jc.user, "test")
        self.assertEqual(jc.password, "test")
        self.assertEqual(jc.device_type, "pc")
        self.assertEqual(jc.command_timeout_sec, 3.0)
        self.assertEqual(jc.recv_buffer_bytes, 32768)
        self.assertEqual(jc.status_log_interval_sec, 5.0)
        self.assertEqual(jc.battery_log_interval_sec, 30.0)


def test_manual_control_defaults():
    mc = ManualControlSettings()
    assert mc.enabled is True
    assert mc.drive_trans == 200
    assert mc.drive_rot == 30
    assert mc.drive_speed == 200
    assert mc.drive_lat == 0
    assert mc.heartbeat_ms == 300
    assert mc.watchdog_ms == 800
    assert mc.watchdog_ms > mc.heartbeat_ms
    assert mc.step_distance_mm == 500
    assert mc.default_move_speed == 100


def test_manual_control_overrides_from_dict():
    mc = ManualControlSettings(**{"enabled": False, "drive_speed": 120, "watchdog_ms": 1000})
    assert mc.enabled is False
    assert mc.drive_speed == 120
    assert mc.watchdog_ms == 1000


def test_config_toml_does_not_define_robot_specific_identity_or_endpoints():
    data = tomllib.loads(Path("config/config.toml").read_text(encoding="utf-8"))

    vehicle = data.get("vehicle", {})
    assert "serial_number" not in vehicle
    assert "vehicle_ip" not in vehicle
    assert "vehicle_port" not in vehicle

    ezi = data.get("ezi", {})
    assert "ezi_io" not in ezi
    assert "ezi_motor" not in ezi


def test_settings_polling_timeout_knob_defaults():
    """Task 5.1: [settings] polling/timeout knobs have correct defaults."""
    from config.config import get_config
    s = get_config().settings
    assert s.acs_cmd_subscribe_interval_sec == 1.0
    assert s.node_position_poll_interval_sec == 0.2
    assert s.standstill_poll_interval_sec == 0.1
    assert s.adapter_loop_sleep_sec == 1.0
    assert s.jibot_command_default_timeout_sec == 3.0
    assert s.jibot_ack_timeout_min_sec == 0.1
    assert s.node_unreached_delay_sec == 1.0


def test_settings_accepts_legacy_deployed_config_keys():
    """Robot config.toml files survive upgrades and may retain old keys."""
    from config.config import Settings

    settings = Settings(
        speed=0.05,
        map_id="lab2m",
        state_publish_delay=5,
        action_time=1.0,
        robot_count=3,
        state_frequency=1,
        visualization_frequency=1,
        map_refresh_sec=300.0,
    )

    assert settings.state_frequency == 1
    assert settings.visualization_frequency == 1
    assert settings.map_refresh_sec == 300.0


def test_path_point_is_default_fms_driving_node_mode():
    from config.config import Settings

    assert Settings.__dataclass_fields__["nearest_node_mode"].default == "pathPoint"
    assert (
        Settings.__dataclass_fields__[
            "use_nearest_node_as_last_node_when_missing"
        ].default
        is False
    )


def test_nearest_node_last_node_fallback_is_enabled_in_deployed_config():
    from config.config import get_config

    assert get_config().settings.use_nearest_node_as_last_node_when_missing is True


def test_missing_last_node_reach_gate_defaults_to_off():
    """0 keeps the seed ungated, which is how it shipped and how deployments
    that already enable the seed behave. A non-zero default would silently
    narrow lastNodeId recovery on every robot at upgrade."""
    from config.config import Settings, get_config

    assert Settings.__dataclass_fields__["missing_last_node_reach_xy"].default == 0.0
    assert get_config().settings.missing_last_node_reach_xy == 0.0


def test_last_node_release_default_reaches_robots_that_keep_their_config():
    """기본값이 곧 현장 동작이다. update-jibot-adapter-over-ssh.sh 는
    --config-toml-mode keep 이 기본이라 로봇 config.toml 을 절대 덮지 않는다.
    2026-08-23 로봇 62 실측: 코드는 배포됐는데 config.toml 에 키가 없어
    기본값 0.0(해제 off)이 먹었고, p36~p37 사이에서 lastNodeId 가 p2 로 고착했다.
    0.0 이면 이 수정은 어느 로봇에도 도달하지 못한다."""
    from config.config import Settings

    assert Settings.__dataclass_fields__["last_node_release_xy"].default == 500.0


def test_deployed_last_node_release_keeps_a_hysteresis_band_above_the_capture_reach():
    """캡처는 idle_last_node_reach_xy 안에서, 해제는 last_node_release_xy 밖에서만
    일어난다. 두 값이 같으면 히스테리시스 구간이 없어져, 경계에 선 pose 가
    갱신마다 캡처와 해제를 번갈아 하게 된다."""
    from config.config import get_config

    settings = get_config().settings
    assert settings.last_node_release_xy == 500.0
    assert settings.last_node_release_xy > settings.idle_last_node_reach_xy


def test_dock_polling_timing_knob_defaults():
    """Task 5.2: [dock] polling/timing knobs have correct defaults."""
    from config.config import get_config
    d = get_config().dock
    assert d.dock_wait_poll_interval_sec == 0.2
    assert d.dock_approach_poll_interval_sec == 0.2
    assert d.charging_start_poll_interval_sec == 0.2
    assert d.charging_stop_poll_interval_sec == 0.2
    assert d.dock_approach_unreached_delay_sec == 1.0


def test_charge_circuit_timeout_knob_defaults():
    """Task 5.3: [charge_circuit] terminate/off timeout knobs have correct defaults."""
    from config.config import get_config
    cc = get_config().charge_circuit
    assert cc.terminate_timeout_sec == 3.0
    assert cc.off_timeout_sec == 5.0


def test_air_shower_poll_interval_knob_default():
    """Task 5.3: [air_shower_pio] poll_interval_sec knob has correct default."""
    from config.config import get_config
    aps = get_config().air_shower_config
    assert aps.poll_interval_sec == 0.2


def test_internal_actions_defaults():
    """Task 5.4: [internal_actions] synthetic docking-status action id/type defaults."""
    from config.config import get_config
    ia = get_config().internal_actions
    assert ia.docking_status_action_id == "__jibot_docking__"
    assert ia.docking_status_action_type == "dock"


def test_action_plugin_config_defaults_empty():
    from config.config import ActionPluginConfig, get_config

    action = ActionPluginConfig(action_type="customPing")
    assert action.runner == "inline"
    assert action.module is None
    assert action.command == []
    assert action.timeout_sec == 0.0
    assert action.motion is False
    assert action.snapshot_fields == []

    cfg = get_config()
    assert cfg.actions == []


def test_action_plugin_config_loads_actions_table(tmp_path):
    from config.config import get_config

    extensions_text = open("config/extensions.hcl", encoding="utf-8").read()
    extensions_text += """

action "customDoorOpen" {
runner = "subprocess"
command = ["python", "actions/custom_door_open.py"]
timeout_sec = 10
motion = true
snapshot_fields = ["vehicle.vehicle_ip"]
}
"""
    path = tmp_path / "extensions.hcl"
    path.write_text(extensions_text, encoding="utf-8")

    cfg = get_config(extensions_path=path)
    action = cfg.actions[0]
    assert action.action_type == "customDoorOpen"
    assert action.enabled is True
    assert action.runner == "subprocess"
    assert action.command == ["python", "actions/custom_door_open.py"]
    assert action.timeout_sec == 10
    assert action.motion is True
    assert action.snapshot_fields == ["vehicle.vehicle_ip"]


def test_action_plugin_config_parses_enabled_false(tmp_path):
    from config.config import get_config

    extensions_text = open("config/extensions.hcl", encoding="utf-8").read()
    extensions_text += """

action "pioReadIn" {
enabled = false
}
"""
    path = tmp_path / "extensions.hcl"
    path.write_text(extensions_text, encoding="utf-8")

    cfg = get_config(extensions_path=path)
    assert cfg.actions[0].action_type == "pioReadIn"
    assert cfg.actions[0].enabled is False


def test_action_modules_default_empty():
    from config.config import get_config

    assert get_config().action_modules == []


def test_state_actions_load_start_and_end(tmp_path):
    from config.config import get_config

    extensions_text = Path("config/extensions.hcl").read_text(encoding="utf-8")
    extensions_text += '''

state_action "DRIVING" {
  start = { action = "pioInit", parameters = { station = "A" } }
  end = { action = "pioDisconnect" }
}
'''
    path = tmp_path / "extensions.hcl"
    path.write_text(extensions_text, encoding="utf-8")

    cfg = get_config(extensions_path=path)
    binding = cfg.state_actions[0]
    assert binding.state == "DRIVING"
    assert binding.start.action == "pioInit"
    assert binding.start.parameters == {"station": "A"}
    assert binding.end.action == "pioDisconnect"
    assert binding.end.parameters == {}


def test_action_modules_load_tables(tmp_path):
    from config.config import get_config

    extensions_text = open("config/extensions.hcl", encoding="utf-8").read()
    extensions_text += """

module "custom_actions.air_shower" {}

module "extensions.clamp" {
enabled = false
}
"""
    path = tmp_path / "extensions.hcl"
    path.write_text(extensions_text, encoding="utf-8")

    cfg = get_config(extensions_path=path)
    assert cfg.action_modules[0].module == "custom_actions.air_shower"
    assert cfg.action_modules[0].enabled is True
    assert cfg.action_modules[1].module == "extensions.clamp"
    assert cfg.action_modules[1].enabled is False


def test_recipes_load_steps_and_cleanup(tmp_path):
    from config.config import get_config

    path = tmp_path / "recipes.hcl"
    path.write_text(
        '''recipe "elevatorEnter" {
          label = "Elevator — enter"
          timeout_sec = 30
          motion = true
          step "pioInit" {}
          step "pioWriteOut" {
            parameters = { index = var.floorPin, state = "on" }
          }
          cleanup "pioDisconnect" {}
        }''',
        encoding="utf-8",
    )

    cfg = get_config(recipes_path=path)
    recipe = cfg.recipes[0]
    assert recipe.action_type == "elevatorEnter"
    assert recipe.label == "Elevator — enter"
    assert recipe.timeout_sec == 30
    assert recipe.motion is True
    assert [step.extension for step in recipe.steps] == ["pioInit", "pioWriteOut"]
    assert recipe.steps[1].parameters == {
        "index": "${var.floorPin}",
        "state": "on",
    }
    assert recipe.cleanup[0].extension == "pioDisconnect"


def test_ezi_motor_test_delay_knob_default():
    """Task 5.5: [ezi] motor_test_delay_sec knob has correct default."""
    from config.config import get_config, EziConfig
    # Dataclass default
    assert EziConfig.__dataclass_fields__["motor_test_delay_sec"].default == 2.0
    # Loaded from config.toml
    ec = get_config().ezi_config
    assert ec.motor_test_delay_sec == 2.0


def test_sound_settings_sound_test_duration_knob_default():
    """Task 5.5: [sound_settings] sound_test_duration_sec knob has correct default."""
    from config.config import get_config, SoundSettings
    # Dataclass default
    assert SoundSettings.__dataclass_fields__["sound_test_duration_sec"].default == 10.0
    # Loaded from config.toml
    ss = get_config().sound_settings
    assert ss.sound_test_duration_sec == 10.0


def test_pio_advanced_config_defaults():
    """Task 5.5: [pio_advanced] PioAdvancedConfig all 5 fields have correct defaults."""
    from config.config import get_config, PioAdvancedConfig
    # Dataclass defaults
    pa = PioAdvancedConfig()
    assert pa.init_default_timeout_sec == 2.0
    assert pa.read_default_timeout_sec == 2.0
    assert pa.write_output_timeout_sec == 2.0
    assert pa.scenario_default_timeout_sec == 2.0
    assert pa.call_poll_interval_sec == 0.05
    # Loaded from config.toml (absent section => all defaults)
    pa_loaded = get_config().pio_advanced
    assert pa_loaded.init_default_timeout_sec == 2.0
    assert pa_loaded.read_default_timeout_sec == 2.0
    assert pa_loaded.write_output_timeout_sec == 2.0
    assert pa_loaded.scenario_default_timeout_sec == 2.0
    assert pa_loaded.call_poll_interval_sec == 0.05


def test_sound_settings_timeout_knob_defaults():
    """Task 5.6a: [sound_settings] player_wait_timeout_sec and pactl_timeout_sec defaults."""
    from config.config import get_config, SoundSettings
    # Dataclass defaults
    ss = SoundSettings()
    assert ss.player_wait_timeout_sec == 1.0
    assert ss.pactl_timeout_sec == 2.0
    # Loaded from config.toml
    ss_loaded = get_config().sound_settings
    assert ss_loaded.player_wait_timeout_sec == 1.0
    assert ss_loaded.pactl_timeout_sec == 2.0


def test_sound_settings_replay_gap_defaults():
    from config.config import get_config, SoundSettings
    ss = SoundSettings()
    assert ss.state_replay_gap_sec == 0.0
    assert ss.state_replay_gap_overrides == {}
    ss_loaded = get_config().sound_settings
    assert ss_loaded.state_replay_gap_sec == 0.0
    # 배포 config.toml은 [sound_settings.state_replay_gap_overrides] 표를 갖는다
    # (현재 fault = 3.0 — 현장에서 범퍼 정지가 7분 40초 걸린 뒤 넣은 값이다).
    # 값 자체는 현장 값이라 여기에 박지 않는다. 표가 {소문자 토큰: float}으로
    # 파싱되는지만 본다 — 조회 규칙은 test_jibot_bumper_errors.py가 검사한다.
    overrides = ss_loaded.state_replay_gap_overrides
    assert isinstance(overrides, dict)
    for token, gap in overrides.items():
        assert token == token.lower(), f"override 키는 소문자 토큰이다: {token}"
        assert isinstance(gap, float), f"{token} 의 gap이 float이 아니다: {gap!r}"


def test_sound_player_timeout_kwarg_wiring():
    """Task 5.6a: SoundPlayer stores injected timeout kwargs."""
    from utils.sound import SoundPlayer
    sp = SoundPlayer(player_wait_timeout_sec=0.5, pactl_timeout_sec=3.5)
    assert sp._player_wait_timeout == 0.5
    assert sp._pactl_timeout == 3.5


def test_ezi_motor_poll_interval_knob_default():
    """Task 5.6a: [ezi] ezi_motor_poll_interval_sec knob has correct default."""
    from config.config import get_config, EziConfig
    # Dataclass default
    assert EziConfig.__dataclass_fields__["ezi_motor_poll_interval_sec"].default == 0.1
    # Loaded from config.toml
    ec = get_config().ezi_config
    assert ec.ezi_motor_poll_interval_sec == 0.1


def test_clamp_servo_policy_default():
    from config.config import get_config, EziConfig

    assert (
        EziConfig.__dataclass_fields__["clamp_servo_policy"].default
        == "auto_on_auto_off"
    )
    assert get_config().ezi_config.clamp_servo_policy == "auto_on_auto_off"


def test_clamp_servo_timeout_defaults():
    """clamp servo 게이트가 쓰는 대기 한계값의 기본값 계약."""
    from config.config import get_config, EziConfig

    fields = EziConfig.__dataclass_fields__
    assert fields["clamp_servo_on_timeout_sec"].default == 3.0
    assert fields["clamp_motion_start_timeout_sec"].default == 1.0
    assert fields["clamp_motion_timeout_sec"].default == 30.0

    ec = get_config().ezi_config
    assert ec.clamp_servo_on_timeout_sec == 3.0
    assert ec.clamp_motion_start_timeout_sec == 1.0
    assert ec.clamp_motion_timeout_sec == 30.0


def test_clamp_position_tolerance_default():
    """도착 판정에 쓰는 위치 허용 오차(엔코더 counts)의 기본값 계약."""
    from config.config import get_config, EziConfig

    assert EziConfig.__dataclass_fields__["clamp_position_tolerance"].default == 500
    assert get_config().ezi_config.clamp_position_tolerance == 500


def test_ezi_motor_client_poll_interval_kwarg_wiring():
    """Task 5.6a: EziMotorClient stores injected poll_interval_sec kwarg."""
    from utils.ezi_motor import EziMotorClient
    client = EziMotorClient("1.2.3.4", poll_interval_sec=0.3)
    assert client._poll_interval == 0.3


def test_pio_advanced_config_util_side_defaults():
    """Task 5.6b: PioAdvancedConfig 5 util-side fields have correct defaults."""
    from config.config import get_config, PioAdvancedConfig
    # Dataclass defaults match prior hard-coded literals
    pa = PioAdvancedConfig()
    assert pa.socket_timeout_sec == 0.2
    assert pa.connect_delay_sec == 0.5
    assert pa.read_frame_poll_sec == 0.05
    assert pa.read_frames_wait_sec == 2.0
    assert pa.send_wait_sec == 2.0
    # Loaded from config.toml (keys present in [pio_advanced] => same values)
    pa_loaded = get_config().pio_advanced
    assert pa_loaded.socket_timeout_sec == 0.2
    assert pa_loaded.connect_delay_sec == 0.5
    assert pa_loaded.read_frame_poll_sec == 0.05
    assert pa_loaded.read_frames_wait_sec == 2.0
    assert pa_loaded.send_wait_sec == 2.0


def test_pio_master_kwargs_stored():
    """Task 5.6b: PIOMaster constructor kwargs are stored on the instance."""
    import sys
    from unittest.mock import MagicMock
    # serial is not installed in the test venv; stub it out
    sys.modules.setdefault("serial", MagicMock())
    # Re-import after stub is in place
    import importlib
    import utils.pio as pio_mod
    importlib.reload(pio_mod)
    PIOMaster = pio_mod.PIOMaster
    pm = PIOMaster("/dev/null", 38400, send_wait_sec=0.3, read_frames_wait_sec=1.5,
                   connect_delay_sec=0.7, read_frame_poll_sec=0.02)
    assert pm._send_wait == 0.3
    assert pm._read_frames_wait == 1.5
    assert pm._connect_delay == 0.7
    assert pm._read_frame_poll == 0.02


def test_pio_master_connect_reuses_an_open_port():
    """이미 열린 포트를 다시 열지 않는다 (pyserial 배타 잠금으로 실패하므로)."""
    import sys
    from unittest.mock import MagicMock
    sys.modules.setdefault("serial", MagicMock())
    import importlib
    import utils.pio as pio_mod
    importlib.reload(pio_mod)
    pm = pio_mod.PIOMaster("/dev/null", 38400, connect_delay_sec=0.0)
    opened = MagicMock()
    opened.is_open = True
    pm.ser = opened

    pm.connect()

    assert pm.ser is opened          # 새 Serial 객체로 교체하지 않는다
    # 닫힌 뒤에는 다시 연다
    opened.is_open = False
    pm.connect()
    assert pm.ser is not opened


def test_pio_master_none_default_read_frames():
    """Task 5.6b: read_frames(None) resolves to _read_frames_wait; explicit arg preserved.

    Uses _read_frames_wait=0.0 so the deadline is already past => the read loop
    body never runs and the call returns instantly. Reaching "" (instead of a
    TypeError on ``time.time() + None``) proves None was resolved to the stored
    float. NOTE: do NOT mock time.time() to a constant here — that makes the
    ``while time.time() < end_time`` deadline loop spin forever.
    """
    import sys
    from unittest.mock import MagicMock
    sys.modules.setdefault("serial", MagicMock())
    import importlib
    import utils.pio as pio_mod
    importlib.reload(pio_mod)
    PIOMaster = pio_mod.PIOMaster
    pm = PIOMaster("/dev/null", 38400, read_frames_wait_sec=0.0, read_frame_poll_sec=0.0)
    pm.ser = MagicMock()
    pm.ser.is_open = True
    pm.ser.in_waiting = 0
    pm.ser.read.return_value = b""
    assert pm.read_frames(None) == ""   # None -> _read_frames_wait (0.0): resolves, no hang
    assert pm.read_frames(0.0) == ""    # explicit arg preserved


def test_pio_master_none_default_send_wait():
    """Task 5.6b: send_and_read / send_bc / send_channel / monitor_data / send_raw
    with no wait_sec resolve to _send_wait; an explicit wait_sec is preserved.

    read_frames is stubbed so we assert the forwarded wait_sec directly without
    running its real (time-bounded) loop.
    """
    import sys
    from unittest.mock import MagicMock
    sys.modules.setdefault("serial", MagicMock())
    import importlib
    import utils.pio as pio_mod
    importlib.reload(pio_mod)
    PIOMaster = pio_mod.PIOMaster
    pm = PIOMaster("/dev/null", 38400, send_wait_sec=0.3)
    pm.ser = MagicMock()
    pm.ser.is_open = True
    pm.ser.write = MagicMock()
    pm.ser.flush = MagicMock()
    pm.read_frames = MagicMock(return_value="")   # stub the loop; capture forwarded wait_sec
    for call in (
        lambda: pm.send_and_read("X"),
        lambda: pm.send_bc(1, "S1", 1, 1, "OHT1"),
        lambda: pm.send_channel(1),
        lambda: pm.monitor_data(1),
        lambda: pm.send_raw("RAW"),
    ):
        pm.read_frames.reset_mock()
        call()
        pm.read_frames.assert_called_once_with(0.3)   # no arg -> _send_wait (0.3)
    pm.read_frames.reset_mock()
    pm.send_raw("RAW", wait_sec=0.9)
    pm.read_frames.assert_called_once_with(0.9)        # explicit wait_sec preserved


def test_extension_settings_come_from_extensions_hcl(tmp_path):
    """Task 3: get_config()가 extensions.hcl을 병합한다 (pio_baudrate는 extensions.hcl 값)."""
    from config.config import get_config

    ext = tmp_path / "extensions.hcl"
    ext.write_text(
        open("config/extensions.hcl", encoding="utf-8").read().replace(
            'pio_baudrate = 38400', 'pio_baudrate = 19200'
        ),
        encoding="utf-8",
    )

    cfg = get_config(extensions_path=ext)
    assert cfg.pio_config.pio_baudrate == 19200


def test_missing_extensions_file_fails_boot(tmp_path):
    """Task 3: extensions.hcl이 없으면 ExtensionsError가 그대로 올라가 부팅이 멈춘다."""
    from config.config import get_config
    from config.extensions import ExtensionsError

    import pytest

    with pytest.raises(ExtensionsError):
        get_config(extensions_path=tmp_path / "nope.hcl")


def test_robot_override_wins_over_extensions_file():
    """ezi_io는 extensions.hcl이 아니라 로봇 항목이 채운다."""
    from config.config import get_config

    cfg = get_config(overrides={"ezi": {"ezi_io": "10.9.9.9"}})
    assert cfg.ezi_config.ezi_io == "10.9.9.9"


def test_pyserial_is_declared_because_pio_needs_it():
    """utils/pio.py는 `import serial`을 한다. requirements에 없으면 로봇에서
    pioInit이 ModuleNotFoundError로 죽는다 — 2026-07-29 현장 장애."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert "import serial" in (root / "utils" / "pio.py").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "pyserial" in requirements


def test_shipped_pio_port_is_a_posix_device():
    """config/extensions.hcl은 배포 시 로봇에 그대로 시드된다. Windows 포트명이
    실리면 그 로봇은 첫 pioInit부터 포트를 못 연다."""
    from config.config import get_config

    port = get_config().pio_config.pio_serial_port
    assert port.startswith("/dev/"), f"pio_serial_port must be a POSIX device: {port!r}"


def test_pio_serial_port_loads_from_extensions(tmp_path):
    """시리얼 장치 경로는 pio_serial_port다 — BC 프레임의 port 필드와 다른 값이다."""
    from config.config import get_config

    text = open("config/extensions.hcl", encoding="utf-8").read()
    path = tmp_path / "extensions.hcl"
    path.write_text(text, encoding="utf-8")

    cfg = get_config(extensions_path=path)
    assert cfg.pio_config.pio_serial_port.startswith("/dev/")


def test_legacy_pio_port_key_names_the_rename(tmp_path):
    """옛 키가 남은 로봇은 부팅을 멈추되, 무엇을 고쳐야 하는지 말해줘야 한다."""
    import pytest
    from config.config import get_config
    from config.extensions import ExtensionsError

    text = open("config/extensions.hcl", encoding="utf-8").read()
    text = text.replace("pio_serial_port", "pio_port")
    path = tmp_path / "extensions.hcl"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ExtensionsError) as excinfo:
        get_config(extensions_path=path)
    message = str(excinfo.value)
    assert "pio_port" in message
    assert "pio_serial_port" in message


class TestMovedPioStationAndChannelKeys(unittest.TestCase):
    def _load_with_pio_lines(self, *lines):
        import tempfile

        from config.config import get_config

        text = open("config/extensions.hcl", encoding="utf-8").read()
        text = text.replace(
            'extension "pio" {', 'extension "pio" {\n' + "\n".join(lines)
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "extensions.hcl"
            path.write_text(text, encoding="utf-8")
            return get_config(extensions_path=path)

    def _load_with_pio_line(self, line):
        return self._load_with_pio_lines(line)

    def test_moved_pio_station_and_channel_are_refused(self):
        """옮겨진 키가 남아 있으면 조용히 무시하지 않고 부팅을 막는다.

        무시하면 운영자가 extension "pio"를 고쳐도 값이 안 바뀌는 상태가 된다.
        station_id와 channel은 owner가 다르다: station_id는 motion_rules
        항목마다 다르지만, channel은 extension "elevator" 블록 레벨 하나뿐이다
        (motion_rules에는 channel 필드가 없다) — 잘못 안내하면 운영자가 여섯 개
        규칙에 channel을 적고도 ElevatorMotionRule이 조용히 버려 라벨 없는
        KeyError로 이어진다.
        """
        from config.extensions import ExtensionsError

        cases = {
            "station_id": (
                '  station_id = "123456"',
                'extension "elevator" motion_rules의 pio_station_id',
            ),
            "channel": (
                "  channel = 250",
                'extension "elevator"의 channel',
            ),
        }
        for key, (line, elevator_owner) in cases.items():
            with self.subTest(key=key):
                with self.assertRaises(ExtensionsError) as caught:
                    self._load_with_pio_line(line)
                message = str(caught.exception)
                self.assertIn(key, message)
                self.assertIn("airshower", message)
                self.assertIn(elevator_owner, message)

    def test_moved_pio_channel_owner_is_not_motion_rules(self):
        """channel의 owner는 extension "elevator" 블록이지 motion_rules가 아니다.

        motion_rules에는 pio_station_id만 있고 channel 필드가 없으므로, 안내
        문구가 "motion_rules의 channel"이라고 하면 운영자가 존재하지 않는
        자리에 값을 적게 된다.
        """
        from config.extensions import ExtensionsError

        with self.assertRaises(ExtensionsError) as caught:
            self._load_with_pio_line("  channel = 250")
        message = str(caught.exception)
        self.assertNotIn("motion_rules의 channel", message)

    def test_moved_pio_station_and_channel_are_both_reported_at_once(self):
        """192.168.101.61처럼 두 옛 줄이 모두 남아 있으면 한 번에 알려준다.

        하나씩만 알려주면 운영자가 station_id를 고치고 재시작한 뒤에야
        channel도 옮겨야 한다는 사실을 알게 된다.
        """
        from config.extensions import ExtensionsError

        with self.assertRaises(ExtensionsError) as caught:
            self._load_with_pio_lines('  station_id = "123456"', "  channel = 250")
        message = str(caught.exception)
        self.assertIn("station_id", message)
        self.assertIn("channel", message)


class TestMissingFacilityKeysAfterHalfMigration(unittest.TestCase):
    """옛 station_id/channel 줄을 지웠지만 설비 블록에 새 키를 아직 채우지
    않은 반쪽 마이그레이션. 그대로 두면 AirShowerPioConfig/ElevatorPioConfig가
    라벨 없는 TypeError/KeyError로 죽는다 — 무엇을, 어느 블록에 적을지 짚어줘야
    한다. 기본값을 주면 조용히 엉뚱한 설비로 주소를 잡으므로 주지 않는다."""

    def _load_without_line(self, line):
        import tempfile

        from config.config import get_config

        text = open("config/extensions.hcl", encoding="utf-8").read()
        self.assertIn(line, text, "fixture line not found in extensions.hcl")
        text = text.replace(line, "", 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "extensions.hcl"
            path.write_text(text, encoding="utf-8")
            return get_config(extensions_path=path)

    def test_airshower_missing_pio_station_id_names_the_block_and_key(self):
        from config.extensions import ExtensionsError

        with self.assertRaises(ExtensionsError) as caught:
            self._load_without_line('pio_station_id = "000030"')
        message = str(caught.exception)
        self.assertIn("airshower", message)
        self.assertIn("pio_station_id", message)

    def test_airshower_missing_channel_names_the_block_and_key(self):
        from config.extensions import ExtensionsError

        with self.assertRaises(ExtensionsError) as caught:
            self._load_without_line("channel        = 250")
        message = str(caught.exception)
        self.assertIn("airshower", message)
        self.assertIn("channel", message)

    def test_elevator_missing_channel_names_the_block_and_key(self):
        from config.extensions import ExtensionsError

        with self.assertRaises(ExtensionsError) as caught:
            self._load_without_line("channel = 250")
        message = str(caught.exception)
        self.assertIn("elevator", message)
        self.assertIn("channel", message)

    def test_missing_keys_are_not_silently_defaulted(self):
        """세 키 모두 지워도 부팅이 죽어야 한다 — 하나라도 조용히 기본값이
        채워지면 엉뚱한 설비로 주소를 잡는다."""
        import tempfile

        from config.config import get_config
        from config.extensions import ExtensionsError

        text = open("config/extensions.hcl", encoding="utf-8").read()
        for needle in ('pio_station_id = "000030"', "channel        = 250", "channel = 250"):
            self.assertIn(needle, text)
            text = text.replace(needle, "", 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "extensions.hcl"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ExtensionsError) as caught:
                get_config(extensions_path=path)
        message = str(caught.exception)
        self.assertIn("pio_station_id", message)
        self.assertIn("airshower", message)
        self.assertIn("elevator", message)


def test_config_toml_cannot_define_pio_sections(tmp_path):
    """PIO 계열 설정의 단일 출처는 extensions.hcl이다. config.toml에 남은 옛
    섹션이 조용히 값을 채우면 운영자는 Config 화면을 고쳐도 동작이 안 바뀐다."""
    import pytest
    from config.config import get_config
    from config.extensions import ExtensionsError

    base = open("config/config.toml", encoding="utf-8").read()
    for section in ("pio", "pio_advanced", "air_shower_pio", "elevator_pio"):
        path = tmp_path / f"{section}.toml"
        path.write_text(f"{base}\n[{section}]\nfoo = 1\n", encoding="utf-8")

        with pytest.raises(ExtensionsError) as excinfo:
            get_config(config_path=path)
        message = str(excinfo.value)
        assert "config.toml" in message
        assert section in message
        assert "extensions.hcl" in message


def test_config_toml_may_still_define_non_pio_sections(tmp_path):
    """규칙은 PIO 계열에만 건다 — [ezi] 등 다른 섹션까지 막으면 이번 변경의
    범위를 넘어 현장 로봇이 예고 없이 부팅에 실패한다."""
    from config.config import get_config

    path = tmp_path / "config.toml"
    path.write_text(
        open("config/config.toml", encoding="utf-8").read()
        + '\n[ezi]\nmotor_speed = 12345\n',
        encoding="utf-8",
    )

    assert get_config(config_path=path) is not None
