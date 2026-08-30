# hybrid-last-node-capture

### 목표
- `last_node_capture_mode`에 `hybrid` 모드 추가: order 진행 중일 때 현재 위치 보정을 더 엄격/정확하게

### 지금
- brainstorming 스킬로 설계 진행 중 (기존 proximity/settled/disabled 코드 파악 완료)

### 완료
- 기존 구조 파악: `_capture_idle_last_node` 분기, `_find_nearest_node`, `_bootstrap_v3_order_*` 게이트

### 다음
- 사용자에게 hybrid의 "더 가깝게"의 의미 확인 → 설계안 제시 → spec 작성

### 검증
- 아직 없음
