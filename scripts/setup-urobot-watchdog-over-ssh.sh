#!/usr/bin/env bash
# JIBOT 온보드 PC에 urobot 7273 watchdog을 SSH로 설치한다.
#
# 무엇을 대체하는가: urobot 재시작밖에 할 줄 모르던 watchdog이다. 2026-08-22에 .61은
# 14:30부터 21:19까지 7273이 닫힌 채였는데, 원인은 urobot이 전혀 아니었다 — FT4232H
# MCU 링크를 물고 있는 VIA Labs 허브가 열거에 실패해 열 ttyUSB 포트 자체가 없었다.
# 구 watchdog은 거기에 재시작 3회를 다 쓰고 "포기" 래치를 건 뒤, 허브가 스스로 돌아온
# 두 번의 기회(19:07:09, 21:14:08)에도 가만히 있었다.
#
# 새 스크립트는 /dev/ttyUSB* 개수를 세어 두 고장을 구분하고, 없는 쪽이 시리얼 계층일
# 때는 xHCI 컨트롤러를 리셋하며, 포트가 돌아오면 포기 래치를 스스로 푼다. 각 상수의
# 근거와 실측 타이밍은 urobot-watchdog.sh 헤더에 적혀 있다.
#
# 정상 로봇에 설치해도 안전하다: watchdog이 가장 먼저 하는 일이 7273 리슨 여부 확인이고
# 열려 있으면 그대로 빠져나가므로, 실제로 뭔가 고장 나기 전까지는 아무것도 건드리지 않는다.
#
# 자산은 로봇의 ~/urobot-watchdog에 올린다(재부팅 때 비워지는 /tmp가 아니다).
# 설치는 sudo가 비밀번호를 물을 수 있도록 대화형 SSH 세션에서 실행한다.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/setup-urobot-watchdog-over-ssh.sh <user@target-host> [--min-serial N]

Arguments:
  <user@target-host>   설치 대상 로봇. 예: ucore@192.168.101.61

Options:
  --min-serial N   MCU 링크가 있다고 볼 ttyUSB 포트 개수 (기본: 4).
                   정상 .61은 5개(FT4232H 4 + PL2303 1)이고 허브가 실패하면
                   0개라 4로 두면 여유 있게 갈린다. MCU 링크가 FT4232H가 아닌
                   로봇에서만 낮춰 잡는다.
  -h, --help       도움말

여기서는 urobot을 재시작하지 않으므로, 지금 주행 중인 로봇은 계속 주행한다.
설치 후 확인:
  ssh <target> 'systemctl is-active urobot-watchdog.timer; ls /dev/ttyUSB*'
USAGE
}

TARGET=""
MIN_SERIAL="4"

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --min-serial) MIN_SERIAL="${2:?--min-serial needs a value}"; shift 2 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      [ -n "$TARGET" ] && { echo "only one target may be given" >&2; exit 2; }
      TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || { usage >&2; exit 2; }
case "$MIN_SERIAL" in
  ''|*[!0-9]*) echo "--min-serial must be a number: $MIN_SERIAL" >&2; exit 2 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="urobot-watchdog"

for f in "$HERE/urobot-watchdog.sh" \
         "$HERE/systemd/urobot-watchdog.service" \
         "$HERE/systemd/urobot-watchdog.timer"; do
  [ -f "$f" ] || { echo "missing asset: $f" >&2; exit 1; }
done

echo "==> staging assets on $TARGET:~/$STAGE"
ssh "$TARGET" "mkdir -p ~/$STAGE"
scp -q "$HERE/urobot-watchdog.sh" \
       "$HERE/systemd/urobot-watchdog.service" \
       "$HERE/systemd/urobot-watchdog.timer" \
       "$TARGET:~/$STAGE/"

# 설치 단계를 `ssh -t ... bash -s`로 파이프하지 않고 파일로 올려서 실행하는 이유:
# 본문을 stdin으로 넣으면 stdin이 터미널이 아니게 되어 ssh가 pty 할당을 거부하고,
# 그러면 sudo가 비밀번호를 물어볼 방법이 없어진다.
ssh "$TARGET" "cat > ~/$STAGE/install-remote.sh" <<'REMOTE'
set -euo pipefail
cd ~/"$STAGE"

# 재바인드 경로는 이 보드가 노출하는 플랫폼 xHCI 인스턴스를 대상으로 한다(.61은
# xhci-hcd.5.auto). 설치 시점에 이름을 확인해 두면, 이름이 다르거나 아예 없는 보드
# (예: PCIe 전용 호스트)를 새벽 장애 중이 아니라 지금 잡을 수 있다.
XHCI_DRV=/sys/bus/platform/drivers/xhci-hcd
XHCI_DEV=""
if [ -d "$XHCI_DRV" ]; then
  for d in "$XHCI_DRV"/*; do
    [ -L "$d" ] || continue
    case "${d##*/}" in bind|unbind|uevent|module) continue ;; esac
    XHCI_DEV="${d##*/}"; break
  done
fi
if [ -n "$XHCI_DEV" ]; then
  echo "    xhci platform device: $XHCI_DEV"
else
  echo "    WARNING: no xhci platform device under $XHCI_DRV --"
  echo "             the USB recovery path will log and skip on this board."
fi

echo "    ttyUSB ports present now: $(ls /dev/ttyUSB* 2>/dev/null | wc -l) (threshold: $MIN_SERIAL)"

sudo install -m 755 urobot-watchdog.sh /usr/local/sbin/urobot-watchdog
sudo install -m 644 urobot-watchdog.service /etc/systemd/system/urobot-watchdog.service
sudo install -m 644 urobot-watchdog.timer   /etc/systemd/system/urobot-watchdog.timer

# 로봇별 임계값을 스크립트 수정 없이 넘겨서, 같은 유닛 파일이 어디서나 그대로 쓰이게 한다.
sudo mkdir -p /etc/systemd/system/urobot-watchdog.service.d
sudo tee /etc/systemd/system/urobot-watchdog.service.d/environment.conf >/dev/null <<EOF
[Service]
Environment=MIN_SERIAL=$MIN_SERIAL
EOF

# 구 스크립트가 남긴 래치 파일을 지운다. 안 지우면 다음 재부팅까지 새 스크립트가
# 묶여 있게 된다: serial= 줄이 없는 gaveup 파일은 "포트가 있는 채로 포기한 것"으로
# 읽혀 영원히 래치가 풀리지 않는다.
sudo rm -f /run/urobot-watchdog.gaveup /run/urobot-watchdog.count \
           /run/urobot-watchdog.rebinds

sudo systemctl daemon-reload
sudo systemctl enable --now urobot-watchdog.timer
# `start urobot-watchdog.service`는 일부러 하지 않는다: 지금 장애 중인 로봇이라면
# 설치하는 사람 발밑에서 재바인드가 시작돼 버린다. 어차피 타이머가 60초 안에 집어간다.

echo
echo "==> verification"
echo -n "    timer: ";  systemctl is-active urobot-watchdog.timer || true
echo -n "    next run: "
systemctl show urobot-watchdog.timer -p NextElapseUSecRealtime --value || true
echo -n "    script has USB recovery: "
grep -q rebind_xhci /usr/local/sbin/urobot-watchdog && echo yes || echo "NO -- stale script"
echo -n "    script releases give-up: "
grep -q 'serial=absent' /usr/local/sbin/urobot-watchdog && echo yes || echo "NO -- stale script"
echo -n "    MIN_SERIAL in effect: "
systemctl show urobot-watchdog.service -p Environment --value | tr ' ' '\n' | grep MIN_SERIAL || echo "(unset)"
echo -n "    7273: "
ss -ltnH 'sport = :7273' 2>/dev/null | grep -q . && echo listening || echo "NOT listening"
echo -n "    ttyUSB: "; ls /dev/ttyUSB* 2>/dev/null | tr '\n' ' '; echo
REMOTE

echo "==> installing (sudo will prompt on the robot)"
ssh -t "$TARGET" "STAGE='$STAGE' MIN_SERIAL='$MIN_SERIAL' bash ~/$STAGE/install-remote.sh"

echo
echo "==> done. The watchdog stays silent until 7273 actually goes down."
echo "    Watch what it decides during an outage:"
echo "      ssh $TARGET 'journalctl -t urobot-watchdog -f'"
echo "    Confirm it can still see the serial layer after a reboot:"
echo "      ssh $TARGET 'ls /dev/ttyUSB*; ss -ltn | grep 7273'"
