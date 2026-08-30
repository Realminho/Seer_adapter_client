# pio-station-channel-task1

### 목표
- Task 1: PIO station/channel ownership 리팩터 — AirShowerPioConfig/ElevatorPioConfig에 신규 필드 추가, 3곳 HCL에 키 추가. 아직 아무도 읽지 않음(그린 유지).

### 지금
- 완료. 커밋 1dc559a.

### 완료
- test_extensions_config.py에 facility_blocks 테스트 추가, RED 확인
- config.py: AirShowerPioConfig(pio_station_id, channel), ElevatorPioConfig(channel) 필드 추가
- config.py get_config(): ElevatorPioConfig 생성 호출에 channel=elevator_raw["channel"] 추가 (필수 필드라 생성자가 깨짐)
- 3벌 HCL(adaptor/config/extensions.hcl, .example, repo-root) 모두 동일 편집
- 타깃 테스트 94 passed, 전체 스위트 7 pre-existing failed / 1950 passed (신규 실패 없음)
- 커밋 1dc559a

### 다음
- (Task 1 완료, 대기 없음 — Task 2가 이어받음)

### 검증
- scripts/run-tests.sh --python .venv/bin/python tests/test_extensions_config.py -k facility_blocks -v → RED 확인 후 GREEN
- scripts/run-tests.sh --python .venv/bin/python tests/test_extensions_config.py tests/test_config.py tests/test_recipes_config.py tests/test_recipe_acceptance.py -q → 94 passed
- scripts/run-tests.sh --python .venv/bin/python -q (full) → 7 failed(기존), 1950 passed
