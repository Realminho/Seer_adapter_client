# check-mqtt-retained

### 목표
- 연결 정보(connection)가 MQTT retained 메시지로 발행되는지 확인

### 지금
- 확인 완료

### 완료
- 작업 복구 로그 생성
- JIBOT/Hexplorer의 connection 발행과 MQTT Last Will retain 설정 확인

### 다음
- 없음

### 검증
- main.py 기동/종료 호출, adapter publish, transport, paho publish/will_set 경로를 정적 확인
