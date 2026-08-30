# Clamp and PIO Actions Design

## Purpose

Add VDA5050 actions for clamp and PIO hardware control.

The same action type names are used for both paths:

- `instantActions`: manual test and field commissioning.
- `order.actions`: ACS operational workflows.

The adapter keeps separate VDA5050 status surfaces, but both paths call the same internal handlers so behavior and validation stay identical.

## Clamp Actions

Clamp actions expose the tray clamp domain, not low-level motor terminology.

- `clamp`: move clamp to configured clamp/close position.
- `unclamp`: move clamp to configured unclamp/open position.
- `clampTeach`: initialize or learn clamp/open positions when supported by the EZI motor API.
- `clampOn`: enable clamp servo.
- `clampOff`: disable clamp servo.
- `clampStop`: stop clamp motor movement.

`clampEmergencyStop` is intentionally not part of the initial operational set. The EZI motor has an emergency stop command, but exposing it as a normal ACS action is risky. It can be added later as a test-only action if field commissioning needs it.

Clamp actions require `Adapter._ezi_motor`. If the motor is not attached, the action fails with a clear result description.

## PIO Actions

PIO supports both low-level test actions and scenario-based operational execution.

- `pioInit`: connect to the PIO master, send the BC connection request, optionally clear outputs, and report the connection result.
- `pioReadIn`: read current PIO input states 1 through 8.
- `pioWriteOut`: set one output index 1 through 8 to `on` or `off`.
- `pioDisconnect`: clear outputs when requested and close the PIO connection.
- `pioScenario`: run a declarative sequence after initialization succeeds.

PIO connection parameters are action parameters:

- `media`
- `stationId`
- `channel`
- `port`
- `ohtNumber`
- `timeoutSec` optional default timeout

## PIO Scenario DSL

`pioScenario` receives a `scenario` action parameter containing a list of steps.

Each step is one of:

```json
{ "type": "out", "index": 1, "state": "on" }
{ "type": "in", "index": 3, "state": "on", "timeoutSec": 5 }
{ "type": "delay", "sec": 0.5 }
```

Rules:

- `index` is 1 through 8.
- `state` is `on` or `off`.
- `out` writes immediately.
- `in` waits until the input reaches the requested state.
- `delay` sleeps for the requested seconds.
- `timeoutSec` on an `in` step overrides the scenario default timeout.
- `pioScenario` automatically performs `pioInit` before running steps.
- `pioScenario` disconnects at the end unless an action parameter explicitly asks to keep the connection open.

## Result Reporting

Actions report concise results through VDA5050 action state result descriptions.

Successful scenario example:

```text
pioScenario finished: 4/4 steps ok; inputs={1:off,2:off,3:on}
```

Failed scenario example:

```text
pioScenario failed at step 2: expected in3=on within 5.0s; last inputs={1:off,2:off,3:off}
```

The adapter logs a full result object containing:

- `ok`
- `action`
- `failedStep`
- `failedReason`
- `message`
- latest `inputs`
- per-step results

The VDA5050 state carries a short summary to avoid bloating state payloads.

## EZI IO Sensor Actions

EZI IO sensor reads are separate from PIO reads.

- `ezioReadIn`: read all EZI IO input bits 1 through 16.
- `photoSensorRead`: read the six tray photo sensors configured by `ezi.tray_slot_pin`.

The adapter already uses `ezi.tray_slot_pin` to map photo sensors into
`state.loads`. A `photoSensorRead` action updates `state.loads` immediately.
The background tray-slot polling loop also uses the same helper, and when any
photo sensor changes a load state it calls `request_state_publish("photo sensor changed")`
so ACS receives a fresh state without waiting for the periodic heartbeat.

`photoSensorRead` result summaries include:

- all EZI IO inputs as indexed `on`/`off` states.
- six photo sensor states.
- the configured EZI IO pin numbers used for those six sensors.
- whether `state.loads` changed.

## Internal Design

Add shared handler methods in `adapter_jibot.py`:

- parse action parameters into typed clamp/PIO commands.
- validate index, state, timeout, and scenario step shape.
- execute clamp commands through `EZIMOTORClient`.
- execute PIO commands through a small adapter-owned PIO controller.
- execute EZI IO sensor reads through the attached `EZIIOClient`.
- keep `state.loads` synchronized with photo sensor reads and publish state on changes.
- update either instant action state or order action state depending on caller.

`instant_actions_accept_procedure` dispatches these action types directly.

Order action execution reuses the same dispatch path currently used for JIBOT command actions, extended to recognize clamp and PIO action types.

## Testing

Add focused offline unit tests with fake EZI motor and fake PIO controller:

- instantAction `clamp` moves to configured clamp position.
- instantAction `unclamp` moves to configured unclamp position.
- missing motor fails with a clear status.
- `pioScenario` success records all steps and finishes.
- `pioScenario` input timeout fails at the correct step and includes last inputs.
- order action and instantAction use the same handler behavior.
- `ezioReadIn` reports 16 EZI IO inputs.
- `photoSensorRead` reports six photo sensors and updates `state.loads`.
- the tray-slot polling loop requests a state publish when sensor values change.

No real hardware, serial port, MQTT broker, or robot is required for tests.
