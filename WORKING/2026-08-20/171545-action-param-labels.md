# action-param-labels

### 목표
- WebUI `/actions` 의 extension 패널/recipe 카드에서 파라미터 칸이 무엇인지 보이게 한다
- 모든 액션 파라미터에 사람이 읽는 label 을 붙인다
- `stationId` 처럼 설정 목록이 힌트일 뿐인 select 는 직접 입력도 되게 한다

### 지금
- 구현/테스트 완료, 전체 스위트 확인 중

### 완료
- `ActionParameterSpec` 에 `label`/`editable` 추가 (core/action_registry.py)
- render: `_param_key` 이름표 + `_panel_field` 로 패널 칸에도 이름표, `editable` 은 datalist 입력
- pio/ezio/facility/clamp/localize 파라미터에 label 부여, pioPing·pioScenario stationId 는 editable
- `.ap-row`/`.cf-row` 를 세로 배치로 바꿔 이름표가 잘리지 않게 함
- recipes.hcl 붙여넣기 오타 수정: `signal = "elevator2f_2f" = 2` → `signal = "elevator2f_2f"` (2곳, 파일 전체가 파싱 실패 중이었음)

### 다음
- 없음. WebUI 프로세스를 재시작해야 화면에 반영됨

### 검증
- `scripts/run-tests.sh tests/test_action_module_panels_render.py tests/test_web_render.py tests/test_web_server.py tests/test_action_modules.py tests/test_registry.py tests/test_facility_extensions.py tests/test_recipes.py` → 362 passed
