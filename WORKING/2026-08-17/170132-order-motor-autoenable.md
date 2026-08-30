# order-motor-autoenable

### 목표
- order 수신 후 주행 시작 전에 모터 상태를 확인하고, 꺼져 있으면 켜고 주행 시작

### 지금
- 완료. .61 재시작만 사용자 실행 대기

### 완료
- Phase 1 루트 원인 확정 (.61 journalctl 실측 증거)
  - 17:02:23 order 수신(p39 -> 1_01CH, dock-work) 시점 `motor=False`
  - 어댑터가 그대로 `UmDock` 송신 -> 로봇은 부저만 울리고 정지
  - 17:03:01 사용자가 수동으로 모터 ON (`motor=False` -> `True`)
  - 이후 17:05:33 취소까지 2.5분간 `mode=Stop status=Stopped`, 재송신 로그 0건
- 원인 A: 주행 명령 전 모터 전원 선행조건이 없음.
  `_send_node_motion`은 충전 해제(`_ensure_not_charging_before_order_motion`)만 확인하고
  모터는 보지 않은 채 UmDock/UmGoto를 보낸다. 펌웨어는 수락하지만 구동 불가.
- 원인 B: 모터 ON 엣지에 재송신이 없음.
  - dock 경로(`_wait_until_docking_complete`)는 "충전 시작"만 폴링, 재시도 자체가 없음
  - goto 경로(`_wait_until_node_position_reached`)는 재시도가 있으나 3회x3초 후 give_up,
    `retries_used` 리셋 조건이 `not _is_jibot_stopped()`라 정지 상태에서 모터를 켜도 리셋 안 됨
- 주행 명령 송신 지점 4곳 확인:
  `_send_node_motion` / `_resend_node_goto` / `_run_move_segment` / `_run_dock`
- `_motor_flag`는 UmGetMotorState 1초 폴링으로 갱신됨 (main.py:346) → 권위 있는 값
- `um_set_motor`는 fire-and-forget → 송신 후 `_motor_flag` 확인 대기 필요
- .61에는 /jrobot_status(ROS) 데이터가 없음 → `_derive_jibot_stop_reason()`는 None 반환

- Phase 4 구현 완료
  - `tests/test_order_motor_enable.py` 신규 11건 (먼저 7건 실패 확인 후 구현)
  - `_is_motor_power_off()`: `_motor_flag`가 명시적 off일 때만 True. None/""는 "모름"이라 건드리지 않음
  - `_ensure_motor_enabled_before_order_motion(node)`:
    충전 가드와 동일 계약(None이면 진행, 문자열이면 스텝 거부).
    EMERGENCY/PROTECTIVE_STOP/BUMPER/MOTOR_FAULT 래치는 강제 ON 금지 → 이유 반환.
    UmSetMotor는 응답이 없으므로 `_motor_flag` ON을 timeout까지 폴링 확인.
  - 4개 송신 지점 모두에 삽입:
    `_send_node_motion` / `_resend_node_goto` / `_run_move_segment` / `_run_dock`
  - 설정 2개 추가: `order_motor_auto_enable` (기본 true), `order_motor_enable_timeout_sec` (기본 5.0)
    → `adaptor/config/config.toml`과 저장소 루트 `config.toml` 양쪽에 기입
- 남은 갭 (이번 범위 밖, 보고만):
  dock-work 대기 `_wait_until_docking_complete`는 timeout/재시도가 아예 없어
  주행 중 모터가 꺼지면 여전히 무한 대기. goto/move 경로는 stall 재시도가 모터를 다시 켬.

### 다음
- 사용자가 .61 재시작 실행:
  `ssh -t ucore@192.168.101.61 'sudo systemctl restart amr-adaptor.service'`
- 재시작 후 모터 끈 채로 order 넣어 `[ORDER NODE MOTOR ENABLE]` -> `[ORDER NODE MOTOR ENABLED]` 확인

### 검증
- 신규 테스트 11건 통과, 전체 스위트 1997 passed / 1 failed
- .61 배포 완료 (백업 `*.bak-20260817-1740`):
  adapter_jibot.py / config/config.py md5 일치, config.toml에 키 2개 패치
  로봇에서 get_config() 통과, 송신 지점 4곳 모두 가드 존재 확인
- 주의: .61 adapter_jibot.py는 범퍼/stop-reason 5커밋만큼 뒤처져 있었고
  사용자 선택으로 전체 동기화함 -> 범퍼 접촉 FATAL(JIBOT_BUMPER)이 이번에 처음 활성화됨
- 기존 실패 기준선: `test_goto_nearest_node.py::test_timeout_stops_robot_and_fails` 1건 확정 실패
  + `pio_init`/`pio_ping` 6건은 부하 의존 flaky (단독 실행 통과)
