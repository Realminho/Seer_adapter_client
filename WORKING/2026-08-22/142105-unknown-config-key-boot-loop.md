# unknown-config-key-boot-loop

### 목표
- 모르는 설정 키(`input_signals`)가 부팅 자체를 막고 systemd 크래시 루프를 만드는 문제의 근본 원인 제거
- 설정 오류는 config-error 모드로 살아남아 MQTT로 보고되어야 함

### 지금
- 근본 수정 완료, 전체 스위트 확인 완료 (미커밋)
- 2차 제보(JIBOT 재접속 중 사망) 원인 조사 대기 — 종료 직전 로그 근거 필요

### 완료
- 재현: extension "pio"에 모르는 키 → `PioConfig(**...)`가 라벨 없는 TypeError
- 근본 원인: 구조 오류를 **예외 타입**으로 가르는데, 손으로 열거한 키
  (pio_port/station_id/channel)만 ExtensionsError가 되고 나머지는 TypeError →
  보고 경로를 못 타고 SystemExit(1) → 재시작 → 동일 실패 반복
- `config/errors.py`: 공용 `ConfigError(path=...)` 신설, Extensions/RecipesError가 상속
- `config/config.py`: `_section()`이 섹션 dict→dataclass 변환 시 모르는 키/빠진
  필수 키를 검증하고 파일·블록·쓸 수 있는 키를 담은 ConfigError로 올림 (22개 지점)
- `get_config_with_fallback`: ConfigError 전체를 보고 경로로 구제, 4단계 하강
  (로봇 toml→base toml→lenient) — lenient는 보고 전용 config에만
- `main.config_error_path`: `error.path`를 우선 사용
- elevator 블록도 같은 검증 대상으로 편입 (전에는 빠진 키=KeyError, 남은 키=무시)
- 빠진 키는 어느 파일에도 없으므로, 그 섹션을 갖고 있는 파일을 주인으로 지목

### 다음
- 커밋 (사용자 확인 후)
- JIBOT 재접속 중 프로세스 종료 건: journalctl에서 `[TASK DIED]` / `[TASK EXITED]` /
  `[ADAPTER EXIT]` 줄과 그 traceback 확보 → 어느 supervised 루프가 죽는지 확인

### 검증
- `pytest tests/test_unknown_config_key.py tests/test_config_error_path.py` 12 passed
- 전체 2071 passed / 6 failed, 6건은 내 변경 전 HEAD 복사본에서도 동일하게 실패
  (shipped extensions.hcl/recipes.hcl 내용 드리프트 — 이 작업과 무관)
- 3가지 실패 유형(모르는 키/빠진 키/elevator 오타) 모두 부팅 유지 + 정확한 파일 지목 확인
