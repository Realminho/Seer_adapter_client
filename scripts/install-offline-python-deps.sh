#!/usr/bin/env bash
# Install adapter Python dependencies from offline_packages.
# Works even when venvJIBOT has no pip/ensurepip by extracting pure Python wheels.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER_DIR="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"

if [[ ! -d "$ADAPTER_DIR" ]]; then
  echo "Adapter directory not found: $ADAPTER_DIR" >&2
  exit 1
fi

if [[ ! -f "$ADAPTER_DIR/requirements.txt" ]]; then
  echo "Missing requirements.txt: $ADAPTER_DIR/requirements.txt" >&2
  exit 1
fi

if [[ ! -d "$ADAPTER_DIR/offline_packages" ]]; then
  echo "Missing offline_packages: $ADAPTER_DIR/offline_packages" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
PIP_USER_ARG=()

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ADAPTER_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ADAPTER_DIR/.venv/bin/python"
  elif [[ -x "$ADAPTER_DIR/venvJIBOT/bin/python" ]]; then
    PYTHON_BIN="$ADAPTER_DIR/venvJIBOT/bin/python"
  elif [[ -x "$ADAPTER_DIR/venvJIBOT/bin/python3" ]]; then
    PYTHON_BIN="$ADAPTER_DIR/venvJIBOT/bin/python3"
  else
    PYTHON_BIN="python3"
    PIP_USER_ARG=(--user)
  fi
fi

echo "Adapter dir: $ADAPTER_DIR"
echo "Python: $PYTHON_BIN"

if "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install "${PIP_USER_ARG[@]}" --no-index \
    --find-links "$ADAPTER_DIR/offline_packages" \
    -r "$ADAPTER_DIR/requirements.txt"
elif "$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install "${PIP_USER_ARG[@]}" --no-index \
    --find-links "$ADAPTER_DIR/offline_packages" \
    -r "$ADAPTER_DIR/requirements.txt"
else
  echo "pip/ensurepip unavailable; extracting pure Python wheels directly."
  "$PYTHON_BIN" - "$ADAPTER_DIR/offline_packages" <<'PY'
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
    print(f"extracted {wheel.name} -> {target}")
PY
fi

"$PYTHON_BIN" -c \
  "import paho.mqtt.client; import tomli; import hcl2; from hcl2.utils import SerializationOptions; print('offline deps ok (paho, tomli, hcl2)')"
