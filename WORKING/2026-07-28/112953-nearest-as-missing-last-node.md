# nearest-as-missing-last-node

### 목표
- lastNodeId가 없을 때 nearestNodeId로 초기화하는 옵션 추가 및 활성화

### 지금
- 구현 및 검증 완료

### 완료
- `use_nearest_node_as_last_node_when_missing` 설정 추가
- 배포 config.toml에서 옵션 활성화
- lastNodeId가 내부/state 모두 비어 있을 때만 nearestNodeId와 sequenceId로 초기화
- 기존 lastNodeId는 fallback이 덮어쓰지 않도록 처리
- PathPoint가 전혀 없는 구형 맵은 Goal/Dock 후보로 호환 fallback
- 옵션 및 기존 capture/goto 동작 회귀 테스트 보완

### 다음
-

### 검증
- 설정/gotoNearestNode/맵 테스트 57개 통과(샌드박스 소켓 테스트 1개 제외)
- nearest/idle capture/PathPoint 주문 선별 테스트 50개 통과
- git diff --check 및 py_compile 통과
