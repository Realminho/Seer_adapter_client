# pio-station-channel-task2

### 목표
- Task 2: 모든 consumer가 facility 소유 station_id/channel을 읽도록 전환

### 지금
- 완료. 커밋 8128a55.

### 완료
- Step1-2: 테스트 수정 및 강제 RED 확인 후 복원
- Step3: airshower.py station_id/channel -> air_shower_config
- Step4: elevator.py EVWorkflow에 channel kwarg-only 인자 추가
- Step5: facility/__init__.py _run_elevator에 channel 전달
- Step6: pio/__init__.py pio_link_params 폴백 제거 + known_station_ids 교체
- 전체 스위트 첫 실행에서 test_adapter_jibot_v3_order.py의 pioInit/pioPing/pioScenario
  테스트 24개가 새로 깨진 것 발견 (station_id/channel 폴백에 의존하던 테스트들).
  각 호출부에 stationId/channel을 명시로 채워 넣어 수정. 폴백 자체를 검증하던
  테스트 1개는 "폴백이 사라지고 필수값이 됐다"를 검증하도록 재작성.
- 커밋 완료: 8128a55 "refactor(pio): read station and channel from the facility that owns them"

### 다음
- 없음 (Task 2 완료, Task 3로 이관)

### 검증
- tests/test_recipes_config.py tests/test_recipe_acceptance.py tests/test_facility_extensions.py tests/test_elevator_handler_returns.py -q: 39 passed
- tests/test_action_modules.py tests/test_action_module_panels_render.py -q: 49 passed (unrelated 기존 diff 영향 없음)
- 전체 스위트: 7 failed (모두 기존 사전 실패), 1950 passed — 새 실패 없음
