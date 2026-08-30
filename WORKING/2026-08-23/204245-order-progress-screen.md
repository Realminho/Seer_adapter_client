# order-progress-screen

### 목표
- AMR 현재 위치 / 단계 / 액션을 리스트로 두고 하나씩 클리어되는 WebUI 화면 추가

### 지금
- 구현·테스트 완료. 다른 세션이 37e2327 로 함께 커밋함. 이슈 등록하고 세션 종료

### 완료
- 판정: 어댑터(adapter_jibot.py) 수정 불필요. actionStates 가 오더 전 액션을 담고 끝나도 유지됨
- core/monitor.py: workingStateDetail / activeActionType / activeStepActionType 추출 추가
- web/render.py: order_progress_page + 체크리스트 CSS, 상세 페이지에 progress 버튼
  (instant action 은 이력이라 체크리스트 아닌 _tele 카드로)
- web/server.py: GET /adapter/<key>/progress 라우트
- docs/guide/web-ui.md: 진행 절 추가, §9 현황 정정
- GitHub issue #9(오더 원본 보관), #10(현장 실기 검증) 등록

### 다음
- 커밋 불필요 — 37e2327 에 이미 포함됨(다른 세션 변경과 한 커밋에 섞였다)
- 현장 검증은 #10 체크리스트대로
- 지나온 노드/노드별 액션 묶음은 #9 (order.json 보관) 착수 시

### 검증
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_monitor.py tests/test_web_render.py tests/test_web_server.py tests/test_file_monitor.py -q → 312 passed
