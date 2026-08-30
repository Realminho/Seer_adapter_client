# task5-clamp-teach-servo-cycle

### 목표
- clampTeach 분기를 servo on/off 사이클로 감싼다 (task-5-brief.md 그대로)

### 지금
- 완료. 커밋 9c7ade8.

### 완료
- 테스트 2개 추가(clamp_teach_runs_inside_the_servo_cycle, clamp_teach_leaves_servo_alone_when_manual) → 실패 확인
- clampTeach 분기를 _servo_on_and_wait/_disable_servo_after_motion으로 감쌈 → 통과 확인
- 두 파일의 무관한 WIP과 분리해 내 hunk만 git apply --cached로 스테이징 후 커밋(9c7ade8)

### 다음
- (task 5 완료, 후속 task 있으면 새 세션 파일에서 진행)

### 검증
- `cd adaptor && PYTHONPATH= uv run pytest tests/test_adapter_jibot_v3_order.py -k clamp -q` → 60 passed
- `git diff --stat 9bf988b HEAD` → 2 files changed, 52 insertions(+), 4 deletions(-) (의도한 hunk와 일치)
- 전체 스위트: test_pio_* 13건 실패는 사전 공지된 무관 WIP 이슈, clamp 관련 실패 없음
