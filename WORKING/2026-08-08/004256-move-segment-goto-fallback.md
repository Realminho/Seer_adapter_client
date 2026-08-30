# move-segment-goto-fallback

### 목표
- Task 5: `_run_move_segment`에 pose precondition 추가, `_active_move_segment` 발행, 짧게 멈춘 move는 goto로 fallback

### 지금
- Fix round 1 완료: 커밋 f1a76f5 (F1-F6 전부 해결), 리포트에 이어 붙임

### 완료
- Step 1-7 (Task 5 최초 구현): 커밋 77d38f6
- Fix round 1 F1: "unreach" 부분 문자열 → error_type == JIBOT_NODE_UNREACHED로 교체
- Fix round 1 F2: _active_goto_node fallback wait 중 설정 검증 테스트 추가 (non-vacuous 증명 완료)
- Fix round 1 F3: _active_move_segment 5개 속성 in-flight 캡처 테스트 추가 (non-vacuous 증명 완료)
- Fix round 1 F4: _resend_node_goto가 _send_node_motion 대신 _send_node_goto 호출하도록 수정 (dock 오발사 방지, non-vacuous 증명 완료 — 되돌리면 hang)
- Fix round 1 F5: test_move_segment_inside_zone_sends_no_goto를 실제 성공 경로(전체 주행 후 zone 안착)로 재작성, ~20초 단축
- Fix round 1 F6: fallback rejection 테스트에 _active_goto_node None, goto 실제 시도 검증 추가
- config.py mode="move" docstring 갱신
- 커밋 f1a76f5, task-5-report.md에 fix round 1 섹션 이어붙임

### 다음
- (없음 — Task 5 + fix round 1 완료. 이후 Task 6에서 _active_move_segment 소비 예정)

### 검증
- tests/test_adapter_jibot_v3_order.py -k move_segment: 22 passed (41.66s)
- tests/test_adapter_jibot_v3_order.py 전체: 6 failed(기존 결함, 불변), 618 passed (baseline 604 passed → 612 → 618, 회귀 없음)
