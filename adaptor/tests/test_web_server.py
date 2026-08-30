import base64
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import pytest
import web.server as server
from web.credentials import WebUiCredentials
from web.server import WebUi, _dashboard_visible


@dataclass
class FakeMetrics:
    exists: bool = True
    active_state: str = "active"
    enabled_state: str = "enabled"
    uptime_sec: int = 10
    cpu_percent: float = 0.0
    memory_bytes: int = 1024
    restarts: int = 0


@dataclass
class FakeController:
    unit: str = "amr-adaptor.service"
    calls: list = field(default_factory=list)
    def poll(self):
        return FakeMetrics()
    def _verb(self, name):
        self.calls.append(name)
        return (True, f"{name} ok")
    def start(self): return self._verb("start")
    def stop(self): return self._verb("stop")
    def restart(self): return self._verb("restart")
    def enable(self): return self._verb("enable")
    def disable(self): return self._verb("disable")


@dataclass
class FakeHostController:
    calls: list = field(default_factory=list)
    ok: bool = True
    msg: str = "reboot requested"

    def reboot(self):
        self.calls.append("reboot")
        return self.ok, self.msg


@dataclass
class FakeSnap:
    connection_state: str = "ONLINE"
    operating_mode: str = "AUTOMATIC"
    active_emergency_stop: str = "NONE"
    battery_soc: float = 90.0
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0
    errors: tuple = ()
    localization_score: float = 0.0
    order_id: str = ""
    order_update_id: int = 0


@dataclass
class FakeMonitor:
    operating_mode: str = "AUTOMATIC"
    active_emergency_stop: str = "NONE"
    broker_connected: bool = True
    last_error: str | None = None
    published: list = field(default_factory=list)

    def get_snapshot(self):
        return FakeSnap(
            operating_mode=self.operating_mode,
            active_emergency_stop=self.active_emergency_stop,
        )

    def publish_json(self, topic_suffix, obj, qos=0):
        self.published.append((topic_suffix, obj))
        return True

    def get_io(self):
        from types import SimpleNamespace as NS
        ezio = NS(configured=True, ip="10.8.8.87", port=3002, connected=True,
                  board="EZI-IO X", inputs=[1] + [0] * 15, inputs_updated_at=1.0,
                  outputs=[0] * 16, outputs_updated_at=1.0, error="")
        pio = NS(configured=True, port="/dev/ttyUSB0", baudrate=19200,
                 connected=False, inputs={"1": "on"}, inputs_updated_at=1.0, error="")
        return NS(updated_at=1.0, ezio=ezio, pio=pio)


@dataclass
class FakeSpec:
    key: str
    display_name: str
    unit: str = "amr-adaptor.service"
    manufacturer: str = "jibot"
    serial: str = "robot-1"
    vda_full_version: str = "2.0.0"
    topic_prefix: str = "amr/v2/jibot/robot-1"
    mqtt_host: str = "192.168.3.108"
    mqtt_port: int = 11883
    vehicle_host: str = "10.0.0.11"
    vehicle_port: int = 7273
    runtime_kind: str = "systemd"
    exec_script: str = "run-main.sh"
    monitor_kind: str = "vda5050"
    instant_actions: tuple = ()
    action_modules: tuple = ()
    recipes: tuple = ()
    runnable: tuple = ()
    workdir: str = "."


@dataclass
class FakeVideoConfig:
    enabled: bool = True
    web_video_server_url: str = "http://127.0.0.1:8080"
    web_video_server_public_url: str = "http://192.168.3.222:8080"
    stream_type: str = "mjpeg"
    stream_topics: list = field(default_factory=lambda: ["/camera/cam2/image_raw"])
    snapshot_quality: int = 40


def _fake_action(action_type, label="", motion=False, parameters=()):
    return type("A", (), {
        "action_type": action_type, "label": label or action_type,
        "motion": motion, "parameters": parameters,
    })()


@pytest.fixture
def ui():
    spec = FakeSpec("jibot", "JIBOT")
    # discover 된 EZIO/PIO 모듈이 있는 어댑터. out 배지 토글은 모듈이 쓰기 액션을
    # 노출할 때만 유효하므로 spec 에도 그대로 실려 있어야 한다.
    ezio = _fake_action("ezioWriteOut", "EZIO write out")
    pio = _fake_action("pioWriteOut", "PIO write out")
    spec.instant_actions = (_fake_action("startPause", "Pause"), ezio, pio)
    spec.action_modules = (
        type("M", (), {"module": "extensions.ezio", "title": "EZIO",
                       "actions": (ezio,), "panel_template": None})(),
        type("M", (), {"module": "extensions.pio", "title": "PIO",
                       "actions": (pio,), "panel_template": None})(),
    )
    controllers = {"jibot": FakeController()}
    monitors = {"jibot": FakeMonitor()}
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers=controllers, monitors=monitors,
        credentials=cred, host="127.0.0.1", port=0,
    )
    web.start()
    yield web, spec, controllers, monitors
    web.stop()


def _get(web, path, auth=("admin", "a-strong-pass-123"), headers=None):
    url = f"http://127.0.0.1:{web.port}{path}"
    req = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return urllib.request.urlopen(req, timeout=5)


def test_requires_auth(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/", auth=None)
    assert e.value.code == 401


def test_bad_credentials_401(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/", auth=("admin", "wrong-password-xx"))
    assert e.value.code == 401


def test_unknown_path_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/nope")
    assert e.value.code == 404


def test_assets_logo_served_as_png(ui):
    web, *_ = ui
    resp = _get(web, "/assets/mw-logo-blue-small.png")
    assert resp.status == 200
    assert resp.headers.get("Content-Type") == "image/png"
    assert resp.read()[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes


def test_assets_non_png_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/assets/server.py")
    assert e.value.code == 404


def _post(web, path, fields, auth=("admin", "a-strong-pass-123")):
    url = f"http://127.0.0.1:{web.port}{path}"
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    return urllib.request.urlopen(req, timeout=5)


def _post_no_redirect(web, path, fields, auth=("admin", "a-strong-pass-123")):
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    url = f"http://127.0.0.1:{web.port}{path}"
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(), method="POST",
    )
    token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    return opener.open(req, timeout=5)


def test_post_without_csrf_403(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(web, "/adapter/jibot/control", {"verb": "start"})
    assert e.value.code == 403


def test_post_wrong_csrf_403(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(web, "/adapter/jibot/control", {"verb": "start", "csrf_token": "wrong"})
    assert e.value.code == 403


def test_post_valid_csrf_reaches_dispatch_404(ui):
    web, *_ = ui
    # valid CSRF passes the CSRF check, then hits the stub dispatch -> 404 (not 403)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(web, "/whatever", {"csrf_token": web._csrf})
    assert e.value.code == 404


def test_malformed_auth_header_401(ui):
    web, *_ = ui
    req = urllib.request.Request(f"http://127.0.0.1:{web.port}/")
    req.add_header("Authorization", "Basic !!!notbase64")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 401


def test_wrong_username_401(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/", auth=("wronguser", "a-strong-pass-123"))
    assert e.value.code == 401


def test_root_lists_adapters(ui):
    web, spec, *_ = ui
    body = _get(web, "/").read().decode()
    assert "JIBOT" in body
    assert "/adapter/jibot" in body


def test_root_hides_not_installed_adapters():
    installed = FakeSpec("jibot", "JIBOT")
    ghost = FakeSpec("hex", "Hexplorer")

    class NotInstalled(FakeController):
        def poll(self):
            return FakeMetrics(exists=False, active_state="inactive")

    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[installed, ghost],
        controllers={"jibot": FakeController(), "hex": NotInstalled()},
        monitors={"jibot": FakeMonitor(), "hex": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
    )
    web.start()
    try:
        body = _get(web, "/").read().decode()
        assert "JIBOT" in body            # installed unit shown
        assert "Hexplorer" not in body    # not-installed unit hidden from the list
    finally:
        web.stop()


def test_root_hides_disabled_inactive_template_instances():
    assert _dashboard_visible(
        FakeMetrics(exists=True, active_state="inactive", enabled_state="enabled")
    )
    assert _dashboard_visible(
        FakeMetrics(exists=True, active_state="active", enabled_state="disabled")
    )
    assert not _dashboard_visible(
        FakeMetrics(exists=True, active_state="inactive", enabled_state="disabled")
    )


def test_root_lists_two_instances_with_distinct_live():
    # Two robots on one PC (fleet) -> two rows, each with its OWN live data.
    # Guards against the mirroring bug where instances share one MQTT monitor.
    spec_a = FakeSpec("jibot:ROBOT-A", "JIBOT ROBOT-A", topic_prefix="amr/v3/ROBOT-A")
    spec_b = FakeSpec("jibot:ROBOT-B", "JIBOT ROBOT-B", topic_prefix="amr/v3/ROBOT-B")

    class MonA(FakeMonitor):
        def get_snapshot(self):
            return FakeSnap(battery_soc=74.0, x=6605.0, y=4806.0, theta=176.0)

    class MonB(FakeMonitor):
        def get_snapshot(self):
            return FakeSnap(battery_soc=12.0, x=1.0, y=2.0, theta=3.0)

    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec_a, spec_b],
        controllers={"jibot:ROBOT-A": FakeController(), "jibot:ROBOT-B": FakeController()},
        monitors={"jibot:ROBOT-A": MonA(), "jibot:ROBOT-B": MonB()},
        credentials=cred, host="127.0.0.1", port=0,
    )
    web.start()
    try:
        body = _get(web, "/").read().decode()
        assert "JIBOT ROBOT-A" in body and "JIBOT ROBOT-B" in body
        assert "/adapter/jibot:ROBOT-A" in body and "/adapter/jibot:ROBOT-B" in body
        # distinct live data -> the two rows are NOT mirroring one monitor
        assert "74.0% / pos (6605.0, 4806.0, 176.0)" in body
        assert "12.0% / pos (1.0, 2.0, 3.0)" in body
    finally:
        web.stop()


def test_root_includes_camera_service_row():
    spec = FakeSpec("jibot", "JIBOT")

    class CameraCtl(FakeController):
        def poll(self):
            return FakeMetrics(active_state="active", enabled_state="enabled",
                               uptime_sec=42, memory_bytes=999999, restarts=3)

    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()}, credentials=cred,
        host="127.0.0.1", port=0,
        camera_controller=CameraCtl(unit="amr-camera.service"),
        camera_config=FakeVideoConfig(),
    )
    web.start()
    try:
        body = _get(web, "/").read().decode()
        assert "999999" in body     # camera systemctl memory shown on the dashboard
        assert "restarts" in body
    finally:
        web.stop()


def test_root_hides_camera_row_when_service_not_installed():
    spec = FakeSpec("jibot", "JIBOT")

    class CameraNotInstalled(FakeController):
        def poll(self):
            return FakeMetrics(exists=False, active_state="inactive",
                               enabled_state="not-found", memory_bytes=999999)

    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()}, credentials=cred,
        host="127.0.0.1", port=0,
        camera_controller=CameraNotInstalled(unit="amr-camera.service"),
        camera_config=FakeVideoConfig(),
    )
    web.start()
    try:
        body = _get(web, "/").read().decode()
        assert "999999" not in body   # uninstalled camera service hidden from the list
    finally:
        web.stop()


def test_detail_includes_video_link():
    spec = FakeSpec("jibot", "JIBOT")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()}, credentials=cred,
        host="127.0.0.1", port=0, video_url="http://192.168.3.222:8080",
    )
    web.start()
    try:
        body = _get(web, "/adapter/jibot").read().decode()
        assert 'href="/camera"' in body
        assert ">camera<" in body
    finally:
        web.stop()


def test_camera_page_renders_topics_and_service_status():
    spec = FakeSpec("jibot", "JIBOT")
    camera = FakeController(unit="amr-camera.service")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()}, credentials=cred,
        host="127.0.0.1", port=0,
        camera_controller=camera, camera_config=FakeVideoConfig(),
    )
    web.start()
    try:
        body = _get(web, "/camera").read().decode()
        assert "amr-camera" in body
        assert "실행 중" in body
        assert "/camera/cam2/image_raw" in body
        assert "http://192.168.3.222:8080/stream?topic=/camera/cam2/image_raw" in body
        assert "http://127.0.0.1:8080/snapshot?topic=/camera/cam2/image_raw&amp;quality=40" in body
    finally:
        web.stop()


def test_camera_page_derives_stream_host_from_webui_request_when_public_url_empty():
    spec = FakeSpec("jibot", "JIBOT")
    camera = FakeController(unit="amr-camera.service")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    video = FakeVideoConfig(web_video_server_public_url="")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()}, credentials=cred,
        host="127.0.0.1", port=0,
        camera_controller=camera, camera_config=video,
    )
    web.start()
    try:
        body = _get(web, "/camera", headers={"Host": "192.168.3.230:9000"}).read().decode()
        assert "http://192.168.3.230:9001/stream?topic=/camera/cam2/image_raw" in body
    finally:
        web.stop()


def test_camera_control_start_invokes_web_video_controller():
    spec = FakeSpec("jibot", "JIBOT")
    camera = FakeController(unit="amr-camera.service")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()}, credentials=cred,
        host="127.0.0.1", port=0,
        camera_controller=camera, camera_config=FakeVideoConfig(),
    )
    web.start()
    try:
        resp = _post(web, "/camera/control", {"csrf_token": web._csrf, "verb": "start"})
        assert resp.status == 200
        assert camera.calls == ["start"]
    finally:
        web.stop()


def test_camera_control_restart_fires_without_confirm():
    spec = FakeSpec("jibot", "JIBOT")
    camera = FakeController(unit="amr-camera.service")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()}, credentials=cred,
        host="127.0.0.1", port=0,
        camera_controller=camera, camera_config=FakeVideoConfig(),
    )
    web.start()
    try:
        # confirm removed for camera service control -> fires on a single click
        _post(web, "/camera/control", {"csrf_token": web._csrf, "verb": "restart"})
        assert camera.calls == ["restart"]
    finally:
        web.stop()


def test_camera_control_from_dashboard_redirects_back_to_root():
    spec = FakeSpec("jibot", "JIBOT")
    camera = FakeController(unit="amr-camera.service")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()}, credentials=cred,
        host="127.0.0.1", port=0,
        camera_controller=camera, camera_config=FakeVideoConfig(),
    )
    web.start()
    try:
        resp = _post(
            web, "/camera/control",
            {"csrf_token": web._csrf, "verb": "start", "return_to": "dashboard"},
        )
        assert resp.status == 200  # PRG: 302 -> followed to dashboard GET (200)
        assert urllib.parse.urlsplit(resp.geturl()).path == "/"
        assert camera.calls == ["start"]
    finally:
        web.stop()


def test_detail_renders(ui):
    web, *_ = ui
    body = _get(web, "/adapter/jibot").read().decode()
    assert "live state" in body  # monitor_kind=vda5050 -> live state section
    assert "battery" in body
    assert "adapter info" in body
    assert "10.0.0.11:7273" in body
    assert "192.168.3.108:11883" in body
    assert "amr/v2/jibot/robot-1" in body
    assert 'action="/adapter/jibot/control"' in body
    assert 'action="/adapter/jibot/action"' in body


def test_actions_page_renders_pio_panel(ui):
    # extension 화면이 어댑터의 io 스냅샷(FakeMonitor.get_io)을 받아 in8/out8
    # 패널을 낸다. 어느 패널이 나오는지는 discover 된 모듈이 정한다.
    web, *_ = ui
    body = _get(web, "/adapter/jibot/actions").read().decode()
    assert "PIO" in body
    assert "/dev/ttyUSB0" in body          # pio port from FakeMonitor.get_io()
    assert 'title="in 1: on"' in body      # in 8 circle markup


def test_detail_page_has_no_io_panel(ui):
    web, *_ = ui
    body = _get(web, "/adapter/jibot").read().decode()
    assert "/dev/ttyUSB0" not in body
    assert "/io/out" not in body


def test_detail_renders_clamp_and_pio_tests():
    spec = FakeSpec("jibot", "JIBOT")
    spec.runnable = (
        type(
            "D",
            (),
            {
                "label": "unittest: clamp actions",
                "argv": ("{py}", "-c", "print(1)"),
                "note": "",
                "requires_manual": True,
            },
        )(),
        type(
            "D",
            (),
            {
                "label": "unittest: PIO actions",
                "argv": ("{py}", "-c", "print(1)"),
                "note": "",
                "requires_manual": True,
            },
        )(),
    )
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec],
        controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor(operating_mode="MANUAL")},
        credentials=cred,
        host="127.0.0.1",
        port=0,
        py="/usr/bin/true",
    )
    web.start()
    try:
        body = _get(web, "/adapter/jibot").read().decode()
        assert "unittest: clamp actions" in body
        assert "unittest: PIO actions" in body
        assert 'action="/adapter/jibot/tests/run"' in body
    finally:
        web.stop()


def test_detail_unknown_key_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/adapter/ghost")
    assert e.value.code == 404


def test_control_page_renders(ui):
    web, *_ = ui
    body = _get(web, "/adapter/jibot/control").read().decode()
    assert "control" in body
    assert "start" in body  # service verb form present
    assert "live state" in body
    assert "battery" in body


def test_control_page_renders_host_reboot_when_configured():
    spec = FakeSpec("jibot", "JIBOT")
    host = FakeHostController()
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()}, monitors={"jibot": FakeMonitor()},
                credentials=cred, host="127.0.0.1", port=0, host_controller=host)
    web.start()
    try:
        body = _get(web, "/adapter/jibot/control").read().decode()
        assert "host" in body
        assert "reboot OS" in body
        assert 'action="/adapter/jibot/host/reboot"' in body
        assert "confirm" in body
    finally:
        web.stop()


def _get_no_redirect(web, path, auth=("admin", "a-strong-pass-123")):
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    url = f"http://127.0.0.1:{web.port}{path}"
    req = urllib.request.Request(url)
    token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    return opener.open(req, timeout=5)


def test_control_route_redirects_to_adapter_page(ui):
    # The standalone control page was merged into the adapter console; the old
    # URL now 302-redirects there instead of rendering its own page.
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get_no_redirect(web, "/adapter/jibot/control")
    assert e.value.code == 302
    assert e.value.headers["Location"] == "/adapter/jibot"


def test_set_sound_volume_marks_nearest_preset_pressed(ui):
    web, spec, *_ = ui
    spec.instant_actions = (
        type("A", (), {"action_type": "setSoundVolume", "label": "Sound volume", "motion": False})(),
    )
    _post(web, "/adapter/jibot/action",
          {"csrf_token": web._csrf, "action_type": "setSoundVolume", "volume": "47"})
    body = _get(web, "/adapter/jibot").read().decode()
    # nearest preset to the commanded 47 is 50 -> shown pressed (is-active)
    assert 'name="volume" value="50"><button class="btn is-active">50%</button>' in body
    assert 'name="volume" value="40"><button class="btn is-active">40%</button>' not in body


def test_control_unknown_key_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/adapter/ghost/control")
    assert e.value.code == 404


def test_control_start_invokes_controller(ui):
    web, spec, controllers, _ = ui
    resp = _post(web, "/adapter/jibot/control",
                 {"csrf_token": web._csrf, "verb": "start"})
    assert resp.status == 200  # PRG: 302 -> followed to control GET (200)
    assert "start" in controllers["jibot"].calls


def test_control_from_detail_redirects_back_to_detail(ui):
    web, spec, controllers, _ = ui
    resp = _post(
        web,
        "/adapter/jibot/control",
        {"csrf_token": web._csrf, "verb": "start", "return_to": "dashboard"},
    )
    assert resp.status == 200
    assert urllib.parse.urlsplit(resp.geturl()).path == "/adapter/jibot"
    assert "start" in controllers["jibot"].calls


def test_control_stop_fires_without_confirm(ui):
    web, spec, controllers, _ = ui
    # confirm removed for service control -> a single click fires immediately
    _post(web, "/adapter/jibot/control", {"csrf_token": web._csrf, "verb": "stop"})
    assert "stop" in controllers["jibot"].calls


def test_control_rejects_unknown_verb(ui):
    web, spec, controllers, _ = ui
    _post(web, "/adapter/jibot/control", {"csrf_token": web._csrf, "verb": "nuke"})
    assert controllers["jibot"].calls == []


def test_control_post_unknown_key_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(web, "/adapter/ghost/control", {"csrf_token": web._csrf, "verb": "start"})
    assert e.value.code == 404


def test_control_disable_fires_without_confirm(ui):
    web, spec, controllers, _ = ui
    _post(web, "/adapter/jibot/control", {"csrf_token": web._csrf, "verb": "disable"})
    assert "disable" in controllers["jibot"].calls


def test_control_enable_no_confirm_needed(ui):
    web, spec, controllers, _ = ui
    _post(web, "/adapter/jibot/control", {"csrf_token": web._csrf, "verb": "enable"})
    assert "enable" in controllers["jibot"].calls  # non-destructive -> dispatched without confirm


def test_host_reboot_requires_confirm():
    spec = FakeSpec("jibot", "JIBOT")
    host = FakeHostController()
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()}, monitors={"jibot": FakeMonitor()},
                credentials=cred, host="127.0.0.1", port=0, host_controller=host)
    web.start()
    try:
        _post(web, "/adapter/jibot/host/reboot", {"csrf_token": web._csrf})
        assert host.calls == []
        _post(web, "/adapter/jibot/host/reboot", {"csrf_token": web._csrf, "confirm": "on"})
        assert host.calls == ["reboot"]
    finally:
        web.stop()


def test_host_reboot_unknown_key_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(web, "/adapter/ghost/host/reboot", {"csrf_token": web._csrf, "confirm": "on"})
    assert e.value.code == 404


def test_host_reboot_without_controller_redirects_error(ui):
    web, *_ = ui
    resp = _post(web, "/adapter/jibot/host/reboot", {"csrf_token": web._csrf, "confirm": "on"})
    assert resp.status == 200


def test_action_publishes_payload(ui):
    web, spec, _, monitors = ui
    resp = _post(web, "/adapter/jibot/action",
                 {"csrf_token": web._csrf, "action_type": "startPause"})
    assert resp.status == 200
    assert monitors["jibot"].published, "expected publish_json call"
    suffix, payload = monitors["jibot"].published[-1]
    assert suffix == "instantActions"
    # Keys confirmed from core/control.py build_instant_actions return dict:
    assert payload["headerId"] == 0
    assert payload["actions"][0]["actionType"] == "startPause"
    assert payload["actions"][0]["actionId"]


@pytest.mark.parametrize("return_to", (
    "", "https://evil.example/path", "//evil.example/path", r"/\\evil.example/path",
    "/adapter/jibot\r\nX-Evil: yes", "/adapter/jibot\x00bad", "/한글",
))
def test_safe_return_to_rejects_unsafe_values(return_to):
    assert server._safe_return_to(return_to, "/adapter/jibot") == "/adapter/jibot"


def test_safe_return_to_accepts_local_path_with_query_and_fragment():
    return_to = "/adapter/jibot?panel=motion#controls"
    assert server._safe_return_to(return_to, "/fallback") == return_to


def test_safe_return_to_accepts_percent_encoded_ascii_path():
    return_to = "/%ED%95%9C%EA%B8%80?q=%ED%95%9C%EA%B8%80"
    assert server._safe_return_to(return_to, "/fallback") == return_to


def test_action_redirects_to_safe_return_to_and_reserves_parameter(ui):
    web, _, _, monitors = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "return_to": "/adapter/jibot?panel=motion#controls",
        })
    assert exc_info.value.code == 302
    assert exc_info.value.headers["Location"] == \
        "/adapter/jibot?panel=motion&msg=delivered&act=startPause#controls"
    action = monitors["jibot"].published[-1][1]["actions"][0]
    assert all(param["key"] != "return_to" for param in action["actionParameters"])


def test_action_external_return_to_falls_back_to_adapter_detail(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "return_to": "https://evil.example/path",
        })
    assert exc_info.value.code == 302
    assert exc_info.value.headers["Location"] == "/adapter/jibot?msg=delivered&act=startPause"


def test_action_return_to_replaces_stale_flash_query_and_preserves_fragment(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "return_to": "/adapter/jibot?tab=actions&err=old&empty=&msg=old#controls",
        })
    assert exc_info.value.code == 302
    assert exc_info.value.headers["Location"] == \
        "/adapter/jibot?tab=actions&empty=&msg=delivered&act=startPause#controls"


def test_action_motion_fires_without_confirm():
    spec = FakeSpec("jibot", "JIBOT")
    spec.instant_actions = (
        type("A", (), {"action_type": "startPause", "label": "Pause", "motion": True})(),
    )
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    mon = FakeMonitor()
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()},
                monitors={"jibot": mon}, credentials=cred, host="127.0.0.1", port=0)
    web.start()
    try:
        # confirm removed for motion vehicle actions -> publishes on a single click
        _post(web, "/adapter/jibot/action", {"csrf_token": web._csrf, "action_type": "startPause"})
        assert len(mon.published) == 1
    finally:
        web.stop()


def test_goto_nearest_requires_confirm():
    spec = FakeSpec("jibot", "JIBOT")
    spec.instant_actions = (
        type("A", (), {"action_type": "gotoNearestNode",
                       "label": "Go to nearest node", "motion": True})(),
    )
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    mon = FakeMonitor()
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()},
                monitors={"jibot": mon}, credentials=cred, host="127.0.0.1", port=0)
    web.start()
    try:
        # No confirm -> rejected, nothing published.
        _post(web, "/adapter/jibot/action",
              {"csrf_token": web._csrf, "action_type": "gotoNearestNode"})
        assert len(mon.published) == 0
        # With confirm -> published once.
        _post(web, "/adapter/jibot/action",
              {"csrf_token": web._csrf, "action_type": "gotoNearestNode", "confirm": "on"})
        assert len(mon.published) == 1
    finally:
        web.stop()


def test_localize_requires_confirm_and_publishes_goal():
    spec = FakeSpec("jibot", "JIBOT")
    spec.instant_actions = (
        type(
            "A",
            (),
            {
                "action_type": "localize",
                "label": "Localize robot",
                "motion": True,
                "parameters": (),
            },
        )(),
    )
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    mon = FakeMonitor()
    web = WebUi(
        specs=[spec],
        controllers={"jibot": FakeController()},
        monitors={"jibot": mon},
        credentials=cred,
        host="127.0.0.1",
        port=0,
    )
    web.start()
    try:
        form = {
            "csrf_token": web._csrf,
            "action_type": "localize",
            "target": "goal",
            "goal": "p2",
        }
        _post(web, "/adapter/jibot/action", form)
        assert mon.published == []

        form["confirm"] = "on"
        _post(web, "/adapter/jibot/action", form)

        action = mon.published[-1][1]["actions"][0]
        assert action["actionType"] == "localize"
        params = {p["key"]: p["value"] for p in action["actionParameters"]}
        assert params == {"target": "goal", "goal": "p2"}
    finally:
        web.stop()


def test_action_post_unknown_key_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(web, "/adapter/ghost/action", {"csrf_token": web._csrf, "action_type": "startPause"})
    assert e.value.code == 404


def test_action_unknown_type_not_published(ui):
    web, spec, _, monitors = ui
    _post(web, "/adapter/jibot/action", {"csrf_token": web._csrf, "action_type": "bogus"})
    assert monitors["jibot"].published == []  # unknown action_type -> no MQTT publish


def test_goto_publishes_umgoto(ui):
    web, spec, _, monitors = ui
    resp = _post(web, "/adapter/jibot/goto",
                 {"csrf_token": web._csrf, "goal": "F1_60", "confirm": "on"})
    assert resp.status == 200
    assert monitors["jibot"].published, "expected publish_json call"
    suffix, payload = monitors["jibot"].published[-1]
    assert suffix == "instantActions"
    action = payload["actions"][0]
    assert action["actionType"] == "jibotCommand"
    params = {p["key"]: p["value"] for p in action["actionParameters"]}
    assert params["command"] == "UmGoto"
    assert params["goal"] == "F1_60"


def test_goto_fires_without_confirm(ui):
    web, spec, _, monitors = ui
    # confirm removed for goto -> publishes UmGoto on a single click
    _post(web, "/adapter/jibot/goto", {"csrf_token": web._csrf, "goal": "F1_60"})
    assert monitors["jibot"].published, "expected publish without confirm"


def test_goto_requires_target(ui):
    web, spec, _, monitors = ui
    _post(web, "/adapter/jibot/goto",
          {"csrf_token": web._csrf, "goal": "   ", "confirm": "on"})
    assert monitors["jibot"].published == []  # empty/whitespace target -> no publish


def test_goto_unknown_key_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(web, "/adapter/ghost/goto",
              {"csrf_token": web._csrf, "goal": "F1_60", "confirm": "on"})
    assert e.value.code == 404


def test_io_out_pio_publishes_pio_write_out(ui):
    web, spec, _, monitors = ui
    _post(web, "/adapter/jibot/io/out",
          {"csrf_token": web._csrf, "board": "pio", "index": "3", "state": "on"})
    suffix, payload = monitors["jibot"].published[-1]
    assert suffix == "instantActions"
    action = payload["actions"][0]
    assert action["actionType"] == "pioWriteOut"
    params = {p["key"]: p["value"] for p in action["actionParameters"]}
    assert params["index"] == "3"
    assert params["state"] == "on"


def test_io_out_ezio_publishes_ezio_write_out(ui):
    web, spec, _, monitors = ui
    _post(web, "/adapter/jibot/io/out",
          {"csrf_token": web._csrf, "board": "ezio", "index": "5", "state": "off"})
    suffix, payload = monitors["jibot"].published[-1]
    action = payload["actions"][0]
    assert action["actionType"] == "ezioWriteOut"
    params = {p["key"]: p["value"] for p in action["actionParameters"]}
    assert params["index"] == "5"
    assert params["state"] == "off"


def test_io_out_rejected_when_write_action_not_enabled(ui):
    # [[actions]] 로 pioWriteOut 을 끄면 spec 에서 빠진다. 렌더는 배지를 표시
    # 전용으로 그리지만, 폼을 우회한 직접 POST 도 서버가 막아야 한다.
    web, spec, _, monitors = ui
    spec.instant_actions = tuple(
        a for a in spec.instant_actions if a.action_type != "pioWriteOut"
    )
    _post(web, "/adapter/jibot/io/out",
          {"csrf_token": web._csrf, "board": "pio", "index": "3", "state": "on"})
    assert monitors["jibot"].published == []


def test_io_out_redirects_back_to_the_actions_page(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/io/out",
                          {"csrf_token": web._csrf, "board": "pio",
                           "index": "3", "state": "on"})
    assert exc_info.value.code == 302
    assert exc_info.value.headers["Location"].startswith("/adapter/jibot/actions?")


def test_action_confirm_gate_covers_destructive_extension_actions(ui):
    # 예전엔 각 모듈 panel.html 의 required 속성뿐이라 폼을 우회하면 통과했다.
    web, _spec, _, monitors = ui
    before = len(monitors["jibot"].published)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "pioWriteOut",
            "index": "3",
            "state": "on",
        })
    assert exc_info.value.code == 302
    assert len(monitors["jibot"].published) == before
    assert "confirm%20required" in exc_info.value.headers["Location"]


def test_io_out_invalid_board_not_published(ui):
    web, spec, _, monitors = ui
    _post(web, "/adapter/jibot/io/out",
          {"csrf_token": web._csrf, "board": "bogus", "index": "1", "state": "on"})
    assert monitors["jibot"].published == []  # invalid board -> no publish


def test_io_out_invalid_state_not_published(ui):
    web, spec, _, monitors = ui
    _post(web, "/adapter/jibot/io/out",
          {"csrf_token": web._csrf, "board": "pio", "index": "1", "state": "maybe"})
    assert monitors["jibot"].published == []  # state must be on/off


def test_web_server_uses_py310_compatible_utc():
    """Robot runs Python 3.10, where datetime.UTC does not exist (added in 3.11).

    Command POST handlers (/action, /goto) build instant-action timestamps and
    must use datetime.timezone.utc; datetime.UTC raises AttributeError on the
    robot, breaking the request mid-handler (no response -> broken page). A
    runtime test cannot catch this — it passes on the 3.11+ dev interpreter — so
    this static guard scans the source instead. See commit a8eeecd."""
    from pathlib import Path
    import web.server as server_mod
    source = Path(server_mod.__file__).read_text()
    assert "datetime.UTC" not in source, (
        "datetime.UTC is Python 3.11+ only; the robot runs 3.10. "
        "Use datetime.timezone.utc instead."
    )


def test_control_multi_instance_key_dispatches():
    spec = FakeSpec("jibot:robot-1", "JIBOT robot-1")
    controllers = {"jibot:robot-1": FakeController()}
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(specs=[spec], controllers=controllers, monitors={},
                credentials=cred, host="127.0.0.1", port=0)
    web.start()
    try:
        resp = _post(web, "/adapter/jibot:robot-1/control",
                     {"csrf_token": web._csrf, "verb": "start"})
        assert resp.status == 200  # 302 -> followed to control GET (200), not 404
        assert "start" in controllers["jibot:robot-1"].calls
    finally:
        web.stop()


def _spec_with_diag(key="jibot", name="JIBOT"):
    s = FakeSpec(key, name)
    s.runnable = (
        type("D", (), {"label": "unit tests", "argv": ("{py}", "-c", "print(1)"), "note": ""})(),
    )
    s.workdir = "."
    return s


def _ui_with_diag():
    spec = _spec_with_diag()
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()}, monitors={"jibot": FakeMonitor()},
                credentials=cred, host="127.0.0.1", port=0, py="/usr/bin/true")
    web.start()
    return web


def _ui_with_manual_diag(
    mode="AUTOMATIC",
    active_emergency_stop="NONE",
    broker_connected=True,
):
    spec = FakeSpec("jibot", "JIBOT")
    spec.runnable = (
        type(
            "D",
            (),
            {
                "label": "manual clamp check",
                "argv": ("{py}", "-c", "print(1)"),
                "note": "",
                "requires_manual": True,
            },
        )(),
    )
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()},
                monitors={
                    "jibot": FakeMonitor(
                        operating_mode=mode,
                        active_emergency_stop=active_emergency_stop,
                        broker_connected=broker_connected,
                    )
                },
                credentials=cred, host="127.0.0.1", port=0, py="/usr/bin/true")
    web.start()
    return web


def test_tests_page_renders():
    web = _ui_with_diag()
    try:
        body = _get(web, "/adapter/jibot/tests").read().decode()
        assert "unit tests" in body
        assert "live state" in body
        assert "battery" in body
    finally:
        web.stop()


def test_tests_run_starts_runner():
    web = _ui_with_diag()
    try:
        resp = _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "0"})
        assert resp.status == 200
        assert "jibot" in web._runners
    finally:
        web.stop()


def test_tests_run_allows_manual_diagnostic_with_live_state():
    # Emergency-stop interlock removed — manual diagnostics run with a live
    # adapter even outside e-stop.
    web = _ui_with_manual_diag(active_emergency_stop="NONE")
    try:
        _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "0"})
        assert "jibot" in web._runners
    finally:
        web.stop()


def test_tests_run_allows_manual_diagnostic_in_emergency_stop():
    web = _ui_with_manual_diag(active_emergency_stop="MANUAL")
    try:
        _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "0"})
        assert "jibot" in web._runners
    finally:
        web.stop()


def test_tests_run_rejects_manual_diagnostic_when_mqtt_is_disconnected():
    web = _ui_with_manual_diag(
        active_emergency_stop="MANUAL",
        broker_connected=False,
    )
    try:
        _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "0"})
        assert "jibot" not in web._runners
    finally:
        web.stop()


def test_tests_run_allows_manual_diagnostic_in_emergency_operating_mode():
    web = _ui_with_manual_diag(mode="EMERGENCY")
    try:
        _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "0"})
        assert "jibot" in web._runners
    finally:
        web.stop()


def test_tests_run_rejects_out_of_range_index():
    web = _ui_with_diag()
    try:
        _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "9"})
        assert "jibot" not in web._runners
    finally:
        web.stop()


def test_tests_run_unknown_key_404():
    web = _ui_with_diag()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(web, "/adapter/ghost/tests/run", {"csrf_token": web._csrf, "index": "0"})
        assert e.value.code == 404
    finally:
        web.stop()


def test_tests_run_rejects_non_int_index():
    web = _ui_with_diag()
    try:
        _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "abc"})
        assert "jibot" not in web._runners  # non-int index -> no subprocess started
    finally:
        web.stop()


def test_tests_get_unknown_key_404():
    web = _ui_with_diag()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(web, "/adapter/ghost/tests")
        assert e.value.code == 404
    finally:
        web.stop()


def test_tests_stop_unknown_key_404():
    web = _ui_with_diag()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(web, "/adapter/ghost/tests/stop", {"csrf_token": web._csrf})
        assert e.value.code == 404
    finally:
        web.stop()


def test_tests_stop_ok():
    web = _ui_with_diag()
    try:
        resp = _post(web, "/adapter/jibot/tests/stop", {"csrf_token": web._csrf})
        assert resp.status == 200  # PRG -> followed to tests GET (200)
    finally:
        web.stop()


_SAMPLE_TOML = """[mqtt_broker]
host = "127.0.0.1"   # broker
port = 1883
"""

_FACTSHEET_TOML = """[mqtt_broker]
host = "127.0.0.1"
port = 1883

[vehicle]
serial_number = "robot-1"
manufacturer = "jibot"
vda_full_version = "2.0.0"
vehicle_ip = "10.0.0.11"
vehicle_port = 7273

[settings]
state_publish_delay = 1.0
debug_log = false
speed = 1.0

[ezi]
ezi_io = "/dev/ttyUSB0"
tray_slot_pin = [8, 9, 10]
select = 1
go = 2
ezi_motor = "/dev/ttyUSB1"
do_motor_test = 0
motor_speed = 100
close_motor_before_motion = false
origin_encoder_offset = 0
action_key = []

[factsheet]
series_name = "JIBOT"
agv_kinematic = "DIFF"
agv_class = "CARRIER"
localization_types = ["NATURAL"]
navigation_types = ["AUTONOMOUS"]
coordinate_unit_position = "mm"
coordinate_unit_orientation = "deg"
speed_min = 0.0
min_order_interval_sec = 1.0
"""


def _ui_with_config(tmp_path, validate=None):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_SAMPLE_TOML, encoding="utf-8")
    spec = FakeSpec("jibot", "JIBOT")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()}, monitors={"jibot": FakeMonitor()},
                credentials=cred, host="127.0.0.1", port=0,
                config_path=cfg, validate_config=(validate or (lambda: (True, "ok"))))
    web.start()
    return web, cfg


def _ui_with_robots(tmp_path, validate=None):
    robots = tmp_path / "robots.hcl"
    robots.write_text('robot "R-1" {}\n', encoding="utf-8")
    spec = FakeSpec("jibot", "JIBOT")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec],
        controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()},
        credentials=cred,
        host="127.0.0.1",
        port=0,
        robots_path=robots,
        validate_robots=validate,
    )
    web.start()
    return web, robots


def test_config_page_get(tmp_path):
    web, cfg = _ui_with_config(tmp_path)
    try:
        body = _get(web, "/config").read().decode()
        assert "[mqtt_broker]" in body      # section heading
        assert "127.0.0.1" in body
        assert 'name="section"' in body     # per-field form
        assert str(cfg) in body
        assert "[mqtt_broker].host" in body
    finally:
        web.stop()


def test_config_save_rewrites_field(tmp_path):
    web, cfg = _ui_with_config(tmp_path)
    try:
        _post(web, "/config", {"csrf_token": web._csrf, "section": "mqtt_broker", "key": "host", "kind": "str", "value": "10.0.0.5"})
        text = cfg.read_text()
        assert 'host = "10.0.0.5"' in text
        assert "# broker" in text  # comment preserved
    finally:
        web.stop()


def test_config_save_restores_on_invalid(tmp_path):
    web, cfg = _ui_with_config(tmp_path, validate=lambda: (False, "bad config"))
    try:
        _post(web, "/config", {"csrf_token": web._csrf, "section": "mqtt_broker", "key": "host", "kind": "str", "value": "10.0.0.9"})
        text = cfg.read_text()
        assert 'host = "127.0.0.1"' in text  # restored on invalid
        assert "10.0.0.9" not in text
    finally:
        web.stop()


def test_config_unknown_field_rejected(tmp_path):
    web, cfg = _ui_with_config(tmp_path)
    try:
        before = cfg.read_text()
        # section/key absent from config.toml -> rewrite_scalar KeyError -> file unchanged
        _post(web, "/config", {"csrf_token": web._csrf, "section": "foo", "key": "bar", "kind": "str", "value": "evil"})
        after = cfg.read_text()
        assert after == before
        assert "evil" not in after
    finally:
        web.stop()


def test_config_bad_coerce_flashes_not_500(tmp_path):
    web, cfg = _ui_with_config(tmp_path)
    try:
        before = cfg.read_text()
        resp = _post(web, "/config", {"csrf_token": web._csrf, "section": "mqtt_broker", "key": "port", "kind": "int", "value": "notanint"})
        assert resp.status == 200       # 302 -> followed to /config GET (200), not a 500
        assert cfg.read_text() == before  # rejected before any write
    finally:
        web.stop()


def test_robots_page_get(tmp_path):
    web, robots = _ui_with_robots(tmp_path)
    try:
        body = _get(web, "/robots").read().decode()
        assert 'action="/robots"' in body
        assert "robot &quot;R-1&quot; {}" in body
        assert str(robots) in body
    finally:
        web.stop()


def test_robots_save_valid_fleet(tmp_path):
    web, robots = _ui_with_robots(tmp_path)
    try:
        updated = 'robot "R-2" {\n  mqtt_host = "192.0.2.10"\n}\n'
        _post(web, "/robots", {"csrf_token": web._csrf, "text": updated})
        assert robots.read_text(encoding="utf-8") == updated
    finally:
        web.stop()


def test_robots_save_restores_invalid_fleet(tmp_path):
    web, robots = _ui_with_robots(tmp_path)
    try:
        before = robots.read_text(encoding="utf-8")
        invalid = 'robot "R-1" {}\n\nrobot "R-1" {}\n'
        _post(web, "/robots", {"csrf_token": web._csrf, "text": invalid})
        assert robots.read_text(encoding="utf-8") == before
    finally:
        web.stop()


def _ui_with_logs(reader):
    spec = FakeSpec("jibot", "JIBOT")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()}, monitors={"jibot": FakeMonitor()},
                credentials=cred, host="127.0.0.1", port=0, log_reader=reader)
    web.start()
    return web


def test_logs_page_uses_reader():
    web = _ui_with_logs(lambda key: [f"log for {key}", "second"])
    try:
        body = _get(web, "/adapter/jibot/logs").read().decode()
        assert "log for jibot" in body
        assert "second" in body
    finally:
        web.stop()


def test_logs_unknown_key_404():
    web = _ui_with_logs(lambda key: [])
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(web, "/adapter/ghost/logs")
        assert e.value.code == 404
    finally:
        web.stop()


def test_urobot_restart_requires_confirm():
    spec = FakeSpec("jibot", "JIBOT")
    urobot = FakeController()
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
        urobot_controller=urobot,
    )
    web.start()
    try:
        _post(web, "/adapter/jibot/urobot/restart", {"csrf_token": web._csrf})
        assert "restart" not in urobot.calls  # confirm 없음 -> 미호출
        _post(web, "/adapter/jibot/urobot/restart",
              {"csrf_token": web._csrf, "confirm": "on"})
        assert "restart" in urobot.calls       # confirm -> 호출
    finally:
        web.stop()


def test_urobot_restart_rejected_for_non_jibot_spec():
    spec = FakeSpec("hex", "Hexplorer")
    urobot = FakeController()
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"hex": FakeController()},
        monitors={"hex": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
        urobot_controller=urobot,
    )
    web.start()
    try:
        _post(web, "/adapter/hex/urobot/restart",
              {"csrf_token": web._csrf, "confirm": "on"})
        assert "restart" not in urobot.calls   # 비-jibot -> 거부
    finally:
        web.stop()


def test_detail_renders_urobot_button_when_controller_configured():
    spec = FakeSpec("jibot", "JIBOT")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
        urobot_controller=FakeController(),
    )
    web.start()
    try:
        body = _get(web, "/adapter/jibot").read().decode()
        assert "restart urobot" in body
        assert 'action="/adapter/jibot/urobot/restart"' in body
    finally:
        web.stop()


# ---------------------------------------------------------------------------
# Task 8: _post_action param pass-through + /manual deadman JSON endpoint
# ---------------------------------------------------------------------------

def _ui_with_manual_actions():
    """WebUi with manualMove, manualDrive, manualStop instant actions registered."""
    spec = FakeSpec("jibot", "JIBOT")
    spec.instant_actions = (
        type("A", (), {"action_type": "manualMove", "label": "Manual Move", "motion": True})(),
        type("A", (), {"action_type": "manualDrive", "label": "Manual Drive", "motion": False})(),
        type("A", (), {"action_type": "manualStop", "label": "Manual Stop", "motion": False})(),
    )
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
    )
    web.start()
    return web


def test_post_action_forwards_distance_speed_params():
    """_post_action passes extra form keys as actionParameters."""
    web = _ui_with_manual_actions()
    try:
        resp = _post(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf, "action_type": "manualMove",
            "confirm": "on", "distance": "-2500", "speed": "200",
            "flag": "2", "io": "3", "obs_avoid_dist": "900",
            "side_avoid_dist": "40", "use_io": "true", "note": "7",
        })
        assert resp.status == 200
        mon = web._monitors["jibot"]
        assert mon.published, "expected publish_json call"
        _, payload = mon.published[-1]
        params = {p["key"]: p["value"] for p in payload["actions"][0]["actionParameters"]}
        assert params == {
            "distance": "-2500",
            "speed": "200",
            "flag": "2",
            "io": "3",
            "obs_avoid_dist": "900",
            "side_avoid_dist": "40",
            "use_io": "true",
            "note": "7",
        }
    finally:
        web.stop()


def test_post_manual_returns_json_and_requires_armed():
    """POST /adapter/<key>/manual: returns JSON, requires armed flag for non-stop actions."""
    import json as _json
    web = _ui_with_manual_actions()
    try:
        # armed 누락 → 400, 미전송
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(web, "/adapter/jibot/manual", {
                "csrf_token": web._csrf, "action_type": "manualDrive", "confirm": "on",
            })
        err = exc_info.value
        assert err.code == 400
        body = _json.loads(err.read().decode())
        assert body["delivered"] is False
        assert web._monitors["jibot"].published == []

        # armed=on → 200, JSON, payload 전송됨
        resp = _post(web, "/adapter/jibot/manual", {
            "csrf_token": web._csrf, "action_type": "manualDrive",
            "confirm": "on", "armed": "on", "trans": "150",
        })
        assert resp.status == 200
        data = _json.loads(resp.read().decode())
        assert data["delivered"] is True
        mon = web._monitors["jibot"]
        assert mon.published
        _, payload = mon.published[-1]
        assert payload["actions"][0]["actionType"] == "manualDrive"
    finally:
        web.stop()


def test_post_manual_stop_allowed_without_armed():
    """manualStop is always dispatched even without the armed flag."""
    import json as _json
    web = _ui_with_manual_actions()
    try:
        resp = _post(web, "/adapter/jibot/manual", {
            "csrf_token": web._csrf, "action_type": "manualStop",
        })
        assert resp.status == 200
        data = _json.loads(resp.read().decode())
        assert data["delivered"] is True
        _, payload = web._monitors["jibot"].published[-1]
        assert payload["actions"][0]["actionType"] == "manualStop"
    finally:
        web.stop()


def test_post_manual_unknown_action_type_400():
    """Unknown action_type returns 400 JSON."""
    import json as _json
    web = _ui_with_manual_actions()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(web, "/adapter/jibot/manual", {
                "csrf_token": web._csrf, "action_type": "bogusAction", "armed": "on",
            })
        err = exc_info.value
        assert err.code == 400
        body = _json.loads(err.read().decode())
        assert body["delivered"] is False
    finally:
        web.stop()


def test_post_manual_unknown_key_404():
    """Unknown adapter key returns 404 JSON."""
    web = _ui_with_manual_actions()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(web, "/adapter/ghost/manual", {
                "csrf_token": web._csrf, "action_type": "manualStop",
            })
        assert exc_info.value.code == 404
    finally:
        web.stop()


def test_root_has_no_ezio_pio_panel(ui):
    # IO 상태는 extension 화면 소관이다. 대시보드는 어댑터 목록만 낸다.
    web, *_ = ui
    body = _get(web, "/").read().decode()
    assert "/dev/ttyUSB0" not in body
    assert "/io/out" not in body


# ---------------------------------------------------------------------------
# Task 4.3: /factsheet page — view rendered factsheet + edit [factsheet] config
# ---------------------------------------------------------------------------

def _ui_with_factsheet_config(tmp_path, validate=None):
    import shutil
    from core import configio as _cio
    cfg = tmp_path / "config.toml"
    shutil.copy(str(_cio.CONFIG_PATH), str(cfg))
    spec = FakeSpec("jibot", "JIBOT")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()}, monitors={"jibot": FakeMonitor()},
                credentials=cred, host="127.0.0.1", port=0,
                config_path=cfg, validate_config=(validate or (lambda: (True, "ok"))))
    web.start()
    return web, cfg


def test_factsheet_get_is_readonly_preview(tmp_path):
    web, _ = _ui_with_factsheet_config(tmp_path)
    try:
        body = _get(web, "/factsheet").read().decode()
        assert "seriesName" in body          # rendered JSON preview
        assert 'href="/config"' in body      # editing lives on /config now
        # preview-only: no factsheet edit form / inputs on this page
        assert 'action="/factsheet"' not in body
    finally:
        web.stop()


def test_factsheet_page_has_nav_link(tmp_path):
    web, _ = _ui_with_factsheet_config(tmp_path)
    try:
        body = _get(web, "/factsheet").read().decode()
        assert "/factsheet" in body
    finally:
        web.stop()


def test_factsheet_post_is_gone(tmp_path):
    # factsheet editing moved to /config; POST /factsheet is no longer a route.
    web, _ = _ui_with_factsheet_config(tmp_path)
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(web, "/factsheet", {"csrf_token": web._csrf, "section": "factsheet", "key": "series_name", "kind": "str", "value": "X"})
        assert e.value.code == 404
    finally:
        web.stop()


def test_mqtt_live_table_shows_dash_for_unknown_battery():
    # An unknown SoC (link down → -1 → monitor maps to None) must render "—%" in
    # both the live telemetry chip and the pose-detail row, never a bare "%".
    from web import render

    spec = FakeSpec("hex", "Hexplorer")  # non-jibot key skips the urobot motor card

    known = render._mqtt_live_table(spec, FakeSnap(battery_soc=90.0))
    assert "90.0%" in known
    assert "—%" not in known

    unknown = render._mqtt_live_table(spec, FakeSnap(battery_soc=None))
    assert "—%" in unknown


def test_action_folds_blank_key_value_rows_into_parameters(ui):
    """Actions 페이지의 extension 행은 __k0/__v0으로 온다.

    파라미터 스키마가 없는 extension에 이름 칸을 주려면 이 형태밖에 없고,
    서버가 다시 접어 넣지 않으면 액션이 __k0라는 이름의 파라미터를 받는다.
    """
    web, _, _, monitors = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "__k0": "index",
            "__v0": "3",
            "__k1": "state",
            "__v1": "on",
        })
    assert exc_info.value.code == 302
    action = monitors["jibot"].published[-1][1]["actions"][0]
    params = {p["key"]: p["value"] for p in action["actionParameters"]}
    assert params == {"index": "3", "state": "on"}


def test_action_drops_blank_key_value_rows(ui):
    """빈 행은 그냥 안 쓴 칸이다. 빈 이름의 파라미터를 보내면 안 된다."""
    web, _, _, monitors = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "__k0": "index",
            "__v0": "3",
            "__k1": "",
            "__v1": "",
            "__k2": "  ",
            "__v2": "ignored",
        })
    assert exc_info.value.code == 302
    action = monitors["jibot"].published[-1][1]["actions"][0]
    params = {p["key"]: p["value"] for p in action["actionParameters"]}
    assert params == {"index": "3"}


def test_action_drops_empty_recipe_variable_inputs(ui):
    """recipe 변수 칸을 비우면 그 파라미터는 보내지 않는다."""
    web, _, _, monitors = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "moveSpeed": "200",
            "targetMapId": "",
        })
    assert exc_info.value.code == 302
    action = monitors["jibot"].published[-1][1]["actions"][0]
    params = {p["key"]: p["value"] for p in action["actionParameters"]}
    assert params == {"moveSpeed": "200"}


def test_action_rejects_missing_declared_required_parameter(ui):
    from core.action_registry import ActionParameterSpec

    web, spec, _, monitors = ui
    spec.instant_actions = (
        type(
            "A",
            (),
            {
                "action_type": "pioWriteOut",
                "label": "PIO write",
                "motion": False,
                "parameters": (
                    ActionParameterSpec("index", required=True),
                    ActionParameterSpec("state", required=True),
                ),
            },
        )(),
    )
    before = len(monitors["jibot"].published)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "pioWriteOut",
            "confirm": "on",       # confirm 게이트를 지난 뒤의 파라미터 검증을 본다
            "index": "3",
        })
    assert exc_info.value.code == 302
    assert len(monitors["jibot"].published) == before
    assert "missing%20required%20parameter%3A%20state" in exc_info.value.headers["Location"]


def test_action_converts_declared_json_parameter(ui):
    from core.action_registry import ActionParameterSpec

    web, spec, _, monitors = ui
    spec.instant_actions = (
        type(
            "A",
            (),
            {
                "action_type": "pioScenario",
                "label": "PIO scenario",
                "motion": False,
                "parameters": (
                    ActionParameterSpec(
                        "scenario", required=True, value_type="json",
                    ),
                ),
            },
        )(),
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "pioScenario",
            "scenario": '[{"type":"out","index":1,"state":"on"}]',
        })
    assert exc_info.value.code == 302
    action = monitors["jibot"].published[-1][1]["actions"][0]
    assert action["actionParameters"][0]["value"] == [
        {"type": "out", "index": 1, "state": "on"}
    ]


def test_actions_page_renders(ui):
    web, *_ = ui
    body = _get(web, "/adapter/jibot/actions").read().decode()
    assert "Extensions" in body
    assert "Recipes" in body
    # recipe가 없는 스펙이므로 예시 파일 안내가 보여야 한다.
    assert "recipes.hcl.example" in body


def test_actions_page_unknown_adapter_404(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _get(web, "/adapter/nope/actions")
    assert exc_info.value.code == 404


def test_actions_nav_goes_straight_to_the_only_adapter(ui):
    """nav의 Actions는 전역 주소라 로봇이 하나뿐이면 곧장 그 로봇으로 보낸다."""
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _get_no_redirect(web, "/actions")
    assert exc_info.value.code == 302
    assert exc_info.value.headers["Location"] == "/adapter/jibot/actions"


def test_actions_nav_lists_adapters_when_more_than_one():
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[FakeSpec("jibot", "JIBOT"), FakeSpec("jibot2", "JIBOT 2")],
        controllers={"jibot": FakeController(), "jibot2": FakeController()},
        monitors={"jibot": FakeMonitor(), "jibot2": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
    )
    web.start()
    try:
        body = _get(web, "/actions").read().decode()
        assert 'href="/adapter/jibot/actions"' in body
        assert 'href="/adapter/jibot2/actions"' in body
    finally:
        web.stop()


def test_action_redirect_carries_the_acted_action_type(ui):
    """리다이렉트 후 어느 카드를 눌렀는지 페이지가 알아야 표시할 수 있다."""
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "return_to": "/adapter/jibot/actions#act-startPause",
        })
    assert exc_info.value.code == 302
    location = exc_info.value.headers["Location"]
    assert "act=startPause" in location
    # 앵커가 살아 있어야 브라우저가 그 카드로 돌아간다.
    assert location.endswith("#act-startPause")


def test_action_redirect_replaces_a_stale_act(ui):
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "return_to": "/adapter/jibot/actions?act=oldOne#act-startPause",
        })
    location = exc_info.value.headers["Location"]
    assert "act=oldOne" not in location
    assert "act=startPause" in location


def test_action_parameters_survive_the_redirect(ui):
    """실행 후 폼이 다시 그려져도 방금 넣은 값이 남아 있어야 한다."""
    web, *_ = ui
    with pytest.raises(urllib.error.HTTPError):
        _post_no_redirect(web, "/adapter/jibot/action", {
            "csrf_token": web._csrf,
            "action_type": "startPause",
            "index": "3",
            "state": "off",
        })
    remembered = web._remembered_action_params("jibot")
    assert remembered["startPause"] == {"index": "3", "state": "off"}


def test_remembered_parameters_are_replaced_on_the_next_run(ui):
    """다시 실행하면 그때 넣은 값이 남아야 한다 — 예전 값이 남으면 헷갈린다."""
    web, *_ = ui
    for index in ("3", "7"):
        with pytest.raises(urllib.error.HTTPError):
            _post_no_redirect(web, "/adapter/jibot/action", {
                "csrf_token": web._csrf,
                "action_type": "startPause",
                "index": index,
            })
    assert web._remembered_action_params("jibot")["startPause"] == {"index": "7"}


def test_unknown_adapter_has_no_remembered_parameters(ui):
    web, *_ = ui
    assert web._remembered_action_params("nope") == {}


def _ui_with_hcl(tmp_path):
    """config.toml + extensions.hcl 을 갖춘 WebUi 를 띄운다."""
    web, cfg = _ui_with_config(tmp_path)
    (cfg.parent / "extensions.hcl").write_text(
        'extension "pio" {\n'
        "  pio_baudrate = 38400          # 직렬 통신 속도\n"
        "  media        = 2\n"
        "}\n",
        encoding="utf-8",
    )
    return web, cfg


def test_config_page_lists_hcl_sections(tmp_path):
    """로봇 /config 화면이 HCL 파일 값도 편집 폼으로 보여야 한다.

    adaptor 는 tree-sitter 로 이미 고칠 수 있는데 이 화면만 못 고치면
    "MW 에서는 되는데 로봇에서는 안 되는" 비대칭이 남는다.
    """
    web, _cfg = _ui_with_hcl(tmp_path)
    try:
        body = _get(web, "/config").read().decode()
        assert "extensions.hcl:pio" in body
        assert "pio_baudrate" in body
    finally:
        web.stop()


def test_config_page_saves_hcl_scalar(tmp_path):
    """HCL 값 저장이 실제 파일에 반영되고 주석·줄수가 유지돼야 한다."""
    import hcl2

    web, cfg = _ui_with_hcl(tmp_path)
    ext = cfg.parent / "extensions.hcl"
    before = ext.read_text(encoding="utf-8")
    try:
        _post(web, "/config", {
            "csrf_token": web._csrf,
            "section": "extensions.hcl:pio",
            "key": "pio_baudrate",
            "kind": "int",
            "value": "9600",
        })
        after = ext.read_text(encoding="utf-8")
        assert "pio_baudrate = 9600" in after
        assert "# 직렬 통신 속도" in after
        assert len(after.splitlines()) == len(before.splitlines())
        assert hcl2.loads(after)
    finally:
        web.stop()


def test_config_hcl_unknown_key_leaves_file_untouched(tmp_path):
    """없는 키를 쓰면 파일이 그대로여야 한다(어댑터가 다음 기동에서 죽지 않게)."""
    web, cfg = _ui_with_hcl(tmp_path)
    ext = cfg.parent / "extensions.hcl"
    before = ext.read_text(encoding="utf-8")
    try:
        _post(web, "/config", {
            "csrf_token": web._csrf,
            "section": "extensions.hcl:pio",
            "key": "nonexistent_key",
            "kind": "int",
            "value": "1",
        })
        assert ext.read_text(encoding="utf-8") == before
    finally:
        web.stop()


# --- 원문 편집(/source) — MW/로봇 비대칭 해소 축 ---------------------------------
#
# 값 단위 폼(/config)으로는 recipe/extension 블록을 추가·삭제할 수 없다. MW 에서만 되고
# 로봇에서는 안 되면, MW 가 닿지 않을 때 고칠 창구가 사라진다.

import shutil as _shutil
from pathlib import Path as _Path

_ADAPTOR_CONFIG_DIR = _Path(__file__).resolve().parents[1] / "config"


def _ui_with_sources(tmp_path):
    """실제 설정 파일을 복사해 띄운다. 부팅 로더 재검증이 진짜로 도는지 보려면 정본이 필요하다."""
    for name in ("config.toml", "extensions.hcl", "recipes.hcl", "robots.hcl"):
        src = _ADAPTOR_CONFIG_DIR / name
        if src.exists():
            _shutil.copy2(src, tmp_path / name)
    spec = FakeSpec("jibot", "JIBOT")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec],
        controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()},
        credentials=cred,
        host="127.0.0.1",
        port=0,
        config_path=tmp_path / "config.toml",
        robots_path=tmp_path / "robots.hcl",
    )
    web.start()
    return web, tmp_path / "recipes.hcl"


def test_source_page_shows_raw_text_and_nav(tmp_path):
    web, recipes = _ui_with_sources(tmp_path)
    try:
        body = _get(web, "/source/recipes.hcl").read().decode()
        assert 'action="/source/recipes.hcl"' in body
        assert "pioElevatorOpen1f" in body
        assert str(recipes) in body
        # MW 에서만 되는 상태를 두지 않기 위한 진입점이 nav 에 있어야 한다
        assert 'href="/source/recipes.hcl"' in body
        assert 'href="/source/extensions.hcl"' in body
    finally:
        web.stop()


def test_source_save_adds_recipe_block(tmp_path):
    web, recipes = _ui_with_sources(tmp_path)
    try:
        updated = recipes.read_text(encoding="utf-8") + '\nrecipe "webAdded" {\n  step "pioInit" {}\n}\n'
        _post(web, "/source/recipes.hcl", {"csrf_token": web._csrf, "text": updated})
        assert 'recipe "webAdded"' in recipes.read_text(encoding="utf-8")
    finally:
        web.stop()


def test_source_save_restores_when_loader_rejects(tmp_path):
    """문법은 맞고 부팅만 멈추는 원문. hcl2 검사만 하면 통과해 로봇이 다음 기동에서 죽는다."""
    web, recipes = _ui_with_sources(tmp_path)
    try:
        before = recipes.read_text(encoding="utf-8")
        broken = before + '\nrecipe "pioElevatorOpen1f" {\n  label = "dup"\n}\n'
        _post(web, "/source/recipes.hcl", {"csrf_token": web._csrf, "text": broken})
        assert recipes.read_text(encoding="utf-8") == before
    finally:
        web.stop()


def test_source_rejects_secret_before_write(tmp_path):
    web, recipes = _ui_with_sources(tmp_path)
    try:
        before = recipes.read_text(encoding="utf-8")
        leaked = before + '\nrecipe "leak" {\n  api_key = "AKIA-LEAK"\n}\n'
        _post(web, "/source/recipes.hcl", {"csrf_token": web._csrf, "text": leaked})
        assert recipes.read_text(encoding="utf-8") == before
        assert "AKIA-LEAK" not in recipes.read_text(encoding="utf-8")
    finally:
        web.stop()


def test_source_rejects_unknown_file(tmp_path):
    web, _recipes = _ui_with_sources(tmp_path)
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _get(web, "/source/config.toml")
        assert excinfo.value.code == 404
    finally:
        web.stop()


def test_progress_route_renders(ui):
    web, spec, *_ = ui
    body = _get(web, "/adapter/jibot/progress").read().decode()
    assert "progress" in body
    assert "Actions cleared" in body


def test_progress_route_unknown_adapter_404(ui):
    web, spec, *_ = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/adapter/nope/progress")
    assert e.value.code == 404
