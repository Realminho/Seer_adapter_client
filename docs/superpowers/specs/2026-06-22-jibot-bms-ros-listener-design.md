# JIBOT BMS voltage/current → VDA5050 via an in-adapter ROS listener

**Date:** 2026-06-22
**Status:** Design approved, ready for implementation plan
**Audience:** developers/AI agents implementing battery telemetry on the JIBOT adapter

## Problem

VDA5050 state publishes `batteryState.batteryVoltage` (VDA2 + VDA3) and
`powerSupply.batteryCurrent` (VDA3), but both are always empty on JIBOT.

Investigation (see also
[`docs/reference/jibot-charging-dock-bms.md`](../../reference/jibot-charging-dock-bms.md))
found two layered causes:

1. `UmGetBatteryInfo` is never polled in the production loop
   (`robot_info_loop` in `adaptor/main.py` polls `get_robot_info` /
   `get_motor_state` / `get_localization_info` / `get_map` only — never
   `get_battery_info`).
2. **Decisive:** even if it were polled, `UmGetBatteryInfo` over TCP 7273 is a
   firmware **stub** on this robot. Captured read-only on bot A (2026-06-21/22)
   it returned nested zeros
   (`{"EQ":{"EQ":0},"bms_current":{"bms_current":0},"bms_voltage":{"bms_voltage":0}}`)
   while the pack was really at ~53 V. The real BMS truth lives only on the
   MCU, surfaced over **ROS topic `/jrobot_status`** (`jarvis_msgs/RobotStatus`),
   which the 7273-only adapter cannot reach.

So the empty fields are not a mapping bug — the data does not exist on the 7273
channel. This design adds a path to the data that does exist.

## Scope

**In scope:** surface `/jrobot_status.bms_voltage` and `/jrobot_status.bms_current`
into the existing VDA5050 fields.

**Out of scope (data unavailable):**
- `battery_health` (SOH), `battery_temperatures`, `battery_cells` — not present
  on `/jrobot_status`. They stay `None`/`[]`.
- SOC and charging flag — already sourced from 7273 (`UmGetLocState.battery` and
  the status-derived `_charging`). **Not changed.** This deliberately avoids
  touching the existing charge/battery-judgement logic.

## Why this architecture

Constraints that drove the choice (all verified):

- The adapter runs **on the robot** (`ucore@ubuntu:~/adapter`), co-located with
  the ROS1 Noetic master at `localhost:11311`.
- The adapter targets **python ≥ 3.11** (`adaptor/pyproject.toml`,
  `venvJIBOT/lib/python3.12`). Noetic `rospy`/`jarvis_msgs` are catkin-built for
  **system python 3.8**, so `import rospy` inside the adapter process is not
  possible (ABI/path mismatch), and the venv cannot host rospy.
- The robot is modest (6× Cortex-A53, 3.7 GiB, no GPU, load typically 6–8), so
  the solution must be lightweight.

Chosen approach: the adapter starts **one long-lived child process** that runs
the robot's own ROS CLI and streams the topic. The child uses the system ROS
env (python 3.8); the adapter reads its stdout. This keeps the adapter
**ROS-free / python 3.12**, needs **no sidecar service** and **no rosbridge
package**, and spawns the process **once** (not per poll), avoiding repeated
process-spawn cost on a hot CPU.

Rejected alternatives: native in-process `rospy` (requires re-platforming the
adapter to python 3.8 + sourced ROS — large blast radius); rosbridge websocket
(extra package + service on a hot CPU); per-poll `rostopic echo -n1` (process
spawn on every reading); separate sidecar systemd service (extra deployable
unit). All are heavier than a single supervised child inside the adapter.

## Components

### `adaptor/bms_ros_listener.py` — `BmsRosListener`

A self-contained, testable unit.

- **What it does:** supervises one child process
  `bash -lc 'source <ros_setup> && exec rostopic echo -p <topic>'`
  (env `ROS_MASTER_URI=<uri>`), reads its stdout line by line, parses each
  record, and pushes `bms_voltage`/`bms_current` onto the vehicle.
- **How you use it:** `main.py` constructs it with `(vehicle, config)` and runs
  `await listener.run()` as an asyncio task alongside `robot_info_loop`.
- **Depends on:** `asyncio` subprocess + the vehicle's BMS setter. No ROS import.

The stream parsing is factored into a pure function that consumes an async line
iterator, so tests inject a fake stream with no real ROS or subprocess.

#### Output format (`rostopic echo -p`)

`-p` emits CSV: a header row naming columns, then one row per message. Example
header:

```
%time,field.charge,field.EQ,field.bms_voltage,field.bms_current,field.system_status
```

Parsing rules:
- Parse the header once; locate columns whose name ends with `bms_voltage` and
  `bms_current` (suffix match, so nesting/prefix changes don't break it).
- For each data row: split CSV, read those two columns, convert to `float`.
- A malformed/short/non-numeric row is skipped (does not crash, does not clear
  cached values).
- `bms_current` is **signed** (negative = discharge/idle, positive = charging);
  it is passed through raw (no sign flip). `bms_voltage` is volts, `bms_current`
  is amps — both map 1:1 to VDA5050 units.

### Vehicle setter (`jibot-client`)

Add `set_bms(voltage, current)` on the JIBOT client to encapsulate the write:
sets `_battery_voltage`, `_battery_current`, and `_bms_last_update` (a monotonic
timestamp). A `clear_bms()` (or `set_bms(None, None)`) resets them to `None`.

The existing simulator vehicle gets a no-op/compatible setter so the listener
(which is disabled under `--simulator` anyway) never breaks it.

## Data flow & VDA5050 mapping

```
/jrobot_status (ROS, MCU)
  → rostopic echo -p (child, py3.8)
  → BmsRosListener stdout parse (adapter, py3.12)
  → vehicle.set_bms(voltage, current)   [_battery_voltage / _battery_current]
  → existing getters _get_vehicle_battery_voltage() / _get_vehicle_battery_current()
  → batteryState.batteryVoltage (VDA2+VDA3), powerSupply.batteryCurrent (VDA3)
```

**No VDA5050 mapping code changes.** The getters in `adapter_jibot.py` already
read `_battery_voltage` / `_battery_current` and are already wired into the
state/powerSupply builders. Filling the cache is sufficient.

## Lifecycle & failure handling

The adapter must never die because of this listener.

- Started as an asyncio task in `main.py`, next to `robot_info_loop`.
- **Child death** (ROS/urobot restart, master down): caught; restart after
  `restart_backoff_sec` with simple backoff.
- **Spawn failure** (`rostopic` absent / no ROS): log once, keep retrying with
  backoff; cached values stay `None`.
- **Staleness:** if no valid row arrives within `stale_after_sec`, or the child
  dies, the listener calls `clear_bms()` so VDA5050 stops publishing stale
  voltage/current. While the child streams normally, values stay fresh.
- **Simulator:** when `--simulator` is set, the listener is disabled entirely.
- The listener catches its own exceptions; it logs and retries rather than
  propagating into the main loops.

## Configuration

New `config.toml` section `[bms_ros]`:

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Master on/off. Forced off under `--simulator`. |
| `topic` | `/jrobot_status` | ROS topic to echo. |
| `ros_setup` | auto-detect `/opt/ros/<distro>/setup.bash`, else `/opt/ros/noetic/setup.bash` | Sourced before `rostopic`. |
| `ros_master_uri` | `http://localhost:11311` | Exported to the child env. |
| `stale_after_sec` | `30` | No valid row within this window → clear to `None`. |
| `restart_backoff_sec` | `3` | Delay before restarting a dead/failed child. |

ROS-distro auto-detection mirrors the existing logic in
`scripts/setup-adaptor-service.sh` (scans `/opt/ros`).

## Testing (TDD)

No real ROS required — all tests inject fake input.

1. **Parser** (`tests/test_bms_ros_listener_parse.py`):
   - header + rows → correct `(voltage, current)`.
   - column order independence (suffix match).
   - short/non-numeric/empty rows skipped, cache untouched.
   - signed current preserved.
2. **Listener supervision** (`tests/test_bms_ros_listener.py`), driving the
   stream-reading coroutine with an injected async line iterator and a fake
   clock/setter:
   - valid rows call `set_bms` with parsed values.
   - stream ends / child "dies" → restart path invoked.
   - no rows for `stale_after_sec` → `clear_bms()`.
   - disabled (`enabled=false` / simulator) → no child spawned.

Tests follow the existing `adaptor/tests/` style (unittest/pytest, fakes for
external I/O, as in `test_mqtt.py` / `test_jibot_client_*`).

## Side note (optional, no behavior change)

Add a one-line comment at the `UmGetBatteryInfo` parse site in
`jibot-client/src/jibot_client/client.py` noting it reads back a 7273 stub on
this firmware and that the real BMS voltage/current come from the ROS listener.
This prevents a future reader from "fixing" the unused poll path.

## Acceptance

- With the robot streaming `/jrobot_status`, VDA5050 state shows non-null
  `batteryState.batteryVoltage` and `powerSupply.batteryCurrent` matching the
  live pack (≈53–55 V; current signed).
- With ROS/topic absent or under `--simulator`, behavior is unchanged from today
  (both fields `None`); the adapter runs normally.
- SOC and charging behavior are byte-for-byte unchanged.
