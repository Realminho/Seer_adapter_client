# check-extension-clamp

### 목표
- extension에 clamp/unclamp 기능이 있는지 확인

### 지금
- 확인 완료, 사용자에게 결과 전달 준비

### 완료
- extensions.clamp 기본 모듈 등록 및 clamp/unclamp 액션 구현 확인
- Web UI의 Clamp/Unclamp 버튼과 위치 설정 우선순위 확인

### 다음
- 없음

### 검증
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_action_modules.py::test_real_clamp_package_has_specs_and_panel -q` → 1 passed
