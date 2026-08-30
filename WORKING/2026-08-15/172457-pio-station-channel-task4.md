# pio-station-channel-task4

### 목표
- Task 4: pioInit/pioPing/pioScenario 패널에 stationId/channel 입력을 실제로 붙인다 (task-4-brief.md 그대로).

### 지금
- 브리핑/기존 파일 상태 조사 완료. panel.html·test_action_modules.py·test_action_module_panels_render.py 는 다른 작업(webui 리팩터)의 미커밋 변경을 이미 갖고 있음 — pio 관련 부분만 손댄다.

### 완료
- action_specs: pioPing stationId/channel required=True, 빈 선택지 제거, pioScenario에 stationId/channel 추가
- panel.html: pioPing 설명 문구 교체, pioScenario 카드에 $field_pioScenario_stationId/_channel 추가
- test_action_modules.py: pioPing required/choices 검증, stations[0]==pio_config 단언 제거
- 커밋 3abb13c (3개 파일만 스테이지 확인)

### 다음
- (완료 — 재개 불필요)

### 검증
- tests/test_action_modules.py -k pio: RED→GREEN 확인
- tests/test_action_modules.py + test_action_module_panels_render.py: 49 passed
- 전체 스위트: 7 failed(기존 known failures와 동일), 1951 passed
