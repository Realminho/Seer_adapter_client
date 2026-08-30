#!/usr/bin/env bash
# Fetches JIBOT map and route parameter files from a remote host over SSH.
# File contents are streamed with tar over SSH, so scp/rsync is not required.
# SSH connection sharing is used so password authentication is requested once.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  cat >&2 <<'USAGE'
Usage:
  scripts/fetch-jibot-params-over-ssh.sh <user@host> [local_output_dir]

Examples:
  scripts/fetch-jibot-params-over-ssh.sh ucore@192.168.3.10
  scripts/fetch-jibot-params-over-ssh.sh ucore@192.168.3.10 jibot/params

Environment:
  SSH_PORT=10020               SSH port override (미지정 시 ~/.ssh/config 값)
  SSH_OPTS="-i ~/.ssh/key"     Additional ssh options
  BACKUP_LOCAL=1               Back up existing local map/routes before overwrite
  LOCAL_BACKUP_DIR=...         Backup root override

Remote source:
  MAP    /usr/local/urobot/params/map/
  ROUTE  /usr/local/urobot/params/routes/

Local destination:
  <local_output_dir>/map/
  <local_output_dir>/routes/
USAGE
  exit 2
fi

REMOTE_HOST="$1"
LOCAL_OUTPUT_DIR="${2:-jibot/params}"
# SSH_PORT 를 비워 두면 -p 를 아예 넘기지 않는다. 명시적 -p 는 ~/.ssh/config 의
# Port 를 덮어쓰기 때문이다 — 2026-08-26 `ucore@amr2`(config 상 Port 10020) 로
# 받으려다 하드코딩된 -p 22 가 config 를 눌러 22번으로 붙어 인증 거부됐다.
# 값이 없으면 ssh 자신의 해석(config 의 Port, 없으면 22)에 맡긴다.
SSH_PORT="${SSH_PORT:-}"
SSH_OPTS="${SSH_OPTS:-}"
BACKUP_LOCAL="${BACKUP_LOCAL:-1}"
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-jibot/backups/params}"
REMOTE_PARAMS_DIR="/usr/local/urobot/params"

# ControlPath 소켓은 sockaddr_un 한계(macOS 104바이트)에 들어가야 하는데 "%C" 만
# 40자다. macOS 의 TMPDIR 은 /var/folders/<...>/T/ 라서 그냥 mktemp -d 하면
# 그것만으로 넘긴다 — 2026-08-26 amr2 로 fetch 시 실측 103바이트에서
# "ControlPath too long" 로 실패했다. 제어 디렉터리를 /tmp 에 고정해 짧게 유지한다.
# (선례: scripts/update-jibot-adapter-over-ssh.sh)
CONTROL_DIR="$(mktemp -d /tmp/amr-ssh.XXXXXX)"

ssh_base=( ssh )
if [[ -n "$SSH_PORT" ]]; then
  ssh_base+=( -p "$SSH_PORT" )
fi
ssh_base+=(
  -o ControlMaster=auto
  -o ControlPersist=60
  -o "ControlPath=$CONTROL_DIR/%C"
)
if [[ -n "$SSH_OPTS" ]]; then
  # shellcheck disable=SC2206
  ssh_base+=( $SSH_OPTS )
fi

cleanup() {
  "${ssh_base[@]}" -O exit "$REMOTE_HOST" >/dev/null 2>&1 || true
  rm -rf "$CONTROL_DIR"
}
trap cleanup EXIT

if [[ "$BACKUP_LOCAL" != "0" && "$BACKUP_LOCAL" != "1" ]]; then
  echo "Invalid BACKUP_LOCAL: $BACKUP_LOCAL (expected 1 or 0)" >&2
  exit 2
fi

mkdir -p "$LOCAL_OUTPUT_DIR"

if [[ "$BACKUP_LOCAL" == "1" && ( -e "$LOCAL_OUTPUT_DIR/map" || -e "$LOCAL_OUTPUT_DIR/routes" ) ]]; then
  backup_stamp="$(date +%Y%m%d-%H%M%S)"
  backup_dir="$LOCAL_BACKUP_DIR/$backup_stamp"
  backup_suffix=1
  while [[ -e "$backup_dir" ]]; do
    backup_dir="$LOCAL_BACKUP_DIR/$backup_stamp-$backup_suffix"
    backup_suffix=$((backup_suffix + 1))
  done
  mkdir -p "$backup_dir"
  if [[ -e "$LOCAL_OUTPUT_DIR/map" ]]; then
    cp -a "$LOCAL_OUTPUT_DIR/map" "$backup_dir/map"
  fi
  if [[ -e "$LOCAL_OUTPUT_DIR/routes" ]]; then
    cp -a "$LOCAL_OUTPUT_DIR/routes" "$backup_dir/routes"
  fi
  echo "Backed up local map/routes to $backup_dir"
fi

echo "Fetching JIBOT params from $REMOTE_HOST:$REMOTE_PARAMS_DIR to $LOCAL_OUTPUT_DIR"
rm -rf "$LOCAL_OUTPUT_DIR/map" "$LOCAL_OUTPUT_DIR/routes"
"${ssh_base[@]}" "$REMOTE_HOST" "
  set -e
  test -d '$REMOTE_PARAMS_DIR/map'
  test -d '$REMOTE_PARAMS_DIR/routes'
  tar -C '$REMOTE_PARAMS_DIR' -cf - map routes
" | tar -C "$LOCAL_OUTPUT_DIR" -xvf - | while IFS= read -r path; do
  case "$path" in
    ./) continue ;;
    ./*) path="${path#./}" ;;
  esac
  printf 'Fetched %s/%s\n' "$LOCAL_OUTPUT_DIR" "$path"
done

echo "Done. MAP and ROUTE files are under $LOCAL_OUTPUT_DIR"
