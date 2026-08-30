# recipe-delay-and-ezio-wait-in

### 목표
- recipe step `delay_sec` + `ezioWaitIn` (완료)
- pioElevatorOpen이 정말 "열림" 핀을 치는지 검증하고, 새 recipe/신호가 로봇까지
  올라가게 배포 경로 확보

### 지금
- 전체 adaptor 테스트 재확인 중 (berebceyz)

### 완료
- delay_sec: RecipeStep + recipes.py 파싱 + _hold()로 예산 clamp, 문 recipe 0.2s
- ezioWaitIn: 조건 대off기 액션 + EziConfig knob + extensions.hcl(.example)
- signal 사슬 검증: test_recipe_acceptance가 production resolver로
  이름 -> out -> EZI IO 핀을 풀고 extension "elevator"의 door pin과 비교
- 버그 수정: output_signals가 elevatorOpen=4(→EZI IO out3=close_door_pin)였다.
  docs/guide/extension-recipe-acceptance.md:36과 현장 노트 기준 5/4로 복구
- 배포: EXTENSIONS_HCL_MODE / RECIPES_HCL_MODE (keep|overwrite|ask) 추가,
  기본값은 keep 유지. 원격 case 분기 + 모드 전달 + 매뉴얼 갱신

### 다음
- 다른 세션 작업으로 tests/test_adaptor_service_scripts.py의
  test_run_adapter_dispatches_multi_robot_fleet_to_run_multi가 실패 중
  (run-adapter.sh:98이 SIMULATOR_ROBOTS 인자를 추가했는데 테스트는 옛 형태를 단정).
  내 변경과 무관 — 그 세션이 정리해야 함
- 문 열림 확인 입력 핀 번호를 현장에서 확인하면 recipes.hcl에 ezioWaitIn step 추가

### 검증
- tests/test_update_jibot_adapter_over_ssh.py 54 passed
- adaptor: test_recipe_acceptance/test_recipes_config/test_extensions_config 35 passed
