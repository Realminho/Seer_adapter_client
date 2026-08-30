# last-node-missing-battery-info

### 목표
- LAST_NODE_ID_MISSING 에러 발생 시 배터리 정보가 제대로 안 나오는 원인 규명
- 에러는 에러대로 보내면서 state 정보(배터리 등)는 항상 온전히 발행되게 하기

### 지금
- Phase 1 근본 원인 조사 (코드 경로 파악 완료, 관측 지점 확인 대기)

### 완료
- publish_state 루프(adaptor/adapter_jibot.py:655~800) 확인: 배터리/information/errors는 매 사이클 항상 함께 발행됨. lastNodeId 누락이 발행을 막는 게이트는 없음
- _refresh_last_node_id_errors (adapter_jibot.py:2695) : last_node_id 비어있으면 무조건 CRITICAL 에러 추가, description은 하드코딩("pose is not within any known node reach zone")
- _get_vehicle_battery_charge (adapter_jibot.py:1119) : _is_jibot_link_healthy() False면 -1.0 센티널 반환
- core/monitor.py:189 : SoC<0 → None → WebUI에서 "—%" 표시
- 유력 가설: JIBOT 링크 단절/무수신 → 배터리 -1(—%) + 포즈 미갱신 → 노드 매칭 실패 → LAST_NODE_ID_MISSING. 두 증상이 같은 원인

### 다음
- 사용자에게 관측 지점(WebUI/ACS) 및 동시 JIBOT_CONNECTION_LOST 유무 확인
- 확인 후 Phase 3 가설 검증 → 실패 테스트 작성 → 수정

### 검증
- (미실행)
