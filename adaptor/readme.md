THIS IS Jibot Adapter which follows VDA5050 protocols

=======================
How to install
=======================

##### (1) How to install #####
python environment or native python
#### Installation Window ( Conda env ) or ( Python env )####
Recommended to install with virtual environment "Python env"

python -m venv venvJIBOT
source venvJIBOT/bin/activate

( install with virtual environment )
1) pip3 install -r requirements.txt


IF PC is offline ( no internet access )
##### (2) Install from offline_packages #####
`offline_packages/`가 이미 `~/adapter` 아래에 있으면 전용 스크립트로 설치한다.
이 스크립트는 pip/ensurepip이 없을 때도 pure Python wheel을 직접 풀어서 복구한다.

```bash
ucore@ubuntu:~/adapter$ scripts/install-offline-python-deps.sh .
```

`offline_packages/`가 없으면 인터넷 가능한 PC에서 wheel을 만든 뒤 온보드 PC로 복사한다.

```bash
python3 -m pip download -r requirements.txt -d offline_packages
tar czf offline_packages.tgz offline_packages
scp offline_packages.tgz ucore@192.168.3.10:~/adapter/
```

Ubuntu 20.04 / Python 3.8에서 WebUi가 아래 로그로 계속 재시작하면 `tomli` 의존성이 빠진 상태이다.
Python 3.8에는 `tomllib`가 내장되어 있지 않으므로 오프라인 wheel을 WebUi가 쓰는 가상환경에 설치한다.

```
ModuleNotFoundError: No module named 'tomllib'
```

일반 원격 업데이트 중에도 의존성 설치가 필요하면 `--install-py-deps`를 지정해 업로드 후 같은
오프라인 의존성 설치를 수행한다.

```bash
scripts/update-jibot-adapter-over-ssh.sh --install-py-deps ucore@192.168.3.10
```

이미 온보드 PC에 `offline_packages/`가 있다면 전용 스크립트를 실행한다.

```bash
cd ~/adapter
scripts/install-offline-python-deps.sh .
sudo systemctl daemon-reload
sudo systemctl restart amr-webui
sudo journalctl -u amr-webui -n 50 --no-pager
```

`scripts/install-offline-python-deps.sh`는 `.venv` → `venvJIBOT` → system `python3` 순서로 Python을 고르고,
pip/ensurepip이 없으면 pure Python wheel을 직접 풀어서 설치한다.


======================= 
How to RUN
=======================
(venvJIBOT) ucore@ubuntu:~/adapter$ python3 main.py

# or use the runner script
ucore@ubuntu:~/adapter$ ./run-main.sh

# JIBOT AMR 없이 로컬 시뮬레이터로 실행
ucore@ubuntu:~/adapter$ ./run-main.sh --simulator
ucore@ubuntu:~/adapter$ python3 main.py --simulator

# 시뮬레이터 시작 위치를 실행 시점에 지정 (--x/--y/--theta, 일부만 줘도 됨)
ucore@ubuntu:~/adapter$ python3 main.py --simulator --x 1.5 --y 2.0 --theta 0.3
ucore@ubuntu:~/adapter$ python3 main.py --simulator --x 1.5         # y/theta는 저장값 또는 0

# 시뮬레이터 시작 배터리(%)를 실행 시점에 지정 (--battery, 0-100 범위로 clamp)
ucore@ubuntu:~/adapter$ python3 main.py --simulator --battery 25    # 안 주면 랜덤(30-100%)

# 충전 중(도킹) 상태로 시작 (--charging, status=charging + 배터리 상승)
ucore@ubuntu:~/adapter$ python3 main.py --simulator --charging
ucore@ubuntu:~/adapter$ python3 main.py --simulator --battery 20 --charging   # 20%에서 충전 시작

# 시뮬레이터를 특정 MQTT broker / 로봇 ID로 실행
# --id는 VDA5050 serialNumber이며 MQTT topic prefix에 사용된다.
ucore@ubuntu:~/adapter$ ./run-main.sh --simulator --id SIM-001 --mqtt-host 192.168.3.108 --mqtt-port 11883
ucore@ubuntu:~/adapter$ python3 main.py --simulator --id SIM-002 --mqtt-host 127.0.0.1 --mqtt-port 1883

# MQTT/EZI 연결 없이 vehicle 명령 경로만 빠르게 확인하고 종료
ucore@ubuntu:~/adapter$ python3 main.py --simulator --vehicle-smoke-test
ucore@ubuntu:~/adapter$ JIBOT_RECORD=1 python3 main.py --simulator --vehicle-smoke-test

# 터미널에서 JIBOT 명령을 선택하고 파라미터를 입력해 응답 확인
ucore@ubuntu:~/adapter$ python3 main.py --simulator --vehicle-console
ucore@ubuntu:~/adapter$ JIBOT_RECORD=1 python3 main.py --robot HN-SH6-TR-002 --vehicle-console

# JIBOT 장비의 map/routes 파일을 adaptor 로컬 경로로 복사
ucore@ubuntu:~/adapter$ python3 main.py --sync-jibot-params
ucore@ubuntu:~/adapter$ python3 main.py --sync-jibot-params --jibot-params-dest ./jibot/params

# robots.hcl의 특정 로봇 설정으로 실행
ucore@ubuntu:~/adapter$ ./run-main.sh --robot HN-SH6-TR-002
ucore@ubuntu:~/adapter$ ./run-main.sh --simulator --robot HN-SH6-TR-002

`--simulator`를 지정하면 실제 AMR TCP socket 대신 내장 파이썬 시뮬레이터를 사용한다.
단, 시뮬레이터는 **AMR vehicle만** 대체하며 MQTT broker와 EZI IO/모터는 그대로
실제 연결을 시도한다. MQTT/EZI 없이 vehicle 경로만 보려면 `--vehicle-smoke-test`
또는 `--vehicle-console`을 쓴다. 자세한 사용법: [docs/guide/simulator.md](../docs/guide/simulator.md)

로봇별 `id`/`vehicle_ip`/`ezi_io`/`ezi_motor`는 `config/robots.hcl`에 둔다.
PIO/EZI 튜닝, 에어샤워·엘리베이터, 액션 모듈 설정은
`config/extensions.hcl`에 둔다. 등록된 extension을 순서대로 조합하는 workflow는
선택 파일인 `config/recipes.hcl`의 `recipe`/`step`/`cleanup` 블록으로 정의한다.
로봇별로 다른 설정이 필요하면 `robots.hcl`의 `extensions`/`recipes` 키로 별도
파일을 지정한다.

`workingState` 진입/이탈에 extension 또는 recipe를 연결하려면
`extensions.hcl`에 `state_action`을 둔다. 상태 이름은 `IDLE`, `DRIVING`,
`ACTING`, `CHARGING`, `PAUSED`, `BLOCKED`, `ERROR` 중 하나이며 `start`와
`end`는 각각 생략할 수 있다. 동일 상태가 유지되는 동안에는 재실행하지 않는다.

```hcl
state_action "DRIVING" {
  start = { action = "drivingWarningOn" }
  end   = { action = "drivingWarningOff" }
}
```

상태 이탈 시 실행 중인 `start`를 취소하고 recipe cleanup을 마친 뒤 `end`를
실행한다. action 실패는 로그에 남지만 로봇의 상태 전환이나 이동은 막지 않는다.

현장 적용 순서는
[extension recipe acceptance](../docs/guide/extension-recipe-acceptance.md)를 따른다.
`--id`, `--vehicle-ip`, `--mqtt-host` 같은 CLI 옵션은 수동 디버깅용 일회성 override이며
config 파일을 수정하지 않는다.

----- 지도 저장 / 시뮬레이터 시작 위치 -----

실제 AMR로 실행하면 adaptor가 로봇의 지도(`UmGetMap`)를 받아올 때마다 이를
`runtime/jibot-map.json`에 자동 저장한다(파싱된 노드 + 원본 응답). 이후
`--simulator`로 실행하면 이 저장된 지도를 읽어 시뮬레이터를 그 지도 위에서
생성하므로, 빈 가상 공간이 아니라 실제 station 배치 그대로 노드 기반 주행을
시험할 수 있다. 저장된 지도가 없으면 시뮬레이터는 기존처럼 FMS order가 주는
노드 위치에만 의존한다.

시뮬레이터의 **시작 위치**는 실행할 때 `--x`, `--y`, `--theta`(rad)로 지정한다.
세 값 중 일부만 줘도 되며, 주지 않은 축은 마지막으로 저장된 위치
(`runtime/jibot-position.json`) 값을, 그마저 없으면 0을 쓴다. 따라서 플래그를
하나도 주지 않으면 기존처럼 마지막 저장 위치(또는 원점)에서 시작한다. 이
플래그는 시뮬레이터 전용이다.

시뮬레이터의 **시작 배터리**는 `--battery`(%)로 지정한다. 0-100 범위로 clamp되며,
주지 않으면 기존처럼 랜덤(30-100%)으로 시작한다. 이 플래그도 시뮬레이터 전용이다.

`--charging`을 주면 충전기에 도킹된 채 켜진 것처럼 **충전 중 상태로 시작**한다
(`status=charging`, 배터리 1%/초 상승, `powerSupply.charging=true`). 주행/정지
명령이 들어오면 충전기에서 벗어난 것으로 보고 충전이 멈춘다. `--battery`와 함께
쓰면 지정한 배터리에서 충전을 시작한다. 이 플래그도 시뮬레이터 전용이다.

> 참고: 마지막 위치 저장 기능(`runtime/jibot-position.json`)은 그대로 유지된다.
> 실제 AMR 실행 중 주기적으로 pose를 저장하고, 시뮬레이터 시작 시 위 우선순위의
> fallback으로 사용된다.

=======================
adaptor 여러 개 동시 실행
=======================
한 broker에 여러 대(robot)를 붙이려면 인스턴스마다 고유한 `--id`(serial_number)를
준다. 토픽 prefix가 `amr/v3/<id>`로 갈라지고 MQTT client_id도 분리된다.

# 한 대만 옵션으로 지정해 기동
ucore@ubuntu:~/adapter$ ./run-main.sh --robot HN-SH6-TR-001

# 여러 대를 robots.hcl 목록으로 한 번에 기동 (로봇마다 main.py 프로세스 1개)
ucore@ubuntu:~/adapter$ cp config/robots.hcl.example config/robots.hcl   # 값 수정 후
ucore@ubuntu:~/adapter$ ./run-multi.sh
ucore@ubuntu:~/adapter$ ./run-multi.sh --simulator                          # 전체 시뮬레이터로

# robots.hcl의 특정 로봇 하나만 fleet 설정으로 기동 (수동 디버깅 경로)
ucore@ubuntu:~/adapter$ python3 main.py --robot HN-SH6-TR-001

주요 인스턴스별 옵션: `--id`(=`--serial-number`), `--config`, `--vehicle-ip`,
`--vehicle-port`, `--ezi-io`, `--ezi-motor`, `--mqtt-host`, `--mqtt-port`.
robots.hcl 형식은 [config/robots.hcl.example](config/robots.hcl.example) 참고.

robots.hcl의 `robot` 블록은 라벨(id)만 필수다. 나머지 키는 생략하면
config.toml 기본값을 그대로 쓴다.
- 시뮬레이터/가상 인스턴스: `id`(+ `simulator = true`)만 줘도 토픽만
  `amr/v3/<id>`로 갈라진 인스턴스가 뜬다(= `--id`만 준 효과).
- 실제 로봇: 인스턴스마다 `vehicle_ip`/`ezi_io`/`ezi_motor`를 더해
  하드웨어를 분리한다(같은 하드웨어에 두 인스턴스를 붙이면 안 됨).
인스턴스마다 토픽 prefix가 달라 WebUi 목록에 각자 다른 live 값으로 표시된다.

기본 fleet은 로봇 수와 관계없이 `amr-adaptor.service`를 사용한다.
`config/robots.hcl`이 없거나 비어 있으면 시작에 실패한다.
`config/robots.hcl`에 `robot` 블록이 1개면 그 로봇 하나를
`main.py --robot <id>`로 실행하고, 2개 이상이면 같은 서비스 아래에서
`run_multi.py`가 모든 로봇을 실행한다. `robots.hcl`은 항상 있어야 하며,
로봇별 identity/IP/EZI/MQTT override의 단일 출처다. 자세한 내용:
[docs/guide/simulator.md](../docs/guide/simulator.md) ·
[docs/guide/web-ui.md](../docs/guide/web-ui.md)
`amr-adaptor@<name>.service`는 명시적 `[[adapter.instances]]`와 의도적으로 지정한 JIBOT robot ID/수동 호환 경로를
지원하며, robots.hcl에 2개 이상 있다는 이유만으로 자동 선택되지는 않는다.

ucore@ubuntu:~/adapter$ scripts/setup-adaptor-service.sh --jibot
ucore@ubuntu:~/adapter$ sudo systemctl start amr-adaptor.service            # 1대/2대 이상 기본 fleet

=======================
WebUi 관리/모니터링 (systemd)
=======================
adaptor를 systemd 서비스로 등록하고, 브라우저 기반 WebUi로
상태/로그/테스트/동작/설정을 한 곳에서 관리한다. 실시간 로봇 상태는 adaptor가
publish하는 같은 MQTT broker를 구독해서 보여준다. 자세한 사용법: docs/guide/web-ui.md
(curses TUI는 Phase 4에서 제거됨; 기록은 docs/guide/adaptor-tui.md 참고)

----- 실행 방법 -----

# (1) 한 번만: JIBOT 서비스 등록. (adaptor가 있는 그 장비에서 실행)
#     - /etc/systemd/system/*.service 생성, venvJIBOT 오프라인 복구, sudoers 등록까지 수행.
#     - sudo가 필요해 한 번 비밀번호를 묻는다. --dry-run으로 바뀌는 내용을 먼저 확인 가능.
ucore@ubuntu:~/adapter$ scripts/setup-adaptor-service.sh --jibot           # JIBOT full-stack 등록
ucore@ubuntu:~/adapter$ scripts/setup-adaptor-service.sh --jibot --dry-run # 미리보기(변경 없음)
ucore@ubuntu:~/adapter$ scripts/setup-adaptor-service.sh --hexplorer       # Hexplorer 장비에서만 등록

# systemd 관리 WebUI는 amr-webui.service: 상태 확인 / 수동 복구
ucore@ubuntu:~/adapter$ systemctl status amr-webui.service --no-pager
ucore@ubuntu:~/adapter$ sudo systemctl restart amr-webui.service

# 서비스는 기본으로 enable 후 start/restart된다. enable만 하려면 setup에 --no-start를 지정한다.
systemd 관리 WebUI는 `amr-webui.service`다.

----- 설정 파일 / 관련 파일 -----

config/config.toml                      adaptor와 WebUI가 함께 읽는 설정. WebUI는 특히 아래 값을 사용한다.
                                          [mqtt_broker] host, port            → 모니터링용 broker 접속 대상
                                          [vehicle] serial_number, vda_version,
                                          [mqtt_broker] vda_interface         → MQTT topic prefix
                                                                                (예: amr/v3/HN-SH6-TR-001)
                                        ※ adaptor는 config를 起動 시 1회만 읽으므로, 값을 바꾸면
                                          서비스를 재시작해야 반영된다(Config 뷰가 검증 후 재시작을 안내).
                                        ※ JIBOT/Hexplorer 두 adaptor는 같은 config.toml과 prefix를 공유한다.

scripts/setup-adaptor-service.sh        설정 vendor의 서비스 설치. --jibot/--hexplorer는 vendor 일치 검증. (User=현재계정, WorkingDirectory=이 adaptor 경로)
run-web.sh                              systemd를 사용하지 않는 수동/개발용 WebUi 런처. `uv run python -m web` 실행.
run-adapter.sh                          systemd ExecStart dispatcher. vendor/fleet에 맞는 launcher를 선택한다.
scripts/systemd/amr-adaptor.service     기본 fleet systemd 유닛 "템플릿". 설치 시 __USER__/__WORKDIR__가 치환된다.
scripts/systemd/amr-adaptor@.service   명시적 adapter instance와 targeted JIBOT/manual compatibility 인스턴스 템플릿.
run-main.sh / run-hexplorer.sh          dispatcher가 선택하는 launcher. venvJIBOT 활성화 후 해당 main을 실행한다.

설치 후 장비에 생성되는 파일:
/etc/systemd/system/amr-adaptor.service         어댑터 서비스 유닛 (vendor는 config.toml [adapter].vendor로 결정)
/etc/systemd/system/amr-adaptor@.service        명시적/targeted 호환 인스턴스 유닛
/etc/sudoers.d/adaptor-tui                       호환성을 위해 유지하는 legacy 파일명. deploy user에게 관리 대상
                                                 서비스의 제한된 systemctl 제어만 허용한다. 제거하면 권한 회수.

# 서비스 직접 제어가 필요할 때(참고):
ucore@ubuntu:~$ sudo systemctl start|stop|restart amr-adaptor.service
ucore@ubuntu:~$ systemctl status amr-adaptor.service          # 상태 (sudo 불필요)
ucore@ubuntu:~$ journalctl -u amr-adaptor.service -f          # 로그 follow
`--vehicle-smoke-test`는 vehicle 연결 후 `UmGetRobotInfo`, `UmGetMotorState`, `UmGetLocState`를 보내고 현재 상태를 출력한 뒤 종료한다.
`--vehicle-console`은 연결 후 터미널에서 JIBOT 명령 목록을 보고 번호 또는 명령명으로 선택해 파라미터를 입력하고 응답을 확인하는 모드이다. `list`, `state`, `quit` 명령도 사용할 수 있다.
`--sync-jibot-params`는 `/usr/local/urobot/params/map/`, `/usr/local/urobot/params/routes/`를 adaptor 로컬 경로로 복사한 뒤 종료한다. 기본 대상은 `adaptor/jibot/params/`이고, 기존 대상 파일은 `adaptor/backups/jibot-params/<timestamp>/` 아래에 백업한 뒤 덮어쓴다.
VDA5050 v3 instantActions에서도 `actionType: "syncJibotParams"`로 같은 동작을 실행할 수 있다.
VDA5050 v3 표준 위치 값은 `state.mobileRobotPosition`에 `x`, `y`, `theta`, `mapId`, `localized`, `localizationScore`로 보낸다. JIBOT 원본 `mode`, `status`, battery raw 값처럼 표준 필드가 아닌 값만 `state.information[].infoReferences`에 `jibotMode`, `jibotStatus` 등으로 추가한다.

## JIBOT startup / 초기 배터리 수신 대기

실제 JIBOT은 adapter 시작 직후 첫 `UmGetLocState`/status 프레임을 받기 전까지
배터리 SOC가 아직 알려지지 않는다. 이 상태를 숫자 `0%`로 취급하면 ACS가
실제 저전압으로 오해하므로, adapter는 "미수신"과 "실제 0%"를 구분한다.

- `jibot-client`는 시작 시 `_battery = None`, `_battery_known = False`로 둔다.
  status 응답에 `battery` 필드가 들어온 순간에만 `_battery_known = True`가 된다.
- `[jibot_client].require_battery_before_ready = true`이면 배터리 첫 수신 전까지
  VDA5050 `operatingMode`는 `STARTUP`으로 보고된다.
- 이 동안 `powerSupply.stateOfCharge`는 `[jibot_client].startup_battery_soc`
  값을 임시로 사용한다. 기본값은 `100.0`이며, 실제 배터리 수신 후에는 로봇이
  준 SOC를 그대로 사용한다. 따라서 실제 `battery=0` 수신은 진짜 `0%`로 유지된다.
- 상태 진단은 `state.information[].infoReferences`에 함께 실린다:
  `adapterInitializing=true|false`, `adapterInitPending=battery`, 그리고
  `jibotBatteryKnown=true|false`.

운영에서 startup 상태가 오래 지속되면 low battery가 아니라 JIBOT status stream에서
`battery` 필드가 들어오지 않는 문제로 보아야 한다. 빠른 기동이 더 중요하고 배터리
첫 수신을 ready 조건으로 삼고 싶지 않다면 `require_battery_before_ready = false`로
낮출 수 있지만, 이 경우에도 `jibotBatteryKnown=false` 진단은 남는다.

시뮬레이터는 `goto_xyz`, `goto_point`, `stop_motion`, JIBOT instant command 호출을 받아 상태값과 위치를 갱신한다.
MQTT broker와 EZI IO/Motor 연결은 기존 설정을 그대로 사용하므로, 필요하면 `config/config.toml`에서 테스트 환경에 맞게 조정해야 한다.

```json
{
  "headerId": 1,
  "timestamp": "2026-06-05T16:10:00.000Z",
  "version": "3.0.0",
  "manufacturer": "jibot",
  "serialNumber": "HM-CAR-TR-01",
  "actions": [
    {
      "actionType": "syncJibotParams",
      "actionId": "sync-jibot-params-001",
      "blockingType": "NONE",
      "actionParameters": [
        { "key": "sourceDir", "value": "/usr/local/urobot/params" },
        { "key": "targetDir", "value": "./jibot/params" },
        { "key": "backupDir", "value": "./backups/jibot-params" }
      ]
    }
  ]
}
```

# JIBOT TCP 송수신 녹화
ucore@ubuntu:~/adapter$ JIBOT_RECORD=1 JIBOT_RECORD_DIR=logs/jibot python3 main.py

`JIBOT_RECORD=1`을 지정하면 JIBOT TCP 요청/응답을 JSONL 형식으로 저장한다.
기본 저장 경로는 `logs/jibot/YYYYMMDD-HHMMSS-session.jsonl`이고, `JIBOT_RECORD_FILE=/path/to/session.jsonl`로 파일을 고정할 수 있다.
`password`, `token`, `api_key` 같은 민감 필드는 자동으로 `***` 처리된다.


=======================
How to setup Ubuntu daemon
=======================

Ubuntu 장비에서 `systemd` service로 등록하면 부팅 후 자동 실행되고, 프로세스가 종료되면 자동 재시작된다.
JIBOT adapter/WebUi와, 카메라 필수 조건이 준비된 경우 camera를 등록하는 표준 설치 경로는 다음 명령이다.

```bash
ucore@ubuntu:~/adapter$ scripts/setup-adaptor-service.sh --jibot
```

`install-systemd-service.sh`는 adapter 서비스만 설치하는 호환용 도구다. 기본 service 이름은
`amr-adaptor.service`이고, 현재 adapter 폴더의 `run-adapter.sh` dispatcher를 실행한다.
`run-adapter.sh`는 `config/robots.hcl`을 읽어 단일/다중 실행을 선택한다.
설치 후에는 `systemctl daemon-reload`, `systemctl enable amr-adaptor.service`, `systemctl restart amr-adaptor.service`가 자동으로 실행된다.

실제 AMR identity/IP는 `config/robots.hcl`의 해당 `robot` 블록에 둔다.

다른 사용자 또는 service 이름으로 실행해야 하면 옵션을 지정한다.

```bash
ucore@ubuntu:~/adapter$ ./install-systemd-service.sh --name amr-adaptor --user ucore --group ucore
ucore@ubuntu:~/adapter$ ./install-systemd-service.sh --args "--instance HN-SH6-TR-001 --simulator"
```

운영 명령:

```bash
sudo systemctl status amr-adaptor.service --no-pager
sudo journalctl -u amr-adaptor.service -f
sudo systemctl restart amr-adaptor.service
sudo systemctl stop amr-adaptor.service
```

service를 등록한 뒤 SSH 업데이트 스크립트를 사용할 때는 adapter와 설치된 WebUi를 함께 다시 불러온다.

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --restart \
  ucore@192.168.3.10
```


Topic

amr/v3/HM-CAR-TR-01/state   order   instantAction



=======================
How to update over SSH
=======================

로컬 `adaptor` 디렉터리 전체, `jibot-client/src/jibot_client` 패키지, `jibot-simulator`를 원격 adapter 폴더에 덮어쓴다.
scp/rsync 없이 SSH로 접속한 뒤 `tar` 스트림을 원격에서 풀어 넣는 방식이다.
스크립트 내부에서 SSH connection sharing을 사용하므로 password 인증은 첫 연결에서 한 번만 입력하면 된다.

기본 원격 경로는 `~/adaptor` 이다.

```bash
# repo root에서 실행
scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.10

# 옵션 확인
scripts/update-jibot-adapter-over-ssh.sh --help

# 원격 adapter 경로가 다르면 host 앞에 옵션으로 지정
scripts/update-jibot-adapter-over-ssh.sh --remote-dir /home/ucore/adapter ucore@192.168.3.10

# 포트 또는 키가 필요하면 환경변수 사용
SSH_PORT=2222 SSH_OPTS="-i ~/.ssh/id_rsa" scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.10

# 원격의 오래된 adapter 관리 파일을 먼저 정리한 뒤 업로드
scripts/update-jibot-adapter-over-ssh.sh --clean-remote ucore@192.168.3.10

# 자동 실행에서 config.toml 처리 방식을 미리 선택
scripts/update-jibot-adapter-over-ssh.sh --config-toml-mode keep ucore@192.168.3.10
scripts/update-jibot-adapter-over-ssh.sh --config-toml-mode overwrite ucore@192.168.3.10

# 자동 실행에서 robots.hcl 처리 방식을 미리 선택
scripts/update-jibot-adapter-over-ssh.sh --robots-hcl-mode keep ucore@192.168.3.10
scripts/update-jibot-adapter-over-ssh.sh --robots-hcl-mode overwrite ucore@192.168.3.10

# 업데이트 후 adapter와 설치된 WebUi 재시작까지 실행
scripts/update-jibot-adapter-over-ssh.sh \
  --restart \
  ucore@192.168.3.10
```

업데이트 대상은 action module/panel을 포함한 `adaptor` 아래 파일 전체, JIBOT 직접 통신용 `jibot_client` 패키지, `jibot-simulator`이다. action module/panel은 기존 adaptor tar에 이미 포함되므로 별도 복사 옵션이 필요하지 않다. 단, `__pycache__`, `*.pyc`, `config/config.toml`, `config/robots.hcl`, `config/extensions.hcl`, `config/recipes.hcl`은 제외한다.
원격에 기존 `config/config.toml`이 있으면 기본으로 보존한다. 원격에 파일이 없을 때는 로컬 기본 파일을 한 번 생성한다.
원격에 기존 `config/robots.hcl`이 있으면 기본으로 보존한다. 원격에 파일이 없을 때는 기본으로 만들지 않는다.
원격에 기존 `config/extensions.hcl`이 있으면 현장 하드웨어 튜닝을 보존하고,
없을 때만 로컬 기본 파일을 생성한다.
원격에 기존 `config/recipes.hcl`이 있으면 현장 workflow를 보존하고, 없으면
선택 파일이므로 생성하지 않는다.
자동 실행에서는 `--config-toml-mode keep|overwrite` 또는 `--robots-hcl-mode keep|overwrite`로 선택을 고정할 수 있고, `ask`를 쓰면 실행 중 선택한다.
업로드 중에는 갱신된 원격 위치가 `Updated user@host:~/adapter/path` 형식으로 콘솔에 출력된다.
기본 동작은 덮어쓰기이므로 원격에만 남아 있는 오래된 파일은 삭제하지 않는다.
오래된 프로젝트 파일까지 정리하려면 `--clean-remote`를 사용한다. 이 옵션은 `venvJIBOT` 같은 가상환경이나 로그 파일은 지우지 않고, adapter가 관리하는 `main.py`, `config` 하위 파일, `utils`, `protocol`, `offline_packages` 등을 먼저 삭제한 뒤 업로드한다. 이때도 `config/config.toml`과 `config/robots.hcl`은 각 mode 옵션대로 처리한다.

`--restart`를 지정하지 않으면 업데이트 후 원격 장비에서 adapter와 WebUi 프로세스를 재시작해야 변경이 반영된다. 특정 fleet instance만 의도적으로 재시작할 때는 `--restart-cmd`를 사용한다.

How to change a JIBOT's network (IP / Wi-Fi)
============================================

JIBOT의 주소를 옮길 때 수정해야 하는 3군데를 한 번에 처리한다.

1. `/etc/netplan/01-network-manager-all.yaml` — `wlan0`의 `addresses`(IP/서브넷)와 게이트웨이
2. `/etc/wpa_supplicant/wpa_supplicant.conf` — Wi-Fi `ssid` / `psk`
3. 적용: 활성 `wlan0` NetworkManager 프로필의 SSID/PSK 갱신 → `netplan generate`(검증)
   → `netplan apply` → `NetworkManager` 재시작 및 프로필 재연결 → `urobot.service` 재시작

현재 JIBOT 이미지에서는 `wlan0`가 NetworkManager 연결 프로필로 관리된다. 따라서
`wpa_supplicant.conf`만 수정해서는 현재 연결이 바뀌지 않으며, 스크립트는 `nmcli`로 활성 프로필도
함께 갱신한다. 이때 실제 SSID뿐 아니라 NetworkManager 프로필 이름(`connection.id`)도 새 SSID로
맞추고, 이름 변경과 무관하게 안정적으로 재활성화할 수 있도록 UUID로 프로필을 추적한다.
NetworkManager가 관리하지 않는 장비에서는 `wpa_supplicant.service` 재시작 또는
`wpa_cli reconfigure`로 폴백한다.

첫 인자는 **항상 로봇의 현재 주소**(SSH 접속 대상)이고, `--ip`가 **새 주소**다. 둘을 분리해
`.222`/`.223`을 혼동하지 않는다. 적용 단계는 원격에서 **detached**로 돌아가므로(바꾸는 인터페이스가
곧 접속 경로라 SSH가 끊긴다) 세션이 끊겨도 적용은 완료된다. 끊긴 뒤에는 **새 IP로 재접속**한다.
편집 전 각 파일은 `*.bak.<timestamp>`로 백업되고, `netplan generate`가 실패하면 자동 복원 후 중단한다.

```bash
# repo root에서 실행

# 먼저 미리보기(쓰기/적용 없이 diff만)
scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 --ip 192.168.3.230/24 --dry-run

# .222 로봇을 .230으로 이동(서브넷 /24, 게이트웨이 지정)
scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 \
  --ip 192.168.3.230/24 --gateway 192.168.3.1

# Wi-Fi(SSID/비밀번호)까지 함께 변경
scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 \
  --ip 192.168.3.230/24 --gateway 192.168.3.1 --ssid NEW_AP --psk 'secret-pass'

# 원격 wlan0에서 보이는 Wi-Fi 목록 중 선택하고 비밀번호는 숨김 입력
scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 --wifi-select

# 파일만 수정하고 적용은 직접(원격 콘솔에서) 하고 싶을 때
scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 --ip 192.168.3.230/24 --no-apply

# 포트/키, 또는 파일 경로·서비스명이 다르면 환경변수로
SSH_PORT=2222 SSH_OPTS="-i ~/.ssh/id_rsa" \
  scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 --ip 192.168.3.230/24
UROBOT_SERVICE=urobot.service NETPLAN_FILE=/etc/netplan/01-network-manager-all.yaml \
  WPA_FILE=/etc/wpa_supplicant/wpa_supplicant.conf \
  scripts/change-jibot-network-over-ssh.sh ucore@192.168.3.222 --ip 192.168.3.230/24
```

서브넷은 `--ip 192.168.3.230/24`처럼 CIDR로 붙이거나 `--prefix 24`로 따로 줄 수 있다(기본 `/24`).
`--ip`/`--gateway`(netplan)와 `--ssid`/`--psk` 또는 `--wifi-select`(wpa) 중 최소 한 그룹은 지정해야 한다.
`--wifi-select`는 원격 장비에서 `sudo iw dev wlan0 scan`을 실행해 빈 SSID를 제외하고, 중복 SSID는 가장
강한 signal 하나만 보여준다. `nmcli`는 사용하지 않는다.

> 주소(`vehicle_ip`)는 `config/robots.hcl`, 영상 URL(`web_video_server_public_url`)은
> `config/config.toml`에 둔다. 로봇 OS 네트워크 변경과 어댑터 연결 설정은 별도다.

How to update the adapter video URL (config.toml)
=================================================

위 네트워크 스크립트가 건드리지 않는 **어댑터 쪽 영상 URL 설정**(`config/config.toml`)을 수정한다.

- `[video].web_video_server_public_url` — FMS가 여는 영상 URL (온보드 PC host:port)

기본은 **로컬 리포의 `adaptor/config/config.toml`**만 수정한다. `--host`를 주면 로봇의 원격
`~/adapter/config/config.toml`도 SSH로 함께 수정한다(운영 중 빠른 변경). 주석·서식은 보존되고
편집 전 `*.bak.<timestamp>` 백업을 남긴다. 어댑터는 재시작해야 반영되므로 원격은 `--restart`로
같이 재시작할 수 있다.

```bash
# repo root에서 실행

# 미리보기(diff만)
scripts/update-jibot-adapter-config.sh --video-url http://192.168.3.230:9001 --dry-run

# 로컬 리포만: 영상 host 변경(영상 포트는 유지)
scripts/update-jibot-adapter-config.sh --video-host 192.168.3.230

# 로컬 + 운영 중인 로봇 둘 다 수정하고 어댑터 재시작
scripts/update-jibot-adapter-config.sh --video-host 192.168.3.230 \
  --host ucore@192.168.3.222 --restart

# 원격만 수정(로컬 리포는 그대로)
scripts/update-jibot-adapter-config.sh --video-host 192.168.3.230 --host ucore@192.168.3.222 --no-local
```

`--video-host`는 host만 바꾸고 scheme(과 host에 포트가 없으면 기존 포트)을 유지한다.
전체 URL을 바꾸려면 `--video-url`을 쓴다. 로컬만 고쳤다면 `update-jibot-adapter-over-ssh.sh`로 배포한다.

##### Camera / Video to FMS #####

카메라 영상은 MQTT로 픽셀을 흘리지 않는다. 온보드 PC의 ROS1 노드 `web_video_server`가
**ROS image 토픽을 구독해 HTTP로 서빙**하고(그 자체가 HTTP 서버), adapter는 얇은 클라이언트다.

```text
ROS image topic --(subscribe)--> web_video_server (HTTP :9001) --HTTP--+--> FMS (라이브, 직접)
                                                                       +--> adapter GET /snapshot --> MQTT (이벤트 썸네일)
```

- 라이브: FMS가 `requestVideo` instant action을 보내면 adapter가 스트림 URL을 응답한다
  (action result + `state.information.VIDEO_STREAMS`). FMS는 그 URL을 직접 연다.
- 이벤트 스냅샷: `brake`/`slowdown`/`watch`/`waiting` 발생 시 adapter가 `/snapshot`에서
  JPEG 한 장을 받아 `amr/v3/<serial>/event_snapshot/<event>`로 바이너리 발행한다.

설치(온보드 PC, sudo 필요 → 대화형 SSH로 동작, sudo 비밀번호를 묻는다):

```bash
# 기본 대상 ucore@192.168.3.222, 포트 9001
scripts/setup-web-video-server-on-onboard.sh ucore@192.168.3.222
# 환경변수로 조정: SSH_PORT, ROS_DISTRO, WEB_VIDEO_PORT
```

`ros-<distro>-web-video-server`가 이미 설치되어 있는지 확인하고 systemd 유닛
(`scripts/systemd/amr-camera.service`)을 등록·활성화한 뒤 테스트 URL을 출력한다.
패키지를 인터넷에서 설치하지 않는다. 설치 후 `config/config.toml`의 `[video]`에서
`web_video_server_public_url`을 FMS가 접근 가능한 host:port(예: `http://192.168.3.222:9001`)로
맞추고 adapter를 재시작한다.

JIBOT 온보드는 **인터넷이 없으므로** 미리 받아둔 arm64 deb로 오프라인 설치한다
(`scripts/offline-debs/web-video-server/` 참고):

```bash
ssh ucore@192.168.3.222 'mkdir -p /tmp/wvs_debs'
scp scripts/offline-debs/web-video-server/*.deb ucore@192.168.3.222:/tmp/wvs_debs/
ssh -t ucore@192.168.3.222 'sudo dpkg -i /tmp/wvs_debs/*.deb'
scripts/setup-web-video-server-on-onboard.sh ucore@192.168.3.222   # 패키지 확인 후 서비스만 등록
```

systemd에 직접 등록해서 실행하려면(온보드 PC에서 수행):

```bash
# 1) 유닛 파일 설치
sudo cp scripts/systemd/amr-camera.service /etc/systemd/system/amr-camera.service

# 2) 필요 시 유닛의 User=, ROS 경로, 포트 수정
sudo vi /etc/systemd/system/amr-camera.service

# 3) systemd 반영 + 부팅 자동시작 + 즉시 실행
sudo systemctl daemon-reload
sudo systemctl enable --now amr-camera.service

# 4) 상태/로그 확인
systemctl status amr-camera --no-pager
journalctl -u amr-camera -n 50 --no-pager
```

제공 유닛(`scripts/systemd/amr-camera.service`)은 ROS1/noetic 기준이다.
온보드 PC의 ROS 배포판이 다르거나 ROS2라면 `ExecStart`에서 `/opt/ros/<distro>/setup.bash`,
`rosrun`/`ros2 run`, 포트(`9001`)를 환경에 맞게 바꾼다. 자동 설치 스크립트를 쓰면 이 부분은
ROS 설치를 감지해서 생성된다.

`[video].snapshot_topic`은 **항상 발행되는 토픽**(cam2/cam3 또는 berxel depth)을 쓴다.
`/bundle_detection_image`는 검출 중에만 나와서 평소엔 타임아웃한다. front는 RGB가 꺼져 있어
(`color_enable=false`) 깊이만 전방으로 나온다 — 컬러가 필요하면 로봇 스택에서 활성화해야 한다.

설치 확인:

```bash
curl -s http://192.168.3.222:9001/ | head
curl -s "http://192.168.3.222:9001/snapshot?topic=/camera/cam2/image_raw" -o frame.jpg
# 브라우저: http://192.168.3.222:9001/stream?topic=/camera/cam2/image_raw
```

서비스 제어(SSH). 상태/로그는 sudo 불필요, start/stop/enable/disable은 sudo 필요(`-t`로 비밀번호 tty 확보).
`stop`/`start`는 지금 동작, `disable`/`enable`은 부팅 자동시작 — 서로 독립이다.

```bash
# 상태 / 로그 (sudo 불필요)
ssh ucore@192.168.3.222 'systemctl status amr-camera --no-pager'
ssh ucore@192.168.3.222 'journalctl -u amr-camera -n 50 --no-pager'

# 지금 끄기 / 켜기 / 재시작 (sudo)
ssh -t ucore@192.168.3.222 'sudo systemctl stop amr-camera'
ssh -t ucore@192.168.3.222 'sudo systemctl start amr-camera'
ssh -t ucore@192.168.3.222 'sudo systemctl restart amr-camera'

# 부팅 자동시작 끄기 / 켜기 (지금 동작 상태는 안 바뀜)
ssh -t ucore@192.168.3.222 'sudo systemctl disable amr-camera'
ssh -t ucore@192.168.3.222 'sudo systemctl enable amr-camera'

# 한 번에: 지금 끄고+부팅도 끄기 / 지금 켜고+부팅도 켜기
ssh -t ucore@192.168.3.222 'sudo systemctl disable --now amr-camera'
ssh -t ucore@192.168.3.222 'sudo systemctl enable --now amr-camera'

# 서비스 완전 제거 (패키지는 유지)
ssh -t ucore@192.168.3.222 'sudo systemctl disable --now amr-camera; sudo rm /etc/systemd/system/amr-camera.service; sudo systemctl daemon-reload'
```

서비스 끈 상태에서 임시로 한 번만 띄우려면: `stop`으로 9001 비운 뒤
`rosrun web_video_server web_video_server _port:=9001 _address:=0.0.0.0`.

설계/근거: `jibot-client/docs/jibot-video-to-fms-design.md`, 카메라 토픽 맵: `jibot-client/docs/jibot-camera-topics.md`.
