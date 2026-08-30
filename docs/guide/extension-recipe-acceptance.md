# Extension recipe 현장 acceptance

이 문서는 `extensions.hcl`과 `recipes.hcl`을 로봇에 적용하기 전 simulator 검증과
PIO·에어샤워·엘리베이터 실설비 검증 순서를 정의한다.

## 1. 설정 준비

`config/recipes.hcl`은 이미 저장소에 있다. 백지에서 새로 쓸 때만 예시를 복사한다.

```bash
cd adaptor
cp config/recipes.hcl.example config/recipes.hcl   # 처음부터 새로 쓸 때만
```

현장 값으로 다음 항목을 조정한다.

- 에어샤워: 입구·출구 door pin, 진입·이탈 거리, 이동 속도
- 엘리베이터: 출발·도착 floor pin, PIO station ID, 도착 map ID
- 모든 이동 거리는 mm, 속도는 JIBOT `move_distance`가 받는 단위를 사용한다.
- `robots.hcl`에서 로봇별 파일을 쓸 때는 `recipes = "..."` 상대 경로를 지정한다.

### 기본 `config/recipes.hcl`

로봇별 `recipes` 키를 지정하지 않으면 모든 로봇이 이 파일을 읽는다. 현재 `extensions.hcl`
에서 확인된 값을 반영해 recipe 5개가 들어 있다.

엘리베이터 문 (주행 없음, `motion = false`):

- `pioElevatorOpen1f`, `pioElevatorOpen2f`, `pioElevatorClose1f`, `pioElevatorClose2f`
- 문 버튼은 설비로 나가는 PIO 신호선이라 step이 `pioWriteOut`이고, 그 신호를 실제로
  구동하는 것은 내부적으로 EZI IO다(`extensions/pio` `pio_write_output` →
  `ezi_io.turn_on_output`). 직렬 PIO는 station 페어링 전용이다.
- recipe는 숫자가 아니라 신호 이름을 적는다: `signal = "elevatorOpen"`. 풀이는
  `extension "pio"`에서 끝난다 — `output_signals`(이름 → PIO out 번호) →
  `output_pin_map`(out 번호 → EZI IO 핀). 기본값에서
  `elevatorOpen` → out `5` → EZI IO out`4` = `open_door_pin`.
- 배선이 바뀌면 `output_pin_map`과 `extension "elevator"`의 door pin만 고치고
  `recipes.hcl`은 그대로 둔다. 예전 `output_pins` 리스트도 계속 읽지만
  (`output_pin_map`이 있으면 무시), 위치로 짝을 추론해 한 칸 밀려도 알 수 없다.
- 버튼이 momentary pulse이므로 on 뒤에 off가 따라오고, off를 cleanup에도 둔다.
- 문이 실제로 열렸는지는 확인하지 않는다. 확인이 필요하면 아래 통과 절차를 쓴다.

설비 통과 절차 (주행 포함, `motion = true`):

- `airShowerPassage`, `elevatorUp`, `elevatorDown`
- 에어샤워 entry/exit door pin: `0` / `1`
- 1층: floor pin `0`, PIO station ID `000010`
- 상층: floor pin `1`, PIO station ID `000020`

map ID와 진입·이탈 거리·속도는 저장소에 확정된 안전값이 없어 실행 action parameter로
남겨 두었다. 현장 측정 없이 임의값을 고정하지 않는다. 따라서 통과 절차 recipe를 부를 때는
`enterDistanceMm`, `exitDistanceMm`, `moveSpeed`가, 엘리베이터에는 `targetMapId`가 함께
와야 한다. 빠지면 해당 step에서 `missing recipe parameter: <이름>`으로 실패한다.

로봇마다 설비 절차가 달라지면 그때 파일을 나눈다. `robots.hcl`의 robot 블록에
`recipes = "..."`를 쓰면 그 로봇은 기본 파일 대신 그 파일**만** 읽으므로, 아직 필요한
recipe는 새 파일로 옮겨 담아야 한다.

설정만 먼저 검증한다.

```bash
../scripts/validate-extension-recipes.py \
  --robots config/robots.hcl --robot <ROBOT_ID>

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_recipes_config.py tests/test_recipe_acceptance.py -q
```

부팅 시 다음 오류가 하나라도 나면 운행하지 않는다.

- 없는/비활성 extension 참조
- recipe 이름과 기존 action type 충돌
- recipe 중첩
- 누락된 필수 extension 블록
- 잘못된 timeout, retry 또는 HCL 문법

## 2. Simulator 검증

`robots.hcl` 대상 로봇에 `simulator = true`를 설정하고 adapter를 실행한다.

```bash
.venv/bin/python main.py --robot <ROBOT_ID>
```

ACS 또는 WebUI에서 먼저 개별 action을 확인한 뒤 recipe를 실행한다. WebUI에서는
nav의 **Actions**( `/adapter/<key>/actions` )가 extension·recipe의 유일한 실행
창구다 — 어댑터 상세 화면에는 나오지 않는다([web-ui.md](web-ui.md) 참고).

1. `manualStop`
2. `switchMap`
3. `manualMove` — 작은 거리와 저속
4. facility action — simulator에서는 실제 PIO가 없으므로 mock/시험 장비가 있을 때만
5. `pioElevatorOpen1f` / `pioElevatorClose1f` — 주행이 없어 가장 먼저 걸러낼 수 있다
6. `airShowerPassage` 또는 `elevatorUp` / `elevatorDown`

확인 항목:

- recipe action 하나만 VDA5050 state에 보이고 `:stepN:tryN` 합성 action은 노출되지 않음
- 첫 실패 이후 본문 step은 실행되지 않음
- 실패·timeout·취소 후에도 cleanup이 선언 순서대로 실행됨
- motion timeout 시 `um_stop()` 완료 후 다음 attempt 또는 cleanup으로 이동함
- order action으로 실행하면 같은 order의 motion은 허용되고 다른 order는 거부됨

### 실행 중 action 표시와 사운드

recipe나 extension이 실행돼도 `workingState`와 `workingStateDetail`은 로봇의
주행·충전·도킹·안전 상태를 그대로 표시한다. action 실행 정보를
working-state 토큰으로 덮어쓰지 않고, `information[]`의 `infoType="AMR_STATE"`
참조에 다음과 같이 함께 보낸다.

```json
{
  "workingState": "DRIVING",
  "workingStateDetail": "DOCKING",
  "activeActionType": "elevatorUp",
  "activeActionTypes": "[\"elevatorUp\"]",
  "activeStepActionType": "elevatorInside",
  "activeStepActionTypes": "[\"elevatorInside\"]"
}
```

- `activeActionType`: 실행 중인 부모 recipe/extension action 하나. 없으면 빈 문자열
- `activeActionTypes`: 동시 실행을 포함한 부모 action 이름의 JSON 배열 문자열
- `activeStepActionType`: recipe가 현재 실행하는 extension step 하나. 없으면 빈 문자열
- `activeStepActionTypes`: 동시 실행 중인 recipe step 이름의 JSON 배열 문자열

order에 들어 있지만 아직 시작하지 않은 `WAITING` action은 active 목록에
넣지 않는다. recipe 내부의 `:stepN:tryN` action state도 여전히 외부에
노출하지 않고 `activeStepActionType` 값만 바꾼다. 종료·실패·취소 후에는
해당 값과 배열에서 action이 제거되는지 확인한다.

사운드는 extension과 recipe를 따로 구분하지 않고 실제 `actionType`으로
파일을 찾는다. 대소문자까지 action 선언과 동일해야 한다.

```text
sounds/action-elevatorUp.mp3
sounds/action-elevatorInside.mp3
```

재생 우선순위는 에러코드·비상·위치상실·결함·브레이크·회피 →
현재 recipe step → 부모 recipe/extension action → 기존 `workingStateDetail` →
`workingState`다. step 파일이 없으면 부모 action 파일로, 그것도 없으면
기존 상태 사운드로 폴백한다. 상세한 파일 규칙과 장치 설정은
[`adaptor/sounds/README.md`](../../adaptor/sounds/README.md)를 따른다.

현장 acceptance에서는 recipe의 각 step을 충분히 길게 실행하여 다음을
확인한다.

1. `workingState`/`workingStateDetail`이 action 이름 때문에 바뀌지 않는다.
2. 부모 recipe가 시작하면 `activeActionType`이 recipe actionType으로 바뀐다.
3. step이 바뀐 때마다 `activeStepActionType`과 재생 파일이 같이 바뀐다.
4. 안전 상태가 발생하면 action 사운드보다 안전 사운드가 우선한다.
5. recipe 종료 후 active 참조는 비워지고 사운드는 현재 working state로 돌아간다.

## 3. 실설비 사전 점검

로봇을 바닥에서 띄우거나 즉시 정지 가능한 시험 구역에 둔다.

- 물리 E-stop과 수동 정지 담당자를 배치한다.
- recipe의 진입·이탈 거리를 최소값으로 낮춘다.
- `extensions.hcl`의 serial port와 EZI IP가 해당 로봇 값인지 확인한다.
- adapter 프로세스가 PIO serial port를 단독 점유하는지 확인한다.
- `manualStop`과 물리 E-stop을 먼저 시험한다.

## 4. 에어샤워

개별 action 순서:

1. `airShowerEnter`
2. 저속 `manualMove`
3. `airShowerInside`
4. 저속 `manualMove`
5. `airShowerPassed`
6. `pioDisconnect`

각 단계에서 door pin, SELECT/GO, occupied, airflow 신호와 실제 문 상태가 일치해야 한다.
개별 검증 뒤 `airShowerPassage`를 실행한다.

## 5. 엘리베이터

개별 action 순서:

1. `elevatorEnter`
2. 저속 `manualMove`
3. `elevatorInside`
4. `switchMap`
5. 저속 `manualMove`
6. `elevatorPassed`
7. `pioDisconnect`

층 이동 후 `switchMap`이 완료되기 전에 이탈 이동이 시작되면 실패로 판정한다.
floor pin과 PIO station ID는 출발·도착 층별로 교차 확인한다.

## 6. 실패 및 복구

다음 상황에서는 즉시 `manualStop` 또는 물리 E-stop을 사용한다.

- timeout 뒤에도 차량이 움직임
- cleanup 전에 다음 recipe가 시작됨
- 같은 serial port를 여는 프로세스가 둘 이상임
- 문/엘리베이터 실제 상태와 PIO 상태가 다름
- 늦은 완료가 다음 retry를 완료 처리함

설정 롤백은 원격 `config/recipes.hcl`을 이전 파일로 복원한다. recipe 0개로 돌릴 때는
`robots.hcl`의 명시적 `recipes` 키를 제거한 뒤 기본 `config/recipes.hcl`도 다른 이름으로
보관하고 재기동한다. 명시한 경로의 파일만 없애면 부팅 오류가 된다.
`extensions.hcl`은 필수 파일이므로 삭제해서 비활성화하지 않는다.
