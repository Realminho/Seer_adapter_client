# check-ssh-update-scope

### 목표
- SSH 업데이트 시 PathPoint 및 nearest-to-last 설정 변경 적용 범위 확인

### 지금
- 스크립트 배포 정책 확인 완료

### 완료
- Python 소스는 항상 업로드됨을 확인
- 기존 원격 config.toml은 기본 keep이므로 새 활성화 설정은 자동 반영되지 않음을 확인
- 서비스 재시작도 기본 비활성임을 확인

### 다음
-

### 검증
- update-jibot-adapter-over-ssh.sh의 staging, config mode, restart 옵션 정적 확인
