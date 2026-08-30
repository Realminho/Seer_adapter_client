# active-action-sound

### 목표
- 기존 working state를 유지하면서 실행 중인 recipe/extension action을 표시하고 사운드 선택에 반영한다.

### 지금
- 구현과 관련 회귀 검증을 완료했다.

### 완료
- working state를 바꾸지 않고 active action/recipe step 정보를 AMR_STATE에 추가했다.
- action step/parent 파일 우선순위로 사운드를 선택하고 문서를 갱신했다.

### 다음
- 실제 actionType에 맞는 `action-<actionType>.mp3` 파일을 배치해 현장에서 확인한다.

### 검증
- 관련 60 tests passed, py_compile 및 git diff --check 통과.
- 확대 실행은 196 passed 후 기존 PIO pairing 테스트 1건 실패로 중단(이번 변경과 무관).
