# error-on-unknown-action

### 목표
- 정의되지 않은 action 입력 시 오류가 발생하도록 수정한다.

### 지금
- 미등록 action 오류 처리와 회귀 검증을 완료했다.

### 완료
- 미등록 instant/order action을 `ACTION_NOT_FOUND` 경고로 state.errors에 기록한다.
- 미등록 instant action의 FAILED 결과에 action type을 포함한다.
- 임의의 `jibotUm*` action이 성공하던 와일드카드를 지원 목록으로 제한한다.

### 다음
- 없음.

### 검증
- 관련 pytest 6개 통과, py_compile 및 git diff --check 통과.
