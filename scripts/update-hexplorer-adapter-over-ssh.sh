#!/usr/bin/env bash
# Updates the Hexplorer adapter files on a remote host over SSH.
# File contents are streamed with tar over SSH, so scp/rsync is not required.
# SSH connection sharing is used so password authentication is requested once.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/update-hexplorer-adapter-over-ssh.sh [options] <user@host> [remote_adapter_dir]

Examples:
  scripts/update-hexplorer-adapter-over-ssh.sh robot@192.168.12.1
  scripts/update-hexplorer-adapter-over-ssh.sh robot@192.168.12.1 /home/robot/adapter
  scripts/update-hexplorer-adapter-over-ssh.sh --restart robot@192.168.12.1

Options:
  --restart                    Restart the adapter services after upload
  --no-restart                 Do not restart the adapter services (default)
  --restart-cmd CMD            Custom remote command to run after upload (overrides --restart)
  -h, --help                   Show this help

Environment:
  SSH_PORT=22                  SSH port override
  SSH_OPTS="-i ~/.ssh/key"     Additional ssh options
  CLEAN_REMOTE=1               Remove managed adapter files before upload, preserving config/config.toml
  CONFIG_TOML_MODE=ask|keep|overwrite
                               How to handle an existing remote config/config.toml
  INCLUDE_OFFLINE_PACKAGES=1   Upload offline_packages/ wheels for offline pip installs (0 to skip)
  RESTART_ADAPTER=1            Restart the adapter services after upload
  RESTART_CMD="..."            Optional custom remote restart command (overrides RESTART_ADAPTER)
USAGE
}

SSH_PORT="${SSH_PORT:-22}"
SSH_OPTS="${SSH_OPTS:-}"
CLEAN_REMOTE="${CLEAN_REMOTE:-0}"
CONFIG_TOML_MODE="${CONFIG_TOML_MODE:-ask}"
INCLUDE_OFFLINE_PACKAGES="${INCLUDE_OFFLINE_PACKAGES:-1}"
RESTART_ADAPTER="${RESTART_ADAPTER:-0}"
RESTART_CMD="${RESTART_CMD:-}"

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --restart)
      RESTART_ADAPTER=1
      shift
      ;;
    --no-restart)
      RESTART_ADAPTER=0
      shift
      ;;
    --restart-cmd)
      [[ $# -ge 2 && -n "${2:-}" ]] || { echo "Missing value for --restart-cmd" >&2; exit 2; }
      RESTART_CMD="$2"
      shift 2
      ;;
    --restart-cmd=*)
      [[ -n "${1#*=}" ]] || { echo "Missing value for --restart-cmd" >&2; exit 2; }
      RESTART_CMD="${1#*=}"
      shift
      ;;
    --)
      shift
      POSITIONAL+=("$@")
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL[@]} -lt 1 || ${#POSITIONAL[@]} -gt 2 ]]; then
  usage >&2
  exit 2
fi

if [[ "$RESTART_ADAPTER" != "0" && "$RESTART_ADAPTER" != "1" ]]; then
  echo "Invalid RESTART_ADAPTER: $RESTART_ADAPTER (expected 1 or 0)" >&2
  exit 2
fi

REMOTE_HOST="${POSITIONAL[0]}"
REMOTE_ADAPTER_DIR="${POSITIONAL[1]:-}"
LOCAL_ADAPTER_DIR="adaptor"
LOCAL_HEXPLORER_CLIENT_DIR="hexplorer-client/src/hexplorer_client"
LOCAL_SERVICE_HELPER="scripts/adaptor-services.sh"

# ControlPath 소켓은 sockaddr_un 한계(macOS 104바이트)에 들어가야 하는데 "%C" 만
# 40자다. macOS 의 TMPDIR 은 /var/folders/<...>/T/ 라서 그냥 mktemp -d 하면
# 그것만으로 넘긴다 — 2026-08-26 amr2 로 fetch 시 실측 103바이트에서
# "ControlPath too long" 로 실패했다. 제어 디렉터리를 /tmp 에 고정해 짧게 유지한다.
# (선례: scripts/update-jibot-adapter-over-ssh.sh)
CONTROL_DIR="$(mktemp -d /tmp/amr-ssh.XXXXXX)"

ssh_base=(
  ssh
  -p "$SSH_PORT"
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

remote_quote() {
  printf "%q" "$1"
}

prompt_config_toml_mode() {
  if [[ ! -r /dev/tty ]]; then
    echo "No interactive terminal found. Keeping existing remote config/config.toml." >&2
    echo "keep"
    return
  fi

  while true; do
    printf "Remote config/config.toml already exists. Keep or overwrite? [K/o] " > /dev/tty
    IFS= read -r reply < /dev/tty || reply=""
    case "${reply,,}" in
      ""|k|keep)
        echo "keep"
        return
        ;;
      o|overwrite)
        echo "overwrite"
        return
        ;;
      *)
        echo "Please choose K to keep or O to overwrite." > /dev/tty
        ;;
    esac
  done
}

if [[ -z "$REMOTE_ADAPTER_DIR" ]]; then
  remote_dir_expr='"$HOME/adapter"'
  remote_dir_display='~/adapter'
else
  remote_dir_expr="$(remote_quote "$REMOTE_ADAPTER_DIR")"
  remote_dir_display="$REMOTE_ADAPTER_DIR"
fi

if [[ ! -d "$LOCAL_ADAPTER_DIR" ]]; then
  echo "Missing local adapter directory: $LOCAL_ADAPTER_DIR" >&2
  exit 1
fi

if [[ ! -d "$LOCAL_HEXPLORER_CLIENT_DIR" ]]; then
  echo "Missing local Hexplorer client package: $LOCAL_HEXPLORER_CLIENT_DIR" >&2
  exit 1
fi

[[ -r "$LOCAL_SERVICE_HELPER" ]] || {
  echo "Missing or unreadable local service helper: $LOCAL_SERVICE_HELPER" >&2
  exit 1
}

if [[ "$INCLUDE_OFFLINE_PACKAGES" != "0" && "$INCLUDE_OFFLINE_PACKAGES" != "1" ]]; then
  echo "Invalid INCLUDE_OFFLINE_PACKAGES: $INCLUDE_OFFLINE_PACKAGES (expected 1 or 0)" >&2
  exit 2
fi

"${ssh_base[@]}" "$REMOTE_HOST" "mkdir -p $remote_dir_expr"

config_toml_action="create"
if "${ssh_base[@]}" "$REMOTE_HOST" "test -f $remote_dir_expr/config/config.toml"; then
  case "${CONFIG_TOML_MODE,,}" in
    ask)
      config_toml_action="$(prompt_config_toml_mode)"
      ;;
    keep|preserve)
      config_toml_action="keep"
      ;;
    overwrite|replace)
      config_toml_action="overwrite"
      ;;
    *)
      echo "Invalid CONFIG_TOML_MODE: $CONFIG_TOML_MODE (expected ask, keep, or overwrite)" >&2
      exit 2
      ;;
  esac
fi

if [[ "$CLEAN_REMOTE" == "1" ]]; then
  # Only purge remote offline_packages when we are about to re-upload them,
  # so disabling INCLUDE_OFFLINE_PACKAGES leaves existing remote deps intact.
  if [[ "$INCLUDE_OFFLINE_PACKAGES" == "1" ]]; then
    clean_offline=" offline_packages"
  else
    clean_offline=""
  fi
  echo "Removing managed Hexplorer adapter files from $REMOTE_HOST:$remote_dir_display"
  "${ssh_base[@]}" "$REMOTE_HOST" "
    set -e
    cd $remote_dir_expr
    rm -rf \
      main_hexplorer.py \
      adapter_hexplorer.py \
      requirements.txt \
      readme.md \
      core \
      extensions \
      web \
      utils \
      protocol \
      hexplorer_client${clean_offline}
    if [ -d config ]; then
      find config -depth -mindepth 1 ! -name config.toml -exec rm -rf {} +
    fi
  "
fi

echo "Uploading $LOCAL_ADAPTER_DIR to $REMOTE_HOST:$remote_dir_display"
# Never ship local-only or non-relocatable artifacts: the dev venv is an x86
# build with absolute paths baked in, logs are local runtime noise, and .git
# is repo metadata. External deps travel as wheels under offline_packages.
adapter_tar_excludes=(
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='./.git'
  --exclude='./logs'
  --exclude='./venvJIBOT'
  --exclude='./config/config.toml'
  --exclude='config/config.toml'
)
if [[ "$INCLUDE_OFFLINE_PACKAGES" != "1" ]]; then
  adapter_tar_excludes+=( --exclude='./offline_packages' )
fi
tar \
  -C "$LOCAL_ADAPTER_DIR" \
  "${adapter_tar_excludes[@]}" \
  -cf - \
  . | "${ssh_base[@]}" "$REMOTE_HOST" "
    set -e
    tar -C $remote_dir_expr -xvf - | while IFS= read -r path; do
      case \"\$path\" in
        ./) continue ;;
        ./*) path=\"\${path#./}\" ;;
      esac
      printf 'Updated %s:%s/%s\n' '$REMOTE_HOST' '$remote_dir_display' \"\$path\"
    done
  "

echo "Uploading $LOCAL_HEXPLORER_CLIENT_DIR to $REMOTE_HOST:$remote_dir_display/hexplorer_client"
tar \
  -C "hexplorer-client/src" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -cf - \
  hexplorer_client | "${ssh_base[@]}" "$REMOTE_HOST" "
    set -e
    tar -C $remote_dir_expr -xvf - | while IFS= read -r path; do
      case \"\$path\" in
        ./) continue ;;
        ./*) path=\"\${path#./}\" ;;
      esac
      printf 'Updated %s:%s/%s\n' '$REMOTE_HOST' '$remote_dir_display' \"\$path\"
    done
  "

echo "Uploading service helper to $REMOTE_HOST:$remote_dir_display/scripts/adaptor-services.sh"
"${ssh_base[@]}" "$REMOTE_HOST" \
  "mkdir -p $remote_dir_expr/scripts && cat > $remote_dir_expr/scripts/adaptor-services.sh && chmod +x $remote_dir_expr/scripts/adaptor-services.sh" \
  < "$LOCAL_SERVICE_HELPER"

case "$config_toml_action" in
  create)
    echo "Creating missing $REMOTE_HOST:$remote_dir_display/config/config.toml"
    "${ssh_base[@]}" "$REMOTE_HOST" "mkdir -p $remote_dir_expr/config && cat > $remote_dir_expr/config/config.toml" < "$LOCAL_ADAPTER_DIR/config/config.toml"
    ;;
  overwrite)
    echo "Overwriting $REMOTE_HOST:$remote_dir_display/config/config.toml"
    "${ssh_base[@]}" "$REMOTE_HOST" "mkdir -p $remote_dir_expr/config && cat > $remote_dir_expr/config/config.toml" < "$LOCAL_ADAPTER_DIR/config/config.toml"
    ;;
  keep)
    echo "Keeping existing $REMOTE_HOST:$remote_dir_display/config/config.toml"
    ;;
esac

restart_command=""
if [[ -n "$RESTART_CMD" ]]; then
  echo "Running restart command on $REMOTE_HOST"
  restart_command="$RESTART_CMD"
elif [[ "$RESTART_ADAPTER" == "1" ]]; then
  echo "Restarting adapter services on $REMOTE_HOST"
  restart_command="cd $remote_dir_expr && scripts/adaptor-services.sh restart"
fi

if [[ -n "$restart_command" ]]; then
  # ssh has to inherit this shell's stdin: with connection sharing, handing it a
  # separately opened /dev/tty makes the mux master copy raw termios onto the
  # remote pty (-onlcr stair-steps the output) and swallow every keystroke, so a
  # remote sudo prompt can never be answered. The fd-3 form is the fallback for
  # a non-tty stdin, where the leading `stty sane` repairs the pty modes.
  if [[ -t 0 ]]; then
    "${ssh_base[@]}" -tt "$REMOTE_HOST" "$restart_command"
  elif { exec 3</dev/tty; } 2>/dev/null; then
    "${ssh_base[@]}" -tt "$REMOTE_HOST" \
      "if [ -t 0 ]; then stty sane 2>/dev/null || true; fi; $restart_command" <&3
    exec 3<&-
  else
    "${ssh_base[@]}" "$REMOTE_HOST" "$restart_command"
  fi
fi

echo "Done. Hexplorer adapter updated on $REMOTE_HOST:$remote_dir_display"
