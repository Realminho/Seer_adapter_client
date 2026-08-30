# ultimate2-runtime

### 목표
- Ultimate 2의 확인된 축과 R1을 실제 AMR 수동주행 runtime에 연결한다.

### 지금
- 구현과 자동 탐지 상태를 검증하고 문서화한다.

### 완료
- R2/L2/왼쪽 스틱/R1을 UmDrive/UmStop과 heartbeat/watchdog에 연결했다.

### 다음
- controller를 깨워 310b/js0 상태에서 저속 실입력 시험을 수행한다.

### 검증
- joystick/runtime/extensions 35 tests 및 2 subtests, py_compile 통과; 현재 receiver-only 3109 확인
