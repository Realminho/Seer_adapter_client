# action-panels-design-revise

### 목표
- 액션 모듈 패널 설계 문서의 3개 공백 반영 개정 → 구현 계획 작성

### 지금
- 신규 작업: deploy-compat 구현 코드 리뷰 (커밋 38bcfe4..b50d138)
- 검증: bash -n OK, 쉘테스트 2개 PASS, pytest SSH 54개 PASS, git diff --check 클린
- MUST 2건 해결 확인: Hexplorer 헬퍼 업로드+검증, JIBOT --restart webui 포함(옵셔널)
- 남은 SHOULD: Hexplorer --restart는 -tt/sudo fallback 없음(JIBOT 대비 비대칭)

### 완료
- 설계 문서 개정: D1(ActionSpec.label)·D2(import-light)·D3(core 공유 발견 함수),
  6.1 비대칭 실패, 6.2 _JIBOT_INSTANT_ACTIONS 분해, 12 배포, 13 결정 로그 추가
- 구현 계획 작성: docs/superpowers/plans/2026-07-19-action-module-panels.md (14 task, TDD)

### 다음
- 사용자가 실행 방식(subagent-driven / inline) 선택하면 착수
- 실행 시 유의: whitelist(instant_actions) 유지, import-light 검증, get_config 최소 TOML

### 검증
- 계획 git diff --check 통과, placeholder 없음, task 14개
