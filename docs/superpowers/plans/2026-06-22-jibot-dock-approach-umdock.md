# JIBOT dock approach via UmDock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an order targets a charger node that has a configured approach
(`_BEFORE`) waypoint, the adapter drives to the approach via UmGoto, then UmDocks
to seat into the charger — and fails out (rejecting the order step) instead of
hanging in DOCKING if charging never starts.

**Architecture:** A new `[dock_approach]` mapping in a JIBOT-specific
`jibot-config.toml` (deep-merged into the existing config) is the sole trigger.
`_process_v3_node_step` gains a branch that delegates the whole approach→dock
sequence to one isolated orchestrator `_run_approach_then_dock`, which owns
per-phase error surfacing. Existing node/dock paths are untouched.

**Tech Stack:** Python 3.12, `tomllib`, `asyncio`, `unittest` (offline tests with
a `FakeVehicle` stand-in — no broker/robot needed).

**Design spec:** `docs/superpowers/specs/2026-06-22-jibot-dock-approach-umdock-design.md`

## Global Constraints

- Approach→charger mapping lives in `adaptor/config/jibot-config.toml`, section
  `[dock_approach]`, keys `nodes` (inline table `{charger = "approach"}`) and
  `fail_timeout_sec` (float, default `30.0`). File absent ⇒ defaults ⇒ behaviour
  unchanged (backward compatible).
- The final leg into a charger is **UmDock**, never UmGoto.
- All command sends in the orchestrator are wrapped so a transport exception
  becomes a rejected step, not a crashed order worker.
- Dock/approach failures surface as **FATAL** errors (FM holds + re-sends → retry).
  Approach-goto rejection reuses the existing `_set_jibot_goto_rejected_error`
  (`JIBOT_GOTO_REJECTED`, command=`UmGoto`); the new `JIBOT_DOCK_FAILED` error is
  only for the post-UmDock seating timeout / dock-send failure / missing coords.
- TDD: write the failing test first, watch it fail, implement minimally, watch it
  pass, commit. Run tests from the `adaptor/` directory.
- Tests reuse the existing scaffolding in
  `adaptor/tests/test_adapter_jibot_v3_order.py` (`FakeVehicle`, `_make_adapter`,
  `_start_state`, `Node.from_dict`, `OrderStep`).

---

### Task 1: Config — `DockApproachConfig` + `jibot-config.toml` + loader merge

**Files:**
- Create: `adaptor/config/jibot-config.toml`
- Modify: `adaptor/config/config.py` (add dataclass, `Config` field, loader merge)
- Test: `adaptor/tests/test_dock_approach_config.py`

**Interfaces:**
- Produces: `config.dock_approach: DockApproachConfig` with
  `nodes: Dict[str, str]` (default `{}`) and `fail_timeout_sec: float` (default
  `30.0`). Accessed everywhere as `self.config.dock_approach`.

- [ ] **Step 1: Create the JIBOT config file**

Create `adaptor/config/jibot-config.toml`:

```toml
# JIBOT 하드웨어 특성 매핑.
# 충전기 진입은 UmGoto가 아니라 approach(_BEFORE) 노드까지 UmGoto 후
# 거기서 UmDock으로 seating. ACS는 충전기 노드만 보냄(approach는 어댑터 내부).
[dock_approach]
nodes = { F2_90_S2CH = "F2_90_S2CH_BEFORE" }   # charger node -> approach node
fail_timeout_sec = 30.0                         # reject if not charging within Ns after UmDock
```

- [ ] **Step 2: Write the failing config test**

Create `adaptor/tests/test_dock_approach_config.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import DockApproachConfig, get_config


class DockApproachConfigTest(unittest.TestCase):
    def test_dataclass_defaults(self):
        cfg = DockApproachConfig()
        self.assertEqual(cfg.nodes, {})
        self.assertEqual(cfg.fail_timeout_sec, 30.0)

    def test_jibot_config_toml_is_merged(self):
        cfg = get_config().dock_approach
        self.assertEqual(cfg.nodes.get("F2_90_S2CH"), "F2_90_S2CH_BEFORE")
        self.assertEqual(cfg.fail_timeout_sec, 30.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_dock_approach_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'DockApproachConfig'`.

- [ ] **Step 4: Add the dataclass**

In `adaptor/config/config.py`, after the `DockConfig` dataclass (ends at the
`stop_charging_verify_timeout_sec` field, ~line 124), add:

```python
@dataclass
class DockApproachConfig:
    # JIBOT chargers that need an approach (_BEFORE) waypoint: the adapter
    # UmGotos to the approach node, then UmDocks to seat into the charger.
    # ACS only sends the charger node; the approach is adapter-internal.
    # Lives in jibot-config.toml [dock_approach].
    nodes: Dict[str, str] = field(default_factory=dict)
    fail_timeout_sec: float = 30.0
```

- [ ] **Step 5: Add the field to `Config`**

In the `Config` dataclass (~line 199), add the field after `dock: DockConfig`:

```python
    dock: DockConfig
    dock_approach: DockApproachConfig
```

- [ ] **Step 6: Merge `jibot-config.toml` in the loader**

In `adaptor/config/config.py`, after `_CONFIG_PATH` (~line 216) add:

```python
_JIBOT_CONFIG_PATH = Path(__file__).resolve().parent / "jibot-config.toml"
```

In `get_config`, after the base TOML is loaded and BEFORE overrides are applied,
merge the JIBOT overlay. Replace:

```python
    path = Path(config_path) if config_path is not None else _CONFIG_PATH
    with open(path, "rb") as files:
        config_dict = tomllib.load(files)

    if overrides:
        _deep_merge(config_dict, overrides)
```

with:

```python
    path = Path(config_path) if config_path is not None else _CONFIG_PATH
    with open(path, "rb") as files:
        config_dict = tomllib.load(files)

    # JIBOT hardware-quirk overlay (shared across instances). Absent => defaults.
    if _JIBOT_CONFIG_PATH.exists():
        with open(_JIBOT_CONFIG_PATH, "rb") as jibot_files:
            _deep_merge(config_dict, tomllib.load(jibot_files))

    if overrides:
        _deep_merge(config_dict, overrides)
```

Then build the config object. After the `dock_config = ...` line (~line 265) add:

```python
    dock_config = DockConfig(**config_dict.get("dock", {}))
    dock_approach = DockApproachConfig(**config_dict.get("dock_approach", {}))
```

And pass it into the `Config(...)` constructor, after `dock=dock_config,`:

```python
        dock=dock_config,
        dock_approach=dock_approach,
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_dock_approach_config.py -v`
Expected: PASS (both tests).

- [ ] **Step 8: Confirm nothing else broke**

Run: `cd adaptor && python -m pytest tests/test_charge_circuit_config.py tests/test_bms_ros_config.py -v`
Expected: PASS (existing config tests still green).

- [ ] **Step 9: Commit**

```bash
git add adaptor/config/jibot-config.toml adaptor/config/config.py adaptor/tests/test_dock_approach_config.py
git commit -m "feat(jibot): dock_approach config in jibot-config.toml"
```

---

### Task 2: `JIBOT_DOCK_FAILED` error type + `_set_jibot_dock_fail_error`

**Files:**
- Modify: `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` (new `ErrorType` member)
- Modify: `adaptor/adapter_jibot.py` (new helper)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Produces: `ErrorType.JIBOT_DOCK_FAILED`; `_set_jibot_dock_fail_error(self, node, reason: str) -> None`
  appends a single FATAL error (de-duped by type) and requests a publish.

- [ ] **Step 1: Write the failing test**

Add to `adaptor/tests/test_adapter_jibot_v3_order.py` (inside the main test
class, alongside the other dock tests):

```python
    def test_set_jibot_dock_fail_error_is_fatal_and_deduped(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            state_task = await self._start_state(adapter)
            try:
                from types import SimpleNamespace
                node = SimpleNamespace(node_id="F2_90_S2CH", sequence_id=4)

                adapter._set_jibot_dock_fail_error(node, "not charging within 30.0s")
                adapter._set_jibot_dock_fail_error(node, "still not charging")

                dock_errors = [
                    e for e in adapter.state.errors
                    if e.error_type == ErrorType.JIBOT_DOCK_FAILED
                ]
                self.assertEqual(len(dock_errors), 1)  # de-duped
                self.assertEqual(dock_errors[0].error_level, ErrorLevel.FATAL)
                refs = {r.reference_key: r.reference_value
                        for r in dock_errors[0].error_references}
                self.assertEqual(refs["nodeId"], "F2_90_S2CH")
                self.assertEqual(refs["command"], "UmDock")
                self.assertEqual(refs["reason"], "still not charging")
            finally:
                state_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k dock_fail_error -v`
Expected: FAIL — `AttributeError: ... has no attribute '_set_jibot_dock_fail_error'`.

- [ ] **Step 3: Add the error type**

In `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py`, in the `ErrorType` enum,
add after the `JIBOT_GOTO_REJECTED` line:

```python
    JIBOT_GOTO_REJECTED = "JIBOT_GOTO_REJECTED"  # JIBOT rejected or did not acknowledge a node goto
    JIBOT_DOCK_FAILED = "JIBOT_DOCK_FAILED"  # UmDock did not lead to charging within the timeout
```

- [ ] **Step 4: Add the helper**

In `adaptor/adapter_jibot.py`, directly after `_set_jibot_goto_rejected_error`
(it ends with `self.request_state_publish("goto rejected")`, ~line 1749), add:

```python
    def _set_jibot_dock_fail_error(self, node: Any, reason: str) -> None:
        """Surface a failed approach-dock (UmDock did not start charging) as FATAL.

        FATAL so the fleet manager holds and re-sends the order, retrying the
        approach->dock. De-duped by type like the other JIBOT error setters.
        """
        if self.state is None:
            return

        self.state.errors = [
            error
            for error in self.state.errors
            if getattr(error, "error_type", None) != ErrorType.JIBOT_DOCK_FAILED
        ]
        self.state.errors.append(
            Error(
                error_type=ErrorType.JIBOT_DOCK_FAILED,
                error_level=ErrorLevel.FATAL,
                error_references=[
                    ErrorReference("nodeId", str(getattr(node, "node_id", "") or "")),
                    ErrorReference("sequenceId", str(getattr(node, "sequence_id", "") or "")),
                    ErrorReference("command", "UmDock"),
                    ErrorReference("reason", reason),
                    ErrorReference("jibotMode", str(getattr(self._vehicle, "_mode", "") or "")),
                    ErrorReference("jibotStatus", str(getattr(self._vehicle, "_status", "") or "")),
                ],
                error_description=(
                    "JIBOT did not start charging after the approach UmDock; "
                    "the order is held until the fleet manager re-sends it."
                ),
            )
        )
        self.request_state_publish("dock failed")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k dock_fail_error -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): JIBOT_DOCK_FAILED error for failed approach-dock"
```

---

### Task 3: `_dock_approach_node` + `_pose_in_reach_zone` extraction

**Files:**
- Modify: `adaptor/adapter_jibot.py`
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Produces: `_dock_approach_node(self, node_id: str) -> Optional[str]` (approach
  name or None); `_pose_in_reach_zone(self, vx, vy, tx, ty, deviation) -> bool`
  (square/circle per `settings.reach_zone_shape`).

- [ ] **Step 1: Write the failing tests**

Add to `adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
    def test_dock_approach_node_lookup(self) -> None:
        adapter = self._make_adapter()
        adapter.config.dock_approach.nodes = {"F2_90_S2CH": "F2_90_S2CH_BEFORE"}
        self.assertEqual(adapter._dock_approach_node("F2_90_S2CH"), "F2_90_S2CH_BEFORE")
        self.assertIsNone(adapter._dock_approach_node("F1_60"))

    def test_pose_in_reach_zone_square_and_circle(self) -> None:
        adapter = self._make_adapter()
        adapter.config.settings.reach_zone_shape = "square"
        self.assertTrue(adapter._pose_in_reach_zone(105, 95, 100, 100, 10))
        self.assertFalse(adapter._pose_in_reach_zone(120, 100, 100, 100, 10))
        adapter.config.settings.reach_zone_shape = "circle"
        self.assertTrue(adapter._pose_in_reach_zone(100, 109, 100, 100, 10))
        self.assertFalse(adapter._pose_in_reach_zone(108, 108, 100, 100, 10))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "dock_approach_node_lookup or pose_in_reach_zone" -v`
Expected: FAIL — `AttributeError` on `_dock_approach_node`.

- [ ] **Step 3: Add `_dock_approach_node`**

In `adaptor/adapter_jibot.py`, directly before `_is_charge_node` (~line 2932),
add:

```python
    def _dock_approach_node(self, node_id: str) -> Optional[str]:
        """Approach (_BEFORE) waypoint for a charger node, or None.

        From jibot-config.toml [dock_approach].nodes. When set, the order node is
        driven via UmGoto(approach) then UmDock, instead of a plain goto.
        """
        mapping = getattr(self.config.dock_approach, "nodes", {}) or {}
        return mapping.get(node_id)
```

- [ ] **Step 4: Add `_pose_in_reach_zone` and use it in the node waiter**

In `adaptor/adapter_jibot.py`, add the helper directly before
`_wait_until_node_position_reached` (~line 3138):

```python
    def _pose_in_reach_zone(
        self, vx: float, vy: float, tx: float, ty: float, deviation: float
    ) -> bool:
        """True when (vx, vy) is inside the reach zone around (tx, ty)."""
        zone_shape = str(
            getattr(self.config.settings, "reach_zone_shape", "square")
        ).strip().lower()
        if zone_shape == "circle":
            return math.hypot(vx - tx, vy - ty) <= deviation
        return abs(vx - tx) <= deviation and abs(vy - ty) <= deviation
```

Then in `_wait_until_node_position_reached`, replace the inline square/circle
block:

```python
                if zone_shape == "circle":
                    inside = (
                        math.hypot(vx - target_x, vy - target_y)
                        <= effective_deviation
                    )
                else:
                    inside = (
                        abs(vx - target_x) <= effective_deviation
                        and abs(vy - target_y) <= effective_deviation
                    )
                if inside:
```

with:

```python
                if self._pose_in_reach_zone(
                    vx, vy, target_x, target_y, effective_deviation
                ):
```

(The local `zone_shape` variable a few lines above is still used by the
`[ORDER NODE WAIT]` log line, so leave its assignment in place.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "dock_approach_node_lookup or pose_in_reach_zone" -v`
Expected: PASS.

- [ ] **Step 6: Regression — existing node-arrival tests still pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -v`
Expected: PASS (the `_pose_in_reach_zone` extraction must not change arrival
behaviour).

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): dock-approach lookup + extract pose reach-zone check"
```

---

### Task 4: approach + dock waiters

**Files:**
- Modify: `adaptor/adapter_jibot.py`
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `_pose_in_reach_zone`, `_effective_reach_deviation_xy`,
  `_optional_float`, `_is_jibot_stopped`, `_is_jibot_obstacle_wait`,
  `_set_jibot_node_unreached_error`, `_is_vehicle_charging`.
- Produces:
  - `async _wait_until_pose_reached(self, node, target_x, target_y, deviation_xy, label, poll_sec=0.2) -> None`
    — pose arrival at an internal waypoint; keeps the stopped-and-unreached
    diagnostic; no hard timeout. `deviation_xy` is the **raw** deviation; the
    helper applies `_effective_reach_deviation_xy` once.
  - `async _wait_until_dock_charge_or_timeout(self, node) -> bool` — True when
    charging starts, False after `config.dock_approach.fail_timeout_sec`.

- [ ] **Step 1: Write the failing tests**

Add to `adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
    def test_wait_until_pose_reached_returns_when_inside_zone(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            await self._start_state(adapter)
            from types import SimpleNamespace
            node = SimpleNamespace(node_id="F2_90_S2CH", sequence_id=2)
            vehicle.arrive_at(100.0, 200.0)  # already at the approach target
            await asyncio.wait_for(
                adapter._wait_until_pose_reached(node, 100.0, 200.0, 10.0, "APPROACH"),
                timeout=1.0,
            )

        asyncio.run(scenario())

    def test_wait_until_pose_reached_flags_unreached_when_stopped_short(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            await self._start_state(adapter)
            from types import SimpleNamespace
            node = SimpleNamespace(node_id="F2_90_S2CH", sequence_id=2)
            vehicle._status = "Stopped"
            vehicle.arrive_at(9000.0, 9000.0)  # far outside the zone

            waiter = asyncio.create_task(
                adapter._wait_until_pose_reached(node, 0.0, 0.0, 10.0, "APPROACH", poll_sec=0.05)
            )
            for _ in range(60):
                await asyncio.sleep(0.05)
                if any(e.error_type == ErrorType.JIBOT_NODE_UNREACHED
                       for e in adapter.state.errors):
                    break
            waiter.cancel()
            self.assertTrue(any(e.error_type == ErrorType.JIBOT_NODE_UNREACHED
                                for e in adapter.state.errors))

        asyncio.run(scenario())

    def test_wait_until_dock_charge_or_timeout(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            await self._start_state(adapter)
            from types import SimpleNamespace
            node = SimpleNamespace(node_id="F2_90_S2CH", sequence_id=2)

            adapter.config.dock_approach.fail_timeout_sec = 0.2
            # never charges -> timeout -> False
            self.assertFalse(await adapter._wait_until_dock_charge_or_timeout(node))

            # charges -> True
            vehicle._charging = True
            adapter.config.dock_approach.fail_timeout_sec = 1.0
            self.assertTrue(await adapter._wait_until_dock_charge_or_timeout(node))

        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "wait_until_pose_reached or wait_until_dock_charge" -v`
Expected: FAIL — `AttributeError` on the new methods.

- [ ] **Step 3: Implement the waiters**

In `adaptor/adapter_jibot.py`, directly after `_wait_until_node_position_reached`
(it ends with the `await asyncio.sleep(poll_sec)` of its loop, ~line 3219), add:

```python
    async def _wait_until_pose_reached(
        self,
        node: Any,
        target_x: float,
        target_y: float,
        deviation_xy: float,
        label: str,
        poll_sec: float = 0.2,
    ) -> None:
        """Block until the robot pose enters an internal waypoint's reach zone.

        Used for the dock approach hop (not a VDA order node), so it skips the
        order arrival-mismatch warning but KEEPS the stopped-and-unreached
        diagnostic: UmGoto is no-ack, so a silently dropped approach goto would
        otherwise wait here with no signal. No hard timeout, like the node
        waiter (cancelOrder / new order cancels the worker).
        """
        effective_deviation = self._effective_reach_deviation_xy(deviation_xy)
        print(
            f"[ORDER NODE APPROACH WAIT] id={getattr(node, 'node_id', '')} "
            f"approach={label} target=({target_x}, {target_y}) "
            f"radius={effective_deviation}"
        )
        wait_started_at = time.monotonic()
        unreached_error_reported = False
        while True:
            vx = self._optional_float(getattr(self._vehicle, "_x", None))
            vy = self._optional_float(getattr(self._vehicle, "_y", None))
            if vx is not None and vy is not None:
                if self._pose_in_reach_zone(
                    vx, vy, target_x, target_y, effective_deviation
                ):
                    print(
                        f"[ORDER NODE APPROACH REACHED] approach={label} "
                        f"pos=({vx:.1f}, {vy:.1f})"
                    )
                    return
                if (
                    self._is_jibot_stopped()
                    and not self._is_jibot_obstacle_wait()
                    and time.monotonic() - wait_started_at >= 1.0
                    and not unreached_error_reported
                ):
                    station = str(getattr(self._vehicle, "_station", "") or "").strip()
                    self._set_jibot_node_unreached_error(
                        node, target_x, target_y, vx, vy, effective_deviation, station
                    )
                    unreached_error_reported = True
                    print(
                        f"[ORDER NODE APPROACH UNREACHED] approach={label} "
                        f"status={getattr(self._vehicle, '_status', '')} "
                        f"pos=({vx:.1f}, {vy:.1f}) target=({target_x:.1f}, {target_y:.1f})"
                    )
            await asyncio.sleep(poll_sec)

    async def _wait_until_dock_charge_or_timeout(self, node: Any) -> bool:
        """True when charging starts, False after fail_timeout_sec.

        The escape the original _wait_until_docking_complete lacks: a failed
        seat (no charge) returns False so the caller rejects the step instead of
        hanging in DOCKING forever.
        """
        timeout = float(getattr(self.config.dock_approach, "fail_timeout_sec", 30.0))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_vehicle_charging():
                return True
            await asyncio.sleep(0.2)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k "wait_until_pose_reached or wait_until_dock_charge" -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): approach pose waiter + dock charge-or-timeout waiter"
```

---

### Task 5: orchestrator + `_process_v3_node_step` branch (integration)

**Files:**
- Modify: `adaptor/adapter_jibot.py`
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `_dock_approach_node`, `_map_nodes`, `goto_point`, `_await_goto_ack`,
  `_wait_until_pose_reached`, `um_dock`, `_docking_started_node_ids`,
  `_sync_docking_action_state`, `_wait_until_dock_charge_or_timeout`,
  `_set_jibot_goto_rejected_error`, `_set_jibot_dock_fail_error`,
  `_clear_jibot_goto_rejected_error`, `_finish_step_placeholder_action`.
- Produces:
  - `async _run_approach_then_dock(self, node, approach: str) -> Optional[str]`
    — runs UmGoto(approach) → arrival → UmDock → charge-or-timeout; sets the
    phase-appropriate error itself; returns a reason string on failure, else None.
  - `_finalize_v3_node_step(self, step, node) -> bool` — shared node-completion
    bookkeeping (returns True).
  - New branch at the top of `_process_v3_node_step`.

- [ ] **Step 1: Write the failing integration tests**

Add to `adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
    def _approach_node(self, node_id="F2_90_S2CH", seq=2):
        from protocol.vda5050_3_0.messages import Node
        return Node.from_dict({
            "nodeId": node_id, "sequenceId": seq, "released": True,
            "nodePosition": {"x": 100.0, "y": 200.0, "theta": 0.0, "mapId": "lab2m"},
            "actions": [],
        })

    def test_approach_dock_happy_path(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.dock_approach.nodes = {"F2_90_S2CH": "F2_90_S2CH_BEFORE"}
                vehicle._map_nodes = {"F2_90_S2CH_BEFORE": (100.0, 200.0, 0.0)}
                vehicle.arrive_at(100.0, 200.0)  # at the approach -> arrival immediate
                node = self._approach_node()

                async def finish_docking() -> None:
                    await asyncio.sleep(0.05)
                    vehicle._charging = True
                    vehicle._status = adapter.config.jibot_status.charging

                asyncio.create_task(finish_docking())
                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.goto_targets, ["F2_90_S2CH_BEFORE"])  # UmGoto to approach
                self.assertEqual(vehicle.dock_calls, 1)                         # then UmDock
                self.assertEqual(adapter.state.last_node_id, "F2_90_S2CH")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_approach_dock_fails_when_never_charging(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.dock_approach.nodes = {"F2_90_S2CH": "F2_90_S2CH_BEFORE"}
                adapter.config.dock_approach.fail_timeout_sec = 0.2
                vehicle._map_nodes = {"F2_90_S2CH_BEFORE": (100.0, 200.0, 0.0)}
                vehicle.arrive_at(100.0, 200.0)
                node = self._approach_node()

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertEqual(vehicle.dock_calls, 1)
                self.assertNotIn("F2_90_S2CH", adapter._docking_started_node_ids)
                self.assertTrue(any(e.error_type == ErrorType.JIBOT_DOCK_FAILED
                                    for e in adapter.state.errors))
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_approach_dock_missing_coords_sends_no_command(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.dock_approach.nodes = {"F2_90_S2CH": "F2_90_S2CH_BEFORE"}
                vehicle._map_nodes = {}  # approach not on the map
                node = self._approach_node()

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertEqual(vehicle.goto_targets, [])  # nothing dispatched
                self.assertEqual(vehicle.dock_calls, 0)
                self.assertTrue(any(e.error_type == ErrorType.JIBOT_DOCK_FAILED
                                    for e in adapter.state.errors))
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_approach_dock_goto_rejection_is_goto_error(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.dock_approach.nodes = {"F2_90_S2CH": "F2_90_S2CH_BEFORE"}
                vehicle._map_nodes = {"F2_90_S2CH_BEFORE": (100.0, 200.0, 0.0)}
                vehicle.goto_response = {"#CMD#": "error", "msg": "robot in stop mode"}
                node = self._approach_node()

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertEqual(vehicle.dock_calls, 0)  # never reached UmDock
                self.assertTrue(any(e.error_type == ErrorType.JIBOT_GOTO_REJECTED
                                    for e in adapter.state.errors))
            finally:
                state_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k approach_dock -v`
Expected: FAIL — `_process_v3_node_step` has no approach branch yet (the order
node is treated as a plain goto; `goto_targets`/`dock_calls` assertions fail).

- [ ] **Step 3: Add `_finalize_v3_node_step`**

In `adaptor/adapter_jibot.py`, add directly before `_process_v3_node_step`
(~line 3221):

```python
    def _finalize_v3_node_step(self, step: "OrderStep", node: Any) -> bool:
        """Shared node-completion bookkeeping; returns True (step done)."""
        self._last_node_id = node.node_id
        self._last_node_sequence_id = node.sequence_id
        self._finish_step_placeholder_action(step)
        if self.state is not None:
            self.state.last_node_id = self._last_node_id
            self.state.last_node_sequence_id = self._last_node_sequence_id
        self.request_state_publish("node reached")
        return True
```

Then in `_process_v3_node_step`, replace the existing finalize tail:

```python
        self._last_node_id = node.node_id
        self._last_node_sequence_id = node.sequence_id
        self._finish_step_placeholder_action(step)
        if self.state is not None:
            self.state.last_node_id = self._last_node_id
            self.state.last_node_sequence_id = self._last_node_sequence_id
        self.request_state_publish("node reached")
        return True
```

with:

```python
        return self._finalize_v3_node_step(step, node)
```

- [ ] **Step 4: Add the orchestrator**

In `adaptor/adapter_jibot.py`, add `_run_approach_then_dock` directly after
`_finalize_v3_node_step`:

```python
    async def _run_approach_then_dock(self, node: Any, approach: str) -> Optional[str]:
        """Drive to the approach waypoint, then UmDock into the charger.

        Owns per-phase error surfacing; returns a reason string on failure
        (error already published) or None on success. Every command send is
        wrapped so a transport exception rejects the step instead of crashing
        the order worker (which only wraps steps in try/finally).
        """
        # 0. validate approach coords BEFORE dispatching any motion. Dock must
        #    not fall back to send-and-complete: missing coords = hard fail.
        coords = self._map_nodes().get(approach)
        if not coords:
            reason = f"approach node '{approach}' missing from map"
            self._set_jibot_dock_fail_error(node, reason)
            return reason

        # 1. drive to the approach via UmGoto (by name).
        print(
            f"[ORDER NODE APPROACH GOTO] id={node.node_id} approach={approach}"
        )
        try:
            await self._vehicle.goto_point(approach)
        except Exception as exc:  # transport/send failure
            reason = f"approach goto send failed: {exc}"
            self._set_jibot_goto_rejected_error(node, reason)
            return reason
        reason = await self._await_goto_ack(node)
        if reason is not None:
            self._set_jibot_goto_rejected_error(node, reason)
            return reason

        # 2. wait for pose arrival at the approach waypoint.
        deviation = float(
            getattr(self.config.settings, "default_node_deviation_xy", 10.0)
        )
        await self._wait_until_pose_reached(
            node, coords[0], coords[1], deviation, label=approach
        )

        # 3. UmDock to seat into the charger.
        print(f"[ORDER NODE APPROACH DOCK] id={node.node_id} command=UmDock")
        try:
            await self._vehicle.um_dock()
        except Exception as exc:
            reason = f"UmDock send failed: {exc}"
            self._set_jibot_dock_fail_error(node, reason)
            return reason
        self._docking_started_node_ids.add(str(node.node_id))
        self._sync_docking_action_state()  # surface DOCKING immediately

        # 4. wait for charging, or fail out on timeout (anti-hang).
        if not await self._wait_until_dock_charge_or_timeout(node):
            self._docking_started_node_ids.discard(str(node.node_id))
            timeout = float(getattr(self.config.dock_approach, "fail_timeout_sec", 30.0))
            reason = f"dock failed: not charging within {timeout}s after UmDock"
            self._set_jibot_dock_fail_error(node, reason)
            return reason
        return None
```

- [ ] **Step 5: Add the branch in `_process_v3_node_step`**

In `_process_v3_node_step`, the body currently begins (after the `self._vehicle
is None` guard that `return False`s) with:

```python
        rejection_reason = await self._send_node_motion(node)
```

Insert the approach branch immediately before that line:

```python
        approach = self._dock_approach_node(node.node_id)
        if approach is not None:
            reason = await self._run_approach_then_dock(node, approach)
            if reason is not None:
                print(
                    f"[ORDER NODE APPROACH-DOCK FAILED] id={node.node_id} "
                    f"reason={reason}"
                )
                return False
            self._clear_jibot_goto_rejected_error()
            return self._finalize_v3_node_step(step, node)

        rejection_reason = await self._send_node_motion(node)
```

- [ ] **Step 6: Run integration tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k approach_dock -v`
Expected: PASS (all four).

- [ ] **Step 7: Full regression**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py tests/test_dock_approach_config.py -v`
Expected: PASS (no existing order/dock test regressed).

- [ ] **Step 8: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): approach-then-UmDock order flow for charger nodes"
```

---

## Self-Review

**Spec coverage:**
- §A config (new file + dataclass + loader merge) → Task 1. ✓
- §B detection / orchestrator / finalize extraction → Tasks 3, 5. ✓
- §B helpers (`_wait_until_pose_reached`, `_wait_until_dock_charge_or_timeout`,
  `_pose_in_reach_zone`) → Tasks 3, 4. ✓
- §C failure taxonomy (FATAL goto reuse + new dock-fail FATAL, anti-hang discard)
  → Tasks 2, 5. ✓
- §D tests (config merge, detection, happy path, fail timeout, command-send
  exception via coords/dock paths, missing coords, goto rejected, approach
  unreached) → Tasks 1–5. ✓
- R1 (wrapped sends) → Task 5 orchestrator. R2 (unreached diagnostic) → Task 4.
  R3 (coords before dispatch) → Task 5 step 0. R4 (reuse FATAL goto error) →
  Task 5. ✓

**Note vs. spec:** the spec sketch applied `_effective_reach_deviation_xy` in the
orchestrator and again in the waiter (double application). This plan is
authoritative: the orchestrator passes the **raw** `default_node_deviation_xy`
and `_wait_until_pose_reached` applies `_effective_reach_deviation_xy` **once**,
matching `_wait_until_node_position_reached`.

**Placeholder scan:** none — every step has concrete file paths, code, commands,
and expected output.

**Type consistency:** `DockApproachConfig.nodes` / `fail_timeout_sec`,
`ErrorType.JIBOT_DOCK_FAILED`, `_dock_approach_node`, `_pose_in_reach_zone`,
`_wait_until_pose_reached(node, x, y, deviation_xy, label, poll_sec)`,
`_wait_until_dock_charge_or_timeout(node)`, `_run_approach_then_dock(node,
approach)`, `_finalize_v3_node_step(step, node)` are referenced consistently
across tasks.

## Out of scope (tracked separately)

- Stale `config.charge.nodes` (`F2_M01_091_S2CH` vs map `F2_90_S2CH`).
- Inverted map Dock/Goal categories; residual
  `_is_jibot_map_dock_node("F2_90_S2CH_BEFORE") == True` (harmless: ACS never
  sends `_BEFORE` as an order node).
- Wrapping the pre-existing `_send_node_motion` UmDock send in try/except.
