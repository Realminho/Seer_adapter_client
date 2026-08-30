# Action Module Deploy Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every supported deploy and service path cleanly install and reload action-module code and WebUI panels without breaking hosts that omit optional services.

**Architecture:** Keep the existing SSH scripts and service helper. JIBOT retains its legacy-aware inline adapter discovery and adds optional WebUI restart; Hexplorer uploads and reuses the existing service helper, forwarding restart sudo prompts through a local TTY when available. Tests exercise local text contracts, shell dry-runs, and generated units without contacting robots.

**Tech Stack:** Bash, systemd CLI contracts, Python 3/pytest, tar.

---

## File map

- `scripts/adaptor-services.sh`: canonical local restart/start/stop/status helper; optional WebUI/camera detection.
- `scripts/update-jibot-adapter-over-ssh.sh`: JIBOT bundle cleanup and legacy-aware adapter/WebUI restart.
- `scripts/update-hexplorer-adapter-over-ssh.sh`: Hexplorer CLI parsing, helper upload, cleanup, and restart.
- `adaptor/install-systemd-service.sh`: compatibility installer using the vendor dispatcher.
- `scripts/setup-adaptor-service.sh`, `scripts/systemd/amr-adaptor.service`: active operator wording.
- `tests/test_update_jibot_adapter_over_ssh.py`: deploy/setup static and dry-run contracts.
- `tests/test_update_hexplorer_adapter_over_ssh.py`: focused Hexplorer deploy contracts.
- `scripts/test-install-systemd-service.sh`, `scripts/test-run-adapter-dispatch.sh`: executable shell smoke tests.
- `README.md`, `adaptor/readme.md`, `docs/manual/jibot-adapter-ssh-update.md`: canonical operator commands.

### Task 1: Optional services and active setup wording

**Files:**
- Modify: `tests/test_update_jibot_adapter_over_ssh.py`
- Modify: `scripts/adaptor-services.sh:95-112`
- Modify: `scripts/setup-adaptor-service.sh:1-10,427-452,942-954`
- Modify: `scripts/systemd/amr-adaptor.service:1-8`

- [ ] **Step 1: Write failing contract tests**

Add to `tests/test_update_jibot_adapter_over_ssh.py`:

```python
SERVICES_SCRIPT = REPO_ROOT / "scripts" / "adaptor-services.sh"
ADAPTOR_UNIT = REPO_ROOT / "scripts" / "systemd" / "amr-adaptor.service"


def test_service_helper_treats_webui_and_camera_as_optional():
    text = SERVICES_SCRIPT.read_text()
    assert 'if optional_unit_available "amr-webui.service"; then' in text
    assert 'if optional_unit_available "amr-camera.service"; then' in text


def test_active_setup_text_does_not_point_to_removed_tui():
    setup = SETUP_SCRIPT.read_text()
    unit = ADAPTOR_UNIT.read_text()
    assert "Launch the TUI:" not in setup
    assert "Inside the TUI" not in setup
    assert "scripts/adaptor-tui.sh" not in unit
    assert "AMR service control" in setup
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=. python -m pytest \
  tests/test_update_jibot_adapter_over_ssh.py::test_service_helper_treats_webui_and_camera_as_optional \
  tests/test_update_jibot_adapter_over_ssh.py::test_active_setup_text_does_not_point_to_removed_tui -q
```

Expected: both tests fail because WebUI is unconditional and active TUI text remains.

- [ ] **Step 3: Make optional-unit and wording changes**

In `scripts/adaptor-services.sh`, replace the unconditional WebUI append with:

```bash
  if optional_unit_available "amr-webui.service"; then
    units+=("amr-webui.service")
  fi
  if optional_unit_available "amr-camera.service"; then
    units+=("amr-camera.service")
  fi
```

Keep `optional_unit_available()`'s existing dry-run success behavior, so dry-run still prints all three units.

In `scripts/setup-adaptor-service.sh`, update the file header to describe the
WebUI and service helpers instead of the removed TUI. Keep
`/etc/sudoers.d/adaptor-tui` and `ADAPTOR_TUI_CTL` names, but change their
comments to current service management wording:

```bash
  local content="# Managed by scripts/setup-adaptor-service.sh — lets the deploy user control
# exactly these AMR services without a password. Remove this file to revoke.
```

Replace the final removed-TUI lines with:

```text
AMR service control: scripts/adaptor-services.sh start|restart|stop|status
(The scoped /etc/sudoers.d/adaptor-tui grant keeps these service operations
 non-interactive; its legacy filename is retained for compatibility.)
```

In `scripts/systemd/amr-adaptor.service`, replace the TUI comment with:

```text
# run-adapter.sh dispatches to the right launcher. Manage with the WebUI,
# scripts/adaptor-services.sh, or plain systemctl.
```

- [ ] **Step 4: Verify GREEN and dry-run behavior**

Run:

```bash
PYTHONPATH=. python -m pytest tests/test_update_jibot_adapter_over_ssh.py -q
scripts/adaptor-services.sh restart --dry-run --no-sudo
bash scripts/setup-adaptor-service.sh --jibot --dry-run --no-venv --no-camera --no-start
```

Expected: pytest passes; both dry-runs show adapter and WebUI, and no active `scripts/adaptor-tui.sh` instruction appears.

- [ ] **Step 5: Commit**

```bash
git add tests/test_update_jibot_adapter_over_ssh.py scripts/adaptor-services.sh scripts/setup-adaptor-service.sh scripts/systemd/amr-adaptor.service
git commit -m "fix(deploy): treat auxiliary services as optional"
```

### Task 2: JIBOT clean and WebUI restart

**Files:**
- Modify: `tests/test_update_jibot_adapter_over_ssh.py`
- Modify: `scripts/update-jibot-adapter-over-ssh.sh:466-490,655-701`

- [ ] **Step 1: Add failing JIBOT deploy assertions**

Add:

```python
def test_jibot_clean_removes_module_and_web_source_trees_before_extract():
    text = SCRIPT.read_text()
    clean = text[text.index('echo "[remote] removing managed adapter files"'):text.index('echo "[remote] extracting upload bundle"')]
    for managed in ("core", "extensions", "web"):
        assert f"    {managed} \\" in clean


def test_jibot_restart_adds_installed_webui_without_dropping_legacy_units():
    text = SCRIPT.read_text()
    assert "webui_unit=" in text
    assert "amr-webui.service" in text
    assert '[[ -n "$webui_unit" ]]' in text
    assert "'jibot-adapter*.service'" in text
    assert "'amr-adapter*.service'" in text
    assert 'units="$(printf' in text
```

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH=. python -m pytest \
  tests/test_update_jibot_adapter_over_ssh.py::test_jibot_clean_removes_module_and_web_source_trees_before_extract \
  tests/test_update_jibot_adapter_over_ssh.py::test_jibot_restart_adds_installed_webui_without_dropping_legacy_units -q
```

Expected: both fail because the clean list and restart discovery omit the new paths/WebUI.

- [ ] **Step 3: Extend clean and restart minimally**

Add these entries to the existing remote `rm -rf` list before `utils`:

```bash
    core \
    extensions \
    web \
```

Keep the adapter-only empty check unchanged. Immediately after that check and before `rc=0`, add optional WebUI discovery:

```bash
webui_unit="$(
  {
    systemctl list-units --no-legend --plain --all amr-webui.service 2>/dev/null || true
    systemctl list-unit-files --no-legend amr-webui.service 2>/dev/null || true
  } | awk '{print $1}' | grep -x 'amr-webui.service' | head -n 1 || true
)"
if [[ -n "$webui_unit" ]]; then
  units="$(printf '%s\n%s\n' "$units" "$webui_unit" | sort -u)"
fi
```

This preserves the existing legacy adapter discovery and sudo fallback loop.

- [ ] **Step 4: Verify GREEN and syntax**

```bash
PYTHONPATH=. python -m pytest tests/test_update_jibot_adapter_over_ssh.py -q
bash -n scripts/update-jibot-adapter-over-ssh.sh
```

Expected: tests and syntax check pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_update_jibot_adapter_over_ssh.py scripts/update-jibot-adapter-over-ssh.sh
git commit -m "fix(deploy): reload JIBOT action modules in WebUI"
```

### Task 3: Hexplorer restart contract and helper upload

**Files:**
- Create: `tests/test_update_hexplorer_adapter_over_ssh.py`
- Modify: `scripts/update-hexplorer-adapter-over-ssh.sh:1-35,135-225`

- [ ] **Step 1: Create failing Hexplorer deploy tests**

Create `tests/test_update_hexplorer_adapter_over_ssh.py`:

```python
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "update-hexplorer-adapter-over-ssh.sh"


def test_help_documents_restart_options():
    result = subprocess.run(
        [str(SCRIPT), "--help"], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 0
    assert "--restart" in result.stdout
    assert "--no-restart" in result.stdout
    assert "--restart-cmd CMD" in result.stdout


def test_uploads_service_helper_and_uses_it_for_restart():
    text = SCRIPT.read_text()
    assert 'LOCAL_SERVICE_HELPER="scripts/adaptor-services.sh"' in text
    assert 'mkdir -p $remote_dir_expr/scripts' in text
    assert 'cat > $remote_dir_expr/scripts/adaptor-services.sh' in text
    assert 'chmod +x $remote_dir_expr/scripts/adaptor-services.sh' in text
    assert 'scripts/adaptor-services.sh restart' in text


def test_clean_removes_module_and_web_source_trees():
    text = SCRIPT.read_text()
    clean = text[text.index("rm -rf \\"):text.index("if [ -d config ]")]
    for managed in ("core", "extensions", "web"):
        assert managed in clean
```

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH=. python -m pytest tests/test_update_hexplorer_adapter_over_ssh.py -q
```

Expected: three failures because the CLI, helper upload, and cleanup do not exist.

- [ ] **Step 3: Add compatible option parsing**

Replace the initial positional-only block with a `usage()` function and this parser:

```bash
RESTART_ADAPTER="${RESTART_ADAPTER:-0}"
RESTART_CMD="${RESTART_CMD:-}"
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --restart) RESTART_ADAPTER=1; shift ;;
    --no-restart) RESTART_ADAPTER=0; shift ;;
    --restart-cmd)
      [[ $# -ge 2 ]] || { echo "Missing value for --restart-cmd" >&2; exit 2; }
      RESTART_CMD="$2"; shift 2 ;;
    --restart-cmd=*) RESTART_CMD="${1#*=}"; shift ;;
    --) shift; POSITIONAL+=("$@"); break ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
[[ ${#POSITIONAL[@]} -ge 1 && ${#POSITIONAL[@]} -le 2 ]] || { usage >&2; exit 2; }
[[ "$RESTART_ADAPTER" == "0" || "$RESTART_ADAPTER" == "1" ]] || {
  echo "Invalid RESTART_ADAPTER: $RESTART_ADAPTER (expected 1 or 0)" >&2
  exit 2
}
REMOTE_HOST="${POSITIONAL[0]}"
REMOTE_ADAPTER_DIR="${POSITIONAL[1]:-}"
```

Document all three restart options and `RESTART_ADAPTER=1` in `usage()`.

- [ ] **Step 4: Upload the helper and wire restart**

Add the constant:

```bash
LOCAL_SERVICE_HELPER="scripts/adaptor-services.sh"
```

After the Hexplorer client upload, add:

```bash
echo "Uploading service helper to $REMOTE_HOST:$remote_dir_display/scripts/adaptor-services.sh"
"${ssh_base[@]}" "$REMOTE_HOST" \
  "mkdir -p $remote_dir_expr/scripts && cat > $remote_dir_expr/scripts/adaptor-services.sh && chmod +x $remote_dir_expr/scripts/adaptor-services.sh" \
  < "$LOCAL_SERVICE_HELPER"
```

Add `core`, `extensions`, and `web` to the existing clean list. Replace the final restart block with:

```bash
if [[ -n "$RESTART_CMD" ]]; then
  echo "Running restart command on $REMOTE_HOST"
  "${ssh_base[@]}" "$REMOTE_HOST" "$RESTART_CMD"
elif [[ "$RESTART_ADAPTER" == "1" ]]; then
  echo "Restarting adapter services on $REMOTE_HOST"
  "${ssh_base[@]}" "$REMOTE_HOST" \
    "cd $remote_dir_expr && scripts/adaptor-services.sh restart"
fi
```

- [ ] **Step 5: Verify GREEN and commit**

```bash
PYTHONPATH=. python -m pytest tests/test_update_hexplorer_adapter_over_ssh.py -q
bash -n scripts/update-hexplorer-adapter-over-ssh.sh
git add tests/test_update_hexplorer_adapter_over_ssh.py scripts/update-hexplorer-adapter-over-ssh.sh
git commit -m "fix(deploy): restart Hexplorer adapter and optional WebUI"
```

### Task 4: Compatibility installer and deterministic dispatcher smoke

**Files:**
- Modify: `scripts/test-install-systemd-service.sh`
- Modify: `adaptor/install-systemd-service.sh:1-35,100-130`
- Modify: `scripts/test-run-adapter-dispatch.sh`

- [ ] **Step 1: Change shell expectations first**

In `scripts/test-install-systemd-service.sh`, require:

```bash
assert_contains "$UNIT_FILE" "Description=AMR VDA5050 Adapter"
assert_contains "$UNIT_FILE" "ExecStart=$ROOT_DIR/adaptor/run-adapter.sh --instance SIM-001 --simulator"
```

Replace `scripts/test-run-adapter-dispatch.sh`'s invocation and assertions with:

```bash
instance="__dispatch-smoke-jibot__"
out="$(AMR_DISPATCH_PRINT=1 ./run-adapter.sh --instance "$instance" --simulator)"
echo "instance -> $out"
if [[ "$out" == *run-main.sh* && "$out" == *" --robot $instance"* && "$out" == *" --simulator" ]]; then
  echo "PASS: explicit JIBOT instance dispatches to run-main.sh"
else
  echo "FAIL: unexpected dispatch: $out" >&2
  exit 1
fi
```

- [ ] **Step 2: Verify RED**

```bash
bash scripts/test-install-systemd-service.sh
bash scripts/test-run-adapter-dispatch.sh
```

Expected: installer test fails on Description/ExecStart; dispatcher smoke passes independently of fleet size after its test-only correction.

- [ ] **Step 3: Point the compatibility installer at the dispatcher**

In `adaptor/install-systemd-service.sh`:

```bash
# Installs the AMR VDA5050 adapter as an adapter-only Ubuntu systemd service.
```

Change help text from `run-main.sh` to `run-adapter.sh`, describe `--args` as arguments forwarded through the dispatcher, and change:

```bash
RUNNER="$ADAPTER_DIR/run-adapter.sh"
```

Use the unit description:

```ini
Description=AMR VDA5050 Adapter
```

- [ ] **Step 4: Verify GREEN and commit**

```bash
bash scripts/test-install-systemd-service.sh
bash scripts/test-run-adapter-dispatch.sh
bash -n adaptor/install-systemd-service.sh scripts/test-install-systemd-service.sh scripts/test-run-adapter-dispatch.sh
git add adaptor/install-systemd-service.sh scripts/test-install-systemd-service.sh scripts/test-run-adapter-dispatch.sh
git commit -m "fix(systemd): install the adapter dispatcher"
```

### Task 5: Operator documentation and full verification

**Files:**
- Modify: `README.md:94-121`
- Modify: `adaptor/readme.md:298-335,344-384`
- Modify: `docs/manual/jibot-adapter-ssh-update.md:6-105`

- [ ] **Step 1: Add a failing documentation contract**

Add to `tests/test_update_jibot_adapter_over_ssh.py`:

```python
SSH_MANUAL = REPO_ROOT / "docs" / "manual" / "jibot-adapter-ssh-update.md"
ROOT_README = REPO_ROOT / "README.md"
ADAPTOR_README = REPO_ROOT / "adaptor" / "readme.md"


def test_operator_docs_use_current_remote_dir_and_full_stack_restart_commands():
    docs = "\n".join(path.read_text() for path in (SSH_MANUAL, ROOT_README, ADAPTOR_README))
    assert "ucore@192.168.3.222 /home/ucore/adapter" not in docs
    assert "ucore@192.168.3.10 /home/ucore/adapter" not in docs
    assert "--remote-dir /home/ucore/adapter" in docs
    assert "--restart" in docs
```

- [ ] **Step 2: Run and verify RED**

```bash
PYTHONPATH=. python -m pytest tests/test_update_jibot_adapter_over_ssh.py::test_operator_docs_use_current_remote_dir_and_full_stack_restart_commands -q
```

Expected: failure because positional remote-directory examples remain.

- [ ] **Step 3: Correct active commands**

Apply these command rules consistently:

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --remote-dir /home/ucore/adapter \
  --restart \
  ucore@192.168.3.222
```

- Replace every JIBOT second-positional remote path with `--remote-dir DIR` before the host.
- Use `--restart` when both adapter and WebUI code must reload.
- In daemon quick starts, recommend `scripts/setup-adaptor-service.sh --jibot`; describe `adaptor/install-systemd-service.sh` only as the adapter-only compatibility installer.
- Keep examples that intentionally restart one fleet instance via `--restart-cmd`, but include `amr-webui.service` when WebUI code changed.

- [ ] **Step 4: Run complete verification**

```bash
PYTHONPATH=. python -m pytest tests/test_update_jibot_adapter_over_ssh.py tests/test_update_hexplorer_adapter_over_ssh.py -q
bash scripts/test-install-systemd-service.sh
bash scripts/test-run-adapter-dispatch.sh
bash scripts/test-install-amr-tmpfiles.sh
scripts/adaptor-services.sh restart --dry-run --no-sudo
bash scripts/setup-adaptor-service.sh --jibot --dry-run --no-venv --no-camera --no-start
bash -n scripts/update-jibot-adapter-over-ssh.sh scripts/update-hexplorer-adapter-over-ssh.sh scripts/adaptor-services.sh scripts/setup-adaptor-service.sh adaptor/install-systemd-service.sh adaptor/run-adapter.sh adaptor/run-web.sh
cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py tests/test_action_module_panels_render.py tests/test_registry.py tests/test_config.py -q
git diff --check
```

Expected: every command exits 0; no dry-run references the removed `scripts/adaptor-tui.sh`.

- [ ] **Step 5: Commit**

```bash
git add README.md adaptor/readme.md docs/manual/jibot-adapter-ssh-update.md tests/test_update_jibot_adapter_over_ssh.py
git commit -m "docs(deploy): align SSH and service instructions"
```

### Task 6: Hexplorer restart TTY forwarding

**Files:**
- Modify: `tests/test_update_hexplorer_adapter_over_ssh.py`
- Modify: `scripts/update-hexplorer-adapter-over-ssh.sh:289-300`

- [ ] **Step 1: Write the failing restart transport contract**

Add to `tests/test_update_hexplorer_adapter_over_ssh.py`:

```python
def test_restart_uses_tty_when_available_and_keeps_headless_fallback():
    text = SCRIPT.read_text()
    assert 'restart_command=""' in text
    assert 'restart_command="$RESTART_CMD"' in text
    assert 'restart_command="cd $remote_dir_expr && scripts/adaptor-services.sh restart"' in text
    assert 'if { exec 3</dev/tty; } 2>/dev/null; then' in text
    assert '"${ssh_base[@]}" -tt "$REMOTE_HOST" "$restart_command" <&3' in text
    assert 'exec 3<&-' in text
    assert '"${ssh_base[@]}" "$REMOTE_HOST" "$restart_command"' in text
    assert '[[ -r /dev/tty ]]' not in text
```

This single contract covers custom-command precedence, the built-in helper
command, interactive sudo prompting, and the unchanged headless path.
Add a local fake-SSH subprocess test that runs the real script with
`stdin=subprocess.DEVNULL` and `start_new_session=True`; it must finish through
the plain SSH branch, log the helper restart command, and never log `-tt`.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
PYTHONPATH=. python -m pytest \
  tests/test_update_hexplorer_adapter_over_ssh.py::test_restart_uses_tty_when_available_and_keeps_headless_fallback -q
```

Expected: failure because the current restart branches invoke SSH directly and
contain neither `restart_command` nor an openable-TTY branch.

- [ ] **Step 3: Resolve one restart command, then select the SSH transport**

Replace the two direct SSH invocations with:

```bash
restart_command=""
if [[ -n "$RESTART_CMD" ]]; then
  echo "Running restart command on $REMOTE_HOST"
  restart_command="$RESTART_CMD"
elif [[ "$RESTART_ADAPTER" == "1" ]]; then
  echo "Restarting adapter services on $REMOTE_HOST"
  restart_command="cd $remote_dir_expr && scripts/adaptor-services.sh restart"
fi

if [[ -n "$restart_command" ]]; then
  if { exec 3</dev/tty; } 2>/dev/null; then
    "${ssh_base[@]}" -tt "$REMOTE_HOST" "$restart_command" <&3
    exec 3<&-
  else
    "${ssh_base[@]}" "$REMOTE_HOST" "$restart_command"
  fi
fi
```

Do not change default no-restart behavior, helper upload timing, restart-command
precedence, or root/passwordless-sudo behavior.

- [ ] **Step 4: Verify GREEN and regression coverage**

```bash
PYTHONPATH=. python -m pytest tests/test_update_hexplorer_adapter_over_ssh.py -q
PYTHONPATH=. python -m pytest \
  tests/test_update_jibot_adapter_over_ssh.py \
  tests/test_update_hexplorer_adapter_over_ssh.py -q
bash -n scripts/update-hexplorer-adapter-over-ssh.sh
git diff --check
```

Expected: all tests and checks exit 0. The source contains exactly one remote
restart execution block with TTY and non-TTY transports.

- [ ] **Step 5: Commit**

```bash
git add tests/test_update_hexplorer_adapter_over_ssh.py scripts/update-hexplorer-adapter-over-ssh.sh
git commit -m "fix(deploy): forward Hexplorer restart sudo prompts"
```

## Final self-review checklist

- Spec §3 JIBOT deploy: Task 2.
- Spec §3 Hexplorer deploy: Tasks 3 and 6.
- Spec §3 shared helper and optional services: Task 1.
- Spec §3 compatibility installer and TUI wording: Tasks 1 and 4.
- Spec §3 documentation commands: Task 5.
- Spec §4 test coverage and full verification: Tasks 1-6.
- No new deploy framework, dependency, service, or remote integration test.
