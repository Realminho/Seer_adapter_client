#!/usr/bin/env bash
# Installs the AMR adapter as local systemd services (amr-adaptor.service /
# amr-adaptor@.service) and wires up the WebUI and service helpers:
#
#   1. generates /etc/systemd/system/<unit> from scripts/systemd/*.service
#      (substituting this host's User= and WorkingDirectory=),
#   2. (re)builds adaptor/.venv pinned to Python >= requires-python (via uv),
#      from the offline wheel cache, so the adapter and WebUI both have paho-mqtt
#      and never silently run on a too-old interpreter,
#   3. drops a scoped /etc/sudoers.d/adaptor-tui so service helpers can manage
#      the units without a password prompt.
#
# Vendor is determined by config/config.toml [adapter].vendor.
# Mirrors scripts/setup-web-video-server-on-onboard.sh. Run it ON the host that
# runs the adapter (local-only management).
#
# Usage:
#   scripts/setup-adaptor-service.sh [--jibot] [--hexplorer]
#                                    [--dry-run] [--no-venv] [--no-sudoers]
#                                    [--no-camera] [--no-xboxdrv] [--with-xboxdrv]
#                                    [--no-start] [--with-mise]
#
#   --jibot/--hexplorer validate that config/config.toml [adapter].vendor matches;
#     vendor is determined by config, not the flag. Fail on mismatch.
#   --dry-run prints the generated unit files and planned actions, changes nothing.
#   --no-start installs/enables units without starting them.
#   --no-xboxdrv skips the joystick bridge; --with-xboxdrv installs it even when
#     the xpad driver is present (which otherwise skips it, since the kernel
#     already exposes the receiver and the two would fight over it).
#   --with-mise DOWNLOADS Python via mise when no Python >= the pyproject floor
#     is found (installs mise if missing; needs network). Off by default — without
#     it, a missing interpreter is a hard error that points back at this flag.
#     A Python mise has *already* installed is used without this flag.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -f "$REPO_ROOT/run-main.sh" ]]; then
  ADAPTER_DIR="$REPO_ROOT"
else
  ADAPTER_DIR="$REPO_ROOT/adaptor"
fi
SYSTEMD_SRC="$SCRIPT_DIR/systemd"

DO_JIBOT=0
DO_HEXPLORER=0
DRY_RUN=0
DO_VENV=1
DO_SUDOERS=1
DO_CAMERA=1
DO_XBOXDRV=1
FORCE_XBOXDRV=0
DO_START=1
WITH_MISE=0
CAMERA_SERVICE_AVAILABLE=0
XBOXDRV_SERVICE_AVAILABLE=0
WEBUI_DEFAULT_USER="admin"
WEBUI_DEFAULT_PASSWORD="labtomarket1231"
WEB_VIDEO_PORT="${WEB_VIDEO_PORT:-9001}"
ROS_DISTRO="${WEB_VIDEO_ROS_DISTRO:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --jibot) DO_JIBOT=1 ;;
    --hexplorer) DO_HEXPLORER=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-venv) DO_VENV=0 ;;
    --no-sudoers) DO_SUDOERS=0 ;;
    --no-camera) DO_CAMERA=0 ;;
    --no-xboxdrv) DO_XBOXDRV=0 ;;
    --with-xboxdrv) FORCE_XBOXDRV=1 ;;
    --no-start) DO_START=0 ;;
    --with-mise) WITH_MISE=1 ;;
    -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# The deploy user is the human invoking the script, even under sudo.
DEPLOY_USER="${SUDO_USER:-$(id -un)}"
if ! getent passwd "$DEPLOY_USER" >/dev/null 2>&1; then
  echo "Deploy user does not exist on this host: $DEPLOY_USER" >&2
  exit 1
fi
SYSTEMCTL_BIN="$(command -v systemctl || true)"
if [[ -z "$SYSTEMCTL_BIN" ]]; then
  echo "systemctl not found — this host does not use systemd." >&2
  exit 1
fi

# config.toml [adapter] 섹션의 vendor 값(따옴표/주석 제거). 없으면 jibot.
# TOML 라이브러리에 의존하지 않게 awk로 읽는다(로봇 Python 3.10/venv 상태 무관).
CONFIG_VENDOR="$(awk '
  /^\[adapter\]/ {insec=1; next}
  /^\[/ {insec=0}
  insec && /^[[:space:]]*vendor[[:space:]]*=/ {
    sub(/.*=[[:space:]]*/,""); gsub(/["'"'"' \r]/,""); sub(/#.*/,""); print; exit
  }
' "$ADAPTER_DIR/config/config.toml" 2>/dev/null)"
CONFIG_VENDOR="${CONFIG_VENDOR:-jibot}"

EXPECTED_VENDOR=""
[[ $DO_JIBOT -eq 1 ]] && EXPECTED_VENDOR="jibot"
[[ $DO_HEXPLORER -eq 1 ]] && EXPECTED_VENDOR="hexplorer"
if [[ -n "$EXPECTED_VENDOR" && "$CONFIG_VENDOR" != "$EXPECTED_VENDOR" ]]; then
  echo "ERROR: --$EXPECTED_VENDOR given, but config/config.toml [adapter].vendor is '$CONFIG_VENDOR'." >&2
  echo "       Set [adapter].vendor = \"$EXPECTED_VENDOR\" in config.toml and re-run." >&2
  exit 1
fi

run_root() {
  # Execute a command as root, or just print it in dry-run mode.
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] $*"
    return 0
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

configure_journald_logging() {
  echo "==> journald logging"
  local needs_unmask=0 needs_persistent=0 needs_group=0 restart_journald=0
  local unit state
  local journald_units=(
    systemd-journald.service
    systemd-journald.socket
    systemd-journald-dev-log.socket
  )

  for unit in "${journald_units[@]}"; do
    state="$("$SYSTEMCTL_BIN" is-enabled "$unit" 2>/dev/null || true)"
    if [[ "$state" == "masked" ]]; then
      needs_unmask=1
    fi
  done

  [[ ! -d /var/log/journal ]] && needs_persistent=1

  if ! id -nG "$DEPLOY_USER" 2>/dev/null | tr ' ' '\n' | grep -qx systemd-journal; then
    needs_group=1
  fi

  if [[ $needs_unmask -eq 0 && $needs_persistent -eq 0 && $needs_group -eq 0 ]]; then
    echo "  already configured."
    return 0
  fi

  if [[ $needs_unmask -eq 1 ]]; then
    echo "  unmasking systemd-journald units"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  [dry-run] $SYSTEMCTL_BIN unmask ${journald_units[*]}"
    else
      run_root "$SYSTEMCTL_BIN" unmask "${journald_units[@]}" || \
        echo "  WARNING: could not unmask one or more systemd-journald units." >&2
    fi
    restart_journald=1
  fi

  if [[ $needs_persistent -eq 1 ]]; then
    echo "  enabling persistent journal storage (/var/log/journal)"
    run_root mkdir -p /var/log/journal
    if command -v systemd-tmpfiles >/dev/null 2>&1; then
      run_root systemd-tmpfiles --create --prefix /var/log/journal || \
        echo "  WARNING: systemd-tmpfiles could not apply /var/log/journal permissions." >&2
    fi
    restart_journald=1
  fi

  if [[ $needs_group -eq 1 ]]; then
    echo "  granting journal read access to $DEPLOY_USER"
    run_root usermod -aG systemd-journal "$DEPLOY_USER" || \
      echo "  WARNING: could not add $DEPLOY_USER to systemd-journal; use sudo journalctl." >&2
    echo "  NOTE: $DEPLOY_USER must log out and back in before sudo-less journalctl works."
  fi

  if [[ $restart_journald -eq 1 ]]; then
    echo "  restarting systemd-journald"
    run_root "$SYSTEMCTL_BIN" daemon-reload
    run_root "$SYSTEMCTL_BIN" restart systemd-journald.service || \
      echo "  WARNING: could not restart systemd-journald; run 'sudo systemctl status systemd-journald'." >&2
  fi
}

install_pure_python_wheels_without_pip() {
  local py="$1" wheel_dir="$2"
  echo "  pip/ensurepip unavailable; extracting pure Python wheels directly."
  "$py" - "$wheel_dir" <<'PY'
import pathlib
import sys
import sysconfig
import zipfile

wheel_dir = pathlib.Path(sys.argv[1])
target = pathlib.Path(sysconfig.get_paths()["purelib"])
target.mkdir(parents=True, exist_ok=True)
wheels = sorted(wheel_dir.glob("*-none-any.whl"))
if not wheels:
    raise SystemExit(f"no pure Python wheels found in {wheel_dir}")
for wheel in wheels:
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
    print(f"  extracted {wheel.name} -> {target}")
PY
}

install_unit() {
  local name="$1" template="$SYSTEMD_SRC/$1"
  if [[ ! -f "$template" ]]; then
    echo "Missing unit template: $template" >&2
    exit 1
  fi
  echo "==> $name (User=$DEPLOY_USER, WorkingDirectory=$ADAPTER_DIR)"
  local rendered
  rendered="$(sed -e "s|__USER__|$DEPLOY_USER|g" -e "s|__WORKDIR__|$ADAPTER_DIR|g" \
                  -e "s|__SCRIPTSDIR__|$SCRIPT_DIR|g" "$template")"
  if [[ "$rendered" == *"__USER__"* || "$rendered" == *"__WORKDIR__"* \
        || "$rendered" == *"__SCRIPTSDIR__"* ]]; then
    echo "Template placeholders were not fully rendered for $name" >&2
    exit 1
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "----- /etc/systemd/system/$name -----"
    echo "$rendered"
    echo "-------------------------------------"
    return 0
  fi
  printf '%s\n' "$rendered" | run_root tee "/etc/systemd/system/$name" >/dev/null
}

# Minimum Python for the adapter venv, read from adaptor/pyproject.toml's
# requires-python so it can't drift. The adapter evaluates PEP 604 'X | None'
# unions at runtime, so an older interpreter dies at startup with "unsupported
# operand type(s) for |: 'type' and 'NoneType'". Falls back to 3.11.
adapter_min_python() {
  local v
  v="$(sed -nE 's/^[[:space:]]*requires-python[[:space:]]*=.*>=[[:space:]]*"?([0-9]+\.[0-9]+).*/\1/p' \
        "$ADAPTER_DIR/pyproject.toml" 2>/dev/null)"
  echo "${v:-3.11}"
}

# 0 (success) iff interpreter $1 exists and is >= version $2 (e.g. "3.11").
python_meets_min() {
  local py="$1" min="$2"
  [[ -n "$py" && -x "$py" ]] || return 1
  "$py" - "$min" <<'PY' 2>/dev/null
import sys
maj, mn = (int(x) for x in sys.argv[1].split("."))
raise SystemExit(0 if sys.version_info[:2] >= (maj, mn) else 1)
PY
}

# Echo a uv binary path if one is reachable (PATH, the deploy user's ~/.local or
# ~/.cargo bin, /usr/local/bin, or a mise-managed uv) — covers running setup
# under sudo, where sudoers secure_path drops ~/.local/share/mise/shims and
# $HOME is root's, so a perfectly good `mise use -g uv` looks like no uv at all.
find_uv() {
  local home c mise
  home="$(eval echo "~$DEPLOY_USER" 2>/dev/null)"
  for c in uv "$home/.local/bin/uv" "$home/.cargo/bin/uv" "$HOME/.local/bin/uv" \
           /usr/local/bin/uv "$home/.local/share/mise/shims/uv"; do
    if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return 0; fi
  done
  if mise="$(find_mise || true)" && [[ -n "$mise" ]]; then
    c="$(run_as_deploy_user "$mise" which uv 2>/dev/null || true)"
    if [[ -n "$c" && -x "$c" ]]; then echo "$c"; return 0; fi
  fi
  return 1
}

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

# Echo a Python >= $1 that an ALREADY-INSTALLED mise can provide, or return
# nonzero. Installs nothing and needs no network, so unlike mise_provide_python
# this is safe to consult without --with-mise: a robot with mise fully set up
# would otherwise be told "no Python >= 3.11 found", because sudo strips
# ~/.local/share/mise/shims from PATH and mise's installs live under the deploy
# user's home, not root's. Silent — callers decide what to report.
mise_existing_python() {
  local min="$1" mise home wdir c
  mise="$(find_mise || true)"
  [[ -n "$mise" ]] || return 1
  home="$(eval echo "~$DEPLOY_USER" 2>/dev/null)"
  # `mise where` resolves the version pinned by the user's config; the shim and
  # a bare `mise which` cover a global/parent-dir pin of a newer minor.
  wdir="$(run_as_deploy_user "$mise" where "python@$min" 2>/dev/null || true)"
  for c in ${wdir:+"$wdir/bin/python" "$wdir/bin/python3"} \
           "$home/.local/share/mise/shims/python3" \
           "$(run_as_deploy_user "$mise" which python3 2>/dev/null || true)"; do
    if python_meets_min "$c" "$min"; then echo "$c"; return 0; fi
  done
  return 1
}

# Provide a Python >= $1 interpreter via mise, installing mise (official script)
# and the interpreter if needed. Echoes the interpreter path to stdout on
# success; returns nonzero (guidance on stderr) on failure. Honors DRY_RUN.
# All progress goes to stderr so the captured stdout is just the path.
mise_provide_python() {
  local min="$1" mise wdir py
  # Defensive: repair_venv already short-circuits on DRY_RUN before any
  # interpreter lookup, so this guard is normally unreached — it just keeps the
  # helper safe if a future caller invokes it during a dry run.
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
  # The units were installed and enabled before this point, so exiting here
  # leaves them armed for the next boot with no .venv behind them. Say so:
  # otherwise the only symptom is a crash-looping service whose journal blames
  # the system python3 the launcher fell back to.
  if [[ $DRY_RUN -eq 0 ]]; then
    echo "  NOTE: the systemd units are installed and ENABLED but cannot start without" >&2
    echo "        the venv — the adapter will crash-loop at boot until you re-run this" >&2
    echo "        script successfully (or 'sudo systemctl disable --now amr-adaptor.service')." >&2
  fi
  exit 1
}

repair_venv() {
  local venv="$ADAPTER_DIR/.venv"   # uv's default; run-main.sh prefers it
  local min req wheels uv have_ver
  min="$(adapter_min_python)"
  req="$ADAPTER_DIR/requirements.txt"
  wheels="$ADAPTER_DIR/offline_packages"
  echo "==> venv check ($venv, requires Python >= $min)"

  # Reuse only when the venv is BOTH new enough AND has the deps. The old check
  # skipped the version test, so a pre-3.11 venv with paho installed survived a
  # system-Python upgrade and the adapter then crashed at startup.
  if python_meets_min "$venv/bin/python" "$min" \
     && "$venv/bin/python" -c "import paho.mqtt.client; import tomli; import hcl2" >/dev/null 2>&1; then
    have_ver="$("$venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    echo "  venv OK (Python $have_ver, deps present); leaving it as is."
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] would (re)create $venv pinned to Python >= $min and install deps (offline if offline_packages/ exists)."
    [[ $WITH_MISE -eq 1 ]] && echo "  [dry-run] --with-mise: would bootstrap Python $min via mise if none is found."
    return 0
  fi

  echo "  (re)creating venv pinned to Python >= $min…"
  rm -rf "$venv"
  uv="$(find_uv || true)"

  if [[ -n "$uv" ]]; then
    # Try the exact floor first; otherwise let uv honour pyproject's
    # requires-python (run from ADAPTER_DIR). Creating a venv needs no network.
    if ! { "$uv" venv --python "$min" "$venv" 2>/dev/null \
           || ( cd "$ADAPTER_DIR" && "$uv" venv "$venv" ); }; then
      local py=""
      # An already-installed mise counts as "the interpreter is here"; only
      # downloading one is gated behind --with-mise.
      if py="$(mise_existing_python "$min")" && [[ -n "$py" ]]; then
        echo "  using the Python $min already installed via mise: $py" >&2
        "$uv" venv --python "$py" "$venv" \
          || die_no_python "uv venv failed with the mise interpreter $py"
      elif [[ $WITH_MISE -eq 1 ]]; then
        if py="$(mise_provide_python "$min")" && [[ -n "$py" ]]; then
          "$uv" venv --python "$py" "$venv" \
            || die_no_python "uv venv failed with mise-provided interpreter $py"
        else
          die_no_python "mise bootstrap failed to provide Python >= $min"
        fi
      else
        die_no_python "uv could not provide Python >= $min (and mise has none installed)"
      fi
    fi
    if [[ -d "$wheels" ]]; then
      "$uv" pip install --python "$venv/bin/python" --no-index --find-links "$wheels" -r "$req" \
        || { echo "  ERROR: offline dep install failed (offline_packages/ missing wheels?)." >&2; exit 1; }
    else
      "$uv" pip install --python "$venv/bin/python" -r "$req" \
        || { echo "  ERROR: dep install failed (no offline_packages/, and online install failed)." >&2; exit 1; }
    fi
  else
    # No uv: probe PATH for an explicit >= $min interpreter, then stdlib venv+pip.
    local cand found=""
    for cand in "python$min" python3.13 python3.12 python3.11; do
      if python_meets_min "$(command -v "$cand" 2>/dev/null)" "$min"; then found="$cand"; break; fi
    done
    if [[ -z "$found" ]]; then
      # Same as the uv branch: an interpreter mise already has is not a
      # bootstrap, so it needs no flag. PATH alone misses it under sudo.
      if found="$(mise_existing_python "$min")" && [[ -n "$found" ]]; then
        echo "  using the Python $min already installed via mise: $found" >&2
      elif [[ $WITH_MISE -eq 1 ]]; then
        found="$(mise_provide_python "$min")" && [[ -n "$found" ]] \
          || die_no_python "no Python >= $min on PATH, no uv, and mise bootstrap failed"
      else
        die_no_python "no Python >= $min on PATH, no uv, and mise has none installed"
      fi
    fi
    "$found" -m venv "$venv" || { echo "  ERROR: '$found -m venv' failed (install the python$min-venv package)." >&2; exit 1; }
    "$venv/bin/python" -m pip --version >/dev/null 2>&1 \
      || "$venv/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 \
      || install_pure_python_wheels_without_pip "$venv/bin/python" "$wheels"
    if "$venv/bin/python" -m pip --version >/dev/null 2>&1; then
      "$venv/bin/python" -m pip install --no-index --find-links "$wheels" -r "$req" \
        || "$venv/bin/python" -m pip install -r "$req" \
        || { echo "  ERROR: dep install failed." >&2; exit 1; }
    fi
  fi

  # Verify the result actually meets the floor (the "compare and proceed" gate),
  # then retire the legacy venv so run-main.sh can't fall back to a stale one.
  python_meets_min "$venv/bin/python" "$min" \
    || { echo "  ERROR: built venv is Python < $min; aborting." >&2; exit 1; }
  have_ver="$("$venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  if [[ -d "$ADAPTER_DIR/venvJIBOT" ]]; then
    echo "  removing legacy venvJIBOT (superseded by .venv)…"
    rm -rf "$ADAPTER_DIR/venvJIBOT"
  fi
  echo "  venv ready: Python $have_ver at $venv"
}

# Print fleet robot ids (one per line) from config/robots.hcl, or nothing if
# there is no fleet file. Uses the adapter's python so tomllib/tomli is present.
list_robot_ids() {
  local py="$ADAPTER_DIR/.venv/bin/python"
  [[ -x "$py" ]] || py="$ADAPTER_DIR/venvJIBOT/bin/python"
  [[ -x "$py" ]] || py="python3"
  "$py" "$ADAPTER_DIR/run_multi.py" --list 2>/dev/null || true
}

sudoers_escape_arg() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//,/\\,}"
  value="${value//:/\\:}"
  value="${value//=/\\=}"
  printf '%s' "$value"
}

install_sudoers() {
  local file="/etc/sudoers.d/adaptor-tui"
  local units=()
  local unit escaped_unit
  # Ordinary robots.hcl entries stay in amr-adaptor.service, while explicit
  # adapter instances are enumerated in JIBOT_UNITS. Manually targeted JIBOT
  # @robot units not configured by setup may prompt; do not grant them broadly.
  for unit in ${JIBOT_UNITS[@]+"${JIBOT_UNITS[@]}"}; do
    units+=("$unit")
  done
  # amr-webui.service is NOT polkit-managed (the WebUi must not restart itself
  # from its own control page), so the over-ssh deploy's --restart-cmd and the
  # service helper restart it via sudo without prompting for a password.
  units+=("amr-webui.service")
  units+=("amr-camera.service")
  units+=("amr-xboxdrv.service")
  local cmds=""
  for unit in "${units[@]}"; do
    escaped_unit="$(sudoers_escape_arg "$unit")"
    for verb in start stop restart enable disable; do
      [[ -n "$cmds" ]] && cmds+=", "
      cmds+="$SYSTEMCTL_BIN $verb $escaped_unit"
    done
  done
  local content="# Managed by scripts/setup-adaptor-service.sh — lets the deploy user control
# exactly these AMR services without a password. Remove this file to revoke.
Cmnd_Alias ADAPTOR_TUI_CTL = $cmds
$DEPLOY_USER ALL=(root) NOPASSWD: ADAPTOR_TUI_CTL"
  echo "==> sudoers drop-in ($file) for user '$DEPLOY_USER'"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "----- $file -----"
    echo "$content"
    echo "-----------------"
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  printf '%s\n' "$content" >"$tmp"
  # Validate before installing; a broken sudoers file can lock out sudo.
  if ! visudo -cf "$tmp" >/dev/null 2>&1; then
    echo "  ERROR: generated sudoers failed validation; not installing." >&2
    rm -f "$tmp"
    return 1
  fi
  run_root install -m 0440 -o root -g root "$tmp" "$file"
  rm -f "$tmp"
}

install_webui_credentials() {
  local file="$ADAPTER_DIR/config/web-credentials.toml"
  echo "==> web-ui credentials ($file)"
  if [[ -f "$file" ]]; then
    echo "  existing credentials file found; leaving it as is."
    return 0
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] would create $file with default user '$WEBUI_DEFAULT_USER'"
    return 0
  fi
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<EOF
username = "$WEBUI_DEFAULT_USER"
password = "$WEBUI_DEFAULT_PASSWORD"
EOF
  chmod 600 "$file"
}

render_webui_polkit() {
  local polkit_units=("amr-adaptor.service" "urobot.service")
  [[ $DO_CAMERA -eq 1 ]] && polkit_units+=("amr-camera.service")

  local managed_units=""
  local unit
  for unit in "${polkit_units[@]}"; do
    managed_units+="            \"$unit\",
"
  done

  local jibot_template_rule='        if (unit) {
            if (unit.indexOf("amr-adaptor@") === 0) {
                if (unit.endsWith(".service")) { isManaged = true; }
            }
        }'

  local rendered
  rendered="$(sed "s|__USER__|$DEPLOY_USER|g" "$SYSTEMD_SRC/amr-webui-polkit.rules")"
  rendered="${rendered/__MANAGED_UNITS__/$managed_units}"
  rendered="${rendered/__JIBOT_TEMPLATE_RULE__/$jibot_template_rule}"
  printf '%s\n' "$rendered"
}

# True (exit 0) when this host's polkit is old enough to need pklocalauthority
# (.pkla) instead of JS rules (.rules). The JS rules.d backend landed in polkit
# 0.106, so a 0.105 host (e.g. the jibot onboard / Ubuntu 16.04) silently
# ignores rules.d/*.rules and must be authorized via a .pkla file instead.
polkit_uses_pkla() {
  local v
  v="$(pkaction --version 2>/dev/null | awk '{print $NF}')"
  [[ -n "$v" ]] || return 1          # unknown/absent -> assume modern polkit (.rules)
  [[ "$v" == "0.106" ]] && return 1  # exactly 0.106 already has JS rules
  # version < 0.106 ? the smaller of {v, 0.106} under version sort is v.
  [[ "$(printf '%s\n0.106\n' "$v" | sort -V | head -n1)" == "$v" ]]
}

# pklocalauthority (.pkla) form of the WebUi authorization for polkit < 0.106.
# Unlike the JS rule, .pkla CANNOT scope per-unit (there is no action.lookup), so
# the systemctl actions are granted to the WebUi user for ANY unit — broader than
# the .rules grant, but acceptable on a single-account onboard. ResultAny=yes is
# the key line: the WebUi runs as a system service with no login session, so
# polkit classifies it as "any", not "active".
render_webui_pkla() {
  cat <<EOF
# Managed by scripts/setup-adaptor-service.sh — lets the AMR WebUi (user
# '$DEPLOY_USER') control the adapter units without interactive auth on polkit
# < 0.106, which ignores rules.d/*.rules. Remove this file to revoke.
[AMR WebUi: manage units (start/stop/restart, enable/disable)]
Identity=unix-user:$DEPLOY_USER
Action=org.freedesktop.systemd1.manage-units;org.freedesktop.systemd1.manage-unit-files
ResultAny=yes
ResultInactive=yes
ResultActive=yes

[AMR WebUi: reboot host]
Identity=unix-user:$DEPLOY_USER
Action=org.freedesktop.login1.reboot;org.freedesktop.login1.reboot-multiple-sessions;org.freedesktop.login1.reboot-ignore-inhibit
ResultAny=yes
ResultInactive=yes
ResultActive=yes
EOF
}

# Install the WebUi polkit authorization in the format this host understands:
# JS .rules for polkit >= 0.106 (the original method, used by the newer AMR
# hosts), pklocalauthority .pkla for older polkit (the jibot onboard). The
# opposite format is removed so the two backends can never disagree.
install_webui_polkit() {
  local rules_path="/etc/polkit-1/rules.d/10-amr-webui.rules"
  local pkla_path="/etc/polkit-1/localauthority/50-local.d/10-amr-webui.pkla"
  local ver tmp
  ver="$(pkaction --version 2>/dev/null | awk '{print $NF}')"

  if polkit_uses_pkla; then
    echo "==> polkit ${ver:-?} (<0.106): pklocalauthority rule ($pkla_path)"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "----- $pkla_path -----"
      render_webui_pkla
      echo "----------------------------------------------------"
      return 0
    fi
    tmp="$(mktemp)"
    render_webui_pkla >"$tmp"
    run_root install -D -m 644 "$tmp" "$pkla_path"
    rm -f "$tmp"
    run_root rm -f "$rules_path"   # drop stale JS rule, if any
  else
    echo "==> polkit ${ver:-?} (>=0.106): JS rule ($rules_path)"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "----- $rules_path -----"
      render_webui_polkit
      echo "-----------------------------------------------------"
      return 0
    fi
    tmp="$(mktemp)"
    render_webui_polkit >"$tmp"
    # -D creates /etc/polkit-1/rules.d/ if it does not exist yet (hosts with no
    # prior polkit JS rules) so the install does not fail.
    run_root install -D -m 644 "$tmp" "$rules_path"
    rm -f "$tmp"
    run_root rm -f "$pkla_path"    # drop stale pkla, if any
  fi
}

install_amr_tmpfiles() {
  # /run/amr-adaptor root for the adapter<->WebUi local IPC (tmpfs). The adapter
  # creates its own <serial>/ subdir at runtime; this just owns the root so a
  # non-root service user can write under /run. systemd RuntimeDirectory is not
  # used (multiple jibot-adapter@<id> instances would clobber the shared root).
  local src="$SYSTEMD_SRC/tmpfiles.d/amr-adaptor.conf"
  local rendered
  rendered="$(sed "s|__USER__|$DEPLOY_USER|g" "$src")"

  # Refuse to install a config that would leave the IPC root unowned or
  # uncreated. An empty/ownerless /etc/tmpfiles.d/amr-adaptor.conf is exactly how
  # the adapter ends up running but unable to mkdir /run/amr-adaptor, which the
  # WebUi surfaces only as "adapter offline -- not delivered".
  if [[ -z "$DEPLOY_USER" ]]; then
    echo "ERROR: DEPLOY_USER is empty; refusing to install an ownerless tmpfiles config." >&2
    exit 1
  fi
  if ! grep -qE '^d[[:space:]]+/run/amr-adaptor[[:space:]]' <<<"$rendered"; then
    echo "ERROR: rendered tmpfiles config is missing the '/run/amr-adaptor' directive." >&2
    echo "       source: $src" >&2
    exit 1
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "----- /etc/tmpfiles.d/amr-adaptor.conf -----"
    printf '%s\n' "$rendered"
    echo "--------------------------------------------"
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  printf '%s\n' "$rendered" >"$tmp"
  run_root install -m 644 "$tmp" /etc/tmpfiles.d/amr-adaptor.conf
  rm -f "$tmp"
  run_root systemd-tmpfiles --create /etc/tmpfiles.d/amr-adaptor.conf

  # Verify the apply actually took, instead of silently continuing. systemd-tmpfiles
  # can exit 0 yet create nothing (e.g. a bad/empty config), and the failure would
  # otherwise stay invisible until the adapter cannot bind its control socket.
  if [[ ! -s /etc/tmpfiles.d/amr-adaptor.conf ]]; then
    echo "ERROR: /etc/tmpfiles.d/amr-adaptor.conf is empty after install." >&2
    exit 1
  fi
  if [[ ! -d /run/amr-adaptor ]]; then
    echo "ERROR: systemd-tmpfiles did not create /run/amr-adaptor." >&2
    echo "       The adapter (User=$DEPLOY_USER) cannot create its IPC dir under /run," >&2
    echo "       so the WebUi would report 'adapter offline'. See the command output above." >&2
    exit 1
  fi
  echo "  /run/amr-adaptor ready: $(ls -ld /run/amr-adaptor)"
}

detect_ros_distro() {
  if [[ -n "$ROS_DISTRO" ]]; then
    echo "$ROS_DISTRO"
    return 0
  fi
  if [[ ! -d /opt/ros ]]; then
    return 1
  fi
  local found=()
  mapfile -t found < <(ls -1 /opt/ros 2>/dev/null || true)
  if [[ "${#found[@]}" -ne 1 ]]; then
    return 1
  fi
  echo "${found[0]}"
}

dpkg_package_installed() {
  local pkg="$1"
  [[ "$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)" == "install ok installed" ]]
}

# Install every .deb staged under a bundled offline directory.
#
# Robots have no internet, so there is no apt fallback: what is not staged in
# the directory cannot be installed. Callers print their own "==> ..." header,
# so this helper emits indented body lines only.
#
#   $1  label named in messages (a package name, or the tool being installed)
#   $2  directory holding the .deb files
#   $3  optional extra hint printed after a dpkg failure
#
# Returns non-zero when nothing was staged or dpkg did not complete, so the
# caller decides whether that is fatal. Under `set -e` every call site must
# terminate the OR-list itself — `|| true` to ignore the status, or
# `|| some_flag=1` to keep it — or setup aborts here.
#
# A non-zero return does NOT mean nothing was installed. `dpkg -i` unpacks a
# package whose Depends are unsatisfied and only refuses to configure it, so
# the binaries are on disk either way. Callers must not treat "the file exists"
# as "the install worked"; gate on this status.
install_bundled_offline_debs() {
  local label="$1" offline_deb_dir="$2" extra_hint="${3:-}"
  local offline_debs=()
  if [[ -d "$offline_deb_dir" ]]; then
    mapfile -t offline_debs < <(find "$offline_deb_dir" -maxdepth 1 -type f -name '*.deb' | sort)
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    if [[ ${#offline_debs[@]} -gt 0 ]]; then
      echo "  [dry-run] would install bundled offline debs from $offline_deb_dir"
    else
      echo "  [dry-run] no bundled offline debs found; $label install would be skipped"
    fi
    return 0
  fi
  if [[ ${#offline_debs[@]} -eq 0 ]]; then
    echo "  WARNING: $label is not installed and no bundled offline debs were found in $offline_deb_dir." >&2
    echo "  Stage $label and its .deb dependencies there, then re-run setup." >&2
    return 1
  fi
  echo "  installing bundled offline debs from $offline_deb_dir ..."
  # --refuse-downgrade: dpkg permits downgrades by default, and every way this
  # directory can end up holding an older .deb than the robot already has ends
  # in a silent rollback that apt cannot undo offline. Two real paths: a stale
  # sibling left next to a newer file (the whole directory goes to one `dpkg -i`
  # and `sort` is lexicographic, so 0.8.8-10 installs before 0.8.8-2), and a
  # robot carrying a newer libusb-1.0-0/libdbus-glib-1-2 from focal-updates,
  # which scripts/fetch-offline-debs.sh never reads. Refusing costs an error
  # message; accepting costs a bricked dependency on an offline machine.
  if ! run_root dpkg -i --refuse-downgrade "${offline_debs[@]}"; then
    echo "  ERROR: bundled offline deb install did not complete; not using apt because this target is offline." >&2
    echo "  Stage missing .deb dependencies in $offline_deb_dir and re-run." >&2
    if [[ -n "$extra_hint" ]]; then
      echo "  $extra_hint" >&2
    fi
    return 1
  fi
  return 0
}

amr_camera_ros_package_available() {
  local ros_ver="$1" setup="$2"
  if [[ "$ros_ver" == "2" ]]; then
    /bin/bash -lc "source '$setup' && ros2 pkg prefix web_video_server >/dev/null"
  else
    /bin/bash -lc "source '$setup' && rospack find web_video_server >/dev/null"
  fi
}

install_amr_camera_service() {
  echo "==> amr-camera.service"
  local distro
  if ! distro="$(detect_ros_distro)"; then
    echo "  no single ROS distro detected under /opt/ros; skipping camera service."
    echo "  set WEB_VIDEO_ROS_DISTRO=<distro> and re-run to force one."
    return 0
  fi
  local setup="/opt/ros/$distro/setup.bash"
  if [[ ! -f "$setup" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  [dry-run] $setup is missing on this host; rendering expected unit anyway."
    else
      echo "  missing $setup; skipping camera service."
      return 0
    fi
  fi

  local pkg="ros-${distro}-web-video-server"
  if command -v dpkg >/dev/null 2>&1 && ! dpkg_package_installed "$pkg"; then
    install_bundled_offline_debs "$pkg" \
      "$REPO_ROOT/scripts/offline-debs/web-video-server" \
      "The JIBOT image may require ffmpeg and related libav packages in that directory." || true
  fi

  local ros_ver="1"
  if [[ -f "$setup" ]]; then
    # ROS setup scripts are not guaranteed to be nounset clean.
    set +eu
    # shellcheck disable=SC1090
    source "$setup"
    ros_ver="${ROS_VERSION:-1}"
    set -eu
  fi

  if [[ $DRY_RUN -eq 0 ]] && ! amr_camera_ros_package_available "$ros_ver" "$setup"; then
    echo "  WARNING: ROS package 'web_video_server' is not available after install; skipping amr-camera.service." >&2
    echo "  Stage ros-${distro}-web-video-server and its bundled offline deb dependencies, then re-run setup." >&2
    return 0
  fi

  local master_env exec_line
  if [[ "$ros_ver" == "2" ]]; then
    master_env="# ROS2 has no master"
    exec_line="/bin/bash -lc 'source $setup && exec ros2 run web_video_server web_video_server --ros-args -p port:=$WEB_VIDEO_PORT -p address:=0.0.0.0'"
  else
    master_env="Environment=ROS_MASTER_URI=http://localhost:11311"
    exec_line="/bin/bash -lc 'source $setup && exec rosrun web_video_server web_video_server _port:=$WEB_VIDEO_PORT _address:=0.0.0.0'"
  fi

  local rendered
  rendered="[Unit]
Description=AMR camera (ROS image topics over HTTP)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$DEPLOY_USER
$master_env
ExecStart=$exec_line
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "----- /etc/systemd/system/amr-camera.service -----"
    printf '%s\n' "$rendered"
    echo "--------------------------------------------------"
    echo "  [dry-run] $SYSTEMCTL_BIN enable amr-camera.service"
    CAMERA_SERVICE_AVAILABLE=1
    return 0
  fi
  printf '%s\n' "$rendered" | run_root tee /etc/systemd/system/amr-camera.service >/dev/null
  run_root "$SYSTEMCTL_BIN" enable amr-camera.service
  CAMERA_SERVICE_AVAILABLE=1
}

# --- 8BitDo Ultimate 2 joystick bridge ---
# The adapter's joystick runtime drives from a virtual gamepad that exists only
# while xboxdrv bridges the receiver. Robots without the receiver have no
# xboxdrv binary, so installing the unit there would only crash-loop; gate on
# the binary the same way the camera unit gates on its ROS package.
#
# The bridge is a fallback for kernels that cannot drive the receiver themselves.
# Where xpad exists it claims the same USB interface first, so xboxdrv would
# either fail to claim it and crash-loop, or take it away and kill the working
# /dev/input/jsN path the runtime prefers. Neither is what an operator running
# plain setup wants, so xpad's presence skips the bridge; --with-xboxdrv forces
# it for a host where xpad is present but known not to bind this receiver.
xpad_driver_available() {
  [[ -d /sys/module/xpad ]] && return 0
  modinfo xpad >/dev/null 2>&1
}

install_amr_xboxdrv_service() {
  # The xpad gate runs first. On a host where the kernel already drives the
  # receiver the bridge is skipped anyway, so there is no reason to push an
  # unused package onto an offline robot. --with-xboxdrv clears this gate and
  # still installs.
  if [[ $DRY_RUN -eq 0 && $FORCE_XBOXDRV -eq 0 ]] && xpad_driver_available; then
    echo "==> amr-xboxdrv.service"
    echo "  WARNING: the xpad driver is present; skipping amr-xboxdrv.service." >&2
    echo "           The kernel already exposes this receiver, and xboxdrv would" >&2
    echo "           fight it for the USB interface. Pass --with-xboxdrv to" >&2
    echo "           install the bridge anyway." >&2
    return 0
  fi
  # No apt here: the robot is offline. Install the .deb bundle that shipped with
  # the deployed scripts/ directory instead. install_unit prints its own header
  # later, so this branch prints one of its own.
  #
  # The helper's exit status is load-bearing, so it is captured rather than
  # discarded with `|| true`. `dpkg -i` on a package with an unsatisfied
  # Depends still *unpacks* it and only refuses to configure it — there are no
  # Pre-Depends here to stop the unpack — so /usr/bin/xboxdrv lands on disk even
  # though dpkg exits non-zero. A plain `command -v xboxdrv` re-check would then
  # find that half-installed binary and happily enable the unit, which starts,
  # dies in the dynamic linker ("libX11.so.6: cannot open shared object file"),
  # and crash-loops every RestartSec=2 on a robot whose dpkg state apt cannot
  # repair offline. A failed bundle install must skip the unit instead.
  # `|| bundle_failed=1` terminates an OR-list, so `set -e` stays quiet.
  local bundle_failed=0
  if ! command -v xboxdrv >/dev/null 2>&1; then
    echo "==> amr-xboxdrv.service (offline deb bundle)"
    install_bundled_offline_debs "xboxdrv" \
      "$REPO_ROOT/scripts/offline-debs/xboxdrv" \
      "Add the missing package to PACKAGES in scripts/fetch-offline-debs.sh and re-run it on the build machine." || bundle_failed=1
  fi
  # Dry-run still renders the unit: install_bundled_offline_debs returns 0 in
  # dry-run, so bundle_failed stays 0 there.
  if [[ $DRY_RUN -eq 0 ]] && { [[ $bundle_failed -eq 1 ]] || ! command -v xboxdrv >/dev/null 2>&1; }; then
    echo "==> amr-xboxdrv.service"
    echo "  WARNING: xboxdrv is not installed; skipping amr-xboxdrv.service." >&2
    echo "           Install it and re-run setup to enable the joystick bridge." >&2
    return 0
  fi
  install_unit "amr-xboxdrv.service"
  # The adapter opens /dev/input/event* as DEPLOY_USER, which needs the input
  # group. Existing processes keep their old groups, so this takes effect on
  # the service restart below.
  if ! run_root usermod -aG input "$DEPLOY_USER"; then
    echo "  WARNING: could not add '$DEPLOY_USER' to the input group;" >&2
    echo "           the adapter may fail to open the virtual gamepad." >&2
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] $SYSTEMCTL_BIN enable amr-xboxdrv.service"
    XBOXDRV_SERVICE_AVAILABLE=1
    return 0
  fi
  run_root "$SYSTEMCTL_BIN" enable amr-xboxdrv.service
  XBOXDRV_SERVICE_AVAILABLE=1
}

echo "Adapter dir: $ADAPTER_DIR"
[[ $DRY_RUN -eq 1 ]] && echo "(dry-run: no changes will be made)"

install_unit "amr-adaptor.service"
install_unit "amr-adaptor@.service"

# 구 이름으로 설치/enable된 유닛을 새 amr-* 유닛과 공존하지 않게 정리한다.
# (정적 단일 유닛, 템플릿, enable된 인스턴스 포함)
migrate_legacy_units() {
  # NOTE: amr-adapter (…ER) is the *misspelled* legacy name of the canonical
  # amr-adaptor (…OR) unit. A host that was set up under the old spelling keeps
  # BOTH enabled, so two identical adapter processes run at once — both subscribe
  # to MQTT (every WebUI instant action handled twice) and both open a UDP socket
  # to the single EZI motor (10.8.8.2:3002). The two clients collide on the motor
  # endpoint and clamp servo_enable times out ("no response from EZI motor"); the
  # audio device races too. Retire the …ER units here. The one-letter difference
  # (adaptER vs adaptOR) means none of these names/globs match the canonical unit.
  local legacy_static=(
    jibot-adapter.service jibot-adapter@.service
    hexplorer-adapter.service web_video_server.service
    amr-adapter.service amr-adapter@.service
  )
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] migrate: stop/disable enabled jibot-adapter@<id> / amr-adapter@<id> instances"
    echo "  [dry-run] migrate: remove ${legacy_static[*]} (if present)"
    return 0
  fi
  # enable/active된 템플릿 인스턴스를 열거한다. systemctl list-* 는 glob 패턴 인자를
  # 받으므로(quoted여도 systemctl이 매칭) 실제 인스턴스 이름을 얻을 수 있다.
  local instances u
  instances="$(
    { "$SYSTEMCTL_BIN" list-unit-files 'jibot-adapter@*.service' --no-legend 2>/dev/null
      "$SYSTEMCTL_BIN" list-units --all 'jibot-adapter@*.service' --no-legend 2>/dev/null
      "$SYSTEMCTL_BIN" list-unit-files 'amr-adapter@*.service' --no-legend 2>/dev/null
      "$SYSTEMCTL_BIN" list-units --all 'amr-adapter@*.service' --no-legend 2>/dev/null
    } | awk '{print $1}' | grep -E '^(jibot-adapter|amr-adapter)@.+\.service$' | sort -u || true
  )"
  for u in $instances "${legacy_static[@]}"; do
    [[ -n "$u" ]] || continue
    run_root "$SYSTEMCTL_BIN" stop "$u" 2>/dev/null || true
    run_root "$SYSTEMCTL_BIN" disable "$u" 2>/dev/null || true
  done
  # 유닛 파일 제거: 정적 이름(템플릿 파일 포함) + 직접 설치된 인스턴스 파일(드묾).
  run_root rm -f \
    /etc/systemd/system/jibot-adapter.service \
    /etc/systemd/system/jibot-adapter@.service \
    /etc/systemd/system/hexplorer-adapter.service \
    /etc/systemd/system/web_video_server.service \
    /etc/systemd/system/amr-adapter.service \
    /etc/systemd/system/amr-adapter@.service
  run_root bash -c 'rm -f /etc/systemd/system/jibot-adapter@*.service' 2>/dev/null || true
  run_root bash -c 'rm -f /etc/systemd/system/amr-adapter@*.service' 2>/dev/null || true
}

echo "==> migrating legacy unit names"
migrate_legacy_units
echo "==> systemctl daemon-reload"
run_root "$SYSTEMCTL_BIN" daemon-reload

configure_journald_logging

# OS sound needs the adapter (a boot-time *system* service) to reach $DEPLOY_USER's
# PulseAudio at /run/user/<uid>/pulse. run-adapter.sh/run-main.sh default
# XDG_RUNTIME_DIR to that path, but /run/user/<uid> only exists while the user has
# a session. enable-linger makes systemd create it (and start the user manager,
# which socket-activates pulseaudio) at boot, before anyone logs in — so audio and
# volume work without an interactive login. Idempotent.
LOGINCTL_BIN="$(command -v loginctl || true)"
if [[ -n "$LOGINCTL_BIN" ]]; then
  echo "==> loginctl enable-linger $DEPLOY_USER (boot-time /run/user/<uid> for PulseAudio)"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] $LOGINCTL_BIN enable-linger $DEPLOY_USER"
  else
    run_root "$LOGINCTL_BIN" enable-linger "$DEPLOY_USER" || \
      echo "  WARNING: enable-linger failed; OS sound may be silent until $DEPLOY_USER logs in."
  fi
else
  echo "==> loginctl not found; skipping enable-linger (OS sound needs $DEPLOY_USER logged in)"
fi

echo "==> /run/amr-adaptor tmpfiles (adapter<->WebUi local IPC root)"
install_amr_tmpfiles

# config.toml [[adapter.instances]] 이름 목록(있으면 한 줄에 하나).
list_adapter_instances() {
  local py="$ADAPTER_DIR/venvJIBOT/bin/python"
  [[ -x "$py" ]] || py="$ADAPTER_DIR/.venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  "$py" - <<'PY' 2>/dev/null || true
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
with open("config/config.toml", "rb") as fh:
    data = tomllib.load(fh)
for inst in data.get("adapter", {}).get("instances", []):
    name = inst.get("name")
    if name:
        print(name)
PY
}

# Enable adapters: config.toml [[adapter.instances]]가 있으면 그 이름들로
# amr-adaptor@<name>.service를 enable. 기본 설치는 robots.hcl 내용과 무관하게
# 단일 amr-adaptor.service만 enable한다.
JIBOT_UNITS=()
ADAPTER_INSTANCES="$(cd "$ADAPTER_DIR" && list_adapter_instances)"
if [[ -n "$ADAPTER_INSTANCES" ]]; then
  while IFS= read -r name; do
    [[ -n "$name" ]] && JIBOT_UNITS+=("amr-adaptor@${name}.service")
  done <<<"$ADAPTER_INSTANCES"
  echo "==> config adapter instances: $(echo "$ADAPTER_INSTANCES" | tr '\n' ' ')"
else
  JIBOT_UNITS+=("amr-adaptor.service")
  echo "==> no config adapter instances; using single amr-adaptor.service"
fi

for unit in ${JIBOT_UNITS[@]+"${JIBOT_UNITS[@]}"}; do
  echo "==> enabling $unit"
  run_root "$SYSTEMCTL_BIN" enable "$unit"
done

# --- AMR WebUi (browser dashboard + control) ---
echo "==> amr-webui.service + polkit authorization"
install_webui_credentials
[[ $DO_CAMERA -eq 1 ]] && install_amr_camera_service
[[ $DO_XBOXDRV -eq 1 ]] && install_amr_xboxdrv_service
install_unit "amr-webui.service"
# Don't let a sudoers validation failure (set -e) abort setup after the exact
# adapter and auxiliary unit selection; service control can still prompt.
if [[ $DO_SUDOERS -eq 1 ]]; then
  install_sudoers || echo "  (continuing without the sudoers rule; service control will prompt for a password)"
fi
install_webui_polkit
# Apply the new authorization now. JS .rules auto-reload via inotify, but
# pklocalauthority (.pkla) on old polkit needs a restart to take effect.
run_root "$SYSTEMCTL_BIN" restart polkit 2>/dev/null \
  || run_root "$SYSTEMCTL_BIN" restart polkitd 2>/dev/null || true
echo "==> systemctl daemon-reload (WebUi/camera units)"
run_root "$SYSTEMCTL_BIN" daemon-reload
echo "  [amr-webui] Authorization installed for user '$DEPLOY_USER' (polkit auto-detected)."
echo "==> enabling amr-webui.service"
run_root "$SYSTEMCTL_BIN" enable amr-webui.service

[[ $DO_VENV -eq 1 ]] && repair_venv

restart_installed_services() {
  if [[ $DO_START -eq 0 ]]; then
    echo "==> service restart skipped (--no-start)"
    return 0
  fi

  echo "==> restarting installed services"
  local unit
  # The bridge leads: restarting it afterwards would drop the adapter's open
  # virtual gamepad and latch manual drive until the sticks return to neutral.
  if [[ $XBOXDRV_SERVICE_AVAILABLE -eq 1 ]]; then
    echo "  restarting amr-xboxdrv.service"
    run_root "$SYSTEMCTL_BIN" restart amr-xboxdrv.service
  fi
  for unit in ${JIBOT_UNITS[@]+"${JIBOT_UNITS[@]}"}; do
    echo "  restarting $unit"
    run_root "$SYSTEMCTL_BIN" restart "$unit"
  done
  echo "  restarting amr-webui.service"
  run_root "$SYSTEMCTL_BIN" restart amr-webui.service
  if [[ $CAMERA_SERVICE_AVAILABLE -eq 1 ]]; then
    echo "  restarting amr-camera.service"
    run_root "$SYSTEMCTL_BIN" restart amr-camera.service
  fi
}

restart_installed_services

START_HINT="sudo systemctl start amr-adaptor.service"
[[ ${#JIBOT_UNITS[@]} -gt 0 ]] && START_HINT="sudo systemctl start ${JIBOT_UNITS[0]}"

cat <<EOF

Done.
Start a service:   $START_HINT
AMR service control: scripts/adaptor-services.sh start|restart|stop|status
(The scoped /etc/sudoers.d/adaptor-tui grant keeps these service operations
 non-interactive; its legacy filename is retained for compatibility.)
EOF
