# unify-mqtt-config-logging

### 목표
- MQTT 설정 우선순위 명확화, 연결 로그 보강, WebUI robots.toml 편집 추가

### 지금
- 구현 및 검증 완료

### 완료
- MQTT 시작/성공/실패/끊김 로그에 최종 broker, robot, client/topic 정보 추가
- WebUI Robots 원문 편집·검증·실패 원복 기능과 override 안내 추가
- config.toml의 중복 가능 필드에 robots.toml override 설명 추가

### 다음
- 서비스 배포 후 WebUI 저장과 journalctl 로그를 실제 환경에서 확인

### 검증
- WebUI 서버 103개, 관련 MQTT/설정 163개, 추가 집중 테스트 6개 통과
- py_compile 및 git diff --check 통과
