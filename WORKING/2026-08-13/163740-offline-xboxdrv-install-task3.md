# offline-xboxdrv-install-task3

### 목표
- Task 3: setup 중 번들 xboxdrv .deb 설치 (install_amr_xboxdrv_service 게이트 순서 변경 + install_bundled_offline_debs 호출)

### 지금
- 완료. 커밋 d0577a5 생성함.

### 완료
- 테스트 작성 (RED 확인) -> install_amr_xboxdrv_service 게이트 순서 변경 (xpad 먼저, binary 나중) + install_bundled_offline_debs 호출 추가 (GREEN)
- docs/manual/joystick-runtime-setup.md 설치 섹션 갱신
- 커밋: d0577a5 feat: install xboxdrv from the offline deb bundle during setup

### 다음
- (완료, 후속 작업 없음)

### 검증
- ./scripts/run-tests.sh --python .../adaptor/.venv/bin/python -o addopts="" ../tests -q -> 145 passed, 2 failed (기존 baseline과 동일한 2개만 실패)
- --dry-run 출력 확인: "==> amr-xboxdrv.service (offline deb bundle)" / "[dry-run] would install bundled offline debs from .../scripts/offline-debs/xboxdrv" 정상 출력, unit도 계속 렌더링됨
