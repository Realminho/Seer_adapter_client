# pio-serial-port-by-id

### 목표
- `pio_serial_port`를 `/dev/serial/by-id/...` 안정 경로로 바꿔 로봇마다 값이 갈리지 않게 함
- 그 값을 현장에서 찾을 수 있는 수단 제공

### 지금
- 구현 완료. `.61` 적용·재시작까지 끝. 커밋은 안 함

### 배경 (오늘 현장에서 확인된 것)
- `.61` = `/dev/ttyUSB4`, `.62` = `/dev/ttyUSB0` — 같은 PL2303인데 열거 순서가 달라 값이 갈림
- 오늘 USB 포트를 여러 번 옮기면서 `ttyUSB4`가 생겼다 사라졌다 함
- 로봇 실측: pyserial이 by-id 심볼릭 링크를 **그대로 연다** (코드 변경 불필요)
  ```
  OPEN OK : /dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0
  ```
- 저장소에 시리얼 포트 나열 기능 **없음** (`comports`/`list_ports`/`by-id` grep 0건)

### 계획
1. 시드 + 두 로봇의 `pio_serial_port`를 by-id 경로로 교체 (코드 변경 0)
2. `scripts/list-serial-ports.sh` 신규 — by-id 이름 / ttyUSB / VID:PID / 제품 표로 출력,
   PL2303(`067b:2303`)을 PIO 후보로 표시. 인자 없으면 로컬, `user@host`면 SSH
3. `extensions/pio/__init__.py:454` 실패 메시지에 by-id 후보 목록 추가

### 완료
1. **시드 3곳을 by-id로 교체** (코드 변경 0 — pyserial이 심볼릭 링크를 그대로 연다)
   - `adaptor/config/extensions.hcl:11` + 이유 주석
   - `adaptor/config/extensions.hcl.example`
   - `adaptor/config/fleet.py` (로봇별 override 예시)
2. **`scripts/list-serial-ports.sh` 신규** — by-id / ttyUSB / VID:PID / 비고 표.
   `067b:2303`은 PIO 후보, `0403:6011`은 드라이브 MCU로 표시. 인자 없으면 로컬,
   `user@robot`이면 SSH. 읽기 전용, sudo 불필요
3. **실패 메시지에 후보 나열** — `extensions/pio/__init__.py`에
   `serial_port_candidates()` / `describe_serial_port_candidates()` 추가,
   BC 프레임 검증 실패 메시지(`pio_init`)에 연결
4. **`.61` 실적용** — 백업 `extensions.hcl.bak-20260819-byid`, 재시작 PID 189318

### 다음
- **`.62` 미적용** — 11:27부터 접속 불가. 복구되면
  `scripts/list-serial-ports.sh ucore@192.168.101.62`로 by-id 이름이 `.61`과 같은지
  확인 후 같은 값 적용
- 커밋 미실시

### 검증
- TDD: `tests/test_pio_serial_port_candidates.py` 4건.
  RED(`ImportError`) 확인 → 구현 → GREEN
- **전체 스위트 `2048 passed`** (340초). 경고 2900건은 기존 `datetime.utcnow()` deprecation
- `.61` 실장비: `VALIDATE: True config valid`,
  `serial.Serial(by-id 경로)` → `is_open = True`,
  재시작 후 `io.json`의 `pio.port`가 by-id 경로로 반영됨
- `scripts/list-serial-ports.sh ucore@192.168.101.61` 실행 → PL2303을 PIO 후보로 정확히 표시
- `bash -n` 문법 검사 통과

### 주의
- `.62`의 by-id 이름 **미확인** (11:27부터 접속 불가). 같은 PL2303이라 동일할 가능성이 높지만
  다르면 두 로봇 값이 또 갈림 → 복구 후 확인 필요
- Prolific 칩은 시리얼 번호가 없어 by-id에 개체 식별자가 없음. PL2303을 2개 꽂으면 충돌 → by-path 필요

---

## 이월된 미해결 (현장 작업, 2026-08-18~19)

- **`output_signals`가 두 로봇에서 모순**: `.61` = 5/4 (recipe=workflow 일치),
  `.62` = 4/3 (사용자 현장 테스트 결과). 같은 엘리베이터인데 한쪽은 틀림.
  `.62`는 recipe(EZI3/EZI2)와 workflow(EZI4/EZI3)가 어긋난 상태 → **주문 경로에서 닫기가 문을 엶**
- **`pioElevatorOpen*`에 층 호출·도착 확인 단계 없음** — 8/18 밤 문이 안 열린 원인.
  프로덕션 상태머신 `utils/elevator.py:363`은 `floor_pin` 입력을 먼저 보고 카를 부름
- **`pio_init`이 BC 응답의 station을 요청값과 대조하지 않음** — 층을 틀려도 성공으로 보임
- **UNSUPPORTED HARD 액션에서 주행이 계속됨** (`adapter_jibot.py:4218` 설계)
- **로봇 배포본이 저장소 기준에서 벗어나도 잡을 점검 수단 없음** (8/18 23:27 회귀를 아무도 못 잡음)
- **진동**: `EV=b`라 EV_FF 없음. `.61`에 xboxdrv가 깔렸으니 `--force-feedback` 시험 가능해짐
- **`.62` 접속 불가** 11:27부터. keeper v2인데 자동복구 안 됨 → 전원 의심
