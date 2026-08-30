#!/usr/bin/env bash
# Runs the Jibot VDA5050 adapter main.py from its adapter directory.
# If venvJIBOT exists, it is activated before starting the adapter.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Make the user's PulseAudio reachable when launched as a boot systemd service
# (no login-session env -> XDG_RUNTIME_DIR unset -> mplayer/pactl can't reach
# pulse -> silent audio + no-op volume). See run-adapter.sh for the full note.
if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

PYTHON_BIN="python3"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venvJIBOT/bin/python" ]]; then
  PYTHON_BIN="venvJIBOT/bin/python"
elif [[ -x "venvJIBOT/bin/python3" ]]; then
  PYTHON_BIN="venvJIBOT/bin/python3"
fi

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
elif [[ -f "venvJIBOT/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "venvJIBOT/bin/activate"
fi

# Refuse to start on an interpreter older than the adapter's floor. The code
# uses runtime PEP 604 'X | None' unions, which need >= 3.11; an older venv dies
# mid-startup with the cryptic "unsupported operand type(s) for |: 'type' and
# 'NoneType'". Compare up front and fail with an actionable message instead.
MIN_PY="$(sed -nE 's/^[[:space:]]*requires-python[[:space:]]*=.*>=[[:space:]]*"?([0-9]+\.[0-9]+).*/\1/p' pyproject.toml 2>/dev/null)"
MIN_PY="${MIN_PY:-3.11}"
if ! "$PYTHON_BIN" -c "import sys; mj, mn = (int(x) for x in '${MIN_PY}'.split('.')); raise SystemExit(0 if sys.version_info[:2] >= (mj, mn) else 1)"; then
  cur="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
  echo "[run-main] FATAL: $PYTHON_BIN is Python $cur but the adapter needs >= $MIN_PY." >&2
  echo "[run-main] Rebuild the venv:  uv venv --python $MIN_PY && uv pip install -r requirements.txt" >&2
  echo "[run-main]   (or re-run scripts/setup-adaptor-service.sh)" >&2
  exit 1
fi

exec "$PYTHON_BIN" main.py "$@"
