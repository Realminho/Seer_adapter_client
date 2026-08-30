# diagnose-auto-charging-start

### 목표
- 충전 노드 도착 후 자동 충전이 시작되지 않은 원인 진단

### 지금
- 2호기 실로그로 원인 확정

### 완료
- 자동 경로는 mode=dock 규칙 매칭 때만 UmDock, 수동 startCharging은 규칙과 무관하게 UmDock 실행함을 확인
- 현재 작업 설정의 자동 dock 대상은 1_01CH이며, 이전 2호기 사례는 UmDock 후 charging 미검출과 ModeCharge 잔류였음을 확인
- 자동 _run_dock은 _charging뿐 아니라 status=charging 및 charging#...도 충전 성공으로 인정하여 실제 전류 미인가 상태를 도착 완료로 오인할 수 있음을 확인
- 수동 버튼은 두 번째 UmDock 후 최대 60초 동안 같은 충전 판정을 다시 확인함
- 18:27경 첫 UmDock은 pos=(10727,-2729)에서 #brake로 멈추고 전류 -2.2A, 200초 후 JIBOT_DOCK_FAILED 발생
- 18:44 수동 조작 후 로봇이 Stop→전진 이탈→두 번째 UmDock으로 재접근했고 pos=(10757,-2699)에서 18:46:25 전류 +26.7A로 충전 시작
- 첫 시도는 어댑터가 도착 완료로 오인한 것이 아니라 실제로 충전 실패 처리했음을 확인
- 현재 원격 adapter는 PIO output_pin_map의 out 0 설정 오류로 반복 재시작 중임을 별도 확인

### 다음
- 첫 UmDock #brake 원인(장애물/도킹 파라미터/반사판 접근) 조정 및 현재 PIO 설정 오류 복구

### 검증
- 192.168.101.62 journal: 18:30:43 dock failed, 18:46:25 charge current +26.7A; 서비스 config fatal 반복 확인
