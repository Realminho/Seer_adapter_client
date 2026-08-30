# lastNodeId Idle / Non-Active Pass-Through Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resume idle `lastNodeId` capture **after an order finishes** (and during
free-roam/parking) by making the capture gate finished-aware, so `lastNodeId`
stops freezing at the last order node once `order_id` is retained post-completion.

**Architecture:** One gate change in `_update_nearest_node_from_position`
(`adaptor/adapter_jibot.py`), the per-cycle publish-loop updater. The bare
`not order_id` gate becomes `not self._is_v3_order_active()` (finished-aware).
Capture still never runs during an active order, so the order worker remains the
single `lastNodeId` writer during orders — no second writer, no regression, no
state/queue desync. Nearest-node computation, the arrival writer, prune, and
rebuild are untouched.

**Tech Stack:** Python 3.11+, asyncio, unittest. Tests are fully offline (fake
vehicle; no MQTT broker or robot).

Governing spec:
`docs/superpowers/specs/2026-06-23-last-node-id-runtime-passthrough-capture-design.md`.

## Global Constraints

- **Capture only when no order is active.** Gate on
  `not self._is_v3_order_active()`. Do NOT capture during an active order — that
  path was reviewed and withdrawn as unsafe (dual writer vs. the order state
  machine). See the spec's "Why not in-order capture (withdrawn)".
- **Do not touch** `_finalize_v3_node_step` (`:3414`), `_prune_v3_order_state_before_last_node`,
  `_rebuild_v3_order_queue_from_state`, the order worker, `_nearest_node_*`, or the
  `NEAREST_NODE` entry.
- **No cross-restart persistence.** Cold boot keeps `lastNodeId == ""` until first
  in-reach capture.
- **Config key unchanged:** reuse `idle_last_node_reach_xy` (default `500.0` mm,
  `adaptor/config/config.py:74`) and `_idle_last_node_reach_xy()`
  (`adapter_jibot.py:5638`). Do not rename. Update only its comment.
- **Active-order predicate** mirrors the existing inline pattern at
  `adapter_jibot.py:4950`: `bool(state.order_id) and not self._is_v3_order_finished()`.
  `state.order_id` is NOT cleared on completion.
- **Commits:** branch `jibot-client-refactor` has unrelated uncommitted changes.
  Stage only the listed files; do NOT `git add -A`/`git add .`.
- Run tests from the `adaptor/` directory.

---

### Task 1: Finished-aware idle capture gate

A single behavior change: idle capture resumes once an order is finished
(`order_id` retained but `_is_v3_order_finished()` true), covering free-roam and
post-completion parking. Existing active-order and idle tests are unaffected (the
gate is still off during an active order); two new tests cover the finished case
and guard against the withdrawn map-only-seed concern.

**Files:**
- Modify: `adaptor/adapter_jibot.py`
  - Add `_is_v3_order_active()` near `_is_v3_order_finished()` (`:2600-2603`).
  - Change the capture gate in `_update_nearest_node_from_position` (`:5823-5832`).
- Modify: `adaptor/config/config.py` — update the `idle_last_node_reach_xy` comment (`:68-73`).
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` — add two methods to
  `AdapterV3OrderTest`.

**Interfaces:**
- Produces: `_is_v3_order_active(self) -> bool` on `Adapter`.
- Consumes (existing): `_find_nearest_node` → `(node_id, sequence_id, distance)`,
  `_idle_last_node_reach_xy() -> float`, `_is_v3_order_finished() -> bool`,
  `self.state.order_id`, `self._last_node_id` / `self._last_node_sequence_id`.
- Test setup (existing pattern in this file): active order = `state.order_id`
  non-empty + `state.node_states` a non-empty list of
  `Node(node_id, sequence_id, released, actions)` (imported at
  `test_adapter_jibot_v3_order.py:35`). Finished order = `order_id` set +
  `node_states`/`edge_states` empty. Idle = `order_id == ""`.

- [ ] **Step 1: Write the two new tests**

In `adaptor/tests/test_adapter_jibot_v3_order.py`, add to `AdapterV3OrderTest`
(e.g. right after `test_update_nearest_no_match_clears_fields_and_information`):

```python
    def test_update_nearest_finished_order_parks_like_idle(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {"PARK": (0.0, 0.0, 0.0)}
            state_task = await self._start_state(adapter)
            try:
                # order_id retained but the order is finished (no node/edge
                # states) -> _is_v3_order_active() is False -> idle proximity
                # capture resumes and follows the robot to where it parks.
                adapter.order = None
                adapter.state.order_id = "done-order"
                adapter.state.node_states = []
                adapter.state.edge_states = []
                adapter._last_node_id = "OLD"
                adapter._last_node_sequence_id = 5
                adapter.state.last_node_id = "OLD"
                adapter.state.last_node_sequence_id = 5

                adapter._update_nearest_node_from_position(30.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "PARK")
                self.assertEqual(adapter._last_node_id, "PARK")
                self.assertEqual(adapter.state.last_node_id, "PARK")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_update_nearest_active_order_does_not_seed_map_only_node(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            vehicle._map_nodes = {
                "N0": (0.0, 0.0, 0.0),
                "SIDE": (50.0, 0.0, 0.0),   # not part of the order
            }
            state_task = await self._start_state(adapter)
            try:
                adapter.order = None
                adapter.state.order_id = "active"
                adapter.state.node_states = [
                    Node("N0", 0, True, []),
                    Node("N2", 2, True, []),
                ]
                adapter._last_node_id = ""        # nothing reached yet
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = ""
                adapter.state.last_node_sequence_id = 0

                # Robot sits on the non-order SIDE node during the active order.
                adapter._update_nearest_node_from_position(50.0, 0.0, 0.0)

                # Capture must NOT fire during an active order: no map-only node
                # is latched as lastNodeId.
                self.assertEqual(adapter._nearest_node_id, "SIDE")
                self.assertEqual(adapter._last_node_id, "")
                self.assertEqual(adapter.state.last_node_id, "")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())
```

- [ ] **Step 2: Run the two new tests to confirm the expected starting state**

Run from `adaptor/`:

```bash
python -m pytest tests/test_adapter_jibot_v3_order.py -k \
"finished_order_parks_like_idle or active_order_does_not_seed_map_only_node" -v
```

Expected: `test_update_nearest_finished_order_parks_like_idle` FAILS (current bare
`not order_id` gate sees `order_id="done-order"` and skips capture, so
`_last_node_id` stays `"OLD"`). `test_update_nearest_active_order_does_not_seed_map_only_node`
PASSES already (a guard; capture is skipped during an order under both old and new
gates).

- [ ] **Step 3: Add the `_is_v3_order_active()` helper**

In `adaptor/adapter_jibot.py`, immediately after `_is_v3_order_finished`
(`:2600-2603`), add:

```python
    def _is_v3_order_active(self) -> bool:
        """True when a v3 order is accepted and not yet finished.

        Mirrors the inline guard used elsewhere (e.g. adapter_jibot.py:4950):
        state.order_id is not cleared on completion, so a finished/parked robot
        must read as inactive.
        """
        if self.state is None:
            return False
        return bool(getattr(self.state, "order_id", "")) and not self._is_v3_order_finished()
```

- [ ] **Step 4: Change the capture gate in `_update_nearest_node_from_position`**

In `adaptor/adapter_jibot.py`, replace the current block (`:5823-5832`):

```python
        # Idle/no-order pose update: when the robot is within the configured
        # reach zone of a map node, publish that node as lastNodeId. During an
        # active order lastNodeId is advanced ONLY by the order arrival path.
        order_id = getattr(self.state, "order_id", "") if self.state is not None else ""
        if not order_id and distance <= self._idle_last_node_reach_xy():
            self._last_node_id = node_id
            self._last_node_sequence_id = sequence_id
            if self.state is not None:
                self.state.last_node_id = self._last_node_id
                self.state.last_node_sequence_id = self._last_node_sequence_id
```

with:

```python
        # Idle / non-active-order pose update: when no order is ACTIVE and the
        # robot is within the configured reach zone of a map node, publish that
        # node as lastNodeId. The gate is finished-aware (order_id is retained
        # after completion), so capture resumes once an order finishes -- this
        # covers free-roam and post-completion parking. During an active order
        # lastNodeId is advanced ONLY by the order arrival path.
        if not self._is_v3_order_active() and distance <= self._idle_last_node_reach_xy():
            self._last_node_id = node_id
            self._last_node_sequence_id = sequence_id
            if self.state is not None:
                self.state.last_node_id = self._last_node_id
                self.state.last_node_sequence_id = self._last_node_sequence_id
```

- [ ] **Step 5: Update the config comment (Finding 4)**

In `adaptor/config/config.py`, replace the `idle_last_node_reach_xy` comment
(`:68-73`):

```python
    # No-order/idle lastNodeId threshold in raw map units. When there is no
    # active order, the adapter may recover/update lastNodeId from the nearest
    # map node only if the current pose is within this distance. Active-order
    # node completion does NOT use this value.
    # order가 없거나 idle 상태일 때 현재 pose로 lastNodeId를 복구/갱신하기 위한
    # raw map unit 기준 임계치. active order node 완료 판정에는 사용하지 않는다.
```

with:

```python
    # Idle / non-active-order lastNodeId threshold in raw map units. When no
    # order is ACTIVE (no order_id, or the order is finished -- order_id is kept
    # after completion), the adapter recovers/updates lastNodeId from the nearest
    # map node only if the current pose is within this distance. This covers
    # idle, free-roam, and post-completion parking. Active-order node completion
    # does NOT use this value (the order worker owns that).
    # order가 active가 아닐 때(order_id가 없거나, 완료되어 order_id만 남은 경우)
    # 현재 pose로 lastNodeId를 복구/갱신하기 위한 raw map unit 기준 임계치.
    # idle/자유주행/완료 후 주차를 커버한다. active order node 완료 판정에는
    # 사용하지 않는다(worker가 담당).
```

(The `idle_last_node_reach_xy: float = 500.0` line itself is unchanged.)

- [ ] **Step 6: Run the two new tests to verify they PASS**

```bash
python -m pytest tests/test_adapter_jibot_v3_order.py -k \
"finished_order_parks_like_idle or active_order_does_not_seed_map_only_node" -v
```

Expected: both PASS.

- [ ] **Step 7: Run the full order + initial-pose suites**

```bash
python -m pytest tests/test_adapter_jibot_v3_order.py tests/test_initial_pose_and_map.py -q
```

Expected: all PASS. In particular these must stay green unchanged (they assert no
capture during an active order — still true under the finished-aware gate — or
idle capture behavior):
`test_update_nearest_does_not_touch_last_node_during_active_order`,
`test_active_order_last_node_follows_nearest_xy_position`,
`test_active_order_state_loop_updates_last_node_from_nearest_position`,
`test_publish_loop_does_not_overwrite_last_node_during_active_order`,
`test_last_node_can_move_to_lower_sequence_when_xy_is_nearest`,
`test_update_nearest_idle_*`, `test_last_node_uses_nearest_map_node_outside_active_order`,
`test_last_node_id_resolved_from_loaded_map_and_pose`,
`test_last_node_id_starts_on_random_loaded_map_node`.

- [ ] **Step 8: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/config/config.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "fix(jibot): finished-aware gate for idle lastNodeId capture

order_id is retained after order completion, so the bare not-order_id
gate froze lastNodeId at the last order node during free-roam/parking.
Gate idle capture on not _is_v3_order_active() instead, so capture
resumes once an order finishes. Capture still never runs during an
active order; the worker remains the sole writer there.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Finished-aware gate (idle resumes post-completion) → Steps 3-4 + Step 1's
  `test_update_nearest_finished_order_parks_like_idle`. ✓
- No in-order capture / no map-only seed → gate skips active orders (Step 4) +
  Step 1's `test_update_nearest_active_order_does_not_seed_map_only_node`. ✓
- Config comment corrected (Finding 4) → Step 5. ✓
- Arrival writer / prune / rebuild / nearest / NEAREST_NODE untouched → no step
  modifies them; Step 7 re-runs the active-order guards that prove it. ✓
- No persistence → no persistence step. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full before/after code;
every run step shows command + expected result. ✓

**Type/name consistency:** `_is_v3_order_active()` defined in Step 3, used in
Step 4; `Node(...)`, `_update_nearest_node_from_position`,
`_idle_last_node_reach_xy()`, `_is_v3_order_finished()` match existing signatures
verified in source. New test method names are consistent across Steps 1/2/6. ✓
