# charging-dock-brake-order

### 목표
- 충전기 밀착 상태에서 brake 지속 + 소리 반복 원인 규명
- 1_01CH 도착 후 order 가 FMS 에서 완료 처리 안 되는 원인 규명

### 대상
- HN-SH6-TR-002 / 10.8.8.8 (유선 10.6.6.6 과 동일 로봇). ssh 직접 접속함
- 배포본 `adapter_jibot.py` md5 = 로컬 트리와 **동일**(fee6955...). 코드 기준 추론 유효

---

## A. brake 지속 + 소리 — 원인 확정

**05:06:02 ~ 06:20:13, 74분간 `mode=ModeCharge status=nrunto 1#brake` 연속.**
(`nrunto 1#brake` 표본 873개 / 5초 폴링)

전이 지점 실측:
- ~05:04 `mode=ModeCharge status=charging`, **battery_current = -1.4A (방전)**, battery 99%
  → 만충으로 충전 릴레이가 떨어진 상태. status 문자열만 `charging` 이었음
- 05:06:02 JIBOT 이 스스로 `nrunto 1`(ModeCharge 접근 단계)로 복귀 → 즉시 `#brake`
- 05:06:04 `[VIDEO SNAPSHOT PUBLISHED] event=brake`
- **이 74분 동안 어댑터가 보낸 비폴링 명령 0건.** 어댑터가 시킨 동작이 아님

원인:
- **`_run_dock` 이 충전 성공 후 JIBOT 을 ModeCharge 에서 빼내지 않음**
  (adapter_jibot.py:5859). 도킹 성공 = ModeCharge 유지가 정상 종료 상태
- 충전이 끊기면 JIBOT 은 ModeCharge 안에서 알아서 재접근(`nrunto`)을 시도하는데,
  이미 충전기에 밀착해 있어 전방이 막혀 있음 → `#brake` 영구 고착
- 어댑터는 이를 그대로 반영: `state.safety_state.field_violation =
  _is_jibot_obstacle_wait()` (adapter_jibot.py:753) → workingState BLOCKED
  → `_update_sound_for_working_state` 가 `brake.mp3` 반복 재생 (adapter_jibot.py:1626)

즉 "충전기에 붙어 있으면 잠깐 움직였다 마는" 것의 실체는 **JIBOT 펌웨어의
ModeCharge 재접근 루프**이고, 어댑터는 그 상태를 충실히 중계하고 있을 뿐임.
막으려면 도킹/충전 성립 후 ModeCharge 를 벗어나게 하는 처리가 있어야 함.

---

## B. order 미완료 — 어댑터 결백 확인, FMS 수락 조건 미확정

### 어댑터 쪽은 정상
- 8/23 이후 `ORDER NODE DOCK BLOCKED / DOCK FAILED / ORDER STEP WAIT` **전무**
  → dock 대기 hang 가설(8/19 brake 제외 변경 관련)은 폐기
- 오프라인 재현(`scratchpad/repro_order_state.py`, 배포본과 동일 코드 + 배포본 config
  `last_node_capture_mode="proximity"`, `nearest_node_mode="pathPoint"`):
  완료 직후 / 1초 후 / 3초 후 publish state 모두 완전함
  `nodeStates=0 edgeStates=0 actionStates=[clampMin FINISHED]
   lastNodeId=1_01CH lastNodeSequenceId=2 orderId=T-2`
  → `lastNodeSequenceId` 가 proximity 캡처로 0 으로 덮인다는 가설도 **반증됨**
  (`self.order` 는 완료 시 None 이 안 되고, `_all_order_nodes()` 가 원본 노드 목록을
   계속 돌려주므로 seq 가 유지됨. `self.order = None` 은 cancel 경로뿐 — :8443)

### FMS 쪽 관측 (자연 실험, 4/4 일치)
| 시각 | orderId | 최종 노드 seq | FMS 반응 |
|---|---|---|---|
| 00:33:57 | 20260825-5-1 | 1_01CH @ **0** | 수락, 재전송 없음 |
| 06:28:09 | 20260825-7-1 | p39 @ **0** | 수락, 재전송 없음 |
| 03:49:22 | FMSMR-…-S0-3dc1 | 1_01CH @ **2** | 90초 간격 2회 재전송 → cancelOrder |
| 06:30:06 | 20260825-8-1 | 1_01CH @ **2** | 90초 간격 2회 재전송 → (재부팅) |

- 갈리는 변수는 **최종 노드 sequenceId** 이지 노드 정체도 dock 경로도 아님
  (`1_01CH @ seq 0` 은 정상 완료됨 → "도킹이 완료를 막는다"는 설명은 성립 안 함)
- 그런데 어댑터가 publish 하는 state 는 seq=0/seq=2 둘 다 완전함
  → **FMS 가 무엇을 보고 거부하는지는 아직 미확정.** 실제 publish state 캡처 필요

### 실제 시퀀스 그대로 재현해도 어댑터 state 는 완전함
`scratchpad/repro2.py` — 한 인스턴스에서 p39 오더 완료 → 1초 뒤 충전 오더(updateId=0)
→ FMS 재전송(updateId=1, `[ORDER STEP ACTIONS ONLY]` 경로)까지 실제 로그 순서대로 재현.
세 시점 모두 publish state 가 완전함:
```
lastNode=1_01CH/2  order=T-8-1/0(→1)  nodes=0 edges=0
actions=[('clampMin','FINISHED')]  errors=[]  driving=False  opMode=AUTOMATIC
```
- `build_action_states` 는 액션 없는 노드/엣지에 placeholder entry 를 만들지 않음
  → 잘려나간 prefix 노드(p39 seq=0)가 미완료 actionState 로 남는 경로도 없음
- 즉 **B 는 어댑터가 publish 하는 필드로는 설명이 안 됨.** 실측 state 원문 비교가 필요

### 실측 state 를 볼 수 있는 곳 (다른 세션 로그에서 확인)
`/run/amr-adaptor/HN-SH6-TR-002/state.json` — 어댑터가 매 주기 쓰는 publish state 원문.
tmpfs 라 리부팅하면 날아간다. MQTT 캡처와 같은 내용이므로 둘 중 아무거나 쓰면 됨.

### 진단 캡처 걸어둠 (읽기 전용)
- `/home/ucore/claude-capture.py` (setsid nohup, paho 구독)
- 출력 `/home/ucore/claude-vda-capture.jsonl` — state/order/instantActions 원문, 200MB 상한
- 중지: `pkill -f claude-capture.py`

---

## C. 같이 발견한 별건 결함 — FMS 세그먼트 오더 거절
> **다른 세션이 이미 손대는 중.** 같은 `adapter_jibot.py` 에 링크 다운 시 오더 거절
> 가드, `_drop_order_after_worker_crash`, `_ORDER_SCOPED_ERROR_TYPES` 가 들어와 있음.
> 이 세션에서는 건드리지 않음 (편집 영역 겹치지 않음).


FMS 는 한 작업을 `…-R0-S0-…`, `-S1-`, `-S2-` 세그먼트로 쪼개 **연속으로(170ms 간격)**
발행하는데, 어댑터가 뒤 세그먼트를 전부 거절함:

```
06:58:59.402 [ORDER REJECTED] orderId=FMSMR-b06f80999140-R0-S1-rude
             activeOrderId=FMSMR-b06f80999140-R0-S0-n9zp reason=current order in progress
```
- 거절된 S1 = p39→p40→p37→p38 (4노드/3엣지) 전체 경로. 즉 본 주행이 통째로 날아감
- state 에 `ORDER_CURRENT_NOT_FINISHED` 에러로 노출됨
- 이력: 8/22 14:56 3건, 8/25 06:58 1건
- 판정 위치 adapter_jibot.py:3726~3740

---

## D. 설정 함정 — `stop_charging_on_arrival` 이 1_01CH 에는 안 걸림
배포본 `[dock] nodes = []`, `stop_charging_on_arrival = true`.
그런데 `_process_v3_node_step` 은 `_dock_segment_rule` 분기에서 바로
`return self._finalize_v3_node_step(...)` 로 빠지고,
`stop_charging_on_arrival` / `_stop_charging_after_dock_work` 는 그 뒤쪽
`_is_dock_work_node` 분기에만 있음. 1_01CH 는 motion_rules dock 이고 `dock.nodes` 는
비어 있으므로 **이 노브는 1_01CH 에서 한 번도 동작하지 않음.** 켜져 있다고 믿기 쉬움

## E. 인프라 메모
- 06:46 재부팅 후 urobot 7273 미기동 → 07:00:32 에 복구됨, 07:01:23 어댑터 재접속
- 06:58:59 의 `'NoneType' object has no attribute 'write'` 는 그 링크 단절 구간 산물

### 완료 — A 근본 수정 구현
사용자 선택: "인계 후 ModeCharge 이탈". TDD 로 실패 테스트 6건 먼저 작성 후 구현.

- `adaptor/adapter_jibot.py`
  - `_run_dock` 이 충전 확인 후 `_handover_dock_charge_to_relay(node)` 를 호출
  - `_handover_dock_charge_to_relay`: relay hold 를 **먼저** 잡고(`start_hold`,
    `_charge_in_place_active=True`) → `_stop_charging_after_dock_work` 로 UmStop
    → `_wait_until_charge_relay_holds` 로 충전 유지 확인
    - 순서가 중요: 반대로 하면 UmStop 의 OpenChargingCircuit 과 hold 사이에 충전 공백
    - `_wait_until_charge_relay_holds` 는 `(유지됨, 새 표본을 본 적 있음)` 을 돌려줌.
      "링크가 조용해 확인을 못 했다"와 "표본이 충전 아님이라 했다"는 다른 사실이고,
      전자에서 재도킹을 걸면 **상태를 볼 수 없는 로봇에 UmDock 을 쏘는** 셈이 됨.
      전자면 폴백 없이 사유만 반환(`no JIBOT telemetry ...`)
    - 실패 시 hold 를 놓고(`release_charge_in_place`) **재도킹으로 폴백**
      (`_redock_after_failed_handover`). 그냥 두면 ModeCharge 도 걷히고 hold 도 없어
      로봇이 충전기 위에서 방전만 한다 — 이 변경 이전보다 나쁜 상태다. 재도킹으로
      충전이 살아나면 노드는 성공으로 끝내되 `_set_charge_handover_warning` 으로
      WARNING 을 남긴다(이 로봇은 다시 ModeCharge 라 brake 루프 재발 가능).
      재도킹으로도 안 붙으면 `_set_jibot_dock_fail_error` + 사유 반환
    - `charge_circuit.enabled=false` 면 relay 를 못 쥐므로 인계 자체를 건너뜀
      (UmStop 만 보내면 충전이 끊기기만 함)
  - `_send_leave_mode_charge_stops`: UmStop 을 정해진 횟수 그대로 보냄.
    `_stop_charging_after_dock_work` 재사용 불가 — 그쪽의 `already stopped — skip`
    가드는 "충전을 멈추려는" 전제라, 릴레이 경쟁에서 밀려 충전이 꺼져 보이면 두 번째
    UmStop 을 건너뛰어 **ModeCharge 에 남은 채 hold 만 싸우는** 상태가 됨
  - `_wait_until_charge_relay_holds(since)`: **UmStop 이후 새로 도착한 표본**으로만
    판정. `_charging` 은 상태 스냅샷이 올 때만 갱신되는 캐시고 실측 주기가 약 5초라
    (06:30:10/:16/:21/:26/:31), 벽시계 창으로 판정하면 릴레이가 열리기 전에 찍힌
    표본을 보고 400ms 만에 "유지됨"이라 답하게 됨 → 릴레이 열린 채 ModeCharge 만
    빠져나온 상태를 성공으로 보고, SOC 떨어질 때까지 아무도 모름.
    `seconds_since_last_rx()` 로 프레임 도착 시각을 잡고, 연속 N개 새 표본이 충전을
    보고해야 성공. 깜빡임은 연속 조건으로 흡수
  - `handover_hold_settle_sec`(기본 3.0): `start_hold()` 는 `rostopic pub` 를 spawn 만
    하고 즉시 반환함. 등록 전에 UmStop 을 보내면 OpenChargingCircuit 이 먼저 이겨
    릴레이가 열린 채 남음. 그래서 hold 를 잡고 잠깐 기다렸다가 UmStop 을 보냄
- `adaptor/config/config.py` — `DockConfig` 에 4개 추가
  `handover_charge_to_relay_after_dock=True`, `handover_hold_settle_sec=3.0`,
  `handover_verify_timeout_sec=30.0`, `handover_verify_samples=2`
  (확인창에 `charge_circuit.verify_timeout_sec`(5s)를 재사용하지 않음 — 표본 주기
   약 5초라 새 표본을 한 번도 못 볼 수 있음)
- `adaptor/config/config.toml`, `config.toml` — 같은 키 문서화(기본 true)
- `adaptor/tests/test_dock_charge_handover.py` (신규 12건)
  - 표본 주기를 모사하는 `_TelemetryVehicle` 로 "낡은 캐시만으로는 인계를 확인할 수
    없다", "새 표본을 기다린다", "한 주기보다 늦은 재폐로도 성공", "끝내 안 붙으면 실패"
    를 각각 고정함
- `adaptor/tests/test_adapter_jibot_v3_order.py` — `FakeVehicle` 이 릴레이 hold 를
  모델링하도록 수정. 기존 fake 는 "UmStop = 충전 종료" 만 알고 있어 인계가 항상
  실패로 보였다(dock 테스트 10건 실패). `charge_relay_held` 중에는 UmStop 이
  ModeCharge 만 걷어내고 충전은 남는다. hold 를 vehicle 로 전달하는
  `_VehicleLinkedChargeCircuit` 을 `_make_adapter` 에 연결
  → dock/charg 서브셋 78건 전부 통과 (직전 10 실패)

### 테스트
- 신규 `tests/test_dock_charge_handover.py` 12건 통과
- `tests/test_adapter_jibot_v3_order.py -k "dock or charg"` 78건 통과 (수정 전 10 실패)
- `tests/test_order_motor_enable.py` 11건 통과 — `make_adapter` 에서 인계를 끔.
  이 파일이 보는 건 "모터 가드가 주행 명령 앞에 오는가"인데 UmStop 이 붙어 명령 순서
  단언이 깨졌고, `MotorVehicle` 은 릴레이 hold 를 몰라 인계가 항상 실패함
- 전체 스위트 `14 failed, 2256 passed` — 남는 실패 14건은 **이번 변경과 무관한 기존 실패**.
  pristine worktree(HEAD=37e2327)에서 동일하게 14 실패 재현 확인:
  `test_unknown_config_key`(pio 미지 키 ConfigError 미발생), `test_recipes_config`,
  `test_airshower_pairing_handover`, `test_airshower_passage_recipes`
  → 직전 커밋 "feat: error process logic updated" 계열. 이 세션 범위 밖

### 이 변경의 트레이드오프 — 결정 필요
relay hold 는 `rostopic pub -r` **자식 프로세스**로 유지된다
(`utils/charge_circuit.py:78,90`). 따라서 **어댑터가 재시작되면 hold 가 죽고 충전이 멈춘다.**
- 기존 동작: ModeCharge 가 남아 있어 어댑터 재시작과 무관하게 충전이 이어짐
  (대신 그게 이번 brake 무한루프의 원인)
- 8/25 하루에만 어댑터 재시작 3회(04:09, 06:19, 06:46) — 드문 일이 아님
- 재시작 후 충전 복구를 무엇이 책임질지 미정. 후보:
  (a) FMS 가 `charging=false` 를 보고 충전 오더 재발행 — 실제로 그러는지 미확인
  (b) hold 의도를 runtime 파일에 저장해 기동 시 같은 dock 노드면 재확보
  (c) 그대로 두고 운영으로 흡수
- 되돌리려면 `[dock] handover_charge_to_relay_after_dock = false`

### 설정 주의 (문서화함)
`[dock] obstacle_timeout_sec` 을 0 이하(무제한)로 두면, 인계 실패 후 재도킹 폴백의
대기도 무한이 된다. 폴백은 충전기에 앉은 채 UmDock 을 다시 거는 경로라 `#brake` 가
안 풀리면 오더가 영영 안 끝난다 — 이번에 고치려던 그 고착이다.
배포본에는 이 키가 없어 기본값 300.0 이 적용됨(확인함).

### 다음 — 전부 issue 로 이관함 (lab2m/unified-amr-adaptor)
- #5 충전 relay 인계 후 어댑터 재시작하면 충전이 멈춤 — 배포 전 결정 필요
- #6 인계 현장 검증 (UmStop 의 OpenChargingCircuit vs relay hold 승자)
- #7 1_01CH 도착 오더를 FMS 가 완료로 안 받아들임 (어댑터 state 는 완전)
- #8 테스트 스위트 상시 실패 14건 — HEAD 에서 재현, 새 회귀를 가림

(원문 유지)
- **배포 전 결정**: 어댑터 재시작 시 relay hold 가 죽어 충전이 멈추는 건을 어떻게 할지
  (위 트레이드오프 절). 정하기 전에는 로봇에 `handover_charge_to_relay_after_dock=true`
  를 올리지 말 것
- 현장 1회 검증 필요: UmStop 의 OpenChargingCircuit 과 relay hold 중 어느 쪽이 이기는지는
  실기에서만 확인됨. 로그로 `[ORDER NODE DOCK HANDOVER DONE]` 이 뜨는지 보면 됨
- **FMS 에서 1_01CH 충전 오더를 한 번 태울 것.** 캡처가 완료 직후 state 원문을 잡음.
  seq=0 오더(수락됨)와 seq=2 오더(거절됨)의 publish state 를 직접 diff 하면 확정됨
- A 건: 충전 성립 후 ModeCharge 이탈 처리 설계 필요 (UmStop? 별도 명령? 미검증)
- C 건: 세그먼트 오더 수용 방식 결정 필요 (큐잉 vs 즉시 교체)

### 검증
- 배포본/로컬 adapter_jibot.py md5 동일: fee695511aaca8b22cc96bb9f86d7c1e
- brake 구간: `nrunto 1#brake` 873표본, 05:06:02~06:20:13, 그 사이 비폴링 TX 0건
- 오프라인 재현: seq=2 / seq=0 두 경우 모두 완료 후 3초까지 state 완전 (드리프트 없음)
- 자연 실험 4건 로그 원문 확인 (journalctl, 00:33 / 03:49 / 06:28 / 06:30)
