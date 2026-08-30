# duplicate-action-p36-p2

### 목표
- p36 → p2 이동 전 action 이 두 번 동작하는 원인 확인 및 수정
- (추가 질문) p2 도착 후 rotateTo 가 뒤로 갔다 회전하는 이유 규명

### 지금
- 중복 action: 수정 + 회귀 테스트 완료
- rotateTo 후진: (a) pose 재조회로도 실기 후진 재현 → (b) UmDrive 제자리 회전 적용 완료

### 완료
- 원인 확정: **1회 수신 → 2회 실행**. 액션 실행 중 orderUpdate 1회당 재실행 1회
  1. `_finalize_v3_node_step` 이 도착 즉시 lastNodeSequenceId 를 올림 (액션 실행 전)
  2. `_is_start_node_with_pending_actions` 가 actions 존재 여부만 봐서 p36 재큐잉
  3. `_active_order_worker_step_is_obsolete` 가 액션 실행 중 스텝을 stale 로 보고 워커 취소·재시작
  4. dispatch 경로에 actionId 멱등 가드 없음
- 수정 (adaptor/adapter_jibot.py)
  - `_dispatched_order_action_ids` 원장 신설 (주문 수락/취소 시 리셋)
  - `_order_action_is_pending()` 신설 → `_is_start_node_with_pending_actions` 가 이걸 사용
  - `_order_action_step_in_flight` 로 액션 실행 중 스텝은 stale 판정에서 제외
  - `_process_v3_step_actions` 에 `[ORDER ACTION SKIP]` 멱등 가드
- 회귀 테스트 4건 추가 (test_adapter_jibot_v3_order.py): RED 확인 후 GREEN
- 주석 전부 한국어 (CLAUDE.md 규칙)

### 다음
- 62 재배포 후 실기 확인 (조그 회전)
  - **rot 부호 규약**: 설계문서상 좌+/우−. 반대로 돌면 코드가 아니라
    `settings.rotate_to_rot_sign = -1` 로 뒤집는다 (타임아웃 로그에 last= 각도 남음)
  - **rot 단위 미검증**(deg/s 인지 비율인지). 지나치면 `rotate_to_slow_rot` 낮추거나
    `rotate_to_tick_sec` 줄인다. th 보고 분해능이 ±1도라 tolerance 는 3도 유지
  - 되돌리려면 `settings.rotate_to_mode = "goto"`
- 남은 실패 `test_manual_drive_rejected_while_order_active` 는 joystick 작업 쪽 기대값 갱신 필요

### rotateTo 후진 (2번째 질문)
- 원인: rotateTo = `UmGoto target=pose poseX/poseY=<캐시된 x/y> poseTh=<목표각>`.
  캐시는 main.py `robot_info_loop(interval_sec=1)` 결과라 최대 1초 묵음.
  아직 굴러가는 중이면 목표가 뒤에 찍혀 후진 후 회전. localize 직후면
  재앵커 반영(실측 1.3s) 전이라 오차가 미터 단위.
- 수정 (a): `_rotate()` 안에서 ① `_wait_until_automatic_motion_stopped()` 로 정지 확인
  ② `_refresh_vehicle_pose()` 로 UmGetRobotInfo(gap=200) 재조회 ③ 그 뒤에 pose 캡처.
  pose 미상 FAILED 판정도 재조회 뒤로 이동.
- `_refresh_vehicle_pose()` 는 응답 큐를 소비하지 않음. jibot-client 에
  `_status_snapshot_at` 스탬프를 추가해 그것만 관찰 (큐는 UmGoto accept 대기와 공유).
- 스탬프/`um_get_robot_info` 없는 구현(시뮬레이터·테스트 페이크)은 캐시 폴백.
- 회귀 테스트 3건 추가 (test_rotate_to_action.py), RED→GREEN

### rotateTo — (b) UmDrive 제자리 회전 (최종 채택)
- (a) pose 재조회 적용 후에도 실기에서 후진 재현 → 원인은 좌표 지연이 아니라
  urobot 의 pose-goal 접근 기동. `target=pose` 는 늘 경로로 풀린다.
- 구현: `_rotate_to_with_jog()` 폐루프
  - 짧은 쪽 각오차 → `um_drive(trans=0, rot=±, speed, lat)` 연속 속도
  - slow zone 안에서 감속, tolerance 안에 들면 `um_stop()`
  - 매 tick `_refresh_vehicle_pose()` (기본 폴링 1Hz 로는 한 tick 에 지나침)
  - 타임아웃 → UmStop + FAILED(마지막 heading 기록)
  - **양보 가드**: blockingType=NONE 이라 배경에서 도는 동안 워커가 다음 노드로
    출발하면(`_order_node_motion_seq` 변화) 회전을 접고 UmStop 도 보내지 않는다.
    안 그러면 조그가 UmGoto 와 싸우고 UmStop 이 남의 주행을 세운다.
- 폴백: `settings.rotate_to_mode = "goto"` 또는 UmDrive 없는 구현(시뮬레이터)
- 노브를 Settings 에 정식 추가: rotate_to_mode / tolerance_deg / timeout_sec /
  rot / slow_rot / slow_zone_deg / speed / lat / rot_sign / tick_sec
  (tolerance_deg·timeout_sec 는 그동안 getattr 기본값이라 config.toml 로 못 바꿨다)
- 회귀 테스트 7건 추가 (조그 6 + 양보 1), 기존 goto 테스트 6건은 폴백 모드로 고정

### 검증
- tests/test_adapter_jibot_v3_order.py: **724 passed, 1 failed**
  - 실패 1건 `test_manual_drive_rejected_while_order_active` 는 내 변경과 무관.
    사용자 joystick 작업(`_order_motion_in_flight` 신설)으로 게이트가 완화됐는데
    테스트가 안 따라감. 8e23059 에서는 통과, 현재 HEAD 에서 실패.
- 최종 전체 스위트: **2221 passed / 13 failed**.
  13건(airshower 5 + recipes_config 4 + unknown_config_key 4)은 8e23059 워크트리에서도
  동일하게 실패 → 전부 사전 실패, 이번 변경과 무관
