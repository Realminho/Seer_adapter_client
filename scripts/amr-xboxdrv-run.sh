#!/bin/sh
# Keep xboxdrv bound to the 8BitDo receiver while a controller is awake.
#
# The receiver publishes two USB product ids: 310b while a controller is paired
# and awake, 3109 once it sleeps. xboxdrv claims its USB handle once at startup,
# never re-claims it, and keeps running after the device is gone, leaving a
# virtual gamepad that never emits an event. Restart=always cannot notice that,
# because the process itself never dies. So follow the device instead: wait for
# 310b, run xboxdrv, and exit as soon as 310b goes away so systemd starts the
# cycle over. Installed as /usr/local/bin/amr-xboxdrv-run.sh.
set -eu

VENDOR=${AMR_XBOXDRV_VENDOR:-2dc8}
PRODUCT=${AMR_XBOXDRV_PRODUCT:-310b}
SYSFS=${AMR_XBOXDRV_SYSFS:-/sys/bus/usb/devices}
XBOXDRV=${AMR_XBOXDRV_BIN:-/usr/bin/xboxdrv}
# The poll interval is the window in which a drive held as the controller sleeps
# keeps being re-sent, so keep it well under the adapter's manual watchdog. Each
# tick is a handful of sysfs reads, which is far cheaper than that risk.
POLL_SEC=${AMR_XBOXDRV_POLL_SEC:-0.25}

receiver_present() {
    for device in "$SYSFS"/*; do
        [ -r "$device/idVendor" ] || continue
        [ -r "$device/idProduct" ] || continue
        [ "$(cat "$device/idVendor")" = "$VENDOR" ] || continue
        # The receiver keeps its bus path across the re-enumeration and swaps
        # only the product id, so the id has to be re-read on every poll.
        [ "$(cat "$device/idProduct")" = "$PRODUCT" ] || continue
        return 0
    done
    return 1
}

until receiver_present; do
    sleep "$POLL_SEC"
done

"$XBOXDRV" --device-by-id "$VENDOR:$PRODUCT" --type xbox360 --silent &
child=$!
echo "[amr-xboxdrv] receiver $VENDOR:$PRODUCT present; xboxdrv pid=$child"

shutdown_child() {
    # SIGTERM, not SIGINT: a non-interactive shell starts background jobs with
    # SIGINT ignored, and that disposition survives exec, so an INT here would
    # be dropped and the wait below would block forever.
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
    exit 0
}
trap shutdown_child INT TERM

while kill -0 "$child" 2>/dev/null; do
    if ! receiver_present; then
        echo "[amr-xboxdrv] receiver $VENDOR:$PRODUCT gone; stopping xboxdrv"
        shutdown_child
    fi
    sleep "$POLL_SEC"
done

wait "$child" 2>/dev/null || true
echo "[amr-xboxdrv] xboxdrv exited on its own; systemd will restart"
