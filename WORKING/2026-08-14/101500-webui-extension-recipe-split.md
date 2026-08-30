# webui-extension-recipe-split

### 목표
- 어댑터 페이지(`/adapter/<key>`)·대시보드(`/`)에 하드코딩된 clamp·pio·ezio 전용 UI 제거
- 그 기능들을 extension/recipe 화면(`/actions`)이 extension 설정을 따라 제공
- 새 extension 이 붙어도 어댑터 페이지를 안 고치는 상태(보편성)

### 지금
- 구현·검증 완료. panel.html 은 유지하고 /actions 가 사용하도록 결정됨

### 완료
- `web/render.py`
  - 대시보드/어댑터 페이지에서 `_io_panel` 제거, `io_by_key`/`io` 파라미터 삭제
  - `_control_forms` 의 `_action_module_panel` 그룹 제거
  - 제외 집합에 recipe action_type 추가(`extension_action_types`) — recipe 가
    파라미터 칸 없는 맨 버튼으로 새던 결함 수정
  - `_ACTION_META` 의 clamp 8종 삭제, `_action_module_panel`/`_module_fallback_cards`
    삭제, 미사용 `import string` 정리
  - `CONFIRM_REQUIRED_ACTIONS` 신설(gotoNearestNode, localize, pioPing,
    pioWriteOut, clampMoveTo) — `_run_form` 이 체크박스를 그린다
  - `_module_status_panel` + `_IO_STATUS_SECTIONS` 신설: 모듈명 끝조각 → io 속성
    규약. 표시 여부 = discover 된 모듈, out 토글 = 그 모듈이 쓰기 액션을 노출할 때만
  - `actions_page(..., io=None)` 가 모듈 그룹 위에 상태 패널을 붙인다
- `web/server.py`
  - `/adapter/<key>/actions` 가 `io=self._monitor_io(...)` 전달
  - `_CONFIRM_REQUIRED_ACTIONS = render.CONFIRM_REQUIRED_ACTIONS` (한 벌로 통일)
  - `_post_io_out` → `/actions` 로 리다이렉트, 쓰기 액션이 spec 에 없으면 거절
- 문서: `docs/guide/web-ui.md` 에 Actions 화면 절 추가,
  `docs/guide/extension-recipe-acceptance.md` 에 실행 창구 명시

- `panel.html` 을 /actions 에서 사용: `_module_panel()` 로 부활시키고,
  `_PANEL_ACTION_RE` 로 패널이 덮는 액션을 알아내 나머지만 생성 폼(`— 그 외` 그룹)
  으로 보완. panel.html 이 없는 모듈은 전부 생성 폼

- panel 계약 확장: `$field_<actionType>_<param>` 치환자 추가(`_param_field()` 를
  `_param_rows` 에서 분리해 재사용). signal/stationId 선택지가 extensions.hcl 에서
  오므로 패널에 박으면 설정을 고쳐도 안 따라오는 문제를 없앤다
- pio/panel.html 을 모듈과 동기화: 6개 액션 전부 + 파라미터 칸 12개
  (pioInit/pioPing 의 media·stationId·channel·port·timeoutSec,
   pioWriteOut 의 signal·index·state, pioDisconnect 의 clearOutputs·timeoutSec,
   pioScenario 의 scenario). 기존 pioInit 은 필수 파라미터 4개가 칸이 없어
  누르면 실패하던 상태였음
- clamp/panel.html: clampTeach 카드 추가, clampMoveTo 를 스키마 필드로 전환,
  confirm 에 required 추가
- 회귀 테스트 추가: 실제 panel.html 이 렌더되는지 + 모듈 액션/파라미터를 다 덮는지
  (패널이 깨지면 조용히 생성 폼으로 폴백해 눈으로는 못 잡음 — 실제로 이 테스트가
   주석의 `$field_` 가 치환자로 파싱되는 버그를 잡았다)

### 다음
- 없음

### 검증 (panel.html 동기화 후)
- 액션 38개(26 + recipe 12) 전부 렌더, **파라미터/recipe 변수 누락 0**
- 그룹: Clamp / PIO 상태 / PIO / EZIO 상태 / EZIO / Facility workflows / Recipes
  ("— 그 외" 그룹 없음 = 패널이 모듈을 다 덮는다)
- pioWriteOut 칸: signal(select) index state confirm.
  signal 선택지 = extensions.hcl:33-36 의 output_signals 와 일치
- 실서버 HTTP: 6개 화면 전부 200

### 검증
- webui 관련 346 passed (test_web_render / test_web_server /
  test_action_module_panels_render / test_web_main / test_action_modules /
  test_fleet_registry)
- 실제 config 로 렌더 확인: 모듈 4개(clamp/pio/ezio/facility) 26개 액션 + recipe 12개
  전부 /actions 에 렌더, 파라미터·recipe 변수 칸 누락 0, confirm 3개 정상
- 상태 패널: pio/ezio 만 나옴(clamp/facility 없음) — 설정을 따름 확인
- 어댑터 상세에 extension/recipe form 누출 0, `/io/out` 흔적 없음
- 실서버 기동 HTTP 확인: /actions·/source/extensions.hcl·/source/recipes.hcl·
  /config·/·/adapter/<key> 전부 200, 실행 버튼 38개(26+12), 원문 편집기 정상

### 참고 (내 작업 아님)
- 같은 체크아웃에서 다른 세션이 elevator recipe 이름을 `1`/`2` → `1f`/`2f` 로
  바꾸는 중. 전체 스위트의 실패 8건은 전부 그쪽 것이다
  (test_adapter_jibot_v3_order 6, test_goto_nearest_node 1, test_recipes_config 1).
  web 을 import 하지 않거나(`goto_nearest_node`), `web.senders` 만 쓴다.
