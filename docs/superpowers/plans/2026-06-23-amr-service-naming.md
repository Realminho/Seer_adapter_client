# AMR systemd 서비스 명칭 통일 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** systemctl로 제어하는 유닛 이름을 `amr-*` 네임스페이스로 통일한다 — 기본 어댑터는 벤더 없는 `amr-adaptor.service`, 멀티는 config 기반 `amr-adaptor@<name>.service`, 카메라는 `amr-camera.service`.

**Architecture:** 기존 파이썬 엔트리포인트(`main.py`/`main_hexplorer.py`)는 그대로 두고, 새 쉘 디스패처 `run-adapter.sh`가 config의 vendor 필드를 읽어 `run-main.sh`(jibot)/`run-hexplorer.sh`(hexplorer)로 분기한다. config에 `[adapter]`(기본 벤더) + `[[adapter.instances]]`(멀티)를 추가하고, setup 스크립트·registry·web·문서를 새 이름으로 맞춘다.

**Tech Stack:** Python 3.10 (robot) / 3.12 (dev), bash, systemd, TOML(tomllib/tomli), unittest + pytest.

## Global Constraints

- **Python 호환성**: `adaptor/pyproject.toml`은 `requires-python = ">=3.11"`로 선언돼 있으나, 로봇 온보드는 실제로 **Python 3.10**으로 구동된다(과거 `datetime.UTC` 등 3.11 전용 API가 로봇에서만 깨진 전례 있음). 따라서 이 작업에서 추가하는 코드는 **3.10 호환**을 유지한다(`tomllib` 없으면 `tomli` 폴백, 3.11+ 전용 API 금지). pyproject의 floor는 이 작업에서 바꾸지 않는다(별도 결정 사안). 일부 온보드는 **Ubuntu 16.04 / bash 4.3 / polkit 0.105**다 — bash 4.3에서 `set -u` 하의 빈 배열 전개(`"${arr[@]}"`)는 에러나므로 길이 가드 후 전개한다.
- VDA5050 graceful shutdown 설정(`KillSignal=SIGINT`, `TimeoutStopSec=15`)과 `CPUAccounting=yes`(웹UI CPU% 표시)는 새 어댑터 유닛에 그대로 유지한다.
- 벤더 기본값은 어디서나 `"jibot"`다(하위 호환).
- `web_video_server` ROS 패키지/바이너리 이름은 바꾸지 않는다 — systemd **유닛 이름만** `amr-camera.service`로.
- `edge-agent*`, `urobot.service`, `amr-webui.service`, `polkit`은 이름 변경 대상이 아니다.
- 어댑터(`adaptor/`) 테스트는 `cd adaptor && python -m unittest tests.<module> -v`로, 리포 루트 테스트는 `python -m pytest tests/<file>.py -v`로 실행한다. `python`은 프로젝트 인터프리터(`adaptor/.venv/bin/python` 또는 `adaptor/venvJIBOT/bin/python`)를 쓴다.

---

### Task 1: config — `[adapter]` 벤더/인스턴스 파싱

**Files:**
- Modify: `adaptor/config/config.py` (dataclass 추가 + `get_config` 파싱 + `Config` 필드)
- Modify: `adaptor/config/config.toml` (기본 `[adapter]` 섹션 추가)
- Test: `adaptor/tests/test_config_adapter.py` (신규)

**Interfaces:**
- Produces:
  - `AdapterInstance(name: str, vendor: str = "jibot", config: Optional[str] = None)`
  - `AdapterConfig(vendor: str = "jibot", instances: List[AdapterInstance] = [])`
  - `config.py` 모듈 함수 `_adapter_from_dict(d: Mapping) -> AdapterConfig`
  - `Config.adapter: AdapterConfig`

- [ ] **Step 1: Write the failing test**

`adaptor/tests/test_config_adapter.py`:

```python
import unittest

from config.config import AdapterConfig, AdapterInstance, _adapter_from_dict, get_config


class AdapterConfigParseTest(unittest.TestCase):
    def test_empty_defaults_to_jibot(self):
        adapter = _adapter_from_dict({})
        self.assertEqual(adapter.vendor, "jibot")
        self.assertEqual(adapter.instances, [])

    def test_vendor_only(self):
        adapter = _adapter_from_dict({"vendor": "hexplorer"})
        self.assertEqual(adapter.vendor, "hexplorer")
        self.assertEqual(adapter.instances, [])

    def test_instances_parsed(self):
        adapter = _adapter_from_dict(
            {
                "vendor": "jibot",
                "instances": [
                    {"name": "line1", "vendor": "hexplorer", "config": "config/line1.toml"},
                    {"name": "line2"},
                ],
            }
        )
        self.assertEqual(
            adapter.instances,
            [
                AdapterInstance(name="line1", vendor="hexplorer", config="config/line1.toml"),
                AdapterInstance(name="line2", vendor="jibot", config=None),
            ],
        )

    def test_real_config_has_adapter_section(self):
        cfg = get_config()
        self.assertIsInstance(cfg.adapter, AdapterConfig)
        self.assertEqual(cfg.adapter.vendor, "jibot")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_config_adapter -v`
Expected: FAIL — `ImportError: cannot import name 'AdapterConfig'`.

- [ ] **Step 3: Add the dataclasses + parser + Config field**

In `adaptor/config/config.py`, add after `class VideoConfig` (before `@dataclass class Config`):

```python
@dataclass
class AdapterInstance:
    name: str
    vendor: str = "jibot"
    config: Optional[str] = None

@dataclass
class AdapterConfig:
    vendor: str = "jibot"
    instances: List[AdapterInstance] = field(default_factory=list)
```

Add `adapter` to the `Config` dataclass (after `video: VideoConfig`):

```python
    video: VideoConfig
    adapter: AdapterConfig
```

Add the parser helper (module level, e.g. just above `get_config`):

```python
def _adapter_from_dict(adapter_dict: Mapping[str, Any]) -> AdapterConfig:
    """Build AdapterConfig from the raw ``[adapter]`` TOML table.

    vendor 기본값은 "jibot"이고, [[adapter.instances]]는 인스턴스 목록이 된다.
    """
    instances = [
        AdapterInstance(**inst) for inst in adapter_dict.get("instances", [])
    ]
    return AdapterConfig(
        vendor=adapter_dict.get("vendor", "jibot"),
        instances=instances,
    )
```

In `get_config`, build it (next to `video = ...`):

```python
    video = VideoConfig(**config_dict.get("video", {}))
    adapter = _adapter_from_dict(config_dict.get("adapter", {}))
```

And pass it into the `Config(...)` constructor (after `video=video,`):

```python
        video=video,
        adapter=adapter,
    )
```

- [ ] **Step 4: Add the default `[adapter]` section to config.toml**

In `adaptor/config/config.toml`, add immediately after the `[mqtt_broker]` block (before `[vehicle]`):

```toml
[adapter]
# 이 호스트의 기본 단일 어댑터 벤더: "jibot" | "hexplorer". 기본값 "jibot".
# amr-adaptor.service 가 이 벤더로 뜬다.
vendor = "jibot"
# 한 호스트에 어댑터가 여러 개일 때만 사용. 각 항목이 amr-adaptor@<name>.service.
# [[adapter.instances]]
# name   = "line1"
# vendor = "hexplorer"
# config = "config/line1.toml"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_config_adapter -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/tests/test_config_adapter.py
git commit -m "feat(config): add [adapter] vendor + instances parsing"
```

---

### Task 2: 디스패치 해석기 `resolve_dispatch`

**Files:**
- Create: `adaptor/config/adapter_dispatch.py`
- Test: `adaptor/tests/test_adapter_dispatch.py` (신규)

**Interfaces:**
- Produces: `resolve_dispatch(instance: Optional[str], *, config_path=None) -> Tuple[str, List[str]]` — `(vendor, extra_args)`. `python -m config.adapter_dispatch <instance>` 는 첫 줄에 vendor, 이후 줄마다 인자 하나씩 출력.

- [ ] **Step 1: Write the failing test**

`adaptor/tests/test_adapter_dispatch.py`:

```python
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config.adapter_dispatch import resolve_dispatch


def _write(tmp, name, body):
    path = Path(tmp) / name
    path.write_text(textwrap.dedent(body).strip() + "\n")
    return str(path)


class ResolveDispatchTest(unittest.TestCase):
    def test_default_no_instance_uses_adapter_vendor(self):
        with TemporaryDirectory() as tmp:
            cfg = _write(tmp, "config.toml", '[adapter]\nvendor = "hexplorer"\n')
            self.assertEqual(resolve_dispatch(None, config_path=cfg), ("hexplorer", []))

    def test_default_missing_adapter_table_is_jibot(self):
        with TemporaryDirectory() as tmp:
            cfg = _write(tmp, "config.toml", "[mqtt_broker]\nhost = \"x\"\n")
            self.assertEqual(resolve_dispatch(None, config_path=cfg), ("jibot", []))

    def test_named_instance_match(self):
        with TemporaryDirectory() as tmp:
            cfg = _write(
                tmp,
                "config.toml",
                """
                [adapter]
                vendor = "jibot"
                [[adapter.instances]]
                name = "line1"
                vendor = "hexplorer"
                config = "config/line1.toml"
                """,
            )
            self.assertEqual(
                resolve_dispatch("line1", config_path=cfg),
                ("hexplorer", ["--config", "config/line1.toml"]),
            )

    def test_unmatched_instance_is_jibot_robot(self):
        # 명시 인스턴스가 아니면 jibot fleet의 robot id(--robot)로 간주한다.
        with TemporaryDirectory() as tmp:
            cfg = _write(tmp, "config.toml", '[adapter]\nvendor = "jibot"\n')
            self.assertEqual(
                resolve_dispatch("ROBOT-A", config_path=cfg),
                ("jibot", ["--robot", "ROBOT-A"]),
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_adapter_dispatch -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config.adapter_dispatch'`.

- [ ] **Step 3: Create the resolver**

`adaptor/config/adapter_dispatch.py`:

```python
"""Resolve a systemd instance name to (vendor, launcher-args) for run-adapter.sh.

amr-adaptor.service(기본) 또는 amr-adaptor@<instance>.service의 <instance>를
받아 어느 벤더로/어떤 인자로 띄울지 결정한다. main.py / main_hexplorer.py는
그대로 두고, 이 결과로 run-main.sh / run-hexplorer.sh를 고른다.

해석 우선순위:
    1) instance 없음                    -> [adapter].vendor (기본 "jibot"), 인자 없음
    2) [[adapter.instances]] name 일치  -> 그 vendor, config 있으면 --config <path>
    3) 그 외(robots.toml robot id 포함)  -> ("jibot", ["--robot", <instance>])
"""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import sys
from pathlib import Path
from typing import List, Optional, Tuple

_CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"


def _adapter_table(config_path: Optional[str]) -> dict:
    path = Path(config_path) if config_path else _CONFIG_PATH
    with open(path, "rb") as fh:
        return tomllib.load(fh).get("adapter", {})


def resolve_dispatch(
    instance: Optional[str],
    *,
    config_path: Optional[str] = None,
) -> Tuple[str, List[str]]:
    adapter = _adapter_table(config_path)
    if not instance:
        return adapter.get("vendor", "jibot"), []

    for inst in adapter.get("instances", []):
        if inst.get("name") == instance:
            vendor = inst.get("vendor", "jibot")
            args = ["--config", inst["config"]] if inst.get("config") else []
            return vendor, args

    # 명시 인스턴스가 아니면 jibot fleet의 robot id로 간주한다.
    return "jibot", ["--robot", instance]


def _main(argv: List[str]) -> int:
    instance = argv[1] if len(argv) > 1 and argv[1] else None
    vendor, args = resolve_dispatch(instance)
    print(vendor)
    for arg in args:
        print(arg)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_adapter_dispatch -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/config/adapter_dispatch.py adaptor/tests/test_adapter_dispatch.py
git commit -m "feat(config): resolve_dispatch for amr-adaptor vendor selection"
```

---

### Task 3: 쉘 디스패처 `run-adapter.sh`

**Files:**
- Create: `adaptor/run-adapter.sh`
- Test: `scripts/test-run-adapter-dispatch.sh` (신규)

**Interfaces:**
- Consumes: `python -m config.adapter_dispatch <instance>` (Task 2).
- Produces: `run-adapter.sh [--instance <name>] [extra args...]` — vendor에 따라 `run-main.sh`/`run-hexplorer.sh`를 exec. `AMR_DISPATCH_PRINT=1`이면 exec 대신 `"<launcher> <args>"` 한 줄을 출력하고 종료.

- [ ] **Step 1: Write the failing test**

`scripts/test-run-adapter-dispatch.sh`:

```bash
#!/usr/bin/env bash
# Smoke test for run-adapter.sh dispatch (print mode, no real exec).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/adaptor"

out="$(AMR_DISPATCH_PRINT=1 ./run-adapter.sh)"
echo "default -> $out"
case "$out" in
  *run-main.sh*) echo "PASS: default dispatches to run-main.sh (jibot)" ;;
  *) echo "FAIL: expected run-main.sh, got: $out" >&2; exit 1 ;;
esac
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/test-run-adapter-dispatch.sh`
Expected: FAIL — `./run-adapter.sh: No such file or directory`.

- [ ] **Step 3: Create the dispatcher**

`adaptor/run-adapter.sh`:

```bash
#!/usr/bin/env bash
# Vendor dispatcher for amr-adaptor.service / amr-adaptor@<instance>.service.
# Reads the vendor for this instance from config (config/adapter_dispatch.py)
# and execs the matching launcher: run-main.sh (jibot) or run-hexplorer.sh
# (hexplorer). main.py / main_hexplorer.py themselves are unchanged.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INSTANCE=""
if [[ "${1:-}" == "--instance" ]]; then
  INSTANCE="${2:-}"
  shift 2
fi

PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venvJIBOT/bin/python" ]]; then
  PYTHON_BIN="venvJIBOT/bin/python"
fi

# First line = vendor, remaining lines = extra launcher args.
mapfile -t DISPATCH < <("$PYTHON_BIN" -m config.adapter_dispatch "$INSTANCE")
VENDOR="${DISPATCH[0]:-jibot}"
EXTRA_ARGS=()
[[ ${#DISPATCH[@]} -gt 1 ]] && EXTRA_ARGS=("${DISPATCH[@]:1}")

case "$VENDOR" in
  hexplorer) LAUNCHER="./run-hexplorer.sh" ;;
  *)         LAUNCHER="./run-main.sh" ;;
esac

# bash 4.3 (onboard): guard empty-array expansion under `set -u`.
ARGS=()
[[ ${#EXTRA_ARGS[@]} -gt 0 ]] && ARGS+=("${EXTRA_ARGS[@]}")
[[ $# -gt 0 ]] && ARGS+=("$@")

if [[ "${AMR_DISPATCH_PRINT:-0}" == "1" ]]; then
  echo "$LAUNCHER ${ARGS[*]:-}"
  exit 0
fi

exec "$LAUNCHER" ${ARGS[@]+"${ARGS[@]}"}
```

Make it executable:

```bash
chmod +x adaptor/run-adapter.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/test-run-adapter-dispatch.sh`
Expected: `PASS: default dispatches to run-main.sh (jibot)`.

- [ ] **Step 5: Commit**

```bash
git add adaptor/run-adapter.sh scripts/test-run-adapter-dispatch.sh
git commit -m "feat(adaptor): run-adapter.sh vendor dispatcher"
```

---

### Task 4: systemd 유닛 리네임 (`amr-adaptor` / `amr-adaptor@` / 구 유닛 삭제)

**Files:**
- Create: `scripts/systemd/amr-adaptor.service`
- Create: `scripts/systemd/amr-adaptor@.service`
- Delete: `scripts/systemd/jibot-adapter.service`, `scripts/systemd/jibot-adapter@.service`, `scripts/systemd/hexplorer-adapter.service`
- Test: `tests/test_systemd_units.py` (신규, 리포 루트)

**Interfaces:**
- Produces: `ExecStart=__WORKDIR__/run-adapter.sh` (기본), `ExecStart=__WORKDIR__/run-adapter.sh --instance %i` (템플릿).

- [ ] **Step 1: Write the failing test**

`tests/test_systemd_units.py`:

```python
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = REPO_ROOT / "scripts" / "systemd"


def test_amr_adapter_units_exist_and_use_dispatcher():
    base = (SYSTEMD / "amr-adaptor.service").read_text()
    assert "run-adapter.sh" in base
    assert "KillSignal=SIGINT" in base
    assert "CPUAccounting=yes" in base

    tmpl = (SYSTEMD / "amr-adaptor@.service").read_text()
    assert "run-adapter.sh --instance %i" in tmpl


def test_old_adapter_units_removed():
    for stale in ("jibot-adapter.service", "jibot-adapter@.service", "hexplorer-adapter.service"):
        assert not (SYSTEMD / stale).exists(), f"{stale} should be deleted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_systemd_units.py -v`
Expected: FAIL — `amr-adaptor.service` 없음 / 구 유닛 존재.

- [ ] **Step 3: Create `amr-adaptor.service`**

`scripts/systemd/amr-adaptor.service`:

```ini
[Unit]
# AMR VDA5050 adapter (default single instance). Installed by
# scripts/setup-adaptor-service.sh, which substitutes __USER__ / __WORKDIR__ for
# this host. The vendor (jibot/hexplorer) comes from config [adapter].vendor;
# run-adapter.sh dispatches to the right launcher. Manage with the TUI
# (scripts/adaptor-tui.sh) or plain systemctl.
Description=AMR VDA5050 adapter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=__USER__
WorkingDirectory=__WORKDIR__
# run-adapter.sh reads the vendor from config and execs run-main.sh /
# run-hexplorer.sh (which activate the venv and exec python).
ExecStart=__WORKDIR__/run-adapter.sh
Restart=on-failure
RestartSec=3
# Graceful VDA5050 OFFLINE publish happens on SIGINT/SIGTERM in main.py.
KillSignal=SIGINT
TimeoutStopSec=15
# Enable CPU accounting so `systemctl show` reports CPUUsageNSec; the web UI
# derives the adapter's CPU% from it (core/systemd.py).
CPUAccounting=yes

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Create `amr-adaptor@.service`**

`scripts/systemd/amr-adaptor@.service`:

```ini
[Unit]
# Per-instance AMR VDA5050 adapter (systemd template unit). %i is the instance
# name — a robots.toml robot id (JIBOT fleet) or an [[adapter.instances]] name
# from config.toml, e.g.
#   systemctl start amr-adaptor@HN-SH6-TR-001.service
# Installed by scripts/setup-adaptor-service.sh, which substitutes __USER__ /
# __WORKDIR__. run-adapter.sh --instance %i resolves the vendor + args.
Description=AMR VDA5050 adapter (instance %i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=__USER__
WorkingDirectory=__WORKDIR__
ExecStart=__WORKDIR__/run-adapter.sh --instance %i
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=15
CPUAccounting=yes

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Delete the old unit files**

```bash
git rm scripts/systemd/jibot-adapter.service scripts/systemd/jibot-adapter@.service scripts/systemd/hexplorer-adapter.service
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_systemd_units.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add scripts/systemd/amr-adaptor.service scripts/systemd/amr-adaptor@.service tests/test_systemd_units.py
git commit -m "feat(systemd): amr-adaptor[.service|@.service] replace per-vendor units"
```

---

### Task 5: registry 유닛명/노출 + Hexplorer 엔트리포인트 `--config`

**Files:**
- Modify: `adaptor/core/registry.py` (`_jibot_specs` 유닛명, 새 `_hexplorer_spec`, `build_registry` 재작성)
- Modify: `adaptor/adapter_hexplorer.py` (`HexplorerAdapter.__init__`에 `config_path` 주입)
- Modify: `adaptor/main_hexplorer.py` (`--config` 파싱 → `config_path` 전달)
- Test: `adaptor/tests/test_fleet_registry.py`, `adaptor/tests/test_registry.py` (assert 갱신), `adaptor/tests/test_main_hexplorer_args.py` (신규)

**Interfaces:**
- Consumes: `config.adapter` (`AdapterConfig`, Task 1).
- Produces:
  - `_hexplorer_spec(config, *, key: str, display_name: str, unit: str) -> AdaptorSpec`
  - `build_registry`가 config.adapter 구성에 따라 spec을 만든다(아래 규칙). edge agents는 기존대로 append.
  - `HexplorerAdapter(client=None, *, config_path: Optional[str] = None)` — `config_path`를 `get_config`로 전달.
  - `main_hexplorer._parse_args(argv=None)` — `--config` 옵션을 갖는 `argparse.Namespace`.

> F1: 디스패처(Task 2/3)는 hexplorer 인스턴스에 `--config <path>`를 넘기므로, `main_hexplorer.py`가 이를 파싱해 `HexplorerAdapter`에 주입해야 실제로 인스턴스별 config가 적용된다. 이 task가 그 연결을 완성한다.

- [ ] **Step 1: Update `_jibot_specs` unit names**

`adaptor/core/registry.py`의 `_jibot_specs` 안에서 유닛 문자열 3곳을 바꾼다:

- `unit="jibot-adapter.service"` (FleetError 폴백) → `unit="amr-adaptor.service"`
- `unit="jibot-adapter.service"` (단일 로봇) → `unit="amr-adaptor.service"`
- `unit=f"jibot-adapter@{robot_id}.service"` → `unit=f"amr-adaptor@{robot_id}.service"`

- [ ] **Step 2: Extract `_hexplorer_spec` and rewrite `build_registry`**

기존 `build_registry`의 인라인 hexplorer 생성을 별도 함수로 빼고, config 기반으로 다시 쓴다. `build_registry`(364–409행)를 통째로 아래로 교체:

```python
def _hexplorer_spec(config, *, key: str, display_name: str, unit: str) -> AdaptorSpec:
    """Build one Hexplorer spec from an already-resolved ``Config``."""
    serial = config.vehicle.serial_number
    prefix = _topic_prefix(
        config.mqtt_broker.vda_interface, config.vehicle.vda_version, serial
    )
    return AdaptorSpec(
        key=key,
        display_name=display_name,
        unit=unit,
        workdir=ADAPTER_ROOT,
        exec_script="run-hexplorer.sh",
        manufacturer="dobot",
        serial=serial,
        vda_full_version=config.vehicle.vda_full_version,
        topic_prefix=prefix,
        mqtt_host=config.mqtt_broker.host,
        mqtt_port=config.mqtt_broker.port,
        vehicle_host=config.vehicle.vehicle_ip,
        vehicle_port=config.vehicle.vehicle_port,
        test_suites=(
            Diagnostic(
                "unittest: hexplorer state",
                ("{py}", "-m", "unittest", "tests.test_adapter_hexplorer_state", "-v"),
            ),
        ),
        diagnostics=(),
        instant_actions=(
            InstantAction("getCameraInfo", "Get camera info", motion=False),
            InstantAction("stop", "Stop (zero velocity)", motion=True),
            InstantAction("standUp", "Stand up", motion=True),
            InstantAction("standDown", "Stand down", motion=True),
            InstantAction("walkMode", "Walk mode", motion=True),
        ),
    )


def build_registry(config) -> List[AdaptorSpec]:
    """Build adaptor specs from the host's configured adapters.

    config.toml의 [adapter] 구성을 반영한다:
      - [[adapter.instances]]가 있으면 인스턴스마다 amr-adaptor@<name>.service.
      - 없으면 [adapter].vendor가 hexplorer면 단일 hexplorer(amr-adaptor.service),
        그 외에는 JIBOT(robots.toml fleet 규칙: amr-adaptor[.service|@<id>]).
    edge-agent는 기존대로 discover해서 덧붙인다.
    """
    from config.config import get_config

    specs: List[AdaptorSpec] = []
    instances = config.adapter.instances

    if instances:
        for inst in instances:
            unit = f"amr-adaptor@{inst.name}.service"
            effective = get_config(config_path=inst.config) if inst.config else config
            if inst.vendor == "hexplorer":
                specs.append(
                    _hexplorer_spec(
                        effective,
                        key=f"hexplorer:{inst.name}",
                        display_name=f"Hexplorer {inst.name}",
                        unit=unit,
                    )
                )
            else:
                specs.append(
                    _jibot_spec(
                        effective,
                        key=f"jibot:{inst.name}",
                        display_name=f"JIBOT {inst.name}",
                        unit=unit,
                    )
                )
    elif config.adapter.vendor == "hexplorer":
        specs.append(
            _hexplorer_spec(
                config,
                key="hexplorer",
                display_name="Hexplorer Adapter",
                unit="amr-adaptor.service",
            )
        )
    else:
        specs.extend(_jibot_specs(config))

    edge_agents = [make_edge_agent_spec(unit) for unit in discover_edge_agent_units()]
    return [*specs, *edge_agents]
```

- [ ] **Step 3: Update `test_fleet_registry.py` asserts**

`adaptor/tests/test_fleet_registry.py`:

- `test_one_jibot_spec_per_robot`: 기본(vendor jibot, 인스턴스 없음)이라 hexplorer는 더 이상 나오지 않는다. 교체:

```python
    def test_one_jibot_spec_per_robot(self):
        specs = self._build_with_fleet(FLEET_TOML)
        by_key = {s.key: s for s in specs}
        self.assertIn("jibot:ROBOT-A", by_key)
        self.assertIn("jibot:ROBOT-B", by_key)
        self.assertNotIn("hexplorer", by_key)

        a = by_key["jibot:ROBOT-A"]
        self.assertEqual(a.unit, "amr-adaptor@ROBOT-A.service")
        self.assertEqual(a.topic_prefix, "amr/v3/ROBOT-A")
        self.assertEqual(a.serial, "ROBOT-A")

        b = by_key["jibot:ROBOT-B"]
        self.assertEqual(b.unit, "amr-adaptor@ROBOT-B.service")
        self.assertEqual(b.topic_prefix, "amr/v3/ROBOT-B")
```

- `test_single_robot_fleet_uses_default_service_name`: 교체:

```python
    def test_single_robot_fleet_uses_default_service_name(self):
        specs = self._build_with_fleet(SINGLE_FLEET_TOML)
        keys = [s.key for s in specs]
        self.assertEqual(keys, ["jibot"])
        jibot = specs[0]
        self.assertEqual(jibot.display_name, "JIBOT ROBOT-A")
        self.assertEqual(jibot.unit, "amr-adaptor.service")
        self.assertEqual(jibot.topic_prefix, "amr/v3/ROBOT-A")
        self.assertEqual(jibot.serial, "ROBOT-A")
```

- `test_falls_back_to_single_unit_without_fleet`: 교체:

```python
    def test_falls_back_to_single_unit_without_fleet(self):
        original = fleet.DEFAULT_FLEET_PATH
        fleet.DEFAULT_FLEET_PATH = Path("/no/such/robots.toml")
        try:
            specs = build_registry(get_config())
        finally:
            fleet.DEFAULT_FLEET_PATH = original
        keys = [s.key for s in specs]
        self.assertEqual(keys, ["jibot"])
        self.assertEqual(specs[0].unit, "amr-adaptor.service")
```

- 새 테스트 2개 추가(클래스 `RegistryFleetTest` 안):

```python
    def test_hexplorer_default_vendor_uses_amr_adapter_unit(self):
        from config.config import AdapterConfig
        cfg = get_config()
        cfg.adapter = AdapterConfig(vendor="hexplorer")
        specs = build_registry(cfg)
        keys = [s.key for s in specs]
        self.assertEqual(keys, ["hexplorer"])
        self.assertEqual(specs[0].unit, "amr-adaptor.service")
        self.assertEqual(specs[0].exec_script, "run-hexplorer.sh")

    def test_instances_produce_named_units(self):
        from config.config import AdapterConfig, AdapterInstance
        cfg = get_config()
        cfg.adapter = AdapterConfig(
            instances=[
                AdapterInstance(name="j1", vendor="jibot"),
                AdapterInstance(name="h1", vendor="hexplorer"),
            ]
        )
        by_key = {s.key: s for s in build_registry(cfg)}
        self.assertEqual(by_key["jibot:j1"].unit, "amr-adaptor@j1.service")
        self.assertEqual(by_key["hexplorer:h1"].unit, "amr-adaptor@h1.service")
```

- [ ] **Step 4: Update `test_registry.py` asserts**

`adaptor/tests/test_registry.py`:

- `test_build_registry_appends_discovered_edge_agents` (76–85행): stub Config에 `adapter` 추가하고 hexplorer 단독 시나리오로 만든다. `with patch("core.registry._jibot_specs", ...)` 줄을 제거하고, Config에 hexplorer vendor를 준다:

```python
    def test_build_registry_appends_discovered_edge_agents(self):
        from config.config import AdapterConfig

        class Vehicle:
            serial_number = "HN"
            vda_full_version = "3.0.0"
            vda_version = "v3"
            vehicle_ip = "10.0.0.21"
            vehicle_port = 7273

        class Broker:
            vda_interface = "uagv"
            host = "127.0.0.1"
            port = 1883

        class Config:
            vehicle = Vehicle()
            mqtt_broker = Broker()
            adapter = AdapterConfig(vendor="hexplorer")

        units = [EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service")]
        with patch("core.registry.discover_edge_agent_units", return_value=units):
            specs = registry.build_registry(Config())

        self.assertEqual(
            [spec.key for spec in specs], ["hexplorer", "edge-agent:cell-a"]
        )
        self.assertEqual(specs[0].vehicle_host, "10.0.0.21")
        self.assertEqual(specs[0].vehicle_port, 7273)
```

- `test_jibot_spec_adds_clamp_pio_and_ezi_diagnostics` (110–115행): `_jibot_spec(... unit="jibot-adapter.service")` → `unit="amr-adaptor.service"`.

- [ ] **Step 5: Write the failing Hexplorer entrypoint test**

`adaptor/tests/test_main_hexplorer_args.py`:

```python
import unittest
from unittest.mock import patch

import adapter_hexplorer
from main_hexplorer import _parse_args


class HexplorerArgsTest(unittest.TestCase):
    def test_config_flag_parsed(self):
        self.assertEqual(_parse_args(["--config", "config/line1.toml"]).config, "config/line1.toml")

    def test_config_defaults_to_none(self):
        self.assertIsNone(_parse_args([]).config)


class HexplorerAdapterConfigPathTest(unittest.TestCase):
    def test_config_path_forwarded_to_get_config(self):
        # Real default config so __init__ has all the fields it reads, but assert
        # the path argument is forwarded. client=object() avoids a real client.
        real = adapter_hexplorer.get_config()
        with patch.object(adapter_hexplorer, "get_config", return_value=real) as gc:
            adapter_hexplorer.HexplorerAdapter(client=object(), config_path="config/line1.toml")
        gc.assert_called_once_with(config_path="config/line1.toml")


if __name__ == "__main__":
    unittest.main()
```

> 만약 `HexplorerAdapter(client=object())` 인스턴스화가 부작용(네트워크/ROS)을 일으키면, 두 번째 테스트는 `__init__` 진입 직후 `get_config` 호출만 검증하도록 `self.config` 설정 라인을 첫 줄로 두고(아래 Step 7) `client` 사용을 그 뒤로 미루면 안전하다.

- [ ] **Step 6: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_main_hexplorer_args -v`
Expected: FAIL — `_parse_args` 없음 / `config_path` 인자 미지원.

- [ ] **Step 7: Add `config_path` to HexplorerAdapter + `--config` to main_hexplorer**

`adaptor/adapter_hexplorer.py`의 `__init__` 시그니처/첫 줄을 바꾼다:

```python
    def __init__(self, client: HexplorerClient | None = None, *, config_path: str | None = None) -> None:
        self.config = get_config(config_path=config_path)
```

`adaptor/main_hexplorer.py`를 교체:

```python
"""Runtime entrypoint for the Dobot Hexplorer VDA5050 adapter."""

from __future__ import annotations

import argparse
import asyncio

from adapter_hexplorer import HexplorerAdapter
from protocol.vda5050_3_0.messages import ConnectionState


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the Dobot Hexplorer VDA5050 adapter.")
    parser.add_argument(
        "--config",
        default=None,
        help="Instance config TOML path. Default: config/config.toml.",
    )
    return parser.parse_args(argv)


async def main(config_path: str | None = None) -> None:
    adapter = HexplorerAdapter(config_path=config_path)
    try:
        await adapter.run()
    finally:
        adapter.publish_connection(ConnectionState.OFFLINE)
        adapter.disconnect_mqtt()
        adapter.client.stop()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(config_path=args.config))
```

- [ ] **Step 8: Run all Task 5 tests to verify they pass**

Run: `cd adaptor && python -m unittest tests.test_fleet_registry tests.test_registry tests.test_main_hexplorer_args -v`
Expected: PASS (모든 테스트).

- [ ] **Step 9: Commit**

```bash
git add adaptor/core/registry.py adaptor/adapter_hexplorer.py adaptor/main_hexplorer.py \
        adaptor/tests/test_fleet_registry.py adaptor/tests/test_registry.py \
        adaptor/tests/test_main_hexplorer_args.py
git commit -m "feat(registry): amr-adaptor unit names, config-driven exposure, hexplorer --config"
```

---

### Task 6: WebUi 카메라 유닛 → `amr-camera.service`

**Files:**
- Modify: `adaptor/web/main.py:98`
- Test: `adaptor/tests/test_web_server.py` (camera 유닛 문자열 갱신), `adaptor/tests/test_web_render.py` (어댑터 유닛 fake 이름 갱신)

**Interfaces:**
- Consumes/Produces: 없음(문자열 변경). `SystemdController` 시그니처 불변.

- [ ] **Step 1: Update the failing tests first**

`adaptor/tests/test_web_server.py`에서 `web_video_server.service` → `amr-camera.service`로 모두 교체(302, 327, 357, 378, 397, 416행의 `unit=...`). 368행 `assert "web_video_server" in body`는 그대로 둔다 — body에는 video URL(`web_video_server_url`)로 `web_video_server`가 여전히 등장한다(유닛명이 아니라 URL). 만약 이 assert가 카메라 컨트롤러 유닛명에 의존했던 거라면 `assert "amr-camera" in body`로 바꾼다(실행 후 실패 시 조정).

`adaptor/tests/test_web_render.py`의 어댑터 유닛 fake 이름도 갱신: 10행 `unit: str = "jibot-adapter.service"`, 155행 `assert "jibot-adapter.service" in out` → `amr-adaptor.service`. 480행 `assert "web_video_server" in out`은 video URL이라 **그대로 둔다**.

```bash
cd adaptor && sed -i 's/unit="web_video_server.service"/unit="amr-camera.service"/g' tests/test_web_server.py
cd adaptor && sed -i 's/jibot-adapter\.service/amr-adaptor.service/g' tests/test_web_render.py
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_web_server -v`
Expected: FAIL — 컨트롤러 유닛(`web_video_server.service`)과 테스트(`amr-camera.service`) 불일치하는 케이스에서 실패.

- [ ] **Step 3: Update the camera controller unit**

`adaptor/web/main.py:98`:

```python
                camera_controller=SystemdController("amr-camera.service", use_sudo=False),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && python -m unittest tests.test_web_server tests.test_web_render -v`
Expected: PASS. (test_web_server 368행 assert가 실패하면 `amr-camera`로 조정 후 재실행.)

- [ ] **Step 5: Commit**

```bash
git add adaptor/web/main.py adaptor/tests/test_web_server.py adaptor/tests/test_web_render.py
git commit -m "feat(web): camera systemd unit web_video_server -> amr-camera"
```

---

### Task 7: setup 스크립트 — 유닛/카메라/polkit/sudoers/마이그레이션/플래그

**Files:**
- Modify: `scripts/setup-adaptor-service.sh`
- Test: `tests/test_update_jibot_adapter_over_ssh.py` (assert 갱신)

**Interfaces:**
- Produces: setup가 `amr-adaptor.service` + `amr-adaptor@.service`를 설치, `amr-camera.service` 생성/enable, polkit/sudoers 관리 유닛을 새 이름으로, 구 유닛을 stop/disable/rm.

- [ ] **Step 1: Update the failing tests first**

먼저 새 스크립트 구조에 맞춰 assert를 바꾼다. 이 테스트들은 setup 스크립트 소스를 문자열로 검사하므로, Step 3에서 만들 정확한 라인과 일치해야 한다.

- polkit 관련 (185–189행, `test_setup_script_renders_webui_polkit_units_from_selected_targets`): 기존 `[[ $DO_JIBOT ... ]] && polkit_units+=(...)` 4줄 assert를 아래로 교체:

```python
    assert "render_webui_polkit" in text
    assert 'local polkit_units=("amr-adaptor.service" "urobot.service")' in text
    assert '[[ $DO_CAMERA -eq 1 ]] && polkit_units+=("amr-camera.service")' in text
    # 구 어댑터 유닛은 polkit 목록에 없다.
    assert "jibot-adapter.service" not in text.split("migrate_legacy_units")[0]
```

- 209행 `assert "hexplorer-adapter.service" not in result.stdout`: 그대로 둔다(구 이름이 dry-run 출력에 안 나오는지 확인). 단, 마이그레이션 문맥에서 등장할 수 있으므로 실패하면 `assert "install_unit \"hexplorer-adapter.service\"" not in result.stdout`로 좁힌다.
- 289행 `JIBOT_UNITS+=("jibot-adapter.service")` → `JIBOT_UNITS+=("amr-adaptor.service")`
- 290행 `JIBOT_UNITS+=("jibot-adapter@${rid}.service")` → `JIBOT_UNITS+=("amr-adaptor@${rid}.service")`
- 297행 `assert "web_video_server.service" in text` → `assert "amr-camera.service" in text`
- 299행 `assert "sudo systemctl enable --now web_video_server.service" not in text` → `... "amr-camera.service" not in text`

마이그레이션 + 인스턴스 enable 검증 테스트 추가:

```python
def test_setup_migrates_old_unit_names():
    text = SETUP_SCRIPT.read_text()
    assert "migrate_legacy_units" in text
    for stale in ("jibot-adapter.service", "hexplorer-adapter.service", "web_video_server.service"):
        assert stale in text  # 마이그레이션 대상으로 언급


def test_setup_enables_config_adapter_instances():
    text = SETUP_SCRIPT.read_text()
    # config.toml [[adapter.instances]]의 이름으로 amr-adaptor@<name>.service를 enable.
    assert "adapter.instances" in text
    assert 'JIBOT_UNITS+=("amr-adaptor@${name}.service")' in text
```

> `SETUP_SCRIPT` 상수가 없으면 파일 상단 상수부에 추가: `SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup-adaptor-service.sh"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_update_jibot_adapter_over_ssh.py -v`
Expected: FAIL — 새 이름/마이그레이션 헬퍼 부재.

- [ ] **Step 3: Update `setup-adaptor-service.sh`**

다음을 적용한다(파일: `scripts/setup-adaptor-service.sh`):

1. **install_sudoers** (171–172행) 유닛 목록 → 새 이름, hexplorer 분기 제거:

```bash
  units+=("amr-adaptor.service" "amr-adaptor@*.service")
```
(기존 `[[ $DO_JIBOT ... jibot-adapter ...]]`와 `[[ $DO_HEXPLORER ... hexplorer-adapter ...]]` 두 줄을 위 한 줄로 대체)

2. **render_webui_polkit** (224–244행) 관리 유닛 → 새 이름, 템플릿 규칙의 접두 변경:

```bash
  local polkit_units=("amr-adaptor.service" "urobot.service")
  [[ $DO_CAMERA -eq 1 ]] && polkit_units+=("amr-camera.service")
```
그리고 템플릿 규칙(239–243행)의 `unit.indexOf("jibot-adapter@")` → `unit.indexOf("amr-adaptor@")`. 이 규칙은 항상 설치하므로 `if [[ $DO_JIBOT -eq 1 ]]` 가드를 제거(또는 그대로 둬도 무방하나, 새 모델에선 vendor 무관하게 amr-adaptor@가 쓰이므로 항상 포함).

3. **install_web_video_server** → 유닛 경로/enable을 `amr-camera.service`로. (373, 424, 440, 443, 446, 447행)
   - 함수 첫 echo: `echo "==> amr-camera.service"`
   - 렌더 `Description=web_video_server (...)`는 유지(설명은 ROS 패키지명 그대로).
   - dry-run/실설치 경로 `/etc/systemd/system/web_video_server.service` → `/etc/systemd/system/amr-camera.service`
   - `enable web_video_server.service` → `enable amr-camera.service`

4. **어댑터 유닛 설치** (457–461행) → DO_JIBOT/DO_HEXPLORER 분기를 단일 설치로 교체. 새 모델은 항상 amr-adaptor 유닛을 설치한다:

```bash
install_unit "amr-adaptor.service"
install_unit "amr-adaptor@.service"
```
(`install_unit "jibot-adapter.service"` / `"jibot-adapter@.service"` / `[[ $DO_HEXPLORER ...]] && install_unit "hexplorer-adapter.service"` 제거)

5. **enable 로직** (475–504행): config.toml `[[adapter.instances]]`를 먼저 보고, 있으면 그 이름들로 `amr-adaptor@<name>.service`를 enable한다. 없으면 기존 robots.toml fleet 판단을 새 이름으로 쓴다. hexplorer enable 블록(501–504행)은 제거. 아래로 교체:

```bash
# config.toml [[adapter.instances]] 이름 목록(있으면 한 줄에 하나).
list_adapter_instances() {
  local py="$ADAPTER_DIR/venvJIBOT/bin/python"
  [[ -x "$py" ]] || py="$ADAPTER_DIR/.venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  "$py" - <<'PY' 2>/dev/null || true
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
with open("config/config.toml", "rb") as fh:
    data = tomllib.load(fh)
for inst in data.get("adapter", {}).get("instances", []):
    name = inst.get("name")
    if name:
        print(name)
PY
}

JIBOT_UNITS=()
ADAPTER_INSTANCES="$(cd "$ADAPTER_DIR" && list_adapter_instances)"
if [[ -n "$ADAPTER_INSTANCES" ]]; then
  while IFS= read -r name; do
    [[ -n "$name" ]] && JIBOT_UNITS+=("amr-adaptor@${name}.service")
  done <<<"$ADAPTER_INSTANCES"
  echo "==> config adapter instances: $(echo "$ADAPTER_INSTANCES" | tr '\n' ' ')"
else
  ROBOT_IDS="$(list_robot_ids)"
  if [[ -n "$ROBOT_IDS" ]]; then
    ROBOT_COUNT="$(printf '%s\n' "$ROBOT_IDS" | sed '/^$/d' | wc -l)"
    if [[ "$ROBOT_COUNT" -eq 1 ]]; then
      JIBOT_UNITS+=("amr-adaptor.service")
      echo "==> one robot detected: $(printf '%s\n' "$ROBOT_IDS" | sed -n '1p'); using amr-adaptor.service"
    else
      while IFS= read -r rid; do
        [[ -n "$rid" ]] && JIBOT_UNITS+=("amr-adaptor@${rid}.service")
      done <<<"$ROBOT_IDS"
      echo "==> multi-robot fleet detected: $(echo "$ROBOT_IDS" | tr '\n' ' ')"
    fi
  else
    JIBOT_UNITS+=("amr-adaptor.service")
    echo "==> no config/robots.toml; using single amr-adaptor.service"
  fi
fi
```
(기존 `if [[ $DO_JIBOT -eq 1 ]]; then ... fi`의 JIBOT_UNITS 구성 블록과 그 뒤 hexplorer enable 블록을 위 코드로 대체. `for unit in "${JIBOT_UNITS[@]}"; do ... enable ... done` 루프는 그대로 둔다 — 단, 빈 배열 가드를 위해 `for unit in ${JIBOT_UNITS[@]+"${JIBOT_UNITS[@]}"}; do`로 바꾼다.)

6. **플래그(안전 가드)**: `--all`(47행) 제거. 벤더는 config.toml `[adapter].vendor`가 결정하므로, `--jibot`/`--hexplorer`는 그것과 **일치하는지 검증**만 하고 불일치면 즉시 실패시킨다(조용한 무효화 금지 — F4). 인자 파싱·`DEPLOY_USER` 확정 직후에 추가:

```bash
# config.toml [adapter] 섹션의 vendor 값(따옴표/주석 제거). 없으면 jibot.
# TOML 라이브러리에 의존하지 않게 awk로 읽는다(로봇 Python 3.10/venv 상태 무관).
CONFIG_VENDOR="$(awk '
  /^\[adapter\]/ {insec=1; next}
  /^\[/ {insec=0}
  insec && /^[[:space:]]*vendor[[:space:]]*=/ {
    sub(/.*=[[:space:]]*/,""); gsub(/["'"'"' ]/,""); sub(/#.*/,""); print; exit
  }
' "$ADAPTER_DIR/config/config.toml" 2>/dev/null)"
CONFIG_VENDOR="${CONFIG_VENDOR:-jibot}"

EXPECTED_VENDOR=""
[[ $DO_JIBOT -eq 1 ]] && EXPECTED_VENDOR="jibot"
[[ $DO_HEXPLORER -eq 1 ]] && EXPECTED_VENDOR="hexplorer"
if [[ -n "$EXPECTED_VENDOR" && "$CONFIG_VENDOR" != "$EXPECTED_VENDOR" ]]; then
  echo "ERROR: --$EXPECTED_VENDOR given, but config/config.toml [adapter].vendor is '$CONFIG_VENDOR'." >&2
  echo "       Set [adapter].vendor = \"$EXPECTED_VENDOR\" in config.toml and re-run." >&2
  exit 1
fi
```
> 주의: `DO_JIBOT`/`DO_HEXPLORER`는 인자 파싱에서 둘 다 0이면 `DO_JIBOT=1`로 보정되는 기존 기본값(58–60행)을 **제거**한다. 그러지 않으면 플래그 없이 실행했는데 config가 hexplorer인 호스트에서 잘못된 검증 실패가 난다. 즉 위 검증은 사용자가 플래그를 **명시했을 때만** 동작한다(`EXPECTED_VENDOR`가 비면 skip).

help 텍스트(15–21행)도 갱신: `--all` 제거, "`--jibot`/`--hexplorer`는 config.toml `[adapter].vendor`와 일치하는지 검증만 한다(벤더는 config가 결정)"로 설명. polkit/카메라 가드에서 `DO_JIBOT`/`DO_HEXPLORER`는 더 이상 어댑터 분기에 쓰지 않는다(`DO_CAMERA`만 유지).

7. **마이그레이션 헬퍼 추가**: 구 유닛을 stop/disable/rm 하는 함수를 추가하고, daemon-reload 직전에 호출:

```bash
# 구 이름으로 설치/enable된 유닛(정적 jibot-adapter.service / hexplorer-adapter.service
# / web_video_server.service, 템플릿 jibot-adapter@.service, 그리고 enable된
# jibot-adapter@<id>.service 인스턴스)을 새 amr-* 유닛과 공존하지 않게 정리한다.
migrate_legacy_units() {
  local legacy_static=(jibot-adapter.service jibot-adapter@.service hexplorer-adapter.service web_video_server.service)
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [dry-run] migrate: stop/disable enabled jibot-adapter@<id> instances"
    echo "  [dry-run] migrate: remove ${legacy_static[*]} (if present)"
    return 0
  fi
  # enable/active된 템플릿 인스턴스를 열거한다. systemctl list-* 는 glob 패턴 인자를
  # 받으므로(quoted여도 systemctl이 매칭) 실제 인스턴스 이름을 얻을 수 있다.
  local instances u
  instances="$(
    { "$SYSTEMCTL_BIN" list-unit-files 'jibot-adapter@*.service' --no-legend 2>/dev/null
      "$SYSTEMCTL_BIN" list-units --all 'jibot-adapter@*.service' --no-legend 2>/dev/null
    } | awk '{print $1}' | grep -E '^jibot-adapter@.+\.service$' | sort -u || true
  )"
  for u in $instances "${legacy_static[@]}"; do
    [[ -n "$u" ]] || continue
    run_root "$SYSTEMCTL_BIN" stop "$u" 2>/dev/null || true
    run_root "$SYSTEMCTL_BIN" disable "$u" 2>/dev/null || true
  done
  # 유닛 파일 제거: 정적 이름(템플릿 파일 포함) + 직접 설치된 인스턴스 파일(드묾).
  run_root rm -f \
    /etc/systemd/system/jibot-adapter.service \
    /etc/systemd/system/jibot-adapter@.service \
    /etc/systemd/system/hexplorer-adapter.service \
    /etc/systemd/system/web_video_server.service
  run_root bash -c 'rm -f /etc/systemd/system/jibot-adapter@*.service' 2>/dev/null || true
}
```
호출은 `echo "==> systemctl daemon-reload"` (469행) 바로 앞에 `echo "==> migrating legacy unit names"; migrate_legacy_units` 추가. (`disable`은 .wants 심링크를, 정적 `rm`은 유닛/템플릿 파일을 지운다.)

8. **START_HINT** (521–522행): `sudo systemctl start jibot-adapter.service` → `sudo systemctl start amr-adaptor.service`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_update_jibot_adapter_over_ssh.py -v`
Expected: PASS. 추가로 dry-run 동작 확인:

Run: `bash scripts/setup-adaptor-service.sh --dry-run`
Expected: 출력에 `amr-adaptor.service`, `amr-adaptor@.service`, `amr-camera.service`, `migrate: ... (if present)`가 보이고, `jibot-adapter`/`hexplorer-adapter`는 마이그레이션 문맥에서만 등장.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup-adaptor-service.sh tests/test_update_jibot_adapter_over_ssh.py
git commit -m "feat(setup): install amr-adaptor/amr-camera units, migrate legacy names"
```

---

### Task 8: 보조 스크립트/CLI 텍스트 리네임

**Files:**
- Modify: `scripts/setup-web-video-server-on-onboard.sh` (생성 유닛 → `amr-camera.service`)
- Modify: `adaptor/install-systemd-service.sh` (기본 `SERVICE_NAME` → `amr-adaptor`)
- Modify: `adaptor/main.py` (help/주석의 `jibot-adapter*` 표현)
- Modify: `scripts/systemd/tmpfiles.d/amr-adaptor.conf` (주석의 `jibot-adapter@<id>` → `amr-adaptor@<id>`)
- Modify: `adaptor/config/robots.toml` (주석의 `jibot-adapter@<id>.service` → `amr-adaptor@<id>.service`)

**Interfaces:** 없음(문자열/기본값/주석 변경).

- [ ] **Step 1: setup-web-video-server-on-onboard.sh**

124, 151, 153행의 `web_video_server.service` → `amr-camera.service`. (124행 `tee /etc/systemd/system/...`, 151행 `enable --now`, 153행 `status`)

- [ ] **Step 2: install-systemd-service.sh**

7행 `SERVICE_NAME="jibot-adapter"` → `SERVICE_NAME="amr-adaptor"`. 24행 help의 `(default: jibot-adapter)` → `(default: amr-adaptor)`.

- [ ] **Step 3: main.py + 주석(tmpfiles.d, robots.toml)**

`adaptor/main.py`: 117행 `"(jibot-adapter@<id>.service) uses."` → `"(amr-adaptor@<id>.service) uses."`. 258행 한글 주석의 `기본 jibot-adapter.service` → `기본 amr-adaptor.service`.
`scripts/systemd/tmpfiles.d/amr-adaptor.conf`: 6행 주석 `jibot-adapter@<id> instances ...` → `amr-adaptor@<id> instances ...`.
`adaptor/config/robots.toml`: 3행 주석 `systemd 유닛 jibot-adapter@<id>.service` → `systemd 유닛 amr-adaptor@<id>.service`.

- [ ] **Step 4: Verify no stale adapter unit names remain in these files**

Run:
```bash
grep -n "jibot-adapter\|hexplorer-adapter\|web_video_server.service" \
  scripts/setup-web-video-server-on-onboard.sh adaptor/install-systemd-service.sh adaptor/main.py \
  scripts/systemd/tmpfiles.d/amr-adaptor.conf adaptor/config/robots.toml
```
Expected: 어댑터 유닛 관련 구 이름이 더 이상 없음(빈 출력 또는 의도된 잔존만).

- [ ] **Step 5: Run the install-systemd smoke test (regression)**

Run: `bash scripts/test-install-systemd-service.sh`
Expected: PASS(기존과 동일; `--name test-jibot`을 쓰므로 영향 없음).

- [ ] **Step 6: Commit**

```bash
git add scripts/setup-web-video-server-on-onboard.sh adaptor/install-systemd-service.sh adaptor/main.py \
        scripts/systemd/tmpfiles.d/amr-adaptor.conf adaptor/config/robots.toml
git commit -m "chore: rename adapter/camera unit references in scripts and CLI help"
```

---

### Task 9: 문서 갱신

**Files:**
- Modify: `README.md`, `adaptor/readme.md`, `docs/guide/web-ui.md`
- Modify: `docs/guide/adaptor-tui.md`, `docs/guide/simulator.md`, `docs/README.md`, `docs/manual/README.md`, `docs/manual/jibot-adapter-ssh-update.md`, `docs/reference/jibot-onboard-access.md`, `docs/reference/jibot-jmanager-reconnect-troubleshooting.md`

**Interfaces:** 없음(문서).

**범위 주의:** 문서 **본문의 systemctl 유닛 이름**(`systemctl ... jibot-adapter.service`, `hexplorer-adapter.service`, `web_video_server.service`)만 새 이름으로 바꾼다. **문서 파일명**(`jibot-adapter-ssh-update.md` 등)과 **배포 스크립트 파일명**(`scripts/update-jibot-adapter-over-ssh.sh` 등), 그리고 config **키 이름**(`web_video_server_public_url`)은 이번 작업 범위가 아니므로 바꾸지 않는다. `jibot-client/` 서브프로젝트 문서도 범위 밖.

- [ ] **Step 1: README.md**

systemctl 예시/표를 새 이름으로:
- `jibot-adapter.service` → `amr-adaptor.service`
- `jibot-adapter@HN-SH6-TR-001.service` → `amr-adaptor@HN-SH6-TR-001.service`
- `web_video_server.service` → `amr-camera.service`
- 151–152행 표의 유닛 템플릿 경로(`scripts/systemd/{jibot,hexplorer}-adapter.service`, `*-adapter.service`)를 `scripts/systemd/amr-adaptor.service` / `amr-adaptor@.service`로.
- `--restart-cmd "sudo systemctl restart jibot-adapter.service"` → `amr-adaptor.service`.
- `amr-webui`는 그대로.

- [ ] **Step 2: adaptor/readme.md**

175–234행의 `jibot-adapter.service` / `jibot-adapter@<id>.service` / `hexplorer-adapter.service` 언급을 새 모델로:
- 기본 `amr-adaptor.service`, 멀티 `amr-adaptor@<name>.service`.
- 벤더는 `config.toml [adapter].vendor`로 정해지고, hexplorer는 별도 유닛이 아니라 vendor 설정/인스턴스로 뜬다는 점 반영.
- 222–229행 파일 표의 유닛 템플릿/생성 경로를 `amr-adaptor*`로.

- [ ] **Step 3: docs/guide/web-ui.md**

`jibot-adapter`/`hexplorer-adapter`/`web_video_server.service` 표기를 `amr-adaptor*` / `amr-camera.service`로. (먼저 `grep -n "jibot-adapter\|hexplorer-adapter\|web_video_server" docs/guide/web-ui.md`로 위치 확인)

- [ ] **Step 4: 기타 운영자 문서**

다음 문서들의 systemctl 유닛 이름을 새 이름으로 바꾼다(본문만; 파일명/스크립트명/config 키는 유지 — 위 범위 주의 참고). 각 파일에서 먼저 위치를 확인한다:

```bash
grep -n "jibot-adapter\|hexplorer-adapter\|web_video_server.service" \
  docs/guide/adaptor-tui.md docs/guide/simulator.md docs/README.md \
  docs/manual/README.md docs/manual/jibot-adapter-ssh-update.md \
  docs/reference/jibot-onboard-access.md docs/reference/jibot-jmanager-reconnect-troubleshooting.md
```
치환 규칙: `jibot-adapter.service` → `amr-adaptor.service`, `jibot-adapter@<id>.service` → `amr-adaptor@<id>.service`, `hexplorer-adapter.service` → (hexplorer는 별도 유닛이 아니라 vendor/인스턴스로 뜬다는 설명으로) `amr-adaptor.service`/`amr-adaptor@<name>.service`, `web_video_server.service` → `amr-camera.service`. SSH-update 문서(`jibot-adapter-ssh-update.md`)의 `--restart-cmd` 예시도 `amr-adaptor.service`로.

- [ ] **Step 5: Verify**

Run:
```bash
grep -rn "jibot-adapter\.service\|hexplorer-adapter\.service\|web_video_server\.service" \
  README.md adaptor/readme.md docs/
```
Expected: 어댑터/카메라 구 유닛명 잔존 없음(있다면 의도된 마이그레이션 설명 문맥, 또는 파일명/`web_video_server_public_url` 키처럼 범위 밖인 경우만).

- [ ] **Step 6: Commit**

```bash
git add README.md adaptor/readme.md docs/
git commit -m "docs: amr-adaptor / amr-camera service names"
```

---

## 최종 검증 (전체 회귀)

- [ ] 어댑터 테스트:

```bash
cd adaptor && python -m unittest \
  tests.test_config_adapter tests.test_adapter_dispatch \
  tests.test_fleet_registry tests.test_registry tests.test_main_hexplorer_args \
  tests.test_web_server tests.test_web_render -v
```
Expected: 전부 PASS.

- [ ] 리포 루트 테스트:

```bash
python -m pytest tests/test_systemd_units.py tests/test_update_jibot_adapter_over_ssh.py -v
```
Expected: 전부 PASS.

- [ ] 셸 스모크:

```bash
bash scripts/test-run-adapter-dispatch.sh
bash scripts/test-install-systemd-service.sh
bash scripts/setup-adaptor-service.sh --dry-run | grep -E "amr-adaptor|amr-camera|migrate"
```
Expected: PASS / 새 이름 출력.

- [ ] 구 **유닛명** 잔존 스캔(코드/스크립트/문서/config, 마이그레이션·설명 문맥 제외):

```bash
grep -rIn "jibot-adapter\.service\|hexplorer-adapter\.service\|web_video_server\.service" \
  adaptor scripts docs README.md \
  | grep -viE "migrate|legacy|test-jibot|docs/superpowers/"
```
Expected: 빈 출력(또는 의도된 잔존만 — 예: setup의 마이그레이션 대상 목록).

- [ ] 범위 밖이라 **남아 있어야 정상**인 것 확인(거짓양성 구분용): 배포 스크립트 파일명(`scripts/update-jibot-adapter-*.sh`), 문서 파일명(`docs/**/jibot-adapter-*.md`), config 키 `web_video_server_public_url`, `jibot-client/` 서브프로젝트. 이들은 이 작업에서 바꾸지 않는다.
