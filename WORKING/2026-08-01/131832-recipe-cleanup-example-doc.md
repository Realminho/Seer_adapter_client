# recipe-cleanup-example-doc

### 목표
- `recipes.hcl.example`에 `step`과 `cleanup`의 실행 의미를 설명한다.

### 지금
- cleanup 의미 설명 추가와 검증을 마쳤다.

### 완료
- `step`의 fail-fast와 `cleanup`의 항상 실행 차이를 예시 파일 머리말에 추가했다.
- cleanup의 용도, 별도 timeout 예산, cleanup 실패 시 최종 결과를 설명했다.

### 다음
- 없음.

### 검증
- shipped example 로드·reference 해결 테스트 1개 통과, `git diff --check` 통과.
