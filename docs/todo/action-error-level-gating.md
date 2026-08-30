# Action execution gating by error level

**Status:** not started. Design agreed in principle 2026-08-19; implementation deferred.

Define, per action (built-in / extension / recipe), the error level at which that
action becomes unexecutable — instead of today's scattered, per-call-site guards.

---

## Why this exists

The FMS refuses to dispatch orders while the adapter publishes a `CRITICAL`
error (confirmed by the operator, 2026-08-19). The adapter itself, however, has
**no error-level gate at all** — verified by grep over `adaptor/adapter_jibot.py`:
`error_level` is only ever *written* into published errors, never *read* to
decide whether something may run.

What the adapter has instead is a handful of unrelated, hand-written guards at
each call site:

| Guard | Where |
|---|---|
| `_work_in_progress is not None` (BUSY) | `adapter_jibot.py:3446, 5812, 5944, 6245, 7001` |
| order worker already running | `_handle_goto_nearest_node_instant_action`, `adapter_jibot.py:6253` |
| not localized (`_x`/`_y` unknown) | `adapter_jibot.py:6239` |
| charging before order motion | `_ensure_not_charging_before_order_motion:4820` |
| motor off before order motion | `_ensure_motor_enabled_before_order_motion:4851` |
| residual motion before SOFT/HARD action | `_stop_for_blocking_order_actions:4192` |

Each was added for one bug. There is no single place that answers "may this
action run right now?", so every new action re-derives its own answer and new
failure states get missed.

## The constraint that shapes the design

**A recovery action must survive the error it exists to clear.**

The concrete case: an empty `lastNodeId` publishes `LAST_NODE_ID_MISSING` at
`ErrorLevel.CRITICAL` (`adapter_jibot.py:2540`). The FMS then holds all orders.
The only way out is for an operator to run `gotoNearestNode`, which drives the
robot back onto a node and writes `lastNodeId`. A naive "block everything at
CRITICAL" gate would block that action too and strand the robot in a state only
that action can clear.

This is locked in by a regression test — it must keep passing through any
implementation of this feature:

    adaptor/tests/test_goto_nearest_node.py
      test_goto_nearest_node_still_runs_while_a_critical_error_is_active

It asserts the CRITICAL is actually present (so it cannot pass vacuously) and
then that `gotoNearestNode` reaches `FINISHED`.

## Open design questions

**1. Is error *level* the right key at all?**

Every guard in the table above blocks on an error *kind*, not a level: motor
off, charging, BUSY, order in progress. A single `blocked_above: CRITICAL`
per action cannot express "charging blocks motion actions but not sound
actions". The policy may need to key on `error_type`, with level as a coarse
default.

**2. How are exemptions declared?**

`gotoNearestNode` proves that a per-action ceiling is not enough — the relation
is between *an action* and *a specific error*. Some form of "this error does not
block this action" is required, i.e. a two-sided declaration rather than one
threshold per action. Candidate: each action declares both a default ceiling and
an explicit exempt-list of error types.

**3. Where does the declaration live?**

- `adaptor/core/action_registry.py` / `action_modules.py` — validated at load,
  but changing a policy needs a redeploy.
- `adaptor/config/extensions.hcl`, `adaptor/config/recipes.hcl` — tunable in the
  field without redeploy, but weaker validation and easy to get wrong on a
  running robot.

Extension and recipe actions are declared in HCL, so the policy probably has to
be expressible there regardless; the question is whether built-ins share that
mechanism or keep a code-side declaration.

**4. What happens to an action that is blocked?**

`FAILED` with a `result_description` naming the blocking error is the obvious
answer and matches how the existing guards report. Confirm the FMS distinguishes
"rejected because blocked" from "attempted and failed" — if it does not, a
blocked action may be retried forever.

## Scope note

Do **not** fold the six existing guards into the new layer in the same change.
Land the mechanism plus one or two migrated guards, verify on a robot, then
migrate the rest. Several of the guards (motor, charging) also perform recovery
before proceeding, not just rejection — they are not pure predicates and do not
translate one-to-one.

## Related

- Session that produced this: `WORKING/2026-08-19/143000-goto-nearest-lastnode-attribution.md`
- `LAST_NODE_ID_MISSING` and the seed gate: `WORKING/2026-08-19/000722-far-arrival-completed-diag.md`
- Action plugin design: `docs/superpowers/plans/` (action plugin spec commits)
- The other missing per-action declaration — which *scopes* an action is valid
  in (INSTANT / NODE / EDGE): [action-scope-and-dispatch-unification.md](action-scope-and-dispatch-unification.md)
