from dataclasses import dataclass
from unittest import mock

from web.main import load_web_specs, visible_specs, wire_monitors_and_senders


@dataclass
class S:
    key: str


def test_filters_edge_agent():
    specs = [S("jibot"), S("hexplorer"), S("edge-agent"), S("edge-agent:cell-2"), S("jibot:robot-3")]
    keys = [s.key for s in visible_specs(specs)]
    assert keys == ["jibot", "hexplorer", "jibot:robot-3"]


def test_load_web_specs_keeps_webui_available_when_fleet_is_missing(monkeypatch):
    from config.config import get_config
    from config.fleet import FleetError
    from core import registry

    monkeypatch.setattr(
        registry,
        "build_registry",
        lambda _config: (_ for _ in ()).throw(
            FleetError("fleet file not found: /tmp/robots.hcl")
        ),
    )

    specs, startup_error = load_web_specs(get_config())

    assert [spec.key for spec in specs] == ["jibot"]
    assert specs[0].serial == "UNCONFIGURED"
    assert "어댑터가 정상적으로 켜지지 않았습니다" in startup_error
    assert "robots.hcl" in startup_error


@dataclass
class WSpec:
    key: str
    manufacturer: str = "jibot"
    serial: str = "A"
    monitor_kind: str = "vda5050"
    mqtt_host: str = ""
    mqtt_port: int = 0
    topic_prefix: str = "amr/v3/A"


def test_wire_jibot_reads_files_and_sends_via_uds():
    from core.monitor import FileMonitor
    from web.senders import MqttSender, UdsSender

    specs = [
        WSpec(key="jibot", manufacturer="jibot"),
        WSpec(key="hex", manufacturer="dobot"),
        WSpec(key="edge", manufacturer="dobot", monitor_kind="none"),
    ]
    with mock.patch("core.monitor.MqttMonitor") as FakeMon:
        monitors, senders = wire_monitors_and_senders(specs, "broker", 1883)

    # jibot is fully broker-independent: read from files, command over UDS
    assert isinstance(monitors["jibot"], FileMonitor)
    assert isinstance(senders["jibot"], UdsSender)
    # non-jibot keeps the MqttMonitor for read + MqttSender; none is skipped
    assert monitors["hex"] is FakeMon.return_value
    assert isinstance(senders["hex"], MqttSender)
    assert "edge" not in monitors
    assert FakeMon.return_value.start.called
