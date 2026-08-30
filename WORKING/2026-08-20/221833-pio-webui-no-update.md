# pio-webui-no-update

### 목표
- webui "PIO — JIBOT Adapter" 섹션 값이 갱신되지 않는 원인 규명 및 수정 (ezio는 정상)

### 지금
- 수정 완료, 배포 후 현장 확인 대기

### 완료
- 원인: `_note_pio(inputs=/outputs=)`가 pio* action 실행 경로에서만 호출됨
  (`pio_read_inputs`/`wait_pio_input`/`pio_write_output`). 주기 폴링 없음.
  EZIO는 `extensions/ezio/manage_tray_slot` 1초 루프가 `_note_ezio(inputs=...)` 갱신 → 잘 나옴.
- PIO in/out은 직렬이 아니라 EZI IO 레지스터의 다른 이름(input_pins / output_pin_map)이므로
  이미 읽고 있는 비트를 그대로 옮기면 됨.
- `extensions/pio`: `map_pio_inputs()` / `map_pio_outputs()` 추가 (strict 플래그).
  `pio_read_inputs`는 strict=True로 재사용.
- `adapter_jibot.Adapter._refresh_pio_from_ezio()` 추가, `_io_throttle_tick`(2.5초)에서 호출.
  타임스탬프는 `_ezio_*_at`을 그대로 물려받음(EZI IO 읽기가 끊기면 PIO도 같이 늙음).
- out 표시 의미 변경: '마지막 명령값' → 'EZI IO 되읽은 실제값'. render.py docstring 갱신.
- 빈 맵 가드: 핀 맵이 어긋나 옮길 점이 없으면 기존 값/시각을 유지(명령한 출력 표시가 지워지지 않게).
- `connected` pill과 error notice는 직렬 BC pairing 상태라 그대로 action 시점 갱신.

### 다음
- 로봇 배포 후 `cat /run/amr-adaptor/<serial>/io.json`으로 pio.inputs_updated_at이
  ezio.inputs_updated_at와 함께 움직이는지 확인

### 검증
- `scripts/run-tests.sh tests/test_adapter_io_snapshot.py` 17 passed (신규 3건 포함)
- 전체 스위트: 2062 passed / 10 failed — 실패 10건 중 9건은 변경 전에도 실패(다른 세션의
  recipes.hcl 작업 등), test_senders 1건은 부하 시 flaky (단독 실행 시 통과)
