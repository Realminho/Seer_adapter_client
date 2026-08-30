# action-scope-doc

### 목표
- stopCharging order 실패 조사 결과와 action 스코프 통합 방향을 문서로 남김
  (구현은 나중)

### 지금
- 문서 작성 완료. 커밋 안 함 (현재 develop 브랜치이고 다른 세션의 미커밋 작업이 있음)

### 완료
- `docs/todo/action-scope-and-dispatch-unification.md` 신규 작성
  - 원인: `_dispatch_order_action`에 stopCharging 분기 없음, 39갈래 중 2개만 손복사
  - 목록 3벌 불일치 (factsheet 50 / WebUi 23 / 실행 39+2)
  - 39개 중 33개가 order에서 실패, 17개는 INSTANT 선언인데 order에서 동작
  - 4단계 제안: 선언 테이블 → 3소비자 통합 → 핸들러 이관 → 공통 코어 분리
  - SEER 관련: extension은 이미 vehicle 호출 0건, 공통 로직이 어댑터에 갇힌 게 문제
- `docs/todo/action-error-level-gating.md`에 상호 참조 한 줄 추가
- 설명용 아티팩트: https://claude.ai/code/artifact/4a43a741-1d9c-4a40-9527-2e206b7c9063

### 다음
- 진행 시 1단계(`ActionSpec.scopes` 추가 + 39개 등록)부터
- 커밋하려면 develop 말고 브랜치 먼저 만들 것

### 검증
- 모든 수치 develop에서 재측정 (2026-08-22): elif 39, order ok 6 / fail 33,
  factsheet 50, WebUi 23, silent-order-ok 17, scoped 2,
  adapter_jibot.py 8504줄, vehicle 21종 44곳, extensions vehicle 호출 0건
- 재현 재확인: stopCharging → FAILED "Unsupported order action type"
- 문서의 file:line 13곳 전부 현재 develop 기준으로 대조·수정함 (병합으로 전부 밀려 있었음)
