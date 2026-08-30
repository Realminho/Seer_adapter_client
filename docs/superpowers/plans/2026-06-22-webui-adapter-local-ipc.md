# WebUi ↔ adapter 로컬 IPC (file + UDS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WebUi가 MQTT 브로커 가용성과 무관하게 robot 상태를 보고(파일 IPC) 제어(UDS)하도록, 내부 통신을 `/run/amr-adaptor/<serial>/`로 옮기고 MQTT는 ACS 전용으로 남긴다.

**Architecture:** adapter는 매 발행 주기 VDA5050 state를 `state.json`(tmpfs)에 atomic write하고 브로커 연결 상태를 `health.json`에 적는다. WebUi는 `FileMonitor`로 이 파일들을 on-demand로 읽어 렌더한다. 명령은 adapter가 여는 Unix domain socket(`control.sock`)으로 보내고 **delivered(접수) 수준** ack를 받는다(action별 결과는 state의 `instantActionStates`로 관찰). 둘 다 같은 robot 온보드 PC에 공존하는 두 프로세스 간 로컬 IPC다.

**Tech Stack:** Python 3, asyncio, paho-mqtt(ACS 전용으로 잔존), stdlib `socket`(AF_UNIX), systemd `tmpfiles.d`.

**설계 출처:** `docs/superpowers/specs/2026-06-22-webui-adapter-local-ipc-design.md`

## Global Constraints

- **스코프 = JIBOT adapter.** 파일/UDS는 jibot spec에만 적용. 비-jibot spec(hexplorer/edge-agent)은 현행 `MqttMonitor` 유지(범위 밖).
- **state.json 내용은 `V3State.to_dict()` 출력 그대로 + `updated_at` 한 필드.** (`V3State` = `protocol.vda5050_3_0.messages.State`, `adapter_jibot.py:38`에서 `State as V3State`로 import; `_build_v3_state_message()`가 반환.) 키 변형/누락 금지(1순위 키 `activeEmergencyStop`/`mobileRobotPosition`/`powerSupply`).
- **로컬 파일 write는 항상 MQTT publish보다 먼저**, MQTT publish는 예외 격리. 브로커 장애가 로컬 경로를 막지 않는다.
- **명령 응답 = delivered 수준.** `instant_actions_accept_procedure`는 반환값이 없으므로(adapter_jibot.py:2771-2879) 동기 per-action verdict 금지. 결과는 `instantActionStates`로.
- **fail-closed.** adapter가 죽어 있으면 UDS connect 실패 → "not delivered". 큐잉/지연 발사 없음.
- **`/run/amr-adaptor` root는 tmpfiles.d로**, adapter는 자기 `<serial>/` 하위만 생성·소유(systemd `RuntimeDirectory` 미사용 — 다중 인스턴스 clobber 회피).
- **권한:** adapter/WebUi 동일 사용자(현행) 가정, dir 0750.
- 기존 테스트 패턴 준수: `adaptor/core/*`·`adaptor/utils/*` 테스트는 `unittest`, `adaptor/web/*` 테스트는 `pytest`. 테스트 실행은 `adaptor/` 디렉터리에서.

---

## File Structure

**신규**
- `adaptor/core/ipc_paths.py` — `/run/amr-adaptor/<serial>/` 경로 헬퍼 + atomic JSON write. adapter·WebUi 공용.
- `adaptor/web/senders.py` — 명령 송신 seam(`MqttSender`, Phase 2의 `UdsSender`). `_post_action`이 transport에 무관해지게 한다.
- `adaptor/tests/test_ipc_paths.py`, `adaptor/tests/test_file_monitor.py`, `adaptor/tests/test_senders.py` — 신규 테스트.
- `scripts/systemd/tmpfiles.d/amr-adaptor.conf` — `/run/amr-adaptor` root 선언.

**수정**
- `adaptor/adapter_jibot.py` — `_write_state_file`, `_write_health_file`, `_on_acs_broker_change`, publish 루프 reorder/격리, `run_adapter`에 UDS 서버 + 초기 health write.
- `adaptor/utils/mqtt_client.py` — `on_connection_change` 파라미터 + `on_disconnect` 핸들러.
- `adaptor/core/monitor.py` — `FileMonitor` 추가, `extract_state` `instantActionStates` 파싱, `StateSnapshot.instant_action_states`.
- `adaptor/web/server.py` — `senders` 주입, `_post_action`을 sender로, `_monitor_snapshot`에 `acs_broker_connected`.
- `adaptor/web/main.py` — jibot read=FileMonitor, 명령 sender 와이어링(Phase 1 MqttSender → Phase 2 UdsSender).
- `adaptor/web/render.py` — ACS 연결/ adapter offline·stale / 최근 instant action 결과 표시.
- `scripts/setup-adaptor-service.sh`, `scripts/update-jibot-adapter-over-ssh.sh` — tmpfiles.d 설치.
- `adaptor/tests/test_monitor.py`, `adaptor/tests/test_web_server.py`, `adaptor/tests/test_web_render.py`, `tests/test_update_jibot_adapter_over_ssh.py` — 회귀.
- `docs/guide/web-ui.md` — 통신 모델 갱신.

---

# Phase 1 — 상태 읽기 경로 (브로커 없이 상태가 보인다)

## Task 1: ipc_paths 모듈 (경로 + atomic write)

**Files:**
- Create: `adaptor/core/ipc_paths.py`
- Test: `adaptor/tests/test_ipc_paths.py`

**Interfaces:**
- Produces:
  - `RUNTIME_ROOT: Path` (= `/run/amr-adaptor`)
  - `runtime_dir(serial: str) -> Path`
  - `state_path(serial: str) -> Path`
  - `health_path(serial: str) -> Path`
  - `control_sock_path(serial: str) -> Path`
  - `ensure_runtime_dir(serial: str) -> Path` (mkdir parents, 0750)
  - `atomic_write_json(path: Path, obj: dict) -> None` (tmp + `os.replace`)

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_ipc_paths.py
import json
import unittest
from pathlib import Path

from core import ipc_paths


class IpcPathsTest(unittest.TestCase):
    def test_paths_are_under_runtime_root_keyed_by_serial(self):
        self.assertEqual(ipc_paths.runtime_dir("S1"), ipc_paths.RUNTIME_ROOT / "S1")
        self.assertEqual(ipc_paths.state_path("S1").name, "state.json")
        self.assertEqual(ipc_paths.health_path("S1").name, "health.json")
        self.assertEqual(ipc_paths.control_sock_path("S1").name, "control.sock")
        self.assertTrue(str(ipc_paths.state_path("S1")).endswith("amr-adaptor/S1/state.json"))

    def test_atomic_write_json_replaces_via_tmp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.json"
            ipc_paths.atomic_write_json(target, {"a": 1, "updated_at": 2.0})
            self.assertEqual(json.loads(target.read_text()), {"a": 1, "updated_at": 2.0})
            # no leftover tmp file
            self.assertEqual([p.name for p in Path(d).iterdir()], ["state.json"])

    def test_runtime_dir_rejects_traversal_and_sanitizes(self):
        with self.assertRaises(ValueError):
            ipc_paths.runtime_dir("..")
        with self.assertRaises(ValueError):
            ipc_paths.runtime_dir("")
        # path separators collapse to a single safe component (no escape)
        self.assertEqual(ipc_paths.runtime_dir("a/b").name, "a_b")
        self.assertEqual(ipc_paths.runtime_dir("S1"), ipc_paths.RUNTIME_ROOT / "S1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_ipc_paths -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.ipc_paths'`

- [ ] **Step 3: Write the implementation**

```python
# adaptor/core/ipc_paths.py
"""Filesystem IPC paths shared by the adapter and the WebUi.

The adapter writes state/health under /run/amr-adaptor/<serial>/ (tmpfs) and
listens on a control socket there; the WebUi reads those files and connects to
the socket. /run/amr-adaptor itself is created by tmpfiles.d; each adapter
creates and owns only its own <serial>/ subdirectory.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

RUNTIME_ROOT = Path("/run/amr-adaptor")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_serial(serial: str) -> str:
    """Sanitize ``serial`` into a single safe path component.

    serial comes from config (and feeds the MQTT topic prefix), but defend the
    runtime path regardless: chars outside ``[A-Za-z0-9._-]`` (incl. ``/``)
    collapse to ``_``; ``""``/``.``/``..`` are rejected so the path cannot
    escape RUNTIME_ROOT.
    """
    s = _UNSAFE.sub("_", str(serial))
    if s in ("", ".", ".."):
        raise ValueError(f"unsafe serial for runtime path: {serial!r}")
    return s


def runtime_dir(serial: str) -> Path:
    return RUNTIME_ROOT / safe_serial(serial)


def state_path(serial: str) -> Path:
    return runtime_dir(serial) / "state.json"


def health_path(serial: str) -> Path:
    return runtime_dir(serial) / "health.json"


def control_sock_path(serial: str) -> Path:
    return runtime_dir(serial) / "control.sock"


def ensure_runtime_dir(serial: str) -> Path:
    d = runtime_dir(serial)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o750)
    return d


def atomic_write_json(path: Path, obj: dict) -> None:
    """Write JSON to ``path`` atomically (tmp file + rename on same dir)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_ipc_paths -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/ipc_paths.py adaptor/tests/test_ipc_paths.py
git commit -m "feat(ipc): add /run/amr-adaptor path helpers + atomic json write"
```

---

## Task 2: adapter가 state.json을 쓴다 (write-first + publish 격리)

**Files:**
- Modify: `adaptor/adapter_jibot.py` (publish 루프 `:501`, 신규 메서드 `_write_state_file`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` (신규 테스트 추가)

**Interfaces:**
- Consumes: `core.ipc_paths` (Task 1), `self._build_v3_state_message() -> V3State`, `self.config.vehicle.serial_number`.
- Produces: `Adapter._write_state_file(state_msg) -> None` — `state_msg.to_dict()` + `updated_at`를 `state_path(serial)`에 atomic write; 자체 예외는 삼킨다.

- [ ] **Step 1: Write the failing test**

기존 테스트의 adapter 생성 패턴(`test_adapter_jibot_v3_order.py`의 fake-hardware 셋업)을 재사용한다. 새 테스트 함수를 추가:

```python
# adaptor/tests/test_adapter_jibot_v3_order.py 에 메서드 추가 (AdapterV3OrderTest 내부)
    def test_write_state_file_dumps_v3_state_with_updated_at(self):
        import json
        import tempfile
        from pathlib import Path
        from core import ipc_paths

        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.json"
            # state_path를 임시 경로로, ensure_runtime_dir는 무력화
            with mock.patch.object(ipc_paths, "state_path", return_value=target), \
                 mock.patch.object(ipc_paths, "ensure_runtime_dir", return_value=Path(d)):
                self.adapter._write_state_file(self.adapter._build_v3_state_message())
            data = json.loads(target.read_text())
        self.assertIn("safetyState", data)
        self.assertIn("activeEmergencyStop", data["safetyState"])
        self.assertIsInstance(data["updated_at"], float)
```

> 참고: 이 테스트 파일은 이미 `mock`을 사용한다(없으면 `from unittest import mock` 추가). `self.adapter`는 기존 setUp에서 만든 fake-hardware adapter다. 테스트 메서드명을 기존 명명 규칙(`test_...`)에 맞춘다.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_write_state_file_dumps_v3_state_with_updated_at -v`
Expected: FAIL — `AttributeError: ... has no attribute '_write_state_file'`

- [ ] **Step 3: Implement `_write_state_file` + 루프 reorder**

`adapter_jibot.py` 상단 import에 `from core import ipc_paths` 추가(`import time`가 없으면 함께 추가).

신규 메서드(예: `publish_state` 근처):

```python
    def _write_state_file(self, state_msg) -> None:
        """Write the same VDA5050 state dict the adapter publishes, plus a
        wall-clock ``updated_at``, to /run/amr-adaptor/<serial>/state.json
        (tmpfs). Errors are swallowed so a file fault never blocks the loop."""
        try:
            serial = self.config.vehicle.serial_number
            ipc_paths.ensure_runtime_dir(serial)
            data = state_msg.to_dict()
            data["updated_at"] = time.time()
            ipc_paths.atomic_write_json(ipc_paths.state_path(serial), data)
        except Exception as exc:  # noqa: BLE001
            print(f"[STATE FILE WRITE FAILED] {exc}")
```

루프 `:501`을 write-first + publish 격리로 교체:

```python
            msg = self._build_v3_state_message()
            self._write_state_file(msg)          # local WebUi first — broker 무관
            try:
                self._vda3.publish_state(msg, qos=0)   # ACS
            except Exception as exc:  # noqa: BLE001
                print(f"[ACS STATE PUBLISH FAILED] {exc}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_write_state_file_dumps_v3_state_with_updated_at -v`
Expected: PASS

- [ ] **Step 5: Run the full adapter test file (회귀)**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order -v`
Expected: PASS (기존 + 신규)

- [ ] **Step 6: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(adapter): write state.json before MQTT publish, isolate publish"
```

---

## Task 3: MQTTClient 연결 훅 + adapter health.json

**Files:**
- Modify: `adaptor/utils/mqtt_client.py` (`__init__`, `_on_connect`, 신규 `_on_disconnect`)
- Modify: `adaptor/adapter_jibot.py` (`__init__` 와이어링, `_on_acs_broker_change`, `_write_health_file`, `run_adapter` 초기 write)
- Test: `adaptor/tests/test_mqtt.py` (신규 테스트 추가)

**Interfaces:**
- Produces:
  - `MQTTClient(client_id=None, config=None, on_connection_change: Optional[Callable[[bool], None]] = None)`; `_on_connect`(rc==0→True else False)·`_on_disconnect`(False)가 `on_connection_change(connected)` 호출.
  - `Adapter._on_acs_broker_change(connected: bool) -> None`, `Adapter._write_health_file() -> None`.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_mqtt.py 에 추가
import unittest
from unittest import mock

from utils.mqtt_client import MQTTClient
from config.config import get_config


class MqttConnectionHookTest(unittest.TestCase):
    def _client(self, cb):
        # paho Client 생성을 막고 콜백 등록만 검증
        with mock.patch("utils.mqtt_client.mqtt.Client") as fake_client:
            c = MQTTClient(config=get_config(), on_connection_change=cb)
        return c

    def test_on_connect_success_fires_true(self):
        seen = []
        c = self._client(seen.append)
        c._on_connect(None, None, None, 0)
        self.assertEqual(seen, [True])

    def test_on_connect_failure_fires_false(self):
        seen = []
        c = self._client(seen.append)
        c._on_connect(None, None, None, 5)
        self.assertEqual(seen, [False])

    def test_on_disconnect_fires_false(self):
        seen = []
        c = self._client(seen.append)
        c._on_disconnect(None, None, 0)
        self.assertEqual(seen, [False])
```

> `get_config()`가 테스트 환경에서 동작하는지 확인. 기존 `test_mqtt.py`가 어떻게 config를 얻는지 보고 동일 패턴을 쓴다(필요 시 `config=mock.Mock()`로 대체하고 `.mqtt_broker.host/port`, `.vehicle.*`만 채운다).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_mqtt.MqttConnectionHookTest -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'on_connection_change'`

- [ ] **Step 3: Implement MQTTClient hook**

`utils/mqtt_client.py`:

```python
    def __init__(
        self,
        client_id: Optional[str] = None,
        config: Optional[Config] = None,
        on_connection_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        ...
        self._on_connection_change = on_connection_change
        self._client = mqtt.Client(client_id=client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        ...

    def _on_connect(self, client, userdata, flags, rc) -> None:  # type: ignore[override]
        self._connected = rc == 0
        if not self._connected:
            print(f"MQTT connection failed with code {rc}")
        if self._on_connection_change is not None:
            self._on_connection_change(self._connected)

    def _on_disconnect(self, client, userdata, rc) -> None:  # type: ignore[override]
        self._connected = False
        if self._on_connection_change is not None:
            self._on_connection_change(False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_mqtt.MqttConnectionHookTest -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire adapter health.json**

`adapter_jibot.py` `__init__`에서 MQTT 생성부(`:102`)와 상태 필드 추가:

```python
        self._acs_broker_connected = False
        self._adapter_started_at = time.time()
        self._mqtt = MQTTClient(
            config=self.config,
            on_connection_change=self._on_acs_broker_change,
        )
```

신규 메서드:

```python
    def _on_acs_broker_change(self, connected: bool) -> None:
        self._acs_broker_connected = connected
        self._write_health_file()

    def _write_health_file(self) -> None:
        try:
            serial = self.config.vehicle.serial_number
            ipc_paths.ensure_runtime_dir(serial)
            now = time.time()
            ipc_paths.atomic_write_json(
                ipc_paths.health_path(serial),
                {
                    "pid": os.getpid(),
                    "started_at": self._adapter_started_at,
                    "acs_broker_connected": self._acs_broker_connected,
                    "acs_broker_last_change": now,
                    "updated_at": now,
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[HEALTH FILE WRITE FAILED] {exc}")
```

`run_adapter`(`:271`) 시작부에 초기(미연결) health write 1회:

```python
        self._write_health_file()   # 초기 acs_broker_connected=false
```

(`os`가 import 안 돼 있으면 `import os` 추가.)

- [ ] **Step 6: Run adapter test file (회귀)**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add adaptor/utils/mqtt_client.py adaptor/adapter_jibot.py adaptor/tests/test_mqtt.py
git commit -m "feat(adapter): publish ACS broker connection state to health.json"
```

---

## Task 4: extract_state가 instantActionStates를 파싱한다

**Files:**
- Modify: `adaptor/core/monitor.py` (`StateSnapshot`, `extract_state`, `MqttMonitor.get_snapshot` 복사)
- Test: `adaptor/tests/test_monitor.py` (신규 테스트 추가)

**Interfaces:**
- Produces: `StateSnapshot.instant_action_states: List[Dict[str, Any]]`; `extract_state` 출력에 `instant_action_states` 포함.

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_monitor.py 의 ExtractStateTest 에 추가
    def test_instant_action_states_parsed(self):
        out = monitor.extract_state(
            {"instantActionStates": [{"actionId": "a1", "actionStatus": "FAILED",
                                      "actionType": "clamp",
                                      "resultDescription": "busy"}]}
        )
        self.assertEqual(len(out["instant_action_states"]), 1)
        self.assertEqual(out["instant_action_states"][0]["actionStatus"], "FAILED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_monitor.ExtractStateTest.test_instant_action_states_parsed -v`
Expected: FAIL — `KeyError: 'instant_action_states'`

- [ ] **Step 3: Implement**

`monitor.py` `StateSnapshot`에 필드 추가(`action_states` 인접, `:53`):

```python
    instant_action_states: List[Dict[str, Any]] = field(default_factory=list)
```

`extract_state`의 list 매핑 루프(`:125-132`)에 한 쌍 추가:

```python
    for src, dst in (
        ("nodeStates", "node_states"),
        ("edgeStates", "edge_states"),
        ("actionStates", "action_states"),
        ("instantActionStates", "instant_action_states"),
    ):
```

`MqttMonitor.get_snapshot`(`:198-206`)의 복사 목록에 추가:

```python
            snap.instant_action_states = list(self._snapshot.instant_action_states)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_monitor -v`
Expected: PASS (기존 + 신규)

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/monitor.py adaptor/tests/test_monitor.py
git commit -m "feat(monitor): parse instantActionStates into StateSnapshot"
```

---

## Task 5: FileMonitor (state.json + health.json 읽기)

**Files:**
- Modify: `adaptor/core/monitor.py` (신규 `FileMonitor`)
- Test: `adaptor/tests/test_file_monitor.py`

**Interfaces:**
- Consumes: `core.ipc_paths` (Task 1), `extract_state`/`StateSnapshot` (Task 4).
- Produces: `FileMonitor(serial: str, stale_after: float = 15.0)` with:
  - `get_snapshot() -> StateSnapshot` (state.json → extract_state, `state_ts = updated_at`)
  - `broker_connected: bool` (마지막 get_snapshot이 fresh였는지 = adapter alive)
  - `last_error: Optional[str]`
  - `acs_broker_connected: bool` (health.json에서)

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_file_monitor.py
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from core import ipc_paths, monitor


class FileMonitorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "state.json"
        self.health = self.dir / "health.json"
        patcher_s = mock.patch.object(ipc_paths, "state_path", return_value=self.state)
        patcher_h = mock.patch.object(ipc_paths, "health_path", return_value=self.health)
        patcher_s.start(); patcher_h.start()
        self.addCleanup(patcher_s.stop); self.addCleanup(patcher_h.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write_state(self, age_sec, **over):
        payload = {"safetyState": {"activeEmergencyStop": "NONE"},
                   "powerSupply": {"stateOfCharge": 50.0},
                   "updated_at": time.time() - age_sec}
        payload.update(over)
        self.state.write_text(json.dumps(payload))

    def test_fresh_state_is_parsed_and_alive(self):
        self._write_state(age_sec=1.0)
        m = monitor.FileMonitor("S1", stale_after=15.0)
        snap = m.get_snapshot()
        self.assertEqual(snap.battery_soc, 50.0)
        self.assertTrue(m.broker_connected)
        self.assertIsNone(m.last_error)

    def test_stale_state_marks_not_alive(self):
        self._write_state(age_sec=60.0)
        m = monitor.FileMonitor("S1", stale_after=15.0)
        m.get_snapshot()
        self.assertFalse(m.broker_connected)

    def test_missing_file_returns_empty_snapshot(self):
        m = monitor.FileMonitor("S1")
        snap = m.get_snapshot()
        self.assertIsNone(snap.battery_soc)
        self.assertFalse(m.broker_connected)

    def test_corrupt_json_sets_last_error(self):
        self.state.write_text("{not json")
        m = monitor.FileMonitor("S1")
        m.get_snapshot()
        self.assertIsNotNone(m.last_error)

    def test_acs_broker_connected_read_from_health(self):
        self.health.write_text(json.dumps({"acs_broker_connected": True}))
        m = monitor.FileMonitor("S1")
        self.assertTrue(m.acs_broker_connected)
        self.health.write_text(json.dumps({"acs_broker_connected": False}))
        self.assertFalse(m.acs_broker_connected)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_file_monitor -v`
Expected: FAIL — `AttributeError: module 'core.monitor' has no attribute 'FileMonitor'`

- [ ] **Step 3: Implement FileMonitor**

`monitor.py` 하단에 추가(상단에 `from core import ipc_paths` import):

```python
class FileMonitor:
    """Reads adapter state/health files instead of subscribing to MQTT.

    Drop-in for the WebUi read path: exposes ``get_snapshot``/``broker_connected``/
    ``last_error`` like :class:`MqttMonitor`, plus ``acs_broker_connected`` from
    health.json. ``broker_connected`` here means "adapter alive" (state file
    fresh), not a broker session.
    """

    def __init__(self, serial: str, stale_after: float = 15.0) -> None:
        self.serial = serial
        self.stale_after = stale_after
        self._fresh = False
        self._last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return True

    @property
    def adapter_online(self) -> bool:
        """True when state.json is present and fresh (adapter is alive)."""
        return self._fresh

    @property
    def broker_connected(self) -> bool:
        # Back-compat alias: server historically read broker_connected. For
        # FileMonitor this means "adapter alive", NOT a broker session.
        return self._fresh

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def acs_broker_connected(self) -> bool:
        data = self._read_json(ipc_paths.health_path(self.serial))
        return bool(data.get("acs_broker_connected")) if isinstance(data, dict) else False

    def _read_json(self, path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    def get_snapshot(self) -> StateSnapshot:
        snap = StateSnapshot()
        data = self._read_json(ipc_paths.state_path(self.serial))
        if isinstance(data, dict):
            for key, value in extract_state(data).items():
                setattr(snap, key, value)
            ts = data.get("updated_at")
            snap.state_ts = float(ts) if isinstance(ts, (int, float)) else None
            self._last_error = None
        else:
            snap.state_ts = None
        now = time.time()
        self._fresh = (
            snap.state_ts is not None and (now - snap.state_ts) <= self.stale_after
        )
        return snap
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_file_monitor -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/monitor.py adaptor/tests/test_file_monitor.py
git commit -m "feat(monitor): add FileMonitor reading state.json/health.json"
```

---

## Task 6: 명령 송신 seam (MqttSender) + server `_post_action` 전환

**Files:**
- Create: `adaptor/web/senders.py`
- Modify: `adaptor/web/server.py` (`__init__` `senders`, `_post_action`, `_monitor_snapshot`)
- Test: `adaptor/tests/test_senders.py`, `adaptor/tests/test_web_server.py` (회귀)

**Interfaces:**
- Produces:
  - `web.senders.MqttSender(monitor)` with `send(payload: dict, meta: dict) -> tuple[bool, str]` — `monitor.publish_json("instantActions", payload)` 위임.
  - `WebUi(..., senders: dict)` — `_post_action`이 `self._senders[key].send(payload, meta)` 사용; `meta = {confirmed, source_user, created_at}`.
  - `_monitor_snapshot`이 `monitor.acs_broker_connected`가 있으면 `snap.acs_broker_connected`에 얹음.

- [ ] **Step 1: Write the failing test (senders)**

```python
# adaptor/tests/test_senders.py
import unittest

from web.senders import MqttSender


class FakeMon:
    def __init__(self, ok): self.ok = ok; self.calls = []
    def publish_json(self, topic, obj, qos=0):
        self.calls.append((topic, obj)); return self.ok


class MqttSenderTest(unittest.TestCase):
    def test_send_delivers_on_publish_ok(self):
        mon = FakeMon(True)
        delivered, msg = MqttSender(mon).send({"x": 1}, {"source_user": "op"})
        self.assertTrue(delivered)
        self.assertEqual(mon.calls[0][0], "instantActions")

    def test_send_reports_failure(self):
        delivered, msg = MqttSender(FakeMon(False)).send({"x": 1}, {})
        self.assertFalse(delivered)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_senders -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.senders'`

- [ ] **Step 3: Implement senders.py**

```python
# adaptor/web/senders.py
"""Command-send seam for the Control view.

The WebUi builds a VDA5050 instantActions payload and hands it to a sender.
Phase 1 uses MqttSender (publishes to the broker); Phase 2 swaps in UdsSender
(local Unix socket) without touching server.py.
"""

from __future__ import annotations

from typing import Tuple


class MqttSender:
    def __init__(self, monitor) -> None:
        self._monitor = monitor

    def send(self, payload: dict, meta: dict) -> Tuple[bool, str]:
        ok = self._monitor.publish_json("instantActions", payload)
        return (ok, "delivered" if ok else "publish failed")
```

- [ ] **Step 4: Run senders test (PASS)**

Run: `cd adaptor && python -m unittest tests.test_senders -v`
Expected: PASS

- [ ] **Step 5: Wire server.py**

`server.py` `WebUi.__init__` 시그니처에 `senders=None` 추가하고 저장(없으면 monitor를 MqttSender로 감싸 backward-compat):

```python
    def __init__(self, *, specs, controllers, monitors, credentials,
                 senders=None, host="127.0.0.1", port=0, ...):
        ...
        self._monitors = monitors
        from web.senders import MqttSender
        self._senders = senders or {k: MqttSender(m) for k, m in monitors.items()}
```

`_post_action`(`:551-565`) 교체:

```python
        from core import control  # reuse VDA5050 payload builder
        payload = control.build_instant_actions(
            header_id=self._next_header_id(key),
            timestamp=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            version=spec.vda_full_version,
            manufacturer=spec.manufacturer,
            serial_number=spec.serial,
            action_type=action_type,
            action_id=str(uuid.uuid4()),
        )
        meta = {
            "confirmed": _confirmed(form),
            "source_user": self._auth_user(h.headers.get("Authorization")),
            "created_at": time.time(),
        }
        delivered, text = self._senders[key].send(payload, meta)
        self._audit(h, key, f"action:{action_type}", f"delivered={delivered}")
        flash = "msg" if delivered else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(text)}")
```

`_monitor_snapshot`(`:98-115`)를 **transport-중립 필드 우선 + `mqtt_*` 호환 alias**로
정리하고 ACS 상태를 얹는다(필드명에서 MQTT 의미 제거):

```python
    def _monitor_snapshot(self, key: str):
        monitor = self._monitors.get(key)
        if monitor is None:
            return None
        snap = monitor.get_snapshot()
        if hasattr(monitor, "broker_connected"):
            online = bool(getattr(monitor, "broker_connected"))  # FileMonitor: adapter alive
            age = snap.age(time.time()) if hasattr(snap, "age") else None
            fresh = online and age is not None and age <= _MQTT_STATE_STALE_SEC
            # primary, transport-neutral fields
            setattr(snap, "adapter_online", online)
            setattr(snap, "state_age_sec", age)
            setattr(snap, "state_fresh", fresh)
            setattr(snap, "state_last_error", getattr(monitor, "last_error", None))
            # back-compat aliases for any untouched render/test references
            setattr(snap, "mqtt_connected", online)
            setattr(snap, "mqtt_state_age_sec", age)
            setattr(snap, "mqtt_state_fresh", fresh)
            setattr(snap, "mqtt_last_error", getattr(monitor, "last_error", None))
        if hasattr(monitor, "acs_broker_connected"):
            setattr(snap, "acs_broker_connected", bool(getattr(monitor, "acs_broker_connected")))
        return snap
```

그리고 `_manual_test_block_reason`(`:50-61`)을 중립 필드 + 정확한 문구로 갱신(MQTT
의미 제거; 기존 e-stop/operating_mode 분기는 그대로):

```python
def _manual_test_block_reason(snapshot) -> str:
    if getattr(snapshot, "adapter_online", True) is False:
        return "requires live adapter state"
    if getattr(snapshot, "state_fresh", True) is False:
        return "requires fresh adapter state"
    e_stop = str(getattr(snapshot, "active_emergency_stop", "") or "").upper()
    if e_stop not in ("", "NONE"):
        return ""
    mode = str(getattr(snapshot, "operating_mode", "") or "").upper().replace("-", "_")
    if mode in ("EMERGENCY", "EMERGENCY_STOP", "E_STOP", "ESTOP"):
        return ""
    return "requires emergency stop"
```

(`time`은 server.py에 이미 import됨 `:18`. `_MQTT_STATE_STALE_SEC` 상수 재사용.)

- [ ] **Step 6: Update existing web_server test + run**

`test_web_server.py`의 `_post_action` 관련 테스트가 `FakeMonitor.publish_json`/`published`를 검증한다면, `MqttSender(FakeMonitor)`가 그대로 위임하므로 통과한다. 단, audit 문자열이 `published=`→`delivered=`로 바뀌었으니 해당 assert가 있으면 갱신한다. ACS 항목 회귀로 `FakeMonitor`에 `acs_broker_connected: bool = True` 필드를 추가한다.

Run: `cd adaptor && python -m pytest tests/test_web_server.py -q`
Expected: PASS (필요한 assert 갱신 후)

- [ ] **Step 7: Commit**

```bash
git add adaptor/web/senders.py adaptor/web/server.py adaptor/tests/test_senders.py adaptor/tests/test_web_server.py
git commit -m "feat(web): command-send seam (MqttSender) + ACS status in snapshot"
```

---

## Task 7: render — ACS 연결 / adapter offline·stale / instant action 결과

**Files:**
- Modify: `adaptor/web/render.py`
- Test: `adaptor/tests/test_web_render.py`

**Interfaces:**
- Consumes: snapshot의 `adapter_online`/`state_fresh`/`acs_broker_connected`(Task 6), `instant_action_states`(Task 4). (`mqtt_*`는 호환 alias로만 존재 — 신규 마크업은 중립 필드 사용.)

- [ ] **Step 1: Write the failing test**

```python
# adaptor/tests/test_web_render.py 에 추가 (기존 import/헬퍼 재사용)
def test_detail_shows_acs_disconnected_and_instant_result(make_snap):
    snap = make_snap(acs_broker_connected=False,
                     instant_action_states=[{"actionType": "clamp",
                                             "actionStatus": "FAILED",
                                             "resultDescription": "busy"}])
    html = render.adapter_detail_page(SPEC, METRICS, snap, {}, csrf="x")
    assert "ACS" in html
    assert "disconnected" in html.lower() or "끊김" in html
    assert "clamp" in html and ("FAILED" in html or "실패" in html)
```

> 기존 `test_web_render.py`의 fixture/헬퍼(스냅샷·SPEC·METRICS 생성 방식)를 그대로 사용한다. 위 `make_snap`/`SPEC`/`METRICS`는 그 파일의 실제 이름으로 맞춘다. 없으면 기존 테스트가 snapshot을 만드는 패턴을 복사해 `acs_broker_connected`/`instant_action_states`를 추가한 더미를 만든다.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_web_render.py -k acs_disconnected -q`
Expected: FAIL (ACS/결과 마크업 없음)

- [ ] **Step 3: Implement render 변경**

`_mqtt_live_table`(또는 상세 렌더)에서:
- 기존 "connection" 행을 **두 항목으로 분리**: "adapter" = `getattr(snap, "adapter_online", False)` 및 `getattr(snap, "state_fresh", False)`면 `online`, 아니면 `offline/stale`; "ACS" = `getattr(snap, "acs_broker_connected", False)`면 `connected`, 아니면 `disconnected`.
- 최근 instant action 결과 블록 추가: `getattr(snap, "instant_action_states", [])`를 순회해 `actionType` / `actionStatus` / `resultDescription`을 짧게 표시(없으면 생략).

(구체 마크업은 기존 `_mqtt_live_table`/행 헬퍼 스타일을 그대로 따른다. `adapter_list_page` 요약에도 ACS 토큰을 한 개 덧붙일 수 있으나 상세 우선.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_web_render.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/web/render.py adaptor/tests/test_web_render.py
git commit -m "feat(web): split adapter/ACS status rows + show instant action results"
```

---

## Task 8: tmpfiles.d + 설치/배포 스크립트

**Files:**
- Create: `scripts/systemd/tmpfiles.d/amr-adaptor.conf`
- Modify: `scripts/setup-adaptor-service.sh`, `scripts/update-jibot-adapter-over-ssh.sh`
- Test: `tests/test_update_jibot_adapter_over_ssh.py` (회귀)

- [ ] **Step 1: Write the failing test**

`tests/test_update_jibot_adapter_over_ssh.py`의 기존 렌더 검증 패턴을 보고, 설치 스크립트가 tmpfiles 라인을 렌더하는지(`d /run/amr-adaptor 0750 <user>`) 확인하는 테스트를 추가한다. 기존 polkit 렌더 테스트(`test_setup_script_renders_webui_polkit_units_from_selected_targets`)와 같은 방식으로 스크립트 출력/파일 내용을 grep.

```python
def test_setup_script_installs_amr_adaptor_tmpfiles(...):
    # setup-adaptor-service.sh가 tmpfiles.d/amr-adaptor.conf 를 __USER__로 렌더해
    # 설치 경로(/etc/tmpfiles.d/)에 두는지 검증
    rendered = render_tmpfiles(user="bot")          # 실제 헬퍼명에 맞춤
    assert "d /run/amr-adaptor 0750 bot" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_update_jibot_adapter_over_ssh.py -k tmpfiles -q`
Expected: FAIL

- [ ] **Step 3: Implement**

`scripts/systemd/tmpfiles.d/amr-adaptor.conf`(플레이스홀더):

```
# /run/amr-adaptor: adapter↔WebUi 로컬 IPC root (tmpfs). 각 adapter가 <serial>/ 하위를 만든다.
d /run/amr-adaptor 0750 __USER__ __GROUP__ -
```

`scripts/setup-adaptor-service.sh`: polkit 렌더 함수 인접에, tmpfiles 템플릿의 `__USER__`/`__GROUP__`를 치환해 `/etc/tmpfiles.d/amr-adaptor.conf`로 설치하고 `systemd-tmpfiles --create /etc/tmpfiles.d/amr-adaptor.conf`를 실행하는 단계를 추가. `scripts/update-jibot-adapter-over-ssh.sh`는 이 파일을 함께 전송/설치하도록 파일 목록에 추가.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_update_jibot_adapter_over_ssh.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/systemd/tmpfiles.d/amr-adaptor.conf scripts/setup-adaptor-service.sh scripts/update-jibot-adapter-over-ssh.sh tests/test_update_jibot_adapter_over_ssh.py
git commit -m "feat(deploy): install /run/amr-adaptor tmpfiles root"
```

---

## Task 9: WebUi가 jibot read를 FileMonitor로 와이어링 (Phase 1 통합)

**Files:**
- Modify: `adaptor/web/main.py`
- Test: `adaptor/tests/test_web_main.py` (회귀/신규)

**Interfaces:**
- Consumes: `FileMonitor`(Task 5), `MqttSender`(Task 6), `spec.manufacturer`/`spec.serial`.

- [ ] **Step 1: Write the failing test**

`test_web_main.py`의 기존 와이어링 테스트 패턴을 보고, jibot spec에 대해 `monitors[key]`가 `FileMonitor`이고 `senders[key]`가 `MqttSender`인지 검증하는 테스트를 추가. 비-jibot spec은 `MqttMonitor` 유지.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_web_main.py -q`
Expected: FAIL

- [ ] **Step 3: Implement main.py 와이어링**

`main.py`의 monitor 루프(`:54-65`)를 spec.manufacturer로 분기:

```python
    from core.monitor import FileMonitor, MqttMonitor
    from web.senders import MqttSender

    monitors: dict = {}
    senders: dict = {}
    sessions: dict = {}
    for s in specs:
        if s.monitor_kind == "none":
            continue
        if s.manufacturer == "jibot":
            monitors[s.key] = FileMonitor(s.serial)
            # Phase 1: 명령은 아직 MQTT. 작은 MqttMonitor를 송신 전용으로 둔다.
            h = s.mqtt_host or broker_host
            p = s.mqtt_port or broker_port
            sess_key = (h, p, s.topic_prefix)
            if sess_key not in sessions:
                sessions[sess_key] = MqttMonitor(
                    h, p, s.topic_prefix, client_id=f"amr-webui-{os.getpid()}-{s.serial}"
                )
                sessions[sess_key].start()
            senders[s.key] = MqttSender(sessions[sess_key])
        else:
            h = s.mqtt_host or broker_host
            p = s.mqtt_port or broker_port
            sess_key = (h, p, s.topic_prefix)
            if sess_key not in sessions:
                sessions[sess_key] = MqttMonitor(
                    h, p, s.topic_prefix, client_id=f"amr-webui-{os.getpid()}-{s.serial}"
                )
                sessions[sess_key].start()
            monitors[s.key] = sessions[sess_key]
            senders[s.key] = MqttSender(sessions[sess_key])
```

`WebUi(...)` 생성에 `senders=senders` 추가.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_web_main.py -q`
Expected: PASS

- [ ] **Step 5: Phase 1 전체 회귀**

Run (두 명령 **각각** 성공해야 함 — 종료 코드를 `; echo` 등으로 가리지 말 것):
```bash
cd adaptor && python -m pytest tests/ -q
cd adaptor && python -m unittest tests.test_ipc_paths tests.test_file_monitor tests.test_monitor tests.test_mqtt tests.test_senders -v
```
Expected: 두 명령 모두 exit 0 (PASS). 어느 하나라도 실패하면 게이트 불통과.

- [ ] **Step 6: Commit**

```bash
git add adaptor/web/main.py adaptor/tests/test_web_main.py
git commit -m "feat(web): jibot read path uses FileMonitor (state without broker)"
```

> **Phase 1 완료 — 검증 게이트:** 브로커를 내려도(또는 ACS 단절) WebUi 대시보드가 state.json 기반으로 계속 동작하고, "adapter online/stale"·"ACS disconnected"가 정확히 표시된다. 명령은 아직 MQTT.

---

# Phase 2 — 명령 경로 (브로커 없이 제어된다)

## Task 10: adapter UDS 제어 서버

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`run_adapter`에 UDS 서버, 신규 `_handle_control_conn`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` (신규)

**Interfaces:**
- Consumes: `core.ipc_paths.control_sock_path`, `instant_actions_accept_procedure`, `InstantActions.from_dict`.
- Produces: control.sock에서 `{"instantActions": {...}, "meta": {...}}`를 받아 `instant_actions_accept_procedure` 호출 후 `{"delivered": true, "action_ids": [...]}` 응답. 파싱 실패 시 `{"delivered": false, "error": ...}`.

- [ ] **Step 1: Write the failing test (handler 단위)**

소켓 없이 핸들러 로직만 검증하기 위해, 파싱+디스패치를 담당하는 순수 메서드 `_process_control_request(req: dict) -> dict`를 분리해 테스트한다.

```python
# test_adapter_jibot_v3_order.py 에 추가
    def test_process_control_request_dispatches_and_reports_delivered(self):
        with mock.patch.object(self.adapter, "instant_actions_accept_procedure") as proc:
            resp = self.adapter._process_control_request({
                "instantActions": {
                    "headerId": 1, "timestamp": "t", "version": "3.0.0",
                    "manufacturer": "jibot", "serialNumber": "S1",
                    "actions": [{"actionId": "a1", "actionType": "stateRequest",
                                 "blockingType": "NONE", "actionParameters": []}],
                },
                "meta": {"source_user": "op", "confirmed": True, "created_at": 1.0},
            })
        self.assertTrue(resp["delivered"])
        self.assertEqual(resp["action_ids"], ["a1"])
        proc.assert_called_once()

    def test_process_control_request_rejects_bad_payload(self):
        resp = self.adapter._process_control_request({"instantActions": {"bad": 1}})
        self.assertFalse(resp["delivered"])
        self.assertIn("error", resp)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_process_control_request_dispatches_and_reports_delivered -v`
Expected: FAIL — `AttributeError: ... '_process_control_request'`

- [ ] **Step 3: Implement handler + asyncio UDS 서버**

`adapter_jibot.py` (import에 `import asyncio`, `import json`, `from protocol...InstantActions` 이미 존재):

```python
    def _process_control_request(self, req: dict) -> dict:
        """Parse a WebUi control request and dispatch via the shared instant
        actions procedure. Returns a delivered-level ack (per-action verdict is
        observed via state.instantActionStates, not here)."""
        try:
            ia = InstantActions.from_dict(req["instantActions"])
        except Exception as exc:  # noqa: BLE001
            return {"delivered": False, "error": f"invalid instantActions payload: {exc}"}
        meta = req.get("meta", {})
        print(f"[CONTROL UDS] source_user={meta.get('source_user')} "
              f"confirmed={meta.get('confirmed')} actions={len(ia.actions)}")
        action_ids = [a.action_id for a in ia.actions]
        self.instant_actions_accept_procedure(ia)
        return {"delivered": True, "action_ids": action_ids}

    async def _handle_control_conn(self, reader, writer):
        try:
            raw = await reader.read(65536)
            req = json.loads(raw.decode("utf-8"))
            resp = self._process_control_request(req)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            resp = {"delivered": False, "error": f"bad request: {exc}"}
        except Exception as exc:  # noqa: BLE001
            resp = {"delivered": False, "error": f"server error: {exc}"}
        try:
            writer.write(json.dumps(resp).encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()

    async def _serve_control_socket(self):
        import os
        from core import ipc_paths
        serial = self.config.vehicle.serial_number
        ipc_paths.ensure_runtime_dir(serial)
        sock = ipc_paths.control_sock_path(serial)
        try:
            if sock.exists():
                os.unlink(sock)            # stale socket from a previous run
        except OSError:
            pass
        server = await asyncio.start_unix_server(self._handle_control_conn, path=str(sock))
        os.chmod(sock, 0o660)
        print(f"[CONTROL UDS] listening on {sock}")
        async with server:
            await server.serve_forever()
```

`run_adapter`(`:271`)에 태스크 추가(다른 `create_task`들 인접):

```python
        self.control_server_task = asyncio.create_task(self._serve_control_socket())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_process_control_request_dispatches_and_reports_delivered tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_process_control_request_rejects_bad_payload -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(adapter): UDS control server -> instant_actions_accept_procedure"
```

---

## Task 11: WebUi UdsSender + jibot 명령 전환 + paho 정리

**Files:**
- Modify: `adaptor/web/senders.py` (신규 `UdsSender`)
- Modify: `adaptor/web/main.py` (jibot senders=UdsSender, jibot용 MqttMonitor 제거)
- Test: `adaptor/tests/test_senders.py` (UdsSender round-trip + fail-closed)

**Interfaces:**
- Produces: `web.senders.UdsSender(sock_path)` with `send(payload, meta) -> (bool, str)`; adapter 미가동 시 `(False, "adapter offline — not delivered")`.

- [ ] **Step 1: Write the failing test (실제 UDS 라운드트립 + fail-closed)**

```python
# adaptor/tests/test_senders.py 에 추가
import json, socket, threading
from pathlib import Path
from tempfile import TemporaryDirectory
from web.senders import UdsSender


def _serve_once(sock_path, response):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path); srv.listen(1)
    def run():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(json.dumps(response).encode())
        conn.close(); srv.close()
    t = threading.Thread(target=run, daemon=True); t.start()
    return t


class UdsSenderTest(unittest.TestCase):
    def test_round_trip_delivered(self):
        with TemporaryDirectory() as d:
            sock = str(Path(d) / "control.sock")
            t = _serve_once(sock, {"delivered": True, "action_ids": ["a1"]})
            delivered, msg = UdsSender(sock).send({"x": 1}, {"source_user": "op"})
            t.join(timeout=2)
            self.assertTrue(delivered)

    def test_fail_closed_when_adapter_absent(self):
        with TemporaryDirectory() as d:
            sock = str(Path(d) / "missing.sock")
            delivered, msg = UdsSender(sock).send({"x": 1}, {})
            self.assertFalse(delivered)
            self.assertIn("not delivered", msg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_senders.UdsSenderTest -v`
Expected: FAIL — `ImportError: cannot import name 'UdsSender'`

- [ ] **Step 3: Implement UdsSender**

`web/senders.py`에 추가(상단 `import json, socket`):

```python
class UdsSender:
    def __init__(self, sock_path, timeout: float = 2.0) -> None:
        self._path = str(sock_path)
        self._timeout = timeout

    def send(self, payload: dict, meta: dict) -> Tuple[bool, str]:
        req = json.dumps({"instantActions": payload, "meta": meta}).encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self._timeout)
                s.connect(self._path)
                s.sendall(req)
                s.shutdown(socket.SHUT_WR)
                chunks = []
                while True:
                    b = s.recv(4096)
                    if not b:
                        break
                    chunks.append(b)
            data = json.loads(b"".join(chunks).decode("utf-8"))
            delivered = bool(data.get("delivered"))
            return delivered, data.get("error") or ("delivered" if delivered else "rejected")
        except (FileNotFoundError, ConnectionRefusedError):
            return False, "adapter offline — not delivered"
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"command failed: {exc}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_senders -v`
Expected: PASS (MqttSender + UdsSender)

- [ ] **Step 4b: UDS 통합 테스트 (server + UdsSender end-to-end)**

handler 단위(Task 10)·sender 단위(위)만으로는 `start_unix_server`↔`UdsSender` 결선이
검증되지 않는다. 실제 소켓 생성 → 전송 → `instant_actions_accept_procedure` 호출 →
delivered 응답을 확인하는 asyncio 통합 테스트를 `test_adapter_jibot_v3_order.py`
(self.adapter fixture)에 추가:

```python
    def test_uds_server_and_sender_integration(self):
        import asyncio
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from core import ipc_paths
        from web.senders import UdsSender

        async def scenario():
            with TemporaryDirectory() as d:
                sock = Path(d) / "control.sock"
                with mock.patch.object(ipc_paths, "control_sock_path", return_value=sock), \
                     mock.patch.object(ipc_paths, "ensure_runtime_dir", return_value=Path(d)), \
                     mock.patch.object(self.adapter, "instant_actions_accept_procedure") as proc:
                    server_task = asyncio.create_task(self.adapter._serve_control_socket())
                    await asyncio.sleep(0.1)   # let the server bind
                    payload = {"headerId": 1, "timestamp": "t", "version": "3.0.0",
                               "manufacturer": "jibot", "serialNumber": "S1",
                               "actions": [{"actionId": "a1", "actionType": "stateRequest",
                                            "blockingType": "NONE", "actionParameters": []}]}
                    delivered, _ = await asyncio.get_running_loop().run_in_executor(
                        None, UdsSender(sock).send, payload, {"source_user": "op"})
                    server_task.cancel()
                    return delivered, proc.call_count

        delivered, calls = asyncio.run(scenario())
        self.assertTrue(delivered)
        self.assertEqual(calls, 1)
```

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_uds_server_and_sender_integration -v`
Expected: PASS

- [ ] **Step 5: Swap main.py to UdsSender for jibot + drop jibot MqttMonitor**

`main.py` jibot 분기를 명령까지 파일/소켓로 전환:

```python
    from core.monitor import FileMonitor, MqttMonitor
    from core import ipc_paths
    from web.senders import MqttSender, UdsSender
    ...
        if s.manufacturer == "jibot":
            monitors[s.key] = FileMonitor(s.serial)
            senders[s.key] = UdsSender(ipc_paths.control_sock_path(s.serial))
        else:
            ...  # 비-jibot: 기존 MqttMonitor + MqttSender 유지
```

jibot만 있는 배포에선 `MqttMonitor`/`broker_host`/`broker_port`가 더 이상 쓰이지 않으면 import·읽기를 비-jibot 존재 여부로 가드(완전 미사용 시 제거). 비-jibot이 남으면 그대로 둔다.

- [ ] **Step 6: Update test_web_main + run**

`test_web_main.py`에서 jibot senders가 `UdsSender`인지로 갱신.

Run: `cd adaptor && python -m pytest tests/test_web_main.py tests/test_senders.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add adaptor/web/senders.py adaptor/web/main.py adaptor/tests/test_senders.py adaptor/tests/test_web_main.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(web): jibot commands via UDS (fail-closed), broker no longer needed"
```

> **Phase 2 완료 — 검증 게이트:** 브로커를 내려도 Control 뷰에서 instant action이 전달되고(adapter 가동 시), adapter를 내리면 "not delivered"가 즉시 뜬다(fail-closed). BUSY 차단은 `instantActionStates`에 FAILED로 표시.

---

## Task 12: 문서 + 통합 점검

**Files:**
- Modify: `docs/guide/web-ui.md`
- (테스트 없음 — 문서/수동 점검)

- [ ] **Step 1: web-ui.md 갱신**

통신 모델 절에 다음을 반영: MQTT=ACS 전용; 내부는 `/run/amr-adaptor/<serial>/`의 state.json·health.json(읽기)·control.sock(명령); "adapter online/stale" vs "ACS connected/disconnected" 구분; 명령은 delivered-ack + 결과는 instantActionStates; 권한/노출은 `[web_ui].host`·tmpfiles 설정을 따름.

- [ ] **Step 2: 전체 테스트 회귀**

Run: `cd adaptor && python -m pytest tests/ -q` 그리고 core/utils unittest 스위트(`python -m unittest tests.test_ipc_paths tests.test_file_monitor tests.test_monitor tests.test_mqtt tests.test_senders -v`)
Expected: 전부 PASS

- [ ] **Step 3: 수동 스모크(로봇 또는 시뮬레이터)**

브로커 중지 상태에서: (a) 대시보드가 state.json으로 갱신되는지(`cat /run/amr-adaptor/<serial>/state.json`로 교차 확인), (b) "ACS disconnected" 표시, (c) Control에서 stateRequest "delivered", (d) adapter 중지 후 명령이 "not delivered"인지.

- [ ] **Step 4: Commit**

```bash
git add docs/guide/web-ui.md
git commit -m "docs(web-ui): file+UDS local IPC model, ACS status, fail-closed commands"
```

---

## Self-Review (작성자 점검 결과)

- **Spec 커버리지:** §변경1(state/health write, FileMonitor, ACS 항목)=Task 2·3·5·6·7·9; §1e(instantActionStates)=Task 4·7; §변경2(UDS delivered)=Task 10·11; §변경3(tmpfiles, paho 정리)=Task 8·11; 스키마=Task 2·3; 안전(fail-closed/confirm/audit)=Task 6·10·11; 테스트=각 Task. 누락 없음.
- **Placeholder:** 코드 단계는 실제 코드를 담음. render(Task 7)·main/web_main·tmpfiles 회귀(Task 8·9)는 "기존 파일의 실제 fixture/헬퍼명에 맞춘다"고 명시 — 실행자가 해당 파일을 열어 이름을 맞춰야 하는 부분이며, 의도된 적응 지점(파일 위치·검증 대상은 특정됨).
- **타입 일관성:** sender `send(payload, meta) -> (bool, str)`는 Task 6(MqttSender)·11(UdsSender)·server `_post_action`에서 동일. `FileMonitor.get_snapshot/broker_connected/last_error/acs_broker_connected`는 Task 5 정의·Task 6·9 소비에서 일치. `_process_control_request(dict)->dict`·`instant_actions_accept_procedure`는 Task 10에서 일치.
- **순서 의존:** Task 1→2/3, 4→5, 5+6→9(Phase 1 통합), 10→11(Phase 2). 각 Task 끝에 독립 테스트.
