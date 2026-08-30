# commit-and-smoke-test-default-ports

### 목표
- 포트 및 camera 상태 표시 변경을 실제 WebUI로 실증하고 관련 파일만 커밋

### 지금
- 실증 및 선별 커밋 완료

### 완료
- 실제 HTTP camera 화면 확인 및 관련 변경만 adb3f82로 커밋

### 다음
- 9000 점유 프로세스와 amr-camera.service 설치 상태는 운영 환경에서 확인

### 검증
- HTTP 401/auth 화면/중지됨 배지/:9001 링크 확인; WebUI 199 passed, scripts 20 passed
