# TUI Live Adaptor Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the adaptor TUI show the adaptor instances that are actually expected or running, instead of only showing two fixed systemd units.

**Architecture:** Keep systemd service control as one status source, but add two more sources: `config/robots.toml` for expected JIBOT fleet entries and local process scanning for manually launched `main.py --id ...` processes. MQTT remains the source of robot live state; each discovered/configured serial gets its own monitor for `{vda_interface}/{vda_version}/{serial}`. Rendering uses a small pure formatter so the UI can say `not installed`, `active`, or `manual process` accurately.

**Tech Stack:** Python 3.12 stdlib, `curses`, `tomllib`/`tomli`, existing `paho-mqtt`, existing `unittest` tests under `adaptor/tests`.

---

## File Structure

- Modify `adaptor/tui/registry.py`
  - Load `config/robots.toml` when present.
  - Build one JIBOT `AdaptorSpec` per robot entry.
  - Keep current single JIBOT + Hexplorer fallback when no `robots.toml` exists.
- Create `adaptor/tui/processes.py`
  - Parse local `ps` output.
  - Detect running adaptor `main.py` processes and map them by serial id.
  - Convert process CPU, RSS, and elapsed time into display metrics.
- Create `adaptor/tui/status.py`
  - Combine systemd metrics and process metrics into one display status.
  - Keep this logic pure and unit-testable.
- Modify `adaptor/tui/app.py`
  - Poll process metrics alongside systemd metrics.
  - Build MQTT monitors per unique broker/topic prefix.
  - Render `manual process` when systemd is missing but a matching process exists.
  - Show serial/topic details on the dashboard so mismatched topics are obvious.
- Modify `adaptor/tui/widgets.py`
  - Add ASCII-safe gauge fallback for terminals that cannot render block glyphs.
- Create `adaptor/tests/test_tui_registry.py`
  - Cover `robots.toml` parsing and registry fallback.
- Create `adaptor/tests/test_tui_processes.py`
  - Cover process parsing, serial extraction, and metric conversion.
- Create `adaptor/tests/test_tui_status.py`
  - Cover status precedence between systemd and process data.
- Create or modify `adaptor/tests/test_tui_widgets.py`
  - Cover ASCII and Unicode gauge output.
- Modify `docs/guide/adaptor-tui.md`
  - Document that TUI now displays systemd-managed, `robots.toml` expected, and manually running adaptors.

## Task 1: Build Registry From `robots.toml`

**Files:**
- Modify: `adaptor/tui/registry.py`
- Create: `adaptor/tests/test_tui_registry.py`

- [ ] **Step 1: Write failing tests for fleet registry behavior**

Create `adaptor/tests/test_tui_registry.py`:

```python
import tempfile
import unittest
from pathlib import Path

from config.config import get_config
from tui import registry


class RegistryFleetTest(unittest.TestCase):
    def setUp(self):
        self.config = get_config()

    def test_load_robot_entries_returns_empty_when_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "robots.toml"
            self.assertEqual(registry.load_robot_entries(path), [])

    def test_load_robot_entries_parses_robot_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "robots.toml"
            path.write_text(
                """
[[robot]]
id = "HN-SH6-TR-001"
vehicle_ip = "10.0.0.11"
vehicle_port = 7273
ezi_io = "10.8.8.87"
ezi_motor = "10.8.8.2"

[[robot]]
id = "HN-SH6-TR-002"
mqtt_host = "192.168.3.108"
mqtt_port = 11883
simulator = true
""",
                encoding="utf-8",
            )

            robots = registry.load_robot_entries(path)

        self.assertEqual([robot.robot_id for robot in robots], ["HN-SH6-TR-001", "HN-SH6-TR-002"])
        self.assertEqual(robots[0].vehicle_ip, "10.0.0.11")
        self.assertEqual(robots[0].vehicle_port, 7273)
        self.assertEqual(robots[1].mqtt_host, "192.168.3.108")
        self.assertEqual(robots[1].mqtt_port, 11883)
        self.assertTrue(robots[1].simulator)

    def test_build_registry_uses_robot_entries_for_jibot_specs(self):
        robots = [
            registry.RobotEntry(robot_id="HN-SH6-TR-001", vehicle_ip="10.0.0.11", vehicle_port=7273),
            registry.RobotEntry(robot_id="HN-SH6-TR-002", mqtt_host="192.168.3.108", mqtt_port=11883),
        ]

        specs = registry.build_registry(self.config, robot_entries=robots)

        keys = [spec.key for spec in specs]
        self.assertIn("jibot:HN-SH6-TR-001", keys)
        self.assertIn("jibot:HN-SH6-TR-002", keys)
        self.assertIn("hexplorer", keys)

        first = next(spec for spec in specs if spec.key == "jibot:HN-SH6-TR-001")
        second = next(spec for spec in specs if spec.key == "jibot:HN-SH6-TR-002")
        self.assertEqual(first.serial, "HN-SH6-TR-001")
        self.assertEqual(first.display_name, "JIBOT HN-SH6-TR-001")
        self.assertEqual(first.topic_prefix, f"{self.config.mqtt_broker.vda_interface}/{self.config.vehicle.vda_version}/HN-SH6-TR-001")
        self.assertEqual(second.mqtt_host, "192.168.3.108")
        self.assertEqual(second.mqtt_port, 11883)

    def test_build_registry_falls_back_to_single_config_jibot_when_no_robots(self):
        specs = registry.build_registry(self.config, robot_entries=[])

        self.assertEqual(specs[0].key, "jibot")
        self.assertEqual(specs[0].display_name, "JIBOT Adapter")
        self.assertEqual(specs[0].serial, self.config.vehicle.serial_number)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run registry tests and verify they fail**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_registry -v
```

Expected: FAIL with errors for missing `load_robot_entries`, `RobotEntry`, or `robot_entries` parameter.

- [ ] **Step 3: Extend `AdaptorSpec` and add robot entry parsing**

Modify `adaptor/tui/registry.py`:

```python
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
```

Add fields to `AdaptorSpec`:

```python
    mqtt_host: str = ""
    mqtt_port: int = 0
    runtime_kind: str = "systemd"
```

Add this dataclass after `AdaptorSpec`:

```python
@dataclass(frozen=True)
class RobotEntry:
    robot_id: str
    vehicle_ip: str = ""
    vehicle_port: int | None = None
    ezi_io: str = ""
    ezi_motor: str = ""
    mqtt_host: str = ""
    mqtt_port: int | None = None
    config: str = ""
    simulator: bool = False
```

Add this function:

```python
def load_robot_entries(path: Path | None = None) -> List[RobotEntry]:
    robots_path = path or (ADAPTER_ROOT / "config" / "robots.toml")
    if not robots_path.exists():
        return []
    with open(robots_path, "rb") as fh:
        data = tomllib.load(fh)
    entries: List[RobotEntry] = []
    for item in data.get("robot", []):
        robot_id = str(item.get("id") or "").strip()
        if not robot_id:
            continue
        entries.append(
            RobotEntry(
                robot_id=robot_id,
                vehicle_ip=str(item.get("vehicle_ip") or ""),
                vehicle_port=item.get("vehicle_port"),
                ezi_io=str(item.get("ezi_io") or ""),
                ezi_motor=str(item.get("ezi_motor") or ""),
                mqtt_host=str(item.get("mqtt_host") or ""),
                mqtt_port=item.get("mqtt_port"),
                config=str(item.get("config") or ""),
                simulator=bool(item.get("simulator", False)),
            )
        )
    return entries
```

- [ ] **Step 4: Split JIBOT spec construction**

Add helper functions in `adaptor/tui/registry.py`:

```python
def _jibot_spec(config, serial: str, key: str, display_name: str, mqtt_host: str = "", mqtt_port: int = 0) -> AdaptorSpec:
    prefix = _topic_prefix(
        config.mqtt_broker.vda_interface,
        config.vehicle.vda_version,
        serial,
    )
    return AdaptorSpec(
        key=key,
        display_name=display_name,
        unit="jibot-adapter.service",
        workdir=ADAPTER_ROOT,
        exec_script="run-main.sh",
        manufacturer=config.vehicle.manufacturer or "jibot",
        serial=serial,
        vda_full_version=config.vehicle.vda_full_version,
        topic_prefix=prefix,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        test_suites=(
            Diagnostic(
                "unittest: jibot v3 order",
                ("{py}", "-m", "unittest", "tests.test_adapter_jibot_v3_order", "-v"),
            ),
            Diagnostic(
                "unittest: video streamer",
                ("{py}", "-m", "unittest", "tests.test_video", "-v"),
            ),
        ),
        diagnostics=(
            Diagnostic(
                "smoke test (simulator)",
                ("{py}", "main.py", "--simulator", "--vehicle-smoke-test"),
                "Connects to the local JIBOT simulator and prints a state snapshot.",
            ),
            Diagnostic(
                "smoke test (real vehicle)",
                ("{py}", "main.py", "--vehicle-smoke-test"),
                "Connects to the configured vehicle IP/port and prints a snapshot.",
            ),
            Diagnostic(
                "comms self-test",
                ("{py}", "../scripts/test-jibot-communication.py"),
                "Runs the fake-server + simulator communication checks.",
            ),
        ),
        instant_actions=(
            InstantAction("stateRequest", "Request state", motion=False),
            InstantAction("factsheetRequest", "Request factsheet", motion=False),
            InstantAction("startPause", "Pause (startPause)", motion=True),
            InstantAction("stopPause", "Resume (stopPause)", motion=True),
            InstantAction("startCharging", "Start charging", motion=True),
            InstantAction("cancelOrder", "Cancel order", motion=True),
        ),
    )
```

Then change `build_registry` signature and JIBOT creation:

```python
def build_registry(config, robot_entries: List[RobotEntry] | None = None) -> List[AdaptorSpec]:
    """Build adaptor specs from config.toml plus optional robots.toml fleet entries."""

    if robot_entries is None:
        robot_entries = load_robot_entries()

    if robot_entries:
        jibot_specs = [
            _jibot_spec(
                config,
                serial=robot.robot_id,
                key=f"jibot:{robot.robot_id}",
                display_name=f"JIBOT {robot.robot_id}",
                mqtt_host=robot.mqtt_host,
                mqtt_port=int(robot.mqtt_port or 0),
            )
            for robot in robot_entries
        ]
    else:
        jibot_specs = [
            _jibot_spec(
                config,
                serial=config.vehicle.serial_number,
                key="jibot",
                display_name="JIBOT Adapter",
            )
        ]
```

Keep the existing Hexplorer `AdaptorSpec`, but set its MQTT fields:

```python
        mqtt_host=config.mqtt_broker.host,
        mqtt_port=config.mqtt_broker.port,
```

Return:

```python
    return jibot_specs + [hexplorer]
```

- [ ] **Step 5: Run registry tests and verify they pass**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_registry -v
```

Expected: PASS.

- [ ] **Step 6: Commit registry changes**

```bash
git add adaptor/tui/registry.py adaptor/tests/test_tui_registry.py
git commit -m "feat(tui): build adaptor registry from robots toml"
```

## Task 2: Detect Manually Running Adaptor Processes

**Files:**
- Create: `adaptor/tui/processes.py`
- Create: `adaptor/tests/test_tui_processes.py`

- [ ] **Step 1: Write failing tests for process parsing**

Create `adaptor/tests/test_tui_processes.py`:

```python
import unittest

from tui import processes


class ProcessParsingTest(unittest.TestCase):
    def test_parse_elapsed_seconds(self):
        self.assertEqual(processes.parse_elapsed_seconds("42"), 42)
        self.assertEqual(processes.parse_elapsed_seconds("03:04"), 184)
        self.assertEqual(processes.parse_elapsed_seconds("02:03:04"), 7384)
        self.assertEqual(processes.parse_elapsed_seconds("1-02:03:04"), 93784)
        self.assertIsNone(processes.parse_elapsed_seconds("-"))

    def test_extract_serial_from_main_py_args(self):
        self.assertEqual(
            processes.extract_serial(["python", "main.py", "--id", "HN-SH6-TR-002"]),
            "HN-SH6-TR-002",
        )
        self.assertEqual(
            processes.extract_serial(["python", "main.py", "--serial-number=HN-SH6-TR-003"]),
            "HN-SH6-TR-003",
        )
        self.assertIsNone(processes.extract_serial(["python", "main.py"]))

    def test_parse_ps_rows_detects_main_py_processes(self):
        text = """  PID  PPID %CPU   RSS     ELAPSED COMMAND
 1234     1  2.5 51200       03:04 /home/u/adaptor/venvJIBOT/bin/python /home/u/adaptor/main.py --id HN-SH6-TR-002 --mqtt-host 192.168.3.108 --mqtt-port 11883
 5678     1  0.1 10240       00:10 /usr/bin/python unrelated.py
"""

        found = processes.parse_ps(text)

        self.assertIn("HN-SH6-TR-002", found)
        metric = found["HN-SH6-TR-002"]
        self.assertEqual(metric.pid, 1234)
        self.assertEqual(metric.ppid, 1)
        self.assertEqual(metric.cpu_percent, 2.5)
        self.assertEqual(metric.memory_bytes, 51200 * 1024)
        self.assertEqual(metric.uptime_sec, 184)
        self.assertIn("--mqtt-host", metric.args)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run process tests and verify they fail**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_processes -v
```

Expected: FAIL because `tui.processes` does not exist.

- [ ] **Step 3: Implement process parsing**

Create `adaptor/tui/processes.py`:

```python
"""Local process discovery for adaptor instances launched outside systemd."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ProcessMetrics:
    pid: int
    ppid: int
    cpu_percent: float
    memory_bytes: int
    uptime_sec: Optional[float]
    args: str


def parse_elapsed_seconds(value: str) -> Optional[float]:
    text = value.strip()
    if not text or text == "-":
        return None
    days = 0
    if "-" in text:
        day_text, _, text = text.partition("-")
        try:
            days = int(day_text)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        seconds = nums[0]
    elif len(nums) == 2:
        minutes, seconds = nums
        seconds = minutes * 60 + seconds
    elif len(nums) == 3:
        hours, minutes, seconds = nums
        seconds = hours * 3600 + minutes * 60 + seconds
    else:
        return None
    return float(days * 86400 + seconds)


def extract_serial(argv: List[str]) -> Optional[str]:
    if not any(arg.endswith("main.py") or arg == "main.py" for arg in argv):
        return None
    for index, arg in enumerate(argv):
        if arg in ("--id", "--serial-number") and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--id="):
            return arg.partition("=")[2]
        if arg.startswith("--serial-number="):
            return arg.partition("=")[2]
    return None


def parse_ps(text: str) -> Dict[str, ProcessMetrics]:
    result: Dict[str, ProcessMetrics] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("PID "):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid_text, ppid_text, cpu_text, rss_text, elapsed_text, args = parts
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
            cpu = float(cpu_text)
            rss_kib = int(rss_text)
        except ValueError:
            continue
        try:
            argv = shlex.split(args)
        except ValueError:
            argv = args.split()
        serial = extract_serial(argv)
        if serial is None:
            continue
        result[serial] = ProcessMetrics(
            pid=pid,
            ppid=ppid,
            cpu_percent=cpu,
            memory_bytes=rss_kib * 1024,
            uptime_sec=parse_elapsed_seconds(elapsed_text),
            args=args,
        )
    return result


class ProcessScanner:
    def __init__(self, timeout: float = 3.0) -> None:
        self.timeout = timeout

    def poll(self) -> Dict[str, ProcessMetrics]:
        try:
            proc = subprocess.run(
                ["ps", "-eo", "pid,ppid,pcpu,rss,etime,args"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        if proc.returncode != 0:
            return {}
        return parse_ps(proc.stdout)
```

- [ ] **Step 4: Run process tests and verify they pass**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_processes -v
```

Expected: PASS.

- [ ] **Step 5: Commit process scanner**

```bash
git add adaptor/tui/processes.py adaptor/tests/test_tui_processes.py
git commit -m "feat(tui): detect manually running adaptor processes"
```

## Task 3: Compose Accurate Runtime Status

**Files:**
- Create: `adaptor/tui/status.py`
- Create: `adaptor/tests/test_tui_status.py`

- [ ] **Step 1: Write failing tests for status precedence**

Create `adaptor/tests/test_tui_status.py`:

```python
import unittest

from tui.processes import ProcessMetrics
from tui.status import compose_runtime_status
from tui.systemd import ServiceMetrics


class RuntimeStatusTest(unittest.TestCase):
    def test_active_systemd_wins(self):
        svc = ServiceMetrics(
            exists=True,
            active_state="active",
            sub_state="running",
            main_pid=10,
            cpu_percent=1.2,
            memory_bytes=1000,
            uptime_sec=30,
            restarts=0,
        )
        proc = ProcessMetrics(pid=20, ppid=1, cpu_percent=3.0, memory_bytes=2000, uptime_sec=40, args="python main.py --id A")

        status = compose_runtime_status(svc, proc)

        self.assertEqual(status.source, "systemd")
        self.assertEqual(status.state_text, "active")
        self.assertEqual(status.pid, 10)
        self.assertEqual(status.cpu_percent, 1.2)

    def test_manual_process_used_when_unit_missing(self):
        svc = ServiceMetrics(exists=False, active_state="unknown")
        proc = ProcessMetrics(pid=20, ppid=1, cpu_percent=3.0, memory_bytes=2048, uptime_sec=40, args="python main.py --id A")

        status = compose_runtime_status(svc, proc)

        self.assertEqual(status.source, "process")
        self.assertEqual(status.state_text, "manual process")
        self.assertEqual(status.pid, 20)
        self.assertEqual(status.memory_bytes, 2048)
        self.assertEqual(status.uptime_sec, 40)

    def test_missing_unit_without_process_is_not_installed(self):
        svc = ServiceMetrics(exists=False, active_state="unknown")

        status = compose_runtime_status(svc, None)

        self.assertEqual(status.source, "none")
        self.assertEqual(status.state_text, "not installed")
        self.assertIsNone(status.pid)

    def test_installed_inactive_unit_without_process_stays_inactive(self):
        svc = ServiceMetrics(exists=True, active_state="inactive", sub_state="dead", enabled_state="enabled")

        status = compose_runtime_status(svc, None)

        self.assertEqual(status.source, "systemd")
        self.assertEqual(status.state_text, "inactive")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run status tests and verify they fail**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_status -v
```

Expected: FAIL because `tui.status` does not exist.

- [ ] **Step 3: Implement status composition**

Create `adaptor/tui/status.py`:

```python
"""Pure runtime status formatting for the TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tui.processes import ProcessMetrics
from tui.systemd import ServiceMetrics


@dataclass(frozen=True)
class RuntimeStatus:
    source: str
    state_text: str
    pid: Optional[int]
    cpu_percent: Optional[float]
    memory_bytes: Optional[int]
    uptime_sec: Optional[float]
    restarts: Optional[int]


def compose_runtime_status(
    service: ServiceMetrics,
    process: Optional[ProcessMetrics],
) -> RuntimeStatus:
    if service.exists:
        return RuntimeStatus(
            source="systemd",
            state_text=service.active_state,
            pid=service.main_pid,
            cpu_percent=service.cpu_percent,
            memory_bytes=service.memory_bytes,
            uptime_sec=service.uptime_sec,
            restarts=service.restarts,
        )
    if process is not None:
        return RuntimeStatus(
            source="process",
            state_text="manual process",
            pid=process.pid,
            cpu_percent=process.cpu_percent,
            memory_bytes=process.memory_bytes,
            uptime_sec=process.uptime_sec,
            restarts=None,
        )
    return RuntimeStatus(
        source="none",
        state_text="not installed",
        pid=None,
        cpu_percent=None,
        memory_bytes=None,
        uptime_sec=None,
        restarts=None,
    )
```

- [ ] **Step 4: Run status tests and verify they pass**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_status -v
```

Expected: PASS.

- [ ] **Step 5: Commit status composition**

```bash
git add adaptor/tui/status.py adaptor/tests/test_tui_status.py
git commit -m "feat(tui): compose runtime status from service and process data"
```

## Task 4: Wire Runtime Status Into the App

**Files:**
- Modify: `adaptor/tui/app.py`
- Modify: `adaptor/tests/test_tui_status.py`

- [ ] **Step 1: Add a test for process status with installed-but-inactive service**

Append this test to `RuntimeStatusTest` in `adaptor/tests/test_tui_status.py`:

```python
    def test_manual_process_used_when_unit_is_installed_but_inactive(self):
        svc = ServiceMetrics(exists=True, active_state="inactive", sub_state="dead", enabled_state="enabled")
        proc = ProcessMetrics(pid=20, ppid=1, cpu_percent=3.0, memory_bytes=2048, uptime_sec=40, args="python main.py --id A")

        status = compose_runtime_status(svc, proc)

        self.assertEqual(status.source, "process")
        self.assertEqual(status.state_text, "manual process")
        self.assertEqual(status.pid, 20)
```

- [ ] **Step 2: Run status tests and verify the new test fails**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_status -v
```

Expected: FAIL because `compose_runtime_status` currently prefers every installed systemd unit.

- [ ] **Step 3: Update status precedence**

Modify `compose_runtime_status` in `adaptor/tui/status.py` so active or failed systemd states win, but an inactive service with a live process displays the process:

```python
def compose_runtime_status(
    service: ServiceMetrics,
    process: Optional[ProcessMetrics],
) -> RuntimeStatus:
    if service.exists and service.active_state not in ("inactive", "unknown"):
        return RuntimeStatus(
            source="systemd",
            state_text=service.active_state,
            pid=service.main_pid,
            cpu_percent=service.cpu_percent,
            memory_bytes=service.memory_bytes,
            uptime_sec=service.uptime_sec,
            restarts=service.restarts,
        )
    if process is not None:
        return RuntimeStatus(
            source="process",
            state_text="manual process",
            pid=process.pid,
            cpu_percent=process.cpu_percent,
            memory_bytes=process.memory_bytes,
            uptime_sec=process.uptime_sec,
            restarts=None,
        )
    if service.exists:
        return RuntimeStatus(
            source="systemd",
            state_text=service.active_state,
            pid=service.main_pid,
            cpu_percent=service.cpu_percent,
            memory_bytes=service.memory_bytes,
            uptime_sec=service.uptime_sec,
            restarts=service.restarts,
        )
    return RuntimeStatus(
        source="none",
        state_text="not installed",
        pid=None,
        cpu_percent=None,
        memory_bytes=None,
        uptime_sec=None,
        restarts=None,
    )
```

- [ ] **Step 4: Import scanner and status composer in the app**

Modify imports in `adaptor/tui/app.py`:

```python
from tui.processes import ProcessMetrics, ProcessScanner
from tui.status import compose_runtime_status
```

- [ ] **Step 5: Track process metrics in `App.__init__`**

Add after `self.metrics` initialization:

```python
        self.process_scanner = ProcessScanner()
        self.process_metrics: Dict[str, ProcessMetrics] = {}
```

- [ ] **Step 6: Poll process metrics in `_poll_loop`**

At the start of each `_poll_loop` iteration, before polling systemd controllers:

```python
            process_by_serial = self.process_scanner.poll()
            with self._metrics_lock:
                self.process_metrics = process_by_serial
```

Add this method after `get_metrics`:

```python
    def get_process_metrics(self, serial: str) -> Optional[ProcessMetrics]:
        with self._metrics_lock:
            return self.process_metrics.get(serial)
```

- [ ] **Step 7: Use composed status in the left process list**

In `_draw_process_list`, replace direct `metrics` display with:

```python
            metrics = self.get_metrics(spec.key)
            proc = self.get_process_metrics(spec.serial)
            runtime = compose_runtime_status(metrics, proc)
            snap = self.spec_monitor[spec.key].get_snapshot()
```

Replace state/cpu/memory/uptime/restart lines in that method:

```python
            dot_state = "active" if runtime.source == "process" else metrics.active_state
            dot_attr = color(_active_color(dot_state))
```

```python
            state_txt = runtime.state_text
            addstr(self.stdscr, row, 4, state_txt[: width - 6], color(_active_color(dot_state)))
```

```python
            cpu = "-" if runtime.cpu_percent is None else f"{runtime.cpu_percent:.0f}%"
            mem = format_bytes(runtime.memory_bytes)
```

```python
            up = format_uptime(runtime.uptime_sec)
            restarts = "-" if runtime.restarts is None else str(runtime.restarts)
```

- [ ] **Step 8: Use composed status in the dashboard service block**

At the top of `_draw_dashboard`, after `metrics`:

```python
        proc = self.get_process_metrics(spec.serial)
        runtime = compose_runtime_status(metrics, proc)
```

Replace the `if not metrics.exists:` branch with:

```python
        field("unit", spec.unit)
        field("status", runtime.state_text, color(_active_color("active" if runtime.source == "process" else metrics.active_state)))
        field("runtime", runtime.source)
        field("serial", spec.serial)
        field("topic", spec.topic_prefix)
        if runtime.pid is not None:
            field("pid", str(runtime.pid))
        if runtime.source == "none":
            addstr(self.stdscr, row, left, "Run: scripts/setup-adaptor-service.sh --" + spec.key.split(":", 1)[0], color(widgets.CP_WARN))
            row += 1
        else:
            field("uptime", format_uptime(runtime.uptime_sec))
            field("restarts", "-" if runtime.restarts is None else str(runtime.restarts))
            cpu = "-" if runtime.cpu_percent is None else f"{runtime.cpu_percent:.1f}%"
            field("cpu", cpu)
            field("memory", format_bytes(runtime.memory_bytes))
```

Remove the old `else:` block that duplicated unit, active, pid, uptime, restart, cpu, and memory fields.

- [ ] **Step 9: Use per-spec broker overrides for MQTT monitors**

In `App.__init__`, replace monitor creation host/port:

```python
                host = spec.mqtt_host or self._broker_host()
                port = spec.mqtt_port or self._broker_port()
                self.monitors[key] = MqttMonitor(
                    host,
                    port,
                    spec.topic_prefix,
                    client_id=f"adaptor-tui-{os.getpid()}-{spec.serial}",
                )
```

- [ ] **Step 10: Run TUI unit tests**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_registry tests.test_tui_processes tests.test_tui_status tests.test_tui_monitor tests.test_tui_systemd -v
```

Expected: PASS.

- [ ] **Step 11: Commit app integration**

```bash
git add adaptor/tui/app.py adaptor/tui/status.py adaptor/tests/test_tui_status.py
git commit -m "feat(tui): show manual adaptor runtime status"
```

## Task 5: Add ASCII Gauge Fallback

**Files:**
- Modify: `adaptor/tui/widgets.py`
- Create: `adaptor/tests/test_tui_widgets.py`

- [ ] **Step 1: Write failing widget tests**

Create `adaptor/tests/test_tui_widgets.py`:

```python
import unittest

from tui import widgets


class GaugeTest(unittest.TestCase):
    def test_unicode_gauge(self):
        self.assertEqual(widgets.gauge(50, 6, ascii_only=False), "[██··]")

    def test_ascii_gauge(self):
        self.assertEqual(widgets.gauge(50, 6, ascii_only=True), "[##--]")

    def test_missing_value_uses_question_marks(self):
        self.assertEqual(widgets.gauge(None, 6, ascii_only=True), "[????]")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run widget tests and verify they fail**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_widgets -v
```

Expected: FAIL because `gauge` does not accept `ascii_only`.

- [ ] **Step 3: Update gauge implementation**

Modify `gauge` in `adaptor/tui/widgets.py`:

```python
def gauge(value: Optional[float], width: int, vmax: float = 100.0, ascii_only: bool = False) -> str:
    """Render a battery/progress bar for a 0..vmax value."""
    width = max(3, width)
    inner = width - 2
    if value is None:
        return "[" + "?" * inner + "]"
    frac = max(0.0, min(1.0, value / vmax)) if vmax else 0.0
    filled = int(round(frac * inner))
    full = "#" if ascii_only else "█"
    empty = "-" if ascii_only else "·"
    return "[" + full * filled + empty * (inner - filled) + "]"
```

- [ ] **Step 4: Detect ASCII mode in the app**

In `App.__init__`, add:

```python
        self.ascii_only = os.environ.get("ADAPTOR_TUI_ASCII", "").lower() in ("1", "true", "yes", "on")
```

In `_draw_dashboard`, change the battery gauge line:

```python
        bar = widgets.gauge(soc, min(24, max(8, width - 28)), ascii_only=self.ascii_only)
```

- [ ] **Step 5: Run widget tests**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_widgets -v
```

Expected: PASS.

- [ ] **Step 6: Commit gauge fallback**

```bash
git add adaptor/tui/widgets.py adaptor/tui/app.py adaptor/tests/test_tui_widgets.py
git commit -m "fix(tui): support ascii battery gauges"
```

## Task 6: Update Documentation

**Files:**
- Modify: `docs/guide/adaptor-tui.md`

- [ ] **Step 1: Update launch and status documentation**

Modify `docs/guide/adaptor-tui.md` sections as follows:

```markdown
## What the TUI Discovers

The TUI shows three kinds of adaptor runtime information:

- **systemd service:** `jibot-adapter.service` or `hexplorer-adapter.service` is installed and reported by `systemctl show`.
- **configured fleet:** if `adaptor/config/robots.toml` exists, each `[[robot]]` entry becomes a JIBOT row with its own serial/topic prefix.
- **manual process:** if a local `python main.py --id <serial>` process is running, the row shows `manual process` even when the systemd unit is not installed or inactive.

Live robot fields still come from MQTT. For a serial `<id>`, the TUI subscribes to:

```text
<vda_interface>/<vda_version>/<id>/connection
<vda_interface>/<vda_version>/<id>/state
```

If service/process status is visible but live state is blank, check the broker host/port and the serial/topic prefix printed in the Dashboard view.
```

Add this troubleshooting bullet:

```markdown
- **`manual process` on a card** → an adaptor is running outside systemd, usually from `run-main.sh`, `run-multi.sh`, or `python main.py --id ...`. Service buttons still target the configured systemd unit; stop the manual process from the terminal that launched it.
- **Battery bar shows question marks or broken glyphs** → run `ADAPTOR_TUI_ASCII=1 scripts/adaptor-tui.sh` to force ASCII gauges.
```

- [ ] **Step 2: Commit docs**

```bash
git add docs/guide/adaptor-tui.md
git commit -m "docs: explain tui adaptor discovery"
```

## Task 7: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused TUI tests**

Run:

```bash
cd adaptor
python -m unittest tests.test_tui_registry tests.test_tui_processes tests.test_tui_status tests.test_tui_widgets tests.test_tui_monitor tests.test_tui_systemd tests.test_tui_runner tests.test_tui_control tests.test_tui_configio -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run an import smoke test**

Run:

```bash
cd adaptor
python - <<'PY'
from config.config import get_config
from tui.registry import build_registry

config = get_config()
specs = build_registry(config)
print([(spec.key, spec.serial, spec.topic_prefix) for spec in specs])
PY
```

Expected: prints at least one JIBOT spec and one Hexplorer spec. If `adaptor/config/robots.toml` exists, prints one JIBOT spec per `[[robot]]` entry.

- [ ] **Step 3: Run the TUI in ASCII mode for visual sanity**

Run:

```bash
cd adaptor
ADAPTOR_TUI_ASCII=1 python -m tui --no-sudo
```

Expected:

- The left pane shows configured `robots.toml` JIBOT serials when `robots.toml` exists.
- A manually running `python main.py --id <serial>` process shows `manual process`.
- Battery gauge uses `#` and `-`, not block glyphs.
- Dashboard shows `serial` and `topic`.

- [ ] **Step 4: Check git diff**

Run:

```bash
git diff --check
git status --short
```

Expected:

- `git diff --check` exits 0.
- `git status --short` shows only the intended TUI, test, and docs changes.

