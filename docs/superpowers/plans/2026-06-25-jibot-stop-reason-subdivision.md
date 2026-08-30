# JIBOT motor-off Stop-Reason Subdivision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Subdivide the single `motor_flag==0 → EMERGENCY` mapping into real stop reasons (MANUAL/EMERGENCY/PROTECTIVE_STOP/BUMPER/MOTOR_FAULT) using `/jrobot_status` safety fields, surface them over MQTT, consume them in the fabris-equipments AMR v3 eq, and add a `disableMotor` instant action (verify `enableMotor`).

**Architecture:** The on-robot `bms_ros_listener` (already subscribing `/jrobot_status`) caches the firmware safety/motor fields on the vehicle. The adapter classifies a `stopReason` token and uses it to drive the existing VDA5050 `safetyState.eStop`, `operatingMode`, `fieldViolation` and `errors` (real fix, with a freshness fallback to the legacy `motor_flag` behaviour). A new additive `JIBOT_SAFETY` information block carries the raw fields + token to the eq, which declares them as `@EquipmentStatus` and reuses its existing MANUAL/EMERGENCY/BLOCKED/ERROR derivations.

**Tech Stack:** Python 3.11+ (adapter, unittest/pytest), TypeScript (fabris-equipments eq, jest), ROS1 `rostopic` (child process), VDA5050 v3 MQTT.

**Spec:** `docs/superpowers/specs/2026-06-25-jibot-stop-reason-subdivision-design.md`

## Global Constraints

- stopReason token strings are a cross-repo contract; they MUST be identical in adapter and eq. Canonical set (exact strings): `NONE`, `MANUAL`, `EMERGENCY`, `PROTECTIVE_STOP`, `BUMPER`, `MOTOR_FAULT`.
- Classification keys off `system_status` TEXT first (polarity-safe), flags second. `bumpe_stop` polarity is NOT trusted for classification.
- The subdivision is only active where the ROS listener runs (on-robot). When safety data is stale/absent, the adapter MUST fall back to the legacy `EStop.AUTOACK if motor_flag==0` behaviour. dev/sim must be unaffected.
- The `JIBOT_SAFETY` info block is published EVERY cycle; stale/absent → emit empty strings (never omit the block — omission lets the eq retain a stale value).
- Adapter venv must be Python >= 3.11. Follow existing patterns (mirror `set_bms`/`clear_bms`, `_refresh_*_errors`, existing `InstantAction` entries).
- Tests use the existing adapter test layout (`adaptor/tests/`, import modules bare e.g. `from bms_ros_listener import ...`, `from jibot_client import JIBOT`).

---

## Part A — Adapter (unified-amr-adaptor)

### Adapter test harness (used by A4–A7)

The real harness is `class AdapterV3OrderTest(unittest.TestCase)` in
`adaptor/tests/test_adapter_jibot_v3_order.py`: `_make_adapter()` is just
`Adapter(); adapter.set_vehicle(FakeVehicle()); return adapter`, and `adapter.state`
is `None` until the publish loop runs (helper `_start_state` spins it up). `FakeVehicle`
(line 116) has `_motor_flag=1`, `_status="Stopped"` and accepts arbitrary attrs.

To keep new test files self-contained (no inherited-test re-runs), **copy this helper
module verbatim into each new adapter test that needs a live `adapter.state`**, created
as `adaptor/tests/_stop_reason_harness.py`:

```python
import asyncio

from adapter_jibot import Adapter
from tests.test_adapter_jibot_v3_order import FakeVehicle


def make_adapter():
    adapter = Adapter()
    adapter.set_vehicle(FakeVehicle())
    return adapter


async def start_state(adapter):
    """Spin the publish loop until adapter.state exists; return the task to cancel."""
    adapter._loop = asyncio.get_running_loop()
    task = asyncio.create_task(adapter.publish_state("state", interval_sec=0.02))
    for _ in range(200):
        if adapter.state is not None:
            break
        await asyncio.sleep(0.01)
    assert adapter.state is not None
    return task
```

Create this file as the first step of Task A4 (the first test that imports it).

### Task A1: Vehicle robot-safety cache

**Files:**
- Modify: `jibot-client/src/jibot_client/client.py` (`JIBOT.__init__` ~230-236; add methods near `set_bms`/`clear_bms` ~1139-1154)
- Test: `adaptor/tests/test_jibot_client_robot_safety.py` (create)

**Interfaces:**
- Produces: `JIBOT._robot_safety: dict`, `JIBOT._robot_safety_last_update: float`, `JIBOT.set_robot_safety(safety: dict) -> None`, `JIBOT.clear_robot_safety() -> None`.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_jibot_client_robot_safety.py`:

```python
import time
import unittest

from jibot_client import JIBOT


class RobotSafetyCacheTest(unittest.TestCase):
    def _vehicle(self):
        return JIBOT(robot_ip="127.0.0.1", robot_port=7273)

    def test_defaults_empty(self):
        v = self._vehicle()
        self.assertEqual(v._robot_safety, {})
        self.assertEqual(v._robot_safety_last_update, 0.0)

    def test_set_caches_copy_and_stamps_time(self):
        v = self._vehicle()
        src = {"system_status": "Press ON to Enable.", "motor_enable": "0"}
        before = time.monotonic()
        v.set_robot_safety(src)
        self.assertEqual(v._robot_safety["system_status"], "Press ON to Enable.")
        self.assertGreaterEqual(v._robot_safety_last_update, before)
        src["motor_enable"] = "1"  # mutating source must not affect cache
        self.assertEqual(v._robot_safety["motor_enable"], "0")

    def test_clear_empties_dict(self):
        v = self._vehicle()
        v.set_robot_safety({"hmi_estop": "1"})
        v.clear_robot_safety()
        self.assertEqual(v._robot_safety, {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_jibot_client_robot_safety.py -v`
Expected: FAIL (`AttributeError: 'JIBOT' object has no attribute '_robot_safety'`)

- [ ] **Step 3: Add state init**

In `jibot-client/src/jibot_client/client.py` `__init__`, immediately after the `self._bms_last_update = 0.0` line (~236) add:

```python
        # Latest /jrobot_status safety/motor fields from bms_ros_listener
        # (system_status, motor_enable, hmi_estop, ...); monotonic stamp for
        # staleness. Empty until the ROS listener feeds it (on-robot only).
        self._robot_safety = {}
        self._robot_safety_last_update = 0.0
```

- [ ] **Step 4: Add methods**

In the same file, immediately after the `clear_bms` method (~1154), add:

```python
    def set_robot_safety(self, safety):
        """Cache the latest /jrobot_status safety/motor fields (dict of str).

        Fed by bms_ros_listener alongside set_bms. Stored as a copy so the
        listener can reuse its row dict. Stamped for staleness checks.
        """
        self._robot_safety = dict(safety)
        self._robot_safety_last_update = time.monotonic()

    def clear_robot_safety(self):
        """Drop cached safety fields (stream stale or down)."""
        self._robot_safety = {}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_jibot_client_robot_safety.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add jibot-client/src/jibot_client/client.py adaptor/tests/test_jibot_client_robot_safety.py
git commit -m "feat: cache /jrobot_status safety fields on JIBOT vehicle"
```

---

### Task A2: Generalized column finder + safety row parse

**Files:**
- Modify: `adaptor/bms_ros_listener.py` (add `find_robot_status_columns`, `parse_robot_safety_row`; keep `find_bms_columns`/`parse_bms_row`)
- Test: `adaptor/tests/test_bms_ros_listener_safety_parse.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `find_robot_status_columns(header: str) -> dict[str, int]` (suffix→index for the safety fields); `parse_robot_safety_row(row: str, columns: dict[str, int]) -> dict[str, str]` (field→value string, csv-parsed).

`SAFETY_FIELDS` constant (exact suffixes): `("system_status", "motor_enable", "motor_enable_status", "hmi_estop", "bumpe_stop", "pc_estop", "motor_error", "pc_enable", "charge")`.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_bms_ros_listener_safety_parse.py`:

```python
import unittest

from bms_ros_listener import find_robot_status_columns, parse_robot_safety_row

HEADER = (
    "%time,field.system_status,field.motor_enable,field.hmi_estop,"
    "field.bumpe_stop,field.pc_estop,field.motor_error,field.pc_enable,"
    "field.bms_voltage,field.bms_current,field.charge"
)


class SafetyColumnsTest(unittest.TestCase):
    def test_finds_safety_columns_by_suffix(self):
        cols = find_robot_status_columns(HEADER)
        self.assertEqual(cols["system_status"], 1)
        self.assertEqual(cols["hmi_estop"], 3)
        self.assertEqual(cols["motor_error"], 6)
        self.assertEqual(cols["charge"], 10)
        self.assertNotIn("bms_voltage", cols)  # only safety fields

    def test_parse_extracts_values(self):
        cols = find_robot_status_columns(HEADER)
        row = '1700000000000000000,Press ON to Enable.,0,0,1,0,0,1,52.9,-0.8,0'
        out = parse_robot_safety_row(row, cols)
        self.assertEqual(out["system_status"], "Press ON to Enable.")
        self.assertEqual(out["motor_enable"], "0")
        self.assertEqual(out["bumpe_stop"], "1")
        self.assertEqual(out["hmi_estop"], "0")

    def test_parse_handles_quoted_comma_in_status(self):
        cols = find_robot_status_columns(HEADER)
        row = '1700000000000000000,"Stopped, waiting",1,0,0,0,0,1,52.9,-0.8,0'
        out = parse_robot_safety_row(row, cols)
        self.assertEqual(out["system_status"], "Stopped, waiting")

    def test_parse_short_row_returns_empty(self):
        cols = find_robot_status_columns(HEADER)
        self.assertEqual(parse_robot_safety_row("1,2", cols), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_safety_parse.py -v`
Expected: FAIL (`ImportError: cannot import name 'find_robot_status_columns'`)

- [ ] **Step 3: Implement in `bms_ros_listener.py`**

Add `import csv` to the top imports (next to `import asyncio`). After `parse_bms_row` (~line 40) add:

```python
SAFETY_FIELDS = (
    "system_status",
    "motor_enable",
    "motor_enable_status",
    "hmi_estop",
    "bumpe_stop",
    "pc_estop",
    "motor_error",
    "pc_enable",
    "charge",
)


def find_robot_status_columns(header):
    """Map a `rostopic echo -p` CSV header to {safety_field: column index}.

    Matches by suffix (column names look like `field.hmi_estop`) so a prefix or
    nesting change does not break it. Only SAFETY_FIELDS are returned.
    """
    columns = {}
    names = next(csv.reader([header]))
    for i, name in enumerate(n.strip() for n in names):
        for field in SAFETY_FIELDS:
            if name.endswith(field):
                columns[field] = i
    return columns


def parse_robot_safety_row(row, columns):
    """Extract {safety_field: value-string} from one CSV data row.

    Uses the csv module so a comma/quote inside system_status is handled.
    Returns {} when the row is too short for the mapped columns.
    """
    if not columns:
        return {}
    parts = next(csv.reader([row]))
    if len(parts) <= max(columns.values()):
        return {}
    return {field: parts[idx].strip() for field, idx in columns.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_safety_parse.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the existing parse test for no regression**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_parse.py -v`
Expected: PASS (unchanged)

- [ ] **Step 6: Commit**

```bash
git add adaptor/bms_ros_listener.py adaptor/tests/test_bms_ros_listener_safety_parse.py
git commit -m "feat: parse /jrobot_status safety columns in bms listener"
```

---

### Task A3: Listener feeds safety cache

**Files:**
- Modify: `adaptor/bms_ros_listener.py` (`_consume`)
- Test: `adaptor/tests/test_bms_ros_listener_safety_consume.py` (create)

**Interfaces:**
- Consumes: `find_robot_status_columns`, `parse_robot_safety_row` (Task A2); `vehicle.set_robot_safety`/`clear_robot_safety` (Task A1).
- Produces: `_consume` now also calls `set_robot_safety` per data row and `clear_robot_safety` on timeout/EOF.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_bms_ros_listener_safety_consume.py`:

```python
import asyncio
import unittest

from bms_ros_listener import BmsRosListener


class FakeVehicle:
    def __init__(self):
        self.safety = None
        self.cleared = 0

    def set_bms(self, v, c):
        pass

    def clear_bms(self):
        pass

    def set_robot_safety(self, safety):
        self.safety = safety

    def clear_robot_safety(self):
        self.cleared += 1


class FakeCfg:
    stale_after_sec = 30.0


class FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0).encode("utf-8")
        return b""  # EOF


HEADER = (
    "%time,field.system_status,field.motor_enable,field.hmi_estop,"
    "field.bumpe_stop,field.pc_estop,field.motor_error,field.pc_enable,"
    "field.bms_voltage,field.bms_current,field.charge\n"
)


class SafetyConsumeTest(unittest.TestCase):
    def test_consume_sets_robot_safety(self):
        veh = FakeVehicle()
        listener = BmsRosListener(veh, FakeCfg())
        row = "1,Press ON to Enable.,0,0,1,0,0,1,52.9,-0.8,0\n"
        stdout = FakeStdout([HEADER, row])
        asyncio.run(listener._consume(stdout))
        self.assertIsNotNone(veh.safety)
        self.assertEqual(veh.safety["system_status"], "Press ON to Enable.")
        self.assertEqual(veh.safety["hmi_estop"], "0")
        self.assertEqual(veh.cleared, 1)  # EOF triggers clear


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_safety_consume.py -v`
Expected: FAIL (`veh.safety` is None — `_consume` does not call `set_robot_safety`)

- [ ] **Step 3: Wire `_consume`**

In `bms_ros_listener.py` `_consume`, change the header/row handling so safety columns are tracked and fed. Replace the body of `_consume` (currently lines ~55-78) with:

```python
    async def _consume(self, stdout):
        voltage_idx = current_idx = None
        safety_columns = {}
        while True:
            try:
                raw = await asyncio.wait_for(
                    stdout.readline(), timeout=self._cfg.stale_after_sec
                )
            except asyncio.TimeoutError:
                self._vehicle.clear_bms()  # no fresh row within the window
                self._vehicle.clear_robot_safety()
                continue
            if not raw:
                self._vehicle.clear_robot_safety()
                return  # EOF: child exited
            line = (
                raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray))
                else str(raw)
            ).strip()
            if not line:
                continue
            if "bms_voltage" in line:  # CSV header (re)appears on (re)start
                voltage_idx, current_idx = find_bms_columns(line)
                safety_columns = find_robot_status_columns(line)
                continue
            parsed = parse_bms_row(line, voltage_idx, current_idx)
            if parsed is not None:
                self._vehicle.set_bms(parsed[0], parsed[1])
            safety = parse_robot_safety_row(line, safety_columns)
            if safety:
                self._vehicle.set_robot_safety(safety)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_safety_consume.py tests/test_bms_ros_listener_consume.py -v`
Expected: PASS (new test passes, existing consume test unchanged)

- [ ] **Step 5: Commit**

```bash
git add adaptor/bms_ros_listener.py adaptor/tests/test_bms_ros_listener_safety_consume.py
git commit -m "feat: feed robot safety cache from bms listener consume loop"
```

---

### Task A4: stopReason derivation helper

**Files:**
- Modify: `adaptor/adapter_jibot.py` (add `_jibot_safety_if_fresh`, `_derive_jibot_stop_reason` near `_is_jibot_lost` ~908)
- Test: `adaptor/tests/test_jibot_stop_reason.py` (create)

**Interfaces:**
- Consumes: `vehicle._robot_safety`, `vehicle._robot_safety_last_update` (Task A1); `self.config.bms_ros.stale_after_sec`.
- Produces: `Adapter._jibot_safety_if_fresh() -> Optional[dict]`; `Adapter._derive_jibot_stop_reason() -> Optional[str]` returning one of the canonical tokens or `None` (stale/absent).

- [ ] **Step 0: Create the shared harness module**

Create `adaptor/tests/_stop_reason_harness.py` exactly as given in the "Adapter test harness" section above.

- [ ] **Step 1: Write the failing test**

`_derive_jibot_stop_reason` reads only `vehicle._robot_safety` + `config.bms_ros`, so it needs
no live `adapter.state`; use the sync `make_adapter()`.

Create `adaptor/tests/test_jibot_stop_reason.py`:

```python
import time
import unittest

from tests._stop_reason_harness import make_adapter


class StopReasonTest(unittest.TestCase):
    def _adapter_with_safety(self, safety, age_sec=0.0):
        adapter = make_adapter()
        v = adapter._vehicle
        v._robot_safety = safety
        v._robot_safety_last_update = time.monotonic() - age_sec
        return adapter

    def test_press_on_to_enable_is_manual(self):
        a = self._adapter_with_safety(
            {"system_status": "Press ON to Enable.", "motor_enable": "0", "hmi_estop": "0"}
        )
        self.assertEqual(a._derive_jibot_stop_reason(), "MANUAL")

    def test_estop_pressed_is_emergency(self):
        a = self._adapter_with_safety(
            {"system_status": "Estop Pressed!", "hmi_estop": "1", "motor_enable": "0"}
        )
        self.assertEqual(a._derive_jibot_stop_reason(), "EMERGENCY")

    def test_pc_estop_is_protective_stop(self):
        a = self._adapter_with_safety({"system_status": "Enter Estop!", "pc_estop": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "PROTECTIVE_STOP")

    def test_motor_error_is_motor_fault(self):
        a = self._adapter_with_safety({"system_status": "Normal...", "motor_error": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "MOTOR_FAULT")

    def test_bumper_text_is_bumper(self):
        a = self._adapter_with_safety({"system_status": "bumper Trigger!", "bumpe_stop": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "BUMPER")

    def test_normal_is_none(self):
        a = self._adapter_with_safety({"system_status": "Normal...", "motor_enable": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "NONE")

    def test_emergency_wins_over_motor_error(self):
        a = self._adapter_with_safety({"system_status": "Estop Pressed!", "hmi_estop": "1", "motor_error": "1"})
        self.assertEqual(a._derive_jibot_stop_reason(), "EMERGENCY")

    def test_stale_returns_none(self):
        a = self._adapter_with_safety(
            {"system_status": "Press ON to Enable.", "motor_enable": "0"}, age_sec=999.0
        )
        self.assertIsNone(a._derive_jibot_stop_reason())

    def test_absent_returns_none(self):
        adapter = make_adapter()
        adapter._vehicle._robot_safety = {}
        adapter._vehicle._robot_safety_last_update = 0.0
        self.assertIsNone(adapter._derive_jibot_stop_reason())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_jibot_stop_reason.py -v`
Expected: FAIL (`AttributeError: ... no attribute '_derive_jibot_stop_reason'`)

- [ ] **Step 3: Implement helpers in `adapter_jibot.py`**

After `_is_jibot_lost` (~912) add:

```python
    def _jibot_safety_if_fresh(self):
        """Return cached /jrobot_status safety dict if fresh, else None.

        None means the subdivision data is unavailable (listener down, sim, or
        stale) and callers must fall back to the legacy motor_flag behaviour.
        """
        v = self._vehicle
        if v is None:
            return None
        safety = getattr(v, "_robot_safety", None)
        last = getattr(v, "_robot_safety_last_update", 0.0)
        if not safety or last <= 0.0:
            return None
        if (time.monotonic() - last) > self.config.bms_ros.stale_after_sec:
            return None
        return safety

    def _derive_jibot_stop_reason(self):
        """Classify the robot stop/disable reason from /jrobot_status fields.

        Returns one of NONE/MANUAL/EMERGENCY/PROTECTIVE_STOP/BUMPER/MOTOR_FAULT,
        or None when safety data is stale/absent. Keys off the firmware
        system_status TEXT first (polarity-safe), then boolean flags. Priority:
        EMERGENCY > PROTECTIVE_STOP > MOTOR_FAULT > BUMPER > MANUAL > NONE.
        """
        safety = self._jibot_safety_if_fresh()
        if safety is None:
            return None
        status = str(safety.get("system_status", "") or "").strip().lower()

        def on(name):
            return str(safety.get(name, "") or "").strip() in ("1", "true", "True")

        if "estop pressed" in status or on("hmi_estop"):
            return "EMERGENCY"
        if "enter estop" in status or on("pc_estop"):
            return "PROTECTIVE_STOP"
        if on("motor_error"):
            return "MOTOR_FAULT"
        if "bumper" in status:
            return "BUMPER"
        motor_enable = str(safety.get("motor_enable", "") or "").strip()
        if "press on to enable" in status or motor_enable in ("0", "false", "False"):
            return "MANUAL"
        return "NONE"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_jibot_stop_reason.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_jibot_stop_reason.py
git commit -m "feat: derive JIBOT stop reason from /jrobot_status safety fields"
```

---

### Task A5: Apply stopReason to safety/operatingMode/errors (real fix)

**Files:**
- Modify: `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` (add `ErrorType.JIBOT_MOTOR_FAULT`)
- Modify: `adaptor/adapter_jibot.py` (state loop ~555-565; `_derive_operating_mode` ~882; add `_refresh_jibot_motor_fault_errors`)
- Test: `adaptor/tests/test_jibot_stop_reason_apply.py` (create)

**Interfaces:**
- Consumes: `_derive_jibot_stop_reason` (Task A4).
- Produces: state-loop side effects — `state.safety_state.e_stop`, `state.safety_state.field_violation`, `state.driving`, `state.operating_mode`, `state.errors` reflect the stop reason; `_derive_operating_mode()` returns `OperatingMode.MANUAL` when reason==MANUAL; legacy fallback preserved when reason is None.

- [ ] **Step 1: Add the error type (no test needed yet — enum constant)**

In `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py`, in `class ErrorType`, after the `JIBOT_DOCK_FAILED` line add:

```python
    JIBOT_MOTOR_FAULT = "JIBOT_MOTOR_FAULT"  # Drive motor reported a fault (motor_error) via /jrobot_status
```

- [ ] **Step 2: Write the failing test**

`_apply_jibot_stop_reason` runs inside the publish loop and needs `adapter.state`; use the
async `start_state` harness and assert on the live state after one cycle. Create
`adaptor/tests/test_jibot_stop_reason_apply.py`:

```python
import asyncio
import time
import unittest

from protocol.vda_2_0_0.vda5050_2_0_0_state import EStop, ErrorType, OperatingMode
from tests._stop_reason_harness import make_adapter, start_state


def _run_with_safety(safety, motor_flag=0):
    """Start the publish loop with the given /jrobot_status safety dict cached,
    let one cycle apply it, and return the live state."""
    async def scenario():
        adapter = make_adapter()
        v = adapter._vehicle
        v._motor_flag = motor_flag
        v._robot_safety = safety
        v._robot_safety_last_update = time.monotonic()
        task = await start_state(adapter)
        try:
            await asyncio.sleep(0.1)
            return {
                "e_stop": adapter.state.safety_state.e_stop,
                "field_violation": adapter.state.safety_state.field_violation,
                "operating_mode": adapter.state.operating_mode,
                "error_types": [e.error_type for e in adapter.state.errors],
            }
        finally:
            task.cancel()
    return asyncio.run(scenario())


class StopReasonApplyTest(unittest.TestCase):
    def test_manual_no_estop_and_operating_mode_manual(self):
        s = _run_with_safety({"system_status": "Press ON to Enable.", "motor_enable": "0", "hmi_estop": "0"})
        self.assertEqual(s["e_stop"], EStop.NONE)
        self.assertEqual(s["operating_mode"], OperatingMode.MANUAL)

    def test_emergency_sets_estop_manual(self):
        s = _run_with_safety({"system_status": "Estop Pressed!", "hmi_estop": "1"})
        self.assertEqual(s["e_stop"], EStop.MANUAL)

    def test_protective_stop_sets_estop_remote(self):
        s = _run_with_safety({"system_status": "Enter Estop!", "pc_estop": "1"})
        self.assertEqual(s["e_stop"], EStop.REMOTE)

    def test_bumper_sets_field_violation(self):
        s = _run_with_safety({"system_status": "bumper Trigger!"})
        self.assertTrue(s["field_violation"])

    def test_motor_fault_adds_fatal_error(self):
        s = _run_with_safety({"system_status": "Normal...", "motor_error": "1"})
        self.assertIn(ErrorType.JIBOT_MOTOR_FAULT, s["error_types"])

    def test_no_motor_fault_when_clear(self):
        s = _run_with_safety({"system_status": "Normal...", "motor_error": "0", "motor_enable": "1"})
        self.assertNotIn(ErrorType.JIBOT_MOTOR_FAULT, s["error_types"])

    def test_stale_falls_back_to_legacy_autoack(self):
        s = _run_with_safety({}, motor_flag=0)  # no fresh safety → legacy motor_flag path
        self.assertEqual(s["e_stop"], EStop.AUTOACK)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_jibot_stop_reason_apply.py -v`
Expected: FAIL (`no attribute '_apply_jibot_stop_reason'`)

- [ ] **Step 4: Implement `_apply_jibot_stop_reason` + motor-fault refresh + operatingMode**

In `adapter_jibot.py`, add a method (place near `_refresh_jibot_connection_errors` ~1539):

```python
    def _refresh_jibot_motor_fault_errors(self, active):
        """Set/clear a FATAL JIBOT_MOTOR_FAULT state error each cycle."""
        if self.state is None:
            return
        self.state.errors = [
            e for e in self.state.errors
            if getattr(e, "error_type", None) != ErrorType.JIBOT_MOTOR_FAULT
        ]
        if active:
            self.state.errors.append(
                Error(
                    error_type=ErrorType.JIBOT_MOTOR_FAULT,
                    error_level=ErrorLevel.FATAL,
                    error_references=[ErrorReference("reason", "motorError")],
                    error_description="JIBOT reported a drive motor fault (motor_error)",
                )
            )

    def _apply_jibot_stop_reason(self):
        """Drive safetyState/operatingMode/errors from the /jrobot_status stop
        reason. Falls back to the legacy motor_flag→AUTOACK e-stop when the
        safety data is stale/absent (reason is None)."""
        if self.state is None:
            return
        reason = self._derive_jibot_stop_reason()
        self._jibot_stop_reason = reason  # cached for operatingMode + info block

        if reason is None:  # legacy fallback (no subdivision data)
            self.state.safety_state.e_stop = (
                EStop.AUTOACK if self._vehicle._motor_flag == 0 else EStop.NONE
            )
            self._refresh_jibot_motor_fault_errors(False)
            return

        if reason == "EMERGENCY":
            self.state.safety_state.e_stop = EStop.MANUAL
        elif reason == "PROTECTIVE_STOP":
            self.state.safety_state.e_stop = EStop.REMOTE
        else:
            self.state.safety_state.e_stop = EStop.NONE

        if reason == "BUMPER":
            self.state.safety_state.field_violation = True
            self.state.driving = False

        self._refresh_jibot_motor_fault_errors(reason == "MOTOR_FAULT")
```

Add `self._jibot_stop_reason = None` to `__init__` (near other instance state).

- [ ] **Step 5: Replace the legacy e_stop line in the state loop**

In the state loop (~555-558), replace:

```python
                # safety_state
                self.state.safety_state.e_stop = (
                    EStop.AUTOACK if self._vehicle._motor_flag == 0 else EStop.NONE
                )

                # JIBOT brake ...
                self.state.safety_state.field_violation = self._is_jibot_obstacle_wait()
```

with (note: field_violation keeps obstacle-wait, then `_apply_jibot_stop_reason` may OR in bumper):

```python
                # JIBOT brake (obstacle wait) → field_violation; bumper may add to it.
                self.state.safety_state.field_violation = self._is_jibot_obstacle_wait()

                # safety_state: subdivide motor-off into the real stop reason
                # (e_stop / field_violation / driving / motor-fault errors). Falls
                # back to legacy motor_flag→AUTOACK when /jrobot_status is stale.
                self._apply_jibot_stop_reason()
```

Ensure `_apply_jibot_stop_reason()` runs AFTER `self.state.driving = self._derive_driving()` so the bumper `driving=False` override is not clobbered. Move the `_apply_jibot_stop_reason()` call to just after the `# driving` block (~569), keeping only the `field_violation` assignment in the brake block. Final order in the loop: operating_mode-independent signals → `driving` → `_apply_jibot_stop_reason()`.

- [ ] **Step 6: Update `_derive_operating_mode`**

Replace `_derive_operating_mode` (~882-885) with:

```python
    def _derive_operating_mode(self) -> OperatingMode:
        if self._is_startup_initializing():
            return OperatingMode.STARTUP
        # Motor disabled without a real e-stop (Press ON to Enable) → MANUAL, so
        # the eq reuses its existing operatingMode=MANUAL → AmrState.MANUAL path.
        if getattr(self, "_jibot_stop_reason", None) == "MANUAL":
            return OperatingMode.MANUAL
        return OperatingMode.AUTOMATIC
```

Ensure `self.state.operating_mode = self._derive_operating_mode()` (~565) runs AFTER `_apply_jibot_stop_reason()` set `_jibot_stop_reason`. Move that assignment below the `_apply_jibot_stop_reason()` call.

- [ ] **Step 7: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_jibot_stop_reason_apply.py -v`
Expected: PASS (6 tests)

- [ ] **Step 8: Run the existing motor/emergency regression tests**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -v`
Expected: PASS. NOTE: `test_motor_stop_flag_surfaces_as_v3_emergency_stop` (motor_flag=0, no safety cache) must still expect MANUAL — it hits the stale/None fallback (AUTOACK→v3 MANUAL). If it fails because safety cache is unset, confirm fallback is correct; do NOT weaken the assertion.

- [ ] **Step 9: Commit**

```bash
git add adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py adaptor/adapter_jibot.py adaptor/tests/test_jibot_stop_reason_apply.py
git commit -m "feat: drive eStop/operatingMode/errors from JIBOT stop reason"
```

---

### Task A6: Publish JIBOT_SAFETY information block

**Files:**
- Modify: `adaptor/adapter_jibot.py` (add `_refresh_jibot_safety_information`; call it in the publish cycle next to `_refresh_amr_state_information`)
- Test: `adaptor/tests/test_jibot_safety_info.py` (create)

**Interfaces:**
- Consumes: `_derive_jibot_stop_reason`, `_jibot_safety_if_fresh` (Task A4).
- Produces: an `Information` block with `info_type="JIBOT_SAFETY"` in `state.information`, refs: `stopReason, systemStatus, motorEnable, hmiEstop, bumperEstop, pcEstop, motorError, pcEnable, charge`. Always present; empty strings when stale/absent.

- [ ] **Step 1: Write the failing test**

Integrated style (`start_state` runs the publish loop which now calls
`_refresh_jibot_safety_information`). Create `adaptor/tests/test_jibot_safety_info.py`:

```python
import asyncio
import time
import unittest

from tests._stop_reason_harness import make_adapter, start_state


def _safety_refs(safety, fresh=True):
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._robot_safety = safety
        adapter._vehicle._robot_safety_last_update = time.monotonic() if fresh else 0.0
        task = await start_state(adapter)
        try:
            await asyncio.sleep(0.1)
            blocks = [
                i for i in adapter.state.information
                if getattr(i, "info_type", None) == "JIBOT_SAFETY"
            ]
            assert len(blocks) == 1, f"expected exactly one JIBOT_SAFETY block, got {len(blocks)}"
            return {r.reference_key: r.reference_value for r in blocks[0].info_references}
        finally:
            task.cancel()
    return asyncio.run(scenario())


class JibotSafetyInfoTest(unittest.TestCase):
    def test_block_populated_when_fresh(self):
        refs = _safety_refs({
            "system_status": "Press ON to Enable.", "motor_enable": "0",
            "hmi_estop": "0", "bumpe_stop": "1", "pc_estop": "0",
            "motor_error": "0", "pc_enable": "1", "charge": "0",
        })
        self.assertEqual(refs["stopReason"], "MANUAL")
        self.assertEqual(refs["systemStatus"], "Press ON to Enable.")
        self.assertEqual(refs["bumperEstop"], "1")

    def test_block_empty_strings_when_stale(self):
        refs = _safety_refs({}, fresh=False)
        self.assertEqual(refs["stopReason"], "")
        self.assertEqual(refs["systemStatus"], "")
        self.assertEqual(refs["hmiEstop"], "")
```

NOTE: the `assert len(blocks) == 1` inside `_safety_refs` is the singleton check (the loop
replaces the block each cycle).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_jibot_safety_info.py -v`
Expected: FAIL (`no attribute '_refresh_jibot_safety_information'`)

- [ ] **Step 3: Implement the refresh method**

In `adapter_jibot.py`, after `_refresh_amr_state_information` (~1092) add:

```python
    def _refresh_jibot_safety_information(self) -> None:
        """Publish raw /jrobot_status safety fields + derived stopReason as a
        JIBOT_SAFETY info block. Always present; empty strings when stale/absent
        so the eq never retains a stale value."""
        if self.state is None:
            return
        safety = self._jibot_safety_if_fresh()
        reason = self._derive_jibot_stop_reason()

        def f(name):
            return "" if safety is None else str(safety.get(name, "") or "")

        references = [
            InfoReference("stopReason", reason or ""),
            InfoReference("systemStatus", f("system_status")),
            InfoReference("motorEnable", f("motor_enable")),
            InfoReference("hmiEstop", f("hmi_estop")),
            InfoReference("bumperEstop", f("bumpe_stop")),
            InfoReference("pcEstop", f("pc_estop")),
            InfoReference("motorError", f("motor_error")),
            InfoReference("pcEnable", f("pc_enable")),
            InfoReference("charge", f("charge")),
        ]
        self.state.information = [
            i for i in self.state.information
            if getattr(i, "info_type", None) != "JIBOT_SAFETY"
        ]
        self.state.information.append(
            Information(
                info_type="JIBOT_SAFETY",
                info_level=InfoLevel.INFO,
                info_references=references,
                info_description="JIBOT motor/safety stop reason and raw flags",
            )
        )
```

- [ ] **Step 4: Call it in the publish cycle**

Find where `_refresh_amr_state_information()` is invoked in the publish loop and add `self._refresh_jibot_safety_information()` immediately after it (same cycle, after driving/eStop/errors are set).

Run: `cd adaptor && grep -n "_refresh_amr_state_information()" adaptor/adapter_jibot.py`
Add the new call on the next line at the matching call site(s).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_jibot_safety_info.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_jibot_safety_info.py
git commit -m "feat: publish JIBOT_SAFETY info block with stop reason + raw flags"
```

---

### Task A7: disableMotor instant action

**Files:**
- Modify: `adaptor/core/factsheet.py` (`INSTANT_ACTION_TYPES`)
- Modify: `adaptor/core/registry.py` (InstantAction list ~227)
- Modify: `adaptor/adapter_jibot.py` (dispatch ~3825; add `_handle_disable_motor_instant_action`)
- Test: `adaptor/tests/test_disable_motor_action.py` (create)

**Interfaces:**
- Consumes: `vehicle.disable_motor()` (exists → `um_set_motor(False)`); `config.manual_control.enabled`; `_update_instant_action_status`.
- Produces: `"disableMotor"` registered in both catalogs; `_handle_disable_motor_instant_action(action)`.

- [ ] **Step 1: Register the action type**

In `adaptor/core/factsheet.py` `INSTANT_ACTION_TYPES`, add `"disableMotor",` immediately after `"enableMotor",`.

In `adaptor/core/registry.py`, after `InstantAction("enableMotor", "Enable motor", motion=True),` (~227) add:

```python
    InstantAction("disableMotor", "Disable motor (test)", motion=True),
```

- [ ] **Step 2: Write the failing test**

Dispatch the real way (`InstantActions.from_dict` → `instant_actions_accept_procedure`) and
capture statuses by wrapping `_update_instant_action_status` (terminal states get cleared).
`disable_motor` is patched onto the vehicle instance. Create
`adaptor/tests/test_disable_motor_action.py`:

```python
import asyncio
import unittest

from protocol.vda5050_3_0.messages import InstantActions
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from tests._stop_reason_harness import make_adapter, start_state


def _disable_motor_ia(action_id):
    return InstantActions.from_dict({
        "headerId": 1,
        "timestamp": "2026-06-25T00:00:00.000Z",
        "version": "3.0.0",
        "manufacturer": "jibot",
        "serialNumber": "HN-TEST-001",
        "actions": [{
            "actionType": "disableMotor",
            "actionId": action_id,
            "blockingType": "NONE",
            "actionParameters": [],
        }],
    })


def _dispatch(enabled):
    async def scenario():
        adapter = make_adapter()
        adapter.config.manual_control.enabled = enabled
        calls = []

        async def fake_disable():
            calls.append(True)

        adapter._vehicle.disable_motor = fake_disable
        statuses = []
        orig = adapter._update_instant_action_status

        def capture(action_id, status, **kw):
            statuses.append(status)
            return orig(action_id, status, **kw)

        adapter._update_instant_action_status = capture
        task = await start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(_disable_motor_ia("d1"))
            await asyncio.sleep(0.1)
            return calls, statuses
        finally:
            task.cancel()
    return asyncio.run(scenario())


class DisableMotorTest(unittest.TestCase):
    def test_disable_motor_calls_vehicle_and_finishes(self):
        calls, statuses = _dispatch(enabled=True)
        self.assertEqual(calls, [True])
        self.assertIn(ActionStatus.FINISHED, statuses)

    def test_disable_motor_gated_off_fails(self):
        calls, statuses = _dispatch(enabled=False)
        self.assertEqual(calls, [])
        self.assertIn(ActionStatus.FAILED, statuses)


if __name__ == "__main__":
    unittest.main()
```

NOTE: confirm the dispatch entry method name with
`grep -n "def instant_actions_accept_procedure" adaptor/adapter_jibot.py`; if the project uses a
different public entry for instant actions, call that one instead (the `test_set_map_instant_action.py`
test uses `instant_actions_accept_procedure`).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_disable_motor_action.py -v`
Expected: FAIL (`no attribute '_handle_disable_motor_instant_action'`)

- [ ] **Step 4: Add the dispatch + handler**

In `adapter_jibot.py` dispatch, after the `enableMotor` elif (~3825-3826) add:

```python
            elif action.action_type == "disableMotor":
                self._handle_disable_motor_instant_action(action)
```

Add the handler next to `_handle_enable_motor_instant_action` (~4146), mirroring it:

```python
    def _handle_disable_motor_instant_action(self, action: Any) -> None:
        if not self.config.manual_control.enabled:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="manual control disabled",
            )
            return
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                await self._vehicle.disable_motor()
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FINISHED,
                    result_description="motor disabled",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"disableMotor failed: {exc}",
                )

        self._run_on_adapter_loop(_run)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_disable_motor_action.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Verify enableMotor chain (no code change — documentation check)**

Run: `cd adaptor && grep -n "async def enable_motor\|async def disable_motor\|um_set_motor" jibot-client/src/jibot_client/client.py`
Confirm `enable_motor → um_set_motor(True)` and `disable_motor → um_set_motor(False)` both exist. Record the result in the commit body.

- [ ] **Step 7: Commit**

```bash
git add adaptor/core/factsheet.py adaptor/core/registry.py adaptor/adapter_jibot.py adaptor/tests/test_disable_motor_action.py
git commit -m "feat: add disableMotor instant action (verify enableMotor chain)"
```

---

### Task A8: WebUI disableMotor button

**Files:**
- Modify: `adaptor/web/render.py` (`_EMERGENCY_ACTION_TYPES` ~1112; `_emergency_forms` quick list ~1129)
- Test: `adaptor/tests/test_web_render.py` (add a case)

**Interfaces:**
- Consumes: registry `disableMotor` InstantAction (Task A7).
- Produces: a quick-control button for `disableMotor` rendered when the spec exposes it.

- [ ] **Step 1: Write the failing test**

In `adaptor/tests/test_web_render.py`, add (mirror an existing `_emergency_forms`/quick-control render test; locate one with `grep -n "enableMotor\|_emergency_forms\|cmd-wrap" adaptor/tests/test_web_render.py`):

```python
def test_emergency_forms_includes_disable_motor():
    from web import render
    spec = _spec_with_actions(["manualStop", "enableMotor", "disableMotor"])  # use this file's existing spec builder
    out = render._emergency_forms(spec, "tok")
    assert "disableMotor" in out
```

Use the test file's existing spec/instant-action builder instead of `_spec_with_actions` if named differently.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_web_render.py -k disable_motor -v`
Expected: FAIL (`disableMotor` not in output)

- [ ] **Step 3: Implement**

In `adaptor/web/render.py`:
- Change `_EMERGENCY_ACTION_TYPES` to `("manualStop", "stopCharging", "enableMotor", "disableMotor")`.
- In `_emergency_forms`, change the quick list comprehension iterable from `("stopCharging", "enableMotor")` to `("stopCharging", "enableMotor", "disableMotor")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_web_render.py -k disable_motor -v`
Expected: PASS

- [ ] **Step 5: Run the full web render suite for no regression**

Run: `cd adaptor && python -m pytest tests/test_web_render.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add adaptor/web/render.py adaptor/tests/test_web_render.py
git commit -m "feat: add disableMotor quick-control button to WebUI"
```

---

## Part B — Equipment (fabris-equipments, TypeScript)

> Run `cd /home/lab2m-llm1/workspaces/fabris-equipments`. Tests use the root jest config (per project convention). Before each task, open the target files and the nearest existing `jibot*` `@EquipmentStatus` declarations + morphism mappings to mirror their exact style.

### Task B1: Declare JIBOT_SAFETY status vars + morphism mappings

**Files:**
- Modify: `packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts` (info-type consts ~26-29; morphism schema ~85-146; `@EquipmentStatus` declarations next to existing `jibotMode`/`jibotStatus`)
- Test: `packages/amr/src/lib/amr/amr-l2m-v3/*.spec.ts` (add to the existing v3-base spec, or create `amr-l2m-v3-jibot-safety.spec.ts`)

**Interfaces:**
- Consumes: MQTT `information[]` block `infoType="JIBOT_SAFETY"` with refs from Task A6; existing `getInformationReferenceValue(state, infoType, key)` helper.
- Produces: `@EquipmentStatus` props `jibotStopReason, jibotSystemStatus, motorEnable, hmiEstop, bumperEstop, pcEstop, motorError` populated from the block.

- [ ] **Step 1: Locate the exact insertion points**

```bash
cd /home/lab2m-llm1/workspaces/fabris-equipments
grep -n "JIBOT_STATUS_INFO_TYPE\|getInformationReferenceValue\|jibotMode\|jibotStatus" packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts
```
Note the const block, the morphism schema entries for `jibotMode`/`jibotStatus`, and where the `@EquipmentStatus jibotMode` property is declared.

- [ ] **Step 2: Write the failing test**

Create/extend a spec that builds a minimal Vda5050 state with a JIBOT_SAFETY information block and asserts the morphism maps each ref. Mirror the existing v3-base spec's state-construction + `applyVda5050State` (or the morphism function) usage:

```typescript
// amr-l2m-v3-jibot-safety.spec.ts (adjust imports/harness to match sibling specs)
import { AmrL2mV3Equipment } from './amr-l2m-v3.equipment';

function stateWithSafety(refs: Record<string, string>) {
  return {
    information: [
      {
        infoType: 'JIBOT_SAFETY',
        infoReferences: Object.entries(refs).map(([referenceKey, referenceValue]) => ({
          referenceKey,
          referenceValue,
        })),
      },
    ],
  } as any;
}

describe('JIBOT_SAFETY mapping', () => {
  it('maps stopReason and raw flags onto equipment status', () => {
    const eq = makeV3Equipment(); // use the spec helper the sibling specs use
    eq.applyVda5050State(
      stateWithSafety({
        stopReason: 'MANUAL',
        systemStatus: 'Press ON to Enable.',
        motorEnable: '0',
        hmiEstop: '0',
        bumperEstop: '1',
      }),
    );
    expect(eq.jibotStopReason).toBe('MANUAL');
    expect(eq.jibotSystemStatus).toBe('Press ON to Enable.');
    expect(eq.motorEnable).toBe('0');
    expect(eq.bumperEstop).toBe('1');
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/lab2m-llm1/workspaces/fabris-equipments && npx jest amr-l2m-v3-jibot-safety -t "maps stopReason"`
Expected: FAIL (`jibotStopReason` undefined / property missing)

- [ ] **Step 4: Add the info-type constant**

In `amr-l2m-v3-base.equipment.ts`, next to `const JIBOT_STATUS_INFO_TYPE = 'JIBOT_STATUS';` add:

```typescript
const JIBOT_SAFETY_INFO_TYPE = 'JIBOT_SAFETY';
```

- [ ] **Step 5: Declare the @EquipmentStatus properties**

Next to the existing `jibotMode`/`jibotStatus` `@EquipmentStatus` declarations, add (mirror their decorator style):

```typescript
@EquipmentStatus({ dataType: 'string', persist: 'none', source: 'device', tier: 'essential', tags: ['safety', 'jibot'], range: { criticalValues: ['EMERGENCY', 'PROTECTIVE_STOP', 'MOTOR_FAULT'] } })
jibotStopReason = '';

@EquipmentStatus({ dataType: 'string', persist: 'none', source: 'device', tier: 'important', tags: ['safety', 'jibot'] })
jibotSystemStatus = '';

@EquipmentStatus({ dataType: 'string', persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
motorEnable = '';

@EquipmentStatus({ dataType: 'string', persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
hmiEstop = '';

@EquipmentStatus({ dataType: 'string', persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
bumperEstop = '';

@EquipmentStatus({ dataType: 'string', persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
pcEstop = '';

@EquipmentStatus({ dataType: 'string', persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
motorError = '';
```

- [ ] **Step 6: Add morphism schema mappings**

Next to the `jibotMode`/`jibotStatus` morphism entries, add:

```typescript
jibotStopReason: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'stopReason'),
jibotSystemStatus: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'systemStatus'),
motorEnable: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'motorEnable'),
hmiEstop: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'hmiEstop'),
bumperEstop: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'bumperEstop'),
pcEstop: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'pcEstop'),
motorError: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'motorError'),
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd /home/lab2m-llm1/workspaces/fabris-equipments && npx jest amr-l2m-v3-jibot-safety`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd /home/lab2m-llm1/workspaces/fabris-equipments
git add packages/amr/src/lib/amr/amr-l2m-v3/
git commit -m "feat: consume JIBOT_SAFETY stop reason + flags as @EquipmentStatus"
```

---

### Task B2: Regression — MANUAL state reuse + unknown-token fallback

**Files:**
- Test only: the spec file from Task B1 (add cases)

**Interfaces:**
- Consumes: existing `operatingMode → AmrState.MANUAL` mapping; existing unknown-token fallback for `receivedWorkingState*`.

- [ ] **Step 1: Write the tests**

Add to the JIBOT_SAFETY spec:

```typescript
it('operatingMode=MANUAL still yields AmrState.MANUAL (no regression)', () => {
  const eq = makeV3Equipment();
  eq.applyVda5050State({ operatingMode: 'MANUAL', safetyState: { activeEmergencyStop: 'NONE' } } as any);
  eq.decideState();
  expect(eq.state).toBe(AmrState.MANUAL); // use the project's actual state accessor
});

it('unknown stopReason is passed through verbatim (no crash)', () => {
  const eq = makeV3Equipment();
  eq.applyVda5050State(stateWithSafety({ stopReason: 'WAT_NEW_TOKEN' }) as any);
  expect(eq.jibotStopReason).toBe('WAT_NEW_TOKEN');
});
```

Adjust `eq.state`/`decideState`/`AmrState` references to the project's actual accessors (grep a sibling spec for how equipment state is asserted).

- [ ] **Step 2: Run the tests**

Run: `cd /home/lab2m-llm1/workspaces/fabris-equipments && npx jest amr-l2m-v3-jibot-safety`
Expected: PASS

- [ ] **Step 3: Run the AMR package suite for no regression**

Run: `cd /home/lab2m-llm1/workspaces/fabris-equipments && npx jest packages/amr`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /home/lab2m-llm1/workspaces/fabris-equipments
git add packages/amr/src/lib/amr/amr-l2m-v3/
git commit -m "test: MANUAL state reuse + unknown stopReason fallback"
```

---

## Self-Review (completed)

- **Spec coverage:** §4.1 listener → A2/A3; §4.2 derivation → A4; §4.3 real-fix (eStop/opMode/fieldViolation+driving/JIBOT_MOTOR_FAULT) → A5; §4.4 JIBOT_SAFETY always-publish → A6; §4.5 enable/disableMotor (+registry) → A7/A8; §5 eq @EquipmentStatus + morphism → B1; §3.1/§8 MANUAL reuse + unknown fallback → B2; §8 shared token table → encoded in A4/B1 test fixtures. Covered.
- **Freshness fallback** (global constraint) → A4 (`_jibot_safety_if_fresh`) + A5 (`reason is None` branch + regression note in A5 step 8).
- **Always-publish empty strings** → A6 step 1 (`test_block_empty_strings_when_stale`).
- **Cross-repo token identity** → same six tokens asserted in A4 and B1 tests.
- **Open caveats carried from spec:** `bumpe_stop` polarity (classification keys off text, A4); `operatingMode=MANUAL` blast radius (isolated in `_derive_operating_mode`, A5 step 6).
- **Placeholder scan:** test helpers that depend on the existing test harness (`_AdapterV3TestBase`, `_InstantAction`, eq `makeV3Equipment`) are flagged with explicit "grep the sibling and mirror" instructions rather than invented APIs.
