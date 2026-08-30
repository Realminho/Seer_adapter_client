# JIBOT 수동 컨트롤 (조그 + 거리이동) — 설계

## 문제 / 목표

JIBOT 프로그램(JManager/HMI)에는 **화살표키 조그**가 있어 누르면 즉시 움직이고
떼면 즉시 멈춘다. 운영자가 같은 방식으로 로봇을 **WebUI에서 직접 수동 운전**할 수
있게 한다. 두 가지 수동 이동 메커니즘을 추가한다:

1. **조그(연속 속도, 덱맨)** — 화살표키/화면 패드를 누르는 동안 `UmDrive`로 주행,
   떼면 `UmStop`. JIBOT 프로그램의 화살표키와 동일한 UX. **핵심 기능.**
2. **거리 이동(상대 거리)** — `distance`(±mm)·`speed`를 입력해 1회 이동하거나
   고정 스텝 버튼으로 nudge. 내부적으로 `cmd:"move"` **라우트 스텝**을 실행한다.

두 메커니즘 모두 **WebUI 버튼/키 + VDA5050 instant action** 양쪽으로 노출한다.
instant action이 본체이고 WebUI는 그 위에 얹는 프론트엔드다(ACS/FM이 직접 보내도
동일 동작). JIBOT 어댑터 전용.

## 배경 — 현재 코드 상태

> **현재 미구현(전부 신규).** `manualDrive`/`manualMove`/`manualStop`/`enableMotor`는
> 오늘 코드 어디에도 없다: registry는 클램프까지만(`core/registry.py:187`),
> factsheet `SUPPORTED_INSTANT_ACTIONS`에 없음(`adapter_jibot.py:1993`), 디스패치 체인에
> 분기 없음(`:3658`), WebUI도 `action`/`goto` 라우트만(`web/server.py:392`). 이 문서는
> "현재 구현 검토"가 아니라 **그린필드 설계안**이다.

- **연속 속도/정지/모터 명령은 클라이언트에 이미 있다.**
  `jibot-client/src/jibot_client/client.py`:
  - `um_drive(trans, rot, speed, lat, gap=-1)` (`:757`) → wire `{"#CMD#":"UmDrive", trans, rot, speed, lat}`. 연속 속도 명령(거리 개념 없음).
  - `um_stop()` (`:924`), `um_set_motor(flag)` (`:906`), `enable_motor()`/`disable_motor()` (`:699`/`:696`).
- **`move`는 최상위 명령이 아니라 라우트 스텝이다.** `COMMAND_SPECS`(`:27`)에 `UmMove`가
  없다. `move`는 `goto`/`dock`/`drive`/`curve`처럼 라우트 안의 `cmd`로만 실행된다.
  관측 예시(`jibot-client/jibot-params/222/routes_ali.json`):
  ```json
  "move": { "a": { "cmd":"move", "distance":-1800, "speed":100,
                   "flag":1, "io":1, "obs_avoid_dist":1000,
                   "side_avoid_dist":50, "use_io":false, "note":1 } }
  ```
  라우트 실행 래퍼는 존재: `um_routes(routes, key, id)` (`:894`),
  `um_set_routes(routes)` (`:915`), `um_scheduler_this(routes, key, content)` (`:900`),
  `call_routes(name, key, id)` (`:586`). **정확한 거리/속도 가변 실행 경로는 미검증**
  (아래 "리스크" 참고).
- **instant action 인프라가 있다.** `SUPPORTED_INSTANT_ACTIONS`(`adapter_jibot.py:1993`)가
  factsheet `agvActions`(`_build_factsheet`, `:2050`)를 만들고, 수신 instant action은
  `instant_actions_accept_procedure`의 `action.action_type ==` 체인(`:3658`~`:3726`)으로
  디스패치된다. 클램프 핸들러(`_handle_clamp_instant_action`, `:3750`)가 구조화 파라미터를
  받아 `_run_on_adapter_loop(_run)`(`:254`~`:266`)로 어댑터 asyncio 루프에서 async 실행하는
  선례다. `_loop`(`:95`, `:303`)이 어댑터 이벤트 루프.
- **registry**에 액션 노출 메타데이터가 있다. `InstantAction(action_type, label, motion)`
  (`core/registry.py:41`), `_JIBOT_INSTANT_ACTIONS` 튜플(`:187`). `motion=True`면 WebUI에서
  확인 체크박스 필수.
- **WebUI 제어 경로.** `_post_action`(`web/server.py:547`)이 `spec.instant_actions`에서 액션을
  찾아 `control.build_instant_actions(...)`(`:564`)로 payload를 만들고
  `self._senders[key].send(payload, meta)`로 보낸다. JIBOT은 `UdsSender`(`web/senders.py:26`)가
  `control.sock`에 instant action을 전달하고 `(delivered, text)`를 반환(어댑터 죽었으면
  fail-closed "not delivered"). **현재 `_post_action`은 action_parameters를 싣지 않는다** →
  파라미터 전달이 필요.
- **WebUI는 서버렌더 무-JS 원칙**(`docs/guide/web-ui.md`). 거리이동/nudge는 이 원칙대로 풀폼
  POST로 가능하지만, **조그 덱맨만 최소 inline JS**(keydown/keyup + ~300ms fetch)가 필요하다.
- **관련 메모리.** EZI 드라이브는 servo OFF면 move를 조용히 드롭한다(클램프 선례) → 모터
  OFF면 수동 이동도 무시될 수 있어 **모터 상태 표시 + 수동 ON 버튼**으로 보완(자동 인에이블은
  미채택). `UmGoto/UmDrive/UmStop`은 fire-and-forget(ack 없음) — 수용은 텔레메트리, 누락이
  거부는 아님.

## 범위 (확정된 결정)

| # | 결정 | 비고 |
|---|------|------|
| 1 | 조그(UmDrive 덱맨) + 거리이동(move) **둘 다** | 사용자 확정 |
| 2 | WebUI + VDA5050 instant action **양쪽** 노출 | instant action이 본체 |
| 3 | 조작: 조그=화살표키+화면패드 hold-to-move, 거리=입력+Go / 고정스텝 nudge | |
| 4 | 덱맨 안전: ~300ms heartbeat 재발사 + **어댑터 워치독 자동 UmStop** | keyup 유실/탭전환/끊김 대비 |
| 5 | 게이팅: JIBOT 전용 + **다층 방어**(WebUI Arm 토글 + 서버 armed/confirm 검증 + 어댑터 `manual_control.enabled`·busy/estop 정책) | Arm 단독은 ACS/직접 fetch 우회 가능(검토 #4) |
| 6 | 모터: 자동 인에이블 안 함 → **상태 표시 + 수동 ON 버튼**(enableMotor) | |
| 7 | **busy/오더 중 정책(검토 #3 — 확정: 거부)**: loading/unloading work 또는 활성 오더 중이면 manualDrive/manualMove **거부**(자동·수동 동시 속도 충돌 방지). **manualStop은 항상 허용.** | 기존 work-busy 차단(`:3642`)과 일관; 신규 manual* 액션을 `_is_motion_instant_action`에 포함 |
| 8 | **WebUI 오더 취소(신규 요구)**: manual 페이지에 오더 상태 표시 + "오더 취소" 버튼(기존 `cancelOrder` 재사용). 운영자가 오더 취소 후 수동 전환. | cancelOrder는 기존(registry:193/`:5305`) — UI 노출만 |
| — | **제외**: operatingMode(MANUAL/SERVICE) 실로봇 반영 | 별개 작업 |

## 컴포넌트

### A. 클라이언트 — `jibot-client/src/jibot_client/client.py`

- 재사용: `um_drive`, `um_stop`, `um_set_motor`(+`enable_motor`).
- **신규** `move_distance(distance, speed, *, obs_avoid_dist=1000, side_avoid_dist=50,
  flag=1, io=1, use_io=False, gap=-1)` — `cmd:"move"` 한 스텝짜리 ad-hoc 라우트를 구성해
  실행하는 고수준 헬퍼. 기본값은 routes_ali.json 관측값. 실행은 검증된 경로 사용(B 리스크).
- (선택) `move_distance` 단위테스트를 위해 라우트 dict 빌더를 순수 함수로 분리.

### B. 어댑터 — `adapter_jibot.py` + `core/registry.py`

- **registry `_JIBOT_INSTANT_ACTIONS`에 추가** (모두 `motion=True`):
  - `InstantAction("manualDrive", "Manual drive (jog)", motion=True)`
  - `InstantAction("manualMove", "Manual move (distance)", motion=True)`
  - `InstantAction("manualStop", "Manual stop", motion=True)`
  - `InstantAction("enableMotor", "Enable motor", motion=True)`
- **`SUPPORTED_INSTANT_ACTIONS`**(factsheet)에도 같은 actionType 추가 → eq/ACS 발견 가능.
- **디스패치 체인**(`instant_actions_accept_procedure`)에 분기 추가:
  - `manualDrive` → `_handle_manual_drive_instant_action`
  - `manualMove` → `_handle_manual_move_instant_action`
  - `manualStop` → `_handle_manual_stop_instant_action`
  - `enableMotor` → `_handle_enable_motor_instant_action`
- **파라미터**(VDA5050 `action_parameters` 키/값):
  - `manualDrive`: `trans`(전진+/후진−), `rot`(좌+/우−), `speed`, **`lat`**(횡이동/모드 — `um_drive`
    4번째 인자, 기본 `drive_lat=0`). `um_drive(trans, rot, speed, lat)`는 4개를 모두 요구하므로
    `lat`를 빠뜨리면 런타임 오류 → config 기본값으로 항상 채운다(검토 #2).
  - `manualMove`: `distance`(±mm), `speed`. 미지정 시 config 기본값.
- **핸들러 패턴**: 클램프와 동일하게 `_update_instant_action_status(RUNNING)` →
  `_run_on_adapter_loop(_run)`에서 async 실행 → FINISHED/FAILED. fire-and-forget이라
  "전송 성공"을 FINISHED로 본다.
- **어댑터 측 게이팅(검토 #3·#4)** — WebUI Arm/서버 검증을 우회하는 ACS·직접 경로까지 막는
  최종 방어선:
  - `manual_control.enabled=false`면 manualDrive/manualMove/enableMotor를 FAILED로 거부.
  - **`_is_motion_instant_action`에 `manualDrive`/`manualMove` 추가** → 기존 work-busy 차단
    (`:3642`)이 그대로 적용(loading/unloading work 중 거부).
  - **활성 오더 중 거부**: 오더 워커가 노드를 실행 중이면 manualDrive/manualMove를 FAILED로
    거부(자동·수동 동시 속도 충돌 방지). 운영자는 `startPause`/`cancelOrder` 선행.
  - **`manualStop`은 어떤 상태에서도 항상 허용**(긴급 정지 경로). work-busy 차단 분기보다
    먼저 처리하거나 모션 차단에서 예외 처리.
- **덱맨 워치독** (`_handle_manual_drive_instant_action` 내부):
  - `manualDrive` 수신마다 `um_drive(...)` 발사 + 워치독 타이머(`watchdog_ms`, 기본 800ms) 리셋.
  - 타이머 만료 시 자동 `um_stop()` → heartbeat 끊기면(keyup 유실/탭닫힘/끊김) 안전 정지.
  - `manualStop` 또는 zero-velocity 수신 시 워치독 취소 + 즉시 `um_stop()`.
  - 구현: 어댑터 루프의 단일 `asyncio.Task`/`call_later` 핸들을 잡고, 새 drive마다
    이전 핸들 취소 후 재무장. 상태는 어댑터 인스턴스 필드(`_manual_drive_deadline` 등).
- **heartbeat를 velocity stream으로 취급(검토 #6)**: 조그는 VDA5050 액션이라기보다 속도 명령
  스트림이다. manualDrive는 **속도 setpoint 갱신 + 워치독 재무장**만 하고, 매 비트마다 새
  instantActionState를 쌓거나 `request_state_publish`를 강제하지 않는다(터미널 상태는 기존
  `_clear_terminal_instant_action_states`로 정리). 결과적으로 300ms 스트림이 action state/
  state-publish를 과도하게 churn하지 않는다. ACS가 보내는 manualDrive도 동일 경로로 합류.

### C. WebUI — `web/server.py` + `web/render.py` (+ `web/main.py` 와이어링)

- **신규 페이지** `GET /adapter/<key>/manual` (JIBOT 전용; control 페이지에 링크,
  비-JIBOT엔 미노출).
  - **Arm 토글** 체크박스 — 끄면 모든 조작 비활성(클라이언트 측 게이팅).
  - **조그 패드**: ▲(전진)▼(후진)◀▶(회전) 버튼 + **키보드 화살표키**. 누르는 동안
    inline JS가 `manualDrive`를 ~300ms마다 fetch, 떼면 `manualStop`. (JIBOT 프로그램 UX 동일.)
  - **거리 이동**: `distance`(±mm)·`speed` 입력 + Go → `manualMove` (무-JS 풀폼 POST).
  - **방향 nudge**: 전진/후진 버튼(config 고정 스텝) → `manualMove` (무-JS 풀폼 POST).
  - **Stop** 버튼 → `manualStop`.
  - **모터 상태 표시 + 모터 ON 버튼** → `enableMotor`.
  - **오더 상태 + 오더 취소(신규 요구, 검토 #3 후속)**: 활성 오더/work 상태를 표시해
    "왜 수동이 막히는지" 운영자가 즉시 인지. **"오더 취소" 버튼**은 **기존 `cancelOrder`
    instant action 재사용**(registry:193 / 핸들러 `adapter_jibot.py:5305`, 신규 백엔드 불필요) —
    order worker 취소·큐 클리어·`stop_motion`·`self.order=None`까지 수행한다. 취소되면
    "활성 오더 중 거부" 게이트가 풀려 수동 주행 가능. **단 loading/unloading work
    (`_work_in_progress`)는 `cancelOrder`가 해제하지 않음** → 그 경우 `stopLoading`/`stopUnloading`로
    별도 해제(기존 액션, 필요 시 manual 페이지에도 노출). 운영자 흐름: 오더 취소 → (work면 work 정지) → 수동 운전.
- **파라미터 전달(검토 #5)**: `build_instant_actions(parameters=...)`는 **이미 지원**
  (`core/control.py:25`). `_post_action`(`web/server.py:564`)이 현재 params를 안 넘기는 것뿐 →
  form에서 `distance`/`speed`(거리이동) 등을 읽어 `parameters`로 전달하도록 `_post_action`만 보강.
  builder 변경 불필요.
- **서버 측 게이팅(검토 #4)**: manual 액션 POST는 CSRF + motion 확인(`_confirmed`, 기존 패턴) +
  **armed 플래그** 검증. armed/confirm 누락 시 거부. 클라이언트 Arm 토글만 믿지 않는다.
- **신규 경량 엔드포인트** `POST /adapter/<key>/manual` — 덱맨 heartbeat 전용. 작은 JSON
  (`{"delivered":true}`) 반환(풀페이지 리로드 없음), CSRF + armed 검증, `manualDrive`/`manualStop`
  instant action을 control.sock으로 전송. 기존 `UdsSender` 재사용.
- **감사 정책(검토 #6)**: 일반 제어/액션은 기존대로 `_audit`. 단 **덱맨 heartbeat는 매 비트를
  감사하지 않는다** — 조그 **시작/방향 변경/정지**만 감사 1줄. 300ms마다 audit 줄·flash·redirect를
  쌓지 않는다.

### D. 설정 — `config.toml` `[manual_control]` + `config/config.py`

```toml
[manual_control]
enabled = true            # 끄면 WebUI manual 페이지/액션 비노출
drive_trans = 200         # 조그 전진/후진 속도 성분 (UmDrive trans)
drive_rot = 30            # 조그 회전 속도 성분 (UmDrive rot)
drive_speed = 200         # 조그 speed
drive_lat = 0             # UmDrive 4번째 인자(lat) 기본값 — 항상 채움(검토 #2)
heartbeat_ms = 300        # WebUI 덱맨 재발사 주기
watchdog_ms = 800         # 어댑터 자동정지 타임아웃 (> heartbeat)
step_distance_mm = 500    # nudge 1클릭 거리
default_move_speed = 100  # 거리이동 기본 speed
move_obs_avoid_dist = 1000
move_side_avoid_dist = 50
```

`ManualControlSettings` 데이터클래스로 파싱. 미설정 시 위 기본값.

## 데이터 흐름

```
[조그 덱맨]
키다운/버튼누름 → inline JS ─POST /adapter/<key>/manual {manualDrive,trans,rot,speed,lat}→
  server(CSRF+armed 검증) → UdsSender → control.sock → 어댑터 게이팅(enabled·busy·order) →
    _handle_manual_drive → um_drive(trans,rot,speed,lat) + 워치독 재무장 → urobot UmDrive
키업/버튼뗌    → inline JS ─POST /adapter/<key>/manual {manualStop}→ … → um_stop()  [항상 허용]
(heartbeat 끊김) → 어댑터 워치독 만료 → um_stop()  [안전망]
(busy/오더 중)  → 어댑터가 manualDrive/manualMove FAILED 거부 → 운영자 startPause/cancelOrder 선행

[거리 이동]
distance/speed 입력 + Go → 풀폼 POST /adapter/<key>/action {manualMove,distance,speed} →
  server _post_action → UdsSender → control.sock → 어댑터 _handle_manual_move →
    move_distance(...) → UmSetRoutes/UmRoutes(또는 UmSchedulerThis) → urobot move 라우트

[ACS 경로] eq/FM → MQTT instant action(manualDrive/manualMove/manualStop) → 동일 핸들러 합류
```

## 에러 처리

- **어댑터 다운**: `UdsSender`가 fail-closed "not delivered" → WebUI 플래시 err. 조그는
  heartbeat가 안 가므로 로봇은 워치독으로 정지(이미 정지면 무동작).
- **모터 OFF**: move/drive가 로봇에서 드롭될 수 있음 → 페이지에 모터 상태 경고 + ON 버튼.
  자동 인에이블 안 함(미채택).
- **fire-and-forget**: UmDrive/UmStop/move는 ack 없음 → 핸들러는 "전송됨"을 FINISHED로 본다.
  실제 수용/정지는 텔레메트리(position) 변화로 운영자가 확인.
- **워치독 우선**: `watchdog_ms > heartbeat_ms` 불변식 유지(기본 800>300)로 정상 누름 중
  오동작 정지 방지.
- **busy/오더 중 거부**: loading/unloading work 또는 활성 오더 중 manualDrive/manualMove는
  FAILED(`result_description`에 사유) → 운영자가 startPause/cancelOrder로 중단 후 재시도.
  manualStop은 거부 대상에서 제외(항상 정지 가능).

## 테스트

- **클라이언트**: `move_distance` 라우트 dict 빌더/직렬화 단위테스트(distance/speed/opts 반영).
- **어댑터**:
  - 각 instant action 핸들러가 올바른 클라이언트 호출로 매핑되는지(fake client).
    특히 `manualDrive`가 `um_drive(trans, rot, speed, lat)` **4인자 모두**로 호출(lat 기본값 포함).
  - 워치독: drive 후 heartbeat 없으면 타임아웃에 `um_stop` 호출(fake clock/loop),
    drive 재수신 시 재무장, manualStop 시 즉시 정지·타이머 취소.
  - **게이팅**: `manual_control.enabled=false` 시 거부; loading/unloading work 중
    manualDrive/manualMove 거부(`_is_motion_instant_action` 포함 확인); 활성 오더 중 거부;
    **manualStop은 busy/오더 중에도 허용**.
  - **오더 취소 후 해제**: `cancelOrder`로 `self.order=None` 된 뒤 manualDrive가 더 이상
    거부되지 않는지(활성 오더 게이트 해제 확인).
  - factsheet `agvActions`에 새 actionType 노출 확인.
- **웹**(fake senders/specs 주입):
  - manual 페이지 렌더(JIBOT만, Arm 토글, 패드/입력/모터 영역).
  - `/adapter/<key>/manual` 경량 엔드포인트: CSRF + armed 검증·params·JSON 응답.
  - 서버 게이팅: armed/confirm 누락 시 거부(검토 #4).
  - `_post_action` params 전달(manualMove distance/speed) — builder 변경 없이 호출부만(검토 #5).
  - heartbeat 감사 정책: 시작/방향변경/정지만 audit, 매 비트 미감사(검토 #6).
  - manual 페이지에 오더 상태 표시 + "오더 취소" 버튼 렌더, POST 시 `cancelOrder` 전송.
  - 비-JIBOT엔 manual 링크/페이지 미노출.

## 구현 순서

1. config `[manual_control]`(`drive_lat` 포함) + `ManualControlSettings`.
2. 클라이언트 `move_distance` + 라우트 빌더 + 단위테스트.
3. 어댑터 핸들러 4종(`um_drive` 4인자) + 워치독 + **게이팅(enabled·`_is_motion_instant_action`
   포함·활성 오더 거부·manualStop 항상 허용)** + registry/factsheet 노출 + 테스트.
4. 웹 `_post_action` params 전달 + 경량 manual 엔드포인트(CSRF+armed) + 서버 게이팅.
5. 웹 manual 페이지 렌더(패드/입력/모터/Arm + 오더 상태·취소 버튼[기존 cancelOrder]) +
   inline JS(덱맨, heartbeat 감사 최소화) + 테스트.
6. `docs/guide/web-ui.md`에 manual 페이지 문서화.

## 범위 밖 (YAGNI)

- operatingMode(MANUAL/SERVICE) 실로봇 반영 — 별개 작업.
- 회전을 `move`/라우트로 구현(거리이동은 직선만; 회전은 조그 rot로 충분).
- **자동 모터 인에이블** — 미채택(수동 ON 버튼으로 대체).
- 게임패드/조이스틱 하드웨어, 속도 슬라이더 — 후속.
- (참고: "busy/오더 중 차단"은 검토 #3로 **범위 안**으로 이동 — 결정표 #7.)

## 리스크 / 검증 필요(실기)

- **거리/속도 가변 `move` 실행 경로** — `UmRoutes`는 미리 저장된 라우트를 name+key로
  트리거하는 것으로 보인다(`call_routes(name, key)`). 매번 다른 distance/speed를 주려면
  `UmSetRoutes`(라우트 업로드) → `UmRoutes`(트리거), 또는 `UmSchedulerThis(routes,key,content)`
  인라인 중 하나가 필요. **정확한 의미·동작은 벤더 문서/실기 확인 후 확정**하고, 동작하는
  경로로 `move_distance`를 구현(폴백 포함). 클라이언트 주석도 라우트 파라미터 규칙은
  "확인 필요"로 명시.
- **UmDrive 자체 타임아웃 여부** — urobot이 명령 미수신 시 자체 정지하는지 미확인. 어느
  쪽이든 본 설계(heartbeat + 어댑터 워치독 + 뗌 시 UmStop)로 안전.
- **IPC 경로 지연** — JIBOT 프로그램은 로봇 내부 직결이라 즉각적; 우리는
  WebUI→control.sock→어댑터→TCP라 LAN 지연 수십 ms 추가(허용 범위, heartbeat로 흡수).
- **안전 경계** — WebUI는 소프트웨어 게이트일 뿐 E-stop/안전 PLC를 대체하지 않는다
  (web-ui.md 명시 유지).
