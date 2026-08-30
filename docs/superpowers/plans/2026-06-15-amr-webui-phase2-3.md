# AMR WebUi Phase 2 (Tests) + Phase 3 (Config) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add the Tests view (run diagnostics, stream output) and the Config view (edit config.toml hot fields) to the AMR WebUi — porting two more of the curses TUI's five views.

**Architecture:** Extend `adaptor/web/server.py` (new routes) + `adaptor/web/render.py` (new pages), reusing `tui/runner.py` (`StreamProcess`) and `tui/configio.py` (comment-preserving TOML edit + validation) unchanged. Same patterns as Phase 1: stdlib http.server, Basic auth + CSRF (already enforced in `do_GET`/`do_POST`), server-rendered HTML, PRG redirects, audit logging.

**Tech Stack:** Python 3.12 stdlib + pytest. Run from `adaptor/` with `env PYTHONPATH= uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-06-15-amr-webui-design.md` §10 (Phase 2/3). Builds on Phase 1 (merged).

**Verified backend APIs (do not modify these modules):**
- `tui/runner.py`: `StreamProcess()` → `.start(argv, cwd=None, label="")`, `.stop()`, `.lines() -> List[str]`, `.running: bool`, `.returncode: int|None`, `.label: str`.
- `tui/registry.py`: `AdaptorSpec.runnable -> Tuple[Diagnostic, ...]` (= test_suites + diagnostics); `Diagnostic.label`, `Diagnostic.argv` (tuple; the literal token `"{py}"` must be substituted with the python executable), `Diagnostic.note`; `AdaptorSpec.workdir: Path`.
- `tui/configio.py`: `CONFIG_PATH`; `HOT_FIELDS: List[(section, key, kind, label)]` (kinds: str/int/num/bool); `load_raw(path=CONFIG_PATH) -> dict`; `read_text(path=CONFIG_PATH) -> str`; `current_value(raw, section, key)`; `coerce(raw_str, kind)`; `toml_literal(value, kind) -> str`; `rewrite_scalar(text, section, key, literal) -> str` (raises KeyError if not found); `validate_on_disk() -> (ok, msg)` (re-runs the adaptor's `get_config()` against the on-disk `CONFIG_PATH`).

**Conventions:** All commands from `adaptor/`. Tests import `from web import server, render`. The `WebUi` test fixture (`ui`) and helpers (`_get`, `_post`, fakes `FakeSpec`/`FakeController`/`FakeMonitor`) already exist in `tests/test_web_server.py`.

---

## File Structure

**Modify:**
- `adaptor/web/server.py` — `WebUi.__init__` gains `py`, `config_path`, `validate_config`; new routes for tests + config; `_runners` dict.
- `adaptor/web/render.py` — `tests_page`, `config_page`; add Config + Tests links to nav/pages.
- `adaptor/web/main.py` — pass `py=sys.executable`, `config_path=configio.CONFIG_PATH`, `validate_config=configio.validate_on_disk` to `WebUi`.
- `adaptor/tests/test_web_server.py`, `adaptor/tests/test_web_render.py` — new tests.

Responsibilities unchanged: `render.py` = HTML only; `server.py` = routing + backend orchestration; `main.py` = wiring.

---

## Task 1: render — tests_page

**Files:** Modify `adaptor/web/render.py`; Test `adaptor/tests/test_web_render.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_web_render.py`)

```python
class _Runner:
    def __init__(self, lines, running=False, returncode=0, label="pytest"):
        self._lines = lines
        self.running = running
        self.returncode = returncode
        self.label = label
    def lines(self):
        return self._lines


def _spec_with_diags():
    s = _Spec("jibot", "JIBOT")
    s.runnable = (
        type("D", (), {"label": "unit tests", "argv": ("{py}", "-m", "pytest"), "note": ""})(),
    )
    return s


def test_tests_page_lists_runnables_and_run_form():
    spec = _spec_with_diags()
    out = render.tests_page(spec, None, "tok", {})
    assert "unit tests" in out
    assert 'action="/adapter/jibot/tests/run"' in out
    assert 'name="index"' in out
    assert 'name="csrf_token"' in out


def test_tests_page_shows_output_and_stop_while_running():
    spec = _spec_with_diags()
    runner = _Runner(["$ pytest", "collected 1 item", "PASSED"], running=True, label="unit tests")
    out = render.tests_page(spec, runner, "tok", {})
    assert "PASSED" in out
    assert "running" in out
    assert 'action="/adapter/jibot/tests/stop"' in out


def test_tests_page_shows_exit_code_when_done():
    spec = _spec_with_diags()
    runner = _Runner(["[exit 0]"], running=False, returncode=0, label="unit tests")
    out = render.tests_page(spec, runner, "tok", {})
    assert "exit 0" in out
    assert "/tests/stop" not in out  # no stop button when not running
```

> `_Spec` lacks a `runnable` field; `_spec_with_diags` sets it dynamically. If `_Spec` is a frozen dataclass, add `runnable: tuple = ()` to its definition instead.

- [ ] **Step 2: Run, verify FAIL** — `env PYTHONPATH= uv run pytest tests/test_web_render.py -q` → AttributeError (no `tests_page`).

- [ ] **Step 3: Implement** — add to `adaptor/web/render.py`:

```python
def tests_page(spec, runner, csrf: str, q: dict) -> str:
    rows = []
    for i, d in enumerate(spec.runnable):
        rows.append(
            f'<form class="btn" method="post" action="/adapter/{esc(spec.key)}/tests/run">'
            f'{_csrf_field(csrf)}<input type="hidden" name="index" value="{esc(i)}">'
            f"<button>{esc(d.label)}</button></form>"
        )
    listing = "".join(rows) or "(none)"
    output = ""
    refresh = None
    if runner is not None and (runner.label or runner.lines()):
        if runner.running:
            status = "running"
            refresh = 2
            stop = (
                f'<form class="btn" method="post" action="/adapter/{esc(spec.key)}/tests/stop">'
                f"{_csrf_field(csrf)}<button>stop</button></form>"
            )
        else:
            status = f"exit {esc(runner.returncode)}"
            stop = ""
        text = "\n".join(esc(line) for line in runner.lines())
        output = f"<h4>{esc(runner.label)} [{status}]</h4>{stop}<pre>{text}</pre>"
    body = (
        f"{_flash(q)}<h3>{esc(spec.display_name)} — tests</h3>{listing}{output}"
        f'<p><a href="/adapter/{esc(spec.key)}">&larr; dashboard</a></p>'
    )
    return page(f"{spec.display_name} tests", body, refresh=refresh)
```

Also add a Tests link to the control page nav: in `control_page`, change the trailing `<p>` to also link tests:
```python
        f'<p><a href="/adapter/{esc(spec.key)}">&larr; dashboard</a> | '
        f'<a href="/adapter/{esc(spec.key)}/tests">tests</a></p>'
```
And in `adapter_detail_page`, add a tests link next to the control link:
```python
        f'<p><a href="/adapter/{esc(spec.key)}/control">control</a> | '
        f'<a href="/adapter/{esc(spec.key)}/tests">tests</a> | '
        f'<a href="/adapter/{esc(spec.key)}?refresh={esc(r)}">refresh</a> (auto {esc(r)}s; 0=off)</p>'
```

- [ ] **Step 4: Run, verify PASS** — `env PYTHONPATH= uv run pytest tests/test_web_render.py -q`.

- [ ] **Step 5: Commit**
```bash
git add web/render.py tests/test_web_render.py
git commit -m "feat(web): render tests_page (Phase 2)"
```

---

## Task 2: server — Tests routes (run / stop / view)

**Files:** Modify `adaptor/web/server.py`; Test `adaptor/tests/test_web_server.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_web_server.py`)

```python
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


def test_tests_page_renders():
    web = _ui_with_diag()
    try:
        body = _get(web, "/adapter/jibot/tests").read().decode()
        assert "unit tests" in body
    finally:
        web.stop()


def test_tests_run_starts_runner():
    web = _ui_with_diag()
    try:
        resp = _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "0"})
        assert resp.status == 200
        assert "jibot" in web._runners  # a StreamProcess was created for this adapter
    finally:
        web.stop()


def test_tests_run_rejects_out_of_range_index():
    web = _ui_with_diag()
    try:
        _post(web, "/adapter/jibot/tests/run", {"csrf_token": web._csrf, "index": "9"})
        assert "jibot" not in web._runners  # no runner started for a bad index
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
```

- [ ] **Step 2: Run, verify FAIL** — tests routes 404 / `_runners` missing.

- [ ] **Step 3: Implement** in `adaptor/web/server.py`:

(a) Add imports at top: `import sys` and `from tui.runner import StreamProcess`.

(b) In `WebUi.__init__` signature add `py: str | None = None` (after `port`), and in the body add:
```python
        self._py = py or sys.executable
        self._runners: dict = {}
```

(c) In `_dispatch_get`, add a branch for tests (before the final 404), alongside the existing `/adapter/<key>/control` branch:
```python
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "tests":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            h._html(200, render.tests_page(spec, self._runners.get(spec.key), self._csrf, q))
            return
```

(d) In `_dispatch_post`, add branches (before the final 404):
```python
        if len(parts) == 4 and parts[0] == "adapter" and parts[2] == "tests" and parts[3] == "run":
            self._post_tests_run(h, parts[1], form)
            return
        if len(parts) == 4 and parts[0] == "adapter" and parts[2] == "tests" and parts[3] == "stop":
            self._post_tests_stop(h, parts[1], form)
            return
```

(e) Add the two methods to `WebUi`:
```python
    def _post_tests_run(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = f"/adapter/{urllib.parse.quote(key, safe=':')}/tests"
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
        h._redirect(f"/adapter/{urllib.parse.quote(key, safe=':')}/tests?msg={urllib.parse.quote('stopped')}")
```

- [ ] **Step 4: Run, verify PASS** — `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`.

- [ ] **Step 5: Commit**
```bash
git add web/server.py tests/test_web_server.py
git commit -m "feat(web): tests routes — run (index whitelist, {py} subst), stop, view (Phase 2)"
```

---

## Task 3: render — config_page

**Files:** Modify `adaptor/web/render.py`; Test `adaptor/tests/test_web_render.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_web_render.py`)

```python
def test_config_page_renders_fields():
    fields = [
        ("mqtt_broker", "host", "str", "MQTT broker host", "127.0.0.1"),
        ("mqtt_broker", "port", "int", "MQTT broker port", 1883),
    ]
    out = render.config_page(fields, "tok", {})
    assert 'name="csrf_token"' in out
    assert 'name="mqtt_broker.host"' in out
    assert "127.0.0.1" in out
    assert "MQTT broker host" in out
    assert 'action="/config"' in out
```

- [ ] **Step 2: Run, verify FAIL** — no `config_page`.

- [ ] **Step 3: Implement** — add to `adaptor/web/render.py`:

```python
def config_page(fields, csrf: str, q: dict) -> str:
    # fields: iterable of (section, key, kind, label, current_value)
    rows = []
    for section, key, _kind, label, value in fields:
        name = f"{section}.{key}"
        rows.append(
            f"<p><label>{esc(label)} ({esc(name)})<br>"
            f'<input name="{esc(name)}" value="{esc(value)}"></label></p>'
        )
    body = (
        f"{_flash(q)}<h3>config.toml (hot fields)</h3>"
        f'<form method="post" action="/config">{_csrf_field(csrf)}'
        f"{''.join(rows)}<button>save</button></form>"
        "<p>저장 후 적용하려면 어댑터 서비스 재시작이 필요합니다.</p>"
    )
    return page("config", body)
```

Add a Config link to the nav so it's reachable from every page. In `_nav`:
```python
def _nav() -> str:
    return '<a href="/">Adapters</a> <a href="/config">Config</a>'
```

- [ ] **Step 4: Run, verify PASS** — `env PYTHONPATH= uv run pytest tests/test_web_render.py -q`.

- [ ] **Step 5: Commit**
```bash
git add web/render.py tests/test_web_render.py
git commit -m "feat(web): render config_page + nav link (Phase 3)"
```

---

## Task 4: server — Config routes (view / save)

**Files:** Modify `adaptor/web/server.py`; Test `adaptor/tests/test_web_server.py`.

Config edits operate on a config file path + a validation callback, both injected so tests use a temp file and a stub validator (the real `configio.validate_on_disk` reads the fixed `CONFIG_PATH`).

- [ ] **Step 1: Write the failing test** (append to `tests/test_web_server.py`)

```python
import pathlib


_SAMPLE_TOML = """[mqtt_broker]
host = "127.0.0.1"   # broker
port = 1883
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


def test_config_page_get(tmp_path):
    web, _ = _ui_with_config(tmp_path)
    try:
        body = _get(web, "/config").read().decode()
        assert "mqtt_broker.host" in body
        assert "127.0.0.1" in body
    finally:
        web.stop()


def test_config_save_rewrites_field(tmp_path):
    web, cfg = _ui_with_config(tmp_path)
    try:
        _post(web, "/config", {"csrf_token": web._csrf, "mqtt_broker.host": "10.0.0.5", "mqtt_broker.port": "1883"})
        text = cfg.read_text()
        assert 'host = "10.0.0.5"' in text
        assert "# broker" in text  # comment preserved
    finally:
        web.stop()


def test_config_save_restores_on_invalid(tmp_path):
    web, cfg = _ui_with_config(tmp_path, validate=lambda: (False, "bad config"))
    try:
        _post(web, "/config", {"csrf_token": web._csrf, "mqtt_broker.host": "10.0.0.9"})
        text = cfg.read_text()
        assert 'host = "127.0.0.1"' in text  # restored to original on invalid
        assert "10.0.0.9" not in text
    finally:
        web.stop()
```

- [ ] **Step 2: Run, verify FAIL** — `/config` 404; constructor lacks `config_path`/`validate_config`.

- [ ] **Step 3: Implement** in `adaptor/web/server.py`:

(a) Add import: `from tui import configio`.

(b) `WebUi.__init__` signature: add `config_path=None, validate_config=None` (after `py`). Body:
```python
        self._config_path = config_path if config_path is not None else configio.CONFIG_PATH
        self._validate_config = validate_config if validate_config is not None else configio.validate_on_disk
```

(c) `_dispatch_get` — add (before final 404):
```python
        if path == "/config":
            raw = configio.load_raw(self._config_path)
            fields = [
                (section, key, kind, label, configio.current_value(raw, section, key))
                for section, key, kind, label in configio.HOT_FIELDS
            ]
            h._html(200, render.config_page(fields, self._csrf, q))
            return
```

(d) `_dispatch_post` — add (before final 404):
```python
        if path == "/config":
            self._post_config(h, form)
            return
```

(e) Add method:
```python
    def _post_config(self, h, form: dict):
        orig = configio.read_text(self._config_path)
        text = orig
        changed = []
        for section, key, kind, _label in configio.HOT_FIELDS:
            name = f"{section}.{key}"
            if name not in form:
                continue
            try:
                literal = configio.toml_literal(configio.coerce(form[name], kind), kind)
                text = configio.rewrite_scalar(text, section, key, literal)
            except (ValueError, KeyError) as exc:
                self._audit(h, "config", name, f"rejected:{exc}")
                h._redirect(f"/config?err={urllib.parse.quote(f'{name}: {exc}')}")
                return
            changed.append(name)
        if changed:
            self._config_path.write_text(text, encoding="utf-8")
            ok, msg = self._validate_config()
            if not ok:
                self._config_path.write_text(orig, encoding="utf-8")  # restore
                self._audit(h, "config", ",".join(changed), f"rejected:invalid {msg}")
                h._redirect(f"/config?err={urllib.parse.quote(f'invalid: {msg}')}")
                return
        self._audit(h, "config", ",".join(changed) or "(none)", "saved")
        h._redirect(f"/config?msg={urllib.parse.quote('saved — restart adapters to apply')}")
```

- [ ] **Step 4: Run, verify PASS** — `env PYTHONPATH= uv run pytest tests/test_web_server.py -q`.

- [ ] **Step 5: Commit**
```bash
git add web/server.py tests/test_web_server.py
git commit -m "feat(web): config routes — view + comment-preserving save with validate/restore (Phase 3)"
```

---

## Task 5: wire py / config_path / validate into main.py

**Files:** Modify `adaptor/web/main.py`. (No new test — covered by `test_web_main.py` filter test + manual parse check; the wiring is verified by the server tests via injection.)

- [ ] **Step 1: Implement** — in `web/main.py` `run()`, add `import sys` at top and pass the real collaborators when constructing `WebUi`:
```python
    import sys
    ...
    web = WebUi(specs=specs, controllers=controllers, monitors=monitors,
                credentials=cred, host=host, port=port,
                py=sys.executable, config_path=configio.CONFIG_PATH,
                validate_config=configio.validate_on_disk)
```
(`configio` is already imported in `run()`.)

- [ ] **Step 2: Verify** — `env PYTHONPATH= uv run python -c "import ast; ast.parse(open('web/main.py').read()); print('ok')"` and run the full suite `env PYTHONPATH= uv run pytest -q`.

- [ ] **Step 3: Commit**
```bash
git add web/main.py
git commit -m "feat(web): wire py/config_path/validate into -m web entry (Phase 2/3)"
```

---

## Self-Review (author checklist — done)

- **Spec coverage (§10):** Phase 2 Tests view = run diagnostics via `StreamProcess` with polling refresh (T1 render, T2 routes); Phase 3 Config view = configio hot-field edit + validate + restart notice (T3 render, T4 routes); wiring T5. Deferred (Phase 4): Logs + `tui/`→`core/` + TUI retirement.
- **Security:** Tests run is index-whitelisted into `spec.runnable` (never arbitrary argv from the form); `{py}` substitution only replaces the exact token. Config save only touches `HOT_FIELDS` keys, comment-preserving, validates and restores-on-invalid. Auth+CSRF already enforced upstream for all routes. Audit logging on run/stop/config.
- **Type consistency:** `WebUi(specs, controllers, monitors, credentials, host, port, py, config_path, validate_config)` extended consistently; tests inject `py`, `config_path`, `validate_config`. `tests_page(spec, runner, csrf, q)`, `config_page(fields, csrf, q)` signatures match between render and server callers. Backend calls match verified APIs (`StreamProcess.start(argv, cwd, label)`, `configio.rewrite_scalar(text, section, key, literal)`, `configio.coerce/toml_literal(.., kind)`).
- **No new tui/ modifications:** only imports of `tui.runner.StreamProcess` and `tui.configio`.
