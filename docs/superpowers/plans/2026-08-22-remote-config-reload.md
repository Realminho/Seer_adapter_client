# 원격 config reload / restart 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WCS가 EPR로 바꾼 설정을 현장 사람 없이 반영하도록 `reloadConfig` / `restartAdapter` instant action 두 개를 추가한다.

**Architecture:** 어댑터는 `self.config`와 `self._action_registry`를 통째로 갈아끼운다 — 확장·디스패치·factsheet가 실행 시점에 읽는 구조라 스왑이 다음 액션부터 반영된다. 부팅 때 값을 복사해 간 소비자(MQTT, 사운드, 루프 주기)는 스왑으로 안 바뀌므로, 그 필드가 실제로 변했으면 "재시작 필요"로 보고하고 별도 `restartAdapter`로 마무리한다. 재시작은 systemctl이 아니라 기존 graceful shutdown 경로를 타고 non-zero 종료해 systemd가 되살린다.

**Tech Stack:** Python 3.12, asyncio, unittest(`unittest.IsolatedAsyncioTestCase`), pytest 러너(`scripts/run-tests.sh`), VDA5050 instant action, systemd

**Spec:** `docs/superpowers/specs/2026-08-22-remote-config-reload-design.md`

## Global Constraints

- 코드 주석은 **한국어로만** 작성한다 (`CLAUDE.md`). 영어 주석 옆에 한국어를 덧붙이는 이중 표기는 하지 않는다. 주석은 "무엇을"이 아니라 **"왜"**를 적는다.
- 작업 복구 로그를 `WORKING/YYYY-MM-DD/HHMMSS-{session-name}.md`에 유지한다 (`CLAUDE.md`).
- 분류는 **기본 거부**다. 명시적으로 reload 된다고 적힌 것만 reload로 치고 나머지는 재시작 필요로 본다 (spec §4.4).
- `EquipmentParameterApplyResult` envelope(`adaptor/amr_parameter_publish.py:801`)은 **건드리지 않는다**. WCS와의 계약이다 (spec §4.7).
- `setParameters`는 이번 변경에서 **자동 reload 하지 않는다** (spec §3).
- 두 액션 모두 **IDLE일 때만** 수행한다. 게이트를 우회하는 `force` 파라미터는 만들지 않는다 (spec §5).
- 테스트 실행: 저장소 루트에서 `scripts/run-tests.sh <경로>` (러너가 `adaptor/`로 이동하므로 경로는 `adaptor/` 기준).
- 커밋 메시지 말미: `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `adaptor/core/config_reload.py` (신규) | 무엇이 reload 되고 무엇이 재시작을 요구하는지 **선언**하고 두 config를 비교한다. 어댑터를 import 하지 않는 순수 모듈 |
| `adaptor/tests/test_config_reload.py` (신규) | 분류 완전성 + 비교 함수 |
| `adaptor/tests/test_remote_reload_actions.py` (신규) | 게이트·두 액션의 어댑터 쪽 동작 |
| `adaptor/adapter_jibot.py` (수정) | `config_loader` 보관, 게이트, 두 핸들러, 분기 2개, 재시작 요청 |
| `adaptor/main.py` (수정) | 부팅 로드를 재현하는 loader 생성·주입, 루프의 재시작 감시, 종료 코드 |
| `adaptor/core/factsheet.py` (수정) | `INSTANT_ACTION_TYPES`에 두 타입 |
| `adaptor/core/registry.py` (수정) | WebUI 버튼 2개 |
| `adaptor/readme.md` (수정) | 두 액션과 "재시작 필요" 개념 |

---

### Task 1: 분류 테이블과 비교 함수

**Files:**
- Create: `adaptor/core/config_reload.py`
- Test: `adaptor/tests/test_config_reload.py`

**Interfaces:**
- Consumes: `config.config.Config` (dataclass, 최상위 28개 필드)
- Produces:
  - `RELOADABLE_WHOLE: frozenset[str]`
  - `RESTART_WHOLE: frozenset[str]`
  - `RELOADABLE_SUBFIELDS: dict[str, frozenset[str]]`
  - `restart_required_changes(old, new) -> list[str]` — 정렬된 점 경로 목록

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`adaptor/tests/test_config_reload.py`:

```python
"""무엇이 reload 되고 무엇이 재시작을 요구하는지 — 분류가 곧 이 기능의 안전장치다."""

import sys
import unittest
from dataclasses import fields, replace
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from config.config import Config, get_config
from core import config_reload


class ClassificationCompletenessTest(unittest.TestCase):
    def test_every_top_level_field_is_classified_exactly_once(self):
        declared = (
            config_reload.RELOADABLE_WHOLE
            | config_reload.RESTART_WHOLE
            | set(config_reload.RELOADABLE_SUBFIELDS)
        )
        self.assertEqual({f.name for f in fields(Config)}, declared)

    def test_the_three_groups_are_disjoint(self):
        split = set(config_reload.RELOADABLE_SUBFIELDS)
        self.assertEqual(config_reload.RELOADABLE_WHOLE & config_reload.RESTART_WHOLE, set())
        self.assertEqual(config_reload.RELOADABLE_WHOLE & split, set())
        self.assertEqual(config_reload.RESTART_WHOLE & split, set())

    def test_subfield_names_exist_on_the_real_config(self):
        config = get_config()
        for section, subs in config_reload.RELOADABLE_SUBFIELDS.items():
            owner = getattr(config, section)
            names = {f.name for f in fields(owner)}
            self.assertTrue(subs <= names, f"{section}: 없는 하위 필드 {subs - names}")


class RestartRequiredChangesTest(unittest.TestCase):
    def test_no_change_reports_nothing(self):
        config = get_config()
        self.assertEqual(config_reload.restart_required_changes(config, config), [])

    def test_reloadable_change_is_not_reported(self):
        old = get_config()
        new = replace(old, recipes=[])
        self.assertEqual(config_reload.restart_required_changes(old, new), [])

    def test_restart_whole_change_is_reported(self):
        old = get_config()
        new = replace(old, settings=replace(old.settings, map_id="other-map"))
        self.assertEqual(config_reload.restart_required_changes(old, new), ["settings"])

    def test_unlisted_subfield_of_a_split_section_is_reported(self):
        old = get_config()
        new = replace(
            old, pio_config=replace(old.pio_config, pio_serial_port="/dev/ttyUSB9")
        )
        self.assertEqual(
            config_reload.restart_required_changes(old, new),
            ["pio_config.pio_serial_port"],
        )

    def test_listed_subfield_of_a_split_section_is_not_reported(self):
        old = get_config()
        new = replace(old, pio_config=replace(old.pio_config, input_pins=[1, 2, 3]))
        self.assertEqual(config_reload.restart_required_changes(old, new), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_config_reload.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.config_reload'`

- [ ] **Step 3: 모듈을 만든다**

`adaptor/core/config_reload.py`:

```python
"""reload로 반영되는 설정과 재시작이 필요한 설정의 경계.

어댑터는 self.config를 통째로 갈아끼울 수 있다 — 확장이 adapter.config를 실행
시점에 읽기 때문이다. 하지만 부팅 때 값을 복사해 간 소비자(MQTTClient,
SoundPlayer, 루프 인자)는 스왑으로 바뀌지 않는다. 그 경계를 여기 한곳에 적는다.

기본 거부인 이유는 틀렸을 때의 결과가 비대칭이라서다. 재시작이 필요 없는 것을
필요하다고 말하면 성가실 뿐이지만, 반영 안 된 것을 반영됐다고 말하면 운영자는
바뀐 줄 알고 로봇을 내보낸다.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Dict, FrozenSet, List

# 통째로 reload 된다 — 확장·레지스트리·디스패치가 실행 시점에 읽는다
RELOADABLE_WHOLE: FrozenSet[str] = frozenset({
    "recipes",
    "actions",
    "action_modules",
    "pio_advanced",
    "air_shower_config",
    "elevator_config",
    "motion_rules",
    "dock",
    "charge",
    "manual_control",
    "jibot_status",
    "factsheet",
})

# 통째로 재시작이 필요하다 — 부팅 때 값을 복사해 간 소비자가 있다.
# internal_actions: adapter_jibot.py:378,:383 에서 _docking_status_action_id/_type 로 복사
# settings: map_id 가 _current_map_id 로 복사(:392), 주기값이 루프 인자로 캡처(:568,:582,:604)
# ezi_config: 주소와 핀 값이 섞여 있어 v1 에서는 통째로 둔다(기본 거부)
RESTART_WHOLE: FrozenSet[str] = frozenset({
    "mqtt_broker",
    "vehicle",
    "settings",
    "ezi_config",
    "sound_settings",
    "charge_circuit",
    "bms_ros",
    "hexplorer",
    "video",
    "adapter",
    "jibot_client",
    "web_ui",
    "internal_actions",
    "state_actions",
    "joystick",
})

# 한 섹션 안에서 갈리는 것. 여기 적힌 하위 필드만 reload 되고 나머지는 기본 거부다.
# 핀맵은 extensions/pio/__init__.py:791,:812,:822,:833 이 실행 시점에 읽는다.
# 반면 pio_serial_port/pio_baudrate 는 이미 열린 시리얼이 살아 있어 반영되지 않는다.
RELOADABLE_SUBFIELDS: Dict[str, FrozenSet[str]] = {
    "pio_config": frozenset({
        "input_pins",
        "output_pins",
        "output_pin_map",
        "output_signals",
    }),
}


def restart_required_changes(old: Any, new: Any) -> List[str]:
    """스왑으로 반영되지 않는 항목 중 값이 실제로 달라진 경로만 정렬해 돌려준다.

    바뀌지 않은 항목까지 싣으면 운영자가 매번 전체 목록을 보고 재시작 필요 여부를
    직접 가려야 한다. 실제 차이만 보고해야 목록이 곧 할 일이 된다.
    """
    changed: List[str] = []

    for name in sorted(RESTART_WHOLE):
        if getattr(old, name) != getattr(new, name):
            changed.append(name)

    for section, reloadable in RELOADABLE_SUBFIELDS.items():
        old_section = getattr(old, section)
        new_section = getattr(new, section)
        for field in fields(old_section):
            if field.name in reloadable:
                continue
            if getattr(old_section, field.name) != getattr(new_section, field.name):
                changed.append(f"{section}.{field.name}")

    return sorted(changed)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `scripts/run-tests.sh tests/test_config_reload.py -v`
Expected: PASS (8개)

완전성 테스트가 실패하면 `Config`에 내가 모르는 필드가 있다는 뜻이다. 그 필드를 실제로
소비하는 곳을 찾아(`grep -rn "config\.<이름>" adaptor/ --include=*.py`) 부팅 때 복사되면
`RESTART_WHOLE`, 실행 시점에 읽히면 `RELOADABLE_WHOLE`에 넣는다. **추측으로 넣지 않는다.**

- [ ] **Step 5: 커밋**

```bash
git add adaptor/core/config_reload.py adaptor/tests/test_config_reload.py
git commit -m "feat(config): reload 가능 설정과 재시작 필요 설정의 경계 선언

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: config_loader 주입 — overrides 보존

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`Adapter.__init__`, 186~200 부근)
- Modify: `adaptor/main.py` (`main()`의 config 로드 760~765, `Adapter(...)` 936~942)
- Test: `adaptor/tests/test_remote_reload_actions.py` (신규)

**Interfaces:**
- Produces:
  - `Adapter(..., config_loader: Optional[Callable[[], Config]] = None)` → `self._config_loader`
  - `main.make_config_loader(config_path, overrides, extensions_path, recipes_path) -> Callable[[], Config]`

**왜 필요한가.** 어댑터는 자기 config가 어떻게 만들어졌는지 모른다. `main.py:760`이
`get_config_with_fallback(config_path=…, overrides=…, extensions_path=…, recipes_path=…)`로
읽지만 `main.py:936`의 `Adapter(...)`에는 **`overrides`가 넘어가지 않는다**. 어댑터가 경로만
가지고 `get_config()`를 다시 부르면 로봇별 serial_number / vehicle_ip / ezi 주소 / 브로커가
`config.toml` 기본값으로 조용히 되돌아간다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`adaptor/tests/test_remote_reload_actions.py`:

```python
"""원격 reload / restart 액션 — 게이트와 스왑 동작."""

import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_ROOT.parent
for p in (ADAPTER_ROOT, REPO_ROOT / "jibot-simulator", REPO_ROOT / "jibot-client" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import main as adapter_main
from adapter_jibot import Adapter


class ConfigLoaderInjectionTest(unittest.TestCase):
    def test_adapter_keeps_the_injected_loader(self):
        sentinel = object()
        adapter = Adapter(config_loader=lambda: sentinel)
        self.assertIs(adapter._config_loader(), sentinel)

    def test_adapter_without_a_loader_has_none(self):
        self.assertIsNone(Adapter()._config_loader)

    def test_make_config_loader_reapplies_overrides(self):
        """robots.hcl override 가 reload 후에도 살아 있어야 한다."""
        loader = adapter_main.make_config_loader(
            config_path=None,
            overrides={"vehicle": {"serial_number": "TEST-ROBOT-9"}},
            extensions_path=None,
            recipes_path=None,
        )
        self.assertEqual(loader().vehicle.serial_number, "TEST-ROBOT-9")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'config_loader'`

- [ ] **Step 3: 최소 구현**

`adaptor/adapter_jibot.py` — `Adapter.__init__` 시그니처에 인자를 더하고(기존 인자 뒤),
`self.config` 대입 직후에 보관한다.

```python
        robots_path: Optional[Union[str, Path]] = None,
        config_loader: Optional[Callable[[], Config]] = None,
    ) -> None:
```

```python
        self.config = config if config is not None else get_config()
        # 부팅 로드를 그대로 재현하는 callable. reload 가 경로만 가지고 get_config 를
        # 다시 부르면 robots.hcl override(serial_number/ip/ezi/브로커)가 config.toml
        # 기본값으로 조용히 되돌아간다. 그래서 만들어진 방법 자체를 주입받는다.
        self._config_loader = config_loader
```

`Callable`이 `typing` import에 없으면 추가한다.

`adaptor/main.py` — `config_error_path` 위쪽(모듈 수준)에 loader 팩토리를 둔다.

```python
def make_config_loader(config_path, overrides, extensions_path, recipes_path):
    """부팅과 동일한 인자로 config 를 다시 읽는 callable 을 만든다.

    reload 가 이 함수를 통해서만 다시 읽게 해서, 부팅 경로와 reload 경로가 인자를
    따로 관리하다 어긋나는 일을 없앤다.
    """

    def load():
        return get_config(
            config_path=config_path,
            overrides=overrides,
            extensions_path=extensions_path,
            recipes_path=recipes_path,
        )

    return load
```

`main()`의 `Adapter(...)` 호출(936~942)에 인자를 더한다.

```python
        adapter = Adapter(
            config=config_data,
            config_path=config_path,
            extensions_path=extensions_path,
            recipes_path=recipes_path,
            robots_path=cli_args.robots,
            config_loader=make_config_loader(
                config_path, overrides, extensions_path, recipes_path
            ),
        )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py -v`
Expected: PASS (3개)

- [ ] **Step 5: 회귀 확인**

Run: `scripts/run-tests.sh tests/test_config_error_path.py tests/test_fleet_registry.py -q`
Expected: PASS — `Adapter` 생성 규약과 `main` 부팅 경로를 건드렸으므로 함께 본다.

- [ ] **Step 6: 커밋**

```bash
git add adaptor/adapter_jibot.py adaptor/main.py adaptor/tests/test_remote_reload_actions.py
git commit -m "feat(adapter): 부팅 로드를 재현하는 config_loader 주입

reload 가 경로만으로 get_config 를 다시 부르면 robots.hcl override 가 유실된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: IDLE 게이트

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`_manual_blocked_reason` 6360 옆에 나란히)
- Test: `adaptor/tests/test_remote_reload_actions.py`

**Interfaces:**
- Produces: `Adapter._reload_blocked_reason() -> Optional[str]` — 막힌 이유 문자열, 가능하면 `None`

게이트가 필요한 이유는 recipe 클로저가 아니라 **확장이다.** 확장은 `adapter.config`를 실행
중에 읽으므로, 액션 중간에 스왑하면 한 액션이 옛 핀맵과 새 핀맵을 섞어 쓸 수 있다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`adaptor/tests/test_remote_reload_actions.py`에 추가:

```python
class ReloadGateTest(unittest.TestCase):
    def _idle_adapter(self):
        adapter = Adapter()
        adapter.order = None
        adapter._work_in_progress = None
        adapter.current_order_step = None
        adapter._manual_control_active = False
        adapter._active_action_steps = {}
        adapter._order_background_action_tasks = set()
        adapter._order_exclusive_background_action_tasks = set()
        return adapter

    def test_idle_adapter_is_not_blocked(self):
        self.assertIsNone(self._idle_adapter()._reload_blocked_reason())

    def test_work_in_progress_blocks(self):
        adapter = self._idle_adapter()
        adapter._work_in_progress = "loading"
        self.assertIn("loading", adapter._reload_blocked_reason())

    def test_running_action_blocks(self):
        adapter = self._idle_adapter()
        adapter._active_action_steps = {"a1": "pioWriteOut"}
        self.assertIn("action", adapter._reload_blocked_reason())

    def test_manual_control_blocks(self):
        adapter = self._idle_adapter()
        adapter._manual_control_active = True
        self.assertIn("manual", adapter._reload_blocked_reason())

    def test_queued_order_step_blocks(self):
        adapter = self._idle_adapter()
        adapter.current_order_step = object()
        self.assertIn("order", adapter._reload_blocked_reason())
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py::ReloadGateTest -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_reload_blocked_reason'`

- [ ] **Step 3: 최소 구현**

`adaptor/adapter_jibot.py`, `_manual_blocked_reason` 바로 아래:

```python
    def _reload_blocked_reason(self) -> Optional[str]:
        """설정 스왑/재시작이 막힌 이유. 가능하면 None.

        확장은 adapter.config 를 실행 중에 읽는다. 액션 도중에 스왑하면 한 액션이
        옛 핀맵과 새 핀맵을 섞어 쓸 수 있어, 조용히 틀린 IO 를 내보낸다.
        """
        if self._work_in_progress is not None:
            return f"busy with {self._work_in_progress} work"
        if self._manual_control_active:
            return "manual control active"
        if self.current_order_step is not None or not self.order_queue.empty():
            return "order in progress"
        if self.order is not None and self._order_motion_in_flight():
            return "order motion in flight"
        running = (
            len(self._active_action_steps)
            + len(self._order_background_action_tasks)
            + len(self._order_exclusive_background_action_tasks)
        )
        if running:
            return f"{running} action(s) still running"
        return None
```

- [ ] **Step 4: 통과를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py -v`
Expected: PASS (8개)

- [ ] **Step 5: 커밋**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_remote_reload_actions.py
git commit -m "feat(adapter): 설정 스왑을 막아야 하는 상태를 한곳에서 판정

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: reloadConfig 액션

**Files:**
- Modify: `adaptor/core/factsheet.py:13` (`INSTANT_ACTION_TYPES`)
- Modify: `adaptor/adapter_jibot.py` (핸들러 + 6231~6313 분기 체인)
- Modify: `adaptor/readme.md`
- Test: `adaptor/tests/test_remote_reload_actions.py`

**Interfaces:**
- Consumes: `core.config_reload.restart_required_changes` (Task 1), `self._config_loader` (Task 2), `self._reload_blocked_reason()` (Task 3)
- Produces: `Adapter._handle_reload_config_instant_action(action) -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`adaptor/tests/test_remote_reload_actions.py`에 추가:

```python
from dataclasses import replace
from types import SimpleNamespace

from core.factsheet import INSTANT_ACTION_TYPES


class ReloadConfigActionTest(unittest.TestCase):
    def _adapter_with_recorder(self, loader):
        adapter = Adapter(config_loader=loader)
        adapter.order = None
        adapter._work_in_progress = None
        adapter.current_order_step = None
        adapter._manual_control_active = False
        adapter._active_action_steps = {}
        adapter._order_background_action_tasks = set()
        adapter._order_exclusive_background_action_tasks = set()
        statuses = []
        adapter._update_instant_action_status = (
            lambda action_id, status, result_description=None: statuses.append(
                (status, result_description or "")
            )
        )
        published = []
        adapter.publish_factsheet = lambda: published.append(True)
        return adapter, statuses, published

    def test_reload_swaps_config_and_registry(self):
        adapter, statuses, published = self._adapter_with_recorder(lambda: None)
        old_config = adapter.config
        old_registry = adapter._action_registry
        adapter._config_loader = lambda: replace(old_config, recipes=[])

        adapter._handle_reload_config_instant_action(SimpleNamespace(action_id="a1"))

        self.assertIsNot(adapter.config, old_config)
        self.assertEqual(adapter.config.recipes, [])
        self.assertIsNot(adapter._action_registry, old_registry)
        self.assertEqual(published, [True])
        self.assertEqual(statuses[-1][0], ActionStatus.FINISHED)

    def test_reload_reports_fields_that_need_a_restart(self):
        adapter, statuses, _ = self._adapter_with_recorder(lambda: None)
        old_config = adapter.config
        adapter._config_loader = lambda: replace(
            old_config, settings=replace(old_config.settings, map_id="other-map")
        )

        adapter._handle_reload_config_instant_action(SimpleNamespace(action_id="a1"))

        self.assertIn("settings", statuses[-1][1])

    def test_reload_keeps_the_old_config_when_loading_fails(self):
        adapter, statuses, published = self._adapter_with_recorder(lambda: None)
        old_config = adapter.config

        def boom():
            raise ValueError("recipe 'x' must contain at least one step")

        adapter._config_loader = boom
        adapter._handle_reload_config_instant_action(SimpleNamespace(action_id="a1"))

        self.assertIs(adapter.config, old_config)
        self.assertEqual(published, [])
        self.assertEqual(statuses[-1][0], ActionStatus.FAILED)
        self.assertIn("at least one step", statuses[-1][1])

    def test_reload_without_a_loader_is_rejected(self):
        adapter, statuses, _ = self._adapter_with_recorder(None)
        adapter._config_loader = None
        old_config = adapter.config

        adapter._handle_reload_config_instant_action(SimpleNamespace(action_id="a1"))

        self.assertIs(adapter.config, old_config)
        self.assertEqual(statuses[-1][0], ActionStatus.FAILED)

    def test_reload_is_rejected_while_busy(self):
        adapter, statuses, published = self._adapter_with_recorder(lambda: None)
        adapter._work_in_progress = "unloading"
        old_config = adapter.config

        adapter._handle_reload_config_instant_action(SimpleNamespace(action_id="a1"))

        self.assertIs(adapter.config, old_config)
        self.assertEqual(published, [])
        self.assertIn("unloading", statuses[-1][1])

    def test_the_action_type_is_advertised(self):
        self.assertIn("reloadConfig", INSTANT_ACTION_TYPES)
```

파일 상단 import에 `from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus`를 더한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py::ReloadConfigActionTest -v`
Expected: FAIL — `AttributeError: … '_handle_reload_config_instant_action'`

- [ ] **Step 3: 최소 구현**

`adaptor/core/factsheet.py:13`의 튜플 끝(`"gotoNearestNode",` 뒤)에 추가:

```python
    "reloadConfig",
    "restartAdapter",
```

`adaptor/adapter_jibot.py` — `_handle_set_parameters_instant_action` 아래에 핸들러를 둔다.

```python
    def _handle_reload_config_instant_action(self, action: Any) -> None:
        """설정 파일을 다시 읽어 메모리 config 와 action registry 를 갈아끼운다.

        부작용이 있는 단계는 마지막 두 줄뿐이다. 그 앞의 로드·검증이 실패하면 옛
        config 가 그대로 살아 있으므로 롤백 코드가 따로 필요 없다.
        """
        from core.config_reload import restart_required_changes

        action_id = getattr(action, "action_id", "")

        blocked = self._reload_blocked_reason()
        if blocked is not None:
            self._update_instant_action_status(
                action_id, ActionStatus.FAILED,
                result_description=f"reload rejected: {blocked}",
            )
            print(f"[RELOAD REJECTED] {blocked}")
            return

        if self._config_loader is None:
            # 경로만 가지고 다시 읽으면 robots.hcl override 가 유실된다. 추측해서
            # 읽느니 거부하는 편이 낫다.
            self._update_instant_action_status(
                action_id, ActionStatus.FAILED,
                result_description="reload unavailable: config loader not injected",
            )
            return

        try:
            new_config = self._config_loader()
            new_registry = build_registry_from_config(
                getattr(new_config, "actions", []),
                first_party_specs=first_party_action_specs(new_config),
            )
            validate_state_actions(getattr(new_config, "state_actions", ()), new_registry)
            validate_joystick_actions(getattr(new_config, "joystick", None), new_registry)
            needs_restart = restart_required_changes(self.config, new_config)
        except Exception as exc:  # noqa: BLE001 - 로더 오류를 그대로 운영자에게 전달
            self._update_instant_action_status(
                action_id, ActionStatus.FAILED,
                result_description=f"reload failed: {type(exc).__name__}: {exc}",
            )
            print(f"[RELOAD FAILED] {type(exc).__name__}: {exc}")
            return

        self.config = new_config
        self._action_registry = new_registry
        self.publish_factsheet()

        summary = f"reloaded: recipes={len(getattr(new_config, 'recipes', []))}"
        if needs_restart:
            summary += "; restart required for: " + ", ".join(needs_restart)
        self._update_instant_action_status(
            action_id, ActionStatus.FINISHED, result_description=summary
        )
        print(f"[RELOAD OK] {summary}")
```

분기 체인(6231~6313)의 `setParameters` 분기 아래에 추가:

```python
            elif action.action_type == "reloadConfig":
                self._handle_reload_config_instant_action(action)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py -v`
Expected: PASS (14개)

- [ ] **Step 5: factsheet 회귀 확인**

Run: `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -q -k factsheet`
Expected: PASS — 광고 목록이 바뀌었으므로 factsheet 관련 테스트를 함께 본다.

- [ ] **Step 6: 문서**

`adaptor/readme.md`에 아래 절을 추가한다(설정 파일 설명 근처).

```markdown
### 설정 반영 — reloadConfig

설정은 부팅 때 한 번 읽는다. 파일을 고쳐도(WebUI 저장, EPR setParameters, 직접 편집)
그 자체로는 돌고 있는 어댑터에 반영되지 않는다.

`reloadConfig` instant action이 파일을 다시 읽어 메모리 config와 action registry를
갈아끼우고 factsheet를 재발행한다. IDLE일 때만 동작한다 — 주문·실행 중 액션·수동 조작·
loading/unloading 중이면 이유와 함께 FAILED다. 확장이 설정을 실행 중에 읽기 때문에,
액션 도중 갈아끼우면 한 액션이 옛 값과 새 값을 섞어 쓴다.

결과의 `restart required for: …` 목록은 **파일에는 들어갔지만 메모리에는 반영되지 않은
항목**이다. 브로커 주소, 시리얼 포트, 루프 주기처럼 부팅 때 값이 복사되는 설정이 여기
해당한다. 그 항목들은 `restartAdapter`나 서비스 재시작이 있어야 실제로 적용된다.

무엇이 reload되고 무엇이 재시작을 요구하는지는 `core/config_reload.py`가 단일 출처다.
```

- [ ] **Step 7: 커밋**

```bash
git add adaptor/core/factsheet.py adaptor/adapter_jibot.py adaptor/readme.md adaptor/tests/test_remote_reload_actions.py
git commit -m "feat(adapter): reloadConfig instant action

설정 파일을 다시 읽어 config/registry 를 갈아끼우고 factsheet 를 재발행한다.
스왑으로 반영되지 않는 항목은 결과에 재시작 필요로 싣는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: restartAdapter 액션

**Files:**
- Modify: `adaptor/adapter_jibot.py` (핸들러, 재시작 요청 상태, 분기)
- Modify: `adaptor/main.py` (루프의 재시작 감시, 종료 코드)
- Modify: `adaptor/readme.md`
- Test: `adaptor/tests/test_remote_reload_actions.py`

**Interfaces:**
- Consumes: `self._reload_blocked_reason()` (Task 3), `self._adapter_started_at` (`adapter_jibot.py:252`)
- Produces:
  - `Adapter.MIN_UPTIME_BEFORE_RESTART_SEC: float = 30.0`
  - `Adapter.restart_requested: bool`
  - `Adapter._handle_restart_adapter_instant_action(action) -> None`
  - `main.RESTART_EXIT_CODE: int = 75`

**폭주 방지가 왜 필요한가.** systemd 기본값은 `StartLimitBurst=5` / `StartLimitIntervalSec=10s`이고
두 유닛 생성기(`scripts/setup-adaptor-service.sh:868`, `adaptor/install-systemd-service.sh:127`)
모두 재정의하지 않는다. 재시작이 빠르게 반복되면 유닛이 failed로 주저앉아 **사람이 갈 때까지
안 뜬다**. 기동 후 최소 가동시간 미만이면 거부하면, 상태를 파일에 남기지 않고도 재시작을 넘어
성립하는 게이트가 된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`adaptor/tests/test_remote_reload_actions.py`에 추가:

```python
import time


class RestartAdapterActionTest(unittest.TestCase):
    def _adapter(self, uptime_sec):
        adapter = Adapter()
        adapter.order = None
        adapter._work_in_progress = None
        adapter.current_order_step = None
        adapter._manual_control_active = False
        adapter._active_action_steps = {}
        adapter._order_background_action_tasks = set()
        adapter._order_exclusive_background_action_tasks = set()
        adapter._adapter_started_at = time.time() - uptime_sec
        statuses = []
        adapter._update_instant_action_status = (
            lambda action_id, status, result_description=None: statuses.append(
                (status, result_description or "")
            )
        )
        return adapter, statuses

    def test_restart_is_requested_when_idle_and_settled(self):
        adapter, statuses = self._adapter(uptime_sec=120)
        adapter._handle_restart_adapter_instant_action(SimpleNamespace(action_id="a1"))
        self.assertTrue(adapter.restart_requested)
        self.assertEqual(statuses[-1][0], ActionStatus.FINISHED)

    def test_restart_is_rejected_right_after_boot(self):
        adapter, statuses = self._adapter(uptime_sec=1)
        adapter._handle_restart_adapter_instant_action(SimpleNamespace(action_id="a1"))
        self.assertFalse(adapter.restart_requested)
        self.assertEqual(statuses[-1][0], ActionStatus.FAILED)
        self.assertIn("uptime", statuses[-1][1])

    def test_restart_is_rejected_while_busy(self):
        adapter, statuses = self._adapter(uptime_sec=120)
        adapter._work_in_progress = "loading"
        adapter._handle_restart_adapter_instant_action(SimpleNamespace(action_id="a1"))
        self.assertFalse(adapter.restart_requested)
        self.assertIn("loading", statuses[-1][1])

    def test_the_result_warns_about_the_group_restart(self):
        adapter, statuses = self._adapter(uptime_sec=120)
        adapter._handle_restart_adapter_instant_action(SimpleNamespace(action_id="a1"))
        self.assertIn("group", statuses[-1][1])

    def test_the_action_type_is_advertised(self):
        self.assertIn("restartAdapter", INSTANT_ACTION_TYPES)


class RestartExitPathTest(unittest.IsolatedAsyncioTestCase):
    async def test_wait_tick_returns_true_when_restart_requested(self):
        adapter = Adapter()
        adapter.restart_requested = True
        self.assertTrue(await adapter_main.wait_tick_or_restart(adapter, 0.01))

    async def test_wait_tick_returns_false_on_a_normal_tick(self):
        adapter = Adapter()
        adapter.restart_requested = False
        self.assertFalse(await adapter_main.wait_tick_or_restart(adapter, 0.01))

    async def test_wait_tick_without_an_adapter_is_a_normal_tick(self):
        self.assertFalse(await adapter_main.wait_tick_or_restart(None, 0.01))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py::RestartAdapterActionTest -v`
Expected: FAIL — `AttributeError: … '_handle_restart_adapter_instant_action'`

- [ ] **Step 3: 어댑터 쪽 구현**

`adaptor/adapter_jibot.py` — 클래스 상수와 상태를 더한다. `_adapter_started_at` 대입(252) 옆:

```python
        # 재시작 요청 플래그. main 의 루프가 이걸 보고 정상 종료 경로로 빠진다.
        self.restart_requested: bool = False
```

클래스 상수(다른 상수들과 같은 자리):

```python
    # 기동 직후의 재시작 요청은 거부한다. systemd 기본 StartLimitBurst=5/10s 라
    # 빠른 반복 재시작은 유닛을 failed 로 주저앉혀 사람이 갈 때까지 안 뜨게 만든다.
    MIN_UPTIME_BEFORE_RESTART_SEC: float = 30.0
```

핸들러:

```python
    def _handle_restart_adapter_instant_action(self, action: Any) -> None:
        """의도된 재시작을 요청한다. 실제 종료는 main 의 정상 종료 경로가 한다.

        os._exit 로 곧장 나가지 않는 이유는 main.py 의 finally 가 connection OFFLINE
        (retained) 발행과 MQTT/vehicle 해제를 이미 하기 때문이다. 그 경로를 건너뛰면
        FMS 는 '내려간다'가 아니라 '죽었다'로 읽는다.
        """
        action_id = getattr(action, "action_id", "")

        blocked = self._reload_blocked_reason()
        if blocked is not None:
            self._update_instant_action_status(
                action_id, ActionStatus.FAILED,
                result_description=f"restart rejected: {blocked}",
            )
            return

        uptime = time.time() - self._adapter_started_at
        if uptime < self.MIN_UPTIME_BEFORE_RESTART_SEC:
            self._update_instant_action_status(
                action_id, ActionStatus.FAILED,
                result_description=(
                    f"restart rejected: uptime {uptime:.0f}s < "
                    f"{self.MIN_UPTIME_BEFORE_RESTART_SEC:.0f}s"
                ),
            )
            return

        self._update_instant_action_status(
            action_id, ActionStatus.FINISHED,
            result_description=(
                "restarting; in a multi-robot deployment this is a group restart"
            ),
        )
        print("[RESTART REQUESTED] instant action; exiting through shutdown path")
        self.restart_requested = True
```

분기 체인에 추가:

```python
            elif action.action_type == "restartAdapter":
                self._handle_restart_adapter_instant_action(action)
```

- [ ] **Step 4: main 쪽 구현**

`adaptor/main.py` — 모듈 상수와 대기 헬퍼:

```python
# 의도된 재시작의 종료 코드. journal 에서 크래시와 구분하려고 0 도 1 도 아닌 값을 쓴다.
RESTART_EXIT_CODE = 75


async def wait_tick_or_restart(adapter, loop_sleep):
    """루프 한 틱을 쉬고, 재시작이 요청됐으면 True 를 돌려준다."""
    if adapter is not None and getattr(adapter, "restart_requested", False):
        return True
    await asyncio.sleep(loop_sleep)
    return bool(adapter is not None and getattr(adapter, "restart_requested", False))
```

`main()`의 `while True` 루프(988~994)를 바꾼다:

```python
        while True:
            try:
                if await wait_tick_or_restart(adapter, _loop_sleep):
                    print("[RESTART] leaving the run loop through the shutdown path")
                    break
            except Exception as e:
                print(f"ACS Adapter flow error: {e}")
                await asyncio.sleep(_loop_sleep)
```

`finally` 블록 **뒤**(함수 끝)에 종료를 둔다:

```python
    if adapter is not None and getattr(adapter, "restart_requested", False):
        # finally 가 OFFLINE 발행과 해제를 끝낸 뒤에 나간다. SystemExit 는
        # BaseException 이라 위쪽 except Exception 에 걸리지 않는다.
        sys.exit(RESTART_EXIT_CODE)
```

- [ ] **Step 5: 통과를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py -v`
Expected: PASS (22개)

- [ ] **Step 6: 회귀 확인**

Run: `scripts/run-tests.sh tests/test_background_task_supervision.py tests/test_config_error_path.py -q`
Expected: PASS — 종료 경로를 건드렸으므로 함께 본다.

- [ ] **Step 7: 문서**

`adaptor/readme.md`의 앞 절(reloadConfig) 바로 뒤에 이어 붙인다.

```markdown
### 원격 재시작 — restartAdapter

`restartAdapter` instant action은 어댑터를 정상 종료 경로로 내보낸다 — connection
OFFLINE(retained)을 발행하고 MQTT/vehicle을 해제한 뒤 종료 코드 75로 나간다. systemd
(`Restart=on-failure`, `RestartSec=3`)가 3초 뒤 되살린다. systemctl 권한은 필요 없다.

두 가지 거부 조건이 있다.

- IDLE이 아니면 거부한다(reloadConfig와 같은 게이트).
- 기동 후 30초가 지나지 않았으면 거부한다. systemd 기본값이 `StartLimitBurst=5` /
  `StartLimitIntervalSec=10s`이라, 빠르게 반복되는 재시작은 유닛을 failed로 주저앉혀
  사람이 갈 때까지 안 뜨게 만든다.

**멀티로봇에서는 그룹 전체가 재시작된다.** `run_multi.py`는 자식 하나가 죽으면 나머지를
정리하고 non-zero로 빠지므로, systemd가 그룹 단위로 되살린다. AMR 한 대만 재시작하는
수단이 아니다.
```

- [ ] **Step 8: 커밋**

```bash
git add adaptor/adapter_jibot.py adaptor/main.py adaptor/readme.md adaptor/tests/test_remote_reload_actions.py
git commit -m "feat(adapter): restartAdapter instant action

정상 종료 경로로 빠져 OFFLINE 을 발행한 뒤 코드 75 로 나가고 systemd 가
되살린다. 기동 후 30초 미만이면 거부해 start limit 주저앉음을 막는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: WebUI 버튼

**Files:**
- Modify: `adaptor/core/registry.py` (`_JIBOT_INSTANT_ACTIONS`, 236~ 부근)
- Test: `adaptor/tests/test_remote_reload_actions.py`

**Interfaces:**
- Consumes: Task 4·5의 action type 이름

WebUI는 control socket(`adapter_jibot.py:1018`)으로 같은 instant action을 밀어 넣으므로,
버튼 정의만 추가하면 어댑터 쪽 추가 작업이 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
class WebUiButtonTest(unittest.TestCase):
    def test_both_actions_are_available_as_buttons(self):
        from core.registry import _JIBOT_INSTANT_ACTIONS

        types = {a.action_type for a in _JIBOT_INSTANT_ACTIONS}
        self.assertIn("reloadConfig", types)
        self.assertIn("restartAdapter", types)

    def test_neither_button_is_marked_as_motion(self):
        from core.registry import _JIBOT_INSTANT_ACTIONS

        for action in _JIBOT_INSTANT_ACTIONS:
            if action.action_type in ("reloadConfig", "restartAdapter"):
                self.assertFalse(action.motion)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py::WebUiButtonTest -v`
Expected: FAIL — `AssertionError: 'reloadConfig' not found in {...}`

- [ ] **Step 3: 최소 구현**

`adaptor/core/registry.py`의 `_JIBOT_INSTANT_ACTIONS`에 추가:

```python
    # 설정 파일을 다시 읽어 메모리에 반영한다. 로봇이 움직이지 않으므로 motion=False.
    InstantAction("reloadConfig", "Reload config files", motion=False),
    # 어댑터 프로세스를 재시작한다. 멀티로봇에서는 그룹 전체가 재시작된다.
    InstantAction("restartAdapter", "Restart adapter process", motion=False),
```

- [ ] **Step 4: 통과를 확인한다**

Run: `scripts/run-tests.sh tests/test_remote_reload_actions.py -v`
Expected: PASS (24개)

- [ ] **Step 5: 전체 스위트**

Run: `scripts/run-tests.sh -q`
Expected: PASS — 여기까지 오면 변경이 전부 들어갔으므로 전체를 한 번 돌린다.

- [ ] **Step 6: 커밋**

```bash
git add adaptor/core/registry.py adaptor/tests/test_remote_reload_actions.py
git commit -m "feat(webui): reloadConfig / restartAdapter 버튼

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 실장비 확인 (구현 후)

테스트는 스왑이 일어났다는 것까지만 보증한다. 실제로 새 값으로 동작하는지는 장비에서 본다.

1. `recipes.hcl`의 PIO step `timeout_sec`을 눈에 띄게 바꾼다 (예: 2 → 8).
2. WebUI에서 `reloadConfig`를 누른다 → 결과에 `reloaded: recipes=N`, `restart required` 없음.
3. 그 recipe를 실행해 타임아웃이 새 값으로 도는지 로그로 확인한다.
4. `config.toml`의 `[settings] map_id`를 바꾸고 `reloadConfig` → 결과에 `restart required for: settings`가 뜨는지 확인한다.
5. `restartAdapter`를 누른다 → 어댑터가 내려갔다 3초 뒤 올라오고, FMS가 OFFLINE→ONLINE을 본다.
6. 곧바로 `restartAdapter`를 다시 누른다 → `uptime … < 30s`로 거부되는지 확인한다.

## 배포 순서 주의

`INSTANT_ACTION_TYPES`가 늘어나므로 factsheet가 바뀐다. WCS plugin이 모르는 액션을 어떻게
다루는지 확인한 뒤 배포 순서를 운영과 합의한다 (spec §9).
