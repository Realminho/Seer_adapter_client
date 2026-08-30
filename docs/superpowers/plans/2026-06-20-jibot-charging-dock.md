# JIBOT Charging Dock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JIBOT charge route execution with `UmDock`-based charge and dock-work node handling.

**Architecture:** Keep FMS-visible node IDs unchanged. The JIBOT order worker classifies node IDs into normal, charge, or dock-work destinations; charge and dock-work destinations send `UmDock` instead of `goto_point` or `call_routes`, then complete from robot charging state rather than station/coordinate arrival. Dock-work nodes call `UmStop` after charging is detected so the robot stays physically docked without continuing to charge.

**Tech Stack:** Python 3.12, `unittest`, existing VDA5050 adapter classes, JIBOT client/simulator command methods.

---

### Task 1: Configuration Model

**Files:**
- Modify: `adaptor/config/config.py`
- Modify: `adaptor/config/config.toml`
- Modify: `adaptor/config/config_bak.toml`

- [ ] **Step 1: Add a failing config test implicitly through import coverage**

Run: `python -m py_compile adaptor/config/config.py`

Expected before implementation if `[dock]` is consumed without a dataclass: FAIL with a missing name. If this still passes before the code change, continue because the next order tests will cover behavior.

- [ ] **Step 2: Add `DockConfig`**

Add this dataclass after `ChargeConfig`:

```python
@dataclass
class DockConfig:
    nodes: List[str] = field(default_factory=list)
    stop_charging_on_arrival: bool = True
```

Add `dock: DockConfig` to `Config`.

- [ ] **Step 3: Load optional `[dock]` config**

In `get_config()`, add:

```python
dock_config = DockConfig(**config_dict.get("dock", {}))
```

and pass `dock=dock_config` to `Config(...)`.

- [ ] **Step 4: Update config files**

Keep `[charge].routes` for legacy compatibility, but add:

```toml
[dock]
nodes = []
stop_charging_on_arrival = true
```

- [ ] **Step 5: Verify**

Run: `python -m py_compile adaptor/config/config.py`

Expected: PASS.

### Task 2: Charge Node Uses UmDock

**Files:**
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py`
- Modify: `adaptor/adapter_jibot.py`

- [ ] **Step 1: Write failing test**

Add a fake vehicle `route_calls` list and this test:

```python
def test_charge_node_motion_uses_um_dock_not_route_or_goto(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        vehicle: FakeVehicle = adapter._vehicle
        adapter._loop = asyncio.get_running_loop()
        adapter.config.charge.nodes = ["CHARGE_C"]
        adapter.config.charge.routes = ["LEGACY_ROUTE"]

        node = Node.from_dict({
            "nodeId": "CHARGE_C",
            "sequenceId": 2,
            "released": True,
            "nodePosition": {"x": 100.0, "y": 200.0, "theta": 0.0, "mapId": "lab2m"},
            "actions": [],
        })

        async def finish_docking() -> None:
            await asyncio.sleep(0.05)
            vehicle._charging = True
            vehicle._status = adapter.config.jibot_status.charging

        asyncio.create_task(finish_docking())
        ok = await adapter._process_v3_node_step(OrderStep("node", node.sequence_id, node))

        self.assertTrue(ok)
        self.assertEqual(vehicle.dock_calls, 1)
        self.assertEqual(vehicle.goto_targets, [])
        self.assertEqual(vehicle.route_calls, [])
        self.assertEqual(adapter.state.last_node_id, "CHARGE_C")

    asyncio.run(scenario())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest adaptor.tests.test_adapter_jibot_v3_order.TestAdapterJibotV3Order.test_charge_node_motion_uses_um_dock_not_route_or_goto -v`

Expected: FAIL because charge nodes still call `call_routes` or wait by coordinate.

- [ ] **Step 3: Implement minimal charge dock path**

Add helpers in `Adapter`:

```python
def _is_charge_node(self, node_id: str) -> bool:
    return node_id in set(getattr(self.config.charge, "nodes", []) or [])

def _is_dock_work_node(self, node_id: str) -> bool:
    return node_id in set(getattr(self.config.dock, "nodes", []) or [])

def _is_dock_motion_node(self, node_id: str) -> bool:
    return self._is_charge_node(node_id) or self._is_dock_work_node(node_id)
```

Change `_send_node_motion()` so dock motion nodes call `await self._vehicle.um_dock()` and never call route.

Add `_wait_until_docking_complete()` that loops until `_charging` is true or `_status` equals `config.jibot_status.charging`.

Change `_process_v3_node_step()` to use `_wait_until_docking_complete()` for dock motion nodes and skip coordinate/station arrival checks for them.

- [ ] **Step 4: Run test to verify it passes**

Run the same unittest command.

Expected: PASS.

### Task 3: Dock-Work Node Stops Charging

**Files:**
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py`
- Modify: `adaptor/adapter_jibot.py`

- [ ] **Step 1: Write failing test**

Extend `FakeVehicle` with:

```python
self.route_calls = []
self.stop_charge_calls = 0

async def call_routes(self, name, key, id=None):
    self.route_calls.append((name, key, id))

async def um_stop(self, gap: int = -1) -> None:
    self.stop_charge_calls += 1
    self._charging = False
    self._status = "Stopped"
```

Add:

```python
def test_dock_work_node_stops_charging_after_docking(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        vehicle: FakeVehicle = adapter._vehicle
        adapter._loop = asyncio.get_running_loop()
        adapter.config.dock.nodes = ["CV_DOCK_C"]
        adapter.config.dock.stop_charging_on_arrival = True

        node = Node.from_dict({
            "nodeId": "CV_DOCK_C",
            "sequenceId": 2,
            "released": True,
            "nodePosition": {"x": 100.0, "y": 200.0, "theta": 0.0, "mapId": "lab2m"},
            "actions": [],
        })

        async def finish_docking() -> None:
            await asyncio.sleep(0.05)
            vehicle._charging = True
            vehicle._status = adapter.config.jibot_status.charging

        asyncio.create_task(finish_docking())
        ok = await adapter._process_v3_node_step(OrderStep("node", node.sequence_id, node))

        self.assertTrue(ok)
        self.assertEqual(vehicle.dock_calls, 1)
        self.assertEqual(vehicle.stop_charge_calls, 1)
        self.assertFalse(vehicle._charging)
        self.assertEqual(adapter.state.last_node_id, "CV_DOCK_C")

    asyncio.run(scenario())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest adaptor.tests.test_adapter_jibot_v3_order.TestAdapterJibotV3Order.test_dock_work_node_stops_charging_after_docking -v`

Expected: FAIL because dock-work nodes are not classified and `UmStop` is not called.

- [ ] **Step 3: Implement stop after dock-work docking**

After `_wait_until_docking_complete()` returns in `_process_v3_node_step()`, if `_is_dock_work_node(node.node_id)` and `config.dock.stop_charging_on_arrival` is true, call a helper:

```python
async def _stop_charging_after_dock_work(self, node_id: str) -> None:
    stop = getattr(self._vehicle, "um_stop", None)
    if not callable(stop):
        raise RuntimeError("JIBOT API does not provide um_stop for dock-work charging stop")
    await stop()
```

- [ ] **Step 4: Run test to verify it passes**

Run the same unittest command.

Expected: PASS.

### Task 4: Avoid Duplicate startCharging Dock

**Files:**
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py`
- Modify: `adaptor/adapter_jibot.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_start_charging_action_does_not_redock_when_node_motion_already_docked(self) -> None:
    async def scenario() -> None:
        adapter = self._make_adapter()
        vehicle: FakeVehicle = adapter._vehicle
        adapter._loop = asyncio.get_running_loop()
        adapter._docking_started_node_ids.add("CHARGE_C")

        action = Order.from_dict({
            "headerId": 1,
            "timestamp": "2026-06-10T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-SH6-TR-001",
            "orderId": "order-charge",
            "orderUpdateId": 0,
            "nodes": [{
                "nodeId": "CHARGE_C",
                "sequenceId": 2,
                "released": True,
                "actions": [{
                    "actionType": "startCharging",
                    "actionId": "a-charge",
                    "blockingType": "SOFT",
                    "actionParameters": [],
                }],
            }],
            "edges": [],
        }).nodes[0].actions[0]
        action_state = ActionState("a-charge", ActionStatus.WAITING, "startCharging")

        await adapter._dock_for_order_action(action, action_state, node_id="CHARGE_C")

        self.assertEqual(vehicle.dock_calls, 0)
        self.assertEqual(action_state.action_status, ActionStatus.FINISHED)
        self.assertIn("already", action_state.result_description)

    asyncio.run(scenario())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest adaptor.tests.test_adapter_jibot_v3_order.TestAdapterJibotV3Order.test_start_charging_action_does_not_redock_when_node_motion_already_docked -v`

Expected: FAIL because `_dock_for_order_action` has no node context and always calls `um_dock`.

- [ ] **Step 3: Implement duplicate guard**

Track `self._docking_started_node_ids: Set[str] = set()` in `Adapter.__init__`. Add node context when executing node actions by passing the step owner ID to `_execute_order_action()` and `_dock_for_order_action()`. If `node_id` is in the set, finish the action without another `UmDock`.

- [ ] **Step 4: Run test to verify it passes**

Run the same unittest command.

Expected: PASS.

### Task 5: Regression Suite

**Files:**
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`
- Test: `adaptor/tests/test_simulator_charging.py`

- [ ] **Step 1: Run focused adapter tests**

Run: `python -m unittest adaptor.tests.test_adapter_jibot_v3_order -v`

Expected: PASS.

- [ ] **Step 2: Run simulator charging tests**

Run: `python -m unittest adaptor.tests.test_simulator_charging -v`

Expected: PASS.

- [ ] **Step 3: Compile changed modules**

Run: `python -m py_compile adaptor/adapter_jibot.py adaptor/config/config.py adaptor/tests/test_adapter_jibot_v3_order.py`

Expected: PASS.
