# amr-speed-control-check

### 목표
- AMR 속도 조절 경로 파악 (자율주행/수동/조이스틱/레시피), WebUI 조절 가능성 확인만

### 지금
- 종료. C안 설계 확정 → GitHub issue #4 로만 남김 (spec 파일/커밋 없음, 구현 안 함).
  https://github.com/lab2m/unified-amr-adaptor/issues/4

### C안 요구사항 (사용자 확답)
- 사용 시나리오: **운영 중 수시 조절** (현장 1회 튜닝 아님) → WebUI 전용 패널 + 현재값 표시 필요
- 복원 정책: **어댑터가 소유** — 기준값을 어댑터 설정에 두고 기동 시 로봇에 재적용,
  운영 중 변경은 기준값 대비 %. 어댑터가 죽어도 다음 기동에 복원
- 조절 대상: **직진 + 회전 + 가감속**

### 발견한 결정적 선례 (설계 근거)
- sound_settings.startup_volume 이 정확히 같은 구조로 이미 돌아감:
  config 값 → 기동 시 적용(adapter_jibot.py:1796) → setSoundVolume instant action 으로
  런타임 변경 → 성공 시 config 에 persist(_persist_sound_startup_volume:7854) →
  WebUI Sound 패널이 마지막 커맨드 기억해 표시(server.py:222, _track_sound_action).
  C안 = 이 패턴의 sink 를 pactl 에서 UmSetConfig 로 바꾼 것.
- getParameters/setParameters(EPR) 도 이미 있음 — WCS 경로는 별도로 붙일 수 있음.

### 완료
- 자율주행 속도: 로봇 펌웨어 설정 전용. UmGoto("target")에 speed 인자 없음
  (client.py:918, adapter_jibot.py:4919 _send_node_goto).
  실차 덤프(WORKING/2026-08-18/umgetconfig-192.168.101.61.json):
  task-goto/max_vel=350, robot/speed_trans_max=500, robot/speed_rotate_max=90,
  nav/limit_run_linear_max_vel=1000, task-go_tag/speed=450, task-dock/speed=100.
- VDA5050 edge.maximumSpeed: 파싱(messages.py:227)·로그(adapter_jibot.py:3601)만 하고
  주행에 미적용. 사실상 무시됨.
- 수동 조그/거리이동: manual_control (adaptor/config/jibot-config.toml:18)
  drive_trans=200 / drive_rot=30 / drive_speed=200 / default_move_speed=100.
  WebUI 수동 컨트롤 화면에 speed 입력칸 있음(render.py:1871, Move distance speed)
  — 그 자리 값은 일회성이고 파일에 저장 안 됨.
- 조이스틱: extensions.hcl joystick 블록 speed_step/min/max/start_percent (20~100%),
  -/+ 버튼 런타임 조절 + L2/R2 아날로그 비율 곱(joystick_runtime.py:377).
- 레시피 move: recipes.hcl var.moveSpeed → manualMove speed.
- WebUI /config 편집 범위: config.toml + extensions.hcl + recipes.hcl + robots.hcl
  (server.py:1206 _hcl_paths). jibot-config.toml은 대상 아님 → manual_control 편집 불가.
- 펌웨어 쓰기 경로: client um_set_config(section, objs) (client.py:1009) + UmReloadConfig 존재.
  adaptor 코드에서 호출하는 곳 없음(grep 0건). instantAction jibotUmSetConfig 로는 가능하나
  _is_motion_instant_action(adapter_jibot.py:6577)이 raw JIBOT 명령을 전부 motion 취급 →
  BUSY 중 차단. amr_parameter_publish.py:19-23 이 같은 함정을 이미 기록(평범한 이름 필요).
- 미확인: objs 페이로드 형태, 영구 저장 여부 (WORKING/2026-08-18/210501-... "남은 위험" 동일).

### 다음 (권고안)
- A. 지금 바로 가능(실차 불필요): WebUI /config 편집 대상에 jibot-config.toml 추가 →
  manual_control.drive_speed/trans/rot/default_move_speed 를 화면에서 저장.
  손댈 곳: paramstore.SOURCES+_EDITORS(tomledit), server._hcl_paths(),
  _save_hcl_field 의 validate 를 확장자별 분기 — toml 이면 _validate_hcl_file 대신
  _post_config 가 이미 쓰는 self._validate_config() 재사용(로더 검증이라 overlay 포함).
  부작용: EPR 스냅샷(amr_parameter_publish) 대상도 늘어남 → 나가는 항목 확인 필요.
- B. 자율주행 속도의 게이트(실차 필요, 선행 필수): UmSetConfig 왕복 시험 스크립트.
  UmGetConfig ret 이 `data:{section:{param:{data}}}` 이므로 objs 는
  section="task-goto", objs={"max_vel": N} 형태로 **추정** → 실물로 확정해야 함.
  백업 → write → UmGetConfig 로 섹션 보존 확인 → 원복 → urobot 재시작 후 재확인(영구성).
  주행 없이 값 왕복만. 이게 안 풀리면 C 착수 불가.
- C. B 통과 후: setDriveSpeed instant action + WebUI 패널.
  - 이름에 jibot/COMMAND_SPECS 접두어 금지(_is_motion_instant_action → BUSY 차단).
  - **쓰기 대상은 섹션/필드 화이트리스트로 고정**(task-goto/max_vel 등). 범용 section+objs
    폼은 만들지 말 것 — robot/speed_trans_max, nav/limit_run_* 는 튜닝값이 아니라
    안전 한계값이라 브라우저에서 로봇 속도 상한을 올릴 수 있게 된다.
  - 상한 클램프는 config 에 박은 값 기준(로봇이 보고한 현재값 기준 금지).
  - read-merge-write + 어댑터 기동 시 기준값 복원.
- edge.maximumSpeed 적용도 C 위에 얹는 것이라 순서 동일(UmGoto 에 speed 인자가 없어서).

### 검증
- 코드/설정 grep 및 실차 UmGetConfig 덤프 대조. 테스트 실행 없음(설계까지만).
- issue 본문의 line number 는 85f549d 기준으로 재확인함(다른 세션 커밋으로 한 번 밀렸었음).
- paramstore.scan 실행 검증은 로컬 tree_sitter 미설치로 못 함 → 코드 판독 기준.
