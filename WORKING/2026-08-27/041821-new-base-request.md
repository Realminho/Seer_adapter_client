# new-base-request

### 목표
- Task 6 (CP-3): base 가 짧아지면 `newBaseRequest` 를 발행해 decision point 급제동을 막는다.

### 지금
- 구현·테스트·회귀 확인·커밋까지 완료.

### 완료
- `_set_new_base_request_from_queue` 구현 + `_finalize_v3_node_step` 배선 완료.
- 실패 테스트 4개(브리프 3개 + 배선 검증 1개) 작성, 구현 되돌려 FAIL 확인 후 복원.
- `tests/test_adapter_jibot_v3_order.py` 765 passed (기존 실패 없음, 4분 5초).

### 다음
- (해당 세션 작업 종료) 실기 검증은 브리프의 별도 세션 항목.

### 검증
- `scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py` → 765 passed, 0 failed.
