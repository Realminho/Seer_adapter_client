# pio-init-ping-test-failures

### 목표
- `test_adapter_jibot_v3_order`의 6건 실패(pio_init/pio_ping) 근본 원인 규명 후 수정
- 이후 feat/move-segment-goto-fallback → develop 머지 + push (옵션 1)

### 지금
- 전부 완료. develop = `36ccf28`, origin과 0/0 동기화

### 완료 — 6건 실패 수정
- 실패 6건 = 테스트 3개 × 2클래스(`ManualControlInstantActionTest(AdapterV3OrderTest)` 상속)
- 근본 원인: 3개 테스트가 GO 재시도 동작을 검증하는데 `pair_confirmation`을 고정하지 않음.
  기본값이 `bc_reply`(config.py:693, `pio_text` 폴백, config/extensions.hcl:69)라
  `pio_establish`(extensions/pio/__init__.py:343)가 단발 분기로 들어가 BC 1회·응답 프레임으로 성공 판정
  → `bcAttempts` 항상 1, `pair_after`/`never_pairs` 무효화
- 프로덕션 버그 아님. 실배포 설비가 bc_reply 장비 → 기본값 유지, 3개 테스트만 `"go"` 고정
- 부수 발견: 기본값 전환 이후 이 3개가 조용히 bc_reply 경로를 검증 중이었음(GO 경로 e2e 커버리지 상실) → 복구됨
- 커밋 `baedcb9`

### 완료 — 커밋 정리 (동시 세션과 병행)
- `7bea46e feat(scripts)`: wifi keeper v2 + systemd 유닛 3개 (untracked였던 5파일)
- `a558408 docs(working)`: WORKING 로그 9개 + pio ownership plan
- airshower `000030` 묶음은 동시 세션이 `3bae3e4`로 직접 커밋 → 내가 안 건드림

### 완료 — develop 반영
- `36ccf28 Merge branch 'develop' into feat/...`: 문서 add/add 충돌 2건을 feat 쪽(최신)으로 해결
  - spec: develop 사본이 feat 원본 `52fb473`과 바이트 동일, feat엔 개정 `b7c0f54`가 더 있음
  - plan: develop 사본은 사라진 worktree의 `.venv` 경로를 박아둔 stale 버전
- develop은 `--ff-only`로 feat까지 전진 → 두 브랜치 트리 동일, `git log develop..feat` 비어 있음
- `git push origin develop` → `5d1e58e..36ccf28`, 0/0 동기화

### 다음
- (이 작업 종료) origin/feat/move-segment-goto-fallback은 미push 상태로 남김
- 동시 세션이 계속 작업 중 — 이후 커밋은 develop에 아직 없음

### 검증
- 실패 재현 → `pair_confirmation="go"` 고정 후 해당 테스트 단독 OK
- 파일 단위: `unittest tests.test_adapter_jibot_v3_order` → Ran 662, OK
- 수정 직후 전체: `unittest discover -s tests` → Ran 1463, OK
- develop 머지 후 전체: `unittest discover -s tests` → Ran 1478, OK
- develop은 feat로 fast-forward라 트리가 동일(`git diff develop feat` 비어 있음) → 재실행 생략
