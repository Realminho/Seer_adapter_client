#!/usr/bin/env bash
# Installs the AMR VDA5050 adapter as an adapter-only Ubuntu systemd service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVICE_NAME="amr-adaptor"
ADAPTER_DIR="$SCRIPT_DIR"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"
RUN_ARGS=""
START_SERVICE=1

UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
SUDO_BIN="${SUDO_BIN:-sudo}"

usage() {
  cat <<'USAGE'
Usage:
  ./install-systemd-service.sh [options]

Options:
  --name <service-name>     systemd service name without .service (default: amr-adaptor)
  --adapter-dir <path>      adapter directory containing run-adapter.sh (default: this directory)
  --user <user>             Linux user to run the adapter as (default: current user)
  --group <group>           Linux group to run the adapter as (default: current group)
  --args <args>             arguments forwarded through run-adapter.sh
                            start with --instance NAME for one robot/vendor; the rest reach the launcher
  --no-start                enable the service but do not restart it now
  -h, --help                show this help

Examples:
  ./install-systemd-service.sh
  ./install-systemd-service.sh --args "--instance HN-SH6-TR-002"
  ./install-systemd-service.sh --name amr-adaptor --user ucore --group ucore
USAGE
}

die() {
  echo "Error: $*" >&2
  exit 1
}

need_value() {
  local option="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || die "$option requires a value"
}

run_privileged() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  else
    "$SUDO_BIN" "$@"
  fi
}

run_systemctl() {
  run_privileged "$SYSTEMCTL_BIN" "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      need_value "$1" "${2:-}"
      SERVICE_NAME="$2"
      shift 2
      ;;
    --adapter-dir)
      need_value "$1" "${2:-}"
      ADAPTER_DIR="$2"
      shift 2
      ;;
    --user)
      need_value "$1" "${2:-}"
      RUN_USER="$2"
      shift 2
      ;;
    --group)
      need_value "$1" "${2:-}"
      RUN_GROUP="$2"
      shift 2
      ;;
    --args)
      need_value "$1" "${2:-}"
      RUN_ARGS="$2"
      shift 2
      ;;
    --no-start)
      START_SERVICE=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ "$SERVICE_NAME" =~ ^[A-Za-z0-9_.@-]+$ ]] || die "invalid service name: $SERVICE_NAME"
[[ "$RUN_ARGS" != *$'\n'* ]] || die "--args must not contain newlines"

ADAPTER_DIR="$(cd "$ADAPTER_DIR" && pwd -P)"
RUNNER="$ADAPTER_DIR/run-adapter.sh"
UNIT_FILE="$UNIT_DIR/$SERVICE_NAME.service"

[[ -f "$RUNNER" ]] || die "missing runner script: $RUNNER"
chmod +x "$RUNNER"

UNIT_CONTENT="$(cat <<UNIT
[Unit]
Description=AMR VDA5050 Adapter
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$ADAPTER_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$RUNNER${RUN_ARGS:+ $RUN_ARGS}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
)"

mkdir -p "$UNIT_DIR" 2>/dev/null || run_privileged mkdir -p "$UNIT_DIR"

if [[ -w "$UNIT_DIR" ]]; then
  printf '%s\n' "$UNIT_CONTENT" > "$UNIT_FILE"
else
  TMP_UNIT="$(mktemp)"
  trap 'rm -f "${TMP_UNIT:-}"' EXIT
  printf '%s\n' "$UNIT_CONTENT" > "$TMP_UNIT"
  run_privileged install -m 0644 "$TMP_UNIT" "$UNIT_FILE"
fi

run_systemctl daemon-reload
run_systemctl enable "$SERVICE_NAME.service"

if [[ "$START_SERVICE" == "1" ]]; then
  run_systemctl restart "$SERVICE_NAME.service"
  run_systemctl status "$SERVICE_NAME.service" --no-pager
else
  echo "Service enabled but not started: $SERVICE_NAME.service"
fi

cat <<SUMMARY

Installed $SERVICE_NAME.service
Unit file: $UNIT_FILE

Useful commands:
  sudo systemctl status $SERVICE_NAME.service --no-pager
  sudo journalctl -u $SERVICE_NAME.service -f
  sudo systemctl restart $SERVICE_NAME.service
SUMMARY
