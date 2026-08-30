# verify-nearest-gap-settings-61

### 목표
- nearestNodeGap이 임계치를 넘는데 lastNodeId가 잡히는 현상이 설정 오동작인지
  현재 옵션 조합의 정상 동작인지 판별

### 지금
- 소스 업데이트 후 재확인 완료. 결론 불변. 설정 변경 여부는 사용자 판단 대기

### 완료
- [1차, .61 실측] journal `[LAST NODE FALLBACK] nearestNodeId=p39 gap=449.5`,
  라이브 state lastNodeId=p39/gap 449.5, 맵 p39 d=449.5 차순위 p40 d=1399
- [2차, 소스 재확인] 36ccf28..7e1cfde + 미커밋 변경 전수 대조:
  lastNodeId 캡처/게이트 로직과 knob은 **변경 없음**
  - a33b2bd = order_motor_auto_enable / order_motor_enable_timeout_sec 추가(무관)
  - 미커밋 = _settle_goto_arrival(order 노드 goto 정착 대기, 무관)
- 현재 라인번호: _capture_idle_proximity:8188(무조건 set), _capture_idle_settled:8202,
  _idle_last_node_reach_xy:7894, _missing_last_node_reach_xy:7902(0=게이트없음),
  bootstrap early-return :3578/:3622, 캡처 호출 :8285
- 테스트로 로컬 재현: `scripts/run-tests.sh -q tests/test_goto_nearest_node.py`
  → 1 failed, 8 passed. test_timeout_stops_robot_and_fails 가
  `assert last_node == ""`에서 `'N3'` (거리 1000 ≫ idle_last_node_reach_xy=100).
  `[LAST NODE FALLBACK]` 로그 없음 → seed가 아니라 _capture_idle_proximity가 덮음
- 깨끗한 HEAD 워크트리에서도 동일 실패(1 failed, 8 passed) → 미커밋 변경 탓 아님

### 원인
- `last_node_capture_mode = "proximity"`가 거리/정지/order 게이트를 전부 우회
- `missing_last_node_reach_xy`가 루트 config.toml에 없음 → 기본 0.0 = 게이트 없음
- 결과적으로 `idle_last_node_reach_xy = 100.0`을 소비하는 경로가 없음

### 다음
- 경계를 실제로 걸려면: last_node_capture_mode="settled" +
  idle/missing reach 를 500~1300(권장 600). 그러면 위 실패 테스트도 통과.
- 100 유지 시 .61의 현재 pose(449.5)에서 LAST_NODE_ID_MISSING(CRITICAL) 발생 주의

### 검증
- 소스 diff 전수 대조 + 로컬 테스트 재현 + HEAD 워크트리 대조로 결론 확정
- 이번 회차에서 .61 원격 재확인은 수행하지 않음(SSH 미승인)
