# gotoNearestNode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `gotoNearestNode` instant action + WebUI button that drives the JIBOT to its nearest map node via UmGoto and, on arrival, sets `lastNodeId` — so an off-graph robot can be re-anchored for the FMS.

**Architecture:** New instant action handled entirely adapter-side: the handler reads the live pose, resolves the nearest node with the existing `_find_nearest_node`, issues `goto_point`/`goto_xyz` (reusing the order motion path), polls the pose until it is within the existing lastNodeId-capture threshold (`_idle_last_node_reach_xy()`, live config 100mm), then calls the shared `_set_last_node` writer. A bounded timeout stops the robot and fails the action. The WebUI exposes it as a confirm-gated button in the existing Goto group.

**Tech Stack:** Python 3.11+ (asyncio), VDA5050 instant actions, stdlib `unittest`/`pytest`, server-rendered HTML (no JS framework).

## Global Constraints

- Run tests from the `adaptor/` directory using the project venv (`.venv`, Python ≥3.11): `python -m pytest <path> -v`. Working dir for all commands: `/home/lab2m-llm1/workspaces/unified-amr-adaptor/adaptor`.
- Arrival/lastNodeId threshold is the existing `_idle_last_node_reach_xy()` (live `config.toml` value is `idle_last_node_reach_xy = 100.0`), NOT the 200mm order reach zone.
- `lastNodeId` is written ONLY through the shared writer `_set_last_node(node_id, sequence_id)`.
- The action moves the robot → the WebUI button requires a confirm keystroke, implemented as a dedicated gate in `_post_action` (the generic `/action` path stays single-click for all other actions; do not regress `test_action_motion_fires_without_confirm`).
- No gating on `manual_control.enabled` (this is a general vehicle command, like the existing Goto form).
- Commit after each task.

---

## File Structure

- `adaptor/config/config.py` — add `goto_nearest_timeout_sec` field to `Settings` dataclass.
- `adaptor/config/config.toml` — document the new setting in `[settings]`.
- `adaptor/adapter_jibot.py` — new handler `_handle_goto_nearest_node_instant_action`, helper `_goto_nearest_timeout_sec`, one dispatch branch.
- `adaptor/core/factsheet.py` — add `"gotoNearestNode"` to `INSTANT_ACTION_TYPES`.
- `adaptor/core/registry.py` — add `InstantAction("gotoNearestNode", …, motion=True)`.
- `adaptor/web/server.py` — add a confirm gate for `gotoNearestNode` in `_post_action`.
- `adaptor/web/render.py` — new `_goto_nearest_form`, render it in the Goto group, exclude `gotoNearestNode` from the generic Vehicle-actions list.
- `adaptor/tests/test_goto_nearest_node.py` — new adapter handler tests (created in Task 1).
- `adaptor/tests/test_registry.py`, `adaptor/tests/test_factsheet_config.py` — add registration assertions (Task 2).
- `adaptor/tests/test_web_server.py` — add confirm-gate tests (Task 3).

---

### Task 1: Adapter handler, config, and dispatch

**Files:**
- Modify: `adaptor/config/config.py` (Settings dataclass, after `node_unreached_delay_sec` ~line 133)
- Modify: `adaptor/config/config.toml` (`[settings]`, after the `node_unreached_delay_sec` line)
- Modify: `adaptor/adapter_jibot.py` (dispatch chain ~line 3979; new helper near `_idle_last_node_reach_xy` ~6444; new handler near `_handle_disable_motor_instant_action` ~4323)
- Test: `adaptor/tests/test_goto_nearest_node.py` (create)

**Interfaces:**
- Consumes (existing, verified signatures):
  - `self._find_nearest_node(x: float, y: float) -> Optional[Tuple[str, int, float]]` → `(node_id, sequence_id, distance)`
  - `self._idle_last_node_reach_xy() -> float`
  - `self._distance_to_node_id(node_id: str, position: Any) -> Optional[float]` (`position` needs `.x`/`.y`)
  - `self._set_last_node(node_id: str, sequence_id: int) -> None`
  - `self._map_nodes() -> Dict[str, Tuple[float, float, float]]`
  - `self._is_simulator() -> bool`
  - `self._optional_float(value) -> Optional[float]`
  - `self._update_instant_action_status(action_id, ActionStatus, result_description=None)`
  - `self._run_on_adapter_loop(coro_factory)`, `self.request_state_publish(reason)`
  - `self.order_worker_task`, `self._vehicle._x/_y`, `self._vehicle.goto_point(node_id)`, `self._vehicle.goto_xyz(x,y,theta)`, `self._vehicle.um_stop()`
  - `self.config.settings.goto_nearest_timeout_sec`, `self.config.settings.node_position_poll_interval_sec`
- Produces (for later tasks): instant action type string `"gotoNearestNode"`; handler `_handle_goto_nearest_node_instant_action(action)`.

- [ ] **Step 1: Write the failing tests**

Create `adaptor/tests/test_goto_nearest_node.py`:

```python
import asyncio

from protocol.vda5050_3_0.messages import InstantActions
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from tests._stop_reason_harness import make_adapter, start_state


def _goto_nearest_ia(action_id):
    return InstantActions.from_dict({
        "headerId": 1,
        "timestamp": "2026-06-26T00:00:00.000Z",
        "version": "3.0.0",
        "manufacturer": "jibot",
        "serialNumber": "HN-TEST-001",
        "actions": [{
            "actionType": "gotoNearestNode",
            "actionId": action_id,
            "blockingType": "NONE",
            "actionParameters": [],
        }],
    })


def _status(adapter, action_id):
    for s in adapter.state.instant_action_states:
        if s.action_id == action_id:
            return s.action_status
    return None


def test_goto_then_arrival_sets_last_node():
    async def scenario():
        adapter = make_adapter()
        adapter.config.settings.node_position_poll_interval_sec = 0.01
        adapter.config.settings.goto_nearest_timeout_sec = 2.0
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            issued = list(adapter._vehicle.goto_targets)
            adapter._vehicle.arrive_at(1000.0, 0.0)
            await asyncio.sleep(0.1)
            return issued, adapter.state.last_node_id, _status(adapter, "g1")
        finally:
            task.cancel()
    issued, last_node, status = asyncio.run(scenario())
    assert issued == ["N3"]
    assert last_node == "N3"
    assert status == ActionStatus.FINISHED


def test_already_at_node_skips_motion():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {"N3": (50.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return list(adapter._vehicle.goto_targets), adapter.state.last_node_id, _status(adapter, "g1")
        finally:
            task.cancel()
    issued, last_node, status = asyncio.run(scenario())
    assert issued == []
    assert last_node == "N3"
    assert status == ActionStatus.FINISHED


def test_timeout_stops_robot_and_fails():
    async def scenario():
        adapter = make_adapter()
        adapter.config.settings.node_position_poll_interval_sec = 0.01
        adapter.config.settings.goto_nearest_timeout_sec = 0.1
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.3)
            return adapter._vehicle.um_stop_calls, adapter.state.last_node_id, _status(adapter, "g1")
        finally:
            task.cancel()
    stops, last_node, status = asyncio.run(scenario())
    assert stops >= 1
    assert last_node == ""
    assert status == ActionStatus.FAILED


def test_not_localized_fails():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = None
        adapter._vehicle._y = None
        task = await start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return list(adapter._vehicle.goto_targets), _status(adapter, "g1")
        finally:
            task.cancel()
    issued, status = asyncio.run(scenario())
    assert issued == []
    assert status == ActionStatus.FAILED


def test_active_order_fails():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {"N3": (1000.0, 0.0, 0.0)}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        blocker = asyncio.create_task(asyncio.sleep(5))
        adapter.order_worker_task = blocker
        task = await start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return list(adapter._vehicle.goto_targets), _status(adapter, "g1")
        finally:
            blocker.cancel()
            task.cancel()
    issued, status = asyncio.run(scenario())
    assert issued == []
    assert status == ActionStatus.FAILED


def test_no_nodes_fails():
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._map_nodes = {}
        adapter._vehicle._x = 0.0
        adapter._vehicle._y = 0.0
        task = await start_state(adapter)
        try:
            adapter.instant_actions_accept_procedure(_goto_nearest_ia("g1"))
            await asyncio.sleep(0.05)
            return _status(adapter, "g1")
        finally:
            task.cancel()
    status = asyncio.run(scenario())
    assert status == ActionStatus.FAILED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_goto_nearest_node.py -v`
Expected: FAIL — the dispatch chain has no `gotoNearestNode` branch, so the action is marked FAILED "Unsupported instant action" (the arrival/already-at tests fail their assertions; `goto_targets`/`last_node_id` are never set). `test_arrival...` also errors on `adapter.config.settings.goto_nearest_timeout_sec` (AttributeError) until Step 3.

- [ ] **Step 3: Add the config field**

In `adaptor/config/config.py`, in the `Settings` dataclass, immediately after the `node_unreached_delay_sec: float = 1.0` line, add:

```python
    # gotoNearestNode arrival-polling timeout (seconds). When the robot cannot
    # reach its nearest node within this window the action stops the robot and
    # fails. <=0 falls back to 60s (no unbounded RUNNING).
    goto_nearest_timeout_sec: float = 60.0
```

In `adaptor/config/config.toml`, in the `[settings]` section, immediately after the `node_unreached_delay_sec ...` line, add:

```toml
goto_nearest_timeout_sec        = 60.0  # gotoNearestNode arrival-polling timeout (seconds); <=0 falls back to 60s
```

- [ ] **Step 4: Add the timeout helper**

In `adaptor/adapter_jibot.py`, immediately after the `_idle_last_node_reach_xy` method (ends ~line 6444), add:

```python
    def _goto_nearest_timeout_sec(self) -> float:
        value = float(
            getattr(self.config.settings, "goto_nearest_timeout_sec", 60.0) or 0.0
        )
        return value if value > 0.0 else 60.0
```

- [ ] **Step 5: Add the handler**

In `adaptor/adapter_jibot.py`, immediately after `_handle_disable_motor_instant_action` (ends ~line 4323), add:

```python
    def _handle_goto_nearest_node_instant_action(self, action: Any) -> None:
        """Drive to the nearest map node and set lastNodeId on arrival.

        Resolves the nearest node from the live pose, issues an UmGoto, then
        polls the pose until it is within the lastNodeId capture threshold
        (_idle_last_node_reach_xy). On arrival the shared _set_last_node writer
        records the node. A bounded timeout stops the robot and fails the action
        (so a rejected/blocked goto cannot hang in RUNNING, and a later proximity
        capture cannot be attributed to this attempt).
        """
        vx = self._optional_float(getattr(self._vehicle, "_x", None))
        vy = self._optional_float(getattr(self._vehicle, "_y", None))
        if vx is None or vy is None:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="not localized",
            )
            return
        if self.order_worker_task is not None and not self.order_worker_task.done():
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="order in progress; cancel order first",
            )
            return
        nearest = self._find_nearest_node(vx, vy)
        if nearest is None:
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                result_description="no nearest node",
            )
            return
        node_id, sequence_id, distance = nearest
        threshold = self._idle_last_node_reach_xy()
        if distance <= threshold:
            self._set_last_node(node_id, sequence_id)
            self.request_state_publish("goto nearest: already at node")
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED,
                result_description=f"already at {node_id}",
            )
            return

        self._update_instant_action_status(action.action_id, ActionStatus.RUNNING)
        timeout = self._goto_nearest_timeout_sec()
        poll = float(
            getattr(self.config.settings, "node_position_poll_interval_sec", 0.2)
        )
        map_pos = self._map_nodes().get(node_id)

        async def _run() -> None:
            from types import SimpleNamespace
            try:
                if self._is_simulator() and map_pos is not None:
                    await self._vehicle.goto_xyz(
                        float(map_pos[0]), float(map_pos[1]), float(map_pos[2])
                    )
                else:
                    await self._vehicle.goto_point(node_id)

                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    cx = self._optional_float(getattr(self._vehicle, "_x", None))
                    cy = self._optional_float(getattr(self._vehicle, "_y", None))
                    if cx is not None and cy is not None:
                        d = self._distance_to_node_id(
                            node_id, SimpleNamespace(x=cx, y=cy)
                        )
                        if d is not None and d <= threshold:
                            self._set_last_node(node_id, sequence_id)
                            self.request_state_publish("goto nearest: reached")
                            self._update_instant_action_status(
                                action.action_id, ActionStatus.FINISHED,
                                result_description=f"reached {node_id}",
                            )
                            return
                    await asyncio.sleep(poll)

                await self._vehicle.um_stop()
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"timeout reaching {node_id}",
                )
            except Exception as exc:
                self._update_instant_action_status(
                    action.action_id, ActionStatus.FAILED,
                    result_description=f"gotoNearestNode failed: {exc}",
                )

        self._run_on_adapter_loop(_run)
```

- [ ] **Step 6: Wire the dispatch branch**

In `adaptor/adapter_jibot.py`, in `instant_actions_accept_procedure`, immediately after the `disableMotor` branch (lines 3978-3979):

```python
            elif action.action_type == "disableMotor":
                self._handle_disable_motor_instant_action(action)
```

insert:

```python
            elif action.action_type == "gotoNearestNode":
                self._handle_goto_nearest_node_instant_action(action)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_goto_nearest_node.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 8: Run the broader adapter suite for regressions**

Run: `python -m pytest tests/test_adapter_jibot_v3_order.py tests/test_disable_motor_action.py -q`
Expected: PASS (no regressions in order/instant-action paths).

- [ ] **Step 9: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/adapter_jibot.py adaptor/tests/test_goto_nearest_node.py
git commit -m "feat: gotoNearestNode instant action drives to nearest node, sets lastNodeId"
```

---

### Task 2: Advertise the action (factsheet + registry)

**Files:**
- Modify: `adaptor/core/factsheet.py` (`INSTANT_ACTION_TYPES` tuple, ~line 57, before the closing `)`)
- Modify: `adaptor/core/registry.py` (`_JIBOT_INSTANT_ACTIONS` tuple, ~line 228, before the closing `)`)
- Test: `adaptor/tests/test_registry.py`, `adaptor/tests/test_factsheet_config.py`

**Interfaces:**
- Consumes: action type `"gotoNearestNode"` (Task 1).
- Produces: `gotoNearestNode` present in `INSTANT_ACTION_TYPES` and as an `InstantAction(..., motion=True)` in `_JIBOT_INSTANT_ACTIONS` so the WebUI `_post_action` accepts it and the factsheet advertises it.

- [ ] **Step 1: Write the failing tests**

Add to `adaptor/tests/test_registry.py` (inside the existing test class that holds `test_jibot_instant_actions_include_manual_control`, matching its style):

```python
    def test_jibot_instant_actions_include_goto_nearest(self):
        from core.registry import _JIBOT_INSTANT_ACTIONS
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        assert "gotoNearestNode" in by_type
        assert by_type["gotoNearestNode"].motion is True
```

Add to `adaptor/tests/test_factsheet_config.py` (inside `class TestFactsheet`):

```python
    def test_goto_nearest_node_is_advertised(self):
        from core.factsheet import INSTANT_ACTION_TYPES
        assert "gotoNearestNode" in INSTANT_ACTION_TYPES
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_registry.py::test_jibot_instant_actions_include_goto_nearest tests/test_factsheet_config.py::TestFactsheet::test_goto_nearest_node_is_advertised -v`
Expected: FAIL — `gotoNearestNode` is not in either collection yet.

- [ ] **Step 3: Add to the factsheet catalog**

In `adaptor/core/factsheet.py`, in the `INSTANT_ACTION_TYPES` tuple, after the `"disableMotor",` line, add:

```python
    "gotoNearestNode",
```

- [ ] **Step 4: Add to the registry**

In `adaptor/core/registry.py`, in the `_JIBOT_INSTANT_ACTIONS` tuple, after the `InstantAction("disableMotor", "Disable motor (test)", motion=True)` line (before the closing `)`), add:

```python
    # Drive to the nearest map node and set lastNodeId (off-graph re-anchor).
    # motion=True: the robot moves, so the WebUI requires a confirm keystroke
    # (enforced by a dedicated gate in _post_action).
    InstantAction("gotoNearestNode", "Go to nearest node (set lastNodeId)", motion=True),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_registry.py tests/test_factsheet_config.py -v`
Expected: PASS (new tests pass; `test_full_factsheet_matches_expected_structure` still passes because its expected `agvActions` is derived from `INSTANT_ACTION_TYPES`).

- [ ] **Step 6: Commit**

```bash
git add adaptor/core/factsheet.py adaptor/core/registry.py adaptor/tests/test_registry.py adaptor/tests/test_factsheet_config.py
git commit -m "feat: advertise gotoNearestNode in factsheet + registry"
```

---

### Task 3: WebUI button + confirm gate

**Files:**
- Modify: `adaptor/web/server.py` (module-level constant + `_post_action`, ~line 671)
- Modify: `adaptor/web/render.py` (new `_goto_nearest_form`; Goto group render ~line 1343; generic Vehicle-actions exclusion ~line 1325)
- Test: `adaptor/tests/test_web_server.py`

**Interfaces:**
- Consumes: registered `gotoNearestNode` action (Task 2); existing `_command_form`, `_confirmed`, `_runs_urobot`, `_goto_form`.
- Produces: confirm-gated `gotoNearestNode` POST handling; a "Go to nearest node" button in the Goto group; exclusion from the generic Vehicle-actions list.

- [ ] **Step 1: Write the failing tests**

Add to `adaptor/tests/test_web_server.py` (module-level functions, mirroring `test_action_motion_fires_without_confirm`):

```python
def test_goto_nearest_requires_confirm():
    spec = FakeSpec("jibot", "JIBOT")
    spec.instant_actions = (
        type("A", (), {"action_type": "gotoNearestNode",
                       "label": "Go to nearest node", "motion": True})(),
    )
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    mon = FakeMonitor()
    web = WebUi(specs=[spec], controllers={"jibot": FakeController()},
                monitors={"jibot": mon}, credentials=cred, host="127.0.0.1", port=0)
    web.start()
    try:
        # No confirm -> rejected, nothing published.
        _post(web, "/adapter/jibot/action",
              {"csrf_token": web._csrf, "action_type": "gotoNearestNode"})
        assert len(mon.published) == 0
        # With confirm -> published once.
        _post(web, "/adapter/jibot/action",
              {"csrf_token": web._csrf, "action_type": "gotoNearestNode", "confirm": "on"})
        assert len(mon.published) == 1
    finally:
        web.stop()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_web_server.py::test_goto_nearest_requires_confirm -v`
Expected: FAIL — without the gate, the no-confirm POST publishes, so `len(mon.published) == 0` fails (it is 1).

- [ ] **Step 3: Add the confirm gate**

In `adaptor/web/server.py`, add a module-level constant near the top of the file (after the imports / `_confirmed` helper, ~line 41):

```python
# Actions that move the robot from the generic /action path and therefore
# require an explicit confirm keystroke (the rest of /action stays single-click).
_CONFIRM_REQUIRED_ACTIONS = ("gotoNearestNode",)
```

In `_post_action`, immediately after the `if action is None:` block (after its `return`, ~line 682, before the `# motion vehicle actions fire without a confirm gate` comment), insert:

```python
        if action_type in _CONFIRM_REQUIRED_ACTIONS and not _confirmed(form):
            self._audit(h, key, f"action:{action_type}", "rejected:unconfirmed")
            h._redirect(f"{target}?err={urllib.parse.quote('confirm required')}")
            return
```

- [ ] **Step 4: Run the gate test to verify it passes**

Run: `python -m pytest tests/test_web_server.py::test_goto_nearest_requires_confirm -v`
Expected: PASS.

- [ ] **Step 5: Verify the existing no-confirm behavior is intact**

Run: `python -m pytest tests/test_web_server.py::test_action_motion_fires_without_confirm -v`
Expected: PASS (startPause is not in `_CONFIRM_REQUIRED_ACTIONS`, so it still fires single-click).

- [ ] **Step 6: Add the button form**

In `adaptor/web/render.py`, immediately after the `_goto_form` function (ends ~line 1151), add:

```python
def _goto_nearest_form(spec_key: str, csrf: str, return_to: str = "") -> str:
    """Button: drive to the nearest map node and set lastNodeId on arrival.

    Posts actionType=gotoNearestNode to /action; the adapter resolves the
    nearest node from the live pose (no node id is sent). Requires confirm
    (the robot moves); the server enforces the confirm via _CONFIRM_REQUIRED_ACTIONS.
    """
    return _command_form(
        f"/adapter/{esc(spec_key)}/action", csrf, return_to,
        '<input type="hidden" name="action_type" value="gotoNearestNode">',
        title="가장 가까운 노드로 이동",
        desc="현재 위치에서 가장 가까운 노드로 이동 후 lastNodeId 설정 — 로봇이 실제로 움직임",
        label="Go to nearest node", variant="primary", confirm=True,
    )
```

- [ ] **Step 7: Render the button in the Goto group**

In `adaptor/web/render.py`, replace the Goto-group append (~line 1343):

```python
    if _runs_urobot(spec):
        groups.append(_command_group("Goto (UmGoto)", _goto_form(spec.key, csrf, return_to=return_to)))
```

with:

```python
    if _runs_urobot(spec):
        goto_html = (
            _goto_form(spec.key, csrf, return_to=return_to)
            + _goto_nearest_form(spec.key, csrf, return_to=return_to)
        )
        groups.append(_command_group("Goto (UmGoto)", goto_html))
```

- [ ] **Step 8: Exclude it from the generic Vehicle-actions list**

In `adaptor/web/render.py`, in the `actions = "".join(...)` comprehension (~line 1325-1331), add the `gotoNearestNode` exclusion. Replace:

```python
        and a.action_type != "jibotMotionRule"  # dedicated to/from form below
    )
```

with:

```python
        and a.action_type != "jibotMotionRule"  # dedicated to/from form below
        and a.action_type != "gotoNearestNode"  # dedicated button in the Goto group
    )
```

- [ ] **Step 9: Run the web suite for regressions**

Run: `python -m pytest tests/test_web_server.py -q`
Expected: PASS (all web tests, including the new gate test and the unchanged no-confirm test).

- [ ] **Step 10: Commit**

```bash
git add adaptor/web/server.py adaptor/web/render.py adaptor/tests/test_web_server.py
git commit -m "feat: WebUI gotoNearestNode button with confirm gate"
```

---

## Self-Review

**Spec coverage:**
- Decision #1 (action only, no map viz) → entire plan; no canvas work. ✓
- #2 (UmGoto via node id) → Task 1 Step 5 `goto_point`/`goto_xyz`. ✓
- #3 (adapter resolves nearest from live pose) → Task 1 Step 5 `_find_nearest_node(vx, vy)`; WebUI sends no node id (Task 3 form). ✓
- #4 (arrival threshold = `_idle_last_node_reach_xy()` 100mm) → Task 1 Step 5 `threshold`; Global Constraints; test `test_already_at_node_skips_motion` (50mm < 100mm). ✓
- #5 (bounded poll + `goto_nearest_timeout_sec`) → Task 1 Steps 3-5; `test_timeout_stops_robot_and_fails`. ✓
- #6 (`_set_last_node` on arrival + `request_state_publish`) → Task 1 Step 5. ✓
- #7 (stop on timeout) → Task 1 Step 5 `um_stop()`; `test_timeout_stops_robot_and_fails` asserts `um_stop_calls >= 1` and `last_node == ""`. ✓
- #8 (reject only when order worker active) → Task 1 Step 5 guard; `test_active_order_fails`. ✓
- #9 (Goto-group button, confirm gate) → Task 3. ✓
- "no `manual_control.enabled` gate" → handler omits it; Global Constraints. ✓
- Registration in `core/factsheet.py` (not adapter) → Task 2. ✓

**Deviations from spec (intentional simplifications, still spec-compliant):**
- The optional early `_await_goto_ack` rejection check is **dropped**: a rejected/blocked goto simply never reaches the threshold and is caught by the timeout+stop path. The spec marked this check "(선택)" / optional with the timeout as the safety net, so this is in-scope. Trade-off: a rejected goto surfaces as FAILED after `goto_nearest_timeout_sec` rather than immediately. Noted as a possible future fast-reject enhancement.
- The separate "no map nodes" guard is folded into the `nearest is None → FAILED` check (`_find_nearest_node` returns None when there are no map/order candidates), covered by `test_no_nodes_fails`.
- The live nearest-node label on the button (`→ N3 (1.2 m)`) is **not** implemented; the button uses a generic label. The spec explicitly allows the generic fallback ("텔레메트리 없으면 제네릭 라벨로 폴백"). Surfacing the live `NEAREST_NODE` telemetry into `render.py` is a separate enhancement (the adapter still resolves the true nearest node at click time, so behavior is unchanged).

**Placeholder scan:** No TBD/TODO; every code step contains full code; every run step has an exact command and expected result. ✓

**Type consistency:** `_handle_goto_nearest_node_instant_action`, `_goto_nearest_timeout_sec`, `_goto_nearest_form`, `_CONFIRM_REQUIRED_ACTIONS`, and action string `"gotoNearestNode"` are spelled identically across all tasks and tests. Handler consumes the verified existing signatures listed in the Task 1 Interfaces block. ✓
