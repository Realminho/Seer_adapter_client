# lastNodeId Idle / Non-Active Pass-Through Capture Design

> Baseline: branch `jibot-client-refactor`, `adaptor/adapter_jibot.py`
> (~5800 lines, current working tree). Line refs are against that file.
>
> Evolves `docs/superpowers/specs/2026-06-20-last-node-id-vda5050-design.md`
> (the two-variable split: `lastNodeId` = VDA5050 reached node vs `_nearest_node_*`
> = telemetry). Read it first.
>
> **Scope note:** an earlier draft of this doc proposed capturing `lastNodeId`
> during active orders too (forward-only). That was **withdrawn after review** as
> unsafe — see "Why not in-order capture (withdrawn)". The design below is the
> narrowed, safe version: capture only when **no order is active**.

## Purpose

`lastNodeId` is advanced by two paths today:

1. **Order arrival** — the v3 order worker, `_finalize_v3_node_step`
   (`adapter_jibot.py:3414-3415`).
2. **Idle capture** — the publish loop, in `_update_nearest_node_from_position`
   (`adapter_jibot.py:5826-5832`): when there is **no order** and the robot is
   within `idle_last_node_reach_xy` of the nearest map node, that node becomes
   `lastNodeId`.

The gap is in path 2's gate. It uses bare `state.order_id`, which is **not
cleared on order completion** (`_is_v3_order_finished()` becomes true while
`order_id` stays set). So once any order completes, idle capture stays **disabled
indefinitely**: the robot can free-roam or reposition (JIBOT `UmGoto` is
fire-and-forget; see `jibot-umgoto-fire-and-forget-no-ack`) or park, and
`lastNodeId` stays frozen at the last order node, going stale. This is the most
likely cause of the observed "between nodes, `lastNodeId` is empty/stale during
operation" symptom. (A cold boot with no order can also keep it `""` until the
robot first drives within reach — unavoidable without persistence, which is out
of scope.)

## Change (single, minimal)

Gate idle capture on a **finished-aware** active check instead of bare `order_id`:

1. Add `_is_v3_order_active()`:
   `bool(state.order_id) and not self._is_v3_order_finished()` — mirrors the
   inline guard already at `adapter_jibot.py:4950`.
2. In `_update_nearest_node_from_position`, change the capture gate
   (`adapter_jibot.py:5827`) from
   `if not order_id and distance <= self._idle_last_node_reach_xy():`
   to
   `if not self._is_v3_order_active() and distance <= self._idle_last_node_reach_xy():`.

Everything else in the capture block is unchanged: pure proximity (the in-reach
nearest node), sticky retention (`_last_node_id` is never reset to `""` at
runtime), sequence id straight from `_find_nearest_node`.

**Result:** capture now runs in idle, free-roam, **and post-completion** (order
finished, `order_id` retained); it still **never** runs during an active order.

## Why not in-order capture (withdrawn)

The earlier draft let capture advance `lastNodeId` during active orders
(forward-only, with an empty-seed branch). Review found three High issues, all
rooted in adding a second `lastNodeId` writer to the order state machine:

- **Regression.** `_finalize_v3_node_step` (`:3414-3415`) overwrites
  `_last_node_sequence_id` **unconditionally** (no forward guard). Capture jumping
  to seq 4, then the worker finalizing seq 2, regresses 4 → 2. So the spec claim
  "both are forward-only and coexist" was false.
- **State/queue desync.** `_prune_v3_order_state_before_last_node` (`:2481`) and
  `_rebuild_v3_order_queue_from_state` (`:2559`) run **only** at order
  accept/update (`:2331-2333`, `:2367-2368`), never in the publish loop. Capture
  advancing `lastNodeId` mid-order would leave `node_states`/the worker queue
  unpruned — inconsistent published state, and the worker could re-drive a node
  the robot already passed.
- **Map-only seed.** `_find_nearest_node` (`:5704`) includes every map node as a
  candidate, returning sequence 0 for non-order nodes. An empty-seed during an
  order could latch a non-order parking/side node as `lastNodeId`.

Making in-order capture safe would require routing all last-node writes through a
shared monotonic writer **and** cancelling/rebuilding the running order worker
from the publish loop on positional drift (what accept/update do) — too invasive
and concurrency-racy for the benefit. During an active order the worker is the
single authority on progress; this design keeps it that way. If in-order
pass-through is later shown to be a real gap (e.g. the worker's arrival reach zone
is tighter than the capture threshold so it misses a node), fix it **inside the
worker's arrival detection**, not with a second writer.

## ACS contract

Preserved (`acs-consumes-lastnodeid-for-progress`): during an active order
`lastNodeId` is only ever a node the worker confirmed reached. Idle /
post-completion capture sets it to a node the robot is within reach of (i.e.
effectively on) — a valid VDA5050 "last reached node", and not consumed as
in-order progress because no order is active.

## Non-goals

- No cross-restart persistence (cold boot keeps `""` until first in-reach capture;
  recovery by physical movement / injection).
- No change to `_finalize_v3_node_step`, prune, rebuild, the arrival writer,
  `_nearest_node_*`, or the `NEAREST_NODE` information entry.
- No forward-guard / empty-seed machinery — unnecessary when capture is gated to
  no-active-order (idle candidates all carry sequence 0; there is no in-order
  progress to protect or seed).

## Config

`idle_last_node_reach_xy` (default `500.0` mm, `config/config.py:74`): key and
value unchanged. Update its comment (`config.py:68-73`): it now governs idle,
free-roam, **and post-completion** capture; it is still **not** used for
active-order node completion (the worker owns that). Do not rename the key —
deployed robot configs reference it.

## Testing

All tests stay offline (fake vehicle / in-process simulator).

**Keep passing unchanged** — these assert "position does not move `lastNodeId`
during an active order"; they still hold because the gate is still off during an
active order (now via the finished-aware check):
`test_update_nearest_does_not_touch_last_node_during_active_order`,
`test_active_order_last_node_follows_nearest_xy_position`,
`test_active_order_state_loop_updates_last_node_from_nearest_position`,
`test_publish_loop_does_not_overwrite_last_node_during_active_order`,
`test_last_node_can_move_to_lower_sequence_when_xy_is_nearest`, the idle-capture
tests (`test_update_nearest_idle_*`), and
`test_last_node_uses_nearest_map_node_outside_active_order`.

**New (TDD driver):** finished order — `order_id` set but `node_states` and
`edge_states` empty (`_is_v3_order_finished()` true) — robot within reach of a
map node → `lastNodeId` updates to it. Fails on the current bare-`order_id` gate
(capture stays disabled), passes after the change.

**New (guard for the withdrawn map-only-seed concern):** active order
(`node_states` non-empty) + `lastNodeId == ""` + robot within reach of a
map-only (non-order) node → `lastNodeId` stays `""` (capture does not fire during
an active order; no map-only node is latched).
