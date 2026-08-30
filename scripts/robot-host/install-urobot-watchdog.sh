#!/usr/bin/env bash
#
# install-urobot-watchdog.sh
#
# Two non-invasive, systemd-level reliability measures for a JIBOT robot host
# (e.g. 192.168.3.222 / .223). The vendor urobot stack — /usr/local/urobot,
# urobot.launch, configs.json — is NOT modified; everything lives in
# /etc/systemd/system drop-ins and a local watchdog, so `--uninstall` fully reverts.
#
#   A-2  ExecStartPre delay on urobot.service so the OS boot burst settles before
#        urobot's ~31-node launch piles on (de-overlaps the two CPU spikes).
#   B-2  A watchdog timer that restarts urobot if its JSrvTcp server (TCP 7273)
#        never comes up. Targets the boot CPU-saturation failure where mg_main
#        times out waiting for "robot ready", exits "Bot start failed!", yet
#        urobot.service stays "active" — leaving 7273 dead and the robot unusable
#        until someone notices and restarts it by hand.
#
# Usage (on the robot):
#   sudo ./install-urobot-watchdog.sh              # install / re-install (idempotent)
#   sudo ./install-urobot-watchdog.sh --uninstall  # remove everything, revert
#
# Tunables (env overrides):
#   BOOT_DELAY=30 START_TIMEOUT=180 MIN_UPTIME=150 MAX_RESTARTS=3 sudo -E ./install-urobot-watchdog.sh
#
set -euo pipefail

BOOT_DELAY="${BOOT_DELAY:-30}"      # A-2: seconds to delay urobot start
START_TIMEOUT="${START_TIMEOUT:-180}" # A-2: must be longer than BOOT_DELAY
MIN_UPTIME="${MIN_UPTIME:-150}"     # B-2: seconds urobot must be "active" before we judge it
MAX_RESTARTS="${MAX_RESTARTS:-3}"   # B-2: auto-restarts per boot before we stop and alert
PORT="${PORT:-7273}"                # B-2: JSrvTcp port to health-check

DROPIN_DIR=/etc/systemd/system/urobot.service.d
DROPIN="$DROPIN_DIR/10-boot-delay.conf"
WD_BIN=/usr/local/sbin/urobot-watchdog
WD_SVC=/etc/systemd/system/urobot-watchdog.service
WD_TIMER=/etc/systemd/system/urobot-watchdog.timer

log() { printf '  %s\n' "$*"; }

need_root() {
    [ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root, e.g. sudo $0 $*" >&2; exit 1; }
}

check_target() {
    if ! systemctl cat urobot.service >/dev/null 2>&1; then
        echo "WARNING: urobot.service not found on this host." >&2
        echo "         Are you on a JIBOT robot host? Continuing anyway." >&2
    fi
}

install() {
    need_root "$@"
    check_target

    echo "Installing urobot reliability measures (A-2 boot delay + B-2 watchdog)..."

    # --- A-2: boot-delay drop-in ------------------------------------------------
    mkdir -p "$DROPIN_DIR"
    cat >"$DROPIN" <<EOF
# A-2: let the OS boot burst settle before urobot's 31-node launch piles on.
# Managed by install-urobot-watchdog.sh. Vendor unit untouched; remove to revert.
[Service]
TimeoutStartSec=${START_TIMEOUT}
ExecStartPre=/bin/sleep ${BOOT_DELAY}
EOF
    log "A-2  $DROPIN  (TimeoutStartSec=${START_TIMEOUT}, ExecStartPre=/bin/sleep ${BOOT_DELAY})"

    # --- B-2: watchdog script ---------------------------------------------------
    # NOTE: written with a quoted heredoc so $VARS stay literal; tunables come in
    # via the service unit's Environment= (see below), with safe defaults here.
    cat >"$WD_BIN" <<'WDEOF'
#!/usr/bin/env bash
# urobot-watchdog — recover urobot if its JSrvTcp (TCP 7273) never comes up.
#
# The trap this guards: urobot.service stays "active" even after mg_main exits
# ("Bot start failed!" on a boot-time CPU-saturation timeout), so systemd never
# sees a failure. We detect "active but 7273 not listening" past the bringup
# window and restart, with a per-boot cap so a real hardware/E-stop fault is not
# masked by an endless restart loop.  Installed by install-urobot-watchdog.sh.
set -u

PORT="${PORT:-7273}"
MIN_UPTIME="${MIN_UPTIME:-150}"     # sec urobot must be "active" before we judge it
MAX_RESTARTS="${MAX_RESTARTS:-3}"   # auto-restarts per boot before we stop and alert
STATE=/run/urobot-watchdog.count
GAVEUP=/run/urobot-watchdog.gaveup
TAG=urobot-watchdog

listening() { ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; }

# Healthy → reset counters and exit.
if listening; then
    : >"$STATE" 2>/dev/null || true
    rm -f "$GAVEUP" 2>/dev/null || true
    exit 0
fi

state=$(systemctl is-active urobot 2>/dev/null || true)
[ "$state" = activating ] && exit 0          # legit start in progress — don't touch

if [ "$state" = active ]; then               # active but 7273 down → enforce uptime gate
    mono_us=$(systemctl show urobot -p ActiveEnterTimestampMonotonic --value 2>/dev/null || echo 0)
    case "$mono_us" in (*[!0-9]*|'') mono_us=0 ;; esac
    [ "$mono_us" -gt 0 ] || exit 0           # can't measure age → stay hands-off
    up_s=$(cut -d. -f1 /proc/uptime)
    active_s=$(( up_s - mono_us / 1000000 ))
    [ "$active_s" -ge "$MIN_UPTIME" ] || exit 0   # still inside bringup window
fi
# else: state is failed/inactive → clearly down, recover immediately.

count=$(cat "$STATE" 2>/dev/null || echo 0)
case "$count" in (*[!0-9]*|'') count=0 ;; esac
if [ "$count" -ge "$MAX_RESTARTS" ]; then
    [ -e "$GAVEUP" ] || logger -t "$TAG" \
      "7273 still down after $count restarts (state=$state) — stopping auto-restart; suspect E-stop/MCU/hardware, manual check needed."
    : >"$GAVEUP"
    exit 1
fi
count=$(( count + 1 ))
echo "$count" >"$STATE"
logger -t "$TAG" "7273 not listening (state=$state) — restart #$count/$MAX_RESTARTS of urobot"
systemctl restart urobot
WDEOF
    chmod 0755 "$WD_BIN"
    log "B-2  $WD_BIN"

    # --- B-2: service (oneshot, fired by the timer) -----------------------------
    cat >"$WD_SVC" <<EOF
[Unit]
Description=urobot 7273 health watchdog (auto-recover failed boot bringup)
After=urobot.service

[Service]
Type=oneshot
Environment=PORT=${PORT} MIN_UPTIME=${MIN_UPTIME} MAX_RESTARTS=${MAX_RESTARTS}
ExecStart=${WD_BIN}
EOF
    log "B-2  $WD_SVC  (MIN_UPTIME=${MIN_UPTIME} MAX_RESTARTS=${MAX_RESTARTS})"

    # --- B-2: timer -------------------------------------------------------------
    cat >"$WD_TIMER" <<EOF
[Unit]
Description=Run urobot 7273 watchdog periodically

[Timer]
OnBootSec=120
OnUnitActiveSec=60
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF
    log "B-2  $WD_TIMER  (first check +120s, then every 60s)"

    systemctl daemon-reload
    systemctl enable --now urobot-watchdog.timer

    echo
    echo "Done. A-2 applies on next urobot (re)start; B-2 is live now."
    echo "Verify:"
    echo "  systemctl list-timers urobot-watchdog* --no-pager"
    echo "  sudo $WD_BIN; echo exit=\$?      # 7273 up -> exit=0"
    echo "  journalctl -t urobot-watchdog --no-pager -n 20"
}

uninstall() {
    need_root "$@"
    echo "Removing urobot reliability measures..."
    systemctl disable --now urobot-watchdog.timer 2>/dev/null || true
    rm -f "$WD_TIMER" "$WD_SVC" "$WD_BIN" "$DROPIN"
    rmdir "$DROPIN_DIR" 2>/dev/null || true
    rm -f /run/urobot-watchdog.count /run/urobot-watchdog.gaveup
    systemctl daemon-reload
    echo "Done. urobot.service reverted to vendor defaults (restart urobot to drop the boot delay)."
}

case "${1:-install}" in
    install)             install "$@" ;;
    --uninstall|uninstall) uninstall "$@" ;;
    -h|--help)           sed -n '2,30p' "$0" ;;
    *) echo "usage: $0 [install|--uninstall]" >&2; exit 2 ;;
esac
