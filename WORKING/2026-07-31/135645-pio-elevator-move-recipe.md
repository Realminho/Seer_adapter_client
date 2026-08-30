# recipe-cleanup-guide-check

### 목표
- `step`과 `cleanup`의 실행 차이가 가이드에 문서화되어 있는지 확인한다.

### 지금
- 가이드와 설정 주석의 문서화 위치를 확인했다.

### 완료
- acceptance 가이드에 첫 실패 이후 step 중단과 실패·timeout·취소 후 cleanup 실행이 명시되어 있다.
- recipe 설정 머리말과 설계 문서에도 cleanup의 실행·결과 정책이 상세히 적혀 있다.

### 다음
- 없음.

### 검증
- `rg`로 guide/config/spec의 cleanup 설명과 줄 위치를 확인했다.
