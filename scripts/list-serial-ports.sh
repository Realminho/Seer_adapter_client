#!/usr/bin/env bash
# List the USB serial converters on a robot by their *stable* names, so
# extension "pio"'s pio_serial_port can be set to one.
#
# Why this exists: /dev/ttyUSBn is assigned in enumeration order, and that order
# is not a property of the robot's wiring. The same PL2303 came up as ttyUSB4 on
# 192.168.101.61 and ttyUSB0 on .62, purely because the FTDI quad enumerated
# first on one of them. Moving the converter between USB ports renumbers it
# again. So a config pinned to ttyUSBn is correct only until the next reboot or
# re-plug, and when it goes wrong the failure is quiet: pyserial opens a wrong
# but existing port without error, and only the BC reply looks odd.
#
# udev's /dev/serial/by-id/ names come from the device's own USB strings, so
# they survive both renumbering and re-plugging. pyserial opens the symlink
# directly, so no adapter code has to change -- point pio_serial_port at the
# by-id path this prints.
#
# The PL2303 (067b:2303) is flagged because that is the PIO converter on this
# fleet; the FTDI quad (0403:6011) is the drive MCU side and must not be used.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/list-serial-ports.sh [user@robot]

Arguments:
  [user@robot]   Inspect this robot over SSH, e.g. ucore@192.168.101.61.
                 Omitted, the local machine is inspected instead.

Examples:
  scripts/list-serial-ports.sh
  scripts/list-serial-ports.sh ucore@192.168.101.61
USAGE
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

if [[ $# -gt 1 ]]; then
  echo "ERROR: expected at most one argument (user@robot)." >&2
  usage >&2
  exit 2
fi

# Kept as one self-contained snippet so the same code runs locally and over SSH.
# No sudo, no writes: this only reads /dev and /sys.
read -r -d '' PROBE <<'PROBE_EOF' || true
by_id=/dev/serial/by-id
if [ ! -d "$by_id" ]; then
  echo "no $by_id on this host (no USB serial converter attached, or udev did not populate it)"
  exit 0
fi

shopt -s nullglob
entries=("$by_id"/*)
if [ ${#entries[@]} -eq 0 ]; then
  echo "$by_id is empty (no USB serial converter attached)"
  exit 0
fi

printf '%-66s %-9s %-10s %s\n' "STABLE PATH (use this as pio_serial_port)" "DEVICE" "VID:PID" "NOTE"
for link in "${entries[@]}"; do
  target="$(readlink -f "$link" 2>/dev/null || true)"
  dev="${target##*/}"
  vid=""; pid=""
  # Walk up from the tty to the USB device node that carries idVendor/idProduct.
  syspath="$(readlink -f "/sys/class/tty/$dev/device" 2>/dev/null || true)"
  while [ -n "$syspath" ] && [ "$syspath" != "/" ]; do
    if [ -r "$syspath/idVendor" ] && [ -r "$syspath/idProduct" ]; then
      vid="$(cat "$syspath/idVendor")"
      pid="$(cat "$syspath/idProduct")"
      break
    fi
    syspath="$(dirname "$syspath")"
  done

  note=""
  case "$vid:$pid" in
    067b:2303) note="<- PIO converter (PL2303)" ;;
    0403:6011) note="drive MCU side (FTDI quad) -- not PIO" ;;
    ::)        note="USB ids not found" ;;
  esac

  printf '%-66s %-9s %-10s %s\n' "$link" "${dev:-?}" "${vid:-?}:${pid:-?}" "$note"
done
PROBE_EOF

if [[ $# -eq 1 ]]; then
  ssh -o BatchMode=yes "$1" bash -s <<<"$PROBE"
else
  bash -c "$PROBE"
fi
