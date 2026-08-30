# adaptor-services-install-remove

### 목표
- `scripts/adaptor-services.sh`에 `install`과 `remove` 명령을 추가하고 기존 설치/제거 스크립트를 확인한다.

### 지금
- 구현과 검증을 완료했다.

### 완료
- `install`은 전체 설치 스크립트로, `remove`는 전체 제거 스크립트로 인자와 함께 위임하도록 추가했다.

### 다음
- 대상 장비에서 `./scripts/adaptor-services.sh install` 또는 `remove`를 사용한다.

### 검증
- `bash -n` 통과, 서비스 스크립트 테스트 18개 통과, 기존 `start --dry-run` 및 도움말 동작 확인.
