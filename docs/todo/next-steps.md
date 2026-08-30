# VDA5050 Adapter — Next Steps

## Context

The high-severity protocol gaps are done (see
[vda5050-compliance-gaps.md](vda5050-compliance-gaps.md)): node arrival
verification, real action execution, standard instant actions, factsheet,
event-driven state publishing, and spec-subset nodeStates/edgeStates. The
simulator was extended to match (`UmDock`, `UmLocalize`).

This document is the prioritized plan for what remains. Items are grouped by
priority and each carries a goal, code location, approach, and done-criteria so
the work can be picked up directly. Severity labels map back to the gap
catalog.

Before starting, resolve the **blocking questions** below — three medium items
(pose units, lastNodeId, operatingMode) depend on JIBOT API answers that only
the robot/ACS owners have.

---

## Blocking questions (answer first)

These gate the work that follows. Owners: JIBOT API team + ACS/FMS team.

1. **Position/heading units.** What units does the JIBOT API return for
   `x`/`y` (mm? map units?) and `theta` (deg? rad?)? Needed for P1-1.
2. **localizationScore scale.** What is the valid range of
   `_localization_score` (observed `493.0`)? A ratio, a covariance, an error
   distance? Needed for P1-3.
3. **operatingMode source.** Does the JIBOT API expose a discrete mode
   (AUTOMATIC/MANUAL/SERVICE), or must it be inferred from mode/status
   strings? Needed for P1-2.
4. **SOC source.** Is state-of-charge available directly, or must it be
   derived from voltage (observed `batteryCharge: 0.0`, `voltage: 82.0`)? See
   state-data-quality.md.
5. **Dialect agreement.** Has ACS agreed to the in-house "3.0.0" dialect
   (`mobileRobotPosition`/`powerSupply`), or must we emit official VDA5050 2.x
   field names? Changes the scope of P1-1 and the schema work.
6. **stopCharging/undock.** Is there any JIBOT command to leave the charger?
   Until confirmed, `stopCharging` stays rejected.
7. **Order action types.** Which node/edge action types will ACS actually
   send (e.g. `docking`, `open_motor` from `[port]` config)? Each needs an
   explicit executor; unknown types are currently FAILED.

> Q1 (pose units), Q2 (localizationScore scale) and Q3 (operatingMode source) may
> be answerable read-only from the robot itself — see **D2** in
> [jibot-subsystem-deep-dives.md](jibot-subsystem-deep-dives.md) (EKF/localization
> chain) before escalating to the API owners.

---

## P1 — Data correctness (medium severity)

State values are wrong/misleading for a standards-compliant master. Do these
before P2.

### P1-1. Normalize pose to spec units (m / rad)
- **Severity:** medium. Also tracked in state-data-quality.md.
- **Where:** `adapter_jibot.py` `publish_state()` (~345) and
  `_build_v3_state_message()` (~770); units currently advertised via factsheet
  `coordinateUnits`.
- **Approach:** add a conversion layer (config-driven scale: mm→m =
  /1000, deg→rad) applied right before building `MobileRobotPosition`. Convert
  node-match math (`_find_nearest_node`, reach zones) consistently or keep it
  in raw units internally and convert only at publish. Decide based on Q1/Q5.
- **Done when:** published `mobileRobotPosition` is in m/rad within `[-pi, pi]`;
  factsheet `coordinateUnits` becomes `m`/`rad`; node matching still works.

### P1-2. Real operatingMode mapping
- **Severity:** medium.
- **Where:** `_derive_operating_mode()` (adapter_jibot.py:507) — currently
  returns the previous value, so it is effectively pinned to AUTOMATIC.
- **Approach:** map the JIBOT mode/status (Q3) to `OperatingMode`. Replace the
  fragile self-referential return with an explicit table; keep motion status
  (`driving`) separate from operating mode.
- **Done when:** robot manual/service/teach modes surface in `operatingMode`.

### P1-3. Localization state + score normalization
- **Severity:** medium.
- **Where:** `publish_state()` sets `position_initialized=True` hard-coded
  (adapter_jibot.py:345); `localized` derives from it (line ~779).
- **Approach:** derive `localized`/`positionInitialized` from a
  `localization_score` threshold (Q2). Normalize the score to `0.0–1.0` (or
  omit when untrustworthy) before publishing.
- **Done when:** `localized` reflects real localization; `localizationScore`
  is in range or absent.

### P1-4. lastNodeId = last traversed node (not nearest) — ✅ Resolved 2026-06-21
- **Severity:** medium.
- **Status:** ✅ Resolved 2026-06-21. See
  `docs/superpowers/specs/2026-06-20-last-node-id-vda5050-design.md`.
- **What shipped:** `lastNodeId`/`lastNodeSequenceId` now advance only on
  confirmed arrival in the order worker (plus reset / station bootstrap /
  idle-startup seeding) and are never overwritten per cycle from nearest-node
  matching. The per-cycle method was renamed
  `_update_last_node_from_position` → `_update_nearest_node_from_position` and
  now maintains a separate `_nearest_node_*` variable surfaced as the
  `NEAREST_NODE` information entry (telemetry only; the `sequenceId=0` map-node
  fallback lives there, not in `lastNodeId`).
- **Done when (met):** `lastNodeId`/`lastNodeSequenceId` only advance when a
  node is actually reached; `""`/`0` before the first node.

---

## P2 — Order/cancel protocol correctness (medium severity)

### P2-1. Order-update validation (idempotency + base continuity)
- **Severity:** medium.
- **Where:** `_update_v3_order_for_queue()` (the live v3 path). The full
  checks already exist in the dead legacy `order_accept_procedure()`
  (adapter_jibot.py:2553) — port them.
- **Approach:**
  - Same `orderUpdateId` re-delivery → discard silently (idempotent), do not
    reject with `ORDER_UPDATE_ID_INVALID`.
  - New base's first node id + sequenceId must match the end of the current
    base → else `ORDER_START_NODE_INVALID` / `ORDER_START_SEQUENCE_ID_INVALID`.
- **Done when:** duplicate updates are no-ops; discontinuous updates are
  rejected with the correct errorType.

### P2-2. cancelOrder waits for standstill
- **Severity:** medium.
- **Where:** `_handle_cancel_order_instant_action()` +
  `_finish_cancel_order()`.
- **Status:** done. `cancelOrder` now stays RUNNING until `stop_motion`
  completes and the vehicle reports not driving; simulator and general-vehicle
  tests cover the behavior.
- **Approach:** mark cancelOrder FINISHED only after `stop_motion` completes
  and the robot reports stopped (`driving`/status), not immediately on
  scheduling the stop.
- **Done when:** the cancelOrder instantAction reaches FINISHED only once the
  vehicle stands still.

### P2-3. Error object quality
- **Severity:** medium.
- **Where:** `order_reject()` (~end of file, builds `ErrorReference`).
- **Approach:**
  - Fix swapped `referenceKey`/`referenceValue` semantics.
  - Reference the rejected order's id, not the current `state.order_id`.
  - Dedupe repeated identical rejections instead of appending every cycle.
  - Stop collapsing E-stop `AUTOACK` → `NONE` in the v3 mapping
    (`_build_v3_emergency_stop`); preserve the distinction or document why.
- **Done when:** errors carry correct references, no duplicates, e-stop state
  is not lost.

---

## P3 — Cleanup / completeness (low severity)

### P3-1. Remove the dead legacy execution path
- **Where:** `order_accept_procedure` (2553), `order_accept` (2646),
  `order_update_accept` (3217), `_execute_path` (2986), `_wait_until_reached`
  (3089), `update_or_append` (2968), `cancel_order` (2640),
  `manage_instant_actions` (2509).
- **Approach:** these duplicate the v3 queue worker and are no longer called.
  Confirm no references remain, then delete. Port any continuity checks needed
  by P2-1 *before* removing `order_accept_procedure`.
- **Done when:** only the v3 queue path remains; tests still pass.

### P3-2. Validate inbound message identity
- **Where:** `handle_incoming_acs_cmd()`.
- **Approach:** reject/ignore order/instantActions whose
  `manufacturer`/`serialNumber` don't match this vehicle (`version` optional).
- **Done when:** orders addressed to another vehicle are not executed.

### P3-3. Fill remaining state fields
- **Where:** `publish_state()`.
- **Approach:** populate `velocity` (from JIBOT motor/odom),
  `distanceSinceLastNode`, `newBaseRequest`, `zoneSetId` where data exists;
  otherwise document why omitted.

### P3-4. Publish the visualization topic
- **Where:** new task in `run_adapter()`; `visualization_frequency` config
  exists but is unused.
- **Approach:** publish a lightweight position/velocity message on the
  `visualization` topic at the configured frequency (higher rate than state).

### P3-5. Apply edge attributes to motion
- **Where:** order worker edge handling.
- **Approach:** use `maximumSpeed`/`orientation`/`direction`/`trajectory`
  when issuing motion (depends on JIBOT API support); tie edge-step completion
  to actual traversal rather than completing on the following node arrival.

---

## P4 — Validation & tests

### P4-1. State schema validation + regression tests
- **Approach:** validate generated state against a v3 JSON schema when
  `vda_version = "v3"`. Add regression tests on the observed payload shape
  that fail on missing required fields / out-of-range values (units, SOC,
  score). Extend `tests/test_adapter_jibot_v3_order.py`.
- **Done when:** CI catches a malformed state before it ships.

---

## Suggested order

1. Answer the blocking questions (unblocks P1).
2. P1-1 → P1-3 → P1-2 → P1-4 (pose units first; node matching depends on it).
3. P2-1 → P2-3 → P2-2.
4. P3-1 (delete dead code once P2-1 has ported what it needs).
5. P3-2 … P3-5 as capacity allows.
6. P4-1 alongside each change.
