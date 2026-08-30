# JIBOT Loading/Unloading Busy State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While JIBOT does loading/unloading work, expose it as a long-lived RUNNING VDA5050 action, reject incoming orders, and block motion commands, until an explicit `stopLoading`/`stopUnloading` injects completion.

**Architecture:** Add a two-field in-memory BUSY flag (`_work_in_progress`, `_work_action_id`) on `Adapter`. The incoming `loading`/`unloading` instant action is held in RUNNING status (not auto-finished); the matching `stopLoading`/`stopUnloading` finishes it (terminal → auto-removed from the published list) and clears BUSY. A guard in `_handle_v3_order` rejects orders while BUSY; a guard at the top of the instant-action dispatch loop fails motion-causing instant actions (`startCharging` + all JIBOT-command instant actions) while BUSY.

**Tech Stack:** Python 3.12, `unittest`, existing VDA5050 adapter classes (`adapter_jibot.py`), `FakeVehicle` test double.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-21-jibot-loading-unloading-busy-state-design.md`.
- New instant action types: `loading`, `unloading` (start), `stopLoading`, `stopUnloading` (completion injection).
- BUSY ⇔ `self._work_in_progress is not None` (`None` | `"loading"` | `"unloading"`).
- BUSY is in-memory, adapter-process lifetime only; restart recovery is out of scope.
- The adapter sends NO JIBOT robot command for loading/unloading; it only manages VDA5050 state + gating.
- Terminal instant action states (FINISHED/FAILED) are auto-removed by `_clear_terminal_instant_action_states` inside `_update_instant_action_status` (`adapter_jibot.py:4035`). Tests assert *absence*, never a lingering FINISHED entry.
- Test class: `AdapterV3OrderTest` in `adaptor/tests/test_adapter_jibot_v3_order.py`. Run tests from the `adaptor/` directory.
- Imports already present in the test file: `asyncio`, `InstantActions`, `Order`, `ActionStatus`, `ErrorType`.

---

### Task 1: BUSY fields, shared start handler, dispatch + factsheet

**Files:**
- Modify: `adaptor/adapter_jibot.py` — `Adapter.__init__` (~line 112), `instant_actions_accept_procedure` dispatch (~line 2750), `SUPPORTED_INSTANT_ACTIONS` (~line 1429), new handler.
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` — add a module-level test helper + tests.

**Interfaces:**
- Produces:
  - `Adapter._work_in_progress: Optional[str]` — `None` | `"loading"` | `"unloading"`.
  - `Adapter._work_action_id: Optional[str]` — action_id of the RUNNING work action.
  - `Adapter._handle_work_start_instant_action(self, action, work_type: str) -> None`.
  - Test helper `_work_instant_actions(action_type: str, action_id: str) -> InstantActions` (module-level in the test file).

- [ ] **Step 1: Add the test helper at module level in the test file**

Add near the top of `adaptor/tests/test_adapter_jibot_v3_order.py`, after the imports (before `class FakeVehicle`):

```python
def _work_instant_actions(action_type: str, action_id: str) -> InstantActions:
    """Build a one-action InstantActions request for loading/unloading tests."""
    return InstantActions.from_dict(
        {
            "headerId": 1,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "actions": [
                {
                    "actionType": action_type,
                    "actionId": action_id,
                    "blockingType": "HARD",
                    "actionParameters": [],
                }
            ],
        }
    )
```

- [ ] **Step 2: Write the failing test (loading enters BUSY, stays RUNNING)**

Add to `AdapterV3OrderTest`:

```python
def test_loading_instant_action_enters_busy_and_stays_running(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        state_task = await self._start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("loading", "work-1")
            )
            self.assertEqual(adapter._work_in_progress, "loading")
            self.assertEqual(adapter._work_action_id, "work-1")
            matches = [
                s for s in adapter.state.instant_action_states
                if s.action_id == "work-1"
            ]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].action_status, ActionStatus.RUNNING)
        finally:
            state_task.cancel()

    asyncio.run(scenario())

def test_unloading_instant_action_enters_busy_and_stays_running(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        state_task = await self._start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("unloading", "work-2")
            )
            self.assertEqual(adapter._work_in_progress, "unloading")
            matches = [
                s for s in adapter.state.instant_action_states
                if s.action_id == "work-2"
            ]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].action_status, ActionStatus.RUNNING)
        finally:
            state_task.cancel()

    asyncio.run(scenario())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_loading_instant_action_enters_busy_and_stays_running -v`

Expected: FAIL with `AttributeError: 'Adapter' object has no attribute '_work_in_progress'` (handler/fields not present; `loading` falls into the "Unsupported instant action" else and is not held RUNNING).

- [ ] **Step 4: Add the BUSY fields in `Adapter.__init__`**

In `adaptor/adapter_jibot.py`, right after `self._docking_started_node_ids: Set[str] = set()` (~line 112):

```python
        # Loading/unloading BUSY state (in-memory, adapter-process lifetime only).
        # BUSY <=> _work_in_progress is not None. While BUSY the adapter rejects
        # orders and motion instant actions. JIBOT cannot self-detect completion,
        # so the work action is held RUNNING until stopLoading/stopUnloading.
        self._work_in_progress: Optional[str] = None  # None | "loading" | "unloading"
        self._work_action_id: Optional[str] = None
```

- [ ] **Step 5: Add the start handler**

Add a new method to `Adapter` (place it near `_handle_start_charging_instant_action`):

```python
    def _handle_work_start_instant_action(self, action: Any, work_type: str) -> None:
        """loading/unloading: enter BUSY and hold the action RUNNING.

        JIBOT cannot self-detect completion, so the action is kept RUNNING until
        a matching stopLoading/stopUnloading injects completion. While BUSY the
        adapter rejects orders and motion instant actions.
        """
        if self.state is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="State is not initialized",
            )
            return

        self._work_in_progress = work_type
        self._work_action_id = action.action_id
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.RUNNING,
            result_description=f"{work_type} in progress",
        )
        print(
            f"[WORK START] type={work_type} actionId={action.action_id} -> BUSY"
        )
```

- [ ] **Step 6: Wire the dispatch**

In `instant_actions_accept_procedure`, after the `initPosition` branch (~line 2750), add:

```python
            elif action.action_type in ("loading", "unloading"):
                self._handle_work_start_instant_action(action, action.action_type)
```

- [ ] **Step 7: Advertise in the factsheet**

In `SUPPORTED_INSTANT_ACTIONS` (~line 1429), add `"loading"` and `"unloading"` entries to the tuple:

```python
        "loading",
        "unloading",
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_loading_instant_action_enters_busy_and_stays_running tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_unloading_instant_action_enters_busy_and_stays_running -v`

Expected: PASS (2 tests).

- [ ] **Step 9: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: loading/unloading instant actions enter BUSY (held RUNNING)"
```

---

### Task 2: Reject orders while BUSY

**Files:**
- Modify: `adaptor/adapter_jibot.py` — `_handle_v3_order` (~line 1665).
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` — add test + a module-level order helper.

**Interfaces:**
- Consumes: `_work_in_progress` (Task 1).
- Produces: test helper `_one_node_order(order_id: str) -> Order` (module-level).

- [ ] **Step 1: Add the order helper at module level in the test file**

Add after `_work_instant_actions`:

```python
def _one_node_order(order_id: str) -> Order:
    """Minimal released one-node order for accept/reject tests."""
    return Order.from_dict(
        {
            "headerId": 1,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "orderId": order_id,
            "orderUpdateId": 0,
            "nodes": [
                {
                    "nodeId": "N1",
                    "sequenceId": 0,
                    "released": True,
                    "nodePosition": {
                        "x": 0.0, "y": 0.0, "theta": 0.0, "mapId": "lab2m",
                    },
                    "actions": [],
                }
            ],
            "edges": [],
        }
    )
```

- [ ] **Step 2: Write the failing test**

```python
def test_order_rejected_while_busy(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        state_task = await self._start_state(adapter)
        try:
            adapter._work_in_progress = "loading"
            adapter._work_action_id = "work-1"

            adapter._handle_v3_order(_one_node_order("order-A"))

            # Order was NOT accepted (order_id unchanged) ...
            self.assertNotEqual(adapter.state.order_id, "order-A")
            # ... and a busy rejection error was recorded.
            self.assertTrue(
                any(
                    "busy" in (e.error_description or "").lower()
                    for e in adapter.state.errors
                )
            )
        finally:
            state_task.cancel()

    asyncio.run(scenario())
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_order_rejected_while_busy -v`

Expected: FAIL — without the guard the order is accepted, so `state.order_id == "order-A"` and no busy error exists.

- [ ] **Step 4: Add the BUSY guard in `_handle_v3_order`**

In `_handle_v3_order`, immediately after the `if self.state is None:` block (before the `if not order_request.nodes:` check), add:

```python
        if self._work_in_progress is not None:
            self.order_reject(
                True,
                ErrorLevel.WARNING,
                ErrorType.ORDER_CURRENT_NOT_FINISHED,
                f"Robot is busy with {self._work_in_progress} work; "
                "cannot accept order",
                error_hint="Send stopLoading/stopUnloading to finish the work first",
            )
            print(
                f"[ORDER REJECTED] orderId={order_request.order_id} "
                f"reason=busy:{self._work_in_progress}"
            )
            return
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_order_rejected_while_busy -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: reject incoming orders while loading/unloading BUSY"
```

---

### Task 3: Completion injection (stopLoading/stopUnloading)

**Files:**
- Modify: `adaptor/adapter_jibot.py` — new handler, dispatch (~line 2750, next to Task 1's branch), `SUPPORTED_INSTANT_ACTIONS` (~line 1429).
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` — add tests.

**Interfaces:**
- Consumes: `_work_in_progress`, `_work_action_id` (Task 1); `_one_node_order` (Task 2); `_work_instant_actions` (Task 1).
- Produces: `Adapter._handle_work_stop_instant_action(self, action, stop_type: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_stop_loading_clears_busy_and_removes_work_action(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        state_task = await self._start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("loading", "work-1")
            )
            self.assertEqual(adapter._work_in_progress, "loading")

            adapter.instant_actions_accept_procedure(
                _work_instant_actions("stopLoading", "stop-1")
            )

            self.assertIsNone(adapter._work_in_progress)
            self.assertIsNone(adapter._work_action_id)
            ids = [s.action_id for s in adapter.state.instant_action_states]
            self.assertNotIn("work-1", ids)  # terminal FINISHED -> removed
            self.assertNotIn("stop-1", ids)

            # Orders are accepted again.
            adapter._handle_v3_order(_one_node_order("order-A"))
            self.assertEqual(adapter.state.order_id, "order-A")
        finally:
            state_task.cancel()

    asyncio.run(scenario())

def test_stop_unloading_type_mismatch_fails_and_keeps_busy(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        state_task = await self._start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("loading", "work-1")
            )
            # stopUnloading does not match active "loading" work.
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("stopUnloading", "stop-x")
            )
            self.assertEqual(adapter._work_in_progress, "loading")  # still BUSY
        finally:
            state_task.cancel()

    asyncio.run(scenario())

def test_stop_loading_with_no_active_work_does_not_enter_busy(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        state_task = await self._start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("stopLoading", "stop-1")
            )
            self.assertIsNone(adapter._work_in_progress)
        finally:
            state_task.cancel()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_stop_loading_clears_busy_and_removes_work_action -v`

Expected: FAIL — `stopLoading` falls into the "Unsupported instant action" else; BUSY is never cleared, so `_work_in_progress` stays `"loading"`.

- [ ] **Step 3: Add the completion handler**

Add to `Adapter` (next to `_handle_work_start_instant_action`):

```python
    def _handle_work_stop_instant_action(self, action: Any, stop_type: str) -> None:
        """stopLoading/stopUnloading: inject completion, finish the held work
        action, and clear BUSY."""
        if self.state is None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description="State is not initialized",
            )
            return

        expected_work = "loading" if stop_type == "stopLoading" else "unloading"
        if self._work_in_progress != expected_work:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=f"no active {expected_work} work to stop",
            )
            print(
                f"[WORK STOP IGNORED] type={stop_type} "
                f"active={self._work_in_progress}"
            )
            return

        # Finish the held work action: terminal status auto-removes it from the
        # published instant_action_states.
        if self._work_action_id is not None:
            self._update_instant_action_status(
                self._work_action_id,
                ActionStatus.FINISHED,
                result_description=f"{expected_work} completed",
            )
        self._work_in_progress = None
        self._work_action_id = None
        self._update_instant_action_status(
            action.action_id,
            ActionStatus.FINISHED,
            result_description=f"{expected_work} stopped",
        )
        print(f"[WORK STOP] type={expected_work} -> IDLE")
```

- [ ] **Step 4: Wire the dispatch**

In `instant_actions_accept_procedure`, directly after the `loading`/`unloading` branch from Task 1, add:

```python
            elif action.action_type in ("stopLoading", "stopUnloading"):
                self._handle_work_stop_instant_action(action, action.action_type)
```

- [ ] **Step 5: Advertise in the factsheet**

In `SUPPORTED_INSTANT_ACTIONS`, add:

```python
        "stopLoading",
        "stopUnloading",
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_stop_loading_clears_busy_and_removes_work_action tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_stop_unloading_type_mismatch_fails_and_keeps_busy tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_stop_loading_with_no_active_work_does_not_enter_busy -v`

Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: stopLoading/stopUnloading inject completion and clear BUSY"
```

---

### Task 4: Start preconditions (order active / already BUSY)

**Files:**
- Modify: `adaptor/adapter_jibot.py` — `_handle_work_start_instant_action`.
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` — add tests.

**Interfaces:**
- Consumes: `_is_v3_order_finished` (existing, `adapter_jibot.py:1941`), `_work_in_progress`.

- [ ] **Step 1: Write the failing tests**

```python
def test_loading_rejected_while_already_busy(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        state_task = await self._start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("loading", "work-1")
            )
            # Second loading while already BUSY must not replace the active work.
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("loading", "work-2")
            )
            self.assertEqual(adapter._work_action_id, "work-1")
        finally:
            state_task.cancel()

    asyncio.run(scenario())

def test_loading_rejected_while_order_active(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        state_task = await self._start_state(adapter)
        try:
            # Simulate an in-progress order: node_states non-empty.
            adapter._handle_v3_order(_one_node_order("order-A"))
            self.assertEqual(adapter.state.order_id, "order-A")
            self.assertFalse(adapter._is_v3_order_finished())

            adapter.instant_actions_accept_procedure(
                _work_instant_actions("loading", "work-1")
            )
            self.assertIsNone(adapter._work_in_progress)  # not BUSY
        finally:
            state_task.cancel()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_loading_rejected_while_already_busy tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_loading_rejected_while_order_active -v`

Expected: FAIL — the current handler always sets BUSY, so `_work_action_id` becomes `"work-2"` and `_work_in_progress` becomes `"loading"` even with an active order.

- [ ] **Step 3: Add the preconditions to `_handle_work_start_instant_action`**

In `_handle_work_start_instant_action`, after the `if self.state is None:` block and before `self._work_in_progress = work_type`, insert:

```python
        if self._work_in_progress is not None:
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=(
                    f"work already in progress: {self._work_in_progress}"
                ),
            )
            print(
                f"[WORK START REJECTED] type={work_type} "
                f"active={self._work_in_progress}"
            )
            return

        if self.state.order_id and not self._is_v3_order_finished():
            self._update_instant_action_status(
                action.action_id,
                ActionStatus.FAILED,
                result_description=(
                    f"cannot start {work_type} while an order is in progress"
                ),
            )
            print(
                f"[WORK START REJECTED] type={work_type} "
                f"reason=order-active orderId={self.state.order_id}"
            )
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_loading_rejected_while_already_busy tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_loading_rejected_while_order_active -v`

Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: reject loading/unloading start while busy or order active"
```

---

### Task 5: Block motion instant actions while BUSY + full regression

**Files:**
- Modify: `adaptor/adapter_jibot.py` — new helper + guard at the top of the `instant_actions_accept_procedure` dispatch loop (~line 2730, right after the per-action `print(...)`).
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` — add tests.

**Interfaces:**
- Consumes: `_is_jibot_command_instant_action` (existing, `adapter_jibot.py:3322`), `_work_in_progress`.
- Produces: `Adapter._is_motion_instant_action(self, action) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
def test_start_charging_blocked_while_busy(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        vehicle: FakeVehicle = adapter._vehicle
        state_task = await self._start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("loading", "work-1")
            )
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("startCharging", "charge-1")
            )
            # UmDock must NOT have been issued; BUSY persists.
            self.assertEqual(vehicle.dock_calls, 0)
            self.assertEqual(adapter._work_in_progress, "loading")
        finally:
            state_task.cancel()

    asyncio.run(scenario())

def test_jibot_command_motion_blocked_while_busy(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        vehicle: FakeVehicle = adapter._vehicle
        state_task = await self._start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(
                _work_instant_actions("loading", "work-1")
            )
            goto = InstantActions.from_dict(
                {
                    "headerId": 1,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "actions": [
                        {
                            "actionType": "jibotCommand",
                            "actionId": "goto-1",
                            "blockingType": "NONE",
                            "actionParameters": [
                                {"key": "command", "value": "UmGoto"}
                            ],
                        }
                    ],
                }
            )
            adapter.instant_actions_accept_procedure(goto)
            # No motion was issued; BUSY persists.
            self.assertEqual(vehicle.goto_targets, [])
            self.assertEqual(vehicle.dock_calls, 0)
            self.assertEqual(adapter._work_in_progress, "loading")
        finally:
            state_task.cancel()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_start_charging_blocked_while_busy tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_jibot_command_motion_blocked_while_busy -v`

Expected: FAIL — without the guard, `startCharging` issues `UmDock` (`dock_calls == 1`) and the JIBOT command path runs.

- [ ] **Step 3: Add the motion-detection helper**

Add to `Adapter` (next to `_is_jibot_command_instant_action`):

```python
    def _is_motion_instant_action(self, action: Any) -> bool:
        """True for instant actions that can physically move the robot.

        Conservative: any raw JIBOT command (jibotCommand / jibot* alias /
        direct command-name actionType) can issue UmGoto/UmDock, plus the
        standard startCharging (UmDock). Read-only JIBOT commands are blocked
        too while BUSY by design; allow-list later only if a real need appears.
        """
        return (
            action.action_type == "startCharging"
            or self._is_jibot_command_instant_action(action)
        )
```

- [ ] **Step 4: Add the guard at the top of the dispatch loop**

In `instant_actions_accept_procedure`, inside `for action in instant_actions_request.actions:`, right after the per-action `print(... blockingType ... params ...)` and before `if action.action_type == "cancelOrder":`, add:

```python
            if (
                self._work_in_progress is not None
                and self._is_motion_instant_action(action)
            ):
                self._update_instant_action_status(
                    action.action_id,
                    ActionStatus.FAILED,
                    result_description=(
                        f"busy with {self._work_in_progress} work; motion blocked"
                    ),
                )
                print(
                    f"[WORK BUSY BLOCK] id={action.action_id} "
                    f"type={action.action_type}"
                )
                continue
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_start_charging_blocked_while_busy tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_jibot_command_motion_blocked_while_busy -v`

Expected: PASS (2 tests).

- [ ] **Step 6: Full regression — whole order-worker suite + compile**

Run:
```bash
python -m unittest tests.test_adapter_jibot_v3_order 2>&1 | grep -E "^Ran|^OK|^FAILED|^ERROR"
python -m py_compile adapter_jibot.py config/config.py tests/test_adapter_jibot_v3_order.py
```

Expected: `OK` with the new tests counted, and `py_compile` clean. If a factsheet test pins the exact `SUPPORTED_INSTANT_ACTIONS` list, update that expected list to include the four new action types and re-run.

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: block motion instant actions while loading/unloading BUSY"
```

---

## Self-Review notes

- **Spec coverage:** start/RUNNING (Task 1) · order rejection (Task 2) · completion injection + mismatch/no-active (Task 3) · order-active & already-busy preconditions (Task 4) · movement gating: startCharging + all JIBOT-command instant actions (Task 5) · factsheet advertisement (Tasks 1 & 3) · loading/unloading distinction (shared handlers, both covered). Edge cases `cancelOrder`-does-not-clear-BUSY and process-restart-not-preserved are covered by the design (no code path clears BUSY except `stop*`; fields are in-memory) and need no separate task.
- **Persistence scope:** intentionally in-memory only; no persistence code (matches spec "out of scope").
- **No JIBOT command for loading/unloading:** handlers only mutate state; verified — neither handler touches `self._vehicle`.
