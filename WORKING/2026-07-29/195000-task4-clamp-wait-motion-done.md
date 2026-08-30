# task4-clamp-wait-motion-done

### 목표
- move ACK 직후 servo를 끄던 auto_on_auto_off 정책을 이동 완료까지 대기하도록 수정
- `_wait_motion_done` 추가, 이동 분기 5개 호출 지점에 연결

### 지금
- Task 4 완료(c903dc8), fix round 1 완료(9bf988b) — clampHome은 ORIGINRETURNING/ORIGINRETOK로 판정

### 완료
- Step 1~2: 실패하는 테스트 4개 추가, 실패 확인 (4 failed, 4 passed in 0.70s)
- Step 3: `_wait_motion_done` 구현 + 6개 호출 지점 연결(브리프는 5개라 했으나 clampMoveTo 분기도 필요해서 추가), manual 정책 가드 추가
- Step 4~5: clamp 슬라이스 48 passed in 2.20s(느려진 기존 테스트 12개에 타임아웃 단축 적용), 전체 스위트 1455 passed in 149.86s
- Step 6: 커밋 c903dc8 (isolated hunks only, `git diff --stat 01733ac HEAD` = 184 insertions)
- 리포트: `.superpowers/sdd/2026-07-29-clamp-servo-auto-cycle/task-4-report.md`

- fix round 1: clampHome/goto_origin만 `_wait_origin_done`(FFLAG_ORIGINRETURNING/ORIGINRETOK)로 교체, 나머지 5개 호출지점은 `_wait_motion_done` 유지. manual 정책 문서화(코드 docstring + 설계 문서 §3.1/3.4/3.5/5)
- 새 테스트 4개(clampHome origin-return) 추가, RED(4 failed) 확인 후 구현, GREEN(8 passed in 0.60s) 확인
- 커밋 9bf988b (isolated hunks only, `git diff --stat c903dc8 HEAD` = 226 insertions/12 deletions across 3 files)
- 전체 스위트 재실행: 1 failed(무관한 pio 테스트, 격리 실행 시 통과 — pio 테스트 간 상태 오염, 범위 밖) / 1468 passed in 149.89s

### 다음
- (없음, 완료. pio 테스트 flakiness는 별도 트리아지 필요 — 이 작업 범위 밖)

### 검증
- baseline: `cd adaptor && PYTHONPATH= uv run pytest tests/test_adapter_jibot_v3_order.py -k "clamp" -q` → 40 passed in 1.40s
- Task 4 최종: 같은 명령 → 48 passed in 2.20s / 전체 스위트 1455 passed in 149.86s
- fix round 1 최종: 같은 명령 → 56 passed in 2.72s / 전체 스위트 1468 passed, 1 failed(무관, pio flakiness) in 149.89s
