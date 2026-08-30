# joystick-runtime

### 목표
- Ultimate 2와 Micro 입력을 실제 AMR 수동주행/정지 실행 경로에 연결한다.

### 지금
- Micro runtime service 구현을 검증하고 문서화한다.

### 완료
- Micro D-pad를 UmDrive/UmStop, heartbeat/watchdog, 재연결 처리에 연결했다.

### 다음
- simulator 또는 바퀴를 띄운 상태에서 실제 입력 시험을 수행한다.

### 검증
- joystick/runtime/extensions 32 tests 및 2 subtests, py_compile 통과
