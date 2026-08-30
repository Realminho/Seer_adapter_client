# continuous-path-coalescing

### 목표
- Task 5(CP-1+CP-2): 연속 released 노드 구간을 합쳐 중간 노드에서 정지 대기를 건너뛴다.

### 지금
- 브리프 13스텝 전부 완료. 커밋 남음.

### 완료
- core/coalescing.py + tests/test_coalescing.py 신규 (drivable_run)
- adapter_jibot.py: _should_settle_at / _step_has_blocking_action / _is_coalescing_breaker /
  _is_run_end / _vehicle_xy / _wait_until_node_passed, _coalescing_run_step 배선
- tests/test_adapter_jibot_v3_order.py: ContinuousPathNodeStepTests 11개
- 브리프 이탈 1건: _is_run_end 가 큐에서 노드만 골라낸다(엣지 섞이면 영구 no-op)

### 다음
- 없음. 리뷰 대기.

### 검증
- test_coalescing.py 5 passed / order+coalescing 759 passed (243s), 통합 테스트 2개 포함

### 리뷰 후속 (fix)
- (1) _is_run_end 가 블로킹 액션 단 엣지를 남긴다 (spec §5.2 행 복구)
- (2) _wait_until_node_passed 에 스톨 복구 — _NodeMotionStallGuard 로 도착 대기와 공유
- (3) 앞날 보기가 구간의 실제 시작 노드를 dock/move 방향성 룰에 넘긴다
- (4) 공허하던 motion_seq 테스트를 프로덕션 경로로 재작성 + 선분 보간 테스트 추가
- 검증: coalescing 5 passed / order 761 passed (244s), 신규 5건 판별력 실증
