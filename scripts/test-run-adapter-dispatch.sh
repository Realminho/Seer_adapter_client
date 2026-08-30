#!/usr/bin/env bash
# Smoke test for run-adapter.sh dispatch (print mode, no real exec).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/adaptor"

instance="__dispatch-smoke-jibot__"
out="$(AMR_DISPATCH_PRINT=1 ./run-adapter.sh --instance "$instance" --simulator)"
echo "instance -> $out"
if [[ "$out" == *run-main.sh* && "$out" == *" --robot $instance"* && "$out" == *" --simulator"* ]]; then
  echo "PASS: explicit JIBOT instance dispatches to run-main.sh"
else
  echo "FAIL: unexpected dispatch: $out" >&2
  exit 1
fi

simulator_robots="$(mktemp --suffix=-simulator-robots.hcl)"
trap 'rm -f "$simulator_robots"' EXIT
printf 'robot "SIM-001" {}\nrobot "SIM-002" {}\n' >"$simulator_robots"
out="$(AMR_DISPATCH_PRINT=1 ./run-adapter.sh --simulator --simulator-robots "$simulator_robots")"
echo "simulator fleet -> $out"
if [[ "$out" == *run-multi.sh* &&
      "$out" == *"--robots $simulator_robots"* &&
      "$out" == *"--simulator"* ]]; then
  echo "PASS: simulator fleet file controls dispatch and launcher arguments"
else
  echo "FAIL: simulator fleet was not used: $out" >&2
  exit 1
fi

if AMR_DISPATCH_PRINT=1 ./run-adapter.sh --simulator-robots "$simulator_robots" >/dev/null 2>&1; then
  echo "FAIL: --simulator-robots without --simulator should fail" >&2
  exit 1
fi
