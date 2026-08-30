#!/usr/bin/env bash
# Smoke test (drift guard): the tmpfiles directive embedded in
# repair-adaptor-ipc.sh must stay byte-identical to the canonical template
# installed by setup-adaptor-service.sh. If someone edits the template (path,
# mode, ownership) without updating the repair script, the embedded copy would
# silently recreate /run/amr-adaptor with stale settings -- this catches that.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template="$REPO_ROOT/scripts/systemd/tmpfiles.d/amr-adaptor.conf"
repair="$REPO_ROOT/scripts/repair-adaptor-ipc.sh"

# Compare only the 'd ...' tmpfiles directive line(s); the surrounding comments
# are allowed to differ between the two files.
t_dir="$(grep -E '^d[[:space:]]' "$template" || true)"
r_dir="$(grep -E '^d[[:space:]]' "$repair" || true)"

if [[ -z "$t_dir" ]]; then
  echo "FAIL: no 'd ' directive found in template: $template" >&2
  exit 1
fi
if [[ "$t_dir" == "$r_dir" ]]; then
  echo "PASS: repair-adaptor-ipc.sh tmpfiles directive matches template"
  echo "  $t_dir"
else
  echo "FAIL: embedded tmpfiles directive drifted from template" >&2
  echo "  template ($template):" >&2
  echo "    $t_dir" >&2
  echo "  repair   ($repair):" >&2
  echo "    ${r_dir:-<none>}" >&2
  exit 1
fi
