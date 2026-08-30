# order-completion-criteria-review

### 목표
- goto 경로 노드 완료 시 도착(pose)만 보던 것을 자동 주행 정지(standstill)까지 확인하도록 보강

### 지금
- 구현 완료, 전체 테스트 통과 확인

### 완료
- 완료 조건 조사: goto=위치만, move=주행정지+이동거리, dock=충전 시작
- TDD 3사이클: (1) goto 태스크 유지 시 미완료 (2) standstill 타임아웃 시 도착완료로 폴백 (3) move 폴백 goto도 동일
- `_settle_goto_arrival()` 추가, `_process_v3_node_step`/`_run_move_segment` 두 goto 지점에 적용
- 기존 fixture 4건에 `_stop_driving_on_arrival()` 적용(도착 시 goto 태스크 종료 모델링)

### 다음
- 없음. (사용자 판단) 커밋 여부

### 검증
- scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -> 665 passed
- scripts/run-tests.sh (전체) -> 2005 passed, 1 failed
  - 실패 1건 tests/test_goto_nearest_node.py::test_timeout_stops_robot_and_fails 는
    본 변경 이전부터 실패(변경 되돌리고 재현 확인) — gotoNearestNode instant action 경로, 무관
