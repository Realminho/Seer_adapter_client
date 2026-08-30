# Edge Agent TUI Discovery Design

## Goal

Extend the existing adaptor TUI so one PC can show AMR adaptors and one or more
Dobot edge-agent instances together. Multi-instance edge-agent deployments are
operated as systemd template units named `edge-agent@<id>.service`; the current
edge-agent repo also ships a non-template `edge-agent.service`, which must be
shown as a fallback when present.

## Scope

In scope:

- Discover `edge-agent@*.service` units from systemd.
- Discover the non-template `edge-agent.service` when it is installed or loaded.
- Render each instance in the existing left-side adaptor list as
  `Edge Agent <id>`.
- Reuse the existing Dashboard, Logs, Control, and Tests views for service
  health, `journalctl`, and start/stop/restart/enable/disable actions.
- Add edge-agent diagnostics that run from
  `/home/lab2m-llm1/workspaces/dobot/edge-agent`.

Out of scope for this first pass:

- Parsing edge-agent local API state.
- Sparkplug/RMS protocol monitoring inside this TUI.
- Managing edge-agent configuration files.
- Installing or creating the `edge-agent@.service` template unit. This TUI
  discovers and controls units that already exist.

## Design

Add a systemd discovery function to the TUI systemd layer that merges:

- `systemctl list-units 'edge-agent@*.service' 'edge-agent.service' --all`
- `systemctl list-unit-files 'edge-agent@*.service' 'edge-agent.service'`

This catches both active/loaded units and installed-but-inactive unit files. It
must ignore the bare template name `edge-agent@.service`, malformed template
instances, and duplicate units.

Parse units as:

- `edge-agent@<id>.service` -> `id=<id>`, display `Edge Agent <id>`
- `edge-agent.service` -> `id=default`, display `Edge Agent`

If both `edge-agent.service` and one or more `edge-agent@<id>.service` units
exist, show all of them. The non-template unit represents a separate legacy or
single-cell deployment, not a duplicate of an instance.

Add edge-agent specs in `tui.registry`. The specs use:

- `key`: `edge-agent:<id>`
- `display_name`: `Edge Agent <id>`
- `unit`: `edge-agent@<id>.service`
- `workdir`: `/home/lab2m-llm1/workspaces/dobot/edge-agent`
- `runtime_kind`: `systemd`

For the non-template fallback, use:

- `key`: `edge-agent`
- `display_name`: `Edge Agent`
- `unit`: `edge-agent.service`
- `serial`: `edge-agent`

`AdaptorSpec` currently requires fields that are VDA5050-specific
(`exec_script`, `manufacturer`, `serial`, `vda_full_version`, `topic_prefix`).
The implementation must either add safe defaults to `AdaptorSpec` or explicitly
populate edge-agent specs with inert values:

- `exec_script`: `""`
- `manufacturer`: `"dobot"`
- `serial`: the instance id, or `"edge-agent"` for the non-template unit
- `vda_full_version`: `""`
- `topic_prefix`: `""`

`exec_script=""` keeps edge-agent out of the existing JIBOT manual-process
scanner path.

The edge-agent specs do not attach an MQTT monitor in the first pass. Add an
explicit monitor capability flag, such as `monitor_kind="vda5050"` for AMR
adaptors and `monitor_kind="none"` for edge-agent. The app must use that flag
in all places that currently assume `self.spec_monitor[spec.key]` exists:

- Constructor monitor setup skips `monitor_kind="none"` specs.
- The left-side list renders connection and live summary placeholders for
  unmonitored specs.
- Dashboard skips `self.monitor.get_snapshot()` for unmonitored specs and
  renders a concise `LIVE STATE` placeholder such as `not monitored in this
  pass`.
- Control view must not expose VDA5050 instant actions for edge-agent specs.

Service metrics, logs, and start/stop/restart/enable/disable actions continue
through the existing `SystemdController` path.

If no `edge-agent@*.service` or `edge-agent.service` units are found, add no
edge-agent entries. This keeps the TUI focused on real configured instances.

## Diagnostics

Expose focused edge-agent commands in the Tests view. They run against the
developer checkout at `/home/lab2m-llm1/workspaces/dobot/edge-agent`, not
necessarily the deployed `/opt/edge-agent` tree used by the systemd service.
This is intentional for development diagnostics; production deployment checks
can be added later.

- `pytest tests/test_agent.py tests/test_local_api.py`
- `pytest tests/test_sparkplug_connector.py`
- `ruff check .`

Each command should be resolved when the registry is built:

- Prefer `.venv/bin/pytest` or `.venv/bin/ruff` under the edge-agent repo.
- Fall back to `pytest` or `ruff` on `PATH` if the local venv executable does
  not exist.
- If the edge-agent checkout directory does not exist, omit edge-agent
  diagnostics but still show service/log/control views for discovered units.
- A venv created after the TUI starts is picked up after restarting the TUI.

The two pytest diagnostics are separate commands so an operator can run the
core/local API check independently from the Sparkplug connector check.

## Error Handling

Systemd discovery must be best-effort. If `systemctl` is missing, slow, or
returns an error, the TUI should still start and show the existing AMR adaptor
entries.

Unit parsing must ignore malformed names, the bare `edge-agent@.service`
template, and duplicate instances.

Unmonitored specs must not attempt to subscribe to MQTT with an empty topic
prefix and must not raise `KeyError` during list or dashboard rendering.

## Testing

Add unit tests for:

- Parsing `edge-agent@*.service` unit names into ids.
- Parsing `edge-agent.service` as the non-template fallback.
- Ignoring `edge-agent@.service`, malformed names, and duplicate units.
- Best-effort discovery when `systemctl` is missing, times out, or exits
  nonzero.
- Registry creation including discovered edge-agent instances.
- Registry creation including the non-template `edge-agent.service` fallback.
- Edge-agent `AdaptorSpec` construction with all required fields populated or
  with dataclass defaults.
- App monitor setup and rendering helpers for `monitor_kind="none"` specs so
  no `spec_monitor` lookup is required.
- Runtime status behavior for edge-agent specs with systemd metrics.

Run the existing TUI test subset after implementation.
