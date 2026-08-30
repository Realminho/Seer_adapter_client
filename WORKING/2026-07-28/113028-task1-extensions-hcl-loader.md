# task1-extensions-hcl-loader

### 목표
- extensions.hcl 로더(config/extensions.py) 구현: config.toml 섹션 구조와 동일한 dict 반환

### 지금
- 완료. 커밋 fe9d4ea

### 완료
- test_extensions_config.py 작성 → RED 확인 → extensions.py 구현 → GREEN 확인(10 passed, test_shipped_files_load만 예상대로 실패) → 커밋 → 리포트 작성

### 다음
- (Task 1 끝) Task 2에서 config/extensions.hcl, config/extensions.hcl.example 실제 파일 생성 시 test_shipped_files_load가 통과해야 함

### 검증
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_extensions_config.py -v → 2 failed(test_shipped_files_load subtests), 10 passed
