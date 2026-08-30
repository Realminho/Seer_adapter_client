#!/usr/bin/env bash
# Install, remove, or control every AMR adaptor service managed by this repository.
#
# Usage:
#   scripts/adaptor-services.sh install [setup options]
#   scripts/adaptor-services.sh remove [--dry-run]
#   scripts/adaptor-services.sh start [--dry-run] [--no-sudo]
#   scripts/adaptor-services.sh restart [--dry-run] [--no-sudo]
#   scripts/adaptor-services.sh stop [--dry-run] [--no-sudo]
#   scripts/adaptor-services.sh status [--dry-run] [--no-sudo]
#
# Run `scripts/adaptor-services.sh install --help` or
# `scripts/adaptor-services.sh remove --help` for action-specific options.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ADAPTER_DIR="${ADAPTER_DIR:-$REPO_ROOT/adaptor}"
if [[ ! -d "$ADAPTER_DIR" && -f "$REPO_ROOT/run-main.sh" ]]; then
  ADAPTER_DIR="$REPO_ROOT"
fi

usage() {
  sed -n '2,13p' "$0"
}

ACTION="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$ACTION" in
  install)
    exec "$SCRIPT_DIR/setup-adaptor-service.sh" "$@"
    ;;
  remove)
    exec "$SCRIPT_DIR/uninstall-adaptor-service.sh" "$@"
    ;;
  start|restart|stop|status) ;;
  -h|--help|"")
    usage
    exit 0
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage >&2
    exit 2
    ;;
esac

DRY_RUN=0
USE_SUDO=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --no-sudo) USE_SUDO=0 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-$(command -v systemctl || true)}"
if [[ -z "$SYSTEMCTL_BIN" && $DRY_RUN -eq 0 ]]; then
  echo "systemctl not found - this host does not use systemd." >&2
  exit 1
fi
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"

adapter_python() {
  local py
  for py in "$ADAPTER_DIR/.venv/bin/python" "$ADAPTER_DIR/venvJIBOT/bin/python" python3; do
    if command -v "$py" >/dev/null 2>&1; then
      command -v "$py"
      return 0
    fi
  done
  return 1
}

list_adapter_instances() {
  local py
  py="$(adapter_python)" || return 0
  (cd "$ADAPTER_DIR" && "$py" - <<'PY') 2>/dev/null || true
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

optional_unit_available() {
  local unit="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    return 0
  fi
  "$SYSTEMCTL_BIN" list-unit-files "$unit" --no-legend 2>/dev/null | grep -q "^$unit" \
    || "$SYSTEMCTL_BIN" list-units --all "$unit" --no-legend 2>/dev/null | grep -q "^$unit"
}

list_units() {
  local instances name
  local units=()

  # The joystick bridge leads the adapter: the adapter binds the virtual
  # gamepad by name, and cycling the bridge afterwards would drop that handle.
  if optional_unit_available "amr-xboxdrv.service"; then
    units+=("amr-xboxdrv.service")
  fi

  instances="$(list_adapter_instances)"
  if [[ -n "$instances" ]]; then
    while IFS= read -r name; do
      [[ -n "$name" ]] && units+=("amr-adaptor@${name}.service")
    done <<<"$instances"
  else
    units+=("amr-adaptor.service")
  fi

  if optional_unit_available "amr-webui.service"; then
    units+=("amr-webui.service")
  fi
  if optional_unit_available "amr-camera.service"; then
    units+=("amr-camera.service")
  fi

  printf '%s\n' "${units[@]}"
}

systemctl_cmd() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '  [dry-run] %q' "$SYSTEMCTL_BIN"
    local arg
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  if [[ $USE_SUDO -eq 1 && "$(id -u)" -ne 0 && "$ACTION" != "status" ]]; then
    sudo "$SYSTEMCTL_BIN" "$@"
  else
    "$SYSTEMCTL_BIN" "$@"
  fi
}

rc=0
while IFS= read -r unit; do
  [[ -n "$unit" ]] || continue
  echo "==> $ACTION $unit"
  case "$ACTION" in
    start) systemctl_cmd start "$unit" || rc=1 ;;
    restart) systemctl_cmd restart "$unit" || rc=1 ;;
    stop) systemctl_cmd stop "$unit" || rc=1 ;;
    status) systemctl_cmd status "$unit" --no-pager || rc=1 ;;
  esac
done < <(list_units)

exit "$rc"
