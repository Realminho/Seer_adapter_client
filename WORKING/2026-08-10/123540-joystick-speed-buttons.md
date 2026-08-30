# joystick-speed-buttons

### 목표
- joystick의 -/+ 버튼을 속도 조정 기능으로 변경하는 방법을 확인한다.

### 지금
- 조사 완료. 변경 방안을 사용자에게 설명한다.

### 완료
- 작업 복구 로그 생성.
- `-/+`는 버튼 6/7로 설정만 존재하고 runtime에서는 사용하지 않음을 확인.
- Ultimate 2 주행 속도는 `manual_control.drive_trans`, `drive_rot`, `drive_speed`에서 산출됨을 확인.

### 다음
- 요청 시 speed 단계 설정, runtime 처리, 테스트와 문서를 구현한다.

### 검증
- 관련 설정·runtime·테스트 소스 정적 확인.
