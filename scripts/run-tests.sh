#!/usr/bin/env bash
# adaptor 파이썬 테스트 고정 진입점.
#
# 왜 필요한가:
#   ROS(/opt/ros/*)가 launch_testing / launch_testing_ros 를 pytest 플러그인
#   entrypoint 로 등록해 둔다. 이 플러그인들의 hook 시그니처가 현재 pytest 와
#   맞지 않아, 그냥 `pytest` 를 부르면 테스트 수집 전에 PluginValidationError
#   로 죽는다. `-p no:<name>` 으로 하나씩 끄는 방식은 entrypoint 가 늘어날 때마다
#   다시 깨지므로 자동 로드 자체를 끈다.
#
# 사용:
#   scripts/run-tests.sh                          # 전체
#   scripts/run-tests.sh tests/test_web_server.py # 일부
#   scripts/run-tests.sh --python /path/to/venv/bin/python tests/test_configio.py
set -euo pipefail

PYTHON_BIN=""
if [[ "${1:-}" == "--python" ]]; then
    PYTHON_BIN="$2"
    shift 2
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "$PYTHON_BIN" ]]; then
    # uv 는 pyproject.toml 옆(= adaptor/)에 .venv 를 만든다. 루트만 보면 못 찾고
    # 시스템 python3 로 조용히 폴백해, 테스트가 hcl2/tree_sitter 부재로 수집 단계에서
    # 죽는다. 실패가 "환경 문제"가 아니라 "코드 문제"처럼 보이므로 둘 다 본다.
    for candidate in "$REPO_DIR/adaptor/.venv/bin/python" "$REPO_DIR/.venv/bin/python"; do
        if [[ -x "$candidate" ]]; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
    PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
cd "$REPO_DIR/adaptor"
exec "$PYTHON_BIN" -m pytest "$@"
