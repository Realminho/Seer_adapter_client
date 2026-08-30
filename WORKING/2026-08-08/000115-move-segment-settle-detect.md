# move-segment-settle-detect

### 목표
- Task 4: mode="move" 세그먼트 완료 감지 (`_wait_for_vehicle_pose`, `_wait_until_move_settled`) 추가, 테스트 작성
- [Fix round 1] 코디네이터 리뷰 4건(F1~F4) 반영: 공허한 테스트 2건 교정, 코멘트 2건 교정, stopped_since 미보정 버그 수정+테스트 추가

### 지금
- 완료. Fix round 1 커밋 완료 (854c721)

### 완료 (Fix round 1)
- F1: test_obstacle_wait_blocks_the_stall_branch 공허 문제 수정 (`_status="Stopped"`, `_mode="auto #brake"`)
- F2: test_displacement_alone_satisfies_the_start_gate 공허 문제 수정 (start/stall 타임아웃 분리 + elapsed 검증)
- F3: 코멘트/어서션 정확도 2건 수정
- F4: `_wait_until_move_settled`의 pose-missing 분기에 `stopped_since = None` 추가 (adapter_jibot.py), 회귀 테스트 `test_pose_gap_does_not_advance_the_stall_clock` 추가
- F1/F2 non-vacuous 증명: 가드 제거 -> 실패 확인 -> 복원 -> 통과 확인 (양쪽 다 완료)
- 전체 클래스 15 passed, 전체 파일 회귀 6 failed/604 passed (round1 603 + 신규 1, pre-existing 실패만 유지)
- 커밋: 854c72131ac71325520b7689855f6f67a0a1e951
- 리포트에 Fix Round 1 섹션 추가 완료
- 참고: adaptor/config/extensions.hcl 이 세션과 무관한 외부 프로세스에 의해 반복 재작성됨(joystick ultimate2 enabled=true) → 매번 git checkout으로 되돌려 테스트 진행, 최종적으로 클린 상태 확인. 내가 커밋한 파일 아님.

### 완료
- adapter_jibot.py에 `_wait_for_vehicle_pose`, `_wait_until_move_settled` 추가 (4718-4841줄, `_wait_until_node_position_reached` 직후 `_resend_node_goto` 이전)
- test_adapter_jibot_v3_order.py에 `MoveSettleWaitTest` 14개 테스트 추가 (파일 끝, `if __name__` 이전)
- 실패 확인(AttributeError, 14 failed) -> 구현 복원 -> 통과 확인(14 passed, 3회 반복 무플레이키) -> 전체 회귀(6 failed, 603 passed = baseline 589 + 신규 14, 실패는 기존 3개 pre-existing만)
- 커밋: e542e865fdcdd5c569ada18101bdc2571b46d83f
- 리포트 작성: .superpowers/sdd/2026-08-07-move-segment-goto-fallback/task-4-report.md

### 다음
- (없음, Task 4 + Fix round 1 완료. Task 5가 두 메서드를 호출하도록 연결할 차례)

### 검증
- `bash scripts/run-tests.sh --python .../adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py::MoveSettleWaitTest -v` → 15 passed
- `bash scripts/run-tests.sh --python .../adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -q` → 6 failed, 604 passed (pre-existing 실패만, 증가 없음)
- F1/F2 가드 제거 후 재실행 → 의도대로 FAIL 확인, 복원 후 재실행 → PASS 확인
