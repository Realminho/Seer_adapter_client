# lastNodeId VDA5050 Compliance + Nearest-Node Split Design

> Baseline: branch `jibot-client-refactor`, `adaptor/adapter_jibot.py` at
> 4353 lines (post-legacy-removal working tree). All line refs below are against
> that file unless noted.

## Purpose

The state-publish loop overwrites `lastNodeId` every cycle with the globally
nearest map node to the robot's `(x, y)` (`_update_last_node_from_position`
called from `publish_state` at `adapter_jibot.py:467`). That breaks the VDA5050
meaning of `lastNodeId` and corrupts order-progress tracking.

This change splits the two concerns:

1. **`lastNodeId` (VDA5050 standard)** — the last *reached* order node, advanced
   only on arrival, monotonic within an order, with explicit lifecycle seeding.
2. **nearest node (basic "where is the robot roughly" telemetry)** — the
   globally nearest map node, recomputed every cycle, in a separate variable
   surfaced as a `NEAREST_NODE` information entry. Never drives `lastNodeId`
   during an active order.

This is now primarily a **publish-loop writer split**; the old pre-v3 order
executor that also wrote `lastNodeId` is already gone from the live file (see
"Legacy status").

## Background: why the current behavior is wrong

In VDA5050, `lastNodeId` / `lastNodeSequenceId` mean "the last node the AGV
*reached*, or the node it is currently on; empty if none reached." The target
ACS consumes this pair for **order-progress tracking** (confirmed with the
integrator — see Migration note). Within one order it is normally monotonic.

The per-cycle nearest-node overwrite (`adapter_jibot.py:467`) violates this:

- **Position overwrites progress.** The order worker advances `_last_node_id` on
  arrival (`:2552`), then the next publish cycle overwrites it with the nearest
  node — which, mid-route, can be a much-later node (ACS reads the order as
  nearly done) or an earlier node (ACS reads a regression).
- **Contradicts existing monotonic assumptions.**
  `_bootstrap_v3_order_from_vehicle_station` (`:1744`) deliberately refuses to
  move backward in sequence, and `_prune_v3_order_state_before_last_node` uses
  `lastNodeId`'s sequence to drop passed nodes. The overwrite breaks both.
- **Breaks order-update continuity.** An order update must start at the current
  `lastNodeId`; that check is only meaningful when `lastNodeId` is the reached
  node, not the nearest one.

## Final state: two variables

### 1. `lastNodeId` — VDA5050 standard

Fields: `_last_node_id` (str) + `_last_node_sequence_id` (int) — unchanged.

There is exactly one **advancement** writer plus a small set of **lifecycle**
writers. No per-cycle position writer.

**Advancement writer (single): the v3 order worker, on arrival** —
`_process_v3_order_queue` step handler (`:2552`-`:2553`):

- Coordinate-ful node: advanced only after `_wait_until_node_position_reached`
  confirms the robot entered the reach zone (`:2548`).
- Dock node: advanced after `_wait_until_docking_complete` (`:2522`).
- Coordinate-less node (no `nodePosition`, not on the robot map): arrival cannot
  be verified, so the adapter treats successful dispatch + order-step completion
  as best-effort arrival, logs `[ORDER NODE WARN] ... no coordinates known`
  (`:2538`-`:2544`), and advances. This is the **only** path that advances
  `lastNodeId` without a verified reach; the limitation is logged, not silent.

**Lifecycle writers (allowed; they seed/reset, they do not "advance"):**

- **Reset** to `""` / `0` on accepting a new order (`_accept_v3_order_for_queue`,
  `:1672`-`:1673`).
- **Station bootstrap** on a new order from the robot's reported `_station`
  (`_bootstrap_v3_order_from_vehicle_station`, `:1744`), keeping its existing
  no-regression guard.
- **Idle/startup bootstrap (new).** When there is **no active order**
  (`state.order_id == ""`) and `_last_node_id == ""`, seed `lastNodeId` from the
  current nearest node so a freshly-booted/parked robot reports the node it sits
  on. This replaces the *idle* portion of the old per-cycle behavior (and keeps
  the simulator initial-pose tests meaningful) without ever touching `lastNodeId`
  during an active order.

**Monotonic within an order.** `lastNodeSequenceId` does not move backward while
an order is active — guaranteed by sequential queue consumption plus the
bootstrap no-regression guard.

**Key removal.** The per-cycle call at `:467` no longer writes `lastNodeId`; it
is repurposed to update the nearest-node variable (and to perform the idle
bootstrap above).

### 2. nearest node — basic feature, separated out

New fields on `Adapter`:

- `_nearest_node_id: str`
- `_nearest_node_sequence_id: int` — order sequenceId if the node is in the
  active order, else `0` (same fallback `_find_nearest_node` already uses).
- `_nearest_node_distance: Optional[float]` — distance to that node in raw pose
  units (mm), matching the existing gap semantics.

Update rules:

- Recomputed every state-publish cycle from `(x, y)` using the existing
  `_find_nearest_node` logic (`:4114`) unchanged — global nearest over all map
  nodes, order nodePosition fallback. No reach threshold, no monotonicity.
- **No-match branch (new code, not the current early-return).** Today
  `_update_last_node_from_position` simply `return`s when `_find_nearest_node`
  yields nothing (`:4213`), which after the split would leave stale
  `_nearest_node_*` and a stale `NEAREST_NODE` info entry. The renamed method
  must instead, on no-match, reset the fields (`_nearest_node_id = ""`,
  `_nearest_node_sequence_id = 0`, `_nearest_node_distance = None`) **and remove**
  the `NEAREST_NODE` information entry. This is an explicit new branch — the
  rename is *not* a pure body-preserving rename.

Method rename:

- `_update_last_node_from_position` (`:4197`) → `_update_nearest_node_from_position`.
  It writes the `_nearest_node_*` fields (never `_last_node_*` during an active
  order), performs the idle bootstrap described in §1, and the no-match branch
  above. The `publish_state` call site at `:467` is updated to match.

### Surfacing the nearest node in state

- `_refresh_last_node_gap_information` (`:4162`) is renamed and re-keyed:
  - `info_type` `LAST_NODE_GAP` (`:4184`) → `NEAREST_NODE`.
  - reference key `lastNodeId` (`:4187`) → `nearestNodeId`.
  - `gap` reference unchanged (raw mm; unit advertised in factsheet
    `coordinateUnits.position`).
- `lastNodeId` / `lastNodeSequenceId` in the published state now reflect true
  reached-node progress.

> Note: the nearest node is exposed via `state.information` (`NEAREST_NODE`), not
> a new top-level VDA5050 field, because VDA5050 has no standard field for it and
> a non-standard top-level key risks confusing a standard FMS. This is the single
> place to change if a dedicated channel is later needed.

### Migration note (consumer impact)

Two distinct contracts change differently:

**Control contract — corrected, not broken.** The target ACS consumes
`lastNodeId` / `lastNodeSequenceId` for order-progress tracking (which node the
robot reached), confirmed with the integrator. The per-cycle nearest overwrite
was feeding it the *wrong* value (nearest ≠ reached). After this change
`lastNodeId` carries true reached-node progress — a **data-correctness fix**.
Per the VDA5050 schema, `information` *"must not be used for logic in fleet
control"* (`adaptor/protocol/vda5050_v3/json_schemas/state.schema:212`), and no
control consumer reads `NEAREST_NODE`, so placing the nearest node there is safe.

**Telemetry contract — rename migration required.** Visualization/debug consumers
that today read the `LAST_NODE_GAP` info entry, or its `lastNodeId` reference
key, **will break**: the entry becomes `NEAREST_NODE` and the key becomes
`nearestNodeId`. This is a telemetry-only breaking change and must be called out
in the rollout so any dashboard reading the old key is updated. (If a future need
arises to drive control from the nearest node, it must NOT use `information`; it
would need a dedicated control-safe field/topic.)

## Legacy status (no live removal needed)

The pre-v3 order path (`order_accept_procedure`, `order_accept`,
`order_update_accept`, `_schedule_execute_path` / `_execute_path`,
`_wait_until_reached`, and their private helpers) — which contained a second,
buggy `lastNodeId` writer using `get_sequence_by_node()` (`Optional[int]`) — is
**already absent** from `adaptor/adapter_jibot.py` in the current working tree.
The live order path is `handle_incoming_acs_cmd → _handle_v3_order →
_process_v3_order_queue`. This design therefore removes **no** live code for
legacy cleanup.

The only remaining artifact is the legacy backup file
`adaptor/adapter_jibot_.py` (note the trailing underscore). It is **git-tracked**
but **imported nowhere**. Removing it (`git rm adaptor/adapter_jibot_.py`) would
be harmless housekeeping but is out of scope for the lastNodeId logic and not
required for this change. It has been left in place by decision.

## Testing

All tests stay offline (fake vehicle / in-process simulator; no MQTT broker or
real robot).

**Rewrite existing position-based tests** (`adaptor/tests/test_adapter_jibot_v3_order.py`)
so the old nearest behavior is asserted on the *nearest* variable, and
`lastNodeId` is asserted to stay reached-node:

- `test_active_order_last_node_follows_nearest_xy_position` → assert
  `_nearest_node_id` follows nearest xy; assert `lastNodeId` is NOT dragged.
- `test_last_node_can_move_to_lower_sequence_when_xy_is_nearest` → assert the
  nearest variable may take a lower sequence; `lastNodeId` stays put.
- `test_last_node_uses_nearest_map_node_outside_active_order` → assert
  `_nearest_node_id` picks the map-only node; `lastNodeId` unaffected.
- `test_active_order_state_loop_updates_last_node_from_nearest_position`
  (`:1689`) → currently asserts the publish loop pulls `state.last_node_id` to
  the nearest node during an active order. Invert it: during an active order the
  publish loop must **not** overwrite `state.last_node_id`; `_nearest_node_id`
  tracks the nearest instead.

**Update simulator initial-pose tests** (`adaptor/tests/test_initial_pose_and_map.py`),
which call the renamed method directly and assert `_last_node_id`:

- `test_last_node_id_resolved_from_loaded_map_and_pose` (`:216`) and
  `test_last_node_id_starts_on_random_loaded_map_node` (`:244`) → with no active
  order these exercise the **idle bootstrap**: after one
  `_update_nearest_node_from_position` call, `_last_node_id` is seeded from the
  nearest node (still `== vehicle._station`), and `_nearest_node_id` equals it.
  Keep the `_last_node_id` assertions (idle bootstrap path) and add a
  `_nearest_node_id` assertion.

**Add new tests:**

- **Regression guard (key):** during an active order, repeated publish cycles do
  not overwrite `state.last_node_id` away from the reached node, even when the
  robot's position is nearest a later node.
- `lastNodeId` advances only on confirmed reach-zone arrival.
- A coordinate-less node advances `lastNodeId` on step completion (best-effort)
  and emits `[ORDER NODE WARN]`.
- `lastNodeId` does not regress to a lower sequence within an active order.
- Idle bootstrap: a parked robot with no active order reports the nearest node as
  `lastNodeId`; once an order is active, position no longer moves `lastNodeId`.
- `NEAREST_NODE` info is published with `nearestNodeId` + `gap` each cycle, and is
  **removed** (with fields reset) when no node can be matched.

**Must keep passing unchanged** (reach-path regression guard):
`test_order_waits_for_arrival_and_executes_actions`,
`test_order_worker_completes_steps_with_simulator_vehicle`, and the other
arrival-based assertions that already expect `lastNodeId == "Nx"` after the fake
vehicle arrives.

## Out of scope

- Edge/path-projection localization (projecting `(x, y)` onto order edges to
  derive the last passed node). Deferred; can be added later behind the same
  advancement writer if mid-edge precision is needed.
- Deleting the stray `adaptor/adapter_jibot_.py` — optional housekeeping, not
  required by this design.
- Any change to how the robot reports `_station` or `(x, y)`.
