# move-segment-goto-extract

### 목표
- Task 2 (goto-fallback plan): `_send_node_motion`에서 plain-goto 본문을 `_send_node_goto`로 추출 (behaviour-preserving refactor)

### 지금
- 완료. Task 2 종료.

### 완료
- 테스트 2개 추가, 실패 확인(AttributeError) 완료
- _send_node_motion 꼬리부를 _send_node_goto로 추출 완료 (adapter_jibot.py:4373-4429)
- 타겟 테스트 4개 통과, order 스위트 6 failed(기존 pio 실패만)/589 passed
- 전체 스위트 8 failed/1827 passed (실패 8개 중 6개는 pio, 2개는 무관한 기존 실패 — recipes_config, goto_nearest_node; 내가 건드린 파일 아님, 격리 재실행으로 결정적임 확인)
- 커밋 277aa9ceb9d6048c94343056a1d4a3092829f487

### 다음
- (없음, 작업 종료. Task 5가 _send_node_goto 소비 예정)

### 검증
- scripts/run-tests.sh --python .../adaptor/.venv/bin/python tests/test_adapter_jibot_v3_order.py -k "send_node_goto or still_docks" -v → 4 passed
- 전체 스위트 8 failed 유지 (baseline 대비 증가 없음), 리포트: .superpowers/sdd/2026-08-07-move-segment-goto-fallback/task-2-report.md
