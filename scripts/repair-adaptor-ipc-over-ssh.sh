#!/usr/bin/env bash
# Run scripts/repair-adaptor-ipc.sh on a remote adapter host over SSH.
#
# The core script is streamed to the robot, so it needs neither a repo checkout
# nor an up-to-date one there. The core self-elevates via sudo only when a repair
# is needed (--check stays unprivileged); the run uses a TTY so that sudo can
# prompt for a password if the remote user is not NOPASSWD.
#
# Usage:
#   scripts/repair-adaptor-ipc-over-ssh.sh [--check] [--no-restart] <user@host>
#
# Examples:
#   scripts/repair-adaptor-ipc-over-ssh.sh ubuntu@192.168.3.222
#   scripts/repair-adaptor-ipc-over-ssh.sh --check ubuntu@192.168.3.222
#
# Environment:
#   SSH_PORT=22                  SSH port override
#   SSH_OPTS="-i ~/.ssh/key"     Additional ssh options (word-split)
#   SSH_CONNECT_TIMEOUT=10       SSH connection timeout seconds
#   SSH_CONTROL_MASTER=1         Use SSH connection sharing; set 0 to disable
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$SCRIPT_DIR/repair-adaptor-ipc.sh"

SSH_PORT="${SSH_PORT:-22}"
SSH_OPTS="${SSH_OPTS:-}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"
SSH_CONTROL_MASTER="${SSH_CONTROL_MASTER:-1}"

usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; }

REMOTE_HOST=""
PASS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check|--no-restart) PASS_ARGS+=("$1") ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      if [[ -n "$REMOTE_HOST" ]]; then
        echo "ERROR: unexpected extra argument: $1" >&2; exit 2
      fi
      REMOTE_HOST="$1"
      ;;
  esac
  shift
done

if [[ -z "$REMOTE_HOST" ]]; then
  echo "ERROR: missing <user@host>" >&2
  usage >&2
  exit 2
fi
if [[ ! -f "$CORE" ]]; then
  echo "ERROR: core script not found: $CORE" >&2
  exit 1
fi
if [[ "$REMOTE_HOST" != *@* ]]; then
  echo "WARNING: target has no user part: $REMOTE_HOST (usually ubuntu@$REMOTE_HOST)" >&2
fi

# shellcheck disable=SC2206  # SSH_OPTS is intentionally word-split
ssh_base=(ssh -p "$SSH_PORT" -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" $SSH_OPTS)
cleanup() { :; }
if [[ "$SSH_CONTROL_MASTER" == "1" ]]; then
  CTL="$(mktemp -u "${TMPDIR:-/tmp}/repair-ipc-ssh.XXXXXX")"
  ssh_base+=(-o ControlMaster=auto -o ControlPath="$CTL" -o ControlPersist=60)
  cleanup() { "${ssh_base[@]}" -O exit "$REMOTE_HOST" >/dev/null 2>&1 || true; }
fi
trap cleanup EXIT

REMOTE_TMP="/tmp/repair-adaptor-ipc.$$.sh"

echo "==> uploading repair-adaptor-ipc.sh to $REMOTE_HOST:$REMOTE_TMP"
"${ssh_base[@]}" "$REMOTE_HOST" "cat > '$REMOTE_TMP' && chmod 0700 '$REMOTE_TMP'" < "$CORE"

echo "==> running on $REMOTE_HOST: repair-adaptor-ipc.sh ${PASS_ARGS[*]:-} (self-elevates if repairing)"
# The leading `stty sane` undoes the raw termios that connection sharing copies
# onto the remote pty: -onlcr stair-steps the output and -icanon/-echo break the
# sudo password prompt the repair script may raise.
"${ssh_base[@]}" -t "$REMOTE_HOST" \
  "if [ -t 0 ]; then stty sane 2>/dev/null || true; fi; bash '$REMOTE_TMP' ${PASS_ARGS[*]:-}; rc=\$?; rm -f '$REMOTE_TMP'; exit \$rc"
