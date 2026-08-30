# ultimate2-control-mapping

### 목표
- 설치된 Ultimate 2 동글을 확인하고 deadman, 모터 ON/OFF, hold-to-release 브레이크 권장 매핑을 추가한다.

### 지금
- 작업 완료.

### 완료
- 8BitDo UM 2 Receiver(2dc8:3109)와 xpad/joydev 로드를 확인하고 USB receiver 표시를 추가.
- deadman hold, motor ON/OFF 조합·길게 누르기, 정지 및 브레이크 안전 의미를 매핑과 문서에 추가.

### 다음
- 컨트롤러 전원/2.4GHz 연결 후 `/dev/input/jsN`의 실제 버튼 번호를 측정한다.

### 검증
- joystick 테스트 6개, JSON 구문 및 git diff --check 통과. Receiver 감지, joystick 미연결 상태 확인.
