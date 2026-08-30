# goto-nearest-lastnode-attribution

### 목표
- test_goto_nearest_node.py::test_timeout_stops_robot_and_fails 실패 해결
- 타임아웃된 gotoNearestNode가 도달 못 한 노드를 lastNodeId로 남기지 않게 보장

### 지금
- 완료. (가)안 설계 논의만 남음

### 완료
- _set_last_node 래핑해 호출자 추적 -> _capture_idle_proximity(adapter_jibot.py:8237)
- 테스트 실행 시 capture mode = "proximity" (dataclass 기본값은 "settled"인데
  저장소 config.toml이 proximity로 덮음), idle reach = 100.0
- 핸들러 결함이 아니라 pose 기반 proximity 미러가 1000mm 떨어진 N3를 lastNodeId로 세팅
- 같은 저장소의 다른 테스트들은 이미 mode를 명시 고정해 둠
  (test_adapter_jibot_v3_order.py:1461/6108/6155 "deployment default is now proximity")
  -> test_goto_nearest_node.py만 고정 누락된 드리프트

- 하니스에 last_node_capture_mode="disabled" 고정 (기존 use_nearest... 고정과 같은 사유)
  -> lastNodeId assertion이 정책이 아니라 핸들러 계약을 검증하게 됨
- 회귀 테스트 추가: test_timeout_does_not_credit_the_unreached_node_under_settled_capture
- 판별력 확인: 같은 시나리오에서 settled -> '' / proximity -> 'N3'

- [승인됨] 저장소 config.toml, adaptor/config/config.toml 기본값 proximity -> settled
- [승인됨-나안] 에러 레벨 게이트는 지금 만들지 않음. gotoNearestNode가 CRITICAL 상황에서도
  동작하는 것을 회귀 테스트로 고정:
  test_goto_nearest_node_still_runs_while_a_critical_error_is_active
  (LAST_NODE_ID_MISSING/CRITICAL 존재를 assert로 먼저 확인한 뒤 FINISHED 검증)

- 기본값 변경이 test_active_order_state_loop_updates_last_node_from_nearest_position을 깨뜨림
  -> 해당 테스트가 proximity 동작 검증용인데 모드 고정이 없던 동일 드리프트. 모드 명시 고정으로 해결

### 다음
- '가'안은 docs/todo/action-error-level-gating.md 로 기록함 (착수 전)
- dock 수정(adapter_jibot.py) .61 배포 여부 미결

### 검증
- scripts/run-tests.sh (전체) -> 2044 passed, 0 failed
  (어제부터 실패하던 test_timeout_stops_robot_and_fails 포함 전부 통과)
