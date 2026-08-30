#!/usr/bin/env bash
# urobot-watchdog — urobot의 JSrvTcp(TCP 7273)가 끝내 안 열릴 때 복구한다.
# adaptor의 "Connect call failed ('10.8.8.8', 7273)"가 사람 손을 부르지 않게 하는 것이 목적이다.
#
# 7273이 닫히는 고장은 두 가지고, 둘은 정반대 처방을 필요로 한다.
# 이 둘을 구분하는 것이 이 스크립트의 존재 이유다.
#
#   1. urobot이 CPU 경합에 밀린 경우. mg_main이 부팅 시 포화 타임아웃으로 죽어도
#      ("Bot start failed!") urobot.service는 계속 "active"라서 systemd는 실패를
#      보지 못한다. → urobot을 재시작한다.
#
#   2. USB 시리얼 계층이 통째로 사라진 경우. usb 1-1의 VIA Labs 허브가 MCU 링크인
#      FT4232H Quad RS232-HS(ttyUSB0~3)를 물고 있는데, 부팅 시 이 허브가 열거에
#      실패하면 bz_robot은 serialOpen() 실패를 영원히 찍고 7273은 열리지 않는다.
#      이때 urobot 재시작은 아무 의미가 없다 — 열 포트 자체가 없다.
#      → xHCI 컨트롤러를 리셋한 뒤 urobot을 재시작한다.
#
# 2번은 2026-08-22 .61에서 규명했다. 커널 로그가 온전한 11개 부팅 중 3개가 허브
# 없이 올라왔고, 그 3개 전부에서 7273이 닫힌 채였다. 구 watchdog은 이걸 1번으로
# 오판해 시리얼 포트가 하나도 없는 기계에 재시작 3회를 다 쓰고 포기했으며, 로봇은
# 누가 들여다볼 때까지 죽어 있었다. xhci-hcd를 unbind/bind 하자 트리 전체가 약 3초
# 만에 재열거됐다(21:14:08 허브 인식, 21:14:10 ttyUSB0~3 attach). 케이블은 아무도
# 건드리지 않았으므로, 이 사실이 커넥터 접촉 불량 가설도 함께 기각한다 — 드라이버
# 재바인드는 아무것도 다시 꽂아주지 않기 때문이다.
#
# 포기 래치는 이 수정의 나머지 절반이다. 2026-08-22에 허브가 스스로 돌아온 적이
# 두 번 있었는데(19:07:09, 21:14:08) 그때마다 래치가 이미 걸려 있어 아무도 urobot을
# 재시작하지 않았고, 첫 번째는 7273이 24분을 더 닫힌 채로 있었다. 이제 래치는 걸릴
# 당시 시리얼 계층이 있었는지를 함께 기록하고, 그 상태가 바뀌면 스스로 풀린다.
# 포트가 있는 채로 포기한 경우(진짜 MCU/E-stop 고장)는 계속 걸려 있는다 — 그쪽은
# 세상이 달라진 게 없어서 재시도해봐야 루프이기 때문이다.
#
# 재바인드의 대가: 같은 허브에 Realtek USB 랜 2개(eth01/eth02, 그중 하나가
# 192.168.2.0/24 센서 링크)와 조이스틱이 함께 물려 있어 몇 초간 같이 끊긴다.
# 10.8.8.8을 들고 있는 온보드 rk_gmac eth0과 wlan0은 건드리지 않으므로 JIBOT 링크도
# SSH 접속도 여기에 얹혀 있지 않다. urobot을 먼저 멈추는 이유는 그 ROS 노드들이
# 포트를 붙잡고 있고 버스를 걷어내는 동안 CPU를 갉아먹기 때문이다.
#
# setup-urobot-watchdog-over-ssh.sh가 설치하며, 60초 타이머로 실행된다.
set -u

PORT="${PORT:-7273}"
MIN_UPTIME="${MIN_UPTIME:-150}"       # urobot이 "active"로 이만큼 버틴 뒤에야 판정한다(초)
MAX_RESTARTS="${MAX_RESTARTS:-3}"     # 부팅당 urobot 재시작 횟수. 넘으면 래치를 건다
MAX_REBINDS="${MAX_REBINDS:-3}"       # 부팅당 xHCI 재바인드 횟수. 더 비싸므로 따로 센다
MIN_SERIAL="${MIN_SERIAL:-4}"         # "MCU 링크가 있다"고 볼 ttyUSB 포트 개수.
                                      # 정상 .61은 5개(FT4232H 4 + PL2303 1)이고 허브가
                                      # 실패하면 0개라, 4로 두면 여유 있게 갈린다
USB_SETTLE="${USB_SETTLE:-25}"        # bind 후 재열거를 기다리는 시간(초)
STOP_TIMEOUT="${STOP_TIMEOUT:-45}"    # `systemctl stop urobot`을 기다리다 그냥 진행하는
                                      # 한계(초). rebind_xhci 주석 참고
XHCI_DRV="${XHCI_DRV:-/sys/bus/platform/drivers/xhci-hcd}"
SERIAL_GLOB="${SERIAL_GLOB:-/dev/ttyUSB*}"
RUN_DIR="${RUN_DIR:-/run}"            # SERIAL_GLOB과 RUN_DIR은 test-urobot-watchdog.sh가
                                      # 픽스처로 몰아넣기 위해 뚫어둔 것이다.
                                      # 운영에서는 아무도 설정하지 않는다

STATE="$RUN_DIR/urobot-watchdog.count"
REBINDS="$RUN_DIR/urobot-watchdog.rebinds"
GAVEUP="$RUN_DIR/urobot-watchdog.gaveup"
TAG=urobot-watchdog

log() { logger -t "$TAG" "$*"; }

listening() { ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; }

# USB 시리얼 포트 개수. MCU 링크는 FT4232H의 4포트이므로 MIN_SERIAL 미만이면 허브가
# 안 올라온 것이고 urobot은 말 붙일 상대가 없다 — PL2303 하나만 살아남은 경우도 포함된다.
serial_count() {
    # shellcheck disable=SC2086  # 글로브가 펼쳐져야 하므로 일부러 따옴표를 안 씌운다
    set -- $SERIAL_GLOB
    [ -e "$1" ] || { echo 0; return; }
    echo $#
}

serial_ok() { [ "$(serial_count)" -ge "$MIN_SERIAL" ]; }

# 카운터 파일을 읽는다. 없거나 숫자가 아니면 0으로 본다.
counter() {
    n=$(cat "$1" 2>/dev/null || echo 0)
    case "$n" in (*[!0-9]*|'') n=0 ;; esac
    echo "$n"
}

clear_state() {
    : >"$STATE" 2>/dev/null || true
    : >"$REBINDS" 2>/dev/null || true
    rm -f "$GAVEUP" 2>/dev/null || true
}

# 플랫폼 xHCI 장치 이름(예: xhci-hcd.5.auto). 보드마다 인스턴스 번호가 달라서
# 하드코딩하지 않고 드라이버 sysfs 디렉터리에서 읽는다. 그 안에서 심볼릭 링크가
# 아닌 항목들은 드라이버 자신의 제어 파일이다.
xhci_dev() {
    [ -d "$XHCI_DRV" ] || return 1
    for d in "$XHCI_DRV"/*; do
        [ -L "$d" ] || continue
        case "${d##*/}" in
            bind|unbind|uevent|module) continue ;;
        esac
        echo "${d##*/}"
        return 0
    done
    return 1
}

# USB 호스트 컨트롤러를 내렸다 올려 그 아래 모든 장치를 재열거시킨다.
# 재부팅이 해주던 복구를, 재부팅 없이 하는 것이다.
rebind_xhci() {
    dev=$(xhci_dev) || { log "no xhci platform device under $XHCI_DRV -- cannot reset USB"; return 1; }

    # urobot이 시리얼 포트를 붙잡고 있고, 재시도하는 ROS 노드들이 CPU를 포화시킨다.
    # 그 상태로 버스를 뽑으면 둘 다 더 나빠진다.
    # 시간 제한을 두는 이유: 멎은 bringup은 urobot의 TimeoutStopSec 90초를 그대로
    # 다 쓸 수 있는데(.61의 21:13:53 사례), 그래도 재바인드는 성공한다 — 결국
    # systemd가 SIGKILL로 끝내고, 여기는 이미 복구 경로이기 때문이다.
    timeout "$STOP_TIMEOUT" systemctl stop urobot >/dev/null 2>&1 || true

    log "serial layer down ($(serial_count) ttyUSB, need $MIN_SERIAL) -- xhci rebind #$1/$MAX_REBINDS on $dev"
    echo "$dev" >"$XHCI_DRV/unbind" 2>/dev/null || { log "xhci unbind failed on $dev"; return 1; }
    sleep 3
    echo "$dev" >"$XHCI_DRV/bind" 2>/dev/null || { log "xhci bind failed on $dev"; return 1; }

    # 실측 타이밍: bind 후 약 1초에 허브 인식, 다시 약 2초 뒤 ttyUSB0~3 attach.
    # 창을 넉넉히 잡은 것은 CPU가 아직 포화 상태인 부팅 직후를 위해서다.
    i=0
    while [ "$i" -lt "$USB_SETTLE" ]; do
        serial_ok && return 0
        sleep 1
        i=$(( i + 1 ))
    done
    return 1
}

# ---------------------------------------------------------------------------

# 정상 — 7273이 응답하면 래치와 카운터를 모두 비워 다음 장애를 백지에서 시작한다.
if listening; then
    clear_state
    exit 0
fi

# --- 고장 2: 시리얼 계층 부재. 여기서 urobot을 재시작하는 건 순수한 소음이다. ---
if ! serial_ok; then
    rebinds=$(counter "$REBINDS")
    if [ "$rebinds" -ge "$MAX_REBINDS" ]; then
        if [ ! -e "$GAVEUP" ]; then
            log "serial layer still down after $rebinds xhci rebinds -- stopping auto-recovery; suspect hub power/cabling, manual check needed."
            echo "serial=absent" >"$GAVEUP"
        fi
        exit 1
    fi

    rebinds=$(( rebinds + 1 ))
    echo "$rebinds" >"$REBINDS"

    if rebind_xhci "$rebinds"; then
        # 앞서 걸린 포기 래치의 전제가 바뀌었으므로 풀어주고,
        # 이제 실제로 존재하는 포트를 상대로 urobot에 깨끗한 기회를 준다.
        rm -f "$GAVEUP" 2>/dev/null || true
        : >"$STATE" 2>/dev/null || true
        log "serial layer restored ($(serial_count) ttyUSB) -- restarting urobot"
        systemctl restart urobot
    else
        log "xhci rebind #$rebinds did not bring the serial layer back ($(serial_count) ttyUSB)"
    fi
    exit 0
fi

# --- 여기부터는 시리얼 계층이 존재하는 경우다. ---

# 포트가 없는 상태에서 걸린 포기 래치를 푼다. 그 사이 허브가 돌아왔고
# (2026-08-22에 두 번은 스스로 돌아왔다) 이걸 풀지 않으면 아무도 urobot을
# 재시작하지 않는다. 포트가 있는 채로 걸린 래치는 그대로 둔다 — 그쪽은 진짜
# MCU/E-stop 고장이고 재시도는 루프일 뿐이다.
if [ -e "$GAVEUP" ]; then
    if grep -q '^serial=absent$' "$GAVEUP" 2>/dev/null; then
        log "serial layer returned ($(serial_count) ttyUSB) after give-up -- resuming auto-restart"
        rm -f "$GAVEUP" 2>/dev/null || true
        : >"$STATE" 2>/dev/null || true
    else
        exit 1
    fi
fi

state=$(systemctl is-active urobot 2>/dev/null || true)
[ "$state" = activating ] && exit 0          # 정상적인 기동 중 — 건드리지 않는다

if [ "$state" = active ]; then               # active인데 7273이 닫힘 → 기동 유예창을 적용한다
    mono_us=$(systemctl show urobot -p ActiveEnterTimestampMonotonic --value 2>/dev/null || echo 0)
    case "$mono_us" in (*[!0-9]*|'') mono_us=0 ;; esac
    [ "$mono_us" -gt 0 ] || exit 0           # 경과 시간을 잴 수 없으면 손대지 않는다
    up_s=$(cut -d. -f1 /proc/uptime)
    active_s=$(( up_s - mono_us / 1000000 ))
    [ "$active_s" -ge "$MIN_UPTIME" ] || exit 0   # 아직 bringup 창 안이다
fi
# 그 외(failed/inactive)는 명백히 죽은 것이므로 즉시 복구한다.

count=$(counter "$STATE")
if [ "$count" -ge "$MAX_RESTARTS" ]; then
    if [ ! -e "$GAVEUP" ]; then
        log "7273 still down after $count restarts (state=$state, $(serial_count) ttyUSB present) -- stopping auto-restart; suspect E-stop/MCU, manual check needed."
        echo "serial=present" >"$GAVEUP"
    fi
    exit 1
fi
count=$(( count + 1 ))
echo "$count" >"$STATE"
log "7273 not listening (state=$state) -- restart #$count/$MAX_RESTARTS of urobot"
systemctl restart urobot
