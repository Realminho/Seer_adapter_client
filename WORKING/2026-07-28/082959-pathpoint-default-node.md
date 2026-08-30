# pathpoint-default-node

### 목표
- FMS 기본 주행 node를 PathPoint로 통일하고 좌표 fallback 및 회귀 테스트 보완

### 지금
- 구현 및 검증 완료

### 완료
- nearest node 코드 기본값과 알 수 없는 설정 fallback을 PathPoint로 통일
- `nodePosition` 없는 PathPoint 주문도 맵 pose로 도착 판정·주문 재개 가능하도록 좌표 fallback 보완
- Goal/Dock lastNodeId 거리 진단은 모드와 무관하게 유지
- 실기 맵 스냅샷에 PathPoint를 포함하고 시뮬레이터 flat node map을 PathPoint 후보로 사용
- 관련 회귀 테스트 추가

### 다음
-

### 검증
- 설정/최근접/맵 스냅샷 테스트 56개 통과(소켓 제한 테스트 1개 제외)
- 주문 PathPoint·bootstrap·lastNode 거리 선별 테스트 6개 통과
- git diff --check 및 py_compile 통과
