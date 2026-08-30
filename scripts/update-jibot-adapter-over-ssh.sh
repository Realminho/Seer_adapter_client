#!/usr/bin/env bash
# Updates the full Jibot adapter directory on a remote host over SSH.
# File contents are streamed with tar over SSH, so scp/rsync is not required.
# SSH connection sharing is used so password authentication is requested once.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/update-jibot-adapter-over-ssh.sh [options] <user@host> [user@host ...]

Updates every listed host with the same bundle. If a host fails, the remaining
hosts are still attempted and the failures are summarised at the end.

Examples:
  scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.10
  scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.222 ucore@192.168.3.223
  scripts/update-jibot-adapter-over-ssh.sh --remote-dir /home/ucore/adaptor ucore@192.168.3.10
  scripts/update-jibot-adapter-over-ssh.sh --robots-hcl-mode overwrite ucore@192.168.3.10
  scripts/update-jibot-adapter-over-ssh.sh --configure-device ucore@192.168.3.10

Options:
  --remote-dir DIR             Remote adaptor directory (default: ~/adaptor); applies to every host
  --config-toml-mode keep|overwrite|ask
                               How to handle remote config/config.toml; default keeps existing config
  --robots-hcl-mode keep|overwrite|ask
                               How to handle remote config/robots.hcl. Default keeps an
                               existing one; if the robot has none but still has a
                               robots.toml, that is converted in place so the robot keeps
                               its own id/IPs. overwrite installs the local robots.hcl,
                               which names a different robot -- rarely what you want.
  --extensions-hcl-mode keep|overwrite|ask
                               How to handle remote config/extensions.hcl. Default keeps an
                               existing one (site hardware tuning) and seeds it only when the
                               robot has none. overwrite is what ships new PIO signal names
                               and pin maps that new recipes depend on.
  --recipes-hcl-mode keep|overwrite|ask
                               How to handle remote config/recipes.hcl. Default keeps an
                               existing one and seeds it only when the robot has none.
                               overwrite ships changed recipes; pair it with
                               --extensions-hcl-mode overwrite when the recipes use new
                               signal names, or they fail at run time as undeclared.
  --configure-device           Install this repo's config/extensions.hcl and
                               config/recipes.hcl over the robot's copies, keeping a
                               timestamped .bak-* of each. Same as passing both
                               --extensions-hcl-mode overwrite and --recipes-hcl-mode
                               overwrite. Use when setting a robot up or bringing a
                               lagging one back to the site standard; per-robot
                               hardware values (USB port, vehicle_num, measured clamp
                               positions) belong in config/robots.hcl extension blocks,
                               which this never touches, so they survive the overwrite.
  --clean-remote               Remove managed adapter files before upload
  --install-py-deps            Install Python deps from offline_packages after upload
  --no-install-py-deps         Skip Python dependency installation (default)
  --copy-venv                  Upload the local adaptor/.venv to the remote (offline);
                               retires legacy venvJIBOT and verifies it runs there
  --no-copy-venv               Do not upload a venv (default)
  --restart                    Stop services before install and start them afterward (default)
  --no-restart                 Do not stop or start adapter services
  --restart-cmd CMD            Custom remote start command after install; services are still stopped first
  -h, --help                   Show this help

Environment:
  SSH_PORT=22                  SSH port override
  SSH_OPTS="-i ~/.ssh/key"     Additional ssh options; legacy JIBOT crypto is enabled by default
  SSH_CONNECT_TIMEOUT=10       SSH connection timeout seconds
  SSH_SERVER_ALIVE_INTERVAL=10 SSH keepalive interval seconds
  SSH_SERVER_ALIVE_COUNT_MAX=3 SSH keepalive failure count before disconnect
  SSH_CONTROL_MASTER=1         Use SSH connection sharing; set 0 to debug plain ssh behavior
  CONFIGURE_DEVICE=1           Same as --configure-device
  CLEAN_REMOTE=1               Remove managed adapter files before upload, preserving config/config.toml
  INSTALL_PY_DEPS=1            Install Python deps from offline_packages after upload
  COPY_VENV=1                  Upload local adaptor/.venv to remote ~/adaptor/.venv (offline)
  CONFIG_TOML_MODE=keep|overwrite|ask
                               How to handle remote config/config.toml; default keeps existing config
  ROBOTS_HCL_MODE=keep|overwrite|ask
                               How to handle remote config/robots.hcl. Default keeps an
                               existing one; if the robot has none but still has a
                               robots.toml, that is converted in place so the robot keeps
                               its own id/IPs. overwrite installs the local robots.hcl,
                               which names a different robot -- rarely what you want.
  EXTENSIONS_HCL_MODE=keep|overwrite|ask
                               How to handle remote config/extensions.hcl; default keeps the
                               robot's site hardware tuning
  RECIPES_HCL_MODE=keep|overwrite|ask
                               How to handle remote config/recipes.hcl; default keeps the
                               robot's site workflow data
  RESTART_ADAPTER=0            Do not stop/start adapter services (default is 1)
  ADAPTER_SERVICE=amr-adaptor.service
                               Base unit controlled when no amr-adaptor*.service is found
  RESTART_CMD="..."            Optional custom remote start command (overrides RESTART_ADAPTER)
USAGE
}

SSH_PORT="${SSH_PORT:-22}"
SSH_OPTS="${SSH_OPTS:-}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"
SSH_SERVER_ALIVE_INTERVAL="${SSH_SERVER_ALIVE_INTERVAL:-10}"
SSH_SERVER_ALIVE_COUNT_MAX="${SSH_SERVER_ALIVE_COUNT_MAX:-3}"
SSH_CONTROL_MASTER="${SSH_CONTROL_MASTER:-1}"
CLEAN_REMOTE="${CLEAN_REMOTE:-0}"
INSTALL_PY_DEPS="${INSTALL_PY_DEPS:-0}"
COPY_VENV="${COPY_VENV:-0}"
RESTART_ADAPTER="${RESTART_ADAPTER:-1}"
ADAPTER_SERVICE="${ADAPTER_SERVICE:-amr-adaptor.service}"
CONFIG_TOML_MODE="${CONFIG_TOML_MODE:-keep}"
ROBOTS_HCL_MODE="${ROBOTS_HCL_MODE:-keep}"
EXTENSIONS_HCL_MODE="${EXTENSIONS_HCL_MODE:-keep}"
RECIPES_HCL_MODE="${RECIPES_HCL_MODE:-keep}"
# 기기 세팅용 한 방 스위치. 아래 arg 파싱에서 --configure-device도 같은 두 값을
# 건드리므로, 뒤에 오는 명시 플래그가 이긴다.
if [[ "${CONFIGURE_DEVICE:-0}" == "1" ]]; then
  EXTENSIONS_HCL_MODE=overwrite
  RECIPES_HCL_MODE=overwrite
fi

REMOTE_HOSTS=()
REMOTE_ADAPTER_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --remote-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for --remote-dir" >&2; exit 2; }
      REMOTE_ADAPTER_DIR="$2"
      shift 2
      ;;
    --remote-dir=*)
      REMOTE_ADAPTER_DIR="${1#*=}"
      shift
      ;;
    --config-toml-mode)
      [[ $# -ge 2 ]] || { echo "Missing value for --config-toml-mode" >&2; exit 2; }
      CONFIG_TOML_MODE="$2"
      shift 2
      ;;
    --config-toml-mode=*)
      CONFIG_TOML_MODE="${1#*=}"
      shift
      ;;
    --robots-hcl-mode)
      [[ $# -ge 2 ]] || { echo "Missing value for --robots-hcl-mode" >&2; exit 2; }
      ROBOTS_HCL_MODE="$2"
      shift 2
      ;;
    --robots-hcl-mode=*)
      ROBOTS_HCL_MODE="${1#*=}"
      shift
      ;;
    --extensions-hcl-mode)
      [[ $# -ge 2 ]] || { echo "Missing value for --extensions-hcl-mode" >&2; exit 2; }
      EXTENSIONS_HCL_MODE="$2"
      shift 2
      ;;
    --extensions-hcl-mode=*)
      EXTENSIONS_HCL_MODE="${1#*=}"
      shift
      ;;
    --recipes-hcl-mode)
      [[ $# -ge 2 ]] || { echo "Missing value for --recipes-hcl-mode" >&2; exit 2; }
      RECIPES_HCL_MODE="$2"
      shift 2
      ;;
    --recipes-hcl-mode=*)
      RECIPES_HCL_MODE="${1#*=}"
      shift
      ;;
    --configure-device)
      EXTENSIONS_HCL_MODE=overwrite
      RECIPES_HCL_MODE=overwrite
      shift
      ;;
    --clean-remote)
      CLEAN_REMOTE=1
      shift
      ;;
    --install-py-deps)
      INSTALL_PY_DEPS=1
      shift
      ;;
    --no-install-py-deps)
      INSTALL_PY_DEPS=0
      shift
      ;;
    --copy-venv)
      COPY_VENV=1
      shift
      ;;
    --no-copy-venv)
      COPY_VENV=0
      shift
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
      [[ $# -ge 2 ]] || { echo "Missing value for --restart-cmd" >&2; exit 2; }
      RESTART_CMD="$2"
      shift 2
      ;;
    --restart-cmd=*)
      RESTART_CMD="${1#*=}"
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        REMOTE_HOSTS+=("$1")
        shift
      done
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      REMOTE_HOSTS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#REMOTE_HOSTS[@]} -eq 0 ]]; then
  usage >&2
  exit 2
fi

for host in "${REMOTE_HOSTS[@]}"; do
  if [[ "$host" != *@* ]]; then
    echo "WARNING: remote target has no user part: $host" >&2
    echo "         This script is usually run as: scripts/update-jibot-adapter-over-ssh.sh ucore@$host" >&2
  fi
done
LOCAL_ADAPTER_DIR="adaptor"
LOCAL_JIBOT_CLIENT_DIR="jibot-client/src/jibot_client"
LOCAL_JIBOT_SIMULATOR_DIR="jibot-simulator"
LOCAL_SCRIPTS_DIR="scripts"
WEB_VIDEO_OFFLINE_DEB_DIR="$LOCAL_SCRIPTS_DIR/offline-debs/web-video-server"
WEB_VIDEO_REQUIRED_DEB_PATTERNS=(
  "ros-noetic-async-web-server-cpp_*_arm64.deb"
  "ros-noetic-web-video-server_*_arm64.deb"
  "ffmpeg_*_arm64.deb"
  "libavcodec-dev_*_arm64.deb"
  "libavformat-dev_*_arm64.deb"
  "libavutil-dev_*_arm64.deb"
  "libswscale-dev_*_arm64.deb"
)

# The ControlPath socket must fit sockaddr_un (104 bytes on macOS) and "%C"
# alone is 40 chars. macOS TMPDIR is /var/folders/<...>/T/, so a plain
# mktemp -d there already overruns it ("ControlPath too long"); pin the
# control dir under /tmp to keep the socket path short on every platform.
CONTROL_DIR="$(mktemp -d /tmp/amr-ssh.XXXXXX)"
STAGING_DIR=""

ssh_base=(
  ssh
  -p "$SSH_PORT"
  -o "ConnectTimeout=$SSH_CONNECT_TIMEOUT"
  -o ConnectionAttempts=1
  -o "ServerAliveInterval=$SSH_SERVER_ALIVE_INTERVAL"
  -o "ServerAliveCountMax=$SSH_SERVER_ALIVE_COUNT_MAX"
  -o KexAlgorithms=+diffie-hellman-group14-sha1
  -o Ciphers=+aes128-cbc
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
)
if [[ "$SSH_CONTROL_MASTER" == "1" ]]; then
  ssh_base+=(
    -o ControlMaster=auto
    -o ControlPersist=60
    -o "ControlPath=$CONTROL_DIR/%C"
  )
elif [[ "$SSH_CONTROL_MASTER" != "0" ]]; then
  echo "Invalid SSH_CONTROL_MASTER: $SSH_CONTROL_MASTER (expected 1 or 0)" >&2
  exit 2
fi
if [[ -n "$SSH_OPTS" ]]; then
  # shellcheck disable=SC2206
  ssh_base+=( $SSH_OPTS )
fi

# bash 3.2 (the macOS system bash this script is usually launched with) has no
# ${var,,} expansion, so lowercasing goes through tr instead.
lower() {
  printf %s "$1" | tr "[:upper:]" "[:lower:]"
}

# "ssh -tt" switches this terminal to raw mode for the interactive stop/start
# steps and restores it when it exits normally. A Ctrl-C at the remote sudo
# prompt kills it first and leaves the shell with no echo, so snapshot the modes
# up front and put them back from the EXIT trap.
SAVED_TTY_MODES=""
if [[ -r /dev/tty ]]; then
  SAVED_TTY_MODES="$(stty -g < /dev/tty 2>/dev/null || true)"
fi

cleanup() {
  if [[ -n "$SAVED_TTY_MODES" && -r /dev/tty ]]; then
    stty "$SAVED_TTY_MODES" < /dev/tty 2>/dev/null || true
  fi
  if [[ "$SSH_CONTROL_MASTER" == "1" && ${#REMOTE_HOSTS[@]} -gt 0 ]]; then
    for host in "${REMOTE_HOSTS[@]}"; do
      "${ssh_base[@]}" -O exit "$host" >/dev/null 2>&1 || true
    done
  fi
  [[ -n "$STAGING_DIR" ]] && rm -rf "$STAGING_DIR"
  rm -rf "$CONTROL_DIR"
}
trap cleanup EXIT

prompt_config_toml_mode() {
  if [[ ! -r /dev/tty ]]; then
    echo "No interactive terminal found. Keeping existing remote config/config.toml." >&2
    echo "keep"
    return
  fi

  while true; do
    printf "If remote config/config.toml exists, keep or overwrite? [K/o] " > /dev/tty
    IFS= read -r reply < /dev/tty || reply=""
    case "$(lower "$reply")" in
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

prompt_robots_hcl_mode() {
  if [[ ! -r /dev/tty ]]; then
    echo "No interactive terminal found. Keeping existing remote config/robots.hcl." >&2
    echo "keep"
    return
  fi

  while true; do
    printf "If remote config/robots.hcl exists, keep or overwrite? [K/o] " > /dev/tty
    IFS= read -r reply < /dev/tty || reply=""
    case "$(lower "$reply")" in
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

prompt_keep_or_overwrite() {
  # $1: config file path shown to the operator (e.g. config/recipes.hcl)
  local target="$1"
  if [[ ! -r /dev/tty ]]; then
    echo "No interactive terminal found. Keeping existing remote $target." >&2
    echo "keep"
    return
  fi

  while true; do
    printf "If remote %s exists, keep or overwrite? [K/o] " "$target" > /dev/tty
    IFS= read -r reply < /dev/tty || reply=""
    case "$(lower "$reply")" in
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

validate_web_video_offline_debs() {
  local missing=()
  local pattern

  for pattern in "${WEB_VIDEO_REQUIRED_DEB_PATTERNS[@]}"; do
    if ! compgen -G "$WEB_VIDEO_OFFLINE_DEB_DIR/$pattern" >/dev/null; then
      missing+=("$pattern")
    fi
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    return 0
  fi

  echo "ERROR: missing web_video_server offline debs under $WEB_VIDEO_OFFLINE_DEB_DIR:" >&2
  for pattern in "${missing[@]}"; do
    echo "  - $pattern" >&2
  done
  echo "Do not rely on /var/cache/apt/archives; copy the correct arm64/focal debs into $WEB_VIDEO_OFFLINE_DEB_DIR and re-run update." >&2
  exit 1
}

if [[ -z "$REMOTE_ADAPTER_DIR" ]]; then
  remote_dir_display='~/adaptor'
else
  remote_dir_display="$REMOTE_ADAPTER_DIR"
fi

if [[ ! -d "$LOCAL_ADAPTER_DIR" ]]; then
  echo "Missing local adapter directory: $LOCAL_ADAPTER_DIR" >&2
  exit 1
fi

if [[ ! -d "$LOCAL_JIBOT_CLIENT_DIR" ]]; then
  echo "Missing local JIBOT client package: $LOCAL_JIBOT_CLIENT_DIR" >&2
  exit 1
fi

if [[ ! -d "$LOCAL_JIBOT_SIMULATOR_DIR" ]]; then
  echo "Missing local JIBOT simulator directory: $LOCAL_JIBOT_SIMULATOR_DIR" >&2
  exit 1
fi

if [[ ! -d "$LOCAL_SCRIPTS_DIR" ]]; then
  echo "Missing local scripts directory: $LOCAL_SCRIPTS_DIR" >&2
  exit 1
fi

case "$(lower "$CONFIG_TOML_MODE")" in
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

case "$(lower "$ROBOTS_HCL_MODE")" in
  ask)
    robots_hcl_action="$(prompt_robots_hcl_mode)"
    ;;
  keep|preserve)
    robots_hcl_action="keep"
    ;;
  overwrite|replace)
    robots_hcl_action="overwrite"
    ;;
  *)
    echo "Invalid ROBOTS_HCL_MODE: $ROBOTS_HCL_MODE (expected ask, keep, or overwrite)" >&2
    exit 2
    ;;
esac

case "$(lower "$EXTENSIONS_HCL_MODE")" in
  ask)
    extensions_hcl_action="$(prompt_keep_or_overwrite config/extensions.hcl)"
    ;;
  keep|preserve)
    extensions_hcl_action="keep"
    ;;
  overwrite|replace)
    extensions_hcl_action="overwrite"
    ;;
  *)
    echo "Invalid EXTENSIONS_HCL_MODE: $EXTENSIONS_HCL_MODE (expected ask, keep, or overwrite)" >&2
    exit 2
    ;;
esac

case "$(lower "$RECIPES_HCL_MODE")" in
  ask)
    recipes_hcl_action="$(prompt_keep_or_overwrite config/recipes.hcl)"
    ;;
  keep|preserve)
    recipes_hcl_action="keep"
    ;;
  overwrite|replace)
    recipes_hcl_action="overwrite"
    ;;
  *)
    echo "Invalid RECIPES_HCL_MODE: $RECIPES_HCL_MODE (expected ask, keep, or overwrite)" >&2
    exit 2
    ;;
esac

if [[ "$INSTALL_PY_DEPS" != "1" && "$INSTALL_PY_DEPS" != "0" ]]; then
  echo "Invalid INSTALL_PY_DEPS: $INSTALL_PY_DEPS (expected 1 or 0)" >&2
  exit 2
fi

if [[ "$COPY_VENV" != "1" && "$COPY_VENV" != "0" ]]; then
  echo "Invalid COPY_VENV: $COPY_VENV (expected 1 or 0)" >&2
  exit 2
fi

if [[ "$RESTART_ADAPTER" != "1" && "$RESTART_ADAPTER" != "0" ]]; then
  echo "Invalid RESTART_ADAPTER: $RESTART_ADAPTER (expected 1 or 0)" >&2
  exit 2
fi

validate_web_video_offline_debs

STAGING_DIR="$(mktemp -d)"
mkdir -p "$STAGING_DIR/payload" "$STAGING_DIR/default_config"
cp "$LOCAL_ADAPTER_DIR/config/config.toml" "$STAGING_DIR/default_config/config.toml"
cp "$LOCAL_ADAPTER_DIR/config/extensions.hcl" "$STAGING_DIR/default_config/extensions.hcl"
if [[ -f "$LOCAL_ADAPTER_DIR/config/recipes.hcl" ]]; then
  cp "$LOCAL_ADAPTER_DIR/config/recipes.hcl" "$STAGING_DIR/default_config/recipes.hcl"
fi
if [[ -f "$LOCAL_ADAPTER_DIR/config/robots.hcl" ]]; then
  cp "$LOCAL_ADAPTER_DIR/config/robots.hcl" "$STAGING_DIR/default_config/robots.hcl"
fi

if [[ "$COPY_VENV" == "1" ]]; then
  local_venv="$LOCAL_ADAPTER_DIR/.venv"
  if [[ ! -x "$local_venv/bin/python" ]]; then
    echo "ERROR: --copy-venv set but $local_venv/bin/python is missing." >&2
    echo "       Build it first (ideally against the robot's system Python so the venv's" >&2
    echo "       interpreter path resolves on the robot):" >&2
    echo "         (cd $LOCAL_ADAPTER_DIR && uv venv --python 3.11 && uv pip install -r requirements.txt)" >&2
    exit 1
  fi
  echo "Staging local .venv for copy (symlinks preserved) ..."
  echo "  NOTE: a venv embeds its base-interpreter path and arch — copying works only if the"
  echo "        robot has the same CPU arch and that interpreter path (build .venv with the"
  echo "        robot's /usr/bin/python3.11, not a uv-managed interpreter under ~/.local)."
  mkdir -p "$STAGING_DIR/venv_payload"
  cp -a "$local_venv" "$STAGING_DIR/venv_payload/"
fi

echo "Preparing upload bundle ..."
tar \
  -C "$LOCAL_ADAPTER_DIR" \
  --exclude='.pytest_cache' \
  --exclude='.venv' \
  --exclude='venvJIBOT' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='./runtime' \
  --exclude='runtime' \
  --exclude='./config/config.toml' \
  --exclude='config/config.toml' \
  --exclude='./config/robots.hcl' \
  --exclude='config/robots.hcl' \
  --exclude='./config/extensions.hcl' \
  --exclude='config/extensions.hcl' \
  --exclude='./config/recipes.hcl' \
  --exclude='config/recipes.hcl' \
  --exclude='./mise.toml' \
  --exclude='mise.toml' \
  -cf - \
  . | tar -C "$STAGING_DIR/payload" -xf -
tar \
  -C "jibot-client/src" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -cf - \
  jibot_client | tar -C "$STAGING_DIR/payload" -xf -
tar \
  -C "." \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -cf - \
  "$LOCAL_JIBOT_SIMULATOR_DIR" "$LOCAL_SCRIPTS_DIR" | tar -C "$STAGING_DIR/payload" -xf -

read -r -d '' remote_upload_script <<'REMOTE_SCRIPT' || true
set -euo pipefail

remote_dir="${REMOTE_ADAPTER_DIR_ARG:-}"
if [[ -z "$remote_dir" ]]; then
  remote_dir="$HOME/adaptor"
fi

staging="$(mktemp -d)"
cleanup_remote() {
  rm -rf "$staging"
}
trap cleanup_remote EXIT

echo "[remote] preparing $remote_dir"
mkdir -p "$remote_dir"
tar -C "$staging" -xf -

preserved_config="$staging/preserved-config.toml"
had_config=0
if [[ -f "$remote_dir/config/config.toml" ]]; then
  cp "$remote_dir/config/config.toml" "$preserved_config"
  had_config=1
fi
preserved_robots="$staging/preserved-robots.hcl"
had_robots=0
if [[ -f "$remote_dir/config/robots.hcl" ]]; then
  cp "$remote_dir/config/robots.hcl" "$preserved_robots"
  had_robots=1
fi
preserved_extensions="$staging/preserved-extensions.hcl"
had_extensions=0
if [[ -f "$remote_dir/config/extensions.hcl" ]]; then
  cp "$remote_dir/config/extensions.hcl" "$preserved_extensions"
  had_extensions=1
fi
preserved_recipes="$staging/preserved-recipes.hcl"
had_recipes=0
if [[ -f "$remote_dir/config/recipes.hcl" ]]; then
  cp "$remote_dir/config/recipes.hcl" "$preserved_recipes"
  had_recipes=1
fi
# 덮어쓰기 전에 이 로봇이 갖고 있던 사본을 남긴다. 현장 값이 이 파일에만 있는
# 경우가 있어(전에 손으로 고친 로봇) 덮은 뒤에는 되돌릴 방법이 없다.
config_backup_stamp="$(date +%Y%m%d-%H%M%S)"
backup_remote_config() {
  local name="$1" source="$2"
  local backup="$remote_dir/config/$name.bak-$config_backup_stamp"
  cp "$source" "$backup"
  echo "[remote] backed up config/$name -> config/$(basename "$backup")"
}

if [[ "$CLEAN_REMOTE" == "1" ]]; then
  echo "[remote] removing managed adapter files"
  cd "$remote_dir"
  rm -rf \
    main.py \
    adapter_jibot.py \
    run-main.sh \
    requirements.txt \
    readme.md \
    .gitignore \
    core \
    extensions \
    web \
    utils \
    jibot_client \
    jibot-simulator \
    jibot/simulator \
    protocol \
    offline_packages \
    sounds \
    scripts
  if [[ -d config ]]; then
    # robots.toml is kept alongside robots.hcl for one release: a robot mid
    # migration may still only have robots.toml, and it's the sole copy of
    # that robot's fleet inventory. Drop this exclusion once the fleet-wide
    # robots.toml -> robots.hcl migration is confirmed complete.
    find config -depth -mindepth 1 ! -name config.toml ! -name robots.hcl ! -name robots.toml ! -name extensions.hcl ! -name recipes.hcl -exec rm -rf {} +
  fi
fi

echo "[remote] extracting upload bundle"
cp -a "$staging/payload/." "$remote_dir/"
mkdir -p "$remote_dir/config"

case "$CONFIG_TOML_MODE" in
  keep)
    if [[ "$had_config" == "1" ]]; then
      cp "$preserved_config" "$remote_dir/config/config.toml"
      echo "[remote] kept existing config/config.toml"
    else
      cp "$staging/default_config/config.toml" "$remote_dir/config/config.toml"
      echo "[remote] created missing config/config.toml"
    fi
    ;;
  overwrite)
    cp "$staging/default_config/config.toml" "$remote_dir/config/config.toml"
    echo "[remote] overwrote config/config.toml"
    ;;
  *)
    echo "Invalid CONFIG_TOML_MODE on remote: $CONFIG_TOML_MODE" >&2
    exit 2
    ;;
esac

case "$ROBOTS_HCL_MODE" in
  keep)
    if [[ "$had_robots" == "1" ]]; then
      cp "$preserved_robots" "$remote_dir/config/robots.hcl"
      echo "[remote] kept existing config/robots.hcl"
    elif [[ -f "$remote_dir/config/robots.toml" ]]; then
      # This robot predates the HCL migration. Convert its own robots.toml
      # rather than installing the build machine's robots.hcl: robots.toml is
      # the only copy of this robot's id/IPs, and the local file names a
      # different robot and may be simulator=true. Runs after the payload is
      # extracted, so scripts/ is present.
      echo "[remote] no config/robots.hcl; converting this robot's config/robots.toml"
      convert_py=python3
      [[ -x "$remote_dir/venvJIBOT/bin/python" ]] && convert_py="$remote_dir/venvJIBOT/bin/python"
      [[ -x "$remote_dir/.venv/bin/python" ]] && convert_py="$remote_dir/.venv/bin/python"
      if "$convert_py" "$remote_dir/scripts/convert-robots-toml-to-hcl.py" \
           "$remote_dir/config/robots.toml" \
           -o "$remote_dir/config/robots.hcl"; then
        echo "[remote] created config/robots.hcl from robots.toml"
        echo "[remote]   VERIFY vehicle_ip / ezi_io / ezi_motor / mqtt_host before relying on it"
      else
        # A half-written file would fail the boot with a syntax error instead of
        # the clear 'file not found' guidance, so leave none behind.
        rm -f "$remote_dir/config/robots.hcl"
        echo "[remote] ERROR: robots.toml -> robots.hcl conversion failed; adapter will not start" >&2
      fi
    else
      rm -f "$remote_dir/config/robots.hcl"
      echo "[remote] no existing config/robots.hcl and no robots.toml; left absent"
    fi
    ;;
  overwrite)
    if [[ -f "$staging/default_config/robots.hcl" ]]; then
      cp "$staging/default_config/robots.hcl" "$remote_dir/config/robots.hcl"
      echo "[remote] overwrote config/robots.hcl"
    else
      rm -f "$remote_dir/config/robots.hcl"
      echo "[remote] removed config/robots.hcl (no local file)"
    fi
    ;;
  *)
    echo "Invalid ROBOTS_HCL_MODE on remote: $ROBOTS_HCL_MODE" >&2
    exit 2
    ;;
esac

# extensions.hcl contains site hardware tuning, so keep mode preserves the
# remote copy and only seeds one on first deployment (the file is required for
# startup). overwrite exists because signal names and pin maps live here: a
# recipe shipped with a new signal fails at run time until this file follows.
case "$EXTENSIONS_HCL_MODE" in
  keep)
    if [[ "$had_extensions" == "1" ]]; then
      cp "$preserved_extensions" "$remote_dir/config/extensions.hcl"
      echo "[remote] kept existing config/extensions.hcl"
    else
      cp "$staging/default_config/extensions.hcl" "$remote_dir/config/extensions.hcl"
      echo "[remote] created missing config/extensions.hcl"
    fi
    ;;
  overwrite)
    [[ "$had_extensions" == "1" ]] && backup_remote_config extensions.hcl "$preserved_extensions"
    cp "$staging/default_config/extensions.hcl" "$remote_dir/config/extensions.hcl"
    echo "[remote] overwrote config/extensions.hcl"
    ;;
  *)
    echo "Invalid EXTENSIONS_HCL_MODE on remote: $EXTENSIONS_HCL_MODE" >&2
    exit 2
    ;;
esac

# recipes.hcl is optional and site-owned workflow data. keep mode never touches
# an existing remote copy and seeds the shipped default only when the robot has
# none, so a robot that has never seen this file still gets the recipes it ships
# with. overwrite ships changed recipes.
case "$RECIPES_HCL_MODE" in
  keep)
    if [[ "$had_recipes" == "1" ]]; then
      cp "$preserved_recipes" "$remote_dir/config/recipes.hcl"
      echo "[remote] kept existing config/recipes.hcl"
    elif [[ -f "$staging/default_config/recipes.hcl" ]]; then
      cp "$staging/default_config/recipes.hcl" "$remote_dir/config/recipes.hcl"
      echo "[remote] created missing config/recipes.hcl"
    else
      echo "[remote] no config/recipes.hcl on either side; left absent (recipes disabled)"
    fi
    ;;
  overwrite)
    if [[ -f "$staging/default_config/recipes.hcl" ]]; then
      [[ "$had_recipes" == "1" ]] && backup_remote_config recipes.hcl "$preserved_recipes"
      cp "$staging/default_config/recipes.hcl" "$remote_dir/config/recipes.hcl"
      echo "[remote] overwrote config/recipes.hcl"
    else
      # 로컬에 파일이 없는데 원격을 지우면 그 로봇의 recipe가 통째로 사라진다.
      if [[ "$had_recipes" == "1" ]]; then
        cp "$preserved_recipes" "$remote_dir/config/recipes.hcl"
        echo "[remote] no local config/recipes.hcl to install; kept the existing one"
      else
        echo "[remote] no config/recipes.hcl on either side; left absent (recipes disabled)"
      fi
    fi
    ;;
  *)
    echo "Invalid RECIPES_HCL_MODE on remote: $RECIPES_HCL_MODE" >&2
    exit 2
    ;;
esac

for runner in run-adapter.sh run-main.sh run-hexplorer.sh run_multi.py; do
  [[ -f "$remote_dir/$runner" ]] && chmod +x "$remote_dir/$runner"
done
if [[ -d "$staging/payload/scripts" ]]; then
  while IFS= read -r -d '' uploaded_script; do
    rel="${uploaded_script#"$staging/payload/"}"
    chmod +x "$remote_dir/$rel"
  done < <(find "$staging/payload/scripts" -maxdepth 1 -type f \( -name '*.sh' -o -name '*.py' \) -print0)
fi

if [[ "${COPY_VENV:-0}" == "1" ]]; then
  echo "[remote] installing uploaded .venv (copy mode)"
  src_venv="$staging/venv_payload/.venv"
  if [[ ! -d "$src_venv" ]]; then
    echo "[remote] ERROR: --copy-venv set but no venv_payload/.venv in the bundle" >&2
    exit 1
  fi
  rm -rf "$remote_dir/.venv"
  cp -a "$src_venv" "$remote_dir/.venv"
  # Floor from the uploaded pyproject's requires-python (fallback 3.11).
  min="$(sed -nE 's/^[[:space:]]*requires-python[[:space:]]*=.*>=[[:space:]]*"?([0-9]+\.[0-9]+).*/\1/p' "$remote_dir/pyproject.toml" 2>/dev/null)"
  min="${min:-3.11}"
  # A copied venv embeds the build host's interpreter path/arch; verify it
  # actually runs here (>= floor) and has the deps before trusting it.
  venv_ok=1
  "$remote_dir/.venv/bin/python" - "$min" <<'PY' 2>/dev/null || venv_ok=0
import sys
maj, mn = (int(x) for x in sys.argv[1].split("."))
raise SystemExit(0 if sys.version_info[:2] >= (maj, mn) else 1)
PY
  # 설정 편집기가 tomlkit / tree-sitter 를 쓴다. 빠지면 EPR 적용이 런타임에 죽는다.
  "$remote_dir/.venv/bin/python" -c \
    'import paho.mqtt.client; import hcl2; from hcl2.utils import SerializationOptions; import tomlkit; import tree_sitter; import tree_sitter_hcl' \
    2>/dev/null || venv_ok=0
  if [[ "$venv_ok" == "1" ]]; then
    have="$("$remote_dir/.venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    echo "[remote] copied .venv OK (Python $have, paho+hcl2+tomlkit+tree-sitter present); retiring legacy venvJIBOT"
    rm -rf "$remote_dir/venvJIBOT"
  else
    echo "[remote] ERROR: the copied .venv does not run on this host" >&2
    echo "[remote]   (needs Python >= $min + paho.mqtt + hcl2 + tomlkit + tree_sitter + tree_sitter_hcl)." >&2
    echo "[remote]   A venv embeds the build machine's interpreter path/arch. Rebuild the local" >&2
    echo "[remote]   .venv with the robot's system Python (e.g. /usr/bin/python$min) and matching arch," >&2
    echo "[remote]   or run scripts/setup-adaptor-service.sh on the robot to rebuild it offline." >&2
    exit 1
  fi
fi

if [[ "$INSTALL_PY_DEPS" == "1" ]]; then
  echo "[remote] installing Python dependencies from offline_packages"
  cd "$remote_dir"
  if [[ ! -f requirements.txt ]]; then
    echo 'Missing requirements.txt on remote adapter directory' >&2
    exit 1
  fi
  if [[ ! -d offline_packages ]]; then
    echo 'Missing offline_packages on remote adapter directory' >&2
    exit 1
  fi
  python_bin=python3
  pip_user_arg=--user
  if [[ -x .venv/bin/python ]]; then
    python_bin=.venv/bin/python
    pip_user_arg=
  elif [[ -x venvJIBOT/bin/python ]]; then
    python_bin=venvJIBOT/bin/python
    pip_user_arg=
  elif [[ -x venvJIBOT/bin/python3 ]]; then
    python_bin=venvJIBOT/bin/python3
    pip_user_arg=
  fi
  if ! "$python_bin" -m pip --version >/dev/null 2>&1; then
    "$python_bin" -m ensurepip --upgrade || "$python_bin" - offline_packages <<'PY'
import pathlib
import sys
import sysconfig
import zipfile

wheel_dir = pathlib.Path(sys.argv[1])
target = pathlib.Path(sysconfig.get_paths()["purelib"])
target.mkdir(parents=True, exist_ok=True)
wheels = sorted(wheel_dir.glob("*-none-any.whl"))
if not wheels:
    raise SystemExit(f"no pure Python wheels found in {wheel_dir}")
for wheel in wheels:
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
    print(f"[remote] extracted {wheel.name} -> {target}")
PY
  fi
  if "$python_bin" -m pip --version >/dev/null 2>&1; then
    "$python_bin" -m pip install $pip_user_arg --no-index --find-links=offline_packages -r requirements.txt
  fi
  # 설정 읽기(hcl2)와 편집(tomlkit / tree-sitter) 양쪽을 다 확인한다.
  # 하나라도 빠지면 EPR 적용이 런타임에 죽고, 그때는 로봇에 인터넷이 없어 복구가 어렵다.
  for mod in \
    'import hcl2; from hcl2.utils import SerializationOptions' \
    'import tomlkit' \
    'import tree_sitter' \
    'import tree_sitter_hcl'
  do
    if ! "$python_bin" -c "$mod" >/dev/null 2>&1; then
      echo "[remote] ERROR: offline install did not produce an importable module: $mod" >&2
      echo "[remote] Python: $python_bin ($("$python_bin" -c 'import sys,platform; print("%d.%d %s" % (sys.version_info[0], sys.version_info[1], platform.machine()))' 2>/dev/null))" >&2
      echo "[remote] offline_packages 에 이 Python/CPU 에 맞는 wheel 이 있어야 합니다." >&2
      echo "[remote] 빌드 호스트에서 scripts/fetch-offline-wheels.sh 를 실행해 채운 뒤 다시 배포하세요." >&2
      exit 1
    fi
  done
  echo "[remote] verified offline dependencies: hcl2, tomlkit, tree_sitter, tree_sitter_hcl"
fi

echo "[remote] upload done."
REMOTE_SCRIPT

remote_command="$(printf \
  'CONFIG_TOML_MODE=%q ROBOTS_HCL_MODE=%q EXTENSIONS_HCL_MODE=%q RECIPES_HCL_MODE=%q CLEAN_REMOTE=%q INSTALL_PY_DEPS=%q COPY_VENV=%q REMOTE_ADAPTER_DIR_ARG=%q bash -c %q' \
  "$config_toml_action" \
  "$robots_hcl_action" \
  "$extensions_hcl_action" \
  "$recipes_hcl_action" \
  "$CLEAN_REMOTE" \
  "$INSTALL_PY_DEPS" \
  "$COPY_VENV" \
  "$REMOTE_ADAPTER_DIR" \
  "$remote_upload_script")"

read -r -d '' remote_service_script <<'REMOTE_SERVICE_SCRIPT' || true
set -euo pipefail

# This script runs under "ssh -tt", and with connection sharing (ControlMaster)
# the mux client hands the remote pty the *raw* termios the local ssh client had
# already switched to: -onlcr makes every line stair-step across the screen and
# -icanon/-echo break the sudo password prompt below. Put the pty back to sane
# modes before printing anything.
if [ -t 0 ]; then
  stty sane 2>/dev/null || true
fi

action="${SERVICE_ACTION:?SERVICE_ACTION is required}"
if [[ "$action" != "stop" && "$action" != "start" ]]; then
  echo "[remote] ERROR: invalid service action: $action" >&2
  exit 2
fi

if [[ "$action" == "start" && -n "${RESTART_CMD:-}" ]]; then
  echo "[remote] running custom start command"
  exec bash -lc "$RESTART_CMD"
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "[remote] WARNING: systemctl not found; manage the adapter manually." >&2
  exit 0
fi

svc_default="${ADAPTER_SERVICE:-amr-adaptor.service}"
unit_patterns=(
  'amr-adaptor*.service'
  'jibot-adapter*.service'
  'amr-adapter*.service'
)
units="$(
  {
    for pattern in "${unit_patterns[@]}"; do
      systemctl list-units --no-legend --plain --state=active "$pattern" 2>/dev/null || true
      systemctl list-units --no-legend --plain --all "$pattern" 2>/dev/null || true
      systemctl list-unit-files --no-legend "$pattern" 2>/dev/null || true
    done
    systemctl list-units --no-legend --plain --all "$svc_default" 2>/dev/null || true
    systemctl list-unit-files --no-legend "$svc_default" 2>/dev/null || true
  } | awk '{print $1}' \
    | grep -E '^(amr-adaptor|jibot-adapter|amr-adapter)(@[^.]+)?\.service$' \
    | grep -v '@\.service$' \
    | sort -u
)"

if [[ -z "$units" ]]; then
  echo "[remote] ERROR: no installed adapter systemd unit found." >&2
  echo "[remote]   Checked: ${unit_patterns[*]} and $svc_default" >&2
  echo "[remote]   Install services first: cd '${REMOTE_ADAPTER_DIR_ARG:-$HOME/adaptor}' && scripts/setup-adaptor-service.sh --jibot" >&2
  echo "[remote]   Or pass the exact unit: ADAPTER_SERVICE=name.service scripts/update-jibot-adapter-over-ssh.sh --restart ..." >&2
  exit 1
fi

webui_unit="$(
  {
    systemctl list-units --no-legend --plain --all amr-webui.service 2>/dev/null || true
    systemctl list-unit-files --no-legend amr-webui.service 2>/dev/null || true
  } | awk '{print $1}' | grep -x 'amr-webui.service' | head -n 1 || true
)"
if [[ -n "$webui_unit" ]]; then
  units="$(printf '%s\n%s\n' "$units" "$webui_unit" | sort -u)"
fi

# The joystick bridge runs scripts/amr-xboxdrv-run.sh straight out of the
# deployed tree and the upload rewrites that file in place. /bin/sh reads a
# script by offset as it runs, so a live supervisor must be stopped across the
# upload rather than left to misparse its own replacement.
xboxdrv_unit="$(
  {
    systemctl list-units --no-legend --plain --all amr-xboxdrv.service 2>/dev/null || true
    systemctl list-unit-files --no-legend amr-xboxdrv.service 2>/dev/null || true
  } | awk '{print $1}' | grep -x 'amr-xboxdrv.service' | head -n 1 || true
)"
if [[ -n "$xboxdrv_unit" ]]; then
  units="$(printf '%s\n%s\n' "$units" "$xboxdrv_unit" | sort -u)"
fi

rc=0
for u in $units; do
  if [[ "$action" == "stop" ]]; then
    echo "[remote] stopping $u"
  else
    echo "[remote] starting $u"
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl "$action" "$u" || rc=1
  elif sudo -n systemctl "$action" "$u" 2>/dev/null; then
    :
  else
    # /etc/sudoers.d/adaptor-tui only lists the units that existed when setup
    # last ran, so a unit added later (amr-xboxdrv.service) still asks for a
    # password here. Say why, otherwise the bare sudo prompt looks like a hang.
    echo "[remote] $u is not covered by the passwordless sudo rule; password required"
    echo "[remote]   (to stop being asked: re-run scripts/setup-adaptor-service.sh on this robot)"
    if sudo systemctl "$action" "$u"; then
      :
    elif systemctl "$action" "$u" 2>/dev/null; then
      :
    else
      echo "[remote] ERROR: failed to $action $u." >&2
      echo "[remote]   run it manually: sudo systemctl $action $u" >&2
      rc=1
    fi
  fi
done
exit "$rc"
REMOTE_SERVICE_SCRIPT

service_command="$(printf \
  'SERVICE_ACTION=%q ADAPTER_SERVICE=%q RESTART_CMD=%q REMOTE_ADAPTER_DIR_ARG=%q bash -c %q' \
  '__SERVICE_ACTION__' \
  "$ADAPTER_SERVICE" \
  "${RESTART_CMD:-}" \
  "$REMOTE_ADAPTER_DIR" \
  "$remote_service_script")"
stop_command="${service_command/__SERVICE_ACTION__/stop}"
start_command="${service_command/__SERVICE_ACTION__/start}"

upload_items=(payload default_config)
[[ "$COPY_VENV" == "1" ]] && upload_items+=(venv_payload)

# --mtime pins the archive timestamps but is GNU tar only; the macOS system tar
# (bsdtar) rejects it outright and dies before the upload starts. Detect the
# flavour from --version rather than trial-running the flag: an empty-archive
# probe fails on GNU tar too, which would silently drop the flag everywhere.
tar_mtime_opt=()
tar_version="$(tar --version 2>/dev/null || true)"
case "$tar_version" in
  *"GNU tar"*) tar_mtime_opt=( --mtime='2000-01-01' ) ;;
esac

# Runs an interactive remote command (the remote sudo may prompt) on a tty.
# ssh has to INHERIT this shell's stdin: with connection sharing, handing it a
# separately opened /dev/tty ("< /dev/tty") makes the mux master copy raw termios
# onto the remote pty *and* swallow every keystroke, so the password prompt can
# never be answered. The redirect is only a fallback for a non-tty stdin.
run_remote_tty() {
  local host="$1" command="$2"
  if [[ -t 0 ]]; then
    "${ssh_base[@]}" -tt "$host" "$command"
  elif [[ -r /dev/tty ]]; then
    "${ssh_base[@]}" -tt "$host" "$command" < /dev/tty
  else
    "${ssh_base[@]}" "$host" "$command"
  fi
}

failed_hosts=()
for host in "${REMOTE_HOSTS[@]}"; do
  services_stopped=0
  if [[ -n "${RESTART_CMD:-}" || "$RESTART_ADAPTER" == "1" ]]; then
    echo "==> Stopping adapter services on $host before install"
    if ! run_remote_tty "$host" "$stop_command"; then
      echo "==> $host: FAILED while stopping services; install skipped" >&2
      failed_hosts+=("$host")
      continue
    fi
    services_stopped=1
  fi

  echo "==> Uploading bundle to $host:$remote_dir_display"
  if tar -C "$STAGING_DIR" ${tar_mtime_opt[@]+"${tar_mtime_opt[@]}"} -cf - "${upload_items[@]}" | "${ssh_base[@]}" "$host" "$remote_command"; then
    if [[ "$services_stopped" == "1" ]]; then
      echo "==> Starting adapter services on $host after install"
      if run_remote_tty "$host" "$start_command"; then
        echo "==> $host: done"
      else
        echo "==> $host: FAILED while starting services" >&2
        failed_hosts+=("$host")
      fi
    else
      echo "==> $host: done. Restart the adapter process to apply changes."
    fi
  else
    echo "==> $host: FAILED during install" >&2
    if [[ "$services_stopped" == "1" ]]; then
      echo "==> Attempting to start adapter services on $host after failed install" >&2
      run_remote_tty "$host" "$start_command" || true
    fi
    failed_hosts+=("$host")
  fi
done

if [[ ${#failed_hosts[@]} -gt 0 ]]; then
  echo "Failed hosts (${#failed_hosts[@]}/${#REMOTE_HOSTS[@]}): ${failed_hosts[*]}" >&2
  exit 1
fi

echo "All ${#REMOTE_HOSTS[@]} host(s) updated successfully."
