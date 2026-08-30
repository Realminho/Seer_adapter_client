# l2r2-analog-speed-feasibility

### 목표
- (A) L2/R2 아날로그 비율을 `UmDrive`의 `speed` 인자에도 반영한다. → 완료
- (B) `-`/`+` 버튼을 "주행 중 앞쪽 장애물 여유거리" 조절로 바꾼다. → 보류 (실차 점검 후 재개)

### 지금
- A 완료. B는 사용자 결정으로 **보류** — 실차 점검 일정 후 재개.

### 완료
- RED: `test_ultimate2_partial_trigger_scales_the_umdrive_speed`,
  `..._partial_steering_alone_scales_the_umdrive_speed`,
  `..._speed_follows_the_larger_of_trigger_and_steering` → speed=100 고정으로 실패 확인.
- GREEN: `Ultimate2JoystickService._apply`가 `demand = max(|throttle|, |steering|)`를
  `drive_speed * scale`에 곱한다 (joystick_runtime.py:368).
- `docs/manual/joystick-runtime-setup.md` 배율 설명 갱신.
- B 조사 결론: 조이스틱 조그는 `UmDrive(trans,rot,speed,lat)` 4개 인자뿐이라 여유거리
  파라미터 자체가 없다. 로봇 66개 명령에 clearance setter 없음
  (`UmGetPathPlanningClearances`는 getter이고 펌웨어가 `num = 0, cur none` 반환,
  `UmSetSafeDrive`는 bool on/off). 앞쪽 여유거리로 실제 존재하는 값은 `move` 라우트
  스텝의 `obs_avoid_dist`(mm, 기본 1000)뿐 — 거리이동/`mode="move"` 구간에만 적용.
- 실차 프로브(192.168.101.61, UmGetConfig 읽기 전용) 완료 — 49개 섹션 확인.
  덤프: WORKING/2026-08-18/umgetconfig-192.168.101.61.json (재프로브 안 하려고 보관)
  - `task-avoid`: clearance_front_min=200, front_max=200, back_min=200, back_max=300,
    side_min/max=100, clearance_slow_vel=100, clearance_fast_vel=700 (속도 연동 여유거리)
  - `task-drive`: clearance_front_min=400, drive_vel_acc=900, drive_angleVel_acc=100
  - `task-move`: clearance_front_min=300 / `task-goto`: clearance_front_init=100, auto_clearnce=true
- 즉 파라미터는 실재하고 `UmSetConfig(section, objs)`+`UmReloadConfig`로 쓸 수 있다.
  클라이언트에 `um_set_config()`(client.py:1009)도 이미 있다.

### 남은 위험/미확인
- 조그(`UmDrive`)를 실제로 지배하는 섹션이 `task-drive`(400)인지 `task-avoid`(200)인지 미확인.
- `objs` 페이로드 형태 미확인 — 로그에 UmSetConfig 실제 호출 기록이 없다.
  부분 dict가 섹션을 덮어쓰면 회피 설정이 날아갈 수 있으므로 read-merge-write 필요.
- `UmSetConfig`가 파일에 영구 저장되는지 미확인. 영구라면 "런타임 전용" 요구와 충돌하고,
  어댑터가 낮춘 여유거리 상태로 죽으면 로봇에 그대로 남는다 → 복구 로직 필수.

### 다음 (B 재개 시)
- 로봇에 UmSetConfig 쓰기 시험: task-avoid 원본 백업 → clearance_front_min만 write →
  UmGetConfig로 반영/섹션 보존 확인 → 즉시 원복. (주행 없이 값 왕복만)
- 그다음 조그가 task-avoid(200)인지 task-drive(400)인지 실주행으로 확정.
- 재프로브 방법: JIBOT(host, 7273) → connect_socket() → connect() →
  send_command_and_wait("UmGetConfig", gap=-1). gap=-1로 일회성 조회여야 구독이 안 남는다.
- `-`/`+`는 그때까지 기존 속도 배율 그대로 둔다.

### 검증
- 전체 스위트 `scripts/run-tests.sh -q`: 1 failed, 2009 passed (286s).
  유일한 실패 `tests/test_goto_nearest_node.py::test_timeout_stops_robot_and_fails`는
  HEAD(7e1cfde) worktree에서도 동일하게 실패 — 이번 변경과 무관한 기존 실패.
