# clamp-servo-auto-cycle

### 목표
- clamp/unclamp 시 servo가 off면 on → 동작 → 다시 off까지 이어지게 함
- servo 자동 off를 기본 동작으로 두고, 유지 여부는 설정 옵션으로 선택

### 지금
- 구현·리뷰 완료. 브랜치 마무리(merge/PR) 방식 결정 대기

### 완료
- 설계 `docs/superpowers/specs/2026-07-29-clamp-servo-auto-cycle-design.md`
- 계획 `docs/superpowers/plans/2026-07-29-clamp-servo-auto-cycle.md`
- Task 1~6 구현 + 태스크별 리뷰 통과, 전체 브랜치 리뷰의 7개 수정까지 반영
- develop에 16개 커밋 (`edb2a0c..e83a41d`)

### 다음
- **실기 확인 필수**: `clampOff` → `clamp` 한 번 → 모터가 실제로 닫히고 끝나면 servo가 꺼지는지
- 특히 `FFLAG_SERVOON`이 실제 여자 상태를 따라가는지, 아니면 enable 명령을 그대로 되돌려주는 echo인지 확인. echo라면 현장 버그가 안 고쳐진 것임

### 검증
- `cd adaptor && PYTHONPATH= uv run pytest -q` → 전체 통과 (clamp 96, config 57 직접 확인)
- 주의: 앞에 `PYTHONPATH=` 없으면 ROS 경로 때문에 collection이 깨짐
