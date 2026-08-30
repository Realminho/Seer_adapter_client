# bundled-offline-deb-installer

### 목표
- Task 1: scripts/setup-adaptor-service.sh 에서 카메라 서비스 오프라인 deb 설치 인라인 블록을 install_bundled_offline_debs() 함수로 추출 (behavior-preserving refactor)

### 지금
- 완료. 커밋 3d4e39f.

### 완료
- install_bundled_offline_debs() 헬퍼 추가, 카메라 인라인 블록을 헬퍼 호출로 교체, 테스트 갱신, 커밋 완료

### 다음
- (없음 — Task 1 완료)

### 검증
- ./scripts/run-tests.sh --python .../adaptor/.venv/bin/python -o addopts="" ../tests → 139 passed, 2 failed (기존 알려진 실패 2건과 동일)
- dry-run before/after diff → IDENTICAL
