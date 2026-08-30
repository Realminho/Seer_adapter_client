# extension-recipe-sound

### 목표
- extension 또는 recipe 실행 중에도 실행 상태에 맞춰 sound를 재생할 수 있는지 확인한다.

### 지금
- 현재 동작과 구현 가능한 연결 지점을 확인했다.

### 완료
- 사운드는 현재 workingState/detail만 보며, extension/recipe RUNNING 상태는 별도로 추적됨을 확인했다.

### 다음
- 필요 시 action type 기반 사운드 우선순위와 설정 문법을 정해 구현한다.

### 검증
- adapter_jibot.py의 sound resolver, action state 갱신, registry/recipe 실행 경로를 정적 확인했다.
