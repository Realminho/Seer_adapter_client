# Clamp and PIO Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add shared JIBOT VDA5050 instantAction and order action support for clamp controls and PIO scenario execution.

**Architecture:** Extend `adaptor/adapter_jibot.py` with a shared hardware-action executor used by both instant actions and order actions. Clamp execution calls the existing async `EZIMOTORClient`; PIO execution uses an adapter-owned PIO client factory so tests can inject a fake and production can create `PIOMaster`.
EZI IO sensor reads use the attached `EZIIOClient`; photo sensor reads update `state.loads` and trigger immediate state publication when values change.

**Tech Stack:** Python 3, `unittest`, existing VDA5050 v3 message models, existing `EZIMOTORClient` and `PIOMaster`.

---

## Files

- Modify `adaptor/adapter_jibot.py`: add clamp/PIO action detection, parsing, execution, and status updates.
- Modify `adaptor/tests/test_adapter_jibot_v3_order.py`: add fake hardware clients and offline tests.

## Task 1: Clamp Instant Actions

- [ ] **Step 1: Add failing tests**

Add fake motor and tests to `adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
class FakeClampMotor:
    def __init__(self) -> None:
        self.calls = []

    async def servo_enable(self, enable=True):
        self.calls.append(("servo_enable", enable))
        return {"ok": True}

    async def move_single_axis_abs_pos(self, position, speed):
        self.calls.append(("move_single_axis_abs_pos", position, speed))
        return {"ok": True}

    async def move_stop(self):
        self.calls.append(("move_stop",))
        return {"ok": True}
```

Add tests:

```python
def make_instant_action(action_type, action_id="ia-hw", params=None):
    return InstantActions.from_dict({
        "headerId": 44,
        "timestamp": "2026-06-19T00:00:00.000Z",
        "version": "3.0.0",
        "manufacturer": "jibot",
        "serialNumber": "HN-SH6-TR-001",
        "actions": [{
            "actionType": action_type,
            "actionId": action_id,
            "blockingType": "HARD",
            "actionParameters": [
                {"key": key, "value": value}
                for key, value in (params or {}).items()
            ],
        }],
    })

def test_clamp_instant_action_moves_to_position(self):
    adapter = Adapter()
    adapter.state = adapter.initialize_state()
    motor = FakeClampMotor()
    adapter.set_ezi_motor(motor)

    adapter.instant_actions_accept_procedure(
        make_instant_action("clamp", params={"position": 1234, "speed": 55})
    )
    asyncio.run(asyncio.sleep(0))

    self.assertIn(("move_single_axis_abs_pos", 1234, 55), motor.calls)
```

- [ ] **Step 2: Run test to confirm failure**

Run:

```bash
python -m unittest adaptor.tests.test_adapter_jibot_v3_order.TestAdapterJibotV3Order.test_clamp_instant_action_moves_to_position -v
```

Expected: fail because `clamp` is unsupported.

- [ ] **Step 3: Implement clamp dispatch**

In `adapter_jibot.py`, add helpers:

```python
_CLAMP_ACTION_TYPES = {"clamp", "unclamp", "clampTeach", "clampOn", "clampOff", "clampStop"}

def _is_clamp_action(self, action: Any) -> bool:
    return action.action_type in self._CLAMP_ACTION_TYPES
```

Add async executor:

```python
async def _execute_clamp_action(self, action: Any) -> str:
    if self._ezi_motor is None:
        raise ValueError("EZI clamp motor is not initialized")
    params = self._action_params(action)
    speed = int(params.get("speed", self.config.ezi_config.motor_speed))
    offset = int(self.config.ezi_config.origin_encoder_offset)
    if action.action_type == "clampOn":
        await self._ezi_motor.servo_enable(True)
        return "clampOn finished: servo enabled"
    if action.action_type == "clampOff":
        await self._ezi_motor.servo_enable(False)
        return "clampOff finished: servo disabled"
    if action.action_type == "clampStop":
        await self._ezi_motor.move_stop()
        return "clampStop finished: motor stopped"
    if action.action_type == "clamp":
        position = int(params.get("position", offset))
        await self._ezi_motor.move_single_axis_abs_pos(position, speed)
        return f"clamp finished: position={position} speed={speed}"
    if action.action_type == "unclamp":
        position = int(params.get("position", -offset))
        await self._ezi_motor.move_single_axis_abs_pos(position, speed)
        return f"unclamp finished: position={position} speed={speed}"
    if action.action_type == "clampTeach":
        result = await self._ezi_motor.intialized_open_close_encoder_position(offset)
        return f"clampTeach finished: {result}"
    raise ValueError(f"Unsupported clamp action: {action.action_type}")
```

- [ ] **Step 4: Verify tests pass**

Run the new clamp tests and confirm PASS.

## Task 2: PIO Scenario

- [ ] **Step 1: Add fake PIO and failing tests**

Add a fake PIO client with `connect`, `send_bc`, `send_raw`, `monitor_data`, and `close`. Add tests for successful `pioScenario` and input timeout.

- [ ] **Step 2: Implement PIO client wiring**

Add adapter attributes `_pio_client`, `_pio_client_factory`, and methods `set_pio_client`, `set_pio_client_factory`, `_get_pio_client`.

- [ ] **Step 3: Implement PIO parser/executor**

Implement `_execute_pio_action`, `_pio_init`, `_pio_read_inputs`, `_pio_write_output`, `_execute_pio_scenario`, and `_summarize_pio_result`.

PIO input parsing accepts response text containing eight `0`/`1` values and records state as `on`/`off`.

- [ ] **Step 4: Verify PIO tests pass**

Run the new PIO tests and confirm PASS.

## Task 3: Order Actions Share The Same Handlers

- [ ] **Step 1: Add failing order action test**

Create an order with a node action `clamp` and verify the same fake motor method is called and the action state finishes.

- [ ] **Step 2: Route order actions**

In `_execute_order_action`, before the JIBOT command check, detect clamp/PIO action types and call the shared async hardware executor.

- [ ] **Step 3: Verify order tests pass**

Run:

```bash
python -m unittest adaptor.tests.test_adapter_jibot_v3_order -v
```

Expected: PASS.

## Task 4: EZI IO Sensor Actions

- [ ] **Step 1: Add failing tests**

Add fake EZI IO tests in `adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
class FakeEziIo:
    def __init__(self, input_frames=None) -> None:
        self.input_frames = list(input_frames or [[0] * 16])

    async def get_input(self):
        inputs = self.input_frames.pop(0) if len(self.input_frames) > 1 else self.input_frames[0]
        return {"comm_status": 0, "input_raw": 0, "latch_raw": 0, "inputs": inputs, "latches": [0] * 16}
```

Tests:

- `photoSensorRead` updates `state.loads` from `ezi.tray_slot_pin`.
- `photoSensorRead` reports six photo sensor states.
- `ezioReadIn` reports all 16 EZI IO input bits.
- `manage_tray_slot()` calls `request_state_publish("photo sensor changed")` when sensor values change.

- [ ] **Step 2: Implement EZI IO action support**

Add action types:

```python
_EZIO_ACTION_TYPES = {"ezioReadIn", "photoSensorRead"}
```

Implement `_execute_ezio_action()`, `_read_ezio_input_bits()`, `_update_loads_from_photo_sensor_inputs()`, and summary helpers.

- [ ] **Step 3: Route actions and update factsheet**

Dispatch `ezioReadIn` and `photoSensorRead` from `instant_actions_accept_procedure()`, order action hardware routing, and `SUPPORTED_INSTANT_ACTIONS`.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
python -m unittest adaptor.tests.test_adapter_jibot_v3_order -v
```

Expected: PASS.

## Task 5: Final Verification

- [ ] Run focused unit tests:

```bash
python -m unittest adaptor.tests.test_adapter_jibot_v3_order -v
```

- [ ] Run formatting/syntax check by compiling changed files:

```bash
python -m py_compile adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
```

- [ ] Commit implementation:

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py docs/superpowers/plans/2026-06-19-clamp-pio-actions.md
git commit -m "feat: add clamp and pio actions"
```
