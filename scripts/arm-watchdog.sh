#!/usr/bin/env bash
# Arm the systemd-managed hardware watchdog on a JIBOT onboard host.
#
# Why: bot .222 has a recurring FULL SoC hard-lockup (SSH-dead). The hardware
# watchdog (dw_wdt) exists but nothing arms it, so a hang sits dead for hours
# until a human power-cycles it. Setting RuntimeWatchdogSec makes systemd ping
# the watchdog; if the whole system wedges, the chip auto-reboots within
# ~RuntimeWatchdog seconds. This BOUNDS downtime — it is NOT a root-cause fix
# (that is a vendor/board matter). Some boards (dw_wdt "No valid TOPs array
# specified") refuse to arm; this script VERIFIES and tells you if it took.
#
# What it does: sets RuntimeWatchdogSec / RebootWatchdogSec in
# /etc/systemd/system.conf (idempotent; backs up the original once) and
# `systemctl daemon-reexec` to apply, then checks the kernel log to confirm.
#
# Usage:
#   sudo scripts/arm-watchdog.sh                      # 60s runtime, 2min reboot
#   sudo scripts/arm-watchdog.sh --runtime 90 --reboot 3min
#        scripts/arm-watchdog.sh --dry-run            # show plan, change nothing
#   sudo scripts/arm-watchdog.sh --revert             # restore the pre-arm config
set -euo pipefail

ORIG_ARGS=("$@")

RUNTIME_SEC=60
REBOOT_DUR="2min"
DRY_RUN=0
REVERT=0
CONF="/etc/systemd/system.conf"
BACKUP="${CONF}.amr-watchdog.bak"

usage() {
  grep -E '^#( |$)' "$0" | sed -E 's/^# ?//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime) RUNTIME_SEC="${2:?--runtime needs a value}"; shift 2 ;;
    --runtime=*) RUNTIME_SEC="${1#*=}"; shift ;;
    --reboot) REBOOT_DUR="${2:?--reboot needs a value}"; shift 2 ;;
    --reboot=*) REBOOT_DUR="${1#*=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --revert) REVERT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

SYSTEMCTL_BIN="$(command -v systemctl || true)"
if [[ -z "$SYSTEMCTL_BIN" ]]; then
  echo "systemctl not found — this host does not use systemd." >&2
  exit 1
fi

# The real run edits /etc and re-execs PID 1, which needs root. Dry-run does not.
if [[ $DRY_RUN -eq 0 && "$(id -u)" -ne 0 ]]; then
  echo "==> re-executing under sudo for root access"
  exec sudo -- "$0" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
fi

# set_conf_key KEY VALUE — set KEY=VALUE under [Manager] in $CONF, replacing the
# first existing (commented or live) line, else appending under [Manager].
set_conf_key() {
  local key="$1" val="$2"
  if grep -qE "^[[:space:]]*#?[[:space:]]*${key}=" "$CONF"; then
    sed -i -E "0,/^[[:space:]]*#?[[:space:]]*${key}=.*/s//${key}=${val}/" "$CONF"
  else
    grep -qE '^\[Manager\]' "$CONF" || printf '\n[Manager]\n' >>"$CONF"
    sed -i -E "/^\[Manager\]/a ${key}=${val}" "$CONF"
  fi
}

if [[ $REVERT -eq 1 ]]; then
  if [[ -f "$BACKUP" ]]; then
    echo "==> restoring $CONF from $BACKUP"
    [[ $DRY_RUN -eq 1 ]] && { echo "  [dry-run] cp $BACKUP $CONF"; } || cp -- "$BACKUP" "$CONF"
  else
    echo "==> no backup found; disabling watchdog (RuntimeWatchdogSec=0)"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  [dry-run] set RuntimeWatchdogSec=0, RebootWatchdogSec=off in $CONF"
    else
      set_conf_key "RuntimeWatchdogSec" "0"
      set_conf_key "RebootWatchdogSec" "off"
    fi
  fi
else
  echo "==> arming watchdog: RuntimeWatchdogSec=${RUNTIME_SEC}, RebootWatchdogSec=${REBOOT_DUR}"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] cp -n $CONF $BACKUP"
    echo "  [dry-run] set RuntimeWatchdogSec=${RUNTIME_SEC} in $CONF"
    echo "  [dry-run] set RebootWatchdogSec=${REBOOT_DUR} in $CONF"
  else
    cp -n -- "$CONF" "$BACKUP" 2>/dev/null || true   # keep the first (pre-arm) copy only
    set_conf_key "RuntimeWatchdogSec" "${RUNTIME_SEC}"
    set_conf_key "RebootWatchdogSec" "${REBOOT_DUR}"
  fi
fi

echo "==> systemctl daemon-reexec (apply manager config)"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "  [dry-run] $SYSTEMCTL_BIN daemon-reexec"
  echo "  [dry-run] verify: $SYSTEMCTL_BIN show -p RuntimeWatchdogUSec --value"
  exit 0
fi
"$SYSTEMCTL_BIN" daemon-reexec

# --- verify it actually armed ---------------------------------------------
echo "==> verifying"
us="$("$SYSTEMCTL_BIN" show -p RuntimeWatchdogUSec --value 2>/dev/null || true)"
echo "    RuntimeWatchdogUSec=${us:-<unknown>}"
if journalctl -b 2>/dev/null | grep -iqE "Set hardware watchdog to|Watchdog running with"; then
  echo "    OK: kernel/systemd confirms the hardware watchdog is armed."
elif journalctl -b 2>/dev/null | grep -iqE "Watchdog hardware is disabled|Failed to .*watchdog"; then
  echo "    WARNING: systemd reports the watchdog hardware is disabled —"
  echo "             this board will NOT auto-reboot. Root cause stays vendor-side."
else
  echo "    NOTE: could not confirm from the boot log. Check manually:"
  echo "          journalctl -b | grep -i watchdog"
fi
