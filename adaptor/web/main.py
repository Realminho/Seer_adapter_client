"""WebUi 부트스트랩 — config/registry 로드, edge-agent 필터, 컨트롤러/모니터 와이어링, 기동."""
from __future__ import annotations

import os
import sys
from dataclasses import replace


def visible_specs(specs):
    """edge-agent 스펙 제외(자체 WebUi로 운영). 키가 'edge-agent' 또는 'edge-agent:'."""
    return [s for s in specs if not (s.key == "edge-agent" or s.key.startswith("edge-agent:"))]


def load_web_specs(config):
    """Build dashboard specs without letting a broken fleet file kill WebUi."""
    from config.fleet import FleetError
    from core.registry import _default_jibot_spec, build_registry

    try:
        return visible_specs(build_registry(config)), ""
    except FleetError as exc:
        fallback = replace(
            _default_jibot_spec(config),
            serial="UNCONFIGURED",
            topic_prefix="",
        )
        return [fallback], (
            "어댑터가 정상적으로 켜지지 않았습니다. "
            f"robots.hcl 설정 오류로 amr-adaptor.service가 시작되지 못했습니다: {exc}"
        )


def wire_monitors_and_senders(specs, broker_host, broker_port):
    """Build {key: read-monitor} and {key: command-sender} for the dashboard.

    JIBOT specs read live state from /run/amr-adaptor files (FileMonitor); in
    Phase 1 they still send instant actions over MQTT (MqttSender on a small
    shared session). Non-jibot specs keep the MqttMonitor for both read and send.
    """
    from core import ipc_paths
    from core.monitor import FileMonitor, MqttMonitor
    from web.senders import MqttSender, UdsSender

    monitors: dict = {}
    senders: dict = {}
    sessions: dict = {}

    def _mqtt_session(s):
        h = s.mqtt_host or broker_host
        p = s.mqtt_port or broker_port
        sess_key = (h, p, s.topic_prefix)
        if sess_key not in sessions:
            sessions[sess_key] = MqttMonitor(
                h, p, s.topic_prefix, client_id=f"amr-webui-{os.getpid()}-{s.serial}"
            )
            sessions[sess_key].start()
        return sessions[sess_key]

    for s in specs:
        if s.monitor_kind == "none":
            continue
        if s.manufacturer == "jibot":
            # Fully broker-independent: read from files, command over UDS.
            monitors[s.key] = FileMonitor(s.serial)
            senders[s.key] = UdsSender(ipc_paths.control_sock_path(s.serial))
        else:
            session = _mqtt_session(s)
            monitors[s.key] = session
            senders[s.key] = MqttSender(session)
    return monitors, senders


def run() -> int:
    from config.config import get_config
    from core import configio
    from core.systemd import HostController, SystemdController
    from web.credentials import load_credentials
    from web.server import WebUi

    config = get_config()
    specs, startup_error = load_web_specs(config)

    w = config.web_ui
    if not w.enabled:
        print("web_ui.enabled is false in config.toml; not starting.")
        return 0
    if not w.credentials_path:
        print("web_ui.credentials_path is required when web_ui.enabled=true")
        return 2
    cred = load_credentials(
        w.credentials_path,
        min_length=w.password_min_length,
        forbidden_tokens=tuple(w.password_forbidden_tokens),
    )

    raw = configio.load_raw()
    broker_host = raw["mqtt_broker"]["host"]
    broker_port = int(raw["mqtt_broker"]["port"])

    # web_video_server index link for the adapter detail page. Prefer the
    # FMS/LAN-reachable public base (the web UI is browsed from other PCs);
    # fall back to the local base. Empty when video is disabled.
    video = config.video
    video_url = (
        (video.web_video_server_public_url or video.web_video_server_url or "").strip()
        if video.enabled
        else ""
    )

    controllers = {s.key: SystemdController(s.unit, use_sudo=False) for s in specs}
    monitors, senders = wire_monitors_and_senders(specs, broker_host, broker_port)

    web = WebUi(specs=specs, controllers=controllers, monitors=monitors,
                senders=senders,
                credentials=cred, host=w.host, port=w.port,
                py=sys.executable, config_path=configio.CONFIG_PATH,
                validate_config=lambda: configio.validate_on_disk(configio.CONFIG_PATH), video_url=video_url,
                camera_controller=SystemdController("amr-camera.service", use_sudo=False),
                camera_config=video, host_controller=HostController(use_sudo=False),
                urobot_controller=SystemdController("urobot.service", use_sudo=False),
                web_cfg=w, startup_error=startup_error)
    web.start()
    print(f"web_ui on http://{w.host}:{web.port}/")
    try:
        web.join()
    except KeyboardInterrupt:
        pass
    finally:
        web.stop()
    return 0
