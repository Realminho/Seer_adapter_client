# JIBOT BMS ROS Listener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface JIBOT `/jrobot_status.bms_voltage` and `bms_current` into the existing VDA5050 `batteryState.batteryVoltage` / `powerSupply.batteryCurrent` fields.

**Architecture:** The adapter runs on the robot but is ROS-free (python 3.12; Noetic rospy is python 3.8). A new in-adapter component, `BmsRosListener`, keeps ONE long-lived child process alive — `bash -lc 'source <ros_setup>; export ROS_MASTER_URI=...; exec rostopic echo -p /jrobot_status'` — and parses its CSV stdout, caching voltage/current on the vehicle. The existing VDA5050 getters already read those vehicle attributes, so no mapping code changes are needed.

**Tech Stack:** Python 3.12, asyncio subprocess, ROS1 Noetic `rostopic` (in the child only), pytest + unittest.

## Global Constraints

- Adapter targets **python >= 3.11**; never `import rospy`/ROS packages in adapter code — ROS lives only inside the `rostopic` child process. (`adaptor/pyproject.toml`)
- The adapter process must **never crash** because of this listener: catch all listener exceptions, log, back off, retry.
- **Do not change** SOC or charging behavior (still sourced from 7273 `UmGetLocState`/status). Scope is voltage/current only.
- Follow the existing ROS-subprocess pattern in `adaptor/utils/charge_circuit.py` (`source <ros_setup>; export ROS_MASTER_URI=...;` prefix, injectable spawn for tests).
- ROS setup default is `/usr/local/urobot/jarvis/setup.bash` (the urobot workspace overlay that provides `jarvis_msgs`), matching `ChargeCircuitConfig.ros_setup`.
- Tests run from the `adaptor/` directory: `python -m pytest tests/<file> -v` (pyproject sets `testpaths=["tests"]`, `pythonpath=["."]`). Tests needing the client insert `Path(__file__).resolve().parents[2] / "jibot-client" / "src"` onto `sys.path` then `from jibot_client import JIBOT`.
- `bms_current` is signed (negative = discharge/idle, positive = charging); pass it through raw, no sign flip. Units map 1:1 (volts, amps).

---

### Task 1: BMS CSV parser (pure functions)

Pure, ROS-free parsing of `rostopic echo -p` output. No I/O.

**Files:**
- Create: `adaptor/bms_ros_listener.py`
- Test: `adaptor/tests/test_bms_ros_listener_parse.py`

**Interfaces:**
- Produces:
  - `find_bms_columns(header: str) -> tuple[Optional[int], Optional[int]]` — returns `(voltage_idx, current_idx)`; either may be `None` if absent.
  - `parse_bms_row(row: str, voltage_idx, current_idx) -> Optional[tuple[float, float]]` — returns `(voltage, current)` or `None` for malformed/short/non-numeric rows or `None` indices.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_bms_ros_listener_parse.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from bms_ros_listener import find_bms_columns, parse_bms_row

HEADER = "%time,field.charge,field.EQ,field.bms_voltage,field.bms_current,field.system_status"


class FindBmsColumnsTest(unittest.TestCase):
    def test_locates_columns_by_suffix(self):
        self.assertEqual(find_bms_columns(HEADER), (3, 4))

    def test_order_independent(self):
        header = "%time,field.bms_current,field.bms_voltage"
        self.assertEqual(find_bms_columns(header), (2, 1))

    def test_missing_columns_return_none(self):
        self.assertEqual(find_bms_columns("%time,field.charge,field.EQ"), (None, None))


class ParseBmsRowTest(unittest.TestCase):
    def test_parses_floats(self):
        self.assertEqual(parse_bms_row("1700000000,1,88,54.6,7.2,Normal", 3, 4), (54.6, 7.2))

    def test_signed_current_preserved(self):
        self.assertEqual(parse_bms_row("1700000000,0,77,53.0,-0.4,Normal", 3, 4), (53.0, -0.4))

    def test_short_row_returns_none(self):
        self.assertIsNone(parse_bms_row("1700000000,0,77", 3, 4))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(parse_bms_row("1700000000,0,77,n/a,x,Normal", 3, 4))

    def test_none_index_returns_none(self):
        self.assertIsNone(parse_bms_row("a,b,c", None, 4))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bms_ros_listener'`.

- [ ] **Step 3: Write minimal implementation**

Create `adaptor/bms_ros_listener.py`:

```python
"""Stream JIBOT BMS voltage/current from ROS /jrobot_status into the vehicle.

UmGetBatteryInfo over TCP 7273 is a firmware stub (returns zeros) on this robot;
the real BMS truth is only on ROS topic /jrobot_status (jarvis_msgs/RobotStatus).
The adapter core stays ROS-free: this listener keeps ONE `rostopic echo -p`
subprocess alive (system ROS env / python 3.8) and parses its CSV stdout. The
parsed values feed the existing VDA5050 batteryState/powerSupply fields via
vehicle.set_bms(). See docs/reference/jibot-charging-dock-bms.md.
"""

from typing import Optional, Tuple


def find_bms_columns(header: str) -> Tuple[Optional[int], Optional[int]]:
    """Map a `rostopic echo -p` CSV header to (voltage_idx, current_idx).

    Column names look like `field.bms_voltage`; match by suffix so a prefix or
    nesting change does not break it. Either index is None when absent.
    """
    voltage_idx = current_idx = None
    for i, name in enumerate(c.strip() for c in header.split(",")):
        if name.endswith("bms_voltage"):
            voltage_idx = i
        elif name.endswith("bms_current"):
            current_idx = i
    return voltage_idx, current_idx


def parse_bms_row(row: str, voltage_idx, current_idx) -> Optional[Tuple[float, float]]:
    """Extract (voltage, current) floats from one CSV data row, or None."""
    if voltage_idx is None or current_idx is None:
        return None
    parts = [p.strip() for p in row.split(",")]
    if len(parts) <= max(voltage_idx, current_idx):
        return None
    try:
        return float(parts[voltage_idx]), float(parts[current_idx])
    except ValueError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_parse.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/bms_ros_listener.py adaptor/tests/test_bms_ros_listener_parse.py
git commit -m "feat(bms): rostopic CSV parser for /jrobot_status voltage/current"
```

---

### Task 2: Vehicle BMS cache (`set_bms` / `clear_bms`)

The listener writes parsed values onto the JIBOT client, where the existing
VDA5050 getters already read `_battery_voltage` / `_battery_current`.

**Files:**
- Modify: `jibot-client/src/jibot_client/client.py` (init block at lines 203-218; add methods after it)
- Test: `adaptor/tests/test_jibot_client_bms.py`

**Interfaces:**
- Produces (on `JIBOT`, inherited by `SimulatedJIBOT`):
  - `set_bms(self, voltage, current) -> None` — sets `_battery_voltage`, `_battery_current`, and `_bms_last_update = time.monotonic()`.
  - `clear_bms(self) -> None` — sets `_battery_voltage` and `_battery_current` back to `None`.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_jibot_client_bms.py`:

```python
import sys
import unittest
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[2] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from jibot_client import JIBOT


class BmsCacheTest(unittest.TestCase):
    def test_set_bms_caches_values_and_timestamp(self):
        v = JIBOT("127.0.0.1", 7273)
        self.assertIsNone(v._battery_voltage)
        self.assertIsNone(v._battery_current)
        v.set_bms(54.6, 7.2)
        self.assertEqual(v._battery_voltage, 54.6)
        self.assertEqual(v._battery_current, 7.2)
        self.assertGreater(v._bms_last_update, 0.0)

    def test_clear_bms_resets_to_none(self):
        v = JIBOT("127.0.0.1", 7273)
        v.set_bms(54.6, 7.2)
        v.clear_bms()
        self.assertIsNone(v._battery_voltage)
        self.assertIsNone(v._battery_current)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_jibot_client_bms.py -v`
Expected: FAIL — `AttributeError: 'JIBOT' object has no attribute 'set_bms'`.

- [ ] **Step 3: Write minimal implementation**

In `jibot-client/src/jibot_client/client.py`, add the init field right after
`self._charging = False` (line 218):

```python
        self._charging = False
        # Latest BMS reading from the ROS /jrobot_status listener (bms_ros_listener);
        # monotonic timestamp of the last successful set_bms, for staleness checks.
        self._bms_last_update = 0.0
```

Then add these two methods to the `JIBOT` class (place them near the other
small accessors, e.g. just before `_update_status_snapshot`):

```python
    def set_bms(self, voltage, current):
        """Cache the latest BMS voltage/current (volts, signed amps) from ROS.

        UmGetBatteryInfo over 7273 is a stub on this firmware, so these come from
        the /jrobot_status ROS listener. The existing VDA5050 getters read
        _battery_voltage / _battery_current.
        """
        self._battery_voltage = voltage
        self._battery_current = current
        self._bms_last_update = time.monotonic()

    def clear_bms(self):
        """Drop cached BMS values (stream stale or down) so VDA5050 stops
        publishing stale voltage/current."""
        self._battery_voltage = None
        self._battery_current = None
```

(`import time` already exists at the top of the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_jibot_client_bms.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jibot-client/src/jibot_client/client.py adaptor/tests/test_jibot_client_bms.py
git commit -m "feat(bms): set_bms/clear_bms cache on JIBOT client"
```

---

### Task 3: `BmsRosConfig` + config wiring

Add an optional `[bms_ros]` config section, mirroring how `charge_circuit` /
`video` are parsed (`SectionConfig(**config_dict.get("section", {}))`).

**Files:**
- Modify: `adaptor/config/config.py` (dataclasses ~line 101; `Config` ~line 160; `get_config` ~line 227-248)
- Modify: `adaptor/config/config.toml` (append section)
- Test: `adaptor/tests/test_bms_ros_config.py`

**Interfaces:**
- Produces: `config.config.BmsRosConfig` with fields `enabled: bool`, `topic: str`, `ros_setup: str`, `ros_master_uri: str`, `stale_after_sec: float`, `restart_backoff_sec: float`; reachable as `Config.bms_ros`.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_bms_ros_config.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from config.config import BmsRosConfig, get_config


class BmsRosConfigTest(unittest.TestCase):
    def test_defaults(self):
        c = BmsRosConfig()
        self.assertTrue(c.enabled)
        self.assertEqual(c.topic, "/jrobot_status")
        self.assertEqual(c.ros_setup, "/usr/local/urobot/jarvis/setup.bash")
        self.assertEqual(c.ros_master_uri, "http://localhost:11311")
        self.assertEqual(c.stale_after_sec, 30.0)
        self.assertEqual(c.restart_backoff_sec, 3.0)

    def test_config_exposes_bms_ros(self):
        cfg = get_config()
        self.assertIsInstance(cfg.bms_ros, BmsRosConfig)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'BmsRosConfig'`.

- [ ] **Step 3: Write minimal implementation**

In `adaptor/config/config.py`, add the dataclass after `ChargeCircuitConfig`
(after line 109):

```python
@dataclass
class BmsRosConfig:
    # Read-only ROS listener: streams /jrobot_status (jarvis_msgs/RobotStatus)
    # for the BMS voltage/current that UmGetBatteryInfo over 7273 stubs out.
    # Safe to default on (read-only); auto-disabled under --simulator.
    enabled: bool = True
    topic: str = "/jrobot_status"
    ros_setup: str = "/usr/local/urobot/jarvis/setup.bash"
    ros_master_uri: str = "http://localhost:11311"
    stale_after_sec: float = 30.0
    restart_backoff_sec: float = 3.0
```

Add the field to the `Config` dataclass (after `charge_circuit: ChargeCircuitConfig`, line 170):

```python
    charge_circuit: ChargeCircuitConfig
    bms_ros: BmsRosConfig
```

In `get_config`, parse it (after the `charge_circuit = ...` line, line 227):

```python
    charge_circuit = ChargeCircuitConfig(**config_dict.get("charge_circuit", {}))
    bms_ros = BmsRosConfig(**config_dict.get("bms_ros", {}))
```

And pass it into the returned `Config(...)` (after `charge_circuit=charge_circuit,`, line 243):

```python
        charge_circuit=charge_circuit,
        bms_ros=bms_ros,
```

- [ ] **Step 4: Append the section to `adaptor/config/config.toml`**

Add at the end of the file:

```toml
[bms_ros]
enabled = true
topic = "/jrobot_status"
ros_setup = "/usr/local/urobot/jarvis/setup.bash"
ros_master_uri = "http://localhost:11311"
stale_after_sec = 30.0
restart_backoff_sec = 3.0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/tests/test_bms_ros_config.py
git commit -m "feat(bms): [bms_ros] config section"
```

---

### Task 4: `BmsRosListener` stream consumer

The stdout-reading loop: parse lines, cache values, and clear on staleness.
Process management is added in Task 5; this task isolates and tests the
consume loop with a fake stream.

**Files:**
- Modify: `adaptor/bms_ros_listener.py` (add the class + `_consume`)
- Test: `adaptor/tests/test_bms_ros_listener_consume.py`

**Interfaces:**
- Consumes: `find_bms_columns`, `parse_bms_row` (Task 1); `vehicle.set_bms` / `vehicle.clear_bms` (Task 2); `BmsRosConfig` (Task 3).
- Produces:
  - `BmsRosListener(vehicle, cfg, spawn=None, sleep=None)`.
  - `async BmsRosListener._consume(self, stdout) -> None` — reads `await stdout.readline()` (bytes) until EOF (`b""`); on `asyncio.TimeoutError` after `cfg.stale_after_sec` calls `vehicle.clear_bms()`; header lines (containing `bms_voltage`) set column indices; data rows call `vehicle.set_bms(v, c)`.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_bms_ros_listener_consume.py`:

```python
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from bms_ros_listener import BmsRosListener


class FakeConfig:
    enabled = True
    topic = "/jrobot_status"
    ros_setup = "/x/setup.bash"
    ros_master_uri = "http://localhost:11311"
    stale_after_sec = 0.05
    restart_backoff_sec = 0.01


class FakeVehicle:
    def __init__(self):
        self.set_calls = []
        self.clear_calls = 0

    def set_bms(self, voltage, current):
        self.set_calls.append((voltage, current))

    def clear_bms(self):
        self.clear_calls += 1


class ScriptedStream:
    """async readline() that yields queued byte lines then EOF (b'')."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class BlockingStream:
    async def readline(self):
        await asyncio.Event().wait()  # never returns -> triggers wait_for timeout


HEADER = b"%time,field.charge,field.EQ,field.bms_voltage,field.bms_current,field.system_status\n"


class ConsumeTest(unittest.IsolatedAsyncioTestCase):
    async def test_header_then_rows_cache_values(self):
        vehicle = FakeVehicle()
        listener = BmsRosListener(vehicle, FakeConfig())
        stream = ScriptedStream([
            HEADER,
            b"1700000000,1,88,54.6,7.2,Normal\n",
            b"1700000001,1,88,54.7,7.1,Normal\n",
        ])
        await listener._consume(stream)
        self.assertEqual(vehicle.set_calls, [(54.6, 7.2), (54.7, 7.1)])

    async def test_data_before_header_ignored(self):
        vehicle = FakeVehicle()
        listener = BmsRosListener(vehicle, FakeConfig())
        stream = ScriptedStream([b"1700000000,1,88,54.6,7.2,Normal\n"])
        await listener._consume(stream)
        self.assertEqual(vehicle.set_calls, [])

    async def test_staleness_clears(self):
        vehicle = FakeVehicle()
        listener = BmsRosListener(vehicle, FakeConfig())
        task = asyncio.ensure_future(listener._consume(BlockingStream()))
        await asyncio.sleep(0.12)  # > stale_after_sec, allows >=1 timeout cycle
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.assertGreaterEqual(vehicle.clear_calls, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_consume.py -v`
Expected: FAIL — `ImportError: cannot import name 'BmsRosListener'`.

- [ ] **Step 3: Write minimal implementation**

Append to `adaptor/bms_ros_listener.py`:

```python
import asyncio


class BmsRosListener:
    """Keeps one `rostopic echo -p` child alive and caches BMS values on the
    vehicle. The adapter stays ROS-free: ROS lives only in the child process."""

    def __init__(self, vehicle, cfg, spawn=None, sleep=None):
        self._vehicle = vehicle
        self._cfg = cfg
        # Spawn default is resolved lazily in _run_once (Task 5) so this class is
        # constructible before _default_spawn exists / without a real ROS env.
        self._spawn = spawn
        self._sleep = sleep or asyncio.sleep

    async def _consume(self, stdout):
        voltage_idx = current_idx = None
        while True:
            try:
                raw = await asyncio.wait_for(
                    stdout.readline(), timeout=self._cfg.stale_after_sec
                )
            except asyncio.TimeoutError:
                self._vehicle.clear_bms()  # no fresh row within the window
                continue
            if not raw:
                return  # EOF: child exited
            line = (
                raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray))
                else str(raw)
            ).strip()
            if not line:
                continue
            if "bms_voltage" in line:  # CSV header (re)appears on (re)start
                voltage_idx, current_idx = find_bms_columns(line)
                continue
            parsed = parse_bms_row(line, voltage_idx, current_idx)
            if parsed is not None:
                self._vehicle.set_bms(parsed[0], parsed[1])
```

> Note: `_default_spawn` is referenced here but defined in Task 5. That is fine
> — `_consume` (the only thing tested in this task) never calls it, and the
> attribute is only resolved when `_default_spawn` is actually invoked. Task 5
> adds the method before any call path reaches it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_consume.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/bms_ros_listener.py adaptor/tests/test_bms_ros_listener_consume.py
git commit -m "feat(bms): BmsRosListener stdout consume loop with staleness clear"
```

---

### Task 5: Process supervision + `start_bms_listener` helper

Spawn the child, supervise/restart on death, and provide a testable factory
gated on simulator/enabled.

**Files:**
- Modify: `adaptor/bms_ros_listener.py` (add `command`, `_default_spawn`, `_terminate`, `_run_once`, `run`, and module-level `start_bms_listener`)
- Test: `adaptor/tests/test_bms_ros_listener_supervise.py`

**Interfaces:**
- Produces:
  - `BmsRosListener.command(self) -> list[str]` — `["bash", "-lc", "source <ros_setup>; export ROS_MASTER_URI=<uri>; exec rostopic echo -p <topic>"]`.
  - `async BmsRosListener._default_spawn(self)` — `asyncio.create_subprocess_exec(*self.command(), stdout=PIPE, stderr=DEVNULL)`.
  - `async BmsRosListener._run_once(self)` — spawn, `await self._consume(proc.stdout)`, then terminate.
  - `async BmsRosListener.run(self)` — returns immediately if `cfg.enabled` is false; else loops `_run_once` forever, calling `clear_bms()` and `await self._sleep(cfg.restart_backoff_sec)` between attempts.
  - `start_bms_listener(vehicle, cfg, simulator) -> Optional[asyncio.Task]` — `None` when `simulator` or `not cfg.enabled`; otherwise `asyncio.create_task(BmsRosListener(vehicle, cfg).run())`.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_bms_ros_listener_supervise.py`:

```python
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from bms_ros_listener import BmsRosListener, start_bms_listener


class FakeConfig:
    enabled = True
    topic = "/jrobot_status"
    ros_setup = "/x/setup.bash"
    ros_master_uri = "http://localhost:11311"
    stale_after_sec = 0.05
    restart_backoff_sec = 0.0


class FakeVehicle:
    def __init__(self):
        self.clear_calls = 0

    def set_bms(self, voltage, current):
        pass

    def clear_bms(self):
        self.clear_calls += 1


class EofStdout:
    async def readline(self):
        return b""  # immediate EOF -> _run_once returns at once


class FakeProc:
    def __init__(self):
        self.stdout = EofStdout()
        self.returncode = None  # alive, like a real child -> _terminate calls terminate()
        self.terminated = False

    def terminate(self):
        self.terminated = True

    async def wait(self):
        return 0


class _StopLoop(Exception):
    pass


class CommandTest(unittest.TestCase):
    def test_command_sources_ros_and_echoes_topic(self):
        listener = BmsRosListener(FakeVehicle(), FakeConfig())
        cmd = listener.command()
        self.assertEqual(cmd[0], "bash")
        self.assertEqual(cmd[1], "-lc")
        self.assertIn("source /x/setup.bash", cmd[2])
        self.assertIn("export ROS_MASTER_URI=http://localhost:11311", cmd[2])
        self.assertIn("exec rostopic echo -p /jrobot_status", cmd[2])


class RunSupervisionTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_restarts_until_sleep_stops_it(self):
        vehicle = FakeVehicle()
        spawned = []

        async def fake_spawn():
            proc = FakeProc()
            spawned.append(proc)
            return proc

        calls = {"n": 0}

        async def fake_sleep(_):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise _StopLoop

        listener = BmsRosListener(vehicle, FakeConfig(), spawn=fake_spawn, sleep=fake_sleep)
        with self.assertRaises(_StopLoop):
            await listener.run()

        self.assertEqual(len(spawned), 3)               # respawned each cycle
        self.assertTrue(all(p.terminated for p in spawned))
        self.assertGreaterEqual(vehicle.clear_calls, 3)  # cleared between cycles

    async def test_run_disabled_does_not_spawn(self):
        vehicle = FakeVehicle()
        spawned = []

        async def fake_spawn():
            spawned.append(1)
            return FakeProc()

        cfg = FakeConfig()
        cfg.enabled = False
        listener = BmsRosListener(vehicle, cfg, spawn=fake_spawn)
        await listener.run()
        self.assertEqual(spawned, [])


class StartHelperTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_for_simulator(self):
        self.assertIsNone(start_bms_listener(FakeVehicle(), FakeConfig(), simulator=True))

    async def test_returns_none_when_disabled(self):
        cfg = FakeConfig()
        cfg.enabled = False
        self.assertIsNone(start_bms_listener(FakeVehicle(), cfg, simulator=False))

    async def test_returns_task_when_enabled(self):
        task = start_bms_listener(FakeVehicle(), FakeConfig(), simulator=False)
        self.assertIsNotNone(task)
        task.cancel()  # cancel before it yields -> no real subprocess spawned
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_supervise.py -v`
Expected: FAIL — `ImportError: cannot import name 'start_bms_listener'`.

- [ ] **Step 3: Write minimal implementation**

Append the process-management methods to `BmsRosListener` in
`adaptor/bms_ros_listener.py`:

```python
    def command(self):
        prefix = (
            f"source {self._cfg.ros_setup}; "
            f"export ROS_MASTER_URI={self._cfg.ros_master_uri}; "
        )
        inner = f"exec rostopic echo -p {self._cfg.topic}"
        return ["bash", "-lc", prefix + inner]

    async def _default_spawn(self):
        return await asyncio.create_subprocess_exec(
            *self.command(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _terminate(self, proc):
        try:
            if proc.returncode is None:
                proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except Exception:
            pass

    async def _run_once(self):
        spawn = self._spawn or self._default_spawn  # lazy default (see __init__)
        proc = await spawn()
        try:
            await self._consume(proc.stdout)
        finally:
            await self._terminate(proc)

    async def run(self):
        """Supervise the child forever. Never raises out of normal operation:
        any error is logged and retried after a backoff. Cancellation
        (adapter shutdown) propagates normally."""
        if not self._cfg.enabled:
            return
        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # spawn failure, decode error, etc.
                print(f"[BMS ROS] listener error: {exc}")
            self._vehicle.clear_bms()
            await self._sleep(self._cfg.restart_backoff_sec)
```

Add at module level (end of file):

```python
def start_bms_listener(vehicle, cfg, simulator):
    """Create the listener task, or None when it should not run.

    Returns None under --simulator (no ROS) or when disabled in config.
    """
    if simulator or not cfg.enabled:
        return None
    listener = BmsRosListener(vehicle, cfg)
    return asyncio.create_task(listener.run())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_bms_ros_listener_supervise.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/bms_ros_listener.py adaptor/tests/test_bms_ros_listener_supervise.py
git commit -m "feat(bms): supervise rostopic child + start_bms_listener factory"
```

---

### Task 6: Wire the listener into `main.py`

One line, gated on the (tested) helper and the vehicle's simulator flag.

**Files:**
- Modify: `adaptor/main.py` (import near other adapter imports; call after `vehicle_task` is created, ~line 691)

**Interfaces:**
- Consumes: `start_bms_listener` (Task 5), `config_data.bms_ros` (Task 3), `vehicle.is_simulator`.

- [ ] **Step 1: Add the import and the wiring call**

In `adaptor/main.py`, after the `vehicle_task = asyncio.create_task(robot_info_loop(...))`
block (ends line 691), add:

```python
        # Stream BMS voltage/current from ROS /jrobot_status (UmGetBatteryInfo is
        # a 7273 stub). No-op under --simulator or when [bms_ros] is disabled.
        from bms_ros_listener import start_bms_listener
        bms_task = start_bms_listener(vehicle, config_data.bms_ros, vehicle.is_simulator)
```

- [ ] **Step 2: Verify the module imports and parses**

Run: `cd adaptor && python -c "import ast; ast.parse(open('main.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run the full adapter test suite (no regressions)**

Run: `cd adaptor && python -m pytest tests/ -q`
Expected: PASS — all tests green, including the four new `test_bms_ros_*` /
`test_jibot_client_bms` files.

- [ ] **Step 4: Commit**

```bash
git add adaptor/main.py
git commit -m "feat(bms): start ROS BMS listener from main (simulator-gated)"
```

- [ ] **Step 5: On-robot manual verification (record result)**

This wiring cannot be unit-tested end-to-end (needs a real ROS master). On bot A,
after deploy:

```bash
# 1. The child can read the topic at all:
ssh ucore@192.168.3.222 \
  "bash -lc 'source /usr/local/urobot/jarvis/setup.bash; export ROS_MASTER_URI=http://localhost:11311; timeout 5 rostopic echo -p -n3 /jrobot_status'"
# Expect a CSV header containing field.bms_voltage / field.bms_current and rows
# with a ~53-55 V voltage and signed current.

# 2. After the adapter runs, the VDA5050 state shows non-null voltage/current.
```

Expected: `batteryState.batteryVoltage` ≈ 53–55 V and `powerSupply.batteryCurrent`
present (signed) in the published state; SOC/charging unchanged. If `/jrobot_status`
is absent, both stay `None` and the adapter runs normally.

---

### Task 7 (optional): Comment the dead `UmGetBatteryInfo` stub path

Prevent a future reader from "fixing" the unused 7273 poll. No behavior change,
no test.

**Files:**
- Modify: `jibot-client/src/jibot_client/client.py` (the `if response.get("#CMD#") == "UmGetBatteryInfo":` block, ~line 1006)

- [ ] **Step 1: Add the comment**

Insert above the `UmGetBatteryInfo` handler:

```python
        # NOTE: UmGetBatteryInfo over 7273 is a firmware STUB on this robot
        # (returns nested zeros). Real BMS voltage/current come from the ROS
        # /jrobot_status listener (adaptor/bms_ros_listener.py). Kept for
        # forward-compat if firmware ever populates it.
        if response.get("#CMD#") == "UmGetBatteryInfo":
```

- [ ] **Step 2: Verify nothing broke**

Run: `cd adaptor && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add jibot-client/src/jibot_client/client.py
git commit -m "docs(bms): note UmGetBatteryInfo is a 7273 stub; real source is ROS"
```

---

## Self-Review

**Spec coverage:**
- voltage/current → VDA5050 via ROS: Tasks 1–6. No VDA5050 mapping change needed (existing getters read `_battery_voltage`/`_battery_current`) — confirmed in spec §"Data flow".
- In-adapter, ROS-free, single supervised child: Tasks 4–5 (`command`, `_run_once`, `run`).
- Lifecycle/failure (restart, spawn-fail, staleness, simulator): Task 5 (`run` backoff + `clear_bms`), Task 4 (staleness), Task 6 (simulator gate).
- Config section with the spec's keys/defaults: Task 3 (note `ros_setup` corrected to `/usr/local/urobot/jarvis/setup.bash`, the urobot overlay that provides `jarvis_msgs` — better than the spec's `/opt/ros/noetic/setup.bash`).
- Tests without real ROS: Tasks 1,4,5 use pure functions / fake streams / fake spawn.
- Optional `UmGetBatteryInfo` comment: Task 7.
- SOC/charging untouched: nothing in any task writes SOC or `_charging`.

**Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output.

**Type consistency:** `find_bms_columns`/`parse_bms_row` signatures match between Tasks 1 and 4. `set_bms(voltage, current)`/`clear_bms()` match between Tasks 2, 4, 5. `BmsRosConfig` field names match between Task 3 and the `FakeConfig`/usage in Tasks 4–5. `start_bms_listener(vehicle, cfg, simulator)` matches between Tasks 5 and 6. `_spawn` is an async no-arg callable in both `_default_spawn` and the injected `fake_spawn`.
