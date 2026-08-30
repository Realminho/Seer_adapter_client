# Action Module Panels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group related VDA5050 actions plus an optional dedicated WebUi panel into one Python package ("action module"), so PIO/clamp and trusted custom modules share one contract and their `panel.html` drives the detail dashboard.

**Architecture:** A single pure discovery function in `core/` imports the default first-party modules (`extensions.clamp/pio/ezio`) and any enabled `[[action_modules]]`, validates their `action_specs()`, and reads each package's optional `panel.html`. The adapter feeds the discovered specs into the existing `ActionRegistry`; the WebUi feeds the same specs into `AdaptorSpec` and renders each module's `panel.html` (or falls back to generic action cards). Panel POSTs reuse the existing `/adapter/{key}/action` route, CSRF, and whitelist unchanged.

**Tech Stack:** Python 3.11+, stdlib only (`string.Template`, `importlib.resources`, `importlib`), `dataclasses`. WebUi is a stdlib `http.server` app that builds HTML with f-strings. Tests use `pytest`.

## Global Constraints

- **Python floor:** 3.11+ (repo `.venv`). No syntax below 3.11.
- **Stdlib only in WebUi and discovery:** no new third-party deps. Template = `string.Template`; resource read = `importlib.resources`.
- **Import-light module `__init__.py`:** a module package's `__init__.py` MUST NOT top-level import ROS, `serial`, `utils.ezi_motor`, or other hardware libs. Hardware access happens only when a handler runs (via `ctx.adapter`). `clamp.py`/`pio.py` already satisfy this; keep it so after the package move.
- **Whitelist preserved:** `AdaptorSpec.instant_actions` is the `/action` whitelist (`server.py:716`). Module action types MUST remain in `instant_actions` or their POSTs get rejected as `unknown`. Module types are only *excluded from generic card rendering*, never from the whitelist.
- **No `/action` contract break:** the only server change permitted is honoring the already-`_reserved` `return_to` field (Task 13). CSRF, whitelist, and the `_CONFIRM_REQUIRED_ACTIONS` confirm gate are unchanged.
- **Test invocation:** run from the `adaptor/` directory with `PYTHONPATH=.` to bypass ROS launch plugins: `cd adaptor && PYTHONPATH=. python -m pytest <path> -v`.
- **Commit style:** small commits per task.
- **UI copy:** existing dashboard copy is Korean; keep panel labels/descriptions consistent with the current `_ACTION_META` tone.

## File Structure

Created:
- `adaptor/core/action_modules.py` — the shared discovery function `discover_action_modules(config)`, `first_party_action_specs(config)`, `DiscoveredModule`, `default_module_names()`.
- `adaptor/extensions/clamp/__init__.py` — clamp module (moved from `extensions/clamp.py`).
- `adaptor/extensions/clamp/panel.html` — clamp command panel fragment.
- `adaptor/extensions/pio/__init__.py` — PIO module (moved from `extensions/pio.py`).
- `adaptor/extensions/pio/panel.html` — PIO command panel fragment.
- `adaptor/extensions/ezio/__init__.py` — EZIO module (moved from `extensions/ezio.py`; no panel).
- `adaptor/tests/test_action_modules.py` — discovery unit tests.
- `adaptor/tests/test_action_module_panels_render.py` — WebUi panel render tests.

Modified:
- `adaptor/core/action_registry.py` — add `ActionSpec.label`.
- `adaptor/config/config.py` — add `ActionModuleConfig`, `Config.action_modules`, TOML parse.
- `adaptor/adapter_jibot.py` — build `first_party_specs` from discovery.
- `adaptor/core/registry.py` — `ActionModuleView`, `AdaptorSpec.action_modules`, populate in `_jibot_spec`, drop clamp from `_JIBOT_INSTANT_ACTIONS`.
- `adaptor/web/render.py` — `_action_module_panel()`, module loop in `_control_forms`, remove hardcoded clamp group helpers.
- `adaptor/web/server.py` — honor `return_to` in `_post_action`.
- `adaptor/config/config.toml` — documented `[[action_modules]]` example.

---

### Task 1: Add `label` field to `ActionSpec`

**Files:**
- Modify: `adaptor/core/action_registry.py:48-58`
- Test: `adaptor/tests/test_action_registry.py`

**Interfaces:**
- Produces: `ActionSpec(action_type=..., handler=..., motion=..., label="...")` — new optional `label: str = ""`. Consumed by discovery (Task 5) and the WebUi view (Task 9) as the fallback card label.

- [ ] **Step 1: Write the failing test**

Add to `adaptor/tests/test_action_registry.py`:

```python
def test_action_spec_label_defaults_empty_and_roundtrips():
    from core.action_registry import ActionSpec

    assert ActionSpec(action_type="x").label == ""
    assert ActionSpec(action_type="x", label="Air shower start").label == "Air shower start"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_registry.py::test_action_spec_label_defaults_empty_and_roundtrips -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'label'`

- [ ] **Step 3: Add the field**

In `adaptor/core/action_registry.py`, add `label` to the frozen dataclass (append after `snapshot_fields` so no positional call breaks):

```python
@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    enabled: bool = True
    runner: str = "inline"
    handler: Optional[Callable[[Any], Any]] = None
    module: Optional[str] = None
    command: List[str] = field(default_factory=list)
    timeout_sec: float = 0.0
    motion: bool = False
    snapshot_fields: List[str] = field(default_factory=list)
    label: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_registry.py -v`
Expected: PASS (all existing action-registry tests still green)

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/action_registry.py adaptor/tests/test_action_registry.py
git commit -m "feat(actions): add optional label to ActionSpec for fallback cards"
```

---

### Task 2: Add `ActionModuleConfig` and `Config.action_modules`

**Files:**
- Modify: `adaptor/config/config.py:428-437` (add dataclass), `:456-479` (add field), `:573-601` (parse)
- Test: `adaptor/tests/test_config.py`

**Interfaces:**
- Produces: `ActionModuleConfig(module: str, enabled: bool = True)` and `Config.action_modules: List[ActionModuleConfig]`. Consumed by discovery (Task 3).

- [ ] **Step 1: Write the failing test**

Add to `adaptor/tests/test_config.py`:

```python
def test_action_modules_parsed_from_toml(tmp_path):
    from config.config import get_config

    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text(
        '[[action_modules]]\n'
        'module = "custom_actions.air_shower"\n'
        'enabled = true\n'
        '[[action_modules]]\n'
        'module = "extensions.clamp"\n'
        'enabled = false\n'
    )
    cfg = get_config(config_path=str(cfg_file))
    mods = {m.module: m.enabled for m in cfg.action_modules}
    assert mods["custom_actions.air_shower"] is True
    assert mods["extensions.clamp"] is False


def test_action_modules_default_empty(tmp_path):
    from config.config import get_config

    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text("")
    cfg = get_config(config_path=str(cfg_file))
    assert cfg.action_modules == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_config.py::test_action_modules_default_empty -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'action_modules'`

- [ ] **Step 3: Add the dataclass, field, and parse**

In `adaptor/config/config.py`, after `ActionPluginConfig` (line ~437) add:

```python
@dataclass
class ActionModuleConfig:
    module: str
    enabled: bool = True
```

In the `Config` dataclass (after `actions: List[ActionPluginConfig] = field(default_factory=list)`, line ~479) add:

```python
    action_modules: List["ActionModuleConfig"] = field(default_factory=list)
```

In the parser, after the `actions = [...]` block (line ~576) add:

```python
    action_modules = [
        ActionModuleConfig(**module)
        for module in config_dict.get("action_modules", [])
    ]
```

In the `return Config(...)` call (after `actions=actions,`, line ~601) add:

```python
        action_modules=action_modules,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_config.py -k action_modules -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/config/config.py adaptor/tests/test_config.py
git commit -m "feat(config): add [[action_modules]] config schema"
```

---

### Task 3: Discovery core — import modules and collect specs

**Files:**
- Create: `adaptor/core/action_modules.py`
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Produces:
  - `DiscoveredModule` — frozen dataclass with `module: str`, `title: str`, `specs: tuple[ActionSpec, ...]`, `panel_suppressed: bool = False`, `panel_template: Optional[str] = None`.
  - `default_module_names() -> tuple[str, ...]` → `("extensions.clamp", "extensions.pio", "extensions.ezio")`.
  - `discover_action_modules(config, *, default_modules=None, reserved_types=None) -> tuple[DiscoveredModule, ...]`.
- Consumed by: adapter (Task 8), WebUi `_jibot_spec` (Task 9). Tests inject `default_modules` to avoid depending on the real extensions until Tasks 5-7.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_action_modules.py`:

```python
import sys
import textwrap
import importlib

import pytest


def _write_module(tmp_path, name, body):
    """Write a single-file module `name` under tmp_path and put it on sys.path."""
    pkg_root = tmp_path / "modroot"
    pkg_root.mkdir(exist_ok=True)
    (pkg_root / f"{name}.py").write_text(textwrap.dedent(body))
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))
    importlib.invalidate_caches()


class _Cfg:
    def __init__(self, actions=(), action_modules=()):
        self.actions = list(actions)
        self.action_modules = list(action_modules)


def test_discovers_module_and_collects_specs(tmp_path):
    from core.action_modules import discover_action_modules

    _write_module(tmp_path, "good_mod", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (
                ActionSpec(action_type="airOn", label="Air on"),
                ActionSpec(action_type="airOff", label="Air off"),
            )
    """)
    mods = discover_action_modules(
        _Cfg(), default_modules=("good_mod",), reserved_types=set()
    )
    assert len(mods) == 1
    assert mods[0].module == "good_mod"
    assert {s.action_type for s in mods[0].specs} == {"airOn", "airOff"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py::test_discovers_module_and_collects_specs -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.action_modules'`

- [ ] **Step 3: Write minimal implementation**

Create `adaptor/core/action_modules.py`:

```python
"""Shared discovery of action-module packages for the adapter and WebUi.

An action module is a Python package exposing ``action_specs() -> Iterable[ActionSpec]``
and an optional ``panel.html`` resource. First-party PIO/clamp/ezio and trusted
custom ``[[action_modules]]`` share this one contract. The function is pure over
``config`` so the adapter and WebUi (separate processes) reach the same verdict.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Tuple

from core.action_registry import ActionSpec, _builtin_instant_action_types


@dataclass(frozen=True)
class DiscoveredModule:
    module: str
    title: str
    specs: Tuple[ActionSpec, ...]
    panel_suppressed: bool = False
    panel_template: Optional[str] = None


def default_module_names() -> Tuple[str, ...]:
    return ("extensions.clamp", "extensions.pio", "extensions.ezio")


def _module_title(module: Any, module_name: str) -> str:
    return getattr(module, "MODULE_TITLE", module_name.rsplit(".", 1)[-1])


def _module_names(config, default_modules) -> Tuple[str, ...]:
    defaults = default_module_names() if default_modules is None else tuple(default_modules)
    overrides = {
        m.module: bool(getattr(m, "enabled", True))
        for m in getattr(config, "action_modules", [])
    }
    names = [n for n in defaults if overrides.get(n, True)]
    for m in getattr(config, "action_modules", []):
        if bool(getattr(m, "enabled", True)) and m.module not in names:
            names.append(m.module)
    return tuple(names)


def discover_action_modules(config, *, default_modules=None, reserved_types=None):
    names = _module_names(config, default_modules)
    discovered = []
    for name in names:
        try:
            module = importlib.import_module(name)
            specs = tuple(module.action_specs())
        except Exception as exc:  # import-light contract failed or bad module
            print(f"[ACTION MODULE SKIP] {name}: {exc}")
            continue
        if not specs or any(not isinstance(s, ActionSpec) for s in specs):
            print(f"[ACTION MODULE SKIP] {name}: action_specs() returned no valid ActionSpec")
            continue
        discovered.append(
            DiscoveredModule(
                module=name,
                title=_module_title(module, name),
                specs=specs,
            )
        )
    return tuple(discovered)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/action_modules.py adaptor/tests/test_action_modules.py
git commit -m "feat(actions): add action-module discovery core"
```

---

### Task 4: Discovery validation — dedup, reserved types, import failure

**Files:**
- Modify: `adaptor/core/action_modules.py`
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Consumes: `DiscoveredModule`, `discover_action_modules` from Task 3.
- Produces: same signature; adds cross-module de-duplication and reserved-built-in protection, both all-or-nothing per module.

- [ ] **Step 1: Write the failing tests**

Add to `adaptor/tests/test_action_modules.py`:

```python
def test_duplicate_type_across_modules_rejects_second(tmp_path):
    from core.action_modules import discover_action_modules

    _write_module(tmp_path, "mod_a", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="dup"),)
    """)
    _write_module(tmp_path, "mod_b", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="dup"), ActionSpec(action_type="unique_b"))
    """)
    mods = discover_action_modules(
        _Cfg(), default_modules=("mod_a", "mod_b"), reserved_types=set()
    )
    assert [m.module for m in mods] == ["mod_a"]  # mod_b wholesale rejected


def test_module_shadowing_reserved_type_rejected(tmp_path):
    from core.action_modules import discover_action_modules

    _write_module(tmp_path, "mod_shadow", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="stateRequest"),)
    """)
    mods = discover_action_modules(
        _Cfg(), default_modules=("mod_shadow",), reserved_types={"stateRequest"}
    )
    assert mods == ()


def test_import_failure_skips_only_that_module(tmp_path):
    from core.action_modules import discover_action_modules

    _write_module(tmp_path, "mod_boom", """
        raise RuntimeError("heavy import failed")
    """)
    _write_module(tmp_path, "mod_ok", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="ok1"),)
    """)
    mods = discover_action_modules(
        _Cfg(), default_modules=("mod_boom", "mod_ok"), reserved_types=set()
    )
    assert [m.module for m in mods] == ["mod_ok"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py -k "duplicate or shadow" -v`
Expected: FAIL — `mod_b`/`mod_shadow` are currently still discovered (no dedup / reserved check).

- [ ] **Step 3: Add validation to `discover_action_modules`**

Replace the body of the `for name in names:` loop in `adaptor/core/action_modules.py` with:

```python
    names = _module_names(config, default_modules)
    reserved = set(_builtin_instant_action_types()) if reserved_types is None else set(reserved_types)
    seen = set(reserved)
    discovered = []
    for name in names:
        try:
            module = importlib.import_module(name)
            specs = tuple(module.action_specs())
        except Exception as exc:
            print(f"[ACTION MODULE SKIP] {name}: {exc}")
            continue
        if not specs or any(not isinstance(s, ActionSpec) for s in specs):
            print(f"[ACTION MODULE SKIP] {name}: action_specs() returned no valid ActionSpec")
            continue
        types = [s.action_type for s in specs]
        if len(set(types)) != len(types):
            print(f"[ACTION MODULE SKIP] {name}: duplicate action_type within module")
            continue
        clash = seen.intersection(types)
        if clash:
            print(f"[ACTION MODULE SKIP] {name}: action_type already registered: {sorted(clash)}")
            continue
        seen.update(types)
        discovered.append(
            DiscoveredModule(module=name, title=_module_title(module, name), specs=specs)
        )
    return tuple(discovered)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py -v`
Expected: PASS (all discovery tests green)

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/action_modules.py adaptor/tests/test_action_modules.py
git commit -m "feat(actions): dedup + reserved-type protection in module discovery"
```

---

### Task 5: Discovery — per-action disable and panel suppression

**Files:**
- Modify: `adaptor/core/action_modules.py`
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Consumes: `discover_action_modules`, `DiscoveredModule` (Task 4).
- Produces: modules whose individual actions are disabled via `[[actions]] enabled=false` drop those specs and set `panel_suppressed=True`.

- [ ] **Step 1: Write the failing test**

Add to `adaptor/tests/test_action_modules.py`:

```python
class _Act:
    def __init__(self, action_type, enabled=True):
        self.action_type = action_type
        self.enabled = enabled


def test_disabled_action_drops_spec_and_suppresses_panel(tmp_path):
    from core.action_modules import discover_action_modules

    _write_module(tmp_path, "mod_partial", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="keepMe"), ActionSpec(action_type="dropMe"))
    """)
    cfg = _Cfg(actions=(_Act("dropMe", enabled=False),))
    mods = discover_action_modules(
        cfg, default_modules=("mod_partial",), reserved_types=set()
    )
    assert len(mods) == 1
    assert {s.action_type for s in mods[0].specs} == {"keepMe"}
    assert mods[0].panel_suppressed is True


def test_no_disable_keeps_panel_unsuppressed(tmp_path):
    from core.action_modules import discover_action_modules

    _write_module(tmp_path, "mod_full", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="a1"), ActionSpec(action_type="a2"))
    """)
    mods = discover_action_modules(
        _Cfg(), default_modules=("mod_full",), reserved_types=set()
    )
    assert mods[0].panel_suppressed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py::test_disabled_action_drops_spec_and_suppresses_panel -v`
Expected: FAIL — `dropMe` still present, `panel_suppressed` is False.

- [ ] **Step 3: Apply per-action disable**

In `adaptor/core/action_modules.py`, after computing `reserved`/`seen` and before the loop, add:

```python
    disabled_types = {
        a.action_type for a in getattr(config, "actions", [])
        if not bool(getattr(a, "enabled", True))
    }
```

Then, inside the loop, after the `clash` check and `seen.update(types)`, replace the append with:

```python
        seen.update(types)
        kept = tuple(s for s in specs if s.action_type not in disabled_types)
        suppressed = len(kept) != len(specs)
        if not kept:
            print(f"[ACTION MODULE SKIP] {name}: all actions disabled")
            continue
        discovered.append(
            DiscoveredModule(
                module=name,
                title=_module_title(module, name),
                specs=kept,
                panel_suppressed=suppressed,
            )
        )
```

(Remove the older `discovered.append(...)` that lacked `panel_suppressed`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/action_modules.py adaptor/tests/test_action_modules.py
git commit -m "feat(actions): drop disabled module actions and suppress their panel"
```

---

### Task 6: Discovery — read `panel.html` via `importlib.resources`

**Files:**
- Modify: `adaptor/core/action_modules.py`
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Consumes: `discover_action_modules` (Task 5).
- Produces: `DiscoveredModule.panel_template` = raw `panel.html` text when the package has one and is not suppressed; else `None`.

- [ ] **Step 1: Write the failing tests**

Add to `adaptor/tests/test_action_modules.py` (these use real *packages*, since `importlib.resources` needs a package):

```python
def _write_package(tmp_path, name, init_body, panel=None):
    pkg_root = tmp_path / "pkgroot"
    pkg = pkg_root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(init_body))
    if panel is not None:
        (pkg / "panel.html").write_text(panel)
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))
    importlib.invalidate_caches()


def test_panel_html_read_when_present(tmp_path):
    from core.action_modules import discover_action_modules

    _write_package(tmp_path, "pkg_with_panel", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="wp1"),)
    """, panel="<section>hi $adapter_key</section>")
    mods = discover_action_modules(
        _Cfg(), default_modules=("pkg_with_panel",), reserved_types=set()
    )
    assert mods[0].panel_template == "<section>hi $adapter_key</section>"


def test_panel_html_none_when_absent(tmp_path):
    from core.action_modules import discover_action_modules

    _write_package(tmp_path, "pkg_no_panel", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="np1"),)
    """)
    mods = discover_action_modules(
        _Cfg(), default_modules=("pkg_no_panel",), reserved_types=set()
    )
    assert mods[0].panel_template is None


def test_panel_suppressed_forces_none(tmp_path):
    from core.action_modules import discover_action_modules

    _write_package(tmp_path, "pkg_suppress", """
        from core.action_registry import ActionSpec
        def action_specs():
            return (ActionSpec(action_type="s1"), ActionSpec(action_type="s2"))
    """, panel="<section>panel</section>")
    cfg = _Cfg(actions=(_Act("s2", enabled=False),))
    mods = discover_action_modules(
        cfg, default_modules=("pkg_suppress",), reserved_types=set()
    )
    assert mods[0].panel_suppressed is True
    assert mods[0].panel_template is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py -k panel -v`
Expected: FAIL — `panel_template` is always `None` (never read).

- [ ] **Step 3: Read the panel resource**

In `adaptor/core/action_modules.py`, add the import at the top:

```python
from importlib import resources
```

Add a helper above `discover_action_modules`:

```python
def _read_panel(module_name: str) -> Optional[str]:
    try:
        resource = resources.files(module_name).joinpath("panel.html")
        if not resource.is_file():
            return None
        return resource.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[ACTION MODULE PANEL SKIP] {module_name}: {exc}")
        return None
```

In the loop, replace the `discovered.append(...)` from Task 5 with:

```python
        panel = None if suppressed else _read_panel(name)
        discovered.append(
            DiscoveredModule(
                module=name,
                title=_module_title(module, name),
                specs=kept,
                panel_suppressed=suppressed,
                panel_template=panel,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/action_modules.py adaptor/tests/test_action_modules.py
git commit -m "feat(actions): read module panel.html via importlib.resources"
```

---

### Task 7: Convert `extensions/clamp` to a package with `panel.html`

**Files:**
- Move: `adaptor/extensions/clamp.py` → `adaptor/extensions/clamp/__init__.py`
- Create: `adaptor/extensions/clamp/panel.html`
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `extensions.clamp` importable as a package; `from extensions.clamp import execute_clamp_action, is_clamp_action, action_specs` still resolves; `importlib.resources.files("extensions.clamp")/"panel.html"` exists. Adds `MODULE_TITLE = "Clamp"`.

- [ ] **Step 1: Move the module into a package (git-preserving)**

```bash
cd adaptor/extensions
mkdir clamp_pkg
git mv clamp.py clamp_pkg/__init__.py
git mv clamp_pkg clamp
```

- [ ] **Step 2: Add `MODULE_TITLE` and labels to clamp specs**

In `adaptor/extensions/clamp/__init__.py`, add near the top (after `CLAMP_ACTION_TYPES`):

```python
MODULE_TITLE = "Clamp"

_CLAMP_LABELS = {
    "clampOn": "Servo ON (clampOn)",
    "clampOff": "Servo OFF (clampOff)",
    "clamp": "Clamp / close (clamp)",
    "unclamp": "Unclamp / open (unclamp)",
    "clampStop": "Clamp stop (clampStop)",
    "clampMin": "Clamp min (clampMin)",
    "clampMax": "Clamp max (clampMax)",
    "clampHome": "Clamp home (clampHome)",
    "clampMoveTo": "Clamp move to position (clampMoveTo)",
    "clampTeach": "Clamp teach (clampTeach)",
}
```

Update `action_specs()` to attach labels:

```python
def action_specs() -> Iterable[ActionSpec]:
    return tuple(
        ActionSpec(
            action_type=action_type,
            handler=handle_clamp_action,
            motion=True,
            label=_CLAMP_LABELS.get(action_type, action_type),
        )
        for action_type in CLAMP_ACTION_TYPES
    )
```

- [ ] **Step 3: Create the clamp panel fragment**

Create `adaptor/extensions/clamp/panel.html` (reproduces the current Clamp group: 8 servo/clamp/limit buttons + the clampMoveTo numeric input; reuses existing CSS classes):

```html
<section class="command-group">
  <div class="command-head"><h2>Clamp</h2></div>
  <div class="command-body"><div class="cmd-wrap">
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="clampOn">
      <span class="ac-info"><strong>Servo ON (clampOn)</strong><span>클램프 서보 ON</span></span>
      <button class="btn">Servo ON</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="clampOff">
      <span class="ac-info"><strong>Servo OFF (clampOff)</strong><span>클램프 서보 OFF</span></span>
      <button class="btn danger">Servo OFF</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="clamp">
      <span class="ac-info"><strong>Clamp / close (clamp)</strong><span>클램프 닫기 (config clamp_position / 기본 +offset)</span></span>
      <button class="btn warning">Clamp</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="unclamp">
      <span class="ac-info"><strong>Unclamp / open (unclamp)</strong><span>클램프 열기 (config unclamp_position / 기본 -offset)</span></span>
      <button class="btn warning">Unclamp</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="clampStop">
      <span class="ac-info"><strong>Clamp stop (clampStop)</strong><span>클램프 모터 정지</span></span>
      <button class="btn danger">Stop</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="clampMin">
      <span class="ac-info"><strong>Clamp min (clampMin)</strong><span>최소 위치 (config min_position / 없으면 -리미트 센서)</span></span>
      <button class="btn">Min</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="clampMax">
      <span class="ac-info"><strong>Clamp max (clampMax)</strong><span>최대 위치 (config max_position / 없으면 +리미트 센서)</span></span>
      <button class="btn">Max</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="clampHome">
      <span class="ac-info"><strong>Clamp home (clampHome)</strong><span>원점 (config home_position / 없으면 origin)</span></span>
      <button class="btn">Home</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="clampMoveTo">
      <span class="ac-info"><strong>클램프 위치 이동 (clampMoveTo)</strong><span>엔코더 절대 위치를 직접 입력해 이동 — 로봇이 실제로 움직임</span></span>
      <span class="command-fields"><input type="number" name="position" step="1" placeholder="엔코더 위치 (절대)" required></span>
      <div class="ac-actions"><label class="confirm"><input type="checkbox" name="confirm"> confirm</label>
      <button class="btn primary">Move</button></div>
    </form>
  </div></div>
</section>
```

- [ ] **Step 3b: Verify import-light**

Run: `cd adaptor && PYTHONPATH=. python -c "import extensions.clamp; print('ok'); print([s.action_type for s in extensions.clamp.action_specs()])"`
Expected: prints `ok` and the 10 clamp action types, with no serial/ROS import error. (If any hardware import was added at module top during the move, move it into the handler.)

- [ ] **Step 4: Write and run the package test**

Add to `adaptor/tests/test_action_modules.py`:

```python
def test_real_clamp_package_has_specs_and_panel():
    import importlib
    from importlib import resources

    clamp = importlib.import_module("extensions.clamp")
    types = {s.action_type for s in clamp.action_specs()}
    assert "clampMoveTo" in types
    assert all(s.label for s in clamp.action_specs())
    assert resources.files("extensions.clamp").joinpath("panel.html").is_file()
    # import-light: importing the package must not require pulling handlers' hardware libs
    assert hasattr(clamp, "execute_clamp_action")
```

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py::test_real_clamp_package_has_specs_and_panel tests/test_ezi_motor.py -v`
Expected: PASS (clamp package intact; existing clamp/motor tests unaffected)

- [ ] **Step 5: Commit**

```bash
git add adaptor/extensions/clamp adaptor/tests/test_action_modules.py
git commit -m "refactor(clamp): move to package with panel.html + labeled specs"
```

---

### Task 8: Convert `extensions/pio` to a package with `panel.html`

**Files:**
- Move: `adaptor/extensions/pio.py` → `adaptor/extensions/pio/__init__.py`
- Create: `adaptor/extensions/pio/panel.html`
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Produces: `extensions.pio` importable as a package; `from extensions.pio import execute_pio_action, is_pio_action, action_specs, summarize_pio_result` still resolves; adds `MODULE_TITLE = "PIO"` and labels.

- [ ] **Step 1: Move the module into a package**

```bash
cd adaptor/extensions
mkdir pio_pkg
git mv pio.py pio_pkg/__init__.py
git mv pio_pkg pio
```

- [ ] **Step 2: Add `MODULE_TITLE` and labels**

In `adaptor/extensions/pio/__init__.py`, add after `PIO_ACTION_TYPES`:

```python
MODULE_TITLE = "PIO"

_PIO_LABELS = {
    "pioInit": "PIO init (연결)",
    "pioReadIn": "PIO read inputs",
    "pioWriteOut": "PIO write output",
    "pioDisconnect": "PIO disconnect",
    "pioScenario": "PIO scenario 실행",
}
```

Update `action_specs()`:

```python
def action_specs() -> Iterable[ActionSpec]:
    return tuple(
        ActionSpec(
            action_type=action_type,
            handler=handle_pio_action,
            label=_PIO_LABELS.get(action_type, action_type),
        )
        for action_type in PIO_ACTION_TYPES
    )
```

- [ ] **Step 3: Create the PIO panel fragment**

Create `adaptor/extensions/pio/panel.html` (connect/read/disconnect buttons + a write-output form + a scenario form; the write/scenario params flow as VDA5050 `parameters` through the existing `/action` path):

```html
<section class="command-group">
  <div class="command-head"><h2>PIO</h2></div>
  <div class="command-body"><div class="cmd-wrap">
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="pioInit">
      <span class="ac-info"><strong>PIO init</strong><span>PIO 보드 연결/초기화</span></span>
      <button class="btn">Init</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="pioReadIn">
      <span class="ac-info"><strong>PIO read inputs</strong><span>입력 비트 스냅샷 요청</span></span>
      <button class="btn primary">Read</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="pioDisconnect">
      <span class="ac-info"><strong>PIO disconnect</strong><span>PIO 보드 연결 해제</span></span>
      <button class="btn danger">Disconnect</button>
    </form>
    <form class="action-card" method="post" action="/adapter/$adapter_key/action">
      <input type="hidden" name="csrf_token" value="$csrf_token">
      <input type="hidden" name="return_to" value="$return_to">
      <input type="hidden" name="action_type" value="pioWriteOut">
      <span class="ac-info"><strong>PIO write output</strong><span>출력 핀(index 1-8)에 값(0/1) 기록 — 실제 출력 변동</span></span>
      <span class="command-fields">
        <input type="number" name="index" min="1" max="8" step="1" placeholder="index (1-8)" required>
        <select name="state" required><option value="on">on</option><option value="off">off</option></select>
      </span>
      <div class="ac-actions"><label class="confirm"><input type="checkbox" name="confirm"> confirm</label>
      <button class="btn warning">Write</button></div>
    </form>
  </div></div>
</section>
```

`pioScenario`는 현재 handler가 구조화된 list 파라미터를 요구하지만 WebUi의 일반
form 경로는 문자열 파라미터만 만들기 때문에 이번 패널에서는 노출하지 않는다.

- [ ] **Step 3b: Verify import-light**

Run: `cd adaptor && PYTHONPATH=. python -c "import extensions.pio; print([s.action_type for s in extensions.pio.action_specs()])"`
Expected: prints the 5 PIO action types with no hardware import error.

- [ ] **Step 4: Write and run the package test**

Add to `adaptor/tests/test_action_modules.py`:

```python
def test_real_pio_package_has_specs_and_panel():
    import importlib
    from importlib import resources

    pio = importlib.import_module("extensions.pio")
    types = {s.action_type for s in pio.action_specs()}
    assert {"pioInit", "pioReadIn", "pioWriteOut"} <= types
    assert resources.files("extensions.pio").joinpath("panel.html").is_file()
```

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py -k "pio or clamp" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/extensions/pio adaptor/tests/test_action_modules.py
git commit -m "refactor(pio): move to package with panel.html + labeled specs"
```

---

### Task 9: Convert `extensions/ezio` to a package (no panel)

**Files:**
- Move: `adaptor/extensions/ezio.py` → `adaptor/extensions/ezio/__init__.py`
- Test: `adaptor/tests/test_ezio_extension.py`

**Interfaces:**
- Produces: `extensions.ezio` importable as a package; all current `from extensions.ezio import ...` names still resolve; no `panel.html` (falls back to cards). Adds `MODULE_TITLE = "EZIO"`.

- [ ] **Step 1: Move the module into a package**

```bash
cd adaptor/extensions
mkdir ezio_pkg
git mv ezio.py ezio_pkg/__init__.py
git mv ezio_pkg ezio
```

- [ ] **Step 2: Add `MODULE_TITLE`**

At the top of `adaptor/extensions/ezio/__init__.py` (after the module docstring/imports), add:

```python
MODULE_TITLE = "EZIO"
```

- [ ] **Step 3: Verify import-light**

Run: `cd adaptor && PYTHONPATH=. python -c "import extensions.ezio; print([s.action_type for s in extensions.ezio.action_specs()])"`
Expected: prints the EZIO action types (`ezioReadIn`, `ezioWriteOut`, `photoSensorRead`) with no hardware import error. If a top-level `import serial` (or similar) was present, move it into the function that uses it.

- [ ] **Step 4: Run existing ezio tests**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_ezio_extension.py -v`
Expected: PASS (package move is import-name-preserving)

- [ ] **Step 5: Commit**

```bash
git add adaptor/extensions/ezio
git commit -m "refactor(ezio): move to package for uniform module discovery"
```

---

### Task 10: Wire the adapter to discovery

**Files:**
- Modify: `adaptor/adapter_jibot.py:111-114`
- Modify: `adaptor/core/action_modules.py` (add `first_party_action_specs`)
- Modify: `adaptor/core/registry.py` (remove clamp from the reserved built-in list before discovery is wired)
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Produces: `first_party_action_specs(config) -> tuple[ActionSpec, ...]` — flattens `discover_action_modules(config)` into specs for `build_registry_from_config`.

- [ ] **Step 1: Write the failing test**

Add to `adaptor/tests/test_action_modules.py`:

```python
def test_first_party_action_specs_flattens_defaults():
    from core.action_modules import first_party_action_specs
    from core.action_registry import build_registry_from_config

    cfg = _Cfg()  # no config actions/modules -> defaults only
    specs = first_party_action_specs(cfg)
    types = {s.action_type for s in specs}
    assert {"clampOn", "clampMoveTo", "pioInit", "ezioReadIn"} <= types
    # feeds the existing registry builder cleanly
    reg = build_registry_from_config(cfg.actions, first_party_specs=specs)
    assert reg.has("clampOn") and reg.has("pioInit")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py::test_first_party_action_specs_flattens_defaults -v`
Expected: FAIL — `ImportError: cannot import name 'first_party_action_specs'`

- [ ] **Step 3: Remove the clamp reservation, add the helper, and rewire the adapter**

In `adaptor/core/registry.py`, remove the clamp `InstantAction(...)` entries from
`_JIBOT_INSTANT_ACTIONS`. Discovery protects that list as reserved types, so leaving
clamp there would reject the new `extensions.clamp` module.

In `adaptor/core/action_modules.py`, add:

```python
def first_party_action_specs(config) -> Tuple[ActionSpec, ...]:
    return tuple(
        spec
        for module in discover_action_modules(config)
        for spec in module.specs
    )
```

In `adaptor/adapter_jibot.py`, replace the registry construction at lines 111-114:

```python
        from core.action_modules import first_party_action_specs
        self._action_registry = build_registry_from_config(
            getattr(self.config, "actions", []),
            first_party_specs=first_party_action_specs(self.config),
        )
```

(Leave the existing `hardware_action_specs` import in place only if still used elsewhere; if the sole use was this call site, remove that now-unused import. Verify with `grep -n hardware_action_specs adaptor/adapter_jibot.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py::test_first_party_action_specs_flattens_defaults tests/test_adapter_dispatch.py tests/test_disable_motor_action.py -v`
Expected: PASS (adapter still registers first-party actions; disable path still works)

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/action_modules.py adaptor/core/registry.py adaptor/adapter_jibot.py adaptor/tests/test_action_modules.py
git commit -m "feat(adapter): build first-party action specs from module discovery"
```

---

### Task 11: WebUi registry — expose modules on `AdaptorSpec`

**Files:**
- Modify: `adaptor/core/registry.py:35-64` (add `ActionModuleView`, `AdaptorSpec.action_modules`), `:182-234` (drop clamp from `_JIBOT_INSTANT_ACTIONS`), `:289-353` (`_jibot_spec` populate)
- Test: `adaptor/tests/test_registry.py`

**Interfaces:**
- Produces:
  - `ActionModuleView` — frozen: `module: str`, `title: str`, `actions: tuple[InstantAction, ...]`, `panel_template: Optional[str]`.
  - `AdaptorSpec.action_modules: tuple[ActionModuleView, ...] = ()`.
  - `_jibot_spec` sets `instant_actions` = non-clamp built-ins + **module actions** (for the whitelist) + config actions, and `action_modules` = views from discovery.
- Consumes: `discover_action_modules` (Task 6), `InstantAction` (existing).

- [ ] **Step 1: Write the failing test**

Add to `adaptor/tests/test_registry.py`:

```python
def test_jibot_spec_exposes_clamp_as_module_not_generic_action():
    from config.config import get_config
    from core.registry import _jibot_spec

    cfg = get_config()
    spec = _jibot_spec(cfg, key="jibot:test", display_name="JIBOT test", unit="amr-adaptor.service")

    module_types = {a.action_type for m in spec.action_modules for a in m.actions}
    assert "clampOn" in module_types           # clamp is now a module
    assert "clampOn" in {a.action_type for a in spec.instant_actions}  # still whitelisted
    # a clamp module view carries the panel template
    clamp_view = next(m for m in spec.action_modules if "clampOn" in {a.action_type for a in m.actions})
    assert clamp_view.panel_template is not None
    assert clamp_view.title == "Clamp"
    # clamp motion flag preserved
    clamp_on = next(a for a in clamp_view.actions if a.action_type == "clampOn")
    assert clamp_on.motion is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_registry.py::test_jibot_spec_exposes_clamp_as_module_not_generic_action -v`
Expected: FAIL — `AttributeError: 'AdaptorSpec' object has no attribute 'action_modules'`

- [ ] **Step 3a: Add `ActionModuleView` and the `AdaptorSpec` field**

In `adaptor/core/registry.py`, after the `InstantAction` dataclass (line ~42) add:

```python
@dataclass(frozen=True)
class ActionModuleView:
    """A discovered action module as the WebUi sees it: its actions (for the
    whitelist + fallback cards) and an optional panel.html template."""

    module: str
    title: str
    actions: Tuple[InstantAction, ...]
    panel_template: Optional[str] = None
```

Add `Optional` to the typing import at the top: `from typing import List, Optional, Tuple`.

In `AdaptorSpec` (after `instant_actions`, line ~64) add:

```python
    action_modules: Tuple[ActionModuleView, ...] = field(default_factory=tuple)
```

- [ ] **Step 3b: Verify clamp entries are absent from `_JIBOT_INSTANT_ACTIONS`**

Task 10 already removed the clamp `InstantAction(...)` lines from
`_JIBOT_INSTANT_ACTIONS`. Confirm they remain absent while every non-clamp entry
(stateRequest … gotoNearestNode) remains. Clamp enters `instant_actions` via module
discovery in Step 3c.

- [ ] **Step 3c: Populate module views in `_jibot_spec`**

In `adaptor/core/registry.py`, inside `_jibot_spec` (after `configured_actions = (...)`, line ~316), add:

```python
    from core.action_modules import discover_action_modules

    module_views = tuple(
        ActionModuleView(
            module=m.module,
            title=m.title,
            actions=tuple(
                InstantAction(s.action_type, s.label or s.action_type, motion=s.motion)
                for s in m.specs
            ),
            panel_template=m.panel_template,
        )
        for m in discover_action_modules(config)
    )
    module_actions = tuple(a for v in module_views for a in v.actions)
```

Then change the `AdaptorSpec(...)` return so `instant_actions` includes module actions (whitelist) and add `action_modules`:

```python
        instant_actions=builtin_actions + module_actions + configured_actions,
        action_modules=module_views,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_registry.py tests/test_fleet_registry.py tests/test_registry_sound_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/registry.py adaptor/tests/test_registry.py
git commit -m "feat(webui): expose action modules on AdaptorSpec, clamp via discovery"
```

---

### Task 12: WebUi render — module panels and fallback

**Files:**
- Modify: `adaptor/web/render.py` — add `import string` + `_action_module_panel()`; replace clamp block in `_control_forms` (lines ~1538-1550) with a module loop; add module-action exclusion to the Vehicle-actions filter (lines ~1524-1535); remove now-dead `_CLAMP_ACTION_TYPES`, `_CLAMP_INPUT_ACTION_TYPES`, `_clamp_input_enabled`, `_clamp_move_form` (lines ~1330-1363, 1342-1363)
- Test: `adaptor/tests/test_action_module_panels_render.py`

**Interfaces:**
- Consumes: `AdaptorSpec.action_modules` / `ActionModuleView` (Task 11).
- Produces: `_action_module_panel(spec_key, module_view, csrf, return_to) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `adaptor/tests/test_action_module_panels_render.py`:

```python
from core.registry import AdaptorSpec, ActionModuleView, InstantAction
from web import render


def _spec(action_modules):
    return AdaptorSpec(
        key="jibot:test",
        display_name="JIBOT test",
        unit="amr-adaptor.service",
        workdir=render.Path(".") if hasattr(render, "Path") else __import__("pathlib").Path("."),
        exec_script="run-main.sh",
        manufacturer="jibot",
        serial="TEST01",
        vda_full_version="2.0.0",
        topic_prefix="uagv/v2/jibot/TEST01",
        instant_actions=tuple(a for m in action_modules for a in m.actions),
        action_modules=tuple(action_modules),
    )


def test_panel_rendered_once_with_substitution():
    view = ActionModuleView(
        module="extensions.clamp", title="Clamp",
        actions=(InstantAction("clampOn", "Servo ON", motion=True),),
        panel_template='<section class="command-group" data-k="$adapter_key">'
                       '<input value="$csrf_token"><input value="$return_to"></section>',
    )
    html = render._action_module_panel("jibot:test", view, "CSRF123", "/adapter/jibot:test")
    assert html.count("command-group") == 1
    assert 'data-k="jibot:test"' in html
    assert "CSRF123" in html
    assert "$adapter_key" not in html  # fully substituted


def test_missing_panel_falls_back_to_cards():
    view = ActionModuleView(
        module="extensions.ezio", title="EZIO",
        actions=(InstantAction("ezioReadIn", "EZIO read", motion=False),),
        panel_template=None,
    )
    html = render._action_module_panel("jibot:test", view, "CSRF", "/adapter/jibot:test")
    assert "EZIO" in html
    assert "ezioReadIn" in html
    assert "action-card" in html


def test_template_error_falls_back_to_cards():
    view = ActionModuleView(
        module="broken", title="Broken",
        actions=(InstantAction("brokeAct", "Broke", motion=False),),
        panel_template="oops $unknown_var here",  # KeyError on strict substitute
    )
    html = render._action_module_panel("jibot:test", view, "CSRF", "/adapter/jibot:test")
    assert "brokeAct" in html          # fell back
    assert "action-card" in html


def test_control_forms_excludes_module_actions_from_vehicle_cards():
    view = ActionModuleView(
        module="extensions.clamp", title="Clamp",
        actions=(InstantAction("clampOn", "Servo ON", motion=True),),
        panel_template='<section class="command-group">CLAMP-PANEL</section>',
    )
    spec = _spec([view])
    html = render._control_forms(spec, "CSRF", return_to="/adapter/jibot:test")
    assert "CLAMP-PANEL" in html                       # panel rendered
    assert html.count('value="clampOn"') <= 1          # not duplicated as a generic card
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_module_panels_render.py -v`
Expected: FAIL — `AttributeError: module 'web.render' has no attribute '_action_module_panel'`

- [ ] **Step 3a: Add `_action_module_panel`**

In `adaptor/web/render.py`, add `import string` near the top imports. Add this function next to the other command builders (e.g. after `_command_group`, line ~977):

```python
def _module_fallback_cards(spec_key: str, module_view, csrf: str, return_to: str) -> str:
    """Render a module's actions as generic cards inside its own group — used when
    the module has no panel.html or its panel is suppressed by a disabled action."""
    cards = "".join(
        _card_action_form(spec_key, a, csrf, return_to=return_to)
        for a in module_view.actions
    )
    return _command_group(module_view.title, f'<div class="cmd-wrap">{cards}</div>')


def _action_module_panel(spec_key: str, module_view, csrf: str, return_to: str) -> str:
    """Render one action module: its panel.html fragment (with $adapter_key /
    $csrf_token / $return_to substituted and HTML-escaped) if present, else
    fallback cards. Any substitution error falls back to cards so the page opens."""
    template = getattr(module_view, "panel_template", None)
    if not template:
        return _module_fallback_cards(spec_key, module_view, csrf, return_to)
    try:
        return string.Template(template).substitute(
            adapter_key=esc(spec_key),
            csrf_token=esc(csrf),
            return_to=esc(return_to),
        )
    except Exception as exc:  # KeyError / ValueError from a bad $var or lone $
        print(f"[ACTION MODULE PANEL RENDER FALLBACK] {module_view.module}: {exc}")
        return _module_fallback_cards(spec_key, module_view, csrf, return_to)
```

- [ ] **Step 3b: Replace the clamp block and exclude module actions from generic cards**

In `_control_forms` (`adaptor/web/render.py`), compute the module action-type set at the top of the function (right after `groups = []`, line ~1504):

```python
    module_action_types = {
        a.action_type for m in getattr(spec, "action_modules", ()) for a in m.actions
    }
```

In the "Vehicle actions" filter (lines ~1524-1535), remove the two clamp exclusions (`_CLAMP_INPUT_ACTION_TYPES`, `_CLAMP_ACTION_TYPES`) and add a single module exclusion. The filter becomes:

```python
    actions = "".join(
        _card_action_form(spec.key, a, csrf, return_to=return_to)
        for a in spec.instant_actions
        if a.action_type not in _SOUND_ACTION_TYPES
        and a.action_type not in module_action_types  # → dedicated module panel/group
        and a.action_type not in _DRIVE_ACTION_TYPES
        and a.action_type not in _RECOVERY_ACTION_TYPES
        and a.action_type not in _EMERGENCY_ACTION_TYPES
        and a.action_type != "jibotMotionRule"
        and a.action_type != "gotoNearestNode"
    )
```

Replace the entire hardcoded Clamp block (lines ~1538-1550: `clamp_cards`, `clamp_move`, `if clamp_cards or clamp_move:` group) with a module loop:

```python
    for module_view in getattr(spec, "action_modules", ()):
        groups.append(_action_module_panel(spec.key, module_view, csrf, return_to=return_to))
```

- [ ] **Step 3c: Remove now-dead clamp helpers**

Delete `_CLAMP_INPUT_ACTION_TYPES` (line ~1330), `_CLAMP_ACTION_TYPES` (lines ~1335-1338), `_clamp_input_enabled` (lines ~1342-1346), and `_clamp_move_form` (lines ~1349-1363). Keep `_ACTION_META` clamp entries (used by fallback cards).

Verify nothing else references them:

Run: `cd adaptor && grep -rn "_CLAMP_ACTION_TYPES\|_CLAMP_INPUT_ACTION_TYPES\|_clamp_move_form\|_clamp_input_enabled" web/`
Expected: no matches (all removed)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_module_panels_render.py -v`
Expected: PASS

Then a smoke render of a real spec:

Run: `cd adaptor && PYTHONPATH=. python -c "from config.config import get_config; from core.registry import _jibot_spec; from web import render; s=_jibot_spec(get_config(), key='jibot:t', display_name='t', unit='amr-adaptor.service'); h=render._control_forms(s,'CSRF',return_to='/adapter/jibot:t'); print('Clamp' in h, 'clampMoveTo' in h)"`
Expected: prints `True True` (clamp panel present, move input present)

- [ ] **Step 5: Commit**

```bash
git add adaptor/web/render.py adaptor/tests/test_action_module_panels_render.py
git commit -m "feat(webui): render action-module panels with card fallback"
```

---

### Task 13: Honor `return_to` in `_post_action`

**Files:**
- Modify: `adaptor/web/server.py:709-752`
- Test: `adaptor/tests/` (new `test_post_action_return_to.py` or extend an existing server test)

**Interfaces:**
- Consumes: existing `_post_action`, `_reserved` already contains `return_to`.
- Produces: redirect target uses a safe local `return_to` when provided; open-redirect-guarded.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_post_action_return_to.py`:

```python
from web.server import _safe_return_to


def test_safe_return_to_accepts_local_path():
    assert _safe_return_to("/adapter/jibot:t?tab=io", "/adapter/jibot:t") == "/adapter/jibot:t?tab=io"


def test_safe_return_to_rejects_external():
    assert _safe_return_to("https://evil.example/x", "/adapter/jibot:t") == "/adapter/jibot:t"
    assert _safe_return_to("//evil.example", "/adapter/jibot:t") == "/adapter/jibot:t"
    assert _safe_return_to("", "/adapter/jibot:t") == "/adapter/jibot:t"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_post_action_return_to.py -v`
Expected: FAIL — `ImportError: cannot import name '_safe_return_to'`

- [ ] **Step 3: Add the guard and use it**

In `adaptor/web/server.py`, near the module-level helpers (after `_confirmed`, line ~41) add:

```python
def _safe_return_to(return_to: str, default: str) -> str:
    """Only honor a same-origin local path (avoids open redirect)."""
    if return_to.startswith("/") and not return_to.startswith("//"):
        return return_to
    return default
```

In `_post_action`, change the redirect target computation. After the whitelist/confirm checks and before building the payload, replace the final redirect (line ~752) so the flash param uses the right separator:

At line ~714, keep `target = f"/adapter/{urllib.parse.quote(key, safe=':')}"`. Then just before `delivered, text = ...` (or right after computing `target`), add:

```python
        target = _safe_return_to(form.get("return_to", ""), target)
```

Change the final redirect (line ~752) to use a correct separator:

```python
        sep = "&" if "?" in target else "?"
        h._redirect(f"{target}{sep}{flash}={urllib.parse.quote(text)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_post_action_return_to.py tests/test_control.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/web/server.py adaptor/tests/test_post_action_return_to.py
git commit -m "feat(webui): honor safe return_to in /action redirect"
```

---

### Task 14: Document `[[action_modules]]` and full-chain smoke test

**Files:**
- Modify: `adaptor/config/config.toml` (documented example near the `[[actions]]` docs, lines ~380-412)
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Consumes: everything above. This task adds operator-facing docs and one end-to-end assertion.

- [ ] **Step 1: Write the failing end-to-end test**

Add to `adaptor/tests/test_action_modules.py`:

```python
def test_default_config_discovers_all_first_party_modules():
    from config.config import get_config
    from core.action_modules import discover_action_modules

    mods = {m.module for m in discover_action_modules(get_config())}
    assert {"extensions.clamp", "extensions.pio", "extensions.ezio"} <= mods


def test_disabled_default_module_via_config_is_dropped():
    from core.action_modules import discover_action_modules

    class _Mod:
        def __init__(self, module, enabled):
            self.module = module
            self.enabled = enabled

    cfg = _Cfg(action_modules=(_Mod("extensions.pio", False),))
    mods = {m.module for m in discover_action_modules(cfg)}
    assert "extensions.pio" not in mods
    assert "extensions.clamp" in mods
```

- [ ] **Step 2: Run test to verify it fails / passes**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py -k "default_config or disabled_default" -v`
Expected: PASS if Tasks 7-9 are done (real packages exist). If `test_disabled_default_module_via_config_is_dropped` fails, confirm `_module_names` honors `enabled=false` overrides for defaults (Task 3).

- [ ] **Step 3: Document the config**

In `adaptor/config/config.toml`, near the `[[actions]]` documentation block (lines ~380-412), add a commented example:

```toml
# ── Action modules ────────────────────────────────────────────────────────
# An action module is a Python package exposing action_specs() and an optional
# panel.html. First-party PIO/clamp/ezio are built in — do NOT list them here
# unless disabling one. Custom modules must be import-light and on the sys.path
# of BOTH the adapter and the WebUi units.
#
# [[action_modules]]
# module = "custom_actions.air_shower"
# enabled = true
#
# Disable a built-in module:
# [[action_modules]]
# module = "extensions.pio"
# enabled = false
```

- [ ] **Step 4: Run the full suite for regressions**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_action_modules.py tests/test_action_module_panels_render.py tests/test_registry.py tests/test_action_registry.py tests/test_config.py tests/test_ezio_extension.py tests/test_ezi_motor.py tests/test_adapter_dispatch.py -v`
Expected: PASS (no regressions across discovery, render, registry, config, extensions, adapter)

- [ ] **Step 5: Commit**

```bash
git add adaptor/config/config.toml adaptor/tests/test_action_modules.py
git commit -m "docs(config): document [[action_modules]] + full-chain discovery test"
```

---

## Self-Review

**Spec coverage** (design §1-13):
- §4 module contract (`action_specs()`, unique types, import-light, `label`) → Tasks 1, 3-6, 7-9.
- §5 panel contract (`string.Template`, `$adapter_key/$csrf_token/$return_to`, escaping, strict substitute) → Task 12.
- §6 discovery/data-flow, shared `core` function (D3), `_JIBOT_INSTANT_ACTIONS` split, whitelist preserved → Tasks 3-6, 10, 11.
- §6.1 separate-process import + asymmetric-failure skip (D2) → Tasks 3-4 (import failure skip), 7-9 (import-light verify).
- §7 display/fallback (panel / cards / suppression / disable) → Tasks 5, 12.
- §8 security boundary (fixed `panel.html`, escaped substitution, no arbitrary path) → Tasks 6, 12; open-redirect guard Task 13.
- §9 relation to existing code (pio/clamp/ezio → packages, `/io/out` untouched, `return_to`, confirm gate untouched) → Tasks 7-9, 13.
- §10 tests → every task's test steps.
- §11 completion (PIO+clamp modules+panels, custom fixture, auto-discover, fallback, regressions) → Tasks 7-8, 3 (fixtures), 12, 14.
- §12 deployment (PYTHONPATH both units) → documented Task 14.
- §13 decision log (D1/D2/D3) → D1 Task 1, D2 Tasks 3-4/7-9, D3 Tasks 3/10/11.

**Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"; every code step shows concrete code.

**Type consistency:** `DiscoveredModule(module,title,specs,panel_suppressed,panel_template)` used identically in Tasks 3-6, 10, 11. `ActionModuleView(module,title,actions,panel_template)` consistent Tasks 11-12. `discover_action_modules(config,*,default_modules,reserved_types)` signature stable across Tasks 3-6, 10, 11, 14. `_action_module_panel(spec_key, module_view, csrf, return_to)` consistent Task 12. `first_party_action_specs(config)` consistent Tasks 10, 11-lineage.
