# cp4-edge-state-release-timing

### 목표
- CP-4: edgeStates를 엣지 진입이 아니라 다음 노드 통과 시점에 제거하도록 수정 (task-2-brief.md)

### 지금
- 완료. 커밋 d042a52, 리포트 작성 완료

### 완료
- EdgeStateReleaseTimingTests 작성, 임시 비활성화로 AttributeError 확인(FAIL 확인)
- _clear_edge_states_up_to 구현, _finalize_v3_node_step 에서 호출
- _clear_v3_order_step 에서 엣지 제거 분기 삭제(노드 분기 유지), edges= 로그 필드는 복원
- test_adapter_jibot_v3_order.py 단독 736개 전부 PASS
- 다른 edge_states 쓰기 지점(3929/3997-4003/4131-4134/8926) 검토 — 충돌 없음 확인
- adaptor/adapter_jibot.py, adaptor/tests/test_adapter_jibot_v3_order.py 커밋(d042a52)
- task-2-report.md 작성 완료

### 다음
- (없음, 이 세션 작업 종료)

### 검증
- scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k EdgeStateReleaseTiming -v → PASS
- scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py → 736 passed
- 전체 회귀는 코디네이터가 별도 실행(내가 우연히 겹쳐 돌린 결과: 17 failed/2271 passed, 실패는 전부 airshower/recipes/pio/config 쪽, 내 변경과 무관)

---
### Fix round 1 (리뷰 반영)
- 배선 고정 테스트 2건 추가(finalize 호출/else 분기 삭제 각각 임시로 깨서 FAIL 확인 후 원복)
- 로그 태그 [ORDER EDGE CLEAR] → [ORDER EDGE RELEASE] (_finalize_v3_node_step 쪽만)
- scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py → 738 passed
- 커밋 b7298c9, 리포트에 append 완료
