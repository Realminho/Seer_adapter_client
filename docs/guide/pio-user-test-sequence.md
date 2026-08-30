# PIO 사용자 단위 테스트 시퀀스

PIO 코드 검증부터 실장비 입·출력 확인까지 한 대의 로봇에서 순서대로 수행한다.
앞 단계가 실패하면 다음 단계로 진행하지 않는다.

## 1. 테스트 목적

- PIO 설정과 action 등록 상태를 확인한다.
- PIO 입·출력 번호가 실제 EZI IO 핀에 올바르게 매핑되는지 확인한다.
- `SELECT → BC 송신 → GO ON` pairing과 `SELECT 토글 → GO OFF` unpairing을 확인한다.
- 성공과 실패 모두에서 출력과 시리얼 연결이 안전하게 정리되는지 확인한다.

## 2. 사전 조건

- 로봇을 정지시키고 자동 운행과 신규 order를 차단한다.
- 출력 1~8이 순차적으로 on/off되어도 안전하도록 설비 담당자와 확인한다.
- 물리 E-stop과 수동 정지 담당자를 배치한다.
- `adaptor/config/extensions.hcl`에서 아래 값을 실장비 값과 대조한다.
  - `pio_serial_port`, `pio_baudrate`
  - `media`, `port`, `vehicle_num` (이 로봇을 가리키는 값 — `extension "pio"`)
  - `pio_station_id`, `channel` (`extension "airshower"`)
  - `pio_station_id`(`motion_rules` 항목별, 층마다 다름), `channel`(`extension
    "elevator"` 블록 — 엘리베이터 시스템 하나에 하나)
  - EZI의 `select`, `go`
  - PIO out 매핑 `output_pin_map`(out 번호 → EZI IO 핀)과 신호 이름
    `output_signals`(이름 → out 번호). 예전 `output_pins` 리스트를 쓰는 설정이면
    위치로 짝을 추론하므로(리스트 i번째 = out i+1) 한 칸 밀리지 않았는지 본다.
  - PIO `input_pins`를 별도로 설정한 경우 그 매핑
- PIO 시리얼 포트를 다른 프로세스가 점유하지 않는지 확인한다.

## 3. 사용자 단위 테스트

저장소 루트에서 실행한다. 이 단계는 fake hardware를 사용하므로 실제 출력을 변경하지
않는다.

```bash
cd adaptor
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_adapter_jibot_v3_order.py \
  tests/test_action_modules.py \
  tests/test_registry.py \
  -k 'pio' -q
```

통과 기준:

- 종료 코드가 `0`이다.
- 실패(`failed`)와 오류(`error`)가 없다.
- 다음 동작군이 테스트 결과에 포함된다.
  - `pioInit`: BC 프레임·체크섬 검증, GO 재시도, 잘못된 포트 응답 거부
  - `pioReadIn`: 입력 1~8과 EZI IO 입력 매핑
  - `pioWriteOut`: 출력 매핑, read-back, 불일치 검출
  - `pioPing`: pairing, 출력 1~8 sweep, 실패 시 unpair
  - `pioDisconnect`: GO OFF 확인과 포트 close
  - `pioScenario`: out/in/blink/delay와 timeout cleanup, pair/disconnect 생략 경로

## 4. WebUI 읽기 전용 확인

1. 대상 로봇의 WebUI `Actions` 화면을 연다.
2. PIO 패널에서 포트와 baudrate가 대상 장비 값인지 확인한다.
3. `PIO read inputs`의 `Read`를 누른다.
4. 설비 담당자가 입력 1~8을 하나씩 변화시키고 매번 다시 읽는다.

통과 기준:

- 결과가 `inputs={'1': 'on|off', ..., '8': 'on|off'}` 형태로 8점을 모두 표시한다.
- 실제로 변화시킨 신호와 같은 번호만 바뀐다.
- 번호가 다르면 이후 출력 시험을 하지 않고 `input_pins`와 현장 배선을 확인한다.

## 5. 개별 출력 확인

설비의 구동부를 분리하거나 출력 변화가 움직임을 만들지 않는 안전 상태에서 수행한다.

각 `index` 1~8에 대해 다음을 반복한다.

1. `PIO write output`에서 `index`를 입력하고 `state=on`을 선택한다.
2. `confirm`을 체크하고 `Write`를 누른다.
3. 해당 설비 입력 또는 EZI IO 출력 한 점만 ON인지 확인한다.
4. 같은 `index`에 `state=off`를 실행한다.
5. 같은 점이 OFF로 복귀했는지 확인한다.

통과 기준:

- 각 action 결과가 `pioWriteOut finished: outN=... (EZI IO outM, read back)`이다.
- 요청한 한 점만 바뀌고 read-back mismatch가 없다.
- 실패하거나 예상하지 않은 설비가 반응하면 즉시 해당 출력을 OFF하고 시험을 중단한다.

## 6. Pairing과 해제 확인

1. 설비가 pairing 가능한 대기 상태인지 확인한다.
2. `PIO init`에서 시험할 설비의 `stationId`/`channel`을 입력한다. 두 값은
   `extension "pio"`로 폴백하지 않으므로 비우면 즉시 실패한다 — 위 2절에서 대조한
   `pio_station_id`/`channel`(해당 설비 블록) 값을 그대로 넣는다.
3. `Init`을 누른다.
4. 결과에서 유효한 `bcReplyFramed`, `bcAttempts`, `GO input ... on`을 확인한다.
5. WebUI PIO 상태가 connected로 바뀌는지 확인한다.
6. `PIO disconnect`의 `Disconnect`를 누른다.
7. 결과에서 `GO input ... went off`와 시리얼 포트 close를 확인한다.

통과 기준:

- Init 후 SELECT는 다시 OFF이고 GO는 ON이다.
- Disconnect 후 GO는 OFF이며 WebUI PIO 상태가 disconnected다.
- 응답이 있어도 체크섬이 틀린 프레임은 성공으로 처리되지 않는다.

실패 메시지별 확인 지점:

| 메시지 | 우선 확인 |
|---|---|
| `cannot open ...` | `pio_serial_port`, 포트 점유, USB 연결 |
| `BC response timeout` | 포트·baudrate, SELECT 배선, PIO 전원 |
| `no valid PIO frame` | PIO 변환기 포트인지, baudrate |
| `GO ... stayed off` | station ID·channel, GO 배선, 설비 pairing 조건 |
| `GO ... stayed on` | unpair 처리, SELECT 배선, 설비 해제 조건 |

## 7. 전체 자동 점검

개별 입·출력과 pairing 시험이 모두 통과한 뒤에만 수행한다.

1. 모든 출력이 OFF이고 설비가 안전 상태인지 확인한다.
2. `PIO 연결 확인`에서 시험할 설비의 `stationId`/`channel`을 입력한다(§6과 같은
   값 — 비우면 실행되지 않는다).
3. `confirm`을 체크하고 `Ping`을 누른다.
4. 설비 담당자는 out1부터 out8까지 각 점이 on→off 되는 순서를 관찰한다.

통과 기준:

- `paired → out1~8 on/off → unpaired → close` 순서로 끝난다.
- 출력 결과가 `8/8 ok`이다.
- 종료 후 모든 시험 출력이 OFF이고 GO도 OFF다.
- WebUI 상태가 disconnected이고 PIO error가 비어 있다.

## 8. 실패·중단 복구

1. 움직임이 발생하면 `manualStop` 또는 물리 E-stop을 사용한다.
2. 안전이 확보되면 각 출력 1~8에 `off`를 실행한다.
3. `PIO disconnect`를 실행해 GO OFF와 포트 close를 확인한다.
4. GO가 계속 ON이면 adapter를 통한 재시험을 중단하고 설비 측 pairing을 해제한다.
5. 원인과 실패한 단계, PIO/EZI 핀 상태, action 결과 메시지를 기록한다.

최종 합격은 단위 테스트 통과, 입력 8점, 출력 8점, pairing, unpairing,
`pioPing`의 `8/8 ok`를 모두 확인했을 때로 한다.
