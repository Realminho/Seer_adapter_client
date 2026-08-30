# Config 노출 확장 + Factsheet 조회/편집 페이지

Date: 2026-06-23
Status: Approved (design); plan reviewed 2026-06-23 — 5 findings folded into the plan
(agvActions single-source, factsheet byte-identity test, Module-5 util injection,
validate-path fix, WebUi scalar-only edit scope)
Scope: `unified-amr-adaptor` — jibot adapter, jibot-client, WebUi, config

## Problem / 배경

설정으로 뺄 수 있는 값 다수가 코드에 하드코딩돼 있다. `config.toml`은 이미
풍부하지만, JIBOT 하드웨어 오버레이(`jibot-config.toml`)는 `motion_rules` 하나뿐이고,
도킹·연결·WebUi·factsheet 영역에 운영자가 만질 수 없는 리터럴이 흩어져 있다.
추가로 운영자가 VDA5050 factsheet를 **조회/편집**할 WebUi 페이지가 없다.

목표: 하드코딩된 운영 노브를 **표준 TOML 키**로 노출하고(=`config.toml`에서 관리,
`get_config()` 로드, 기존 WebUi `/config` 인프라로 편집), factsheet를 config 기반으로
구조화해 새 `/factsheet` 페이지에서 보고 고친다.

선행 설계: [2026-06-22-jibot-dock-approach-umdock-design.md](2026-06-22-jibot-dock-approach-umdock-design.md)
(motion_rules + approach + fail_timeout는 이미 구현됨). 본 설계의 Module 1은 그 위에
UmDock JSON 파라미터 10종을 config로 노출하는 확장이다.

## 결정 사항 (확정)

- **배치**: 운영자가 편집하는 모든 노브는 `config.toml`에 평범한 TOML 키로 둔다.
  `jibot-config.toml`은 선택적 JIBOT 하드웨어 오버레이(deep-merge)로 유지하며,
  하드웨어 특성값(도킹 기하, factsheet 차량 스펙)의 **권장값 예시를 주석**으로 둔다.
- **Module 5 범위**: 감사에서 나온 ~40개 하드코딩 값을 **전부 개별 키로** 노출한다
  (사용자 선택). 단 FMS 계약을 깨는 synthetic action ID/type은 노출하되 "변경 금지"
  경고 주석을 단다.
- **Factsheet 편집**: config 기반 구조화 편집. `[factsheet]` 스칼라 필드를 `/config`
  패턴(comment-preserving `rewrite_scalar`)으로 WebUi 폼에서 인라인 편집. 리스트
  (`localization_types` 등)·중첩 테이블은 WebUi 폼 대상이 아니며 `config.toml`을 직접
  편집(또는 TUI 앱의 `$EDITOR`)한다 — WebUi에는 전체 TOML 편집 라우트가 없다.
- **UmConnect 자격증명**: 기존 동작값(`user="test"`, `password="test"`,
  `device_type="pc"`) 기본 유지 + config화. config 미설정 시 기존 동작.

## Non-goals

- 0.2초 폴링을 단일 키로 통합하는 리팩터(개별 노출로 결정됨).
- jibot-client 프레임 프로토콜 상수(`$#`/`##`/`$~`/`#CMD#`/regex) 노출 — 프로토콜 스펙.
- 어댑터의 boot-once 로드 정책 변경(설정 반영엔 재시작 필요, 기존 그대로).
- 새 factsheet 필드의 ACS 측 소비 검증(어댑터는 발행만, ACS 동작은 별도).

---

## Module 1 — UmDock 접근 파라미터 노출

`jibot-client`는 이미 `UmDock`의 선택 파라미터 10종을 받는다
(`client.py` `COMMAND_OPTIONAL_PARAMS["UmDock"]`): `goal`,
`detect_charging_signal`, `dock_move_additional_dist`,
`dock_rotate_additional_angle`, `need_turn_around`, `need_heading`,
`use_avoid_area`, `disable_motor_secs`, `clearance_back_min`,
`clearance_front_min`. 그러나 어댑터의 4개 호출부는 전부 **bare** `um_dock()`이다
(`adapter_jibot.py:3004, 3133, 3530, 5246`). `None` 값은 클라이언트가 자동 strip하므로
config 미설정 시 bare 호출과 동일하다.

### 설계

- `config.py`에 `DockApproachParams` dataclass 신설(모든 필드 `Optional`, 기본 `None`):
  위 10개 키.
- `DockConfig`에 `approach_params: DockApproachParams = field(default_factory=...)` 추가.
  TOML 표현은 `[dock.approach_params]` 하위 테이블.
- `MotionRule`에 선택적 규칙별 오버라이드 `approach_params: Optional[dict]` 추가
  (규칙 인라인 테이블 안에서 지정 가능; 없으면 전역 `[dock.approach_params]` 사용).
- 어댑터에 `_dock_approach_params(node_id) -> dict` 헬퍼: 규칙별 → 전역 순으로 병합,
  `None` 제거 후 dict 반환. 4개 호출부를 `await self._vehicle.um_dock(**params)`로 변경.
- `jibot-config.toml`에 `[dock.approach_params]` 권장값 예시를 주석으로 추가.

### 영향 파일

- `adaptor/config/config.py` (dataclass)
- `adaptor/adapter_jibot.py` (헬퍼 + 4개 호출부)
- `adaptor/config/config.toml`, `adaptor/config/jibot-config.toml` (예시)
- 테스트: `adaptor/tests/test_jibot_client_um_dock_params.py`(존재) 확장,
  신규 어댑터 호출부 테스트(`test_adapter_jibot_v3_order.py`의 mock `um_dock(**params)`로 검증).

### 위험

낮음. 기본 `None`이면 현행과 동일. 잘못된 기하값은 도킹 실패를 유발할 수 있으나
fail_timeout으로 reject되어 무한 DOCKING은 방지됨.

---

## Module 2 — jibot-client 연결 설정

### 설계

`JIBOT.__init__` 시그니처 확장(모두 기본값 = 현행 동작 보존):

```python
def __init__(self, robot_ip, robot_port=7273, config=None,
             charging_status="charging", recorder=None,
             user="test", password="test", device_type="pc",
             command_timeout=3.0, recv_buffer_bytes=32768,
             status_log_interval_sec=5.0, battery_log_interval_sec=30.0):
```

- `um_connect()`의 하드코딩 로그인 필드(`client.py:757-759`)를 위 인자로 대체.
- `receive_data_from_server()`의 `32768`(`:1013`), 로그 throttle(`:1220`, `:1252`),
  명령 기본 타임아웃(`send_command_and_wait`/`wait_for_response` 기본 3.0)을 인자화.
- 어댑터의 `JIBOT(...)` 생성부에서 config 값으로 주입.

### Config

`config.toml`에 새 `[jibot_client]` 섹션:

```toml
[jibot_client]
user = "test"
password = "test"
device_type = "pc"
command_timeout_sec = 3.0
recv_buffer_bytes = 32768
status_log_interval_sec = 5.0
battery_log_interval_sec = 30.0
```

`config.py`에 `JibotClientConfig` dataclass + `get_config()` 파싱 + `Config`에 추가.

### 영향 파일

- `jibot-client/src/jibot_client/client.py` (생성자 + um_connect + 상수 인자화)
- `adaptor/config/config.py`, `config.toml`
- 어댑터의 JIBOT 생성부 (`adapter_jibot.py` / `main.py`)
- 테스트: 신규 `test_jibot_client_connection_config.py` (um_connect가 주입값을 보냄,
  미설정 시 기본값), `test_config.py` 확장.

### 위험

자격증명 노출은 보안 민감 → `web-credentials.toml`처럼 평문 주의 주석. 기본값 유지로
동작 호환. recorder가 이미 password/token redaction을 하므로 로그 유출은 방지됨.

---

## Module 3 — WebUiConfig 스키마화

현재 `config.toml`의 `[web_ui]`는 dataclass 없이 `web/main.py:65`에서 raw dict로 읽힌다.
서버/렌더의 타임아웃·한도·정책은 하드코딩이다.

### 설계

`config.py`에 `WebUiConfig` dataclass 신설 + `get_config()` 파싱 + `Config`에 추가.
`web/main.py`는 raw dict 대신 `WebUiConfig`를 사용, `WebUi` 생성자에 전달, 하드코딩 제거.

필드(현 동작값 = 기본값):

| 키 | 기본 | 출처 |
|---|---|---|
| `enabled` | false | main.py:65 |
| `host` | "127.0.0.1" | main.py:69 |
| `port` | 8090 | main.py:70 |
| `credentials_path` | "config/web-credentials.toml" | main.py:71 |
| `http_request_timeout_sec` | 10.0 | server.py:222 |
| `shutdown_timeout_sec` | 2.0 | server.py:129 |
| `log_read_timeout_sec` | 5.0 | server.py:171 |
| `state_stale_threshold_sec` | 15.0 | server.py:35 (`_MQTT_STATE_STALE_SEC`) |
| `max_post_body_bytes` | 65536 | server.py:36 (`_MAX_BODY`) |
| `page_default_refresh_sec` | 5 | render.py:534/596 |
| `test_output_poll_sec` | 2 | render.py:1007 |
| `password_min_length` | 12 | credentials.py:12 (`_MIN_LEN`) |
| `password_forbidden_tokens` | ["set-me","changeme","password","admin"] | credentials.py:13 |
| `camera_default_port` | 8080 | server.py:189 |

`credentials.load_credentials()`는 `min_length`/`forbidden_tokens`를 인자로 받도록 확장.
`manual_drive_repeat_interval_ms`(render.py:828의 300)은 기존 `manual_control.heartbeat_ms`에서
파생하도록 통일(중복 노브 신설 안 함).

### 영향 파일

- `adaptor/config/config.py`, `config.toml` ([web_ui] 확장)
- `adaptor/web/main.py`, `server.py`, `render.py`, `credentials.py`
- 테스트: 신규 `test_web_ui_config.py`, `test_configio.py` 확장.

### 위험

중간. WebUi 시작 경로를 건드림 → 기본값을 현 리터럴과 동일하게 두고 회귀 테스트.

---

## Module 4 — `[factsheet]` config + `/factsheet` 페이지 (신규)

`_build_factsheet()`(`adapter_jibot.py:2055`)는 config 일부 + 하드코딩 상수로 factsheet를
만든다. 하드코딩 필드를 `[factsheet]`로 노출하고, `/config`와 같은 방식의 편집 페이지를 추가한다.

### 4a. `[factsheet]` config

`config.py`에 `FactsheetConfig` dataclass(현 하드코딩 = 기본값):

```toml
[factsheet]
series_name = "JIBOT"               # typeSpecification.seriesName
agv_kinematic = "DIFF"
agv_class = "CARRIER"
localization_types = ["NATURAL"]
navigation_types = ["AUTONOMOUS"]
coordinate_unit_position = "mm"     # coordinateUnits.position
coordinate_unit_orientation = "deg"
speed_min = 0.0                     # physicalParameters.speedMin
min_order_interval_sec = 1.0        # protocolLimits.timing.minOrderInterval
# 선택: geometry (현재 비어있음). 미설정 시 빈 객체 유지.
# length_m / width_m / height_m -> physicalParameters
```

`_build_factsheet()`를 `[factsheet]`에서 읽도록 변경.
`serialNumber/manufacturer/version`(=`[vehicle]`), `speedMax`(=`settings.speed`),
`minStateInterval`(=`settings.state_publish_delay`), `videoStreams`(=`[video]`),
`loadPositions`(=tray pins)는 기존대로 파생(중복 노브 안 만듦).

### 4b. `/factsheet` WebUi 페이지

- 라우팅: `server.py`에 GET `/factsheet`(조회+편집 폼), POST `/factsheet`(저장).
  기존 `/config`(server.py:394 GET / 434 POST) 패턴 그대로.
- 조회: 어댑터가 발행할 factsheet를 `[factsheet]` config + 파생값으로 **렌더링한 JSON**을
  read-only로 표시(어댑터 라이브 발행과 동일 함수 로직). WebUi는 별도 프로세스이므로
  `_build_factsheet`의 순수 부분을 신규 `adaptor/core/factsheet.py`의 `build_factsheet(config)`로
  추출해 어댑터·WebUi 양쪽이 쓰게 한다(타임스탬프/headerId 같은 런타임 값은 호출부에서 주입).
- 편집: `[factsheet]` 스칼라 필드만 `configio.HOT_FIELDS`와 동일 방식으로 WebUi 폼에서
  인라인 편집(comment-preserving). 리스트(`localization_types`/`navigation_types`)는
  WebUi 폼 대상이 아니며 `config.toml` 직접 편집(또는 TUI)으로 다룬다. 단, read-only
  JSON 미리보기에는 리스트 포함 전체 factsheet가 표시된다.
- 저장 후 "어댑터 재시작 후 적용 + factsheetRequest로 재발행" 안내(`/config`와 동일 톤).

### 영향 파일

- `adaptor/config/config.py`, `config.toml`
- `adaptor/adapter_jibot.py` (`_build_factsheet` → 공유 빌더 사용)
- `adaptor/core/configio.py` (factsheet 필드용 편집 지원; 필요시 섹션 인자화)
- `adaptor/core/factsheet.py` 신규 — `build_factsheet(config)` 순수 함수
- `adaptor/web/server.py`, `render.py` (페이지)
- 테스트: `test_factsheet_config.py`(빌더가 config 반영), `test_configio.py`,
  WebUi 라우팅 테스트.

### 위험

중간. factsheet는 ACS가 capability 발견에 쓰는 retained 메시지. 기본값을 현 하드코딩과
**완전히 동일**하게 유지하면 발행 바이트가 변하지 않음(회귀 테스트로 고정).

---

## Module 5 — 어댑터 튜닝 노브 전수 노출

감사에서 나온 하드코딩 값을 개별 TOML 키로 노출한다. 논리 그룹별 섹션:

### 5a. `[settings]` 추가
- `acs_cmd_subscribe_interval_sec` = 1.0 (adapter_jibot.py:323)
- `tray_slot_poll_interval_sec` = 1.0 (:390)
- `node_position_poll_interval_sec` = 0.2 (:3294)
- `standstill_poll_interval_sec` = 0.1 (:5574)
- `adapter_loop_sleep_sec` = 1.0 (main.py:770/774)
- `jibot_command_default_timeout_sec` = 3.0 (:2875)
- `jibot_ack_timeout_min_sec` = 0.1 (:4867) — **RISKY**: 명령 deadline 하한, 주석 경고
- `node_unreached_delay_sec` = 1.0 (:3342) — **RISKY**: 도착판정 타이밍, 주석 경고

### 5b. `[dock]` 추가
- `dock_wait_poll_interval_sec` = 0.2 (:3211)
- `dock_approach_poll_interval_sec` = 0.2 (:3372)
- `charging_start_poll_interval_sec` = 0.2 (:5353)
- `charging_stop_poll_interval_sec` = 0.2 (:5341)
- `dock_approach_unreached_delay_sec` = 1.0 (:3405) — **RISKY**: 주석 경고

### 5c. `[ezi]` 추가
- `motor_test_delay_sec` = 2.0 (main.py:745-754)
- `ezi_motor_poll_interval_sec` = 0.1 (utils/ezi_motor.py)

### 5d. `[sound_settings]` 추가
- `sound_test_duration_sec` = 10.0 (:2046)
- `player_wait_timeout_sec` = 1.0 (utils/sound.py:83)
- `pactl_timeout_sec` = 2.0 (utils/sound.py:92)

### 5e. 새 `[pio_advanced]`
- `call_poll_interval_sec` = 0.05 (:4311/4410)
- `connect_delay_sec` = 0.5 (utils/pio.py:32)
- `read_frame_poll_sec` = 0.05 (utils/pio.py:80)
- `socket_timeout_sec` = 0.2 (utils/pio.py:14)
- `init_default_timeout_sec` = 2.0 (:4146)
- `read_default_timeout_sec` = 2.0 (:4185)
- `scenario_default_timeout_sec` = 2.0 (:4200)
- `write_output_timeout_sec` = 2.0 (:4189)
- `read_frames_wait_sec` = 2.0 (utils/pio.py:65)
- `send_wait_sec` = 2.0 (utils/pio.py:88-100)

### 5f. `[charge_circuit]` 추가
- `terminate_timeout_sec` = 3.0 (utils/charge_circuit.py:132)
- `off_timeout_sec` = 5.0 (utils/charge_circuit.py:138)

### 5g. 새 `[air_shower_pio]` 추가
- `poll_interval_sec` = 0.2 (utils/airshower.py)

### 5h. 새 `[internal_actions]` — **변경 금지 경고**
- `docking_status_action_id` = "__jibot_docking__" (:2044)
- `docking_status_action_type` = "dock" (:2045)
- FMS가 학습한 action 계약. 노출하되 "기존 배포 호환 위해 변경 금지" 주석.

각 dataclass 필드는 기본값 = 현행 리터럴. 어댑터/유틸의 해당 리터럴을 `getattr(config…, key, default)`로 치환.

### 영향 파일

- `adaptor/config/config.py` (`Settings`/`DockConfig`/`EziConfig`/`SoundSettings`/
  `ChargeCircuitConfig`/`AirShowerPioConfig` 확장 + 신규 `PioAdvancedConfig`,
  `InternalActionsConfig`)
- `adaptor/adapter_jibot.py`, `adaptor/main.py`, `adaptor/utils/*.py`
- `config.toml` (그룹별 키 + 주석)
- 테스트: `test_config.py` 확장 + 각 유틸 단위 테스트.

### 위험

대부분 낮음(폴링/타임아웃). RISKY 표시 3종은 동작 영향 → 기본값 고정 + 경고 주석 +
회귀 테스트. config 비대화는 그룹 섹션 + 주석으로 가독성 관리.

---

## 공통: configio / WebUi 편집 인프라

- `configio.rewrite_scalar`는 `section.key` 플랫 스칼라만 지원. WebUi에는 전체 TOML
  편집 라우트가 없으므로(그 경로는 TUI 앱), 신규 중첩(`[dock.approach_params]`,
  `[pio_advanced]`)·리스트 노브는 `config.toml`을 직접 편집(또는 TUI `$EDITOR`)한다.
- `/config` `HOT_FIELDS`는 현행 유지(인플레 방지). 새 ~40개 노브는 WebUi 폼에 추가하지
  않고 `config.toml` 직접 편집으로 관리한다(모두 한 파일에서 관리된다는 목표는 유지).
- `/factsheet`는 `[factsheet]` 스칼라 전용 인라인 편집 + 전체 factsheet JSON 미리보기.
- 모든 저장은 검증(`validate_on_disk(path)` → 편집한 그 파일을 `get_config(path)`로
  재파싱; 현재 path 무시 버그도 수정) 후 "재시작 필요" 안내.

## 테스트 전략

- 단위: 각 dataclass 기본값/파싱(`test_config.py`), configio 편집/검증.
- 동작 보존: factsheet 빌더가 config 기본값에서 **현행과 동일 JSON** 생성(회귀 고정).
- 클라이언트: um_connect 자격증명 주입, um_dock 파라미터 전달(기존 테스트 확장).
- WebUi: `/factsheet` GET 200 + 폼, POST 저장→리다이렉트.
- 기존 테스트 스위트 전체 green 유지.

## 단계 (writing-plans에서 상세화)

1. Module 1 (UmDock 파라미터) — 선행 spec 확장, 위험 낮음.
2. Module 5 (튜닝 노브) — 기계적, dataclass 확장 중심.
3. Module 2 (client 연결) — 자격증명/상수.
4. Module 3 (WebUiConfig).
5. Module 4 (factsheet config + 페이지) — 공유 빌더 추출 포함.
