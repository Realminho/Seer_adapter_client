# AMR WebUi Phase 4 — Logs view + retire curses TUI

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** Add the Logs view (snapshot journal tail — no persistent process), then move the shared backend `tui/` → `core/` and delete the curses TUI. End state: the WebUi is the single operator interface.

**Architecture:** Logs uses a per-request `journalctl -n N --no-pager` snapshot (zero idle cost), reusing `SystemdController.journal_argv(lines, follow=False)`. The refactor renames the 8 curses-free backend modules from `tui/` to `core/` and removes the curses presentation (`app.py`, `widgets.py`, `__main__.py`) + its launcher/tests/docs.

**Tech Stack:** Python 3.12 stdlib + pytest. Run from `adaptor/` with `env PYTHONPATH= uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-06-15-amr-webui-design.md` §10 Phase 4.

**Verified facts:**
- `tui/systemd.py` `SystemdController.journal_argv(self, lines: int = 300, follow: bool = True) -> List[str]` → journalctl argv; with `follow=False` it omits `-f` (snapshot that exits).
- Backend modules (curses-free, MOVE to core/): `registry, monitor, systemd, control, configio, runner, processes, status` (+ `__init__`).
- Curses-bound (DELETE): `tui/app.py`, `tui/widgets.py`, `tui/__main__.py`.
- Path computations in `configio.py` (`CONFIG_PATH = parents[1]/"config"/"config.toml"`) and `registry.py` (`ADAPTER_ROOT = parents[1]`) resolve relative to the module's parent-of-parent = `adaptor/`. `core/` sits at the same depth as `tui/` (both `adaptor/<pkg>/`), so these stay correct after the move.
- Importers of `tui.*` outside the package: `web/main.py`, `web/server.py`, and tests `test_tui_{configio,control,processes,registry,runner,status,systemd,monitor}.py`, `test_fleet_registry.py`, `test_web_server.py`. Intra-package: `registry.py` (`from tui.systemd import ...`), `status.py`. `tui-live-adaptor-discovery` branch is merged/stale (no conflict).

---

## Task 1: Logs view (snapshot)

**Files:** Modify `adaptor/web/render.py`, `adaptor/web/server.py`; Test `adaptor/tests/test_web_render.py`, `adaptor/tests/test_web_server.py`.

- [ ] **Step 1: failing render test** (append to `tests/test_web_render.py`)

```python
def test_logs_page_renders_lines():
    spec = _Spec("jibot", "JIBOT")
    out = render.logs_page(spec, ["line one", "<b>oops</b>"], {})
    assert "line one" in out
    assert "&lt;b&gt;oops&lt;/b&gt;" in out   # log lines escaped
    assert "/adapter/jibot/logs" in out        # refresh link
```

- [ ] **Step 2: run, verify FAIL** — `env PYTHONPATH= uv run pytest tests/test_web_render.py -q`

- [ ] **Step 3: implement** — add to `web/render.py`:

```python
def logs_page(spec, lines, q: dict) -> str:
    try:
        r = int(q.get("refresh", "5"))
    except (TypeError, ValueError):
        r = 5
    text = "\n".join(esc(line) for line in lines)
    body = (
        f"{_flash(q)}<h3>{esc(spec.display_name)} — logs</h3>"
        f"<pre>{text}</pre>"
        f'<p><a href="/adapter/{esc(spec.key)}/logs?refresh={esc(r)}">refresh</a> (auto {esc(r)}s; 0=off) | '
        f'<a href="/adapter/{esc(spec.key)}">&larr; dashboard</a></p>'
    )
    return page(f"{spec.display_name} logs", body, refresh=(r if r > 0 else None))
```

Add a `logs` link in `adapter_detail_page` (next to the tests link):
`... <a href="/adapter/{esc(spec.key)}/tests">tests</a> | <a href="/adapter/{esc(spec.key)}/logs">logs</a> | ...`

- [ ] **Step 4: run, verify PASS** — `env PYTHONPATH= uv run pytest tests/test_web_render.py -q`

- [ ] **Step 5: failing server test** (append to `tests/test_web_server.py`)

```python
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
```

- [ ] **Step 6: run, verify FAIL**, then **implement** in `web/server.py`:

(a) Add `import subprocess` at top (if not present).

(b) `WebUi.__init__`: add param `log_reader=None` (after `validate_config`). Body:
```python
        self._log_reader = log_reader if log_reader is not None else self._default_log_reader
```

(c) Add the default reader method:
```python
    def _default_log_reader(self, key: str):
        ctrl = self._controllers.get(key)
        if ctrl is None:
            return []
        try:
            out = subprocess.run(
                ctrl.journal_argv(lines=200, follow=False),
                capture_output=True, text=True, timeout=5,
            )
            return (out.stdout or "").splitlines()
        except Exception as exc:  # noqa: BLE001
            return [f"[log read failed] {exc}"]
```

(d) In `_dispatch_get`, add a branch (before the final 404), alongside the tests branch:
```python
        if len(parts) == 3 and parts[0] == "adapter" and parts[2] == "logs":
            spec = self._specs.get(parts[1])
            if spec is None:
                h._html(404, render.page("not found", "<p>unknown adapter</p>"))
                return
            h._html(200, render.logs_page(spec, self._log_reader(spec.key), q))
            return
```

- [ ] **Step 7: run, verify PASS** — `env PYTHONPATH= uv run pytest tests/test_web_server.py tests/test_web_render.py -q`

- [ ] **Step 8: commit**
```bash
git add web/render.py web/server.py tests/test_web_render.py tests/test_web_server.py
git commit -m "feat(web): Logs view — snapshot journal tail (Phase 4)"
```

---

## Task 2: rename backend tui/ → core/ and delete the curses TUI

This is an atomic refactor — the repo is only consistent after the whole task. The gate is the full suite staying green. Do all steps, then run `env PYTHONPATH= uv run pytest -q`.

**Files:** git-mv 8 modules; delete 3 curses modules + launcher + 2 tui-only tests; rewrite imports in web/ + tests + intra-core; update docs.

- [ ] **Step 1: move the 8 backend modules to a new `core/` package**
```bash
cd /home/lab2m-llm1/workspaces/unified-amr-adaptor/.worktrees/<WORKTREE>/adaptor
git mv tui/registry.py tui/monitor.py tui/systemd.py tui/control.py \
       tui/configio.py tui/runner.py tui/processes.py tui/status.py core/ 2>/dev/null || {
  mkdir -p core
  for m in registry monitor systemd control configio runner processes status; do git mv tui/$m.py core/$m.py; done
}
git mv tui/__init__.py core/__init__.py
```
(After this, `core/` holds the 8 modules + `__init__.py`; `tui/` holds only `app.py`, `widgets.py`, `__main__.py`.)

- [ ] **Step 2: delete the curses TUI + launcher**
```bash
git rm tui/app.py tui/widgets.py tui/__main__.py
git rm ../scripts/adaptor-tui.sh
rmdir tui 2>/dev/null || true
```

- [ ] **Step 3: delete the curses-only tests**
```bash
git rm tests/test_tui_app.py tests/test_tui_widgets.py
```

- [ ] **Step 4: rewrite intra-core imports**
In `core/registry.py`: `from tui.systemd import` → `from core.systemd import`.
In `core/status.py`: any `from tui.` / `import tui.` → `from core.` / `import core.`.
Grep to be sure: `grep -rn "tui" core/` should show NO remaining `tui` references (except possibly in comments/docstrings — fix those too).

- [ ] **Step 5: rewrite importers in web/**
In `web/main.py` and `web/server.py`: replace every `from tui.X import` / `from tui import X` with the `core` equivalent (`from core.runner import StreamProcess`, `from core import configio`, `from core.registry import build_registry`, `from core.monitor import MqttMonitor`, `from core.systemd import SystemdController`, `from core import control`, `from core import configio`, etc.). Grep: `grep -rn "tui" web/` → none remaining.

- [ ] **Step 6: rename + rewrite the backend tests**
Rename and update imports (`from tui.X` → `from core.X`):
```bash
git mv tests/test_tui_configio.py tests/test_configio.py
git mv tests/test_tui_control.py tests/test_control.py
git mv tests/test_tui_processes.py tests/test_processes.py
git mv tests/test_tui_registry.py tests/test_registry.py
git mv tests/test_tui_runner.py tests/test_runner.py
git mv tests/test_tui_status.py tests/test_status.py
git mv tests/test_tui_systemd.py tests/test_systemd.py
git mv tests/test_tui_monitor.py tests/test_monitor.py
```
Then in each renamed test file, replace `from tui.` / `import tui` with `core` equivalents. Also fix `test_fleet_registry.py` and `test_web_server.py` if they import `tui.*`. Grep: `grep -rln "tui" tests/` → only matches should be in strings unrelated to the package (verify each; none should be a `from tui`/`import tui`).

- [ ] **Step 7: update docs**
- `docs/guide/adaptor-tui.md`: prepend a SUPERSEDED banner pointing to `web-ui.md` (the curses TUI was removed; the WebUi replaces it). Do not delete the file (keep as historical record), OR `git rm` it if preferred — prepend-banner is safer.
- `README.md` / `docs/guide/README.md` / `adaptor/readme.md`: remove or update any `python -m tui` / `adaptor-tui.sh` references to point at the WebUi (`python -m web` / `run-web.sh`). Grep the repo: `grep -rn "python -m tui\|adaptor-tui" --include=*.md .` and fix each.

- [ ] **Step 8: verify the whole suite is green**
```bash
env PYTHONPATH= uv run pytest -q
```
Expected: all pass (the web + backend tests), with the 2 deleted curses tests gone. Confirm no import errors. Also: `grep -rn "from tui\|import tui\| -m tui" --include=*.py --include=*.sh --include=*.md . | grep -v core` → no functional `tui` references remain.

- [ ] **Step 9: commit**
```bash
git add -A
git commit -m "refactor(web): move backend tui/ -> core/, remove curses TUI (Phase 4)

The 8 curses-free backend modules (registry/monitor/systemd/control/
configio/runner/processes/status) move to core/; the WebUi now fully
replaces the curses TUI, which is deleted along with its launcher,
curses-only tests, and -m tui entry. Backend tests renamed/repointed."
```

---

## Self-Review (author checklist — done)

- **Spec coverage (§10 Phase 4):** Logs view (snapshot, no persistent process — resource-safe per the chosen design) = Task 1; `tui/`→`core/` refactor + curses TUI retirement = Task 2. After this, all 5 TUI views exist in the WebUi and the curses TUI is gone.
- **Resource note:** Logs deliberately uses per-request `journalctl -n 200 --no-pager` (exits immediately) — zero idle cost, bounded per-view output. NOT a persistent `-f` follow.
- **Path safety:** `core/` is at the same directory depth as `tui/`, so `parents[1]`-based `CONFIG_PATH`/`ADAPTER_ROOT` stay correct.
- **No behavior change in Task 2:** pure move + delete + import rewrite; the full suite is the regression gate.
- **edge-agent integration:** `discover_edge_agent_units`/`make_edge_agent_spec` move to `core/` with registry/systemd and stay inert (WebUi filters edge-agent); no behavior change.
- **Testability:** Logs route uses an injectable `log_reader` (default = journalctl snapshot) so tests stub it without invoking journalctl.
