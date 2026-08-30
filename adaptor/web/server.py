"""AMR WebUi — stdlib HTTP 서버. Basic 인증 + 프로세스 단일 CSRF 토큰 + 서버렌더 HTML.

백엔드(specs/controllers/monitors)는 주입받는다(테스트가 fake 주입). systemctl은
controller(use_sudo=False)가 polkit 인가로 실행한다. instant action은 monitor가 MQTT publish.
"""
from __future__ import annotations

import base64
import binascii
import subprocess
import datetime
import hmac
import itertools
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from config.config import WebUiConfig, get_config
from config.fleet import (
    DEFAULT_FLEET_PATH,
    load_fleet,
    resolve_robot_path,
    robot_overrides,
)
from core import configio
from core.runner import StreamProcess
from core.systemd import ServiceMetrics

from web import render
from web.credentials import WebUiCredentials
from web.senders import MqttSender

logger = logging.getLogger(__name__)

_VERBS = ("start", "stop", "restart", "enable", "disable")
_MQTT_STATE_STALE_SEC = 15.0
_MAX_BODY = 65536  # 64 KiB cap; guards against negative/huge Content-Length


def _config_kind_of(value) -> Optional[str]:
    """값에서 /config 폼의 kind 문자열을 정한다.

    :param value: 설정 값
    :returns: "bool"/"int"/"num"/"str". 낱값이 아니면 None
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "num"
    if isinstance(value, str):
        return "str"
    return None


def _validate_hcl_file(path):
    """저장된 HCL 파일이 여전히 파싱되는지 확인한다.

    :param path: 검사할 파일
    :returns: (성공 여부, 메시지)
    """
    try:
        import hcl2

        with open(path, "r", encoding="utf-8") as handle:
            hcl2.load(handle)
        return True, "hcl valid"
    except Exception as exc:  # noqa: BLE001 - 로더 오류를 그대로 운영자에게 전달
        return False, f"{type(exc).__name__}: {exc}"


def _confirmed(form: dict) -> bool:
    return form.get("confirm") in ("on", "yes", "true", "1")


def _safe_return_to(return_to: str, default: str) -> str:
    if (not return_to or not return_to.isascii() or not return_to.startswith("/")
            or return_to.startswith("//")
            or "\\" in return_to or any(ord(char) < 32 or ord(char) == 127 for char in return_to)):
        return default
    try:
        parsed = urllib.parse.urlsplit(return_to)
    except ValueError:
        return default
    return return_to if not parsed.scheme and not parsed.netloc else default


# 로봇을 움직이거나 설비 출력을 구동해서 확인 체크박스를 요구하는 액션. 폼을 그리는
# render 와 같은 목록을 써야 폼을 우회한 POST 도 막힌다 — 두 벌로 두면 어긋난다.
_CONFIRM_REQUIRED_ACTIONS = render.CONFIRM_REQUIRED_ACTIONS


def _dashboard_visible(metrics: ServiceMetrics) -> bool:
    if not metrics.exists:
        return False
    if metrics.active_state in ("active", "failed") or getattr(metrics, "sub_state", "") == "failed":
        return True
    return metrics.enabled_state not in ("", "unknown", "not-found", "disabled")


def _manual_test_block_reason(snapshot) -> str:
    if getattr(snapshot, "adapter_online", True) is False:
        return "requires live adapter state"
    if getattr(snapshot, "state_fresh", True) is False:
        return "requires fresh adapter state"
    # Emergency-stop interlock removed: eStop detection is unreliable in the
    # field and was blocking manual diagnostics. Manual hardware tests now only
    # require a live adapter.
    return ""


#: Actions 페이지의 빈 key/value 행: __k0/__v0 형태로 들어온다.
_KV_FIELD = re.compile(r"^__([kv])(\d+)$")

class WebUi:
    def __init__(self, *, specs, controllers, monitors, senders=None, credentials: WebUiCredentials,
                 host: str = "127.0.0.1", port: int = 0, py: str | None = None,
                 config_path=None, validate_config=None, video_url: str = "", log_reader=None,
                 camera_controller=None, camera_config=None, host_controller=None,
                 urobot_controller=None, web_cfg: WebUiConfig | None = None,
                 robots_path=None, validate_robots=None, startup_error: str = ""):
        self._specs = {s.key: s for s in specs}
        self._controllers = controllers
        self._monitors = monitors
        self._senders = senders or {k: MqttSender(m) for k, m in monitors.items()}
        self._video_url = video_url
        self._camera_controller = camera_controller
        self._camera_config = camera_config
        self._host_controller = host_controller
        self._urobot_controller = urobot_controller
        self._web_cfg = web_cfg
        self._startup_error = str(startup_error or "")
        missing = set(self._specs) - set(self._controllers)
        if missing:
            raise ValueError(f"controllers missing for specs: {sorted(missing)}")
        self._cred = credentials
        self._csrf = secrets.token_urlsafe(32)
        self._header_ids: dict[str, itertools.count] = {}
        self._py = py or sys.executable
        self._runners: dict = {}
        # Last sound volume/mute the WebUi commanded per adapter, so the Sound
        # panel can mark the preset closest to the current volume as pressed.
        # The OS sink has no reliable read-back (old pactl lacks get-sink-*), so
        # this tracks what we last sent; seeded from sound_settings.startup_volume.
        self._sound_state: dict[str, dict] = {}
        # Last parameters submitted per (adapter, action). The action POST
        # redirects, so the Actions page is re-rendered from scratch and every
        # field would come back empty; the operator would retype the same pins
        # to run the same check twice. Mirrors _sound_state above.
        self._action_params: dict[str, dict[str, dict]] = {}
        self._config_path = config_path if config_path is not None else configio.CONFIG_PATH
        # 편집한 파일과 같은 파일을 검증해야 한다. 인자 없이 validate_on_disk 를 부르면
        # 항상 기본 CONFIG_PATH 를 읽어서, 여러 로봇을 한 IPC 에서 다룰 때 B 로봇 설정을
        # 고치고 A 로봇 설정을 검사하게 된다. 그러면 잘못된 편집이 되돌려지지 않는다.
        self._validate_config = (
            validate_config
            if validate_config is not None
            else lambda: configio.validate_on_disk(self._config_path)
        )
        self._robots_path = Path(robots_path) if robots_path is not None else DEFAULT_FLEET_PATH
        self._validate_robots = (
            validate_robots
            if validate_robots is not None
            else lambda: self._validate_robots_on_disk()
        )
        self._log_reader = log_reader if log_reader is not None else self._default_log_reader
        handler = self._make_handler()
        self._server = ThreadingHTTPServer((host, port), handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def _monitor_snapshot(self, key: str):
        monitor = self._monitors.get(key)
        if monitor is None:
            return None
        snap = monitor.get_snapshot()
        if hasattr(monitor, "broker_connected"):
            online = bool(getattr(monitor, "broker_connected"))  # FileMonitor: adapter alive
            last_error = getattr(monitor, "last_error", None)
            setattr(snap, "adapter_online", online)
            setattr(snap, "state_last_error", last_error)
            # back-compat aliases (render still reads mqtt_* until the render task)
            setattr(snap, "mqtt_connected", online)
            setattr(snap, "mqtt_last_error", last_error)
            if hasattr(snap, "age"):
                age = snap.age(time.time())
                stale_sec = (
                    self._web_cfg.state_stale_threshold_sec
                    if self._web_cfg is not None
                    else _MQTT_STATE_STALE_SEC
                )
                fresh = online and age is not None and age <= stale_sec
                setattr(snap, "state_age_sec", age)
                setattr(snap, "state_fresh", fresh)
                setattr(snap, "mqtt_state_age_sec", age)
                setattr(snap, "mqtt_state_fresh", fresh)
        if hasattr(monitor, "acs_broker_connected"):
            setattr(snap, "acs_broker_connected", bool(getattr(monitor, "acs_broker_connected")))
        return snap

    def _monitor_io(self, key: str):
        monitor = self._monitors.get(key)
        get_io = getattr(monitor, "get_io", None)
        return get_io() if callable(get_io) else None

    def _sound_ui_state(self, key: str) -> dict:
        """Last-commanded {volume, muted} for the Sound panel's pressed-preset
        highlight. Seeds volume from sound_settings.startup_volume on first read
        so a fresh WebUi still shows a sensible pressed preset before any click."""
        state = self._sound_state.get(key)
        if state is None:
            volume = None
            try:
                from config.config import get_config
                volume = get_config(self._config_path).sound_settings.startup_volume
            except Exception:
                volume = None  # config unreadable -> no pressed preset until set
            state = {"volume": volume, "muted": False}
            self._sound_state[key] = state
        return state

    def _remember_action_params(self, key: str, action_type: str, parameters) -> None:
        if not parameters:
            return
        self._action_params.setdefault(key, {})[action_type] = {
            str(name): str(value) for name, value in parameters
        }

    def _remembered_action_params(self, key: str) -> dict:
        return self._action_params.get(key, {})

    def _track_sound_action(self, key: str, form: dict) -> None:
        """Remember the volume/mute a setSoundVolume POST carried, so the next
        render can mark the matching preset as pressed."""
        state = self._sound_ui_state(key)
        raw_vol = form.get("volume")
        if raw_vol not in (None, ""):
            try:
                state["volume"] = max(0, min(100, int(float(raw_vol))))
                state["muted"] = False
            except (TypeError, ValueError):
                pass
        raw_mute = form.get("mute")
        if raw_mute is not None:
            state["muted"] = str(raw_mute).strip().lower() in ("1", "true", "yes", "on")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            shutdown_timeout = (
                self._web_cfg.shutdown_timeout_sec
                if self._web_cfg is not None
                else 2
            )
            self._thread.join(timeout=shutdown_timeout)

    def join(self) -> None:
        if self._thread is not None:
            self._thread.join()

    def _auth_user(self, header: str | None) -> str:
        try:
            if header and header.startswith("Basic "):
                raw = base64.b64decode(header[6:], validate=True).decode("utf-8")
                return raw.split(":", 1)[0]
        except (binascii.Error, ValueError, UnicodeDecodeError):
            pass
        return "?"

    def _auth_ok(self, header: str | None) -> bool:
        if not header or not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:], validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return False
        if ":" not in raw:
            return False
        user, pw = raw.split(":", 1)
        # Evaluate BOTH compare_digest calls unconditionally (bitwise & not `and`)
        # so a wrong username can't be distinguished from a wrong password by timing.
        ok = hmac.compare_digest(user.encode(), self._cred.username.encode())
        ok = hmac.compare_digest(pw.encode(), self._cred.password.encode()) & ok
        return bool(ok)

    def _next_header_id(self, key: str) -> int:
        counter = self._header_ids.setdefault(key, itertools.count())
        return next(counter)

    def _default_log_reader(self, key: str):
        ctrl = self._controllers.get(key)
        if ctrl is None:
            return []
        log_timeout = (
            self._web_cfg.log_read_timeout_sec
            if self._web_cfg is not None
            else 5
        )
        try:
            out = subprocess.run(
                ctrl.journal_argv(lines=200, follow=False),
                capture_output=True, text=True, timeout=log_timeout,
            )
            return (out.stdout or "").splitlines()
        except Exception as exc:  # noqa: BLE001
            return [f"[log read failed] {exc}"]

    def _camera_enabled(self) -> bool:
        return bool(getattr(self._camera_config, "enabled", False))

    def _camera_public_base_url(self, request_host: str = "") -> str:
        cfg = self._camera_config
        public = (getattr(cfg, "web_video_server_public_url", "") or "").strip()
        if public:
            return public.rstrip("/")
        host = request_host.strip()
        if host:
            hostname = host.rsplit("@", 1)[-1].split(":", 1)[0].strip("[]")
            if hostname:
                cam_port = (
                    self._web_cfg.camera_default_port
                    if self._web_cfg is not None
                    else 9001
                )
                return f"http://{hostname}:{cam_port}"
        local = (getattr(cfg, "web_video_server_url", "") or "").strip()
        return local.rstrip("/")

    def _camera_local_base_url(self) -> str:
        return (getattr(self._camera_config, "web_video_server_url", "") or "").strip().rstrip("/")

    def _camera_stream_url(self, topic: str, request_host: str = "") -> str:
        query = {"topic": topic}
        stream_type = (getattr(self._camera_config, "stream_type", "") or "").strip()
        if stream_type and stream_type != "mjpeg":
            query["type"] = stream_type
        return f"{self._camera_public_base_url(request_host)}/stream?{urllib.parse.urlencode(query, safe='/')}"

    def _camera_snapshot_url(self, topic: str) -> str:
        query = {
            "topic": topic,
            "quality": int(getattr(self._camera_config, "snapshot_quality", 40)),
        }
        return f"{self._camera_local_base_url()}/snapshot?{urllib.parse.urlencode(query, safe='/')}"

    def _camera_rows(self, request_host: str = ""):
        if not self._camera_enabled():
            return []
        return [
            (topic, self._camera_stream_url(topic, request_host), self._camera_snapshot_url(topic))
            for topic in (getattr(self._camera_config, "stream_topics", None) or [])
        ]

    def _make_handler(self):
        ui = self
        _handler_timeout = (
            ui._web_cfg.http_request_timeout_sec
            if ui._web_cfg is not None
            else 10
        )
        _max_body = (
            ui._web_cfg.max_post_body_bytes
            if ui._web_cfg is not None
            else _MAX_BODY
        )

        class Handler(BaseHTTPRequestHandler):
            timeout = _handler_timeout

            def log_message(self, *a):
                pass

            def _auth(self) -> bool:
                if ui._auth_ok(self.headers.get("Authorization")):
                    return True
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="amr-webui"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                logger.warning("web-ui auth fail from %s path=%s",
                               self.client_address[0], self.path)
                return False

            def _html(self, code: int, body: str):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _redirect(self, location: str):
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _form(self) -> dict:
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except (TypeError, ValueError):
                    length = 0
                length = max(0, min(length, _max_body))
                body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
                return {k: v[0] for k, v in parsed.items()}

            def _query(self) -> dict:
                q = urllib.parse.urlsplit(self.path).query
                return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

            def _json(self, code: int, obj: dict):
                import json as _jsonlib
                data = _jsonlib.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if not self._auth():
                    return
                ui._dispatch_get(self)

            def do_POST(self):
                if not self._auth():
                    return
                form = self._form()
                if not hmac.compare_digest(form.get("csrf_token", ""), ui._csrf):
                    self._html(403, render.page("forbidden", "<p>CSRF check failed</p>"))
                    return
                ui._dispatch_post(self, form)

        return Handler

    # --- routing (extended in later tasks) ---
    def _dispatch_get(self, h):
        path = urllib.parse.urlsplit(h.path).path
        q = h._query()
        if path.startswith("/assets/"):
            # Static brand/logo assets from adaptor/web/assets (PNG only).
            name = os.path.basename(path)  # basename blocks path traversal
            fpath = os.path.join(os.path.dirname(__file__), "assets", name)
            if name.endswith(".png") and os.path.isfile(fpath):
                try:
                    with open(fpath, "rb") as f:
                        data = f.read()
                    h.send_response(200)
                    h.send_header("Content-Type", "image/png")
                    h.send_header("Content-Length", str(len(data)))
                    h.send_header("Cache-Control", "public, max-age=86400")
                    h.end_headers()
                    h.wfile.write(data)
                    return
                except OSError:
                    pass
            h._html(404, render.page("not found", "<p>asset not found</p>"))
            return
        if path == "/":
            rows = []
            for key, spec in self._specs.items():
                metrics = self._controllers[key].poll()
                if not _dashboard_visible(metrics):
                    continue  # unit not deployed here -> hide template-only instances
                snap = self._monitor_snapshot(key)
                rows.append((spec, metrics, snap))
            camera_metrics = None
            if self._camera_controller is not None:
                cam = self._camera_controller.poll()
                if _dashboard_visible(cam):  # hide when web_video_server isn't deployed
                    camera_metrics = cam
            h._html(200, render.adapter_list_page(
                rows, camera_metrics=camera_metrics, csrf=self._csrf, q=q,
                startup_error=self._startup_error))
            return
        if path == "/actions":
            # The nav link is global but every actions page is robot-scoped:
            # one adapter goes straight through, several get a picker.
            specs = list(self._specs.values())
            if len(specs) == 1:
                h._redirect(
                    f"/adapter/{urllib.parse.quote(specs[0].key, safe=':')}/actions"
                )
                return
            h._html(200, render.actions_index_page(specs, q))
            return
        if path == "/camera":
            metrics = (
                self._camera_controller.poll()
                if self._camera_controller is not None
                else ServiceMetrics(load_state="not-found")
            )
            h._html(
                200,
                render.camera_page(
                    metrics,
                    self._camera_rows(h.headers.get("Host", "")),
                    self._csrf,
                    q,
                    enabled=self._camera_enabled(),
                ),
            )
            return
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "adapter":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            metrics = self._controllers[spec.key].poll()
            snap = self._monitor_snapshot(spec.key)
            jog = {}
            try:  # jog magnitudes for the embedded Drive D-pad; defaults if absent
                from config.config import get_config
                _mc = get_config(self._config_path).manual_control
                jog = {
                    "drive_trans": _mc.drive_trans,
                    "drive_rot": _mc.drive_rot,
                    "drive_speed": _mc.drive_speed,
                }
            except Exception:
                pass
            h._html(
                200,
                render.adapter_detail_page(
                    spec,
                    metrics,
                    snap,
                    q,
                    csrf=self._csrf,
                    runner=self._runners.get(spec.key),
                    video_url=self._video_url,
                    host_reboot_enabled=self._host_controller is not None,
                    urobot_restart_enabled=self._urobot_controller is not None,
                    page_default_refresh_sec=(
                        self._web_cfg.page_default_refresh_sec if self._web_cfg is not None else 5
                    ),
                    test_output_poll_sec=(
                        self._web_cfg.test_output_poll_sec if self._web_cfg is not None else 2
                    ),
                    sound_state=self._sound_ui_state(spec.key),
                    **jog,
                ),
            )
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "control":
            # The standalone control page was merged into the adapter page; keep
            # the URL working (old bookmarks / footer links) by redirecting.
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            h._redirect(f"/adapter/{urllib.parse.quote(spec.key, safe=':')}")
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "manual":
            # The standalone manual jog page was merged into the adapter console
            # (Drive group). GET /manual now redirects there; the POST endpoint
            # (_post_manual) still serves the embedded jog.
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            h._redirect(f"/adapter/{urllib.parse.quote(spec.key, safe=':')}")
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "progress":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            h._html(200, render.order_progress_page(
                spec,
                self._monitor_snapshot(spec.key),
                q,
                page_default_refresh_sec=(
                    self._web_cfg.page_default_refresh_sec if self._web_cfg is not None else 5
                ),
            ))
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "tests":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            snap = self._monitor_snapshot(spec.key)
            h._html(200, render.tests_page(
                spec, self._runners.get(spec.key), self._csrf, q, snapshot=snap,
                test_output_poll_sec=(
                    self._web_cfg.test_output_poll_sec if self._web_cfg is not None else 2
                ),
            ))
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "actions":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            h._html(200, render.actions_page(
                spec, self._csrf, q,
                remembered=self._remembered_action_params(spec.key),
                # extension 화면이 라이브 IO 상태까지 낸다. 어느 패널이 나오는지는
                # discover 된 모듈이 정하므로 여기서는 스냅샷만 넘긴다.
                io=self._monitor_io(spec.key),
            ))
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "logs":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            h._html(200, render.logs_page(
                spec, self._log_reader(spec.key), q,
                page_default_refresh_sec=(
                    self._web_cfg.page_default_refresh_sec if self._web_cfg is not None else 5
                ),
            ))
            return
        if path == "/config":
            text = configio.read_text(self._config_path)
            raw = configio.load_raw(self._config_path)
            locations = configio.scan_scalar_locations(text, self._config_path)
            sections = list(configio.iter_config_sections(
                raw,
                locations=locations,
                source_path=self._config_path,
            ))
            # HCL 파일도 편집 가능해야 한다. adaptor 는 tree-sitter 로 이미 고칠 수 있는데
            # 이 화면만 못 고치면 "MW 에서는 되는데 로봇에서는 안 되는" 비대칭이 남는다.
            sections.extend(self._hcl_config_sections())
            h._html(200, render.config_page(sections, self._csrf, q))
            return
        if path.startswith("/source/"):
            self._get_source(h, path[len("/source/"):], q)
            return
        if path == "/robots":
            try:
                text = self._robots_path.read_text(encoding="utf-8")
            except OSError as exc:
                h._html(500, render.robots_page("", self._robots_path, self._csrf, {"err": str(exc)}))
                return
            h._html(200, render.robots_page(text, self._robots_path, self._csrf, q))
            return
        if path == "/factsheet":
            import json as _json
            from config.config import get_config
            from core.factsheet import build_factsheet
            try:
                cfg = get_config(self._config_path)
                tray = cfg.ezi_config.tray_slot_pin
                load_positions = [f"slot{i}" for i in range(1, len(tray) + 1)]
                preview = build_factsheet(
                    cfg, header_id=0, timestamp="(preview)", simulation=False,
                    load_positions=load_positions, video_streams=None,
                )
                preview_json = _json.dumps(preview, indent=2, ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001
                preview_json = f"(preview unavailable: {exc})"
            h._html(200, render.factsheet_page(preview_json, q))
            return
        h._html(404, render.page("not found", "<p>not found</p>"))

    def _dispatch_post(self, h, form):
        path = urllib.parse.urlsplit(h.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "control":
            self._post_control(h, parts[1], form)
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "action":
            self._post_action(h, parts[1], form)
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "goto":
            self._post_goto(h, parts[1], form)
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "manual":
            self._post_manual(h, parts[1], form)
            return
        if len(parts) == 4 and parts[0] == "adapter" and parts[2] == "host" and parts[3] == "reboot":
            self._post_host_reboot(h, parts[1], form)
            return
        if len(parts) == 4 and parts[0] == "adapter" and parts[2] == "urobot" and parts[3] == "restart":
            self._post_urobot_restart(h, parts[1], form)
            return
        if len(parts) == 4 and parts[0] == "adapter" and parts[2] == "io" and parts[3] == "out":
            self._post_io_out(h, parts[1], form)
            return
        if len(parts) == 4 and parts[0] == "adapter" and parts[2] == "tests" and parts[3] == "run":
            self._post_tests_run(h, parts[1], form)
            return
        if len(parts) == 4 and parts[0] == "adapter" and parts[2] == "tests" and parts[3] == "stop":
            self._post_tests_stop(h, parts[1], form)
            return
        if path == "/camera/control":
            self._post_camera_control(h, form)
            return
        if path == "/config":
            self._post_config(h, form)
            return
        if path.startswith("/source/"):
            self._post_source(h, path[len("/source/"):], form)
            return
        if path == "/robots":
            self._post_robots(h, form)
            return
        h._html(404, render.page("not found", "<p>not found</p>"))

    def _audit(self, h, key: str, op: str, result: str) -> None:
        user = self._auth_user(h.headers.get("Authorization"))
        logger.info("web-ui control client=%s user=%s adapter=%s op=%s result=%s",
                    h.client_address[0], user, key, op, result)

    def _adapter_post_target(self, key: str, section: str, form: dict) -> str:
        base = f"/adapter/{urllib.parse.quote(key, safe=':')}"
        if form.get("return_to") == "dashboard":
            return base
        return f"{base}/{section}" if section else base

    def _post_control(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        verb = form.get("verb", "")
        target = self._adapter_post_target(key, "control", form)
        if verb not in _VERBS:
            self._audit(h, key, f"verb:{verb[:40]!r}", "rejected:unknown")
            h._redirect(f"{target}?err={urllib.parse.quote('unknown verb')}")
            return
        # service control fires without a confirm gate (host/robot actions keep theirs)
        ok, msg = getattr(self._controllers[key], verb)()
        self._audit(h, key, f"verb:{verb}", f"ok={ok} {msg}")
        flash = "msg" if ok else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(msg)}")

    def _post_camera_control(self, h, form: dict):
        # buttons live on both /camera and the dashboard; return_to picks the redirect.
        target = "/" if form.get("return_to") == "dashboard" else "/camera"
        if self._camera_controller is None:
            self._audit(h, "camera", "verb", "rejected:not-configured")
            h._redirect(f"{target}?err={urllib.parse.quote('camera service is not configured')}")
            return
        verb = form.get("verb", "")
        if verb not in _VERBS:
            self._audit(h, "camera", f"verb:{verb[:40]!r}", "rejected:unknown")
            h._redirect(f"{target}?err={urllib.parse.quote('unknown verb')}")
            return
        # camera service control fires without a confirm gate
        ok, msg = getattr(self._camera_controller, verb)()
        self._audit(h, "camera", f"verb:{verb}", f"ok={ok} {msg}")
        flash = "msg" if ok else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(msg)}")

    def _post_host_reboot(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = self._adapter_post_target(key, "control", form)
        if self._host_controller is None:
            self._audit(h, key, "host:reboot", "rejected:not-configured")
            h._redirect(f"{target}?err={urllib.parse.quote('host reboot is not configured')}")
            return
        if not _confirmed(form):
            self._audit(h, key, "host:reboot", "rejected:unconfirmed")
            h._redirect(f"{target}?err={urllib.parse.quote('confirm required')}")
            return
        ok, msg = self._host_controller.reboot()
        self._audit(h, key, "host:reboot", f"ok={ok} {msg}")
        flash = "msg" if ok else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(msg)}")

    def _post_urobot_restart(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = self._adapter_post_target(key, "control", form)
        if self._urobot_controller is None or not render._runs_urobot(spec):
            self._audit(h, key, "urobot:restart", "rejected:not-available")
            h._redirect(f"{target}?err={urllib.parse.quote('urobot restart is not available')}")
            return
        if not _confirmed(form):
            self._audit(h, key, "urobot:restart", "rejected:unconfirmed")
            h._redirect(f"{target}?err={urllib.parse.quote('confirm required')}")
            return
        ok, msg = self._urobot_controller.restart()
        self._audit(h, key, "urobot:restart", f"ok={ok} {msg}")
        flash = "msg" if ok else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(msg)}")

    def _post_tests_run(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = self._adapter_post_target(key, "tests", form)
        try:
            idx = int(form.get("index", ""))
        except ValueError:
            idx = -1
        items = spec.runnable
        if not (0 <= idx < len(items)):
            self._audit(h, key, f"test:{idx}", "rejected:bad-index")
            h._redirect(f"{target}?err={urllib.parse.quote('invalid test index')}")
            return
        diag = items[idx]
        snap = self._monitor_snapshot(key)
        block_reason = _manual_test_block_reason(snap)
        if getattr(diag, "requires_manual", False) and block_reason:
            self._audit(h, key, f"test:{diag.label}", f"rejected:{block_reason}")
            h._redirect(f"{target}?err={urllib.parse.quote(block_reason)}")
            return
        argv = [self._py if part == "{py}" else part for part in diag.argv]
        runner = self._runners.setdefault(key, StreamProcess())
        runner.start(argv, cwd=str(spec.workdir), label=diag.label)
        self._audit(h, key, f"test:{diag.label}", "started")
        h._redirect(f"{target}?msg={urllib.parse.quote('started')}")

    def _post_tests_stop(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        runner = self._runners.get(key)
        if runner is not None:
            runner.stop()
        self._audit(h, key, "test", "stopped")
        target = self._adapter_post_target(key, "tests", form)
        h._redirect(f"{target}?msg={urllib.parse.quote('stopped')}")

    def _post_action(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = f"/adapter/{urllib.parse.quote(key, safe=':')}"
        action_type = form.get("action_type", "")
        action = next((a for a in spec.instant_actions if a.action_type == action_type), None)
        if action is None:
            self._audit(h, key, f"action:{action_type[:40]!r}", "rejected:unknown")
            h._redirect(f"{target}?err={urllib.parse.quote('unknown action')}")
            return
        if action_type in _CONFIRM_REQUIRED_ACTIONS and not _confirmed(form):
            self._audit(h, key, f"action:{action_type}", "rejected:unconfirmed")
            h._redirect(f"{target}?err={urllib.parse.quote('confirm required')}")
            return
        # motion vehicle actions fire without a confirm gate (host/urobot keep theirs)
        from core import control  # reuse VDA5050 payload builder
        _reserved = ("csrf_token", "action_type", "confirm", "return_to", "verb", "armed")
        param_keys = [
            k
            for k in form
            if k not in _reserved and not _KV_FIELD.match(k)
        ]
        parameters = [(k, form[k]) for k in param_keys]
        # The Actions page offers blank name/value rows for extensions, which
        # have no parameter schema to render fields from. They arrive as
        # __k<n>/__v<n> pairs; fold them back into real parameters here so the
        # rest of this handler (and every other caller) stays unchanged.
        for index, name in sorted(
            (int(m.group(2)), form[k])
            for k, m in ((k, _KV_FIELD.match(k)) for k in form)
            if m and m.group(1) == "k"
        ):
            name = str(name).strip()
            if name and name not in _reserved:
                parameters.append((name, form.get(f"__v{index}", "")))
        parameter_specs = {
            parameter.name: parameter
            for parameter in getattr(action, "parameters", ())
        }
        missing = [
            name
            for name, parameter in parameter_specs.items()
            if parameter.required and not str(form.get(name, "")).strip()
        ]
        if missing:
            message = f"missing required parameter: {', '.join(missing)}"
            self._audit(h, key, f"action:{action_type}", f"rejected:{message}")
            h._redirect(f"{target}?err={urllib.parse.quote(message)}")
            return
        converted = []
        for name, value in parameters:
            parameter = parameter_specs.get(name)
            if parameter is not None and parameter.value_type == "json":
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    message = f"invalid JSON parameter: {name}"
                    self._audit(h, key, f"action:{action_type}", f"rejected:{message}")
                    h._redirect(f"{target}?err={urllib.parse.quote(message)}")
                    return
            converted.append((name, value))
        parameters = converted
        parameters = [(k, v) for k, v in parameters if str(v).strip() != ""] or None
        self._remember_action_params(key, action_type, parameters or ())
        payload = control.build_instant_actions(
            header_id=self._next_header_id(key),
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            version=spec.vda_full_version,
            manufacturer=spec.manufacturer,
            serial_number=spec.serial,
            action_type=action_type,
            action_id=str(uuid.uuid4()),
            parameters=parameters,
        )
        meta = {
            "confirmed": _confirmed(form),
            "source_user": self._auth_user(h.headers.get("Authorization")),
            "created_at": time.time(),
        }
        delivered, text = self._senders[key].send(payload, meta)
        # Remember the commanded volume/mute so the Sound panel can show the
        # matching preset as pressed on the next render (sink has no read-back).
        if delivered and action_type == "setSoundVolume":
            self._track_sound_action(key, form)
        self._audit(h, key, f"action:{action_type}", f"delivered={delivered}")
        flash = "msg" if delivered else "err"
        target = _safe_return_to(form.get("return_to", ""), target)
        parsed = urllib.parse.urlsplit(target)
        query = [pair for pair in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                 if pair[0] not in ("msg", "err", "act")]
        query.append((flash, text))
        # Which control was clicked. return_to's fragment scrolls the browser
        # back to it; this lets the page mark it so the operator can see which
        # one ran without hunting for the notice at the top.
        query.append(("act", action_type))
        target = urllib.parse.urlunsplit(parsed._replace(
            query=urllib.parse.urlencode(query),
        ))
        h._redirect(target)

    _IO_OUT_ACTION = {"pio": "pioWriteOut", "ezio": "ezioWriteOut"}

    def _post_io_out(self, h, key: str, form: dict):
        """Set a single PIO/EZIO output pin from a clicked out badge.

        board=pio -> pioWriteOut (index 1-8); board=ezio -> ezioWriteOut (index
        0-15). state is the TOGGLE target (on/off) computed by the badge. No
        confirm gate (fast toggle), but it drives real equipment outputs.

        배지는 extension 화면에만 있으므로 리다이렉트도 그 화면으로 돌아간다.
        쓰기 액션이 설정에서 빠진 어댑터는 거절한다 — 렌더가 배지를 표시 전용으로
        그리지만, 서버도 같은 판단을 해야 직접 POST 로 우회되지 않는다.
        """
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = f"/adapter/{urllib.parse.quote(key, safe=':')}/actions"
        board = form.get("board", "")
        action_type = self._IO_OUT_ACTION.get(board)
        index = form.get("index", "").strip()
        state = form.get("state", "").strip().lower()
        if action_type is None or not index or state not in ("on", "off"):
            self._audit(h, key, f"io_out:{board!r}", "rejected:invalid")
            h._redirect(f"{target}?err={urllib.parse.quote('invalid io out request')}")
            return
        if not any(a.action_type == action_type for a in spec.instant_actions):
            self._audit(h, key, f"io_out:{board}", "rejected:action-disabled")
            h._redirect(
                f"{target}?err={urllib.parse.quote(f'{action_type} is not enabled')}"
            )
            return
        from core import control  # reuse VDA5050 payload builder
        payload = control.build_instant_actions(
            header_id=self._next_header_id(key),
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            version=spec.vda_full_version,
            manufacturer=spec.manufacturer,
            serial_number=spec.serial,
            action_type=action_type,
            action_id=str(uuid.uuid4()),
            parameters=[("index", index), ("state", state)],
        )
        meta = {
            "confirmed": True,
            "source_user": self._auth_user(h.headers.get("Authorization")),
            "created_at": time.time(),
        }
        delivered, text = self._senders[key].send(payload, meta)
        self._audit(h, key, f"io_out:{board}:{index}={state}", f"delivered={delivered}")
        flash = "msg" if delivered else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(text)}")

    def _post_manual(self, h, key: str, form: dict):
        """Deadman/jog endpoint: returns small JSON, requires armed flag."""
        spec = self._specs.get(key)
        if spec is None:
            h._json(404, {"delivered": False, "error": "unknown adapter"})
            return
        action_type = form.get("action_type", "")
        action = next((a for a in spec.instant_actions if a.action_type == action_type), None)
        if action is None:
            self._audit(h, key, f"manual:{action_type[:40]!r}", "rejected:unknown")
            h._json(400, {"delivered": False, "error": "unknown action"})
            return
        # manualStop is always allowed; all other actions require armed + confirm.
        if action_type != "manualStop":
            if form.get("armed") not in ("on", "1", "true"):
                self._audit(h, key, f"manual:{action_type}", "rejected:unarmed")
                h._json(400, {"delivered": False, "error": "not armed"})
                return
            if action.motion and not _confirmed(form):
                h._json(400, {"delivered": False, "error": "confirm required"})
                return
        from core import control
        _reserved_manual = ("csrf_token", "action_type", "confirm", "armed", "return_to", "verb")
        param_keys = [k for k in form if k not in _reserved_manual]
        parameters = [(k, form[k]) for k in param_keys] or None
        payload = control.build_instant_actions(
            header_id=self._next_header_id(key),
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            version=spec.vda_full_version,
            manufacturer=spec.manufacturer,
            serial_number=spec.serial,
            action_type=action_type,
            action_id=str(uuid.uuid4()),
            parameters=parameters,
        )
        meta = {
            "confirmed": _confirmed(form),
            "source_user": self._auth_user(h.headers.get("Authorization")),
            "created_at": time.time(),
        }
        delivered, text = self._senders[key].send(payload, meta)
        # heartbeat 비감사: manualDrive 반복은 audit하지 않음; 시작/정지/이동만 감사.
        if action_type in ("manualMove", "manualStop"):
            self._audit(h, key, f"manual:{action_type}", f"delivered={delivered}")
        h._json(200 if delivered else 502, {"delivered": delivered, "text": text})

    def _post_goto(self, h, key: str, form: dict):
        """Send a JIBOT UmGoto to the goal/node typed in the Control view.

        Builds actionType=jibotCommand with command=UmGoto + goal=<input>; the
        adapter normalises target='goal'. UmGoto moves the robot, so a confirm
        keystroke is required.
        """
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = self._adapter_post_target(key, "control", form)
        goal = (form.get("goal") or "").strip()
        if not goal:
            self._audit(h, key, "goto", "rejected:no-target")
            h._redirect(f"{target}?err={urllib.parse.quote('goto target required')}")
            return
        # UmGoto fires without a confirm gate (host/urobot keep theirs)
        from core import control  # reuse VDA5050 payload builder
        payload = control.build_instant_actions(
            header_id=self._next_header_id(key),
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            version=spec.vda_full_version,
            manufacturer=spec.manufacturer,
            serial_number=spec.serial,
            action_type="jibotCommand",
            action_id=str(uuid.uuid4()),
            parameters=[("command", "UmGoto"), ("goal", goal)],
        )
        meta = {
            "confirmed": True,
            "source_user": self._auth_user(h.headers.get("Authorization")),
            "created_at": time.time(),
        }
        delivered, text = self._senders[key].send(payload, meta)
        self._audit(h, key, f"goto:{goal[:40]}", f"delivered={delivered}")
        flash = "msg" if delivered else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(text)}")

    def _post_config(self, h, form: dict):
        """Save ONE config value (per-field form: section/key/kind/value).

        Only the posted field is rewritten, so editing one value never touches
        another. Invalid edits are rolled back after re-validation.
        """
        section = (form.get("section") or "").strip()
        key = (form.get("key") or "").strip()
        kind = (form.get("kind") or "str").strip()
        name = f"{section}.{key}"
        if not section or not key:
            self._audit(h, "config", name, "rejected:missing field")
            h._redirect(f"/config?err={urllib.parse.quote('missing section/key')}")
            return
        try:
            value = configio.coerce(form.get("value", ""), kind)
        except ValueError as exc:
            self._audit(h, "config", name, f"rejected:{exc}")
            h._redirect(f"/config?err={urllib.parse.quote(f'{name}: {exc}')}")
            return

        # 섹션 이름에 ':' 가 있으면 HCL 파일이다(`extensions.hcl:pio`).
        # section+key 를 이으면 그대로 EPR opaque key 가 된다.
        if ":" in section:
            self._save_hcl_field(h, section, key, value, name)
            return

        try:
            literal = configio.toml_literal(value, kind)
            configio.write_config(
                self._config_path,
                lambda text: configio.rewrite_scalar(text, section, key, literal),
                validate=lambda: self._invalid_prefixed(self._validate_config()),
            )
        except (ValueError, KeyError, TimeoutError, OSError) as exc:
            self._audit(h, "config", name, f"rejected:{exc}")
            h._redirect(f"/config?err={urllib.parse.quote(f'{name}: {exc}')}")
            return
        self._audit(h, "config", name, "saved")
        h._redirect(
            f"/config?msg={urllib.parse.quote(f'{name} 저장됨 — restart to apply')}"
        )

    def _save_hcl_field(self, h, section: str, key: str, value, name: str) -> None:
        """HCL 파일의 값 하나를 저장한다.

        MW 의 EPR 적용과 같은 경로(core.paramstore + configio.write_config)를 쓴다.
        저장 후 hcl2 로 재파싱해 문법이 깨졌으면 원본을 되돌린다.

        :param h: 요청 핸들러
        :param section: ``{source}:{경로앞부분}``
        :param key: 필드명
        :param value: 저장할 값
        :param name: 감사 로그용 표시 이름
        """
        from core import paramstore

        try:
            source, path = paramstore.decode_key(f"{section}.{key}")
        except ValueError as exc:
            self._audit(h, "config", name, f"rejected:{exc}")
            h._redirect(f"/config?err={urllib.parse.quote(str(exc))}")
            return

        target = self._hcl_paths().get(source)
        if target is None or not Path(target).exists():
            self._audit(h, "config", name, "rejected:missing file")
            h._redirect(f"/config?err={urllib.parse.quote(f'{source} 파일이 없음')}")
            return

        try:
            configio.write_config(
                Path(target),
                lambda text: paramstore.set_value(source, text, path, value),
                validate=lambda: self._invalid_prefixed(_validate_hcl_file(target)),
            )
        except (ValueError, KeyError, TimeoutError, OSError) as exc:
            self._audit(h, "config", name, f"rejected:{exc}")
            h._redirect(f"/config?err={urllib.parse.quote(f'{name}: {exc}')}")
            return

        self._audit(h, "config", name, "saved")
        h._redirect(f"/config?msg={urllib.parse.quote(f'{name} 저장됨 — restart to apply')}")

    @staticmethod
    def _invalid_prefixed(result):
        """검증 실패 메시지에 원인 구분을 붙인다(치환 실패와 검증 실패를 화면에서 구별)."""
        ok, msg = result
        return ok, msg if ok else f"invalid: {msg}"

    def _hcl_config_sections(self):
        """HCL 설정 파일들을 /config 화면의 섹션 형태로 만든다.

        섹션 이름을 ``{source}:{경로앞부분}`` 으로 두면 폼의 section+key 가 그대로
        EPR opaque key(``extensions.hcl:pio.media``)가 되어 저장 경로가 하나로 합쳐진다.

        :returns: (섹션명, scalars, readonly) 목록
        """
        from core import paramstore

        out = []
        for source, path in self._hcl_paths().items():
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                continue
            grouped: dict = {}
            for value_path, found in paramstore.scan(source, text).items():
                if isinstance(found.value, (dict, list)):
                    continue  # 컨테이너는 내부 항목이 따로 나온다
                kind = _config_kind_of(found.value)
                if kind is None:
                    continue
                prefix = ".".join(str(s) for s in value_path[:-1])
                # 리스트 원소는 앞 마디에 [N] 이 붙어 있어 key 로 되돌릴 때 그대로 쓰인다
                name = str(value_path[-1])
                grouped.setdefault(f"{source}:{prefix}", []).append(
                    configio.ConfigScalar(
                        section=f"{source}:{prefix}",
                        key=name,
                        kind=kind,
                        value=found.value,
                        location=configio.ConfigLocation(
                            path=str(path), line=found.line, key_path=f"{prefix}.{name}"
                        ),
                    )
                )
            for section, rows in grouped.items():
                out.append((section, rows, []))
        return out

    def _hcl_paths(self) -> dict:
        """이 인스턴스가 편집할 HCL 파일 경로.

        :returns: source → 경로
        """
        config_dir = Path(self._config_path).parent
        return {
            "extensions.hcl": config_dir / "extensions.hcl",
            "recipes.hcl": config_dir / "recipes.hcl",
            "robots.hcl": self._robots_path,
        }

    def _validate_robots_on_disk(self):
        try:
            robots = load_fleet(self._robots_path)
            for robot in robots:
                resolved = resolve_robot_path(robot, "config", self._robots_path)
                # extensions 경로도 config와 같은 방식으로 풀어서 넘긴다. 그래야
                # WebUI 저장 검증이 실제 기동(main.py resolve_instance)과 같은
                # config.toml + extensions.hcl 조합을 검사한다.
                resolved_ext = resolve_robot_path(robot, "extensions", self._robots_path)
                resolved_recipes = resolve_robot_path(
                    robot, "recipes", self._robots_path
                )
                get_config(
                    config_path=str(resolved) if resolved is not None else None,
                    overrides=robot_overrides(robot),
                    extensions_path=str(resolved_ext) if resolved_ext is not None else None,
                    recipes_path=(
                        str(resolved_recipes)
                        if resolved_recipes is not None
                        else None
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - operator-facing validation
            return False, str(exc)
        return True, "ok"

    def _source_target(self, source: str):
        """원문 편집 대상 파일 경로를 돌려준다. 허용 목록 밖이면 None.

        :param source: 파일명
        :returns: (경로, 막힘 사유). 경로가 None 이면 알 수 없는 source
        """
        from amr_parameter_publish import TEXT_EDITABLE_SOURCES, text_edit_block_reason

        if source not in TEXT_EDITABLE_SOURCES:
            return None, f"원문 편집 대상이 아님: {source or '(없음)'}"
        target = self._hcl_paths().get(source)
        if target is None or not Path(target).exists():
            return None, f"{source} 파일이 없음"
        try:
            text = Path(target).read_text(encoding="utf-8")
        except OSError as exc:
            return None, str(exc)
        # MW 와 같은 게이트를 쓴다. 로봇 web UI 도 공유 계정으로 네트워크에 열려 있어
        # secret 이 원문으로 새는 경로는 동일하다
        return Path(target), text_edit_block_reason(source, text) or ""

    def _validate_config_sources(self):
        """저장된 설정이 **실제 부팅 로더**를 통과하는지 확인한다(MW 적용 경로와 같은 검사).

        ``hcl2.load`` 문법 검사만 하면 중복 recipe 이름처럼 파싱은 되는데 부팅이 멈추는
        파일이 통과한다. 블록을 통째로 추가·삭제하는 원문 편집에서는 흔한 실패다.

        :returns: (성공 여부, 메시지)
        """
        paths = self._hcl_paths()
        kwargs = {}
        for source, keyword in (("extensions.hcl", "extensions_path"), ("recipes.hcl", "recipes_path")):
            candidate = paths.get(source)
            if candidate is not None and Path(candidate).exists():
                kwargs[keyword] = candidate
        try:
            get_config(config_path=self._config_path, **kwargs)
            return True, "config valid"
        except Exception as exc:  # noqa: BLE001 - 로더 오류를 그대로 운영자에게 전달
            return False, f"{type(exc).__name__}: {exc}"

    def _get_source(self, h, source: str, q: dict):
        """원문 편집 화면을 그린다."""
        source = urllib.parse.unquote(source)
        target, blocked = self._source_target(source)
        if target is None:
            h._html(404, render.page("not found", f"<p>{render.esc(blocked)}</p>"))
            return
        text = Path(target).read_text(encoding="utf-8")
        h._html(200, render.source_page(source, text, target, self._csrf, q, blocked))

    def _post_source(self, h, source: str, form: dict):
        """원문을 그대로 저장하고, 부팅 로더 재검증에 실패하면 되돌린다."""
        source = urllib.parse.unquote(source)
        redirect = f"/source/{urllib.parse.quote(source)}"
        target, blocked = self._source_target(source)
        if target is None or blocked:
            reason = blocked or "unknown source"
            self._audit(h, "source", source, f"rejected:{reason}")
            h._redirect(f"{redirect}?err={urllib.parse.quote(reason)}")
            return

        text = form.get("text")
        if text is None:
            self._audit(h, "source", source, "rejected:missing text")
            h._redirect(f"{redirect}?err=missing+text")
            return

        from amr_parameter_publish import text_edit_block_reason

        # 들어온 원문도 검사한다. 쓴 뒤에 막으면 이미 파일에 박힌 뒤다
        incoming = text_edit_block_reason(source, text)
        if incoming:
            self._audit(h, "source", source, f"rejected:{incoming}")
            h._redirect(f"{redirect}?err={urllib.parse.quote(incoming)}")
            return

        try:
            configio.write_config(
                Path(target),
                lambda _current: text,
                validate=lambda: self._invalid_prefixed(self._validate_config_sources()),
            )
        except (ValueError, TimeoutError, OSError) as exc:
            self._audit(h, "source", source, f"rejected:{exc}")
            h._redirect(f"{redirect}?err={urllib.parse.quote(str(exc))}")
            return

        self._audit(h, "source", source, "saved")
        h._redirect(f"{redirect}?msg=" + urllib.parse.quote(f"{source} 저장됨 — restart to apply"))

    def _post_robots(self, h, form: dict):
        """Save the fleet file verbatim, restoring it when validation fails."""
        text = form.get("text")
        if text is None:
            self._audit(h, "robots", "robots.hcl", "rejected:missing text")
            h._redirect("/robots?err=missing+text")
            return
        try:
            configio.write_config(
                self._robots_path,
                lambda _current: text,
                validate=lambda: self._invalid_prefixed(self._validate_robots()),
            )
        except (ValueError, TimeoutError, OSError) as exc:
            self._audit(h, "robots", "robots.hcl", f"rejected:{exc}")
            h._redirect(f"/robots?err={urllib.parse.quote(str(exc))}")
            return
        self._audit(h, "robots", "robots.hcl", "saved")
        h._redirect(
            "/robots?msg=" + urllib.parse.quote("robots.hcl 저장됨 — restart to apply")
        )
