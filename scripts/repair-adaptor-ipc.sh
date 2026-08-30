#!/usr/bin/env bash
# Repair the adapter<->WebUi local IPC root (/run/amr-adaptor) and its tmpfiles
# config.
#
# Fixes the "adapter offline -- not delivered" failure that happens when
# /run/amr-adaptor is missing: the tmpfiles config /etc/tmpfiles.d/amr-adaptor.conf
# is empty/absent, so systemd-tmpfiles never creates the root at boot, the
# non-root adapter cannot mkdir under /run, its control.sock is never bound, and
# the WebUi cannot deliver instant actions. A bound but missing socket (adapter
# alive, IPC task died) is also handled by restarting the adapter.
#
# Run ON the adapter host. Re-execs itself via sudo when a repair is needed and
# the caller is not root (--check is read-only and needs no root). For remote
# use see scripts/repair-adaptor-ipc-over-ssh.sh.
#
# Usage:
#   scripts/repair-adaptor-ipc.sh [--check] [--no-restart]
#
#   --check       Diagnose only; make no changes. Exit 0 = healthy, 1 = repair
#                 needed. Usable from monitoring/CI.
#   --no-restart  Recreate /run/amr-adaptor but do not restart the adapter.
#   -h, --help    Show this help.
set -euo pipefail

CONF=/etc/tmpfiles.d/amr-adaptor.conf
RUNTIME_ROOT=/run/amr-adaptor
SERVICE=amr-adaptor.service

# Canonical tmpfiles content. The source of truth is
# scripts/systemd/tmpfiles.d/amr-adaptor.conf; the "d /run/amr-adaptor ..."
# directive below is kept byte-identical to it, guarded by
# scripts/test-repair-adaptor-ipc-conf.sh so the embedded copy cannot drift.
read -r -d '' TMPFILES_TEMPLATE <<'EOF' || true
# /run/amr-adaptor: adapter <-> WebUi local IPC root (tmpfs).
# Installed by scripts/setup-adaptor-service.sh and repair-adaptor-ipc.sh, which
# substitute __USER__ for this host's deploy user. Each adapter creates and owns
# its own <serial>/ subdirectory under here at runtime (state.json, health.json,
# control.sock); systemd RuntimeDirectory is intentionally NOT used so multiple
# amr-adaptor@<id> instances don't clobber the shared root.
d /run/amr-adaptor 0750 __USER__ __USER__ -
EOF

usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; }

log()  { printf '[repair-ipc] %s\n' "$*"; }
err()  { printf '[repair-ipc] ERROR: %s\n' "$*" >&2; }

CHECK=0
NO_RESTART=0
for a in "$@"; do
  case "$a" in
    -h|--help) usage; exit 0 ;;
  esac
done
# A real repair touches /etc and restarts a unit; re-exec as root unless we are
# only diagnosing (--check is read-only) or already root.
if [[ "$(id -u)" -ne 0 ]] && [[ " $* " != *" --check "* ]]; then
  exec sudo -- "$0" "$@"
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK=1 ;;
    --no-restart) NO_RESTART=1 ;;
    -h|--help) usage; exit 0 ;;
    *) err "unknown argument: $1"; usage >&2; exit 2 ;;
  esac
  shift
done

# --- detection -------------------------------------------------------------

detect_user() {
  # The user the adapter runs as owns its <serial>/ subdir, so the IPC root must
  # be writable by it. Read it from the installed unit; fall back to the caller.
  local u
  u="$(systemctl show "$SERVICE" -p User 2>/dev/null | sed -n 's/^User=//p')"
  [[ -n "$u" ]] || u="${SUDO_USER:-$(id -un)}"
  printf '%s' "$u"
}

conf_ok() {
  # Present, non-empty, and carrying the /run/amr-adaptor directive.
  [[ -s "$CONF" ]] && grep -qE '^d[[:space:]]+/run/amr-adaptor[[:space:]]' "$CONF"
}

root_ok() { [[ -d "$RUNTIME_ROOT" ]]; }

sock_present() {
  local socks=()
  shopt -s nullglob
  socks=("$RUNTIME_ROOT"/*/control.sock)
  shopt -u nullglob
  [[ ${#socks[@]} -gt 0 ]]
}

SVC_USER="$(detect_user)"

conf_state=ok;  conf_ok  || conf_state=BROKEN
root_state=ok;  root_ok  || root_state=MISSING
sock_state=ok;  sock_present || sock_state=MISSING

log "service user        : $SVC_USER"
log "tmpfiles conf       : $CONF [$conf_state]"
log "IPC root            : $RUNTIME_ROOT [$root_state]"
log "control.sock        : $RUNTIME_ROOT/<serial>/control.sock [$sock_state]"

needs_conf_fix=0
[[ "$conf_state" == ok && "$root_state" == ok ]] || needs_conf_fix=1
needs_restart=0
[[ $needs_conf_fix -eq 1 || "$sock_state" != ok ]] && needs_restart=1

if [[ $needs_conf_fix -eq 0 && $needs_restart -eq 0 ]]; then
  log "healthy: nothing to repair."
  exit 0
fi

# --- check mode: report verdict, change nothing -----------------------------

if [[ $CHECK -eq 1 ]]; then
  if [[ $needs_conf_fix -eq 1 ]]; then
    log "VERDICT: repair needed (recreate IPC root + restart adapter)."
  else
    log "VERDICT: repair needed (IPC root ok, adapter restart needed for control.sock)."
  fi
  exit 1
fi

# --- repair ----------------------------------------------------------------

if [[ $needs_conf_fix -eq 1 ]]; then
  log "writing $CONF (user=$SVC_USER)"
  tmp="$(mktemp)"
  printf '%s\n' "${TMPFILES_TEMPLATE//__USER__/$SVC_USER}" >"$tmp"
  install -m 644 "$tmp" "$CONF"
  rm -f "$tmp"

  log "applying: systemd-tmpfiles --create $CONF"
  systemd-tmpfiles --create "$CONF"

  if ! root_ok; then
    err "$RUNTIME_ROOT still missing after systemd-tmpfiles --create."
    err "check 'systemd-tmpfiles --create $CONF' output and that User=$SVC_USER exists."
    exit 1
  fi
  log "IPC root present: $(ls -ld "$RUNTIME_ROOT")"
fi

if [[ $NO_RESTART -eq 1 ]]; then
  log "skipping adapter restart (--no-restart); control.sock will rebind on next restart."
  exit 0
fi

# Restart only the adapter units that are currently active (base unit and/or
# templated amr-adaptor@<id> instances), so the control socket is rebound.
units="$(systemctl list-units --no-legend --plain --state=active 'amr-adaptor*.service' 2>/dev/null | awk '{print $1}')"
[[ -n "$units" ]] || units="$SERVICE"
for u in $units; do
  log "restarting $u"
  systemctl restart "$u"
done

# Give the adapter a moment to rebind the socket, then confirm.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sock_present && break
  sleep 1
done
if sock_present; then
  log "OK: control.sock is bound; WebUi can deliver again."
else
  err "control.sock still absent after restart; inspect 'journalctl -u $SERVICE -b' for a deeper fault."
  exit 1
fi
