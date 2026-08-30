# SEER drop-in 테스트 가이드

## VDA5050 FMS 동일 입력 검증 원칙

현재 WebUI와 `manual_test.py --vda5050`의 FMS-facing 입력은 실물/Simulator 모두 동일하다.
이름 있는 경로는 MQTT `/order`의 `nodes[] + edges[]`, 즉시 Action은 MQTT
`/instantActions`의 `actions[]`를 사용한다. 중간 대기는 node `actions[]`의 `seerWait`
(HARD)로 표현한다. 아래에 남아 있는 API 3051/3066 설명은 Adapter 아래쪽 SEER southbound
동작 검증 항목이며 WebUI가 그 API JSON을 직접 보내는다는 뜻이 아니다.


모든 명령은 `adaptor`, `seer_client`, `seer_simulator`가 보이는 저장소 루트에서
실행한다. 이 가이드의 방법은 원본 폴더와 파일을 수정하지 않는다.

## 구현 기능

| 구분 | 기능 |
|---|---|
| 연결 | SEER 16바이트 헤더, JSON body, 요청/응답 번호 검증 |
| STATE 19204 | 위치, 배터리, 작업, 차단, 현재 맵, IO, emergency 조회 |
| CONTROL 19205 | 정지, 속도 주행, 재위치, 맵 변경 |
| TASK 19206 | 랜드마크·좌표·상대 이동, 회전, 일시정지·재개·취소 |
| CONFIG 19207 | API 4011로 현재 `.smap` 지도 다운로드 |
| OTHER 19210 | DO, 모터, API 6004 software emergency 제어 |
| Adapter | 원본 `adaptor/main.py` 런타임 주입 |
| WebUI | SEER 지도·위치, 공통 Fleet Map, Live state, 주행 `/order`·Action `/instantActions` MQTT 발행 |
| VDA5050 검증 | WebUI/manual_test에서 order·instantActions·state·connection 원문 및 왕복 확인 |
| HCL 메뉴 | Actions·Recipes·Extensions와 안전한 Block Builder |

## 1. 자동 테스트

먼저 `README.md`의 설치 명령으로 `seer_client`를 editable 설치한다. 원본 Adapter의
기존 테스트는 예전 폴더명 `seer-client/src`를 하드코딩하므로 설치하지 않고 시험할
때만 현재 경로를 환경 변수로 지정한다.

```powershell
python -m unittest discover -s .\seer_client\tests -p "test_*.py" -v
python -m unittest discover -s .\seer_simulator\tests -p "test_*.py" -v
$env:PYTHONPATH = (Resolve-Path .\seer_client\src).Path
python -m unittest discover -s .\adaptor\tests -p "test_seer_client_*.py" -v
```

## 2. 시뮬레이션 상태와 IO

```powershell
python .\seer_client\manual_test.py `
  --simulator --x 1500 --y -500 --theta 90 --battery 55 `
  --command "status" `
  --command "do 3 on" `
  --command "io"
```

판정 기준:

1. `connected`가 `true`다.
2. 좌표가 `1500`, `-500`, `90`으로 표시된다.
3. 배터리가 약 `55%`다.
4. 마지막 IO 결과의 DO 3이 `true`다.
5. 종료할 때 `Disconnected.`가 표시된다.

DI는 장비로 들어오는 입력이고 DO는 장비가 내보내는 출력이다. `do 3 on`을 실행해도
DI 3은 자동으로 켜지지 않는다.

## 2-1. VDA5050 전체 왕복

Mosquitto와 `run_webui.py --simulator --id SEER-SIM-001 --mqtt-host 127.0.0.1
--mqtt-port 1883`을 먼저 실행한 뒤 별도 터미널에서 다음을 실행한다.

```powershell
python .\seer_client\manual_test.py `
  --vda5050 --id SEER-SIM-001 `
  --mqtt-host 127.0.0.1 --mqtt-port 1883 `
  --allow-write `
  --command "status" `
  --command "order test-1 LM1 LM5 LM4" `
  --command "goto LM4 LM1" `
  --command "watch 5"
```

manual_test는 주기 수신을 조용히 저장한다. `status`는 다음 state 한 건만 보여주고,
`last state`는 최신 저장값을 한 번, `watch 5`는 5초 동안만 실시간 RX를 보여준다.
Tab 한 번은 공통 접두사를 완성하고 Tab 두 번은 문맥에 맞는 명령·파라미터 후보를
보여준다. `emc`는 소프트웨어 비상정지 설정, `emc_release`는 명시적 해제다.
터미널의 `[VDA5050 MQTT TX/RX/LAST]`와 WebUI 상단 `VDA5050` 페이지에서
`order`, `instantActions`, `state`, `connection` 네 토픽을 모두 확인한다.
일반 WebUI 주행은 `WEBUI MQTT TX / order`, 즉시 Action은 `WEBUI MQTT TX / instantActions`로 표시되고 결과는 MQTT `state`에 표시된다.
실물 실행과 JSON 샘플은 `VDA5050_TEST.md`를 참고한다.

## 3. 대화형 이동 시험

```powershell
python .\seer_client\manual_test.py --simulator
```

```text
status
goto_xyz 2000 1000 45
watch 10 0.1
translate 500 100
watch 10 0.1
turn 45 30
watch 10 0.1
stop
quit
```

이동 후 좌표와 상태가 바뀌고 `stop` 후 `Stopped`가 표시되는지 확인한다.

## 4. 원본 Adapter 스모크 테스트

```powershell
python .\seer_client\run_adapter.py `
  --simulator --id SEER-SIM-001 `
  --x 1500 --y -500 --theta 90 --battery 55 `
  --vehicle-smoke-test
```

이 테스트는 원본 Adapter 로드, `SimulatedSEER` 주입, 로컬 TCP 5포트 연결과 상태
변환까지 확인하고 MQTT/EZI 초기화 전에 종료한다.

## 5. WebUI 시뮬레이션

`seer_client/webui_credentials.toml`을 한 번만 수정한다.

```toml
[webui]
username = "seer"
password = "현장에서-정한-12자이상-비밀번호"
```

```powershell
python .\seer_client\run_webui.py `
  --simulator --id SEER-SIM-001 `
  --x 1000 --y 2000 --theta 90 --battery 70
```

브라우저에서 `http://127.0.0.1:9010/`에 접속한다.

- 사용자 이름: `webui_credentials.toml`의 `username` 값
- 비밀번호: `webui_credentials.toml`의 `password` 값
- 서비스 Start/Stop/Restart: 독립 SEER Adapter 프로세스 제어
- Map: API 4011로 받은 `.smap`, 포인트 이름, 현재 AMR 위치/방향/current point;
  `+ / − / 맞춤`, 휠 확대와 드래그 이동을 확인한다. `화살표` 슬라이더를 35%와
  150%로 바꿨을 때 AMR 위치 마커만 작아지고 커지는지, 자동 갱신 뒤에도 선택 크기가
  유지되는지 확인한다. 1초 자동 갱신을 켠 채 2초 이상
  천천히 드래그해도 지도가 중간에 순간이동하지 않고, 손을 놓은 위치가 다음 갱신
  뒤에도 유지되는지 확인한다.
- RoboShop에서 크게 휘어진 `DegenerateBezier` 경로가 WebUI에서도 같은 방향과
  굴곡으로 보이는지 확인한다. 특히 S자 경로의 위·아래 굴곡이 평평해지지 않아야 한다.
- Live state: `connection=ONLINE`, `paused=RUNNING`, `blocked=CLEAR`인지 확인
- 자동 갱신: 처음부터 1초인지 확인한다. 값을 3초로 바꿔도 runtime
  `state_publish_delay = 1.0`은 유지되고, 0은 브라우저 갱신만 정지한다.
- IO: 상단 `Camera` 옆 `IO`를 눌러 시뮬레이터 DI0~DI23(24개), DO0~DO15(16개)가
  표시되는지 확인한다. DO3를 ON/OFF하고 색뿐 아니라 흰 손잡이가 OFF=왼쪽,
  ON=오른쪽으로 즉시 이동하는지 확인한다.
- Drive: 선속도 기본 `0.05 m/s`, 각속도 기본 `5 deg/s`; 누르는 동안에만 주행
- Path Nav: 기본 시뮬레이터에서 `SIM_START` 상태로 `SIM_GOAL`을 누르면
  `SIM_UPPER` 경로와 `SIM_LOWER` 경로 두 개, 각각의 총거리(m)가 표시되어야 한다.
  라디오 선택을 바꾸면 해당 지도 선이 노란색으로 바뀌어야 한다. `경로 겹침`을
  `노란선 위`/`노란선 아래`로 바꿨을 때 파란 기본선과 노란 선택선의 앞뒤 순서가 즉시
  바뀌고, 화면을 다시 열어도 선택이 유지되는지 확인한다. `포인트 간 대기=0초`에서
  선택한 전체 포인트가 API 3066 한 번의 `move_task_list`로 전송되어야 한다.
  `1 → 5 → 4`를 골랐다면 전송
  구간도 정확히 `1→5`, `5→4`여야 하며 API 3051을 구간별로 반복하면 안 된다.
  실행 창이 닫힌 뒤에도 선택 경로 전체가 주행 중 계속 노란색이어야 하고, 1초 자동 갱신과
  페이지 재진입으로 사라지면 안 된다. 목적지 도착·실패·취소 후에는 다음 갱신에서 노란
  실행 경로가 사라져야 한다. 같은 경로를 다시 시작하고 네트워크를 잠시 끊었을 때는
  노란 경로가 사라지지 않아야 한다. 연결 복구 후 계속 주행 중이면 같은 경로가 다시
  표시되고, 도착/중단 확인 뒤에만 사라져야 한다. 다음에는 `포인트 간 대기=0.5초`로
  실행해 API 3066이 직접 구간별로 전송되고 중간 포인트마다 약 0.5초 기다리는지 확인한다.
  지정 경로 후보가 하나 이상 있으면 `지정 경로 1`이 기본 선택되어야 하고, `Path Nav 자동`은
  목록의 맨 아래에 있어야 한다. 지정 경로 후보가 없을 때만 `Path Nav 자동`이 기본 선택된다.
  Path Nav 자동은 source_id를 비워 목표 id만 API 3051로 보내며, 먼저 `freeGo`를 보내면 안 된다.
  LM1-LM4 사이 경로 위에서 LM1을 선택했을 때도 SEER가 전진/후진 방향과 경로 진입을 계획하도록
  해야 한다. `current point`에는 가능하면 `RoboShop <current_station>`이 표시되고, 실제 pose가
  벗어나 있으면 `off ... m` 거리도 함께 보여야 한다. 포인트 클릭·선택·취소·실행 과정에서 주소가 바뀌거나 메인
  화면이 새로고침되지 않고, 보고 있던 지도 확대/이동 위치가 유지되어야 한다.
- Pause/Resume: 이동 중 Pause를 눌러 좌표가 멈추고 `paused=PAUSED`가 되는지 확인한다.
  Resume을 누르면 새 Path Nav를 보내지 않고 같은 작업의 남은 이동을 계속해야 한다.
  명령 순서는 TASK API `3001 → 3002`이고, 그 사이에 취소 API 3003이 없어야 한다.
- Emergency: 체크 없이 `비상정지`를 누르면 문구가 `비상정지 해제`로 바뀌면서 즉시
  초록색 활성 상태가 되고, 다시 누르면 `비상정지`와 빨간색 비활성 상태로 돌아온다.
  버튼 내부에는 동작 이름만 있고, 상세 설명은 버튼 위에 작게 표시되어야 한다.
  버튼 제출 직후 돌아온 화면에서 1초 갱신을 기다리지 않아도 상태가
  맞는지 확인한다. Path Nav 도중 활성화하면 `API 6004 ON → TASK_CANCEL` 순서로
  정지·취소되고, 다시 눌러 해제한 뒤에도 기존 Path Nav가 재개되지 않아야 한다.
- Actions: 원본 WebUI의 VDA5050 instantAction 경로와 SEER 긴급정지 확장.
  `Vehicle actions` 첫 줄에는 파랑 Request state, 주황 Pause, 초록 Resume만 있어야 하고
  Request factsheet는 다음 줄이어야 한다. 지정 포인트 이동, DO 펄스, Custom Action
  카드는 즉시 발행하지 않고 해당 Actions 입력·확인 폼 책갈피로 이동해야 한다.
- 버튼/알림: 화면 중간까지 스크롤한 상태에서 각 POST 버튼을 눌러도 위치가 유지되어야
  한다. `delivered` 등의 결과는 스크롤을 따라다니지 않고 오른쪽 아래 알림으로 잠시
  표시된 뒤 사라져야 한다.
- Tests/Logs: SEER 테스트 실행과 `seer_client/runtime/seer-adapter.log`

종료는 실행 터미널에서 `Ctrl+C`를 한 번 누른다. 정상 종료 시 다음 메시지가
표시되고 `http://127.0.0.1:9010/` 접속도 닫힌다.

```text
Stopping SEER WebUI ...
SEER adapter stopped (...)
SEER WebUI stopped.
```

`MQTT CONNECT FAILED`가 15초마다 보여도 Adapter가 종료된 것은 아니다. 이는 생성된
설정의 FMS broker(기본 예: `192.168.3.108:11883`)가 접근 불가능하다는 뜻이다.
WebUI의 `SEER live state · local IPC`, 지도와 IO가 계속 갱신되면 SEER TCP/로컬 제어는
정상이다. FMS 연동 시험에서는 접근 가능한 값을 다음처럼 지정한다.

```powershell
python .\seer_client\run_webui.py --simulator --id SEER-SIM-001 `
  --mqtt-host 127.0.0.1 --mqtt-port 1883
```

해당 주소에는 실제 MQTT broker가 실행 중이어야 한다. SEER 통신만 분리해서 확인하려면
4번 스모크 테스트를 사용한다.

## 6. 하나의 WebUI에서 여러 시뮬레이터 시험

```powershell
Copy-Item .\seer_client\seer-fleet.toml.example .\seer_client\seer-fleet.toml
python .\seer_client\run_webui.py `
  --fleet .\seer_client\seer-fleet.toml
```

기본 예제에는 다음 두 로봇이 들어 있다.

| AMR | 시작 좌표 | 배터리 |
|---|---:|---:|
| `SEER-SIM-001` | `(1000, 2000, 90)` | `70%` |
| `SEER-SIM-002` | `(-500, 750, 0)` | `55%` |

판정 기준:

1. `http://127.0.0.1:9010/`의 목록에 두 AMR이 모두 표시된다.
2. 각 AMR 이름을 눌렀을 때 서로 다른 좌표와 배터리가 표시된다.
3. 한 AMR의 Stop/Start를 눌러도 다른 AMR은 계속 `active` 상태다.
4. 각 AMR의 `Request state`가 해당 로봇에만 전달된다.
5. Logs 화면은 `runtime/<AMR-ID>/seer-adapter.log`를 각각 보여준다.
6. 터미널에서 `Ctrl+C`를 한 번 누르면 두 Adapter와 WebUI가 모두 종료된다.

### Fleet Map 확인

1. fleet WebUI 상단의 `Fleet Map`을 누른다.
2. 같은 `map_id`를 사용하는 AMR 두 대 이상의 색상 화살표와 ID가 한 지도에 표시되는지
   확인한다.
3. AMR 선택 카드를 눌러 제어 대상을 바꾼다.
4. 지도 포인트를 누르고 Path Nav를 실행한다.
5. 선택한 AMR만 움직이고 다른 AMR은 그대로인지 확인한다.
6. 다른 `map_id`인 AMR은 지도에 잘못 겹치지 않고 `다른 지도`로 표시되는지 확인한다.

### Actions·Recipes·Extensions와 Block Builder 확인

1. `Extensions`를 열어 runtime `extensions.hcl`이 보이고 다음 Action이 있는지 확인한다:
   `seerPathNav`, `seerCoordinateNav`, `seerTranslate`, `seerTurn`, `seerSetDO`, `seerWait`.
2. `Recipes`를 열어 기본 `seerNavigateToPoint`, `seerPulseDO`가 보이는지 확인한다.
3. `Block Builder`의 `이동 / 흐름 / IO / 판단 / 계산 / 자료 / 함수 / 장비` 카테고리, `블록 꾸러미`,
   `블록 조립소`가 구분되는지 확인한다. 조립소 안에는 `전체 실행 흐름`과 `함수 정의` 두 영역이
   따로 있어야 하고 `둘 다 보기 / 전체 흐름만 / 함수만` 버튼이 정확히 전환되어야 한다. 최상위
   블록 두 개를 서로 떨어진 위치로 드래그해 자유 배치한 다음, 한 블록을 다른 블록의 바로 아래로
   가져가 자석 강조와 연결선이 나타나며 두 블록이 붙는지 확인한다. 연결된 위 블록을 옮기면 아래
   블록도 함께 이동해야 하고 저장·편집했을 때 위치와 연결 상태가 복원되어야 한다. 특히 `변수 정하기`처럼
   한 줄 문장이 폭에 따라 늘어나는 블록을 위에 연결한 뒤 Recipe를 다시 불러와도, 아래 블록이 저장 전처럼
   바로 붙어 있어야 하며 중간에 빈 세로 간격이 생기지 않아야 한다. 드래그 중에는
   블록이 흐리거나 일부만 보이지 않고 펼친 설정과 내부 블록까지 포함한 전체 블록이 선명해야 한다.
   특히 연결 묶음의 맨 위 블록을 끌 때는 연결된 모든 최상위 블록이 하나의 미리보기로 함께 움직여야 한다.
   실행 흐름 블록에는 `1, 2, ...`, 함수 정의에는 `ƒ`가 표시되어야 한다. 연결 순서로 번호가 자동
   변경되고 번호 원의 목록에서 새 실행 번호를 선택하거나 `↑ / ↓`를 눌러 번호 순서를 변경할 수
   있어야 한다. 함수 정의 블록끼리는 자동 연결되지 않아야 한다. 브라우저 폭을 약 700px로 줄여도 가로 스크롤이나
   오른쪽 잘림이 없어야 한다. 팔레트 블록은 한 줄 높이, 조립소 블록은 기본 접힘 상태여야
   하며 제목 또는 `⚙`을 눌렀을 때만 상세 입력이 펼쳐져야 한다. 블록을 끌어 순서를 바꾸고
   조립소를 60~200% 확대·축소한 뒤 다음 순서로 블록을 만든다.
   - Path Nav: `지도에서 지정 경로 선택`을 열고 현재 포인트에서 목적지까지 후보를 확인한다.
     작은 맵의 노란 강조선, 목록의 포인트 순서와 총거리(m)가 서로 맞는 후보를 고른 뒤
      `선택 경로 적용`을 누른다. 경로가 둘 이상이면 다른 목록을 눌러 강조선도 바뀌는지
      확인한다. 적용 뒤 접힌 Path Nav 블록의 작은 요약에는 중간 경로를 생략하고 실제 출발지와
      최종 목적지만(예: `LM1 → LM4`) 표시되어야 한다.
   - 작은 맵에서 `＋`/`−`와 마우스 휠로 50–600% 확대·축소하고, 확대한 뒤 드래그해도
     맵이 튀지 않는지 확인한다. 포인트 클릭은 목적지만 바꾸고, 드래그 직후에는 포인트가
     잘못 선택되지 않아야 한다. `맞춤`은 100%와 원래 중심을 복원해야 한다.
   - 화면 위 `매뉴얼 열기`를 눌러 작은 전용창이 표시되는지 확인한다. 왼쪽 목차 1~8과 각 장의
     블록 그림, 상세 설명, Path Nav 지도 그림, 실물 장비 확인 사항이 보여야 한다. 목차를 누르면
     해당 장으로 이동하고 배경, `✕`, `Esc`로 닫혀야 하며 배경 화면 스크롤 잠금도 해제되어야 한다.
   - `예제 블록코딩`을 펼쳐 네 가지 예제가 보이는지 확인한다. 각 `예제로 불러오기`를 눌렀을 때
     실제 블록과 중첩 구조가 조립소에 만들어지고 Recipe 이름도 바뀌어야 한다. 기존 블록이 있으면
     교체 확인창이 나타나야 하며, 불러오기만으로 Recipe 저장·Actions 실행이 일어나면 안 된다.
   - `시뮬레이터 지정 경로 왕복`은 Path Nav→대기→회전→Path Nav가 연결되어야 한다.
     `함수 정의 후 반복 실행`은 함수 정의 영역의 `DemoMotion`과 전체 흐름의 반복/함수 호출이
     분리되어 보여야 한다.
   - 직선 거리 이동: `0.1 m`, `0.05 m/s`
   - 제자리 회전: `90 deg`, `5 deg/s`
   - 대기: 입력을 비워 기본 `1초`
4. 거리와 회전각을 `변수로 사용`으로 바꾸고 각각 `distance`, `angle`로 저장한다.
   첫 번째 블록의 `이 블록 완료 후 추가 대기`는 `0.2초`, Path Nav 블록은 `0초`로
   설정한다. Path Nav가 목적지에 도착한 뒤 다음 블록이 불필요하게 기다리지 않아야 한다.
5. `Recipe 저장 및 적용`을 누르고 성공 메시지와 Adapter 재시작을 확인한다.
6. `Actions`에서 생성한 Recipe를 찾는다. `distance`와 `angle` 입력을 비우고 실행하면
   블록 기본값이 사용되는지 확인한다.
7. 다시 실행해 `distance=0.2`, `angle=-45`를 입력하면 전달한 변수값으로 실행되는지
   Logs와 시뮬레이터 pose로 확인한다.
8. `seer_client/runtime/recipes.hcl`에 생성 Recipe가
   `SEER_BLOCK_RECIPE_BEGIN/END` 사이에 저장됐는지 확인한다.
9. `Block Builder 관리 Recipe`의 `편집`을 눌러 기존 블록과 변수명이 복원되는지 확인하고,
   블록 값, `Recipe 이름`, `화면 표시 이름`을 바꿔 `Recipe 변경 저장 및 적용` 후 Actions에
   새 이름으로 반영되고 이전 이름은 사라지는지 확인한다. 이미 존재하는 Recipe 이름으로
   바꾸면 충돌 오류가 표시되고 기존 파일이 그대로 유지되어야 한다.
10. 해당 항목의 `삭제`를 누르고 확인 화면에서 `삭제 확인`을 선택한다. Actions 목록에서
    사라지고 수동 작성 Recipe는 그대로 남는지 확인한다.
11. Path Nav의 Task ID 접두어를 `1`로 둔 Recipe를 안전한 왕복 경로로 두 번 실행하고,
    Logs의 실제 API 3066 구간 `task_id`가 실행마다 다른지 확인한다. `delivered`는
    Adapter 전달 확인일 뿐이므로 최종 포인트와 pose까지 확인한다.
12. 맵에서 직접 연결되지 않은 두 포인트를 수동 HCL에 넣은 경우
    `has no direct map connection`으로 실패하고 장비가 움직이지 않는지 확인한다.
13. 같은 Recipe를 `완료 후 추가 대기=0초`와 `0.5초`로 각각 실행해 Logs의 다음 블록
    시작시각 차이가 설정값만큼 나는지 확인한다. `0초`에서는 이전 동작의 완료 확인 직후
    다음 블록이 시작되어야 한다. 마지막 블록에 값을 넣으면 Recipe 완료 보고가 그만큼
    늦어지므로 보통 `0초`를 사용한다.
14. `반복하기` 안에 `신호 보내기 ON → 대기 0.1초 → 신호 보내기 OFF`를 넣고 반복 횟수 2로
    저장·실행한다. IO와 Logs에서 정확히 두 번 실행되는지 확인한다.
15. `만일 ~이라면 / 아니면`에 `DI0 == 1`, `배터리 >= 50`, `현재 포인트 == LM1`
    중 하나를 설정하고 참/거짓 영역에 서로 다른 신호 보내기를 넣는다. 시뮬레이터 상태를
    바꿔 양쪽 분기가 실제로 실행되는지 확인한다. 반복 횟수·비교값을 변수로 만든 뒤
    Actions 입력을 비웠을 때 저장된 기본값이 사용되는지도 확인한다.
16. `DI 신호를 받으면`을 추가하고 `DI2 rising`, 제한시간 `30초`, 확인주기 `0.1초`로
    설정한다. 내부 점선 영역에 `DO3 ON → Path Nav`를 넣는다. DI2가 계속 OFF인 동안에는
    실행되지 않고, 시뮬레이터 또는 실제 설비에서 DI2가 OFF→ON으로 바뀐 뒤에만 내부
    블록이 실행되어야 한다. `falling`은 ON→OFF에서만 실행되는지 반대로 확인한다.
17. `DI 신호 기다리기` 뒤에 `DO 펄스`를 배치한다. 신호 뒤 설정한 DO가 ON되고 지정한
    유지시간 후 OFF로 돌아오는지 IO와 Logs에서 확인한다. 제한시간 안에 신호가 없으면
    Recipe가 실패하고 뒤 블록은 실행되지 않아야 한다.
18. `변수 정하기 retry_count=0 → 변수 더하기 +1 → 만일 variable retry_count == 1`을
    만들고 참 영역에 로그 또는 DO를 넣는다. 실행 중 변수값으로 분기되는지 확인한다.
    `조건까지 반복`은 최대 반복 횟수를 넘으면 실패하고, `현재 반복 중단`은 가장 가까운
    반복만 끝내며, `이 코드 멈추기` 뒤 블록은 실행하지 않고 정상 완료해야 한다.
19. 시험용 지도와 안전한 정지 상태에서 `지도 전환`, `위치 초기화`, `모터 전원`을 각각
    확인한다. 실제 장비에서는 Roboshop의 지도 이름과 정확한 `motor_names`를 사용한다.
    비상정지 중 모터 ON은 거부되고, 모터 OFF는 가능해야 한다.
20. `신호 보내기`의 DO가 `DO0~DO15`, DI 관련 블록이 `DI0~DI23` 목록 상자로 표시되는지
    확인한다. `신호 보내고 기다리기`를 `DO2 ON → DI3 rising → DO2 OFF`로 설정하고 DI3를
    OFF→ON으로 바꿔야 다음 블록이 실행되는지 확인한다. timeout 때도 DO2가 OFF로 복구되어야 한다.
21. `계산하기 total = $counter add 1 → 두 값 판단하기 ready = $total >= 1 → 만일 variable
    ready == 1` 순서로 만들고 참 영역의 로그/DO가 실행되는지 확인한다. 선언한 `counter`,
    `total`, `ready`가 변수 목록에, `$counter`가 계산 입력의 목록에 나타나야 한다.
22. `계속 반복하기` 안에서 카운터를 증가시키고 1회차는 `이번 반복 건너뛰기`, 2회차는
    `현재 반복 중단`을 실행한다. 카운터가 정확히 2인지 확인한다. 중단 없는 계속 반복은
    안전 상한 1000 실행에서 실패해야 하며 WebUI Cancel로 즉시 취소할 수 있어야 한다.
23. `함수 정의` 영역에 `함수 정의하기 Demo`를 만들고 내부에 DO/대기 블록을 넣는다. 왼쪽
    함수 꾸러미에 `Demo 함수 실행` 블록이 자동 생성되어야 한다. `전체 실행 흐름` 영역에
    `반복하기 2회`를 놓고 그 내부 점선 영역에 `Demo 함수 실행`을 넣는다. 실행하면 Demo 내용이
    정확히 두 번 호출되어야 하며 정의 내용 자체는 시작할 때 자동 실행되지 않아야 한다. 저장 후
    편집 화면에서도 함수 영역, Demo 실행 블록과 연결이 복원되어야 한다. 정의되지 않은 함수와
    재귀 호출은 실패해야 한다.
24. 실행 변수 `runs=0`에서 `변수 더하기 runs +1 → 만일 runs < 2이면 처음부터 다시 실행하기`를
    구성한다. 두 번째 실행에서 다음 블록으로 진행하고 Logs에 1회 restart가 기록되는지 확인한다.

좌표 이동과 Path Nav는 SEER API 3051/3066에 속도 인자가 없으므로 Roboshop 프로파일을
사용한다. 실제 TCP 요청의 선속도·각속도 검증은 API 3055 직선 이동과 API 3056 회전
블록으로 수행한다.

실제 장비를 섞어 시험하려면 해당 블록을 다음처럼 바꾸고 먼저 조회 전용 연결 시험을
통과시킨다.

```toml
[[robot]]
id = "SEER-REAL-001"
simulator = false
vehicle_ip = "192.168.192.5"
```

## 7. 실제 장비 조회 전용 시험

```powershell
Test-NetConnection 192.168.192.5 -Port 19204
Test-NetConnection 192.168.192.5 -Port 19205
Test-NetConnection 192.168.192.5 -Port 19206
Test-NetConnection 192.168.192.5 -Port 19207
Test-NetConnection 192.168.192.5 -Port 19210

python .\seer_client\manual_test.py --ip 192.168.192.5
```

먼저 다음 읽기 명령만 사용한다.

```text
status
loc
battery
task
blocked
emergency
map
io
di 0
quit
```

실물 모드에서 `--allow-write`를 주지 않으면 이동과 DO 변경은 코드에서 차단된다.

## 8. 실제 장비 쓰기 시험

안전구역, 비상정지와 배선표를 확인한 경우에만:

```powershell
python .\seer_client\manual_test.py `
  --ip 192.168.192.5 --allow-write
```

```text
io
do 3 on
io
do 3 off
goto SAFE_TEST_POINT
watch 30 0.5
stop
quit
```

`3`과 `SAFE_TEST_POINT`는 예시다. 실제 안전한 IO 번호와 등록 랜드마크를 사용한다.

SEER 소프트웨어 비상정지 배선/운영 규칙을 확인한 뒤에만 다음 명령을 시험한다.

```text
emergency
emc
emergency
emc_release
emergency
```

`toggle_emergency`는 API 1012로 현재 상태를 다시 읽고 `soft_emc`의 반대값을 API
6004로 설정한다. 물리 비상정지나 드라이버 비상정지는 이 명령으로 해제하지 못한다.
`emc`/`emc_release`는 토글이 아니라 각각 API 6004의 true/false 상태를 명시한다.

## 9. 실제 장비 WebUI

```powershell
python .\seer_client\run_webui.py `
  --id SEER-REAL-001 `
  --vehicle-ip 192.168.192.5 `
  --host 127.0.0.1 `
  --port 9010 `
  --mqtt-host 127.0.0.1 `
  --mqtt-port 1883
```

위 예시는 같은 PC의 `127.0.0.1:1883`을 사용하는 경우다. 포트가 닫혀 있으면
`run_webui.py`가 설치된 Mosquitto를 찾아 자동으로 백그라운드 실행하며, 이미 broker가
실행 중이면 그대로 재사용한다. WebUI 종료 시 Mosquitto는 유지된다. 자동 실행 Mosquitto의
로그는 프로젝트 밖 `%LOCALAPPDATA%\SEER Client\logs\mosquitto-autostart.log`에 기록되고,
프로세스 작업 디렉터리도 Mosquitto 설치 폴더를 사용하므로 broker가 살아 있어도 프로젝트 폴더
교체를 막지 않는다. 자동 실행을 끄려면 `--no-auto-start-mqtt`를 사용한다. broker가 다른 PC에
있으면 Adapter PC에서 접속 가능한
주소로 `--mqtt-host`를 바꾼다. 원격 broker는 자동 실행 대상이 아니다.

실물 연결은 이 코드 작성 환경에서 수행하지 않았다. 조회 전용 시험을 먼저 통과한 뒤
WebUI 이동 명령을 사용한다.

확인 순서:

1. Map에 실제 `.smap`과 AMR 마커가 나타나는지 확인하고 `화살표` 슬라이더로 현장에
   맞는 마커 크기를 정한다. 등록 포인트를 누른 뒤 목적지 이름과 후보 경로별 포인트
   순서·거리·지도 강조선이 맞는지 확인한다. 먼저 취소 시험을 한 뒤 안전한 지정 경로를
   골라 API 3066 한 번으로 실행한다. 같은 창에서 Free Nav도 선택할 수 있는지 확인하고,
   AMR이 이름 있는 포인트 밖에 있거나 경로가 없을 때는 API 3051 `freeGo`를 사용한다.
   두 과정 모두 메인 화면과 브라우저 주소는 그대로여야 한다.
2. Live state가 `ONLINE / RUNNING / CLEAR`인지 확인한다. 정지 작업이면 PAUSED,
   장애물 또는 주행 차단이면 BLOCKED가 표시되어야 한다.
3. `FMS MQTT=CONNECTED`와 prefix `amr/v3/SEER-REAL-001`을 확인한다. FMS 주문을
   보냈을 때 WebUI의 `order`, `nodes/edges/actions`와 Logs가 바뀌어야 실제 명령 경로까지
   검증된 것이다.
4. 수동조작의 `수동조작 활성화` 체크가 기본 OFF인지 확인한다. 사용자가 체크하기 전에는
   방향키/방향 버튼이 주행 명령을 보내지 않아야 한다. 체크 후 같은 화면의 카드 자동 갱신에서는
   체크가 유지되어야 하지만, 30초 동안 방향키·버튼·속도 입력이 없거나 다른 화면/탭으로 이동하면
   자동으로 OFF되어야 한다. 기본 속도값은 `0.05 m/s`, `5 deg/s`다. 각 속도 입력칸의 숫자를 전부 지울 수 있어야 하고,
   빈 상태에서 이전 숫자가 자동으로 되살아나면 안 된다. 새 숫자를 입력한 뒤 화면을 이동했다가
   돌아와도 새 값이 유지되는지 확인한다. 그 다음 `↑+←`, `↑+→`, `↓+←`, `↓+→`를 각각 눌러
   선속도와 각속도가 동시에 적용되는지, 한 키만 먼저 놓았을 때 나머지 방향 입력이 계속되는지 확인한다.
5. 안전구역에서 버튼을 짧게 눌렀다가 놓았을 때 즉시 정지하고, 키를 길게 누르는 동안
   API 2010 속도가 중간중간 0으로 떨어지는 끊김이 없는지 확인한다.
6. 안전한 Path Nav 작업 중 Pause를 눌러 SEER TASK API 3001과 정지를 확인한 다음,
   Resume으로 API 3002가 전송되고 같은 작업이 이어지는지 확인한다. 이 과정에 API 3003
   또는 새 API 3051/3066 이동 명령이 나타나면 정상 동작이 아니다.
7. 현장 절차가 허용하는 경우에만 소프트웨어 비상정지 설정/해제를 시험한다. 버튼을
   누른 전후로 브라우저 주소, 현재 화면과 스크롤 위치가 유지되어야 한다.
8. 상단 IO에서 실제 API 1013 채널 수와 배선표가 일치하는지 확인한다. DO 전환은 실제
   장비 출력이므로 안전 담당자가 승인한 채널만 ON/OFF한다.
9. Logs에 `SEER POLL ERROR ... response sequence`가 발생하더라도 다음 요청 전에 해당
   STATE 포트를 자동 재연결한다. 같은 오류가 계속 반복되면 실행 중인 구버전 프로세스를
   종료하고 새 `seer_client`로 WebUI를 재시작한다.

## 10. `last_node_id_missing` 진단

이 메시지는 AMR 좌표를 모른다는 뜻이 아니라 VDA5050 `lastNodeId` 후보가 없다는
뜻이다. 지도와 x/y/theta가 WebUI에 정상 표시되어도 다음 경우에는 발생할 수 있다.

- 현재 AMR이 `.smap`의 이름 있는 `advancedPointList` 포인트 사이에 있다.
- 로봇이 움직이는 중이거나 노드 도달 반경 밖에 있다.
- 현재 맵에 이름 있는 포인트가 없거나 맵/좌표 단위가 맞지 않는다.

AMR을 이름 있는 포인트 근처에 정지시킨 뒤 상태를 다시 확인한다. 계속되면 다운로드된
`seer-map.json`의 `points`와 현재 x/y가 같은 미터 좌표계인지 확인한다.

## 11. 생성 파일과 원본 보존 확인

실행 중 생성되는 파일은 다음 위치에 한정된다.

```text
seer_client/runtime/config.toml
seer_client/runtime/robots.toml
seer_client/runtime/seer-adapter.log
seer_client/runtime/seer-map.json
seer_client/runtime/seer-io.json
seer_client/runtime/seer-active-route.json  # 지정 경로 실행 중에만 존재
seer_client/runtime/ipc/<AMR-ID>/state.json
seer_client/runtime/ipc/<AMR-ID>/health.json
seer_client/runtime/ipc/<AMR-ID>/io.json
seer_client/runtime/records/*.jsonl         # SEER_RECORD=1일 때만 생성
seer_client/runtime/<AMR-ID>/config.toml
seer_client/runtime/<AMR-ID>/seer-adapter.log
seer_client/runtime/<AMR-ID>/seer-map.json
seer_client/runtime/<AMR-ID>/seer-io.json
seer_client/runtime/<AMR-ID>/seer-active-route.json  # 지정 경로 실행 중에만 존재
```

제어 명령은 디스크 파일을 만들지 않고 프로세스별 `127.0.0.1` TCP 포트를 사용한다.
운영체제 임시 폴더와 원본 `adaptor` 폴더에는 SEER 상태·로그·IPC·`__pycache__`를
생성하지 않아야 한다. 시뮬레이터 상태 자체는 메모리에서 실행되며 필요한 Adapter 로그와
상태 파일은 동일하게 `seer_client/runtime` 아래에 기록된다.

원본 `adaptor/config/config.toml`, `adaptor/config/robots.toml`, `run-adapter.sh`,
`jibot-simulator`는 수정하지 않는다.

## 1주차 기반 기능 검증

### 1. 전체 회귀시험

```powershell
python -m unittest discover -s .\seer_client\tests -p "test_*.py" -v
python -m unittest discover -s .\seer_simulator\tests -p "test_*.py" -v
python .\seer_client\run_adapter.py --simulator --id SEER-SIM-001 --vehicle-smoke-test
```

판정 기준:

1. 기존 시험과 신규 1주차 시험이 모두 `OK`다.
2. smoke test의 종료코드가 0이고 처리되지 않은 예외가 없다.
3. 실패한 시험을 skip 처리하여 통과시키지 않는다.

### 2. Capability/Factsheet 시험

```powershell
python .\seer_client\print_capabilities.py --runtime-adapter --simulator
python -m unittest .\seer_client\tests\test_week1_capabilities.py -v
```

성공 기준:

- `startCharging`, `stopCharging`, `jibotCommand` 등 확인되지 않은 JIBOT/vendor 기능은
  실제 SEER Factsheet에 없어야 한다.
- `manualDrive`, `stateRequest`, `seerSetDO`처럼 실행 handler가 있는 기능은 표시되어야 한다.
- Simulator에서만 안전하게 지원하는 `setMap`, `setMapSnapshot`은 실물 구성에서는
  표시되지 않아야 한다.

Factsheet는 FMS가 주문을 만들 때 참고하는 기능 계약이다. 광고된 기능과 실제 handler가
다르면 주문 접수 후 실행 중 실패하므로 두 집합이 같아야 성공이다.

### 3. 시작 진단 시험

```powershell
python -m unittest .\seer_client\tests\test_week1_diagnostics.py -v
```

다음 값은 장비 TCP 연결 전에 실패해야 한다.

- 프로토콜 버전 1·2 이외
- 포트 범위 오류 또는 5개 포트 중복
- 0 이하 timeout/polling 간격
- 빈 모터 이름 또는 중복 이름
- 유효하지 않은 수동주행 제한

오류 설정에서 장비 연결을 시도하지 않아야 성공이다. 설정 실수를 런타임 통신 오류나
실물 오동작으로 확대하지 않기 위한 fail-fast 기준이다.

### 4. 포트 건강도와 오류 복구 시험

```powershell
python -m unittest .\seer_client\tests\test_week1_health_faults.py -v
```

시험은 TASK 포트 단절, `ret_code` 오류, 잘못된 sequence, 부분 body, timeout을
재현한다.

성공 기준:

- 전체 포트 정상: `ONLINE`
- TASK 한 포트만 단절: `DEGRADED`
- 복구 뒤 실제 요청 성공 후: `ONLINE`
- 잘못된 sequence/부분 body 뒤 다음 정상 요청의 payload가 정확함
- DO 쓰기 timeout 중 Simulator가 받은 API 6001 요청은 정확히 1회

마지막 기준이 1회인 이유는 timeout이 응답 유실일 수도 있기 때문이다. 같은 쓰기
명령을 자동으로 다시 보내면 장비에서는 DO나 이동이 두 번 실행될 수 있다.

### 5. 공통 기능 동등성 Harness

```powershell
python -m unittest .\seer_client\tests\test_week1_parity_harness.py -v
```

공통 Harness는 JIBOT 기준 결과와 SEER 결과를 다음 운영 관점으로 비교한다.

- terminal 상태
- 오류 의미
- 장치/출력 cleanup
- 최종 VDA 상태

API 번호가 서로 달라도 FMS가 받는 성공·실패·취소 의미가 같아야 기능 동등성으로
판정한다.

### 6. 이동 lifecycle와 수동주행 Watchdog

```powershell
python -m unittest discover -s .\seer_client\tests -p "test_week1_navigation_lifecycle.py" -v
```

성공 기준:

- API 3051/3066 응답 직후 task가 `Driving`이면 Action은 아직 완료되지 않는다.
- `task_status=4`와 목표 station/pose 확인 후에만 완료된다.
- 없는 station은 현재 위치에서 성공하지 않고 API 오류가 난다.
- timeout 시 API 3051/3066은 재전송되지 않고 API 3003 취소가 정확히 1회다.
- API 2010 입력 갱신이 끊기면 `duration` 이후 속도와 task 상태가 정지로 돌아간다.
- 후진과 시계방향 회전은 실제 목표 pose까지 도달한 뒤 완료된다.

이 결과가 필요한 이유는 명령 수락과 물리 완료가 다른 사건이기 때문이다. 수락 직후
`FINISHED`를 보내면 FMS가 로봇이 이동 중인데 다음 작업을 시작할 수 있다.

## WebUI 지도 Path Nav 자동 회귀 확인

지도에서 포인트를 클릭하고 `Path Nav 자동`을 선택하면 지정 경로용 `route_points` 빈 값이 POST에 포함되면 안 됩니다. 자동 모드는 `navigation_mode=path`, 목표 `id`만 전달하고 `route_points`는 폼에서 비활성화되어 공통 WebUI JSON 파서가 빈 문자열을 거절하지 않도록 합니다. 지정 경로를 선택한 경우에만 `route_points`가 유효한 JSON 배열로 활성화됩니다. 지도 팝업은 POST 리다이렉트의 `err` 메시지도 확인하여 서버가 요청을 거절했는데 성공으로 표시하지 않아야 합니다.
