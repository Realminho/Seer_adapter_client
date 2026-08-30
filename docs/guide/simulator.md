# JIBOT 시뮬레이터 사용 가이드

실제 JIBOT AMR 장비 없이 adaptor를 돌려보기 위한 **in-process 시뮬레이터** 사용법.
주문 처리, 상태 publish, instant action 흐름을 물리 로봇 없이 검증할 때 쓴다.

구현: [`jibot-simulator/cls_jibot_simulator.py`](../../jibot-simulator/cls_jibot_simulator.py)
(`SimulatedJIBOT`은 `JIBOT` 클라이언트를 상속해 adaptor가 호출하는 부분만 흉내낸다).

---

## 1. 시뮬레이터가 대체하는 것 / 대체하지 않는 것

`--simulator`는 **AMR vehicle(TCP socket) 부분만** 가짜로 바꾼다. 나머지는 그대로
실제 연결을 시도한다.

| 구성요소 | `--simulator` 사용 시 |
| --- | --- |
| JIBOT AMR (TCP 7273) | ✅ **가짜.** 소켓을 열지 않고 in-process로 위치/배터리/상태를 시뮬레이션 |
| MQTT broker | ❌ **실제 연결.** `config.toml`의 `[mqtt_broker] host:port`로 접속 |
| EZI IO 모듈 | ❌ **실제 연결.** `[ezi] ezi_io` IP로 접속 시도 |
| EZI 모터 드라이버 | ❌ **실제 연결.** `[ezi] ezi_motor` IP로 접속 시도 |

> ⚠️ 그래서 개발 PC에서 `main.py --simulator`만 실행하면 MQTT broker가 없거나
> EZI 하드웨어(10.8.8.x)에 닿지 못해 멈추거나 실패할 수 있다. MQTT/EZI 없이
> vehicle 명령 경로만 빠르게 보려면 아래 **smoke test / console** 모드를 쓴다.

---

## 2. 기본 실행

```bash
# 로컬 시뮬레이터로 adaptor 전체 기동 (MQTT broker + EZI 연결 필요)
./run-main.sh --simulator
python3 main.py --simulator

# config의 AMR IP/PORT 대신 실행 시점에 지정 (시뮬레이터에선 라벨 용도)
./run-main.sh --simulator --vehicle-ip 127.0.0.1 --vehicle-port 7273
```

시뮬레이터에서 `--vehicle-ip`/`--vehicle-port`는 실제 접속에 쓰이지 않고 로그
라벨로만 표시된다. MQTT broker 주소는 `config/config.toml`(또는 `--mqtt-host`/
`--mqtt-port`, `--config`)로 정한다.

---

## 3. MQTT/EZI 없이 vehicle 경로만 보기

### smoke test — 명령 몇 개 보내고 바로 종료

MQTT/EZI 셋업 **전에** 종료하므로 broker도 EZI 하드웨어도 필요 없다. CI나 개발
PC에서 가장 가볍게 동작 확인하는 방법.

```bash
python3 main.py --simulator --vehicle-smoke-test
# 송수신 프레임까지 기록하려면
JIBOT_RECORD=1 python3 main.py --simulator --vehicle-smoke-test
```

### console — 명령을 직접 골라 응답 확인

터미널에서 JIBOT 명령을 번호/이름으로 선택하고 파라미터를 입력해 응답을 본다.
이 역시 MQTT/EZI 셋업 전에 동작한다.

```bash
python3 main.py --simulator --vehicle-console
```

`list`로 명령 목록, `state`로 현재 차량 스냅샷, `quit`로 종료.

---

## 4. 시뮬레이터의 동작 모델

`SimulatedJIBOT`은 다음을 흉내낸다 (`cls_jibot_simulator.py` 참고):

- **초기 상태**: `mode=auto`, `status=Stopped`, `battery=30~100%` 랜덤,
  `localization_score=1.0`. 저장된 맵을 로드하면 로드된 node 중 하나에 랜덤
  배치되고, 저장된 맵이 없으면 위치 `x=y=th=0`, `station="SIM"`에서 시작한다.
  시작 배터리는 `--battery <%>`로 지정할 수 있고(0-100 범위로 clamp), 주지 않으면
  위처럼 랜덤이다. `--charging`을 주면 도킹된 채 **충전 중 상태로 시작**한다
  (`status=charging`, 배터리 상승). 주행/정지 시 충전이 멈춘다.
- **이동(goto pose/node)**: `config.toml`의 `[settings] speed`(m/s)를 VDA pose 단위인
  mm/s로 환산해 목표 좌표까지 0.1초 간격으로 직선 이동한다. 목표 30mm 이내면
  target 좌표로 snap하고 `Stopped`로 전환. node 이동 명령 1회마다 배터리는 1%씩
  감소한다.
  - 실제 맵/로컬라이제이션이 없으므로 **VDA5050 order의 `nodePosition` 좌표**를
    그대로 따라간다. node id는 `station`에 기록돼 상태 보고에 반영된다.
- **route 호출 / 단거리 node 이동**: 약 1초 후 `Stopped`로 전환(좌표 변화 없음).
- **배터리**: `Driving` 상태에서만 아주 천천히 감소(거의 일정).
- **충전(UmDock, startCharging instant action)**: `status=charging`,
  `powerSupply.charging=true`로 보고.
- **위치 초기화(UmLocalize, initPosition instant action)**: 지정한 pose를 즉시 적용.
- **RX watchdog**: in-process라 수신 프레임이 없어 워치독이 적용되지 않는다
  (`is_rx_stale()`가 항상 `False`).

### 지원하는 명령

`UmConnect`, `UmStop`, `UmSetMotor`, `UmGetRobotInfo`, `UmGetMotorState`,
`UmGetLocState`, `UmGoto`(pose/node), `UmDock`, `UmLocalize`.
그 외 명령은 무시되거나 응답이 없을 수 있다.

---

## 5. 시뮬레이터 여러 대 동시 실행

`run_multi.py`로 시뮬레이터 로봇을 여러 대 한 번에 띄울 수 있다. 각 로봇은
별도 `main.py` 프로세스로 뜨고, 고유한 `id`(serial_number)로 MQTT 토픽
(`amr/v3/<id>`)이 갈라지므로 한 broker에 충돌 없이 붙는다.

```bash
# (1) 로봇 목록 준비
cp config/robots.hcl.example config/robots.hcl   # id를 SIM-001/SIM-002 식으로

# (2-a) robots.hcl에 simulator=true를 적었으면 그대로
./run-multi.sh

# (2-b) 또는 전체를 강제로 시뮬레이터 모드로
./run-multi.sh --simulator
python3 run_multi.py --robots config/robots.hcl --simulator

# systemd/run-adapter 진입점에서 실차 목록과 별도의 simulator 목록 사용
./run-adapter.sh --simulator --simulator-robots config/robots.simulator.hcl
```

### 1. 1번 로봇 한 대만 실행

저장소에는 1번 로봇 `HN-SH6-TR-001`만 정의한
[`config/robots-hana.hcl`](../../adaptor/config/robots-hana.hcl)이 있다.
기본 `config/robots.hcl`을 변경하지 않고 이 로봇 한 대만 시뮬레이터로
실행하려면 `adaptor` 디렉터리에서 다음 명령을 사용한다.

```bash
./run-adapter.sh \
  --simulator \
  --simulator-robots config/robots-hana.hcl
```

이 명령은 `robots-hana.hcl`의 로봇이 한 대이므로 내부적으로 다음과 같이
단일 `run-main.sh` 실행으로 dispatch된다.

```bash
./run-main.sh \
  --robots config/robots-hana.hcl \
  --robot HN-SH6-TR-001 \
  --simulator
```

`--simulator-robots`는 simulator 전용 옵션이다. `--simulator` 없이 사용하면
실행되지 않는다.

### 2. 기본 실행에서도 설정 파일 명시

`run-main.sh`로 기본 실행할 때도 어떤 robots HCL과 로봇을 사용할지 명시한다.
1번 로봇 설정을 사용하는 명령은 다음과 같다.

```bash
./run-main.sh \
  --robots config/robots-hana.hcl \
  --robot HN-SH6-TR-001
```

`config/robots-hana.hcl`의 해당 로봇에는 `simulator = true`가 선언되어 있으므로
이 명령은 별도의 `--simulator` 인자가 없어도 시뮬레이터로 실행된다. CLI에서
실행 모드를 분명하게 보이려면 다음처럼 `--simulator`를 함께 적어도 된다.

```bash
./run-main.sh \
  --robots config/robots-hana.hcl \
  --robot HN-SH6-TR-001 \
  --simulator
```

설정 파일의 상대 경로는 명령을 실행하는 `adaptor` 디렉터리를 기준으로 작성한다.

`robots.hcl` 예시(전 대수 시뮬레이터):

```hcl
robot "SIM-001" {
  vehicle_ip   = "127.0.0.1"
  vehicle_port = 7273
  simulator    = true
}

robot "SIM-002" {
  vehicle_ip   = "127.0.0.1"
  vehicle_port = 7274
  simulator    = true
}
```

- 로그는 `[SIM-001] ...` 처럼 로봇 id 접두사로 구분된다.
- `Ctrl+C` 한 번이면 모든 시뮬레이터 프로세스를 graceful shutdown.
- `id`가 비었거나 중복이면 실행 전에 막는다(토픽 충돌 방지).
- 시뮬레이터 다대수도 MQTT broker에는 실제로 붙으므로 broker는 떠 있어야 한다.
  여러 대가 같은 EZI 하드웨어 IP를 공유하면 충돌하므로, 순수 검증은
  `extra_args = ["--vehicle-smoke-test"]`로 EZI/MQTT를 건너뛰게 할 수도 있다.

`run_multi.py`는 내부적으로 로봇마다 `main.py --robot <id>`를 띄운다. 한 대만
fleet 설정으로 직접 띄우려면:

```bash
python3 main.py --robot SIM-001                      # config/robots.hcl에서 SIM-001 읽음
python3 main.py --robots config/robots.hcl --robot SIM-002
```

자세한 다중 실행 옵션은 [`config/robots.hcl.example`](../../adaptor/config/robots.hcl.example) 참고.

### systemd + TUI 대시보드 연동

`config/robots.hcl`이 없거나 `robot` 블록이 1개면 기존 단일
`amr-adaptor.service`로 동작한다. `robot` 블록이 2개 이상이면
`setup-adaptor-service.sh`가 로봇마다 `amr-adaptor@<id>.service` 인스턴스를
등록하고, **TUI/WebUi 대시보드 목록에 로봇이 전부 따로 표시된다**
(각자 systemd 유닛 + `amr/v3/<id>` 토픽으로 상태/제어).

```bash
cp config/robots.hcl.example config/robots.hcl      # 로봇 목록 작성
../scripts/setup-adaptor-service.sh --jibot
sudo systemctl start amr-adaptor.service            # 0/1대 기본
sudo systemctl start amr-adaptor@SIM-001.service    # 2대 이상 개별 기동 (또는 WebUi에서)
run-web.sh                                             # 대시보드에 전 로봇 표시
```

자세한 내용: [web-ui.md](web-ui.md)

---

## 6. 한계

- 실제 맵·로컬라이제이션이 없다. 위치는 order의 `nodePosition` 좌표나 명시
  pose에만 의존하며, node id만 주는 주문은 1초 후 도착으로 단순 처리된다.
- 모터/IO 토글은 플래그만 바뀔 뿐 물리 동작이 없다.
- 센서/카메라 스트림(video)·detection 같은 ROS 토픽은 시뮬레이션하지 않는다.

---

## 7. 자주 겪는 문제

| 증상 | 원인 / 해결 |
| --- | --- |
| `--simulator`인데 시작에서 멈춤 | MQTT broker 또는 EZI IP에 접속 시도 중. broker를 띄우거나 `--vehicle-smoke-test`/`--vehicle-console`로 확인 |
| MQTT에 상태가 안 보임 | broker 주소(`[mqtt_broker]` 또는 `--mqtt-host/--mqtt-port`)와 토픽 prefix `amr/v3/<serial>` 확인. TUI는 같은 broker를 구독한다 |
| 다대수 실행 시 한 대만 보임 | `id`(serial_number)가 중복이면 같은 토픽·client_id로 서로를 끊는다. 각 로봇 `id`를 고유하게 |
| 로봇이 안 움직임 | order에 `nodePosition` 좌표가 있는지, `[settings] speed > 0`인지 확인 |

관련 문서: [adaptor/readme.md](../../adaptor/readme.md) · [docs/guide/web-ui.md](web-ui.md)
