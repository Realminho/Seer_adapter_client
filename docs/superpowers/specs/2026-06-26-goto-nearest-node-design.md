# 가장 가까운 노드로 이동(gotoNearestNode) — lastNodeId 채우기 설계

## 문제 / 목표

로봇이 어떤 맵 노드에서도 멀리 떨어져 있어(lastNodeId 캡처 임계치 밖)
`lastNodeId`가 **빈 상태**일 때, 운영자가 WebUI 버튼 한 번으로 **가장 가까운 노드로
UmGoto** 시키고, 도착하면 `lastNodeId`를 그 노드로 채우게 한다.

FMS/ACS는 `lastNodeId`를 오더 진행의 기준(reached-node / order-progress)으로 읽으므로,
부팅 직후·수동으로 밀려난 뒤·자유주행 후 등 "노드 위가 아닌 곳에 멈춘" 로봇은
`lastNodeId`가 비어 오더를 받지 못한다. 이 기능이 그 공백을 메운다.

> **핵심**: 이 기능의 본질은 "현 위치를 노드 단위로 확정(=lastNodeId 채우기)"이며,
> 그 수단이 "가장 가까운 노드로의 goto"다. 새 지도 시각화/route 그리기는 **범위 밖**
> (사용자 확정).

JIBOT 어댑터 전용. instant action이 본체이고 WebUI는 그 위에 얹는 프론트엔드
(ACS/FM이 직접 보내도 동일 동작).

## 배경 — 현재 코드 상태 (전부 재사용, 신규 액션 1개)

필요한 부품은 대부분 이미 있다. 신규는 **instant action 1종 + 핸들러 1개 + WebUI 버튼
1개 + config 1개 + `_post_action` confirm gate 1줄**.

- **최근접 노드 계산 — 있음.** `_find_nearest_node(x, y)`(`adapter_jibot.py:6495`)가
  `(node_id, sequence_id, distance)` 반환(맵 노드 우선, 없으면 오더 노드 폴백, **유클리드
  거리(hypot)**). 매 사이클 `_update_nearest_node_from_position`(`:6704`)이 `_nearest_node_id`/
  `_nearest_node_distance`를 갱신하고 `NEAREST_NODE` 텔레메트리(`state.information`,
  `infoType=NEAREST_NODE`, refs `nearestNodeId`/`gap`)로 발행한다(`:6574`~). 특정 노드까지의
  거리는 `_distance_to_node_id(node_id, position)`(`:6543`).
- **lastNodeId 공용 writer — 있음(단, 유일 writer는 아님).** `_set_last_node(node_id,
  sequence_id)`(`:6448`)가 `_last_node_id`/`_last_node_sequence_id`(init `:223`)와
  `state.last_node_id`를 함께 갱신. **신규 핸들러는 이 공용 writer를 사용**한다. 단 현재
  오더 노드 완료 경로 `_finalize_v3_node_step`(`:3704`)은 아직 필드를 **직접** 쓴다
  (이 작업의 통합 대상은 아님 — 표현만 정확히).
- **idle 캡처 — 있음(백스톱).** `_capture_idle_last_node`(`:6644`)가 `last_node_capture_mode`
  로 분기: `proximity`(`:6663`, **order-무관**, pose가 임계치 안이면 무조건 캡처),
  `settled`(`:6704`, 정지 AND 수동무관 AND order 비활성일 때만), `disabled`(`:6697`, 캡처 안 함).
  - **임계치**: `idle_last_node_reach_xy` → 헬퍼 `_idle_last_node_reach_xy()`(`:6438`).
  - **⚠ 실배포 config.toml(`:88-89`) 오버라이드: `idle_last_node_reach_xy = 100.0`,
    `last_node_capture_mode = "proximity"`** (config.py 기본값 500/settled가 아님). 즉 현장에서
    lastNodeId는 **노드 100mm 이내면 order-무관으로 자동 캡처**된다. 이 기능은 "100mm 밖이라
    자동으로 안 차는" 경우를 **운영자가 능동적으로 해결**하는 보완재.
  - **proximity는 `_manual_control_active`를 보지 않는다**(그 플래그는 `settled`에서만 효과).
    → 주행 중 캡처 억제를 `_manual_control_active`로 하려는 접근은 실배포(proximity)에서 무효.
- **노드 도착 reach zone(오더용, 별개 임계치) — 있음.** `_pose_in_reach_zone`(`:3599`),
  `_effective_reach_deviation_xy`(`:6426`) → `default_node_deviation_xy`(10) ×
  `reach_zone_scale`(20) = **200mm**. 이는 **오더 노드 완료 판정**용이며 lastNodeId 캡처용
  100mm와 다르다. **이 기능의 "도착=lastNodeId 설정"에는 캡처 임계치
  `_idle_last_node_reach_xy()`(100mm)를 쓴다**(결정 #4) — 오더 reach zone(200mm) 아님.
  `_wait_until_node_position_reached`(`:3610`)는 timeout이 없고 `node` 객체에 결합돼 있어
  instant action엔 직접 못 씀(결정 #5).
- **goto 송신 경로 — 있음.** 오더 노드 실행 `_execute_order_node_position`(`:3440`):
  시뮬 `goto_xyz(x,y,theta)`, 실로봇 `goto_point(node_id)`. 수용/거부 조기감지
  `_await_goto_ack(node)`(`:3480`, 텔레메트리 기반). 정지는 cancelOrder 경로의
  `stop_motion`/`um_stop` 재사용.
- **UmGoto는 fire-and-forget.** ack 없음 → 도착은 텔레메트리 position으로만 확인
  (메모리: `jibot-umgoto-fire-and-forget-no-ack`). "전송 성공 = 도착"이 아니라
  **pose가 노드 임계치 안에 들어옴 = 도착**.
- **instant action 인프라 — 있음.** 디스패치는 `instant_actions_accept_procedure`의
  `action.action_type ==` if/elif 체인(`:3948`~`:4042`). 최신 선례:
  `_handle_disable_motor_instant_action`(`:4285`, 파라미터 없는 액션 + 퀵컨트롤 버튼),
  `_handle_manual_move_instant_action`(`:4126`, 게이트 + `_run_on_adapter_loop(_run)` async
  실행). 상태전이는 `_update_instant_action_status(action_id, ActionStatus.X,
  result_description=...)`.
- **액션 노출 메타데이터 — 있음.**
  - factsheet **단일 소스 `core/factsheet.py:13 INSTANT_ACTION_TYPES`**; 어댑터는
    `adapter_jibot.py:2358`에서 `SUPPORTED_INSTANT_ACTIONS = INSTANT_ACTION_TYPES` **alias만**.
    → 신규 actionType은 **`core/factsheet.py`에 추가**(결정/순서에 반영).
  - registry `_JIBOT_INSTANT_ACTIONS`(`core/registry.py:187`),
    `InstantAction(action_type, label, motion)`.
- **활성 오더 감지 — 있음.** `order_worker_task`(`:111`); 실행중 판정은
  `order_worker_task is not None and not order_worker_task.done()`(이미 `:2569`/`:6052`에서
  cancel 경로가 쓰는 바로 그 체크).
- **WebUI 제어 경로 — 있음(단, motion 액션은 confirm 없음).** `_post_action`
  (`web/server.py:671`)이 `spec.instant_actions`에서 액션을 찾아 `control.build_instant_actions`
  로 payload 생성 → `UdsSender`가 `control.sock`으로 전달, 어댑터 다운 시 fail-closed.
  **현재 `_post_action`(`:683`)과 `_action_form`(`render.py:1059`)은 motion 액션도 confirm
  없이 단일 클릭으로 발행**(주석·`test_web_server.py:702`가 이 동작 고정). 단 **전용 Goto 폼
  `_post_goto`는 confirm을 요구**(두 정책 공존). → gotoNearestNode는 전용 confirm gate 필요
  (결정 #8).

## 범위 (확정된 결정)

| # | 결정 | 비고 |
|---|------|------|
| 1 | **gotoNearestNode 액션만** (지도 시각화/route 그리기 제외) | 사용자 확정 |
| 2 | 주행은 **UmGoto(node id)** — 로봇 자체 플래너가 경로/회피 처리 | 사용자 확정 |
| 3 | 어댑터가 **실행 시점 최신 pose**로 최근접 노드 해석(WebUI는 node id 안 보냄) | 결정적·최신 |
| 4 | 도착 판정 = **lastNodeId 캡처 임계치 `_idle_last_node_reach_xy()`(실배포 100mm)** | 오더 reach zone(200mm) 아님; 캡처와 동일 기준으로 정렬 |
| 5 | 무한 RUNNING 방지: **bounded 폴링 + `goto_nearest_timeout_sec`(기본 60s)** | `_wait_until_node_position_reached`는 timeout 없음→직접 폴링 |
| 6 | 도착 시 **`_set_last_node` 명시적 호출**(공용 writer) + `request_state_publish` | capture_mode 무관 보장; proximity 캡처(100mm)도 같은 노드로 수렴 |
| 7 | **타임아웃/실패(주행 후) 시 stop 전송**(`stop_motion`/`um_stop`) | 로봇이 계속 가 나중에 캡처를 바꾸는 것 차단 → FAILED 의미 정합 |
| 8 | **활성 오더 워커 실행중일 때만 거부**. idle·완료 후엔 허용 | 사용자 의도("일반적으론 거부 불필요, 타이밍만 주의") |
| 9 | WebUI 버튼은 **기존 Goto 그룹 옆**, 최근접 노드+gap 표시, **confirm 키 필요** | _post_action에 gotoNearestNode 전용 confirm gate 추가(사용자 확정) |
| — | `manual_control.enabled` 게이트 **안 함** | 일반 vehicle 명령(ACS도 사용); 기존 goto 폼과 동일 정책 |
| — | `_manual_control_active`로 캡처 억제 **안 함** | proximity가 그 플래그 무시 + 최근접 노드라 중간 churn 비현실적 |

## 동작 (핸들러 흐름)

`_handle_goto_nearest_node_instant_action(action)` — `manualMove` 핸들러 패턴 차용
(`_update_instant_action_status(RUNNING)` → `_run_on_adapter_loop(_run)`):

```
1. 가드(즉시 FAILED, result_description에 사유):
   - pose/localization 없음                → "not localized"
   - 맵 노드 없음(_map_nodes() 비어있음)    → "no map nodes"
   - 활성 오더 워커 실행중(order_worker_task not done) → "order in progress; cancel first"
2. 현재 pose(vx,vy) 읽기 → _find_nearest_node(vx,vy) → (node_id, seq, distance)
   - None이면 FAILED "no nearest node"
   - threshold = self._idle_last_node_reach_xy()   # 실배포 100mm
3. 이미 도착(distance <= threshold)이면:
   → _set_last_node(node_id, seq); request_state_publish(...); FINISHED "already at <node>"
4. 아니면 UmGoto 발행(_execute_order_node_position 패턴):
   - 시뮬: await vehicle.goto_xyz(tx,ty,ttheta)   (target = _map_nodes()[node_id])
   - 실로봇: await vehicle.goto_point(node_id)
   - (선택) _await_goto_ack 패턴으로 모터OFF/estop 등 거부를 조기 FAILED(주행 안 했으니 stop 불요)
5. bounded 도착 폴링(<= goto_nearest_timeout_sec):
   while time.monotonic() < deadline:
       d = self._distance_to_node_id(node_id, 최신 position)
       if d is not None and d <= threshold: 도착
       await asyncio.sleep(node_position_poll_interval_sec)  # 기존 0.2s 재사용
6. 도착 → _set_last_node(node_id, seq); request_state_publish("goto nearest: reached"); FINISHED
   타임아웃 → stop_motion()/um_stop() 전송; FAILED "timeout"; 핸들러는 lastNodeId 안 씀
```

- **stop-on-timeout**으로 로봇이 더 안 가므로, FAILED일 때 핸들러는 lastNodeId를 쓰지 않고
  proximity 캡처도 (100mm 밖에 멈췄으면) 안 일어난다 → "FAILED ⟹ 핸들러가 lastNodeId 미설정".
  (만약 정지 지점이 우연히 100mm 안이면 정상 idle 캡처가 채울 수 있으며, 그것은 올바른 동작.)
- **성공(100mm 진입)** 시 우리 명시적 set과 proximity 캡처(100mm)가 **같은 노드**로 수렴 →
  충돌 없음. settled/disabled 모드에선 명시적 set이 유일/우선 경로.
- 긴 async 대기는 RUNNING으로 노출, WebUI POST는 전송 즉시 `delivered=true` 반환.
  RUNNING→FINISHED/FAILED 전이는 텔레메트리 폴링으로 보인다(기존 장시간 instant action과 동일).

## 등록 (3곳)

- **factsheet** `core/factsheet.py INSTANT_ACTION_TYPES`에 `"gotoNearestNode"` 추가
  (어댑터 `SUPPORTED_INSTANT_ACTIONS`는 alias라 자동 반영) → eq/ACS 발견 가능.
- **registry** `core/registry.py _JIBOT_INSTANT_ACTIONS`에
  `InstantAction("gotoNearestNode", "Go to nearest node (set lastNodeId)", motion=True)` 추가.
- **디스패치 체인**(`instant_actions_accept_procedure`)에
  `elif action.action_type == "gotoNearestNode": self._handle_goto_nearest_node_instant_action(action)` 추가.

## WebUI

- **버튼 위치**: 어댑터 상세 페이지의 **기존 Goto 그룹 옆**(`render.py` Goto 영역).
- **라벨**: 현재 최근접 노드와 gap을 함께 표시 — 예 `가장 가까운 노드로 이동 → N3 (1.2 m)`.
  값은 **이미 발행 중인 `NEAREST_NODE` 텔레메트리**(live 스냅샷의 `state.information`)에서 읽어
  렌더. 텔레메트리 없으면 제네릭 라벨 `가장 가까운 노드로 이동`로 폴백하고 여전히 클릭 가능.
- **POST**: `/adapter/<key>/action`, `action_type=gotoNearestNode`, **node id는 안 보냄**.
- **confirm gate(F1 — 사용자 확정)**: 기존 `_post_action`은 motion을 confirm 없이 발행하므로,
  `_post_action`에 **gotoNearestNode 전용 confirm 검증**을 추가한다(예: 작은
  `_CONFIRM_REQUIRED_ACTIONS = {"gotoNearestNode"}` 집합 → 해당 액션은 `_confirmed(form)`
  아니면 거부). 기존 "motion no-confirm" 동작(다른 액션·`test_web_server.py:702`)은 **불변**.
  버튼 폼은 confirm 체크박스/키를 포함(기존 confirm UI 패턴 재사용).
- **중복 노출 방지**: registry 등록 시 제네릭 "Vehicle actions" 자동 목록에도 뜰 수 있으므로,
  emergency/manual 액션이 자동목록에서 제외되는 기존 방식과 동일하게 `gotoNearestNode`를
  **자동목록에서 제외**하고 Goto 그룹 전용 버튼으로만 렌더(정확한 제외 지점은 구현 단계 확정).
- 비-JIBOT 어댑터엔 미노출(액션 미지원).

## 설정 — `config/config.py` Settings (+ `config.toml`)

```toml
[settings]
# (기존 항목들 …)
goto_nearest_timeout_sec = 60.0   # gotoNearestNode 도착 폴링 타임아웃(초)
```

- `goto_nearest_timeout_sec: float = 60.0` 추가. `<=0`이면 무한대기 금지 → 양수로 클램프.
- **도착 임계치는 신규 설정 없이** `idle_last_node_reach_xy`(`_idle_last_node_reach_xy()`,
  실배포 100mm)를 재사용한다(결정 #4) — lastNodeId 캡처와 동일 기준.

## 데이터 흐름

```
[WebUI]
Goto 그룹 "가장 가까운 노드로 이동" + confirm → POST /adapter/<key>/action {gotoNearestNode}
  → server _post_action(confirm gate: gotoNearestNode면 _confirmed 필수)
    → control.build_instant_actions → UdsSender → control.sock
      → 어댑터 가드(localize·노드有·오더워커 idle) → _find_nearest_node(최신 pose)
         → distance<=100mm이면 즉시 _set_last_node + FINISHED
         → 아니면 UmGoto(goto_point/goto_xyz) → bounded 도착폴링(≤timeout)
            → 도착(≤100mm): _set_last_node + request_state_publish + FINISHED
            → 타임아웃: stop_motion/um_stop + FAILED(핸들러 lastNodeId 미설정)
  → state.last_node_id 발행 → FMS/ACS가 오더 진행 기준으로 사용

[ACS 경로] eq/FM → MQTT instant action(gotoNearestNode) → 동일 핸들러 합류
```

## 에러 처리

- **미localize / 노드 없음 / 최근접 노드 없음**: 즉시 FAILED, 사유 명시. 로봇 무동작.
- **활성 오더 중**: FAILED "order in progress; cancel first" → 운영자가 `cancelOrder` 선행.
- **goto 거부(모터 OFF / estop 등)**: 가능하면 `_await_goto_ack` 패턴으로 조기 FAILED(주행
  전이라 stop 불요); 못 잡으면 도착 폴링 타임아웃으로 흡수.
- **타임아웃**: `goto_nearest_timeout_sec` 경과 시 **stop 전송 후 FAILED**. stop으로 로봇이
  더 안 가므로 이 시도의 주행이 나중에 proximity 캡처를 유발하지 않는다 → FAILED 시 핸들러는
  lastNodeId를 쓰지 않는다.
- **어댑터 다운**: `UdsSender` fail-closed "not delivered" → WebUI 플래시 err.
- **fire-and-forget**: UmGoto는 ack 없음 → 도착은 pose 폴링으로만 확인(설계 전제).

## 테스트 (TDD; fake vehicle/client + fake clock 주입)

- **최근접 노드로 UmGoto 발행**: 멀리(>100mm) 있는 pose + 맵 노드 → 핸들러가 최근접 node로
  `goto_point`(실)/`goto_xyz`(시뮬) 호출.
- **도착 → lastNodeId 설정**: 폴링 중 fake distance가 ≤100mm로 진입 → `state.last_node_id`가
  최근접 node id로, `last_node_sequence_id`가 seq로 설정 + FINISHED + state publish 요청.
- **이미 ≤100mm**: 주행(goto 호출) 없이 즉시 `_set_last_node` + FINISHED.
- **가드**: 미localize → FAILED; `_map_nodes()` 비어있음 → FAILED; `order_worker_task`
  실행중 → FAILED(주행 호출 없음).
- **타임아웃**: fake distance가 끝까지 >100mm → 타임아웃에 **stop 호출** + FAILED,
  핸들러가 `state.last_node_id`를 안 씀(fake clock).
- **goto 거부**: `_await_goto_ack`가 거부 사유 반환 → 폴링 진입 전 FAILED(stop 호출 없음).
- **임계치 정렬**: 도착 판정이 `_effective_reach_deviation_xy`(200mm)가 아니라
  `_idle_last_node_reach_xy`(100mm)를 쓰는지 확인.
- **registry/factsheet 노출**: `core/factsheet.py INSTANT_ACTION_TYPES`·`agvActions`·registry에
  `gotoNearestNode` 존재, `motion=True`.
- **웹**:
  - Goto 그룹에 버튼 렌더(최근접 텔레메트리 있으면 `→ <node> (<gap>)`, 없으면 제네릭).
  - **confirm gate**: confirm 없이 POST → 거부; confirm 있으면 전송. **기존 "motion 액션
    no-confirm" 테스트(다른 액션)는 그대로 통과**(전용 gate가 gotoNearestNode에만 적용됨 확인).
  - POST 시 `action_type=gotoNearestNode`(node id 미포함) 전송.
  - 자동 Vehicle actions 목록엔 미중복; 비-JIBOT 미노출.

## 구현 순서

1. config `goto_nearest_timeout_sec`(Settings + config.toml).
2. 어댑터 `_handle_goto_nearest_node_instant_action` + 디스패치 분기 + 가드/도착폴링/stop-on-timeout
   + 테스트.
3. 노출: `core/factsheet.py INSTANT_ACTION_TYPES` + `core/registry.py _JIBOT_INSTANT_ACTIONS` + 테스트.
4. WebUI Goto 그룹 버튼(최근접 라벨, confirm) + `_post_action` confirm gate(전용) + 자동목록 제외
   + 테스트.
5. `docs/guide/web-ui.md`에 버튼 동작 한 줄 문서화(선택).

## 범위 밖 (YAGNI)

- **지도 시각화 / route 그리기** — 사용자가 명시적으로 제외(이번 범위는 액션만).
- **임의 노드 선택** — 이 버튼은 "최근접" 전용. 임의 node 이동은 기존 Goto 폼이 이미 제공.
- **move(상대 거리) 방식** — 미채택(UmGoto로 확정, 결정 #2).
- **`_finalize_v3_node_step`를 `_set_last_node`로 통합** — 별개 정리 작업(이 기능 범위 밖).

## 리스크 / 검증 필요(실기)

- **`_await_goto_ack`의 node 의존** — order Node 객체를 받는다. instant action엔 Node가
  없으므로 (a) 경량 shim(`node_id`만) 또는 (b) 수용/거부 판정 로직 추출. 구현 단계에서 최소
  침습 경로 선택. 못 쓰면 도착 폴링 타임아웃(+stop)이 안전망.
- **100mm 도달 가능성** — 실배포 proximity 캡처가 이미 100mm를 쓰므로(현장 동작 중) UmGoto가
  100mm 안으로 들이는 것은 검증된 전제. 만약 정밀도 부족으로 spurious timeout이 잦으면
  `goto_nearest_timeout_sec` 상향 또는 별도 도착 임계치 도입 검토(후속).
- **stop 정지 경로** — cancelOrder가 쓰는 `stop_motion`/`um_stop`을 재사용. 정확한 심볼은
  구현 단계에서 확정(둘 중 instant-action 핸들러에서 호출 가능한 것).
- **좌표 단위 일관성** — `_find_nearest_node`/pose/임계치 모두 raw map unit(mm)에서 비교
  (기존 nearest/idle 캡처와 동일 경로) → 추가 변환 불필요.
- **안전 경계** — WebUI는 소프트웨어 게이트일 뿐 E-stop/안전 PLC를 대체하지 않는다.
