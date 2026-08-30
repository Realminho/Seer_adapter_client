# localization-recipe-design

### 목표
- localization1f/2f recipe(UmLocalize, pose 동일·각도 0/-90) 추가 설계.
- recipe step 정의 위치, localize 브리지 필요성, seer 이식성 조사 문서화.

### 지금
- 완료. localize 브리지 + 현재값 동적 재위치 핸들러 + localization1f/2f recipe 구현·검증.

### 완료
- step 등록 소스 3곳 확인(extensions/*/action_specs, core/action_bridge, config.actions).
- localize는 non-composable(instant switch adapter_jibot.py:5897)라 recipe step 불가 → 브리지 필요 확인.
- 공통 contract AmrClient.localize 확인, seer-client가 reloc(2002)로 구현(seer-client client.py:373) 확인.
- recipe는 robot별 경로 로드(resolve_robot_path) 확인.
- 설계 문서 작성: docs/superpowers/specs/2026-08-20-localization-recipe-and-vendor-portability-design.md.
- 브리지 구현(TDD RED→GREEN):
  - core/motion_primitives.py: run_localize 추가(_run_status_action로 instant 핸들러 재사용).
  - core/action_bridge.py: ActionSpec("localize", motion=True, cancel 없음) 등록.
  - tests/test_action_bridge.py: test_localize_is_composable_and_bridges_to_instant_handler(신규).
  - tests/test_action_modules.py: 기대 bridge 목록에 "localize" 추가.

### 다음
- (선택) 커밋. 사용자 요청 시 진행.

### 검증
- 결정: pose=실행시점 현재값 동적, theta 1f=0/2f=-90, recipe motion=false.
- 핸들러: target=pose에서 x/y 생략 시 _vehicle._x/_y 사용, theta 필수, 명시 x/y 회귀 보존.
- run_localize(_run_status_action로 instant 핸들러 재사용) + ActionSpec("localize") 등록.
- recipes.hcl: 기존 스텁(pioInit 복붙) → localize 기반 localization1f/2f로 교체.
- 부팅검증: get_config+first_party_action_specs 통과, steps=[('localize',{target:pose,theta:0/-90})].
- 테스트: test_localize_current_pose(3) + bridge/module/recipes/registry 등 91 passed,
  local_instant 71, web 271 passed.
- 무관 사전 실패: test_fleet_registry(/private/var), shipped-recipe 3개(사이트 config).
- 동시 세션 주의: recipes.hcl/adapter_jibot(pio mirror)/pio/render 등 타 세션 변경분 있음 — 영역 무겹침.
