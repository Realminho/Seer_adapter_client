# JIBOT In-Place Charge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start charging without the ~1 m `UmDock` back-up when the robot is already at the charge node, by holding the ROS `/jcmd cmd:3` charge relay, while leaving the existing move-and-dock charging unchanged.

**Architecture:** A small injectable `ChargeCircuit` component encapsulates "hold the charge relay closed" (production = a managed `rostopic pub -r` subprocess; tests = a fake; simulator/non-JIBOT = a no-op). The adapter gains a `chargeInPlace` instant action and auto-routes `startCharging` to in-place when the charge node equals the robot's current node (`_last_node_id`), otherwise keeps calling `um_dock()`.

**Tech Stack:** Python 3.8+ (asyncio), unittest, ROS1 Noetic `rostopic` CLI (`jarvis_msgs/Cmd`), urobot TCP 7273 (`UmGetRobotInfo.is_charged` for verification).

## Global Constraints

- The adapter core stays **ROS-free**: only `ChargeCircuit`'s production implementation may touch ROS, via `subprocess`. No `rospy` import anywhere.
- In-place charge is **opt-in**: `charge_circuit.enabled` defaults to `false`; when disabled (simulator, non-JIBOT hosts) the adapter uses a `NullChargeCircuit` no-op and all existing 7273-only flows are unchanged.
- Existing behaviour is preserved: move-to-charger + reflector dock (`um_dock()`), charge-stop via double `UmStop`, and `BatteryState.charging` reporting must not change.
- Charge relay wire format (verbatim): topic `/jcmd`, type `jarvis_msgs/Cmd`, message `{cmd: 3, data: {arg_int8: 1, arg_int32: 0, arg_str: ""}}` to close (charge ON), `arg_int8: 0` to open (charge OFF).
- Tests must run with no robot and no ROS (inject fakes); follow the existing `adaptor/tests/` unittest style (`python3 -m unittest tests.<module>` from `adaptor/`).
- Background reference: `docs/reference/jibot-charging-dock-bms.md`. Spec: `docs/superpowers/specs/2026-06-22-jibot-charge-in-place-design.md`.

---

### Task 1: `ChargeCircuit` interface + Null/Fake implementations

**Files:**
- Create: `adaptor/utils/charge_circuit.py`
- Test: `adaptor/tests/test_charge_circuit.py`

**Interfaces:**
- Produces:
  - `class ChargeCircuit` with methods `start_hold() -> None`, `stop_hold() -> None`, property `is_holding -> bool`.
  - `class NullChargeCircuit(ChargeCircuit)` — no-op (default when disabled).
  - `class FakeChargeCircuit(ChargeCircuit)` — records calls for tests: attrs `start_calls: int`, `stop_calls: int`, plus `is_holding`.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_charge_circuit.py
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from utils.charge_circuit import NullChargeCircuit, FakeChargeCircuit


class NullChargeCircuitTest(unittest.TestCase):
    def test_null_is_noop_but_tracks_state(self):
        cc = NullChargeCircuit()
        self.assertFalse(cc.is_holding)
        cc.start_hold()
        self.assertTrue(cc.is_holding)
        cc.stop_hold()
        self.assertFalse(cc.is_holding)


class FakeChargeCircuitTest(unittest.TestCase):
    def test_fake_records_calls(self):
        cc = FakeChargeCircuit()
        cc.start_hold()
        cc.start_hold()  # idempotent: still one hold
        self.assertTrue(cc.is_holding)
        self.assertEqual(cc.start_calls, 1)
        cc.stop_hold()
        self.assertFalse(cc.is_holding)
        self.assertEqual(cc.stop_calls, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python3 -m unittest tests.test_charge_circuit -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.charge_circuit'`.

- [ ] **Step 3: Write minimal implementation**

```python
# adaptor/utils/charge_circuit.py
"""Charge-relay control for JIBOT in-place charging.

In-place charge = hold the charge relay closed by repeatedly publishing
ROS /jcmd jarvis_msgs/Cmd{cmd:3, arg_int8:1} (CloseChargingCircuit); arg_int8:0
opens it. The relay only stays closed while the assertion is held, so the
production implementation keeps a `rostopic pub -r` subprocess alive.

The adapter core stays ROS-free: only SubprocessChargeCircuit touches ROS, and
only via subprocess. NullChargeCircuit is the no-op default (simulator / disabled).
"""


class ChargeCircuit:
    """Interface: hold/release the charge relay."""

    @property
    def is_holding(self) -> bool:
        raise NotImplementedError

    def start_hold(self) -> None:
        raise NotImplementedError

    def stop_hold(self) -> None:
        raise NotImplementedError


class NullChargeCircuit(ChargeCircuit):
    """No-op circuit used when in-place charge is disabled (simulator/non-JIBOT)."""

    def __init__(self) -> None:
        self._holding = False

    @property
    def is_holding(self) -> bool:
        return self._holding

    def start_hold(self) -> None:
        self._holding = True

    def stop_hold(self) -> None:
        self._holding = False


class FakeChargeCircuit(ChargeCircuit):
    """Test double that records calls."""

    def __init__(self) -> None:
        self._holding = False
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def is_holding(self) -> bool:
        return self._holding

    def start_hold(self) -> None:
        if self._holding:
            return
        self._holding = True
        self.start_calls += 1

    def stop_hold(self) -> None:
        if not self._holding:
            return
        self._holding = False
        self.stop_calls += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python3 -m unittest tests.test_charge_circuit -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/utils/charge_circuit.py adaptor/tests/test_charge_circuit.py
git commit -m "feat: add ChargeCircuit interface with Null/Fake impls"
```

---

### Task 2: `SubprocessChargeCircuit` production implementation

**Files:**
- Modify: `adaptor/utils/charge_circuit.py`
- Test: `adaptor/tests/test_charge_circuit.py`

**Interfaces:**
- Consumes: a `cfg` object with attributes `jcmd_topic: str`, `publish_rate_hz: float`, `ros_setup: str`, `ros_master_uri: str` (see Task 3 `ChargeCircuitConfig`).
- Produces: `class SubprocessChargeCircuit(ChargeCircuit)` with `__init__(self, cfg, spawn=None)`. `spawn(cmd: list[str]) -> handle` where `handle` has `.terminate()` and `.wait(timeout=...)`. Default `spawn` uses `subprocess.Popen`. Exposes `hold_command() -> list[str]` and `off_command() -> list[str]` (pure, testable).

- [ ] **Step 1: Write the failing test**

```python
# add to adaptor/tests/test_charge_circuit.py
from utils.charge_circuit import SubprocessChargeCircuit


class _Cfg:
    jcmd_topic = "/jcmd"
    publish_rate_hz = 2
    ros_setup = "/usr/local/urobot/jarvis/setup.bash"
    ros_master_uri = "http://localhost:11311"


class _FakeProc:
    def __init__(self):
        self.terminated = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


class SubprocessChargeCircuitTest(unittest.TestCase):
    def setUp(self):
        self.spawned = []

        def spawn(cmd):
            p = _FakeProc()
            self.spawned.append((cmd, p))
            return p

        self.spawn = spawn

    def test_hold_command_closes_relay(self):
        cc = SubprocessChargeCircuit(_Cfg(), spawn=self.spawn)
        cmd = cc.hold_command()
        joined = " ".join(cmd)
        self.assertIn("rostopic pub -r 2 /jcmd jarvis_msgs/Cmd", joined)
        self.assertIn("arg_int8: 1", joined)
        self.assertIn("source /usr/local/urobot/jarvis/setup.bash", joined)
        self.assertIn("ROS_MASTER_URI=http://localhost:11311", joined)

    def test_off_command_opens_relay_once(self):
        cc = SubprocessChargeCircuit(_Cfg(), spawn=self.spawn)
        joined = " ".join(cc.off_command())
        self.assertIn("rostopic pub -1 /jcmd jarvis_msgs/Cmd", joined)
        self.assertIn("arg_int8: 0", joined)

    def test_start_then_stop_lifecycle(self):
        cc = SubprocessChargeCircuit(_Cfg(), spawn=self.spawn)
        self.assertFalse(cc.is_holding)
        cc.start_hold()
        self.assertTrue(cc.is_holding)
        self.assertEqual(len(self.spawned), 1)  # the hold publisher
        cc.start_hold()  # idempotent: no second spawn
        self.assertEqual(len(self.spawned), 1)
        cc.stop_hold()
        self.assertFalse(cc.is_holding)
        hold_proc = self.spawned[0][1]
        self.assertTrue(hold_proc.terminated)       # hold killed
        self.assertEqual(len(self.spawned), 2)      # off publisher spawned
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python3 -m unittest tests.test_charge_circuit -v`
Expected: FAIL — `ImportError: cannot import name 'SubprocessChargeCircuit'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to adaptor/utils/charge_circuit.py (top: add imports)
import atexit
import subprocess

# jarvis_msgs/Cmd YAML for cmd:3 (charge relay). arg_int8 1=close(on), 0=open(off).
_CLOSE_MSG = '{cmd: 3, data: {arg_int8: 1, arg_int32: 0, arg_str: ""}}'
_OPEN_MSG = '{cmd: 3, data: {arg_int8: 0, arg_int32: 0, arg_str: ""}}'


def _default_spawn(cmd):
    return subprocess.Popen(cmd)


class SubprocessChargeCircuit(ChargeCircuit):
    """Holds the charge relay by keeping a `rostopic pub -r` subprocess alive."""

    def __init__(self, cfg, spawn=None):
        self._cfg = cfg
        self._spawn = spawn or _default_spawn
        self._proc = None
        # Safety net: kill an orphaned hold publisher on process exit so the
        # relay assertion stops (relay opens) however the adapter shuts down.
        atexit.register(self._terminate_on_exit)

    def _terminate_on_exit(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    @property
    def is_holding(self) -> bool:
        return self._proc is not None

    def _ros_prefix(self) -> str:
        return (
            f"source {self._cfg.ros_setup}; "
            f"export ROS_MASTER_URI={self._cfg.ros_master_uri}; "
        )

    def hold_command(self):
        inner = (
            f"exec rostopic pub -r {self._cfg.publish_rate_hz} "
            f"{self._cfg.jcmd_topic} jarvis_msgs/Cmd '{_CLOSE_MSG}'"
        )
        return ["bash", "-lc", self._ros_prefix() + inner]

    def off_command(self):
        inner = (
            f"rostopic pub -1 {self._cfg.jcmd_topic} jarvis_msgs/Cmd '{_OPEN_MSG}'"
        )
        return ["bash", "-lc", self._ros_prefix() + inner]

    def start_hold(self) -> None:
        if self._proc is not None:
            return
        self._proc = self._spawn(self.hold_command())

    def stop_hold(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception as exc:
                print(f"[CHARGE CIRCUIT] terminate hold failed: {exc}")
        # Best-effort single open (relay OFF).
        try:
            off = self._spawn(self.off_command())
            off.wait(timeout=5)
        except Exception as exc:
            print(f"[CHARGE CIRCUIT] open-relay publish failed: {exc}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python3 -m unittest tests.test_charge_circuit -v`
Expected: PASS (5 tests total).

- [ ] **Step 5: Commit**

```bash
git add adaptor/utils/charge_circuit.py adaptor/tests/test_charge_circuit.py
git commit -m "feat: add SubprocessChargeCircuit holding /jcmd cmd:3"
```

---

### Task 3: `ChargeCircuitConfig` + config parsing

**Files:**
- Modify: `adaptor/config/config.py`
- Modify: `adaptor/config/config.toml`
- Test: `adaptor/tests/test_charge_circuit_config.py`

**Interfaces:**
- Produces: `@dataclass ChargeCircuitConfig` with fields `enabled: bool = False`, `jcmd_topic: str = "/jcmd"`, `publish_rate_hz: float = 2.0`, `ros_setup: str = "/usr/local/urobot/jarvis/setup.bash"`, `ros_master_uri: str = "http://localhost:11311"`, `verify_timeout_sec: float = 5.0`. Exposed on `Config` as `charge_circuit: ChargeCircuitConfig`.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_charge_circuit_config.py
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import get_config


class ChargeCircuitConfigTest(unittest.TestCase):
    def test_defaults_present_and_disabled(self):
        cfg = get_config().charge_circuit
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.jcmd_topic, "/jcmd")
        self.assertEqual(cfg.publish_rate_hz, 2.0)
        self.assertEqual(cfg.ros_master_uri, "http://localhost:11311")
        self.assertEqual(cfg.verify_timeout_sec, 5.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python3 -m unittest tests.test_charge_circuit_config -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'charge_circuit'`.

- [ ] **Step 3: Write minimal implementation**

In `adaptor/config/config.py`, add the dataclass after `DockConfig` (around line 94):

```python
@dataclass
class ChargeCircuitConfig:
    # In-place charge holds ROS /jcmd cmd:3 (CloseChargingCircuit). Opt-in: only
    # enable on real JIBOT hosts with the ROS env + rostopic available.
    enabled: bool = False
    jcmd_topic: str = "/jcmd"
    publish_rate_hz: float = 2.0
    ros_setup: str = "/usr/local/urobot/jarvis/setup.bash"
    ros_master_uri: str = "http://localhost:11311"
    verify_timeout_sec: float = 5.0
```

Add to the `Config` dataclass field list (after `dock: DockConfig`, ~line 153):

```python
    charge_circuit: ChargeCircuitConfig
```

In `get_config`, parse it (after `dock_config = ...`, ~line 209):

```python
    charge_circuit = ChargeCircuitConfig(**config_dict.get("charge_circuit", {}))
```

And pass it into the `Config(...)` constructor (after `dock=dock_config,`, ~line 224):

```python
        charge_circuit=charge_circuit,
```

In `adaptor/config/config.toml`, add a section (values are the defaults; keep `enabled = false`):

```toml
[charge_circuit]
enabled = false
jcmd_topic = "/jcmd"
publish_rate_hz = 2.0
ros_setup = "/usr/local/urobot/jarvis/setup.bash"
ros_master_uri = "http://localhost:11311"
verify_timeout_sec = 5.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python3 -m unittest tests.test_charge_circuit_config -v`
Expected: PASS.

Also run the existing config-dependent suite to confirm no regression:
Run: `cd adaptor && python3 -m unittest tests.test_simulator_charging -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/tests/test_charge_circuit_config.py
git commit -m "feat: add [charge_circuit] config section"
```

---

### Task 4: Inject `ChargeCircuit` into the Adapter

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`__init__` ~line 132; add setter near `set_vehicle` ~line 210)
- Modify: `adaptor/main.py` (adapter wiring ~line 698-716)
- Test: `adaptor/tests/test_charge_in_place.py`

**Interfaces:**
- Consumes: `NullChargeCircuit` (Task 1), `SubprocessChargeCircuit` (Task 2), `Config.charge_circuit` (Task 3).
- Produces: `Adapter._charge_circuit` (defaults to `NullChargeCircuit`), `Adapter.set_charge_circuit(cc: ChargeCircuit) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_charge_in_place.py
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapter_jibot import Adapter
from utils.charge_circuit import NullChargeCircuit, FakeChargeCircuit


class ChargeCircuitInjectionTest(unittest.TestCase):
    def test_defaults_to_null_circuit(self):
        adapter = Adapter(config=None)
        self.assertIsInstance(adapter._charge_circuit, NullChargeCircuit)

    def test_set_charge_circuit_replaces(self):
        adapter = Adapter(config=None)
        fake = FakeChargeCircuit()
        adapter.set_charge_circuit(fake)
        self.assertIs(adapter._charge_circuit, fake)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_charge_circuit'`.

- [ ] **Step 3: Write minimal implementation**

In `adaptor/adapter_jibot.py`, add the import near the other `utils` imports (~line 45):

```python
from utils.charge_circuit import ChargeCircuit, NullChargeCircuit
```

In `__init__`, right after `self._docking_started_node_ids: Set[str] = set()` (~line 132):

```python
        self._charge_circuit: ChargeCircuit = NullChargeCircuit()
        self._charge_in_place_active: bool = False
```

Add the setter next to `set_vehicle` (~line 210):

```python
    def set_charge_circuit(self, charge_circuit: ChargeCircuit) -> None:
        self._charge_circuit = charge_circuit
```

In `adaptor/main.py`, after `adapter.set_vehicle(vehicle)` (~line 701), wire the production circuit when enabled:

```python
        if config_data.charge_circuit.enabled:
            from utils.charge_circuit import SubprocessChargeCircuit
            adapter.set_charge_circuit(SubprocessChargeCircuit(config_data.charge_circuit))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/main.py adaptor/tests/test_charge_in_place.py
git commit -m "feat: inject ChargeCircuit into adapter (Null default + setter)"
```

---

### Task 5: `_run_charge_in_place` helper + `chargeInPlace` instant action

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`SUPPORTED_INSTANT_ACTIONS` ~line 1738; dispatch ~line 3130; new handler + helper near `_handle_start_charging_instant_action` ~line 4438)
- Test: `adaptor/tests/test_charge_in_place.py`

**Interfaces:**
- Consumes: `self._charge_circuit` (Task 4), `self._is_vehicle_charging()` (existing, ~line 2495), `self._update_instant_action_status(action_id, status, result_description=...)` (existing ~line 4679), `self._run_on_adapter_loop(coro_factory)` (existing ~line 242), `self.config.charge_circuit.verify_timeout_sec`.
- Produces:
  - `async def _run_charge_in_place(self) -> tuple[bool, str]` — `start_hold()`, poll `_is_vehicle_charging()` up to `verify_timeout_sec`; returns `(True, "charging")` on success, else `stop_hold()` and `(False, "charger not engaged")`.
  - `def _handle_charge_in_place_instant_action(self, action_id: str) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# add to adaptor/tests/test_charge_in_place.py
import asyncio
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


class _FakeVehicleCharging:
    def __init__(self, charging):
        self._charging = charging


def _make_instant(action_id, action_type):
    class A:
        pass
    a = A()
    a.action_id = action_id
    a.action_type = action_type
    a.action_parameters = []
    a.action_descriptor = ""
    return a


class ChargeInPlaceActionTest(unittest.TestCase):
    def _adapter(self, charging):
        adapter = Adapter(config=None)
        adapter.config.charge_circuit.verify_timeout_sec = 0.2
        adapter._vehicle = _FakeVehicleCharging(charging)
        adapter._loop = asyncio.get_event_loop()
        self.fake_cc = FakeChargeCircuit()
        adapter.set_charge_circuit(self.fake_cc)
        return adapter

    def test_charge_in_place_finishes_when_charging(self):
        async def scenario():
            adapter = self._adapter(charging=True)
            ok, _desc = await adapter._run_charge_in_place()
            self.assertTrue(ok)
            self.assertEqual(self.fake_cc.start_calls, 1)
            self.assertEqual(self.fake_cc.stop_calls, 0)  # left charging
        asyncio.run(scenario())

    def test_charge_in_place_fails_and_releases_when_not_charging(self):
        async def scenario():
            adapter = self._adapter(charging=False)
            ok, _desc = await adapter._run_charge_in_place()
            self.assertFalse(ok)
            self.assertEqual(self.fake_cc.start_calls, 1)
            self.assertEqual(self.fake_cc.stop_calls, 1)  # released on failure
        asyncio.run(scenario())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place.ChargeInPlaceActionTest -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_run_charge_in_place'`.

- [ ] **Step 3: Write minimal implementation**

In `adaptor/adapter_jibot.py`, add the helper and handler near `_handle_start_charging_instant_action` (~line 4438):

```python
    async def _run_charge_in_place(self) -> Tuple[bool, str]:
        """Hold the charge relay closed and confirm charging actually starts.

        Returns (ok, description). On failure the hold is released so the relay
        is not left asserted by a robot that never drew current.
        """
        self._charge_circuit.start_hold()
        self._charge_in_place_active = True
        timeout = float(self.config.charge_circuit.verify_timeout_sec)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_vehicle_charging():
                return True, "charging in place"
            await asyncio.sleep(0.2)
        if self._is_vehicle_charging():
            return True, "charging in place"
        self._charge_circuit.stop_hold()
        self._charge_in_place_active = False
        return False, "in-place charge: charger not engaged"

    def _handle_charge_in_place_instant_action(self, action_id: str) -> None:
        """VDA5050 chargeInPlace: close the charge relay without moving."""
        if self._vehicle is None:
            self._update_instant_action_status(
                action_id,
                ActionStatus.FAILED,
                result_description="JIBOT vehicle is not initialized",
            )
            return

        self._update_instant_action_status(action_id, ActionStatus.RUNNING)

        async def _charge() -> None:
            ok, desc = await self._run_charge_in_place()
            self._update_instant_action_status(
                action_id,
                ActionStatus.FINISHED if ok else ActionStatus.FAILED,
                result_description=desc,
            )
            print(f"[CHARGE IN PLACE] actionId={action_id} ok={ok} ({desc})")

        self._run_on_adapter_loop(_charge)
```

Register the action type in `SUPPORTED_INSTANT_ACTIONS` (~line 1738) — add `"chargeInPlace",` after `"startCharging",`:

```python
        "startCharging",
        "chargeInPlace",
```

Add a dispatch branch in `instant_actions_accept_procedure` after the `startCharging` branch (~line 3130):

```python
            elif action.action_type == "chargeInPlace":
                self._handle_charge_in_place_instant_action(action.action_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place.ChargeInPlaceActionTest -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_charge_in_place.py
git commit -m "feat: add chargeInPlace instant action + verify helper"
```

---

### Task 6: Auto-route `startCharging` to in-place when at the charge node

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`_should_charge_in_place` new helper near `_is_dock_motion_node` ~line 2718; hook `_handle_start_charging_instant_action` ~line 4438, `_dock_for_order_action` ~line 2666, `_send_node_motion` ~line 2737)
- Test: `adaptor/tests/test_charge_in_place.py`

**Interfaces:**
- Consumes: `self._last_node_id` (existing ~line 177), `self._is_dock_motion_node(node_id)` (existing ~line 2718), `self._run_charge_in_place()` (Task 5).
- Produces: `def _should_charge_in_place(self, node_id: Optional[str]) -> bool` — True when the (charge) target node equals the robot's current node and that node is a charge/dock node.

- [ ] **Step 1: Write the failing test**

```python
# add to adaptor/tests/test_charge_in_place.py
class _FakeVehicleDock:
    def __init__(self, charging=True):
        self._charging = charging
        self.um_dock_calls = 0

    async def um_dock(self, **params):
        self.um_dock_calls += 1


class AutoRouteTest(unittest.TestCase):
    def _adapter(self):
        adapter = Adapter(config=None)
        adapter.config.charge.nodes = ["CH1"]   # CH1 is a charge node
        adapter.config.charge_circuit.verify_timeout_sec = 0.2
        self.fake_cc = FakeChargeCircuit()
        adapter.set_charge_circuit(self.fake_cc)
        self.vehicle = _FakeVehicleDock(charging=True)
        adapter._vehicle = self.vehicle
        return adapter

    def test_in_place_when_charge_node_is_current_node(self):
        adapter = self._adapter()
        adapter._last_node_id = "CH1"
        self.assertTrue(adapter._should_charge_in_place("CH1"))

    def test_um_dock_when_charge_node_differs_from_current(self):
        adapter = self._adapter()
        adapter._last_node_id = "P5"   # robot parked elsewhere
        self.assertFalse(adapter._should_charge_in_place("CH1"))

    def test_not_in_place_for_non_dock_current_node(self):
        adapter = self._adapter()
        adapter._last_node_id = "P5"
        self.assertFalse(adapter._should_charge_in_place("P5"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place.AutoRouteTest -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_should_charge_in_place'`.

- [ ] **Step 3: Write minimal implementation**

Add the helper after `_is_dock_motion_node` (~line 2723):

```python
    def _should_charge_in_place(self, node_id: Optional[str]) -> bool:
        """True when the charge target is the node the robot is already at.

        node_id is the charge action's node; None means "use the robot's current
        node" (instant startCharging). In-place requires the target to equal
        _last_node_id AND that node to be a charge/dock node.
        """
        current = self._last_node_id
        if not current:
            return False
        target = node_id or current
        return target == current and self._is_dock_motion_node(current)
```

Now hook the three `um_dock()` charge dispatch points.

**(a) Instant `startCharging`** — in `_handle_start_charging_instant_action` (~line 4452), replace the body that schedules `_dock` with an in-place branch first:

```python
        self._update_instant_action_status(action_id, ActionStatus.RUNNING)

        if self._should_charge_in_place(None):
            async def _charge() -> None:
                ok, desc = await self._run_charge_in_place()
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FINISHED if ok else ActionStatus.FAILED,
                    result_description=desc,
                )
                print(f"[START CHARGING IN PLACE] actionId={action_id} ok={ok}")
            self._run_on_adapter_loop(_charge)
            return

        async def _dock() -> None:
            try:
                await self._vehicle.um_dock()
            except Exception as exc:
                self._update_instant_action_status(
                    action_id,
                    ActionStatus.FAILED,
                    result_description=f"startCharging failed: {exc}",
                )
                print(f"[START CHARGING FAILED] actionId={action_id}: {exc}")
                return
            self._update_instant_action_status(
                action_id,
                ActionStatus.FINISHED,
                result_description="UmDock sent; charging follows robot status",
            )
            print(f"[START CHARGING] actionId={action_id} UmDock sent.")

        self._run_on_adapter_loop(_dock)
```

**(b) Order-carried `startCharging`** — in `_dock_for_order_action`, before the `try: await self._vehicle.um_dock()` (~line 2666), add:

```python
        if self._should_charge_in_place(node_id):
            ok, desc = await self._run_charge_in_place()
            action_state.action_status = (
                ActionStatus.FINISHED if ok else ActionStatus.FAILED
            )
            action_state.result_description = desc
            print(
                f"[ORDER ACTION DONE] actionId={action.action_id} "
                f"command=chargeInPlace ok={ok}"
            )
            self.request_state_publish("order action terminal")
            return
```

**(c) Order node motion** — in `_send_node_motion`, inside the `if self._is_dock_motion_node(node.node_id):` block (~line 2737), branch to in-place when already at the node:

```python
        if self._is_dock_motion_node(node.node_id):
            if self._should_charge_in_place(node.node_id):
                print(
                    f"[ORDER NODE CHARGE IN PLACE] seq={node.sequence_id} "
                    f"id={node.node_id}"
                )
                ok, _desc = await self._run_charge_in_place()
                if ok:
                    self._docking_started_node_ids.add(str(node.node_id))
                return
            print(
                f"[ORDER NODE DOCK] seq={node.sequence_id} id={node.node_id} "
                "command=UmDock"
            )
            await self._vehicle.um_dock()
            self._docking_started_node_ids.add(str(node.node_id))
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place.AutoRouteTest -v`
Expected: PASS (3 tests).

Run the full new module + existing dock/charge regression:
Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place tests.test_simulator_charging tests.test_adapter_jibot_v3_order -v`
Expected: PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_charge_in_place.py
git commit -m "feat: auto-route startCharging to in-place at the charge node"
```

---

### Task 7: Release hold on `stopCharging` (process-exit safety via Task 2 atexit)

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`stopCharging` dispatch ~line 3143; teardown/disconnect path)
- Test: `adaptor/tests/test_charge_in_place.py`

**Interfaces:**
- Consumes: `self._charge_circuit`, `self._charge_in_place_active` (Task 4/5).
- Produces: `def _release_charge_in_place(self) -> None` — if a hold is active, `stop_hold()` and clear the flag.

- [ ] **Step 1: Write the failing test**

```python
# add to adaptor/tests/test_charge_in_place.py
class ReleaseTest(unittest.TestCase):
    def test_release_stops_active_hold(self):
        adapter = Adapter(config=None)
        fake = FakeChargeCircuit()
        adapter.set_charge_circuit(fake)
        fake.start_hold()
        adapter._charge_in_place_active = True

        adapter._release_charge_in_place()

        self.assertFalse(adapter._charge_in_place_active)
        self.assertEqual(fake.stop_calls, 1)

    def test_release_is_noop_when_inactive(self):
        adapter = Adapter(config=None)
        fake = FakeChargeCircuit()
        adapter.set_charge_circuit(fake)

        adapter._release_charge_in_place()

        self.assertEqual(fake.stop_calls, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place.ReleaseTest -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_release_charge_in_place'`.

- [ ] **Step 3: Write minimal implementation**

Add the helper near `_run_charge_in_place` (~line 4438):

```python
    def _release_charge_in_place(self) -> None:
        """Release an active in-place charge hold (relay OFF)."""
        if not self._charge_in_place_active:
            return
        self._charge_circuit.stop_hold()
        self._charge_in_place_active = False
        print("[CHARGE IN PLACE] hold released")
```

Call it from the `stopCharging` dispatch branch (~line 3143). Replace the
`("stopCharging", "logReport")` FAILED branch so `stopCharging` releases an active
hold and succeeds when it was in-place, while `logReport` keeps the old behaviour:

```python
            elif action.action_type == "stopCharging":
                if self._charge_in_place_active:
                    self._release_charge_in_place()
                    self._update_instant_action_status(
                        action.action_id,
                        ActionStatus.FINISHED,
                        result_description="in-place charge stopped",
                    )
                else:
                    self._update_instant_action_status(
                        action.action_id,
                        ActionStatus.FAILED,
                        result_description=(
                            "stopCharging has no matching JIBOT command"
                        ),
                    )
            elif action.action_type == "logReport":
```

Process-exit safety (an orphaned hold publisher left asserting the relay) is
already handled by `SubprocessChargeCircuit`'s `atexit` cleanup added in Task 2, so
no additional shutdown hook is needed here — `stopCharging` is the in-band release.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python3 -m unittest tests.test_charge_in_place.ReleaseTest -v`
Expected: PASS (2 tests).

Run the whole feature suite once more:
Run: `cd adaptor && python3 -m unittest tests.test_charge_circuit tests.test_charge_circuit_config tests.test_charge_in_place -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_charge_in_place.py
git commit -m "feat: release in-place charge hold on stopCharging and teardown"
```

---

## Self-Review

**Spec coverage:**
- ChargeCircuit component (hold/release) → Tasks 1, 2.
- Config `[charge_circuit]` + `enabled` default false → Task 3.
- Injection / Null default / simulator no-op → Task 4.
- `chargeInPlace` instant action + `is_charged` verification → Task 5.
- Auto-routing (charge node == current node) at all three dispatch points → Task 6.
- `stopCharging` releases hold; teardown releases hold → Task 7.
- "Move-to-charger UmDock unchanged" → preserved (the `else` branches keep `um_dock()`); regression covered by re-running `test_simulator_charging` and `test_adapter_jibot_v3_order` in Tasks 6–7.

**Real-robot verification (not unit-testable):** the persistence-by-holding assumption (continuous `cmd:3=1` keeps charging) must be confirmed on the live robot after Task 6, with `charge_circuit.enabled=true`, by issuing `chargeInPlace` while seated and watching `is_charged` / `bms_current` stay positive. This is an integration check, called out in the spec's Risks.

**Placeholder scan:** No placeholders. Process-exit safety is concrete via `SubprocessChargeCircuit`'s `atexit` cleanup (Task 2); `stopCharging` release is fully coded (Task 7). All code steps contain complete code.

**Type consistency:** `start_hold`/`stop_hold`/`is_holding`, `_run_charge_in_place() -> (bool, str)`, `_should_charge_in_place(node_id)`, `_release_charge_in_place()`, `_charge_in_place_active`, and `ChargeCircuitConfig` field names are used identically across tasks.
