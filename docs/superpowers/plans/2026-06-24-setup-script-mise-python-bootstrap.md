# setup-adaptor-service.sh mise Python Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `--with-mise` flag to `scripts/setup-adaptor-service.sh` that bootstraps a Python ≥ floor interpreter via mise when the existing uv/PATH lookup can't find one; by default (flag off) the script errors with guidance pointing at `--with-mise`.

**Architecture:** mise becomes a *last-resort* Python provider inside `repair_venv`. The uv-first flow and healthy-host early-return are untouched. Three new helpers (`run_as_deploy_user`, `find_mise`, `mise_provide_python`) are only invoked when `--with-mise` is set. A new `die_no_python` helper centralizes the "no interpreter" error + `--with-mise` guidance.

**Tech Stack:** Bash (must stay POSIX-ish and bash 4.3 compatible for Ubuntu 16.04 onboards), `mise` (single static binary, precompiled python-build-standalone), `uv`, systemd-host setup script. No new repo dependencies.

## Global Constraints

- **Python floor = pyproject `requires-python`** (currently 3.11), read via the existing `adapter_min_python` helper — never hardcode the version; copy it from that function's output (`$min`).
- **bash 4.3 / Ubuntu 16.04 compatible**: no bash-5-only syntax; guard empty-array expansions under `set -u` (existing pattern). mise is bash-version-agnostic (single binary).
- **`set -euo pipefail` is active** (line 26): every new command either succeeds, is guarded with `|| true` / `|| { … }`, or is intended to abort.
- **Default no-op**: when `--with-mise` is NOT passed, the script must never invoke mise or curl. Healthy hosts and CI are unaffected.
- **mise installs into the DEPLOY_USER home** (`~/.local`), even when the script runs under sudo — always route mise through `run_as_deploy_user`.
- **mise progress goes to stderr**; `mise_provide_python` echoes ONLY the interpreter path to stdout (it is captured by `$(...)`).
- **Verification**: `shellcheck` is not installed on dev/onboard → use `bash -n` for syntax. Functional checks are `--dry-run` invocations (the script makes no changes in dry-run).
- **Spec**: `docs/superpowers/specs/2026-06-24-setup-script-mise-python-bootstrap-design.md`.

---

### Task 1: Add the `--with-mise` flag (parsing + usage/help)

**Files:**
- Modify: `scripts/setup-adaptor-service.sh` (defaults block ~line 42-47, usage header lines 17-25, arg `case` lines 49-62)

**Interfaces:**
- Produces: shell variable `WITH_MISE` (`0` default, `1` when `--with-mise` given). Tasks 2-3 read it.

- [ ] **Step 1: Add the default `WITH_MISE=0`**

In the defaults block, add the variable next to the other `DO_*` flags. Change:

```bash
DO_START=1
CAMERA_SERVICE_AVAILABLE=0
```

to:

```bash
DO_START=1
WITH_MISE=0
CAMERA_SERVICE_AVAILABLE=0
```

- [ ] **Step 2: Parse `--with-mise` in the arg loop**

In the `while … case "$1"` block, add a case directly after the `--no-start` line. Change:

```bash
    --no-start) DO_START=0 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
```

to:

```bash
    --no-start) DO_START=0 ;;
    --with-mise) WITH_MISE=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
```

- [ ] **Step 3: Document the flag in the usage header**

The `-h|--help` branch prints the file header via `sed`. Update the usage synopsis line and add a description. Change:

```bash
#   scripts/setup-adaptor-service.sh [--jibot] [--hexplorer]
#                                    [--dry-run] [--no-venv] [--no-sudoers]
#                                    [--no-camera] [--no-start]
#
#   --jibot/--hexplorer validate that config/config.toml [adapter].vendor matches;
#     vendor is determined by config, not the flag. Fail on mismatch.
#   --dry-run prints the generated unit files and planned actions, changes nothing.
#   --no-start installs/enables units without starting them.
```

to:

```bash
#   scripts/setup-adaptor-service.sh [--jibot] [--hexplorer]
#                                    [--dry-run] [--no-venv] [--no-sudoers]
#                                    [--no-camera] [--no-start] [--with-mise]
#
#   --jibot/--hexplorer validate that config/config.toml [adapter].vendor matches;
#     vendor is determined by config, not the flag. Fail on mismatch.
#   --dry-run prints the generated unit files and planned actions, changes nothing.
#   --no-start installs/enables units without starting them.
#   --with-mise bootstraps Python via mise when no Python >= the pyproject floor
#     is found (installs mise if missing; needs network). Off by default — without
#     it, a missing interpreter is a hard error that points back at this flag.
```

- [ ] **Step 4: Syntax check**

Run: `bash -n scripts/setup-adaptor-service.sh`
Expected: no output, exit 0.

- [ ] **Step 5: Verify the flag is accepted and documented**

Run: `scripts/setup-adaptor-service.sh --help | grep -- '--with-mise'`
Expected: prints the two `--with-mise` description lines (and the synopsis line).

Run: `scripts/setup-adaptor-service.sh --with-mise --dry-run --no-sudoers --no-camera --no-start 2>&1 | grep -c 'Unknown option'`
Expected: `0` (the flag is parsed, not rejected).

- [ ] **Step 6: Commit**

```bash
git add scripts/setup-adaptor-service.sh
git commit -m "feat(setup): add --with-mise flag (parsing + help)"
```

---

### Task 2: Add mise helpers (`run_as_deploy_user`, `find_mise`, `mise_provide_python`)

**Files:**
- Modify: `scripts/setup-adaptor-service.sh` — insert after `find_uv()` (ends ~line 184), before `repair_venv()` (line 186)

**Interfaces:**
- Consumes: `DEPLOY_USER`, `DRY_RUN` (globals); `find_uv`'s pattern; `python_meets_min` (existing, line 165).
- Produces:
  - `run_as_deploy_user <cmd...>` → runs cmd as DEPLOY_USER (via sudo when root), else directly.
  - `find_mise` → echoes a mise binary path, returns 0; returns 1 if none found.
  - `mise_provide_python <min>` → on success echoes an absolute interpreter path (stdout) and returns 0; on failure prints guidance to stderr and returns 1. In `DRY_RUN` it prints a note to stderr and returns 0 with NO stdout.

These are dead code after this task (nothing calls them yet); Task 3 wires them in.

- [ ] **Step 1: Insert the three helpers**

Insert this block immediately after the closing `}` of `find_uv` and before the `repair_venv()` line:

```bash
# Run a command as the deploy user. When this script runs under sudo (EUID 0),
# mise must still install into the *deploy* user's home (~/.local), not root's,
# so route through `sudo -u … -H`. When already the user, run directly.
run_as_deploy_user() {
  if [[ "$(id -u)" -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    sudo -u "$DEPLOY_USER" -H "$@"
  else
    "$@"
  fi
}

# Echo a mise binary path if one is reachable (PATH, the deploy user's ~/.local
# bin, or /usr/local/bin) — mirrors find_uv, covering setup run under sudo.
find_mise() {
  local home c
  home="$(eval echo "~$DEPLOY_USER" 2>/dev/null)"
  for c in mise "$home/.local/bin/mise" "$HOME/.local/bin/mise" /usr/local/bin/mise; do
    if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return 0; fi
  done
  return 1
}

# Provide a Python >= $1 interpreter via mise, installing mise (official script)
# and the interpreter if needed. Echoes the interpreter path to stdout on
# success; returns nonzero (guidance on stderr) on failure. Honors DRY_RUN.
# All progress goes to stderr so the captured stdout is just the path.
mise_provide_python() {
  local min="$1" mise wdir py
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] would install mise (if missing) and python@$min via mise" >&2
    return 0
  fi
  mise="$(find_mise || true)"
  if [[ -z "$mise" ]]; then
    echo "  mise not found; installing via https://mise.run (needs network)…" >&2
    if ! command -v curl >/dev/null 2>&1; then
      echo "  ERROR: curl is required to install mise but is not present." >&2
      return 1
    fi
    run_as_deploy_user sh -c 'curl -fsSL https://mise.run | sh' >&2 \
      || { echo "  ERROR: mise install failed (offline or download error)." >&2; return 1; }
    mise="$(find_mise || true)"
    [[ -n "$mise" ]] || { echo "  ERROR: mise still not found after install." >&2; return 1; }
  fi
  echo "  installing python@$min via mise (precompiled standalone)…" >&2
  run_as_deploy_user env MISE_PYTHON_COMPILE=0 "$mise" install "python@$min" >&2 \
    || { echo "  ERROR: 'mise install python@$min' failed." >&2; return 1; }
  wdir="$(run_as_deploy_user "$mise" where "python@$min" 2>/dev/null)" \
    || { echo "  ERROR: could not locate mise python@$min install dir." >&2; return 1; }
  py="$wdir/bin/python"
  [[ -x "$py" ]] || py="$wdir/bin/python3"
  python_meets_min "$py" "$min" \
    || { echo "  ERROR: mise python@$min is not >= $min (resolved $py)." >&2; return 1; }
  echo "$py"
}
```

- [ ] **Step 2: Syntax check**

Run: `bash -n scripts/setup-adaptor-service.sh`
Expected: no output, exit 0.

- [ ] **Step 3: Verify no behavior change (helpers are not yet called)**

Run: `scripts/setup-adaptor-service.sh --dry-run --no-sudoers --no-camera --no-start 2>&1 | grep 'venv check'`
Expected: prints the `==> venv check (…requires Python >= 3.11)` line — same as before this task. No mise output appears (helpers unused).

- [ ] **Step 4: Smoke-test `find_mise` / `run_as_deploy_user` in isolation**

Run:
```bash
bash -c '
  DEPLOY_USER="$(id -un)"; HOME="$HOME"
  source <(sed -n "/^run_as_deploy_user()/,/^}$/p;/^find_mise()/,/^}$/p" scripts/setup-adaptor-service.sh)
  run_as_deploy_user echo "ran-as-user-ok"
  find_mise && echo "found-mise" || echo "no-mise (expected on hosts without mise)"
'
```
Expected: prints `ran-as-user-ok`, then either a mise path + `found-mise` (this dev box has mise) or `no-mise …`. Either is acceptable — it proves both helpers parse and run.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup-adaptor-service.sh
git commit -m "feat(setup): add mise helpers (find_mise, mise_provide_python, run_as_deploy_user)"
```

---

### Task 3: Wire mise into `repair_venv` + `die_no_python` guidance

**Files:**
- Modify: `scripts/setup-adaptor-service.sh` — add `die_no_python` (after the helpers from Task 2); edit `repair_venv` dry-run note (~line 204-207), uv branch (~line 213-218), no-uv branch (~line 226-232)

**Interfaces:**
- Consumes: `WITH_MISE` (Task 1), `mise_provide_python` (Task 2), `adapter_min_python` (existing), `python_meets_min` (existing).
- Produces: `die_no_python <context-msg>` → prints the floor-aware error + `--with-mise` guidance to stderr and `exit 1`.

- [ ] **Step 1: Add `die_no_python` helper**

Insert directly after the `mise_provide_python` function (from Task 2), before `repair_venv()`:

```bash
# Abort with actionable guidance when no suitable Python could be obtained.
# $1 = context (what was already tried). Points at --with-mise as the opt-in fix.
die_no_python() {
  local min context="$1"
  min="$(adapter_min_python)"
  echo "  ERROR: No Python >= $min found, and $context." >&2
  echo "    • Re-run with --with-mise to bootstrap Python $min via mise" >&2
  echo "      (installs mise if missing; requires network access)." >&2
  echo "    • Or install it yourself:  sudo apt install python$min python$min-venv" >&2
  echo "    • Or stage an interpreter / wheels offline and re-run." >&2
  exit 1
}
```

- [ ] **Step 2: Add the dry-run `--with-mise` note**

In `repair_venv`, change the dry-run early-return:

```bash
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] would (re)create $venv pinned to Python >= $min and install deps (offline if offline_packages/ exists)."
    return 0
  fi
```

to:

```bash
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] would (re)create $venv pinned to Python >= $min and install deps (offline if offline_packages/ exists)."
    [[ $WITH_MISE -eq 1 ]] && echo "  [dry-run] --with-mise: would bootstrap Python $min via mise if none is found."
    return 0
  fi
```

- [ ] **Step 3: Wire mise into the uv branch**

Change the uv branch's interpreter-failure handling. Replace:

```bash
    "$uv" venv --python "$min" "$venv" 2>/dev/null \
      || ( cd "$ADAPTER_DIR" && "$uv" venv "$venv" ) \
      || { echo "  ERROR: 'uv venv' found no Python >= $min. Install it (e.g. 'sudo apt install python$min python$min-venv') and re-run." >&2; exit 1; }
```

with:

```bash
    if ! { "$uv" venv --python "$min" "$venv" 2>/dev/null \
           || ( cd "$ADAPTER_DIR" && "$uv" venv "$venv" ); }; then
      if [[ $WITH_MISE -eq 1 ]]; then
        local py=""
        if py="$(mise_provide_python "$min")" && [[ -n "$py" ]]; then
          "$uv" venv --python "$py" "$venv" \
            || die_no_python "uv venv failed with mise-provided interpreter $py"
        else
          die_no_python "mise bootstrap failed to provide Python >= $min"
        fi
      else
        die_no_python "uv could not provide Python >= $min"
      fi
    fi
```

- [ ] **Step 4: Wire mise into the no-uv branch**

Change the PATH-probe failure handling. Replace:

```bash
    [[ -n "$found" ]] || { echo "  ERROR: no Python >= $min on PATH and no uv; install Python $min and re-run." >&2; exit 1; }
```

with:

```bash
    if [[ -z "$found" ]]; then
      if [[ $WITH_MISE -eq 1 ]]; then
        found="$(mise_provide_python "$min")" && [[ -n "$found" ]] \
          || die_no_python "no Python >= $min on PATH, no uv, and mise bootstrap failed"
      else
        die_no_python "no Python >= $min on PATH and no uv"
      fi
    fi
```

- [ ] **Step 5: Syntax check**

Run: `bash -n scripts/setup-adaptor-service.sh`
Expected: no output, exit 0.

- [ ] **Step 6: Verify the dry-run `--with-mise` note appears (and only with the flag)**

Run: `scripts/setup-adaptor-service.sh --with-mise --dry-run --no-sudoers --no-camera --no-start 2>&1 | grep 'bootstrap Python'`
Expected: prints `  [dry-run] --with-mise: would bootstrap Python 3.11 via mise if none is found.`

Run: `scripts/setup-adaptor-service.sh --dry-run --no-sudoers --no-camera --no-start 2>&1 | grep -c 'bootstrap Python'`
Expected: `0` (no mise note without the flag — default path unchanged).

- [ ] **Step 7: Verify `die_no_python` guidance shape in isolation**

Run:
```bash
bash -c '
  ADAPTER_DIR="adaptor"
  source <(sed -n "/^adapter_min_python()/,/^}$/p;/^die_no_python()/,/^}$/p" scripts/setup-adaptor-service.sh)
  ( die_no_python "no Python >= 3.11 on PATH and no uv" ) 2>&1 | sed -n "1,5p"
'
```
Expected: 5 guidance lines starting with `ERROR: No Python >= 3.11 found, and no Python >= 3.11 on PATH and no uv.` and including the `--with-mise` bullet and `sudo apt install python3.11 python3.11-venv`.

- [ ] **Step 8: Confirm the healthy-host path still no-ops**

Run: `scripts/setup-adaptor-service.sh --dry-run --no-sudoers --no-camera --no-start 2>&1 | grep 'venv check'`
Expected: prints the `==> venv check …` line, then either "venv OK" (if `.venv` already valid) or the "would (re)create" dry-run note — identical to pre-change behavior, with no mise output.

- [ ] **Step 9: Commit**

```bash
git add scripts/setup-adaptor-service.sh
git commit -m "feat(setup): use mise as last-resort Python provider under --with-mise"
```

---

## Manual / onboard verification (not automatable here)

The real install path needs network + a host lacking Python ≥ floor, which the dev box does not reproduce (it has python3.12 + uv). On an onboard, after deploying this script:

1. `scripts/setup-adaptor-service.sh --jibot` on a box without python3.11/uv → expect the `die_no_python` guidance and a non-zero exit (no install attempted).
2. `scripts/setup-adaptor-service.sh --jibot --with-mise` on the same box (with network) → expect mise to install, `python@3.11` to download (precompiled), `.venv` to be built and pass the floor re-check, then services to install/start.
3. Re-running without changes → `repair_venv` early-returns ("venv OK"); mise is not touched again.

## Self-Review

- **Spec coverage:** `--with-mise` flag (Task 1 ✓); install mise if missing + install python@floor + build venv (Task 2 `mise_provide_python` + Task 3 wiring ✓); default = no install + error with `--with-mise` guidance (Task 3 `die_no_python` ✓); dry-run note (Task 3 Step 2 ✓); precompiled `MISE_PYTHON_COMPILE=0` for old Ubuntu (Task 2 ✓); DEPLOY_USER/sudo context (Task 2 `run_as_deploy_user` ✓); floor from `adapter_min_python`, never hardcoded (Tasks 2-3 use `$min` / re-derive ✓); venv stays at `ADAPTER_DIR/.venv` (no path change ✓).
- **Placeholder scan:** none — every step shows exact old→new code or an exact command + expected output.
- **Type/name consistency:** `WITH_MISE` (Task 1) read in Task 3; `mise_provide_python`/`find_mise`/`run_as_deploy_user` (Task 2) called in Task 3; `die_no_python` defined and used in Task 3; `python_meets_min`/`adapter_min_python` are existing functions. No drift.
