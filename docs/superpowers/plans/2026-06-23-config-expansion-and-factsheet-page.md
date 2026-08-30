# Config Knob Exposure + Factsheet WebUi Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose ~40 hardcoded adapter/client/WebUi/factsheet knobs as plain TOML config in `config.toml`, and add a `/factsheet` WebUi page to view and edit the VDA5050 factsheet.

**Architecture:** New `@dataclass` configs parsed in `config.get_config()` (same pattern as today), consumed via `getattr(config…, key, default)` at the hardcoded sites so defaults equal current literals. `jibot-client` gains constructor kwargs for connection knobs. The factsheet builder is extracted to a pure `core/factsheet.py:build_factsheet(config)` shared by the adapter and the new WebUi page, which reuses the existing `/config` edit infrastructure (`configio.rewrite_scalar`).

**Tech Stack:** Python (adapter venv ≥3.11; `jibot-client` ≥3.9), `tomllib`/`tomli`, stdlib `http.server` WebUi, `unittest`-style tests run under `pytest`.

## Global Constraints

- **Behavior preservation:** every new field's default MUST equal the current hardcoded literal. Factsheet output MUST be byte-identical when all `[factsheet]` keys are at defaults. UmConnect defaults stay `user="test"`, `password="test"`, `device_type="pc"`.
- **Placement:** all operator-editable knobs are plain TOML keys in `adaptor/config/config.toml`. `adaptor/config/jibot-config.toml` stays an OPTIONAL deep-merge overlay (recommended values as comments only).
- **Defensive reads:** new adapter/util consumption sites use `getattr(self.config.<section>, "<key>", <default>)` — matches existing code and tolerates older config files.
- **Loader pattern:** add each new dataclass to `config.py`, parse in `get_config()` with `config_dict.get("<section>", {})`, add to `Config`. Mirror keys + aligned `#` comments into `config.toml`.
- **Tests:** `unittest.TestCase` style under `adaptor/tests/` (and `jibot-client` tests under `adaptor/tests/` as today). Run with `python -m pytest <path> -v`.
- **Commits:** one per task, Conventional Commits, end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **No `Date.now`/random in code paths under test;** factsheet runtime values (`headerId`, `timestamp`) are injected by the caller, not by `build_factsheet`.

---

## Module 1 — UmDock approach params as config

The client already accepts 10 `UmDock` optional params (`client.py` `COMMAND_OPTIONAL_PARAMS["UmDock"]`). All 4 adapter call sites currently call bare `um_dock()`. Add a config block + per-rule override, thread a `dict` of non-`None` params into every call site.

### Task 1.1: `DockApproachParams` dataclass + `DockConfig.approach_params`

**Files:**
- Modify: `adaptor/config/config.py` (DockConfig ~131-147; add new dataclass above it; parse in `get_config` ~349)
- Test: `adaptor/tests/test_dock_approach_params_config.py` (create)

**Interfaces:**
- Produces: `DockApproachParams` with `Optional` fields `goal:Optional[str]=None`, `detect_charging_signal:Optional[bool]=None`, `dock_move_additional_dist:Optional[float]=None`, `dock_rotate_additional_angle:Optional[float]=None`, `need_turn_around:Optional[bool]=None`, `need_heading:Optional[bool]=None`, `use_avoid_area:Optional[bool]=None`, `disable_motor_secs:Optional[float]=None`, `clearance_back_min:Optional[float]=None`, `clearance_front_min:Optional[float]=None`; method `as_params()->dict` returning only non-`None` fields. `DockConfig.approach_params: DockApproachParams`.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_dock_approach_params_config.py
import unittest
from config.config import DockApproachParams, get_config


class TestDockApproachParams(unittest.TestCase):
    def test_defaults_are_all_none_and_as_params_empty(self):
        p = DockApproachParams()
        self.assertIsNone(p.detect_charging_signal)
        self.assertEqual(p.as_params(), {})

    def test_as_params_drops_none_keeps_set(self):
        p = DockApproachParams(detect_charging_signal=True, clearance_back_min=120.0)
        self.assertEqual(
            p.as_params(),
            {"detect_charging_signal": True, "clearance_back_min": 120.0},
        )

    def test_get_config_default_dock_has_empty_approach_params(self):
        cfg = get_config()
        self.assertEqual(cfg.dock.approach_params.as_params(), {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_dock_approach_params_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'DockApproachParams'`.

- [ ] **Step 3: Add the dataclass and wire DockConfig**

In `adaptor/config/config.py`, add above `DockConfig`:

```python
@dataclass
class DockApproachParams:
    """Optional UmDock approach params forwarded to the JIBOT firmware
    (urobot JModeCharge::Start). Unset (None) fields are omitted so a bare
    UmDock keeps the default dock approach. JIBOT hardware-characteristic
    values belong in jibot-config.toml [dock.approach_params]."""
    goal: Optional[str] = None
    detect_charging_signal: Optional[bool] = None
    dock_move_additional_dist: Optional[float] = None
    dock_rotate_additional_angle: Optional[float] = None
    need_turn_around: Optional[bool] = None
    need_heading: Optional[bool] = None
    use_avoid_area: Optional[bool] = None
    disable_motor_secs: Optional[float] = None
    clearance_back_min: Optional[float] = None
    clearance_front_min: Optional[float] = None

    def as_params(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}
```

Add to `DockConfig` (after `start_charging_verify_timeout_sec`):

```python
    approach_params: DockApproachParams = field(default_factory=DockApproachParams)
```

In `get_config`, replace the dock build line:

```python
    dock_raw = dict(config_dict.get("dock", {}))
    approach_raw = dock_raw.pop("approach_params", {})
    dock_config = DockConfig(**dock_raw)
    dock_config.approach_params = DockApproachParams(**approach_raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_dock_approach_params_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/config/config.py adaptor/tests/test_dock_approach_params_config.py
git commit -m "feat(config): add DockApproachParams (UmDock approach params)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 1.2: Per-rule override on `MotionRule` + adapter resolver

**Files:**
- Modify: `adaptor/config/config.py` (`MotionRule` ~150-164, `_motion_rule_from_dict` ~167)
- Modify: `adaptor/adapter_jibot.py` (add `_dock_approach_params`; helpers near `_dock_fail_timeout_sec:3066`)
- Test: `adaptor/tests/test_dock_approach_params_config.py` (extend)

**Interfaces:**
- Consumes: `DockApproachParams.as_params()`, `cfg.dock.approach_params` (Task 1.1).
- Produces: `MotionRule.approach_params: Optional[dict] = None`; adapter method `_dock_approach_params(self, node_id: str) -> dict` (rule override merged over global, non-`None` only).

- [ ] **Step 1: Write the failing test (config-level merge)**

```python
    def test_motion_rule_carries_approach_params(self):
        from config.config import _motion_rule_from_dict
        rule = _motion_rule_from_dict({
            "to": "CH", "mode": "dock",
            "approach_params": {"detect_charging_signal": True},
        })
        self.assertEqual(rule.approach_params, {"detect_charging_signal": True})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_dock_approach_params_config.py -k motion_rule -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'approach_params'`.

- [ ] **Step 3: Add field to MotionRule**

In `MotionRule` add:

```python
    approach_params: Optional[Dict[str, Any]] = None   # mode="dock" override
```

`_motion_rule_from_dict` already passes through `**fields`, so no change needed there.

- [ ] **Step 4: Add the adapter resolver**

In `adapter_jibot.py`, after `_dock_fail_timeout_sec` (line ~3071):

```python
    def _dock_approach_params(self, node_id: str) -> Dict[str, Any]:
        """Merged UmDock params for a dock node: global [dock.approach_params]
        overlaid with the matching motion rule's approach_params. None-valued
        keys are dropped so a node with no config yields a bare UmDock."""
        params: Dict[str, Any] = {}
        global_ap = getattr(self.config.dock, "approach_params", None)
        if global_ap is not None:
            params.update(global_ap.as_params())
        rule = self._dock_motion_rule(node_id)
        rule_ap = getattr(rule, "approach_params", None) if rule is not None else None
        if isinstance(rule_ap, dict):
            params.update({k: v for k, v in rule_ap.items() if v is not None})
        return params
```

- [ ] **Step 5: Write the failing adapter test**

Add to `adaptor/tests/test_dock_approach_params_config.py` a lightweight resolver test using a stub config (no full adapter init):

```python
class TestAdapterDockApproachResolver(unittest.TestCase):
    def _adapter(self, dock_params, rules):
        from types import SimpleNamespace
        from adapter_jibot import Adapter
        adapter = Adapter.__new__(Adapter)
        adapter.config = SimpleNamespace(
            dock=SimpleNamespace(approach_params=dock_params),
            motion_rules=rules,
        )
        return adapter

    def test_global_only(self):
        from config.config import DockApproachParams, MotionRule
        a = self._adapter(DockApproachParams(detect_charging_signal=True), [])
        self.assertEqual(a._dock_approach_params("CH"), {"detect_charging_signal": True})

    def test_rule_overrides_global(self):
        from config.config import DockApproachParams, MotionRule
        rule = MotionRule(to="CH", mode="dock",
                          approach_params={"detect_charging_signal": False})
        a = self._adapter(DockApproachParams(detect_charging_signal=True), [rule])
        self.assertEqual(a._dock_approach_params("CH"), {"detect_charging_signal": False})
```

> Note: the adapter class is `Adapter` (in `adapter_jibot.py:67`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_dock_approach_params_config.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add adaptor/config/config.py adaptor/adapter_jibot.py adaptor/tests/test_dock_approach_params_config.py
git commit -m "feat(jibot): resolve UmDock approach params (global + per-rule override)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 1.3: Thread params into all 4 `um_dock()` call sites

**Files:**
- Modify: `adaptor/adapter_jibot.py` lines `3133`, `3530`, `5246`, and the order-carried startCharging handler (`um_dock()` at line 3004; `node_id` is in scope there)
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py` (mock `um_dock` already `async def um_dock(self, gap=-1)` at ~186 → change to `async def um_dock(self, gap=-1, **params)` capturing params)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` (extend)

**Interfaces:**
- Consumes: `_dock_approach_params(node_id)` (Task 1.2).

- [ ] **Step 1: Update the test mock to capture params + add assertion**

In `test_adapter_jibot_v3_order.py`, change the mock vehicle's `um_dock`:

```python
    async def um_dock(self, gap: int = -1, **params) -> None:
        self.dock_calls += 1
        self.last_dock_params = params
```

Add a test (configure a dock node + global approach params, assert forwarded):

```python
    def test_dock_node_forwards_configured_approach_params(self):
        # build adapter with dock.approach_params.detect_charging_signal=True
        # drive an order to a dock node; assert vehicle.last_dock_params ==
        # {"detect_charging_signal": True}
        ...
```

> Use the existing order-driving harness in this file as the template (find an existing `test_*dock*` test and copy its setup; set `adapter.config.dock.approach_params = DockApproachParams(detect_charging_signal=True)` before running).

- [ ] **Step 2: Run to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k forwards_configured_approach -v`
Expected: FAIL — `last_dock_params == {}` (bare call).

- [ ] **Step 3: Update the 4 call sites**

`_send_node_motion` (line 3133):

```python
            await self._vehicle.um_dock(**self._dock_approach_params(str(node.node_id)))
```

`_run_approach_then_dock` (line 3530):

```python
            await self._vehicle.um_dock(**self._dock_approach_params(node_id))
```

Instant `_dock()` (line 5246) — resolve from the robot's current node:

```python
                await self._vehicle.um_dock(
                    **self._dock_approach_params(self._last_node_id or "")
                )
```

Order-carried startCharging handler (line 3004) — `node_id` is already in scope; change to `await self._vehicle.um_dock(**self._dock_approach_params(str(node_id)))`.

- [ ] **Step 4: Run targeted + full order tests**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -v`
Expected: PASS (new test + all existing dock tests, since defaults yield `{}` ⇒ bare call).

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(jibot): forward configured UmDock approach params at all dock sites

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 1.4: TOML docs (config.toml + jibot-config.toml)

**Files:**
- Modify: `adaptor/config/config.toml` (`[dock]` block ~100-112)
- Modify: `adaptor/config/jibot-config.toml`

- [ ] **Step 1: Add commented example to config.toml**

Under `[dock]` append:

```toml
# Optional UmDock approach params (JIBOT firmware JModeCharge::Start).
# Unset => bare UmDock (default approach). JIBOT hardware-characteristic values
# are better placed in jibot-config.toml [dock.approach_params].
# [dock.approach_params]
# detect_charging_signal = true
# dock_move_additional_dist = 0.0
# dock_rotate_additional_angle = 0.0
# clearance_back_min = 0.0
# clearance_front_min = 0.0
```

- [ ] **Step 2: Add recommended overlay to jibot-config.toml**

Append a commented `[dock.approach_params]` block documenting JIBOT-recommended values (leave commented; operators uncomment per robot).

- [ ] **Step 3: Validate config still loads**

Run: `cd adaptor && python -c "from config.config import get_config; get_config(); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add adaptor/config/config.toml adaptor/config/jibot-config.toml
git commit -m "docs(config): document [dock.approach_params] UmDock knobs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Module 2 — jibot-client connection config

### Task 2.1: `JIBOT.__init__` connection kwargs + `um_connect` credentials

**Files:**
- Modify: `jibot-client/src/jibot_client/client.py` (`__init__` 172-179; `um_connect` 754-761; `read(32768)` ~1013; `5.0` throttle ~1220; `30.0` throttle ~1252; `send_command_and_wait`/`wait_for_response` defaults 513/523)
- Test: `adaptor/tests/test_jibot_client_connection_config.py` (create)

**Interfaces:**
- Produces: `JIBOT.__init__(..., user="test", password="test", device_type="pc", command_timeout=3.0, recv_buffer_bytes=32768, status_log_interval_sec=5.0, battery_log_interval_sec=30.0)`; instance attrs `self.user/self.password/self.device_type/self.command_timeout/self.recv_buffer_bytes/self.status_log_interval_sec/self.battery_log_interval_sec`.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_jibot_client_connection_config.py
import asyncio, unittest
from jibot_client import JIBOT


class TestConnectionConfig(unittest.TestCase):
    def test_defaults_preserve_current_behavior(self):
        v = JIBOT("1.2.3.4")
        self.assertEqual((v.user, v.password, v.device_type), ("test", "test", "pc"))
        self.assertEqual(v.recv_buffer_bytes, 32768)
        self.assertEqual(v.status_log_interval_sec, 5.0)
        self.assertEqual(v.battery_log_interval_sec, 30.0)

    def test_um_connect_sends_configured_credentials(self):
        v = JIBOT("1.2.3.4", user="ops", password="s3cret", device_type="amr")
        sent = {}

        async def fake_send(cmd):
            sent.update(cmd)

        v.json_cmd_to_jibot = fake_send
        asyncio.run(v.um_connect())
        self.assertEqual(sent["user"], "ops")
        self.assertEqual(sent["password"], "s3cret")
        self.assertEqual(sent["device_type"], "amr")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_jibot_client_connection_config.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'user'`.

- [ ] **Step 3: Extend the constructor**

Replace `__init__` signature (172-179) and add attribute assignment:

```python
    def __init__(
        self,
        robot_ip,
        robot_port=7273,
        config=None,
        charging_status="charging",
        recorder=None,
        user="test",
        password="test",
        device_type="pc",
        command_timeout=3.0,
        recv_buffer_bytes=32768,
        status_log_interval_sec=5.0,
        battery_log_interval_sec=30.0,
    ):
        self.robot_ip = robot_ip
        self.robot_port = robot_port
        self.user = user
        self.password = password
        self.device_type = device_type
        self.command_timeout = command_timeout
        self.recv_buffer_bytes = recv_buffer_bytes
        self.status_log_interval_sec = status_log_interval_sec
        self.battery_log_interval_sec = battery_log_interval_sec
```

(keep the rest of `__init__` unchanged below the existing `self.robot_port = robot_port`.)

- [ ] **Step 4: Use the attrs at their sites**

`um_connect` (754-760):

```python
        json_cmd_connect = {
            "#CMD#": "UmConnect",
            "#GAP#": gap,
            "user": self.user,
            "password": self.password,
            "device_type": self.device_type,
        }
```

`receive_data_from_server` recv (line ~1013): `data = await self.reader.read(self.recv_buffer_bytes)`.
Status-log throttle (~1220): `if now - self._last_status_log_at < self.status_log_interval_sec:`.
Battery-log throttle (~1252): `if now - self._last_battery_info_log_at < self.battery_log_interval_sec:`.
`send_command_and_wait` default (513): `timeout=None` → inside, `timeout = self.command_timeout if timeout is None else timeout`. Apply same to `wait_for_response` (523) OR keep its 3.0 and only change `send_command_and_wait` to default to `self.command_timeout`. (Pick the minimal change: default `send_command_and_wait(timeout=None)` resolving to `self.command_timeout`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd adaptor && python -m pytest tests/test_jibot_client_connection_config.py tests/test_jibot_client_um_dock_params.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add jibot-client/src/jibot_client/client.py adaptor/tests/test_jibot_client_connection_config.py
git commit -m "feat(jibot-client): configurable UmConnect creds + connection knobs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 2.2: `[jibot_client]` config section + adapter injection

**Files:**
- Modify: `adaptor/config/config.py` (new `JibotClientConfig`; parse; add to `Config`)
- Modify: `adaptor/config/config.toml` (new `[jibot_client]`)
- Modify: `adaptor/main.py` (`JIBOT(...)` at 652-657 passes the new kwargs)
- Test: `adaptor/tests/test_config.py` (extend) + `adaptor/tests/test_jibot_client_connection_config.py`

**Interfaces:**
- Produces: `JibotClientConfig(user="test", password="test", device_type="pc", command_timeout_sec=3.0, recv_buffer_bytes=32768, status_log_interval_sec=5.0, battery_log_interval_sec=30.0)`; `Config.jibot_client: JibotClientConfig`.

- [ ] **Step 1: Failing test**

```python
def test_jibot_client_config_defaults(self):
    from config.config import get_config
    jc = get_config().jibot_client
    self.assertEqual(jc.user, "test")
    self.assertEqual(jc.command_timeout_sec, 3.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_config.py -k jibot_client -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'jibot_client'`.

- [ ] **Step 3: Add the dataclass + parse + Config field**

```python
@dataclass
class JibotClientConfig:
    user: str = "test"
    password: str = "test"
    device_type: str = "pc"
    command_timeout_sec: float = 3.0
    recv_buffer_bytes: int = 32768
    status_log_interval_sec: float = 5.0
    battery_log_interval_sec: float = 30.0
```

In `get_config`: `jibot_client = JibotClientConfig(**config_dict.get("jibot_client", {}))`; add `jibot_client=jibot_client` to the `Config(...)` call and the `Config` dataclass.

- [ ] **Step 4: Inject into JIBOT in main.py (652-657)**

```python
            jc = config_data.jibot_client
            vehicle = JIBOT(
                vehicle_ip,
                vehicle_port,
                config=config_data,
                charging_status=config_data.jibot_status.charging,
                user=jc.user,
                password=jc.password,
                device_type=jc.device_type,
                command_timeout=jc.command_timeout_sec,
                recv_buffer_bytes=jc.recv_buffer_bytes,
                status_log_interval_sec=jc.status_log_interval_sec,
                battery_log_interval_sec=jc.battery_log_interval_sec,
            )
```

- [ ] **Step 5: Add `[jibot_client]` to config.toml** (keys + aligned comments; note plaintext-credential warning like `web-credentials.toml`).

- [ ] **Step 6: Run config + load smoke**

Run: `cd adaptor && python -m pytest tests/test_config.py -v && python -c "from config.config import get_config; get_config(); print('ok')"`
Expected: PASS + `ok`.

- [ ] **Step 7: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/main.py adaptor/tests/test_config.py
git commit -m "feat(config): add [jibot_client] and inject into JIBOT()

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Module 3 — WebUiConfig schema

### Task 3.1: `WebUiConfig` dataclass + `get_config` parse

**Files:**
- Modify: `adaptor/config/config.py` (new `WebUiConfig`; parse; add to `Config`)
- Modify: `adaptor/config/config.toml` (`[web_ui]` 198-202 → add new keys)
- Test: `adaptor/tests/test_web_ui_config.py` (create)

**Interfaces:**
- Produces: `WebUiConfig(enabled=False, host="127.0.0.1", port=8090, credentials_path="config/web-credentials.toml", http_request_timeout_sec=10.0, shutdown_timeout_sec=2.0, log_read_timeout_sec=5.0, state_stale_threshold_sec=15.0, max_post_body_bytes=65536, page_default_refresh_sec=5, test_output_poll_sec=2, password_min_length=12, password_forbidden_tokens=["set-me","changeme","password","admin"], camera_default_port=8080)`; `Config.web_ui: WebUiConfig`.

- [ ] **Step 1: Failing test**

```python
# adaptor/tests/test_web_ui_config.py
import unittest
from config.config import WebUiConfig, get_config


class TestWebUiConfig(unittest.TestCase):
    def test_defaults_match_current_literals(self):
        w = WebUiConfig()
        self.assertEqual(w.http_request_timeout_sec, 10.0)
        self.assertEqual(w.max_post_body_bytes, 65536)
        self.assertEqual(w.password_min_length, 12)
        self.assertIn("changeme", w.password_forbidden_tokens)

    def test_get_config_exposes_web_ui(self):
        self.assertIsInstance(get_config().web_ui, WebUiConfig)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_web_ui_config.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Add the dataclass + parse + Config field**

```python
@dataclass
class WebUiConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8090
    credentials_path: str = "config/web-credentials.toml"
    http_request_timeout_sec: float = 10.0
    shutdown_timeout_sec: float = 2.0
    log_read_timeout_sec: float = 5.0
    state_stale_threshold_sec: float = 15.0
    max_post_body_bytes: int = 65536
    page_default_refresh_sec: int = 5
    test_output_poll_sec: int = 2
    password_min_length: int = 12
    password_forbidden_tokens: List[str] = field(
        default_factory=lambda: ["set-me", "changeme", "password", "admin"]
    )
    camera_default_port: int = 8080
```

`get_config`: `web_ui = WebUiConfig(**config_dict.get("web_ui", {}))`; add to `Config`.

- [ ] **Step 4: Add the new keys to config.toml `[web_ui]`** (keep existing 4; append the rest with aligned comments + file:line provenance noted in a header comment).

- [ ] **Step 5: Run tests**

Run: `cd adaptor && python -m pytest tests/test_web_ui_config.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/tests/test_web_ui_config.py
git commit -m "feat(config): add WebUiConfig schema for [web_ui]

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3.2: Wire WebUiConfig into web/main.py, credentials, server, render

**Files:**
- Modify: `adaptor/web/main.py` (53-100: use `config.web_ui` instead of raw dict)
- Modify: `adaptor/web/credentials.py` (`load_credentials(path, *, min_length=12, forbidden_tokens=(...))`)
- Modify: `adaptor/web/server.py` (35-36 constants → from config; 129/171/222/189 timeouts/port → from config; `WebUi.__init__` gains `web_cfg: WebUiConfig`)
- Modify: `adaptor/web/render.py` (534/596/1007 refresh defaults → from passed config)
- Test: `adaptor/tests/test_web_server.py`, `adaptor/tests/test_web_render.py` (extend), `adaptor/tests/test_configio.py`

**Interfaces:**
- Consumes: `Config.web_ui` (Task 3.1).
- Produces: `WebUi.__init__(..., web_cfg: WebUiConfig | None = None)`; `load_credentials(path, *, min_length=12, forbidden_tokens=("set-me","changeme","password","admin"))`.

- [ ] **Step 1: Failing test — credentials honors injected policy**

```python
# adaptor/tests/test_web_credentials_policy.py (or extend existing)
import unittest, tempfile, os
from web.credentials import load_credentials


class TestCredPolicy(unittest.TestCase):
    def _write(self, body):
        fd, p = tempfile.mkstemp(suffix=".toml"); os.close(fd)
        open(p, "w").write(body); return p

    def test_custom_min_length_enforced(self):
        p = self._write('username="u"\npassword="short1"\n')
        with self.assertRaises(ValueError):
            load_credentials(p, min_length=20)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_web_credentials_policy.py -v`
Expected: FAIL — `load_credentials() got an unexpected keyword argument 'min_length'`.

- [ ] **Step 3: Parametrize credentials.py**

```python
def load_credentials(path, *, min_length=12,
                     forbidden_tokens=("set-me", "changeme", "password", "admin")):
    ...
    if len(password) < min_length:
        raise ValueError(f"web-ui credentials: password must be >= {min_length} chars")
    low = password.lower()
    if any(token in low for token in forbidden_tokens):
        ...
```

Keep module constants `_MIN_LEN`/`_PLACEHOLDERS` as the default args' source (or inline the tuple/int as defaults).

- [ ] **Step 4: Use `config.web_ui` in web/main.py**

```python
    config = get_config()
    w = config.web_ui
    if not w.enabled:
        print("web_ui.enabled is false in config.toml; not starting.")
        return 0
    cred = load_credentials(
        w.credentials_path,
        min_length=w.password_min_length,
        forbidden_tokens=tuple(w.password_forbidden_tokens),
    )
    ...
    web = WebUi(specs=specs, controllers=controllers, monitors=monitors,
                senders=senders, credentials=cred, host=w.host, port=w.port,
                py=sys.executable, config_path=configio.CONFIG_PATH,
                validate_config=configio.validate_on_disk, video_url=video_url,
                web_cfg=w, ...)
```

- [ ] **Step 5: Replace server.py / render.py constants from `web_cfg`**

`WebUi.__init__` accepts `web_cfg`; store `self._web_cfg`. Replace `_MQTT_STATE_STALE_SEC`/`_MAX_BODY`/handler `timeout`/shutdown `join(timeout=…)`/log-read timeout/camera fallback port with `self._web_cfg.*` (fallback to current constants when `web_cfg is None`, to keep existing tests that construct `WebUi` without it green). Pass refresh defaults into `render` calls (`page_default_refresh_sec`, `test_output_poll_sec`).

- [ ] **Step 6: Run the WebUi test suites**

Run: `cd adaptor && python -m pytest tests/test_web_server.py tests/test_web_render.py tests/test_web_credentials_policy.py -v`
Expected: PASS (existing tests unaffected via `web_cfg=None` fallback).

- [ ] **Step 7: Commit**

```bash
git add adaptor/web/main.py adaptor/web/credentials.py adaptor/web/server.py adaptor/web/render.py adaptor/tests/test_web_credentials_policy.py
git commit -m "feat(web): drive WebUi from WebUiConfig (timeouts, limits, password policy)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Module 4 — [factsheet] config + /factsheet WebUi page

### Task 4.1: `FactsheetConfig` + extract `core/factsheet.py:build_factsheet`

**Files:**
- Modify: `adaptor/config/config.py` (new `FactsheetConfig`; parse; `Config.factsheet`)
- Create: `adaptor/core/factsheet.py`
- Modify: `adaptor/adapter_jibot.py` (`_build_factsheet` 2055-2129 → call `core.factsheet.build_factsheet`, inject `header_id`/`timestamp`/`simulation`/`video_streams`/`load_positions`)
- Modify: `adaptor/config/config.toml` (`[factsheet]`)
- Test: `adaptor/tests/test_factsheet_config.py` (create)

**Interfaces:**
- Produces: `FactsheetConfig(series_name="JIBOT", agv_kinematic="DIFF", agv_class="CARRIER", localization_types=["NATURAL"], navigation_types=["AUTONOMOUS"], coordinate_unit_position="mm", coordinate_unit_orientation="deg", speed_min=0.0, min_order_interval_sec=1.0)`; `Config.factsheet: FactsheetConfig`.
- Produces: `core.factsheet.build_factsheet(config, *, header_id, timestamp, simulation, load_positions, video_streams=None) -> dict`.
- Produces: `core.factsheet.INSTANT_ACTION_TYPES: tuple[str, ...]` — the **single source** for the factsheet `agvActions` catalog (moved verbatim from `adapter_jibot.py:1994`). The builder constructs `agvActions` from it internally (no injection), so the WebUi preview is identical to what the adapter publishes. The adapter keeps `Adapter.SUPPORTED_INSTANT_ACTIONS = INSTANT_ACTION_TYPES` (alias of the core constant) so its existing usages stay correct and can't drift.

> **Finding-1 resolution:** the action catalog must NOT be passed in as an argument (the WebUi process has no access to the adapter class). It lives in `core/factsheet.py` and both the adapter and the WebUi import it.

- [ ] **Step 1: Failing test — full-dict regression (byte-identity guard, Finding 2)**

The current factsheet is `adapter_jibot.py:2072-2128`. Lock the ENTIRE dict so any structural drift (missing/extra/renamed key, wrong nesting, changed `agvActions` composition) fails. Deployment-derived fields come from `cfg`; the hardcoded constants are pinned literally.

```python
# adaptor/tests/test_factsheet_config.py
import unittest
from config.config import get_config
from core.factsheet import build_factsheet, INSTANT_ACTION_TYPES


class TestFactsheet(unittest.TestCase):
    def test_full_factsheet_matches_expected_structure(self):
        cfg = get_config()
        fs = build_factsheet(cfg, header_id=7, timestamp="T", simulation=False,
                             load_positions=["slot1", "slot2"], video_streams=None)
        expected = {
            "headerId": 7,
            "timestamp": "T",
            "version": cfg.vehicle.vda_full_version,
            "manufacturer": cfg.vehicle.manufacturer,
            "serialNumber": cfg.vehicle.serial_number,
            "coordinateUnits": {"position": "mm", "orientation": "deg"},
            "simulation": False,
            "typeSpecification": {
                "seriesName": "JIBOT", "agvKinematic": "DIFF", "agvClass": "CARRIER",
                "localizationTypes": ["NATURAL"], "navigationTypes": ["AUTONOMOUS"],
            },
            "physicalParameters": {"speedMin": 0.0, "speedMax": float(cfg.settings.speed)},
            "protocolLimits": {
                "maxStringLens": {}, "maxArrayLens": {},
                "timing": {"minOrderInterval": 1.0,
                           "minStateInterval": float(cfg.settings.state_publish_delay)},
            },
            "protocolFeatures": {
                "optionalParameters": [],
                "agvActions": (
                    [{"actionType": a, "actionScopes": ["INSTANT"]}
                     for a in INSTANT_ACTION_TYPES]
                    + [{"actionType": "jibotCommand", "actionScopes": ["NODE", "EDGE"]}]
                ),
            },
            "agvGeometry": {},
            "loadSpecification": {"loadPositions": ["slot1", "slot2"]},
        }
        self.assertEqual(fs, expected)

    def test_video_streams_injected_only_when_present(self):
        cfg = get_config()
        self.assertNotIn("videoStreams",
                         build_factsheet(cfg, header_id=0, timestamp="T",
                                         simulation=False, load_positions=[],
                                         video_streams=None))
        fs = build_factsheet(cfg, header_id=0, timestamp="T", simulation=False,
                             load_positions=[], video_streams=["http://x/s"])
        self.assertEqual(fs["videoStreams"], ["http://x/s"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_factsheet_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.factsheet'`.

- [ ] **Step 3: Add `FactsheetConfig`** (defaults above) + parse `factsheet = FactsheetConfig(**config_dict.get("factsheet", {}))` + add to `Config`.

- [ ] **Step 4: Create `core/factsheet.py`** — move the dict construction out of `_build_factsheet`, reading config + injected runtime values:

```python
"""Pure VDA5050 factsheet builder shared by the adapter and the WebUi.
Runtime-only values (headerId/timestamp/simulation/loadPositions/videoStreams)
are injected by the caller; everything else comes from Config. The instant-
action catalog lives here as the single source so the WebUi /factsheet preview
matches what the adapter publishes."""
from typing import Any, Dict, List, Optional

# Single source for the factsheet agvActions catalog. MOVE this tuple verbatim
# from adapter_jibot.py:1994 (the full SUPPORTED_INSTANT_ACTIONS list); the
# adapter then aliases Adapter.SUPPORTED_INSTANT_ACTIONS = INSTANT_ACTION_TYPES.
INSTANT_ACTION_TYPES = (
    "cancelOrder", "stateRequest", "factsheetRequest", "startPause", "stopPause",
    "startCharging", "chargeInPlace", "initPosition", "jibotCommand",
    "syncJibotParams", "requestVideo", "requestLaser", "stopLaser",
    "setMapSnapshot", "getMap",
    # ... copy the REMAINDER of the tuple from adapter_jibot.py:1994 verbatim.
)


def build_factsheet(config, *, header_id, timestamp, simulation,
                    load_positions, video_streams=None) -> Dict[str, Any]:
    f = config.factsheet
    agv_actions = [
        {"actionType": a, "actionScopes": ["INSTANT"]} for a in INSTANT_ACTION_TYPES
    ]
    agv_actions.append({"actionType": "jibotCommand", "actionScopes": ["NODE", "EDGE"]})
    factsheet = {
        "headerId": header_id,
        "timestamp": timestamp,
        "version": config.vehicle.vda_full_version,
        "manufacturer": config.vehicle.manufacturer,
        "serialNumber": config.vehicle.serial_number,
        "coordinateUnits": {"position": f.coordinate_unit_position,
                            "orientation": f.coordinate_unit_orientation},
        "simulation": simulation,
        "typeSpecification": {
            "seriesName": f.series_name, "agvKinematic": f.agv_kinematic,
            "agvClass": f.agv_class, "localizationTypes": list(f.localization_types),
            "navigationTypes": list(f.navigation_types),
        },
        "physicalParameters": {"speedMin": f.speed_min,
                               "speedMax": float(config.settings.speed)},
        "protocolLimits": {"maxStringLens": {}, "maxArrayLens": {},
                           "timing": {"minOrderInterval": f.min_order_interval_sec,
                                      "minStateInterval": float(config.settings.state_publish_delay)}},
        "protocolFeatures": {"optionalParameters": [], "agvActions": agv_actions},
        "agvGeometry": {},
        "loadSpecification": {"loadPositions": list(load_positions)},
    }
    if video_streams:
        factsheet["videoStreams"] = video_streams
    return factsheet
```

- [ ] **Step 5: Rewire adapter `_build_factsheet` + alias the catalog**

In `adapter_jibot.py`, import the core constant at module top and replace the class-level tuple definition (line 1994) with an alias so there is exactly one source:

```python
from core.factsheet import INSTANT_ACTION_TYPES, build_factsheet
# ... in class Adapter body, where SUPPORTED_INSTANT_ACTIONS was defined:
    SUPPORTED_INSTANT_ACTIONS = INSTANT_ACTION_TYPES
```

Then delegate the builder (no `instant_action_types` arg — the catalog is internal to `core.factsheet`):

```python
    def _build_factsheet(self) -> Dict[str, Any]:
        tray = self.config.ezi_config.tray_slot_pin
        load_positions = [f"slot{i}" for i in range(1, len(tray) + 1)]
        video_streams = None
        if self.config.video.enabled and self.config.video.stream_topics:
            video_streams = self._video.stream_urls(list(self.config.video.stream_topics))
        return build_factsheet(
            self.config, header_id=self.factsheet_header_id,
            timestamp=utils.get_timestamp(), simulation=self._is_simulator(),
            load_positions=load_positions, video_streams=video_streams,
        )
```

Add an assertion test that the alias holds (catches future drift):

```python
    def test_adapter_action_catalog_is_core_single_source(self):
        from adapter_jibot import Adapter
        from core.factsheet import INSTANT_ACTION_TYPES
        self.assertIs(Adapter.SUPPORTED_INSTANT_ACTIONS, INSTANT_ACTION_TYPES)
```

- [ ] **Step 6: Run factsheet + order tests**

Run: `cd adaptor && python -m pytest tests/test_factsheet_config.py tests/test_adapter_jibot_v3_order.py -v`
Expected: PASS.

- [ ] **Step 7: Add `[factsheet]` to config.toml** (keys + comments) and commit:

```bash
git add adaptor/config/config.py adaptor/core/factsheet.py adaptor/adapter_jibot.py adaptor/config/config.toml adaptor/tests/test_factsheet_config.py
git commit -m "feat(factsheet): config-backed factsheet via shared core.factsheet builder

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 4.2: `validate_on_disk(path)` honors the edited file (Finding 4)

Today `configio.validate_on_disk()` (configio.py:159) calls `get_config()` with NO path, so it always validates the DEFAULT `config.toml` — not the file `_post_config` actually wrote (`self._config_path`). `/factsheet` would inherit the same bug. Fix it before adding the page.

**Files:**
- Modify: `adaptor/core/configio.py` (`validate_on_disk`)
- Modify: `adaptor/web/main.py` (bind validate to the edited path)
- Test: `adaptor/tests/test_configio.py` (extend)

**Interfaces:**
- Produces: `configio.validate_on_disk(path: Optional[Path] = None) -> Tuple[bool, str]` — passes `path` to `get_config(path)`; `None` keeps the default.

- [ ] **Step 1: Failing test**

```python
def test_validate_on_disk_uses_given_path(self):
    import tempfile, os
    from core import configio
    fd, p = tempfile.mkstemp(suffix=".toml"); os.close(fd)
    open(p, "w").write("mqtt_broker = 123\n")  # structurally invalid
    ok, msg = configio.validate_on_disk(p)
    self.assertFalse(ok)
    ok2, _ = configio.validate_on_disk(configio.CONFIG_PATH)
    self.assertTrue(ok2)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_configio.py -k validate_on_disk_uses_given_path -v`
Expected: FAIL — `validate_on_disk()` ignores the arg (currently takes no path), `TypeError` or wrong `True`.

- [ ] **Step 3: Add the path param**

```python
def validate_on_disk(path=None) -> Tuple[bool, str]:
    try:
        from config.config import get_config
        get_config(path) if path is not None else get_config()
        return True, "config valid"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
```

- [ ] **Step 4: Bind validate to the edited path in web/main.py**

Change `validate_config=configio.validate_on_disk` to:

```python
                validate_config=lambda: configio.validate_on_disk(configio.CONFIG_PATH),
```

(So WebUi validates exactly the file `config_path=configio.CONFIG_PATH` points at. For a per-instance config both must point to the same file.)

- [ ] **Step 5: Run + commit**

Run: `cd adaptor && python -m pytest tests/test_configio.py -v`
Expected: PASS.

```bash
git add adaptor/core/configio.py adaptor/web/main.py adaptor/tests/test_configio.py
git commit -m "fix(web): validate the edited config file, not the default config.toml

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 4.3: `/factsheet` WebUi page (view + edit, scalar-only — Finding 5)

> **Scope (Finding 5):** the WebUi `/config` and `/factsheet` forms edit FLAT SCALARS only (no `$EDITOR`/full-TOML route exists in the WebUi — that path is the TUI app). List/nested knobs (`localization_types`, `navigation_types`, `[dock.approach_params]`, `[pio_advanced]`, and the Module-5 tuning keys) are edited by editing `config.toml` directly (or via the TUI). The `/factsheet` form therefore exposes only the `[factsheet]` scalar fields; the page still PREVIEWS the full rendered factsheet (incl. lists) read-only.

**Files:**
- Modify: `adaptor/core/configio.py` (add `FACTSHEET_FIELDS` like `HOT_FIELDS`; `rewrite_scalar` already handles flat `[factsheet]` scalars)
- Modify: `adaptor/web/server.py` (GET `/factsheet` ~after 394; POST `/factsheet` ~after 434; `_post_factsheet`)
- Modify: `adaptor/web/render.py` (add `factsheet_page(fields, preview_json, csrf, q)`; add nav link)
- Test: `adaptor/tests/test_configio.py` (extend), `adaptor/tests/test_web_server.py` (extend)

**Interfaces:**
- Consumes: `configio.rewrite_scalar/current_value/load_raw/validate_on_disk` (existing); `core.factsheet.build_factsheet` (Task 4.1).
- Produces: `configio.FACTSHEET_FIELDS: List[Tuple[str,str,str,str]]`; `render.factsheet_page(...)`.

- [ ] **Step 1: Failing test — FACTSHEET_FIELDS + rewrite round-trips a [factsheet] scalar**

```python
def test_rewrite_factsheet_scalar(self):
    from core import configio
    text = '[factsheet]\nseries_name = "JIBOT"  # series\n'
    out = configio.rewrite_scalar(text, "factsheet", "series_name", '"X100"')
    self.assertIn('series_name = "X100"', out)
    self.assertIn("# series", out)  # comment preserved
```

- [ ] **Step 2: Run to verify it fails / passes**

Run: `cd adaptor && python -m pytest tests/test_configio.py -k factsheet -v`
Expected: FAIL only if `FACTSHEET_FIELDS` referenced before added; the `rewrite_scalar` itself already works — add a `FACTSHEET_FIELDS` existence assertion to force the change:

```python
def test_factsheet_fields_defined(self):
    from core import configio
    keys = {k for _, k, _, _ in configio.FACTSHEET_FIELDS}
    self.assertIn("series_name", keys)
    self.assertIn("min_order_interval_sec", keys)
```

- [ ] **Step 3: Add `FACTSHEET_FIELDS` to configio.py**

```python
FACTSHEET_FIELDS: List[Tuple[str, str, str, str]] = [
    ("factsheet", "series_name", "str", "Series name"),
    ("factsheet", "agv_kinematic", "str", "AGV kinematic"),
    ("factsheet", "agv_class", "str", "AGV class"),
    ("factsheet", "coordinate_unit_position", "str", "Position unit"),
    ("factsheet", "coordinate_unit_orientation", "str", "Orientation unit"),
    ("factsheet", "speed_min", "num", "Speed min"),
    ("factsheet", "min_order_interval_sec", "num", "Min order interval (s)"),
]
# localization_types / navigation_types are LISTS — not in this scalar form.
# Edit them directly in config.toml (or via the TUI). They still appear in the
# read-only JSON preview below.
```

- [ ] **Step 4: Add GET/POST `/factsheet` handlers in server.py**

GET (mirror `/config` at 394-401): build `fields` from `FACTSHEET_FIELDS` + `current_value`, build a read-only JSON preview via the SHARED builder — `build_factsheet(get_config(), header_id=0, timestamp="(preview)", simulation=False, load_positions=_preview_load_positions(get_config()), video_streams=None)` (no `instant_action_types` arg; the catalog is internal to `core.factsheet`, so the preview's `agvActions` matches the adapter's). `_preview_load_positions` mirrors the adapter's `slot{i}` derivation from `ezi_config.tray_slot_pin`. Render `render.factsheet_page(...)`.
POST (mirror `_post_config`): for each posted scalar field call `rewrite_scalar`, write `self._config_path`, then `self._validate_config()` (now path-bound, Task 4.2), redirect `/factsheet?msg=…` / `?err=…`.

> Reuse the exact CSRF + write + validate sequence from `_post_config` (server.py ~700-723) so behavior matches `/config`. Validation now checks the edited file (Task 4.2).

- [ ] **Step 5: Add `render.factsheet_page` + nav link** (copy `config_page` structure; render the JSON preview in a `<pre>` block; same flash/err styling).

- [ ] **Step 6: Failing+passing WebUi route test**

```python
def test_factsheet_get_returns_200_and_preview(self):
    # construct WebUi test client as existing tests do; GET /factsheet
    # assert 200 and "seriesName" in body
    ...
```

Run: `cd adaptor && python -m pytest tests/test_web_server.py -k factsheet -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add adaptor/core/configio.py adaptor/web/server.py adaptor/web/render.py adaptor/tests/test_configio.py adaptor/tests/test_web_server.py
git commit -m "feat(web): /factsheet page — view rendered factsheet + edit [factsheet] config

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Module 5 — Expose remaining adapter tuning knobs

> **Finding-3 correction:** these are NOT all mechanical `getattr` swaps. Sites fall into two injection modes; each task states which it uses.
>
> - **Mode A — config already in scope** (`self.config` on the adapter, `config_data` in `main.py`, or a config object already passed into the helper). Add the dataclass field (default = current literal) and replace the literal with `getattr(self.config.<section>, "<key>", <default>)`. Confirmed in-scope: all `adapter_jibot.py` sites; `main.py` sites; `SubprocessChargeCircuit(config_data.charge_circuit)` (main.py:719); airshower `ASWorkflow` (has `self.config_data`, instantiates `PIOMaster` at airshower.py:246).
> - **Mode B — util has NO config object** (`utils/sound.py:SoundPlayer`, `utils/ezi_motor.py:EziMotorClient`, `utils/pio.py:PIOMaster` use constructor/method default args). Add a constructor kwarg (default = current literal), store it as an instance attr, use that attr where the literal/method-default was, and WIRE the value from config at the instantiation site. Task 5.6 does these.

The full key/default/file:line list is in the spec §Module 5 (5a–5h).

> **RISKY knobs** (`jibot_ack_timeout_min_sec`, `node_unreached_delay_sec`, `dock_approach_unreached_delay_sec`) and **`[internal_actions]`** (`docking_status_action_id/type`, FMS contract) get a `# WARNING: do not change …` comment in config.toml. Same default-preserving mechanism.

**Mode-A pattern — example `sound_test_duration_sec` (adapter has `self.config`):**

- [ ] Add `sound_test_duration_sec: float = 10.0` to `SoundSettings`.
- [ ] Test:

```python
def test_sound_test_duration_default(self):
    from config.config import get_config
    self.assertEqual(get_config().sound_settings.sound_test_duration_sec, 10.0)
```

- [ ] Replace `adapter_jibot.py:2046`: `duration = getattr(self.config.sound_settings, "sound_test_duration_sec", 10.0)`.
- [ ] Run `cd adaptor && python -m pytest tests/ -v` → green; add the key to `config.toml`.

**Mode-B pattern — example `player_wait_timeout_sec` (SoundPlayer has no config):**

- [ ] Add `player_wait_timeout_sec: float = 1.0` to `SoundSettings` (config side).
- [ ] Add a kwarg to `SoundPlayer.__init__`: `player_wait_timeout_sec: float = 1.0`; store `self._player_wait_timeout = player_wait_timeout_sec`; use it at `utils/sound.py:83` (where `1.0` was).
- [ ] Wire at the construction site (grep `SoundPlayer(`): pass `player_wait_timeout_sec=config.sound_settings.player_wait_timeout_sec`.
- [ ] Test wiring: `SoundPlayer(player_wait_timeout_sec=0.5)._player_wait_timeout == 0.5`; bare default stays `1.0`.
- [ ] Run → green; add the key to `config.toml`.

### Task 5.1: `[settings]` group (5a) — Mode A
Add `acs_cmd_subscribe_interval_sec=1.0`, `tray_slot_poll_interval_sec=1.0`, `node_position_poll_interval_sec=0.2`, `standstill_poll_interval_sec=0.1`, `adapter_loop_sleep_sec=1.0`, `jibot_command_default_timeout_sec=3.0`, `jibot_ack_timeout_min_sec=0.1` (WARNING), `node_unreached_delay_sec=1.0` (WARNING) to `Settings`. All sites are `adapter_jibot.py` (self.config) or `main.py` (config_data) → direct getattr per spec 5a. Test defaults in `test_config.py`. Commit `feat(config): expose [settings] polling/timeout knobs`.

### Task 5.2: `[dock]` group (5b) — Mode A
Add `dock_wait_poll_interval_sec=0.2`, `dock_approach_poll_interval_sec=0.2`, `charging_start_poll_interval_sec=0.2`, `charging_stop_poll_interval_sec=0.2`, `dock_approach_unreached_delay_sec=1.0` (WARNING) to `DockConfig`. Replace the `poll_sec=0.2` defaults in the adapter `_wait_until_*` methods + the `1.0` confirm (all `self.config`). Test + commit.

### Task 5.3: `[charge_circuit]` + `[air_shower_pio]` (5f, 5g) — Mode A
`ChargeCircuitConfig`: `terminate_timeout_sec=3.0`, `off_timeout_sec=5.0`. `SubprocessChargeCircuit` already receives `config_data.charge_circuit` (main.py:719) → use the section at `utils/charge_circuit.py:132/138` (confirm the ctor stores the section, e.g. `self._cfg`; if it currently stores individual fields, store the section instead). `AirShowerPioConfig`: `poll_interval_sec=0.2`; `ASWorkflow` has `self.config_data` → use `self.config_data.air_shower_config.poll_interval_sec` in its `0.2` poll loops. Test + commit.

### Task 5.4: `[internal_actions]` (5h) — Mode A, WARNING
New `InternalActionsConfig(docking_status_action_id="__jibot_docking__", docking_status_action_type="dock")`, parse, add to `Config`. Replace `adapter_jibot.py:2044/2045` (self.config). config.toml block with prominent `# WARNING: FMS contract — do not change on existing deployments`. Test + commit.

### Task 5.5: adapter-scope sound/ezi/pio defaults (5c/5d/5e — Mode A part)
Add only the fields whose SITES are on the adapter/main: `EziConfig.motor_test_delay_sec=2.0` (main.py:745-754, config_data); `SoundSettings.sound_test_duration_sec=10.0` (adapter_jibot.py:2046, self.config); new `PioAdvancedConfig` with the adapter-side defaults used at `adapter_jibot.py:4146/4185/4189/4200/4311/4410` (self.config). Parse `pio_advanced`; add to `Config`. Test defaults + commit. (Util-resident parts of 5c/5d/5e are Task 5.6.)

### Task 5.6: util constructor injection (5c/5d/5e — Mode B)
Add config fields + constructor kwargs (default = current literal) + caller wiring for the three config-less utils. Each gets a wiring unit test (construct with a non-default kwarg → assert the stored attr; assert the bare default is unchanged).

- **`SoundPlayer`** (`utils/sound.py:18`): kwargs `player_wait_timeout_sec=1.0` (used at :83), `pactl_timeout_sec=2.0` (used at :92). Wire at the `SoundPlayer(` construction site (grep it) from `config.sound_settings.*`.
- **`EziMotorClient`** (`utils/ezi_motor.py:103`, `__init__(self, ip, port=3002, timeout=2)`): kwarg `poll_interval_sec=0.1`; store + use in its `0.1` poll loops. Wire at `main.py:734`: `EziMotorClient(config_data.ezi_config.ezi_motor, poll_interval_sec=config_data.ezi_config.ezi_motor_poll_interval_sec)`.
- **`PIOMaster`** (`utils/pio.py:6`, `__init__(self, port, baudrate, ezi_client=None, timeout=0.2)`): kwargs `socket_timeout_sec=0.2` (maps to the `timeout` arg), `connect_delay_sec=0.5` (:32), `read_frame_poll_sec=0.05` (:80), `read_frames_wait_sec=2.0` (:65), `send_wait_sec=2.0` (:88-100). Store as attrs; for method-level defaults use `def m(self, wait_sec=None): wait_sec = self._send_wait if wait_sec is None else wait_sec` so existing callers/tests stay green. Wire at every `PIOMaster(` site — `utils/airshower.py:246` (`ASWorkflow.config_data.pio_advanced.*`); grep for any other `PIOMaster(` sites and wire them too.

Run the full suite → green. Add the keys to `config.toml`. Commit `feat(config): inject pio/sound/ezi util tuning knobs from config`.

---

## Final verification

- [ ] **Full suite:** `cd adaptor && python -m pytest tests/ -v` → all green.
- [ ] **jibot-client tests:** included above under `adaptor/tests/`; confirm `test_jibot_client_*` green.
- [ ] **Config loads:** `cd adaptor && python -c "from config.config import get_config; c=get_config(); print('sections ok')"`.
- [ ] **WebUi smoke (manual):** start adapter+WebUi, open `/config` and `/factsheet`, edit one factsheet scalar, confirm save + "restart to apply" flash + `validate_on_disk` rejects a bad edit.

---

## Self-Review (done at write time)

- **Spec coverage:** M1→Tasks 1.1-1.4; M2→2.1-2.2; M3→3.1-3.2; M4→4.1 (config+builder), 4.2 (validate-path fix), 4.3 (page); M5→5.1-5.6 (full key list in spec). Factsheet page (new request)→4.3. ✅
- **Placeholder scan:** Module 5 groups tasks by injection mode (A/B) pointing at the spec's verbatim key/default/line list (intentional right-sizing, not a placeholder); every non-mechanical task carries real code. Step 1.3 references the existing in-repo, named order harness as a template — acceptable.
- **Type consistency:** `DockApproachParams.as_params()`, `_dock_approach_params()`, `build_factsheet(config, *, header_id, timestamp, simulation, load_positions, video_streams=None)` (NO `instant_action_types` — catalog is `core.factsheet.INSTANT_ACTION_TYPES`), `Adapter.SUPPORTED_INSTANT_ACTIONS = INSTANT_ACTION_TYPES`, `validate_on_disk(path=None)`, `WebUiConfig`, `JibotClientConfig`, `FactsheetConfig`, `PioAdvancedConfig`, `InternalActionsConfig` used identically across producing/consuming tasks. ✅

### Review findings addressed (2026-06-23)
- **Finding 1 (agvActions source):** catalog moved to single source `core.factsheet.INSTANT_ACTION_TYPES`; builder takes no `instant_action_types`; adapter aliases it; WebUi preview imports the same constant (Task 4.1, 4.3).
- **Finding 2 (byte-identity):** Task 4.1 Step 1 now asserts the FULL factsheet dict (structure + agvActions composition) + videoStreams injection.
- **Finding 3 (Module 5 not mechanical):** split into Mode A (config-in-scope) vs Mode B (util constructor injection + caller wiring); Task 5.6 designs the per-util injection.
- **Finding 4 (validate path):** Task 4.2 makes `validate_on_disk(path)` validate the edited file; web/main.py binds it.
- **Finding 5 (WebUi edit scope):** WebUi forms edit flat scalars only; lists/nested edited in `config.toml` (or TUI). Documented in Task 4.3 scope note (spec updated to match).
