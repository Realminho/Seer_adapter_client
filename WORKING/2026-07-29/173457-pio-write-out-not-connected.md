# pio-write-out-not-connected

### 목표
- `pioWriteOut` 실패를 고치고, 시리얼 링크가 실제로 살아 있는지 확인할 수단을 만든다.

### 지금
- pioPing 추가와 connected 상태 정직화를 마쳤다.

### 완료
- 1차: `ensure_pio_connected()`로 write/read 전 포트를 연다. `PIOMaster.connect()`는 열린 포트를 재사용한다.
- 2차 원인: `pio_write_output`이 응답을 버리고 `connected=True`를 무조건 찍어, 보드를 뽑아도 램프가 켜졌다.
- `pioPing` 액션 추가 — `D={channel}` 읽기 전용 왕복. 포트 미개방/무응답/정상을 구분해 리포트하고 출력은 안 건드린다.
- write는 응답이 있을 때만 `connected=True`, 무응답이면 기존 상태 유지 + 메시지에 pioPing 안내.
- `pioReadIn` 무응답 메시지를 "8 bits" 대신 "PIO board did not respond on {port}"로 바꿈.
- 패널에 Ping 버튼 추가 (pioInit 다음).

### 다음
- 실기 결과: pioPing이 opened:true / rxBytes:0. 동작하는 workflow 코드 대조로 원인 규명.
- 핵심: airshower/elevator는 시리얼 응답을 **한 번도 읽지 않는다**. `send_bc()` 반환값 폐기,
  pairing 성공 판정은 EZI-IO **입력** GO 핀 (airshower.py:303, elevator.py:341).
  → PIO 시리얼은 write-only 채널로 쓰이고 있고, pioPing/pioReadIn/pioInit의 응답 전제가 근거 없음.
- 시리얼 송신은 항상 SELECT(EZI-IO 출력 15) on/off로 감쌈. pio extension은 이걸 안 함.
- 별건 버그: airshower.py:305가 `self.paring()` 호출 — 메서드는 `pairing()`(560).
  재시도 루프가 첫 회에 AttributeError → ERROR_AT_PAIRING. elevator.py:343은 정상.
- 완료: pioPing을 workflow와 같은 순서로 재작성 (SELECT on → BC → SELECT off → GO polling).
  판정은 GO 입력, BC 응답은 참고 필드(bcReply/bcReplyFramed). 실패·예외에도 finally로 SELECT 해제.
  출력 전체 정리(reset_mask)는 안 함 — 다른 출력 떨어뜨리면 안 됨.
- 완료: airshower.py:305 `paring()` → `pairing()` 오타 수정.
- 완료: utils/pio.py connect() 주석 정정 (pyserial 기본은 비배타 open, 배타 잠금 아님).
- 다른 세션이 같은 파일에 find_pio_frame/pio_init 프레임 검증 추가함 — 충돌 없이 재사용함.

- 실기 확인: Ping 성공, GO 올라옴. pairing 성립.
- 원인2: pioDisconnect가 시리얼 fd만 닫아 설비 pairing이 유지됨 (GO 계속 on).
- 완료: `pio_unpair()` 추가 (BC 없이 SELECT on→off, workflow의 handle_unpairing과 동일).
  `pio_disconnect_action()`이 unpair → GO 내려가는지 polling → 포트 close 순으로 실행.
  `clearOutputs` opt-in (기본 off) — reset_mask는 문·층 요청까지 떨어뜨리므로.
  `pio_disconnect()`는 포트만 닫는 저수준으로 남겨 pioScenario 뒷정리가 그대로 쓰게 함.

- 실기 응답 확보: `[BC=2:569A-123456:250:0:OHT1236B]` — 체크섬 6B 검증 통과.
  요청은 `<...>`, 응답은 `[...]`로 구분자가 방향마다 다름. 기존 정규식이 `<...>`만 찾아
  정상 응답을 항상 None 처리 → pioInit이 멀쩡한 링크에서도 실패하던 2차 원인.
- 완료: `find_pio_frame`을 체크섬 검증으로 교체(`pio_checksum_hex`), `[]`/`<>` 모두 허용.
  PIOMaster.checksum_hex와 어긋나지 않게 테스트로 묶음.
- 완료: `pio_init` = SELECT 게이팅 + GO 판정, pairing 유지 (운영용).
- 완료: `pio_ping` = pairing → 출력 1~8 on/off → unpair → (자기가 연 포트면) close.
- 공용 헬퍼 `pio_link_params` / `pio_pair` / `pio_wait_go` / `require_ezi_io`로 init·ping 공유.
- 패널: Ping이 출력을 건드리므로 confirm 게이트 추가.

### recipe 확인 결과
- `ezioElevatorOpen`/`ezioElevatorClose` 있음 → 문 열기/닫기 단위 실행 가능 (EZI-IO 버튼 pulse만).
- "타기" 단위 recipe 없음 — `elevatorUp`/`elevatorDown` 안에만 있고 manualMove+switchMap이 묶여 있음.
- 상태 머신 elevatorEnter/Inside/Passed는 recipe 없이도 WebUI 개별 실행 가능
  (facility에 panel.html이 없어 `_module_fallback_cards`가 액션별 카드 렌더, station은 dropdown).
  ENTER=호출+문열림, INSIDE=문닫고 목표층 이동, PASSED=내린 뒤 문닫고 해제.
- 주의: 두 문 recipe의 첫 step이 `pioInit`이라, 지금까지 step 1에서 막혀 있었을 것 (SELECT 없었음).

- 실기 pioInit 실패: "GO stayed off for 2.0s". 3차 원인 = **재시도 없음**.
  workflow(elevator.py:341-347)는 GO가 올라올 때까지 `pairing()`을 반복하고
  예산은 `timeout_paring_requesting * 60` = 30초. 우리는 한 번 보내고 2초 수동 대기였음.
- 완료: `pio_pair_until_go()` 추가 — GO 올라올 때까지 BC 재전송. init/ping 둘 다 사용.
  `pair_timeout_sec` 기본 30.0 (config knob 신설), 액션 파라미터 `pairTimeoutSec`로도 조정.
  결과에 `bcAttempts` 포함.
- 완료: PioAdvancedConfig에 pair_timeout_sec/select_settle_sec/ping_*/unpair_* 필드 추가,
  extensions.hcl과 .example에 주석과 함께 기재.
- 완료: 단위 recipe 3종 추가 (`elevatorStepEnter`/`StepInside`/`StepPassed`).
  station은 `${var.station}`으로 받고 motion=false, cleanup에 pioDisconnect.

- 재시도 로직은 정상 동작 확인 (11회/30초). 이제 코드 문제가 아님.
- 남은 변수는 **stationId 하나**:
  - 17:55 pioPing 성공 = `123456` (extension "pio" station_id, ping은 파라미터 비우면 config)
  - 19:06 pioInit 실패 = `000010` (운영자 입력, extension "elevator" motion_rules 값)
  - 두 경우 다 보드는 BC에 응답함("BC was answered") → 시리얼·프레임은 정상.
    차이는 설비가 GO를 올리느냐뿐.
- 설정 두 곳이 서로 다른 station을 말하고 있고, 실제로 pairing된 건 123456뿐:
  extension "pio" station_id="123456"  vs  extension "elevator" pio_station_id="000010"/"000020"
  → 000010이 틀린 값이면 elevatorUp/Down과 새 단위 recipe도 전부 pairing 실패한다.

- webui PIO out 배지가 안 먹는 원인: `/io/out` board=pio → `pioWriteOut` → 직렬 `OUT=n:v`.
  `OUT=`은 보드 명령이 아니다 (최초 구현 cls_pio.py 명령은 BC=/C=/D= 뿐). 아무 일도 안 일어남.
- 완료: `pio_write_output`을 EZI IO 구동으로 교체. `PioConfig.output_pins`(기본 [0..7])로
  PIO out 1~8 → EZI IO 핀 매핑. 쓴 뒤 `get_output()`으로 읽어 확인하고 불일치면 실패.
  EZI IO 없으면 명시적 실패. 직렬은 이제 BC pairing 전용.
  ※ output_pins 기본값은 현장 신호 핀이 전부 0~7이라는 근거로 잡은 것 — 실배선 확인 필요.

- 사용자 확인: EZI IO 0~7 = PIO out. output_pins 기본값 맞음.
- 완료: 입력도 대칭으로 교체. `pio_read_inputs`가 `read_ezio_input_bits`로 EZI IO
  digital input을 읽고 `input_pins`(기본 [0..7])로 PIO in 1~8에 매핑.
  직렬 `D={channel}` 경로와 `parse_pio_inputs` 제거, pioReadIn 파라미터 없앰.
  pioScenario의 in 단계도 같은 경로.
- EZI IO 핀 지도 정리: out 0~7 = PIO out(로봇→설비), in 0~7 = PIO in(설비→로봇),
  in 8~13 = 트레이 센서, out15 = SELECT, in15 = GO.
- 완료: airshower.py의 occupied(2)·fun_working(3) 읽기를 `get_output_pin` →
  `get_input_pin`으로 수정(6곳). 설비가 보내는 신호라 입력이 맞다.
  door/floor 읽기는 자기 write 되읽기라 출력 그대로 뒀다.
  옛 코드로 되돌리면 새 테스트가 **멈춘다** — airflow 무한 대기가 실증됨.
  테스트 추가: `adaptor/tests/test_airshower_status_pins.py`

- 완료: pioInit/pioPing의 stationId를 dropdown으로. `known_station_ids(config)`가
  extension "pio"의 station_id(맨 앞)와 elevator motion rule의 pio_station_id를 모은다.
  `action_specs(config=None)` 형태로 바꿔 config를 받는다(없으면 자유 입력 유지).
  → 123456 vs 000010 불일치가 화면에서 바로 보인다. facility를 import하지 않고
  config에서 직접 읽어 그쪽을 꺼도 PIO 패널이 뜬다.

### workflow 감사 (elevator/airshower)
- elevator.py는 airshower식 레지스터 버그 없음. line 400 `get_output_pin(floor)`는
  자기 write 되읽기라 정상. `is_solid_on`(595)은 죽은 코드(주석 블록에서만 참조).
- **버그 3건 수정 (elevator.py)**:
  1. `self.floor`가 정의된 적 없음 — 생성자는 `self.floor_pin`. 5곳이 `self.floor` 참조.
     → REQUESTING_FLOOR/GOTO_FLOOR가 AttributeError, bare except가 실패로 삼킴.
     즉 **ENTER/INSIDE는 한 번도 성공한 적 없음**. PASSED만 floor를 안 써서 동작.
  2. `handle_goto_floor` 성공 경로에 return 없음 → None(falsy) → INSIDE 항상 실패.
  3. `handle_requesting_floor`의 else 분기(다른 층 호출)에 return 없음 → ENTER 실패.
- **버그 1건 수정 (airshower.py)**: line 308이 `self.timeout_paring_requesting`을 보는데
  생성자는 `self.timeout_pairing_requesting`(i 있음). pairing 재시도 루프가
  AttributeError → ERROR_AT_PAIRING. 오전에 고친 `paring()` 오타 바로 다음 줄.
- 테스트 추가: `adaptor/tests/test_elevator_handler_returns.py`
  handler 반환값 + 생성자가 안 만든 self.<attr> 사용 금지 가드(이 가드가 4번을 잡음).

### 남은 것
- 실기에서 Disconnect 눌러 GO 내려가는지 확인.
- extensions.hcl의 select=15(출력)/go=15(입력) 실배선 확인 (사용자 미확답).
- pioInit은 여전히 SELECT 게이팅 없음 — 다른 세션이 그 함수 작업 중이라 손대지 않음.

### 검증
- `adaptor/tests` 1400 passed, 저장소 `tests/` 117 passed.
- PIO 관련 32개 통과 (신규 12개 포함).
