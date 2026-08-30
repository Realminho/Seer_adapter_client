# hana-simulator-robot

### 목표
- 1번 로봇만 포함한 hana 이름의 robots HCL 파일을 만든다.

### 지금
- 생성 및 검증 완료.

### 완료
- `adaptor/config/robots-hana.hcl`에 `HN-SH6-TR-001` 한 대만 추가했다.
- 기존 simulator 및 MQTT 설정을 유지했다.

### 다음
- 없음.

### 검증
- fleet loader가 1개 로봇 `HN-SH6-TR-001`을 정상 로드했다.
- `run-adapter.sh`가 `run-main.sh` 단일 실행으로 dispatch했다.
