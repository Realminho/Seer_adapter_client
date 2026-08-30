#!/usr/bin/env bash
# Update the JIBOT adapter's config.toml video settings -- the FMS<->adapter side
# that the network script (change-jibot-network-over-ssh.sh) intentionally does
# NOT touch. Edits this key, preserving comments/formatting:
#
#   [video]   web_video_server_public_url -> FMS-reachable video URL
#
# By default it edits the LOCAL repo file (adaptor/config/config.toml). With
# --host it ALSO edits the robot's remote ~/adapter/config/config.toml over SSH
# (so a running robot can be changed without a full redeploy).
#
# The adapter must be restarted to pick up the new config; pass --restart to do
# that on the remote after editing, or restart it yourself.
set -euo pipefail

# repo root = parent of this script's dir, so it works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  scripts/update-jibot-adapter-config.sh [options]

Edits adaptor/config/config.toml in the local repo by default. With --host it
also edits ~/adapter/config/config.toml on the robot over SSH.

Options (at least one --video-url / --video-host required):
  --vehicle-ip ADDR        Deprecated: robot control IP now lives in config/robots.hcl
  --video-url URL          Set [video].web_video_server_public_url to a full URL
                           (e.g. http://192.168.3.230:8080)
  --video-host HOST[:PORT] Set only the host[:port] of web_video_server_public_url,
                           keeping the existing scheme (and port if HOST has none)

  --host user@robot        Also edit the robot's remote config over SSH
  --remote-dir DIR         Remote adapter dir (default: ~/adapter)
  --restart                After editing the remote config, restart the adapter
                           (uses RESTART_CMD; needs --host)
  --no-local               Skip the local repo file (only meaningful with --host)
  --dry-run                Show diffs without writing anything
  -y, --yes                Skip the confirmation prompt
  -h, --help               Show this help

Environment:
  CONFIG_FILE=adaptor/config/config.toml   Local config path override
  REMOTE_CONFIG=<remote-dir>/config/config.toml   Remote path override
  SSH_PORT=22                              SSH port
  SSH_OPTS="-i ~/.ssh/key"                 Extra ssh options
  RESTART_CMD="pkill -f main.py || true; cd ~/adapter && nohup ./run-main.sh > adapter.log 2>&1 &"

Examples:
  # Local repo only: point the adapter at a new video host
  scripts/update-jibot-adapter-config.sh --video-host 192.168.3.230

  # Local repo AND the running robot, then restart the adapter
  scripts/update-jibot-adapter-config.sh --video-host 192.168.3.230 \
    --host ucore@192.168.3.222 --restart

  # Preview only
  scripts/update-jibot-adapter-config.sh --video-url http://192.168.3.230:8080 --dry-run
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

VEHICLE_IP=""
VIDEO_URL=""
VIDEO_HOST=""
REMOTE_HOST=""
REMOTE_DIR="~/adapter"
DO_RESTART=0
EDIT_LOCAL=1
DRY_RUN=0
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vehicle-ip) VEHICLE_IP="${2:?--vehicle-ip needs a value}"; shift 2 ;;
    --video-url)  VIDEO_URL="${2:?--video-url needs a value}"; shift 2 ;;
    --video-host) VIDEO_HOST="${2:?--video-host needs a value}"; shift 2 ;;
    --host)       REMOTE_HOST="${2:?--host needs a value}"; shift 2 ;;
    --remote-dir) REMOTE_DIR="${2:?--remote-dir needs a value}"; shift 2 ;;
    --restart)    DO_RESTART=1; shift ;;
    --no-local)   EDIT_LOCAL=0; shift ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -y|--yes)     ASSUME_YES=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; echo >&2; usage >&2; exit 2 ;;
  esac
done

SSH_PORT="${SSH_PORT:-22}"
SSH_OPTS="${SSH_OPTS:-}"
LOCAL_CONFIG="${CONFIG_FILE:-$REPO_ROOT/adaptor/config/config.toml}"
RESTART_CMD="${RESTART_CMD:-pkill -f main.py || true; cd ${REMOTE_DIR} && nohup ./run-main.sh > adapter.log 2>&1 &}"

# --- validation -------------------------------------------------------------
if [[ -n "$VEHICLE_IP" ]]; then
  echo "ERROR: --vehicle-ip moved to config/robots.hcl; edit that file instead." >&2
  exit 2
fi
if [[ -z "$VIDEO_URL" && -z "$VIDEO_HOST" ]]; then
  echo "ERROR: nothing to do -- pass --video-url or --video-host" >&2
  echo >&2; usage >&2; exit 2
fi
if [[ -n "$VIDEO_URL" && -n "$VIDEO_HOST" ]]; then
  echo "ERROR: pass only one of --video-url / --video-host" >&2; exit 2
fi

is_ipv4() {
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  local IFS=. o
  for o in $1; do (( o >= 0 && o <= 255 )) || return 1; done
}
if [[ -n "$VEHICLE_IP" ]] && ! is_ipv4 "$VEHICLE_IP"; then
  echo "ERROR: --vehicle-ip is not a valid IPv4 address: $VEHICLE_IP" >&2; exit 2
fi
if [[ -n "$VIDEO_URL" && ! "$VIDEO_URL" =~ ^https?:// ]]; then
  echo "ERROR: --video-url must start with http:// or https:// : $VIDEO_URL" >&2; exit 2
fi
if [[ -n "$REMOTE_HOST" && ! "$REMOTE_HOST" =~ ^[A-Za-z0-9._@-]+$ ]]; then
  echo "ERROR: invalid --host: '$REMOTE_HOST' (expected [user@]host)" >&2; exit 2
fi
if [[ "$DO_RESTART" == 1 && -z "$REMOTE_HOST" ]]; then
  echo "ERROR: --restart needs --host" >&2; exit 2
fi
if [[ "$EDIT_LOCAL" == 0 && -z "$REMOTE_HOST" ]]; then
  echo "ERROR: --no-local needs --host (otherwise there is nothing to edit)" >&2; exit 2
fi
if [[ "$EDIT_LOCAL" == 1 && ! -f "$LOCAL_CONFIG" ]]; then
  echo "ERROR: local config not found: $LOCAL_CONFIG" >&2; exit 2
fi

REMOTE_CONFIG="${REMOTE_CONFIG:-${REMOTE_DIR}/config/config.toml}"

# --- the shared TOML editor (used identically for local and remote) ---------
# Line-oriented + section-aware so comments/formatting and other keys survive.
# Reads CONFIG_FILE and SET_* from the environment.
EDITOR_PY="$(cat <<'PY'
import os, re, sys
from urllib.parse import urlsplit, urlunsplit

path = os.environ['CONFIG_FILE']
set_video_url  = os.environ.get('SET_VIDEO_URL', '')
set_video_host = os.environ.get('SET_VIDEO_HOST', '')

# Read binary and detect the dominant line ending so a CRLF (Windows) config is
# not silently flipped to LF -- that would make the diff show every line instead
# of just the edited one. Work internally in LF, re-apply NL on write.
with open(path, 'rb') as _fh:
    _raw = _fh.read()
_ncrlf = _raw.count(b'\r\n')
NL = '\r\n' if _ncrlf > (_raw.count(b'\n') - _ncrlf) else '\n'
_text = _raw.decode('utf-8').replace('\r\n', '\n')
_had_final = _text.endswith('\n')
lines = [l + '\n' for l in (_text[:-1] if _had_final else _text).split('\n')]

KV = re.compile(r'^(\s*[A-Za-z0-9_]+\s*=\s*)(["\'])(.*?)(\2)(.*)$')

def replace_value(line, newval):
    m = KV.match(line)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2)}{newval}{m.group(2)}{m.group(5)}\n"

section = None
done = {'web_video_server_public_url': False}

for i, l in enumerate(lines):
    hm = re.match(r'^\s*\[([^\]]+)\]', l)
    if hm:
        section = hm.group(1).strip()
        continue
    if section == 'video' and (set_video_url or set_video_host) and \
            re.match(r'^\s*web_video_server_public_url\s*=', l):
        m = KV.match(l)
        if not m:
            sys.exit("could not parse web_video_server_public_url line: %r" % l)
        if set_video_url:
            newval = set_video_url
        else:
            parts = urlsplit(m.group(3))
            if ':' in set_video_host:
                netloc = set_video_host          # host:port given explicitly
            else:
                netloc = set_video_host + (f":{parts.port}" if parts.port else "")
            scheme = parts.scheme or 'http'
            newval = urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))
        lines[i] = replace_value(l, newval)
        done['web_video_server_public_url'] = True

missing = []
if (set_video_url or set_video_host) and not done['web_video_server_public_url']:
    missing.append('web_video_server_public_url in [video]')
if missing:
    sys.exit("ERROR: key(s) not found in %s: %s" % (path, ", ".join(missing)))

_out = ''.join(lines)
if not _had_final and _out.endswith('\n'):
    _out = _out[:-1]
with open(path, 'wb') as _fh:
    _fh.write(_out.replace('\n', NL).encode('utf-8'))
PY
)"

# --- plan -------------------------------------------------------------------
echo "Adapter config changes:"
[[ -n "$VIDEO_URL"  ]] && echo "  [video].web_video_server_public_url   -> $VIDEO_URL"
[[ -n "$VIDEO_HOST" ]] && echo "  [video].web_video_server_public_url   -> host $VIDEO_HOST (scheme/port kept)"
echo "Targets:"
[[ "$EDIT_LOCAL" == 1 ]] && echo "  local : $LOCAL_CONFIG"
[[ -n "$REMOTE_HOST" ]]  && echo "  remote: $REMOTE_HOST:$REMOTE_CONFIG"
[[ "$DRY_RUN" == 1 ]] && echo "  mode  : DRY RUN (diff only)"
[[ "$DO_RESTART" == 1 ]] && echo "  then  : restart adapter on remote"
echo "The actual file diff is shown below and (unless --yes) confirmed before anything is written."
echo

# Pass values to python via env (already validated; simple ASCII).
export SET_VIDEO_URL="$VIDEO_URL" SET_VIDEO_HOST="$VIDEO_HOST"
LOCAL_NEW=""

# --- SSH setup (shared by the diff phase and the write phase) ---------------
if [[ -n "$REMOTE_HOST" ]]; then
  # ControlPath 소켓은 sockaddr_un 한계(macOS 104바이트)에 들어가야 하는데 "%C" 만
  # 40자다. macOS 의 TMPDIR 은 /var/folders/<...>/T/ 라서 그냥 mktemp -d 하면
  # 그것만으로 넘긴다 — 2026-08-26 amr2 로 fetch 시 실측 103바이트에서
  # "ControlPath too long" 로 실패했다. 제어 디렉터리를 /tmp 에 고정해 짧게 유지한다.
  # (선례: scripts/update-jibot-adapter-over-ssh.sh)
  CONTROL_DIR="$(mktemp -d /tmp/amr-ssh.XXXXXX)"
  ssh_base=( ssh -p "$SSH_PORT" -o ControlMaster=auto -o ControlPersist=60 -o "ControlPath=$CONTROL_DIR/%C" )
  if [[ -n "$SSH_OPTS" ]]; then
    # shellcheck disable=SC2206
    ssh_base+=( $SSH_OPTS )
  fi
  REMOTE_TMP="/tmp/edit_adapter_config.$$.py"
  cleanup() {
    [ -n "$LOCAL_NEW" ] && rm -f "$LOCAL_NEW" || :
    "${ssh_base[@]}" "$REMOTE_HOST" "rm -f '$REMOTE_TMP'" >/dev/null 2>&1 || true
    "${ssh_base[@]}" -O exit "$REMOTE_HOST" >/dev/null 2>&1 || true
    rm -rf "$CONTROL_DIR"
  }
  trap cleanup EXIT
  printf '%s\n' "$EDITOR_PY" | "${ssh_base[@]}" "$REMOTE_HOST" "cat > '$REMOTE_TMP'"
else
  cleanup() { [ -n "$LOCAL_NEW" ] && rm -f "$LOCAL_NEW" || :; }
  trap cleanup EXIT
fi

# --- phase 1: stage edits in temp copies and ALWAYS show the diff -----------
CHANGED=0
if [[ "$EDIT_LOCAL" == 1 ]]; then
  LOCAL_NEW="$(mktemp)"; cp -a "$LOCAL_CONFIG" "$LOCAL_NEW"
  CONFIG_FILE="$LOCAL_NEW" python3 -c "$EDITOR_PY"
  echo "===== local diff: $LOCAL_CONFIG ====="
  diff -u "$LOCAL_CONFIG" "$LOCAL_NEW" || true
  cmp -s "$LOCAL_CONFIG" "$LOCAL_NEW" || CHANGED=1
  echo
fi
if [[ -n "$REMOTE_HOST" ]]; then
  echo "===== remote diff: $REMOTE_HOST:$REMOTE_CONFIG ====="
  "${ssh_base[@]}" "$REMOTE_HOST" "
    set -e
    CFG=\"\$(eval echo $REMOTE_CONFIG)\"
    test -f \"\$CFG\" || { echo 'remote config not found: '\"\$CFG\" >&2; exit 1; }
    T=\$(mktemp); cp \"\$CFG\" \"\$T\"
    CONFIG_FILE=\"\$T\" SET_VIDEO_URL='$VIDEO_URL' SET_VIDEO_HOST='$VIDEO_HOST' python3 '$REMOTE_TMP'
    diff -u \"\$CFG\" \"\$T\" || true
    cmp -s \"\$CFG\" \"\$T\" && echo '[remote] (no change)' || true
    rm -f \"\$T\"
  "
  CHANGED=1   # a remote target is in play; let the user decide from the diff
  echo
fi

# --- dry run stops here -----------------------------------------------------
if [[ "$DRY_RUN" == 1 ]]; then
  echo "[update-jibot-adapter-config] dry run; nothing written."
  exit 0
fi

if [[ "$CHANGED" != 1 ]]; then
  echo "[update-jibot-adapter-config] no effective change; nothing written."
  exit 0
fi

# --- mandatory confirmation AFTER the diff, BEFORE any write ----------------
if [[ "$ASSUME_YES" != 1 ]]; then
  read -r -p "Write the above changes? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted; nothing written."; exit 1; }
fi

# --- phase 2: write (always with a timestamped backup) ----------------------
if [[ "$EDIT_LOCAL" == 1 ]]; then
  bak="$LOCAL_CONFIG.bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "$LOCAL_CONFIG" "$bak"
  cat "$LOCAL_NEW" > "$LOCAL_CONFIG"   # keep original perms; swap contents only
  echo "[local] wrote $LOCAL_CONFIG (backup: $bak)"
fi

if [[ -n "$REMOTE_HOST" ]]; then
  "${ssh_base[@]}" "$REMOTE_HOST" "
    set -e
    CFG=\"\$(eval echo $REMOTE_CONFIG)\"
    test -f \"\$CFG\" || { echo 'remote config not found: '\"\$CFG\" >&2; exit 1; }
    BAK=\"\$CFG.bak.\$(date +%Y%m%d-%H%M%S)\"
    cp -a \"\$CFG\" \"\$BAK\"
    CONFIG_FILE=\"\$CFG\" SET_VIDEO_URL='$VIDEO_URL' SET_VIDEO_HOST='$VIDEO_HOST' python3 '$REMOTE_TMP'
    echo \"[remote] wrote \$CFG (backup: \$BAK)\"
  "

  if [[ "$DO_RESTART" == 1 ]]; then
    echo "[remote] restarting adapter ..."
    "${ssh_base[@]}" "$REMOTE_HOST" "$RESTART_CMD" || \
      echo "[remote] restart command returned non-zero; check the adapter manually." >&2
  else
    echo "[remote] NOTE: restart the adapter to apply (or rerun with --restart)."
  fi
fi

echo "[update-jibot-adapter-config] done."
