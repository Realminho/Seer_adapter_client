# remote-config-reload-design

### 목표
- 원격(EPR setParameters)으로 바꾼 설정을 사람 손 없이 반영하는 경로 설계
- 후보 A: 원격 재시작 instant action / 후보 B: 범위를 좁힌 config reload

### 지금
- 설계 문서 + 구현 계획서 완료. 사용자 리뷰 대기. **구현은 지시 전까지 착수 안 함**

### 완료
- 조사: 설정은 부팅 1회 로드(config.py:1088 load_recipes, configio.py:11)
- 조사: setParameters는 파일 쓰기+검증만, 메모리 미반영(adapter_jibot.py:1948)
- 조사: 원격 재시작 수단 없음(core/factsheet.py:13 INSTANT_ACTION_TYPES)
- 조사: 스왑 seam 존재 — self.config(확장 31곳 live 참조), self._action_registry(:4401,:4472,:5951), _build_factsheet(:3268)
- 조사: 재시작=비정상종료+systemd 패턴 확립(e5b3615, setup-adaptor-service.sh:868 Restart=on-failure)
- 조사: 멀티로봇은 run_multi가 자식 1개 죽으면 전체 그룹 재시작 → blast radius 주의
- 조사: 로컬 control socket(UDS)로 WebUI→어댑터 instant action 주입 가능(:1018)

### 다음
- 사용자가 두 문서 리뷰 → 승인 시 Task 1부터 TDD 실행
- 실행 방식은 subagent-driven / inline 중 사용자 선택

### 완료(설계 결정)
- 트리거=새 instant action 분리 / 범위=전체 재로드+미반영 필드 보고 / 게이트=IDLE만 / restartAdapter 동반
- 난점 발견: Adapter가 overrides를 모른다(main.py:760 vs :936) → config_loader 주입 필요
- 난점 발견: systemd 기본 StartLimitBurst=5/10s → 최소 가동시간 게이트로 폭주 차단

### 검증
- 설계 문서 self-review 완료(참조 번호 오류 1건 수정), 커밋 84d4cf9
- 계획서 self-review 완료: spec 전 항목 커버, 플레이스홀더 0, 타입/이름 일관성 확인
- 계획 중 분류 오류 1건 발견해 spec 수정: internal_actions 는 __init__ 에서 복사(:378,:383)
  → reloadable 아님. 기본 거부 설계로 전환(최상위 28개 3갈래 + 하위는 기본 거부)
