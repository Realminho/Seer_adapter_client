# ssh-key-passphrase

### 목표
- 192.168.101.62 SSH 키 passphrase 반복 요청 해결

### 지금
- ssh -v 결과로 원인 확정 및 해결 명령 안내

### 완료
- 서버가 공개키를 승인하며 로컬 키 passphrase 단계에서 멈추는 것을 확인

### 다음
- ssh-agent에 키 등록하거나 키 passphrase 제거

### 검증
- `Server accepts key` 이후 `Enter passphrase for key` 로그 확인
