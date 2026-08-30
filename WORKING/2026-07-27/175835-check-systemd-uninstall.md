# add-systemd-uninstall

### 목표
- AMR adaptor/WebUI/camera 설치 항목을 안전하게 제거하는 스크립트 추가

### 지금
- 구현 및 검증 완료

### 완료
- scripts/uninstall-adaptor-service.sh 추가
- adaptor/WebUI/camera 유닛과 인스턴스, sudoers, polkit, tmpfiles/runtime 제거 구현
- 사용자 설정·credentials·venv·journald·linger 보존 및 dry-run 지원
- 관련 회귀 테스트 2개 추가

### 다음
- 사용자에게 사용법 전달

### 검증
- bash -n 통과
- scripts/uninstall-adaptor-service.sh --dry-run 정상
- pytest -q tests/test_adaptor_service_scripts.py: 17 passed
- git diff --check 통과
