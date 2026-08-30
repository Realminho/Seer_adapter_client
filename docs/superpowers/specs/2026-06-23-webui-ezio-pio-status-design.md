# WebUi EZIO/PIO 정보·현상태 표시 — 설계

작성일 2026-06-23. 대상: `adaptor/adapter_jibot.py`(adapter 프로세스) +
`adaptor/core/ipc_paths.py` + `adaptor/core/monitor.py` + `adaptor/web/server.py` +
`adaptor/web/render.py`(WebUi 프로세스).

## 동기

운영자가 WebUi 메인 페이지(`/`, 어댑터 대시보드)에서 **EZIO**(FASTECH EZi-IO,
UDP)와 **PIO**(시리얼 PIO master)의 정보와 현재 상태를 한눈에 보고 싶다. 직전
작업에서 카메라 서비스 패널을 메인 페이지에 올린 것의 연장선이다.

제약: WebUi는 adapter와 **별도 프로세스**다([[webui-adapter-local-ipc]] 참조).
EZIO 클라이언트(`set_ezi_io`)와 PIO 클라이언트(`_pio_client`)는 **adapter 프로세스가
보유**한다. WebUi가 같은 하드웨어를 **직접 폴링하면 경합**(특히 PIO 시리얼 단일
소유, EZIO도 중복 세션)이 발생한다. → WebUi는 하드웨어에 직접 붙지 않고,
**adapter가 이미 쓰는 로컬 파일 IPC 경로로 IO 상태를 받아** 표시한다.

## 결정 기록 (2026-06-23 확정)

- **직접 폴링 금지** — WebUi는 EZIO/PIO에 직접 접근하지 않는다(경합).
- **전송 = 로컬 전용 tmpfs 파일** — adapter가
  `/run/amr-adaptor/<serial>/io.json`을 atomic write로 기록, WebUi가 렌더 시
  on-demand read. **MQTT/ACS 미경유**(IO는 로컬 진단 성격 → VDA5050 state·ACS에
  넣지 않음). 기존 `state.json`(=MQTT publish dict)은 **건드리지 않는다**.
- **기록 주기 ≈ 2~3초**(느슨하게) — EZIO 입력을 이미 읽는 `manage_tray_slot`
  루프(매 1초)에서 시간 throttle로 io.json을 ~2.5초마다 기록.
- **EZIO 표시** — 정보: IP(`ezi.ezi_io`)·보드 설명; 현상태: 연결 여부·입력
  16비트(핀별 ON/OFF)·출력 비트·마지막 에러.
- **EZIO 출력/보드 조회는 throttle 시점에만** — 입력은 `manage_tray_slot`의 매
  1초 read를 그대로 두되, `get_output()`·`get_board_info()`는 io.json write
  throttle(~2.5초) 때만 **입력 처리와 분리된 try/except**로 호출한다. EZIIOClient
  기본 `timeout=2`초 + `_send`가 lock을 잡으므로(`utils/ezi_io.py`), 출력 조회를
  매 루프에 붙이면 입력 감지/loads 갱신이 느려진다(리뷰 #1).
- **PIO 표시** — 정보: 포트(`pio.pio_port`)·보드레이트; 현상태: 연결 여부 +
  **최근 read 결과**(8비트, pioReadIn/pioScenario가 읽을 때 캡처). 시리얼 경합을
  피하려 연속 폴링은 하지 않는다 → "현재 상태"는 마지막 관측값(읽은 적 없으면
  config + 미연결). `pioScenario`는 finally에서 disconnect하므로
  **"connected=false + 최근 inputs 있음"이 정상 상태**(리뷰 #3).
- **하위별 관측 시각** — top-level `updated_at`은 EZIO 입력 때문에 계속 갱신되어
  PIO/출력 값의 신선도를 알 수 없다. `ezio.inputs_updated_at`/
  `ezio.outputs_updated_at`/`pio.inputs_updated_at`을 두어 섹션별 stale을 정확히
  표시한다(리뷰 #2).
- **configured 기준** — config에 EZIO/PIO enabled 플래그가 없으므로(`EziConfig`/
  `PioConfig`), `configured = 클라이언트/하드웨어 경로 활성 AND 비-simulator`로
  정의한다(리뷰 #4). EZIO: `self._ezi_io is not None and not simulator`. PIO:
  `pio_port` 설정값 존재 and not simulator. simulator/미구성이면 io.json 미기록 →
  패널 자체가 안 뜬다.
- **표시 위치 = 메인 페이지 패널** — 카메라 패널처럼 어댑터 표 아래에 어댑터별
  "EZIO / PIO" 패널. io.json 데이터가 있는 어댑터만.

## 현재 상태 (정확한 지점)

- **EZIO.** `EZIIOClient`(`utils/ezi_io.py`): `connect()`/`get_board_info()`(dict,
  `description`)/`get_input()`(16비트)/`get_output()`/`get_input_pin` 등. UDP
  `ezi.ezi_io:3002`. main에서 `await io.connect(); await io.get_board_info();
  adapter.set_ezi_io(io)`(`main.py:725-730`). adapter는 `manage_tray_slot`
  루프(`adapter_jibot.py:390-407`)가 **매 1초** `_read_ezio_input_bits()`로 입력을
  읽어 트레이 `loads`/`errors`에만 반영(원시 비트·보드정보·출력은 어디에도 노출
  안 됨). 실패는 `EZI_IO_INPUT_FAILED` 에러로 state에 들어감.
- **PIO.** `PIOMaster`(`utils/pio.py`): 시리얼, `connect()`/`send_and_read`/
  `monitor_data`. adapter는 PIO 액션(pioInit/pioReadIn/pioWriteOut/pioScenario)
  실행 시에만 `_pio_client`를 연결(`adapter_jibot.py:4087+`), 평소 미연결.
  입력은 `_pio_read_inputs(action)`→`_parse_pio_inputs`로 8비트 dict("1".."8" →
  "on"/"off")(`:4330-4339`). 시리얼은 airshower(`utils/airshower.py`)도 사용 →
  단일 소유.
- **로컬 IPC.** adapter가 `state.json`(=`state_msg.to_dict()`+`updated_at`,
  MQTT publish dict와 동일)·`health.json`을 `/run/amr-adaptor/<serial>/`에 atomic
  write(`adapter_jibot.py:560-601`, `core/ipc_paths.py`). WebUi `FileMonitor`가
  `state.json`→`extract_state()`→`StateSnapshot`로 읽고(`core/monitor.py:299-362`),
  `health.json`에서 `acs_broker_connected`를 읽음. server `_dispatch_get("/")`가
  어댑터별 `_monitor_snapshot(key)`로 스냅샷을 모아 `adapter_list_page`를 렌더
  (`web/server.py:295-308`).
- **serial 식별.** `serial = config.vehicle.serial_number`. `FileMonitor(serial)`은
  `.serial`을 가짐 → io.json 경로 도출 가능.

## 결정 / 범위

### 변경 1 — ipc_paths: io.json 경로
`core/ipc_paths.py`에 `io_path(serial) -> runtime_dir(serial)/"io.json"` 추가.
`atomic_write_json`/`ensure_runtime_dir` 재사용.

### 변경 2 — adapter: io.json 기록
- adapter에 IO 스냅샷 상태 보관(미연결/미관측은 None), 각 값에 관측 시각 동반:
  - EZIO: `_ezio_inputs`, `_ezio_inputs_at`, `_ezio_outputs`, `_ezio_outputs_at`,
    `_ezio_connected`, `_ezio_board`, `_ezio_error`.
  - PIO: `_pio_connected`, `_pio_last_inputs`, `_pio_inputs_at`, `_pio_error`.
- **EZIO 입력(매 1초, 기존 경로 유지)**: `manage_tray_slot` 루프의
  `_read_ezio_input_bits()` 성공 시 `_ezio_inputs`/`_ezio_inputs_at=now`/
  `_ezio_connected=True`/`_ezio_error=""`, 실패 시 `_ezio_connected=False`/
  `_ezio_error=str(exc)`. **출력 조회를 이 read 경로에 붙이지 않는다**(리뷰 #1).
- **EZIO 출력·보드 + 파일 write(throttle ~2.5초)**: 마지막 write 후
  `_IO_WRITE_INTERVAL_SEC`(상수, 기본 2.5초) 경과 시에만 다음을 수행 —
  ① `get_output()`을 **입력과 분리된 별도 try/except**로 호출(성공 시
  `_ezio_outputs`/`_ezio_outputs_at=now`, 실패는 `_ezio_error`만 갱신, 입력 루프
  불방해), ② `_ezio_board`가 비어있으면 `get_board_info()` 1회 캐시,
  ③ `_write_io_file()`.
- **PIO 캐시 갱신 규칙**(리뷰 #3) — 단일 헬퍼 `_note_pio(*, connected=None,
  inputs=None, error=None)`로 갱신 시각과 함께 일괄 처리, 각 touchpoint에서 호출:
  - `_pio_init` connect+BC 성공 → `connected=True, error=""`.
  - `_pio_read_inputs` 성공 → `inputs=<8bit>, inputs_at=now, connected=True,
    error=""`.
  - `_pio_write_output` 성공 → `connected=True, error=""`.
  - `_pio_disconnect` → `connected=False`(최근 inputs는 **유지**).
  - `_execute_pio_action` except 분기 → `error=str(exc)`(연결 여부는 불확실 →
    변경 없음; pioInit 실패면 그 안에서 connected=False도 가능).
  - `pioScenario`는 finally에서 disconnect → **connected=False + inputs 잔존이
    정상**(UI에서 에러로 표기하지 않음).
  - 연속 시리얼 폴링은 추가하지 않는다(airshower/액션과 경합 회피).
- **configured 판정**(리뷰 #4): `_is_simulator()`면 io.json 미기록(파일 부재 →
  패널 안 뜸). 비-simulator일 때 — EZIO `configured = self._ezi_io is not None`,
  PIO `configured = bool(self.config.pio_config.pio_port)`.
- `_write_io_file()`: 위 스키마를 atomic write(`ipc_paths`). 예외는 state/health
  write와 동일하게 swallow + 1회 로깅.

**io.json 스키마** (`updated_at` = 파일 write 시각; 섹션별 `*_updated_at`로 신선도 판별)
```json
{
  "updated_at": 1750000000.0,
  "ezio": {
    "configured": true, "ip": "10.8.8.87", "port": 3002,
    "connected": true, "board": "EZI-IO ...",
    "inputs": [0,1,0,0, ...16], "inputs_updated_at": 1750000000.0,
    "outputs": [0,0, ...],       "outputs_updated_at": 1749999998.0,
    "error": ""
  },
  "pio": {
    "configured": true, "port": "/dev/ttyUSB0", "baudrate": 19200,
    "connected": false,
    "inputs": {"1":"off", ...}, "inputs_updated_at": 1749999000.0,
    "error": ""
  }
}
```
- `*_updated_at`는 해당 값을 **마지막으로 관측한 시각**(없으면 null). EZIO 입력은
  매 ~2.5초 갱신되지만 PIO 입력은 액션 실행 때만 갱신되므로, 렌더는 섹션별
  `*_updated_at` 나이로 "n초 전"/"stale"을 표시한다.

### 변경 3 — monitor: IoSnapshot + 파서 + reader
`core/monitor.py`:
- `IoSnapshot` dataclass(ezio/pio 하위 필드 + 섹션별 `*_updated_at` + top-level
  `updated_at`, 모두 Optional/기본값).
- 순수 함수 `extract_io(payload: dict) -> IoSnapshot`(schema-tolerant, 파일 무관
  단위테스트 — `extract_state`와 동일 스타일).
- **주 경로 = `FileMonitor.get_io() -> IoSnapshot`**: `ipc_paths.io_path(self.serial)`
  read → `extract_io`. 파일 없음/파손 시 빈 IoSnapshot. JIBOT WebUi는 이미
  `FileMonitor`+`UdsSender`로 와이어링되므로(`web/main.py`) 이 경로가 실제 사용처다.
- `MqttMonitor.get_io()`는 **비-JIBOT 호환용 빈 stub**(빈 IoSnapshot 반환)으로 충분
  (리뷰 #5).

### 변경 4 — server: 메인 페이지에 io 전달
`web/server.py` `_dispatch_get("/")`: 어댑터 루프에서 `io = monitor.get_io()`를
모아 **`io_by_key: dict[key→IoSnapshot]`** 으로 `adapter_list_page(rows, ...,
io_by_key=...)`에 전달. **`rows` 3-튜플 모양은 유지**(기존 호출부/테스트 불변).
stale 판정은 `updated_at` 나이로(state와 동일 기준) — 오래되면 "stale" 표시.
`get_io`가 없는 monitor(구버전/None)는 빈 맵으로 graceful.

### 변경 5 — render: EZIO/PIO 패널
`web/render.py` `adapter_list_page`: 어댑터 표 + 카메라 패널 아래에, io 데이터가
있는 어댑터별 `_command_group`/패널 스타일 섹션 추가.
- EZIO: 연결 status pill + IP·보드 + 입력 16비트(핀 인덱스별 ON/OFF, 기존 .status
  dot 재사용) + 출력 비트 + 에러. 입력/출력 옆에 `*_updated_at` 나이("n초 전").
- PIO: 연결 status pill + 포트·보드레이트 + 최근 입력 8비트(+ `inputs_updated_at`
  나이) + 에러.
- **"connected=false + 최근 inputs 있음"은 정상**(pioScenario finally disconnect):
  연결 pill은 "idle/미연결"(에러 색 아님)로, 입력은 마지막 관측값 + 시각으로 표시.
  PIO `error`가 실제로 있을 때만 에러 색.
- 섹션별 `*_updated_at`이 일정 임계(예: io write 주기의 수 배)보다 오래되면
  "stale" 표기(top-level `updated_at`이 아니라 섹션 시각 기준 — 리뷰 #2).
- `configured=false`/데이터 없음이면 패널 생략(simulator는 io.json 자체가 없음).
- 순수 렌더(외부 자원 없음), 기존 esc/`_pill` 헬퍼 재사용.

### 범위 밖
- PIO 연속 시리얼 폴링(경합 위험), WebUi에서 IO write/제어, EZIO/PIO를 MQTT/ACS로
  발행, 멀티로봇 레이아웃 고도화.

## 데이터 흐름

```
adapter manage_tray_slot loop (1s)          WebUi GET /
  ├ EZIO get_input (매 1초, 기존) ─────┐       ├ FileMonitor.get_io(serial)
  └ throttle ~2.5s:                    │         └ read io.json → extract_io → IoSnapshot
      ├ EZIO get_output (별도 try)     ├─ io.json ┤
      ├ board info (1회 캐시)          │ (tmpfs)   └ adapter_list_page → EZIO/PIO 패널
      └ _write_io_file ───────────────┘
PIO action path (_pio_init/read/write/disconnect)
  └ _note_pio(connected/inputs/error) → 다음 throttle write에 반영
```

## 테스트 (TDD)

- `extract_io`: 정상/누락/타입오류 payload → IoSnapshot 필드(섹션별 `*_updated_at`
  포함; 순수, 하드웨어 무관).
- `FileMonitor.get_io`: io.json 존재/부재/파손 → 스냅샷/빈값(tmpdir로 ipc_paths
  monkeypatch).
- adapter `_write_io_file`: 스냅샷 상태 → io.json 스키마 + 섹션별 시각 검증(tmpdir).
- adapter throttle: <interval 연속 호출 시 1회만 write; 출력 조회는 throttle
  시점에만(입력 read 경로엔 `get_output` 미호출 — fake EZIO 호출 카운트로 검증, 리뷰 #1).
- adapter PIO 캐시: `_note_pio` 규칙 — read 성공→inputs+connected, disconnect→
  connected=False+inputs 유지, except→error 갱신(리뷰 #3).
- adapter configured: simulator→io.json 미기록; `_ezi_io is None`→ezio.configured
  False(리뷰 #4).
- render: io 있는 어댑터 → EZIO/PIO 패널 마커, 입력 비트 ON/OFF; connected=False +
  inputs 있음 → 에러색 아님; 섹션 시각 오래됨 → "stale"; io 없는 어댑터 → 패널 생략.
- server `/`: io.json seed 후 GET → 패널 본문 포함(통합).

## 마이그레이션/운영

- 신규 파일 1개(io.json) — 기존 tmpfs 디렉터리 권한/정리(tmpfiles.d)에 편승.
- adapter 미배포/구버전: io.json 부재 → WebUi는 패널을 조용히 생략(하위 호환).
