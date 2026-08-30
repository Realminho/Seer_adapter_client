# lastNodeId VDA5050 Compliance + Nearest-Node Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore `lastNodeId` to its VDA5050 reached-node meaning and move the "nearest map node" behavior into a separate `_nearest_node_*` variable surfaced as a `NEAREST_NODE` telemetry entry.

**Architecture:** The state-publish loop currently overwrites `lastNodeId` every cycle with the globally nearest map node. We repurpose that per-cycle call to maintain a new nearest-node variable only, while `lastNodeId` is advanced solely by the order worker on arrival (plus reset/station/idle lifecycle seeding). The nearest-node info entry is renamed `LAST_NODE_GAP` → `NEAREST_NODE` with key `lastNodeId` → `nearestNodeId`.

**Tech Stack:** Python 3.10+, asyncio, `unittest`. VDA5050 v3 message dataclasses under `adaptor/protocol/`.

**Spec:** `docs/superpowers/specs/2026-06-20-last-node-id-vda5050-design.md`

## Global Constraints

- All tests are offline: fake vehicle / in-process simulator, no MQTT broker or real robot.
- Pose and node coordinates are in the robot's raw units (mm), unconverted.
- `lastNodeSequenceId` must be an integer (VDA5050 schema); never `None`.
- `state.information` is visualization/debug only — never used for fleet-control logic (`adaptor/protocol/vda5050_v3/json_schemas/state.schema:212`).
- During an active order (`state.order_id` non-empty), position must NEVER move `lastNodeId`; only confirmed arrival advances it.
- Single source file for logic: `adaptor/adapter_jibot.py`. Tests in `adaptor/tests/`.
- Run tests from the `adaptor/` directory: `cd adaptor && python -m pytest <path> -v` (offline).

## File Structure

- Modify `adaptor/adapter_jibot.py`:
  - `__init__` (`:150`): add `_nearest_node_id`, `_nearest_node_sequence_id`, `_nearest_node_distance` fields.
  - `_refresh_last_node_gap_information` (`:4162`): rename → `_refresh_nearest_node_information`; add `_clear_nearest_node_information` helper; use `NEAREST_NODE` / `nearestNodeId`.
  - `_update_last_node_from_position` (`:4197`): rename → `_update_nearest_node_from_position`; write `_nearest_node_*`; add no-match clearing and idle bootstrap; stop writing `_last_node_*` during active orders.
  - publish loop call site (`:467`): update method name.
- Modify `adaptor/tests/test_adapter_jibot_v3_order.py`: reconcile 4 existing tests; add new unit tests.
- Modify `adaptor/tests/test_initial_pose_and_map.py`: reconcile 2 existing tests.

Unchanged: `_find_nearest_node` (`:4114`) keeps returning `(node_id, sequence_id, distance)`; the arrival writer (`:2552`) and station bootstrap (`:1744`) keep their behavior.

---

### Task 1: Nearest-node fields + telemetry helper (`NEAREST_NODE`)

**Files:**
- Modify: `adaptor/adapter_jibot.py:150-151` (fields), `:4162-4195` (helper)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` (new unit test; one assertion line in an existing test)

**Interfaces:**
- Produces: `self._nearest_node_id: str`, `self._nearest_node_sequence_id: int`, `self._nearest_node_distance: Optional[float]`; methods `_refresh_nearest_node_information(self, node_id: str, distance: float) -> None` and `_clear_nearest_node_information(self) -> None`. Info entry: `info_type="NEAREST_NODE"`, references `nearestNodeId` + `gap`.

- [ ] **Step 1: Write the failing test**

Add to `adaptor/tests/test_adapter_jibot_v3_order.py` inside `AdapterV3OrderTest`:

```python
    def test_refresh_nearest_node_information_uses_nearest_keys(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._refresh_nearest_node_information("S2", 12.3)
                entry = next(
                    info for info in adapter.state.information
                    if getattr(info, "info_type", None) == "NEAREST_NODE"
                )
                refs = {r.reference_key: r.reference_value for r in entry.info_references}
                self.assertEqual(refs["nearestNodeId"], "S2")
                self.assertEqual(refs["gap"], "12.3")
                self.assertFalse(
                    any(getattr(i, "info_type", None) == "LAST_NODE_GAP"
                        for i in adapter.state.information)
                )

                adapter._clear_nearest_node_information()
                self.assertFalse(
                    any(getattr(i, "info_type", None) == "NEAREST_NODE"
                        for i in adapter.state.information)
                )
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_refresh_nearest_node_information_uses_nearest_keys -v`
Expected: FAIL with `AttributeError: 'Adapter' object has no attribute '_refresh_nearest_node_information'`

- [ ] **Step 3: Add the nearest-node fields**

In `adaptor/adapter_jibot.py`, after `self._last_node_sequence_id: int = 0` (`:151`), add:

```python
        self._nearest_node_id: str = ""
        self._nearest_node_sequence_id: int = 0
        self._nearest_node_distance: Optional[float] = None
```

- [ ] **Step 4: Rename and rewrite the telemetry helper**

Replace the whole method `_refresh_last_node_gap_information` (`:4162-4195`) with:

```python
    def _clear_nearest_node_information(self) -> None:
        """Drop any existing NEAREST_NODE info entry from the state."""
        if self.state is None:
            return
        self.state.information = [
            info
            for info in self.state.information
            if getattr(info, "info_type", None) != "NEAREST_NODE"
        ]

    def _refresh_nearest_node_information(
        self,
        node_id: str,
        distance: float,
    ) -> None:
        """Publish how far the robot is from the nearest map node.

        This is visualization/debug telemetry only (VDA5050 forbids using
        state.information for fleet-control logic). The gap is reported in the
        robot's raw pose units; the unit is advertised once in the factsheet
        (coordinateUnits.position).
        """
        self._clear_nearest_node_information()
        if self.state is None:
            return
        self.state.information.append(
            Information(
                info_type="NEAREST_NODE",
                info_level=InfoLevel.INFO,
                info_references=[
                    InfoReference("nearestNodeId", node_id),
                    InfoReference("gap", f"{distance:.1f}"),
                ],
                info_description=(
                    "Distance between the robot and the nearest map node, "
                    "in factsheet coordinateUnits.position (telemetry only)"
                ),
            )
        )
```

- [ ] **Step 5: Reconcile the existing info_type assertion**

In `adaptor/tests/test_adapter_jibot_v3_order.py`, find this line in `test_active_order_last_node_follows_nearest_xy_position`:

```python
                self.assertEqual(adapter.state.information[-1].info_type, "LAST_NODE_GAP")
```

Replace with:

```python
                self.assertEqual(adapter.state.information[-1].info_type, "NEAREST_NODE")
```

(The method `_update_last_node_from_position` still calls the renamed helper after Task 2; until then this test exercises the helper via the old method name, which still calls `_refresh_nearest_node_information` once Step 4's caller is updated. To keep this task self-contained, also update the single call inside `_update_last_node_from_position` at `:4218` from `self._refresh_last_node_gap_information(node_id, distance)` to `self._refresh_nearest_node_information(node_id, distance)`.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_refresh_nearest_node_information_uses_nearest_keys tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_active_order_last_node_follows_nearest_xy_position -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: rename lastNode gap telemetry to NEAREST_NODE; add nearest-node fields"
```

---

### Task 2: Per-cycle updater split — nearest write, no-match clear, idle bootstrap

**Files:**
- Modify: `adaptor/adapter_jibot.py:4197-4223` (the updater), `:467-471` (call site)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` (3 new unit tests + reconcile 4 existing), `adaptor/tests/test_initial_pose_and_map.py` (reconcile 2 existing)

**Interfaces:**
- Consumes: `_find_nearest_node(x, y) -> Optional[Tuple[str, int, float]]`; `_refresh_nearest_node_information`, `_clear_nearest_node_information` (Task 1).
- Produces: `_update_nearest_node_from_position(self, x: float, y: float, theta: Optional[float] = None) -> None` — writes `_nearest_node_*`; during an active order never writes `_last_node_*`; when idle (no active order and `_last_node_id == ""`) seeds `_last_node_*` from the nearest node; on no match clears `_nearest_node_*` and the NEAREST_NODE info.

- [ ] **Step 1: Write the failing tests (3 new unit tests)**

Add to `AdapterV3OrderTest` in `adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
    def test_update_nearest_does_not_touch_last_node_during_active_order(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0

                # Robot drifts nearest to the later node while the order is active.
                adapter._update_nearest_node_from_position(14040.0, 3750.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._nearest_node_sequence_id, 4)
                # lastNodeId must NOT be dragged forward by position.
                self.assertEqual(adapter._last_node_id, "F1_60")
                self.assertEqual(adapter._last_node_sequence_id, 0)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_idle_bootstrap_seeds_last_node(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "A": (0.0, 0.0, 0.0),
                "B": (1000.0, 0.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""   # no active order
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "B")  # idle bootstrap
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_no_match_clears_fields_and_information(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {}   # nothing to match
            state_task = await self._start_state(adapter)
            try:
                adapter.order = None
                adapter.state.order_id = ""
                adapter.state.node_states = []
                adapter._nearest_node_id = "STALE"
                adapter._nearest_node_sequence_id = 7
                adapter._nearest_node_distance = 3.0
                adapter._refresh_nearest_node_information("STALE", 3.0)

                adapter._update_nearest_node_from_position(5.0, 5.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "")
                self.assertEqual(adapter._nearest_node_sequence_id, 0)
                self.assertIsNone(adapter._nearest_node_distance)
                self.assertFalse(
                    any(getattr(i, "info_type", None) == "NEAREST_NODE"
                        for i in adapter.state.information)
                )
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "update_nearest" -v`
Expected: FAIL with `AttributeError: 'Adapter' object has no attribute '_update_nearest_node_from_position'`

- [ ] **Step 3: Rewrite the updater method**

Replace the whole method `_update_last_node_from_position` (`:4197-4223`) with:

```python
    def _update_nearest_node_from_position(
        self,
        x: float,
        y: float,
        theta: Optional[float] = None,
    ) -> None:
        debug = bool(getattr(self.config.settings, "debug_log", False))
        matched_node = self._find_nearest_node(x, y)
        if matched_node is None:
            # No map node and no order nodePosition to match against: clear the
            # nearest-node telemetry so stale values are never published.
            self._nearest_node_id = ""
            self._nearest_node_sequence_id = 0
            self._nearest_node_distance = None
            self._clear_nearest_node_information()
            if debug:
                print(
                    f"[NEAREST NODE] no match for pos=({x:.1f}, {y:.1f}); "
                    f"orderNodes={len(self._all_order_nodes())} "
                    f"mapNodes={len(self._map_nodes())}"
                )
            return

        node_id, sequence_id, distance = matched_node
        self._nearest_node_id = node_id
        self._nearest_node_sequence_id = sequence_id
        self._nearest_node_distance = distance
        self._refresh_nearest_node_information(node_id, distance)

        # Idle/startup bootstrap: with no active order and no reached node yet,
        # seed lastNodeId from the nearest node so a parked/just-booted robot
        # reports the node it sits on. A missing state is treated as "no active
        # order". During an active order lastNodeId is advanced ONLY by arrival.
        order_id = getattr(self.state, "order_id", "") if self.state is not None else ""
        if not order_id and not self._last_node_id:
            self._last_node_id = node_id
            self._last_node_sequence_id = sequence_id

        if debug:
            print(
                f"[NEAREST NODE] pos=({x:.1f}, {y:.1f}) -> nearest={node_id} "
                f"seq={sequence_id} gap={distance:.1f}mm"
            )
```

- [ ] **Step 4: Update the publish-loop call site**

In `adaptor/adapter_jibot.py:467`, change:

```python
            self._update_last_node_from_position(
                agv_position.x,
                agv_position.y,
                agv_position.theta,
            )
```

to:

```python
            self._update_nearest_node_from_position(
                agv_position.x,
                agv_position.y,
                agv_position.theta,
            )
```

(Leave the following `self.state.last_node_id = self._last_node_id` / `self.state.last_node_sequence_id = self._last_node_sequence_id` lines at `:472-473` unchanged — they now mirror the arrival/idle-seeded values.)

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "update_nearest" -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Reconcile the 3 existing nearest-behavior tests in `test_adapter_jibot_v3_order.py`**

These three assert the OLD behavior (`_last_node_id` follows nearest). Update each to assert the NEW behavior on `_nearest_node_*`, and that `lastNodeId` is no longer position-driven.

In `test_active_order_last_node_follows_nearest_xy_position`, replace the two assertions:

```python
                self.assertEqual(adapter._last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_sequence_id, 4)
```

with:

```python
                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._nearest_node_sequence_id, 4)
                # lastNodeId stays at the reached node, not the nearest one.
                self.assertEqual(adapter._last_node_id, "F1_60")
```

(This test sets `adapter._last_node_id = "F1_60"` and `adapter.state.order_id` before the call. If it does not already, add `adapter.state.order_id = "nearest-xy-order"`, `adapter._last_node_id = "F1_60"`, `adapter._last_node_sequence_id = 0` immediately before `adapter._update_last_node_from_position(...)`, and rename that call to `adapter._update_nearest_node_from_position(...)`.)

In `test_last_node_can_move_to_lower_sequence_when_xy_is_nearest`, rename the call to `_update_nearest_node_from_position` and replace:

```python
                self.assertEqual(adapter._last_node_id, "F1_60")
                self.assertEqual(adapter._last_node_sequence_id, 0)
```

with:

```python
                # The NEAREST variable may take a lower sequence; lastNodeId is
                # monotonic and must NOT regress during an active order.
                self.assertEqual(adapter._nearest_node_id, "F1_60")
                self.assertEqual(adapter._nearest_node_sequence_id, 0)
                self.assertEqual(adapter._last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._last_node_sequence_id, 4)
```

(Ensure the test sets `adapter.state.order_id = "lower-seq-order"` so the active-order guard applies; it already sets `_last_node_id = "F2_90_S2CH"` / `_last_node_sequence_id = 4`.)

In `test_last_node_uses_nearest_map_node_outside_active_order`, rename the call to `_update_nearest_node_from_position` and replace:

```python
                self.assertEqual(adapter._last_node_id, "SIDE_NODE")
                self.assertEqual(adapter._last_node_sequence_id, 0)
```

with:

```python
                self.assertEqual(adapter._nearest_node_id, "SIDE_NODE")
                self.assertEqual(adapter._nearest_node_sequence_id, 0)
```

- [ ] **Step 7: Reconcile the active-order publish-loop regression test (`:1689`)**

`test_active_order_state_loop_updates_last_node_from_nearest_position` asserts the publish loop pulls `state.last_node_id` to the nearest node during an active order. Invert it. Replace its three terminal assertions:

```python
                self.assertEqual(adapter.state.last_node_id, "F2_90_S2CH")
                self.assertEqual(adapter.state.last_node_sequence_id, 4)
                self.assertEqual(vehicle.goto_targets, ["F1_60"])
```

with:

```python
                # The publish loop must NOT drag lastNodeId to the nearest node
                # during an active order; it tracks nearest separately instead.
                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
                self.assertEqual(adapter._nearest_node_sequence_id, 4)
                self.assertNotEqual(adapter.state.last_node_id, "F2_90_S2CH")
                self.assertEqual(vehicle.goto_targets, ["F1_60"])
```

- [ ] **Step 8: Reconcile the simulator initial-pose tests**

In `adaptor/tests/test_initial_pose_and_map.py`, both tests call `adapter._update_last_node_from_position(...)` with no active order. Rename the calls to `_update_nearest_node_from_position` and keep the `_last_node_id` assertions (idle bootstrap), adding a `_nearest_node_id` assertion.

In `test_last_node_id_resolved_from_loaded_map_and_pose` (`:241-242`):

```python
        adapter._update_nearest_node_from_position(vehicle._x, vehicle._y, vehicle._th)
        self.assertEqual(adapter._last_node_id, "S2")
        self.assertEqual(adapter._nearest_node_id, "S2")
```

In `test_last_node_id_starts_on_random_loaded_map_node` (`:261-263`):

```python
        adapter._update_nearest_node_from_position(vehicle._x, vehicle._y, vehicle._th)
        self.assertIn(adapter._last_node_id, initial_map["nodes"])
        self.assertEqual(adapter._last_node_id, vehicle._station)
        self.assertEqual(adapter._nearest_node_id, adapter._last_node_id)
```

- [ ] **Step 9: Run both reconciled test files**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py tests/test_initial_pose_and_map.py -v`
Expected: PASS (all tests in both files green)

- [ ] **Step 10: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py adaptor/tests/test_initial_pose_and_map.py
git commit -m "feat: split nearest-node from lastNodeId; lastNodeId advances only on arrival/idle-bootstrap"
```

---

### Task 3: Additive lastNodeId reached-node coverage

**Files:**
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` (new tests; no production code change expected)

**Interfaces:**
- Consumes: the arrival writer (`_process_v3_order_queue`, `:2552`) and `_update_nearest_node_from_position` (Task 2). No new production symbols.

- [ ] **Step 1: Add a publish-loop regression-guard test**

Add to `AdapterV3OrderTest`:

```python
    def test_publish_loop_does_not_overwrite_last_node_during_active_order(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._x = 14040.0
            vehicle._y = 3750.0
            vehicle._map_nodes = {
                "F1_60": (12955.0, 4601.0, 0.0),
                "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("F1_60", 0, True, []),
                    Node("F2_90_S2CH", 4, True, []),
                ]
                adapter._last_node_id = "F1_60"
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = "F1_60"
                adapter.state.last_node_sequence_id = 0

                # Let several publish cycles run.
                await asyncio.sleep(0.2)

                self.assertEqual(adapter.state.last_node_id, "F1_60")
                self.assertEqual(adapter._nearest_node_id, "F2_90_S2CH")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())
```

- [ ] **Step 2: Add a coordinate-less best-effort advance test**

```python
    def test_coordinate_less_node_advances_last_node_on_completion(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)

            order = Order.from_dict(
                {
                    "headerId": 80,
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "version": "3.0.0",
                    "manufacturer": "jibot",
                    "serialNumber": "HN-SH6-TR-001",
                    "orderId": "coord-less-order",
                    "orderUpdateId": 0,
                    "nodes": [
                        {"nodeId": "NX", "sequenceId": 0, "released": True, "actions": []}
                    ],
                    "edges": [],
                }
            )

            try:
                adapter._handle_v3_order(order)
                await asyncio.wait_for(adapter.order_worker_task, timeout=2.0)
                self.assertEqual(adapter._last_node_id, "NX")
                self.assertEqual(adapter._last_node_sequence_id, 0)
            finally:
                state_task.cancel()
                if adapter.order_worker_task is not None and not adapter.order_worker_task.done():
                    adapter.order_worker_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 3: Run the new tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "publish_loop_does_not_overwrite or coordinate_less_node_advances" -v`
Expected: PASS (2 passed). If `test_coordinate_less_node_advances_last_node_on_completion` hangs or fails because the worker waits on arrival, confirm `_resolve_node_target` returns `None` for a node with no `nodePosition` and no map entry (`:2537-2544`); the node should complete on send. Adjust the order/vehicle so no coordinates are resolvable.

- [ ] **Step 4: Commit**

```bash
git add adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "test: cover lastNodeId reached-node semantics (no overwrite, coordinate-less advance)"
```

---

### Task 4: Full offline suite + housekeeping

**Files:**
- Optional: delete `adaptor/adapter_jibot_.py` (git-tracked legacy backup, imported nowhere)

- [ ] **Step 1: Run the full offline adaptor suite**

Run: `cd adaptor && python -m pytest tests/ -v`
Expected: PASS (entire suite green). In particular `test_order_waits_for_arrival_and_executes_actions` and `test_order_worker_completes_steps_with_simulator_vehicle` still pass unchanged (arrival path regression guard).

- [ ] **Step 2: Confirm no remaining references to the old names**

Run: `cd adaptor && grep -rn "_update_last_node_from_position\|_refresh_last_node_gap_information\|LAST_NODE_GAP" adapter_jibot.py tests/`
Expected: no matches (empty output). If any appear, update them and re-run Step 1.

- [ ] **Step 3 (optional): Remove the stray backup file**

The pre-v3 legacy chain lives only in the git-tracked backup `adaptor/adapter_jibot_.py` (imported nowhere). If housekeeping is in scope:

Run: `git rm adaptor/adapter_jibot_.py`

(It is git-tracked, so the removal is a real commit; skip this step if the team wants to keep the backup.)

- [ ] **Step 4: Commit any remaining changes**

```bash
git add -A adaptor/
git commit -m "chore: finalize lastNodeId/nearest-node split" --allow-empty
```

---

## Self-Review

**Spec coverage:**
- VDA5050 `lastNodeId` advanced only on arrival → Task 2 (stop per-cycle write) + Task 3 (coverage). ✓
- Lifecycle writers (reset/bootstrap/idle) → Task 2 idle bootstrap; reset/station bootstrap unchanged in code. ✓
- Nearest variable `_nearest_node_*` → Task 1 (fields) + Task 2 (writes). ✓
- No-match clearing → Task 2 Step 1/3. ✓
- `LAST_NODE_GAP` → `NEAREST_NODE` rename + `nearestNodeId` key → Task 1. ✓
- Telemetry-only / control-correction migration → reflected by tests (no control consumer reads NEAREST_NODE); no code beyond rename. ✓
- Missed tests (`:1689`, initial-pose `:216`/`:244`) → Task 2 Steps 7-8. ✓
- Legacy already removed; stray file housekeeping → Task 4 Step 3. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `_update_nearest_node_from_position` / `_refresh_nearest_node_information` / `_clear_nearest_node_information` used identically across tasks; `InfoReference(reference_key, reference_value)`, `Node(node_id, sequence_id, released, actions)`, `_find_nearest_node -> (node_id, sequence_id, distance)` consistent. ✓
