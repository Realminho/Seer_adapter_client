# JIBOT Loading/Unloading Busy State — Design

**Date:** 2026-06-21
**Status:** Approved design (review feedback incorporated), pending implementation plan

## Goal

While JIBOT performs loading/unloading work at a dock, it must appear **BUSY**
to the FMS/ACS as a RUNNING VDA5050 action, refuse to move, and reject incoming
orders — until an explicit completion signal is injected.

Unlike MOMA (which self-detects loading/unloading completion), JIBOT cannot
detect completion on its own, so completion is **injected** from the outside via
paired `stop*` instant actions.

## Background — current state of the code

Investigated in `adaptor/adapter_jibot.py` and `adaptor/protocol/`:

- **No DOCKING / loading / unloading concept exists.** `OperatingMode` only has
  the VDA5050 standard values (AUTOMATIC, TEACHIN, …). Docking is tracked
  internally with `_docking_started_node_ids` only.
- **Orders are accepted regardless of robot state.** `_handle_v3_order`
  (~line 1617) rejects only: state not initialized, zero nodes, or a *different*
  `orderId` while the current order is unfinished (`ORDER_CURRENT_NOT_FINISHED`).
  There is no rejection based on docking / charging / pause.
- **Closest existing pattern: `startPause`/`stopPause`** — sets `_motion_paused`
  + `state.paused` and flips RUNNING↔PAUSED action states. It does **not** block
  order reception.
- **Instant action states are published.** `instant_actions_accept_procedure`
  (~line 2623) appends each incoming instant action to
  `state.instant_action_states` as `WAITING`; `_update_instant_action_status`
  (~line 3945) mutates status and republishes. A non-terminal status (RUNNING)
  **persists** in the published state; a terminal status (FINISHED/FAILED) is
  cleared by `_clear_terminal_instant_action_states`. This is exactly the hook
  needed to expose a long-lived RUNNING work action.

## Design decisions (confirmed with user)

1. **Representation:** the `loading`/`unloading` instant action itself is held in
   **RUNNING** status (not auto-finished). No separate synthetic action is
   needed — the work action *is* the published RUNNING action.
2. **Order handling while BUSY:** reject with an error response (`order_reject`).
3. **Window control:** explicit start/stop signals, not auto-detection.
4. **Vocabulary:** distinguish loading vs unloading (fleet consistency with MOMA;
   accurate FMS reporting). The `dock`/`UmDock` term is reserved for the physical
   docking maneuver and is intentionally *not* reused here.
5. **Completion injection:** paired `stopLoading` / `stopUnloading` instant
   actions.

## Action vocabulary (new VDA5050 instant actions)

| actionType     | meaning                                  |
|----------------|------------------------------------------|
| `loading`      | begin loading work → enter BUSY(loading) |
| `unloading`    | begin unloading work → enter BUSY(unloading) |
| `stopLoading`  | completion injection for loading → exit BUSY |
| `stopUnloading`| completion injection for unloading → exit BUSY |

All four are delivered as instant actions and dispatched in the
`instant_actions_accept_procedure` elif chain (~line 2652). All four are added to
`SUPPORTED_INSTANT_ACTIONS` (~line 1381) so the factsheet advertises them.

## State model

Two new fields on `Adapter` (`__init__`):

- `_work_in_progress: Optional[str]` — `None` | `"loading"` | `"unloading"`.
  BUSY ⇔ this is not `None`.
- `_work_action_id: Optional[str]` — the `action_id` of the active RUNNING work
  action, so the matching `stop*` can finish exactly that action.

**Persistence scope:** both fields are in-memory and live for the **adapter
process lifetime only**. BUSY survives a robot disconnect/e-stop (it is adapter
state, not robot state) but is **lost on adapter process restart**. Recovery of
BUSY after a restart is out of scope (see Out of scope).

## Behavior

### Start — `loading` / `unloading`

Preconditions, in order:

1. **Order active** (`state.order_id` set and `not _is_v3_order_finished()`):
   reject the action `FAILED` ("cannot start <type> while an order is in
   progress"). Do **not** enter BUSY.
2. **Already BUSY**: reject `FAILED` ("work already in progress: <current type>").
3. Otherwise: set `_work_in_progress=<type>`, `_work_action_id=action.action_id`;
   set the action's instant action state to **RUNNING** via
   `_update_instant_action_status(..., RUNNING, ...)`; `request_state_publish`.

### Order gating (while BUSY)

In `_handle_v3_order`, immediately after the `state is None` guard, add:

> if `_work_in_progress is not None` → `order_reject(True, WARNING,
> ORDER_CURRENT_NOT_FINISHED, "Robot is busy with <type> work; cannot accept
> order", error_hint=…)` and return.

Because BUSY rejects orders, no order can become active during BUSY, so the order
worker issues no motion — movement is blocked as a consequence.

### Movement gating (while BUSY)

Order rejection alone is **not** enough — several instant actions move the robot
without any order. During BUSY, reject every instant action that can issue a
JIBOT motion command, with `FAILED` ("busy with <type> work"):

- `startCharging` (issues `UmDock`).
- Any JIBOT-command instant action matched by `_is_jibot_command_instant_action`
  (~line 3335): `actionType="jibotCommand"`, `jibot*` aliases such as
  `jibotUmGoto` (~line 3345; handler ~line 3558), and actionTypes that are a
  JIBOT command name directly. These can send `UmGoto`/`UmDock` and physically
  move the robot — e.g. `test_adapter_jibot_v3_order.py:2167` drives the vehicle
  via `jibotUmGoto`.

**Policy:** block ALL raw JIBOT-command instant actions during BUSY
(conservative). Enumerating only the motion subset (`UmGoto`, `UmDock`, route
calls, …) is error-prone, and the robot must stay put while docked work
proceeds. Read-only JIBOT commands (volume/laser/info reads) could be
allow-listed later if a real need appears (YAGNI for now).

Non-JIBOT-command instant actions that do not move the robot remain allowed and
must keep working: `stateRequest`, `factsheetRequest`, `getMap`, `initPosition`,
`cancelOrder`, and the `stop*` completion actions themselves.

### Completion — `stopLoading` / `stopUnloading`

1. **Not BUSY, or type mismatch** (e.g. `stopUnloading` while `loading` active):
   reject `FAILED` ("no active <type> work to stop") and emit a warning info.
   BUSY state is left unchanged.
2. **Match**: set the active work action (`_work_action_id`) to **FINISHED**
   (this clears it from the published list); set the `stop*` action itself to
   `FINISHED`; clear `_work_in_progress` and `_work_action_id`;
   `request_state_publish`. Orders are accepted again.

## Edge cases & decisions

- `loading` while an order is active → `FAILED`, stays IDLE.
- `loading`/`unloading` while already BUSY → `FAILED`.
- `stop*` with no active work, or mismatched type → `FAILED` + warning info; BUSY
  unchanged.
- `cancelOrder` while BUSY → cancels **order** state only. BUSY is independent of
  orders and is **not** cleared by `cancelOrder`; work continues until the
  matching `stop*`.
- E-stop / robot disconnect while BUSY → BUSY persists (in-memory, no auto-clear).
  Adapter **process restart** does not preserve BUSY (in-memory fields); restart
  recovery is out of scope.
- The adapter sends **no** JIBOT robot command for loading/unloading; the
  physical transfer is performed by external equipment. The adapter only manages
  VDA5050 state and gating.

## Files to change

- `adaptor/adapter_jibot.py` — new handlers, `_work_in_progress`/`_work_action_id`
  fields, order guard in `_handle_v3_order`, four dispatch entries, four entries
  in `SUPPORTED_INSTANT_ACTIONS`.
- `adaptor/tests/test_adapter_jibot_v3_order.py` — TDD tests (below).

Action type names are hardcoded constants for now; revisit configurability if ACS
uses different names.

## Testing (TDD)

1. `loading` → the action appears in `instant_action_states` with status
   **RUNNING** and persists (non-terminal); `_work_in_progress == "loading"`.
2. Order received while BUSY → rejected (`order_reject`), not queued
   (`state.order_id` unchanged, worker not scheduled).
3. `stopLoading` → the active work action is **removed** from
   `instant_action_states` (terminal status is cleared immediately by
   `_clear_terminal_instant_action_states`; assert *absence*, not a lingering
   FINISHED entry); `_work_in_progress is None`; a subsequent order is accepted.
4. `loading` while an order is active → `FAILED`, not BUSY.
5. `loading` while already BUSY → `FAILED`.
6. `stopUnloading` while `loading` active (mismatch) → `FAILED`, still BUSY.
7. `stopLoading` with no active work → `FAILED`.
8. `startCharging` while BUSY → `FAILED`; no `UmDock` issued (`dock_calls` unchanged).
9. `jibotUmGoto` (and `jibotCommand`) while BUSY → `FAILED`; vehicle not moved
   (`goto_targets`/command list unchanged).
10. `unloading` / `stopUnloading` symmetric happy path.

## Out of scope

- Auto-detection of completion (that is MOMA's behavior; JIBOT injects).
- Commanding external loading/unloading equipment.
- Timeout-based auto-clear of BUSY.
