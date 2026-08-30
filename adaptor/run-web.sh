#!/usr/bin/env bash
# Runs the AMR WebUi (python -m web) from the adapter directory.
# Prefers .venv (uv), then legacy venvJIBOT, then system python3.
# If the configured web_ui port is already in use, asks whether to free it
# (kill the listener) before starting. Non-interactive stdin (e.g. systemd,
# or no terminal) reads EOF -> treated as "no", so nothing is killed
# automatically and the script aborts instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venvJIBOT/bin/python" ]]; then
  PYTHON_BIN="venvJIBOT/bin/python"
fi

# web_ui port from config (fallback 9000).
PORT="$("$PYTHON_BIN" -c "try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
print(tomllib.load(open('config/config.toml','rb')).get('web_ui',{}).get('port',9000))" 2>/dev/null || echo 9000)"

# Pids LISTENing on $PORT (may be empty).
listener_pids() { ss -tlnpH "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u; }

pids="$(listener_pids || true)"
if [[ -n "$pids" ]]; then
  echo "Port $PORT is already in use:" >&2
  for p in $pids; do
    echo "  pid $p: $(ps -o cmd= -p "$p" 2>/dev/null || echo '?')" >&2
  done
  read -r -p "Free the port and continue? [y/N] " ans || ans=""
  case "${ans,,}" in
    y|yes)
      for p in $pids; do kill "$p" 2>/dev/null || true; done
      for _ in $(seq 1 10); do
        [[ -z "$(listener_pids || true)" ]] && break
        sleep 0.3
      done
      remaining="$(listener_pids || true)"
      if [[ -n "$remaining" ]]; then
        echo "Port $PORT still busy; sending SIGKILL." >&2
        for p in $remaining; do kill -9 "$p" 2>/dev/null || true; done
        sleep 0.5
      fi
      ;;
    *)
      echo "Aborted; port $PORT left as-is. (web UI not started)" >&2
      exit 1
      ;;
  esac
fi

exec "$PYTHON_BIN" -m web "$@"
