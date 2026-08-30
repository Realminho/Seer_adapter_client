#!/usr/bin/env bash
# Install the Wi-Fi keeper and its supporting config on a JIBOT onboard PC over
# SSH. This packages the fixes for three separate faults found on 192.168.101.61
# and .62, each of which ended with the robot invisible on the network and
# somebody power-cycling it:
#
#   1. wpa_supplicant.service starts before iwlwifi has created wlan0, dies with
#      "No such device", and burns all five allowed restarts inside one second
#      (default RestartSec is 100ms). Most boots recover by luck; the rest come
#      up with no Wi-Fi at all. -> wpa_supplicant drop-in waits for the device.
#
#   2. Power save on this AX210 triggers reason=4 DISASSOC_DUE_TO_INACTIVITY
#      drops, and bgscan is shipped with a -10 dBm signal threshold that no real
#      link ever beats, pinning wpa_supplicant in its 5-second aggressive scan
#      mode forever. -> power save off, bgscan threshold moved to -70 dBm.
#
#   3. The card can wedge so that every scan returns EINVAL. Restarting
#      wpa_supplicant and bouncing the link do not clear it (observed for 17
#      minutes straight on .61); only re-probing the device does. iwlwifi is
#      built into this kernel, so the keeper escalates to a PCIe remove+rescan.
#
# The journal cap is raised where it is too small, because one-per-second
# scan-failure spam evicts every kernel message within minutes and leaves
# nothing to diagnose. A cap that is already generous is left alone rather
# than shrunk -- .62 keeps 33 boots and that history is worth more than a
# uniform setting.
#
# This script deliberately never restarts wpa_supplicant: on a robot reached
# over the very Wi-Fi being repaired, that drops the link into exactly the
# unrecoverable state described above, and recovery then needs console access.
# The bgscan change therefore takes effect at the next boot.
#
# Assets are uploaded to ~/wifi-fix on the robot (not /tmp, which is cleared on
# reboot) and installed over an interactive SSH session so sudo can prompt for
# the password.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/setup-wifi-keeper-over-ssh.sh <user@target-host> [--iface NAME] [--pci SLOT]

Arguments:
  <user@target-host>   Robot to install on, e.g. ucore@192.168.101.62

Options:
  --iface NAME   Wireless interface to watch (default: wlan0)
  --pci SLOT     PCI slot of the Wi-Fi card for the reset step
                 (default: autodetected from the interface)
  -h, --help     Show this help

Nothing here restarts wpa_supplicant, so the current Wi-Fi link stays up.
Verify afterwards with:
  ssh <target> 'systemctl is-active amr-wifi-keeper; iw dev wlan0 get power_save'
USAGE
}

TARGET=""
IFACE="wlan0"
PCI_SLOT=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --iface) IFACE="${2:?--iface needs a value}"; shift 2 ;;
    --pci) PCI_SLOT="${2:?--pci needs a value}"; shift 2 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      [ -n "$TARGET" ] && { echo "only one target may be given" >&2; exit 2; }
      TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || { usage >&2; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="wifi-fix"

for f in "$HERE/amr-wifi-keeper.sh" \
         "$HERE/systemd/amr-wifi-keeper.service" \
         "$HERE/systemd/wpa-supplicant-wait-for-device.conf" \
         "$HERE/systemd/journald-keep-history.conf" \
         "$HERE/systemd/journald-sync-fast.conf"; do
  [ -f "$f" ] || { echo "missing asset: $f" >&2; exit 1; }
done

echo "==> staging assets on $TARGET:~/$STAGE"
ssh "$TARGET" "mkdir -p ~/$STAGE"
scp -q "$HERE/amr-wifi-keeper.sh" \
       "$HERE/systemd/amr-wifi-keeper.service" \
       "$HERE/systemd/wpa-supplicant-wait-for-device.conf" \
       "$HERE/systemd/journald-keep-history.conf" \
       "$HERE/systemd/journald-sync-fast.conf" \
       "$TARGET:~/$STAGE/"

# Stage the install step as a file rather than piping it to `ssh -t ... bash -s`:
# feeding the body on stdin means stdin is not a terminal, ssh refuses to
# allocate the pty, and sudo then has no way to prompt for the password.
ssh "$TARGET" "cat > ~/$STAGE/install-remote.sh" <<'REMOTE'
set -euo pipefail
cd ~/"$STAGE"

# Autodetect the Wi-Fi card's PCI slot when not given, so the keeper's reset
# step targets the right device on boards that enumerate differently.
if [ -z "${PCI_SLOT:-}" ]; then
  PCI_SLOT="$(basename "$(readlink -f "/sys/class/net/$IFACE/device" 2>/dev/null)" 2>/dev/null || true)"
  case "$PCI_SLOT" in
    [0-9a-f]*:[0-9a-f]*:[0-9a-f]*.[0-9]*) ;;
    *) PCI_SLOT="" ;;
  esac
fi
echo "    interface=$IFACE  pci_slot=${PCI_SLOT:-<none detected>}"

sudo install -m 755 amr-wifi-keeper.sh /usr/local/sbin/amr-wifi-keeper.sh
sudo install -D -m 644 wpa-supplicant-wait-for-device.conf \
     /etc/systemd/system/wpa_supplicant.service.d/wait-for-device.conf
# Only raise the journal cap, never lower it. .61 shipped an explicit 100M
# that evicted every kernel message within minutes, but .62 has no cap at all
# and keeps 33 boots -- dropping that to 500M would destroy the very history
# this is meant to preserve.
# The trailing `|| true` matters: with `set -e` and `pipefail`, a grep that
# simply finds nothing takes the whole script down without printing anything,
# and "no SystemMaxUse anywhere" is exactly the case this branch exists for.
CUR_CAP="$( { cat /etc/systemd/journald.conf /etc/systemd/journald.conf.d/*.conf 2>/dev/null \
              | grep -E '^[[:space:]]*SystemMaxUse=' | tail -1 | cut -d= -f2 | tr -d '[:space:]'; } || true)"
case "$CUR_CAP" in
  "")            echo "    journal cap unset (defaults to 10% of the filesystem), left alone" ;;
  *[Mm])         CUR_MB="${CUR_CAP%[Mm]}"
                 if [ "${CUR_MB:-0}" -lt 500 ] 2>/dev/null; then
                   sudo install -D -m 644 journald-keep-history.conf \
                        /etc/systemd/journald.conf.d/keep-history.conf
                   echo "    journal cap raised ${CUR_CAP} -> 500M"
                 else
                   echo "    journal cap already ${CUR_CAP}, left alone"
                 fi ;;
  *)             echo "    journal cap already ${CUR_CAP}, left alone" ;;
esac

# Always applied, unlike the cap above: these robots lose power without a
# shutdown sequence, and a 5-minute journald sync window can swallow the last
# minutes before the cut -- the only evidence there is.
sudo install -D -m 644 journald-sync-fast.conf \
     /etc/systemd/journald.conf.d/sync-fast.conf
echo "    journal sync interval set to 1s (survives an abrupt power cut)"

sudo install -m 644 amr-wifi-keeper.service /etc/systemd/system/amr-wifi-keeper.service

# Pass the detected interface/slot to the keeper without editing the script, so
# the same unit file works on every robot.
sudo mkdir -p /etc/systemd/system/amr-wifi-keeper.service.d
sudo tee /etc/systemd/system/amr-wifi-keeper.service.d/environment.conf >/dev/null <<EOF
[Service]
Environment=IFACE=$IFACE
${PCI_SLOT:+Environment=PCI_SLOT=$PCI_SLOT}
EOF

# bgscan's shipped -10 dBm threshold keeps wpa_supplicant permanently in its
# aggressive 5s scan mode. Only rewrite when the bad value is actually present,
# so re-running this script is harmless.
WPA_CONF=/etc/wpa_supplicant/wpa_supplicant.conf
if sudo grep -q 'bgscan="simple:5:-10:300"' "$WPA_CONF" 2>/dev/null; then
  sudo cp "$WPA_CONF" "$WPA_CONF.bak.$(date +%Y%m%d-%H%M%S)"
  sudo sed -i 's/bgscan="simple:5:-10:300"/bgscan="simple:30:-70:300"/' "$WPA_CONF"
  echo "    bgscan threshold rewritten (applies at next boot)"
else
  echo "    bgscan already sane or absent, left alone"
fi

sudo systemctl daemon-reload
sudo systemctl restart systemd-journald
sudo systemctl enable --now amr-wifi-keeper.service
sudo systemctl restart amr-wifi-keeper.service

echo
echo "==> verification"
systemctl show wpa_supplicant -p RestartUSec -p StartLimitIntervalUSec
echo -n "    keeper: "; systemctl is-active amr-wifi-keeper || true
echo -n "    keeper has PCIe reset: "
grep -q pci_reset /usr/local/sbin/amr-wifi-keeper.sh && echo yes || echo "NO -- stale script"
echo -n "    keeper knows NetworkManager: "
grep -q nm_owns_iface /usr/local/sbin/amr-wifi-keeper.sh && echo yes || echo "NO -- stale script"
echo -n "    keeper recovery path: "
systemctl show wpa_supplicant -p ExecStart --value | grep -q -- "-i $IFACE" &&
  echo "wpa_supplicant-managed" || echo "NetworkManager-managed"
echo -n "    power_save: "; { iw dev "$IFACE" get power_save 2>&1 | tail -1; } || true
echo -n "    bgscan: "; sudo grep -h bgscan "$WPA_CONF" 2>/dev/null || echo "(none)"
echo -n "    journal: "; journalctl --disk-usage
echo -n "    journal sync: "
grep -rhE "^[[:space:]]*SyncIntervalSec=" /etc/systemd/journald.conf.d/*.conf 2>/dev/null | tail -1 || echo "(default 5min)"
echo -n "    link: "; iw dev "$IFACE" link 2>/dev/null | grep -m1 "Connected to" || echo "not associated"
REMOTE

echo "==> installing (sudo will prompt on the robot)"
ssh -t "$TARGET" "IFACE='$IFACE' PCI_SLOT='$PCI_SLOT' STAGE='$STAGE' bash ~/$STAGE/install-remote.sh"

echo
echo "==> done. The bgscan change takes effect at the next boot."
echo "    After the next reboot, this should print 0 (it was 5 on every boot before):"
echo "      ssh $TARGET 'journalctl -b 0 -u wpa_supplicant --no-pager | grep -c \"Scheduled restart\"'"
echo "    If Wi-Fi drops again, the driver-level snapshot is at:"
echo "      ssh $TARGET 'sudo tail -80 /var/log/amr-wifi-diag.log'"
