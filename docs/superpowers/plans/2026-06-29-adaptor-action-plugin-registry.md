# Adaptor Action Plugin Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hybrid instant-action plugin registry so custom actions can be registered without editing the main JIBOT adapter dispatch ladder.

**Architecture:** Keep existing built-in handlers in `adapter_jibot.py` unchanged and add a final registry delegation before the unsupported-action branch. `ActionRegistry.dispatch()` is a synchronous wrapper: immediate inline handlers finish in place, while async inline and subprocess handlers mark `RUNNING`, schedule work on the adapter event loop, and publish `FINISHED` or `FAILED` on completion.

**Tech Stack:** Python dataclasses, asyncio subprocess APIs, TOML config parsed by `config/config.py`, unittest/pytest.

---

### Task 1: Config Model For Action Specs

**Files:**
- Modify: `adaptor/config/config.py`
- Test: `adaptor/tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Add these tests to `adaptor/tests/test_config.py`:

```python
def test_action_plugin_config_defaults_empty():
    from config.config import ActionPluginConfig, Config

    assert ActionPluginConfig(action_type="customPing").runner == "inline"
    assert ActionPluginConfig(action_type="customPing").timeout_sec == 0.0
    assert ActionPluginConfig(action_type="customPing").motion is False
    assert ActionPluginConfig(action_type="customPing").snapshot_fields == []

    cfg = __import__("config.config", fromlist=["get_config"]).get_config()
    assert isinstance(cfg.actions, list)
    assert cfg.actions == []


def test_action_plugin_config_loads_actions_table(tmp_path):
    from config.config import get_config

    config_text = open("config/config.toml", encoding="utf-8").read()
    config_text += """

[[actions]]
action_type = "customDoorOpen"
runner = "subprocess"
command = ["python", "actions/custom_door_open.py"]
timeout_sec = 10
motion = true
snapshot_fields = ["vehicle.vehicle_ip"]
"""
    path = tmp_path / "config.toml"
    path.write_text(config_text, encoding="utf-8")

    cfg = get_config(config_path=path)
    action = cfg.actions[0]
    assert action.action_type == "customDoorOpen"
    assert action.runner == "subprocess"
    assert action.command == ["python", "actions/custom_door_open.py"]
    assert action.timeout_sec == 10
    assert action.motion is True
    assert action.snapshot_fields == ["vehicle.vehicle_ip"]
```

- [ ] **Step 2: Run config tests and verify RED**

Run: `cd adaptor && python -m pytest tests/test_config.py::test_action_plugin_config_defaults_empty tests/test_config.py::test_action_plugin_config_loads_actions_table -q`

Expected: FAIL because `ActionPluginConfig` and `Config.actions` do not exist.

- [ ] **Step 3: Implement config dataclass and parser**

In `adaptor/config/config.py`, add:

```python
@dataclass
class ActionPluginConfig:
    action_type: str
    runner: str = "inline"
    module: Optional[str] = None
    command: List[str] = field(default_factory=list)
    timeout_sec: float = 0.0
    motion: bool = False
    snapshot_fields: List[str] = field(default_factory=list)
```

Add `actions: List[ActionPluginConfig] = field(default_factory=list)` to `Config`, parse `actions = [ActionPluginConfig(**item) for item in config_dict.get("actions", [])]`, and pass `actions=actions` into `Config(...)`.

- [ ] **Step 4: Run config tests and verify GREEN**

Run: `cd adaptor && python -m pytest tests/test_config.py::test_action_plugin_config_defaults_empty tests/test_config.py::test_action_plugin_config_loads_actions_table -q`

Expected: PASS.

### Task 2: Action Registry Core

**Files:**
- Create: `adaptor/core/action_registry.py`
- Test: `adaptor/tests/test_action_registry.py`

- [ ] **Step 1: Write failing registry tests**

Create `adaptor/tests/test_action_registry.py` with tests for duplicate registration, inline success/failure, subprocess success/failure/timeout, RUNNING terminal rejection, snapshot whitelist, and scheduled dispatch.

- [ ] **Step 2: Run registry tests and verify RED**

Run: `cd adaptor && python -m pytest tests/test_action_registry.py -q`

Expected: FAIL because `core.action_registry` does not exist.

- [ ] **Step 3: Implement registry core**

Create `adaptor/core/action_registry.py` with `ActionResult`, `ActionSpec`, `InlineActionContext`, `SubprocessActionContext`, `ActionRegistry`, `build_registry_from_config`, `build_subprocess_payload`, `InlineRunner`, and `SubprocessRunner`.

Required behavior:
- `ActionResult.status` accepts only terminal statuses: `FINISHED` or `FAILED`.
- `ActionRegistry.dispatch(action, adapter)` is sync.
- Sync inline handlers return and update terminal status immediately.
- Async inline and subprocess handlers set `RUNNING`, schedule via `adapter._run_on_adapter_loop(...)`, and later set terminal status.
- Subprocess discovery uses only config command/module metadata; subprocess commands are never imported.
- Default subprocess snapshot includes only `serial_number`, `simulation`, `current_map_id`, `last_node_id`, `last_node_sequence_id`, `pose`, `order_id`, `paused`, and `work_in_progress`.

- [ ] **Step 4: Run registry tests and verify GREEN**

Run: `cd adaptor && python -m pytest tests/test_action_registry.py -q`

Expected: PASS.

### Task 3: Adapter And WebUI Integration

**Files:**
- Modify: `adaptor/adapter_jibot.py`
- Modify: `adaptor/core/registry.py`
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`
- Test: `adaptor/tests/test_registry.py`

- [ ] **Step 1: Write failing integration tests**

Add tests that:
- A custom non-built-in instant action dispatches through the registry and reaches `FINISHED`.
- A custom `motion=true` action is blocked while `_work_in_progress` is active.
- WebUI registry exposes configured custom actions with the configured motion flag.

- [ ] **Step 2: Run integration tests and verify RED**

Run: `cd adaptor && python -m pytest tests/test_action_registry.py tests/test_registry.py tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_custom_registry_instant_action_finishes tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_custom_registry_motion_action_blocked_while_busy -q`

Expected: FAIL because adapter and WebUI do not yet use the new registry.

- [ ] **Step 3: Wire adapter dispatch**

In `Adapter.__init__`, create `self._action_registry = build_registry_from_config(self.config.actions)`.

In `_is_motion_instant_action`, include `self._action_registry.is_motion(action.action_type)`.

In `instant_actions_accept_procedure`, add a registry branch immediately before the unsupported-action branch:

```python
elif self._action_registry.has(action.action_type):
    self._action_registry.dispatch(action, self)
```

- [ ] **Step 4: Wire WebUI registry**

In `adaptor/core/registry.py`, append configured actions to `_JIBOT_INSTANT_ACTIONS` when building a JIBOT spec:

```python
instant_actions=tuple(_JIBOT_INSTANT_ACTIONS) + tuple(
    InstantAction(a.action_type, a.action_type, motion=a.motion)
    for a in getattr(config, "actions", [])
)
```

- [ ] **Step 5: Run integration tests and verify GREEN**

Run: `cd adaptor && python -m pytest tests/test_action_registry.py tests/test_registry.py tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_custom_registry_instant_action_finishes tests/test_adapter_jibot_v3_order.py::AdapterV3OrderTest::test_custom_registry_motion_action_blocked_while_busy -q`

Expected: PASS.

### Task 4: Focused Regression

**Files:**
- No production changes unless tests reveal a regression.

- [ ] **Step 1: Run focused tests**

Run: `cd adaptor && python -m pytest tests/test_action_registry.py tests/test_config.py tests/test_registry.py -q`

Expected: PASS.

- [ ] **Step 2: Run JIBOT instant-action regression subset**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -q`

Expected: PASS.

- [ ] **Step 3: Check diff**

Run: `git diff -- adaptor/core/action_registry.py adaptor/config/config.py adaptor/core/registry.py adaptor/adapter_jibot.py adaptor/tests/test_action_registry.py adaptor/tests/test_config.py adaptor/tests/test_registry.py adaptor/tests/test_adapter_jibot_v3_order.py docs/superpowers/plans/2026-06-29-adaptor-action-plugin-registry.md`

Expected: only action-registry implementation, tests, and this plan.
