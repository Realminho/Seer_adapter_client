# manual-move-during-order-hang

### 목표
- 192.168.101.62에서 order 중 매뉴얼(조이스틱) 주행 시 어댑터가 먹통이 되는 버그 수정

### 지금
- 구현 완료, 테스트 통과. 배포는 미실시(사용자가 로봇 현 상태 보존 선택)

### 완료
- 근본 원인 확정 (order 20260807-605-1-1, 22:10:08~)
  - 22:10:08 UmGoto p39 → mode=MRosGoto / CurTask.value={'cmd':'goto',...}
  - 22:11:10 mode=**ModeDrive**(수동) → CurTask.value={'cmd':'stop'} = JIBOT이 goto 태스크 폐기
  - 22:11:12 `[ORDER NODE UNREACHED]` 1회 로깅 후 무한 폴링 (재전송/실패처리/타임아웃 전무)
  - 코드: `adapter_jibot.py` `_wait_until_node_position_reached` — pose만 보고 대기, goto 태스크 소멸 미감지
- 수정 (TDD, RED 확인 후 구현)
  - `_is_jibot_manual_drive()` 신규: `jibot_status.manual_modes`(기본 `["ModeDrive"]`)와 `_vehicle._mode` 비교
  - `_derive_operating_mode`: 수동 주행 중 `OperatingMode.MANUAL` 보고
  - `_wait_until_node_position_reached`: 정지 지속 시간 기반 stall 감지 → `_resend_node_goto`로 goto 재전송
    (brake / 수동주행 / `_manual_control_active` / `_motion_paused` 중이면 stall 시계 정지)
    - `node_goto_retry_delay_sec = 3.0`, `node_goto_retry_limit = 3` (Settings 신규)
    - 재시도 소진 시 `JIBOT_NODE_UNREACHED`를 **FATAL** + `retries` 참조로 승격 → FMS 위임
    - 주행 재개 감지 시 재시도 예산 초기화
  - `_set_jibot_node_unreached_error`에 `error_level` / `retries` 파라미터 추가
  - `mode="move"` 세그먼트는 `_active_goto_node is node` 게이트로 영향 없음 (다른 세션의 move-segment 계획과 분리)
- 테스트 7종 신규 (`test_adapter_jibot_v3_order.py`), 전부 통과. 가드 테스트는 가드 제거 시 실패함을 확인
- 전체 스위트: 1820 passed / 8 failed — 8건은 HEAD 워크트리에서도 동일 실패(기존 이슈, pio·recipes·goto_nearest)
- 환경: `adaptor/.venv`에 requirements 누락분 설치(tree-sitter 등) 후 테스트 수집 가능

### 다음
- 사용자 판단: 62번 로봇 배포 여부 (배포 시 재현 상태 소멸)
- 주의: 다른 세션이 동시에 `adapter_jibot.py` / `config.py` / 같은 테스트 파일 수정 중 → 커밋 시 파일 범위 분리 필요
- 주의: move-segment 계획 문서는 "`_wait_until_node_position_reached` 무한 대기 유지" 전제 → 문서 갱신 검토

### 검증
- `scripts/run-tests.sh --python adaptor/.venv/bin/python` 전체 실행, 신규 실패 없음
