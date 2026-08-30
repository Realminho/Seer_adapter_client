# proximity-always-nearest

### 목표
- proximity 모드에서 nearestNodeId를 거리 제한 없이 lastNodeId로 갱신

### 지금
- 구현 및 검증 완료

### 완료
- proximity의 거리/주행 상태 gate 제거, settled에 거리 gate 유지
- 관련 설정 설명·참조 문서·단위 테스트 갱신

### 다음
- 배포 시 192.168.101.62 어댑터 업데이트 및 재시작

### 검증
- nearest/last_node 관련 테스트 70개 통과, git diff --check 통과
