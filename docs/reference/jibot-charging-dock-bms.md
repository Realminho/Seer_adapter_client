# JIBOT charging, docking & BMS telemetry

How JIBOT (bot300) starts/stops charging, how the dock maneuver works, and where
the battery/BMS telemetry actually lives. Audience: AI agents and developers
working on charging behaviour or battery reporting.

All values below were captured read-only on **bot A (`ucore@192.168.3.222`)** on
**2026-06-21/22** (urobot disassembly + live ROS/7273 reads). See also
[`jibot-onboard-access.md`](jibot-onboard-access.md).

## TL;DR

- There are **two ways charging starts**:
  1. **Move-and-dock (existing path):** VDA5050 `startCharging` → `UmDock` → urobot
     `JModeCharge` runs a reflector-aligned dock (**~1 m back-up + re-approach** via
     `JActRefBack`) to seat the contacts, then charges. The ~1 m back-up is
     inherent to the reflector alignment.
  2. **In-place charge (HMI green button):** closes the charge relay **without
     moving** — `JModeCharge::CloseChargingCircuit()` → ROS `/jcmd`
     `jarvis_msgs/Cmd{cmd:3, arg_int8:1}` to the motor-controller MCU. Charges with
     `odom_v ≈ 0` (no real movement). **Not** sent over the `Um*`/7273 channel.
- **Battery/BMS truth is on the MCU**, surfaced via ROS topic **`/jrobot_status`**
  (and `/jbattery`), **not** via `UmGetBatteryInfo` (which read back stub zeros).

## Charge engagement: relay control (cmd:3)

The charge relay is controlled by these urobot/`jarvis-g` functions (symbols in
`jarvis/lib/jarvis-g/jarvis-g`):

| Function | Effect |
| -------- | ------ |
| `JModeCharge::CloseChargingCircuit()` | Close relay → **start charging** |
| `JModeCharge::OpenChargingCircuit()`  | Open relay → **stop charging** |
| `JModeCharge::SetCharged()` / `SetChargeState()` | Charge state machine |

On the wire this is a ROS message on **`/jcmd`** (type `jarvis_msgs/Cmd` =
`int8 cmd; CmdData data`, where `CmdData` = `arg_int8, arg_int32, arg_str`):

- `{cmd: 3, data: {arg_int8: 1}}` → close circuit (charge ON)
- `{cmd: 3, data: {arg_int8: 0}}` → open circuit (charge OFF)
- `cmd: 21` on `/jcmd` is a **status string** ("Dock moving" / "Stopped" /
  "charging"), not a command.

`/jcmd` is consumed by `rosserial_server` (the MCU bridge) and `store_motor`.

**Persistence caveat (verified):** a single latched publish of `cmd:3=1`
(`rostopic pub -1`, 3 s latch) starts charging but it **stops when the publish is
released** — the relay needs the assertion **held**. The HMI green button keeps
charging persistent (battery climbed 77 %→88 %), so the working model is that the
in-place trigger **holds** `cmd:3=1` continuously; once current flows, `jarvis_g`
follows into the `"charging"` state. A momentary pulse is not enough.

## The dock maneuver (UmDock / JModeCharge)

- `UmDock` (7273) → `JModeCharge::Start(json)`. It branches:
  - **charger-detected byte set → `SetCharged()` directly** (no back-up), else
  - **`UpdateDocker()` + reflector approach** (the ~1 m `JActRefBack` back-up).
- `JActRefBack::Init` reads back-up params from the UmDock JSON: `offset`
  (back distance, **default 1000 mm = the "1 m"**), `speed` (default 600), `gap`,
  `ref_max_secs`, `ref_center_robot`, `center`, `reliable_reflector_distance`,
  `ref_distance_by_robot_front`, etc. A `"will check to approaching directly"` path
  exists when the offset is small.
- **`UmDock` accepts JSON params** (urobot side) even though the stock client sent
  none: `goal` (`"auto"`/charger name), `detect_charging_signal`,
  `dock_move_additional_dist`, `dock_rotate_additional_angle`, `need_turn_around`,
  `need_heading`, `use_avoid_area`, `disable_motor_secs`,
  `clearance_back_min/front_min`. The `jibot-client` now allows these as optional
  UmDock params.
- **Tested:** `UmDock{detect_charging_signal:true}` while merely parked (not seated)
  → robot **stays in place** and enters `ModeCharge`, **but `is_charged` stays
  false** (relay never closes; it waits for a charge signal that never asserts),
  then times out back to `Stop`. So skipping the back-up alone does **not** charge —
  the reflector re-approach is what physically seats the contacts.

## BMS / battery telemetry — where to read it

The motor-controller MCU is bridged by `rosserial_server`, which **publishes**
`/jbattery` (`jarvis_msgs/Battery`), `/jrobot_status` (`jarvis_msgs/RobotStatus`),
`/jio` (`jarvis_msgs/Io`), `/jcmd_res`, `/jmotor_*`, `/jodom`, `/jimu`, `/jfault`,
`/jinfo`, `/jsonar`; and **subscribes** to `/jcmd`, `/jvel`, `/jtrack`, `/jrotate`.

**`/jrobot_status` carries the live battery/charge truth:**

| Field | Meaning | Observed |
| ----- | ------- | -------- |
| `charge` | charging flag | `0` = not charging, `1` = charging |
| `EQ` | battery state-of-charge (%) | 77–88 during tests |
| `bms_voltage` | pack/bus voltage (V) | ~`53.0` resting, ~`54.6` while charging |
| `bms_current` | pack current (A), **signed** | **negative** (~`-0.4…-1.2`) idle/discharge; **positive** (~`+7…+23`) charging |
| `odom_v` / `vel_f`/`vel_r` | motion | ~0 when charging in place |
| `system_status` | text | `"Normal..."` |

**Sign convention:** `bms_current < 0` = discharging (idle draw), `bms_current > 0`
= charging. `is_charged` (in 7273 `UmGetRobotInfo`) tracks `charge`.

**Do NOT trust `UmGetBatteryInfo` (7273) for BMS values here:** it returned a
nested stub `{"EQ":{"EQ":0},"bms_current":{"bms_current":0},"bms_voltage":{"bms_voltage":0}}`
(all zeros) even while the real pack was at ~53 V. Use `/jrobot_status` /
`/jbattery` for actual battery data on this robot.

## Startup SOC and `STARTUP` operating mode

At adapter startup, SOC is unknown until the first JIBOT status snapshot carries
the 7273 `battery` field. Unknown SOC must not be represented internally as
`0`, because ACS/FMS layers commonly treat `stateOfCharge=0` as a real low-battery
condition.

Current invariant:

- `jibot-client/src/jibot_client/client.py` starts with `_battery = None` and
  `_battery_known = False`.
- `_battery_known` becomes `True` only when `_update_status_snapshot()` sees a
  `battery` key in a JIBOT response.
- While `[jibot_client].require_battery_before_ready = true` and
  `_battery_known = False`, `adaptor/adapter_jibot.py` reports
  `operatingMode = STARTUP`.
- During that same window, `powerSupply.stateOfCharge` uses the configured
  `[jibot_client].startup_battery_soc` fallback. This is a reporting fallback
  only; once `_battery_known = True`, the actual JIBOT SOC is used, including a
  real `0`.

Diagnostic `information` references published in the VDA5050 state:

| Reference key | Meaning |
| ------------- | ------- |
| `adapterInitializing` | `"true"` while startup readiness checks are pending |
| `adapterInitPending` | comma-separated pending reasons, currently `battery` |
| `jibotBatteryKnown` | `"true"` only after a JIBOT response has included `battery` |
| `jibotBattery` | raw JIBOT SOC value; empty while unknown |

Operational interpretation: if the robot stays in `STARTUP` with
`adapterInitPending=battery`, investigate missing/stale JIBOT status frames or a
firmware response shape change. Do not treat this as a discharged battery unless
`jibotBatteryKnown=true` and the reported SOC is actually low.

## "In contact but not charging" diagnostic

When the robot is parked touching the charger but **not** properly seated, the
charge circuit is open: `UmGetInput = [0,0,0,0]`, `UmGetVirtualIO = 0…0`,
`charge: 0`, `bms_current` negative, `is_charged: false`. Mechanical contact ≠
closed charge circuit — the seating (reflector re-approach, or holding `cmd:3=1`)
is what closes it. Note `UmGetInput`/`UmGetOutput` read the **Topcore** I/O board,
which is `enable:false` on this robot (see
[`jibot-led-control.md`](jibot-led-control.md)), so the charge-detect line is not
visible there regardless.

## Stopping charge

Stopping charge from the order/instant-action path uses **double `UmStop`** (the
firmware ignores a single one) — repeat 2× with a ~3 s gap (configurable in
`DockConfig.stop_charging_repeat_count` / `_gap_sec`). At the relay level, stop is
`cmd:3=0` (`OpenChargingCircuit`).

## Transport reality (for adapter work)

The adapter / `jibot-client` talk to the robot **only over TCP 7273** (`Um*`
commands) — **no ROS**. The in-place charge (`cmd:3` / `CloseChargingCircuit`) is a
**ROS `/jcmd`** path and is **not** exposed as any `Um*` command, and the HMI green
button does **not** use 7273. So adding adapter-driven in-place charge requires a
ROS publish path (hold `/jcmd cmd:3=1`), not a 7273 command.

See also: [`jibot-led-control.md`](jibot-led-control.md) (Topcore disabled, ROS
nodelet patterns), [`jibot-onboard-access.md`](jibot-onboard-access.md).
