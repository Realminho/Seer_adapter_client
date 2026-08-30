# Robot Identity Config Ownership Design

## Goal

Make robot identity and per-robot connectivity have exactly one operational
source of truth. Operators should not have to decide whether `id`,
`vehicle_ip`, or hardware module addresses belong in `config.toml`,
`robots.toml`, CLI flags, or deployment scripts.

## Ownership Model

`config/config.toml` owns shared adapter behavior:

- MQTT protocol defaults that are common for the host.
- VDA and adapter tuning values.
- Docking, sound, PIO, factsheet, WebUi, and other shared runtime knobs.
- Defaults that are safe to copy between robots.

`config/robots.toml` owns robot-specific deployment facts:

- `id`, mapped to `vehicle.serial_number`.
- `vehicle_ip` and `vehicle_port`.
- `ezi_io` and `ezi_motor`.
- Per-robot MQTT host/port only when a robot intentionally differs from the
  host default.
- Per-robot `config` file only when a robot truly needs a separate TOML file.

CLI flags remain temporary runtime overrides for diagnostics and manual runs.
They should not be documented as the normal way to store deployed identity.

`config.toml [vehicle].serial_number`, `[vehicle].vehicle_ip`, and the EZI
addresses are development/template fallback values. They must not be treated as
the normal identity or connectivity source for a real JIBOT systemd deployment.

## Runtime Behavior

The existing `main.py --robot <id>` and `robots.toml` override path remains the
primary runtime mechanism. A host with one JIBOT should still use a one-entry
`robots.toml`; this keeps single-robot and multi-robot deployments consistent.

If `robots.toml` is absent, development and simulator runs may continue to load
`config.toml` defaults. Real deployment paths should either create
`robots.toml` or fail before starting with a placeholder identity.

Startup should reject obvious placeholder identities for real JIBOT service
runs. This prevents `config.toml` template values from accidentally becoming a
published VDA5050 serial number or MQTT topic prefix.

A "real JIBOT service run" means a non-simulator JIBOT process started through
the installed systemd service path, not an explicit manual diagnostic run. The
guard should fail fast in these cases:

- `amr-adaptor.service` starts a real JIBOT and `config/robots.toml` is absent
  or has no usable `[[robot]]` entry.
- `config/robots.toml` has more than one JIBOT robot but the default
  `amr-adaptor.service` starts without a specific `--robot`/instance.
- The resolved real-service identity still comes from the `config.toml`
  fallback instead of a fleet entry or explicit diagnostic CLI override.

The template unit `amr-adaptor@<id>.service` should keep resolving `<id>` through
`robots.toml`. If the requested robot id is not present, startup should fail
instead of silently using `config.toml`.

## Deployment Behavior

`scripts/update-jibot-adapter-over-ssh.sh` should continue to preserve remote
`config/robots.toml` by default. `--config-toml-mode overwrite` should be safe
for common adapter setting updates because robot identity lives outside
`config.toml`.

`--robots-toml-mode overwrite` remains the explicit operator action for changing
deployed robot identity or connectivity.

The repository should not ship an active operational `config/robots.toml`.
Tracked simulator inventory can be copied to a real robot by
`--robots-toml-mode overwrite`, so production inventory should be a local
gitignored file and the repository should provide examples/templates instead
(`robots.toml.example`, and optionally a simulator-specific example). The update
manual should warn that overwriting `robots.toml` replaces the robot inventory.

## Documentation Changes

Docs should consistently state:

- `config.toml` is shared adapter configuration.
- `robots.toml` is the deployment inventory and identity file.
- `id` is unique and required for real robot deployments.
- `config.toml [vehicle].serial_number` is only a default/template fallback,
  not the normal deployed identity.

Prioritize README, adapter README, WebUi guide, service setup, and SSH update
manual language. Do not spend implementation effort aligning superseded docs
until the active operational docs are consistent.

## Testing

Focused tests should cover:

- A one-entry `robots.toml` overrides `config.toml` identity for default service
  startup.
- Placeholder identity is rejected for real service startup when no
  `robots.toml` identity is available.
- Multi-robot `robots.toml` plus default `amr-adaptor.service` without
  `--robot`/instance fails before publishing.
- `amr-adaptor@<id>.service` fails when `<id>` is not in `robots.toml`.
- Simulator/development paths can still run with explicit test identities.
- The SSH update script continues to preserve `robots.toml` by default and only
  overwrites it when requested.

Add direct tests around `main.resolve_instance` and the new service-start guard,
not only fleet registry or systemd dispatch tests. The guard is the safety
boundary that prevents wrong MQTT identity publication.

## Non-Goals

This design does not remove all existing CLI overrides. It also does not require
moving every common network value out of `config.toml`; only values that identify
or connect a specific robot move to `robots.toml` as the normal operational
source of truth.
