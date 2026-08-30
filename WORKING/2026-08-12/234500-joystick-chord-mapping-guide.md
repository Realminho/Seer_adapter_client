# joystick-chord-mapping-guide

### 목표
- D-pad + A/B/X/Y 조합키로 recipe/extension action 매핑
- −/+ 를 음량 대신 주행 속도 배율로 (완료)
- 엘리베이터 recipe 이름/타이밍 정리 + 슬롯 배치 + .61 반영

### 지금
- .61에 pio 설정·recipe 반영 완료(재시작 전). 미결 3건 답변 대기

### 완료
- 속도 스케일링 6커밋 (53f8a1c ae7f500 4b05bab 3804175 7ad3049 cb30f11)
- 층 호출 recipe 출발층-도착층 2x2 (Move1f-1f / 1f-2f / 2f-1f / 2f-2f)
  station = 출발층, out 번호 = 도착층. pioInit에 stationId 박음
- pio 엘리베이터 recipe 8개: pulse 0.5초 / off 뒤 대기 10초
  recipes.hcl TODO: 10초는 눈감은 대기, opened 센서 확인되면 ezioWaitIn으로 교체
- 슬롯 1~12 배치 (Left 행 13~16은 뺌). 전부 enabled=false
- output_pin_map 도입 (로컬 + .61). 위치 추론(output_pins) 대신 짝을 명시
- **.61 off-by-one 수정**: output_signals open=4/close=3 → 5/4.
  이전엔 elevatorOpen이 EZI 3(close_door_pin)을 치고 있었음. recipe 경로만
  틀려 있었고 실제 운행은 utils/elevator.py가 EZI 핀을 직접 쳐서 안 드러남
- .61 백업: config/{recipes,extensions}.hcl.bak-20260815

### 다음
- .61 어댑터 재시작해야 반영: ssh -t ucore@192.168.101.61 'sudo systemctl restart amr-adaptor.service'
- 미결 1: .61 cleanup "pioDisconnect"의 delay_sec=5 를 삭제했음. 현장 값이면 되살릴 것
- 미결 2: .61에 joystick 블록 없음 → 조이스틱 자체가 꺼져 있음. 블록 신설 + 켤 슬롯 결정 필요
- 미결 3: .example / 루트 extensions.hcl은 옛 슬롯 배치 + output_pin_map 없음. 동기화 여부
- 로컬 변경 미커밋 (recipes.hcl, extensions.hcl, 테스트 3종)
- 별건: clamp가 target=30000에서 "motion stopped before reaching the target" 반복 실패

### 검증
- 로컬 전체 1949 passed / 7 failed. output_pin_map 추가 전후 동일 → 회귀 없음
  남은 7건은 pio_init/pio_ping ×6 + goto timeout (착수 전부터 실패, 미커밋 WIP 소관)
- .61에서 get_config() 실행: elevatorOpen→PIO out5→EZI 4(=open_door_pin),
  elevatorClose→out4→EZI 3, 층 호출 out1→EZI 0 / out2→EZI 1(=floor_pin 0/1),
  recipe 8개 모두 로드됨
