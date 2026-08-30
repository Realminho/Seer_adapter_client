# check-instant-actions-topic

### 목표
- actionId `01KYH2JSE707JFVXD37KFD0NBF`의 `gotoNearestNode`가 실행되지 않은 원인을 진단한다.

### 지금
- 로컬 호스트에는 서비스가 없어 현장 로그를 직접 대조할 수 없으며, 소스·설정 기반 원인을 확정해 정리했다.

### 완료
- payload는 정상 파싱되며 `gotoNearestNode` 분기로 연결된다. 현재 robots.toml broker가 발행 확인에 사용한 `192.168.101.50`이 아니라 `192.168.2.61`인 점과, 연결 전 subscribe가 `MQTT_ERR_NO_CONN`으로 유실되는 결함을 확인했다.

### 다음
- 현장 `ucore@ubuntu`에서 15:00:49 전후 journal 로그와 실제 broker 설정을 대조한다.

### 검증
- 동일 payload를 `parse_instant_actions`로 파싱 성공; 미연결 Paho client의 subscribe 반환값은 `(MQTT_ERR_NO_CONN, None)`임을 재현했다.
