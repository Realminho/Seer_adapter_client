# JIBOT in-place charge — design

**Date:** 2026-06-22
**Status:** Approved for planning

## Goal

Let JIBOT (bot300) **start charging without the ~1 m UmDock back-up** when the
robot is **already at the charge node**, while keeping the existing
move-to-charger-and-dock behaviour unchanged.

Background and the underlying mechanism are documented in
[`docs/reference/jibot-charging-dock-bms.md`](../../reference/jibot-charging-dock-bms.md).
Key facts this design builds on:

- In-place charge = **close the charge relay without moving**:
  `JModeCharge::CloseChargingCircuit()` → ROS topic **`/jcmd`**
  (`jarvis_msgs/Cmd{cmd:3, data:{arg_int8:1}}`); `arg_int8:0` opens it.
- Charging only **persists while `cmd:3=1` is held** (a single latched pulse stops
  when released). The HMI green button charges persistently → working model is it
  **holds** the assertion. **This persistence-by-holding is assumed, to be verified
  on the real robot during implementation.**
- The charge relay is **not** reachable over the `Um*`/7273 channel; the adapter,
  which today talks to the robot **only over TCP 7273 (no ROS)**, must publish to
  ROS `/jcmd` for this feature.
- Charging confirmation is available over 7273 via `UmGetRobotInfo.is_charged`
  (no ROS needed to verify).

## Scope

**In scope**

- A `ChargeCircuit` component that holds/releases the `/jcmd cmd:3` charge relay.
- A dedicated JIBOT instant action **`chargeInPlace`** (explicit / unit-test trigger).
- **Auto-routing** inside the existing `startCharging` flow: when the charge node
  equals the robot's current node, use in-place charge; otherwise use the existing
  `UmDock` path.
- `stopCharging` releases an active in-place hold.
- Unit tests (TDD) with an injected fake `ChargeCircuit`.

**Out of scope (unchanged)**

- Move-to-charger + reflector dock (`UmDock`) when travel is required.
- Charge-stop via double `UmStop` for the move-dock path.
- `BatteryState` reporting (already follows `is_charged`).
- The `jibot-client` UmDock optional-param support already added earlier.

## Design

### 1. `ChargeCircuit` component (new, injectable)

New module under `adaptor/utils/` (e.g. `charge_circuit.py`), following the
existing injectable-client pattern (EZI / PIO clients, DockConfig).

Interface:

- `start_hold()` — begin holding the relay closed (charge ON). Idempotent.
- `stop_hold()` — stop holding and open the relay once (charge OFF). Idempotent.
- `is_holding` — current state.

**Production implementation** spawns and manages a long-lived subprocess:

```
rostopic pub -r <rate> /jcmd jarvis_msgs/Cmd \
  '{cmd: 3, data: {arg_int8: 1, arg_int32: 0, arg_str: ""}}'
```

run under a sourced ROS env (`source /usr/local/urobot/jarvis/setup.bash;
export ROS_MASTER_URI=http://localhost:11311`). `-r <rate>` republishes
continuously so the relay stays asserted (the "hold"). `stop_hold()` terminates
the subprocess and publishes one `arg_int8:0` (OpenChargingCircuit).

**Test implementation** is a fake recording `start_hold`/`stop_hold` calls — no
subprocess, no ROS.

The adapter selects prod vs fake the way it already injects vehicle/EZI/PIO; in
**simulator / non-JIBOT** runs the component is a no-op fake (ROS not available).

Config: a new `[charge_circuit]` TOML section parsed into a `ChargeCircuitConfig`
dataclass (alongside `DockConfig`): `jcmd_topic` (`/jcmd`), `publish_rate_hz`
(default `2`), `ros_setup` (path to `setup.bash`), `ros_master_uri`
(`http://localhost:11311`), `verify_timeout_sec` (default `5`), `enabled`
(default `false`; only true on real JIBOT hosts).

### 2. Action flow (`adapter_jibot.py`)

**a) Dedicated instant action `chargeInPlace`** — always in-place, no detection:

- Register in `SUPPORTED_INSTANT_ACTIONS` (near line 1693).
- Dispatch in the instant-action handler (near line 3130) to a new
  `_handle_charge_in_place_instant_action`:
  1. `ChargeCircuit.start_hold()`
  2. Poll `UmGetRobotInfo.is_charged` up to `verify_timeout_sec`.
  3. `is_charged` true → action **FINISHED**; else `stop_hold()` and **FAILED**
     (`"in-place charge: charger not engaged"`).

**b) Auto-routing in `startCharging`** — in-place when already at the node:

Decision rule: **charge node `==` robot current node (`self._last_node_id`) →
in-place; otherwise existing `UmDock`.**

Hook the three existing `um_dock()` dispatch points so that, for a charge/dock
node already equal to `_last_node_id`, they call the in-place path instead:

- `_send_node_motion` (~line 2737): order node processing.
- `_dock_for_order_action` (~line 2631): order-carried `startCharging`.
- `_handle_start_charging_instant_action` (~line 4438): instant `startCharging`
  (uses `_last_node_id` as "where the robot is"; if that node is a charge/dock
  node, treat as already-there → in-place).

When routed to in-place, reuse the same `ChargeCircuit.start_hold()` + `is_charged`
verification as `chargeInPlace`. When not (different node / travel needed), the
existing `um_dock()` behaviour is untouched.

**c) `stopCharging`** (~line 3064 dispatch): if an in-place hold is active, call
`ChargeCircuit.stop_hold()`. The move-dock charge-stop (double `UmStop`,
`DockConfig.stop_charging_repeat_*`) is unchanged.

### 3. State / verification

- `BatteryState.charging` continues to follow `self._vehicle._charging` /
  `UmGetRobotInfo.is_charged` — no change.
- In-place actions complete on observed `is_charged`, so a mis-seated robot that
  closes the relay but draws no current is reported **FAILED** rather than a false
  success.
- Track an in-place-active flag (e.g. reuse/parallel to `_docking_started_node_ids`)
  so `stopCharging` and order transitions release the hold.

### 4. Testing (TDD)

With an injected fake `ChargeCircuit` and the existing fake JIBOT vehicle:

- `chargeInPlace` → `start_hold()` called; `is_charged` true → FINISHED.
- `chargeInPlace` → relay held but `is_charged` stays false → `stop_hold()` called,
  FAILED.
- `startCharging` where charge node `== _last_node_id` → `start_hold()` (no
  `um_dock`).
- `startCharging` where charge node `!= _last_node_id` → `um_dock()` (no
  `start_hold`).
- `stopCharging` with active hold → `stop_hold()`.
- Simulator / no-ROS → component is a no-op fake; existing charge tests still pass.

`ChargeCircuit` production subprocess behaviour is validated separately on the real
robot (it is the unverified persistence assumption), not in unit tests.

## Risks / open questions

- **Persistence-by-holding unverified.** Continuous `cmd:3=1` keeping charge alive
  is the assumed model; must be confirmed on the real robot (does `jarvis_g` follow
  into `charging` and stay, or fight it?). Verification step (`is_charged`) bounds
  the failure mode.
- **ROS-env coupling.** In-place charge only works where the adapter has the JIBOT
  ROS env + `rostopic`. Must degrade cleanly (fake/no-op) elsewhere; never break the
  existing 7273-only flows.
- **Node-match staleness.** `_last_node_id` must reflect the robot actually parked at
  the charger; the `is_charged` verification catches false positives.
- **Stop-relay on shutdown.** Ensure the hold subprocess is torn down on adapter
  stop / disconnect so the relay is not left asserted by an orphaned process.

## Files (anticipated)

- `adaptor/utils/charge_circuit.py` (new) — `ChargeCircuit` + fake.
- `adaptor/adapter_jibot.py` — `chargeInPlace` action, auto-routing, `stopCharging`
  release.
- `adaptor/config/config.py` (+ `config.toml`) — charge-circuit config.
- `adaptor/tests/test_*` — handler + routing tests with the fake.
