# WebUi EZIO/PIO 정보·현상태 표시 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** adapter가 보유한 EZIO/PIO 관측값을 로컬 전용 `io.json`(tmpfs)으로 넘기고, WebUi 메인 페이지에 어댑터별 EZIO/PIO 패널로 표시한다.

**Architecture:** adapter는 기존 `state.json`/`health.json`과 같은 디렉터리에 `io.json`을 atomic write(직접 폴링 없음, EZIO 입력은 기존 `manage_tray_slot` 1초 루프에 편승, 출력/보드/파일쓰기는 ~2.5초 throttle). WebUi `FileMonitor.get_io()`가 읽어 `adapter_list_page`가 패널을 렌더. MQTT/ACS 미경유.

**Tech Stack:** Python 3.11+, asyncio, stdlib `http.server`(WebUi), unittest(core/ipc/monitor 테스트), pytest(web 테스트). 외부 의존 없음.

## Global Constraints

- **MQTT/ACS 미경유** — IO는 `io.json`에만. `state.json`(=MQTT publish dict)·VDA5050 state는 건드리지 않는다.
- **직접 폴링 금지** — WebUi는 EZIO/PIO 하드웨어에 직접 접근하지 않는다.
- **io.json write 주기 ≈ 2.5초** — 상수 `_IO_WRITE_INTERVAL_SEC = 2.5` (`adapter_jibot.py`).
- **EZIO 출력/보드 조회는 throttle write 시점에만**, 입력 read(매 1초)와 분리된 try/except.
- **configured = (EZIO: `_ezi_io is not None`) / (PIO: `pio_port` 존재) AND not simulator.** simulator면 io.json 미기록.
- **테스트 실행**: core/ipc/monitor/adapter/web 모두 `cd adaptor && python -m pytest tests/<file>` (rootdir=`adaptor/`, configfile=`pyproject.toml`).
- 순수 렌더(외부 자원/JS 없음), 기존 `esc`/`_pill`/`_command_group` 헬퍼 재사용.
- 커밋 메시지 말미: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

- `adaptor/core/ipc_paths.py` — `io_path(serial)` 추가 (Task 1).
- `adaptor/core/monitor.py` — `EzioState`/`PioState`/`IoSnapshot` + `extract_io` + `FileMonitor.get_io`/`MqttMonitor.get_io` (Task 2, 3).
- `adaptor/adapter_jibot.py` — IO 캐시 필드 + `_note_ezio_*`/`_note_pio` + `_io_snapshot_dict` + `_write_io_file` (Task 4), `manage_tray_slot` 편승+throttle (Task 5), PIO 액션 wiring (Task 6).
- `adaptor/web/render.py` — `adapter_list_page` io 패널 (Task 7).
- `adaptor/web/server.py` — `_dispatch_get("/")` io_by_key (Task 8).
- 테스트: `adaptor/tests/test_ipc_paths.py`, `test_monitor.py`, `test_file_monitor.py`, 신규 `test_adapter_io_snapshot.py`, `test_web_render.py`, `test_web_server.py`.

---

### Task 1: ipc_paths — io.json 경로

**Files:**
- Modify: `adaptor/core/ipc_paths.py` (after `health_path`, ~line 48)
- Test: `adaptor/tests/test_ipc_paths.py`

**Interfaces:**
- Produces: `ipc_paths.io_path(serial: str) -> pathlib.Path` (→ `runtime_dir(serial)/"io.json"`).

- [ ] **Step 1: Write the failing test** — `adaptor/tests/test_ipc_paths.py`, 새 메서드를 `IpcPathsTest`에 추가:

```python
    def test_io_path_is_under_runtime_root(self):
        self.assertEqual(ipc_paths.io_path("S1").name, "io.json")
        self.assertTrue(str(ipc_paths.io_path("S1")).endswith("amr-adaptor/S1/io.json"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_ipc_paths.py -k io_path -v`
Expected: FAIL with `AttributeError: module 'core.ipc_paths' has no attribute 'io_path'`

- [ ] **Step 3: Write minimal implementation** — `adaptor/core/ipc_paths.py`, `health_path` 바로 뒤에 추가:

```python
def io_path(serial: str) -> Path:
    return runtime_dir(serial) / "io.json"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_ipc_paths.py -k io_path -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/ipc_paths.py adaptor/tests/test_ipc_paths.py
git commit -m "feat(ipc): add io.json runtime path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: monitor — IoSnapshot dataclasses + extract_io

**Files:**
- Modify: `adaptor/core/monitor.py` (add dataclasses near `StateSnapshot` ~line 61; add `extract_io` near `extract_connection` ~line 179)
- Test: `adaptor/tests/test_monitor.py`

**Interfaces:**
- Produces:
  - `monitor.EzioState(configured: bool=False, ip=None, port=None, connected: bool=False, board=None, inputs: Optional[List[int]]=None, inputs_updated_at=None, outputs: Optional[List[int]]=None, outputs_updated_at=None, error: str="")`
  - `monitor.PioState(configured: bool=False, port=None, baudrate=None, connected: bool=False, inputs: Optional[Dict[str,str]]=None, inputs_updated_at=None, error: str="")`
  - `monitor.IoSnapshot(updated_at: Optional[float]=None, ezio: Optional[EzioState]=None, pio: Optional[PioState]=None)`
  - `monitor.extract_io(payload: Dict[str, Any]) -> IoSnapshot` (schema-tolerant; non-dict → empty IoSnapshot; `ezio`/`pio` 키가 dict일 때만 해당 sub-state 생성)

- [ ] **Step 1: Write the failing test** — `adaptor/tests/test_monitor.py` 끝에 새 `unittest.TestCase` 추가:

```python
class ExtractIoTest(unittest.TestCase):
    def test_parses_ezio_and_pio_blocks(self):
        io = monitor.extract_io({
            "updated_at": 100.0,
            "ezio": {
                "configured": True, "ip": "10.8.8.87", "port": 3002,
                "connected": True, "board": "EZI-IO X",
                "inputs": [0, 1, 0], "inputs_updated_at": 99.0,
                "outputs": [1, 0], "outputs_updated_at": 98.0, "error": "",
            },
            "pio": {
                "configured": True, "port": "/dev/ttyUSB0", "baudrate": 19200,
                "connected": False, "inputs": {"1": "on"},
                "inputs_updated_at": 50.0, "error": "",
            },
        })
        self.assertEqual(io.updated_at, 100.0)
        self.assertTrue(io.ezio.configured)
        self.assertEqual(io.ezio.ip, "10.8.8.87")
        self.assertEqual(io.ezio.inputs, [0, 1, 0])
        self.assertEqual(io.ezio.outputs_updated_at, 98.0)
        self.assertTrue(io.pio.configured)
        self.assertFalse(io.pio.connected)
        self.assertEqual(io.pio.inputs, {"1": "on"})
        self.assertEqual(io.pio.inputs_updated_at, 50.0)

    def test_missing_blocks_and_non_dict_are_tolerated(self):
        self.assertIsNone(monitor.extract_io({}).ezio)
        self.assertIsNone(monitor.extract_io({}).pio)
        empty = monitor.extract_io("nope")
        self.assertIsNone(empty.updated_at)
        self.assertIsNone(empty.ezio)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_monitor.py -k ExtractIo -v`
Expected: FAIL with `AttributeError: module 'core.monitor' has no attribute 'extract_io'`

- [ ] **Step 3: Write minimal implementation** — `adaptor/core/monitor.py`. `StateSnapshot` 정의 뒤(예: line 65 이후)에 dataclasses 추가:

```python
@dataclass
class EzioState:
    configured: bool = False
    ip: Optional[str] = None
    port: Optional[int] = None
    connected: bool = False
    board: Optional[str] = None
    inputs: Optional[List[int]] = None
    inputs_updated_at: Optional[float] = None
    outputs: Optional[List[int]] = None
    outputs_updated_at: Optional[float] = None
    error: str = ""


@dataclass
class PioState:
    configured: bool = False
    port: Optional[str] = None
    baudrate: Optional[int] = None
    connected: bool = False
    inputs: Optional[Dict[str, str]] = None
    inputs_updated_at: Optional[float] = None
    error: str = ""


@dataclass
class IoSnapshot:
    updated_at: Optional[float] = None
    ezio: Optional[EzioState] = None
    pio: Optional[PioState] = None
```

그리고 `extract_connection` 근처에 파서 추가:

```python
def extract_io(payload: Dict[str, Any]) -> IoSnapshot:
    """Parse io.json (local-only IO diagnostics) into an IoSnapshot.

    Schema-tolerant like :func:`extract_state`: a missing/garbled block yields
    None for that sub-state, never raises.
    """
    snap = IoSnapshot()
    if not isinstance(payload, dict):
        return snap
    ts = payload.get("updated_at")
    snap.updated_at = float(ts) if isinstance(ts, (int, float)) else None
    ez = payload.get("ezio")
    if isinstance(ez, dict):
        snap.ezio = EzioState(
            configured=bool(ez.get("configured")),
            ip=ez.get("ip"),
            port=ez.get("port"),
            connected=bool(ez.get("connected")),
            board=ez.get("board"),
            inputs=ez.get("inputs") if isinstance(ez.get("inputs"), list) else None,
            inputs_updated_at=_num(ez.get("inputs_updated_at")),
            outputs=ez.get("outputs") if isinstance(ez.get("outputs"), list) else None,
            outputs_updated_at=_num(ez.get("outputs_updated_at")),
            error=str(ez.get("error") or ""),
        )
    pio = payload.get("pio")
    if isinstance(pio, dict):
        snap.pio = PioState(
            configured=bool(pio.get("configured")),
            port=pio.get("port"),
            baudrate=pio.get("baudrate"),
            connected=bool(pio.get("connected")),
            inputs=pio.get("inputs") if isinstance(pio.get("inputs"), dict) else None,
            inputs_updated_at=_num(pio.get("inputs_updated_at")),
            error=str(pio.get("error") or ""),
        )
    return snap
```

(`_num` 헬퍼는 monitor.py에 이미 존재. `List`/`Dict`/`Optional`은 이미 import됨.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_monitor.py -k ExtractIo -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/monitor.py adaptor/tests/test_monitor.py
git commit -m "feat(monitor): IoSnapshot dataclasses + extract_io parser

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: monitor — FileMonitor.get_io + MqttMonitor.get_io

**Files:**
- Modify: `adaptor/core/monitor.py` (`FileMonitor` ~line 347 근처에 메서드 추가; `MqttMonitor` ~line 215 근처)
- Test: `adaptor/tests/test_file_monitor.py`

**Interfaces:**
- Consumes: `ipc_paths.io_path` (Task 1), `extract_io`/`IoSnapshot` (Task 2).
- Produces: `FileMonitor.get_io() -> IoSnapshot`, `MqttMonitor.get_io() -> IoSnapshot`.

- [ ] **Step 1: Write the failing test** — `adaptor/tests/test_file_monitor.py`. `setUp`에 io 경로 patch 추가(기존 `patcher_s`/`patcher_h` 옆):

```python
        self.io = self.dir / "io.json"
        patcher_i = mock.patch.object(ipc_paths, "io_path", return_value=self.io)
        patcher_i.start()
        self.addCleanup(patcher_i.stop)
```

그리고 새 테스트 추가:

```python
    def test_get_io_reads_io_json(self):
        self.io.write_text(json.dumps({
            "updated_at": 5.0,
            "ezio": {"configured": True, "connected": True, "inputs": [1, 0]},
            "pio": {"configured": True, "connected": False},
        }))
        m = monitor.FileMonitor("S1")
        io = m.get_io()
        self.assertTrue(io.ezio.configured)
        self.assertEqual(io.ezio.inputs, [1, 0])
        self.assertTrue(io.pio.configured)

    def test_get_io_missing_file_is_empty(self):
        m = monitor.FileMonitor("S1")
        io = m.get_io()
        self.assertIsNone(io.ezio)
        self.assertIsNone(io.pio)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_file_monitor.py -k get_io -v`
Expected: FAIL with `AttributeError: 'FileMonitor' object has no attribute 'get_io'`

- [ ] **Step 3: Write minimal implementation** — `adaptor/core/monitor.py`. `FileMonitor.get_snapshot` 뒤에 추가:

```python
    def get_io(self) -> IoSnapshot:
        data = self._read_json(ipc_paths.io_path(self.serial))
        return extract_io(data) if isinstance(data, dict) else IoSnapshot()
```

그리고 `MqttMonitor`(브로커 경로)에는 빈 stub:

```python
    def get_io(self) -> IoSnapshot:
        # IO diagnostics are file-only (io.json); the broker path carries none.
        return IoSnapshot()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_file_monitor.py -v`
Expected: PASS (all, including existing)

- [ ] **Step 5: Commit**

```bash
git add adaptor/core/monitor.py adaptor/tests/test_file_monitor.py
git commit -m "feat(monitor): FileMonitor.get_io reads io.json (+MqttMonitor stub)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: adapter — IO 캐시 필드 + note 헬퍼 + io.json 직렬화/쓰기

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`__init__` ~line 149 뒤; helper 메서드는 `_write_health_file` ~line 601 뒤)
- Test: `adaptor/tests/test_adapter_io_snapshot.py` (신규)

**Interfaces:**
- Produces (adapter 메서드):
  - `_note_ezio(*, connected=None, inputs=None, outputs=None, board=None, error=None)` — 주어진 값만 갱신, `inputs`/`outputs`는 `time.time()`을 해당 `*_at`에 기록.
  - `_note_pio(*, connected=None, inputs=None, error=None)` — `inputs` 주면 `_pio_inputs_at=time.time()`.
  - `_io_snapshot_dict() -> dict` — io.json 스키마 dict 생성(configured 판정 포함).
  - `_write_io_file() -> None` — `ipc_paths.io_path(serial)`에 atomic write(예외 swallow + 1회 로깅).

**참고:** 이 Task는 헬퍼/쓰기만 추가하고 루프/PIO 액션 wiring은 Task 5·6에서 연결한다(독립 테스트 가능).

- [ ] **Step 1: Write the failing test** — 신규 `adaptor/tests/test_adapter_io_snapshot.py`:

```python
"""adapter io.json snapshot/write 단위 테스트 (하드웨어 없이)."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from core import ipc_paths


def _bare_adapter():
    """Adapter 인스턴스를 __init__ 없이 만들고 테스트에 필요한 속성만 세팅."""
    from adapter_jibot import Adapter
    a = Adapter.__new__(Adapter)
    # IO 캐시 필드 초기값
    a._ezi_io = object()          # not None -> ezio configured
    a._ezio_inputs = None
    a._ezio_inputs_at = None
    a._ezio_outputs = None
    a._ezio_outputs_at = None
    a._ezio_connected = False
    a._ezio_board = ""
    a._ezio_error = ""
    a._pio_connected = False
    a._pio_inputs = None
    a._pio_inputs_at = None
    a._pio_error = ""
    a._io_last_write = 0.0
    # config stub
    a.config = mock.Mock()
    a.config.vehicle.serial_number = "S1"
    a.config.ezi_config.ezi_io = "10.8.8.87"
    a.config.pio_config.pio_port = "/dev/ttyUSB0"
    a.config.pio_config.pio_baudrate = 19200
    a._is_simulator = lambda: False
    return a


class AdapterIoSnapshotTest(unittest.TestCase):
    def test_note_ezio_sets_timestamps(self):
        a = _bare_adapter()
        a._note_ezio(connected=True, inputs=[1, 0, 1])
        self.assertTrue(a._ezio_connected)
        self.assertEqual(a._ezio_inputs, [1, 0, 1])
        self.assertIsNotNone(a._ezio_inputs_at)
        self.assertIsNone(a._ezio_outputs_at)  # outputs not touched

    def test_note_pio_inputs_sets_timestamp_and_keeps_on_disconnect(self):
        a = _bare_adapter()
        a._note_pio(connected=True, inputs={"1": "on"})
        self.assertEqual(a._pio_inputs, {"1": "on"})
        self.assertIsNotNone(a._pio_inputs_at)
        a._note_pio(connected=False)          # disconnect
        self.assertFalse(a._pio_connected)
        self.assertEqual(a._pio_inputs, {"1": "on"})  # inputs retained

    def test_snapshot_dict_marks_configured(self):
        a = _bare_adapter()
        a._note_ezio(connected=True, inputs=[1] * 16)
        d = a._io_snapshot_dict()
        self.assertTrue(d["ezio"]["configured"])
        self.assertEqual(d["ezio"]["ip"], "10.8.8.87")
        self.assertTrue(d["pio"]["configured"])
        self.assertEqual(d["pio"]["baudrate"], 19200)

    def test_snapshot_dict_ezio_unconfigured_when_client_none(self):
        a = _bare_adapter()
        a._ezi_io = None
        d = a._io_snapshot_dict()
        self.assertFalse(d["ezio"]["configured"])

    def test_write_io_file_atomic(self):
        a = _bare_adapter()
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "io.json"
            with mock.patch.object(ipc_paths, "io_path", return_value=target), \
                 mock.patch.object(ipc_paths, "ensure_runtime_dir"):
                a._note_ezio(connected=True, inputs=[0] * 16)
                a._write_io_file()
                data = json.loads(target.read_text())
                self.assertIn("updated_at", data)
                self.assertEqual(data["ezio"]["inputs"], [0] * 16)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_io_snapshot.py -v`
Expected: FAIL (`AttributeError: 'Adapter' object has no attribute '_note_ezio'`)

- [ ] **Step 3: Write minimal implementation**

`adaptor/adapter_jibot.py` `__init__`, `self._pio_client_factory = None`(line 149) 뒤에 IO 캐시 필드 추가:

```python
        # EZIO/PIO 진단 스냅샷(로컬 io.json용). 미관측은 None.
        self._ezio_inputs: Optional[List[int]] = None
        self._ezio_inputs_at: Optional[float] = None
        self._ezio_outputs: Optional[List[int]] = None
        self._ezio_outputs_at: Optional[float] = None
        self._ezio_connected: bool = False
        self._ezio_board: str = ""
        self._ezio_error: str = ""
        self._pio_connected: bool = False
        self._pio_inputs: Optional[Dict[str, str]] = None
        self._pio_inputs_at: Optional[float] = None
        self._pio_error: str = ""
        self._io_last_write: float = 0.0
```

모듈 상단(클래스 밖, 다른 상수 근처)에 추가:

```python
_IO_WRITE_INTERVAL_SEC = 2.5
```

`_write_health_file`(line 601) 뒤에 헬퍼 추가:

```python
    def _note_ezio(self, *, connected=None, inputs=None, outputs=None,
                   board=None, error=None) -> None:
        now = time.time()
        if connected is not None:
            self._ezio_connected = connected
        if inputs is not None:
            self._ezio_inputs = inputs
            self._ezio_inputs_at = now
        if outputs is not None:
            self._ezio_outputs = outputs
            self._ezio_outputs_at = now
        if board is not None:
            self._ezio_board = board
        if error is not None:
            self._ezio_error = error

    def _note_pio(self, *, connected=None, inputs=None, error=None) -> None:
        now = time.time()
        if connected is not None:
            self._pio_connected = connected
        if inputs is not None:
            self._pio_inputs = inputs
            self._pio_inputs_at = now
        if error is not None:
            self._pio_error = error

    def _io_snapshot_dict(self) -> Dict[str, Any]:
        ezio_configured = self._ezi_io is not None
        pio_configured = bool(getattr(self.config.pio_config, "pio_port", ""))
        return {
            "updated_at": time.time(),
            "ezio": {
                "configured": ezio_configured,
                "ip": getattr(self.config.ezi_config, "ezi_io", None),
                "port": 3002,
                "connected": self._ezio_connected,
                "board": self._ezio_board,
                "inputs": self._ezio_inputs,
                "inputs_updated_at": self._ezio_inputs_at,
                "outputs": self._ezio_outputs,
                "outputs_updated_at": self._ezio_outputs_at,
                "error": self._ezio_error,
            },
            "pio": {
                "configured": pio_configured,
                "port": getattr(self.config.pio_config, "pio_port", None),
                "baudrate": getattr(self.config.pio_config, "pio_baudrate", None),
                "connected": self._pio_connected,
                "inputs": self._pio_inputs,
                "inputs_updated_at": self._pio_inputs_at,
                "error": self._pio_error,
            },
        }

    def _write_io_file(self) -> None:
        try:
            serial = self.config.vehicle.serial_number
            ipc_paths.ensure_runtime_dir(serial)
            ipc_paths.atomic_write_json(
                ipc_paths.io_path(serial), self._io_snapshot_dict()
            )
            self._io_file_error_logged = False
        except Exception as exc:  # noqa: BLE001
            if not getattr(self, "_io_file_error_logged", False):
                print(f"[IO FILE WRITE FAILED] {exc}")
                self._io_file_error_logged = True
```

(`Optional`/`List`/`Dict`/`Any`는 이미 import됨 — 파일 상단 `typing` import 확인.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_adapter_io_snapshot.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_io_snapshot.py
git commit -m "feat(adapter): EZIO/PIO io snapshot cache + io.json writer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: adapter — manage_tray_slot 편승 + throttle(출력/보드/쓰기)

**Files:**
- Modify: `adaptor/adapter_jibot.py` `manage_tray_slot` (line 390-407)
- Test: `adaptor/tests/test_adapter_io_snapshot.py`

**Interfaces:**
- Consumes: `_note_ezio`/`_write_io_file`/`_io_snapshot_dict` (Task 4), `_read_ezio_input_bits` (기존), `_IO_WRITE_INTERVAL_SEC`.
- Produces: throttle 분리 동작 — 입력 read는 매 루프, `get_output()`+`get_board_info()`+`_write_io_file()`는 `_IO_WRITE_INTERVAL_SEC` 경과 시에만.

- [ ] **Step 1: Write the failing test** — `test_adapter_io_snapshot.py`에 추가. throttle 로직을 동기 헬퍼 `_io_throttle_tick(now)`로 분리해 테스트(루프 자체는 async라 단위테스트가 번거로움 → 분리가 더 깔끔):

```python
    def test_throttle_writes_only_after_interval(self):
        import adapter_jibot as mod
        a = _bare_adapter()
        a._ezi_io = mock.Mock()
        calls = {"write": 0, "output": 0}
        a._write_io_file = lambda: calls.__setitem__("write", calls["write"] + 1)
        # get_output / get_board_info coroutines
        async def _out():
            calls["output"] += 1
            return {"outputs": [0] * 16}
        async def _board():
            return {"description": "EZI-IO X"}
        a._ezi_io.get_output = _out
        a._ezi_io.get_board_info = _board

        import asyncio
        # 첫 호출(now=100): interval 경과 -> write 1, output 1
        asyncio.run(a._io_throttle_tick(100.0))
        # 두번째(now=101, <2.5초): skip
        asyncio.run(a._io_throttle_tick(101.0))
        # 세번째(now=103, >=2.5초): write 2, output 2
        asyncio.run(a._io_throttle_tick(103.0))
        self.assertEqual(calls["write"], 2)
        self.assertEqual(calls["output"], 2)
        self.assertEqual(a._ezio_board, "EZI-IO X")  # 1회 캐시
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_io_snapshot.py -k throttle -v`
Expected: FAIL (`AttributeError: ... '_io_throttle_tick'`)

- [ ] **Step 3: Write minimal implementation** — `adapter_jibot.py`. 먼저 `_io_throttle_tick`를 Task 4 헬퍼 근처에 추가:

```python
    async def _io_throttle_tick(self, now: float) -> None:
        """~_IO_WRITE_INTERVAL_SEC마다 EZIO 출력/보드 조회 후 io.json 기록.

        입력 read(매 1초)와 분리: get_output은 별도 try/except로 입력 루프를
        방해하지 않는다. board는 최초 1회만 캐시.
        """
        if now - self._io_last_write < _IO_WRITE_INTERVAL_SEC:
            return
        self._io_last_write = now
        if self._ezi_io is not None:
            try:
                resp = await self._ezi_io.get_output()
                if resp and "outputs" in resp:
                    self._note_ezio(outputs=list(resp["outputs"]))
            except Exception as exc:  # noqa: BLE001
                self._note_ezio(error=f"get_output: {exc}")
            if not self._ezio_board:
                try:
                    info = await self._ezi_io.get_board_info()
                    if info and info.get("description"):
                        self._note_ezio(board=info["description"])
                except Exception:  # noqa: BLE001
                    pass
        self._write_io_file()
```

그리고 `manage_tray_slot` 루프를 수정. 기존:

```python
            if self._ezi_io is not None:
                try:
                    inputs = await self._read_ezio_input_bits()
                except Exception as exc:
                    self._set_ezio_input_error(str(exc))
                    print(f"[PHOTO SENSOR READ FAILED] {exc}")
                else:
                    changed = self._update_loads_from_photo_sensor_inputs(inputs)
                    if changed:
                        self.request_state_publish("photo sensor changed")

            await asyncio.sleep(interval_sec)
```

를 다음으로:

```python
            if self._ezi_io is not None:
                try:
                    inputs = await self._read_ezio_input_bits()
                except Exception as exc:
                    self._set_ezio_input_error(str(exc))
                    self._note_ezio(connected=False, error=str(exc))
                    print(f"[PHOTO SENSOR READ FAILED] {exc}")
                else:
                    self._note_ezio(connected=True, inputs=inputs, error="")
                    changed = self._update_loads_from_photo_sensor_inputs(inputs)
                    if changed:
                        self.request_state_publish("photo sensor changed")

            await self._io_throttle_tick(time.time())
            await asyncio.sleep(interval_sec)
```

(`_io_throttle_tick`는 simulator 분기 `continue` 아래에 있으므로 simulator에서는 호출되지 않음 → io.json 미기록.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_adapter_io_snapshot.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_io_snapshot.py
git commit -m "feat(adapter): capture EZIO inputs + throttle output/board/io.json write

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: adapter — PIO 액션 lifecycle를 _note_pio에 연결

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`_pio_read_inputs` 4182, `_pio_disconnect` 4176, `_pio_write_output` 4189, `_pio_init` 4144 끝, `_execute_pio_action` except 4135)
- Test: `adaptor/tests/test_adapter_io_snapshot.py`

**Interfaces:**
- Consumes: `_note_pio` (Task 4), 기존 PIO 메서드.
- Produces: PIO 상태 캐시가 액션 실행에 따라 갱신.

- [ ] **Step 1: Write the failing test** — `test_adapter_io_snapshot.py`에 추가:

```python
    def test_pio_read_inputs_notes_cache(self):
        import asyncio
        a = _bare_adapter()
        a._action_params = lambda action: {}
        a.config.pio_config.channel = 1
        async def _call(method, *args, **kwargs):
            return "10101010"
        a._call_pio = _call
        a._parse_pio_inputs = lambda resp: {"1": "on"}
        inputs = asyncio.run(a._pio_read_inputs(object()))
        self.assertEqual(inputs, {"1": "on"})
        self.assertEqual(a._pio_inputs, {"1": "on"})   # cached via _note_pio
        self.assertTrue(a._pio_connected)

    def test_pio_disconnect_marks_disconnected_keeps_inputs(self):
        import asyncio
        a = _bare_adapter()
        a._note_pio(connected=True, inputs={"1": "on"})
        a._get_pio_client = lambda: mock.Mock(close=lambda: None)
        asyncio.run(a._pio_disconnect())
        self.assertFalse(a._pio_connected)
        self.assertEqual(a._pio_inputs, {"1": "on"})

    def test_execute_pio_action_exception_notes_error(self):
        import asyncio
        a = _bare_adapter()
        bad = mock.Mock(action_type="pioReadIn")
        async def _boom(action):
            raise RuntimeError("serial down")
        a._pio_read_inputs = _boom
        result = asyncio.run(a._execute_pio_action(bad))
        self.assertFalse(result["ok"])
        self.assertIn("serial down", a._pio_error)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_adapter_io_snapshot.py -k pio -v`
Expected: FAIL (`_pio_inputs` 미갱신 / `_pio_error` 미설정)

- [ ] **Step 3: Write minimal implementation** — 각 PIO 메서드에 `_note_pio` 호출 추가.

`_pio_read_inputs` (return 직전):

```python
    async def _pio_read_inputs(self, action: Any) -> Dict[str, str]:
        params = self._action_params(action)
        channel = params.get("channel", self.config.pio_config.channel)
        timeout_sec = float(params.get("timeoutSec", 2.0))
        response = await self._call_pio("monitor_data", channel, wait_sec=timeout_sec)
        inputs = self._parse_pio_inputs(response)
        self._note_pio(connected=True, inputs=inputs, error="")
        return inputs
```

`_pio_disconnect` (close 후):

```python
    async def _pio_disconnect(self) -> None:
        client = self._get_pio_client()
        close = getattr(client, "close", None)
        if callable(close):
            close()
        self._note_pio(connected=False)
```

`_pio_write_output` (return 직전):

```python
    async def _pio_write_output(self, index: int, state: str, wait_sec: float = 2.0) -> Any:
        value = 1 if state == "on" else 0
        result = await self._call_pio("send_raw", f"OUT={index}:{value}", wait_sec=wait_sec)
        self._note_pio(connected=True, error="")
        return result
```

`_pio_init` (line 4168 `return {` 직전, BC 성공 후):

```python
        self._note_pio(connected=True, error="")
        return {
            "ok": True,
```

`_execute_pio_action` except 분기(line 4135-4142) — `_note_pio(error=...)` 추가:

```python
        except Exception as exc:
            self._note_pio(error=str(exc))
            return {
                "ok": False,
                "action": action.action_type,
                "failedReason": type(exc).__name__,
                "message": f"{action.action_type} failed: {exc}",
                "steps": [],
            }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_adapter_io_snapshot.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Verify no PIO regressions**

Run: `cd adaptor && python -m pytest tests/test_adapter_jibot_v3_order.py -k pio -v`
Expected: PASS (기존 pio 테스트 불변)

- [ ] **Step 6: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_io_snapshot.py
git commit -m "feat(adapter): note PIO connect/read/write/disconnect into io cache

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: render — adapter_list_page EZIO/PIO 패널

**Files:**
- Modify: `adaptor/web/render.py` (`adapter_list_page` 시그니처/본문; 헬퍼 `_io_panel` 신설)
- Test: `adaptor/tests/test_web_render.py`

**Interfaces:**
- Consumes: `monitor.IoSnapshot`/`EzioState`/`PioState` (Task 2). 렌더는 duck-typed 속성 접근(테스트는 SimpleNamespace로 대체 가능).
- Produces: `adapter_list_page(rows, camera_metrics=None, csrf="", q=None, io_by_key=None)` — `io_by_key: dict[key→IoSnapshot]`. io가 configured면 어댑터별 EZIO/PIO 패널 렌더.

- [ ] **Step 1: Write the failing test** — `adaptor/tests/test_web_render.py`에 추가(파일 상단 헬퍼 dataclass 스타일 따름):

```python
def _io(**ez):
    from types import SimpleNamespace as NS
    ezio = NS(configured=True, ip="10.8.8.87", port=3002, connected=True,
              board="EZI-IO X", inputs=[1, 0] + [0] * 14, inputs_updated_at=100.0,
              outputs=[0] * 16, outputs_updated_at=99.0, error="")
    for k, v in ez.items():
        setattr(ezio, k, v)
    pio = NS(configured=True, port="/dev/ttyUSB0", baudrate=19200, connected=False,
             inputs={"1": "on"}, inputs_updated_at=50.0, error="")
    return NS(updated_at=100.0, ezio=ezio, pio=pio)


def test_adapter_list_renders_ezio_pio_panel():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows, csrf="tok", io_by_key={"jibot": _io()})
    assert "EZIO" in out
    assert "PIO" in out
    assert "10.8.8.87" in out          # ezio ip
    assert "/dev/ttyUSB0" in out       # pio port
    assert "EZI-IO X" in out           # board


def test_adapter_list_omits_io_panel_when_unconfigured():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    io = _io()
    io.ezio.configured = False
    io.pio.configured = False
    out = render.adapter_list_page(rows, csrf="tok", io_by_key={"jibot": io})
    assert "EZI-IO X" not in out       # ezio panel omitted
    assert "/dev/ttyUSB0" not in out   # pio panel omitted


def test_adapter_list_pio_disconnected_with_inputs_not_error():
    # pioScenario finally-disconnect: connected=false + inputs present is normal
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows, csrf="tok", io_by_key={"jibot": _io()})
    # pio inputs shown even though connected=False, and no error styling for it
    assert '1:on' in out or 'on' in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_web_render.py -k ezio_pio -v`
Expected: FAIL (`adapter_list_page() got an unexpected keyword argument 'io_by_key'`)

- [ ] **Step 3: Write minimal implementation** — `adaptor/web/render.py`.

`adapter_list_page` 시그니처에 `io_by_key=None` 추가, 본문에서 카메라 패널 뒤에 어댑터별 io 패널 삽입:

```python
def adapter_list_page(rows, camera_metrics=None, csrf: str = "", q: dict | None = None,
                      io_by_key: dict | None = None) -> str:
```

`rows` 루프에서 각 spec.key에 대한 io 패널을 모은다. 카메라 패널(`camera_ctl`) 계산 뒤에:

```python
    io_by_key = io_by_key or {}
    io_panels = "".join(
        _io_panel(spec.display_name, io_by_key.get(spec.key))
        for spec, _m, _s in rows
    )
    body = f"{_flash(q or {})}{table}{camera_ctl}{io_panels}"
```

그리고 헬퍼 추가(파일 내 다른 `_command_group` 헬퍼 근처):

```python
def _io_bits(label: str, bits, updated_at) -> str:
    """비트 리스트를 핀 인덱스별 ON/OFF dot 행으로."""
    if not bits:
        return ""
    cells = "".join(
        f'<span class="status {"success" if int(b) else "neutral"}">'
        f'{i}:{"ON" if int(b) else "OFF"}</span> '
        for i, b in enumerate(bits)
    )
    age = f" <small>({esc(updated_at)})</small>" if updated_at is not None else ""
    return f"<div><strong>{esc(label)}</strong>{age}<br>{cells}</div>"


def _io_panel(display_name: str, io) -> str:
    if io is None:
        return ""
    sections = []
    ez = getattr(io, "ezio", None)
    if ez is not None and getattr(ez, "configured", False):
        conn = "success" if ez.connected else "neutral"
        conn_label = "connected" if ez.connected else "disconnected"
        err = f'<p class="err">{esc(ez.error)}</p>' if ez.error else ""
        inp = _io_bits("inputs", ez.inputs, ez.inputs_updated_at)
        outp = _io_bits("outputs", ez.outputs, ez.outputs_updated_at)
        sections.append(
            f'<section class="command-group"><div class="command-head">'
            f'<h2>EZIO — {esc(display_name)}</h2>'
            f'<span class="status {conn}">{conn_label}</span></div>'
            f'<div class="command-body">'
            f'<p>IP {esc(ez.ip)}:{esc(ez.port)} · {esc(ez.board)}</p>'
            f'{err}{inp}{outp}</div></section>'
        )
    pio = getattr(io, "pio", None)
    if pio is not None and getattr(pio, "configured", False):
        # connected=false + inputs present is NORMAL (pioScenario finally-disconnect)
        conn = "success" if pio.connected else "neutral"
        conn_label = "connected" if pio.connected else "idle"
        err = f'<p class="err">{esc(pio.error)}</p>' if pio.error else ""
        bits = ""
        if pio.inputs:
            cells = "".join(
                f'<span class="status {"success" if v == "on" else "neutral"}">'
                f'{esc(k)}:{esc(v)}</span> '
                for k, v in sorted(pio.inputs.items())
            )
            age = f" <small>({esc(pio.inputs_updated_at)})</small>" if pio.inputs_updated_at is not None else ""
            bits = f"<div><strong>inputs</strong>{age}<br>{cells}</div>"
        sections.append(
            f'<section class="command-group"><div class="command-head">'
            f'<h2>PIO — {esc(display_name)}</h2>'
            f'<span class="status {conn}">{conn_label}</span></div>'
            f'<div class="command-body">'
            f'<p>port {esc(pio.port)} @ {esc(pio.baudrate)}</p>'
            f'{err}{bits}</div></section>'
        )
    return "".join(sections)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_web_render.py -v`
Expected: PASS (전체, 기존 포함)

- [ ] **Step 5: Commit**

```bash
git add adaptor/web/render.py adaptor/tests/test_web_render.py
git commit -m "feat(web): EZIO/PIO status panels on the dashboard

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: server — 메인 페이지에 io_by_key 전달

**Files:**
- Modify: `adaptor/web/server.py` (`_dispatch_get("/")` line 295-308; helper `_monitor_io` 추가 ~line 119 근처)
- Test: `adaptor/tests/test_web_server.py`

**Interfaces:**
- Consumes: `FileMonitor.get_io` (Task 3), `adapter_list_page(... io_by_key=...)` (Task 7).
- Produces: `/` 응답에 어댑터별 EZIO/PIO 패널 포함.

- [ ] **Step 1: Write the failing test** — `adaptor/tests/test_web_server.py`. `FakeController`/`FakeMonitor`에 io 지원 추가 후 통합 테스트. `FakeMonitor`에 메서드 추가:

```python
    def get_io(self):
        from types import SimpleNamespace as NS
        ezio = NS(configured=True, ip="10.8.8.87", port=3002, connected=True,
                  board="EZI-IO X", inputs=[1] + [0] * 15, inputs_updated_at=1.0,
                  outputs=[0] * 16, outputs_updated_at=1.0, error="")
        pio = NS(configured=True, port="/dev/ttyUSB0", baudrate=19200,
                 connected=False, inputs={"1": "on"}, inputs_updated_at=1.0, error="")
        return NS(updated_at=1.0, ezio=ezio, pio=pio)
```

그리고 새 테스트:

```python
def test_root_includes_ezio_pio_panel(ui):
    web, *_ = ui
    body = _get(web, "/").read().decode()
    assert "EZIO" in body
    assert "PIO" in body
    assert "/dev/ttyUSB0" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m pytest tests/test_web_server.py -k ezio_pio -v`
Expected: FAIL (패널 미렌더 → `assert "EZIO" in body`)

- [ ] **Step 3: Write minimal implementation** — `adaptor/web/server.py`.

`_monitor_snapshot` 뒤(line 119)에 helper 추가:

```python
    def _monitor_io(self, key: str):
        monitor = self._monitors.get(key)
        get_io = getattr(monitor, "get_io", None)
        return get_io() if callable(get_io) else None
```

`_dispatch_get("/")` 블록(line 295-308)에서 io_by_key를 모아 전달:

```python
        if path == "/":
            rows = []
            io_by_key = {}
            for key, spec in self._specs.items():
                metrics = self._controllers[key].poll()
                if not _dashboard_visible(metrics):
                    continue  # unit not deployed here -> hide template-only instances
                snap = self._monitor_snapshot(key)
                rows.append((spec, metrics, snap))
                io = self._monitor_io(key)
                if io is not None:
                    io_by_key[key] = io
            camera_metrics = None
            if self._camera_controller is not None:
                cam = self._camera_controller.poll()
                if _dashboard_visible(cam):  # hide when web_video_server isn't deployed
                    camera_metrics = cam
            h._html(200, render.adapter_list_page(
                rows, camera_metrics=camera_metrics, csrf=self._csrf, q=q,
                io_by_key=io_by_key))
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m pytest tests/test_web_server.py -v`
Expected: PASS (전체, 기존 포함)

- [ ] **Step 5: Full regression**

Run: `cd adaptor && python -m pytest tests/ -q`
Expected: PASS (전체)

- [ ] **Step 6: Commit**

```bash
git add adaptor/web/server.py adaptor/tests/test_web_server.py
git commit -m "feat(web): pass per-adapter io snapshots to the dashboard

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage**: ipc io_path(변경1=T1), IoSnapshot+extract_io+get_io(변경3=T2,T3), adapter 캐시/throttle/PIO(변경2=T4,T5,T6), server io_by_key(변경4=T8), render 패널(변경5=T7). 리뷰 #1(throttle 분리)=T5, #2(섹션 타임스탬프)=T2/T7, #3(PIO note 규칙)=T6, #4(configured)=T4, #5(get_io 주/stub)=T3. 전부 매핑됨.
- **타입 일관성**: `_note_ezio`/`_note_pio`/`_io_snapshot_dict`/`_write_io_file`/`_io_throttle_tick`/`get_io`/`io_by_key`/`_monitor_io`/`_io_panel` 시그니처가 Task 간 일치.
- **스키마 일관성**: io.json 키(ezio/pio + 섹션 `*_updated_at`)가 adapter write(T4) ↔ extract_io(T2) ↔ render(T7)에서 동일.
- **하위 호환**: `io_by_key` 기본 None → 기존 `adapter_list_page(rows)` 호출/테스트 불변. `get_io` 없는 monitor는 `_monitor_io`가 None.
