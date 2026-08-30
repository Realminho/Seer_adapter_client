# Move Segment Goto Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `mode="move"` segment that finishes its relative move outside the to-node reach zone drives the remainder with an ordinary goto, instead of publishing `UNREACHED` once and polling forever.

**Architecture:** Move *completion* becomes distance-based (`travelled` vs. commanded `distance`) while node *arrival* stays pose-based against the reach zone. A new `_wait_until_move_settled` answers "has the move finished, and did it land in the zone"; `_run_move_segment` falls back to a newly extracted pure-goto path when it did not. Both the new wait and the existing arrival wait honour `_motion_paused`, and a paused relative move resumes by re-issuing only its remaining distance.

**Tech Stack:** Python 3.12, asyncio, `unittest` + `pytest`, dataclass config (`adaptor/config/config.py`), TOML config (`config.toml`).

**Spec:** `docs/superpowers/specs/2026-08-07-move-segment-goto-fallback-design.md`

## Global Constraints

- Run tests with `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python` — never bare `pytest`. ROS registers `launch_testing` pytest plugins whose hook signatures break collection; the script sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `cd`s into `adaptor/`, so test paths are relative to `adaptor/`. The `--python` flag is required: the script only auto-detects `<repo>/.venv`, but this project's virtualenv lives at `adaptor/.venv`, and without it the run falls back to a system `python3` that lacks `paho` and dies during collection.
- **Pre-existing failures, not yours:** `test_pio_init_fails_when_the_facility_never_raises_go`, `test_pio_init_retries_the_bc_until_the_facility_raises_go`, and `test_pio_ping_fails_when_the_facility_never_raises_go` — 3 tests counted twice because `ManualControlInstantActionTest` inherits from `AdapterV3OrderTest`. Baseline on this branch is exactly `6 failed, 589 passed` for `tests/test_adapter_jibot_v3_order.py`. They need real PIO hardware. Do not try to fix them and do not treat them as regressions; only care that your own tests pass and that this count does not grow.
- All new adapter code goes in `adaptor/adapter_jibot.py`; all new tests in `adaptor/tests/test_adapter_jibot_v3_order.py`. Follow the surrounding style: `print("[ORDER NODE ...] ...")` for operational logging, comments explaining *why* rather than *what*.
- **`_wait_until_node_position_reached` was rewritten by concurrent work and must NOT be modified by any remaining task.** It now keeps its unbounded wait *and* re-sends a stalled goto up to `node_goto_retry_limit` times before escalating to a FATAL error. Two properties matter downstream:
  - Its stall clock already carries `and not self._motion_paused`, so the pause guard originally planned as Task 3 **is already implemented**. Task 3 has been removed.
  - Retry is gated on `goto_recoverable = self._active_goto_node is node`. Its own comment states that a `mode="move"` segment also waits here but was driven by a relative move, so repeating a goto would be wrong. Task 5's fallback therefore *must* set `_active_goto_node` before calling this wait — that is what opts the fallback goto into retry coverage, which is the behaviour we want.
- The fallback applies to every `mode="move"` rule. No per-rule opt-in key.
- New `[settings]` keys and their defaults, used verbatim: `move_start_timeout_sec = 10.0`, `move_started_min_travel_mm = 50.0`, `move_complete_travel_ratio = 0.9`, `move_stall_timeout_sec = 5.0`.
- Existing keys are reused, not duplicated: `node_position_poll_interval_sec` for poll cadence, and `reach_zone_shape` / `reach_zone_scale` / `default_node_deviation_xy` through `_effective_reach_deviation_xy`.
- `math` and `time` are already imported in `adapter_jibot.py`. Do not re-import.
- **There are two `config.toml` files and they are kept in sync by convention.** `adaptor/config/config.py:765` sets `_CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"`, so **`adaptor/config/config.toml` is the one the adapter actually loads** — and the one that maps to `/home/ucore/adaptor/config/config.toml` on the robots. The repo-root `config.toml` is a second copy. `node_unreached_delay_sec`, `goto_nearest_timeout_sec`, and `standstill_timeout_sec` all appear in both. Any settings key you add belongs in both, and a test that asserts a TOML-sourced value only means something once the key is in the loaded file.

---

### Task 1: Configuration knobs

**Files:**
- Modify: `adaptor/config/config.py:267` (inside the `Settings` dataclass, after `node_unreached_delay_sec`)
- Modify: `config.toml` (repo root, `[settings]` section, after `node_unreached_delay_sec`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.settings.move_start_timeout_sec: float`, `config.settings.move_started_min_travel_mm: float`, `config.settings.move_complete_travel_ratio: float`, `config.settings.move_stall_timeout_sec: float`. Tasks 4, 5, and 6 read these.

- [ ] **Step 1: Write the failing test**

Add to `adaptor/tests/test_adapter_jibot_v3_order.py`, inside `class AdapterV3OrderTest`, next to the other `test_move_*` tests (near line 9049):

```python
    def test_move_settle_settings_have_documented_defaults(self) -> None:
        adapter = self._make_adapter()
        settings = adapter.config.settings
        self.assertEqual(settings.move_start_timeout_sec, 10.0)
        self.assertEqual(settings.move_started_min_travel_mm, 50.0)
        self.assertEqual(settings.move_complete_travel_ratio, 0.9)
        self.assertEqual(settings.move_stall_timeout_sec, 5.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k move_settle_settings -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'move_start_timeout_sec'`

- [ ] **Step 3: Add the fields**

In `adaptor/config/config.py`, immediately after the `node_unreached_delay_sec: float = 1.0` line inside `Settings`:

```python
    # mode="move" segments only. Completion of a relative move is distance-based
    # (the commanded distance is deliberately shorter than the segment), so it
    # needs its own knobs rather than reusing node_unreached_delay_sec.
    # Give-up window for a move that never starts, and for the origin pose that
    # distance-based completion needs before dispatch.
    move_start_timeout_sec: float = 10.0
    # Displacement that confirms the move started, for a move too short or fast
    # for any telemetry sample to catch it in motion.
    move_started_min_travel_mm: float = 50.0
    # Fraction of the commanded distance counting as full travel.
    move_complete_travel_ratio: float = 0.9
    # WARNING: affects fallback timing — how long a stop short of the commanded
    # distance must persist before the move counts as ended early.
    move_stall_timeout_sec: float = 5.0
```

- [ ] **Step 4: Document the keys in `config.toml`**

In the repo-root `config.toml`, in `[settings]`, immediately after the `node_unreached_delay_sec` line:

```toml
# mode="move" segment completion. A relative move runs a fixed distance that is
# deliberately shorter than the segment; when it ends outside the to-node reach
# zone the adapter drives the remainder with an ordinary goto.
move_start_timeout_sec          = 10.0  # move never started, or no pose before dispatch
move_started_min_travel_mm      = 50.0  # displacement confirming the move started
move_complete_travel_ratio      = 0.9   # fraction of distance counting as full travel
# WARNING: affects fallback timing — stop duration confirming an early stop.
move_stall_timeout_sec          = 5.0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k move_settle_settings -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add adaptor/config/config.py config.toml adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(config): add mode=move settle knobs"
```

---

### Task 2: Extract a pure goto send path

`_send_node_motion` is not an "ordinary goto": it branches into `should_charge_in_place` and, for `[dock].nodes` members, a bare `UmDock`. `should_charge_in_place` keys off `_is_dock_motion_node`, which is **non-directional** — true for any `mode="dock"` rule's `to` — and `adaptor/config/config.py:385` documents a move rule whose `to` is a charger. A fallback routed through `_send_node_motion` could dock instead of driving to the node.

**Files:**
- Modify: `adaptor/adapter_jibot.py:4292-4369` (`_send_node_motion`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `async def _send_node_goto(self, node: Any) -> Optional[str]` — transmits a plain goto and returns a rejection reason or `None`. Task 5 calls it.

- [ ] **Step 1: Write the failing test**

Add to `class AdapterV3OrderTest`:

```python
    def test_send_node_goto_skips_dock_and_charge_branches(self) -> None:
        """A move rule may target a charger (config.py:385 documents exactly
        that), and _is_dock_motion_node is non-directional, so the fallback
        must not reach _send_node_motion's dock / charge-in-place branches."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter.config.dock.nodes = ["CH"]          # makes CH a dock-work node
            adapter._last_node_id = "CH"                # arms should_charge_in_place
            vehicle._map_path_points["CH"] = (100.0, 200.0, 0.0)
            node = self._move_node(to="CH", x=100.0, y=200.0)

            reason = await adapter._send_node_goto(node)

            self.assertIsNone(reason)
            self.assertEqual(vehicle.goto_targets, [(100.0, 200.0, 0.0)])
            self.assertEqual(vehicle.dock_calls, 0)

        asyncio.run(scenario())

    def test_send_node_motion_still_docks_dock_work_nodes(self) -> None:
        """Guard the extraction: existing callers keep the dock branch."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter.config.dock.nodes = ["CH"]
            adapter._last_node_id = "SOMEWHERE_ELSE"    # disarm charge-in-place
            node = self._move_node(to="CH", x=100.0, y=200.0)

            await adapter._send_node_motion(node)

            self.assertEqual(vehicle.dock_calls, 1)
            self.assertEqual(vehicle.goto_targets, [])

        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k "send_node_goto or still_docks" -v`
Expected: `test_send_node_goto_skips_dock_and_charge_branches` FAILs with `AttributeError: 'Adapter' object has no attribute '_send_node_goto'`. `test_send_node_motion_still_docks_dock_work_nodes` PASSes already — it is the regression guard for Step 3.

- [ ] **Step 3: Extract the goto body**

In `adaptor/adapter_jibot.py`, replace the tail of `_send_node_motion` (everything from `position = getattr(node, "node_position", None)` at line 4332 through `return await self._await_goto_ack(node)` at line 4369) with a single delegating line:

```python
        return await self._send_node_goto(node)
```

Then add the new method immediately after `_send_node_motion`, before `_await_goto_ack`:

```python
    async def _send_node_goto(self, node: Any) -> Optional[str]:
        """Transmit a plain goto to a node; no dock or charge branches.

        Split out of _send_node_motion so the move-segment fallback can ask for
        a goto and get only a goto. _send_node_motion's charge-in-place and
        UmDock branches key off _is_dock_motion_node, which is NON-directional,
        and a mode="move" rule may legitimately target a charger (see the
        MotionRule docstring in config/config.py) — routing the fallback through
        them could dock the robot instead of driving it to the node.

        Returns a rejection reason, or None when the goto was accepted.
        """
        position = getattr(node, "node_position", None)
        if self._is_simulator() and position is not None:
            # The simulator has no onboard map/localization, so it cannot resolve
            # a bare node id to coordinates. Feed it the order's nodePosition and
            # adopt its mapId so reported pose/map follow the commanded route.
            if position.map_id:
                self._current_map_id = position.map_id
            print(
                f"[ORDER NODE GOTO_POSITION:SIM] seq={node.sequence_id} "
                f"id={node.node_id} x={position.x} y={position.y} "
                f"theta={position.theta} mapId={position.map_id}"
            )
            goto_node_position = getattr(self._vehicle, "goto_node_position", None)
            if callable(goto_node_position):
                await goto_node_position(
                    node.node_id, position.x, position.y, position.theta
                )
            else:
                await self._vehicle.goto_xyz(position.x, position.y, position.theta)
            return await self._await_goto_ack(node)

        get_path_point_pose = getattr(self._vehicle, "get_path_point_pose", None)
        path_point_pose = (
            get_path_point_pose(str(node.node_id))
            if callable(get_path_point_pose)
            else None
        )
        if path_point_pose is not None:
            x, y, theta = path_point_pose
            print(
                f"[ORDER NODE GOTO_PATH_POINT_POSE] seq={node.sequence_id} "
                f"id={node.node_id} x={x} y={y} theta={theta}"
            )
            await self._vehicle.goto_xyz(x, y, theta)
        else:
            print(f"[ORDER NODE GOTO_POINT] seq={node.sequence_id} id={node.node_id}")
            await self._vehicle.goto_point(node.node_id)
        return await self._await_goto_ack(node)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k "send_node_goto or still_docks" -v`
Expected: both PASS

- [ ] **Step 5: Run the full order suite for regressions**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -q`
Expected: all PASS. This is a pure refactor; any failure means the extraction changed behaviour.

- [ ] **Step 6: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "refactor: extract _send_node_goto from _send_node_motion"
```

---

### Task 3: REMOVED — already implemented by concurrent work

This task added the `_motion_paused` guard to the `UNREACHED` branch of
`_wait_until_node_position_reached`. Concurrent work rewrote that method for an
unrelated manual-driving fix and included the same guard: its stall condition
now reads `and not self._motion_paused`. The behaviour this task existed to
produce is present, so there is nothing left to implement.

Do not re-add it, and do not modify that method — see Global Constraints.

---

### Task 4: Move completion detection

**Files:**
- Modify: `adaptor/adapter_jibot.py` (add two methods after `_wait_until_node_position_reached`, which ends at line 4591)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `config.settings.move_*` from Task 1; `self._motion_paused` from Task 3.
- Produces:
  - `async def _wait_for_vehicle_pose(self, timeout_sec: float) -> Optional[Tuple[float, float]]`
  - `async def _wait_until_move_settled(self, node: Any, target: Tuple[float, float, float], distance: float, start_pose: Tuple[float, float]) -> bool` — `True` when the pose is in the to-node reach zone, `False` when the move finished outside it.

  Task 5 calls both.

- [ ] **Step 1: Write the failing tests**

Add a new test class at the end of `adaptor/tests/test_adapter_jibot_v3_order.py`. It exercises `_wait_until_move_settled` directly so each branch is isolated from order plumbing.

```python
class MoveSettleWaitTest(unittest.TestCase):
    """Completion detection for mode="move" segments.

    Move completion is distance-based; node arrival stays pose-based. These
    two questions have different answers whenever the commanded distance is
    shorter than the segment, which is the intended way to configure the rule.
    """

    def _adapter(self) -> Adapter:
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
        s.default_node_deviation_xy = 10.0     # -> 200.0 effective radius
        return adapter

    def _node(self, node_id="p39"):
        return SimpleNamespace(node_id=node_id, sequence_id=2)

    def test_full_travel_outside_zone_returns_false(self) -> None:
        """The 2026-08-07 HN-SH6-TR-002 case: commanded 2000mm, travelled
        2031mm, and still 765mm short of p39. Used to hang forever."""
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(12681.0, -2473.0)
            reached = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            )
            self.assertFalse(reached)
        asyncio.run(scenario())

    def test_full_travel_inside_zone_returns_true(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(13400.0, -2505.0)          # 46mm from target, inside 200
            reached = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2800.0, (10650.0, -2522.0)
            )
            self.assertTrue(reached)
        asyncio.run(scenario())

    def test_stopped_but_not_yet_moving_does_not_settle_early(self) -> None:
        """Regression for the premature firing seen on the robot at
        2026-08-05 23:50:55 and 2026-08-06 15:09:51, both exactly 1.0s after
        the move was sent with the robot still stationary."""
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10650.0, -2522.0)          # has not moved at all
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.1)               # past stall, short of start timeout
            self.assertFalse(wait.done())
            wait.cancel()
        asyncio.run(scenario())

    def test_never_starting_returns_false_after_start_timeout(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10650.0, -2522.0)
            reached = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            )
            self.assertFalse(reached)
        asyncio.run(scenario())

    def test_displacement_alone_satisfies_the_start_gate(self) -> None:
        """A move short or fast enough that no sample catches it moving."""
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10750.0, -2522.0)          # 100mm > move_started_min_travel_mm
            reached = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            )
            self.assertFalse(reached)              # settled via stall, not start timeout
        asyncio.run(scenario())

    def test_short_travel_waits_for_the_stall_timeout(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "moving"
            v.arrive_at(11000.0, -2522.0)          # 350mm of a 2000mm move
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.05)
            v._status = "Stopped"                  # stall begins now
            await asyncio.sleep(0.05)
            self.assertFalse(wait.done())          # stall timeout not yet elapsed
            await asyncio.sleep(0.3)
            self.assertTrue(wait.done())
            self.assertFalse(await wait)
        asyncio.run(scenario())

    def test_resumed_motion_restarts_the_stall_timer(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "moving"
            v.arrive_at(11000.0, -2522.0)
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            v._status = "Stopped"
            await asyncio.sleep(0.15)              # most of the stall timeout
            v._status = "moving"                   # clears the stopped-since marker
            await asyncio.sleep(0.1)
            self.assertFalse(wait.done())          # timer restarted, not accumulated
            wait.cancel()
        asyncio.run(scenario())

    def test_obstacle_wait_blocks_the_stall_branch(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped #brake"           # stopped AND obstacle-waiting
            v.arrive_at(11000.0, -2522.0)
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.4)               # past both timeouts
            self.assertFalse(wait.done())
            wait.cancel()
        asyncio.run(scenario())

    def test_zero_distance_settles_on_the_first_stopped_sample(self) -> None:
        """target_travel is 0, so full travel holds immediately. Without the
        full-travel branch preceding the start gate this would wait out
        move_start_timeout_sec instead."""
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(13446.0, -2505.0)
            started = time.monotonic()
            reached = await adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), 0.0, (13446.0, -2505.0)
            )
            self.assertTrue(reached)
            self.assertLess(time.monotonic() - started, 0.15)   # not the 0.2 start timeout
        asyncio.run(scenario())

    def test_distance_below_start_threshold_settles_on_full_travel(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v.arrive_at(10680.0, -2522.0)          # travelled 30 of a 30mm move
            started = time.monotonic()
            reached = await adapter._wait_until_move_settled(
                self._node(), (10680.0, -2522.0, 10.0), -30.0, (10650.0, -2522.0)
            )
            self.assertTrue(reached)
            self.assertLess(time.monotonic() - started, 0.15)
        asyncio.run(scenario())

    def test_paused_move_never_settles(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            adapter._motion_paused = True
            v._status = "Stopped"
            v.arrive_at(12681.0, -2473.0)          # full travel, would settle unpaused
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.4)               # past both timeouts
            self.assertFalse(wait.done())
            wait.cancel()
        asyncio.run(scenario())

    def test_missing_pose_samples_are_skipped(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._status = "Stopped"
            v._x = None
            v._y = None
            wait = asyncio.create_task(adapter._wait_until_move_settled(
                self._node(), (13446.0, -2505.0, 10.0), -2000.0, (10650.0, -2522.0)
            ))
            await asyncio.sleep(0.4)
            self.assertFalse(wait.done())          # no pose, no judgment
            v.arrive_at(12681.0, -2473.0)          # telemetry returns
            await asyncio.sleep(0.1)
            self.assertTrue(wait.done())
            self.assertFalse(await wait)
        asyncio.run(scenario())

    def test_wait_for_vehicle_pose_returns_none_on_timeout(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            adapter._vehicle._x = None
            adapter._vehicle._y = None
            self.assertIsNone(await adapter._wait_for_vehicle_pose(0.05))
        asyncio.run(scenario())

    def test_wait_for_vehicle_pose_returns_first_valid_reading(self) -> None:
        async def scenario() -> None:
            adapter = self._adapter()
            v: FakeVehicle = adapter._vehicle
            v._x = None
            v._y = None

            async def arrive_soon() -> None:
                await asyncio.sleep(0.03)
                v.arrive_at(10650.0, -2522.0)

            asyncio.create_task(arrive_soon())
            self.assertEqual(await adapter._wait_for_vehicle_pose(1.0), (10650.0, -2522.0))
        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py::MoveSettleWaitTest -v`
Expected: every test FAILs with `AttributeError: 'Adapter' object has no attribute '_wait_until_move_settled'` (or `_wait_for_vehicle_pose`).

- [ ] **Step 3: Implement both methods**

Add to `adaptor/adapter_jibot.py`, immediately after `_wait_until_node_position_reached` (which ends at line 4591, before `_wait_until_dock_charge_or_timeout`):

```python
    async def _wait_for_vehicle_pose(
        self, timeout_sec: float
    ) -> Optional[Tuple[float, float]]:
        """Poll until the vehicle reports a usable (x, y), or give up.

        A relative move's completion is measured from its origin, so a missing
        pose makes the judgment meaningless. Callers fail the step rather than
        dispatch motion they cannot monitor.
        """
        poll_sec = getattr(
            self.config.settings, "node_position_poll_interval_sec", 0.2
        )
        deadline = time.monotonic() + timeout_sec
        while True:
            vx = self._optional_float(getattr(self._vehicle, "_x", None))
            vy = self._optional_float(getattr(self._vehicle, "_y", None))
            if vx is not None and vy is not None:
                return (vx, vy)
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(poll_sec)

    async def _wait_until_move_settled(
        self,
        node: Any,
        target: Tuple[float, float, float],
        distance: float,
        start_pose: Tuple[float, float],
    ) -> bool:
        """Block until the relative move has finished; True when it landed in
        the to-node reach zone, False when it finished outside it.

        A mode="move" rule's distance is deliberately shorter than the segment,
        so "the move finished" and "the robot arrived" are different questions.
        This answers the first from the commanded distance and the second from
        the pose, and never publishes UNREACHED: finishing short is normal and
        the caller drives the remainder with a goto.
        """
        settings = self.config.settings
        poll_sec = getattr(settings, "node_position_poll_interval_sec", 0.2)
        start_timeout = float(getattr(settings, "move_start_timeout_sec", 10.0))
        min_travel = float(getattr(settings, "move_started_min_travel_mm", 50.0))
        travel_ratio = float(getattr(settings, "move_complete_travel_ratio", 0.9))
        stall_timeout = float(getattr(settings, "move_stall_timeout_sec", 5.0))

        target_x, target_y, deviation_xy = target
        effective_deviation = self._effective_reach_deviation_xy(deviation_xy)
        target_travel = abs(float(distance))
        start_x, start_y = start_pose

        print(
            f"[ORDER NODE MOVE WAIT] id={node.node_id} "
            f"target=({target_x}, {target_y}) radius={effective_deviation} "
            f"travel={target_travel}"
        )

        started = False
        start_budget = 0.0     # accrues only while unpaused, with a known pose
        stopped_since = None   # monotonic stamp of the CURRENT continuous stop
        last_tick = time.monotonic()

        while True:
            now = time.monotonic()
            tick = now - last_tick
            last_tick = now

            # startPause stops the robot on purpose. Judging while paused would
            # read that as a stall and fire the fallback goto mid-pause.
            if self._motion_paused:
                stopped_since = None
                await asyncio.sleep(poll_sec)
                continue

            vx = self._optional_float(getattr(self._vehicle, "_x", None))
            vy = self._optional_float(getattr(self._vehicle, "_y", None))
            if vx is None or vy is None:
                await asyncio.sleep(poll_sec)
                continue

            start_budget += tick
            travelled = math.hypot(vx - start_x, vy - start_y)
            full_travel = travelled >= target_travel * travel_ratio
            stopped = self._is_jibot_stopped()
            obstacle = self._is_jibot_obstacle_wait()

            if stopped and not obstacle:
                if stopped_since is None:
                    stopped_since = now
            else:
                stopped_since = None

            reason = None
            if full_travel and stopped and not obstacle:
                # Checked BEFORE the start gate on purpose: a robot that covered
                # its commanded distance and stopped is finished whether or not
                # any sample caught it moving. This is also what makes a zero or
                # sub-threshold distance settle promptly instead of waiting out
                # start_timeout.
                reason = "full-travel"
            elif not started:
                if not stopped or travelled >= min_travel:
                    started = True
                elif start_budget >= start_timeout:
                    reason = "not-started"
            elif stopped_since is not None and (now - stopped_since) >= stall_timeout:
                reason = "stall"

            if reason is not None:
                reached = self._pose_in_reach_zone(
                    vx, vy, target_x, target_y, effective_deviation
                )
                print(
                    f"[ORDER NODE MOVE SETTLED] id={node.node_id} reason={reason} "
                    f"pos=({vx:.1f}, {vy:.1f}) "
                    f"travelled={travelled:.1f}/{target_travel:.1f} "
                    f"reached={reached}"
                )
                return reached

            await asyncio.sleep(poll_sec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py::MoveSettleWaitTest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: add distance-based move completion detection"
```

---

### Task 5: Wire the fallback into the move segment

**Files:**
- Modify: `adaptor/adapter_jibot.py:319` (`__init__`, next to `self._motion_paused`)
- Modify: `adaptor/adapter_jibot.py:4673-4728` (`_run_move_segment`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `_wait_for_vehicle_pose`, `_wait_until_move_settled` (Task 4); `_send_node_goto` (Task 2); `config.settings.move_start_timeout_sec` (Task 1).
- Produces: `self._active_move_segment: Optional[SimpleNamespace]` with attributes `node`, `start_pose: Tuple[float, float]`, `distance: float`, `speed: float`, `kwargs: Dict[str, Any]` — set while a relative move is in flight, `None` otherwise. Task 6 consumes it.

- [ ] **Step 1: Write the failing tests**

Add to `class AdapterV3OrderTest`, next to the other `test_move_segment_*` tests:

```python
    def test_move_segment_short_of_node_falls_back_to_goto(self) -> None:
        """The 2026-08-07 HN-SH6-TR-002 failure: order 20260807-2-1 ran its
        commanded 2000mm from the charger and stopped 765mm short of p39, then
        hung — p40, p37 and p38 were never attempted."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0      # -> 200.0 radius
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance
                original_goto_xyz = vehicle.goto_xyz

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(12681.0, -2473.0)   # full travel, still short

                async def arriving_goto_xyz(x, y, z, strict: bool = False) -> None:
                    await original_goto_xyz(x, y, z, strict=strict)
                    vehicle.arrive_at(x, y)

                vehicle.move_distance = moving_move_distance
                vehicle.goto_xyz = arriving_goto_xyz

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(len(vehicle.move_distance_calls), 1)
                self.assertEqual(vehicle.goto_targets, [(13446.0, -2505.0, 0.0)])
                self.assertEqual(vehicle.dock_calls, 0)
                self.assertEqual(adapter.state.last_node_id, "p39")
                self.assertIsNone(adapter._active_move_segment)
                # Finishing short is normal flow, so the settle wait must not
                # have reported the node unreached on the way through.
                self.assertEqual(
                    [e for e in adapter.state.errors
                     if "unreach" in str(getattr(e, "error_description", "")).lower()],
                    [],
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_inside_zone_sends_no_goto(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.settings.node_position_poll_interval_sec = 0.01
                adapter.config.motion_rules = [
                    MotionRule(to="B", mode="move", from_node="A", distance=2500)
                ]
                adapter._last_node_id = "A"
                vehicle._status = "Stopped"
                vehicle.arrive_at(300.0, 400.0)
                node = self._move_node(to="B", x=300.0, y=400.0)

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertTrue(ok)
                self.assertEqual(vehicle.goto_targets, [])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_fails_when_no_pose_before_dispatch(self) -> None:
        """Distance-based completion needs an origin. Never dispatch a relative
        move that cannot be monitored — the segment often starts in a charger."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                adapter.config.settings.node_position_poll_interval_sec = 0.01
                adapter.config.settings.move_start_timeout_sec = 0.05
                adapter.config.motion_rules = [
                    MotionRule(to="B", mode="move", from_node="A", distance=2500)
                ]
                adapter._last_node_id = "A"
                vehicle._x = None
                vehicle._y = None
                node = self._move_node(to="B", x=300.0, y=400.0)

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertEqual(vehicle.move_distance_calls, [])
                self.assertEqual(vehicle.goto_targets, [])
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_move_segment_fallback_goto_rejection_fails_the_step(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                vehicle.goto_response = {"#CMD#": "UmGoto", "result": "error",
                                         "msg": "path blocked"}
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance

                async def moving_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(12681.0, -2473.0)

                vehicle.move_distance = moving_move_distance

                ok = await adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                )

                self.assertFalse(ok)
                self.assertIsNone(adapter._active_move_segment)
            finally:
                state_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k "move_segment_short_of_node or move_segment_inside_zone or no_pose_before_dispatch or fallback_goto_rejection" -v`
Expected: FAIL — `test_move_segment_short_of_node_falls_back_to_goto` times out or asserts `goto_targets == []`; the others fail on `_active_move_segment` / dispatch assertions.

- [ ] **Step 3: Add the `SimpleNamespace` import**

`adaptor/adapter_jibot.py` imports `math`, `time`, and `Tuple` already, but **not** `SimpleNamespace`. Add it next to the other stdlib imports, after `from pathlib import Path` (line 16):

```python
from types import SimpleNamespace
```

- [ ] **Step 4: Declare the in-flight segment**

In `adaptor/adapter_jibot.py`, immediately after `self._motion_paused: bool = False` (line 319):

```python
        # Set while a mode="move" relative move is in flight, so stopPause can
        # resume it with the distance it still owes. Mutually exclusive with
        # _active_goto_node, which covers the fallback goto phase.
        self._active_move_segment: Optional[Any] = None
```

- [ ] **Step 5: Rewrite `_run_move_segment`**

Replace the body of `_run_move_segment` (lines 4673-4728) with:

```python
    async def _run_move_segment(self, node: Any, rule: Any) -> Optional[str]:
        """Traverse a from->to segment with a relative move, then a goto for
        whatever the move did not cover.

        The rule's distance is deliberately shorter than the segment: it only
        has to clear the constrained part (backing out of a charger, say), and
        an ordinary goto finishes the approach. Returns a rejection reason on
        failure, else None.
        """
        distance = getattr(rule, "distance", None)
        if distance is None:
            return f"move rule for node '{node.node_id}' has no distance"
        # Validate the arrival target BEFORE dispatching any motion.
        target = self._resolve_node_target(node)
        if target is None:
            return f"move target node '{node.node_id}' has no coordinates"
        mc = self.config.manual_control
        rule_speed = getattr(rule, "speed", None)
        speed = float(rule_speed) if rule_speed is not None else float(mc.default_move_speed)
        obs_avoid_dist = getattr(rule, "obs_avoid_dist", None)
        side_avoid_dist = getattr(rule, "side_avoid_dist", None)
        flag = getattr(rule, "flag", None)
        io = getattr(rule, "io", None)
        use_io = getattr(rule, "use_io", None)
        note = getattr(rule, "note", None)
        move_kwargs = {
            "obs_avoid_dist": (
                int(obs_avoid_dist) if obs_avoid_dist is not None
                else mc.move_obs_avoid_dist
            ),
            "side_avoid_dist": (
                int(side_avoid_dist) if side_avoid_dist is not None
                else mc.move_side_avoid_dist
            ),
            "flag": int(flag) if flag is not None else 1,
            "io": int(io) if io is not None else 1,
            "use_io": bool(use_io) if use_io is not None else False,
            "note": int(note) if note is not None else 1,
        }
        stop_reason = await self._ensure_not_charging_before_order_motion(node)
        if stop_reason is not None:
            return stop_reason

        # Completion is measured from the origin, so refuse to move blind.
        start_timeout = float(
            getattr(self.config.settings, "move_start_timeout_sec", 10.0)
        )
        start_pose = await self._wait_for_vehicle_pose(start_timeout)
        if start_pose is None:
            return (
                f"move origin pose unavailable within {start_timeout}s; "
                f"relative move to '{node.node_id}' not dispatched"
            )

        print(
            f"[ORDER NODE MOVE] id={node.node_id} distance={distance} speed={speed} "
            f"obs_avoid_dist={move_kwargs['obs_avoid_dist']} "
            f"side_avoid_dist={move_kwargs['side_avoid_dist']}"
        )
        self._active_move_segment = SimpleNamespace(
            node=node,
            start_pose=start_pose,
            distance=float(distance),
            speed=speed,
            kwargs=move_kwargs,
        )
        try:
            try:
                await self._vehicle.move_distance(float(distance), speed, **move_kwargs)
            except Exception as exc:  # transport/send failure
                return f"move send failed: {exc}"
            reached = await self._wait_until_move_settled(
                node, target, float(distance), start_pose
            )
        finally:
            self._active_move_segment = None

        if reached:
            return None

        # The move ran its course and stopped short. Normal flow, not an error:
        # cover the remainder with a plain goto. _send_node_goto rather than
        # _send_node_motion because the latter branches into UmDock and
        # charge-in-place, and a move rule's `to` may be a charger.
        print(
            f"[ORDER NODE MOVE SHORT] id={node.node_id} "
            f"distance={distance} -> goto"
        )
        self._active_goto_node = node
        try:
            rejection = await self._send_node_goto(node)
            if rejection is not None:
                return rejection
            await self._wait_until_node_position_reached(node, target)
        finally:
            self._active_goto_node = None
        return None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k "move_segment" -v`
Expected: all PASS, including the pre-existing `test_move_segment_*` tests.

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: fall back to goto when a move segment stops short"
```

---

### Task 6: Resume a paused relative move

`startPause` calls `stop_motion()`, which cancels the relative move. `stopPause` only re-issues `_active_goto_node`, so without this task a paused move segment never finishes.

**Files:**
- Modify: `adaptor/adapter_jibot.py` (add `_remaining_move_distance` near `_run_move_segment`)
- Modify: `adaptor/adapter_jibot.py:5982-5991` (the `_resume` closure in `_handle_stop_pause_instant_action`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `self._active_move_segment` (Task 5).
- Produces: `def _remaining_move_distance(self, segment: Any) -> float` — signed distance still owed, `0.0` when nothing is.

- [ ] **Step 1: Write the failing tests**

Add to `class AdapterV3OrderTest`:

```python
    def test_remaining_move_distance_keeps_the_original_sign(self) -> None:
        adapter = self._make_adapter()
        adapter._vehicle.arrive_at(11850.0, -2522.0)      # 1200 of a 2000mm move
        segment = SimpleNamespace(start_pose=(10650.0, -2522.0), distance=-2000.0)
        self.assertAlmostEqual(adapter._remaining_move_distance(segment), -800.0, places=3)

    def test_remaining_move_distance_is_zero_once_covered(self) -> None:
        adapter = self._make_adapter()
        adapter._vehicle.arrive_at(12681.0, -2473.0)      # travelled 2031 of 2000
        segment = SimpleNamespace(start_pose=(10650.0, -2522.0), distance=-2000.0)
        self.assertEqual(adapter._remaining_move_distance(segment), 0.0)

    def test_remaining_move_distance_is_zero_without_a_pose(self) -> None:
        """Re-issuing the full distance blind would overshoot; returning 0 lets
        the settle wait fall back to a goto instead."""
        adapter = self._make_adapter()
        adapter._vehicle._x = None
        adapter._vehicle._y = None
        segment = SimpleNamespace(start_pose=(10650.0, -2522.0), distance=-2000.0)
        self.assertEqual(adapter._remaining_move_distance(segment), 0.0)

    def test_stop_pause_reissues_the_remaining_move(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()
            adapter.state = SimpleNamespace(paused=True, action_states=[])
            adapter._update_instant_action_status = lambda *a, **k: None
            adapter._motion_paused = True
            vehicle.arrive_at(11850.0, -2522.0)           # 1200mm covered
            adapter._active_move_segment = SimpleNamespace(
                node=self._move_node(to="p39"),
                start_pose=(10650.0, -2522.0),
                distance=-2000.0,
                speed=150.0,
                kwargs={"obs_avoid_dist": 1000, "side_avoid_dist": 50,
                        "flag": 1, "io": 1, "use_io": False, "note": 1},
            )

            adapter._handle_stop_pause_instant_action("a1")
            await asyncio.sleep(0.1)

            self.assertEqual(len(vehicle.move_distance_calls), 1)
            dist, spd, kwargs = vehicle.move_distance_calls[0]
            self.assertAlmostEqual(dist, -800.0, places=3)
            self.assertEqual(spd, 150.0)
            self.assertEqual(kwargs["obs_avoid_dist"], 1000)
            self.assertEqual(vehicle.goto_targets, [])    # not converted to a goto
            self.assertFalse(adapter._motion_paused)

        asyncio.run(scenario())

    def test_stop_pause_reissues_nothing_once_the_move_is_covered(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            adapter._loop = asyncio.get_running_loop()
            adapter.state = SimpleNamespace(paused=True, action_states=[])
            adapter._update_instant_action_status = lambda *a, **k: None
            adapter._motion_paused = True
            vehicle.arrive_at(12681.0, -2473.0)           # already past 2000mm
            adapter._active_move_segment = SimpleNamespace(
                node=self._move_node(to="p39"),
                start_pose=(10650.0, -2522.0),
                distance=-2000.0,
                speed=150.0,
                kwargs={},
            )

            adapter._handle_stop_pause_instant_action("a1")
            await asyncio.sleep(0.1)

            self.assertEqual(vehicle.move_distance_calls, [])
            self.assertEqual(vehicle.goto_targets, [])
            self.assertFalse(adapter._motion_paused)

        asyncio.run(scenario())

    def test_start_pause_during_a_move_issues_no_goto(self) -> None:
        """Without the pause guard the stall branch fires and the fallback goto
        goes out while the order is paused."""
        async def scenario() -> None:
            adapter = self._make_adapter()
            vehicle: FakeVehicle = adapter._vehicle
            state_task = await self._start_state(adapter)
            try:
                s = adapter.config.settings
                s.node_position_poll_interval_sec = 0.01
                s.move_stall_timeout_sec = 0.05
                s.move_start_timeout_sec = 0.05
                s.reach_zone_scale = 20.0
                s.default_node_deviation_xy = 10.0
                adapter.config.motion_rules = [
                    MotionRule(to="p39", mode="move", from_node="1_01CH",
                               distance=-2000, speed=150)
                ]
                adapter._last_node_id = "1_01CH"
                vehicle._map_path_points["p39"] = (13446.0, -2505.0, 0.0)
                vehicle._status = "Stopped"
                vehicle.arrive_at(10650.0, -2522.0)
                node = self._move_node(to="p39", x=13446.0, y=-2505.0)

                original_move_distance = vehicle.move_distance

                async def pausing_move_distance(distance, speed, **kwargs) -> None:
                    await original_move_distance(distance, speed, **kwargs)
                    vehicle.arrive_at(11850.0, -2522.0)   # partway, then paused
                    adapter._motion_paused = True

                vehicle.move_distance = pausing_move_distance

                step = asyncio.create_task(adapter._process_v3_node_step(
                    OrderStep("node", node.sequence_id, node)
                ))
                await asyncio.sleep(0.3)                  # past both timeouts

                self.assertFalse(step.done())
                self.assertEqual(vehicle.goto_targets, [])
                step.cancel()
            finally:
                state_task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k "remaining_move_distance or stop_pause_reissues or start_pause_during_a_move" -v`
Expected: the `_remaining_move_distance` tests FAIL with `AttributeError`; `test_stop_pause_reissues_the_remaining_move` FAILs with `move_distance_calls == []`. `test_start_pause_during_a_move_issues_no_goto` PASSes already if Task 4 landed — it guards that behaviour here.

- [ ] **Step 3: Add `_remaining_move_distance`**

In `adaptor/adapter_jibot.py`, immediately before `_run_move_segment`:

```python
    def _remaining_move_distance(self, segment: Any) -> float:
        """Signed distance a paused relative move still owes; 0.0 when none.

        Keeps the commanded sign so the resumed move continues in the same
        direction. Without a pose the remainder is unknowable and re-issuing
        the full distance would overshoot, so return 0.0 and let the settle
        wait resolve the segment with its goto fallback.
        """
        vx = self._optional_float(getattr(self._vehicle, "_x", None))
        vy = self._optional_float(getattr(self._vehicle, "_y", None))
        if vx is None or vy is None:
            print("[STOP PAUSE] no pose; not re-issuing the relative move")
            return 0.0
        start_x, start_y = segment.start_pose
        travelled = math.hypot(vx - start_x, vy - start_y)
        remaining = abs(float(segment.distance)) - travelled
        if remaining <= 1.0:   # sub-millimetre remainder is not worth a command
            return 0.0
        return math.copysign(remaining, float(segment.distance))
```

- [ ] **Step 4: Resume the move in `_handle_stop_pause_instant_action`**

In the `_resume` closure (line 5982), replace:

```python
            try:
                node = self._active_goto_node
                if node is not None:
                    # stop_motion cancelled the active goto; the worker is
                    # still waiting on this node, so transmit the motion again.
                    # The return value (rejection reason) is intentionally ignored
                    # here: the worker remains parked on this node and will
                    # re-detect a persistent rejection on its next run.
                    await self._send_node_motion(node)
```

with:

```python
            try:
                # A paused relative move is resumed as a move, not a goto: the
                # rule exists because a goto is unsuitable on this segment.
                # _active_move_segment and _active_goto_node are mutually
                # exclusive (move phase vs. fallback-goto phase).
                segment = self._active_move_segment
                if segment is not None:
                    remaining = self._remaining_move_distance(segment)
                    if remaining:
                        print(
                            f"[STOP PAUSE] re-issuing move remaining={remaining:.1f}"
                        )
                        await self._vehicle.move_distance(
                            remaining, segment.speed, **segment.kwargs
                        )
                else:
                    node = self._active_goto_node
                    if node is not None:
                        # stop_motion cancelled the active goto; the worker is
                        # still waiting on this node, so transmit the motion again.
                        # The return value (rejection reason) is intentionally ignored
                        # here: the worker remains parked on this node and will
                        # re-detect a persistent rejection on its next run.
                        await self._send_node_motion(node)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k "remaining_move_distance or stop_pause_reissues or start_pause_during_a_move" -v`
Expected: all PASS

- [ ] **Step 6: Run the whole suite**

Run: `scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -q`
Expected: the 8 pre-existing failures listed in Global Constraints, and nothing else. Report the pass/fail counts so the growth of unrelated failures is visible.

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat: resume a paused move with its remaining distance"
```

---

## Deployment Note

The robot at `192.168.101.62` keeps its own `config/config.toml`; its rule is
`to = "p39"`, `from = "1_01CH"`, `distance = -2000`. That value stays as-is —
the user confirmed a short distance is intended. Deploying this change requires
`scripts/update-jibot-adapter-over-ssh.sh` plus the new `[settings]` keys, or
the built-in defaults if the keys are omitted. Verify after deploy by watching
for `[ORDER NODE MOVE SHORT] id=p39 ... -> goto` followed by
`[ORDER NODE REACHED] id=p39` in `journalctl -u amr-adaptor`.
