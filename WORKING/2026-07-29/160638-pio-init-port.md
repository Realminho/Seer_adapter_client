# diagnose-stale-air-shower-config

### 목표
- 설정을 수정했는데도 `[air_shower_pio]` 이전 오류가 발생하는 원인을 진단한다.

### 지금
- 기본/로봇별 config 선택과 이전 섹션 검사 조건을 확인했다.

### 완료
- 오류는 로더가 읽은 TOML에 내용이 있는 `[air_shower_pio]` 테이블이 남을 때만 발생함을 확인했다.

### 다음
- 로봇의 서비스 WorkingDirectory, robots.hcl의 `config` 지정, 모든 TOML 잔존 섹션을 확인한다.

### 검증
- `get_config()`의 `_PIO_SECTIONS` 검사와 `resolve_instance()`의 로봇별 config 경로 해석을 대조했다.
