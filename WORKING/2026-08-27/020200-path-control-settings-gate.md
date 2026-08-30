# path-control-settings-gate

### 목표
- task-3-brief.md 대로 `Settings.path_control` / `waypoint_pass_radius_mm` / `Adapter._continuous_path_enabled()` 추가 (기본값은 현행 동작과 동일해야 함)

### 지금
- 완료. task-3-report.md 작성 및 커밋까지 끝남

### 완료
- config.py Settings dataclass에 path_control/waypoint_pass_radius_mm 필드 추가 (switch_map_timeout_seconds 다음)
- config.toml [settings] 섹션에 동일 키 추가
- adapter_jibot.py에 _PATH_CONTROL_VALUES, _continuous_path_enabled(), __init__의 _warned_path_control_values 추가
- test_adapter_jibot_v3_order.py에 ContinuousPathSettingsTests 클래스 추가 (구현 전 AttributeError 실패 확인 후 구현 → 통과 확인)
- 커밋 884c78a

### 다음
- (task-4 등 후속 작업에서 이 게이트를 실제로 사용)

### 검증
- `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k ContinuousPathSettings -v` → 3 passed
- `scripts/run-tests.sh tests/test_configio.py` → 29 passed
- `scripts/run-tests.sh tests/test_unknown_config_key.py` → 4 failed(기준선과 동일, 내 변경과 무관한 pio/extensions.hcl 검증 실패)
