# pio-init-station-id-number

### 목표
- pioInit의 stationId 파라미터를 select 대신 number input으로 변경

### 지금
- 구현 및 검증 완료

### 완료
- 작업 복구 로그 생성
- pioInit stationId를 choices 없는 number input으로 변경
- 관련 테스트를 변경된 동작에 맞게 갱신

### 다음
- 없음

### 검증
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 adaptor/.venv/bin/pytest -q adaptor/tests/test_action_modules.py` — 36 passed
- `git diff --check` — 통과
