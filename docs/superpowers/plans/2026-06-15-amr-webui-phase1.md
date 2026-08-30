# AMR WebUi (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Browser WebUi (Dashboard + Control) that operates the AMR adapters (jibot/hexplorer), reusing the curses TUI's pure-backend modules — first step toward replacing the curses TUI.

**Architecture:** New `adaptor/web/` package mirroring `adaptor/tui/`. stdlib `http.server` (`ThreadingHTTPServer`) + HTTP Basic auth + single per-process CSRF token + server-rendered HTML (no JS/WebSocket). Reuses `tui/registry.py` (`build_registry`/`AdaptorSpec`), `tui/monitor.py` (`MqttMonitor`/`StateSnapshot`), `tui/systemd.py` (`SystemdController`/`ServiceMetrics`), `tui/control.py` (`build_instant_actions`). edge-agent specs filtered out WebUi-side (it has its own WebUi). systemctl runs no-sudo via polkit least-privilege.

**Tech Stack:** Python 3.12, stdlib only (http.server, urllib, hmac, secrets, base64, uuid, datetime), paho-mqtt (already a dep, via MqttMonitor), pytest (run with `uv run pytest` from `adaptor/`).

**Spec:** `docs/superpowers/specs/2026-06-15-amr-webui-design.md` (rev.2).

**Conventions for every task:**
- All commands run from `adaptor/` (the flat-layout project root). Tests import first-party modules directly (`import web.server`, `from config.config import get_config`) because `adaptor/pyproject.toml` sets `pythonpath = ["."]`.
- Run tests with `env PYTHONPATH= uv run pytest ...` (clear PYTHONPATH to avoid the host's ROS pollution).
- Commit after each task.

---

## File Structure

**Create:**
- `adaptor/web/__init__.py` — package marker (empty).
- `adaptor/web/credentials.py` — credential file load + strong-password validation.
- `adaptor/web/render.py` — server-rendered HTML helpers + pages.
- `adaptor/web/server.py` — `WebUi` class: HTTP server, auth, CSRF, routing, holds specs/controllers/monitors, instant-action payload generation, audit log.
- `adaptor/web/__main__.py` — `python -m web` entry: sys.path bootstrap, config load, registry build + edge-agent filter, wire controllers/monitors, start server.
- `adaptor/run-web.sh` — launcher (mirrors `run-main.sh`, `.venv`-first).
- `adaptor/tests/test_web_credentials.py`, `adaptor/tests/test_web_render.py`, `adaptor/tests/test_web_server.py`, `adaptor/tests/test_web_main.py` — tests.
- `scripts/systemd/amr-webui.service` — systemd unit.
- `scripts/systemd/amr-webui-polkit.rules` — polkit least-privilege rule template.

**Modify:**
- `adaptor/config/config.toml` (and any example) — add a `[web_ui]` section (commented example).
- `scripts/setup-adaptor-service.sh` — install the WebUi unit + polkit rule (documented step).

**Responsibilities:** `credentials.py` = auth material only. `render.py` = HTML only (no I/O, no backend calls). `server.py` = request handling + backend orchestration. `__main__.py` = wiring/bootstrap only. Keep each file single-purpose; do not put HTML in `server.py` or backend calls in `render.py`.

---

## Task 1: Credentials loader

**Files:**
- Create: `adaptor/web/__init__.py` (empty)
- Create: `adaptor/web/credentials.py`
- Test: `adaptor/tests/test_web_credentials.py`

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_web_credentials.py
import pytest
from web.credentials import WebUiCredentials, load_credentials


def _write(tmp_path, text):
    p = tmp_path / "web-credentials.toml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_valid(tmp_path):
    path = _write(tmp_path, 'username = "admin"\npassword = "a-strong-pass-123"\n')
    cred = load_credentials(path)
    assert isinstance(cred, WebUiCredentials)
    assert cred.username == "admin"
    assert cred.password == "a-strong-pass-123"


def test_rejects_short_password(tmp_path):
    path = _write(tmp_path, 'username = "admin"\npassword = "short"\n')
    with pytest.raises(ValueError):
        load_credentials(path)


@pytest.mark.parametrize("pw", ["set-me-please", "changeme1234", "passwordpass", "admin-admin99"])
def test_rejects_placeholder(tmp_path, pw):
    path = _write(tmp_path, f'username = "admin"\npassword = "{pw}"\n')
    with pytest.raises(ValueError):
        load_credentials(path)


def test_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError):
        load_credentials(str(tmp_path / "nope.toml"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= uv run pytest tests/test_web_credentials.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web'`.

- [ ] **Step 3: Write minimal implementation**

```python
# adaptor/web/credentials.py
"""WebUi 자격증명 — TOML 파일(username/password) 로드 + 강한 비밀번호 검증."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

_MIN_LEN = 12
_PLACEHOLDERS = ("set-me", "changeme", "password", "admin")


@dataclass(frozen=True)
class WebUiCredentials:
    username: str
    password: str


def load_credentials(path: str) -> WebUiCredentials:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"web-ui credentials file not found: {path}")
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not username:
        raise ValueError("web-ui credentials: username required")
    if len(password) < _MIN_LEN:
        raise ValueError(f"web-ui credentials: password must be >= {_MIN_LEN} chars")
    low = password.lower()
    if any(token in low for token in _PLACEHOLDERS):
        raise ValueError("web-ui credentials: password contains a placeholder/default token")
    return WebUiCredentials(username=username, password=password)
```

```python
# adaptor/web/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= uv run pytest tests/test_web_credentials.py -q`
Expected: PASS (4 tests + parametrize cases).

- [ ] **Step 5: Commit**

```bash
git add web/__init__.py web/credentials.py tests/test_web_credentials.py
git commit -m "feat(web): credential loader with strong-password validation"
```

---

## Task 2: Render helpers + pages

**Files:**
- Create: `adaptor/web/render.py`
- Test: `adaptor/tests/test_web_render.py`

Render functions are pure (string in → HTML out). They receive already-prepared data (the server does backend calls + edge-agent filtering). `AdaptorView`/`StateSnapshot`/`ServiceMetrics` are passed in; render only reads attributes.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_web_render.py
from dataclasses import dataclass
from web import render


@dataclass
class _Spec:
    key: str
    display_name: str
    monitor_kind: str = "vda5050"
    instant_actions: tuple = ()


@dataclass
class _Action:
    action_type: str
    label: str
    motion: bool


@dataclass
class _Metrics:
    exists: bool = True
    active_state: str = "active"
    enabled_state: str = "enabled"
    uptime_sec: int = 120
    cpu_percent: float = 1.5
    memory_bytes: int = 1048576
    restarts: int = 0


@dataclass
class _Snap:
    connection_state: str = "ONLINE"
    operating_mode: str = "AUTOMATIC"
    battery_soc: float = 87.0
    x: float = 1.0
    y: float = 2.0
    theta: float = 0.0
    errors: tuple = ()


def test_esc_escapes_html():
    assert render.esc('<a>&"') == "&lt;a&gt;&amp;&quot;"


def test_csrf_field_present():
    assert 'name="csrf_token"' in render._csrf_field("tok123")
    assert "tok123" in render._csrf_field("tok123")


def test_adapter_list_renders_rows():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows)
    assert "JIBOT" in out
    assert "/adapter/jibot" in out
    assert "active" in out


def test_detail_omits_live_for_monitorless():
    spec = _Spec("hex", "Hexplorer", monitor_kind="none")
    out = render.adapter_detail_page(spec, _Metrics(), None, {})
    assert "Hexplorer" in out
    assert "battery" not in out.lower()  # no live section


def test_control_marks_motion_action_confirm():
    spec = _Spec("jibot", "JIBOT", instant_actions=(_Action("startPause", "Pause", True),))
    out = render.control_page(spec, _Metrics(), "tok", {})
    assert 'name="csrf_token"' in out
    assert "confirm" in out  # motion action shows a confirm checkbox
    assert "startPause" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= uv run pytest tests/test_web_render.py -q`
Expected: FAIL — `AttributeError: module 'web.render' has no attribute ...` / import error.

- [ ] **Step 3: Write minimal implementation**

```python
# adaptor/web/render.py
"""서버렌더 HTML — f-string + 단일 escape 헬퍼. 외부 자원/JS 없음."""
from __future__ import annotations

import html

_CSS = (
    "body{font-family:sans-serif;margin:1.5rem;max-width:820px;color:#222}"
    "nav a{margin-right:.8rem}table{border-collapse:collapse;margin:.4rem 0}"
    "td,th{border:1px solid #ccc;padding:.2rem .6rem;text-align:left}"
    ".err{color:#b00}.ok{color:#070}.btn{margin:.15rem}"
)


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _csrf_field(csrf: str) -> str:
    return f'<input type="hidden" name="csrf_token" value="{esc(csrf)}">'


def _nav() -> str:
    return '<a href="/">Adapters</a>'


def _flash(q: dict) -> str:
    if "err" in q:
        return f'<p class="err">{esc(q["err"])}</p>'
    if "msg" in q:
        return f'<p class="ok">{esc(q.get("msg") or "ok")}</p>'
    return ""


def page(title: str, body: str, refresh: int | None = None) -> str:
    meta = f'<meta http-equiv="refresh" content="{esc(refresh)}">' if refresh else ""
    return (
        f'<!doctype html><html><head><meta charset="utf-8">{meta}'
        f"<title>AMR WebUi — {esc(title)}</title><style>{_CSS}</style></head><body>"
        f"<h2>AMR Adaptor</h2><nav>{_nav()}</nav><hr>{body}</body></html>"
    )


def adapter_list_page(rows) -> str:
    # rows: iterable of (spec, metrics, snapshot|None)
    trs = []
    for spec, m, snap in rows:
        live = "-"
        if snap is not None:
            errc = len(snap.errors or ())
            live = f"{esc(snap.connection_state)} / {esc(snap.operating_mode)} / {esc(snap.battery_soc)}% / err {errc}"
        trs.append(
            f"<tr><td><a href=\"/adapter/{esc(spec.key)}\">{esc(spec.display_name)}</a></td>"
            f"<td>{esc(m.active_state)}</td><td>{live}</td></tr>"
        )
    body = (
        "<table><tr><th>adapter</th><th>service</th><th>live</th></tr>"
        + "".join(trs)
        + "</table>"
    )
    return page("adapters", body)


def adapter_detail_page(spec, metrics, snapshot, q: dict) -> str:
    try:
        r = int(q.get("refresh", "5"))
    except (TypeError, ValueError):
        r = 5
    m = metrics
    svc = (
        "<table>"
        f"<tr><td>service</td><td>{esc(m.active_state)} ({esc(m.enabled_state)})</td></tr>"
        f"<tr><td>uptime</td><td>{esc(m.uptime_sec)}s</td></tr>"
        f"<tr><td>cpu</td><td>{esc(m.cpu_percent)}%</td></tr>"
        f"<tr><td>mem</td><td>{esc(m.memory_bytes)}</td></tr>"
        f"<tr><td>restarts</td><td>{esc(m.restarts)}</td></tr>"
        "</table>"
    )
    live = ""
    if spec.monitor_kind != "none" and snapshot is not None:
        s = snapshot
        errs = "(none)" if not s.errors else ", ".join(esc(e) for e in s.errors)
        live = (
            "<h3>live</h3><table>"
            f"<tr><td>connection</td><td>{esc(s.connection_state)}</td></tr>"
            f"<tr><td>mode</td><td>{esc(s.operating_mode)}</td></tr>"
            f"<tr><td>battery</td><td>{esc(s.battery_soc)}%</td></tr>"
            f"<tr><td>pose</td><td>{esc(s.x)}, {esc(s.y)}, {esc(s.theta)}</td></tr>"
            f"<tr><td>errors</td><td>{errs}</td></tr>"
            "</table>"
        )
    body = (
        f"{_flash(q)}<h3>{esc(spec.display_name)}</h3>{svc}{live}"
        f'<p><a href="/adapter/{esc(spec.key)}/control">control</a> | '
        f'<a href="/adapter/{esc(spec.key)}?refresh={esc(r)}">refresh</a> (auto {esc(r)}s; 0=off)</p>'
    )
    return page(spec.display_name, body, refresh=(r if r > 0 else None))


_VERBS = ("start", "stop", "restart", "enable", "disable")
_DESTRUCTIVE = ("stop", "restart", "disable")


def _verb_form(spec_key: str, verb: str, csrf: str) -> str:
    confirm = ""
    if verb in _DESTRUCTIVE:
        confirm = '<label><input type="checkbox" name="confirm"> confirm</label> '
    return (
        f'<form class="btn" method="post" action="/adapter/{esc(spec_key)}/control">'
        f'{_csrf_field(csrf)}<input type="hidden" name="verb" value="{esc(verb)}">'
        f"{confirm}<button>{esc(verb)}</button></form>"
    )


def _action_form(spec_key: str, action, csrf: str) -> str:
    confirm = ""
    if action.motion:
        confirm = '<label><input type="checkbox" name="confirm"> confirm</label> '
    return (
        f'<form class="btn" method="post" action="/adapter/{esc(spec_key)}/action">'
        f'{_csrf_field(csrf)}<input type="hidden" name="action_type" value="{esc(action.action_type)}">'
        f"{confirm}<button>{esc(action.label)}</button></form>"
    )


def control_page(spec, metrics, csrf: str, q: dict) -> str:
    verbs = "".join(_verb_form(spec.key, v, csrf) for v in _VERBS)
    actions = "".join(_action_form(spec.key, a, csrf) for a in spec.instant_actions)
    body = (
        f"{_flash(q)}<h3>{esc(spec.display_name)} — control</h3>"
        f"<p>service: {esc(metrics.active_state)}</p>"
        f"<h4>service</h4>{verbs}"
        f"<h4>actions</h4>{actions or '(none)'}"
        f'<p><a href="/adapter/{esc(spec.key)}">&larr; dashboard</a></p>'
    )
    return page(f"{spec.display_name} control", body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= uv run pytest tests/test_web_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/render.py tests/test_web_render.py
git commit -m "feat(web): server-rendered HTML pages (list/detail/control)"
```

---

## Task 3: WebUi server — auth, CSRF, routing skeleton

**Files:**
- Create: `adaptor/web/server.py`
- Test: `adaptor/tests/test_web_server.py`

The `WebUi` constructor takes injected collaborators so tests pass fakes (no real systemctl/MQTT). Routes added incrementally (Tasks 4-6); this task delivers auth + CSRF + 404 + a working server on an ephemeral port.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_web_server.py
import base64
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import pytest
from web.credentials import WebUiCredentials
from web.server import WebUi


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
    unit: str = "jibot-adapter.service"
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
class FakeSnap:
    connection_state: str = "ONLINE"
    operating_mode: str = "AUTOMATIC"
    battery_soc: float = 90.0
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0
    errors: tuple = ()


@dataclass
class FakeMonitor:
    published: list = field(default_factory=list)
    def get_snapshot(self):
        return FakeSnap()
    def publish_json(self, topic_suffix, obj, qos=0):
        self.published.append((topic_suffix, obj))
        return True


@dataclass
class FakeSpec:
    key: str
    display_name: str
    unit: str = "jibot-adapter.service"
    manufacturer: str = "jibot"
    serial: str = "robot-1"
    vda_full_version: str = "2.0.0"
    topic_prefix: str = "amr/v2/jibot/robot-1"
    monitor_kind: str = "vda5050"
    instant_actions: tuple = ()


@pytest.fixture
def ui():
    spec = FakeSpec("jibot", "JIBOT")
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


def _get(web, path, auth=("admin", "a-strong-pass-123")):
    url = f"http://127.0.0.1:{web.port}{path}"
    req = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.server'`.

- [ ] **Step 3: Write minimal implementation**

```python
# adaptor/web/server.py
"""AMR WebUi — stdlib HTTP 서버. Basic 인증 + 프로세스 단일 CSRF 토큰 + 서버렌더 HTML.

백엔드(specs/controllers/monitors)는 주입받는다(테스트가 fake 주입). systemctl은
controller(use_sudo=False)가 polkit 인가로 실행한다. instant action은 monitor가 MQTT publish.
"""
from __future__ import annotations

import base64
import binascii
import datetime
import hmac
import itertools
import logging
import secrets
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from web import render
from web.credentials import WebUiCredentials

logger = logging.getLogger(__name__)

_VERBS = ("start", "stop", "restart", "enable", "disable")
_DESTRUCTIVE = ("stop", "restart", "disable")


def _confirmed(form: dict) -> bool:
    return form.get("confirm") in ("on", "yes", "true", "1")


class WebUi:
    def __init__(self, *, specs, controllers, monitors, credentials: WebUiCredentials,
                 host: str = "127.0.0.1", port: int = 0):
        self._specs = {s.key: s for s in specs}
        self._controllers = controllers
        self._monitors = monitors
        self._cred = credentials
        self._csrf = secrets.token_urlsafe(32)
        self._header_ids: dict[str, itertools.count] = {}
        handler = self._make_handler()
        self._server = ThreadingHTTPServer((host, port), handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

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
        ok = hmac.compare_digest(user.encode(), self._cred.username.encode())
        ok = hmac.compare_digest(pw.encode(), self._cred.password.encode()) & ok
        return bool(ok)

    def _next_header_id(self, key: str) -> int:
        counter = self._header_ids.setdefault(key, itertools.count())
        return next(counter)

    def _make_handler(self):
        ui = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 10

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
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
                return {k: v[0] for k, v in parsed.items()}

            def _query(self) -> dict:
                q = urllib.parse.urlsplit(self.path).query
                return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

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
        h._html(404, render.page("not found", "<p>not found</p>"))

    def _dispatch_post(self, h, form):
        h._html(404, render.page("not found", "<p>not found</p>"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`
Expected: PASS (auth 401 ×2, unknown 404). The "/" route 404s for now — fixed in Task 4.

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_web_server.py
git commit -m "feat(web): WebUi server skeleton (Basic auth + CSRF + routing)"
```

---

## Task 4: Dashboard routes (list + detail)

**Files:**
- Modify: `adaptor/web/server.py` (`_dispatch_get`)
- Test: `adaptor/tests/test_web_server.py` (add cases)

- [ ] **Step 1: Write the failing test**

```python
# append to adaptor/tests/test_web_server.py

def test_root_lists_adapters(ui):
    web, spec, *_ = ui
    body = _get(web, "/").read().decode()
    assert "JIBOT" in body
    assert "/adapter/jibot" in body


def test_detail_renders(ui):
    web, *_ = ui
    body = _get(web, "/adapter/jibot").read().decode()
    assert "live" in body  # monitor_kind=vda5050 -> live section
    assert "battery" in body


def test_detail_unknown_key_404(ui):
    web, *_ = ui
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(web, "/adapter/ghost")
    assert e.value.code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`
Expected: FAIL — `/` and `/adapter/jibot` return 404 (current skeleton).

- [ ] **Step 3: Write minimal implementation**

Replace `_dispatch_get` in `adaptor/web/server.py`:

```python
    def _dispatch_get(self, h):
        path = urllib.parse.urlsplit(h.path).path
        q = h._query()
        if path == "/":
            rows = []
            for key, spec in self._specs.items():
                metrics = self._controllers[key].poll()
                snap = self._monitors[key].get_snapshot() if key in self._monitors else None
                rows.append((spec, metrics, snap))
            h._html(200, render.adapter_list_page(rows))
            return
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "adapter":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            metrics = self._controllers[spec.key].poll()
            snap = self._monitors[spec.key].get_snapshot() if spec.key in self._monitors else None
            h._html(200, render.adapter_detail_page(spec, metrics, snap, q))
            return
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "control":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            metrics = self._controllers[spec.key].poll()
            h._html(200, render.control_page(spec, metrics, self._csrf, q))
            return
        h._html(404, render.page("not found", "<p>not found</p>"))
```

> Note: this adds the GET `/adapter/<key>/control` page too (used by Task 5's POST). Adapter keys with `:` arrive URL-decoded in `path`; `self._specs.get(parts[1])` matches the raw key.

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`
Expected: PASS (list, detail, 404, plus Task 3 cases).

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_web_server.py
git commit -m "feat(web): dashboard list + detail + control GET routes"
```

---

## Task 5: Control POST (systemctl verbs)

**Files:**
- Modify: `adaptor/web/server.py` (`_dispatch_post`)
- Test: `adaptor/tests/test_web_server.py` (add cases)

- [ ] **Step 1: Write the failing test**

```python
# append to adaptor/tests/test_web_server.py
import urllib.parse


def _post(web, path, fields):
    url = f"http://127.0.0.1:{web.port}{path}"
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    token = base64.b64encode(b"admin:a-strong-pass-123").decode()
    req.add_header("Authorization", f"Basic {token}")
    # urllib follows the 302 to the GET target (200)
    return urllib.request.urlopen(req, timeout=5)


def test_control_start_invokes_controller(ui):
    web, spec, controllers, _ = ui
    resp = _post(web, "/adapter/jibot/control",
                 {"csrf_token": web._csrf, "verb": "start"})
    assert resp.status == 200
    assert "start" in controllers["jibot"].calls


def test_control_stop_requires_confirm(ui):
    web, spec, controllers, _ = ui
    # no confirm -> rejected, controller not called
    _post(web, "/adapter/jibot/control", {"csrf_token": web._csrf, "verb": "stop"})
    assert "stop" not in controllers["jibot"].calls
    # with confirm -> called
    _post(web, "/adapter/jibot/control",
          {"csrf_token": web._csrf, "verb": "stop", "confirm": "on"})
    assert "stop" in controllers["jibot"].calls


def test_control_rejects_unknown_verb(ui):
    web, spec, controllers, _ = ui
    _post(web, "/adapter/jibot/control", {"csrf_token": web._csrf, "verb": "nuke"})
    assert controllers["jibot"].calls == []


def test_control_missing_csrf_403(ui):
    web, *_ = ui
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(web, "/adapter/jibot/control", {"verb": "start"})
    assert e.value.code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`
Expected: FAIL — control POST 404s (current `_dispatch_post` is a stub).

- [ ] **Step 3: Write minimal implementation**

Replace `_dispatch_post` in `adaptor/web/server.py`:

```python
    def _dispatch_post(self, h, form):
        path = urllib.parse.urlsplit(h.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "control":
            self._post_control(h, parts[1], form)
            return
        h._html(404, render.page("not found", "<p>not found</p>"))

    def _audit(self, h, key: str, op: str, result: str) -> None:
        logger.info("web-ui control client=%s adapter=%s op=%s result=%s",
                    h.client_address[0], key, op, result)

    def _post_control(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        verb = form.get("verb", "")
        target = f"/adapter/{urllib.parse.quote(key)}/control"
        if verb not in _VERBS:
            self._audit(h, key, f"verb:{verb}", "rejected:unknown")
            h._redirect(f"{target}?err={urllib.parse.quote('unknown verb')}")
            return
        if verb in _DESTRUCTIVE and not _confirmed(form):
            self._audit(h, key, f"verb:{verb}", "rejected:unconfirmed")
            h._redirect(f"{target}?err={urllib.parse.quote('confirm required')}")
            return
        ok, msg = getattr(self._controllers[key], verb)()
        self._audit(h, key, f"verb:{verb}", f"ok={ok} {msg}")
        flash = "msg" if ok else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(msg)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`
Expected: PASS (start invokes, stop confirm-gated, unknown verb rejected, CSRF 403).

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_web_server.py
git commit -m "feat(web): control POST (systemctl verbs, confirm gate, audit)"
```

---

## Task 6: Action POST (MQTT instant actions)

**Files:**
- Modify: `adaptor/web/server.py` (`_dispatch_post`, add `_post_action`)
- Test: `adaptor/tests/test_web_server.py` (add cases)

- [ ] **Step 1: Write the failing test**

```python
# append to adaptor/tests/test_web_server.py

def _spec_with_action(motion):
    s = FakeSpec("jibot", "JIBOT")
    s.instant_actions = (type("A", (), {"action_type": "startPause", "label": "Pause", "motion": motion})(),)
    return s


def test_action_publishes_payload(ui):
    web, spec, _, monitors = ui
    resp = _post(web, "/adapter/jibot/action",
                 {"csrf_token": web._csrf, "action_type": "startPause"})
    assert resp.status == 200
    assert monitors["jibot"].published, "expected publish_json call"
    suffix, payload = monitors["jibot"].published[-1]
    assert suffix == "instantActions"
    # payload carries the generated header + action fields
    assert payload["headerId"] == 0
    assert payload["actions"][0]["actionType"] == "startPause"
    assert payload["actions"][0]["actionId"]


def test_action_motion_requires_confirm():
    # build a UI whose action is a motion action
    spec = _spec_with_action(motion=True)
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    mon = FakeMonitor()
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()},
                monitors={"jibot": mon}, credentials=cred, host="127.0.0.1", port=0)
    web.start()
    try:
        _post(web, "/adapter/jibot/action", {"csrf_token": web._csrf, "action_type": "startPause"})
        assert mon.published == []  # blocked, no confirm
        _post(web, "/adapter/jibot/action",
              {"csrf_token": web._csrf, "action_type": "startPause", "confirm": "on"})
        assert len(mon.published) == 1
    finally:
        web.stop()
```

> The existing `ui` fixture's spec has no instant actions, so non-motion publishing is exercised by temporarily giving it one. Adjust the fixture spec to include a non-motion action: in the `ui` fixture, change `FakeSpec("jibot", "JIBOT")` to attach `spec.instant_actions = (type("A", (), {"action_type": "startPause", "label": "Pause", "motion": False})(),)` before constructing `WebUi`.

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`
Expected: FAIL — action POST 404s; payload not built.

- [ ] **Step 3: Write minimal implementation**

In `adaptor/web/server.py`, add the action branch to `_dispatch_post` (before the 404 fallthrough):

```python
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "action":
            self._post_action(h, parts[1], form)
            return
```

Add the `_post_action` method and the import usage:

```python
    def _post_action(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = f"/adapter/{urllib.parse.quote(key)}"
        action_type = form.get("action_type", "")
        action = next((a for a in spec.instant_actions if a.action_type == action_type), None)
        if action is None:
            self._audit(h, key, f"action:{action_type}", "rejected:unknown")
            h._redirect(f"{target}?err={urllib.parse.quote('unknown action')}")
            return
        if action.motion and not _confirmed(form):
            self._audit(h, key, f"action:{action_type}", "rejected:unconfirmed")
            h._redirect(f"{target}?err={urllib.parse.quote('confirm required')}")
            return
        from tui import control  # reuse VDA5050 payload builder
        payload = control.build_instant_actions(
            header_id=self._next_header_id(key),
            timestamp=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            version=spec.vda_full_version,
            manufacturer=spec.manufacturer,
            serial_number=spec.serial,
            action_type=action_type,
            action_id=str(uuid.uuid4()),
        )
        ok = self._monitors[key].publish_json("instantActions", payload)
        self._audit(h, key, f"action:{action_type}", f"published={ok}")
        flash = "msg" if ok else "err"
        text = "published" if ok else "publish failed"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(text)}")
```

> Verify the JSON keys (`headerId`, `actions[].actionType`, `actions[].actionId`) against `tui/control.py:build_instant_actions` output and align the test assertions to the real key names if they differ.

- [ ] **Step 4: Run test to verify it passes**

Run: `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`
Expected: PASS (publish payload, motion confirm gate, header id increments).

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_web_server.py
git commit -m "feat(web): instant-action POST (MQTT publish, motion confirm, audit)"
```

---

## Task 7: `python -m web` entry + edge-agent filter + launcher

**Files:**
- Create: `adaptor/web/__main__.py`
- Create: `adaptor/run-web.sh`
- Test: `adaptor/tests/test_web_main.py`

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_web_main.py
from dataclasses import dataclass
from web.main import visible_specs


@dataclass
class S:
    key: str


def test_filters_edge_agent():
    specs = [S("jibot"), S("hexplorer"), S("edge-agent"), S("edge-agent:cell-2"), S("jibot:robot-3")]
    keys = [s.key for s in visible_specs(specs)]
    assert keys == ["jibot", "hexplorer", "jibot:robot-3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH= uv run pytest tests/test_web_main.py -q`
Expected: FAIL — `No module named 'web.main'`.

- [ ] **Step 3: Write minimal implementation**

Put the filter (testable) in `adaptor/web/main.py`, and a thin `__main__.py` that bootstraps sys.path then calls it:

```python
# adaptor/web/main.py
"""WebUi 부트스트랩 — config/registry 로드, edge-agent 필터, 컨트롤러/모니터 와이어링, 기동."""
from __future__ import annotations

import os


def visible_specs(specs):
    """edge-agent 스펙 제외(자체 WebUi로 운영). 키가 'edge-agent' 또는 'edge-agent:'."""
    return [s for s in specs if not (s.key == "edge-agent" or s.key.startswith("edge-agent:"))]


def run() -> int:
    from config.config import get_config
    from tui import configio
    from tui.monitor import MqttMonitor
    from tui.registry import build_registry
    from tui.systemd import SystemdController
    from web.credentials import load_credentials
    from web.server import WebUi

    config = get_config()
    specs = visible_specs(build_registry(config))

    raw = configio.load_raw()
    web_cfg = raw.get("web_ui", {})
    if not web_cfg.get("enabled", False):
        print("web_ui.enabled is false in config.toml; not starting.")
        return 0
    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", 8090))
    cred = load_credentials(web_cfg["credentials_path"])

    broker_host = raw["mqtt_broker"]["host"]
    broker_port = int(raw["mqtt_broker"]["port"])

    controllers = {s.key: SystemdController(s.unit, use_sudo=False) for s in specs}
    monitors: dict = {}
    sessions: dict = {}
    for s in specs:
        if s.monitor_kind == "none":
            continue
        h = s.mqtt_host or broker_host
        p = s.mqtt_port or broker_port
        sess_key = (h, p, s.topic_prefix)
        if sess_key not in sessions:
            sessions[sess_key] = MqttMonitor(
                h, p, s.topic_prefix, client_id=f"amr-webui-{os.getpid()}-{s.serial}"
            )
            sessions[sess_key].start()
        monitors[s.key] = sessions[sess_key]

    web = WebUi(specs=specs, controllers=controllers, monitors=monitors,
                credentials=cred, host=host, port=port)
    web.start()
    print(f"web_ui on http://{host}:{web.port}/")
    try:
        web._thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        web.stop()
    return 0
```

```python
# adaptor/web/__main__.py
"""``python -m web`` entry point. flat 레이아웃: adaptor/를 sys.path에 넣고 run()."""
import sys
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from web.main import run

if __name__ == "__main__":
    raise SystemExit(run())
```

```bash
# adaptor/run-web.sh
#!/usr/bin/env bash
# Runs the AMR WebUi (python -m web) from the adapter directory.
# Prefers .venv (uv), then legacy venvJIBOT, then system python3.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venvJIBOT/bin/python" ]]; then
  PYTHON_BIN="venvJIBOT/bin/python"
fi

exec "$PYTHON_BIN" -m web "$@"
```

- [ ] **Step 4: Run test + make launcher executable**

Run: `env PYTHONPATH= uv run pytest tests/test_web_main.py -q`
Expected: PASS.
Run: `chmod +x run-web.sh`

- [ ] **Step 5: Commit**

```bash
git add web/main.py web/__main__.py run-web.sh tests/test_web_main.py
git commit -m "feat(web): python -m web entry, edge-agent filter, run-web.sh"
```

---

## Task 8: Deploy assets (systemd unit + polkit) + config example

**Files:**
- Create: `scripts/systemd/amr-webui.service`
- Create: `scripts/systemd/amr-webui-polkit.rules`
- Modify: `adaptor/config/config.toml` (add commented `[web_ui]` example)
- Modify: `scripts/setup-adaptor-service.sh` (install unit + polkit rule)

No TDD (config/ops files). Verify by review + `systemd-analyze verify` where available.

- [ ] **Step 1: systemd unit**

```ini
# scripts/systemd/amr-webui.service
[Unit]
Description=AMR Adaptor WebUi
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=amr-webui
WorkingDirectory=/opt/unified-amr-adaptor/adaptor
ExecStart=/opt/unified-amr-adaptor/adaptor/run-web.sh
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: polkit rule (least-privilege)**

```javascript
// scripts/systemd/amr-webui-polkit.rules
// Allow the amr-webui service user to manage ONLY the adapter units.
// Install to /etc/polkit-1/rules.d/10-amr-webui.rules and adjust unit names.
polkit.addRule(function(action, subject) {
    if (subject.user !== "amr-webui") { return polkit.Result.NOT_HANDLED; }
    var managed = [
        "jibot-adapter.service",
        "hexplorer-adapter.service",
    ];
    var unit = action.lookup("unit");
    // jibot-adapter@<id>.service instances
    var isManaged = managed.indexOf(unit) >= 0 ||
        (unit && unit.indexOf("jibot-adapter@") === 0 && unit.indexOf(".service") > 0);
    if (!isManaged) { return polkit.Result.NOT_HANDLED; }
    if (action.id === "org.freedesktop.systemd1.manage-units" ||
        action.id === "org.freedesktop.systemd1.manage-unit-files") {
        return polkit.Result.YES;
    }
    return polkit.Result.NOT_HANDLED;
});
```

- [ ] **Step 3: config example**

Add to `adaptor/config/config.toml` (commented, so existing behavior unchanged):

```toml
# [web_ui]
# enabled = true
# host = "127.0.0.1"          # LAN 노출 시에만 0.0.0.0/NIC IP
# port = 8090
# credentials_path = "web-credentials.toml"   # username/password (12자+); VCS 커밋 금지
```

- [ ] **Step 4: setup script install step**

In `scripts/setup-adaptor-service.sh`, add (near the existing unit install):

```bash
# AMR WebUi unit + polkit least-privilege rule
sudo install -m 644 scripts/systemd/amr-webui.service /etc/systemd/system/amr-webui.service
sudo install -m 644 scripts/systemd/amr-webui-polkit.rules /etc/polkit-1/rules.d/10-amr-webui.rules
sudo systemctl daemon-reload
echo "Edit unit names in /etc/polkit-1/rules.d/10-amr-webui.rules to match this host, then:"
echo "  sudo useradd --system --no-create-home amr-webui   # if not present"
echo "  sudo systemctl enable --now amr-webui"
```

- [ ] **Step 5: Verify + commit**

Run (best-effort): `systemd-analyze verify scripts/systemd/amr-webui.service` (warns about absolute paths if repo not at /opt — acceptable for a template).
Run full suite: `env PYTHONPATH= uv run pytest -q` — Expected: all web tests pass alongside existing 148.

```bash
git add scripts/systemd/amr-webui.service scripts/systemd/amr-webui-polkit.rules \
        scripts/setup-adaptor-service.sh adaptor/config/config.toml
git commit -m "feat(web): deploy assets (systemd unit + polkit rule) + [web_ui] config"
```

---

## Self-Review (author checklist — done)

- **Spec coverage:** Dashboard list+detail (T4), Control verbs (T5), instant actions (T6), auth/CSRF (T3), credentials (T1), polkit no-sudo + scripts/systemd (T8), `[web_ui]` config + run-web.sh + `-m web` bootstrap/sys.path (T7/T8), edge-agent filter WebUi-side (T7), audit log (T5/T6), render incl. monitor_kind=none omission (T2). All spec §2/§4/§6/§7 items mapped.
- **Deferred (spec §10, intentionally out of Phase 1):** Tests/Config/Logs views, curses TUI retirement, `tui/`→`core/` refactor.
- **Type consistency:** WebUi constructor (`specs`, `controllers`, `monitors`, `credentials`, `host`, `port`) identical across T3-T7. `_VERBS`/`_DESTRUCTIVE` defined once in server.py (T3) and reused. Backend signatures match code: `SystemdController(unit, use_sudo=...)`, `MqttMonitor(host, port, topic_prefix, client_id=...)`, `build_instant_actions(*, header_id, timestamp, version, manufacturer, serial_number, action_type, action_id, ...)`.
- **Verification flags for the implementer:** (a) confirm `build_instant_actions` JSON key names (T6 note); (b) confirm `SystemdController` exposes a `poll()` returning the `ServiceMetrics` fields render uses; (c) `config.toml` real keys for `[mqtt_broker]` host/port already used by the TUI (`configio.load_raw()["mqtt_broker"]`).
