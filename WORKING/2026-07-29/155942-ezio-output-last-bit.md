# ezio-output-last-bit

### 목표
- webui에서 EZIO output 배지를 눌러도 마지막 켜진 출력이 꺼지지 않는 버그 수정

### 지금
- 수정 완료, 사용자에게 elevator 핀 번호 실장비 확인 요청 중

### 완료
- 원인: `adaptor/utils/ezi_io.py:output_bit()`가 n<15에서 `1<<(15+n)`로 한 칸 밀림
  (`get_output`은 output n을 bit 16+n으로 읽음 → 쓰기/읽기 비트 맵 불일치)
  - 배지 n(ON)을 눌러 off를 보내면 실제로는 출력 n-1이 꺼지고, n은 그대로 ON
  - n을 끄려면 배지 n+1을 눌러야 하는데 그 배지는 OFF 표시라 토글 목표가 on → 영원히 안 꺼짐
- d70e304(2026-07-25, elevator 작업)에서 들어온 hack이며 `n>=15`(select=15)만 원래 식 유지
- 수정: `output_bit`을 `1 << (16 + n)`로 되돌리고 0~15 범위 검증 추가
- 테스트 추가: `adaptor/tests/test_ezi_io_output_bits.py` (가짜 보드로 쓰기/읽기 대칭 잠금)

### 다음
- (사용자 확인 필요) extensions.hcl의 elevator `open_door_pin=4`, `close_door_pin=3`이
  밀린 드라이버 기준으로 맞춘 값이면 실제 배선은 3/2 → 실장비에서 확인
- 무관한 기존 실패 1건: tests/test_config.py::test_shipped_pio_port_is_a_posix_device
  (`PioConfig.pio_port` → `pio_serial_port` 개명 작업 중 잔여물, adapter_jibot._io_snapshot_dict도 옛 이름 사용)

### 검증
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q` → 1369 passed, 1 failed(위 무관 건)
- 옛 공식 재현 스크립트: out4만 ON인 상태에서 out4 off를 두 번 눌러도 `outputs[4]`가 1로 유지됨을 확인
