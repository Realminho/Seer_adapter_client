# task1-clamp-servo-config

### 목표
- EziConfig에 clamp_servo_on_timeout_sec / clamp_motion_start_timeout_sec / clamp_motion_timeout_sec 필드 추가
- clamp_servo_policy 기본값을 auto_on_keep_on → auto_on_auto_off로 전환

### 지금
- Task 1 완료, 커밋 완료 (093a65a)

### 완료
- test_config.py: test_clamp_servo_policy_default 교체 + test_clamp_servo_timeout_defaults 추가 → 예상대로 실패 확인
- config.py: clamp_servo_policy 기본값 auto_on_auto_off로 전환, timeout 필드 3개 추가
- extensions.hcl: clamp_servo_policy 값 한 줄만 auto_on_auto_off로 전환
- 기존 작업트리에 무관한 미커밋 변경(WIP: pio_port 개명 등)이 같은 3개 파일에 섞여 있어, git apply --cached로 내 변경분만 골라 스테이징 후 커밋 093a65a로 분리 확정
- 리포트 작성: .superpowers/sdd/2026-07-29-clamp-servo-auto-cycle/task-1-report.md

### 다음
- (이 세션 종료) Task 2부터는 다른 세션/에이전트가 이어감

### 검증
- cd adaptor && PYTHONPATH= uv run pytest tests/test_config.py -q → 43 passed
- cd adaptor && PYTHONPATH= uv run pytest -q → 전체 통과 (1421 passed)
