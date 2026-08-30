# task3-clamp-servo-on-and-wait

### 목표
- Task 3: clamp move 전에 FFLAG_SERVOON을 폴링으로 확인하는 `_servo_on_and_wait`/`_axis_flags` 구현

### 지금
- 완료. task-3-report.md 작성 완료

### 완료
- Step1 테스트 4개 추가 + manual 정책 테스트에 assertNotIn 1줄 추가
- Step2 실패 확인 (8 failed, 2 passed — 기대대로)
- Step3 `_enable_servo_for_motion` 삭제, `_axis_flags`/`_servo_on_and_wait` 구현, 호출부 3곳 교체
- Step4 통과 확인 (clamp -k: 40 passed), 전체 스위트 1445 passed
- Step5 커밋: 01733ac (기존 무관 WIP는 손대지 않고 git apply --cached로 내 hunk만 스테이징)

### 다음
- 없음. Task 4(이동 완료 대기, `_wait_motion_done`)로 넘어가면 됨

### 검증
- `git diff --stat 418e8fc HEAD` == 딱 두 파일, 142줄만 변경됨 확인
- `git status`로 기존 WIP 그대로 남아있음 확인 (파괴 없음)
- repo-root config.toml/extensions.hcl/robots.hcl 미변경(untracked) 확인
