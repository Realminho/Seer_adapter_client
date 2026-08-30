#!/usr/bin/env bash
# adaptor/offline_packages 를 requirements.txt 로부터 다시 채운다.
#
# 로봇은 인터넷이 없다. 배포 스크립트가 `pip install --no-index
# --find-links=offline_packages` 로 설치하므로, 이 디렉터리에 **로봇의 Python
# 버전·CPU 아키텍처에 맞는 wheel** 이 미리 들어 있어야 한다.
#
# C 확장 패키지(regex, tree-sitter …)는 조합마다 wheel 이 다르므로 행렬 전체를 받는다.
# 순수 Python wheel(`-none-any`)은 한 번만 받으면 모든 조합에서 쓰인다.
#
# 지금까지 이 디렉터리는 손으로 채워져 있었다. 의존성을 하나 추가할 때 6개 wheel 을
# 기억해서 받아야 했고, 하나라도 빠지면 **로봇에서 설치가 조용히 실패**한다.
#
# 사용:
#   scripts/fetch-offline-wheels.sh                 # 기본 행렬로 갱신
#   scripts/fetch-offline-wheels.sh --check         # 빠진 wheel 만 보고(다운로드 안 함)
#   scripts/fetch-offline-wheels.sh --python 3.11 --python 3.12
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ADAPTER_DIR="$REPO_DIR/adaptor"
OUT_DIR="$ADAPTER_DIR/offline_packages"
REQUIREMENTS="$ADAPTER_DIR/requirements.txt"

# 로봇에서 볼 수 있는 Python. setup-adaptor-service.sh 의 하한이 3.11 이다.
PY_VERSIONS=()
PLATFORMS=(
  "manylinux2014_x86_64"
  "manylinux2014_aarch64"
)
CHECK_ONLY=0
PYTHON_BIN="${PYTHON:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift ;;
    --python) PY_VERSIONS+=("$2"); shift 2 ;;
    --python=*) PY_VERSIONS+=("${1#*=}"); shift ;;
    --pip-python) PYTHON_BIN="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ ${#PY_VERSIONS[@]} -eq 0 ]] && PY_VERSIONS=(3.11 3.12 3.13)

[[ -f "$REQUIREMENTS" ]] || { echo "Missing $REQUIREMENTS" >&2; exit 1; }
"$PYTHON_BIN" -m pip --version >/dev/null 2>&1 || {
  echo "ERROR: '$PYTHON_BIN -m pip' 를 쓸 수 없음. --pip-python 으로 지정하세요." >&2
  exit 1
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

echo "requirements : $REQUIREMENTS"
echo "행렬         : Python ${PY_VERSIONS[*]}  ×  ${PLATFORMS[*]}"
echo

failed=0
for py in "${PY_VERSIONS[@]}"; do
  for plat in "${PLATFORMS[@]}"; do
    py_tag="${py//./}"
    echo "  받는 중  Python $py / $plat"
    if ! "$PYTHON_BIN" -m pip download \
        --quiet \
        --requirement "$REQUIREMENTS" \
        --dest "$tmp_dir" \
        --only-binary=:all: \
        --python-version "$py_tag" \
        --platform "$plat" \
        2> "$tmp_dir/err-$py_tag-$plat.log"; then
      echo "    실패 — $(tail -1 "$tmp_dir/err-$py_tag-$plat.log")" >&2
      failed=1
    fi
  done
done

if [[ "$failed" == "1" ]]; then
  echo >&2
  echo "ERROR: 일부 조합의 wheel 을 받지 못했습니다. 위 메시지를 확인하세요." >&2
  echo "       (해당 Python/아키텍처용 wheel 이 없는 패키지가 있으면 그 로봇에는 설치할 수 없습니다.)" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
new=0
for wheel in "$tmp_dir"/*.whl; do
  [[ -e "$wheel" ]] || continue
  name="$(basename "$wheel")"
  if [[ -f "$OUT_DIR/$name" ]]; then
    continue
  fi
  new=$((new + 1))
  if [[ "$CHECK_ONLY" == "1" ]]; then
    echo "  빠짐  $name"
  else
    cp "$wheel" "$OUT_DIR/$name"
    echo "  추가  $name"
  fi
done

echo
if [[ "$new" -eq 0 ]]; then
  echo "offline_packages 가 최신입니다 ($(ls "$OUT_DIR"/*.whl 2>/dev/null | wc -l)개 wheel)."
elif [[ "$CHECK_ONLY" == "1" ]]; then
  echo "빠진 wheel $new 개. --check 없이 다시 실행하면 받습니다."
  exit 1
else
  echo "wheel $new 개 추가. 총 $(ls "$OUT_DIR"/*.whl | wc -l)개."
  echo "이 디렉터리는 저장소에 커밋해야 로봇 배포에 실립니다."
fi
