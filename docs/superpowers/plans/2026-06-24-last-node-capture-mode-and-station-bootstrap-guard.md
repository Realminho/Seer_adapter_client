# lastNodeId Capture Mode + Station-Bootstrap Pose Guard Implementation Plan

> **POST-EXECUTION NOTE (2026-06-24):** Executed and complete. ONE deviation from the
> text below: the planned `idle_last_node_reach_xy` default change `500.0 → 200.0` was
> **reverted to 500.0** per an updated spec — the radius is decoupled from this feature
> (it is overridden by `config.toml` in deployment and is orthogonal to both bugs). Any
> radius change is a separate, independent `config.toml` edit. Task 1's defaults test
> asserts `500.0`. Everything else shipped as written; full suite green (860, PYTHONHASHSEED=0).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify the three `lastNodeId` position/station "guessers" behind one selectable, testable policy (`last_node_capture_mode`) so the two field bugs (manual-drive pollution, accept-time jump to an unreached node) are fixed by default yet reproducible on the robot without code edits.

**Architecture:** One config knob `last_node_capture_mode ∈ {proximity, disabled, settled}` (default `settled`) gates two seams in `adaptor/adapter_jibot.py`: (1) idle capture in `_update_nearest_node_from_position`, dispatched to three independently-testable variants; (2) accept-time bootstrap in `_bootstrap_v3_order_from_vehicle_station` (gains a pose guard under `settled`) and `_bootstrap_v3_order_from_current_pose` (skipped under `disabled`). A shared writer `_set_last_node` replaces the scattered inline writes. `proximity` preserves today's (buggy) behavior for A/B comparison on the real robot.

**Tech Stack:** Python 3.11+ (adapter venv `.venv`), `unittest` + `pytest`, offline `FakeVehicle`/in-process tests. No new dependencies.

## Global Constraints

- Python venv must be **>=3.11** (`X | None` syntax is used repo-wide). Run tests with the project `.venv`.
- Do **not** rename `idle_last_node_reach_xy` — deployed configs reference the key.
- `lastNodeId` semantics are VDA5050-standard (last *reached* node, consumed by ACS for order progress). Do not couple it to nearest-node telemetry.
- Unknown `last_node_capture_mode` value falls back to `settled` with a **one-time** warning (never raise).
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit. Frequent commits.
- Test command (run from the `adaptor/` directory):
  `python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::<test_name> -q`
- All new tests go in the existing class `AdapterV3OrderTest` in
  `adaptor/tests/test_adapter_jibot_v3_order.py` (it already provides
  `_make_adapter`, `_start_state`, `_manual_action`, `_action_status`, `FakeVehicle`, `make_order`).
- Non-goals (do NOT touch): worker arrival path (`_finalize_v3_node_step`, `_wait_until_node_position_reached`), prune/rebuild, `NEAREST_NODE`/`lastNodeGap` telemetry, cross-restart persistence, the separate `receivedOrderId` empty WCS finding.

---

## File Structure

- `adaptor/config/config.py` — add `Settings.last_node_capture_mode: str = "settled"`; change `Settings.idle_last_node_reach_xy` default `500.0 → 200.0`.
- `adaptor/config/config.toml` — document the new `last_node_capture_mode` key under `[settings]`.
- `adaptor/adapter_jibot.py`:
  - `__init__`: add `self._manual_control_active` and `self._last_node_capture_mode_warned` flags.
  - `_last_node_capture_mode()` — validated mode accessor.
  - `_set_last_node(node_id, sequence_id)` — single writer for `lastNodeId`.
  - `_capture_idle_last_node` + `_capture_idle_proximity` / `_capture_idle_disabled` / `_capture_idle_settled` — Seam 1 variants.
  - `_update_nearest_node_from_position` — inline capture block → single dispatch call.
  - manual handlers (`_handle_manual_drive_instant_action`, `_handle_manual_move_instant_action`, `_handle_manual_stop_instant_action`, `_manual_drive_timeout`) — set/clear `_manual_control_active`.
  - `_bootstrap_v3_order_from_vehicle_station` — mode-aware (disabled skip, settled pose guard).
  - `_bootstrap_v3_order_from_current_pose` — disabled skip; use `_set_last_node`.
- `adaptor/tests/test_adapter_jibot_v3_order.py` — new tests for every variant; update 4 existing `test_update_nearest_idle_*` tests to pin `proximity`.

---

## Task 1: Config field + validated mode accessor

**Files:**
- Modify: `adaptor/config/config.py:91` (`idle_last_node_reach_xy` default) and add `last_node_capture_mode` field to the same `Settings` dataclass.
- Modify: `adaptor/config/config.toml:67` area (document the new key).
- Modify: `adaptor/adapter_jibot.py:150` (`__init__` flag) and add `_last_node_capture_mode()` after `_idle_last_node_reach_xy` (`adaptor/adapter_jibot.py:6219`).
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` (class `AdapterV3OrderTest`).

**Interfaces:**
- Produces: `Settings.last_node_capture_mode: str` (default `"settled"`); `Adapter._last_node_capture_mode() -> str` returns one of `"proximity" | "disabled" | "settled"`, lowercased, falling back to `"settled"` on unknown with a one-time warning. `Adapter._last_node_capture_mode_warned: bool`.

- [ ] **Step 1: Write the failing tests**

Add `Settings` to the config import near the top of the test file. Find:

```python
from config.config import MotionRule
```

Replace with:

```python
from config.config import MotionRule, Settings
```

Add these tests inside `class AdapterV3OrderTest`. NOTE: `Settings` has several
required fields (no defaults) — do **not** add defaults to them to make
`Settings()` constructible (that would weaken config-load validation, since a
missing `config.toml` key would silently default). Verify the new defaults via
dataclass field introspection instead:

```python
    def test_settings_defaults_capture_mode_and_threshold(self) -> None:
        from dataclasses import fields
        defaults = {f.name: f.default for f in fields(Settings)}
        self.assertEqual(defaults["last_node_capture_mode"], "settled")
        self.assertEqual(defaults["idle_last_node_reach_xy"], 200.0)

    def test_capture_mode_accessor_normalizes_and_validates(self) -> None:
        adapter = self._make_adapter()
        adapter.config.settings.last_node_capture_mode = "PROXIMITY"
        self.assertEqual(adapter._last_node_capture_mode(), "proximity")
        adapter.config.settings.last_node_capture_mode = "disabled"
        self.assertEqual(adapter._last_node_capture_mode(), "disabled")

    def test_capture_mode_accessor_falls_back_to_settled_once(self) -> None:
        adapter = self._make_adapter()
        adapter.config.settings.last_node_capture_mode = "bogus"
        self.assertFalse(adapter._last_node_capture_mode_warned)
        self.assertEqual(adapter._last_node_capture_mode(), "settled")
        self.assertTrue(adapter._last_node_capture_mode_warned)
        # Second call still returns settled, does not re-warn (idempotent flag).
        self.assertEqual(adapter._last_node_capture_mode(), "settled")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_settings_defaults_capture_mode_and_threshold tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_capture_mode_accessor_normalizes_and_validates tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_capture_mode_accessor_falls_back_to_settled_once -q
```
Expected: FAIL — `KeyError: 'last_node_capture_mode'` (field not yet added) / `'Adapter' object has no attribute '_last_node_capture_mode'`.

- [ ] **Step 3: Add the config field and change the default**

In `adaptor/config/config.py`, find:

```python
    idle_last_node_reach_xy: float = 500.0
```

Replace with:

```python
    idle_last_node_reach_xy: float = 200.0
    # lastNodeId capture policy. One of "proximity" | "disabled" | "settled".
    #   proximity: legacy behavior (idle nearest-node capture + station bootstrap
    #              trusted) -- reproduces both field bugs for A/B triage.
    #   disabled:  no idle capture, no accept-time bootstrap; lastNodeId advances
    #              only via order reset/remap + worker arrival.
    #   settled:   idle capture only when stopped AND manual inactive AND within
    #              threshold; station bootstrap only when the pose agrees.
    # Unknown values fall back to "settled" with a one-time warning.
    # lastNodeId 갱신 정책. 알 수 없는 값은 경고 후 "settled"로 폴백.
    last_node_capture_mode: str = "settled"
```

- [ ] **Step 4: Document the key in config.toml**

In `adaptor/config/config.toml`, find:

```toml
idle_last_node_reach_xy = 100.0      # Raw map-unit threshold for idle lastNodeId recovery; active-order completion does not use this / idle lastNodeId 판단 임계치
```

Add immediately after it:

```toml
last_node_capture_mode = "settled"   # lastNodeId policy: "settled" (default, fixes bugs) | "proximity" (legacy/repro) | "disabled" / lastNodeId 갱신 정책
```

- [ ] **Step 5: Add the `__init__` flag**

In `adaptor/adapter_jibot.py`, find:

```python
        self._manual_drive_watchdog: Optional[asyncio.TimerHandle] = None
```

Replace with:

```python
        self._manual_drive_watchdog: Optional[asyncio.TimerHandle] = None
        # True while the operator is manually driving/jogging (no active order).
        # Used by _capture_idle_settled to suppress lastNodeId churn during jogs.
        self._manual_control_active: bool = False
        # One-time guard so an unknown last_node_capture_mode warns once, not every cycle.
        self._last_node_capture_mode_warned: bool = False
```

- [ ] **Step 6: Add the mode accessor**

In `adaptor/adapter_jibot.py`, find the end of `_idle_last_node_reach_xy`:

```python
    def _idle_last_node_reach_xy(self) -> float:
        threshold = float(
            getattr(self.config.settings, "idle_last_node_reach_xy", 500.0) or 0.0
        )
        if threshold <= 0.0:
            threshold = self._effective_reach_deviation_xy()
        return threshold
```

Add directly after it:

```python
    def _last_node_capture_mode(self) -> str:
        """Validated lastNodeId capture mode. Read every use (config-swappable
        via restart). Unknown values fall back to 'settled' with a one-time
        warning so a config typo never silently changes behavior."""
        valid = ("proximity", "disabled", "settled")
        mode = str(
            getattr(self.config.settings, "last_node_capture_mode", "settled") or ""
        ).strip().lower()
        if mode in valid:
            return mode
        if not self._last_node_capture_mode_warned:
            print(
                f"[CONFIG] unknown last_node_capture_mode={mode!r}; "
                f"falling back to 'settled'"
            )
            self._last_node_capture_mode_warned = True
        return "settled"
```

- [ ] **Step 7: Run tests to verify they pass**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_settings_defaults_capture_mode_and_threshold tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_capture_mode_accessor_normalizes_and_validates tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_capture_mode_accessor_falls_back_to_settled_once -q
```
Expected: PASS (3 passed).

- [ ] **Step 8: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): add last_node_capture_mode config + validated accessor"
```

---

## Task 2: `_set_last_node` shared writer + refactor inline writes (no behavior change)

**Files:**
- Modify: `adaptor/adapter_jibot.py` — add `_set_last_node`; refactor the inline `lastNodeId` writes in `_update_nearest_node_from_position`, `_bootstrap_v3_order_from_vehicle_station`, `_bootstrap_v3_order_from_current_pose`, `_remap_last_node_sequence_for_new_order`.
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`.

**Interfaces:**
- Consumes: nothing new.
- Produces: `Adapter._set_last_node(node_id: str, sequence_id: int) -> None` — sets `self._last_node_id`/`self._last_node_sequence_id` and mirrors onto `self.state` when `self.state is not None`.

- [ ] **Step 1: Write the failing test**

Add to `class AdapterV3OrderTest`:

```python
    def test_set_last_node_writes_private_fields_and_state_mirror(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._set_last_node("F1_70", 6)
                self.assertEqual(adapter._last_node_id, "F1_70")
                self.assertEqual(adapter._last_node_sequence_id, 6)
                self.assertEqual(adapter.state.last_node_id, "F1_70")
                self.assertEqual(adapter.state.last_node_sequence_id, 6)
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_set_last_node_without_state_sets_private_fields_only(self) -> None:
        adapter = self._make_adapter()
        self.assertIsNone(adapter.state)
        adapter._set_last_node("F1_70", 6)
        self.assertEqual(adapter._last_node_id, "F1_70")
        self.assertEqual(adapter._last_node_sequence_id, 6)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_set_last_node_writes_private_fields_and_state_mirror tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_set_last_node_without_state_sets_private_fields_only -q
```
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_set_last_node'`.

- [ ] **Step 3: Add `_set_last_node`**

In `adaptor/adapter_jibot.py`, add directly after the `_last_node_capture_mode` method added in Task 1:

```python
    def _set_last_node(self, node_id: str, sequence_id: int) -> None:
        """Single writer for lastNodeId. Always updates the private fields and
        mirrors them onto self.state when present. Replaces the scattered inline
        (private + state) write pairs so capture/bootstrap paths stay consistent."""
        self._last_node_id = node_id
        self._last_node_sequence_id = sequence_id
        if self.state is not None:
            self.state.last_node_id = node_id
            self.state.last_node_sequence_id = sequence_id
```

- [ ] **Step 4: Refactor `_update_nearest_node_from_position` inline write**

Find:

```python
        if not self._is_v3_order_active() and distance <= self._idle_last_node_reach_xy():
            self._last_node_id = node_id
            self._last_node_sequence_id = sequence_id
            if self.state is not None:
                self.state.last_node_id = self._last_node_id
                self.state.last_node_sequence_id = self._last_node_sequence_id
```

Replace with:

```python
        if not self._is_v3_order_active() and distance <= self._idle_last_node_reach_xy():
            self._set_last_node(node_id, sequence_id)
```

(This block is replaced entirely by the dispatch call in Task 4; this interim refactor keeps the tree green between commits.)

- [ ] **Step 5: Refactor `_bootstrap_v3_order_from_vehicle_station` inline write**

Find:

```python
        station_node = max(matching_nodes, key=lambda node: node.sequence_id)

        self._last_node_id = station_node.node_id
        self._last_node_sequence_id = station_node.sequence_id
        self.state.last_node_id = self._last_node_id
        self.state.last_node_sequence_id = self._last_node_sequence_id
        print(
```

Replace with:

```python
        station_node = max(matching_nodes, key=lambda node: node.sequence_id)

        self._set_last_node(station_node.node_id, station_node.sequence_id)
        print(
```

- [ ] **Step 6: Refactor `_bootstrap_v3_order_from_current_pose` inline write**

Find:

```python
        self._last_node_id = node.node_id
        self._last_node_sequence_id = node.sequence_id
        self.state.last_node_id = self._last_node_id
        self.state.last_node_sequence_id = self._last_node_sequence_id
        print(
            f"[ORDER BOOTSTRAP] pose=({x:.1f},{y:.1f}) "
```

Replace with:

```python
        self._set_last_node(node.node_id, node.sequence_id)
        print(
            f"[ORDER BOOTSTRAP] pose=({x:.1f},{y:.1f}) "
```

- [ ] **Step 7: Refactor `_remap_last_node_sequence_for_new_order` inline write**

Find:

```python
        order_node = min(matching_nodes, key=lambda node: node.sequence_id)
        self._last_node_sequence_id = order_node.sequence_id
        self.state.last_node_id = self._last_node_id
        self.state.last_node_sequence_id = self._last_node_sequence_id
```

Replace with:

```python
        order_node = min(matching_nodes, key=lambda node: node.sequence_id)
        self._set_last_node(self._last_node_id, order_node.sequence_id)
```

- [ ] **Step 8: Run the new tests AND the existing lastNode/bootstrap tests to confirm no behavior change**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_set_last_node_writes_private_fields_and_state_mirror tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_set_last_node_without_state_sets_private_fields_only tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_accepting_new_order_preserves_existing_last_node_until_arrival tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_accepting_new_order_bootstraps_last_node_from_current_pose tests/test_adapter_jibot_v3_order.py -k "idle or last_node or bootstrap" -q
```
Expected: PASS (all selected tests pass; no behavior change).

- [ ] **Step 9: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "refactor(jibot): route lastNodeId writes through _set_last_node"
```

---

## Task 3: Manual-active flag wiring

**Files:**
- Modify: `adaptor/adapter_jibot.py` — `_handle_manual_drive_instant_action`, `_handle_manual_move_instant_action`, `_handle_manual_stop_instant_action`, `_manual_drive_timeout`.
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`.

**Interfaces:**
- Consumes: `self._manual_control_active` (added in Task 1).
- Produces: the flag is `True` from the moment a manual drive/move is accepted until stop / watchdog deadman / manualMove run completion. Read only by `_capture_idle_settled` (Task 4).

- [ ] **Step 1: Write the failing tests**

Add to `class AdapterV3OrderTest`:

```python
    def test_manual_drive_sets_manual_control_active(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                self.assertFalse(adapter._manual_control_active)
                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualDrive",
                                        params={"trans": 100, "rot": 0, "speed": 100})
                )
                await asyncio.sleep(0.02)
                self.assertTrue(adapter._manual_control_active)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_manual_stop_clears_manual_control_active(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter._manual_control_active = True
                adapter.instant_actions_accept_procedure(self._manual_action("manualStop"))
                await asyncio.sleep(0.02)
                self.assertFalse(adapter._manual_control_active)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_manual_move_clears_manual_control_active_when_done(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                adapter.config.manual_control.enabled = True
                adapter.order = None
                adapter.instant_actions_accept_procedure(
                    self._manual_action("manualMove",
                                        params={"distance": 500, "speed": 100})
                )
                await asyncio.sleep(0.05)
                # FakeVehicle.move_distance returns immediately, so the run's
                # finally-block has cleared the flag by now.
                self.assertFalse(adapter._manual_control_active)
            finally:
                state_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_manual_drive_sets_manual_control_active tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_manual_stop_clears_manual_control_active tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_manual_move_clears_manual_control_active_when_done -q
```
Expected: FAIL — `test_manual_drive_sets_manual_control_active` asserts `True` but flag stays `False` (drive never sets it). (`manualStop` test may already pass since the default is `False`; the `manualMove` test may already pass — that is fine, they lock in behavior after the drive wiring.)

- [ ] **Step 3: Set the flag in the drive handler**

In `_handle_manual_drive_instant_action`, find:

```python
        self._update_instant_action_status(
            action.action_id, ActionStatus.FINISHED, result_description="manual drive"
        )

        async def _run() -> None:
            await self._vehicle.um_drive(trans, rot, speed, lat)
            self._arm_manual_drive_watchdog()
```

Replace with:

```python
        self._update_instant_action_status(
            action.action_id, ActionStatus.FINISHED, result_description="manual drive"
        )
        self._manual_control_active = True

        async def _run() -> None:
            await self._vehicle.um_drive(trans, rot, speed, lat)
            self._arm_manual_drive_watchdog()
```

- [ ] **Step 4: Set the flag in the move handler and clear it when the run finishes**

In `_handle_manual_move_instant_action`, find:

```python
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)

        async def _run() -> None:
            try:
                await self._vehicle.move_distance(
                    distance, speed,
                    obs_avoid_dist=mc.move_obs_avoid_dist,
                    side_avoid_dist=mc.move_side_avoid_dist,
                )
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FINISHED,
                    result_description=f"move {distance}mm @ {speed}",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"manualMove failed: {exc}",
                )

        self._run_on_adapter_loop(_run)
```

Replace with:

```python
        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)
        self._manual_control_active = True

        async def _run() -> None:
            try:
                await self._vehicle.move_distance(
                    distance, speed,
                    obs_avoid_dist=mc.move_obs_avoid_dist,
                    side_avoid_dist=mc.move_side_avoid_dist,
                )
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FINISHED,
                    result_description=f"move {distance}mm @ {speed}",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"manualMove failed: {exc}",
                )
            finally:
                self._manual_control_active = False

        self._run_on_adapter_loop(_run)
```

- [ ] **Step 5: Clear the flag in the stop handler**

In `_handle_manual_stop_instant_action`, find:

```python
    def _handle_manual_stop_instant_action(self, action: Any) -> None:
        self._update_instant_action_status(
            action.action_id, ActionStatus.FINISHED, result_description="manual stop"
        )
```

Replace with:

```python
    def _handle_manual_stop_instant_action(self, action: Any) -> None:
        self._manual_control_active = False
        self._update_instant_action_status(
            action.action_id, ActionStatus.FINISHED, result_description="manual stop"
        )
```

- [ ] **Step 6: Clear the flag in the watchdog deadman**

In `_manual_drive_timeout`, find:

```python
    async def _manual_drive_timeout(self) -> None:
        self._manual_drive_watchdog = None
        await self._vehicle.um_stop()
        print("[MANUAL DRIVE WATCHDOG] auto-stop (no heartbeat)")
```

Replace with:

```python
    async def _manual_drive_timeout(self) -> None:
        self._manual_drive_watchdog = None
        self._manual_control_active = False
        await self._vehicle.um_stop()
        print("[MANUAL DRIVE WATCHDOG] auto-stop (no heartbeat)")
```

- [ ] **Step 7: Run tests to verify they pass**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_manual_drive_sets_manual_control_active tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_manual_stop_clears_manual_control_active tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_manual_move_clears_manual_control_active_when_done -q
```
Expected: PASS (3 passed).

- [ ] **Step 8: Run the full manual-control test group to confirm no regression**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py -k "manual" -q
```
Expected: PASS (all manual tests).

- [ ] **Step 9: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): track _manual_control_active across manual drive/move/stop"
```

---

## Task 4: Seam 1 — idle capture mode dispatch (fixes Bug #1)

**Files:**
- Modify: `adaptor/adapter_jibot.py` — add `_capture_idle_last_node` + 3 variants; replace the inline idle block in `_update_nearest_node_from_position` with a single dispatch call.
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` — pin `proximity` in the 4 existing `test_update_nearest_idle_*` tests; add new variant tests.
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`.

**Interfaces:**
- Consumes: `_last_node_capture_mode()` (Task 1), `_set_last_node()` (Task 2), `_manual_control_active` (Tasks 1/3), `_is_jibot_stopped()`, `_is_v3_order_active()`, `_idle_last_node_reach_xy()` (existing).
- Produces: `_capture_idle_last_node(node_id: str, sequence_id: int, distance: float) -> None`, dispatching to `_capture_idle_proximity` / `_capture_idle_disabled` / `_capture_idle_settled` (same signature).

- [ ] **Step 1: Pin `proximity` in the 4 existing idle tests**

The new default is `settled`; these tests assert pure proximity-threshold behavior, so pin the mode. In `test_update_nearest_idle_bootstrap_seeds_last_node`, find:

```python
                adapter.config.settings.idle_last_node_reach_xy = 500.0

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)
```

Replace with:

```python
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "proximity"

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)
```

Apply the same one-line insertion (`adapter.config.settings.last_node_capture_mode = "proximity"` directly before the `adapter._update_nearest_node_from_position(...)` call) in the other three: `test_update_nearest_idle_updates_last_node_within_reach_zone`, `test_update_nearest_idle_uses_configured_last_node_threshold`, `test_update_nearest_idle_keeps_last_node_when_outside_reach_zone`.

- [ ] **Step 2: Write the failing variant tests**

Add to `class AdapterV3OrderTest`:

```python
    def test_idle_capture_disabled_does_not_seed(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0), "B": (1200.0, 0.0, 0.0)}
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "disabled"

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")   # telemetry still updates
                self.assertEqual(adapter._last_node_id, "")       # but lastNodeId untouched
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_idle_capture_settled_seeds_when_stopped_and_manual_inactive(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0), "B": (1200.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter._manual_control_active = False
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._last_node_id, "B")
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_idle_capture_settled_skips_during_manual_control(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0), "B": (1200.0, 0.0, 0.0)}
            adapter._vehicle._status = "Stopped"
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter._manual_control_active = True   # Bug #1 condition
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "")   # not polluted
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())

    def test_idle_capture_settled_skips_while_moving(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter._vehicle._map_nodes = {"A": (0.0, 0.0, 0.0), "B": (1200.0, 0.0, 0.0)}
            adapter._vehicle._status = "Driving"   # not stopped
            state_task = await self._start_state(adapter)
            try:
                adapter.state.order_id = ""
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter._manual_control_active = False
                adapter.config.settings.idle_last_node_reach_xy = 500.0
                adapter.config.settings.last_node_capture_mode = "settled"

                adapter._update_nearest_node_from_position(990.0, 0.0, 0.0)

                self.assertEqual(adapter._nearest_node_id, "B")
                self.assertEqual(adapter._last_node_id, "")   # not captured mid-motion
            finally:
                state_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await state_task

        asyncio.run(scenario())
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_idle_capture_disabled_does_not_seed tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_idle_capture_settled_seeds_when_stopped_and_manual_inactive tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_idle_capture_settled_skips_during_manual_control tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_idle_capture_settled_skips_while_moving -q
```
Expected: FAIL — `disabled`/`manual`/`moving` tests still seed `B` because the inline block (Task 2 form) ignores mode and the manual/stopped gates.

- [ ] **Step 4: Add the dispatch + variant methods**

In `adaptor/adapter_jibot.py`, find the start of `_update_nearest_node_from_position`:

```python
    def _update_nearest_node_from_position(
        self,
        x: float,
        y: float,
        theta: Optional[float] = None,
    ) -> None:
```

Add the four methods directly **before** it:

```python
    def _capture_idle_last_node(
        self, node_id: str, sequence_id: int, distance: float
    ) -> None:
        """Dispatch idle (non-active-order) lastNodeId capture on the configured
        mode. Called from _update_nearest_node_from_position after the nearest
        map node and its distance are known."""
        mode = self._last_node_capture_mode()
        if mode == "disabled":
            self._capture_idle_disabled(node_id, sequence_id, distance)
        elif mode == "proximity":
            self._capture_idle_proximity(node_id, sequence_id, distance)
        else:
            self._capture_idle_settled(node_id, sequence_id, distance)

    def _capture_idle_proximity(
        self, node_id: str, sequence_id: int, distance: float
    ) -> None:
        """Legacy behavior: capture whenever no order is active and the robot is
        within the idle reach threshold of a map node."""
        if not self._is_v3_order_active() and distance <= self._idle_last_node_reach_xy():
            self._set_last_node(node_id, sequence_id)

    def _capture_idle_disabled(
        self, node_id: str, sequence_id: int, distance: float
    ) -> None:
        """No-op: lastNodeId is never advanced by idle capture in this mode.
        Explicit (vs. an inline branch) so the policy is unit-testable."""
        return

    def _capture_idle_settled(
        self, node_id: str, sequence_id: int, distance: float
    ) -> None:
        """Proximity capture, but only when the robot is actually at rest and not
        under manual control -- prevents lastNodeId churn while the operator jogs
        (Bug #1). The stopped-gate covers steady jogging (driving status); the
        manual flag additionally suppresses capture during brief jog pauses."""
        if self._manual_control_active:
            return
        if not self._is_jibot_stopped():
            return
        self._capture_idle_proximity(node_id, sequence_id, distance)
```

- [ ] **Step 5: Replace the inline idle block with the dispatch call**

In `_update_nearest_node_from_position`, find:

```python
        # Idle / non-active-order pose update: when no order is ACTIVE and the
        # robot is within the configured reach zone of a map node, publish that
        # node as lastNodeId. The gate is finished-aware (order_id is retained
        # after completion), so capture resumes once an order finishes -- this
        # covers free-roam and post-completion parking. During an active order
        # lastNodeId is advanced ONLY by the order arrival path.
        if not self._is_v3_order_active() and distance <= self._idle_last_node_reach_xy():
            self._set_last_node(node_id, sequence_id)
```

Replace with:

```python
        # Idle / non-active-order pose update: advance lastNodeId from the nearest
        # map node per the configured last_node_capture_mode (see
        # _capture_idle_last_node). The gate is finished-aware (order_id is
        # retained after completion), so capture resumes once an order finishes --
        # covering free-roam and post-completion parking. During an active order
        # lastNodeId is advanced ONLY by the order arrival path.
        self._capture_idle_last_node(node_id, sequence_id, distance)
```

- [ ] **Step 6: Run the new variant tests to verify they pass**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_idle_capture_disabled_does_not_seed tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_idle_capture_settled_seeds_when_stopped_and_manual_inactive tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_idle_capture_settled_skips_during_manual_control tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_idle_capture_settled_skips_while_moving -q
```
Expected: PASS (4 passed).

- [ ] **Step 7: Run the updated existing idle tests + the whole idle group**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py -k "idle" -q
```
Expected: PASS (the 4 pinned-`proximity` tests + 4 new variant tests).

- [ ] **Step 8: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): mode-dispatched idle lastNodeId capture (fixes manual-drive pollution)"
```

---

## Task 5: Seam 2 — accept-time bootstrap mode-awareness (fixes Bug #2)

**Files:**
- Modify: `adaptor/adapter_jibot.py` — `_bootstrap_v3_order_from_vehicle_station` (disabled skip + settled pose guard); `_bootstrap_v3_order_from_current_pose` (disabled skip).
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`.

**Interfaces:**
- Consumes: `_last_node_capture_mode()` (Task 1), `_set_last_node()` (Task 2), `_distance_to_node_id(node_id, position)`, `_idle_last_node_reach_xy()`, `self.state.agv_position` (existing).
- Produces: no new public symbols. Behavior: `disabled` runs neither bootstrap; `proximity` keeps station-trusted bootstrap; `settled` seeds from `_station` only when the current pose is within `_idle_last_node_reach_xy()` of that station node.

- [ ] **Step 1: Write the failing tests**

These reproduce the Bug #2 geometry (station node ~2674 mm from the robot pose). Add to `class AdapterV3OrderTest`. Helper order builder is inline per test to keep them self-contained:

```python
    def _bug2_order(self) -> "Order":
        # Charger order: robot physically at F2_90_S2CH, _station mis-reports
        # F2_80_S2IC (an order node at seq 4) ~2674 mm away.
        return Order.from_dict({
            "headerId": 70,
            "timestamp": "2026-06-23T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "orderId": "bug2-order",
            "orderUpdateId": 1,
            "nodes": [
                {"nodeId": "F2_80_S2IC", "sequenceId": 4, "released": True, "actions": []},
                {"nodeId": "F1_70", "sequenceId": 6, "released": True, "actions": []},
            ],
            "edges": [
                {"edgeId": "e_F2_80_S2IC_F1_70", "sequenceId": 5, "released": True,
                 "startNodeId": "F2_80_S2IC", "endNodeId": "F1_70", "actions": []},
            ],
        })

    def _setup_bug2_adapter(self, adapter: "Adapter") -> None:
        v = adapter._vehicle
        v._station = "F2_80_S2IC"          # mis-reported station (an order node)
        v._x = 14038.0                      # robot is at the charger F2_90_S2CH
        v._y = 3752.0
        v._map_nodes = {
            "F2_80_S2IC": (14159.0, 3615.0, 0.0),   # ~2674 mm from the charger pose
            "F2_90_S2CH": (14038.0, 3752.0, 0.0),
            "F1_70": (12180.0, 3760.0, 0.0),
        }

    def test_bootstrap_proximity_trusts_station_even_when_pose_disagrees(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            self._setup_bug2_adapter(adapter)
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter.config.settings.last_node_capture_mode = "proximity"
                adapter._accept_v3_order_for_queue(self._bug2_order())
                # Legacy bug reproduced: station trusted, lastNodeId jumps to the
                # unreached F2_80_S2IC and seq<=4 is pruned.
                self.assertEqual(adapter._last_node_id, "F2_80_S2IC")
                self.assertEqual(adapter._last_node_sequence_id, 4)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_bootstrap_settled_rejects_station_when_pose_disagrees(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            self._setup_bug2_adapter(adapter)
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = ""
                adapter.state.last_node_sequence_id = 0
                adapter._accept_v3_order_for_queue(self._bug2_order())
                # Bug #2 fixed: station bootstrap rejected (pose ~2674 mm away),
                # lastNodeId NOT advanced to the unreached node.
                self.assertNotEqual(adapter._last_node_id, "F2_80_S2IC")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_bootstrap_settled_accepts_station_when_pose_agrees(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            self._setup_bug2_adapter(adapter)
            # Move the robot onto the station node so the pose agrees.
            adapter._vehicle._x = 14159.0
            adapter._vehicle._y = 3615.0
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter.config.settings.last_node_capture_mode = "settled"
                adapter._accept_v3_order_for_queue(self._bug2_order())
                # Legit resume still works: pose within reach => station seeded.
                self.assertEqual(adapter._last_node_id, "F2_80_S2IC")
                self.assertEqual(adapter._last_node_sequence_id, 4)
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_bootstrap_disabled_runs_neither_bootstrap(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            self._setup_bug2_adapter(adapter)
            # Put the robot right on the station so BOTH station- and pose-bootstrap
            # WOULD seed under settled/proximity; disabled must run neither.
            adapter._vehicle._x = 14159.0
            adapter._vehicle._y = 3615.0
            state_task = await self._start_state(adapter)
            try:
                state_task.cancel()
                adapter._loop = None
                adapter.config.settings.last_node_capture_mode = "disabled"
                adapter._last_node_id = ""
                adapter._last_node_sequence_id = 0
                adapter.state.last_node_id = ""
                adapter.state.last_node_sequence_id = 0
                adapter._accept_v3_order_for_queue(self._bug2_order())
                self.assertEqual(adapter._last_node_id, "")
            finally:
                state_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_bootstrap_proximity_trusts_station_even_when_pose_disagrees tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_bootstrap_settled_rejects_station_when_pose_disagrees tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_bootstrap_settled_accepts_station_when_pose_agrees tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_bootstrap_disabled_runs_neither_bootstrap -q
```
Expected: FAIL — `settled rejects` seeds `F2_80_S2IC` anyway (no pose guard yet); `disabled` seeds it too (no skip yet). (`proximity` and `settled accepts` may already pass — they lock in behavior.)

- [ ] **Step 3: Make `_bootstrap_v3_order_from_vehicle_station` mode-aware**

Find:

```python
    def _bootstrap_v3_order_from_vehicle_station(self, order_request: Order) -> None:
        if self.state is None or self._vehicle is None:
            return

        station = str(getattr(self._vehicle, "_station", "") or "").strip()
        if not station:
            return

        matching_nodes = [
            node for node in order_request.nodes
            if getattr(node, "node_id", None) == station
        ]
        if not matching_nodes:
            return

        station_node = max(matching_nodes, key=lambda node: node.sequence_id)

        self._set_last_node(station_node.node_id, station_node.sequence_id)
        print(
            f"[ORDER BOOTSTRAP] station={station} "
            f"lastNodeId={self._last_node_id} "
            f"lastNodeSequenceId={self._last_node_sequence_id}"
        )
```

Replace with:

```python
    def _bootstrap_v3_order_from_vehicle_station(self, order_request: Order) -> None:
        if self.state is None or self._vehicle is None:
            return

        mode = self._last_node_capture_mode()
        if mode == "disabled":
            return

        station = str(getattr(self._vehicle, "_station", "") or "").strip()
        if not station:
            return

        matching_nodes = [
            node for node in order_request.nodes
            if getattr(node, "node_id", None) == station
        ]
        if not matching_nodes:
            return

        station_node = max(matching_nodes, key=lambda node: node.sequence_id)

        if mode == "settled":
            # Pose guard: _station can be set before the robot physically reaches
            # the node coords (see docs/reference/jibot-arrival-and-last-node.md),
            # which makes lastNodeId jump to an unreached node at order accept
            # (Bug #2). Only trust the station when the current pose agrees.
            gap = self._distance_to_node_id(
                station, getattr(self.state, "agv_position", None)
            )
            if gap is None or gap > self._idle_last_node_reach_xy():
                gap_str = "n/a" if gap is None else f"{gap:.1f}mm"
                print(
                    f"[ORDER BOOTSTRAP] station={station} rejected "
                    f"(pose gap={gap_str} > {self._idle_last_node_reach_xy():.1f}mm)"
                )
                return

        self._set_last_node(station_node.node_id, station_node.sequence_id)
        print(
            f"[ORDER BOOTSTRAP] station={station} "
            f"lastNodeId={self._last_node_id} "
            f"lastNodeSequenceId={self._last_node_sequence_id}"
        )
```

- [ ] **Step 4: Skip pose bootstrap under `disabled`**

In `_bootstrap_v3_order_from_current_pose`, find:

```python
    def _bootstrap_v3_order_from_current_pose(self, order_request: Order) -> None:
        if self.state is None:
            return

        position = getattr(self.state, "agv_position", None)
```

Replace with:

```python
    def _bootstrap_v3_order_from_current_pose(self, order_request: Order) -> None:
        if self.state is None:
            return
        if self._last_node_capture_mode() == "disabled":
            return

        position = getattr(self.state, "agv_position", None)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_bootstrap_proximity_trusts_station_even_when_pose_disagrees tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_bootstrap_settled_rejects_station_when_pose_disagrees tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_bootstrap_settled_accepts_station_when_pose_agrees tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_bootstrap_disabled_runs_neither_bootstrap -q
```
Expected: PASS (4 passed).

- [ ] **Step 6: Run the existing bootstrap + lastNode tests for regressions**

Run:
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py -k "bootstrap or last_node or preserve" -q
```
Expected: PASS (existing `test_accepting_new_order_preserves_existing_last_node_until_arrival`, `test_accepting_new_order_bootstraps_last_node_from_current_pose`, and the new bootstrap tests). Note: the existing pose-bootstrap test has `_station = ""` and default mode `settled`, so the station bootstrap early-returns on empty station and the pose bootstrap (unchanged) still seeds `F2_90_S2CH`.

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): pose-guard station bootstrap + disabled skip (fixes accept-time lastNodeId jump)"
```

---

## Final verification

- [ ] **Run the entire v3 order test file**

Run (from `adaptor/`):
```bash
python -m pytest tests/test_adapter_jibot_v3_order.py -q
```
Expected: PASS (no failures, no errors).

- [ ] **Run the full adapter test suite** to catch cross-file regressions (config default change, manual handlers, render):

Run (from `adaptor/`):
```bash
python -m pytest tests/ -q
```
Expected: PASS. If `test_web_render.py` or other files assert the old `idle_last_node_reach_xy = 500.0` default or expect the old bootstrap behavior, update only those assertions that encode the intentional changes from this plan (default `200.0`, default mode `settled`); investigate any other failure before editing.

---

## Self-Review (completed during planning)

**Spec coverage:**
- Config `last_node_capture_mode` (default `settled`, unknown→settled+warn) → Task 1. ✓
- `idle_last_node_reach_xy` default `500→200`, key not renamed → Task 1. ✓
- Mode table semantics (proximity/disabled/settled across both seams) → Tasks 4 (idle) + 5 (bootstrap). ✓
- Seam 1 variants as independently-testable methods (`_capture_idle_proximity/disabled/settled`) → Task 4. ✓
- Shared writer `_set_last_node`, refactor inline writes, no behavior change → Task 2. ✓
- Seam 2 disabled-skip (both bootstraps) + settled pose guard on station bootstrap; pose bootstrap otherwise unchanged → Task 5. ✓
- `_manual_control_active` set/cleared at drive/move start, stop, deadman, move completion → Task 3. ✓
- TDD test matrix (Seam 1: proximity/disabled/settled±manual±moving; Seam 2: proximity/settled-reject/settled-accept/disabled) → Tasks 4 + 5. ✓
- Keep existing idle tests passing by pinning `proximity` → Task 4 Step 1. ✓
- Non-goals untouched (worker arrival, prune/rebuild, telemetry, persistence). ✓

**Placeholder scan:** No TBD/"handle errors"/"similar to"/bare "write tests" — every code and test step carries complete code. ✓

**Type consistency:** `_capture_idle_last_node` / `_capture_idle_proximity` / `_capture_idle_disabled` / `_capture_idle_settled` all use `(node_id: str, sequence_id: int, distance: float)`; `_set_last_node(node_id: str, sequence_id: int)`; `_last_node_capture_mode() -> str` returns lowercase strings consistently; `_distance_to_node_id(node_id, position)` matches the existing signature. ✓
