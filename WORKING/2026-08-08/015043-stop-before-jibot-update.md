# stop-before-jibot-update

### 목표
- Jibot 원격 업데이트를 서비스 종료 → 설치 → 서비스 시작 순서로 변경

### 지금
- 작업 완료

### 완료
- 기본 배포 흐름을 서비스 stop → 설치 → start로 변경
- 설치 실패 시 서비스 start 복구를 시도하도록 처리

### 다음
- 필요 시 실제 로봇 대상으로 배포 순서 확인

### 검증
- bash -n 통과, git diff --check 통과, --help 출력 확인 (shellcheck 미설치로 생략)
