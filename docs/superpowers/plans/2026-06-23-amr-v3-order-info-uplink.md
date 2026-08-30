# AMR v3 Order Info Uplink — Adaptor Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the robot-native order/task as a dedicated `ORDER_INFO` InfoObject inside the VDA5050 v3 `state.information[]` array on every state cycle.

**Architecture:** Mirror the existing `_build_status_information` / `_refresh_status_information` (`JIBOT_STATUS`) pattern. Add a sibling builder/refresher for `ORDER_INFO` and call the refresher in the state publish loop right after the `JIBOT_STATUS` refresh. Standard VDA5050 order fields and `JIBOT_STATUS` are unchanged.

**Tech Stack:** Python 3, asyncio, VDA5050 v3 message dataclasses (`protocol/vda_2_0_0/vda5050_2_0_0_state.py` → `Information`, `InfoReference`, `InfoLevel`), unittest.

Governing spec: `fabris-equipments/docs/superpowers/specs/2026-06-23-amr-v3-order-info-uplink-design.md`.

## Global Constraints

- Wire contract `ORDER_INFO` (consumed by fabris AMR v3 eq): `info_type="ORDER_INFO"`, `info_level=InfoLevel.INFO`, `info_description="Robot-native order/task info"`. References (all string values): `orderId`, `orderUpdateId`, `orderSource`, `cmd`, `goal`, `target`, `status`, `taskInfoStatus`, `station`.
- **Active-order semantics (critical):** `state.order_id` is NOT cleared on order completion. An order counts as active only when `active = bool(state.order_id) and not self._is_v3_order_finished()` (the existing guard at `adapter_jibot.py:4950`; `_is_v3_order_finished` is true when `node_states` and `edge_states` are both empty, defined at `adapter_jibot.py:2600`). Derive the order references from `active`: `orderSource="vda5050"` iff `active` else `"native"`; `orderId = state.order_id if active else ""`; `orderUpdateId = state.order_update_id if active else 0`. Using bare `state.order_id` presence would misreport a robot running a native task after a completed VDA order.
- Always emit `ORDER_INFO` whenever a vehicle is present (empty strings for missing sub-values). Never raise on missing data — use defensive `getattr`/`isinstance` reads exactly like `_build_status_information`.
- Do NOT change standard `state.order_id` / `state.order_update_id` population.
- Do NOT remove or change `JIBOT_STATUS` `jibotCurTask*` references (backward compat).
- File path for the v3 state message builder already serializes `information=[info.to_dict() for info in self.state.information]`, so adding to `state.information` is sufficient; no message-builder change.
- Commits: this branch (`jibot-client-refactor`) already has unrelated uncommitted changes. Stage only the explicit files listed; do NOT `git add -A`/`git add .`; defer the actual commit to the user unless they ask.

---

### Task 1: Emit `ORDER_INFO` InfoObject every state cycle

**Files:**
- Modify: `adaptor/adapter_jibot.py` — add `_build_order_information()` + `_refresh_order_information()` near `_build_status_information` (currently lines 740–804); add the refresh call right after `self._refresh_status_information()` (currently line 487).
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` — add one test method to class `AdapterV3OrderTest` (line 499), mirroring `test_jibot_task_and_path_diagnostics_surface_in_information` (line 575).

**Interfaces:**
- Consumes: `self._vehicle._cur_task` (`{"data": {"status": str, "value": {"cmd": str, "goal": str, "target": str}}}`), `self._vehicle._task_info` (`{"status": str}`), `self._vehicle._station` (str), `self.state.order_id` (str), `self.state.order_update_id` (int), `self._is_v3_order_finished()` (bool, defined at `adapter_jibot.py:2600`). `Information`, `InfoReference`, `InfoLevel` are already imported at `adapter_jibot.py:24`.
- Produces: `state.information[]` entry with `info_type == "ORDER_INFO"` and the references defined in Global Constraints.

- [ ] **Step 1: Add the `NodeState` import to the test module**

In `adaptor/tests/test_adapter_jibot_v3_order.py`, the import at line 27 is:

```python
from protocol.vda_2_0_0.vda5050_2_0_0_state import (
    ActionState,
    ActionStatus,
    Error,
    ErrorLevel,
    ErrorReference,
    ErrorType,
)
```

Add `NodeState` (keep alphabetical-ish order, matches existing dataclass exports):

```python
from protocol.vda_2_0_0.vda5050_2_0_0_state import (
    ActionState,
    ActionStatus,
    Error,
    ErrorLevel,
    ErrorReference,
    ErrorType,
    NodeState,
)
```

- [ ] **Step 2: Write the failing branch test (native / active / finished)**

Add to class `AdapterV3OrderTest` in `adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
    def test_order_info_surfaces_in_information(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle = adapter._vehicle
            vehicle._station = "F1_60"
            vehicle._cur_task = {
                "#CMD#": "UmGetCurTask",
                "data": {
                    "status": "nrunto F1_60",
                    "value": {"cmd": "goto", "goal": "F1_60", "target": "goal"},
                },
            }
            vehicle._task_info = {"#CMD#": "UmGetTaskInfo", "status": "nrunto F1_60"}
            state_task = await self._start_state(adapter)

            def order_info_refs():
                infos = [
                    info for info in adapter.state.information
                    if info.info_type == "ORDER_INFO"
                ]
                self.assertEqual(len(infos), 1)  # refresh replaces, never duplicates
                return {ref.reference_key: ref.reference_value for ref in infos[0].info_references}

            try:
                # native: no VDA5050 order at all
                adapter.state.order_id = ""
                adapter.state.order_update_id = 0
                adapter.state.node_states = []
                adapter.state.edge_states = []
                adapter._refresh_order_information()
                native = order_info_refs()
                self.assertEqual(native["orderSource"], "native")
                self.assertEqual(native["orderId"], "")
                self.assertEqual(native["cmd"], "goto")
                self.assertEqual(native["goal"], "F1_60")
                self.assertEqual(native["target"], "goal")
                self.assertEqual(native["status"], "nrunto F1_60")
                self.assertEqual(native["taskInfoStatus"], "nrunto F1_60")
                self.assertEqual(native["station"], "F1_60")

                # vda5050: order set AND in progress (node_states non-empty)
                adapter.state.order_id = "order-7"
                adapter.state.order_update_id = 3
                adapter.state.node_states = [NodeState(node_id="N2", sequence_id=2, released=True)]
                adapter.state.edge_states = []
                adapter._refresh_order_information()
                vda = order_info_refs()
                self.assertEqual(vda["orderSource"], "vda5050")
                self.assertEqual(vda["orderId"], "order-7")
                self.assertEqual(vda["orderUpdateId"], "3")

                # finished: order_id lingers but node/edge states drained -> native
                adapter.state.order_id = "order-7"
                adapter.state.order_update_id = 3
                adapter.state.node_states = []
                adapter.state.edge_states = []
                adapter._refresh_order_information()
                finished = order_info_refs()
                self.assertEqual(finished["orderSource"], "native")
                self.assertEqual(finished["orderId"], "")
                self.assertEqual(finished["orderUpdateId"], "0")
            finally:
                state_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_order_info_surfaces_in_information -v`
Expected: FAIL with `AttributeError: ... object has no attribute '_refresh_order_information'`.

- [ ] **Step 4: Add the builder and refresher**

In `adaptor/adapter_jibot.py`, immediately after `_refresh_status_information` (ends ~line 804), add:

```python
    def _build_order_information(self) -> List[Information]:
        if self._vehicle is None:
            return []

        cur_task = getattr(self._vehicle, "_cur_task", None) or {}
        cur_task_data = cur_task.get("data", {}) if isinstance(cur_task, dict) else {}
        cur_task_value = (
            cur_task_data.get("value", {}) if isinstance(cur_task_data, dict) else {}
        )
        task_info = getattr(self._vehicle, "_task_info", None) or {}

        raw_order_id = str(getattr(self.state, "order_id", "") or "") if self.state is not None else ""
        raw_order_update_id = (
            getattr(self.state, "order_update_id", 0) if self.state is not None else 0
        )
        # state.order_id lingers after completion; an order is active only while
        # node/edge states remain (mirrors the guard at adapter_jibot.py:4950).
        order_active = bool(raw_order_id) and self.state is not None and not self._is_v3_order_finished()
        order_id = raw_order_id if order_active else ""
        order_update_id = raw_order_update_id if order_active else 0
        order_source = "vda5050" if order_active else "native"

        references = [
            InfoReference("orderId", order_id),
            InfoReference("orderUpdateId", str(order_update_id if order_update_id is not None else 0)),
            InfoReference("orderSource", order_source),
            InfoReference("cmd", str(cur_task_value.get("cmd", "") if isinstance(cur_task_value, dict) else "")),
            InfoReference("goal", str(cur_task_value.get("goal", "") if isinstance(cur_task_value, dict) else "")),
            InfoReference("target", str(cur_task_value.get("target", "") if isinstance(cur_task_value, dict) else "")),
            InfoReference("status", str(cur_task_data.get("status", "") if isinstance(cur_task_data, dict) else "")),
            InfoReference("taskInfoStatus", str(task_info.get("status", "") if isinstance(task_info, dict) else "")),
            InfoReference("station", str(getattr(self._vehicle, "_station", "") or "")),
        ]

        return [
            Information(
                info_type="ORDER_INFO",
                info_level=InfoLevel.INFO,
                info_references=references,
                info_description="Robot-native order/task info",
            ),
        ]

    def _refresh_order_information(self) -> None:
        if self.state is None:
            return

        self.state.information = [
            info
            for info in self.state.information
            if getattr(info, "info_type", None) != "ORDER_INFO"
        ]
        self.state.information.extend(self._build_order_information())
```

- [ ] **Step 5: Wire the refresher into the publish loop**

In `adaptor/adapter_jibot.py`, find (line ~487):

```python
                self._refresh_status_information()
                self._refresh_jibot_status_errors()
```

Change to:

```python
                self._refresh_status_information()
                self._refresh_order_information()
                self._refresh_jibot_status_errors()
```

- [ ] **Step 6: Run the branch test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_order_info_surfaces_in_information -v`
Expected: PASS (OK).

- [ ] **Step 7: Add the publish-loop wiring test, run it, and confirm it guards Step 5**

This test does NOT call `_refresh_order_information()` directly — it relies on the
real `publish_state` loop started by `_start_state()` to populate `ORDER_INFO`, so it
fails if Step 5's loop wiring is missing. Add to class `AdapterV3OrderTest`:

```python
    def test_order_info_published_by_state_loop(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle = adapter._vehicle
            vehicle._station = "F1_60"
            vehicle._cur_task = {
                "#CMD#": "UmGetCurTask",
                "data": {
                    "status": "nrunto F1_60",
                    "value": {"cmd": "goto", "goal": "F1_60", "target": "goal"},
                },
            }
            vehicle._task_info = {"#CMD#": "UmGetTaskInfo", "status": "nrunto F1_60"}
            state_task = await self._start_state(adapter)
            try:
                # let the publish loop (interval 0.05s) run a few cycles
                refs = None
                for _ in range(100):
                    infos = [
                        info for info in adapter.state.information
                        if info.info_type == "ORDER_INFO"
                    ]
                    if infos:
                        refs = {r.reference_key: r.reference_value for r in infos[0].info_references}
                        break
                    await asyncio.sleep(0.02)
                self.assertIsNotNone(refs, "ORDER_INFO not published by the state loop (loop wiring missing?)")
                self.assertEqual(refs["cmd"], "goto")
                self.assertEqual(refs["station"], "F1_60")
            finally:
                state_task.cancel()

        asyncio.run(scenario())
```

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_order_info_published_by_state_loop -v`
Expected: PASS. (Sanity check the guard: with the Step 5 edit temporarily reverted the test FAILS on the `assertIsNotNone`; restore Step 5 and re-run to PASS.)

- [ ] **Step 8: Run the full order test module (no regression)**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order -v`
Expected: OK — existing tests including `test_jibot_task_and_path_diagnostics_surface_in_information` still pass.

- [ ] **Step 9: Commit (explicit targets only; defer to user if commits are gated)**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(adaptor): publish robot-native order as ORDER_INFO state information"
```

---

## Self-Review

- **Spec coverage:** Wire contract `ORDER_INFO` (Step 4 builder), always-emit (builder returns object whenever vehicle present; refresh called every cycle in Step 5), active-order semantics — `orderSource`/`orderId`/`orderUpdateId` derived from `active = order_id and not _is_v3_order_finished()` (Step 4 builder; Step 2 test covers native / active / finished branches), publish-loop wiring guarded by a loop-driven test (Step 7), standard fields untouched (no change to order_id population), `JIBOT_STATUS` untouched (not modified). Covered.
- **Placeholder scan:** none.
- **Type consistency:** `_build_order_information`/`_refresh_order_information`/`_is_v3_order_finished` names match across builder, refresh, loop wiring, and tests. `NodeState` imported (Step 1). `info_type="ORDER_INFO"` consistent with fabris plan's `ORDER_INFO_INFO_TYPE`.
