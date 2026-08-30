# pio-elevator-recipe-order-action

### 목표
- `use_nearest_node_as_last_node_when_missing` 폴백에 거리 임계값을 추가한다.

### 지금
- TDD: 임계값 밖이면 seed 안 되는 실패 테스트를 먼저 쓴다.

### 설계
- 새 설정 `missing_last_node_reach_xy: float = 0.0` (raw map unit).
- **0 = 무제한**으로 두어 현행 동작을 그대로 보존한다. 이 옵션은 이미 배포 config에서
  켜져 있으므로(`config.toml:66`) 기본값을 바꾸면 현장 동작이 조용히 달라진다.
- 형제 설정 `idle_last_node_reach_xy`는 `<=0`이면 무제한이 아니라
  `_effective_reach_deviation_xy()`로 폴백한다(`adapter_jibot.py:6079-6085`).
  **규약이 다르므로** 주석에 명시한다.
- 게이트 위치: `adapter_jibot.py:6452-6467`의 폴백 조건절. `distance`는 이미
  같은 스코프에 있다(로그에만 쓰이던 값).
- 기존 테스트 `test_missing_last_node_can_be_seeded_from_nearest_without_reach_gate`가
  `idle_last_node_reach_xy = 10.0` + distance 1000에서 seed됨을 단언한다. 새 설정이
  그것과 독립임을 이 테스트가 그대로 지켜준다 — 깨지면 안 된다.

### 완료
- `config/config.py`: `missing_last_node_reach_xy: float = 0.0` 추가(주석에 <=0 규약 차이 명시).
- `adapter_jibot.py`: `_missing_last_node_reach_xy()` 헬퍼 + 폴백 조건에 게이트 적용.
  거부 시 `[LAST NODE FALLBACK SKIP] gap=... > reach=...` 로그를 남긴다 —
  조용히 비면 나중에 LAST_NODE_ID_MISSING만 뜨고 원인을 알 수 없다.
- `config/config.toml`: `missing_last_node_reach_xy = 0.0` 노출 + 주석.
- 테스트 4개 추가(게이트 밖 거부 / 안 허용 / idle_last_node_reach_xy와 독립 /
  기본값 0 고정).

### 다음
- 현장 노드 간격에 맞는 값을 정해 켤지 결정. 지금은 0(제한 없음)이라 동작 변화 없음.

### 검증
- TDD: 게이트 테스트가 먼저 실패(2 failed)하는 것을 확인한 뒤 구현했다.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/ -q`
  -> **1323 passed**. 기존 `test_missing_last_node_can_be_seeded_from_nearest_without_reach_gate`
  (idle_last_node_reach_xy=10, distance=1000에서 seed됨)도 그대로 통과 — 두 설정이
  독립임이 지켜졌다.

### 미커밋 (이전 작업, 그대로 남아 있음)
- `config/recipes.hcl` 신규(recipe 5개), `HN-SH6-TR-001-recipes.hcl` 삭제, NH->HN 철자 수정
- `config/adapter_dispatch.py` 기동 실패 안내 + `_python_bin()`
- `scripts/update-jibot-adapter-over-ssh.sh` recipes.hcl 시드 + robots.toml 자동 변환
- 검증: adaptor 1316 passed / 리포 루트 107 passed
