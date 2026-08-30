# lastNodeId Capture Mode + Station-Bootstrap Pose Guard Design

> Baseline: branch `jibot-client-refactor`, `adaptor/adapter_jibot.py` (current
> working tree). Line refs are against that file at design time and will drift.
>
> Evolves the two-variable split
> (`docs/superpowers/specs/2026-06-20-last-node-id-vda5050-design.md`) and the
> finished-aware idle capture
> (`docs/superpowers/specs/2026-06-23-last-node-id-runtime-passthrough-capture-design.md`).
> Read both first. Related: `docs/reference/jibot-arrival-and-last-node.md`
> (station is unreliable), memory `acs-consumes-lastnodeid-for-progress`.

## Purpose

`lastNodeId` (VDA5050 = last *reached* node, consumed by ACS for order progress)
is currently written by **three position/station "guessers"**, and two of them
corrupt it in the field:

1. **Bug #1 — manual-control pollution.** While the operator manually drives
   (`manualDrive`/`manualMove`, allowed only when no order is active), the idle
   capture in `_update_nearest_node_from_position` (`adapter_jibot.py:6320-6325`)
   fires every cycle (`_is_v3_order_active()` is false), overwriting `lastNodeId`
   with whatever map node the robot passes within reach of.

2. **Bug #2 — `lastNodeId` jumps at order accept (before motion).** For order
   `20260623-32-1-1` (charger F2_90_S2CH → F1_70), WCS equipment log
   (`wcs-nodejs/.../equipment-HN-SH6-TR-001-2026-06-23.log.gz`) shows:

   ```
   22:44:58.106  lastNodeId: F2_90_S2CH -> F2_80_S2IC   (order accept instant)
   22:44:58.107  orderId: ...19-1-1 -> ...32-1-1, nodeStates [] -> [{F1_70, seq6}]
   22:45:13.083  driving: false -> true                 (real motion 15s later)
                 ...nearestNodeId still F2_90_S2CH at accept...
   22:46:03.097  lastNodeId: F2_80_S2IC -> F1_70        (true pose arrival)
   ```

   `lastNodeId` became F2_80_S2IC at the accept instant, 15s **before** the robot
   moved, while the nearest node was still F2_90_S2CH.

Both are the same anti-pattern (infer reached-progress from position/station),
and the fix unifies them behind one selectable, testable policy so the behavior
can be A/B compared on the real robot without code edits.

## Evidence: real map distances refute a threshold cause

Node coordinates from the live `lab2m` map payload (logged `lastMapPayload`,
`uamap.core.v1`, meters → mm):

| node pair | distance (mm) |
| --- | --- |
| F2_90_S2CH ↔ F2_80_S2IC | 2674 |
| F2_90_S2CH ↔ F2_90_S2CH_BEFORE | 1369 |
| F2_90_S2CH_BEFORE ↔ F2_80_S2IC | 1317 |
| F2_80_S2IC ↔ F1_70 | 1876 |
| F1_70 ↔ F1_60 | 1000 (closest pair on the map) |

All nodes are ≥1000 mm apart. A robot parked at the charger (F2_90_S2CH) is
**2674 mm** from F2_80_S2IC — far outside both the idle threshold
(`idle_last_node_reach_xy`, 100 deployed / 500 dataclass default) and the arrival
reach zone (`default_node_deviation_xy 10 × reach_zone_scale 20 = 200` mm). So neither
distance-based writer (idle capture, pose bootstrap) could have produced the
accept-time jump. (An earlier draft blamed a 183 mm S2CH↔S2IC spacing; that was
a **test-fixture** coordinate, not the real map — withdrawn.)

## Root causes

- **Bug #1:** idle capture (`adapter_jibot.py:6320`) has no "is the robot
  actually at rest / not under manual control" gate. During a manual jog the
  robot transits map-node reach zones and `lastNodeId` churns.

- **Bug #2:** `_bootstrap_v3_order_from_vehicle_station`
  (`adapter_jibot.py:2517`) seeds `lastNodeId` from `self._vehicle._station`
  matched against the order's node ids, **with no pose check**. By elimination
  (distance-based paths ruled out above), the robot reported `_station =
  F2_80_S2IC` (an order node at seq 4) while physically at the charger; bootstrap
  trusted it, set `lastNodeId = F2_80_S2IC`, and pruned seq ≤ 4 — exactly the
  observed `nodeStates = [{F1_70, seq6}]`. `jibot-arrival-and-last-node.md`
  already documents that `_station` can be set before the robot reaches the
  coordinates; bootstrap is the last place still trusting it blindly.

  > Final 1% confirmation (station vs. an unknown alt path) is the robot-side
  > console line `[ORDER BOOTSTRAP station=... ]` for this order. The fix (pose
  > guard) is safe for either, so it does not block implementation.

## Design

### Config (`adaptor/config/config.py`, `Settings`)

- New: `last_node_capture_mode: str = "settled"` — one of `proximity` |
  `disabled` | `settled`. Read every use (live-swappable via config + restart).
  Unknown value falls back to `settled` with a one-time warning. Add the key to
  `adaptor/config/config.toml` (default `settled`) so deployed robots pick it up.
- **Threshold is decoupled from this change.** `idle_last_node_reach_xy` is
  already set explicitly in the deployed `config.toml:67` to **100.0** (the
  dataclass default is the stale 500.0 and is overridden, so editing the
  dataclass default has no deployment effect). This threshold is **orthogonal to
  both bugs** — Bug #2 is station trust, not distance, and Bug #1's fix is the
  rest/manual gate, not the radius. `proximity` must reproduce *current* behavior,
  so it uses whatever `idle_last_node_reach_xy` is configured (100 today); do
  **not** bundle a radius change into the mode work. If a different radius (e.g.
  the earlier-discussed 200) is wanted, change `config.toml:67` as a separate,
  independent edit. Do not rename the key (deployed configs reference it).

### Mode semantics

One knob governs both seams:

| mode | idle capture | accept bootstrap | Bug#1 | Bug#2 |
| --- | --- | --- | --- | --- |
| `proximity` | nearest within threshold (current) | station trusted + pose, threshold (current) | ✗ | ✗ |
| `disabled` | off | both bootstraps off (carried-over remap+prune kept; then worker arrival only) | ✓ | ✓ |
| `settled` (default) | only when robot stopped AND manual inactive AND within threshold | station only when pose agrees; pose bootstrap unchanged | ✓ | ✓ |

`proximity` deliberately preserves today's behavior so the robot operator can
reproduce both bugs for comparison.

**What `disabled` does NOT do (clarified after review).** `disabled` turns off
the two accept-time *guessers* (station + pose bootstrap) and idle capture. It
does **not** reset `lastNodeId` on accept. The accept core still runs
`state.last_node_id = self._last_node_id` → `_remap_last_node_sequence_for_new_order`
→ `_prune_v3_order_state_before_last_node` (the carried-over reached node is
remapped into the new order's sequence and its prefix pruned). That is order
state-machine machinery, not position guessing, so it stays. In `disabled` the
only writers of `_last_node_id` are this carried-over value, the new-order reset
in `_remap_*`, and the worker on confirmed arrival — so the carried-over value is
itself always a worker-confirmed reach (idle/station capture are off), making the
remap+prune sound. A harder "wipe to '' on every accept" reset is intentionally
NOT chosen: it would discard a legitimately-reached node on a mid-route re-issue.

### Seam 1 — idle capture (each variant is its own function)

Replace the inline block at `adapter_jibot.py:6320-6325` with a single call
`self._capture_idle_last_node(node_id, sequence_id, distance)` that dispatches on
the mode to three independently testable methods:

- `_capture_idle_proximity(node_id, seq, distance)` — current logic:
  `if not self._is_v3_order_active() and distance <= self._idle_last_node_reach_xy(): set lastNodeId`.
- `_capture_idle_disabled(...)` — no-op (explicit, for symmetry/testing).
- `_capture_idle_settled(node_id, seq, distance)` — proximity guard **plus**
  `self._is_jibot_stopped()` and `not self._manual_control_active`.

Writing helper (shared): `_set_last_node(node_id, seq, *, no_regress=False)`
updates `_last_node_id`, `_last_node_sequence_id`, and the mirror on `self.state`.
When `no_regress=True`, it refuses to move to a `seq` lower than the current
`_last_node_sequence_id` (returns without writing). Accept-time bootstraps call it
with `no_regress=True` (issue 3); idle capture calls it with `no_regress=False`
(an idle/free-roam robot may legitimately sit at an earlier node). Refactor the
existing inline writes to use it; default `no_regress=False` preserves current
behavior.

### Seam 2 — accept-time bootstrap

In `_accept_v3_order_for_queue` the two bootstrap calls (station then pose,
followed by `_prune_v3_order_state_before_last_node`) become mode-aware:

- `disabled`: skip both bootstraps. The carried-over remap+prune still runs (see
  "What `disabled` does NOT do" above); `lastNodeId` then advances only on
  worker arrival.
- `proximity`: unchanged — current behavior, **including its latent gaps**
  (station trusted blindly, no start-node guard, no regression guard). This is
  intentional so the operator can reproduce Bug #2.
- `settled`: `_bootstrap_v3_order_from_vehicle_station` gains three guards, all of
  which `_bootstrap_v3_order_from_current_pose` either already has or now also
  gets via the shared helper:
  1. **Pose guard (Bug #2 core):** seed from `_station` only when the robot's
     current pose is within `_idle_last_node_reach_xy()` of that station node
     (`_distance_to_node_id(station, agv_position)`). Station name alone is never
     trusted.
  2. **Start-node guard (issue 2):** skip when the matched node's
     `sequence_id == min(sequence_id over order nodes)`, mirroring pose
     bootstrap's existing guard (`don't bootstrap-prune the start node`). Prevents
     a one-node order, or first-node actions, from being pruned away at accept.
  3. **Regression guard (issue 3):** write via `_set_last_node(..., no_regress=True)`
     so a station/pose match at a lower sequence than the current
     `_last_node_sequence_id` cannot move `lastNodeId` backward.

Implement the gating inside the two bootstrap methods (early-return on `disabled`;
the three guards added under `settled`; `proximity` keeps the current path). Each
branch is covered by a unit test.

### Manual-active flag (failure-path-safe, issue 4)

Add `self._manual_control_active: bool = False`. Used only by
`_capture_idle_settled`. Lifecycle is designed so **no failure path leaves it
stuck True** (manualDrive's `_run` currently has no try/except, and
`_run_on_adapter_loop` scheduling can itself fail). Rule: set the flag **inside
`_run`**, never in the synchronous handler — so a rejected command (validation),
a scheduling failure, or a never-run `_run` can never set it.

- **`manualDrive`** (continuous jog; "active" outlives `_run`): in `_run`, wrap
  `await self._vehicle.um_drive(...)` in try/except. Set the flag `True` **only
  after `um_drive` returns successfully**, then arm the watchdog. On exception,
  leave it `False` (and surface the failure). Because the jog persists after
  `_run`, the flag is cleared by the lifecycle enders below, not by `_run`.
- **`manualMove`** (bounded; already has try/except): set `True` at the start of
  the `try`, clear in a `finally`, so success, `move_distance` failure, and
  cancellation all clear it.
- **Enders that must clear `True → False`:** `_handle_manual_stop_instant_action`
  (`manualStop`) and `_manual_drive_timeout` (deadman auto-stop). Both already run
  on the adapter loop and call `um_stop`; clear the flag there.

(The `_is_jibot_stopped()` gate alone already suppresses capture during steady
jogging, since the robot is moving then; the flag additionally covers brief
between-command jog pauses where the robot momentarily reports stopped.)

## Non-goals

- No change to the worker arrival path (`_finalize_v3_node_step`,
  `_wait_until_node_position_reached`), prune, rebuild, or the `NEAREST_NODE` /
  `lastNodeGap` telemetry.
- No cross-restart persistence.
- **Separate finding, out of scope:** WCS `orderCompareMessage` shows
  `receivedOrderId` empty for the whole order — WCS never sees the AGV echo the
  orderId. Tracked separately; not part of this change.

## Testing (TDD, all offline: fake vehicle / in-process simulator)

Each test sets `config.settings.last_node_capture_mode` explicitly.

Seam 1 (idle capture):
- `proximity`: no active order, robot within threshold of map node → `lastNodeId`
  captured (current behavior preserved).
- `disabled`: same setup → `lastNodeId` unchanged.
- `settled` stopped + manual inactive + within threshold → captured.
- `settled` while `_manual_control_active = True` → NOT captured (Bug #1).
- `settled` while moving (`_is_jibot_stopped()` false) → NOT captured (Bug #1).

Seam 2 (bootstrap), reproducing Bug #2 geometry (station node 2674 mm from pose):
- `proximity`: `_station` = order node, pose far away → `lastNodeId` seeded from
  station (current buggy behavior reproduced).
- `settled` pose guard (Bug #2 fixed): same setup → station bootstrap **rejected**
  (pose disagrees), `lastNodeId` not advanced to the unreached node.
- `settled` legit resume: `_station` = order node AND pose within reach → seeded.
- `settled` start-node guard (issue 2): `_station` = the order's start node, pose
  agrees → NOT seeded/pruned; a one-node order keeps its single node.
- `settled` regression guard (issue 3): station/pose match at a sequence lower
  than current `_last_node_sequence_id` → `lastNodeId` does not move backward.
- `disabled` (issue 1): neither bootstrap runs, but the carried-over
  remap+prune still applies — assert a carried `_last_node_id` that is an order
  node is remapped and its prefix pruned (documents the clarified semantics).

`_set_last_node` helper:
- `no_regress=True` refuses a lower sequence; `no_regress=False` (default) allows
  it (idle path).

Manual flag lifecycle (issue 4):
- `manualMove` clears `_manual_control_active` in `finally` on both success and
  `move_distance` failure.
- `manualDrive` leaves the flag `False` when `um_drive` raises (no leak); sets it
  `True` on success.
- `manualStop` and deadman timeout (`_manual_drive_timeout`) clear the flag.

Keep passing unchanged: the existing idle-capture / active-order / arrival tests.
Default mode is now `settled`, so any existing test asserting the old proximity
behavior must explicitly set `last_node_capture_mode = "proximity"`; audit and
update those during RED.

## Rollout

Default `settled` fixes both bugs out of the box. To reproduce/triage on the
robot, set `last_node_capture_mode: proximity` (old behavior) or `disabled`
(hardest isolation) in config and restart the adapter.

## Implementation status (2026-06-24)

Most of this design landed earlier in the `wip: snapshot ...` commit (modes +
config + reader + idle-capture functions + `_set_last_node` + station **pose
guard** + manual flag), with its tests; the full suite was green on arrival.
This session reconciled the remaining review items against that code:

- **Done this session — #4 manualDrive flag leak (real bug).** The flag was set
  in the synchronous handler and `_run` had no `try/except`, so a failed
  `um_drive` (or a failed schedule) left `_manual_control_active` stuck `True`,
  which would suppress `settled` idle capture forever. Fixed: the flag is now
  raised inside `_run` only after `um_drive` succeeds, and cleared on failure
  (TDD: `test_manual_drive_does_not_leak_flag_when_um_drive_fails`).
- **Already satisfied:** Bug #1 (settled rest/manual gate), Bug #2 (settled
  station **pose guard**), #1 disabled semantics, #5 threshold (default 200 /
  deployed 100).
- **Deferred — #2 station start-node guard.** NOT implemented. It conflicts with
  the committed test `test_bootstrap_settled_accepts_station_when_pose_agrees`,
  which expects station to seed the start node on pose agreement (resume). Result:
  current `settled` station bootstrap, once the pose agrees, may seed+prune the
  start node — so a first node carrying actions could be pruned at accept. Low
  risk today (charger orders' start nodes have no actions); revisit if a
  first-node-action order appears. The pose guard already blocks the actual Bug #2
  (unreached node), so this is hardening only.
- **Declined — #3 regression guard (`no_regress`).** Not added; in `settled` the
  pose guard already prevents seeding a node the robot is not at, so backward
  jumps are not a live risk.
