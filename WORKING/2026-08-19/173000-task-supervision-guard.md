# task-supervision-guard

### 목표
- 2026-08-18 AMR1 사고(부분 배포로 publish_state Task가 조용히 사망 → state 16분간 미발행)
  재발 방지: 어댑터 장수 Task가 죽으면 시끄럽게 실패하도록 감시 콜백 추가

### 지금
- 구현/테스트 완료. 커밋·배포는 사용자 판단 대기

### 완료
- TDD RED: `adaptor/tests/test_background_task_supervision.py` 4케이스 작성 →
  `AttributeError: 'Adapter' object has no attribute '_supervise_task'`로 전부 실패 확인
- GREEN: `adapter_jibot.py`에 `_supervise_task` / `_on_supervised_task_done` /
  `_terminate_after_task_loss` 추가, `run_adapter`의 장수 Task 4개 배선
  (publish_state, subscribe_acs_cmd, control_socket, monitor_jibot_connection)
- 취소(정상 종료)는 fault로 보지 않음. 예외/정상 리턴은 둘 다 fault로 처리
  (감시 대상 4개는 모두 `while True`라 리턴 자체가 버그)
- 종료 방식: 로그 + stdout/stderr flush + `os._exit(1)` → systemd `Restart=on-failure`
  (RestartSec=3, 실측 확인)가 재시작, MQTT Last Will로 FMS는 OFFLINE 인지
- 변이 검증: publish_state 배선만 되돌리면 wiring 테스트가 실패, 되살리면 통과
- 전체 스위트: 2028 passed / 1 failed (`test_goto_nearest_node.py::
  test_timeout_stops_robot_and_fails`) — HEAD의 adapter_jibot.py로 되돌려도 동일하게
  실패하므로 이 변경과 무관한 선행 실패
- 정상 종료 오탐 없음 확인: main.py `finally`는 `_vehicle`을 None으로 만들지 않고
  state publish 호출부는 이미 try/except → 종료 중 허위 exit 경로 없음

### 다음
- (사용자 판단) 커밋 후 `scripts/update-jibot-adapter-over-ssh.sh`로 전체 트리 배포
- 주의: 이 워크트리에 다른 세션의 미커밋 변경이 다수 섞여 있음(adapter_jibot.py 포함)

### 검증
- scripts/run-tests.sh tests/test_background_task_supervision.py → 4 passed
- 변이 테스트로 wiring 테스트의 유효성 확인
