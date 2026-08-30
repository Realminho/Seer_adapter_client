# webui-action-parameters

### 목표
- WebUI action 입력에 action별로 필요한 파라미터만 이름과 함께 표시한다.

### 지금
- 구현과 검증을 완료했다.

### 완료
- ActionSpec 파라미터 스키마를 추가하고 extension/recipe registry와 WebUI에 전달했다.
- action별 선언 필드만 이름과 함께 렌더링하고 필수값 및 JSON 파라미터를 서버에서 검증한다.
- clamp, PIO, EZIO, facility action에 실제 요구 파라미터를 선언했다.

### 다음
- 배포 시 amr-webui 서비스를 재시작해 반영한다.

### 검증
- 비서버 관련 203 tests + 2 subtests 통과, WebUI 서버 110 tests 통과, 신규 회귀 2 tests 통과, compileall 및 git diff --check 통과.
