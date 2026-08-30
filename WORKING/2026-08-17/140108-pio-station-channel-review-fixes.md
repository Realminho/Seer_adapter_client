# pio-station-channel-review-fixes

### 목표
- 2026-08-15 pio station/channel ownership 브랜치의 코드리뷰 지적사항(Critical 1, Important 2-5, Minor 6-9,12) 일괄 수정

### 지금
- 전체 테스트 스위트 백그라운드 실행 결과 대기 중

### 완료
- Critical 1: config.py `_MOVED_PIO_KEYS["channel"]` owner 문구 수정 + panel.html/spec 동일 수정
- Important 2: config.py에 반쪽 마이그레이션(새 키 누락) guard 추가, ExtensionsError로 라인 안내
- Important 3: recipes.hcl 주석 3곳(폴백/required/channel owner) 수정
- Important 4: pio-user-test-sequence.md L20 + §6/§7에 stationId/channel 입력 단계 추가
- Important 5: extensions/pio/__init__.py pio_link_params에 station/channel 쌍 검증 추가 + known_station_channel_pairs 헬퍼 + 테스트 추가
- Minor 6: config.py 옛 키 두 개 동시 존재 시 한 번에 raise
- Minor 7: test_config.py TestMovedPioStationAndChannelKeys를 ExtensionsError로, elevator owner 텍스트 검증 추가
- Minor 8: utils/elevator.py 사용 예시에 channel 인자 추가
- Minor 9: pio_link_params에서 station_id/channel strip한 값을 실제로 사용하도록 수정
- Minor 12: web/render.py docstring + docs/guide/web-ui.md에 airshower 언급 추가
- 관련 단위 테스트(test_config.py, test_pio_output_mapping.py, test_pio_select_timing.py, test_action_modules.py, test_adapter_jibot_v3_order.py -k pio, test_recipes_config.py, test_recipe_acceptance.py, test_extensions_config.py, test_action_module_panels_render.py) 전부 통과 확인 (기존 6개 pio_init/pio_ping GO-retry 실패는 무관 사전 실패로 그대로)

### 다음
- 전체 스위트(scripts/run-tests.sh, 인자 없음) 백그라운드 실행 결과 확인
- 결과 확인 후 커밋, 최종 리포트 작성

### 검증
- 부분 스위트 전부 통과 (신규 실패 없음, 기존 6개 pio GO-retry 실패만 잔존)
