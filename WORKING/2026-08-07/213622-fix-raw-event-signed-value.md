# fix-raw-event-signed-value

### 목표
- Linux raw input의 음수 축 값을 올바르게 표시한다.

### 지금
- 수정과 검증을 완료했다.

### 완료
- input_event value 필드를 signed 정수로 수정했다.

### 다음
- USB 동글 연결 해제 원인을 커널 로그로 확인한다.

### 검증
- `py_compile`, 음수 -490 decode, `git diff --check` 성공
