#!/usr/bin/env bash
# Launches multiple JIBOT adapters from config/robots.hcl via run_multi.py.
# 여러 대의 JIBOT adapter를 한 번에 띄운다. config/robots.hcl을 읽어 로봇마다
# main.py 프로세스를 하나씩 올린다. venvJIBOT이 있으면 활성화 후 실행한다.
# run-main.sh와 같은 방식이라 systemd에서도 동일하게 다룰 수 있다.
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

exec "$PYTHON_BIN" run_multi.py "$@"
