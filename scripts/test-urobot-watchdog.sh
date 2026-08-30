#!/usr/bin/env bash
# urobot-watchdog.sh를 픽스처 위에서 돌려, 2026-08-22 .61 장애가 걸려 있던 판단들을
# 고정한다: 시리얼 계층 부재와 urobot 실패를 구분하는 것, 그리고 포트가 돌아왔을 때
# 포기 래치를 푸는 것.
#
# ss / systemctl / logger / timeout / sleep은 PATH에 가짜를 놓아 대체하고,
# 시리얼 계층·런타임 상태 디렉터리·xhci 드라이버 디렉터리는
# SERIAL_GLOB / RUN_DIR / XHCI_DRV로 임시 트리에 몰아넣는다.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${SCRIPT:-$ROOT_DIR/scripts/urobot-watchdog.sh}"   # 변이 테스트용으로 덮어쓸 수 있게 둔다
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

mkdir -p "$T/bin" "$T/run" "$T/dev" "$T/sys/xhci" "$T/sys/devices"
ln -s ../devices/xhci-hcd.5.auto "$T/sys/xhci/xhci-hcd.5.auto"
: >"$T/sys/devices/xhci-hcd.5.auto"
: >"$T/sys/xhci/bind"
: >"$T/sys/xhci/unbind"
: >"$T/sys/xhci/uevent"

cat >"$T/bin/ss" <<'FAKE'
#!/usr/bin/env bash
[ "${FAKE_LISTENING:-0}" = 1 ] && echo 'LISTEN 0 0 0.0.0.0:7273 0.0.0.0:*'
exit 0
FAKE

cat >"$T/bin/systemctl" <<'FAKE'
#!/usr/bin/env bash
echo "systemctl $*" >>"$CALLS"
case "${1:-}" in
  is-active) echo "${FAKE_UROBOT_STATE:-active}" ;;
  show)      echo "${FAKE_ACTIVE_MONO_US:-0}" ;;
esac
exit 0
FAKE

cat >"$T/bin/logger" <<'FAKE'
#!/usr/bin/env bash
shift 2   # -t TAG 를 버린다
echo "$*" >>"$LOGS"
FAKE

# 스크립트가 unbind 직전에 마지막으로 하는 일이 `timeout N systemctl stop urobot`이다.
# 여기를 가로채는 것이 "이번 재바인드 도중에 포트가 돌아온다"를 표현하는 방법이다.
cat >"$T/bin/timeout" <<'FAKE'
#!/usr/bin/env bash
if [ -n "${FAKE_SERIAL_RETURNS:-}" ]; then
  for i in 0 1 2 3 4; do : >"$SERIAL_DIR/ttyUSB$i"; done
fi
shift
exec "$@"
FAKE

cat >"$T/bin/sleep" <<'FAKE'
#!/usr/bin/env bash
exit 0
FAKE

chmod +x "$T/bin"/*

CALLS="$T/calls.log"
LOGS="$T/logger.log"
SERIAL_DIR="$T/dev"
export CALLS LOGS SERIAL_DIR

FAILED=0

reset_world() {
    rm -f "$T"/run/* "$T"/dev/* "$CALLS" "$LOGS"
    : >"$CALLS"; : >"$LOGS"
}

make_serial() { for i in 0 1 2 3 4; do : >"$T/dev/ttyUSB$i"; done; }

# watchdog을 한 번 실행한다. FOO=bar 형태의 인자는 그 실행의 환경변수가 된다.
run_watchdog() {
    env PATH="$T/bin:$PATH" \
        RUN_DIR="$T/run" \
        SERIAL_GLOB="$T/dev/ttyUSB*" \
        XHCI_DRV="$T/sys/xhci" \
        USB_SETTLE=1 \
        CALLS="$CALLS" LOGS="$LOGS" SERIAL_DIR="$SERIAL_DIR" \
        "$@" \
        bash "$SCRIPT" >/dev/null 2>&1 || true
}

fail() { echo "FAIL [$CASE] $*" >&2; FAILED=1; }

assert_logged() { grep -Fq "$1" "$LOGS" || fail "expected log: $1 (got: $(tr '\n' '|' <"$LOGS"))"; }
assert_not_logged() { grep -Fq "$1" "$LOGS" && fail "unexpected log: $1"; return 0; }
assert_called() { grep -Fq "$1" "$CALLS" || fail "expected call: $1 (got: $(tr '\n' '|' <"$CALLS"))"; }
assert_not_called() { grep -Fq "$1" "$CALLS" && fail "unexpected call: $1"; return 0; }
assert_file() { [ -e "$1" ] || fail "expected file: ${1##*/}"; }
assert_no_file() { [ -e "$1" ] && fail "unexpected file: ${1##*/}"; return 0; }
assert_count() {
    local want="$1" pat="$2" got
    got=$(grep -Fc "$pat" "$CALLS" || true)
    [ "$got" = "$want" ] || fail "expected $want x '$pat', got $got"
}

# --- 1. 정상: 7273이 응답하면 모든 래치를 비우고 아무것도 건드리지 않는다. ---
CASE="healthy"
reset_world
make_serial
echo 3 >"$T/run/urobot-watchdog.count"
echo "serial=absent" >"$T/run/urobot-watchdog.gaveup"
run_watchdog FAKE_LISTENING=1
assert_no_file "$T/run/urobot-watchdog.gaveup"
assert_not_called "restart urobot"

# --- 2. 시리얼 계층 부재: urobot이 아니라 xHCI를 재바인드한다. ---
# 구 watchdog이 틀렸던 지점이다 — ttyUSB 포트가 하나도 없는 기계에 urobot을 세 번 재시작했다.
CASE="serial-missing-rebind-succeeds"
reset_world
run_watchdog FAKE_LISTENING=0 FAKE_UROBOT_STATE=active FAKE_SERIAL_RETURNS=1
assert_logged "xhci rebind #1/3"
assert_logged "serial layer restored"
assert_called "restart urobot"
assert_no_file "$T/run/urobot-watchdog.gaveup"
[ "$(cat "$T/sys/xhci/unbind")" = "xhci-hcd.5.auto" ] || fail "unbind not written"
[ "$(cat "$T/sys/xhci/bind")" = "xhci-hcd.5.auto" ] || fail "bind not written"

# --- 3. 재바인드에는 상한이 있고, 넘으면 serial=absent로 래치가 걸린다. ---
CASE="rebind-budget-exhausts"
reset_world
for _ in 1 2 3; do run_watchdog FAKE_LISTENING=0 FAKE_UROBOT_STATE=active; done
assert_logged "xhci rebind #3/3"
assert_no_file "$T/run/urobot-watchdog.gaveup"
run_watchdog FAKE_LISTENING=0 FAKE_UROBOT_STATE=active      # 4번째 실행: 예산 없음
assert_logged "stopping auto-recovery"
assert_file "$T/run/urobot-watchdog.gaveup"
grep -q '^serial=absent$' "$T/run/urobot-watchdog.gaveup" ||
    fail "latch should record serial=absent"
assert_not_logged "xhci rebind #4"
# 열 포트가 없는 동안 urobot을 재시작한 적이 한 번도 없어야 한다.
assert_not_called "restart urobot"

# --- 4. 포트가 돌아오면 래치가 스스로 풀린다. ---
# .61이 2026-08-22에 두 번 겪은 상황이다(19:07:09, 21:14:08): 허브는 스스로 돌아왔는데
# 구 래치가 걸린 채라 7273이 24분을 더 닫혀 있었다.
CASE="latch-releases-when-serial-returns"
reset_world
echo "serial=absent" >"$T/run/urobot-watchdog.gaveup"
echo 3 >"$T/run/urobot-watchdog.count"
make_serial
run_watchdog FAKE_LISTENING=0 FAKE_UROBOT_STATE=failed
assert_logged "serial layer returned"
assert_logged "resuming auto-restart"
assert_no_file "$T/run/urobot-watchdog.gaveup"
assert_called "restart urobot"

# --- 5. 포트가 있는 채로 걸린 래치는 유지된다. ---
# 그건 진짜 MCU/E-stop 고장이다. 세상이 달라진 게 없으므로 재시도는 루프일 뿐이다.
CASE="latch-holds-when-serial-was-present"
reset_world
echo "serial=present" >"$T/run/urobot-watchdog.gaveup"
echo 3 >"$T/run/urobot-watchdog.count"
make_serial
run_watchdog FAKE_LISTENING=0 FAKE_UROBOT_STATE=active
assert_not_called "restart urobot"
assert_not_logged "serial layer returned"
assert_file "$T/run/urobot-watchdog.gaveup"

# --- 6. bringup 유예창 안에서는 손대지 않는다. ---
CASE="inside-bringup-window"
reset_world
make_serial
UP_S=$(cut -d. -f1 /proc/uptime)
run_watchdog FAKE_LISTENING=0 FAKE_UROBOT_STATE=active \
             FAKE_ACTIVE_MONO_US=$(( (UP_S - 10) * 1000000 ))
assert_not_called "restart urobot"
assert_not_logged "not listening"

# --- 7. 유예창을 지나면 active로 멎은 urobot을 재시작하되, 3회로 막는다. ---
CASE="urobot-restart-capped"
reset_world
make_serial
for _ in 1 2 3 4; do
    run_watchdog FAKE_LISTENING=0 FAKE_UROBOT_STATE=failed
done
assert_count 3 "restart urobot"
assert_logged "restart #3/3"
assert_logged "stopping auto-restart"
grep -q '^serial=present$' "$T/run/urobot-watchdog.gaveup" ||
    fail "latch should record serial=present"
# 내내 포트가 있었으므로 USB 경로는 발동하지 않아야 한다.
assert_not_logged "xhci rebind"

[ "$FAILED" = 0 ] || { echo "urobot-watchdog: FAILURES" >&2; exit 1; }
echo "PASS urobot-watchdog (7 cases)"
