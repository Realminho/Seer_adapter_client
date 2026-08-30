#!/usr/bin/env bash
# Runs the Dobot Hexplorer VDA5050 adapter main_hexplorer.py from its adapter
# directory. If venvJIBOT exists, it is activated before starting the adapter.
# Mirrors run-main.sh so both adapters launch the same way under systemd.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

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

exec "$PYTHON_BIN" main_hexplorer.py "$@"
