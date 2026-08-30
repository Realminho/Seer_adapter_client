# simulator-robots-file

### 목표
- simulator 모드에서 별도의 robots.hcl 파일을 지정할 수 있게 한다.

### 지금
- 구현 및 검증 완료.

### 완료
- `run-adapter.sh --simulator --simulator-robots <path>` 옵션을 추가했다.
- 지정 파일이 단일/다중 dispatch와 실제 launcher의 `--robots`에 함께 반영된다.
- simulator 없이 옵션을 쓰거나 경로를 생략하면 종료 코드 2로 실패한다.
- 단위/셸 회귀 테스트와 simulator 가이드를 갱신했다.

### 다음
- 없음.

### 검증
- `.venv/bin/python -m unittest tests/test_adapter_dispatch.py` (14 tests OK)
- `bash scripts/test-run-adapter-dispatch.sh` (PASS)
- `bash -n adaptor/run-adapter.sh scripts/test-run-adapter-dispatch.sh` (PASS)
- `git diff --check` (PASS)
