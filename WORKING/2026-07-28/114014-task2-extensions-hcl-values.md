# task2-extensions-hcl-values

### 목표
- config.toml의 [ezi]/[pio]/[pio_advanced]/[air_shower_pio]/[elevator_pio] +
  주석 처리된 action_modules/actions 예시를 extensions.hcl(.example)로 이전

### 지금
- 완료. 커밋 2b0ce69

### 완료
- extensions.hcl / extensions.hcl.example 작성, config.toml 5개 섹션 제거 + 예시 블록 이전
- 값 보존 대조(before.toml vs load_extensions) 5개 섹션 전부 MATCH
- test_extensions_config.py 10 passed (test_shipped_files_load 포함)
- 커밋 2b0ce69 (3개 파일만 명시 스테이징), 리포트 task-2-report.md 작성

### 다음
- (이 세션 종료) Task 3: config.py get_config()가 extensions.hcl 병합하도록 배선
  — 지금 tests/test_config.py는 KeyError('ezi') 21건 (예상된 전이 상태)

### 검증
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_extensions_config.py -q -> 10 passed

### 검증
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_extensions_config.py -q -> 10 passed
