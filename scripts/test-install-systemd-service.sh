#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

UNIT_DIR="$TMP_DIR/systemd"
BIN_DIR="$TMP_DIR/bin"
LOG_FILE="$TMP_DIR/commands.log"
mkdir -p "$UNIT_DIR" "$BIN_DIR"

cat > "$BIN_DIR/fake-systemctl" <<'FAKE_SYSTEMCTL'
#!/usr/bin/env bash
printf 'systemctl'
for arg in "$@"; do
  printf ' [%s]' "$arg"
done
printf '\n'
FAKE_SYSTEMCTL
chmod +x "$BIN_DIR/fake-systemctl"

cat > "$BIN_DIR/fake-sudo" <<FAKE_SUDO
#!/usr/bin/env bash
printf 'sudo'
for arg in "\$@"; do
  printf ' [%s]' "\$arg"
done
printf '\n' >> "$LOG_FILE"
exec "\$@"
FAKE_SUDO
chmod +x "$BIN_DIR/fake-sudo"

(
  cd "$ROOT_DIR"
  UNIT_DIR="$UNIT_DIR" \
  SYSTEMCTL_BIN="$BIN_DIR/fake-systemctl" \
  SUDO_BIN="$BIN_DIR/fake-sudo" \
  bash adaptor/install-systemd-service.sh \
    --name test-jibot \
    --adapter-dir "$ROOT_DIR/adaptor" \
    --user test-user \
    --group test-group \
    --args "--instance SIM-001 --simulator"
) > "$LOG_FILE"

UNIT_FILE="$UNIT_DIR/test-jibot.service"
if [[ ! -f "$UNIT_FILE" ]]; then
  echo "Missing generated unit file: $UNIT_FILE" >&2
  exit 1
fi

assert_contains() {
  local file="$1"
  local expected="$2"
  if ! grep -Fq "$expected" "$file"; then
    echo "Expected to find in $file: $expected" >&2
    echo "--- $file ---" >&2
    cat "$file" >&2
    exit 1
  fi
}

assert_contains "$UNIT_FILE" "[Unit]"
assert_contains "$UNIT_FILE" "Description=AMR VDA5050 Adapter"
assert_contains "$UNIT_FILE" "WorkingDirectory=$ROOT_DIR/adaptor"
assert_contains "$UNIT_FILE" "User=test-user"
assert_contains "$UNIT_FILE" "Group=test-group"
assert_contains "$UNIT_FILE" "Environment=PYTHONUNBUFFERED=1"
assert_contains "$UNIT_FILE" "ExecStart=$ROOT_DIR/adaptor/run-adapter.sh --instance SIM-001 --simulator"
assert_contains "$UNIT_FILE" "Restart=always"
assert_contains "$UNIT_FILE" "RestartSec=5"
assert_contains "$UNIT_FILE" "WantedBy=multi-user.target"

assert_contains "$LOG_FILE" "systemctl [daemon-reload]"
assert_contains "$LOG_FILE" "systemctl [enable] [test-jibot.service]"
assert_contains "$LOG_FILE" "systemctl [restart] [test-jibot.service]"
assert_contains "$LOG_FILE" "systemctl [status] [test-jibot.service] [--no-pager]"

echo "PASS install-systemd-service"
