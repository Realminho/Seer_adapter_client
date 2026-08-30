#!/usr/bin/env bash
# Make NetworkManager the single owner of wlan0 on a JIBOT onboard PC.
#
# These robots ship with three stacks pointed at the same interface:
#
#   * systemd-networkd (via netplan) assigns wlan0 its static address
#   * NetworkManager owns the Wi-Fi association through a saved profile
#   * wpa_supplicant ALSO connects on its own, because the unit was edited to
#     pass `-c .../wpa_supplicant.conf -i wlan0` on top of the `-u -s` that
#     makes it NetworkManager's D-Bus backend
#
# The third one is the problem. On 2026-08-19 .61 associated from
# wpa_supplicant.conf at 17:00:41 (-41 dBm, key negotiation complete) and
# NetworkManager tore it straight back down five seconds later with
# reason=3 DEAUTH_LEAVING, then failed to activate its own profile. Two
# supplicant configs, one radio.
#
# Two smaller faults come with it:
#
#   * Ubuntu ships /etc/NetworkManager/conf.d/default-wifi-powersave-on.conf
#     with `wifi.powersave = 3` (enable). Profiles inherit it, so NM turns power
#     save back on at every activation behind the keeper's back -- and power
#     save is what produces reason=4 DISASSOC_DUE_TO_INACTIVITY on this AX210.
#   * NM ships a keyfile that fails to parse ("connection.type: property is
#     missing"), logged as a warning on every boot.
#
# Applying the ExecStart change means restarting wpa_supplicant, which drops the
# link. So by default this only WRITES the config and leaves it to take effect
# at the next boot. Pass --apply to restart the services now, and only do that
# over a path that is not the Wi-Fi itself (wired, or console).
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/unify-wlan0-networkmanager.sh <user@target-host> [--iface NAME] [--apply] [--hidden-ap]

Arguments:
  <user@target-host>   Robot to change, e.g. ucore@192.168.101.61

Options:
  --iface NAME   Wireless interface (default: wlan0)
  --apply        Restart wpa_supplicant + NetworkManager now. THIS DROPS Wi-Fi.
                 Only use over a wired/console path.
  --hidden-ap    Also set 802-11-wireless.hidden=yes on the active profile, so
                 NM probes for the SSID instead of matching it in scan results.
  -h, --help     Show this help

Without --apply nothing is restarted and the changes land at the next boot.
USAGE
}

TARGET=""
IFACE="wlan0"
APPLY=0
HIDDEN=0

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --iface) IFACE="${2:?--iface needs a value}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --hidden-ap) HIDDEN=1; shift ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      [ -n "$TARGET" ] && { echo "only one target may be given" >&2; exit 2; }
      TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || { usage >&2; exit 2; }

STAGE="wifi-fix"

# Same staging trick as the keeper installer: write the body to a file and run
# it over a real tty, so sudo has somewhere to prompt. Piping it on stdin makes
# ssh decline the pty and sudo then fails outright.
ssh "$TARGET" "mkdir -p ~/$STAGE && cat > ~/$STAGE/unify-remote.sh" <<'REMOTE'
set -euo pipefail
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "==> current state"
echo -n "    wpa_supplicant ExecStart: "
systemctl show wpa_supplicant -p ExecStart --value | sed 's/.*argv\[\]=//; s/ ;.*//'
echo -n "    nmcli device: "; nmcli -t -f DEVICE,STATE,CONNECTION device status | grep "^$IFACE:" || echo "(not listed)"

# --- 1. stop wpa_supplicant from connecting on its own --------------------
# Keep -u -s (the D-Bus backend NetworkManager drives) and drop the -c/-i that
# make it a second, competing supplicant for the same radio.
UNIT=/etc/systemd/system/wpa_supplicant.service
if [ ! -f "$UNIT" ]; then
  echo "    no $UNIT (packaged unit in use); nothing to change"
elif grep -qE '^ExecStart=.*-i[[:space:]]+'"$IFACE" "$UNIT"; then
  sudo cp -a "$UNIT" "$UNIT.bak.$STAMP"
  sudo sed -i -E "s#^(ExecStart=/sbin/wpa_supplicant).*#\1 -u -s -O /run/wpa_supplicant#" "$UNIT"
  echo "    wpa_supplicant unit rewritten as a pure NM backend (backup: $UNIT.bak.$STAMP)"
else
  echo "    wpa_supplicant already a pure NM backend, left alone"
fi

# --- 2. stop NetworkManager re-enabling power save ------------------------
PS=/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf
if [ -f "$PS" ] && grep -qE '^[[:space:]]*wifi\.powersave[[:space:]]*=[[:space:]]*3' "$PS"; then
  sudo sed -i -E 's/^([[:space:]]*wifi\.powersave[[:space:]]*=[[:space:]]*)3/\12/' "$PS"
  echo "    wifi.powersave 3 (enable) -> 2 (disable)"
elif [ -f "$PS" ]; then
  echo "    wifi.powersave already $(grep -oE '=[[:space:]]*[0-9]' "$PS" | tr -d '= '), left alone"
else
  echo "    no $PS, nothing to change"
fi

# --- 3. drop the keyfile NM cannot parse ----------------------------------
# It is not the profile in use -- the working one loads from elsewhere -- it
# just fails on every boot with "connection.type: property is missing".
BROKEN=0
# Enumerate with sudo: the directory is root-only, so a plain shell glob would
# not expand and this would quietly report "nothing to do".
KEYFILES="$(sudo find /etc/NetworkManager/system-connections -maxdepth 1 -name '*.nmconnection' 2>/dev/null || true)"
for f in $KEYFILES; do
  [ -n "$f" ] || continue
  if ! sudo grep -qE '^[[:space:]]*type[[:space:]]*=' "$f" 2>/dev/null; then
    sudo mv "$f" "$f.broken.$STAMP"
    echo "    unparseable keyfile set aside: $(basename "$f")"
    BROKEN=1
  fi
done
[ "$BROKEN" -eq 0 ] && echo "    no unparseable keyfiles"

# --- 4. hidden-SSID probing (opt-in) --------------------------------------
CONN="$(nmcli -t -f DEVICE,CONNECTION device status | grep "^$IFACE:" | cut -d: -f2)"
if [ "${HIDDEN:-0}" = "1" ]; then
  if [ -n "$CONN" ] && [ "$CONN" != "--" ]; then
    sudo nmcli con mod "$CONN" 802-11-wireless.hidden yes
    echo "    802-11-wireless.hidden=yes on '$CONN'"
  else
    echo "    no active profile on $IFACE; skipped hidden-AP setting"
  fi
fi

# --- report the addressing, do not change it ------------------------------
# netplan puts a static address on wlan0 while an NM profile set to
# ipv4.method=auto adds a DHCP lease and a second default route on top. Which
# one should win is a fleet-addressing decision, not something to guess at from
# here, so this only reports it.
echo
echo "==> addressing (not changed by this script)"
echo -n "    addresses: "; ip -4 -br addr show "$IFACE" | awk '{$1="";$2="";print}'
echo "    default routes:"; ip route | grep "^default" | sed 's/^/      /'
if [ "$(ip route | grep -c '^default.*'"$IFACE")" -gt 1 ]; then
  echo "    WARNING: more than one default route on $IFACE."
  echo "             The NM profile is probably ipv4.method=auto on top of a"
  echo "             netplan static. Decide which owns the address."
fi

if [ "${APPLY:-0}" = "1" ]; then
  echo
  echo "==> applying (Wi-Fi will drop)"
  sudo systemctl daemon-reload
  sudo systemctl restart wpa_supplicant
  sleep 3
  sudo systemctl restart NetworkManager
  sleep 15
  echo -n "    device: "; nmcli -t -f DEVICE,STATE,CONNECTION device status | grep "^$IFACE:" || true
  echo -n "    link: ";   iw dev "$IFACE" link 2>/dev/null | grep -m1 "Connected to" || echo "not associated"
  echo -n "    power_save: "; { iw dev "$IFACE" get power_save 2>&1 | tail -1; } || true
else
  echo
  echo "==> not applied. Changes take effect at the next boot."
  echo "    To apply now (over a wired/console path only):"
  echo "      sudo systemctl daemon-reload"
  echo "      sudo systemctl restart wpa_supplicant && sleep 3"
  echo "      sudo systemctl restart NetworkManager"
fi
REMOTE

echo "==> running on $TARGET (sudo will prompt on the robot)"
ssh -t "$TARGET" "IFACE='$IFACE' APPLY='$APPLY' HIDDEN='$HIDDEN' bash ~/$STAGE/unify-remote.sh"
