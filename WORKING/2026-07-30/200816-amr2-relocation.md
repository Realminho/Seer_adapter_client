# amr2-relocation

### 목표
- AMR #2가 P36으로 표시되는 원인을 수정하고 relocation 함수를 추가한다.

### 지금
- localize 액션과 recipe 실행 상태 반영을 구현하고 검증했다.

### 완료
- JIBOT 공용 `localize()` 함수와 goal/pose/auto instant action을 추가했다.
- WebUI에 AMR별 Localize 입력/확인 UI를 추가하고 잘못된 lastNodeId를 재앵커하도록 했다.
- recipe 본문과 cleanup 실행 중 `workingStateDetail=EXECUTING_RECIPE`를 노출하도록 했다.

### 다음
- AMR #2에서 실제 올바른 노드를 선택해 Localize를 실행하고 state를 확인한다.

### 검증
- 관련 pytest 179개, 집중 unittest 3개, WebUI localize 소켓 테스트 1개 통과; py_compile 및 git diff --check 통과.
