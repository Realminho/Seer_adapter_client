# VDA5050 State Data Quality TODO

## Context

Example state payload observed on 2026-06-01:

```json
{
  "headerId": 169,
  "version": "3.0.0",
  "manufacturer": "jibot",
  "serialNumber": "HN-SH6-TR-001",
  "operatingMode": "MANUAL",
  "batteryState": {
    "batteryCharge": 0.0,
    "charging": false,
    "batteryVoltage": 82.0
  },
  "loads": [
    { "loadId": "tray-001", "loadType": "TRAY", "loadPosition": "slot1" },
    { "loadId": "tray-002", "loadType": "NULL", "loadPosition": "slot2" },
    { "loadId": "tray-003", "loadType": "NULL", "loadPosition": "slot3" },
    { "loadId": "tray-004", "loadType": "TRAY", "loadPosition": "slot4" },
    { "loadId": "tray-005", "loadType": "NULL", "loadPosition": "slot5" },
    { "loadId": "tray-006", "loadType": "NULL", "loadPosition": "slot6" }
  ],
  "agvPosition": {
    "x": 12916.0,
    "y": 4608.0,
    "theta": 43.0,
    "mapId": "lab2m",
    "positionInitialized": true,
    "localizationScore": 493.0
  }
}
```

The adapter publishes `version: "3.0.0"` and uses the v3 topic configuration, but the runtime state model still contains v2-style fields. The following tasks should be handled before relying on this state for ACS logic.

## TODO

- [x] Add VDA5050 v3-compatible state serialization.
  - Required v3 fields missing from the observed payload: `powerSupply`, `instantActionStates`.
  - v3 position field should be `mobileRobotPosition`.
  - Current payload still emits legacy-style `batteryState` and `agvPosition`.
  - Relevant files:
    - `adaptor/adapter_jibot.py`
    - `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py`
    - `adaptor/protocol/vda5050_v3/json_schemas/state.schema`

- [x] Use VDA5050 v3 order parsing for received movement orders.
  - Adapter now imports a v3 order model for `/order` payloads.
  - v3 order aliases supported: `nodeDescriptor`, `edgeDescriptor`, `maximumSpeed`, `maximumMobileRobotHeight`, `minimumLoadHandlingDeviceHeight`, `reachOrientationBeforeEntering`.
  - v3 edge start/end nodes are inferred from sequence IDs when `startNodeId`/`endNodeId` are omitted.
  - v3 `SINGLE` action blocking type is accepted.
  - Runtime behavior uses an `asyncio.Queue` worker to process node/edge steps in `sequenceId` order.
  - The worker checks `released`, previews actions, calls `goto_point(nodeId)` for node steps, and clears completed node/edge state.

- [x] Add VDA5050 v3 `cancelOrder` instantAction handling.
  - `cancelOrder` cancels the order worker, clears queued steps and pending node/edge state, marks active actions failed, records the instantAction as finished, and sends robot `stop_motion()`.

- [x] Add separate VDA5050 3.0 send/receive package.
  - New package: `adaptor/protocol/vda5050_3_0`.
  - Message classes are separated from the legacy v2 implementation.
  - `codec.py` provides JSON encode/decode and topic-based order/instantActions parsing.
  - `transport.py` provides MQTT publish/subscribe helpers for state, connection, order, and instantActions.
  - Adapter state publishing now builds `vda5050_3_0.State` and sends it through `publish_state()`.

- [ ] Fix battery state-of-charge logic.
  - Current `batteryCharge` is fixed at `0.0` while `batteryVoltage` is `82.0`.
  - Decide source of truth:
    - Use robot API SOC directly if available.
    - Otherwise calculate SOC from voltage using an agreed voltage range/table.
  - For v3, publish SOC as `powerSupply.stateOfCharge`.

- [ ] Normalize robot position units.
  - Current `x: 12916.0`, `y: 4608.0` are likely raw robot units or millimeters.
  - VDA5050 position fields should be in meters.
  - Add explicit conversion before publishing state.
  - Confirm whether Jibot position source is mm, cm, or map-specific unit.

- [ ] Convert heading angle to radians and clamp/normalize range.
  - Current `theta: 43.0` is outside the v3 radian range `-pi` to `pi`.
  - If robot heading is degrees, convert `43 deg` to about `0.7505 rad`.
  - Normalize published theta into `[-pi, pi]`.

- [ ] Normalize `localizationScore`.
  - Current `localizationScore: 493.0` is outside the v3 range `0.0` to `1.0`.
  - Confirm raw score scale from robot API.
  - Convert to a ratio before publishing, or omit the field if the score cannot be trusted.

- [ ] Filter empty load slots from `loads`.
  - Current payload publishes empty slots as `loadType: "NULL"` with non-empty `loadId`.
  - Prefer publishing only actual loads, for example `slot1` and `slot4`.
  - If slot occupancy must be exposed, use internal diagnostics or `information`, not fake load objects.

- [x] Review `lastNodeId` and `lastNodeSequenceId` updates.
  - Reworked 2026-06-21: `lastNodeId`/`lastNodeSequenceId` now advance only on
    confirmed reach-zone arrival (order worker), and an idle/startup bootstrap
    seeds them from the nearest map node when there is no active order — so a
    localized robot with a loaded map no longer reports `lastNodeId: ""`. The
    per-cycle `_update_nearest_node_from_position()` maintains the separate
    `_nearest_node_*` telemetry instead of overwriting `lastNodeId`.
  - Node matching around reach zones is governed by the arrival path
    (`_wait_until_node_position_reached`).
  - See `docs/superpowers/specs/2026-06-20-last-node-id-vda5050-design.md`.

- [ ] Improve `operatingMode` mapping.
  - Current logic derives mode from robot status string matching.
  - Replace fragile substring checks with a clear mapping from robot API status/mode to VDA5050 operating mode.
  - Keep movement status separate from mode status.

- [ ] Add schema validation for published state.
  - Validate generated state against `adaptor/protocol/vda5050_v3/json_schemas/state.schema` when `vda_version = "v3"`.
  - Add at least one regression test using the observed payload shape.
  - Test should fail on missing v3 required fields and invalid value ranges.

## Suggested Implementation Order

1. Add v3 field aliases/output: `powerSupply`, `mobileRobotPosition`, `instantActionStates`.
2. Add pose normalization: x/y meters, theta radians, localization score ratio.
3. Add battery SOC source or voltage-to-SOC conversion.
4. Filter `loads` so only real loads are published.
5. Re-test `lastNodeId` matching after pose normalization.
6. Add schema validation and regression tests.

## Open Questions

- What unit does the Jibot position API return for `x` and `y`?
- What unit does the Jibot heading API return for `theta`?
- What is the valid range of `_vehicle._localization_score`?
- Is SOC available directly from the robot API, or must it be derived from voltage?
- Does ACS expect strict v3 field names only, or does it currently tolerate legacy aliases like `batteryState` and `agvPosition`?
