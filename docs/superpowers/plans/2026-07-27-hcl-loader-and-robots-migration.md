# HCL 로더와 robots.hcl 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 공통 HCL 로더를 만들고 로봇 인벤토리를 `config/robots.toml`에서 `config/robots.hcl`로 완전히 전환한다.

**Architecture:** `config/hcl.py`가 `python-hcl2` 파싱과 라벨 블록 정규화를 한곳에서 담당하고, `config/fleet.py`는 내부 파서만 HCL로 바꾼 채 기존 공개 API(`load_fleet` / `find_robot` / `robot_overrides` / `robot_ids`)를 그대로 유지한다. 호출부(main.py, run_multi.py, adapter_dispatch.py, web/)는 파일명과 문구만 바뀐다. 상대 경로는 fleet 파일의 부모 디렉터리를 기준으로 푼다.

**Tech Stack:** Python 3.12, `python-hcl2>=8,<9` (lark, regex), uv, pytest, unittest

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-07-27-extension-recipe-hcl-design.md`
- 의존성 버전은 정확히 `python-hcl2>=8,<9`. 파싱 옵션은 항상 `SerializationOptions(with_comments=False, explicit_blocks=False, strip_string_quotes=True)` 세 개를 함께 준다. 하나라도 빠지면 라벨에 따옴표가 남거나 `__is_block__` 마커가 섞인다.
- HCL 전용 즉시 전환이다. `robots.toml` 폴백 경로를 만들지 않는다.
- `load_fleet` / `find_robot` / `robot_overrides` / `robot_ids`의 시그니처와 반환 타입은 바뀌지 않는다. `load_fleet`은 계속 `List[Dict[str, Any]]`를 돌려주고 각 dict는 `id` 키를 가진다.
- 파일 없음 / 문법 오류 / `robot` 블록 0개 / `id` 중복 / `id` 누락은 모두 `FleetError`다.
- **테스트 실행에는 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`이 반드시 필요하다.** 이 호스트의
  `/opt/ros/jazzy` site-packages가 pytest entrypoint 플러그인(`launch_testing_ros`)을
  자동 로드하는데, 그게 `yaml` 미설치로 죽어 **테스트가 한 개도 수집되지 않는다**.
  이 환경 문제는 우리 작업과 무관하며 고치는 것도 이 계획의 범위가 아니다.
  - adaptor 테스트: `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest ...`
  - 리포 루트 테스트: `cd <repo-root> && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests ...`
- **베이스라인: `adaptor/` 전체 스위트는 이미 47개가 실패한다** (커밋 `e86c3c1` 기준
  `47 failed, 1161 passed`). 전부 이 계획 이전부터 있던 실패이며 원인은 배포된
  `config.toml`의 맵/모션 룰 데이터 드리프트다: `tests/test_adapter_jibot_v3_order.py`
  44개, `tests/test_initial_pose_and_map.py` 2개, `tests/test_dock_approach_config.py` 1개.
  **성공 기준은 "전체 통과"가 아니라 "이 47개 외에 새로운 실패가 없을 것"이다.**
  전체 스위트를 돌린 뒤에는 실패 개수와 실패 파일 목록을 베이스라인과 대조해 보고한다.
- 커밋 메시지는 리포 관례대로 `feat(config):`, `refactor(web):` 같은 접두사를 쓴다.
- **스테이징은 항상 파일을 명시한다.** `git add -A` / `git add .`를 쓰지 않는다. 이
  브랜치(`develop`)의 워킹 트리에는 이 계획과 무관한 미커밋 작업이 함께 있다 —
  `adaptor/extensions/groups/`, `adaptor/tests/test_extension_groups.py`,
  `adaptor/adapter_jibot.py`, `adaptor/config/config.py`, `adaptor/config/config.toml`,
  `adaptor/core/action_modules.py`, `adaptor/core/action_registry.py`,
  `adaptor/tests/test_config.py`, `WORKING/` 이하. 이 파일들은 **어느 태스크에서도
  수정하거나 커밋하지 않는다.**
- 작업 브랜치는 `develop`이다 (사용자가 명시적으로 승인). 새 브랜치를 만들지 않는다.

---

## File Structure

**신규**

- `adaptor/config/hcl.py` — HCL 파싱 + 라벨 블록 정규화. fleet/extensions/recipes 로더가 모두 이걸 쓴다.
- `adaptor/config/robots.hcl` — 실제 인벤토리 (기존 robots.toml 내용 이전)
- `adaptor/config/robots.hcl.example` — 배포용 예시 + 키 레퍼런스
- `adaptor/tests/test_hcl.py` — 로더 단위 테스트
- `scripts/convert-robots-toml-to-hcl.py` — 일회성 변환기

**수정**

- `adaptor/pyproject.toml` — 의존성 추가
- `adaptor/config/fleet.py` — 내부 파서 교체, `DEFAULT_FLEET_PATH` 변경, 경로 기준 헬퍼 추가
- `adaptor/main.py:109-128,254-290,603-636` — 도움말·에러 문구
- `adaptor/run_multi.py:3-12,37-43` — docstring
- `adaptor/config/adapter_dispatch.py:8-11` — docstring
- `adaptor/web/main.py:29` — 에러 문구
- `adaptor/web/server.py:977-998` — 감사 로그 라벨과 안내 문구
- `adaptor/run-adapter.sh:36`, `adaptor/run-multi.sh:2-3`, `scripts/setup-adaptor-service.sh:417`, `scripts/systemd/amr-adaptor@.service:3` — 주석
- `scripts/update-jibot-adapter-over-ssh.sh` — 보존/제외 대상 파일명, `--robots-toml-mode` → `--robots-hcl-mode`
- `adaptor/tests/test_fleet_registry.py`, `test_adapter_dispatch.py`, `test_web_server.py`, `test_web_main.py` — 픽스처 HCL화
- `tests/test_adaptor_cli.py`, `tests/test_adaptor_service_scripts.py`, `tests/test_update_jibot_adapter_over_ssh.py` — 픽스처·문자열 갱신

**삭제**

- `adaptor/config/robots.toml`, `adaptor/config/robots.toml.example` (Task 8에서)

---

### Task 1: HCL 로더 (`config/hcl.py`)

**Files:**
- Create: `adaptor/config/hcl.py`
- Create: `adaptor/tests/test_hcl.py`
- Modify: `adaptor/pyproject.toml`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `class HclError(ValueError)` — 파싱/파일 오류
  - `load_hcl(path: Union[str, Path]) -> Dict[str, Any]` — 정규화된 dict. 파일 없으면 `HclError`, 문법 오류도 `HclError`(경로와 원인 포함)
  - `blocks(data: Mapping[str, Any], kind: str) -> List[Tuple[str, Dict[str, Any]]]` — `{"robot": [{"A": {...}}, {"B": {...}}]}` → `[("A", {...}), ("B", {...})]`. 해당 `kind`가 없으면 빈 리스트. 라벨 없는 항목은 `HclError`
  - `PARAM_PATTERN: re.Pattern` — `${var.NAME}` 전체 일치용. 후속 단계(recipe)가 쓴다

- [ ] **Step 1: 의존성 추가**

`adaptor/pyproject.toml`의 `dependencies`를 다음으로 바꾼다.

```toml
dependencies = [
    "paho-mqtt==2.1.0",
    "python-hcl2>=8,<9",
]
```

설치:

```bash
cd adaptor && uv sync
```

- [ ] **Step 2: 실패하는 테스트 작성**

`adaptor/tests/test_hcl.py`:

```python
"""config/hcl.py — HCL 파싱과 라벨 블록 정규화."""

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config import hcl


def _write(tmp: str, name: str, body: str) -> Path:
    path = Path(tmp) / name
    path.write_text(textwrap.dedent(body).strip(), encoding="utf-8")
    return path


class LoadHclTest(unittest.TestCase):
    def test_strips_quotes_and_block_markers(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "sample.hcl",
                """
                # 주석은 결과에 남지 않는다
                robot "ROBOT-A" {
                  vehicle_ip = "10.0.0.11"
                  vehicle_port = 7273
                  simulator = true
                  extra_args = ["--x"]
                }
                """,
            )
            data = hcl.load_hcl(path)

        self.assertNotIn("__comments__", data)
        entry = data["robot"][0]["ROBOT-A"]
        self.assertNotIn("__is_block__", entry)
        self.assertEqual(entry["vehicle_ip"], "10.0.0.11")
        self.assertEqual(entry["vehicle_port"], 7273)
        self.assertIs(entry["simulator"], True)
        self.assertEqual(entry["extra_args"], ["--x"])

    def test_preserves_nested_block_order(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "sample.hcl",
                """
                recipe "r" {
                  step "first" {}
                  step "second" {}
                  step "third" {}
                }
                """,
            )
            data = hcl.load_hcl(path)

        steps = data["recipe"][0]["r"]["step"]
        self.assertEqual([next(iter(s)) for s in steps], ["first", "second", "third"])

    def test_keeps_var_interpolation_unevaluated(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "sample.hcl",
                """
                recipe "r" {
                  step "s" {
                    parameters = { index = var.doorPin, name = "dock-${var.n}" }
                  }
                }
                """,
            )
            data = hcl.load_hcl(path)

        params = data["recipe"][0]["r"]["step"][0]["s"]["parameters"]
        self.assertEqual(params["index"], "${var.doorPin}")
        self.assertEqual(params["name"], "dock-${var.n}")

    def test_missing_file_raises_hcl_error(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(hcl.HclError) as ctx:
                hcl.load_hcl(Path(tmp) / "nope.hcl")
        self.assertIn("nope.hcl", str(ctx.exception))

    def test_syntax_error_mentions_path(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, "bad.hcl", 'robot "A" {\n  vehicle_ip =\n}')
            with self.assertRaises(hcl.HclError) as ctx:
                hcl.load_hcl(path)
        self.assertIn("bad.hcl", str(ctx.exception))


class BlocksTest(unittest.TestCase):
    def test_returns_label_body_pairs_in_order(self):
        data = {"robot": [{"A": {"x": 1}}, {"B": {"y": 2}}]}
        self.assertEqual(hcl.blocks(data, "robot"), [("A", {"x": 1}), ("B", {"y": 2})])

    def test_missing_kind_returns_empty(self):
        self.assertEqual(hcl.blocks({}, "robot"), [])

    def test_unlabelled_block_raises(self):
        with self.assertRaises(hcl.HclError):
            hcl.blocks({"robot": [{}]}, "robot")

    def test_param_pattern_matches_whole_string_only(self):
        self.assertTrue(hcl.PARAM_PATTERN.fullmatch("${var.doorPin}"))
        self.assertIsNone(hcl.PARAM_PATTERN.fullmatch("dock-${var.n}"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_hcl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config.hcl'`

- [ ] **Step 4: 최소 구현 작성**

`adaptor/config/hcl.py`:

```python
"""HCL(HashiCorp Configuration Language) 로딩과 라벨 블록 정규화.

robots.hcl / extensions.hcl / recipes.hcl이 모두 이 모듈을 통해 읽힌다.
python-hcl2는 기본적으로 문자열에 따옴표를 남기고 __is_block__ 마커와
__comments__ 키를 끼워 넣으므로, 세 직렬화 옵션을 항상 함께 준다.

var.X 참조는 파서가 평가하지 않고 "${var.X}" 문자열로 남긴다. 이걸
액션 파라미터 자리표시자로 쓰므로 그대로 보존한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple, Union

import hcl2
from hcl2.utils import SerializationOptions


_OPTIONS = SerializationOptions(
    with_comments=False,
    explicit_blocks=False,
    strip_string_quotes=True,
)

#: 문자열 전체가 ${var.NAME}인 경우에만 일치한다. 부분 삽입은 따로 처리한다.
PARAM_PATTERN = re.compile(r"\$\{var\.([A-Za-z_][A-Za-z0-9_]*)\}")


class HclError(ValueError):
    """HCL 파일이 없거나 문법이 잘못됐을 때."""


def load_hcl(path: Union[str, Path]) -> Dict[str, Any]:
    """HCL 파일을 읽어 정규화된 dict를 돌려준다."""
    file_path = Path(path)
    if not file_path.exists():
        raise HclError(f"HCL file not found: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return hcl2.load(fh, serialization_options=_OPTIONS)
    except HclError:
        raise
    except Exception as exc:
        raise HclError(f"invalid HCL in {file_path}: {exc}") from exc


def blocks(data: Mapping[str, Any], kind: str) -> List[Tuple[str, Dict[str, Any]]]:
    """``kind`` 블록들을 (라벨, 본문) 목록으로 돌려준다 (선언 순서 보존)."""
    result: List[Tuple[str, Dict[str, Any]]] = []
    for index, item in enumerate(data.get(kind, []) or []):
        if not isinstance(item, dict) or len(item) != 1:
            raise HclError(
                f"{kind} block #{index} must have exactly one label"
            )
        label, body = next(iter(item.items()))
        result.append((str(label), body if isinstance(body, dict) else {}))
    return result
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_hcl.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: 커밋**

```bash
git add adaptor/config/hcl.py adaptor/tests/test_hcl.py adaptor/pyproject.toml adaptor/uv.lock
git commit -m "feat(config): add HCL loader with label-block normalization"
```

---

### Task 2: fleet.py를 HCL로 전환

**Files:**
- Modify: `adaptor/config/fleet.py`
- Modify: `adaptor/tests/test_fleet_registry.py:1-45` (픽스처)

**Interfaces:**
- Consumes: `config.hcl.load_hcl`, `config.hcl.blocks`, `config.hcl.HclError`
- Produces:
  - `DEFAULT_FLEET_PATH: Path` — 이제 `config/robots.hcl`
  - `load_fleet(path=None) -> List[Dict[str, Any]]` — 변경 없음. 각 dict에 `id` 키가 라벨에서 채워진다
  - `find_robot`, `robot_overrides`, `robot_ids` — 변경 없음
  - `resolve_robot_path(robot: Dict[str, Any], key: str, fleet_path: Union[str, Path, None] = None) -> Optional[Path]` — robot 엔트리의 경로 키(`config` / 후속 단계의 `extensions` / `recipes`)를 fleet 파일 부모 기준 절대 경로로 푼다. 키가 없으면 `None`

- [ ] **Step 1: 실패하는 테스트 작성**

`adaptor/tests/test_fleet_registry.py`의 상단 픽스처와 `_write`를 아래로 교체한다. 파일 나머지(`LoadFleetTest` 이후의 단언들)는 그대로 둔다.

```python
"""Tests for the robot fleet loader and the per-robot TUI registry.

config/robots.hcl(여러 대)을 읽어 인스턴스별 config override를 만들고, TUI
대시보드가 로봇마다 spec(고유 systemd 유닛 + MQTT 토픽)을 만드는지 검증한다.
"""

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config import fleet
from config.config import get_config
from core.registry import build_registry


FLEET_HCL = textwrap.dedent(
    """
    robot "ROBOT-A" {
      vehicle_ip = "10.0.0.11"
      ezi_io     = "10.8.8.87"
      simulator  = true
    }

    robot "ROBOT-B" {
      vehicle_ip = "10.0.0.12"
      mqtt_port  = 12000
    }
    """
).strip()

SINGLE_FLEET_HCL = textwrap.dedent(
    """
    robot "ROBOT-A" {
      vehicle_ip = "10.0.0.11"
      mqtt_port  = 12000
      simulator  = true
    }
    """
).strip()


def _write(tmp: str, body: str) -> str:
    path = Path(tmp) / "robots.hcl"
    path.write_text(body, encoding="utf-8")
    return str(path)
```

또한 `LoadFleetTest`의 `test_duplicate_id_raises`와 `test_missing_id_raises` 두 메서드는
**삭제한다.** 본문이 TOML 리터럴이라 HCL에서는 파서가 먼저 죽어 이름이 가리키는 검증
로직(중복 id / 빈 라벨)에 도달하지 못한다. 같은 시나리오는 아래 `FleetErrorTest`의
`test_duplicate_id`와 `test_empty_label_rejected`가 유효한 HCL로 제대로 덮는다.

이어서 새 테스트 클래스를 파일 끝(`if __name__ == "__main__":` 앞)에 추가한다.

```python
class FleetErrorTest(unittest.TestCase):
    def test_missing_file(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(Path(tmp) / "missing.hcl")

    def test_no_robot_blocks(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, '# 비어 있음\n')
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.load_fleet(path)
        self.assertIn("no robot", str(ctx.exception))

    def test_duplicate_id(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" {}\n\nrobot "A" {}\n')
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.load_fleet(path)
        self.assertIn("duplicate robot id: A", str(ctx.exception))

    def test_empty_label_rejected(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "" {\n  vehicle_ip = "10.0.0.1"\n}\n')
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(path)

    def test_syntax_error_becomes_fleet_error(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" {\n  vehicle_ip =\n}\n')
            with self.assertRaises(fleet.FleetError):
                fleet.load_fleet(path)


class ResolveRobotPathTest(unittest.TestCase):
    def test_relative_path_resolves_against_fleet_parent(self):
        with TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                'robot "A" {\n  config = "sub/robot-a.toml"\n}\n',
            )
            robots = fleet.load_fleet(path)
            resolved = fleet.resolve_robot_path(robots[0], "config", path)
        self.assertEqual(resolved, Path(tmp) / "sub" / "robot-a.toml")

    def test_absolute_path_kept(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" {\n  config = "/etc/robot-a.toml"\n}\n')
            robots = fleet.load_fleet(path)
            resolved = fleet.resolve_robot_path(robots[0], "config", path)
        self.assertEqual(resolved, Path("/etc/robot-a.toml"))

    def test_missing_key_returns_none(self):
        with TemporaryDirectory() as tmp:
            path = _write(tmp, 'robot "A" {}\n')
            robots = fleet.load_fleet(path)
        self.assertIsNone(fleet.resolve_robot_path(robots[0], "config", path))
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_fleet_registry.py -v`
Expected: FAIL — 기존 로더가 TOML을 기대하므로 `FleetError`(파일 없음/파싱 실패)와 `AttributeError: module 'config.fleet' has no attribute 'resolve_robot_path'`

- [ ] **Step 3: 구현 작성**

`adaptor/config/fleet.py`를 아래로 교체한다.

```python
"""Shared loader for the robot fleet file (``config/robots.hcl``).

robots.hcl 한 파일을 main.py(``--robot``), run_multi.py, TUI registry가 모두
공유하도록 모은 모듈. 각 ``robot "<id>"`` 블록을 config override(섹션 구조)로
바꿔주고, id 검증(비어있음/중복)을 한곳에서 처리한다.

robots.hcl의 키 -> 의미:
    robot "<id>" -> vehicle.serial_number (MQTT 토픽 prefix, 고유 필수)
    vehicle_ip   -> vehicle.vehicle_ip
    vehicle_port -> vehicle.vehicle_port
    ezi_io       -> ezi.ezi_io
    ezi_motor    -> ezi.ezi_motor
    mqtt_host    -> mqtt_broker.host
    mqtt_port    -> mqtt_broker.port
    config       -> 인스턴스 전용 TOML 경로(override 아님; get_config(config_path=...))
    simulator    -> 시뮬레이터 모드 여부(override 아님)
    extra_args   -> 그 밖에 main.py에 그대로 넘길 CLI 인자 배열

경로 키(config 등)의 상대 경로는 robots.hcl 파일의 부모 디렉터리를 기준으로
푼다. 프로세스 CWD(서비스와 수동 실행이 다르다)에 의존하지 않기 위해서다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from config.hcl import HclError, blocks, load_hcl

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FLEET_PATH = ADAPTER_ROOT / "config" / "robots.hcl"

# robots.hcl 키 -> (config 섹션, config 키). override로 바뀌는 값만 둔다.
_OVERRIDE_KEYS = {
    "id": ("vehicle", "serial_number"),
    "vehicle_ip": ("vehicle", "vehicle_ip"),
    "vehicle_port": ("vehicle", "vehicle_port"),
    "ezi_io": ("ezi", "ezi_io"),
    "ezi_motor": ("ezi", "ezi_motor"),
    "mqtt_host": ("mqtt_broker", "host"),
    "mqtt_port": ("mqtt_broker", "port"),
}


class FleetError(ValueError):
    """robots.hcl이 없거나 형식이 잘못됐을 때."""


def load_fleet(path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """robots.hcl을 읽어 robot dict 목록을 돌려준다 (id 검증 포함).

    파일이 없거나, robot 블록이 없거나, id가 비었거나 중복이면 FleetError.
    """
    fleet_path = Path(path) if path is not None else DEFAULT_FLEET_PATH
    try:
        data = load_hcl(fleet_path)
        entries = blocks(data, "robot")
    except HclError as exc:
        raise FleetError(
            f"{exc} (config/robots.hcl.example 을 복사해 만드세요)"
        ) from exc

    if not entries:
        raise FleetError(f"no robot blocks in {fleet_path}")

    robots: List[Dict[str, Any]] = []
    seen = set()
    for index, (label, body) in enumerate(entries):
        rid = str(label).strip()
        if not rid:
            raise FleetError(f"robot #{index} has an empty id label")
        if rid in seen:
            raise FleetError(f"duplicate robot id: {rid}")
        seen.add(rid)
        robot = dict(body)
        robot["id"] = rid
        robots.append(robot)

    return robots


def find_robot(fleet: List[Dict[str, Any]], robot_id: str) -> Dict[str, Any]:
    """fleet에서 id가 일치하는 robot을 찾는다. 없으면 FleetError."""
    for robot in fleet:
        if robot.get("id") == robot_id:
            return robot
    known = ", ".join(r.get("id", "?") for r in fleet) or "(none)"
    raise FleetError(f"robot id '{robot_id}' not in fleet. known ids: {known}")


def robot_overrides(robot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """robot 한 항목을 config override(중첩 dict)로 변환한다.

    None인 값과 빈 섹션은 빼서 config.toml 기본값이 유지되게 한다.
    config/simulator/extra_args는 override가 아니라 별도로 다룬다.
    """
    overrides: Dict[str, Dict[str, Any]] = {}
    for key, (section, conf_key) in _OVERRIDE_KEYS.items():
        value = robot.get(key)
        if value is None:
            continue
        overrides.setdefault(section, {})[conf_key] = value
    return overrides


def resolve_robot_path(
    robot: Dict[str, Any],
    key: str,
    fleet_path: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """robot 엔트리의 경로 키를 fleet 파일 부모 기준 절대 경로로 푼다."""
    raw = robot.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    candidate = Path(str(raw))
    if candidate.is_absolute():
        return candidate
    base = Path(fleet_path) if fleet_path is not None else DEFAULT_FLEET_PATH
    return (base.parent / candidate).resolve()


def robot_ids(path: Optional[Union[str, Path]] = None) -> List[str]:
    """fleet의 robot id 목록(순서 보존). 셸 스크립트에서 호출하기 좋게 분리."""
    return [robot["id"] for robot in load_fleet(path)]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_fleet_registry.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add adaptor/config/fleet.py adaptor/tests/test_fleet_registry.py
git commit -m "refactor(config): parse the fleet file as HCL"
```

---

### Task 3: robots.hcl 실제 파일과 예시 작성

**Files:**
- Create: `adaptor/config/robots.hcl`
- Create: `adaptor/config/robots.hcl.example`

**Interfaces:**
- Consumes: `config.fleet.load_fleet`
- Produces: 기본 경로에 놓이는 실제 인벤토리 파일. 이후 태스크의 수동 검증이 이 파일에 의존한다

- [ ] **Step 1: `adaptor/config/robots.hcl` 작성**

기존 `robots.toml`의 두 항목(HN-SH6-TR-001 시뮬레이터, HN-SH6-TR-002 시뮬레이터, 둘 다 `mqtt_host = "192.168.2.61"`)을 그대로 옮긴다.

```hcl
# Robot inventory / adapter identity source.
#
# amr-adaptor.service는 항상 이 파일을 본다. robot 블록이 1개면 그 로봇을
# main.py --robot <id>로 실행하고, 2개 이상이면 run_multi.py로 모든 로봇을
# 같은 서비스 아래에서 실행한다. 로봇별 identity/IP/EZI/MQTT override는 이
# 파일이 단일 출처이고, config/config.toml은 공통 기본값을 담는다.
#
# 경로 키(config 등)의 상대 경로는 이 파일이 있는 디렉터리를 기준으로 푼다.
#
# Key reference:
#   robot "HN-SH6-TR-001" { ... }
#     JIBOT 한 대의 inventory entry. 블록 라벨이 로봇 id이며 robot identity의
#     단일 출처다. VDA5050 serialNumber와 MQTT topic suffix가 되므로 동시에
#     뜨는 adapter 사이에서 반드시 고유해야 한다.
#
#   vehicle_ip = "10.0.0.11"     실차 권장. JIBOT TCP 제어 주소.
#   vehicle_port = 7273          선택. 생략하면 7273 기본값.
#   ezi_io = "10.8.8.87"         실차 권장. EZI IO module 주소.
#   ezi_motor = "10.8.8.2"       실차 권장. EZI motor driver 주소.
#   mqtt_host = "192.168.3.108"  선택. config.toml [mqtt_broker].host를 대체.
#   mqtt_port = 11883            선택. config.toml [mqtt_broker].port를 대체.
#   config = "robot-a.toml"      선택. 인스턴스 전용 config.toml 경로.
#   simulator = true             선택. true면 Python JIBOT simulator를 쓴다.
#   extra_args = ["--x"]         선택. main.py에 추가로 붙일 CLI 인자 배열.

robot "HN-SH6-TR-001" {
  simulator = true
  mqtt_host = "192.168.2.61"
  # vehicle_ip   = "127.0.0.1"
  # vehicle_port = 7273
  # ezi_io       = "10.8.8.87"
  # ezi_motor    = "10.8.8.2"
  # mqtt_port    = 11883
  # config       = "HN-SH6-TR-001.toml"
  # extra_args   = []
}

robot "HN-SH6-TR-002" {
  simulator = true
  mqtt_host = "192.168.2.61"
  # vehicle_ip   = "127.0.0.1"
  # ezi_io       = "10.8.8.88"
  # ezi_motor    = "10.8.8.3"
  # config       = "HN-SH6-TR-002.toml"
  # extra_args   = []
}
```

- [ ] **Step 2: `adaptor/config/robots.hcl.example` 작성**

```hcl
# Robot fleet definition.
# robots.hcl is the adapter identity source. amr-adaptor.service always reads
# this file: one robot block starts one main.py --robot process, two or more
# blocks start run_multi.py under the same service.
#
# 사용:
#   cp config/robots.hcl.example config/robots.hcl   # 값 수정 후
#   sudo systemctl restart amr-adaptor.service        # service chooses single/multi
#   python main.py --robot HN-SH6-TR-001              # optional one-robot debug path
#   ./run-multi.sh                                    # optional direct multi debug path
#
# 경로 키의 상대 경로는 이 파일이 있는 디렉터리를 기준으로 해석된다.
#
# Key reference:
#   robot "HN-SH6-TR-001" { ... }
#     One robot inventory entry. The block label IS the robot id; it becomes the
#     VDA5050 serialNumber and MQTT topic suffix, so it must be unique per
#     running adapter. Real deployments normally have one block on a robot
#     onboard PC and multiple blocks only on a host that intentionally
#     supervises several adapters.
#
#   vehicle_ip = "10.0.0.11"
#     Optional but expected for real robots. JIBOT TCP control address.
#   vehicle_port = 7273
#     Optional. Omit only when the robot uses the default port 7273.
#   ezi_io = "10.8.8.87"
#     Optional but expected when the robot has a dedicated EZI IO module.
#   ezi_motor = "10.8.8.2"
#     Optional but expected when the robot has a dedicated EZI motor driver.
#   mqtt_host = "192.168.3.108"
#     Optional. Overrides config.toml [mqtt_broker].host for this robot only.
#   mqtt_port = 11883
#     Optional. Overrides config.toml [mqtt_broker].port for this robot only.
#   config = "robot-a.toml"
#     Optional. Per-robot config.toml path, resolved against this file's
#     directory. Use only when a robot truly needs a separate config file.
#   simulator = true
#     Optional. true starts the Python JIBOT simulator instead of connecting to
#     a real AMR vehicle.
#   extra_args = ["--vehicle-smoke-test"]
#     Optional. Additional main.py CLI arguments appended for this robot.

robot "HN-SH6-TR-001" {
  vehicle_ip   = "10.0.0.11"
  vehicle_port = 7273
  ezi_io       = "10.8.8.87"
  ezi_motor    = "10.8.8.2"
  # mqtt_host  = "192.168.3.108"
  # mqtt_port  = 11883
  # config     = "HN-SH6-TR-001.toml"
  # simulator  = false
  # extra_args = []
}

robot "HN-SH6-TR-002" {
  vehicle_ip   = "10.0.0.12"
  vehicle_port = 7273
  ezi_io       = "10.8.8.88"
  ezi_motor    = "10.8.8.3"
}

# 시뮬레이터로 두 대 띄우는 예시:
# robot "SIM-001" {
#   vehicle_ip = "127.0.0.1"
#   mqtt_host  = "127.0.0.1"
#   mqtt_port  = 11883
#   simulator  = true
# }
```

- [ ] **Step 3: 두 파일이 실제로 로드되는지 확인**

Run:

```bash
cd adaptor && .venv/bin/python -c "
from config.fleet import load_fleet, robot_overrides
for p in ('config/robots.hcl', 'config/robots.hcl.example'):
    robots = load_fleet(p)
    print(p, [r['id'] for r in robots])
print(robot_overrides(load_fleet('config/robots.hcl')[0]))
"
```

Expected:
```
config/robots.hcl ['HN-SH6-TR-001', 'HN-SH6-TR-002']
config/robots.hcl.example ['HN-SH6-TR-001', 'HN-SH6-TR-002']
{'vehicle': {'serial_number': 'HN-SH6-TR-001'}, 'mqtt_broker': {'host': '192.168.2.61'}}
```

- [ ] **Step 4: 커밋**

```bash
git add adaptor/config/robots.hcl adaptor/config/robots.hcl.example
git commit -m "feat(config): add robots.hcl inventory and example"
```

---

### Task 4: 변환 스크립트

**Files:**
- Create: `scripts/convert-robots-toml-to-hcl.py`
- Create: `tests/test_convert_robots_to_hcl.py`

**Interfaces:**
- Consumes: 없음 (독립 실행 스크립트, 표준 라이브러리만 사용)
- Produces:
  - `convert(toml_text: str) -> str` — TOML 본문을 HCL 본문으로 변환
  - CLI: `python scripts/convert-robots-toml-to-hcl.py <robots.toml> [-o <robots.hcl>]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_convert_robots_to_hcl.py`:

```python
"""robots.toml -> robots.hcl 일회성 변환기."""

import importlib.util
import re
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "convert-robots-toml-to-hcl.py"


def _load():
    spec = importlib.util.spec_from_file_location("convert_robots", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_converts_ids_scalars_and_lists():
    module = _load()
    out = module.convert(
        textwrap.dedent(
            """
            [[robot]]
            id = "ROBOT-A"
            vehicle_ip = "10.0.0.11"
            vehicle_port = 7273
            simulator = true
            extra_args = ["--x", "--y"]

            [[robot]]
            id = "ROBOT-B"
            mqtt_port = 12000
            """
        )
    )

    # 스크립트가 키를 ljust로 정렬 패딩하므로 `키 = 값` 사이 공백 폭이 가변이다.
    # 그래도 키와 값은 반드시 같은 줄에 묶여 있어야 한다 — 값이 뒤바뀌는 회귀를
    # 잡으려면 줄 단위 정규식으로 결합을 검증해야 한다.
    def line(key, value):
        return re.search(
            rf"^\s*{re.escape(key)}\s*=\s*{re.escape(value)}\s*$", out, re.M
        )

    assert 'robot "ROBOT-A" {' in out
    assert line("vehicle_ip", '"10.0.0.11"')
    assert line("vehicle_port", "7273")
    assert line("simulator", "true")
    assert line("extra_args", '["--x", "--y"]')
    assert 'robot "ROBOT-B" {' in out
    assert line("mqtt_port", "12000")


def test_output_parses_as_valid_fleet():
    module = _load()
    out = module.convert('[[robot]]\nid = "R-1"\nvehicle_ip = "10.0.0.1"\n')

    import sys

    sys.path.insert(0, str(REPO_ROOT / "adaptor"))
    try:
        from config import hcl

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "robots.hcl"
            path.write_text(out, encoding="utf-8")
            data = hcl.load_hcl(path)
        label, body = hcl.blocks(data, "robot")[0]
        assert label == "R-1"
        assert body["vehicle_ip"] == "10.0.0.1"
    finally:
        sys.path.remove(str(REPO_ROOT / "adaptor"))
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd /ssd2/workspaces/unified-amr-adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests/test_convert_robots_to_hcl.py -v`
Expected: FAIL — `FileNotFoundError` / `spec_from_file_location` 실패

- [ ] **Step 3: 스크립트 작성**

`scripts/convert-robots-toml-to-hcl.py`:

```python
#!/usr/bin/env python3
"""config/robots.toml을 config/robots.hcl로 변환한다 (일회성 마이그레이션).

사용:
    python scripts/convert-robots-toml-to-hcl.py adaptor/config/robots.toml \
        -o adaptor/config/robots.hcl

[[robot]] 항목의 id는 블록 라벨이 되고 나머지 키는 그대로 옮긴다. 주석은
TOML 파서가 버리므로 보존되지 않는다. 변환 후 robots.hcl을 열어 주석을
직접 옮기고, config/robots.hcl.example을 참고해 정리할 것.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def convert(toml_text: str) -> str:
    data = tomllib.loads(toml_text)
    robots = data.get("robot", [])
    if not robots:
        raise SystemExit("no [[robot]] entries found")

    chunks = []
    for index, robot in enumerate(robots):
        rid = robot.get("id")
        if not rid:
            raise SystemExit(f"robot #{index} has no 'id'")
        lines = [f'robot "{rid}" {{']
        width = max(
            (len(key) for key in robot if key != "id"),
            default=0,
        )
        for key, value in robot.items():
            if key == "id":
                continue
            lines.append(f"  {key.ljust(width)} = {_literal(value)}")
        lines.append("}")
        chunks.append("\n".join(lines))

    header = (
        "# Generated by scripts/convert-robots-toml-to-hcl.py\n"
        "# 주석은 변환되지 않는다. config/robots.hcl.example을 참고해 정리할 것.\n"
    )
    return header + "\n" + "\n\n".join(chunks) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="robots.toml 경로")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="출력 경로 (기본: stdout)"
    )
    args = parser.parse_args(argv)

    out = convert(args.source.read_text(encoding="utf-8"))
    if args.output is None:
        sys.stdout.write(out)
    else:
        args.output.write_text(out, encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

실행 권한 부여:

```bash
chmod +x scripts/convert-robots-toml-to-hcl.py
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /ssd2/workspaces/unified-amr-adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests/test_convert_robots_to_hcl.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 실제 robots.toml로 왕복 확인**

Run:

```bash
cd /ssd2/workspaces/unified-amr-adaptor && adaptor/.venv/bin/python \
  scripts/convert-robots-toml-to-hcl.py adaptor/config/robots.toml
```

Expected: `robot "HN-SH6-TR-001" {` 와 `robot "HN-SH6-TR-002" {` 두 블록이 출력되고 `simulator = true`, `mqtt_host = "192.168.2.61"` 포함

- [ ] **Step 6: 커밋**

```bash
git add scripts/convert-robots-toml-to-hcl.py tests/test_convert_robots_to_hcl.py
git commit -m "feat(scripts): add robots.toml to robots.hcl converter"
```

---

### Task 5: 파이썬 호출부 문구 갱신

**Files:**
- Modify: `adaptor/main.py:110,114,123,257,258,281,603,628`
- Modify: `adaptor/run_multi.py:3,9,10,12,37,39`
- Modify: `adaptor/config/adapter_dispatch.py:8,9,11`
- Modify: `adaptor/web/main.py:29`
- Modify: `adaptor/web/server.py:977,985,991,994,997`
- Modify: `adaptor/tests/test_web_main.py:27,36`

**Interfaces:**
- Consumes: `config.fleet.DEFAULT_FLEET_PATH` (Task 2)
- Produces: 없음 (문구만 바뀐다). 동작 계약은 그대로다

- [ ] **Step 1: 문자열 치환**

각 파일에서 사용자에게 보이는 `robots.toml` 문자열을 `robots.hcl`로 바꾼다. 아래를 실행한 뒤 diff로 확인한다.

```bash
cd /ssd2/workspaces/unified-amr-adaptor
sed -i 's/robots\.toml/robots.hcl/g' \
  adaptor/main.py \
  adaptor/run_multi.py \
  adaptor/config/adapter_dispatch.py \
  adaptor/web/main.py \
  adaptor/web/server.py \
  adaptor/tests/test_web_main.py
git diff --stat
```

- [ ] **Step 2: `[[robot]]` 표현이 남았는지 확인하고 고치기**

`main.py:281`의 에러 메시지는 TOML 문법을 언급한다. 다음으로 바꾼다.

```python
                "robots.hcl has multiple robot blocks; use run_multi.py "
```

`adaptor/config/robots.toml.example`을 가리키는 `run_multi.py:12`의 주석은 다음으로 바꾼다.

```python
각 로봇 키 -> main.py 옵션 매핑은 config/robots.hcl.example 참고.
```

확인:

```bash
grep -rn "\[\[robot\]\]\|robots\.toml" adaptor/main.py adaptor/run_multi.py \
  adaptor/config/adapter_dispatch.py adaptor/web/main.py adaptor/web/server.py
```

Expected: 출력 없음

- [ ] **Step 3: 웹 서버 감사 라벨 확인**

`web/server.py`의 `self._audit(h, "robots", "robots.hcl", ...)` 호출 5곳이 모두 `robots.hcl`로 바뀌었는지 확인한다.

Run: `grep -n '_audit(h, "robots"' adaptor/web/server.py`
Expected: 5줄 모두 두 번째 인자가 `"robots.hcl"`

- [ ] **Step 4: `config` 경로를 fleet 파일 기준으로 해석하게 고치기**

지금 `main.py`와 `web/server.py`는 robot 엔트리의 `config` 값을 날것 그대로 쓴다
(`main.py:270-271`, `main.py:285-286`, `web/server.py:966`). 그러면 상대 경로가 프로세스
CWD 기준이 되어 "robots.hcl 부모 기준" 규칙이 깨진다. `resolve_robot_path`(Task 2)로 바꾼다.

먼저 실패하는 테스트를 `tests/test_adaptor_cli.py` 끝(`if __name__ == "__main__":` 앞)에 추가한다.

```python
    def test_relative_config_path_resolves_against_fleet_file(self):
        body = textwrap.dedent(
            """
            robot "ROBOT-A" {
              config = "robot-a.toml"
            }
            """
        )
        with TemporaryDirectory() as tmp:
            robots = Path(tmp) / "robots.hcl"
            robots.write_text(body, encoding="utf-8")

            args = main.parse_args(["--robots", str(robots), "--robot", "ROBOT-A"])
            _, config_path, _ = main.resolve_instance(args)

        self.assertEqual(Path(config_path), Path(tmp) / "robot-a.toml")
```

`main.py`의 import 줄 두 곳에 `resolve_robot_path`를 추가하고, 두 대입문을 바꾼다.

```python
        from config.fleet import (
            find_robot,
            load_fleet,
            resolve_robot_path,
            robot_overrides,
        )

        robot = find_robot(load_fleet(cli_args.robots), cli_args.robot)
        overrides = robot_overrides(robot)
        if config_path is None:
            resolved = resolve_robot_path(robot, "config", cli_args.robots)
            if resolved is not None:
                config_path = str(resolved)
```

`elif` 분기도 같은 형태로 바꾼다 (`load_fleet, resolve_robot_path, robot_overrides` import).

`web/server.py:961-968`의 검증 루프도 바꾼다.

```python
    def _validate_robots_on_disk(self):
        try:
            robots = load_fleet(self._robots_path)
            for robot in robots:
                resolved = resolve_robot_path(robot, "config", self._robots_path)
                get_config(
                    config_path=str(resolved) if resolved is not None else None,
                    overrides=robot_overrides(robot),
                )
        except Exception as exc:  # noqa: BLE001 - operator-facing validation
            return False, str(exc)
        return True, "ok"
```

`web/server.py:26`의 import를 바꾼다.

```python
from config.fleet import (
    DEFAULT_FLEET_PATH,
    load_fleet,
    resolve_robot_path,
    robot_overrides,
)
```

- [ ] **Step 5: 관련 테스트 실행**

Run:

```bash
cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_web_main.py tests/test_web_server.py -v
cd /ssd2/workspaces/unified-amr-adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests/test_adaptor_cli.py -v
```

Expected: 새 `test_relative_config_path_resolves_against_fleet_file`가 통과. 나머지 CLI
테스트와 `test_web_server.py` 픽스처는 Task 6에서 HCL로 바꾸므로, 여기서 실패하면 실패
목록을 기록하고 Task 6에서 해소한다

- [ ] **Step 6: 커밋**

```bash
git add adaptor/main.py adaptor/run_multi.py adaptor/config/adapter_dispatch.py \
  adaptor/web/main.py adaptor/web/server.py adaptor/tests/test_web_main.py \
  tests/test_adaptor_cli.py
git commit -m "refactor: point python call sites at robots.hcl"
```

---

### Task 6: 나머지 테스트 픽스처 HCL화

**Files:**
- Modify: `adaptor/tests/test_adapter_dispatch.py:20-91`
- Modify: `adaptor/tests/test_web_server.py:1184-1185`
- Modify: `tests/test_adaptor_cli.py:100-146`

**Interfaces:**
- Consumes: `config.fleet.load_fleet` (Task 2), `config/robots.hcl` (Task 3)
- Produces: 없음

- [ ] **Step 1: `test_adapter_dispatch.py` 픽스처 교체**

`_write(..., "robots.toml", ...)` 세 곳을 `"robots.hcl"`로 바꾸고, TOML 본문을 HCL로 바꾼다. 예를 들어 두 로봇 픽스처는 다음이 된다.

```python
                "robots.hcl",
                'robot "HN-SH6-TR-002" {\n  vehicle_ip = "10.0.0.12"\n}\n',
```

여러 로봇 케이스:

```python
                "robots.hcl",
                'robot "ROBOT-A" {\n  vehicle_ip = "10.0.0.11"\n}\n\n'
                'robot "ROBOT-B" {\n  vehicle_ip = "10.0.0.12"\n}\n',
```

`test_missing_fleet_file`의 `"missing.toml"`은 `"missing.hcl"`로 바꾼다.

- [ ] **Step 2: `test_web_server.py` 픽스처 교체**

`_ui_with_robots`의 두 줄을 바꾼다.

```python
    robots = tmp_path / "robots.hcl"
    robots.write_text('robot "R-1" {}\n', encoding="utf-8")
```

- [ ] **Step 3: `tests/test_adaptor_cli.py` 픽스처 교체**

세 곳의 `Path(tmp) / "robots.toml"`을 `"robots.hcl"`로 바꾸고, 각 `body`를 아래로 바꾼다.

`test_default_service_uses_single_robot_fleet`(92-100줄)과
`test_explicit_robot_uses_fleet_entry`(133-141줄)는 같은 본문을 쓴다.

```python
        body = textwrap.dedent(
            """
            robot "ROBOT-A" {
              vehicle_ip = "10.0.0.11"
              simulator  = true
              mqtt_port  = 12000
            }
            """
        )
```

`test_default_service_rejects_multi_robot_fleet_without_robot`(115-123줄):

```python
        body = textwrap.dedent(
            """
            robot "ROBOT-A" {}

            robot "ROBOT-B" {}
            """
        )
```

단언(`overrides["vehicle"]["serial_number"] == "ROBOT-A"` 등)은 그대로 통과해야 한다.
라벨이 `id`로 정규화되므로 override 매핑 결과가 동일하기 때문이다.

- [ ] **Step 4: 전체 테스트 실행**

Run:

```bash
cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
cd /ssd2/workspaces/unified-amr-adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests -q
```

Expected: adaptor 스위트는 베이스라인과 같은 `47 failed`(test_adapter_jibot_v3_order 44,
test_initial_pose_and_map 2, test_dock_approach_config 1)이고 그 외 실패가 없어야 한다.
루트 `tests`는 전부 통과. 새로운 실패가 남으면 그 테스트의 픽스처가 아직 TOML인지 확인한다

- [ ] **Step 5: 커밋**

```bash
git add adaptor/tests/test_adapter_dispatch.py adaptor/tests/test_web_server.py \
  tests/test_adaptor_cli.py
git commit -m "test: migrate fleet fixtures to HCL"
```

---

### Task 7: 배포 스크립트 전환

**Files:**
- Modify: `scripts/update-jibot-adapter-over-ssh.sh:19,25-26,51,99-105,257-265,349-355,383-384,416-417,459-462,488,519-528`
- Modify: `adaptor/run-adapter.sh:36`, `adaptor/run-multi.sh:2-3`
- Modify: `scripts/setup-adaptor-service.sh:417,439,892`
- Modify: `scripts/systemd/amr-adaptor@.service:3`
- Modify: `scripts/update-jibot-adapter-config.sh:29,102`
- Modify: `tests/test_update_jibot_adapter_over_ssh.py:203,223-233,250`
- Modify: `tests/test_adaptor_service_scripts.py:228`

**Interfaces:**
- Consumes: 없음 (셸 계층)
- Produces: `--robots-hcl-mode keep|overwrite|ask` CLI 플래그

- [ ] **Step 1: 실패하는 테스트 먼저 갱신**

`tests/test_update_jibot_adapter_over_ssh.py`에서 문자열 단언을 바꾼다.

```python
    assert "--robots-hcl-mode keep|overwrite|ask" in text
```

```python
def test_update_script_treats_robots_hcl_as_remote_config():
    text = _script_text()
    assert 'cp "$LOCAL_ADAPTER_DIR/config/robots.hcl" "$STAGING_DIR/default_config/robots.hcl"' in text
    assert "--exclude='./config/robots.hcl'" in text
    assert "--exclude='config/robots.hcl'" in text
    assert 'preserved_robots="$staging/preserved-robots.hcl"' in text
    assert 'if [[ -f "$remote_dir/config/robots.hcl" ]]' in text
    assert 'cp "$preserved_robots" "$remote_dir/config/robots.hcl"' in text
```

68-69번 줄의 매뉴얼 문구 단언과 250번 줄 주석도 `robots.hcl`로 바꾼다.

`tests/test_adaptor_service_scripts.py:228`:

```python
    assert "config/robots.hcl" in proc.stderr
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd /ssd2/workspaces/unified-amr-adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests/test_update_jibot_adapter_over_ssh.py tests/test_adaptor_service_scripts.py -v`
Expected: FAIL — 스크립트가 아직 `robots.toml`을 쓴다

- [ ] **Step 3: 셸 스크립트 치환**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
sed -i 's/robots-toml-mode/robots-hcl-mode/g; s/robots_toml_action/robots_hcl_action/g; \
        s/prompt_robots_toml_mode/prompt_robots_hcl_mode/g; \
        s/preserved-robots\.toml/preserved-robots.hcl/g; \
        s/robots\.toml/robots.hcl/g' \
  scripts/update-jibot-adapter-over-ssh.sh \
  scripts/update-jibot-adapter-config.sh \
  scripts/setup-adaptor-service.sh \
  scripts/systemd/amr-adaptor@.service \
  adaptor/run-adapter.sh \
  adaptor/run-multi.sh
```

- [ ] **Step 4: `find` 보존 목록 확인**

`scripts/update-jibot-adapter-over-ssh.sh`의 원격 정리 줄이 새 파일명을 지키는지 확인한다.

Run: `grep -n "! -name config.toml" scripts/update-jibot-adapter-over-ssh.sh`
Expected: `! -name config.toml ! -name robots.hcl` 형태

- [ ] **Step 5: 문법 검사와 테스트**

Run:

```bash
cd /ssd2/workspaces/unified-amr-adaptor
bash -n scripts/update-jibot-adapter-over-ssh.sh
bash -n scripts/setup-adaptor-service.sh
bash -n adaptor/run-adapter.sh
bash -n adaptor/run-multi.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests -q
```

Expected: `bash -n` 무출력, 루트 `tests` 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add scripts/update-jibot-adapter-over-ssh.sh scripts/update-jibot-adapter-config.sh \
  scripts/setup-adaptor-service.sh scripts/systemd/amr-adaptor@.service \
  adaptor/run-adapter.sh adaptor/run-multi.sh \
  tests/test_update_jibot_adapter_over_ssh.py tests/test_adaptor_service_scripts.py
git commit -m "refactor(scripts): deploy robots.hcl instead of robots.toml"
```

---

### Task 8: 구 파일 제거와 문서 갱신

**Files:**
- Delete: `adaptor/config/robots.toml`, `adaptor/config/robots.toml.example`
- Modify: `README.md`, `adaptor/readme.md`, `docs/guide/adaptor-tui.md`, `docs/guide/jibot-onboard-quick-guide.md`, `docs/guide/simulator.md`, `docs/manual/jibot-adapter-ssh-update.md`, `docs/reference/jibot-onboard-access.md`

**Interfaces:**
- Consumes: 앞선 모든 태스크
- Produces: 없음

- [ ] **Step 1: 구 파일 삭제**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
git rm adaptor/config/robots.toml adaptor/config/robots.toml.example
```

`adaptor/config/robots.toml`은 gitignore 대상일 수 있다. `git rm`이 실패하면 `rm -f`로 지우고 `.gitignore`에 `config/robots.hcl` 항목이 필요한지 확인한다.

Run: `grep -n "robots" .gitignore adaptor/.gitignore 2>/dev/null`

- [ ] **Step 2: 문서 치환**

```bash
sed -i 's/robots-toml-mode/robots-hcl-mode/g; s/robots\.toml/robots.hcl/g' \
  README.md adaptor/readme.md docs/guide/adaptor-tui.md \
  docs/guide/jibot-onboard-quick-guide.md docs/guide/simulator.md \
  docs/manual/jibot-adapter-ssh-update.md docs/reference/jibot-onboard-access.md
```

**플래그명 치환이 먼저 와야 한다.** `--robots-toml-mode`는 하이픈이라 `robots\.toml`
패턴에 걸리지 않는다. 이걸 빠뜨리면 매뉴얼이 Task 7에서 `--robots-hcl-mode`로 바뀐,
**이제 존재하지 않는 플래그**를 계속 안내하게 된다.

Run: `grep -rn "robots-toml-mode" README.md adaptor/readme.md docs/`
Expected: 출력 없음

- [ ] **Step 3: 문서에 남은 TOML 예제 블록 고치기**

치환만으로는 `[[robot]]` 예제가 남는다. 다음으로 찾아서 HCL 블록으로 바꾼다.

Run: `grep -rn "\[\[robot\]\]" README.md adaptor/readme.md docs/`

각 예제를 아래 형태로 바꾼다.

```hcl
robot "HN-SH6-TR-001" {
  vehicle_ip = "10.0.0.11"
  ezi_io     = "10.8.8.87"
}
```

- [ ] **Step 2b: 매뉴얼 문구를 단언하는 테스트 갱신**

`tests/test_update_jibot_adapter_over_ssh.py:68-69`는 `docs/manual/jibot-adapter-ssh-update.md`의
문구를 단언한다. Step 2가 그 문서를 바꾸므로 두 단언도 함께 바꿔야 한다. Task 7이
시퀀싱 때문에 의도적으로 남겨둔 몫이다.

```python
    assert "코드와 `robots.hcl`을 같이 반영하고 adapter 재시작:" not in ssh_manual
    assert "코드와 `robots.hcl`을 같이 반영하고 adapter와 설치된 WebUi 재시작:" in ssh_manual
```

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests/test_update_jibot_adapter_over_ssh.py -q`
Expected: 전부 통과

- [ ] **Step 3b: 남은 포맷 명칭 불일치 정리**

Task 5 리뷰가 남긴 minor다. `adaptor/run_multi.py`의 `--robots` help 문자열이
`"Fleet definition TOML. Default: config/robots.hcl"`로 포맷 이름과 파일명이 어긋나 있다.
`"Fleet definition HCL. Default: config/robots.hcl"`로 고친다.

같은 종류가 더 있는지 확인한다.

Run: `grep -rn "TOML" adaptor/run_multi.py adaptor/main.py adaptor/config/fleet.py adaptor/config/adapter_dispatch.py`
Expected: fleet 파일을 TOML이라 부르는 문구가 남아 있지 않다 (로봇별 `config` 키가 가리키는
per-instance config.toml을 TOML이라 부르는 것은 정상이므로 그대로 둔다)

이 스텝에서 `adaptor/run_multi.py`를 수정했다면 Step 7의 커밋 대상에 추가한다.

- [ ] **Step 4: 마이그레이션 안내 추가**

`docs/manual/jibot-adapter-ssh-update.md` 상단에 다음 절을 추가한다.

```markdown
## robots.toml -> robots.hcl 마이그레이션 (일회성)

이 버전부터 어댑터는 `config/robots.hcl`만 읽는다. `robots.toml`을 들고 있는
로봇은 업데이트 전에 변환한다.

    python scripts/convert-robots-toml-to-hcl.py adaptor/config/robots.toml \
        -o adaptor/config/robots.hcl

주석은 변환되지 않으므로 `config/robots.hcl.example`을 참고해 정리한다.
변환 후 `robots.toml`은 지운다. 남아 있어도 무시되지만 혼동을 부른다.
```

- [ ] **Step 5: 전체 검증**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
grep -rn "robots\.toml" --include='*.py' --include='*.sh' --include='*.md' \
  --include='*.service' . | grep -v '.worktrees\|/WORKING/\|docs/superpowers/plans\|docs/superpowers/specs\|convert-robots-toml-to-hcl'
cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
cd /ssd2/workspaces/unified-amr-adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/python -m pytest tests -q
git diff --check
```

Expected: grep 결과는 변환 스크립트 이름과 마이그레이션 안내 문서만 남는다.
adaptor 스위트는 베이스라인과 동일한 `47 failed`이고 그 외 새 실패가 없어야 한다.
루트 `tests`는 전부 통과. `git diff --check`는 무출력

- [ ] **Step 6: 시뮬레이터 기동 확인**

Run:

```bash
cd adaptor && timeout 20 .venv/bin/python main.py --robot HN-SH6-TR-001 2>&1 | head -30
```

Expected: `robots.hcl`에서 로봇을 찾아 시뮬레이터로 기동하는 로그. `FleetError`가 나오면 안 된다

- [ ] **Step 7: 커밋**

```bash
git add README.md adaptor/readme.md docs/guide/adaptor-tui.md \
  docs/guide/jibot-onboard-quick-guide.md docs/guide/simulator.md \
  docs/manual/jibot-adapter-ssh-update.md docs/reference/jibot-onboard-access.md
git add -u adaptor/config/robots.toml adaptor/config/robots.toml.example
git add tests/test_update_jibot_adapter_over_ssh.py
git add adaptor/run_multi.py   # Step 3b에서 수정했을 때만
git commit -m "docs: migrate robot inventory references to robots.hcl"
```

`git add -A`를 쓰지 않는다. 이 브랜치의 워킹 트리에는 이 계획과 무관한 미커밋
작업(extension_groups 구현, WORKING 로그)이 함께 있어서, 전체 스테이징은 관련 없는
파일을 커밋에 끌어들인다. 모든 태스크에서 커밋 대상은 명시적으로 나열한다.

---

### Task 9: WebUI·registry에 남은 낡은 참조 정리

Task 8의 전수 grep이 드러낸 **계획의 공백**이다. 이 계획의 File Structure 목록이
`adaptor/core/registry.py`, `adaptor/core/configio.py`, `adaptor/web/render.py`,
`adaptor/tests/test_web_render.py`를 빠뜨렸다. 그 결과 운영자가 보는 화면에 이제
존재하지 않는 파일 이름이 남아 있다 — 특히 **저장 버튼이 "robots.toml 저장"이라고
표시하면서 실제로는 `robots.hcl`을 저장한다.**

**Files:**
- Modify: `adaptor/web/render.py:1877,1892,1902`
- Modify: `adaptor/core/configio.py:70,74,78,81,85,89,92`
- Modify: `adaptor/core/registry.py:7,389,456`
- Modify: `adaptor/tests/test_web_render.py:103,109,1004,1005,1008,1035`
- Modify: `adaptor/tests/test_fleet_registry.py:112`

**Interfaces:**
- Consumes: `config/robots.hcl`이 fleet 파일이라는 사실. 코드 동작은 바뀌지 않는다
- Produces: 없음 (문구와 테스트 픽스처만 바뀐다)

- [ ] **Step 1: 실패하는 테스트로 바꾸기**

`adaptor/tests/test_web_render.py`의 단언을 새 문구 기준으로 먼저 고친다.

103행 부근 `startup_error` 픽스처 문자열과 109행 단언:

```python
        startup_error=(
            "어댑터가 정상적으로 켜지지 않았습니다. "
            "robots.hcl 설정 오류로 amr-adaptor.service가 시작되지 못했습니다."
        ),
```

```python
    assert "robots.hcl" in out
```

`test_robots_page_renders_comment_preserving_editor_and_override_help`(1004행 부근)의
픽스처와 단언을 HCL로 바꾼다.

```python
    text = 'robot "R-1" {\n  mqtt_host = "192.0.2.1"\n}\n'
    out = render.robots_page(text, "/tmp/robots.hcl", "tok", {})
    assert 'action="/robots"' in out
    assert 'name="text"' in out
    assert 'robot &quot;R-1&quot; {' in out
    assert "mqtt_host" in out
    assert "config.toml 기본값" in out
```

`robots_page`는 `esc()`로 HTML 이스케이프하므로 큰따옴표가 `&quot;`가 된다.

1035행 부근 config 페이지 단언:

```python
    assert "robots.hcl" in out
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_web_render.py -q`
Expected: FAIL — 렌더러가 아직 `robots.toml` 문구를 내보낸다

- [ ] **Step 3: 사용자에게 보이는 문구 고치기**

`adaptor/web/render.py`의 세 곳:

1877행 config 페이지 안내문 — `robots.toml은 실제` → `robots.hcl은 실제`

1892행 robots 페이지 안내문 — `<code>robots.toml</code>은 로봇별` → `<code>robots.hcl</code>은 로봇별`

1902행 저장 버튼 — `robots.toml 저장` → `robots.hcl 저장`

`robots_page`의 docstring도 고친다. 지금은 "array-of-tables fleet file"이라 되어 있는데
HCL은 array-of-tables가 아니다.

```python
def robots_page(text: str, source_path, csrf: str, q: dict) -> str:
    """Comment-preserving raw editor for the HCL fleet file."""
```

- [ ] **Step 4: configio.py 도움말 문구 고치기**

`adaptor/core/configio.py:70-92`의 7개 도움말 문자열에서 `robots.toml` → `robots.hcl`.
이건 WebUI Config 화면에서 운영자에게 보이는 텍스트다.

Run: `sed -i 's/robots\.toml/robots.hcl/g' adaptor/core/configio.py`

`git diff`로 7곳만 바뀌었는지 확인한다.

- [ ] **Step 5: registry.py 주석 고치기**

`adaptor/core/registry.py`의 7, 389, 456행 docstring/주석에서 `robots.toml` → `robots.hcl`.

Run: `sed -i 's/robots\.toml/robots.hcl/g' adaptor/core/registry.py`

- [ ] **Step 6: test_fleet_registry.py의 남은 경로 문자열**

112행 `fleet.DEFAULT_FLEET_PATH = Path("/no/such/robots.toml")` → `Path("/no/such/robots.hcl")`.
존재하지 않는 경로라 동작에는 영향이 없지만 이름만 부정확하다. Task 2 리뷰가 남긴
deferred minor다.

- [ ] **Step 7: 테스트 통과 확인**

Run:

```bash
cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_web_render.py tests/test_fleet_registry.py tests/test_web_server.py -q
cd adaptor && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
```

Expected: 대상 파일들은 전부 통과. 전체 스위트의 실패는
`test_adapter_jibot_v3_order.py` / `test_initial_pose_and_map.py` /
`test_dock_approach_config.py` / `test_goto_nearest_node.py` 안에만 있어야 한다

- [ ] **Step 8: 최종 grep 검증**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
grep -rn "robots\.toml" --include='*.py' --include='*.sh' --include='*.md' --include='*.service' . \
  | grep -v '\.worktrees\|/WORKING/\|docs/superpowers/\|convert-robots-toml-to-hcl\|\.superpowers/'
```

Expected: 남는 것은 두 종류뿐이다 — `tests/test_convert_robots_to_hcl.py`(변환기의 입력이
TOML이므로 정당)와 `docs/manual/jibot-adapter-ssh-update.md`의 마이그레이션 안내 절
(옛 파일을 가리키는 의도된 언급)

- [ ] **Step 9: 커밋**

```bash
git add adaptor/web/render.py adaptor/core/configio.py adaptor/core/registry.py \
  adaptor/tests/test_web_render.py adaptor/tests/test_fleet_registry.py
git commit -m "refactor(web): rename remaining robots.toml references to robots.hcl"
```

---

## 다음 단계

이 계획이 끝나면 설계 문서 14절의 나머지 단계가 남는다.

2. extensions.hcl 분리 (`[pio]` `[ezi]` `[air_shower_pio]` `[elevator_pio]` `[[action_modules]]` `[[actions]]` 이전, 부재 시 부팅 실패)
3. recipe 개명·실행 의미 (cleanup 결과 정책, 시간 예산)
4. 주행 primitive 분리 + 소유권 게이트
5. 완료 브리지 + 취소 계약
6. 상태 머신 extension 승격

각 단계는 이 계획이 통과한 뒤 별도 계획으로 작성한다.
