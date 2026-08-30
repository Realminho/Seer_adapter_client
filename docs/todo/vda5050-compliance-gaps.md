# VDA5050 Compliance Gaps TODO

## Context

Review of the Jibot adapter (`adaptor/adapter_jibot.py`, `adaptor/main.py`,
`adaptor/protocol/vda_2_0_0`, `adaptor/protocol/vda5050_3_0`) against the
VDA5050 specification, performed 2026-06-10. Items are ordered by severity.
The adapter publishes `version: "3.0.0"` with the in-house v3 dialect
(`mobileRobotPosition` / `powerSupply`); compliance below is judged against the
VDA5050 order/state/action semantics shared by 2.x and that dialect.

Related: [state-data-quality.md](state-data-quality.md) covers payload value
quality (units, SOC, localization score). This document covers protocol
behavior. The prioritized plan for the remaining (medium/low) items lives in
[next-steps.md](next-steps.md).

## High severity (spec violation / broken behavior)

- [x] Node completion without arrival verification.
  - `_process_v3_node_step()` marked a node FINISHED right after sending
    `goto_point`/`goto_xyz`; the jibot-client only transmits the command and
    returns, so nodes were reported reached before the robot moved.
  - Fix: the v3 order worker now resolves the node target coordinates
    (order `nodePosition`, falling back to the robot map) and waits until the
    robot pose enters the reach zone (`reach_zone_shape`/`reach_zone_scale`,
    `default_node_deviation_xy` when the order carries no deviation) before
    completing the step, updating `lastNodeId`, and clearing node state.
  - When no coordinates are available for a node, the adapter logs a warning
    and falls back to send-and-complete (cannot verify arrival).

- [x] Node/edge actions were never executed.
  - `_process_v3_step_actions()` flipped every action
    INITIALIZING -> RUNNING -> FINISHED instantly without doing anything, and
    `blockingType` was ignored.
  - Fix: order actions are now executed through the same JIBOT command
    executor as instant actions (`jibotCommand` form or a bare
    `COMMAND_SPECS` action type). Unsupported action types are reported
    FAILED instead of silently FINISHED. `NONE`/`SOFT` actions can run in
    parallel, `SINGLE`/`HARD` wait for prior actions and run exclusively,
    `NONE`/`SINGLE` allow driving, and `SOFT`/`HARD` stop and verify residual
    automatic motion before starting. V3 instant actions reject any
    `blockingType` other than `NONE`.
  - Node actions now run after arrival at the node (previously before
    motion), matching the spec's "actions are triggered when the node is
    reached".

- [x] Standard instant actions missing.
  - Only `cancelOrder` plus custom actions existed. Added:
    - `stateRequest`: immediate state publish.
    - `factsheetRequest`: publish the factsheet topic.
    - `startPause` / `stopPause`: stop motion / re-issue the active goto,
      maintain `state.paused`, pause/resume RUNNING order actions.
    - `startCharging`: JIBOT `UmDock`.
    - `initPosition`: JIBOT `UmLocalize` from x/y/theta/mapId parameters.
    - `stopCharging`, `logReport`: explicitly rejected as FAILED
      (unsupported by the JIBOT API) and excluded from the factsheet.

- [x] No factsheet.
  - Added a `factsheet` topic message (retained, QoS 1) published at adapter
    startup and on `factsheetRequest`, advertising typeSpecification,
    physicalParameters, protocolLimits, protocolFeatures (supported
    instant/order actions), and loadSpecification (tray slots).
  - Values not available from the robot API use documented defaults; refine
    `physicalParameters` once real vehicle data is confirmed.

- [x] No event-driven state publishing.
  - State was published on a fixed `state_publish_delay` interval only; the
    spec requires immediate publication on state-changing events plus the
    periodic heartbeat.
  - Fix: `request_state_publish()` wakes the publish loop immediately on
    order accept/update/reject, order step completion, action status
    changes, instant action handling, and cancelOrder. The interval remains
    as the heartbeat upper bound.

- [x] Static facts repeated in every state.information.
  - Reviewed all information entries; static ones moved to the factsheet so
    they are published once (retained) instead of every state cycle:
    - `POSITION_UNIT` -> factsheet `coordinateUnits` (mm/deg).
    - `SIMULATION` -> factsheet `simulation` (the mapId reference was
      redundant with `mobileRobotPosition.mapId`).
    - `VIDEO_STREAMS` -> factsheet `videoStreams`; `requestVideo` still
      returns the URLs in its action result.
  - Kept in state (dynamic): `JIBOT_STATUS` (trimmed: voltage/current/health
    dropped as exact duplicates of `powerSupply`; raw SOC, mode/status,
    temperatures, cells remain), `NEAREST_NODE` (the nearest-map-node gap
    telemetry, renamed from `LAST_NODE_GAP` 2026-06-21; static `unit` reference
    dropped), and the ORDER_*_ACCEPTED/REJECTED event entries.

- [x] nodeStates/edgeStates serialized with full order payloads.
  - The published state embedded entire order `Node`/`Edge` objects
    (including `actions`, edge speed limits, etc.).
  - Fix: `_build_v3_state_message()` now serializes the spec NodeState
    subset (`nodeId`, `sequenceId`, `released`, `nodeDescriptor?`,
    `nodePosition?`) and EdgeState subset (`edgeId`, `sequenceId`,
    `released`, `edgeDescriptor?`, `trajectory?`). Internal bookkeeping
    still keeps the full objects.

## Medium severity (spec mismatch)

- [x] `lastNodeId` is computed as nearest node, not last traversed node.
  - Fixed 2026-06-21: `lastNodeId`/`lastNodeSequenceId` now advance only on
    confirmed arrival (order worker) plus reset / station / idle-startup
    seeding. The per-cycle `_update_nearest_node_from_position()` maintains a
    separate `_nearest_node_*` variable (the `sequenceId=0` map-node fallback
    lives there, surfaced as the `NEAREST_NODE` information entry — telemetry
    only). See `docs/superpowers/specs/2026-06-20-last-node-id-vda5050-design.md`.
  - Spec: lastNodeId = last node the AGV traversed (or "" before the first).
- [ ] Order update validation is incomplete.
  - Same `orderUpdateId` re-delivery should be discarded silently
    (idempotent), not rejected.
  - New base start node / sequenceId must match the end of the current base
    (`ORDER_START_NODE_INVALID` / `ORDER_START_SEQUENCE_ID_INVALID`); these
    checks exist only in the dead legacy path `order_accept_procedure()`.
- [ ] `operatingMode` is effectively always AUTOMATIC
  (`_derive_operating_mode` returns the previous value, initialized
  AUTOMATIC). Robot manual/service modes are not reflected.
- [ ] Localization state is not reflected: `positionInitialized`/`localized`
  always True; `localizationScore` has no threshold logic.
- [ ] Pose units are mm/deg instead of spec m/rad (workaround:
  `POSITION_UNIT` information entry). Incompatible with standard masters.
  See state-data-quality.md.
- [x] `cancelOrder` waits until the vehicle stands still before FINISHED.
  `cancelOrder` remains RUNNING while `stop_motion` runs and until the vehicle
  no longer reports driving; tests cover both simulator and general vehicle
  paths.
- [ ] Error object quality: `order_reject()` swaps referenceKey/value
  semantics, references the current order instead of the rejected one, and
  repeated rejections append duplicate errors. E-stop AUTOACK is collapsed
  to NONE in the v3 mapping.

## Low severity / cleanup

- [ ] `visualization` topic is not published (`visualization_frequency`
  config exists but is unused).
- [ ] `velocity`, `distanceSinceLastNode`, `newBaseRequest`, `zoneSetId`
  never set.
- [ ] Incoming order/instantActions `version`/`manufacturer`/`serialNumber`
  are not validated against this vehicle.
- [ ] Edge attributes (trajectory, maximumSpeed, orientation, direction,
  ...) are parsed but not applied to motion; edge traversal completion is
  not tied to actual robot movement along the edge.
- [ ] Dead legacy execution path (`order_accept_procedure`,
  `_execute_path`, `_wait_until_reached`, `update_or_append`,
  `manage_instant_actions`) duplicates the v3 queue worker and should be
  removed once the v3 path covers charging-route behavior.
- [ ] Confirm with the ACS team that the "3.0.0" dialect
  (`mobileRobotPosition`/`powerSupply`/`instantActionStates`) is the agreed
  schema; official VDA5050 releases are 2.x.

## Open questions

- What are the exact semantics of JIBOT `UmLocalize` `target`/`goal`
  parameters? (initPosition mapping currently uses
  `target="pose"`-style defaults; confirm with the JIBOT API owner.)
- Is there any JIBOT command to stop charging / undock? `stopCharging` is
  rejected as unsupported until one is confirmed.
- Which order action types will ACS send (e.g. `docking`, `open_motor` from
  `[port]` config)? They need explicit executors; unknown types are now
  FAILED per spec.
