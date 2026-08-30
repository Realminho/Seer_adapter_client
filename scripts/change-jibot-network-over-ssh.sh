#!/usr/bin/env bash
# Change a JIBOT onboard PC's wlan0 network settings and/or Wi-Fi credentials
# over SSH, then apply. This automates the manual procedure of editing the three
# places involved when a JIBOT's address moves:
#
#   1. /etc/netplan/01-network-manager-all.yaml  -> wlan0 `addresses` + gateway
#   2. /etc/wpa_supplicant/wpa_supplicant.conf    -> Wi-Fi `ssid` + `psk`
#   3. apply: update the active NetworkManager Wi-Fi profile -> netplan apply
#             -> restart NetworkManager/reconnect wlan0 -> restart urobot.service
#
# The SSH TARGET (the robot's CURRENT address) is always required as the first
# argument, and is intentionally SEPARATE from --ip (the NEW address). The robot
# at 192.168.3.222/.223 is reached over wlan0 (the FMS LAN), which is the very
# interface whose IP we change -- so the apply step is run DETACHED on the robot
# (via systemd-run / setsid) so it survives the SSH drop.
#
# >>> YOUR SSH SESSION WILL DROP when the new IP takes effect. Reconnect at the
# >>> new IP afterwards. If the new config is broken the robot will not come back
# >>> on the network; recover at its console (a timestamped .bak of each edited
# >>> file is left next to the original).
#
# File contents/edits are uploaded as a script and run with sudo over an
# interactive SSH session (a tty) so sudo can prompt for the password. SSH
# connection sharing requests the password once.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/change-jibot-network-over-ssh.sh <user@target-host> [options]

The target host is the robot's CURRENT address (always required, so .222 vs .223
are never confused). The new address is given with --ip.

Options (at least one of the IP group or the Wi-Fi group is required):
  IP group (edits netplan wlan0):
    --ip ADDR[/PREFIX]   New wlan0 IPv4 address. PREFIX (CIDR, e.g. 24) may be
                         given inline or via --prefix.
    --prefix N           Subnet prefix length (default 24; ignored if --ip has /N)
    --gateway ADDR       New default gateway for wlan0
  Wi-Fi group (edits wpa_supplicant):
    --ssid NAME          New Wi-Fi SSID
    --psk PASSWORD       New Wi-Fi passphrase
    --wifi-select        Scan remote wlan0 with `iw`, choose an SSID from a list,
                         then enter the passphrase interactively

  --no-apply             Edit files only; do NOT apply/restart network services
  --dry-run              Show what would change (diff) without writing or applying
  -y, --yes              Skip the confirmation prompt
  -h, --help             Show this help

Environment:
  SSH_PORT=22                       SSH port override
  SSH_OPTS="-i ~/.ssh/key"          Extra ssh options
  NETPLAN_FILE=/etc/netplan/01-network-manager-all.yaml
  WPA_FILE=/etc/wpa_supplicant/wpa_supplicant.conf
  UROBOT_SERVICE=urobot.service

Examples:
  # Move .222 robot to .230, new gateway, keep Wi-Fi:
  scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 \
    --ip 192.168.3.230/24 --gateway 192.168.3.1

  # Also switch Wi-Fi network:
  scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 \
    --ip 192.168.3.230/24 --gateway 192.168.3.1 --ssid NEW_AP --psk 'secret-pass'

  # Preview only:
  scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 --ip 192.168.3.230 --dry-run
USAGE
}

if [[ $# -lt 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 2
fi

REMOTE_HOST="$1"; shift

NEW_IP=""
NEW_PREFIX=""
NEW_GW=""
NEW_SSID=""
NEW_PSK=""
WIFI_SELECT=0
DO_APPLY=1
DRY_RUN=0
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ip)      NEW_IP="${2:?--ip needs a value}"; shift 2 ;;
    --prefix)  NEW_PREFIX="${2:?--prefix needs a value}"; shift 2 ;;
    --gateway) NEW_GW="${2:?--gateway needs a value}"; shift 2 ;;
    --ssid)    NEW_SSID="${2:?--ssid needs a value}"; shift 2 ;;
    --psk)     NEW_PSK="${2:?--psk needs a value}"; shift 2 ;;
    --wifi-select) WIFI_SELECT=1; shift ;;
    --no-apply) DO_APPLY=0; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -y|--yes)   ASSUME_YES=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; echo >&2; usage >&2; exit 2 ;;
  esac
done

SSH_PORT="${SSH_PORT:-22}"
SSH_OPTS="${SSH_OPTS:-}"
NETPLAN_FILE="${NETPLAN_FILE:-/etc/netplan/01-network-manager-all.yaml}"
WPA_FILE="${WPA_FILE:-/etc/wpa_supplicant/wpa_supplicant.conf}"
UROBOT_SERVICE="${UROBOT_SERVICE:-urobot.service}"

# --- validate the SSH target early (reject copy-paste artifacts) ------------
if [[ ! "$REMOTE_HOST" =~ ^[A-Za-z0-9._@-]+$ ]]; then
  echo "ERROR: invalid target host: '$REMOTE_HOST' (expected [user@]host)" >&2
  exit 2
fi

# --- SSH plumbing: connection sharing so the password is asked once ---------
# ControlPath 소켓은 sockaddr_un 한계(macOS 104바이트)에 들어가야 하는데 "%C" 만
# 40자다. macOS 의 TMPDIR 은 /var/folders/<...>/T/ 라서 그냥 mktemp -d 하면
# 그것만으로 넘긴다 — 2026-08-26 amr2 로 fetch 시 실측 103바이트에서
# "ControlPath too long" 로 실패했다. 제어 디렉터리를 /tmp 에 고정해 짧게 유지한다.
# (선례: scripts/update-jibot-adapter-over-ssh.sh)
CONTROL_DIR="$(mktemp -d /tmp/amr-ssh.XXXXXX)"
ssh_base=(
  ssh
  -p "$SSH_PORT"
  -o ControlMaster=auto
  -o ControlPersist=60
  -o "ControlPath=$CONTROL_DIR/%C"
)
if [[ -n "$SSH_OPTS" ]]; then
  # shellcheck disable=SC2206
  ssh_base+=( $SSH_OPTS )
fi
cleanup() {
  "${ssh_base[@]}" -O exit "$REMOTE_HOST" >/dev/null 2>&1 || true
  rm -rf "$CONTROL_DIR"
}
trap cleanup EXIT

scan_and_select_wifi() {
  if [[ -n "$NEW_SSID" || -n "$NEW_PSK" ]]; then
    echo "ERROR: --wifi-select cannot be combined with --ssid or --psk" >&2
    exit 2
  fi

  echo "Scanning Wi-Fi networks on $REMOTE_HOST wlan0..."
  echo "sudo may ask for the robot password."
  local scan_output
  if ! scan_output="$("${ssh_base[@]}" -t "$REMOTE_HOST" \
    "if [ -t 0 ]; then stty sane 2>/dev/null || true; fi; sudo iw dev wlan0 scan" 2>/dev/null | tr -d '\r')"; then
    echo "ERROR: failed to scan Wi-Fi networks with: sudo iw dev wlan0 scan" >&2
    exit 1
  fi

  local -a rows=()
  mapfile -t rows < <(
    printf '%s\n' "$scan_output" \
      | awk '
          /^[[:space:]]*signal:/ { signal = $2 }
          /^[[:space:]]*SSID:/ {
            ssid = $0
            sub(/^[[:space:]]*SSID:[[:space:]]*/, "", ssid)
            if (ssid != "") print signal "\t" ssid
          }
        ' \
      | sort -t $'\t' -k1,1nr \
      | awk -F '\t' '!seen[$2]++ { print $1 "\t" $2 }'
  )

  if [[ "${#rows[@]}" -eq 0 ]]; then
    echo "ERROR: no visible SSIDs found on wlan0" >&2
    exit 1
  fi

  echo "Available Wi-Fi networks:"
  local i row signal ssid
  for i in "${!rows[@]}"; do
    row="${rows[$i]}"
    signal="${row%%$'\t'*}"
    ssid="${row#*$'\t'}"
    printf '  %d) %s (%s dBm)\n' "$((i + 1))" "$ssid" "$signal"
  done

  local choice
  printf 'Select Wi-Fi network [1-%d]: ' "${#rows[@]}"
  read -r choice
  if [[ ! "$choice" =~ ^[0-9]+$ || "$choice" -lt 1 || "$choice" -gt "${#rows[@]}" ]]; then
    echo "ERROR: invalid Wi-Fi selection: $choice" >&2
    exit 2
  fi

  row="${rows[$((choice - 1))]}"
  NEW_SSID="${row#*$'\t'}"
  printf 'Passphrase for "%s": ' "$NEW_SSID"
  IFS= read -rs NEW_PSK
  printf '\n'
  if [[ -z "$NEW_PSK" ]]; then
    echo "ERROR: Wi-Fi passphrase cannot be empty" >&2
    exit 2
  fi
}

if [[ "$WIFI_SELECT" == 1 ]]; then
  scan_and_select_wifi
fi

# --- split an inline prefix out of --ip (e.g. 192.168.3.230/24) -------------
if [[ "$NEW_IP" == */* ]]; then
  NEW_PREFIX="${NEW_IP#*/}"
  NEW_IP="${NEW_IP%%/*}"
fi
[[ -n "$NEW_IP" && -z "$NEW_PREFIX" ]] && NEW_PREFIX="24"

# --- basic value validation -------------------------------------------------
is_ipv4() {
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  local IFS=. o
  for o in $1; do
    (( o >= 0 && o <= 255 )) || return 1
  done
}
EDIT_NETPLAN=0
EDIT_WPA=0

if [[ -n "$NEW_IP" || -n "$NEW_GW" ]]; then
  EDIT_NETPLAN=1
  [[ -n "$NEW_IP" ]] || { echo "ERROR: --gateway also needs --ip" >&2; exit 2; }
  is_ipv4 "$NEW_IP" || { echo "ERROR: --ip is not a valid IPv4 address: $NEW_IP" >&2; exit 2; }
  [[ "$NEW_PREFIX" =~ ^[0-9]+$ && "$NEW_PREFIX" -ge 1 && "$NEW_PREFIX" -le 32 ]] \
    || { echo "ERROR: --prefix must be 1..32: $NEW_PREFIX" >&2; exit 2; }
  if [[ -n "$NEW_GW" ]]; then
    is_ipv4 "$NEW_GW" || { echo "ERROR: --gateway is not a valid IPv4 address: $NEW_GW" >&2; exit 2; }
    [[ "$NEW_GW" != "$NEW_IP" ]] \
      || { echo "ERROR: --gateway must not be the same as the robot's --ip ($NEW_IP)" >&2; exit 2; }
  fi
fi

if [[ -n "$NEW_SSID" || -n "$NEW_PSK" ]]; then
  EDIT_WPA=1
fi

if [[ "$EDIT_NETPLAN" == 0 && "$EDIT_WPA" == 0 ]]; then
  echo "ERROR: nothing to do -- pass --ip/--gateway and/or --ssid/--psk" >&2
  echo >&2
  usage >&2
  exit 2
fi

# --- print a plan and confirm -----------------------------------------------
echo "Target robot (current SSH host): $REMOTE_HOST"
echo "Changes to apply on the robot:"
if [[ "$EDIT_NETPLAN" == 1 ]]; then
  echo "  netplan ($NETPLAN_FILE) wlan0:"
  echo "    addresses -> ${NEW_IP}/${NEW_PREFIX}"
  [[ -n "$NEW_GW" ]] && echo "    gateway   -> ${NEW_GW}"
fi
if [[ "$EDIT_WPA" == 1 ]]; then
  echo "  wpa_supplicant ($WPA_FILE):"
  [[ -n "$NEW_SSID" ]] && echo "    ssid -> ${NEW_SSID}"
  [[ -n "$NEW_PSK" ]]  && echo "    psk  -> (hidden)"
fi
if [[ "$DRY_RUN" == 1 ]]; then
  echo "  mode: DRY RUN (show diff only, no write, no apply)"
elif [[ "$DO_APPLY" == 1 ]]; then
  [[ "$EDIT_WPA" == 1 ]] && echo "  then: update active NetworkManager wlan0 profile"
  echo "        -> netplan generate -> netplan apply -> restart NetworkManager"
  [[ "$EDIT_WPA" == 1 ]] && echo "        -> reconnect wlan0"
  echo "        -> restart ${UROBOT_SERVICE} (detached)"
  if [[ "$EDIT_NETPLAN" == 1 ]]; then
    echo "  NOTE: this SSH session will drop when the IP changes; reconnect at $NEW_IP."
  elif [[ "$EDIT_WPA" == 1 ]]; then
    echo "  NOTE: this SSH session may drop while wlan0 reconnects."
  fi
else
  echo "  then: files edited only (--no-apply); apply manually at the robot."
fi
echo "The actual file diff is shown below and (unless --yes) confirmed before anything is written."
echo

# --- base64-encode arbitrary values so robot-side shell quoting is never an
#     issue (Wi-Fi passphrases especially can contain shell metacharacters) --
b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
NEW_IP_B64="$(b64 "$NEW_IP")"
NEW_GW_B64="$(b64 "$NEW_GW")"
NEW_SSID_B64="$(b64 "$NEW_SSID")"
NEW_PSK_B64="$(b64 "$NEW_PSK")"

# --- the remote worker: edits files, validates, applies detached ------------
# Reads its inputs from env. Edits are done by python3 (present on the robot;
# netplan itself depends on python3-yaml) so the YAML/conf structure -- comments,
# indentation, other interfaces -- is preserved instead of blindly rewritten.
REMOTE_SCRIPT="$(cat <<'REMOTE'
set -euo pipefail

b64d() { printf '%s' "${1:-}" | base64 -d 2>/dev/null || true; }
NEW_IP="$(b64d "${NEW_IP_B64:-}")"
NEW_GW="$(b64d "${NEW_GW_B64:-}")"
NEW_SSID="$(b64d "${NEW_SSID_B64:-}")"
NEW_PSK="$(b64d "${NEW_PSK_B64:-}")"
: "${NEW_PREFIX:=24}"
: "${NETPLAN_FILE:?}"; : "${WPA_FILE:?}"; : "${UROBOT_SERVICE:?}"
: "${EDIT_NETPLAN:=0}"; : "${EDIT_WPA:=0}"; : "${DRY_RUN:=0}"; : "${DO_APPLY:=1}"; : "${ASSUME_YES:=0}"

command -v python3 >/dev/null 2>&1 || { echo "[remote] ERROR: python3 not found" >&2; exit 1; }
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUPS=()
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# python editor for the netplan wlan0 block (line-oriented; preserves the rest).
NETPLAN_PY="$WORKDIR/edit_netplan.py"
cat > "$NETPLAN_PY" <<'PY'
import os, re, sys
path = os.environ['NETPLAN_FILE']
ip   = os.environ['NEW_IP']
pfx  = os.environ['NEW_PREFIX']
gw   = os.environ.get('NEW_GW', '')
addr = f"{ip}/{pfx}"

# Read in binary and detect the dominant line ending so we can preserve it.
# Working internally in LF and re-applying NL on write keeps a CRLF (Windows)
# file from being silently flipped to LF -- which would make the diff show the
# whole file instead of just the one edited line.
with open(path, 'rb') as _fh:
    _raw = _fh.read()
_ncrlf = _raw.count(b'\r\n')
NL = '\r\n' if _ncrlf > (_raw.count(b'\n') - _ncrlf) else '\n'
_text = _raw.decode('utf-8').replace('\r\n', '\n')
_had_final = _text.endswith('\n')
lines = [l + '\n' for l in (_text[:-1] if _had_final else _text).split('\n')]

def indent_of(s):
    return len(s) - len(s.lstrip(' '))

# locate the `wlan0:` stanza and its block (lines indented deeper than it)
start = None
w_ind = None
for i, l in enumerate(lines):
    m = re.match(r'^(\s*)wlan0:\s*$', l)
    if m:
        start, w_ind = i, len(m.group(1))
        break
if start is None:
    sys.exit("wlan0: stanza not found in %s" % path)

end = len(lines)
for j in range(start + 1, len(lines)):
    if lines[j].strip() == '':
        continue
    if indent_of(lines[j]) <= w_ind:
        end = j
        break
block = lines[start:end]

def find_key(blk, key):
    for k, l in enumerate(blk):
        if re.match(r'^\s*%s:' % re.escape(key), l):
            return k
    return None

# --- addresses: collapse to an inline list at the existing key indent -------
ai = find_key(block, 'addresses')
if ai is None:
    # no addresses yet: add one just under the wlan0: line
    child_ind = w_ind + 2
    block.insert(1, ' ' * child_ind + 'addresses: [%s]\n' % addr)
else:
    key_ind = indent_of(block[ai])
    inline = '[' in block[ai]
    new_block = block[:ai]
    new_block.append(' ' * key_ind + 'addresses: [%s]\n' % addr)
    rest = ai + 1
    if not inline:  # skip the old "- x.x.x.x/nn" child lines
        while rest < len(block):
            s = block[rest]
            if s.strip() == '' or indent_of(s) > key_ind:
                rest += 1
            else:
                break
    new_block += block[rest:]
    block = new_block

# --- gateway: update whichever form already exists; else add routes default -
if gw:
    gi = find_key(block, 'gateway4')
    via_i = None
    for k, l in enumerate(block):
        if re.match(r'^\s*via:\s', l):
            via_i = k
            break
    gp = find_key(block, 'gateway')  # legacy plain key
    if gi is not None:
        ki = indent_of(block[gi]); block[gi] = ' ' * ki + 'gateway4: %s\n' % gw
    elif via_i is not None:
        ki = indent_of(block[via_i]); block[via_i] = ' ' * ki + 'via: %s\n' % gw
    elif gp is not None and find_key(block, 'gateway4') is None:
        ki = indent_of(block[gp]); block[gp] = ' ' * ki + 'gateway: %s\n' % gw
    else:
        # insert a modern default route right after the addresses line
        ai2 = find_key(block, 'addresses')
        ki = indent_of(block[ai2])
        block[ai2 + 1:ai2 + 1] = [
            ' ' * ki + 'routes:\n',
            ' ' * (ki + 2) + '- to: default\n',
            ' ' * (ki + 4) + 'via: %s\n' % gw,
        ]

_out = ''.join(lines[:start] + block + lines[end:])
if not _had_final and _out.endswith('\n'):
    _out = _out[:-1]
with open(path, 'wb') as _fh:
    _fh.write(_out.replace('\n', NL).encode('utf-8'))
PY

# python editor for wpa_supplicant ssid/psk (first network block).
WPA_PY="$WORKDIR/edit_wpa.py"
cat > "$WPA_PY" <<'PY'
import os, re
path = os.environ['WPA_FILE']
ssid = os.environ.get('NEW_SSID', '')
psk  = os.environ.get('NEW_PSK', '')
# Preserve the file's line ending (see NETPLAN_PY): read binary, work in LF.
with open(path, 'rb') as _fh:
    _raw = _fh.read()
_ncrlf = _raw.count(b'\r\n')
NL = '\r\n' if _ncrlf > (_raw.count(b'\n') - _ncrlf) else '\n'
txt = _raw.decode('utf-8').replace('\r\n', '\n')
if ssid:
    if re.search(r'(?m)^\s*ssid=', txt):
        txt = re.sub(r'(?m)^(\s*ssid=).*$', lambda m: m.group(1) + '"%s"' % ssid, txt, count=1)
    else:
        raise SystemExit("ssid= line not found in %s" % path)
if psk:
    if re.search(r'(?m)^\s*psk=', txt):
        txt = re.sub(r'(?m)^(\s*psk=).*$', lambda m: m.group(1) + '"%s"' % psk, txt, count=1)
    else:
        raise SystemExit("psk= line not found in %s" % path)
with open(path, 'wb') as _fh:
    _fh.write(txt.replace('\n', NL).encode('utf-8'))
PY

restore_all() {
  for f in "${BACKUPS[@]}"; do
    [[ -f "$f.bak.$STAMP" ]] && cp -a "$f.bak.$STAMP" "$f"
  done
}

# ---------------------------------------------------------------------------
# Stage the edits in a scratch dir and ALWAYS show the diff first. Nothing
# touches the real files until the diff has been shown and (unless --yes)
# confirmed. Only the wlan0 stanza of netplan and the ssid/psk of
# wpa_supplicant are changed; every other interface/line is left untouched.
# ---------------------------------------------------------------------------
CHANGED=0
NETPLAN_CHANGED=0
WPA_CHANGED=0
if [[ "$EDIT_NETPLAN" == 1 ]]; then
  test -f "$NETPLAN_FILE" || { echo "[remote] ERROR: not found: $NETPLAN_FILE" >&2; exit 1; }
  cp -a "$NETPLAN_FILE" "$WORKDIR/netplan.new"
  NETPLAN_FILE="$WORKDIR/netplan.new" NEW_IP="$NEW_IP" NEW_PREFIX="$NEW_PREFIX" NEW_GW="$NEW_GW" python3 "$NETPLAN_PY"
  echo "===== proposed change: $NETPLAN_FILE (wlan0 only) ====="
  diff -u "$NETPLAN_FILE" "$WORKDIR/netplan.new" || true
  if ! cmp -s "$NETPLAN_FILE" "$WORKDIR/netplan.new"; then
    NETPLAN_CHANGED=1
    CHANGED=1
  fi
  echo
fi
if [[ "$EDIT_WPA" == 1 ]]; then
  test -f "$WPA_FILE" || { echo "[remote] ERROR: not found: $WPA_FILE" >&2; exit 1; }
  cp -a "$WPA_FILE" "$WORKDIR/wpa.new"
  WPA_FILE="$WORKDIR/wpa.new" NEW_SSID="$NEW_SSID" NEW_PSK="$NEW_PSK" python3 "$WPA_PY"
  echo "===== proposed change: $WPA_FILE (ssid/psk only, psk hidden) ====="
  diff -u "$WPA_FILE" "$WORKDIR/wpa.new" | sed -E 's/(psk=).*/\1<hidden>/' || true
  if ! cmp -s "$WPA_FILE" "$WORKDIR/wpa.new"; then
    WPA_CHANGED=1
    CHANGED=1
  fi
  echo
fi

if [[ "$DRY_RUN" == 1 ]]; then
  echo "[remote] dry run -- diff shown above; nothing written or applied."
  exit 0
fi

if [[ "$CHANGED" != 1 ]]; then
  if [[ "$EDIT_WPA" == 1 ]]; then
    echo "[remote] wpa_supplicant file already matches; continuing to synchronize the NetworkManager profile."
  else
    echo "[remote] no effective change (new values already match current); nothing written or applied."
    exit 0
  fi
fi

# Mandatory confirmation AFTER the diff, BEFORE any write.
if [[ "$ASSUME_YES" != 1 ]]; then
  printf '[remote] Write the above changes and apply? [y/N] '
  read -r ans </dev/tty 2>/dev/null || read -r ans || ans=""
  case "$ans" in
    y|Y|yes|YES) ;;
    *) echo "[remote] aborted by user; nothing written, nothing applied."; exit 1 ;;
  esac
fi

# Always back up, then write. `cat > file` keeps the original owner/permissions
# of the /etc file (a plain cp/mv would not) and only swaps the contents.
if [[ "$NETPLAN_CHANGED" == 1 ]]; then
  cp -a "$NETPLAN_FILE" "$NETPLAN_FILE.bak.$STAMP"; BACKUPS+=("$NETPLAN_FILE")
  cat "$WORKDIR/netplan.new" > "$NETPLAN_FILE"
  echo "[remote] wrote $NETPLAN_FILE (backup: $NETPLAN_FILE.bak.$STAMP)"
fi
if [[ "$WPA_CHANGED" == 1 ]]; then
  cp -a "$WPA_FILE" "$WPA_FILE.bak.$STAMP"; BACKUPS+=("$WPA_FILE")
  cat "$WORKDIR/wpa.new" > "$WPA_FILE"
  echo "[remote] wrote $WPA_FILE (backup: $WPA_FILE.bak.$STAMP)"
fi

# Validate netplan syntax BEFORE applying; restore + abort if it is broken so a
# typo never bricks the robot's network.
if [[ "$EDIT_NETPLAN" == 1 ]]; then
  if ! netplan generate 2>"$WORKDIR/netplan.err"; then
    echo "[remote] ERROR: 'netplan generate' rejected the new config:" >&2
    cat "$WORKDIR/netplan.err" >&2
    echo "[remote] restoring backups and aborting (nothing applied)." >&2
    restore_all
    netplan generate >/dev/null 2>&1 || true
    exit 1
  fi
  echo "[remote] netplan generate OK"
fi

if [[ "$DO_APPLY" != 1 ]]; then
  echo "[remote] --no-apply: files edited and validated, nothing applied."
  echo "[remote] apply manually: sudo netplan apply"
  echo "[remote]                 sudo systemctl restart NetworkManager.service"
  if [[ "$EDIT_WPA" == 1 ]]; then
    echo "[remote]                 update/activate the wlan0 NetworkManager connection with nmcli"
  fi
  echo "[remote]                 sudo systemctl restart $UROBOT_SERVICE"
  exit 0
fi

# Apply DETACHED: changing wlan0's IP drops this SSH session, so the apply must
# outlive it. The standalone script also records every step in APPLY_LOG, which
# remains available after reconnecting even when systemd-run is used.
APPLY_LOG="/tmp/jibot-netchange.$STAMP.log"
APPLY_SCRIPT="/tmp/jibot-netchange.$STAMP.apply.sh"
WIFI_SECRETS="/tmp/jibot-netchange.$STAMP.wifi"
printf 'SSID_B64=%s\nPSK_B64=%s\n' \
  "${NEW_SSID_B64:-}" "${NEW_PSK_B64:-}" >"$WIFI_SECRETS"
chmod 600 "$WIFI_SECRETS"
cat >"$APPLY_SCRIPT" <<'APPLY'
#!/usr/bin/env bash
set -euo pipefail
UROBOT_SERVICE="$1"
EDIT_WPA="$2"
APPLY_LOG="$3"
WIFI_SECRETS="$4"
trap 'rc=$?; echo "[apply] finished with status $rc"; rm -f -- "$0" "$WIFI_SECRETS"; exit "$rc"' EXIT
exec >>"$APPLY_LOG" 2>&1

NM_WIFI_CONNECTION=""
NM_WIFI_UUID=""
TARGET_SSID=""
if [[ "$EDIT_WPA" == 1 ]]; then
  # The deployed JIBOT uses a NetworkManager connection profile for wlan0.
  # Merely editing wpa_supplicant.conf does not change that profile.
  # shellcheck disable=SC1090
  source "$WIFI_SECRETS"
  NEW_SSID="$(printf '%s' "$SSID_B64" | base64 -d)"
  NEW_PSK="$(printf '%s' "$PSK_B64" | base64 -d)"

  if systemctl is-active --quiet NetworkManager.service && command -v nmcli >/dev/null 2>&1; then
    NM_WIFI_CONNECTION="$(nmcli -g GENERAL.CONNECTION device show wlan0 | head -n1)"
    if [[ -z "$NM_WIFI_CONNECTION" || "$NM_WIFI_CONNECTION" == "--" ]]; then
      echo "[apply] ERROR: NetworkManager has no active wlan0 connection" >&2
      exit 1
    fi
    NM_WIFI_UUID="$(nmcli -g UUID connection show "$NM_WIFI_CONNECTION" | head -n1)"
    if [[ -z "$NM_WIFI_UUID" ]]; then
      echo "[apply] ERROR: cannot resolve UUID for NetworkManager connection: $NM_WIFI_CONNECTION" >&2
      exit 1
    fi
    echo "[apply] update NetworkManager wlan0 connection: $NM_WIFI_CONNECTION"
    NMCLI_MODIFY=(connection modify uuid "$NM_WIFI_UUID")
    if [[ -n "$NEW_SSID" ]]; then
      NMCLI_MODIFY+=(
        connection.id "$NEW_SSID"
        802-11-wireless.ssid "$NEW_SSID"
      )
      NM_WIFI_CONNECTION="$NEW_SSID"
    fi
    if [[ -n "$NEW_PSK" ]]; then
      NMCLI_MODIFY+=(
        802-11-wireless-security.key-mgmt wpa-psk
        802-11-wireless-security.psk "$NEW_PSK"
      )
    fi
    NMCLI_MODIFY+=(connection.autoconnect yes)
    nmcli "${NMCLI_MODIFY[@]}"
    TARGET_SSID="$(nmcli -g 802-11-wireless.ssid connection show uuid "$NM_WIFI_UUID")"
  else
    echo "[apply] NetworkManager is not managing wlan0; wpa_supplicant.conf will be reloaded"
  fi
  unset NEW_PSK PSK_B64
fi

echo "[apply] netplan apply"
netplan apply

echo "[apply] restart NetworkManager.service"
systemctl restart NetworkManager.service

if [[ "$EDIT_WPA" == 1 ]]; then
  if [[ -n "$NM_WIFI_CONNECTION" ]]; then
    echo "[apply] activate NetworkManager connection on wlan0: $NM_WIFI_CONNECTION ($NM_WIFI_UUID)"
    if ! nmcli connection up uuid "$NM_WIFI_UUID" ifname wlan0; then
      # Some deployed images also run a standalone wpa_supplicant bound to
      # wlan0. NetworkManager may report the device unavailable even though
      # that process has already associated using the updated config.
      ACTUAL_SSID=""
      for _attempt in {1..10}; do
        ACTUAL_SSID="$(iwgetid wlan0 -r 2>/dev/null || true)"
        [[ "$ACTUAL_SSID" == "$TARGET_SSID" ]] && break
        sleep 1
      done
      if [[ -n "$TARGET_SSID" && "$ACTUAL_SSID" == "$TARGET_SSID" ]]; then
        echo "[apply] WARNING: nmcli activation failed, but wlan0 is connected to target SSID: $ACTUAL_SSID"
      else
        echo "[apply] ERROR: wlan0 did not connect to target SSID '$TARGET_SSID' (actual: '${ACTUAL_SSID:-none}')" >&2
        exit 1
      fi
    fi
  elif systemctl is-active --quiet wpa_supplicant.service; then
    echo "[apply] restart wpa_supplicant.service"
    systemctl restart wpa_supplicant.service
  elif command -v wpa_cli >/dev/null 2>&1; then
    echo "[apply] run wpa_cli reconfigure"
    wpa_cli -i wlan0 reconfigure
  else
    echo "[apply] ERROR: cannot reload Wi-Fi: no active manager found" >&2
    exit 1
  fi
fi

echo "[apply] restart $UROBOT_SERVICE"
systemctl restart "$UROBOT_SERVICE"
APPLY
chmod 700 "$APPLY_SCRIPT"

echo "[remote] scheduling detached apply -> netplan + NetworkManager + Wi-Fi + $UROBOT_SERVICE"
echo "[remote] apply log on robot: $APPLY_LOG"
if command -v systemd-run >/dev/null 2>&1; then
  systemd-run --collect --on-active=3 --unit="jibot-netchange-$STAMP" \
    /bin/bash "$APPLY_SCRIPT" "$UROBOT_SERVICE" "$EDIT_WPA" "$APPLY_LOG" "$WIFI_SECRETS" >/dev/null
else
  setsid /bin/bash -c 'sleep 3; exec "$@"' _ \
    /bin/bash "$APPLY_SCRIPT" "$UROBOT_SERVICE" "$EDIT_WPA" "$APPLY_LOG" "$WIFI_SECRETS" \
    </dev/null >/dev/null 2>&1 &
fi
echo "[remote] done. The robot will move to its new address shortly."
REMOTE
)"

REMOTE_TMP="/tmp/change_jibot_network.$$.sh"

# Step 1: upload the worker script (no tty needed; stdin is the script text).
printf '%s\n' "$REMOTE_SCRIPT" | "${ssh_base[@]}" "$REMOTE_HOST" "cat > '$REMOTE_TMP'"

# Step 2: run it with sudo over an interactive tty so sudo can prompt, the diff
# can be shown, and the y/N confirmation can be read. `sudo env` forwards our
# parameters past sudo's env-stripping. The leading `stty sane` undoes the raw
# termios that connection sharing copies onto the remote pty (-onlcr makes the
# output stair-step, -icanon/-echo break both prompts).
set +e
"${ssh_base[@]}" -t "$REMOTE_HOST" \
  "if [ -t 0 ]; then stty sane 2>/dev/null || true; fi; \
   sudo env \
    NEW_IP_B64='$NEW_IP_B64' NEW_PREFIX='$NEW_PREFIX' NEW_GW_B64='$NEW_GW_B64' \
    NEW_SSID_B64='$NEW_SSID_B64' NEW_PSK_B64='$NEW_PSK_B64' \
    NETPLAN_FILE='$NETPLAN_FILE' WPA_FILE='$WPA_FILE' UROBOT_SERVICE='$UROBOT_SERVICE' \
    EDIT_NETPLAN='$EDIT_NETPLAN' EDIT_WPA='$EDIT_WPA' DRY_RUN='$DRY_RUN' DO_APPLY='$DO_APPLY' \
    ASSUME_YES='$ASSUME_YES' \
    bash '$REMOTE_TMP'; rm -f '$REMOTE_TMP'"
rc=$?
set -e

echo
if [[ $rc -ne 0 ]]; then
  echo "[change-jibot-network] remote exited $rc -- nothing applied (you declined the diff, or an error printed above)."
  exit $rc
elif [[ "$DRY_RUN" == 1 ]]; then
  echo "[change-jibot-network] dry run finished; nothing changed."
elif [[ "$DO_APPLY" == 1 ]]; then
  echo "[change-jibot-network] detached apply scheduled."
  if [[ -n "$NEW_IP" ]]; then
    echo "The robot is moving to $NEW_IP; reconnect with:"
    echo "  ssh ${REMOTE_HOST%@*}@${NEW_IP}"
  else
    echo "Wi-Fi is being reconnected; SSH may drop. Reconnect to the current address if it remains reachable:"
    echo "  ssh $REMOTE_HOST"
  fi
  echo "If it does not come back, recover at the robot console (backups: *.bak.<timestamp>)."
else
  echo "[change-jibot-network] files edited (not applied)."
fi
