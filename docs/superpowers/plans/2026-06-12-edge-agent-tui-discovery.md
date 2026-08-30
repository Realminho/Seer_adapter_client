# Edge Agent TUI Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show discovered Dobot edge-agent systemd units in the existing AMR adaptor TUI, including multiple `edge-agent@<id>.service` instances and the legacy `edge-agent.service` fallback.

**Architecture:** Add pure systemd unit discovery helpers, convert discovered edge-agent units into inert non-VDA5050 `AdaptorSpec` entries, and teach the app to render/control specs without an MQTT monitor. Keep service metrics, logs, lifecycle controls, and diagnostics on the existing TUI paths.

**Tech Stack:** Python stdlib, `unittest`, existing curses TUI modules under `adaptor/tui`.

---

## File Structure

- Modify `adaptor/tui/systemd.py`: add pure unit-name parsing and best-effort `systemctl list-units` / `list-unit-files` discovery.
- Modify `adaptor/tests/test_tui_systemd.py`: cover parsing, deduplication, fallback unit, and discovery error handling.
- Modify `adaptor/tui/registry.py`: add `monitor_kind`, edge-agent checkout/diagnostic helpers, and edge-agent specs.
- Create `adaptor/tests/test_tui_registry.py`: cover registry behavior without requiring real systemd.
- Modify `adaptor/tui/app.py`: skip MQTT monitor setup for unmonitored specs and render placeholders safely.
- Modify `adaptor/tests/test_tui_app.py`: cover monitor setup and placeholder helpers.

## Task 1: Systemd Edge-Agent Unit Discovery

**Files:**
- Modify: `adaptor/tui/systemd.py`
- Test: `adaptor/tests/test_tui_systemd.py`

- [ ] **Step 1: Write failing parser and discovery tests**

Append to `adaptor/tests/test_tui_systemd.py`:

```python
from unittest.mock import patch
import subprocess


class EdgeAgentUnitParsingTest(unittest.TestCase):
    def test_parse_template_instance(self):
        unit = systemd.parse_edge_agent_unit("edge-agent@cell-a.service")

        self.assertEqual(unit, systemd.EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service"))

    def test_parse_non_template_fallback(self):
        unit = systemd.parse_edge_agent_unit("edge-agent.service")

        self.assertEqual(unit, systemd.EdgeAgentUnit(id="default", unit="edge-agent.service"))

    def test_parse_ignores_template_and_malformed_names(self):
        self.assertIsNone(systemd.parse_edge_agent_unit("edge-agent@.service"))
        self.assertIsNone(systemd.parse_edge_agent_unit("edge-agent@bad"))
        self.assertIsNone(systemd.parse_edge_agent_unit("other@cell.service"))

    def test_parse_unit_listing_deduplicates_in_order(self):
        text = """edge-agent@cell-b.service loaded active running Edge Agent B
edge-agent.service loaded inactive dead Edge Agent
edge-agent@cell-b.service enabled
edge-agent@.service disabled
bad.service enabled
"""
        units = systemd.parse_edge_agent_units(text)

        self.assertEqual(
            units,
            [
                systemd.EdgeAgentUnit(id="cell-b", unit="edge-agent@cell-b.service"),
                systemd.EdgeAgentUnit(id="default", unit="edge-agent.service"),
            ],
        )


class EdgeAgentDiscoveryTest(unittest.TestCase):
    def test_discovery_merges_list_units_and_unit_files(self):
        calls = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="edge-agent@cell-a.service loaded active running Edge Agent A\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="edge-agent@cell-b.service enabled\nedge-agent.service enabled\n",
                stderr="",
            ),
        ]

        with patch("tui.systemd.subprocess.run", side_effect=calls):
            units = systemd.discover_edge_agent_units(timeout=1.0)

        self.assertEqual(
            units,
            [
                systemd.EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service"),
                systemd.EdgeAgentUnit(id="cell-b", unit="edge-agent@cell-b.service"),
                systemd.EdgeAgentUnit(id="default", unit="edge-agent.service"),
            ],
        )

    def test_discovery_returns_empty_when_systemctl_missing_or_fails(self):
        with patch("tui.systemd.shutil.which", return_value=None):
            self.assertEqual(systemd.discover_edge_agent_units(), [])

        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with patch("tui.systemd.subprocess.run", return_value=failed):
            self.assertEqual(systemd.discover_edge_agent_units(), [])

        with patch("tui.systemd.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["systemctl"], timeout=1.0)):
            self.assertEqual(systemd.discover_edge_agent_units(timeout=1.0), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=adaptor python -m unittest adaptor.tests.test_tui_systemd -v`

Expected: FAIL with missing `parse_edge_agent_unit`, `EdgeAgentUnit`, or `discover_edge_agent_units`.

- [ ] **Step 3: Implement systemd helpers**

Add to `adaptor/tui/systemd.py` after `ServiceMetrics`:

```python
@dataclass(frozen=True)
class EdgeAgentUnit:
    id: str
    unit: str


def parse_edge_agent_unit(name: str) -> Optional[EdgeAgentUnit]:
    unit = name.strip().split(None, 1)[0]
    if unit == "edge-agent.service":
        return EdgeAgentUnit(id="default", unit=unit)
    prefix = "edge-agent@"
    suffix = ".service"
    if not unit.startswith(prefix) or not unit.endswith(suffix):
        return None
    instance = unit[len(prefix) : -len(suffix)]
    if not instance:
        return None
    return EdgeAgentUnit(id=instance, unit=unit)


def parse_edge_agent_units(text: str) -> List[EdgeAgentUnit]:
    result: List[EdgeAgentUnit] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        parsed = parse_edge_agent_unit(raw_line)
        if parsed is None or parsed.unit in seen:
            continue
        seen.add(parsed.unit)
        result.append(parsed)
    return result


def discover_edge_agent_units(timeout: float = 3.0) -> List[EdgeAgentUnit]:
    if shutil.which("systemctl") is None:
        return []
    commands = (
        [
            "systemctl",
            "list-units",
            "edge-agent@*.service",
            "edge-agent.service",
            "--all",
            "--no-legend",
            "--no-pager",
        ],
        [
            "systemctl",
            "list-unit-files",
            "edge-agent@*.service",
            "edge-agent.service",
            "--no-legend",
            "--no-pager",
        ],
    )
    result: List[EdgeAgentUnit] = []
    seen: set[str] = set()
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            continue
        for unit in parse_edge_agent_units(proc.stdout):
            if unit.unit in seen:
                continue
            seen.add(unit.unit)
            result.append(unit)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=adaptor python -m unittest adaptor.tests.test_tui_systemd -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add adaptor/tui/systemd.py adaptor/tests/test_tui_systemd.py
git commit -m "feat: discover edge-agent systemd units"
```

## Task 2: Edge-Agent Registry Specs and Diagnostics

**Files:**
- Modify: `adaptor/tui/registry.py`
- Create: `adaptor/tests/test_tui_registry.py`

- [ ] **Step 1: Write failing registry tests**

Create `adaptor/tests/test_tui_registry.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tui import registry
from tui.systemd import EdgeAgentUnit


class EdgeAgentRegistryTest(unittest.TestCase):
    def test_make_edge_agent_spec_for_template_instance(self):
        spec = registry.make_edge_agent_spec(
            EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service"),
            edge_root=Path("/tmp/missing-edge-agent"),
        )

        self.assertEqual(spec.key, "edge-agent:cell-a")
        self.assertEqual(spec.display_name, "Edge Agent cell-a")
        self.assertEqual(spec.unit, "edge-agent@cell-a.service")
        self.assertEqual(spec.exec_script, "")
        self.assertEqual(spec.manufacturer, "dobot")
        self.assertEqual(spec.serial, "cell-a")
        self.assertEqual(spec.vda_full_version, "")
        self.assertEqual(spec.topic_prefix, "")
        self.assertEqual(spec.monitor_kind, "none")
        self.assertEqual(spec.instant_actions, ())
        self.assertEqual(spec.runnable, ())

    def test_make_edge_agent_spec_for_non_template_unit(self):
        spec = registry.make_edge_agent_spec(
            EdgeAgentUnit(id="default", unit="edge-agent.service"),
            edge_root=Path("/tmp/missing-edge-agent"),
        )

        self.assertEqual(spec.key, "edge-agent")
        self.assertEqual(spec.display_name, "Edge Agent")
        self.assertEqual(spec.serial, "edge-agent")

    def test_edge_agent_diagnostics_prefer_local_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / ".venv" / "bin"
            bin_dir.mkdir(parents=True)
            pytest = bin_dir / "pytest"
            ruff = bin_dir / "ruff"
            pytest.write_text("#!/bin/sh\n", encoding="utf-8")
            ruff.write_text("#!/bin/sh\n", encoding="utf-8")

            spec = registry.make_edge_agent_spec(
                EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service"),
                edge_root=root,
            )

        self.assertEqual(spec.runnable[0].argv[0], str(pytest))
        self.assertEqual(spec.runnable[1].argv[0], str(pytest))
        self.assertEqual(spec.runnable[2].argv[0], str(ruff))

    def test_build_registry_appends_discovered_edge_agents(self):
        class Vehicle:
            serial_number = "HN"
            vda_full_version = "3.0.0"
            vda_version = "v3"

        class Broker:
            vda_interface = "uagv"
            host = "127.0.0.1"
            port = 1883

        class Config:
            vehicle = Vehicle()
            mqtt_broker = Broker()

        units = [EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service")]
        with patch("tui.registry._jibot_specs", return_value=[]), patch(
            "tui.registry.discover_edge_agent_units", return_value=units
        ):
            specs = registry.build_registry(Config())

        self.assertEqual([spec.key for spec in specs], ["hexplorer", "edge-agent:cell-a"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=adaptor python -m unittest adaptor.tests.test_tui_registry -v`

Expected: FAIL because `make_edge_agent_spec` or `monitor_kind` does not exist.

- [ ] **Step 3: Implement registry support**

Modify `adaptor/tui/registry.py`:

```python
from tui.systemd import EdgeAgentUnit, discover_edge_agent_units
```

Add `monitor_kind` to `AdaptorSpec` after `runtime_kind`:

```python
    monitor_kind: str = "vda5050"
```

Add below `_JIBOT_INSTANT_ACTIONS`:

```python
EDGE_AGENT_ROOT = ADAPTER_ROOT.parents[1] / "dobot" / "edge-agent"


def _edge_tool(edge_root: Path, name: str) -> str:
    candidate = edge_root / ".venv" / "bin" / name
    if candidate.exists():
        return str(candidate)
    return name


def _edge_agent_diagnostics(edge_root: Path) -> Tuple[Diagnostic, ...]:
    if not edge_root.exists():
        return ()
    pytest = _edge_tool(edge_root, "pytest")
    ruff = _edge_tool(edge_root, "ruff")
    return (
        Diagnostic(
            "edge-agent: core/local API tests",
            (pytest, "tests/test_agent.py", "tests/test_local_api.py"),
        ),
        Diagnostic(
            "edge-agent: sparkplug connector tests",
            (pytest, "tests/test_sparkplug_connector.py"),
        ),
        Diagnostic("edge-agent: ruff", (ruff, "check", ".")),
    )


def make_edge_agent_spec(unit: EdgeAgentUnit, edge_root: Path = EDGE_AGENT_ROOT) -> AdaptorSpec:
    is_default = unit.unit == "edge-agent.service"
    key = "edge-agent" if is_default else f"edge-agent:{unit.id}"
    display = "Edge Agent" if is_default else f"Edge Agent {unit.id}"
    serial = "edge-agent" if is_default else unit.id
    return AdaptorSpec(
        key=key,
        display_name=display,
        unit=unit.unit,
        workdir=edge_root,
        exec_script="",
        manufacturer="dobot",
        serial=serial,
        vda_full_version="",
        topic_prefix="",
        runtime_kind="systemd",
        monitor_kind="none",
        diagnostics=_edge_agent_diagnostics(edge_root),
    )
```

In `build_registry`, before `return`, add:

```python
    edge_agents = [make_edge_agent_spec(unit) for unit in discover_edge_agent_units()]

    return [*jibot_specs, hexplorer, *edge_agents]
```

and remove the old `return [*jibot_specs, hexplorer]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=adaptor python -m unittest adaptor.tests.test_tui_registry -v`

Expected: PASS.

- [ ] **Step 5: Run existing registry import smoke**

Run: `PYTHONPATH=adaptor python -m unittest adaptor.tests.test_tui_app adaptor.tests.test_tui_processes -v`

Expected: PASS or only failures that Task 3 is expected to fix for unmonitored specs.

- [ ] **Step 6: Commit**

```bash
git add adaptor/tui/registry.py adaptor/tests/test_tui_registry.py
git commit -m "feat: add edge-agent tui registry specs"
```

## Task 3: App Support for Unmonitored Specs

**Files:**
- Modify: `adaptor/tui/app.py`
- Modify: `adaptor/tests/test_tui_app.py`

- [ ] **Step 1: Write failing helper tests**

Append to `adaptor/tests/test_tui_app.py`:

```python
from pathlib import Path
from tui.registry import AdaptorSpec


class MonitorKindTest(unittest.TestCase):
    def test_spec_monitoring_is_disabled_for_none_kind(self):
        spec = AdaptorSpec(
            key="edge-agent:cell-a",
            display_name="Edge Agent cell-a",
            unit="edge-agent@cell-a.service",
            workdir=Path("/tmp/edge-agent"),
            exec_script="",
            manufacturer="dobot",
            serial="cell-a",
            vda_full_version="",
            topic_prefix="",
            monitor_kind="none",
        )

        self.assertFalse(app._uses_mqtt_monitor(spec))
        self.assertEqual(app._connection_label_for_spec(spec, None), "not monitored")
        self.assertEqual(app._process_live_summary_for_spec(spec, None), "service metrics only")

    def test_vda5050_specs_keep_existing_summary(self):
        spec = AdaptorSpec(
            key="jibot",
            display_name="JIBOT",
            unit="jibot-adapter.service",
            workdir=Path("/tmp/adaptor"),
            exec_script="run-main.sh",
            manufacturer="jibot",
            serial="HN",
            vda_full_version="3.0.0",
            topic_prefix="uagv/v3/HN",
        )
        snap = StateSnapshot(x=1.2, y=3.4, order_id="ORD", battery_soc=50)

        self.assertTrue(app._uses_mqtt_monitor(spec))
        self.assertEqual(app._connection_label_for_spec(spec, snap), "—")
        self.assertEqual(app._process_live_summary_for_spec(spec, snap), "pos 1.2,3.4  ord ORD  bat 50%")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=adaptor python -m unittest adaptor.tests.test_tui_app -v`

Expected: FAIL because `_uses_mqtt_monitor`, `_connection_label_for_spec`, or `_process_live_summary_for_spec` does not exist.

- [ ] **Step 3: Implement helpers and App guards**

Add to `adaptor/tui/app.py` near `_uses_systemd_metrics`:

```python
def _uses_mqtt_monitor(spec: AdaptorSpec) -> bool:
    return spec.monitor_kind == "vda5050" and bool(spec.topic_prefix)


def _empty_snapshot_for_unmonitored() -> StateSnapshot:
    return StateSnapshot()


def _snapshot_for_spec(monitors: Dict[str, MqttMonitor], spec: AdaptorSpec) -> StateSnapshot:
    if not _uses_mqtt_monitor(spec):
        return _empty_snapshot_for_unmonitored()
    monitor = monitors.get(spec.key)
    if monitor is None:
        return _empty_snapshot_for_unmonitored()
    return monitor.get_snapshot()


def _connection_label_for_spec(spec: AdaptorSpec, snap: Optional[StateSnapshot]) -> str:
    if not _uses_mqtt_monitor(spec):
        return "not monitored"
    if snap is None:
        return "—"
    return snap.connection_state or "—"


def _process_live_summary_for_spec(spec: AdaptorSpec, snap: Optional[StateSnapshot]) -> str:
    if not _uses_mqtt_monitor(spec):
        return "service metrics only"
    return _process_live_summary(snap or StateSnapshot())
```

In `App.__init__`, change:

```python
        for spec in specs:
            self._ensure_monitor_for_spec(spec)
```

to:

```python
        for spec in specs:
            if _uses_mqtt_monitor(spec):
                self._ensure_monitor_for_spec(spec)
```

In `monitor` property, guard:

```python
    @property
    def monitor(self) -> MqttMonitor:
        monitor = self.spec_monitor.get(self.spec.key)
        if monitor is None:
            raise RuntimeError(f"{self.spec.key} has no MQTT monitor")
        return monitor
```

In `_draw_process_list`, replace:

```python
            snap = self.spec_monitor[spec.key].get_snapshot()
```

with:

```python
            snap = _snapshot_for_spec(self.spec_monitor, spec)
```

and replace the `conn` and `live` assignments with:

```python
            conn = _connection_label_for_spec(spec, snap)
            live = _process_live_summary_for_spec(spec, snap)
```

In `_draw_dashboard`, replace:

```python
        snap = self.monitor.get_snapshot()
```

with:

```python
        snap = _snapshot_for_spec(self.spec_monitor, spec)
```

Before the current MQTT availability block under `LIVE STATE (MQTT)`, add:

```python
        if not _uses_mqtt_monitor(spec):
            field("monitor", "not monitored in this pass", color(widgets.CP_DIM))
            field("protocol", "service/log/control only")
            return
```

In `_control_items`, only append instant actions when the selected spec has them:

```python
        for action in self.spec.instant_actions:
```

No extra guard is needed once edge-agent specs use `instant_actions=()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=adaptor python -m unittest adaptor.tests.test_tui_app -v`

Expected: PASS.

- [ ] **Step 5: Run focused TUI tests**

Run: `PYTHONPATH=adaptor python -m unittest adaptor.tests.test_tui_app adaptor.tests.test_tui_processes adaptor.tests.test_tui_status -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adaptor/tui/app.py adaptor/tests/test_tui_app.py
git commit -m "fix: render unmonitored tui specs safely"
```

## Task 4: Full TUI Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Run TUI unit test subset**

Run:

```bash
PYTHONPATH=adaptor python -m unittest \
  adaptor.tests.test_tui_systemd \
  adaptor.tests.test_tui_registry \
  adaptor.tests.test_tui_app \
  adaptor.tests.test_tui_processes \
  adaptor.tests.test_tui_status \
  adaptor.tests.test_tui_runner \
  adaptor.tests.test_tui_control \
  adaptor.tests.test_tui_configio \
  adaptor.tests.test_tui_widgets \
  -v
```

Expected: PASS.

- [ ] **Step 2: Check diff for unrelated files**

Run: `git diff --stat HEAD`

Expected: only TUI files and tests from this plan, plus the committed plan/spec docs if not already committed.

- [ ] **Step 3: Commit plan file if still uncommitted**

```bash
git add docs/superpowers/plans/2026-06-12-edge-agent-tui-discovery.md
git commit -m "docs: plan edge-agent tui discovery"
```

Skip this step if the plan file was already committed before implementation.

## Self-Review

- Spec coverage: template units, non-template fallback, required `AdaptorSpec` fields, unmonitored rendering, diagnostics, and best-effort discovery are each mapped to a task.
- Placeholder scan: no unfinished placeholder markers remain.
- Type consistency: `monitor_kind`, `EdgeAgentUnit`, `make_edge_agent_spec`, and helper names are introduced before use.
