# elevator-recipe-station-id

### 목표
- 엘리베이터 문 recipe를 층별 station/channel로 분리하고 이름을 1f/2f 규칙으로 통일, AMR 192.168.101.61 반영

### 지금
- 로컬 수정 + 로봇 반영 완료. 현장 문 동작 확인만 남음

### 완료
- recipes.hcl: 문 recipe 층별 분리 + pioInit에 stationId 000010/000020 + channel 250
- 네이밍 1f/2f 통일: pioElevatorOpen1f/Open2f/Close1f/Close2f, pioElevatorMove1f/Move2f
- extensions.hcl / .example joystick 슬롯 15·16 → pioElevatorOpen1f / pioElevatorClose1f
- 옛 이름을 참조하던 문서/테스트 정리: docs/guide/extension-recipe-acceptance.md,
  docs/manual/joystick-input-test.md, tests/test_io_simulator.py(이전 rename에서 누락되어 실패 중이었음),
  test_recipes_config, test_recipe_acceptance, test_parameter_validation, test_parameter_text_roundtrip,
  test_web_server, sounds/README.md
- 로봇 반영(2회): 백업 recipes.hcl.bak-20260813(원본), .bak-20260814-prerename(1f 이전)
  → 문 recipe 4개 1f/2f 이름 + station/channel, 로봇의 index 4/3·delay(2s, disconnect 5s)는 그대로
  → 11:01 amr-adaptor.service 재시작

### 다음
- 로봇에서 pioElevatorOpen1f/Close1f 실행해 pairing(BC 응답)과 문 동작 확인
- 로봇 output_signals(elevatorOpen=4, elevatorClose=3)는 out 번호로 풀면 EZI 3/2 → elevator open_door_pin=4와 불일치.
  지금은 index 방식이라 동작 영향 없지만 signal 방식으로 옮길 때 정리 필요
- 로봇 config/extensions.py는 구버전(joystick_action 미지원) — 조이스틱 슬롯 반영하려면 코드 배포 필요
- 층 호출(Move1f/2f)이 어느 station으로 나가는지 확인되면 pioInit에 stationId 고정

### 검증
- 로컬 전체 테스트: 1934 passed / 8 failed. 8건 모두 HEAD 클린 워크트리에서도 동일하게 실패(기존 문제)
  - test_adapter_jibot_v3_order pio_init/pio_ping GO 관련 6건, test_goto_nearest_node 1건,
    test_shipped_elevator_move_recipes_use_fixed_floor_outputs 1건(Move press delay 10 vs 기대 0.2)
- rename 영향 파일 8개 단독 실행: 220 passed / 위 Move 1건만 실패
- 로봇 validate-extension-recipes.py OK: enabled recipes=pioElevatorOpen1f, pioElevatorOpen2f,
  pioElevatorClose1f, pioElevatorClose2f, ... (41 actions)
- 로봇 재시작 정상: JIBOT TCP/API 연결, MQTT 연결, Ezi-IO/Ezi-SERVO Ready, factsheet 발행, is-active=active
