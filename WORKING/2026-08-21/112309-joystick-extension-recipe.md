# joystick-extension-recipe

### 목표
- 조이스틱 D-pad+ABXY로 extension/recipe action이 안 나가는 원인 파악 및 수정

### 지금
- 로컬/로봇 설정 수정 + 어댑터 재시작 완료. 조합 실제 눌러 확인만 남음.

### 완료
- 원인 확정: 코드 아님. 설정 2중 차단.
  - actions_enabled = false -> JoystickActionDispatcher.enabled False, fire_slot 즉시 return(로그 없음)
  - joystick_action slot 1~12 전부 enabled = false -> slots dict 비어 있음
  - joystick.enabled/ultimate2_enabled 는 true라 주행만 되던 상태
- 로컬 adaptor/config/extensions.hcl: actions_enabled=true, slot 1~12 enabled=true, 주석 갱신
- 로봇 ~/adaptor/config/extensions.hcl 동일 수정. 백업 extensions.hcl.bak-20260821-1203
- 로봇 하드웨어 경로 정상 확인: 2dc8:310b, amr-xboxdrv 실행,
  /dev/input/event6 = "Xbox Gamepad (userspace driver)". js 노드는 없음(evdev fallback 경로).
  (첫 확인 때 6013/노드 없음이었는데 컨트롤러가 잠들어 있던 상태였음)
- event6 읽기 권한 sudo 없이 확인됨
- sudo 암호 멈춤: 내가 `sudo -n systemctl start amr-adaptor` 로 유닛명에 `.service` 를 빼서 규칙과
  불일치한 것이 원인. `/etc/sudoers.d/adaptor-tui` 는 `/usr/bin/systemctl restart amr-adaptor.service`
  처럼 **정확한 문자열**만 NOPASSWD. 전체 경로+`.service` 로 재시도해 성공
- 어댑터 재시작 완료: `[JOYSTICK ULTIMATE2] connected via xboxdrv evdev: /dev/input/event6`
- 남은 갭 조사 완료: repo 쪽은 이미 정상. scripts/setup-adaptor-service.sh:509 에
  amr-xboxdrv.service 가 units 목록에 있음(d0577a5, 1912e4a). 고칠 코드 없음.
  로봇의 /etc/sudoers.d/adaptor-tui 가 2026-08-10 생성분이라 낡은 것뿐 —
  amr-adaptor/amr-webui/amr-camera 만 NOPASSWD.
  적용은 root 필요라 내가 못 함(sudo 암호). 원격 sudoers 파일 스테이징 시도는 정책상 차단됨.

### 다음
- 조합 눌러 `[JOYSTICK ACTION] slot N -> X` 로그 확인
- sudoers 갱신(사용자 실행, 암호 필요). 로봇 복귀 후:
  ssh -t ucore@10.8.8.8 'cd ~/adaptor && sudo scripts/setup-adaptor-service.sh --no-venv --no-start'
  사전 확인: grep -c amr-xboxdrv.service ~/adaptor/scripts/setup-adaptor-service.sh (0이면 먼저 배포)
  사후 확인: sudo -l | grep xboxdrv
- 2026-08-21 12:20경부터 10.8.8.8 ping 무응답. 복구 후 재개
- xboxdrv evdev A/B/X/Y 코드 실측 미기록. joydev 쪽은 a=1,b=0 으로 스왑 측정돼 있음.
  스왑이면 Up+A가 slot1(clampMin) 대신 slot2(clampMax)를 실행하므로 클램프 물린 상태에서 테스트 금지.
  실측: .venv/bin/python scripts/joystick_input_test.py --event-device /dev/input/event6

### 검증
- 로컬: get_config + build_registry + validate_joystick_actions 통과, slot 1~12 in_registry=True
- 로봇: 같은 검증을 로봇 .venv 로 실행해 통과, slot 1~12 in_registry=True
- tests: test_extensions_config/test_joystick/test_joystick_runtime 포함 85 passed.
  test_recipes_config 4 failed는 다른 세션의 recipes.hcl 작업분(airShower 이름) 때문. 내 변경과 무관.
