# final-review-fixes

### 목표
- feat/move-segment-goto-fallback 최종 리뷰 지적 6건(F1,F2,F3,F5,F6,F7) 일괄 수정 후 커밋

### 지금
- 완료. 보고서 작성까지 끝남

### 완료
- F1: `_wait_until_move_settled` 프리즈 조건에 `_is_jibot_manual_drive()` / `_manual_control_active` 추가
- F2: 반환을 `(reached, travelled)` 튜플로 바꾸고, 이동량이 `move_started_min_travel_mm` 미만이면 goto 폴백 없이 스텝 실패
- F3: `_resend_node_goto`에 `_ensure_not_charging_before_order_motion` 선행 호출
- F5: 10초씩 걸리던 move-segment 테스트 2건을 실제 이동 픽스처 + 짧은 타임아웃으로 교체
- F6: `pytest-timeout` dev 의존성 + `timeout = 60` + `addopts = ["-p", "pytest_timeout"]`
- F7: plain-goto 일시정지 중 `JIBOT_NODE_UNREACHED` 미발행 테스트 추가
- 설계 문서(`docs/superpowers/specs/2026-08-07-...-design.md`) F1/F2 반영

### 다음
- 없음 (머지 대기)

### 검증
- `tests/test_adapter_jibot_v3_order.py`: 6 failed(기존 PIO), 640 passed — 기준선 632 대비 신규 8건만 증가
- 전체 스위트: 8 failed, 1878 passed. 추가 2건은 `ef27492` 워크트리에서도 동일 실패(기존 문제)
- F1/F2 비공허성 증명: 가드/분기 각각 파손 시 FAIL, 복구 시 PASS (보고서에 원문 수록)
- 커밋: `32b1080` (테스트), 프로덕션 변경은 다른 세션 commit-all에 `5797a56`으로 휩쓸려 들어감
