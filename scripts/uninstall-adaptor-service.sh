#!/usr/bin/env bash
# Removes the system integration installed by setup-adaptor-service.sh.
# Project configuration, virtual environments, journald settings, and login
# linger are intentionally preserved because they may contain user data or be
# shared with other applications.
set -euo pipefail

DRY_RUN=0
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/uninstall-adaptor-service.sh [--dry-run]

Options:
  --dry-run   print the planned actions without changing the host
  -h, --help  show this help

Removes:
  amr-adaptor.service, amr-adaptor@.service and installed instances
  amr-webui.service, amr-camera.service and amr-xboxdrv.service
  /etc/sudoers.d/adaptor-tui
  AMR WebUI polkit rules
  /etc/tmpfiles.d/amr-adaptor.conf and /run/amr-adaptor

Preserves repository configuration, credentials, virtual environments,
journald configuration, and login linger.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1 || {
  echo "systemctl not found — this host does not use systemd." >&2
  exit 1
}

run_root() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '  [dry-run]'
    printf ' %q' "$@"
    printf '\n'
  elif [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

adapter_instances() {
  {
    "$SYSTEMCTL_BIN" list-unit-files 'amr-adaptor@*.service' --no-legend 2>/dev/null || true
    "$SYSTEMCTL_BIN" list-units --all 'amr-adaptor@*.service' --no-legend 2>/dev/null || true
  } | awk '{print $1}' | grep -E '^amr-adaptor@.+\.service$' | sort -u || true
}

units=(
  amr-adaptor.service
  amr-webui.service
  amr-camera.service
  amr-xboxdrv.service
)
while IFS= read -r unit; do
  [[ -n "$unit" ]] && units+=("$unit")
done < <(adapter_instances)

echo "==> stopping and disabling AMR services"
for unit in "${units[@]}"; do
  run_root "$SYSTEMCTL_BIN" disable --now "$unit" || true
done

echo "==> removing systemd unit files"
run_root rm -f \
  /etc/systemd/system/amr-adaptor.service \
  /etc/systemd/system/amr-adaptor@.service \
  /etc/systemd/system/amr-webui.service \
  /etc/systemd/system/amr-camera.service \
  /etc/systemd/system/amr-xboxdrv.service

# An instance normally resolves through amr-adaptor@.service, but remove any
# directly installed instance unit files too.
for unit in "${units[@]}"; do
  [[ "$unit" == amr-adaptor@*.service ]] || continue
  run_root rm -f "/etc/systemd/system/$unit"
done

echo "==> removing AMR service authorization"
run_root rm -f \
  /etc/sudoers.d/adaptor-tui \
  /etc/polkit-1/rules.d/10-amr-webui.rules \
  /etc/polkit-1/localauthority/50-local.d/10-amr-webui.pkla

echo "==> removing AMR runtime directory configuration"
run_root rm -f /etc/tmpfiles.d/amr-adaptor.conf
run_root rm -rf /run/amr-adaptor

echo "==> reloading systemd"
run_root "$SYSTEMCTL_BIN" daemon-reload
run_root "$SYSTEMCTL_BIN" reset-failed

if [[ $DRY_RUN -eq 1 ]]; then
  echo
  echo "Dry run complete; no changes were made."
  exit 0
fi

cat <<'SUMMARY'
AMR services uninstalled.
Repository configuration, WebUI credentials, virtual environments, journald
configuration, and login linger were preserved.
SUMMARY
