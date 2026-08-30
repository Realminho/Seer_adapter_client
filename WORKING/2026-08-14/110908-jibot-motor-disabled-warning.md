# jibot-motor-disabled-warning

### 목표
- Task 2: `#disable` 토큰 → `JIBOT_MOTOR_DISABLED` WARNING (TDD)

### 지금
- 완료, 커밋 c3723f6 생성 완료

### 완료
- MotorDisabledTokenTest 2개 테스트 추가 (RED 확인 후 GREEN)
- ErrorType.JIBOT_MOTOR_DISABLED 추가, _is_jibot_motor_disabled 헬퍼 구현, _refresh_jibot_status_errors purge/발행 배선
- 회귀 테스트 23개 통과, 전체 스위트 1351개 중 실패 6개는 모두 손대지 않은 test_adapter_jibot_v3_order.py (범위 외, 코디네이터가 base 커밋에서 독립 검증한 pre-existing 실패)

### 다음
- (완료, 후속 작업 없음)

### 검증
- cd adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_errors -v → 6 tests OK
- cd adaptor && .venv/bin/python -m unittest tests.test_jibot_stop_reason tests.test_jibot_stop_reason_apply tests.test_jibot_safety_info -v → 23 tests OK
