# simulator-startup-error

### 목표
- (완료) `./run-adapter.sh --simulator` 즉시 종료 문제 수정
- (완료) 엘리베이터 문 recipe를 `pioElevatorOpen`/`pioElevatorClose` + `pioWriteOut`으로 전환
- (완료) 실장비 보호: 내가 바꾼 robots.hcl broker 원복, sim 프로세스 확인
- (완료) (a) simulator용 가짜 EZI IO / PIO 보드 주입
- (완료) PIO out <-> EZI IO pin 1:1 맵 + 신호 이름 맵 추가

### 지금
- 전부 완료. 실장비 접속 중이라 simulator는 실행하지 않고 테스트로만 검증했다.

### 완료
- simulator 종료 버그 2건 (main.py `attach_ezi_clients`, `release_charge_hold`)
  + `tests/test_main_ezi_attach.py` 5케이스
- recipe rename + pioWriteOut 전환
- **robots.hcl mqtt_host 원복: 127.0.0.1 -> 192.168.2.61 (두 로봇)**
- sim 프로세스 없음 확인. 내가 돌린 sim은 전부 timeout 20~25초로 묶여 이미 종료됨
- (a) `utils/io_simulator.py`: SimulatedFacility / SimulatedEZIIO / SimulatedPIOMaster
  + `make_simulated_io(ezi_config)`. main.py가 `--simulator`이고 주소가 비었을 때만
  붙이고 PIO client factory까지 주입. 주소가 있으면 실장비가 이김.
  `tests/test_io_simulator.py` 14케이스
- 매핑 설정 (사용자가 "이름 + 1:1 맵 둘 다" 선택):
  - `PioConfig.output_pin_map` {out 번호: EZI IO 핀} — HCL 문자열 키를 int로 정규화,
    범위/1:1 중복 검증. `output_pins`(위치 추론)는 호환용으로 계속 받고 맵이 이김
  - `PioConfig.output_signals` {신호 이름: out 번호}
  - `extensions/pio`: `pio_signal_index`, `resolve_pio_output_index`(signal|index,
    둘 다 오면 거절), `known_output_signals`(WebUI choices), pioWriteOut 스펙에
    `signal` 칸 추가
  - `recipes.hcl`: `signal = "elevatorOpen"/"elevatorClose"` — 숫자 사라짐
  - `extensions.hcl`(+.example): 두 맵 선언
  - `tests/test_pio_output_mapping.py` 신규 16케이스,
    `test_recipe_acceptance.py`는 이름->out->핀 사슬 전체를 검증하도록 갱신
  - 기존 v3_order 테스트 4개는 output_pins 대신 output_pin_map을 세팅하도록 갱신
    (positional 폴백은 test_pio_output_mapping.py가 따로 지킴)
  - docs: extension-recipe-acceptance.md, pio-user-test-sequence.md

### 다음
- 미구현(의도적): 입력쪽 `input_pin_map`은 이번 범위 밖. 설비가 스스로 올리는
  신호(문 열림 확인/점유/airflow)와 EZI 모터(clamp)는 io_simulator가 흉내내지 않음
- p36 -> p2 order + pioElevatorOpen 게이트 통합 테스트는 아직 없음 (필요하면 작성)

### 검증
- `adaptor/` `pytest tests/` **1578 passed**
- `validate-extension-recipes.py --robot HN-SH6-TR-001` OK,
  enabled recipes에 pioElevatorOpen/pioElevatorClose
- 저장소 루트 `pytest tests/` 116 passed / **1 failed (내 변경과 무관)**:
  `test_run_adapter_dispatches_multi_robot_fleet_to_run_multi` —
  다른 세션이 작업 중인 `adaptor/run-adapter.sh`(63줄 추가)가 dispatch 줄에
  `"$SIMULATOR_ROBOTS"` 인자를 더해 테스트의 문자열 일치가 깨진 것. 나는 이 파일을
  건드리지 않았다.

### 주의 (내가 남긴 흔적)
- 로컬 docker broker(127.0.0.1:11883)에 내 sim이 retained 메시지를 덮어썼다:
  - `amr/v3/HN-SH6-TR-001/connection` -> OFFLINE (이전: l2m ONLINE 2026-07-29 12:50)
  - `amr/v3/HN-SH6-TR-001/factsheet` -> `"simulation": true`
  - HN-SH6-TR-002는 손대지 않음
  실제 AMR은 192.168.2.61을 쓰므로 영향 없어 보이나, 지울지는 확인 후에 한다.
