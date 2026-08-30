# navigate-p39-to-1-01ch

### 목표
- FMS 기본 주행 node를 PathPoint로 사용할 때 실제 수정이 필요한 코드 범위 확정

### 지금
- 점검 완료

### 완료
- PathPoint pose 주문 이동 및 PathPoint 기준 gotoNearestNode 구현 상태 확인
- `_node_xy()`의 PathPoint fallback 누락으로 도착 판정·주문 재개 정합이 불완전함을 확인
- config.toml은 pathPoint지만 Settings 기본값은 headingGoal인 불일치 확인
- Goal/Dock은 PathPoint vertex 그래프와 별도이므로 접근 PathPoint 매핑 계약이 필요함을 확인

### 다음
- 요청 시 `_node_xy` fallback, 기본값 통일 및 회귀 테스트 구현

### 검증
- 관련 좌표 조회 호출부, 주문 bootstrap/completion, 최근접 노드 및 공통 맵 변환 정적 점검
