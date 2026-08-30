#!/usr/bin/env bash
# Dispatcher for amr-adaptor.service / amr-adaptor@<instance>.service.
# The default service reads config/robots.hcl and chooses a single JIBOT
# process or run_multi.py. Named instances remain for explicit adapter.instances
# and manual compatibility paths.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# OS sound playback (SoundPlayer -> mplayer/pactl) talks to the user's
# PulseAudio socket under $XDG_RUNTIME_DIR/pulse. A boot-time systemd *system*
# service inherits no login-session env, so XDG_RUNTIME_DIR is unset and pulse
# is unreachable -> silent audio AND no-op volume, with no error (mplayer stderr
# is discarded). Default it to this user's runtime dir so audio works under
# systemd too. /run/user/<uid> must exist at boot: `loginctl enable-linger <user>`
# (setup-adaptor-service.sh does this).
if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

INSTANCE=""
if [[ "${1:-}" == "--instance" ]]; then
  INSTANCE="${2:-}"
  shift 2
fi

# A simulator fleet can live separately from the production inventory. Consume
# this dispatcher-only option here and pass the selected file to both dispatch
# and the eventual JIBOT launcher as the ordinary --robots option.
SIMULATOR_ROBOTS=""
SIMULATOR_MODE=0
FORWARD_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --simulator)
      SIMULATOR_MODE=1
      FORWARD_ARGS+=("$1")
      shift
      ;;
    --simulator-robots)
      [[ $# -ge 2 ]] || {
        echo "[run-adapter] FATAL: --simulator-robots requires a file path." >&2
        exit 2
      }
      SIMULATOR_ROBOTS="$2"
      shift 2
      ;;
    --simulator-robots=*)
      SIMULATOR_ROBOTS="${1#*=}"
      shift
      ;;
    *)
      FORWARD_ARGS+=("$1")
      shift
      ;;
  esac
done
if [[ -n "$SIMULATOR_ROBOTS" && "$SIMULATOR_MODE" != "1" ]]; then
  echo "[run-adapter] FATAL: --simulator-robots requires --simulator." >&2
  exit 2
fi

PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venvJIBOT/bin/python" ]]; then
  PYTHON_BIN="venvJIBOT/bin/python"
fi

# The dispatch below is the first Python the service ever runs, so a broken
# interpreter surfaces here — as a raw traceback from adapter_dispatch, not as
# anything an operator can act on. run-main.sh has the same guard, but it never
# gets the chance: the process dies one layer earlier. Two ways this bites on a
# robot with no usable venv, where PYTHON_BIN falls back to system python3:
#   - Python < 3.11: runtime PEP 604 'X | None' unions raise "unsupported
#     operand type(s) for |: 'type' and 'NoneType'" mid-startup.
#   - deps missing: config.fleet -> config.hcl -> "No module named 'hcl2'".
MIN_PY="$(sed -nE 's/^[[:space:]]*requires-python[[:space:]]*=.*>=[[:space:]]*"?([0-9]+\.[0-9]+).*/\1/p' pyproject.toml 2>/dev/null)"
MIN_PY="${MIN_PY:-3.11}"
if ! "$PYTHON_BIN" -c "import sys; mj, mn = (int(x) for x in '${MIN_PY}'.split('.')); raise SystemExit(0 if sys.version_info[:2] >= (mj, mn) else 1)"; then
  cur="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
  echo "[run-adapter] FATAL: $PYTHON_BIN is Python $cur but the adapter needs >= $MIN_PY." >&2
  [[ "$PYTHON_BIN" == "python3" ]] && \
    echo "[run-adapter] No .venv or venvJIBOT here, so the system python3 was used." >&2
  echo "[run-adapter] Build the venv:  scripts/setup-adaptor-service.sh" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import paho.mqtt.client, hcl2; from hcl2.utils import SerializationOptions' 2>/dev/null; then
  echo "[run-adapter] FATAL: $PYTHON_BIN is missing adapter dependencies (paho-mqtt, python-hcl2)." >&2
  echo "[run-adapter] Install them offline:  scripts/install-offline-python-deps.sh" >&2
  echo "[run-adapter]   (or re-run scripts/setup-adaptor-service.sh to rebuild the venv)" >&2
  exit 1
fi

# First line = vendor, remaining lines = extra launcher args. Use command
# substitution so a bad/missing robots.hcl fails the service under `set -e`.
DISPATCH_OUTPUT="$("$PYTHON_BIN" -m config.adapter_dispatch "$INSTANCE" "$SIMULATOR_ROBOTS")"
mapfile -t DISPATCH <<<"$DISPATCH_OUTPUT"
VENDOR="${DISPATCH[0]:-jibot}"
EXTRA_ARGS=()
[[ ${#DISPATCH[@]} -gt 1 ]] && EXTRA_ARGS=("${DISPATCH[@]:1}")

case "$VENDOR" in
  jibot-multi) LAUNCHER="./run-multi.sh" ;;
  hexplorer) LAUNCHER="./run-hexplorer.sh" ;;
  *)         LAUNCHER="./run-main.sh" ;;
esac

# bash 4.3 (onboard): guard empty-array expansion under `set -u`.
ARGS=()
[[ ${#EXTRA_ARGS[@]} -gt 0 ]] && ARGS+=("${EXTRA_ARGS[@]}")
[[ ${#FORWARD_ARGS[@]} -gt 0 ]] && ARGS+=("${FORWARD_ARGS[@]}")

if [[ "${AMR_DISPATCH_PRINT:-0}" == "1" ]]; then
  echo "$LAUNCHER ${ARGS[*]:-}"
  exit 0
fi

exec "$LAUNCHER" ${ARGS[@]+"${ARGS[@]}"}
