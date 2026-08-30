# offline-xboxdrv-fetch-helper

### 목표
- Task 2: scripts/fetch-offline-debs.sh + scripts/offline-debs/xboxdrv/ 번들 + tests/test_offline_deb_fetch.py 추가 (brief 그대로)

### 지금
- 완료. 커밋 f5e83e9.

### 완료
- tests/test_offline_deb_fetch.py 작성 (RED 확인)
- scripts/fetch-offline-debs.sh 작성 (PACKAGES=( 주석 자기참조 버그 수정)
- 실제 다운로드 완료: xboxdrv/libdbus-glib-1-2/libusb-1.0-0, 브리프 표와 바이트 일치
- idempotence + --check 확인
- scripts/offline-debs/xboxdrv/README.md 작성
- 전체 스위트 144 passed, 2 failed (기존 2건과 동일)
- 커밋 f5e83e9 "feat: stage xboxdrv debs for offline robots"

### 다음
- 없음 (Task 2 완료). Task 3 이 setup-adaptor-service.sh 에서 install_bundled_offline_debs 로 이 디렉터리를 설치.

### 검증
- ./scripts/run-tests.sh --python .../.venv/bin/python -o addopts="" ../tests → 144 passed, 2 failed
