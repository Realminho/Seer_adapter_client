> **SUPERSEDED** — The curses TUI was removed in Phase 4. Use the WebUi instead: see [web-ui.md](web-ui.md).

# Adaptor TUI control panel

A `pm2 monit`-style terminal UI for the JIBOT and Hexplorer VDA5050 adaptors.
It manages them as **systemd** services, **monitors** live status (systemd
metrics + the adaptor's own MQTT state), runs **tests/diagnostics**, **controls**
the robot via VDA5050 instant actions, and views/edits **config**.

It is pure stdlib `curses` (no extra dependencies); live MQTT monitoring uses
`paho-mqtt`, which already ships with the adaptor.

## One-time install (per host)

Run on the host that runs the adaptor (local management only):

```bash
scripts/setup-adaptor-service.sh --all          # install both units
scripts/setup-adaptor-service.sh --jibot        # JIBOT only
scripts/setup-adaptor-service.sh --all --dry-run # preview, change nothing
```

The installer:

1. generates `/etc/systemd/system/amr-adaptor.service`, the per-robot template
   `amr-adaptor@.service` from `scripts/systemd/*.service`, filling in this
   host's `User=` and `WorkingDirectory=` (vendor is set by `config.toml
   [adapter].vendor`);
2. repairs `adaptor/venvJIBOT` from `adaptor/offline_packages` (offline) so the
   adaptor and the TUI both have `paho-mqtt` — skip with `--no-venv`;
3. installs a scoped `/etc/sudoers.d/adaptor-tui` so the TUI's start/stop/restart
   buttons work without a password prompt (only `systemctl <verb> <those units>`
   for your user, including the `amr-adaptor@*.service` instances) — skip with
   `--no-sudoers`.

It needs `sudo` for the `/etc/...` writes and `systemctl` calls; it will prompt
once. The adaptors are `enable`d (start on boot) but not started — start them
from the TUI or `sudo systemctl start amr-adaptor.service`.

### Multiple robots (fleet)

If `adaptor/config/robots.hcl` is absent or contains one `robot` block, the
installer uses the default `amr-adaptor.service`. Only fleets with two or
more `robot` blocks enable one `amr-adaptor@<id>.service` per robot, and
the TUI's left pane lists every robot separately (each with its own systemd
unit and `amr/v3/<id>` MQTT topic). See
[simulator.md](simulator.md#5-시뮬레이터-여러-대-동시-실행) and
`adaptor/config/robots.hcl.example` for the fleet file format.

## Launch

```bash
scripts/adaptor-tui.sh            # uses adaptor/venvJIBOT python
scripts/adaptor-tui.sh --no-sudo  # never use sudo for systemctl (read-only/dev)
```

## What the TUI Discovers

The TUI shows three kinds of adaptor runtime information:

- **systemd service:** `amr-adaptor.service` (or `amr-adaptor@<id>.service` for fleet instances) is installed and reported by `systemctl show`.
- **configured fleet:** if `adaptor/config/robots.hcl` has one `robot` block, it is shown through the default JIBOT row/service with that serial/topic prefix. With two or more entries, each robot becomes a separate JIBOT row.
- **manual process:** if a local `python main.py --id <serial>` process is running, the matching JIBOT row shows `manual process` even when the systemd unit is not installed or inactive.

Live robot fields still come from MQTT. For a serial `<id>`, the TUI subscribes to:

```text
<vda_interface>/<vda_version>/<id>/connection
<vda_interface>/<vda_version>/<id>/state
```

If service/process status is visible but live state is blank, check the broker host/port and the serial/topic prefix printed in the Dashboard view.

## Layout & views

- **Left pane** lists every adaptor (each fleet robot from `config/robots.hcl`
  + Hexplorer) with a status dot, CPU%, memory, uptime, restart count, and MQTT
  connection state. Status is reconciled live from either the systemd unit or a
  matching running process. `◂ ▸` (or `[` `]`) switches the selected adaptor.
- **Views** switch with `1`–`5` or `Tab`:

| # | View      | What it shows / does |
|---|-----------|----------------------|
| 1 | Dashboard | runtime health (status/source, serial/topic, PID, uptime, restarts, CPU, mem) + live MQTT state (connection, mode, battery gauge, x/y/θ, mapId, localization, last node, errors, state staleness) + active order (orderId/updateId, remaining nodes, forward path `last → n1 → n2 …`, action-status counts) |
| 2 | Logs      | `journalctl -u <unit> -f`; `↑↓`/`PgUp`/`PgDn` scroll, `f` toggle follow, `g` jump to bottom |
| 3 | Control   | start / stop / restart / enable / disable + instant actions (JIBOT: pause/resume/cancel/charge/state/factsheet; Hexplorer: stand/walk/stop/camera). `↑↓` select, `Enter` activate |
| 4 | Tests     | run unittest suites & diagnostics (incl. simulator smoke test); `Enter` run, `x` stop, output streams below |
| 5 | Config    | edit hot fields inline (`Enter`) or open the whole file in `$EDITOR`; `Validate config`; `Restart service to apply` |

Global keys: `?` help, `q` quit.

## Safety: robot control

Control-view actions that move the robot or affect order execution
(stop/restart/disable service, and `startPause`/`stopPause`/`cancelOrder`/
`startCharging`/`stand*`/`walkMode`/`stop`) prompt **`y/N`** before firing.
Instant actions are published to `<prefix>/instantActions` on the broker the
adaptor uses, so they reach whichever robot is currently online on that serial.

## Config changes require a restart

The adaptor reads `config/config.toml` once at startup. After editing in the
Config view, the TUI validates the file against the adaptor's own loader and
reminds you to **restart the service** (Config → *Restart service to apply*, or
Control → *Restart service*) for changes to take effect.

## Troubleshooting

- **"not installed"** on a card → run `scripts/setup-adaptor-service.sh`.
- **`manual process` on a card** → a JIBOT adaptor is running outside systemd, usually from `run-main.sh`, `run-multi.sh`, or `python main.py --id ...`. Service buttons still target the configured systemd unit; stop the manual process from the terminal that launched it.
- **Battery bar shows broken block/dot glyphs** → run `ADAPTOR_TUI_ASCII=1 scripts/adaptor-tui.sh` to force ASCII gauges.
- **Control says "needs privileges"** → the sudoers drop-in is missing; re-run
  the installer, or run the printed `sudo systemctl …` command manually.
- **Logs empty** → the unit isn't installed, or your user can't read the journal
  (add it to the `systemd-journal`/`adm` group, or run the TUI with privileges).
- **Live state blank / "broker … connecting"** → the broker
  (`[mqtt_broker] host:port`) is unreachable, or the adaptor isn't publishing yet.
  Service control, tests, and config still work without the broker.
