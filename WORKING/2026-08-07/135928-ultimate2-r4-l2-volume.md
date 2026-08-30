# ultimate2-r4-l2-volume

### 목표
- R4 모터 토글, L2 deadman 이동 조합, +/- 사운드 볼륨 매핑으로 변경한다.

### 지금
- 작업 완료.

### 완료
- R4 motor toggle, L2 80% deadman, +/- 5% volume, L4 dock modifier 정책과 X-input trigger 정규화를 추가.

### 다음
- controller가 `/dev/input/jsN`으로 연결되면 R4/L4 실제 번호를 측정해 null 값을 확정한다.

### 검증
- joystick 테스트 7개, JSON 구문 및 git diff --check 통과. Receiver 감지, controller는 아직 미연결.
