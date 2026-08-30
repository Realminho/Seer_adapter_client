# remove-unavailable-joystick-buttons

### 목표
- Ultimate 2에서 실제 확인된 입력만 남기고 Bluetooth 8BitDo Micro 학습·설정을 추가한다.

### 지금
- Ultimate 2 정리와 Micro evdev 자동 탐지를 완료하고 최종 검증한다.

### 완료
- PL/PR/R4 설정을 제거하고 Micro `Pro Controller` event 자동 선택을 추가했다.

### 다음
- Micro의 실제 버튼 code를 측정해 16개 action evdev 매핑을 확정한다.

### 검증
- joystick/extensions 27 tests 및 2 subtests 통과, py_compile/diff-check 통과, Micro event18 자동 선택 확인
