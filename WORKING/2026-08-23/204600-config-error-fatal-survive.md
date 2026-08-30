# config-error-fatal-survive

### 목표
- motion_rules 같은 라벨 없는 TypeError로 부팅이 죽지 않고 config-error 모드(MQTT FATAL 보고)로 살아남게 함

### 지금
- 코드/테스트 완료. 커밋 대기(다른 세션이 monitor.py/web/*를 동시에 고치는 중이라 파일 지정해서 add).

### 완료
- config/config.py: `_motion_rule_from_dict` 검증 추가 — 모르는 키/빠진 to를 "몇 번째 항목 + 그 줄 내용 + 쓸 수 있는 키"가 실린 ConfigError로 올림. `_motion_rules_from_config` 신설, `_lenient`(보고 전용)면 못 만드는 룰은 건너뜀
- config/config.py: `get_config_with_fallback`이 ConfigError뿐 아니라 **모든 예외**에 대해 보고용 config 사다리를 탐(예전에는 비-ConfigError면 config.toml만 base로 한 번 재시도 → extensions/recipes/base가 깨졌으면 그대로 죽음). `DEFAULT_CONFIG_PATH` 공개
- main.py: `config_error_path`가 config_path=None + 라벨 없는 예외일 때 기본 config.toml 이름을 싣게 함(안 그러면 configPath 참조가 통째로 빠짐). 마지막 Fatal 문구도 "보고용 config도 못 만들었다"로 정정
- tests: test_config_fallback.py에 TestBrokenMotionRuleStillReports 4개, test_config_error_path.py에 bare-error 1개 추가

### 다음
- 커밋 후 배포. 현장(ubuntu 20:44 로그) config.toml의 motion_rules에서 to 없는 줄을 찾아 수정해야 로봇이 실제로 뜬다

### 검증
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_config_error_path.py tests/test_config_fallback.py tests/test_config.py tests/test_config_error_reporter.py tests/test_dock_approach*.py tests/test_config_adapter.py -q` → 95 passed
- 전체 스위트 2235 passed / 15 failed — 15개는 HEAD worktree(+현재 recipes.hcl)에서도 동일하게 실패, 이번 변경과 무관
- 실제 payload 확인: FATAL CONFIG_LOAD_FAILED의 configPath + reason에 `motion_rules 1번째 항목에 반드시 있어야 할 키가 없습니다(to): { from = "F1_60", mode = "move", distance = -1200 }`가 실림
