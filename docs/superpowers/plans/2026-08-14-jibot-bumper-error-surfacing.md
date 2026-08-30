# JIBOT Bumper Error Surfacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface a JIBOT bumper stop as a FATAL `JIBOT_BUMPER` error (so the robot reads as ERROR, not merely BLOCKED) while guaranteeing the running order is never aborted by it.

**Architecture:** Three small predicates plus one purge-then-append error refresher, wired into the existing `_apply_jibot_stop_reason` / `_refresh_jibot_status_errors` cycle. Order protection comes from adding the bumper to the exemption lists of the two stall detectors that already exempt brake / joystick / stopPause.

**Tech Stack:** Python 3 (`adaptor/.venv`), `unittest`, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-12-jibot-bumper-error-surfacing-design.md`

## Global Constraints

- Run every test with the project venv: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest <module> -v`. A bare `python` lacks `paho` and fails on import.
- **Anchor edits on the quoted code text, never on line numbers.** `adapter_jibot.py` is being edited concurrently by another session and shifted twice during design (+5, then +3). Before each task, locate the target with `grep -n`.
- Follow the file's existing style: bilingual comments are used where behaviour is non-obvious; match the surrounding density rather than adding new commentary everywhere.
- `ErrorLevel` is imported from `protocol/vda5050_common.py`; `ErrorType`, `Error`, `ErrorReference` are already imported in `adapter_jibot.py`. No new imports are needed in `adapter_jibot.py` for any task.
- Never lower the bumper error below FATAL. `_derive_amr_working_state` gates `working_state = "ERROR"` on `has_fatal or estopped`; a WARNING would leave the robot at `BLOCKED`, which is the bug being fixed.
- Existing suites that must stay green throughout: `tests.test_jibot_stop_reason`, `tests.test_jibot_stop_reason_apply`, `tests.test_jibot_safety_info` (23 tests at plan time).

---

### Task 1: `JIBOT_BUMPER` FATAL error, set and cleared each cycle

**Files:**
- Modify: `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` (`ErrorType` enum)
- Modify: `adaptor/adapter_jibot.py` (`_refresh_jibot_bumper_errors`, `_apply_jibot_stop_reason`)
- Test: `adaptor/tests/test_jibot_stop_reason_apply.py`

**Interfaces:**
- Consumes: `ErrorType`, `ErrorLevel`, `Error`, `ErrorReference` (already imported); `self._derive_jibot_stop_reason()` returning `"BUMPER"` for the live fixture.
- Produces: `ErrorType.JIBOT_BUMPER`; `Adapter._refresh_jibot_bumper_errors(active: bool) -> None`. Task 3 relies on `self._jibot_stop_reason` holding `"BUMPER"` during a latch (already true today).

- [ ] **Step 1: Extend the shared test helper to expose working state**

In `adaptor/tests/test_jibot_stop_reason_apply.py`, the module-level `_run_with_safety` currently returns five keys. Add two more so later assertions can read them. Replace the `return {...}` block inside `scenario()` with:

```python
            working_state, detail = adapter._derive_amr_working_state()
            return {
                "e_stop": adapter.state.safety_state.e_stop,
                "field_violation": adapter.state.safety_state.field_violation,
                "operating_mode": adapter.state.operating_mode,
                "error_types": [e.error_type for e in adapter.state.errors],
                "error_levels": {
                    e.error_type: e.error_level for e in adapter.state.errors
                },
                "driving": adapter.state.driving,
                "working_state": working_state,
                "detail": detail,
            }
```

Existing tests index only the original keys, so they are unaffected.

- [ ] **Step 2: Write the failing tests**

Append to `adaptor/tests/test_jibot_stop_reason_apply.py`:

```python
# Verbatim /jrobot_status field values recorded on 192.168.101.62 during the
# 2026-08-12 11:21:58-11:29:38 bumper stop (919 of 921 rows carried this exact
# combination). Source: /usr/local/urobot/logs/bot_log/st_2026-08-12__10-59-36.txt
REAL_BUMPER_SAFETY = {
    "system_status": "bumper Trigger!",
    "system_error_code": "0",
    "motor_enable": "0",
    "bumpe_stop": "1",
    "hmi_estop": "0",
    "motor_error": "0",
    "pc_estop": "0",
    "pc_enable": "1",
}


class BumperErrorTest(unittest.TestCase):
    def test_real_bumper_event_adds_fatal_bumper_error(self):
        s = _run_with_safety(dict(REAL_BUMPER_SAFETY), motor_flag=1)
        self.assertIn(ErrorType.JIBOT_BUMPER, s["error_types"])
        self.assertEqual(s["error_levels"][ErrorType.JIBOT_BUMPER], ErrorLevel.FATAL)

    def test_bumper_error_absent_when_normal(self):
        s = _run_with_safety(
            {"system_status": "Normal...", "motor_enable": "1"}, motor_flag=1
        )
        self.assertNotIn(ErrorType.JIBOT_BUMPER, s["error_types"])

    def test_bumper_makes_working_state_error_with_fault_detail(self):
        # The whole point of FATAL: BLOCKED/BRAKE is indistinguishable from an
        # ordinary obstacle wait, which is how the 7m40s stop went unnoticed.
        s = _run_with_safety(dict(REAL_BUMPER_SAFETY), motor_flag=1)
        self.assertEqual(s["working_state"], "ERROR")
        self.assertEqual(s["detail"], "FAULT")

    def test_motor_fault_outranks_bumper_and_only_one_error_fires(self):
        """Priority is EMERGENCY > PROTECTIVE_STOP > MOTOR_FAULT > BUMPER, and
        reason is a single value -- so a motor fault during a bumper contact
        must produce the motor-fault error alone, not both."""
        s = _run_with_safety(
            {**REAL_BUMPER_SAFETY, "motor_error": "1"}, motor_flag=1
        )
        self.assertIn(ErrorType.JIBOT_MOTOR_FAULT, s["error_types"])
        self.assertNotIn(ErrorType.JIBOT_BUMPER, s["error_types"])

    def test_latched_bumper_error_clears_when_safety_goes_stale(self):
        """The ROS listener dying mid-latch must not leave the error stuck.

        reason becomes None and _apply_jibot_stop_reason takes its legacy
        fallback branch, which returns early -- so that branch has to clear the
        bumper error too, exactly as it already clears the motor-fault one.
        """
        async def scenario():
            adapter = make_adapter()
            v = adapter._vehicle
            v._motor_flag = 1
            v._robot_safety = dict(REAL_BUMPER_SAFETY)
            v._robot_safety_last_update = time.monotonic()
            task = await start_state(adapter)
            try:
                await asyncio.sleep(0.1)
                latched = [e.error_type for e in adapter.state.errors]
                v._robot_safety_last_update = time.monotonic() - 999.0
                await asyncio.sleep(0.1)
                return latched, [e.error_type for e in adapter.state.errors]
            finally:
                task.cancel()

        latched, after_stale = asyncio.run(scenario())
        self.assertIn(ErrorType.JIBOT_BUMPER, latched)
        self.assertNotIn(ErrorType.JIBOT_BUMPER, after_stale)
```

Add `ErrorLevel` to the existing protocol import at the top of the file so it reads:

```python
from protocol.vda_2_0_0.vda5050_2_0_0_state import EStop, ErrorLevel, ErrorType, OperatingMode
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_stop_reason_apply -v`

Expected: `BumperErrorTest` fails. The first three fail with `AttributeError: JIBOT_BUMPER` (the enum member does not exist yet); if the import of `ErrorLevel` succeeds, the failure surfaces at attribute access inside the test body.

- [ ] **Step 4: Add the enum member**

In `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py`, find the line:

```python
    JIBOT_MOTOR_FAULT = "JIBOT_MOTOR_FAULT"  # Drive motor reported a fault (motor_error) via /jrobot_status
```

Insert directly beneath it:

```python
    JIBOT_BUMPER = "JIBOT_BUMPER"  # Bumper contact stop reported via /jrobot_status ("bumper Trigger!")
```

- [ ] **Step 5: Add the refresher**

In `adaptor/adapter_jibot.py`, find the end of `_refresh_jibot_motor_fault_errors` (it closes with a lone `)` followed by a blank line, immediately before `def _apply_jibot_stop_reason`). Insert this method between the two:

```python
    def _refresh_jibot_bumper_errors(self, active):
        """Set/clear a FATAL JIBOT_BUMPER state error each cycle.

        FATAL is deliberate: _derive_amr_working_state gates workingState=ERROR
        on has_fatal, so a lower level would leave a bumper stop reading as an
        ordinary BLOCKED/BRAKE obstacle wait.
        """
        if self.state is None:
            return
        self.state.errors = [
            e for e in self.state.errors
            if getattr(e, "error_type", None) != ErrorType.JIBOT_BUMPER
        ]
        if active:
            self.state.errors.append(
                Error(
                    error_type=ErrorType.JIBOT_BUMPER,
                    error_level=ErrorLevel.FATAL,
                    error_references=[ErrorReference("reason", "bumperTrigger")],
                    error_description="JIBOT reported a bumper contact stop (bumper Trigger!)",
                )
            )
```

- [ ] **Step 6: Wire both call sites in `_apply_jibot_stop_reason`**

Still in `adaptor/adapter_jibot.py`, find:

```python
            self._refresh_jibot_motor_fault_errors(False)
            return
```

Replace with:

```python
            self._refresh_jibot_motor_fault_errors(False)
            self._refresh_jibot_bumper_errors(False)
            return
```

Then find the method's last line:

```python
        self._refresh_jibot_motor_fault_errors(reason == "MOTOR_FAULT")
```

Replace with:

```python
        self._refresh_jibot_motor_fault_errors(reason == "MOTOR_FAULT")
        self._refresh_jibot_bumper_errors(reason == "BUMPER")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_stop_reason tests.test_jibot_stop_reason_apply tests.test_jibot_safety_info -v`

Expected: PASS, 28 tests (23 pre-existing + 5 new).

- [ ] **Step 8: Commit**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
git add adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py adaptor/adapter_jibot.py adaptor/tests/test_jibot_stop_reason_apply.py
git commit -m "feat: surface a JIBOT bumper stop as a FATAL error

A bumper stop set fieldViolation and driving=False and nothing else, so
it read as an ordinary BLOCKED/BRAKE obstacle wait. Robot 62 sat stopped
for 7m40s on 2026-08-12 with an empty errors[]. FATAL is what moves
workingState to ERROR; the legacy fallback branch clears the error so a
listener death mid-latch cannot strand it."
```

---

### Task 2: `#disable` predicate and `JIBOT_MOTOR_DISABLED` warning

**Files:**
- Modify: `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` (`ErrorType` enum)
- Modify: `adaptor/adapter_jibot.py` (`_is_jibot_motor_disabled`, `_refresh_jibot_status_errors`)
- Test: `adaptor/tests/test_jibot_motor_disabled.py` (create)

**Interfaces:**
- Consumes: `self._jibot_text_contains(*needles)`; `self._build_jibot_status_error(error_type, reason, description, error_level=ErrorLevel.WARNING)`.
- Produces: `ErrorType.JIBOT_MOTOR_DISABLED`; `Adapter._is_jibot_motor_disabled() -> bool`. **Task 3 calls `_is_jibot_motor_disabled()` directly** — the name and zero-arg signature must match exactly.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_jibot_motor_disabled.py`:

```python
import asyncio
import unittest

from protocol.vda_2_0_0.vda5050_2_0_0_state import ErrorLevel, ErrorType
from tests._stop_reason_harness import make_adapter, start_state

# JIBOT's own task status during the 2026-08-12 bumper stop on 192.168.101.62,
# copied from the adapter journal (UmGetCurTask / UmGetTaskInfo).
BUMPER_TASK_STATUS = "nrunto pose (13870 -2541 0)#disable#slowdown"


class MotorDisabledPredicateTest(unittest.TestCase):
    def _adapter_with_status(self, status, mode="auto"):
        adapter = make_adapter()
        adapter._vehicle._status = status
        adapter._vehicle._mode = mode
        return adapter

    def test_disable_token_is_detected(self):
        a = self._adapter_with_status(BUMPER_TASK_STATUS)
        self.assertTrue(a._is_jibot_motor_disabled())

    def test_slowdown_alone_is_not_motor_disabled(self):
        a = self._adapter_with_status("nrunto pose (13870 -2541 0)#slowdown")
        self.assertFalse(a._is_jibot_motor_disabled())

    def test_normal_status_is_not_motor_disabled(self):
        a = self._adapter_with_status("Normal...")
        self.assertFalse(a._is_jibot_motor_disabled())


class MotorDisabledErrorTest(unittest.TestCase):
    def _errors_for_status(self, status):
        async def scenario():
            adapter = make_adapter()
            adapter._vehicle._status = status
            task = await start_state(adapter)
            try:
                await asyncio.sleep(0.1)
                return {
                    e.error_type: e.error_level for e in adapter.state.errors
                }
            finally:
                task.cancel()
        return asyncio.run(scenario())

    def test_disable_status_adds_warning_error(self):
        levels = self._errors_for_status(BUMPER_TASK_STATUS)
        self.assertIn(ErrorType.JIBOT_MOTOR_DISABLED, levels)
        # WARNING, not FATAL: "#disable" only says the motor is off, which is
        # also true of an ordinary manual disable. Only a confirmed bumper
        # (Task 1) earns FATAL.
        self.assertEqual(levels[ErrorType.JIBOT_MOTOR_DISABLED], ErrorLevel.WARNING)

    def test_error_absent_without_disable_token(self):
        levels = self._errors_for_status("nrunto pose (13870 -2541 0)#slowdown")
        self.assertNotIn(ErrorType.JIBOT_MOTOR_DISABLED, levels)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_motor_disabled -v`

Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_is_jibot_motor_disabled'` and `AttributeError: JIBOT_MOTOR_DISABLED`.

- [ ] **Step 3: Add the enum member**

In `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py`, find the line added in Task 1:

```python
    JIBOT_BUMPER = "JIBOT_BUMPER"  # Bumper contact stop reported via /jrobot_status ("bumper Trigger!")
```

Insert directly beneath it:

```python
    JIBOT_MOTOR_DISABLED = "JIBOT_MOTOR_DISABLED"  # JIBOT task status reports the motor disabled ("#disable")
```

- [ ] **Step 4: Add the predicate**

In `adaptor/adapter_jibot.py`, find `_is_jibot_lost` (it returns `self._jibot_text_contains("#lost", " lost")`). Insert this method directly after it:

```python
    def _is_jibot_motor_disabled(self) -> bool:
        """True when JIBOT's own task status reports the motor disabled ("#disable").

        This arrives over TCP 7273, so it still works when the ROS listener is
        down and the /jrobot_status stop-reason subdivision is unavailable.
        7273으로 오므로 ROS 리스너가 죽어도 동작한다.
        """
        return self._jibot_text_contains("#disable", " disable")
```

- [ ] **Step 5: Emit the warning**

In `adaptor/adapter_jibot.py`, find the purge list at the top of `_refresh_jibot_status_errors`:

```python
            not in (
                ErrorType.JIBOT_CONFLICT,
                ErrorType.JIBOT_AVOIDANCE,
                ErrorType.JIBOT_LOCALIZATION_LOST,
            )
```

Replace with:

```python
            not in (
                ErrorType.JIBOT_CONFLICT,
                ErrorType.JIBOT_AVOIDANCE,
                ErrorType.JIBOT_LOCALIZATION_LOST,
                ErrorType.JIBOT_MOTOR_DISABLED,
            )
```

Then find the end of the same method — the `_is_jibot_lost()` block that closes it:

```python
        if self._is_jibot_lost():
            self.state.errors.append(
                self._build_jibot_status_error(
                    ErrorType.JIBOT_LOCALIZATION_LOST,
                    "lost",
                    "JIBOT reported localization/path lost; relocalization required",
                    error_level=ErrorLevel.FATAL,
                )
            )
```

Append after it (still inside the method):

```python
        # "#disable" says the motor is off without saying why, and an ordinary
        # manual disable raises it too — so WARNING, while a confirmed bumper
        # gets FATAL from _refresh_jibot_bumper_errors. Its value is that it
        # survives a dead ROS listener, which the stop-reason path does not.
        if self._is_jibot_motor_disabled():
            self.state.errors.append(
                self._build_jibot_status_error(
                    ErrorType.JIBOT_MOTOR_DISABLED,
                    "disable",
                    "JIBOT reported the drive motor disabled (#disable)",
                )
            )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_motor_disabled -v`

Expected: PASS, 5 tests.

- [ ] **Step 7: Run the neighbouring suites**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_stop_reason tests.test_jibot_stop_reason_apply tests.test_jibot_safety_info tests.test_jibot_motor_disabled -v`

Expected: PASS, 33 tests.

- [ ] **Step 8: Commit**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
git add adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py adaptor/adapter_jibot.py adaptor/tests/test_jibot_motor_disabled.py
git commit -m "feat: report the JIBOT #disable token as a warning

During the 2026-08-12 bumper stop JIBOT's task status read
\"...#disable#slowdown\" and the adapter parsed only the slowdown, so a
motor-off stop was reported as obstacle avoidance. #disable arrives over
7273 and so survives a dead ROS listener, which the stop-reason path
does not."
```

---

### Task 3: Keep the order alive — exempt a bumper from both stall detectors

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`_is_jibot_bumper_stop`, `_is_motion_disabled_by_machine`, `_wait_until_node_position_reached`, `_wait_until_move_settled`)
- Test: `adaptor/tests/test_jibot_bumper_order_protection.py` (create)

**Interfaces:**
- Consumes: `Adapter._is_jibot_motor_disabled()` from Task 2; `self._jibot_stop_reason` set by `_apply_jibot_stop_reason` (Task 1 leaves this behaviour unchanged).
- Produces: `Adapter._is_jibot_bumper_stop() -> bool`, `Adapter._is_motion_disabled_by_machine() -> bool`.

> **Scope note — spec addendum.** Spec §3.5 named only `_wait_until_node_position_reached`. While writing this plan a *second* stall detector was found in `_wait_until_move_settled` (the mode="move" path), whose freeze guard exempts stopPause and manual driving but not a bumper; unexempted it settles the move early and fires a fallback goto. Both are fixed here. Update spec §3.5 to cover both when this task lands.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_jibot_bumper_order_protection.py`:

```python
"""A bumper stop must never abort the running order.

Both stall detectors treat "robot is stopped and nothing of ours is driving it"
as a stall. A bumper latch looks exactly like that, so without an exemption the
node wait burns its goto retries and settles on a FATAL node-unreached, and the
move wait settles early into a fallback goto. Robot 62 escaped this on
2026-08-12 only because _is_jibot_stopped() wants an exact status match and the
live status was "nrunto pose (...)#disable#slowdown" -- a lucky string
mismatch, not a guard.
"""
import asyncio
import unittest
from types import SimpleNamespace

from adapter_jibot import Adapter
from tests.test_adapter_jibot_v3_order import FakeVehicle

BUMPER_TASK_STATUS = "nrunto pose (13870 -2541 0)#disable#slowdown"


def _node(node_id="p39"):
    return SimpleNamespace(node_id=node_id, sequence_id=2)


class MotionDisabledPredicateTest(unittest.TestCase):
    def _adapter(self):
        adapter = Adapter()
        adapter.set_vehicle(FakeVehicle())
        return adapter

    def test_ros_path_alone_reports_disabled(self):
        a = self._adapter()
        a._vehicle._status = "Stopped"          # no #disable token
        a._jibot_stop_reason = "BUMPER"
        self.assertTrue(a._is_motion_disabled_by_machine())

    def test_seven_two_seven_three_path_alone_reports_disabled(self):
        a = self._adapter()
        a._vehicle._status = BUMPER_TASK_STATUS  # #disable present
        a._jibot_stop_reason = None              # listener down
        self.assertTrue(a._is_motion_disabled_by_machine())

    def test_neither_path_reports_not_disabled(self):
        a = self._adapter()
        a._vehicle._status = "Stopped"
        a._jibot_stop_reason = "NONE"
        self.assertFalse(a._is_motion_disabled_by_machine())


class NodeWaitBumperTest(unittest.TestCase):
    def _adapter(self):
        adapter = Adapter()
        adapter.set_vehicle(FakeVehicle())
        s = adapter.config.settings
        s.node_position_poll_interval_sec = 0.01
        s.node_unreached_delay_sec = 0.02
        s.node_goto_retry_delay_sec = 0.03
        s.node_goto_retry_limit = 3
        s.reach_zone_shape = "square"
        s.reach_zone_scale = 20.0
        s.default_node_deviation_xy = 10.0
        return adapter

    async def _run_wait(self, bumper):
        adapter = self._adapter()
        v = adapter._vehicle
        v._status = "Stopped"                 # _is_jibot_stopped() -> True
        v.arrive_at(0.0, 0.0)                 # far outside the reach zone
        node = _node()
        adapter._active_goto_node = node      # goto_recoverable -> True
        adapter._jibot_stop_reason = "BUMPER" if bumper else "NONE"

        resends = []

        async def fake_resend(node_arg):
            resends.append(node_arg)

        adapter._resend_node_goto = fake_resend
        wait = asyncio.create_task(
            adapter._wait_until_node_position_reached(node, (5000.0, 0.0, 10.0))
        )
        await asyncio.sleep(0.25)             # well past delay + retry_delay
        done = wait.done()
        wait.cancel()
        return resends, done

    def test_bumper_stop_never_retries_the_goto(self):
        resends, done = asyncio.run(self._run_wait(bumper=True))
        self.assertEqual(resends, [])
        self.assertFalse(done)                # still waiting, order intact

    def test_control_without_bumper_does_retry(self):
        # Proves the assertion above is not vacuous: the same setup minus the
        # bumper does reach the retry path.
        resends, _done = asyncio.run(self._run_wait(bumper=False))
        self.assertGreater(len(resends), 0)


class MoveWaitBumperTest(unittest.TestCase):
    def _adapter(self):
        adapter = Adapter()
        adapter.set_vehicle(FakeVehicle())
        s = adapter.config.settings
        s.node_position_poll_interval_sec = 0.01
        s.move_start_timeout_sec = 0.2
        s.move_stall_timeout_sec = 0.2
        s.move_started_min_travel_mm = 50.0
        s.move_complete_travel_ratio = 0.9
        s.reach_zone_shape = "square"
        s.reach_zone_scale = 20.0
        s.default_node_deviation_xy = 10.0
        return adapter

    def test_bumper_freezes_the_move_wait(self):
        async def scenario():
            adapter = self._adapter()
            v = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10650.0, -2522.0)      # has not moved at all
            adapter._jibot_stop_reason = "BUMPER"
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                _node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.5)           # past both 0.2s timeouts
            done = wait.done()
            wait.cancel()
            return done

        # Without the exemption this settles as "not-started" and the caller
        # fires a fallback goto while the bumper is still latched.
        self.assertFalse(asyncio.run(scenario()))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_order_protection -v`

Expected: FAIL. `MotionDisabledPredicateTest` fails with `AttributeError: 'Adapter' object has no attribute '_is_motion_disabled_by_machine'`. `test_bumper_stop_never_retries_the_goto` fails because `resends` is non-empty. `test_bumper_freezes_the_move_wait` fails because the wait completed. `test_control_without_bumper_does_retry` should already PASS — it documents today's behaviour.

- [ ] **Step 3: Add the two predicates**

In `adaptor/adapter_jibot.py`, find `_is_jibot_motor_disabled` (added in Task 2). Insert both methods directly after it:

```python
    def _is_jibot_bumper_stop(self) -> bool:
        """True while the /jrobot_status stop reason is a bumper contact."""
        return getattr(self, "_jibot_stop_reason", None) == "BUMPER"

    def _is_motion_disabled_by_machine(self) -> bool:
        """True when the machine, not the adapter, is holding the robot still.

        No stall judgement applies while this holds: the robot is stopped and
        nothing of ours is driving it, which is exactly what a stall detector
        would otherwise punish. Both signals are checked because they arrive on
        independent transports -- "#disable" over TCP 7273 and the stop reason
        over ROS -- so either one alone still protects the order.
        7273/ROS 중 하나가 죽어도 나머지가 오더를 지킨다.
        """
        return self._is_jibot_motor_disabled() or self._is_jibot_bumper_stop()
```

- [ ] **Step 4: Exempt the bumper in the node-wait stall detector**

In `adaptor/adapter_jibot.py`, find this comment and expression inside `_wait_until_node_position_reached`:

```python
                # The stall clock only runs while the robot stands still with
                # nobody else owning its motion: an obstacle brake, a hand on the
                # joystick and a stopPause are all legitimate reasons to sit here,
                # and none of them are ours to interrupt. _manual_control_active
                # covers jogs dispatched through the adapter, which move the robot
                # before JIBOT's polled mode catches up.
                stalled = (
                    self._is_jibot_stopped()
                    and not self._is_jibot_obstacle_wait()
                    and not self._is_jibot_manual_drive()
                    and not self._manual_control_active
                    and not self._motion_paused
                )
```

Replace with:

```python
                # The stall clock only runs while the robot stands still with
                # nobody else owning its motion: an obstacle brake, a hand on the
                # joystick, a stopPause and a bumper latch are all legitimate
                # reasons to sit here, and none of them are ours to interrupt.
                # _manual_control_active covers jogs dispatched through the
                # adapter, which move the robot before JIBOT's polled mode catches
                # up. Retrying a goto into a latched bumper would burn the retry
                # budget and settle the node as unreached — aborting the order for
                # something that clears itself.
                stalled = (
                    self._is_jibot_stopped()
                    and not self._is_jibot_obstacle_wait()
                    and not self._is_jibot_manual_drive()
                    and not self._manual_control_active
                    and not self._motion_paused
                    and not self._is_motion_disabled_by_machine()
                )
```

- [ ] **Step 5: Exempt the bumper in the move-wait freeze guard**

In `adaptor/adapter_jibot.py`, find this guard inside `_wait_until_move_settled`:

```python
            if (
                self._motion_paused
                or self._is_jibot_manual_drive()
                or self._manual_control_active
            ):
                stopped_since = None
                await asyncio.sleep(poll_sec)
                continue
```

Replace with:

```python
            if (
                self._motion_paused
                or self._is_jibot_manual_drive()
                or self._manual_control_active
                or self._is_motion_disabled_by_machine()
            ):
                stopped_since = None
                await asyncio.sleep(poll_sec)
                continue
```

Then extend the comment immediately above that guard. Find its closing sentence:

```python
            # _manual_control_active covers jogs the adapter itself
            # dispatched, which move the robot before JIBOT's polled _mode
            # catches up.
```

Replace with:

```python
            # _manual_control_active covers jogs the adapter itself
            # dispatched, which move the robot before JIBOT's polled _mode
            # catches up. A bumper latch belongs here for the same reason: the
            # machine is holding the robot, so judging it would settle the move
            # early and fire a fallback goto into a stop that clears itself.
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_order_protection -v`

Expected: PASS, 6 tests.

- [ ] **Step 7: Run the order suite for regressions**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_adapter_jibot_v3_order 2>&1 | tail -20`

Expected: PASS. This suite owns both wait loops, including
`test_never_starting_returns_false_after_start_timeout` and
`test_stopped_but_not_yet_moving_does_not_settle_early`; neither sets
`_jibot_stop_reason` or a `#disable` status, so the new exemption must not
change them. If any test fails, the exemption is firing when it should not —
check that `_jibot_stop_reason` is not left set from an earlier cycle.

- [ ] **Step 8: Commit**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
git add adaptor/adapter_jibot.py adaptor/tests/test_jibot_bumper_order_protection.py
git commit -m "fix: never treat a bumper stop as a stall

Both wait loops read \"stopped with nothing of ours driving it\" as a
stall. A bumper looks exactly like that, so the node wait burned its
goto retries into a FATAL node-unreached and the move wait settled early
into a fallback goto -- aborting an order for a stop that clears itself.
Robot 62 escaped this only because _is_jibot_stopped() wants an exact
status match. Both signals are checked so a dead ROS listener or a
missing #disable token still leaves the order protected."
```

---

### Task 4: Log stop-reason transitions

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`_apply_jibot_stop_reason`)
- Test: `adaptor/tests/test_jibot_stop_reason_log.py` (create)

**Interfaces:**
- Consumes: `self._jibot_stop_reason` (the cached previous value) and `reason` inside `_apply_jibot_stop_reason`.
- Produces: a `[JIBOT STOP REASON] <prev> -> <new>` line on stdout, emitted only on change.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_jibot_stop_reason_log.py`:

```python
"""The 2026-08-12 investigation had to read the robot's own recorder because
the adapter journal held zero "bumper" lines across its entire history. One
line per transition fixes that -- but only per transition: the publish loop
runs at roughly 3Hz, so logging every cycle would bury the journal.
"""
import asyncio
import contextlib
import io
import time
import unittest

from tests._stop_reason_harness import make_adapter, start_state

REAL_BUMPER_SAFETY = {
    "system_status": "bumper Trigger!",
    "motor_enable": "0",
    "bumpe_stop": "1",
    "hmi_estop": "0",
    "motor_error": "0",
    "pc_estop": "0",
    "pc_enable": "1",
}


def _reason_log_lines(safety, settle_sec):
    async def scenario():
        adapter = make_adapter()
        v = adapter._vehicle
        v._motor_flag = 1
        v._robot_safety = dict(safety)
        v._robot_safety_last_update = time.monotonic()
        task = await start_state(adapter)
        try:
            await asyncio.sleep(settle_sec)
        finally:
            task.cancel()

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        asyncio.run(scenario())
    return [
        line for line in buf.getvalue().splitlines()
        if "[JIBOT STOP REASON]" in line
    ]


class StopReasonLogTest(unittest.TestCase):
    def test_transition_logs_exactly_once(self):
        # The harness publishes every 0.02s, so 0.3s is ~15 cycles at one
        # unchanging reason -- exactly one line must come out.
        lines = _reason_log_lines(REAL_BUMPER_SAFETY, settle_sec=0.3)
        self.assertEqual(len(lines), 1, f"expected one line, got {lines}")
        self.assertIn("BUMPER", lines[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_stop_reason_log -v`

Expected: FAIL — `expected one line, got []`, since nothing logs the reason yet.

- [ ] **Step 3: Add the transition log**

In `adaptor/adapter_jibot.py`, find these two lines at the top of `_apply_jibot_stop_reason`:

```python
        reason = self._derive_jibot_stop_reason()
        self._jibot_stop_reason = reason  # cached for operatingMode + info block
```

Replace with:

```python
        reason = self._derive_jibot_stop_reason()
        # Transitions only: the publish loop runs at ~3Hz, so logging every
        # cycle would bury the journal. Without this line a bumper stop leaves
        # no trace at all — the 2026-08-12 investigation had to fall back to the
        # robot's own recorder.
        if reason != self._jibot_stop_reason:
            print(f"[JIBOT STOP REASON] {self._jibot_stop_reason} -> {reason}")
        self._jibot_stop_reason = reason  # cached for operatingMode + info block
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest tests.test_jibot_stop_reason_log -v`

Expected: PASS, 1 test.

- [ ] **Step 5: Run the full bumper-related set**

Run:

```bash
cd /ssd2/workspaces/unified-amr-adaptor/adaptor && .venv/bin/python -m unittest \
  tests.test_jibot_stop_reason \
  tests.test_jibot_stop_reason_apply \
  tests.test_jibot_safety_info \
  tests.test_jibot_motor_disabled \
  tests.test_jibot_bumper_order_protection \
  tests.test_jibot_stop_reason_log 2>&1 | tail -5
```

Expected: PASS, 40 tests.

- [ ] **Step 6: Commit**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
git add adaptor/adapter_jibot.py adaptor/tests/test_jibot_stop_reason_log.py
git commit -m "feat: log JIBOT stop-reason transitions

The adapter journal held zero \"bumper\" lines across its whole history,
so pinning the 2026-08-12 stop to a clock time meant reading the robot's
own recorder. Logged on change only -- the publish loop runs at ~3Hz."
```

---

### Task 5: Update the spec and close the loop

**Files:**
- Modify: `docs/superpowers/specs/2026-08-12-jibot-bumper-error-surfacing-design.md`

- [ ] **Step 1: Record the second stall detector in §3.5**

In the spec, find the heading `### 3.5 stall 감지기에 범퍼 면제 추가 — **오더 보호의 핵심**`. Immediately below that heading, insert:

```markdown
> **구현 중 추가 발견 (2026-08-14).** stall 감지기는 하나가 아니라 **둘**이다.
> `_wait_until_move_settled`(mode="move" 경로)의 freeze 가드도 stopPause/수동주행만 면제하고
> 범퍼는 면제하지 않아, 범퍼 중 move가 조기 settle되어 fallback goto가 나간다. 구현에서는
> 두 곳 모두 `_is_motion_disabled_by_machine()`으로 면제한다.
```

- [ ] **Step 2: Replace the inline expression with the shipped helper**

In the same section find the proposed code block that ends with:

```python
    and not self._is_jibot_motor_disabled()                    # #disable (7273)
    and getattr(self, "_jibot_stop_reason", None) != "BUMPER"  # /jrobot_status
)
```

Replace that whole ```python block with:

```python
# _wait_until_node_position_reached
stalled = (
    self._is_jibot_stopped()
    and not self._is_jibot_obstacle_wait()
    and not self._is_jibot_manual_drive()
    and not self._manual_control_active
    and not self._motion_paused
    and not self._is_motion_disabled_by_machine()   # 범퍼/#disable
)

# _wait_until_move_settled
if (
    self._motion_paused
    or self._is_jibot_manual_drive()
    or self._manual_control_active
    or self._is_motion_disabled_by_machine()        # 범퍼/#disable
):
    stopped_since = None       # 판단 자체를 얼린다
    await asyncio.sleep(poll_sec)
    continue

# 공용 술어
def _is_motion_disabled_by_machine(self) -> bool:
    return self._is_jibot_motor_disabled() or self._is_jibot_bumper_stop()
```

- [ ] **Step 3: Mark the spec implemented**

Find the status line near the top:

```markdown
- 상태: 설계 승인됨 (구현 대기)
```

Replace with:

```markdown
- 상태: 구현 완료 (2026-08-14). 계획: `docs/superpowers/plans/2026-08-14-jibot-bumper-error-surfacing.md`
```

- [ ] **Step 4: Commit**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
git add docs/superpowers/specs/2026-08-12-jibot-bumper-error-surfacing-design.md
git commit -m "docs(spec): record the second stall detector found during implementation"
```

---

## Deployment notes (not code — for whoever ships this)

- **Coordinate with ACS before deploying.** The adapter never cancels an order for a bumper, and Task 3 guarantees neither stall detector does either. But `workingState` now moves `BLOCKED` → `ERROR`, and if ACS cancels orders on `workingState=ERROR` the requirement is broken outside this repo. This is the one place the guarantee does not hold.
- `JIBOT_BUMPER` and `JIBOT_MOTOR_DISABLED` are new errorType tokens. eq ignores unknown errorTypes, so the adapter ships safely on its own; rendering them in eq is a separate ticket.
- **Still unverified (spec §8.5):** whether the adapter kept streaming `UmDrive` during the bumper latch. The robot dropped off the network mid-investigation. Re-check on 192.168.101.62 after deploying, with:
  `journalctl -u amr-adaptor --since "<latch start>" --until "<latch end>" | grep -c "command=UmDrive"`
  (Note: `sudo` on that host prompts for a password and silently yields nothing under `-n`; run `journalctl` without sudo — `ucore` is in the `systemd-journal` group.)
