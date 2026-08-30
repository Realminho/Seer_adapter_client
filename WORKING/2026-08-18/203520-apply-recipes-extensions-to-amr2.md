# apply-recipes-extensions-to-amr2

### 목표
- pioElevatorMove1f-2f 등 recipes.hcl / extensions.hcl을 2호기(192.168.101.62)에 적용
- 기기별 고정값은 robots.hcl로 빼서 extensions.hcl을 덮어도 살아남게
- update 스크립트에 기기 세팅용 덮어쓰기 추가

### 지금
- .62 파일 적용 완료. **어댑터 재시작만 남음(ucore sudo 암호 필요 → 사용자 실행)**
- 저장소 기능 4건 구현·테스트 완료 (미커밋)

### 완료 — .62 적용
- 원인: .62 recipes.hcl이 구세대 이름(pioElevatorOpen/Close, Move1/2) → UNSUPPORTED
- 기준(.61)과 대조: 실제 호기 차이는 pio_serial_port(62 ttyUSB0 / 61 ttyUSB4), vehicle_num(AMR002 / AMR001) 뿐
- 새 .62 extensions.hcl = 저장소 시드 + AMR002 + clamp 30000/-20000 + joystick 12칸 enable
- pairing: pair_confirmation=bc_reply, select_off_timing=after_bc, select_off_delay_sec=0.5(사용자 확정)
- 백업: ~/adaptor/config/{extensions,recipes}.hcl.bak-20260818-2040
- 검증: 로봇 venv로 config 로드 OK, recipe 14개(pioElevatorMove1f-2f 포함), md5 일치

### 완료 — 저장소
- config/fleet.py: robot 블록 안 `extension "pio" { ... }`가 extensions.hcl 섹션을 덮는다
  (라벨→섹션 변환은 config/extensions.py extension_sections()로 일원화, advanced→pio_advanced 승격 포함)
- config/robots.hcl.example: extension 블록 설명 + 2호기 예시
- scripts/update-jibot-adapter-over-ssh.sh: --configure-device (extensions+recipes overwrite),
  overwrite 시 원격 원본을 config/*.bak-<날짜> 로 백업
- tests/test_update_jibot_adapter_over_ssh.py: 원격 설치 본문을 실제로 돌리는 케이스 6개 추가
  (keep/overwrite+백업/신규 로봇/--configure-device 전달/기본 keep/문서화)
- config/extensions.hcl(+.example): select_off_delay_sec 0.0 → 0.5
- robots.hcl `enabled` 지원: false면 load_fleet이 걸러내 fleet에서 빠진다
  (기동·목록·단일/다중 판정 전부). 전부 꺼져 있으면 FleetError로 기동 정지.
  find_robot은 "없다" 대신 "꺼져 있다"라고 말한다(main.py --robot, validate 스크립트가
  include_disabled=True로 읽는다). robots.hcl.example의 2호기 블록에 enabled = false 적용,
  config/adapter_dispatch.py 안내문과 그 가드 테스트도 함께 갱신

### 다음
1. `ssh -t ucore@192.168.101.62 'sudo systemctl restart amr-adaptor.service'` → pioInit/pioPing 실장비 확인
2. (코드 배포 후에만) .62 robots.hcl에 extension 블록으로 pio_serial_port/vehicle_num/clamp 이관
   — 지금 로봇의 fleet.py는 옛 버전이라 extension 블록을 unknown field로 거부한다
3. .61의 select_off_timing = after_go 유지 여부는 미결(0.5만 확정 받음)

### 검증
- `scripts/run-tests.sh` 전체: 2028 passed / 1 failed
  (test_goto_nearest_node::test_timeout_stops_robot_and_fails — 작업 트리의 미커밋
   _settle_goto_arrival 변경 건이며 이번 작업과 무관)
- 루트 `pytest tests`: 152 passed / 2 failed
  (test_run_adapter_dispatches_multi_robot_fleet_to_run_multi,
   test_update_script_restarts_after_upload_with_tty — 둘 다 HEAD 원본에서도 실패하는
   문자열 drift 가드로, 이번 작업과 무관)
- 실물 조합 스모크: 2블록(1호기 disabled) robots.hcl로 validate/dispatch/override 확인
