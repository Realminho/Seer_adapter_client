# task-3-runtime-speed-buttons

### 목표
- joystick_runtime.py: -/+ 버튼이 volume 대신 drive speed를 스케일하도록 수정 (Task 3)

### 지금
- 완료됨

### 완료
- 테스트 픽스처/신규 테스트 4개 작성 -> RED 확인 -> joystick_runtime.py 구현 (step_speed, _apply 스케일링, handle_event/evdev 배선) -> GREEN 확인 -> 전체 스위트 확인 (9 pre-existing 실패만, 신규 실패 없음) -> 커밋 3804175

### 다음
- (완료, 후속 작업 없음. Task 4가 .hcl/docs 담당)

### 검증
- tests/test_joystick_runtime.py 19 passed
- 전체 스위트: 9 failed(기존), 1921 passed — joystick 관련 신규 실패 없음
