# extensions.hcl 분리 Implementation Plan (2단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** extension 전용 설정을 `config/config.toml`에서 `config/extensions.hcl`로 분리하고, 로봇별로 다른 extension 설정 파일을 고를 수 있게 한다.

**Architecture:** `config/extensions.py`가 `config/hcl.py`(1단계 산출물)로 `extensions.hcl`을 읽어 기존 dataclass(`PioConfig`, `EziConfig`, `AirShowerPioConfig`, `ElevatorPioConfig`, `ActionPluginConfig`, `ActionModuleConfig`)를 그대로 채운다. `get_config()`는 config.toml을 읽은 뒤 extensions.hcl 결과를 병합하고, 그 위에 로봇 override를 얹는다. dataclass와 소비자 코드는 바뀌지 않는다 — 값이 어느 파일에서 왔는지만 달라진다.

**Tech Stack:** Python 3.12, `python-hcl2>=8,<9`, uv, pytest, unittest

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-07-27-extension-recipe-hcl-design.md` 4절.
- **dataclass를 바꾸지 않는다.** `PioConfig` 등의 필드와 기본값은 그대로다. 이 단계는 값의 출처만 옮긴다. `Config`의 속성 이름(`pio_config`, `ezi_config`, `air_shower_config`, `elevator_config`, `pio_advanced`, `actions`, `action_modules`)도 그대로 유지한다 — 어댑터 전역이 이 이름을 쓴다.
- **`extensions.hcl`은 필수 파일이다.** 없으면 `FleetError`와 같은 등급으로 부팅을 멈춘다. 근거: 섹션을 config.toml에서 빼면 dataclass 기본값이 조용히 적용돼 PIO가 `pio_port = "COM6"` 같은 값으로 뜬다. 무증상 오설정이 문법 오류보다 위험하다.
- **로드 순서:** config.toml → extensions.hcl 병합 → 로봇 override. `ezi_io`/`ezi_motor`가 extension 설정을 겨냥하므로 override가 마지막이어야 한다.
- 파싱은 `config/hcl.py`의 `load_hcl`/`blocks`를 쓴다. `hcl2`를 직접 부르지 않는다.
- **테스트 실행에는 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`이 필요하다.**
  - `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest ...`
  - `cd <repo-root> && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests ...`
- **베이스라인: `adaptor/` 스위트는 40개가 실패한다. 전부 `tests/test_adapter_jibot_v3_order.py` 한 파일이다.** 원인은 이 계획과 무관한 PathPoint 기본값 전환(`nearest_node_mode`)이며 사용자가 그대로 두기로 했다. **성공 기준은 "실패가 그 파일 안에만 있고 40개를 넘지 않을 것"이다.** 루트 `tests`는 전부 통과해야 한다.
- 커밋 메시지는 리포 관례를 따른다. **스테이징은 항상 파일을 명시한다.** `git add -A` / `git add .` 금지.
- 작업 브랜치는 `develop`이다.

---

## File Structure

**신규**
- `adaptor/config/extensions.py` — extensions.hcl 파싱 → dataclass
- `adaptor/config/extensions.hcl` — 실제 설정 (config.toml에서 이전)
- `adaptor/config/extensions.hcl.example` — 배포용 예시
- `adaptor/tests/test_extensions_config.py` — 로더 테스트

**수정**
- `adaptor/config/config.py` — `get_config`가 extensions.hcl을 병합
- `adaptor/config/config.toml` — extension 섹션 제거
- `adaptor/config/fleet.py` — `_OVERRIDE_KEYS`의 ezi 대상 경로
- `adaptor/config/robots.hcl`, `robots.hcl.example` — `extensions` 키 문서화
- `adaptor/main.py`, `adaptor/web/server.py` — extensions 경로 전달
- `scripts/update-jibot-adapter-over-ssh.sh` — extensions.hcl 보존
- `adaptor/tests/test_config.py` — 픽스처

---

### Task 1: extensions.hcl 로더

**Files:**
- Create: `adaptor/config/extensions.py`
- Create: `adaptor/tests/test_extensions_config.py`

**Interfaces:**
- Consumes: `config.hcl.load_hcl`, `config.hcl.blocks`, `config.hcl.HclError`
- Produces:
  - `class ExtensionsError(ValueError)`
  - `DEFAULT_EXTENSIONS_PATH: Path` — `config/extensions.hcl`
  - `load_extensions(path=None) -> Dict[str, Any]` — config.toml과 같은 섹션 구조의 dict를 돌려준다. 즉 `{"pio": {...}, "pio_advanced": {...}, "ezi": {...}, "air_shower_pio": {...}, "elevator_pio": {...}, "actions": [...], "action_modules": [...]}`. 이렇게 하면 `get_config`가 기존 `config_dict`에 그대로 `_deep_merge` 할 수 있다.

- [ ] **Step 1: 실패하는 테스트 작성**

`adaptor/tests/test_extensions_config.py`:

```python
"""config/extensions.py — extensions.hcl을 config 섹션 dict로 읽는다."""

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config import extensions


SAMPLE = textwrap.dedent(
    """
    extension "pio" {
      pio_port     = "/dev/ttyUSB0"
      pio_baudrate = 38400
      media        = 2
      station_id   = "123456"
      channel      = 250
      port         = 0
      vehicle_num  = "OHT123"

      advanced = {
        init_default_timeout_sec = 2.0
        call_poll_interval_sec   = 0.05
      }
    }

    extension "ezi" {
      tray_slot_pin = [8, 9]
      select        = 15
      go            = 15
      motor_speed   = 20000
    }

    extension "airshower" {
      failure     = 7
      occupied    = 2
      fun_working = 3
      door_pin    = [0, 1]
    }

    extension "elevator" {
      open_door_pin  = 4
      close_door_pin = 3

      motion_rules = [
        { from = "1_05", to = "2_01", mode = "enter", floor_pin = 0, pio_station_id = "000010" },
      ]
    }

    module "extensions.pio" { enabled = false }
    module "custom_actions.thing" {}

    action "customDoorOpen" {
      runner      = "subprocess"
      command     = ["python", "x.py"]
      timeout_sec = 10
    }

    action "pioReadIn" { enabled = false }
    """
).strip()


def _write(tmp: str, body: str) -> Path:
    path = Path(tmp) / "extensions.hcl"
    path.write_text(body, encoding="utf-8")
    return path


class LoadExtensionsTest(unittest.TestCase):
    def _load(self, body=SAMPLE):
        with TemporaryDirectory() as tmp:
            return extensions.load_extensions(_write(tmp, body))

    def test_pio_section_matches_config_toml_shape(self):
        data = self._load()
        self.assertEqual(data["pio"]["pio_port"], "/dev/ttyUSB0")
        self.assertEqual(data["pio"]["pio_baudrate"], 38400)
        self.assertEqual(data["pio"]["port"], 0)
        # advanced 블록은 별도 섹션이 된다 (config.toml의 [pio_advanced]).
        self.assertNotIn("advanced", data["pio"])
        self.assertEqual(data["pio_advanced"]["call_poll_interval_sec"], 0.05)

    def test_ezi_and_airshower_sections(self):
        data = self._load()
        self.assertEqual(data["ezi"]["tray_slot_pin"], [8, 9])
        self.assertEqual(data["air_shower_pio"]["door_pin"], [0, 1])

    def test_elevator_motion_rules_become_a_list(self):
        data = self._load()
        rules = data["elevator_pio"]["elevator_motion_rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["from"], "1_05")
        self.assertEqual(rules[0]["pio_station_id"], "000010")
        self.assertNotIn("motion_rules", data["elevator_pio"])

    def test_modules_become_action_module_entries(self):
        data = self._load()
        self.assertEqual(
            data["action_modules"],
            [
                {"module": "extensions.pio", "enabled": False},
                {"module": "custom_actions.thing", "enabled": True},
            ],
        )

    def test_actions_become_action_plugin_entries(self):
        data = self._load()
        self.assertEqual(
            data["actions"],
            [
                {
                    "action_type": "customDoorOpen",
                    "runner": "subprocess",
                    "command": ["python", "x.py"],
                    "timeout_sec": 10,
                    "enabled": True,
                },
                {"action_type": "pioReadIn", "enabled": False},
            ],
        )

    def test_unknown_extension_name_rejected(self):
        with self.assertRaises(extensions.ExtensionsError) as ctx:
            self._load('extension "nope" {}\n')
        self.assertIn("nope", str(ctx.exception))

    def test_missing_file_raises(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(extensions.ExtensionsError):
                extensions.load_extensions(Path(tmp) / "missing.hcl")

    def test_syntax_error_raises(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load('extension "pio" {\n  pio_port =\n}\n')

    def test_duplicate_extension_rejected(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load('extension "pio" {}\n\nextension "pio" {}\n')

    def test_shipped_files_load(self):
        for name in ("config/extensions.hcl", "config/extensions.hcl.example"):
            with self.subTest(name=name):
                self.assertTrue(extensions.load_extensions(name))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_extensions_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'config.extensions'`

(`test_shipped_files_load`는 Task 2에서 파일을 만들 때까지 계속 실패한다. Task 1에서는 나머지가 통과하면 된다.)

- [ ] **Step 3: 구현 작성**

`adaptor/config/extensions.py`:

```python
"""extensions.hcl을 config.toml과 같은 섹션 dict로 읽는다.

extension 전용 설정(PIO/EZI/에어샤워/엘리베이터 튜닝값, 액션 모듈·플러그인
등록)을 config.toml에서 분리한 파일이다. 반환 모양을 config.toml의 섹션
구조와 똑같이 맞춰서, get_config()가 기존 dict에 그대로 병합할 수 있게 한다.
그래야 dataclass와 소비자 코드를 건드리지 않는다.

블록 이름 -> config 섹션:
    extension "pio"        -> [pio], 그 안의 advanced 블록 -> [pio_advanced]
    extension "ezi"        -> [ezi]
    extension "airshower"  -> [air_shower_pio]
    extension "elevator"   -> [elevator_pio], motion_rule 블록 -> elevator_motion_rules
    module "<import path>"  -> [[action_modules]]
    action "<actionType>"   -> [[actions]]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from config.hcl import HclError, blocks, load_hcl

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTENSIONS_PATH = ADAPTER_ROOT / "config" / "extensions.hcl"

#: extension 블록 라벨 -> config.toml 섹션 이름.
_EXTENSION_SECTIONS = {
    "pio": "pio",
    "ezi": "ezi",
    "airshower": "air_shower_pio",
    "elevator": "elevator_pio",
}


class ExtensionsError(ValueError):
    """extensions.hcl이 없거나 형식이 잘못됐을 때."""


def load_extensions(
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """extensions.hcl을 읽어 config 섹션 구조의 dict를 돌려준다."""
    file_path = Path(path) if path is not None else DEFAULT_EXTENSIONS_PATH
    try:
        data = load_hcl(file_path)
    except HclError as exc:
        raise ExtensionsError(
            f"{exc} (config/extensions.hcl.example 을 복사해 만드세요)"
        ) from exc

    result: Dict[str, Any] = {}

    seen_extensions = set()
    for label, body in blocks(data, "extension"):
        section = _EXTENSION_SECTIONS.get(label)
        if section is None:
            known = ", ".join(sorted(_EXTENSION_SECTIONS))
            raise ExtensionsError(
                f"unknown extension '{label}' in {file_path} (known: {known})"
            )
        if label in seen_extensions:
            raise ExtensionsError(f"duplicate extension '{label}' in {file_path}")
        seen_extensions.add(label)
        result[section] = _extension_section(label, body, result)

    result["action_modules"] = [
        {"module": label, "enabled": bool(body.get("enabled", True))}
        for label, body in blocks(data, "module")
    ]

    result["actions"] = [
        dict(body, action_type=label) if "enabled" in body
        else dict(body, action_type=label, enabled=True)
        for label, body in blocks(data, "action")
    ]

    return result


def _extension_section(
    label: str, body: Dict[str, Any], result: Dict[str, Any]
) -> Dict[str, Any]:
    """extension 블록 본문에서 중첩 설정을 꺼내 별도 섹션으로 승격한다."""
    section = {
        key: value
        for key, value in body.items()
        if key not in ("advanced", "motion_rules")
    }
    if label == "pio":
        advanced = body.get("advanced")
        if advanced is not None:
            if not isinstance(advanced, dict):
                raise ExtensionsError("extension 'pio': advanced must be an object")
            result["pio_advanced"] = dict(advanced)
    if label == "elevator":
        rules = body.get("motion_rules", [])
        if not isinstance(rules, list) or not all(
            isinstance(rule, dict) for rule in rules
        ):
            raise ExtensionsError(
                "extension 'elevator': motion_rules must be a list of objects"
            )
        section["elevator_motion_rules"] = [dict(rule) for rule in rules]
    return section
```

**문법이 확정돼 있다.** `advanced`와 `motion_rules`는 **라벨 없는 중첩 블록이 아니라
평범한 속성**이다. 1단계 로더는 라벨 없는 블록을 `HclError: block has no label`로
거절하기 때문이다(실측 확인). 속성 형태는 정상 처리된다 — 회귀 테스트
`test_attribute_list_of_single_key_objects_is_not_a_block`가 이 모양을 덮는다.

```hcl
extension "pio" {
  pio_port = "/dev/ttyUSB0"
  advanced = { init_default_timeout_sec = 2.0 }
}

extension "elevator" {
  open_door_pin = 4
  motion_rules = [
    { from = "1_05", to = "2_01", mode = "enter", floor_pin = 0, pio_station_id = "000010" },
  ]
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_extensions_config.py -q`
Expected: `test_shipped_files_load`만 실패(파일이 아직 없음), 나머지 통과

- [ ] **Step 5: 커밋**

```bash
git add adaptor/config/extensions.py adaptor/tests/test_extensions_config.py
git commit -m "feat(config): add extensions.hcl loader"
```

---

### Task 2: extensions.hcl 파일 작성과 config.toml 정리

**Files:**
- Create: `adaptor/config/extensions.hcl`, `adaptor/config/extensions.hcl.example`
- Modify: `adaptor/config/config.toml` (섹션 제거)

**Interfaces:**
- Consumes: `config.extensions.load_extensions` (Task 1)
- Produces: 기본 경로의 실제 설정 파일

- [ ] **Step 1: 현재 값 보존해 옮기기**

`adaptor/config/config.toml`의 `[ezi]`, `[pio]`, `[pio_advanced]`,
`[air_shower_pio]`, `[elevator_pio]`, 그리고 주석 처리된 `[[action_modules]]` /
`[[actions]]` 예시 블록을 `adaptor/config/extensions.hcl`로 옮긴다.
**값과 주석을 빠짐없이 보존한다.** Task 1이 정한 문법을 쓴다.

`ezi_io`/`ezi_motor`는 `[ezi]`에 리터럴로 없다(로봇 override가 채운다).
extensions.hcl에도 넣지 않는다.

옮긴 뒤 config.toml에서 그 섹션들을 지운다. 파일 머리말에 한 줄 남긴다.

```toml
# extension 전용 설정(PIO/EZI/에어샤워/엘리베이터, 액션 모듈·플러그인 등록)은
# config/extensions.hcl 로 분리했다.
```

- [ ] **Step 2: 예시 파일 작성**

`adaptor/config/extensions.hcl.example`을 만든다. 실제 파일과 같은 구조에
각 키의 의미를 주석으로 단다. config.toml의 기존 주석 밀도를 유지한다.

- [ ] **Step 3: 값 보존 확인**

Run:

```bash
cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -c "
from config.extensions import load_extensions
d = load_extensions('config/extensions.hcl')
print('pio        :', d['pio'])
print('pio_advanced:', d['pio_advanced'])
print('ezi keys   :', sorted(d['ezi']))
print('airshower  :', d['air_shower_pio'])
print('elevator   :', {k: v for k, v in d['elevator_pio'].items() if k != 'elevator_motion_rules'})
print('elev rules :', len(d['elevator_pio']['elevator_motion_rules']))
"
```

Expected: 이전 config.toml 값과 일치. 특히 `pio_port = "COM6"`,
`pio_baudrate = 38400`, `station_id = "123456"`, elevator 규칙 6개,
`air_shower_pio.door_pin = [0, 1]`

- [ ] **Step 4: 커밋**

```bash
git add adaptor/config/extensions.hcl adaptor/config/extensions.hcl.example adaptor/config/config.toml
git commit -m "feat(config): move extension settings into extensions.hcl"
```

---

### Task 3: get_config 배선과 로봇별 경로

**Files:**
- Modify: `adaptor/config/config.py`, `adaptor/config/fleet.py`, `adaptor/main.py`, `adaptor/web/server.py`
- Modify: `adaptor/config/robots.hcl`, `adaptor/config/robots.hcl.example`
- Modify: `adaptor/tests/test_config.py`

**Interfaces:**
- Consumes: `config.extensions.load_extensions`, `config.fleet.resolve_robot_path`
- Produces: `get_config(config_path=None, overrides=None, extensions_path=None)` — 세 번째 인자가 추가된다. 기존 호출부는 그대로 동작해야 한다(기본 경로 사용)

- [ ] **Step 1: 로드 순서 배선**

`get_config`에서 config.toml을 읽은 직후, override를 적용하기 **전에**
extensions.hcl을 병합한다.

```python
    extensions_data = load_extensions(extensions_path)
    _deep_merge(config_dict, extensions_data)

    if overrides:
        _deep_merge(config_dict, overrides)
```

`load_extensions`가 `ExtensionsError`를 던지면 그대로 올린다 — 부팅 실패다.

- [ ] **Step 2: 로봇별 extensions 경로**

`fleet.py`의 `_FIELD_TYPES`에 이미 `extensions`가 string으로 있다.
`main.py`의 두 분기와 `web/server.py`의 검증 루프에서 `config` 경로를 푸는 것과
같은 방식으로 `extensions` 경로도 풀어 `get_config`에 넘긴다.

```python
        resolved_ext = resolve_robot_path(robot, "extensions", cli_args.robots)
        extensions_path = str(resolved_ext) if resolved_ext is not None else None
```

`robots.hcl`과 `.example`의 키 레퍼런스 주석에 `extensions` 항목을 추가한다.

- [ ] **Step 3: 회귀 테스트**

`adaptor/tests/test_config.py`에 추가한다.

```python
def test_extension_settings_come_from_extensions_hcl(tmp_path):
    from config.config import get_config

    ext = tmp_path / "extensions.hcl"
    ext.write_text(
        open("config/extensions.hcl", encoding="utf-8").read().replace(
            'pio_baudrate = 38400', 'pio_baudrate = 19200'
        ),
        encoding="utf-8",
    )

    cfg = get_config(extensions_path=ext)
    assert cfg.pio_config.pio_baudrate == 19200


def test_missing_extensions_file_fails_boot(tmp_path):
    from config.config import get_config
    from config.extensions import ExtensionsError

    import pytest

    with pytest.raises(ExtensionsError):
        get_config(extensions_path=tmp_path / "nope.hcl")


def test_robot_override_wins_over_extensions_file():
    """ezi_io는 extensions.hcl이 아니라 로봇 항목이 채운다."""
    from config.config import get_config

    cfg = get_config(overrides={"ezi": {"ezi_io": "10.9.9.9"}})
    assert cfg.ezi_config.ezi_io == "10.9.9.9"
```

- [ ] **Step 4: 검증**

```bash
cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_config.py tests/test_extensions_config.py tests/test_fleet_registry.py -q
cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
cd /ssd2/workspaces/unified-amr-adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests -q
```

Expected: 대상 테스트 전부 통과. 전체 스위트의 실패는
`tests/test_adapter_jibot_v3_order.py` 안에만 있고 40개를 넘지 않는다.
루트 `tests`는 전부 통과.

- [ ] **Step 5: 시뮬레이터 기동 확인**

Run: `cd adaptor && timeout 20 .venv/bin/python main.py --robot HN-SH6-TR-001 2>&1 | head -30`
Expected: `ExtensionsError` 없이 기동

- [ ] **Step 6: 커밋**

```bash
git add adaptor/config/config.py adaptor/config/fleet.py adaptor/main.py \
  adaptor/web/server.py adaptor/config/robots.hcl adaptor/config/robots.hcl.example \
  adaptor/tests/test_config.py
git commit -m "feat(config): load extension settings from extensions.hcl"
```

---

### Task 4: 배포 스크립트와 문서

**Files:**
- Modify: `scripts/update-jibot-adapter-over-ssh.sh`
- Modify: `tests/test_update_jibot_adapter_over_ssh.py`
- Modify: `adaptor/readme.md`, `docs/manual/jibot-adapter-ssh-update.md`

**Interfaces:**
- Consumes: 없음
- Produces: 없음

- [ ] **Step 1: 실패하는 테스트 먼저**

`tests/test_update_jibot_adapter_over_ssh.py`에 추가한다.

```python
def test_update_script_preserves_remote_extensions_hcl():
    text = _script_text_or_read()
    assert "! -name extensions.hcl" in text
    assert "--exclude='config/extensions.hcl'" in text
```

기존 테스트가 스크립트 본문을 읽는 방식(`SCRIPT.read_text()`)을 따른다.

- [ ] **Step 2: 스크립트 수정**

`robots.hcl`을 다루는 세 곳과 같은 방식으로 `extensions.hcl`을 보존 대상에
추가한다.

- `find config -depth -mindepth 1 ! -name config.toml ! -name robots.hcl ! -name robots.toml` 에 `! -name extensions.hcl` 추가
- tar exclude 목록에 `'./config/extensions.hcl'`, `'config/extensions.hcl'` 추가
- 원격에 기존 파일이 없을 때 로컬 사본을 올리는 경로가 있다면 같은 형태로 추가

**주의:** `robots.hcl`은 로봇 고유 identity라 기본이 "보존"이다. `extensions.hcl`은
현장 공통 설정에 가까워 코드와 함께 갱신되는 편이 맞을 수 있다. 이 판단이
갈리면 **보존을 기본으로 하고 그 이유를 스크립트 주석에 적는다** — 현장에서
튜닝한 값을 배포가 조용히 되돌리는 것이 더 위험하다.

- [ ] **Step 3: 검증**

```bash
bash -n scripts/update-jibot-adapter-over-ssh.sh
cd /ssd2/workspaces/unified-amr-adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests -q
```

- [ ] **Step 4: 문서 갱신**

`adaptor/readme.md`와 `docs/manual/jibot-adapter-ssh-update.md`의 설정 파일
목록에 `config/extensions.hcl`을 추가한다. 어떤 설정이 어느 파일에 있는지
한 문단으로 정리한다.

- [ ] **Step 5: 커밋**

```bash
git add scripts/update-jibot-adapter-over-ssh.sh tests/test_update_jibot_adapter_over_ssh.py \
  adaptor/readme.md docs/manual/jibot-adapter-ssh-update.md
git commit -m "refactor(scripts): preserve extensions.hcl on deploy"
```

---

## 다음 단계

3단계(recipe 개명·실행 의미)부터는 별도 계획으로 작성한다. 이 계획이 통과하면
`extensions/groups/`를 `extensions/recipes/`로 대체하고 `recipes.hcl`을 붙인다.
