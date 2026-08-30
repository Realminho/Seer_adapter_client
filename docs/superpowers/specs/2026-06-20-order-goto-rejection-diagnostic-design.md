# Order Node Goto Rejection Diagnostic Design

## Purpose

When an order node motion command (`UmGoto`) is rejected or left unacknowledged
by the robot, the adapter must surface the failure instead of silently waiting
forever for an arrival that will never happen.

This was triggered by a live incident: the robot was in `mode=Stop` (a setup
problem, since resolved). The adapter sent `UmGoto goal=F2_90_S2CH`, the robot
neither acknowledged nor moved, and the adapter logged `[ORDER NODE UNREACHED]`
once and then waited indefinitely. Nothing in `errors[]` told the operator or
the fleet manager why the order had stalled.

The goal here is diagnostic visibility, not changing how a healthy robot
executes orders.

## Current Behavior (the gap)

- `_send_node_motion(node)` transmits the goto as **fire-and-forget**:
  - named goal → `goto_point` → `um_goto` → `send_command` (no response read)
  - explicit pose → optional `goto_node_position` hook if present, else
    `goto_xyz` (`UmGoto`; the JIBOT client has no `goto_node_position`)
  - charge node → `call_routes` (a different command, out of scope here)
- `_process_v3_node_step` then calls `_wait_until_node_position_reached`, which
  has **no timeout by design** and polls the robot pose forever.
- A robot that rejects or ignores the goto is **only** surfaced from inside that
  arrival wait: after ~1s of being stopped (and not in an obstacle brake) the
  loop adds a `JIBOT_NODE_UNREACHED` (**WARNING**) error and logs
  `[ORDER NODE UNREACHED]` once (`_set_jibot_node_unreached_error`,
  `adapter_jibot.py:2412`/`:1154`). Then it waits forever, and the step never
  fails.

So the gap is not "no error at all" — it is that the only signal is a late,
**WARNING**-level "stopped before reaching the target" symptom. There is no
distinct, **command-level** signal that the robot rejected or never
acknowledged the goto itself. The two are different causes and deserve
different errors:

- `JIBOT_NODE_UNREACHED` (WARNING): goto was accepted, robot stopped short.
- `JIBOT_GOTO_REJECTED` (FATAL, new): goto was rejected / never acknowledged at
  the ACK stage — the robot was never put in motion.

### Reuse note (not a fully proven pattern)

The order *action* path (`adapter_jibot.py`, the `_jibot_command_rejection_reason`
block around `:2133`) is a partial precedent, not a proven reject-handling
pattern:

- `_dispatch_jibot_command` waits for a response **only for `UmGoto`**; every
  other command runs its method and returns a synthetic
  `{"#CMD#": <command>, "state": True}` with no robot confirmation
  (`adapter_jibot.py:3441-3459`).
- Even for `UmGoto`, an **explicit** robot error is **not** caught today:
  `wait_for_response(command="UmGoto")` returns only frames whose
  `#CMD#=="UmGoto"`; an error frame (`#CMD#:"error"`) is skipped and re-queued,
  so the call times out to `None` and `_jibot_command_rejection_reason` reports
  the generic `"UmGoto no robot acknowledgement"` instead of the real reason
  (`client.py:522`).

What this design reuses is therefore narrow: the UmGoto action path's
**no-acknowledgement** handling. It must first **fix the response filter** so an
explicit error frame is actually returned (Step 0), which also improves the
existing order-action UmGoto path.

## Design

### 0. Recognize error frames as command responses (foundational)

`JIBOT.wait_for_response` / `send_command_and_wait` gain an opt-in
`accept_errors=False` parameter. When `accept_errors=True`, a frame whose
`#CMD#` equals the awaited command **or** equals `"error"` is returned as the
response (other frames are still skipped and re-queued as today).

- Default stays `False` so existing waiters (`get_map`, `get_battery_info`,
  status polls) keep skipping stray error frames and falling back to `None`.
- `_dispatch_jibot_command`'s `UmGoto` branch passes `accept_errors=True`, so
  both the existing order-*action* UmGoto path and the new order-*node* path
  receive the actual error frame and can extract the real reason via
  `_jibot_command_rejection_reason` (which already handles
  `response["#CMD#"] == "error"`).

Without this step the rest of the design still fails the step on rejection, but
every rejection — explicit or not — would be mislabeled as
`"no robot acknowledgement"`. This is the first work item.

### 1. Capture the robot response for node motion

`_send_node_motion` keeps its **existing emission path unchanged** —
`goto_point` for a named node, `goto_xyz` for a pose node (both call
`um_goto`, including the current `strict=False` argument) — and then **awaits
the `UmGoto` response separately** via
`wait_for_response(command="UmGoto", accept_errors=True)` (Step 0). This avoids
re-building `UmGoto` params (which would risk dropping `strict`) and avoids
bypassing the optional `goto_node_position` hook.

Scope of the response check:

- Covers the JIBOT client's `UmGoto` emission: `goto_point` (named) and
  `goto_xyz` (pose). On the JIBOT client there is **no** `goto_node_position`
  method, so the optional `getattr(self._vehicle, "goto_node_position", …)`
  branch (`adapter_jibot.py:2301`) falls back to `goto_xyz` → `UmGoto`, which is
  covered.
- If a non-JIBOT vehicle ever provides `goto_node_position` with different
  response semantics, that path is **out of scope** here.
- Charge-route nodes (`call_routes`) emit a different command, not `UmGoto`, and
  are **out of scope**. The incident was a plain goto; charge routing can be
  added later.
- Acknowledgement timeout defaults to **3.0s**, matching the order-action path.
  Note: a healthy robot now incurs up to this ack wait per node before the
  arrival poll begins (returns immediately once the ack arrives), where the old
  fire-and-forget path added none. This matches the existing order-action path.

### 2. Classify and surface rejection

After the send, run `_jibot_command_rejection_reason(command, response)`:

- **Rejected** (reason is not `None`) — covers both an explicit error response
  and no acknowledgement (`response is None`, i.e. the robot stayed silent for
  the timeout). On rejection:
  1. Append a new error to `state.errors[]`:
     - `errorType = JIBOT_GOTO_REJECTED` (new `ErrorType` member)
     - `errorLevel = FATAL` — the order cannot make progress
     - `errorReferences`: `nodeId`, `sequenceId`, `command`, `reason`,
       `jibotMode`, `jibotStatus`. `jibotMode` reads the vehicle's `_mode`
       attribute (already exposed and used at `adapter_jibot.py:622`); it is the
       field that carried `mode=Stop` in the incident, so it belongs in the
       reference set alongside `_status`.
     - `errorDescription`: explains the robot rejected/did not acknowledge the
       goto and the order is held.
     - The error is replaced (not duplicated) on each attempt, following the
       existing `_set_jibot_node_unreached_error` pattern that filters out the
       prior same-type error before appending.
  2. Log `[ORDER NODE GOTO REJECTED] id=<nodeId> seq=<seq> command=<cmd> reason=<reason>`.
  3. `_process_v3_node_step` returns `False`.
- **Accepted** (reason is `None`) — proceed to the existing
  `_wait_until_node_position_reached` arrival loop. No behavior change for a
  healthy robot.

### 3. Step-failure semantics (soft hold)

Returning `False` from `_process_v3_node_step` reuses the existing
"step not complete" path in `_process_v3_order_queue`: the step is **requeued
and the worker stops** (no busy-spin). The order is held in place with the
`JIBOT_GOTO_REJECTED` error visible in `errors[]`.

**Recovery is FM-driven, not automatic.** `_schedule_v3_order_worker` is called
**only** from order accept and order update (`adapter_jibot.py:1689`, `:1726`);
there is no periodic re-scheduler. So once the worker stops, clearing the
robot-side condition (e.g. taking the robot out of `mode=Stop`) does **not** by
itself resume the order — the fleet manager must re-send the order or an
orderUpdate to restart the worker, which then re-attempts the held step. (This
differs from the `JIBOT_NODE_UNREACHED` path, where the worker stays alive
polling and resumes by itself once the robot moves.)

This is deliberately a *soft hold*, not a hard order cancel:
- Recoverable — the rejection is usually an external/setup condition.
- VDA5050-aligned — the order remains and the error explains the stall.

**Resolved dependency:** the target FM, on receiving a node `FATAL` error,
**keeps the order and re-sends / retries** rather than aborting it (confirmed
with the stakeholder, 2026-06-20). This is what makes the soft-hold model
correct: the worker stops, the `FATAL` error explains why, and the FM's resend
restarts the held step once the robot-side condition is cleared. The
alternative recovery model (stay-alive auto-retry) is therefore **not** needed
and is listed as out of scope.

### 4. Clear the error on success

On rejection the worker stops (Step 3), so the only path back into this node is
a re-attempt whose `UmGoto` is **accepted**. At that point the stale
`JIBOT_GOTO_REJECTED` error is removed from `state.errors[]` before entering the
arrival wait, so it does not linger after recovery. (The replace-before-append
pattern from Step 2 already prevents duplicates across repeated rejections.)

## Components Touched

- `jibot-client/src/jibot_client/client.py` (Step 0) — add `accept_errors=False`
  to `wait_for_response` and `send_command_and_wait`; when set, return a frame
  whose `#CMD#` is the awaited command **or** `"error"`.
- `adaptor/adapter_jibot.py`:
  - `_send_node_motion` — keep the existing `goto_point` / `goto_xyz` emission
    (with `strict`), then `await` the `UmGoto` response via
    `wait_for_response(command="UmGoto", accept_errors=True)` and return it.
  - `_process_v3_node_step` — run `_jibot_command_rejection_reason` on that
    response; on rejection set the error, log, return `False`; on accept clear
    the stale error before the arrival wait.
  - `_dispatch_jibot_command` — pass `accept_errors=True` on the `UmGoto` branch.
    This is independent of the node path; it fixes the **existing** order-action
    UmGoto path so its explicit rejects are no longer mislabeled as no-ack.
  - A small `_set_jibot_goto_rejected_error(...)` helper mirroring
    `_set_jibot_node_unreached_error`.
- `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` — add
  `JIBOT_GOTO_REJECTED` to `ErrorType`.

## Testing

**Client response filter (Step 0)** — `adaptor/tests/test_jibot_client_wait_for_response.py`.
This is the highest-risk piece, since the real bug lives in `wait_for_response`,
not in any fake. Add:

- While awaiting `UmGoto` with `accept_errors=True`, a queued `{"#CMD#": "error",
  …}` frame is **returned** (not skipped to timeout).
- With `accept_errors=False` (default), the same error frame is still skipped and
  re-queued, and the call times out to `None` — proving no regression for
  existing waiters (`get_map`, `get_battery_info`).
- A matching `UmGoto` frame is still returned ahead of an error frame when both
  are queued.

**Adapter node-step handling** — extend `adaptor/tests/test_adapter_jibot_v3_order.py`
and its `FakeVehicle`:

- **Rejected response** (robot returns `{"#CMD#": "error", ...}` /
  `state: false`): node step returns `False`, `errors[]` contains one
  `JIBOT_GOTO_REJECTED` (FATAL) with the expected references (including the real
  reason, not `"no robot acknowledgement"`), and `[ORDER NODE GOTO REJECTED]` is
  logged.
- **No acknowledgement** (robot returns `None` within timeout): same step-failure
  handling, reason recorded as no-acknowledgement.
- **Accepted response**: existing arrival-wait behavior is preserved (no
  regression), and any prior `JIBOT_GOTO_REJECTED` error is cleared.

## Out of Scope (YAGNI)

- Charge-route (`call_routes`) acknowledgement checking.
- Skipping the goto when the robot is already inside the node reach zone
  (mentioned during triage, intentionally a separate change).
- Hard order cancel on rejection (soft hold chosen instead).
- Stay-alive auto-retry recovery (the worker re-emitting `UmGoto` on a timer).
  Recovery is FM-driven by decision; see Step 3.
