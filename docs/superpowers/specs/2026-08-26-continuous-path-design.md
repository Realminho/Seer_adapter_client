# 연속 경로 주행 (Continuous Path) — 설계 문서

- 날짜: 2026-08-26 (실기 측정) / 2026-08-27 (문서화)
- 상태: **설계 확정, 미구현.** 구현 계획서는 `docs/superpowers/plans/`에 별도.
- 목표: FMS가 한 오더에 released로 내려 주는 base 노드들을 **정지 없이 통과**한다.
  지금은 노드마다 goto를 끊어 쏘고 완전 정지를 기다린다.
- 확정 사항: 하달은 **노드 코얼레싱**(연속 노드 N개 → goto 1개), 판정은 **경유지 패스**
  (허용 반경 진입 시 통과 간주). 벤더 라우트 API(`UmSchedulerThis` 계열)는 **쓰지 않는다**.
- 기본값 **off**. `[settings] path_control` 로 켠다.
- **단 CP-4 하나는 예외다 — 게이트 밖이며 기본 배포에서 바로 동작이 바뀐다.** §4 CP-4 참조.

---

## 1. 용어

| 층 | 용어 | 뜻 |
|---|---|---|
| 기능 | **연속 경로 주행 (Continuous Path, CP)** | base 안 released 노드를 정지 없이 통과 |
| 하달 | **노드 코얼레싱 (Node Coalescing)** | 연속 노드 N개를 goto **1개**로 합쳐 내린다 |
| 판정 | **경유지 패스 (Waypoint Passing)** | 반경 진입 시 통과로 치고 정지를 기다리지 않는다 |

"블렌딩/스무딩/경로 최적화"는 이 문서에서 쓰지 않는다. 경로 기하는 urobot이 소유하고
어댑터가 건드리는 층이 아니다. 어댑터가 정하는 것은 **goto를 몇 개로 쪼개 언제 보내는가**뿐이다.

`mode` 라는 단어도 쓰지 않는다. 이미 6군데에서 다른 뜻으로 쓰이고 있다 —
`motion_rules[].mode`(goto/dock/move), extensions.hcl elevator `motion_rules[].mode`(enter/inside/passed),
`vehicle._mode`(JIBOT 텔레메트리), `run_mode`(manualMove), VDA5050 `operatingMode`,
`[settings]`의 `last_node_capture_mode`/`nearest_node_mode`/`rotate_to_mode`.

---

## 2. 지금 무엇이 문제인가

`_process_v3_node_step`(`adaptor/adapter_jibot.py:6485`)이 노드마다 직렬로 돈다.

```
UmGoto(node) → _wait_until_node_position_reached (:5601) → _settle_goto_arrival (:4730)
```

`_settle_goto_arrival`은 `_wait_until_automatic_motion_stopped`(`:4713`)를 await 한다.
즉 **다음 노드를 보내기 전에 로봇이 완전히 설 때까지 기다린다.** 이것은 스펙 요구가 아니라
어댑터 구현 선택이다.

엣지는 주행 대상이 아니다. `_process_v3_edge_step`(`:5066`)은 로그만 찍고 True를 반환하며,
`edge.maximum_speed`는 로그 출력에만 쓰이고 차량에 전달되지 않는다.

### 2.1 스펙 관점

VDA5050 3.0(github.com/VDA5050/VDA5050 `3.0.0`)에서:

- §6.1.1 — released는 "통과가 기대된다"는 뜻이다. 노드 위에서 정지하라는 조문은 없다.
- §6.1.2 — 정지 의무는 **base의 마지막 노드(decision point)** 하나뿐이다.
  *"The mobile robot shall stop at the decision point ... In order to ensure a fluent movement,
  the fleet control should extend the base before the mobile robot reaches the decision point."*
- §6.6.2 — 노드 위 정지는 **SOFT/HARD 블로킹 액션이 있을 때의 예외**로 기술된다.
  통과 판정 주체는 로봇이며 `allowedDeviationXY`는 통과 허용 오차다(주행 코리도어가 아니다).
- §6.6.3 — `newBaseRequest`는 *"prevent unnecessary braking"*이 목적이다.

따라서 현행 동작은 **SHALL 위반은 아니지만** base/horizon·decision point·newBaseRequest라는
스펙 설계 의도를 무력화하는 안티패턴이다. 개선 논거는 "스펙 준수"가 아니라 **성능**으로 잡는다.

### 2.2 실측 규모

`jibot/site/2605-b205-demo/TestingOrderJson/` 픽스처(전부 version 3.0.0):

| 파일 | 노드 | released |
|---|---|---|
| order_merger1_2.json | 8 | **8** |
| order_merger2_1.json | 8 | 7 |
| order_F1_60_F1_40.json | 5 | 3 |

즉 이 FMS는 8노드를 **base로** 내려 준다. horizon(released=false)은 §6.1.1상 주행 금지이므로
CP가 쓸 재료가 아니다. 쓸 재료는 **base 안의 released 노드들**이다.

FMS 마다 창 크기가 다르다. WCS FMS(`packages/api/src/nestjs/fms`)는
`_resolveDynamicReleaseCount` 로 Zone 혼잡도에 따라 창을 정한다 —
여유면 `MAX_RELEASE_COUNT`=4, 절반 이상 차면 2, 꽉 차면 1. 8노드 order 기준으로
`startOrder` 가 0..4 를 열고 node4 도달 시 4..7 을 열어 **완전정지 7회가 2회로 준다.**

이 구조에 좋은 성질이 있다 — **혼잡하면 창이 2, 꽉 차면 1로 줄어 병합이 자동으로 현행
동작으로 되돌아간다.** 별도 안전 게이트를 만들 필요가 없는 이유다.
위 픽스처(8노드 전량 released)는 demo 사이트 외부 FMS 기준이라 창이 더 크다.
**"released 구간 끝까지 한 번에 간다"** 규칙 하나로 양쪽이 커버된다.

---

## 3. 실기 측정 — 이 설계의 근거

로봇 `192.168.101.50:7274`, 맵 `hana.json`(UmGetMap 실시간 수령분 = PathPoint 55개).
측정 전 어댑터 서비스를 내리고, 45샘플 수동 관찰로 외부 간섭 0을 확인했다.
프로브: `scripts/probe-jibot-route-goto.py`.

### 3.1 로봇은 직선 중간 노드를 이미 무정지로 통과한다

단일 goto `p40 → p58`(중간에 p59 통과, 총 5.4 m):

```
84.66  (11350, -992)  vf=1002.7 mm/s   d(p59)=988
85.92  (10038, -937)  vf=1002.1 mm/s   d(p59)=328   ← p59 최근접
86.51  ( 9470, -937)  vf= 998.5 mm/s   d(p59)=895
```

**감속이 전혀 없다.** p58(7618,-965) / p59(10365,-911) / p40(12994,-1077)은 직진 코스다
(총 꺾임 4.7°).

### 3.2 정지는 "노드라서"가 아니라 "꺾여서" 생긴다

같은 주행에서 정지한 곳은 p37·p38·p39·p40 뿐이고 **넷 다 90° 코너**다.

| 노드 | 정지 | out-degree |
|---|---|---|
| p37 | 20.8 s | 2 |
| p38 | 9.7 s | **1** |
| p39 | 10.3 s | **1** |
| p40 | 9.8 s | 2 |
| **p59** | **없음** | 2 |

분기수로는 설명되지 않는다(p38/p39는 out-degree 1인데 정지, p59는 2인데 통과).
차동구동 로봇이 90° 회전을 제자리에서 처리해야 하는 **기구학 제약**이다.
총 91 s 주행 중 정지 51 s(56%)가 코너에서 나왔다.

**코너 정지는 이 설계의 범위 밖이다.** 줄일 수 없는 비용으로 두고, 이득은 직선·완만 구간에서 얻는다.

### 3.3 벤더 라우트 API는 CP에 해롭다

`UmSchedulerThis`로 multi-step 라우트를 내리는 경로는 **기능적으로는 확정**했다 —
라우트가 수락되고(`UmGetCurTask.routes='cp_probe'`), goto 스텝이 집행되고,
스텝 전이(`key a → a1 → a11`)도 관측했다. 별도 `UmRoutes` 트리거는 필요 없다.

그러나 **스텝 경계가 정지를 새로 만든다.** 3.1과 같은 직선의 같은 지점(p59)에서
2스텝 라우트(`a=p59`, `a1=p40`)를 실행한 결과:

```
26.57  vf=405 → 232 → 147 → 101 → 65 → 38 → 31 → 29 mm/s (크리프)
33.28  vf=0                              ← 완전 정지 1.9 s
35.70  vf=-26.4                          ← 소폭 후진
36.27  vf=0                              ← 다시 1.3 s
38.71  vf=341 → 504                      ← 재출발
```

감속 시작(26.6 s)부터 속도 복귀(39.4 s)까지 **약 13 초**, 완전 정지 3.2 초.
같은 지점을 단일 goto는 1002 mm/s로 지나갔다.

**결론: 라우트로 묶으면 안 된다. goto 하나로 합쳐야 한다.**
`UmSchedulerThis` / `UmRoutes` / `UmSchedulerList` 노선은 CP 목적으로 폐기한다.

### 3.4 폴링 주기가 통과 판정을 놓친다 (신규 위험)

직선 통과 실측이 **1002 mm/s**인데 `node_position_poll_interval_sec = 0.2`다.
샘플 간 이동이 **약 200 mm**이므로, 현행 도착존
(`allowedDeviationXY` × `reach_zone_scale=20` = **±40 mm**)은 **건너뛰어진다.**

CP를 켜는 순간 중간 노드를 놓쳐 오더가 진행하지 않는다. §5.1의 보간 판정이 필수다.

---

## 4. 무엇을 만드는가

### CP-1 경유지 패스 (Waypoint Passing)

통과 판정을 **주행 명령의 종료**에서 떼어내 **pose 스트림 + 반경**으로 옮긴다.
`path_control="continuous"`이고 이 노드가 코얼레싱 구간의 마지막이 아니면
`_settle_goto_arrival`(`:4730`)의 완전 정지 대기를 건너뛴다.

이 형태는 오픈소스 두 구현이 독립적으로 같은 결론에 도달한 것이다:
libVDA5050++의 `evalPosition()`(pose+반경으로 통과 판정, 네비 완료와 분리),
inorbit `vda5050_connector`의 액션 **feedback** 기반 `lastNodeId` 갱신
(같은 저장소에서 정지/무정지의 차이가 정확히 갱신 트리거 하나뿐이다).

### CP-2 노드 코얼레싱 (Node Coalescing)

연속 released 구간을 계산해 **마지막 노드 하나로만 goto**를 보낸다.
중간 노드는 주행 명령 없이 통과 판정 + 상태 갱신만 한다.

구간 계산 알고리즘은 inorbit `vda5050_controller.py:1601` `_get_drivable_segment()`를
이식한다(BSD-3, `inorbit-ai/ros_amr_interop`). 순수 파이썬이며 order의
`sequence_id`/`released`/`actions[].blocking_type`만 읽고 ROS 타입에 의존하지 않는다.
저작권 고지를 남긴다.

### CP-3 base 확장 요청 (newBaseRequest)

`new_base_request`는 `adapter_jibot.py:739`에서 `None`으로 하드코딩되어 있고
`:3514`이 그 값을 그대로 발행한다. 즉 스펙이 정한 "불필요한 제동 방지" 신호를
**한 번도 보낸 적이 없다.**

코얼레싱 구간의 끝이 base의 끝(decision point)이면 `newBaseRequest=true`를 발행한다.
이것이 없으면 base 안 7번은 안 멈춰도 8번째에서는 그대로 정지한다.

### CP-4 edgeStates 이탈 시점 교정

`_clear_v3_order_step`(`:6554`)이 엣지 스텝을 처리할 때 `edge_states`를 제거하는데,
엣지 스텝은 주행 없이 즉시 completed가 되므로(`:5066`) 사실상 **엣지 진입 시점**에 지워진다.
스펙 §6.6.2는 *"엣지 이탈 = 그 엣지가 이어지는 다음 노드를 통과할 때"* 제거하도록 규정한다.

FMS는 `edgeStates`로 로봇의 구간 점유를 판단한다. 지금은 노드마다 완전 정지해서
조기 제거 창이 짧을 뿐이고, **CP가 정확히 이 창을 넓힌다.** 따라서 CP-1의 전제조건이다.

#### CP-4는 `path_control` 게이트 밖이다 (의도된 결정, 2026-08-27)

`_clear_edge_states_up_to`는 `_finalize_v3_node_step`에서 **무조건** 호출된다.
즉 `path_control="stop_point"`(기본값)에서도 동작이 바뀐다 — 엣지가 A→B 주행 내내 살아
있다가 다음 노드를 통과할 때 빠진다. 배포가 `--config-toml-mode keep` 기본이므로
**아무도 옵트인하지 않은 채 전 차량에 적용된다.**

그래도 게이트를 씌우지 않는다. 두 방향의 실패 모드가 대칭이 아니기 때문이다:

| | 해제 시점 | 실패 모드 |
|---|---|---|
| 이전 | 엣지 스텝 pop 시(사실상 즉시) | FMS는 구간을 떠났다고 보는데 로봇은 **아직 주행 중** → 충돌 위험 |
| 이후 | 다음 노드 통과 시 | 점유를 더 오래 잡음 → 다른 로봇 대기 → **처리량 감소** |

게이트를 씌우면 옵트인 전까지 **위험한 쪽이 프로덕션에 남는다.** 그리고 CP-4는 기능이 아니라
스펙(§6.6.2) 위반의 수정이므로, 버그 수정을 기능 플래그 뒤에 두면 버그가 살아 있게 된다.

**배포 시 반드시 공지할 것**: 이 변경 이후 구간 점유 유지 시간이 길어진다. 혼잡한 구역에서
처리량이 떨어질 수 있고, 그건 정상 동작이다. 되돌리려면 `_finalize_v3_node_step`의
`_clear_edge_states_up_to` 호출 한 줄을 지우면 이전 동작으로 돌아간다.

---

## 5. 설계 상세

### 5.1 통과 판정 — 보간이 필수다

점 판정(`현재 pose가 반경 안인가`)은 §3.4 때문에 쓸 수 없다. 대신 **직전 샘플과 현재 샘플을
잇는 선분이 노드 반경 안을 지났는가**로 판정한다.

```
passed(node, prev_pose, cur_pose, radius):
    반환 = 점_선분_거리(node_xy, prev_pose, cur_pose) <= radius
```

libVDA5050++가 같은 문제를 `InterpolationType {NONE, LINEAR}` 설정으로 노출한다.
`NONE`이 현행 점 판정, `LINEAR`가 이 선분 판정에 해당한다.

반경은 `waypoint_pass_radius_mm`로 별도로 잡는다. 오더의 `allowedDeviationXY`(×`reach_zone_scale`)
는 **도착 정밀도**이지 통과 반경이 아니고, 실측 ±40 mm는 통과 판정에 너무 작다.
libVDA5050++도 같은 이유로 노드 편차 기본값/덮어쓰기를 별도 설정으로 분리해 두었다.

### 5.2 코얼레싱 경계 — 끊는 조건

| 조건 | 근거 |
|---|---|
| released=false | `_rebuild_v3_order_queue_from_state`(`:4269`) — base 끝에서 자동으로 끊긴다 |
| 노드에 SOFT/HARD 블로킹 액션 | `:4531` `last_driving_blocker` |
| 엣지에 SOFT/HARD 블로킹 액션 | `_process_v3_order_step` — 엣지 액션은 다음 노드 주행 **전에** 실행된다 |
| dock 세그먼트 룰 매칭 | `_dock_segment_rule` `:5108` — UmDock 경로 |
| move 룰 매칭 | `_move_motion_rule` `:5090` — 상대이동은 시작 pose를 먼저 읽어야 해서 정지를 전제한다 |
| dock work 노드 | `_is_dock_work_node` `:5151` |
| 좌표를 모르는 노드 | `_resolve_node_target is None` — 도착 검증 불가 |
| `step.actions_only` | `:4468` — 주행이 없는 스텝 |

**코너는 끊지 않는다.** 코너 정지는 로봇 기구학이고(§3.2), 어댑터가 goto를 쪼갤 이유가 없다.
오히려 합쳐야 로봇이 구간 전체를 보고 처리할 여지가 생긴다.

**미해결 위험 — 인프라 노드는 어댑터 눈에 안 보인다.** WCS FMS 의 `_buildNodeActions`
는 `executionTarget === 'AMR_VDA5050'` 인 액션만 order 에 싣는다. `FMS_INFRA` 액션(문 열기,
리프트 호출)은 서버가 `_handleReachedInfraNode(state, reachedNodeIndex)` 에서 **도달 보고된
인덱스 하나에 대해서만** 처리한다. 즉 **어댑터 눈에 액션이 0개인 노드도 인프라 시퀀스를
가질 수 있고**, 그 노드를 병합해 건너뛰면 인프라 호출이 통째로 유실된다. 점프 advance 라서
건너뛴 인덱스는 영영 처리되지 않는다.

어댑터 단독으로는 이 노드를 식별할 방법이 없다. 타협: 병합은 released 구간 + 위 8개 조건으로만
열고, **현장 적용은 인프라 노드가 없는 구간부터** 한다. 근본 해결은 FMS 가 `coalescable` /
`nodeStopRequired` 를 실어 보내는 것이다(판단 주체를 정보 소유자와 일치시킨다).

액션 관련 주의: 코드상 정지를 강제하는 것은 **SOFT/HARD 뿐**이고 NONE은 아니다.
현장 `pio` 액션(엘리베이터·dock·airshower)은 HARD이지만, 그 노드들은 원래 정지가
필요한 곳이므로 병합 가능 집합은 넓게 유효하다(2026-08-25 사용자 확인).

### 5.2.1 anchor 는 반드시 구간의 **마지막** 노드여야 한다

`_active_order_worker_step_is_obsolete()` 는 `current_order_step.sequence_id <=
state.last_node_sequence_id` 이면 주행 중인 워커를 cancel 한다(orderUpdate 처리 경로,
`adapter_jibot.py:4349` 에서 호출).

- anchor 를 구간 **첫** 노드(seq 2)로 잡으면 첫 통과 보고에서 `2 <= 2` 가 참이 되어
  **자기 주행을 끊는다.**
- anchor 를 구간 **끝** 노드(seq 8)로 잡으면 통과 보고 2·4·6 에 대해 `8 <= 6` 이 거짓이라 안전하다.

같은 함수에 이미 선례가 있다 — `_order_action_step_in_flight` 인 스텝은 이 비교에서
예외 처리되어 있고, 주석이 "노드 도착이 이미 lastNodeSequenceId 를 올려놨기 때문에 아래
비교가 이 스텝을 '지나간 것'으로 읽는다"고 정확히 이 부류의 버그를 서술한다.
코얼레싱은 같은 성질의 세 번째 경우이므로 **같은 방식의 예외가 필요하다**:
코얼레싱 구간 주행 중인 스텝은 obsolete 판정에서 제외한다.

### 5.2.2 중간 통과 보고는 FMS 배려가 아니라 자기 방어다

`_rebuild_v3_order_queue_from_state()` 는 `step.sequence_id <= last_sequence_id` 인
접두부만 건너뛰고 나머지를 전부 다시 큐에 넣는다. 중간 노드 통과를 보고하지 않으면
이미 물리적으로 지나온 노드가 되살아나 병합 주행 뒤에 **뒤로 가는 goto** 가 생긴다.
§5.3 의 노드별 발행은 그래서 선택이 아니라 필수다.

### 5.3 상태 발행

코얼레싱 구간의 중간 노드도 통과할 때마다 `_finalize_v3_node_step`(`:6002`)을 그대로 부른다.
`lastNodeId`/`lastNodeSequenceId`를 올리고 `request_state_publish("node reached")`를 호출한다.
FMS가 점유 관제를 하므로 **노드별 발행을 접으면 안 된다.**

`lastNodeId`의 의미가 "서 있는 노드"에서 "지나간 노드"로 바뀐다. 오더 중 `lastNodeId`를
쓰는 곳은 `_finalize_v3_node_step` 하나뿐이므로 영향 범위는 좁다 —
`last_node_capture_mode` / `last_node_release_xy` / `use_nearest_node_as_last_node_when_missing`
셋은 전부 오더 비활성(idle) 경로 전용이라 충돌하지 않는다(`_release_stale_last_node`(`:9426`)가
`_is_v3_order_active()`면 건너뛴다).

### 5.4 설정 표면

```toml
[settings]
# 연속 경로 주행. stop_point = 노드마다 완전 정지(현행), continuous = 코얼레싱 + 경유지 패스
path_control            = "stop_point"
# 통과 간주 반경(mm). 0 이면 기존 도착존을 쓴다 — 실측 1002 mm/s 에서는 너무 작다(§3.4)
waypoint_pass_radius_mm = 0.0
```

- `adaptor/config/config.toml:46` `[settings]` + `adaptor/config/config.py:194-374` `Settings`
  **양쪽에 같은 이름**을 넣어야 한다. dataclass 필드 없이 toml 키만 넣으면 `_section()`이
  ConfigError를 던져 **부팅이 실패한다**(`config.py:1171`).
- 문자열 enum 검증은 `nearest_node_mode` 선례를 따른다 — 모르는 값이면 경고 후 기본값 폴백
  (`_apply_last_node_capture_mode` 계열의 경고 후 폴백).
- **배포 제약**: `scripts/update-jibot-adapter-over-ssh.sh`가 `--config-toml-mode keep`을
  기본으로 쓴다. 새 키는 현장 로봇 config.toml에 **도달하지 않는다.** 즉 dataclass 기본값이
  곧 현장 동작이고, 켜려면 기본값 변경 배포가 필요하다.
- 설정은 부팅 1회 로드이고 원격 reload는 미구현이므로 토글에 재시작이 필요하다.
- **per-robot 적용은 overlay 가 아니다.** `robots.hcl` 의 `robot { config = "robot-a.toml" }`
  는 base `config.toml` 을 **통째로 교체**한다(`get_config_with_fallback`). 파일럿 1대만
  켜려면 config 전체 사본을 유지해야 하고 이후 base 변경이 그 로봇에 따라오지 않는다 —
  **드리프트가 확정된 파일럿 전략이다.** 파일럿은 dataclass 기본값을 바꿔 배포하는 쪽이 낫다.

---

## 6. 위험

| 위험 | 대응 |
|---|---|
| 폴링 주기가 통과를 놓친다 (§3.4) | 선분 보간 판정 + `waypoint_pass_radius_mm` (§5.1). **이것 없이는 CP가 동작하지 않는다** |
| `edgeStates` 조기 제거 창이 넓어진다 | CP-4를 CP-1보다 **먼저** 넣는다 |
| `_order_node_motion_seq`(`:6487`) 증가 패턴 변화 | rotateTo 배경 루프의 supersede 신호다. 코얼레싱 시 증가 시점을 명시적으로 정한다 |
| 검증 수단 부재 | FakeVehicle이 `_mode="auto"`/`_status="Stopped"`라 `_settle_goto_arrival`이 전 테스트에서 no-op다. 하네스 확장이 **선행 작업**이다 |
| `_handle_motion_rule_instant_action`(`:6942`) WebUI 훅 | order worker와 같은 오케스트레이터를 부른다. CP 게이트를 룰 조회 레벨이 아니라 order worker 안에 둬서 이 경로에 영향을 주지 않는다 |

기존 테스트는 stop-and-go를 계약으로 고정하지 않는다 — 한 오더에서 2홉 이상 주행하는
테스트가 0건이다. 즉 깨질 것은 없지만 고쳤는지 확인할 수단도 없다.

유지해야 할 인접 계약 3건:
(a) SOFT/HARD 액션 전 정지 게이트(`adaptor/tests/test_adapter_jibot_v3_order.py`의 hard-stop 테스트),
(b) 미릴리즈 구간은 큐에 들어가지 않는다,
(c) 노드 도착이 액션 실행 **전에** `lastNodeSequenceId`를 올린다(액션 이중 실행 회귀 방지).

---

## 7. 범위 밖

- **코너 정지 단축** — `start_reach_max_dis`(벤더 라우트 `A-CAM2`에서 300으로 1회 사용),
  회전 최적화. 기구학 제약이라 별도 과제다.
- **속도 상향** — 벤더 goto 스텝의 `max_vel` 실측 범위가 200~1500이고 `AB_600`/`AB_1000`/
  `AB_1500`은 같은 구간의 속도 변종이다. 즉 UmSetConfig 없이 올릴 여지가 있지만,
  단일 goto에 `max_vel=500`을 줬는데 1003 mm/s가 관측된 건이 있어 **거동이 미해명**이다.
- **벤더 라우트 API** — 기능은 확정했으나 §3.3으로 CP에는 해롭다.
- **SEER / Hexplorer** — SEER 3051은 중간 스테이션 무정지 통과가 네이티브이고
  (`robotkit-netprotocol-l-1.2.1` 「固定路径导航」), `adapter_seer.py`는 존재하지 않는다.
  Hexplorer는 order를 구독조차 하지 않는다(`adapter_hexplorer.py`가 `subscribe_order`를 호출하지 않는다).
  공통 오더 큐도 존재하지 않는다(`order_queue`는 `adapter_jibot.py:202` 단독,
  `adaptor/core/`는 WebUI 백엔드다). **CP는 JIBOT 어댑터 경계 안에 둔다.**
- **팩트시트 `navigationTypes`** — 현재 `["AUTONOMOUS"]`(`config.toml:353`)인데 VDA5050 3.0
  확장 enum(`PHYSICAL_LINE_GUIDED`/`VIRTUAL_LINE_GUIDED`/`FREELY_NAVIGATING`)에 없는 값이다.
  FMS 계약일 수 있어 벤더 확인 없이 건드리지 않는다. 별건으로 남긴다.

---

## 8. 구현 순서

1. **테스트 하네스** — FakeVehicle에 주행 중 상태를 만들 수 있게 확장. 2홉 이상 오더 테스트.
2. **CP-4** edgeStates 이탈 시점 교정. CP와 무관하게 옳고, CP의 전제조건이다.
3. **CP-1** 경유지 패스 + 선분 보간 판정. `path_control` 게이트 뒤, 기본 off.
4. **CP-2** 노드 코얼레싱. `_get_drivable_segment()` 이식.
5. **CP-3** newBaseRequest.
6. 실기 A/B — 같은 오더를 `stop_point`/`continuous`로 반복 측정.

---

## 9. 미해명 (구현 전 확인 불필요, 기록용)

- 단일 goto에 `max_vel=500`을 줬는데 1003 mm/s가 관측됐다. 2스텝 실행에서는 504로 지켜졌다.
- 코너 정지 약 10초의 내역(제자리 회전 시간 vs `#watch` 대기)을 분리하지 않았다.
- 각 측정은 1회씩이다. 반복 없이 수치를 일반화하지 말 것.
- 이 로봇은 `target="pose"` goto도 PathPoint 그래프를 따라간다(직선 자유주행이 아니다).
  `vertex` 필드는 방향성이며 단방향 순환 구간이 있다. 8/22 로그의 "로봇은 이미 자유공간
  플래너" 전제는 이 로봇에 성립하지 않는다.
