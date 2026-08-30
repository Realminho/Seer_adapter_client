#!/usr/bin/env bash
# scripts/offline-debs/xboxdrv/ 를 Ubuntu 아카이브에서 다시 채운다.
#
# 로봇은 인터넷이 없다. setup-adaptor-service.sh 가 이 디렉터리의 .deb 를
# `dpkg -i` 로 설치하고 apt 로 폴백하지 않으므로, **로봇의 배포판·아키텍처에
# 맞는 .deb** 가 미리 들어 있어야 한다.
#
# 의존성 폐포를 재귀로 받지 않는다. libc6 같은 핵심 패키지까지 끌어오면 저장소가
# 수십 MB 커지고, 오프라인 로봇에서 dpkg 가 코어 패키지를 다운그레이드하는 사고가
# 난다. 아래 PACKAGES 에는 "베이스 이미지에 없을 만한 것"만 명시한다. dpkg 가
# 의존성 오류를 내면 그 패키지 이름을 한 줄 추가하고 다시 받는다.
#
# 사용:
#   scripts/fetch-offline-debs.sh            # 빠진 deb 를 받는다
#   scripts/fetch-offline-debs.sh --check    # 빠진 것만 보고(다운로드 안 함)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_DIR/scripts/offline-debs/xboxdrv"

# JIBOT 온보드 PC = arm64 / Ubuntu 20.04 focal.
SUITE="${SUITE:-focal}"
ARCH="${ARCH:-arm64}"
# arm64 는 ports.ubuntu.com 이, amd64 는 archive.ubuntu.com 이 호스팅한다.
MIRROR="${MIRROR:-http://ports.ubuntu.com/ubuntu-ports}"
COMPONENTS=(main universe)

# xboxdrv 의 Depends 중 최소 이미지에 없을 수 있는 것만 받는다.
# libc6 libdbus-1-3 libgcc-s1 libglib2.0-0 libstdc++6 libudev1 libx11-6 은
# 어떤 Ubuntu 베이스에도 있으므로 받지 않는다.
#
# 주석에 괄호를 쓰지 말 것. tests/test_offline_deb_fetch.py 가 아래 배열 선언
# 줄부터 줄머리가 닫는 괄호인 줄까지를 잘라 목록을 검증한다.
PACKAGES=(
  xboxdrv           # bridge 바이너리 본체. universe 컴포넌트
  libdbus-glib-1-2  # 레거시 dbus 바인딩. 최소 이미지에 없다
  libusb-1.0-0      # 최소 이미지에 없다
)

CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

for tool in curl xzcat sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "ERROR: '$tool' 이 필요합니다." >&2
    exit 1
  }
done

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

echo "미러   : $MIRROR"
echo "대상   : $SUITE / $ARCH"
echo "저장소 : $OUT_DIR"
echo

# 인덱스는 한 번만 받아 합쳐 둔다. focal universe 인덱스는 수십 MB 라
# 패키지마다 다시 받으면 안 된다.
index="$tmp_dir/Packages"
: > "$index"
for comp in "${COMPONENTS[@]}"; do
  url="$MIRROR/dists/$SUITE/$comp/binary-$ARCH/Packages.xz"
  echo "  인덱스 받는 중  $comp"
  if ! curl -fsSL --max-time 900 -o "$tmp_dir/$comp.xz" "$url"; then
    echo "ERROR: 인덱스를 받지 못했습니다: $url" >&2
    exit 1
  fi
  xzcat "$tmp_dir/$comp.xz" >> "$index"
  # 컴포넌트 경계에서 stanza 가 붙지 않도록 빈 줄을 넣는다.
  echo >> "$index"
done
echo

mkdir -p "$OUT_DIR"
missing=0
added=0
for pkg in "${PACKAGES[@]}"; do
  stanza="$(awk -v want="Package: $pkg" '
    $0 == want { found = 1 }
    found && /^$/ { exit }
    found { print }
  ' "$index")"
  if [[ -z "$stanza" ]]; then
    echo "ERROR: $SUITE/$ARCH 인덱스에서 '$pkg' 를 찾지 못했습니다." >&2
    echo "       패키지 이름과 SUITE 를 확인하세요." >&2
    exit 1
  fi
  filename="$(awk '/^Filename: /{print $2; exit}' <<<"$stanza")"
  sha256="$(awk '/^SHA256: /{print $2; exit}' <<<"$stanza")"
  version="$(awk '/^Version: /{print $2; exit}' <<<"$stanza")"
  base="$(basename "$filename")"

  if [[ -f "$OUT_DIR/$base" ]]; then
    echo "  있음  $base"
    continue
  fi

  missing=$((missing + 1))
  if [[ $CHECK_ONLY -eq 1 ]]; then
    echo "  빠짐  $base  ($pkg $version)"
    continue
  fi

  echo "  받는 중  $base  ($pkg $version)"
  if ! curl -fsSL --max-time 900 -o "$tmp_dir/$base" "$MIRROR/$filename"; then
    echo "ERROR: 다운로드 실패: $MIRROR/$filename" >&2
    exit 1
  fi
  actual="$(sha256sum "$tmp_dir/$base" | awk '{print $1}')"
  if [[ "$actual" != "$sha256" ]]; then
    echo "ERROR: SHA256 불일치 ($base)" >&2
    echo "       기대: $sha256" >&2
    echo "       실제: $actual" >&2
    exit 1
  fi
  # 같은 패키지의 이전 버전 deb 를 남기면 안 된다. setup-adaptor-service.sh 는
  # 디렉터리의 *.deb 를 한 번의 `dpkg -i` 에 통째로 넘기는데, 그 목록은
  # `sort` 로 만든 사전순이라 버전순이 아니다. 예: xboxdrv_0.8.8-10 이
  # xboxdrv_0.8.8-2 보다 앞서므로 새 버전을 깔고 나서 옛 버전을 다시 깔아
  # 로봇이 다운그레이드된다. 새 파일 검증이 끝난 지금 지운다.
  # 이름 끝의 `_` 는 libusb-1.0-0_* 가 libusb-1.0-0-dev_* 를 잡지 않게 한다.
  find "$OUT_DIR" -maxdepth 1 -type f -name "${pkg}_*_${ARCH}.deb" ! -name "$base" -print -delete
  mv "$tmp_dir/$base" "$OUT_DIR/$base"
  added=$((added + 1))
done

echo
if [[ $missing -eq 0 ]]; then
  echo "$OUT_DIR 가 최신입니다 ($(find "$OUT_DIR" -maxdepth 1 -name '*.deb' | wc -l)개 deb)."
elif [[ $CHECK_ONLY -eq 1 ]]; then
  echo "빠진 deb $missing 개. --check 없이 다시 실행하면 받습니다."
  exit 1
else
  echo "deb $added 개 추가. 총 $(find "$OUT_DIR" -maxdepth 1 -name '*.deb' | wc -l)개."
  echo "이 디렉터리는 저장소에 커밋해야 로봇 배포에 실립니다."
fi
