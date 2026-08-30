# nearest-node-current-location

### 목표
- 현재 모드에서 nearestNodeId와 현재 위치(lastNodeId) 관계 확인

### 지금
- 현재 proximity 모드의 거리 제한 동작 확인 완료

### 완료
- nearestNodeId는 무조건 계산되지만 lastNodeId는 gap 100 이하에서만 갱신됨을 확인

### 다음
- 요구 정책이 무조건 동기화라면 proximity 모드의 거리 gate 제거 또는 별도 모드 추가

### 검증
- adaptor/config/config.toml 및 adapter_jibot.py의 capture 분기 대조
