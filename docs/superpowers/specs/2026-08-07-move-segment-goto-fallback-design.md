# Move Segment Goto Fallback Design

## Goal

A `mode="move"` motion rule drives a `from -> to` segment with a relative move
of a fixed `distance`. Today the node step completes only when the pose lands
inside the to-node reach zone; a move that stops short reports
`[ORDER NODE UNREACHED]` once and then polls forever, so the order neither
advances nor fails.

Make the short move a normal, expected outcome: once the relative move has
finished, if the pose is outside the to-node reach zone, drive the rest of the
way with an ordinary goto. The configured `distance` stays deliberately short —
it only needs to clear the segment's constrained part, and the goto finishes the
approach.

Observed failure this design fixes (AMR `HN-SH6-TR-002`, 2026-08-07 10:44,
order `20260807-2-1`, rule `1_01CH -> p39`, `distance = -2000`): the robot
travelled 2031 mm from `x=10650` to `x=12681`, stopping 765 mm short of p39 at
`(13446, -2505)` with a 200 mm reach zone. The order hung there and never
attempted p40, p37, or p38.

## Scope

In scope: completion detection for `mode="move"` segments, the goto fallback
when the move ends outside the reach zone, pause/resume of a relative move, and
extracting a pure goto send path.

Also in scope, because the same defect reaches it: `_wait_until_node_position_reached`
currently ignores `_motion_paused`, so a paused robot publishes a spurious
`UNREACHED` error one second into any pause. A pause guard is added there.

Out of scope, deliberately deferred: the fallback goto's own arrival wait keeps
the existing unbounded behavior, and the fallback applies to every
`mode="move"` rule with no per-rule opt-in key.

## Two Distinct Judgments

The design turns on keeping these separate. Conflating them is what makes a
distance-only rule unsafe.

| Judgment | Question | Basis |
|---|---|---|
| Move completion | Has the relative move finished? | Commanded `distance` vs. travelled displacement, plus robot stop status |
| Node arrival (`reached`) | Is the robot at the to-node? | Pose against the node's reach zone (unchanged) |

Node arrival must stay pose-based. Judging arrival by `distance` would report
p39 as reached while the robot sits 765 mm away, and the fallback would never
trigger.

## Completion Detection

New method on the JIBOT adapter:

```python
async def _wait_until_move_settled(
    self,
    node: Any,
    target: Tuple[float, float, float],
    distance: float,
    start_pose: Tuple[float, float],
) -> Tuple[bool, float]
```

Returns `(reached, travelled)`. `reached` is `True` when the pose is inside the
to-node reach zone (node reached), `False` when the move has finished outside it
(fallback needed). `travelled` is the displacement from `start_pose` at that
moment, which the caller needs to decide whether a fallback is safe at all. It
never publishes an `UNREACHED` error — falling back is normal flow.

Let `travelled = hypot(current_pose - start_pose)`,
`target_travel = abs(distance)`, and
`full_travel = travelled >= target_travel * move_complete_travel_ratio`.

Each poll evaluates, in order:

1. **Paused or pose unknown.** If `_motion_paused` is set, or the pose reads as
   `None`, skip the sample. No judgment advances and no timer accumulates.
2. **Full travel.** `full_travel` and the robot is stopped and not in an
   obstacle wait: the move ran its commanded distance. Complete.
3. **Start gate.** Until the move is seen to start, the stall branch is
   suppressed. The move counts as started at whichever comes first: the robot's
   status leaves the stopped state, or
   `travelled >= move_started_min_travel_mm` (a backstop for a move short or
   fast enough that no telemetry sample catches it in motion). If neither holds
   within `move_start_timeout_sec`, the move never started — return `reached =
   False` with the (near-zero) `travelled`, which the caller reads as "no goto
   fallback": see Fallback below.
4. **Stall.** After the start gate passes: the robot is stopped, not in an
   obstacle wait, and has been continuously stopped for at least
   `move_stall_timeout_sec`. The move ended early. Treat as complete and let the
   fallback recover.

Checking full travel **before** the start gate is deliberate. Premature firing
is only dangerous for the stall branch — a robot that has covered its commanded
distance and stopped is finished regardless of whether any sample caught it
moving. This also makes short distances work without a special case: a
`distance` of 0, or any value at or below `move_started_min_travel_mm`,
completes through branch 2 instead of waiting out `move_start_timeout_sec`.

The stall timer measures one continuous stop. Any sample where the robot is not
stopped clears the stopped-since marker, as does entering pause.

Once complete, evaluate `_pose_in_reach_zone` against the to-node target and
return the result.

### Start pose

`start_pose` is captured immediately before the move is dispatched. The vehicle
pose can read as `None`, which the existing arrival wait tolerates by skipping
the sample — but completion here is distance-based, so a missing origin makes
the judgment meaningless.

`_run_move_segment` therefore waits for a valid pose for up to
`move_start_timeout_sec` **before** dispatching. If none arrives, it fails the
step with a clear reason and sends no motion. Dispatching a relative move that
cannot be monitored is the wrong trade near a charger. Capturing the origin
after dispatch is not an option: it would discard whatever distance was already
covered and skew every later comparison.

## Pause and Resume

`_motion_paused` is currently write-only — set at `_handle_start_pause_instant_action`,
cleared at `_handle_stop_pause_instant_action`, and read nowhere. No wait loop
observes it. Without a guard, `startPause` stops the robot, the stall branch
sees a continuous stop, and `move_stall_timeout_sec` later the fallback issues a
goto while the order is paused.

Two changes:

**Freeze.** `_wait_until_move_settled` skips every sample while `_motion_paused`
is set (branch 1 above). The same branch also freezes while
`_is_jibot_manual_drive()` or `_manual_control_active` holds: manual/teleop
driving discards the running move and leaves the robot reading "stopped", so
without it the stall branch would dispatch an autonomous goto with a hand on the
joystick. `_wait_until_node_position_reached` gains the same
guard on its `UNREACHED` branch, which also removes the spurious error the
existing goto path publishes one second into any pause.

**Resume with the remaining distance.** `stop_motion()` cancels the relative
move, so resume must re-issue it or the segment never finishes. A new
`_active_move_segment` holds the node, the rule, the start pose, and the
commanded distance while the relative move is in flight.
`_handle_stop_pause_instant_action` checks it before `_active_goto_node` and
re-issues a move of

```
remaining = sign(distance) * max(0, abs(distance) - travelled)
```

When `remaining` rounds to zero the move already covered its distance; nothing
is re-issued and the wait completes through the full-travel branch.

Re-issuing the remainder rather than falling back to a goto preserves why the
rule exists: the segment is constrained (backing out of a charger, for one) and
a goto is unsuitable there. `_active_move_segment` and `_active_goto_node` are
mutually exclusive — the first covers the relative move, the second the
fallback goto — so a pause during either phase resumes the right motion.

## Fallback

`_run_move_segment` orchestrates:

1. Resolve the to-node target. Fail the step if it has no coordinates
   (unchanged).
2. Wait for a valid robot pose, then capture `start_pose`. Fail the step if none
   arrives within `move_start_timeout_sec`.
3. Set `_active_move_segment`, dispatch `move_distance(...)` (unchanged).
4. `await _wait_until_move_settled(...)`, clearing `_active_move_segment` when it
   returns. `reached = True` completes the node step exactly as today.
5. On `reached = False` with `travelled < move_started_min_travel_mm`, the move
   never left its origin. Log `[ORDER NODE MOVE NOT STARTED]` naming the
   travelled distance and the threshold, and fail the node step with that as its
   rejection reason. **No goto is sent**: the robot is still inside the
   constrained part of the segment — the charger bay the rule exists to escape —
   and a goto from there is the route the planner cannot solve. The threshold is
   `move_started_min_travel_mm`, reused rather than duplicated: it already means
   "has this move demonstrably begun".
6. On `reached = False` with `travelled >= move_started_min_travel_mm`, log
   `[ORDER NODE MOVE SHORT]` with travelled distance, commanded distance,
   current pose, and target, then drive the remainder with a goto.

### Pure goto extraction

The fallback must send an *ordinary* goto, and `_send_node_motion` is not that
function: it branches into `should_charge_in_place` and, for `[dock].nodes`
members, a bare `UmDock`. Neither is hypothetical here —
`should_charge_in_place` keys off `_is_dock_motion_node`, which is
**non-directional** and true for any `mode="dock"` rule's `to`, and the
`MotionRule` docstring at `adaptor/config/config.py:385` documents exactly the
combination that collides: `{ from = "F1_60", to = "F2_90_S2CH", mode = "move" }`,
a move rule whose `to` is a charger. A fallback routed through
`_send_node_motion` could dock or start in-place charging instead of driving to
the node.

Extract the goto body of `_send_node_motion` — the simulator `nodePosition`
branch, the `get_path_point_pose` branch, the `goto_point` branch, and the
closing `_await_goto_ack` — into `_send_node_goto(node) -> Optional[str]`.
`_send_node_motion` calls it after its charge and dock branches, so every
existing caller keeps its current behavior. The fallback calls
`_send_node_goto` directly and gets a goto and nothing else. A rejection reason
propagates as the step's failure reason, matching the plain goto path.

`_ensure_not_charging_before_order_motion` stays in `_send_node_motion` and is
not repeated in the fallback: the move segment already ran it at dispatch, and
charging cannot begin mid-segment.

During the fallback wait, set `_active_goto_node` as the plain goto path does,
then `await _wait_until_node_position_reached(node, target)`, whose unbounded
behavior is unchanged apart from the pause guard.

## Configuration

Four keys added to `[settings]`, with defaults in `Settings`:

| Key | Default | Meaning |
|---|---|---|
| `move_start_timeout_sec` | 10.0 | Move never started, or no pose before dispatch; fall back / fail |
| `move_started_min_travel_mm` | 50.0 | Displacement confirming the move started |
| `move_complete_travel_ratio` | 0.9 | Fraction of `distance` counting as full travel |
| `move_stall_timeout_sec` | 5.0 | Stop duration confirming an early stop |

Existing keys are reused where they already fit: `node_position_poll_interval_sec`
for the poll cadence and `reach_zone_shape` / `reach_zone_scale` /
`default_node_deviation_xy` for the zone, via `_effective_reach_deviation_xy`.

## Behavior on the Observed Failure

Commanded 2000 mm, travelled 2031 mm, ratio 1.02 >= 0.9, robot stopped: full
travel, complete. Pose `(12681, -2473)` against target `(13446, -2505)` with a
200 mm zone is outside, so the fallback issues a goto to p39. On arrival the
node step finishes and the order proceeds to p40, p37, p38.

## Testing

Unit tests against a fake vehicle, following the existing
`adaptor/tests/test_adapter_jibot_v3_order.py` patterns.

Completion detection:

- Full travel outside the zone returns `False` (the 2026-08-07 case).
- Full travel inside the zone returns `True`; no goto is sent.
- A robot stopped but not yet moving does not complete before
  `move_start_timeout_sec`; regression test for the premature firing observed
  on 2026-08-05 23:50:55 and 2026-08-06 15:09:51.
- No motion within `move_start_timeout_sec` returns `False`.
- Displacement past `move_started_min_travel_mm` satisfies the start gate even
  when the status never samples as moving.
- A stop with travel below the ratio does not complete before
  `move_stall_timeout_sec`, and does complete after it.
- Motion resuming mid-stall clears the stopped-since marker, so the stall timer
  restarts rather than accumulating across separate stops.
- An obstacle wait blocks completion in the stall branch and does not advance
  the stall timer.
- `distance = 0` completes on the first stopped sample through the full-travel
  branch, without waiting out `move_start_timeout_sec`.
- A `distance` at or below `move_started_min_travel_mm` completes through the
  same branch.

Pose availability:

- `_x` or `_y` reading `None` at dispatch fails the step within
  `move_start_timeout_sec` and sends no `move_distance`.
- A pose arriving during that window proceeds normally with the first valid
  reading as the origin.
- A pose dropping to `None` mid-wait skips those samples without advancing the
  stall timer or the start-gate deadline.

Pause:

- `startPause` during the relative move issues no goto, even after
  `move_stall_timeout_sec` and `move_start_timeout_sec` have both elapsed.
- JIBOT reporting a manual drive mode, and `_manual_control_active` for an
  adapter-dispatched jog, each block settling under the same two elapsed
  timeouts.
- `stopPause` re-issues a move for the remaining distance with the original
  sign, and the segment then completes normally.
- `stopPause` with the full distance already covered re-issues nothing and
  completes through the full-travel branch.
- `startPause` during a plain goto node publishes no `UNREACHED` error.

Fallback:

- A `reached = False` result with travel past `move_started_min_travel_mm` sends
  a goto to the to-node and finishes the step on arrival.
- A `reached = False` result with no travel fails the node step and sends no
  goto at all.
- A rejected fallback goto fails the step with the rejection reason.
- A move rule whose `to` is a charger falls back to a goto, not `UmDock` or
  in-place charging — the `_send_node_goto` extraction guard.
- The node step is not marked finished, and no `UNREACHED` error is published,
  while the fallback is in flight.
