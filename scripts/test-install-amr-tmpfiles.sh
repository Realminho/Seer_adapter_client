#!/usr/bin/env bash
# Smoke test for the install_amr_tmpfiles pre-write guards in
# setup-adaptor-service.sh: an empty deploy user or a template missing the
# /run/amr-adaptor directive must be rejected, not silently installed (that is
# how the IPC root ends up uncreated and the WebUi reports "adapter offline").
#
# The real function body is extracted from the live script at test time (no
# duplicated copy to drift), then run in DRY_RUN mode against temp templates so
# the test never touches /etc or /run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
setup="$ROOT/scripts/setup-adaptor-service.sh"

fn="$(sed -n '/^install_amr_tmpfiles()/,/^}/p' "$setup")"
if [[ -z "$fn" ]]; then
  echo "FAIL: could not extract install_amr_tmpfiles from $setup" >&2
  exit 1
fi

# Run the real function with the given DEPLOY_USER and template dir, in dry-run,
# in a subshell so its guard 'exit' is captured rather than killing this test.
run_guard() {  # $1=DEPLOY_USER  $2=SYSTEMD_SRC
  (
    DEPLOY_USER="$1"
    SYSTEMD_SRC="$2"
    DRY_RUN=1
    run_root() { :; }
    eval "$fn"
    install_amr_tmpfiles
  ) >/dev/null 2>&1
}

good_src="$(mktemp -d)/systemd"; mkdir -p "$good_src/tmpfiles.d"
cp "$ROOT/scripts/systemd/tmpfiles.d/amr-adaptor.conf" "$good_src/tmpfiles.d/"

bad_src="$(mktemp -d)/systemd"; mkdir -p "$bad_src/tmpfiles.d"
printf '# comment only, no directive\n' >"$bad_src/tmpfiles.d/amr-adaptor.conf"

fail=0

if run_guard "ucore" "$good_src"; then
  echo "PASS: valid user + template accepted"
else
  echo "FAIL: valid user + template was rejected" >&2; fail=1
fi

if run_guard "" "$good_src"; then
  echo "FAIL: empty DEPLOY_USER was accepted" >&2; fail=1
else
  echo "PASS: empty DEPLOY_USER rejected"
fi

if run_guard "ucore" "$bad_src"; then
  echo "FAIL: template missing /run/amr-adaptor directive was accepted" >&2; fail=1
else
  echo "PASS: template missing directive rejected"
fi

if [[ $fail -ne 0 ]]; then
  echo "FAIL install-amr-tmpfiles guards" >&2
  exit 1
fi
echo "PASS install-amr-tmpfiles guards"
