#!/usr/bin/env bash
# Keeps wlan0 associated so a lost link never needs a human power-cycle.
#
# Observed failure (2026-08-17, .61): the AX210 wedges into a state where every
# scan returns EINVAL (CTRL-EVENT-SCAN-FAILED ret=-22). Restarting
# wpa_supplicant gives a fresh process that fails identically, and bouncing the
# interface changes nothing -- for 17 minutes straight -- so the wedge is below
# wpa_supplicant, in the driver/firmware. Only a reboot cleared it.
#
# iwlwifi is built into this kernel, so there is no module to reload. The
# equivalent is a PCIe remove + rescan, which re-probes the device and reloads
# firmware. That is the last escalation step here, and it is why this script
# exists: it replaces the power-cycle a human would otherwise do.
#
# Two robot setups exist and they need different recovery steps:
#
#   * NetworkManager-managed (.61 since 2026-08-19): wpa_supplicant runs as a
#     bare D-Bus backend (`-u -s -O ...`, no -i/-c), so it never reconnects on
#     its own -- only NetworkManager does. Restarting it there just deletes NM's
#     supplicant interface (NM logs 'supplicant-failed') and downing the link
#     wipes NM's scan results, so the old steps actively prevented recovery: a
#     wedge that clears in 5s took 11 minutes of fighting on 2026-08-19.
#
#   * Legacy: wpa_supplicant owns the interface from its own config file
#     (`-i wlan0 -c ...`) and reconnects by itself.
#
# Escalation per outage, NetworkManager: device connect -> reactivate ->
# radio off/on -> PCIe reset, then around again.
# Escalation per outage, legacy: reconnect -> restart wpa_supplicant ->
# bounce link -> PCIe reset, then around again.

set -u

IFACE=${IFACE:-wlan0}
PCI_SLOT=${PCI_SLOT:-0000:01:00.0}
CHECK_INTERVAL=${CHECK_INTERVAL:-20}
GRACE=${GRACE:-40}              # seconds offline before the first action
DIAG=${DIAG:-/var/log/amr-wifi-diag.log}
DIAG_MAX=${DIAG_MAX:-10485760}  # 10 MiB

log() { echo "[wifi-keeper] $*"; }

apply_radio_settings() {
  # Power save causes reason=4 DISASSOC_DUE_TO_INACTIVITY drops on this card.
  iw dev "$IFACE" set power_save off 2>/dev/null && log "power save off on $IFACE"
}

associated() {
  iw dev "$IFACE" link 2>/dev/null | grep -q "^Connected to"
}

# Which stack actually drives this interface? Decided from the unit definition
# rather than from live device state, because during an outage NetworkManager
# reports the device as 'unavailable' and any runtime probe would flip us onto
# the wrong recovery path exactly when it matters.
nm_owns_iface() {
  command -v nmcli >/dev/null 2>&1 || return 1
  systemctl is-active --quiet NetworkManager || return 1
  # A wpa_supplicant given -i owns the interface itself; without it, it only
  # acts when NetworkManager tells it to.
  systemctl show wpa_supplicant -p ExecStart --value 2>/dev/null |
    grep -q -- "-i $IFACE" && return 1
  return 0
}

# Snapshot the driver-level state once per outage. The journal is capped and
# the SCAN-FAILED spam evicts kernel messages within minutes, so by the time
# anyone looks, dmesg for the outage is already gone.
capture_diagnostics() {
  [ -f "$DIAG" ] && [ "$(stat -c %s "$DIAG" 2>/dev/null || echo 0)" -gt "$DIAG_MAX" ] &&
    mv -f "$DIAG" "$DIAG.1"
  {
    echo "================ link lost at $(date '+%F %T') ================"
    echo "--- iw dev $IFACE link ---";        iw dev "$IFACE" link 2>&1
    echo "--- iw dev $IFACE info ---";        iw dev "$IFACE" info 2>&1
    echo "--- iw reg get ---";                iw reg get 2>&1 | head -12
    echo "--- ip -br link ---";               ip -br link 2>&1
    echo "--- pci present? ---";              ls -d "/sys/bus/pci/devices/$PCI_SLOT" 2>&1
    echo "--- dmesg tail ---";                dmesg 2>&1 | tail -60
    echo "--- wpa_supplicant unit ---";       systemctl --no-pager -n 0 status wpa_supplicant 2>&1 | head -12
    echo "--- wpa_supplicant recent log ---"; journalctl -u wpa_supplicant -n 25 --no-pager 2>&1
    if nm_owns_iface; then
      echo "--- nmcli device ---";            nmcli device status 2>&1 | head -8
      echo "--- NetworkManager recent log ---"
      journalctl -u NetworkManager -n 25 --no-pager 2>&1
    fi
    echo
  } >>"$DIAG" 2>&1
  log "diagnostics captured to $DIAG"
}

pci_reset() {
  if [ ! -e "/sys/bus/pci/devices/$PCI_SLOT/remove" ]; then
    log "PCI slot $PCI_SLOT not present, skipping reset"
    return 1
  fi
  log "PCIe remove+rescan on $PCI_SLOT (driver-level reset)"
  echo 1 >"/sys/bus/pci/devices/$PCI_SLOT/remove" 2>/dev/null
  sleep 3
  echo 1 >/sys/bus/pci/rescan 2>/dev/null
  # give iwlwifi time to reload firmware and recreate the interface
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    [ -e "/sys/class/net/$IFACE" ] && break
    sleep 1
  done
  # The netdev was destroyed and recreated, so the supplicant has to re-attach
  # to a fresh interface either way.
  systemctl restart wpa_supplicant
  if nm_owns_iface; then
    # NetworkManager does re-add the interface on its own, but it does not
    # reliably auto-activate the profile afterwards, so ask it explicitly.
    sleep 3
    nmcli device connect "$IFACE" >/dev/null 2>&1
  fi
  apply_radio_settings
}

if nm_owns_iface; then
  log "watching $IFACE (NetworkManager-managed)"
else
  log "watching $IFACE (wpa_supplicant-managed)"
fi
apply_radio_settings

down_since=0
escalation=0
captured=0

while true; do
  if associated; then
    if [ "$down_since" -ne 0 ]; then
      log "link recovered after $(( $(date +%s) - down_since ))s (escalation reached $escalation)"
      apply_radio_settings
      down_since=0
      escalation=0
      captured=0
    fi
  else
    now=$(date +%s)
    if [ "$down_since" -eq 0 ]; then
      down_since=$now
      log "link lost on $IFACE"
      [ "$captured" -eq 0 ] && { capture_diagnostics; captured=1; }
    fi
    offline=$(( now - down_since ))

    if [ "$offline" -ge "$GRACE" ]; then
      if nm_owns_iface; then
        case "$escalation" in
          0)
            log "offline ${offline}s -> nmcli device connect $IFACE"
            nmcli device connect "$IFACE" >/dev/null 2>&1
            escalation=1
            ;;
          1)
            log "offline ${offline}s -> nmcli reactivate $IFACE"
            nmcli device disconnect "$IFACE" >/dev/null 2>&1
            sleep 2
            nmcli device connect "$IFACE" >/dev/null 2>&1
            escalation=2
            ;;
          2)
            # NetworkManager's own equivalent of a link bounce. Doing it with
            # `ip link` instead would throw away NM's scan results, and without
            # a scan NM fails activation with 'ssid-not-found'.
            log "offline ${offline}s -> toggling NM wifi radio"
            nmcli radio wifi off >/dev/null 2>&1
            sleep 3
            nmcli radio wifi on >/dev/null 2>&1
            apply_radio_settings
            escalation=3
            ;;
          *)
            # Everything above failed, so the card itself is wedged.
            pci_reset
            escalation=1
            ;;
        esac
      else
        case "$escalation" in
          0)
            log "offline ${offline}s -> wpa_cli reconnect"
            wpa_cli -i "$IFACE" reconnect >/dev/null 2>&1
            escalation=1
            ;;
          1)
            log "offline ${offline}s -> restarting wpa_supplicant"
            systemctl restart wpa_supplicant
            escalation=2
            ;;
          2)
            log "offline ${offline}s -> bouncing $IFACE"
            ip link set "$IFACE" down 2>/dev/null
            sleep 2
            ip link set "$IFACE" up 2>/dev/null
            apply_radio_settings
            wpa_cli -i "$IFACE" reconnect >/dev/null 2>&1
            escalation=3
            ;;
          *)
            # Everything above failed, so the card itself is wedged.
            pci_reset
            escalation=1
            ;;
        esac
      fi
      # give the current step time to work before escalating again
      down_since=$(( now - GRACE + 30 ))
    fi
  fi
  sleep "$CHECK_INTERVAL"
done
