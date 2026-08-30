# Unified AMR Adaptor

## Quick Install / systemd

로컬 개발 PC에서 원격 AMR 온보드 PC로 adapter/Web UI/camera 설치 파일을 업로드합니다.
업로드는 하나의 tar bundle과 하나의 SSH 연결로 처리됩니다.

```bash
scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.10
```

일반 `ssh`는 되는데 업로드 스크립트만 연결 단계에서 실패하면 SSH connection sharing을 끄고 다시 실행합니다.

```bash
SSH_CONTROL_MASTER=0 \
  scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.10
```

원격 장비에 SSH 접속 후 adapter/Web UI와, 카메라 필수 조건이 준비된 경우 camera systemd 서비스를 등록합니다.
`amr-camera.service`는 ROS 배포판과 `web_video_server` 패키지를 사용할 수 있을 때 같이 등록됩니다.

```bash
ssh ucore@192.168.3.10
cd ~/adapter
scripts/setup-adaptor-service.sh --jibot
```

ROS 배포판 자동 감지가 안 되는 장비에서는 다음처럼 지정해서 다시 실행합니다.

```bash
WEB_VIDEO_ROS_DISTRO=noetic scripts/setup-adaptor-service.sh --jibot
```

JIBOT 기본 fleet은 `amr-adaptor.service`를 사용합니다. `config/robots.hcl`이 없거나 비어 있으면 시작에 실패합니다.
`robot` 블록이 1개면 단일 `main.py`를, 2개 이상이면 dispatcher가 `run_multi.py`를 실행합니다.

```bash
sudo systemctl start amr-adaptor.service
sudo systemctl status amr-adaptor.service --no-pager
sudo journalctl -u amr-adaptor.service -f
```

`setup-adaptor-service.sh --jibot`은 Web UI도 기본으로 enable 후 start/restart합니다.
아래 명령은 상태 확인과 수동 복구용이며, `--no-start`로 설치한 경우에만 직접 시작해야 합니다.
로그인 파일이 없으면 `config/web-credentials.toml`이 기본값(`admin` / `labtomarket1231`)으로 생성됩니다.

```bash
sudo systemctl enable --now amr-webui
sudo systemctl status amr-webui --no-pager
```

Ubuntu 20.04 / Python 3.8 장비에서 Web UI가 `ModuleNotFoundError: No module named 'tomllib'`로
재시작 루프에 들어가면 `tomli` 의존성이 빠진 것입니다. 일반 SSH 업데이트 중에도 의존성 설치가
필요하면 `--install-py-deps`를 지정해 업로드 후 `offline_packages/`에서 설치합니다.

```bash
scripts/update-jibot-adapter-over-ssh.sh --install-py-deps ucore@192.168.3.10
```

이미 온보드 PC에 파일이 올라가 있다면 전용 오프라인 설치 스크립트를 실행합니다.

```bash
cd ~/adapter
scripts/install-offline-python-deps.sh .
sudo systemctl daemon-reload
sudo systemctl restart amr-webui
```

카메라 HTTP 서버는 필요할 때 systemd나 Web UI에서 실행합니다.

```bash
sudo systemctl start amr-camera.service
sudo systemctl status amr-camera.service --no-pager
```

Web UI의 `/camera` 화면은 `amr-camera.service` 상태/제어와 카메라 stream/snapshot 링크를 제공합니다.
브라우저에서 `http://192.168.3.10:9000/`에 접속하고 기본 로그인 `admin` / `labtomarket1231`을 사용합니다.

## Jibot Adapter

- [Jibot adapter install/run guide](adaptor/readme.md)
  - Jibot VDA5050 adapter의 설치, 실행, Ubuntu daemon 등록, MQTT topic, SSH 업데이트 사용법을 정리한 문서입니다.
- [Simulator usage guide](docs/guide/simulator.md)
  - 실제 AMR 없이 시뮬레이터로 adaptor를 단일/다중으로 돌려보는 방법, 동작 모델, 한계, 트러블슈팅 문서입니다.
- [SSH update usage](adaptor/readme.md#how-to-update-over-ssh)
  - 원격 장비에 SSH로 접속해서 `adaptor` 전체를 `tar` 스트림 방식으로 덮어쓰는 절차입니다.
- [Ubuntu daemon setup](adaptor/readme.md#how-to-setup-ubuntu-daemon)
  - Ubuntu `systemd` service로 adapter를 등록해 부팅 후 자동 실행하고, 장애 시 자동 재시작되도록 설정합니다.

## Ubuntu Daemon Quick Start

Ubuntu 장비의 adapter 폴더에서 다음 명령을 실행하면 JIBOT adapter/Web UI와, 카메라 필수 조건이 준비된 경우 camera systemd 서비스가 등록됩니다.

```bash
cd ~/adapter
scripts/setup-adaptor-service.sh --jibot
```

`adaptor/install-systemd-service.sh`는 dispatcher 기반 adapter 서비스만 필요한 기존 설치와의 호환용입니다.

실제 AMR identity/IP는 `config/robots.hcl`의 `robot` 블록에 둡니다.
`amr-adaptor.service`는 이 파일을 읽어 로봇이 1개면 단일 실행, 2개 이상이면
같은 서비스 아래에서 다중 실행합니다.

운영 중 상태 확인과 로그 확인은 다음 명령을 사용합니다.

```bash
sudo systemctl status amr-adaptor.service --no-pager
sudo journalctl -u amr-adaptor.service -f
sudo systemctl restart amr-adaptor.service
```

SSH 업데이트 후 adapter와 설치된 Web UI를 함께 다시 불러오려면 `--restart`를 지정합니다.

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --remote-dir /home/ucore/adapter \
  --restart \
  ucore@192.168.3.10
```

## Adaptor WebUi control panel (systemd)

브라우저 기반 WebUi로 JIBOT/Hexplorer adaptor를 systemd 서비스로
관리·모니터링·테스트·제어·설정합니다. 실시간 로봇 상태는 adaptor가 publish하는 같은
MQTT broker를 구독해 표시합니다. (curses TUI는 Phase 4에서 제거됨.)

- 자세한 사용법: [docs/guide/web-ui.md](docs/guide/web-ui.md)
- 설정/실행 요약: [adaptor/readme.md](adaptor/readme.md) 내 "WebUi 관리/모니터링" 절

```bash
# (1) 한 번만: JIBOT 서비스 등록 (유닛 생성 + venv 오프라인 복구 + sudoers). --dry-run으로 미리보기.
scripts/setup-adaptor-service.sh --jibot
scripts/setup-adaptor-service.sh --jibot --dry-run
# Hexplorer가 필요한 장비에서만:
scripts/setup-adaptor-service.sh --hexplorer

# systemd 관리 WebUI는 amr-webui.service: 상태 확인 / 수동 복구
systemctl status amr-webui.service --no-pager
sudo systemctl restart amr-webui.service
```

systemd 관리 WebUI는 `amr-webui.service`이며 setup이 기본으로 enable 후 start/restart합니다.

| 파일 | 역할 |
| --- | --- |
| `adaptor/config/config.toml` | adaptor/WebUi 공용 설정. 보드 배포 후 실제 파일은 `~/adapter/config/config.toml`이다. WebUI `/config`는 이 파일의 scalar 값을 자동으로 편집 화면에 노출한다. 리스트/배열은 읽기전용이므로 파일에서 직접 편집한다. config는 시작 시 1회 로드되므로 값 변경 후 **서비스 재시작** 필요. |
| `scripts/setup-adaptor-service.sh` | 설정된 vendor의 서비스를 설치합니다. `--jibot`/`--hexplorer`는 `config.toml`의 vendor와 일치하는지 검증합니다 (`--dry-run` 지원). |
| `adaptor/run-web.sh` | systemd를 사용하지 않는 수동/개발용 WebUi 런처 (`uv run python -m web`). |
| `scripts/systemd/amr-adaptor.service` / `amr-adaptor@.service` | 기본 fleet(1/2+ robot)은 전자를 사용합니다. 후자는 명시적 `[[adapter.instances]]`와 의도적으로 지정한 JIBOT robot ID/수동 호환 경로를 지원하며, robots.hcl의 2+ robot만으로 자동 선택되지 않습니다. |
| 설치 후: `/etc/systemd/system/amr-adaptor.service`, `/etc/sudoers.d/adaptor-tui` | 생성되는 유닛 + WebUi 무암호 제어용 sudoers(해당 계정·해당 유닛 한정). |

## Update Script

- [scripts/update-jibot-adapter-over-ssh.sh](scripts/update-jibot-adapter-over-ssh.sh)
  - 로컬의 `adaptor` 디렉터리 전체를 원격 adapter 폴더로 자동 반영하는 스크립트입니다.
  - `scp`나 `rsync` 없이 SSH와 `tar`로 원격 파일을 직접 갱신합니다.
  - 원격에 기존 `config/config.toml`이 있으면 기본으로 보존합니다. 원격에 없으면 기본 파일을 생성합니다.
  - 원격에 기존 `config/robots.hcl`이 있으면 기본으로 보존합니다. 원격에 없으면 기본으로 만들지 않습니다.
  - SSH connection sharing으로 password 인증은 첫 연결에서 한 번만 받습니다.
  - 기본 원격 경로는 `~/adaptor`이며, 다른 경로는 host 앞에 `--remote-dir DIR`로 지정합니다.
  - `--clean-remote`를 지정하면 원격의 adapter 관리 파일을 먼저 정리한 뒤 업로드합니다.
  - 원격 설정을 덮어써야 할 때만 `--config-toml-mode overwrite` 또는 `--robots-hcl-mode overwrite`를 지정합니다. `ask`를 쓰면 실행 중 선택합니다.
  - `--restart`는 파일 업데이트 후 adapter 유닛과 설치된 Web UI를 함께 재시작합니다. 특정 유닛만 의도적으로 재시작할 때는 `--restart-cmd`를 사용합니다.
- [scripts/fetch-jibot-params-over-ssh.sh](scripts/fetch-jibot-params-over-ssh.sh)
  - 원격 JIBOT 장비의 `/usr/local/urobot/params/map/`, `/usr/local/urobot/params/routes/`를 로컬로 가져옵니다.
  - 기본 저장 위치는 `jibot/params/map/`, `jibot/params/routes/`입니다.
  - 기존 로컬 `map/routes`는 `jibot/backups/params/<timestamp>/`에 백업한 뒤 새 파일로 덮어씁니다.
- [scripts/change-jibot-network-over-ssh.sh](scripts/change-jibot-network-over-ssh.sh)
  - 로봇 온보드 PC의 `wlan0` IP/서브넷/게이트웨이(netplan)와 Wi-Fi SSID/PSK(wpa_supplicant)를 바꾸고 적용합니다.
  - `--wifi-select`를 쓰면 원격 `wlan0`에서 `iw`로 Wi-Fi 목록을 읽고 선택한 뒤 비밀번호를 숨김 입력합니다.
  - 항상 백업을 만들고, 쓰기 전에 실제 diff를 보여주며, IP 변경으로 SSH가 끊겨도 적용이 끝나도록 detached로 실행합니다.
- [scripts/update-jibot-adapter-config.sh](scripts/update-jibot-adapter-config.sh)
  - 어댑터 `config.toml`의 영상 URL을 로컬 리포 및/또는 원격 로봇에서 바꿉니다.
  - 로봇별 `vehicle_ip`는 `config/robots.hcl`에서 관리합니다.
  - 쓰기 전에 diff를 보여주고 항상 백업을 만듭니다.

### Config / WebUI Rule

새 운영 설정을 추가할 때는 기본적으로 `config/config.toml`에 TOML scalar로 넣고
dataclass에 로드되게 만든다. 그러면 WebUI `/config`에 자동 노출된다. 운영자가
WebUI에서 이해할 수 있어야 하는 값은 `core/configio.py`의 field description도
같이 추가한다. 구조화 데이터가 꼭 필요해서 리스트/배열/`[[table]]`을 쓰는 경우에는
WebUI에서 읽기전용으로 보이므로, 문서에 직접 편집 절차를 남긴다.

로봇 SSH 접속 방법(특히 레거시 크립토가 필요한 `192.168.3.223`)과 네트워크 구성은
[docs/reference/jibot-onboard-access.md](docs/reference/jibot-onboard-access.md)를 참고하세요.

GUI 없이 터미널로 장비의 Wi-Fi를 켜고 접속하는 절차(`nmcli` / netplan + `wpa_supplicant`)는
[docs/manual/ubuntu-wifi-cli.md](docs/manual/ubuntu-wifi-cli.md)를 참고하세요.

Basic usage:

```bash
scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.10
scripts/fetch-jibot-params-over-ssh.sh ucore@192.168.3.10
```

## Camera / Video to FMS

JIBOT camera/video integration and setup moved to
[jibot-client/README.md](jibot-client/README.md#camera--video-to-fms).  
Design note: [jibot-client/docs/jibot-video-to-fms-design.md](jibot-client/docs/jibot-video-to-fms-design.md), topic map:
[jibot-client/docs/jibot-camera-topics.md](jibot-client/docs/jibot-camera-topics.md).

## Hexplorer Adapter

The Hexplorer integration should run on the robot onboard Ubuntu 22.04 / ROS2 Humble system.

Recommended architecture:

```text
ACS / Fleet Manager
  <-> VDA5050 MQTT
adaptor/main_hexplorer.py
  <-> ROS2 topics
Hexplorer onboard control nodes
```

ACS should continue to use VDA5050. The adapter converts VDA5050 instantActions into Hexplorer ROS2 topic messages. Initial support is limited to instantActions; VDA5050 order execution is deferred until Hexplorer waypoint/navigation topics and map semantics are confirmed on the onboard robot.

Basic onboard setup:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
cd adaptor
PYTHONPATH=../hexplorer-client/src python3 main_hexplorer.py
```

SSH copy helpers:

```bash
# Upload adapter + hexplorer-client to the onboard robot
scripts/update-hexplorer-adapter-over-ssh.sh robot@192.168.12.1

# Fetch mapping record/result files from the onboard robot
scripts/fetch-hexplorer-maps-over-ssh.sh robot@192.168.12.1

# Fetch specific items from a specific remote directory
scripts/fetch-hexplorer-maps-over-ssh.sh \
  robot@192.168.12.1 \
  hexplorer/maps \
  /home/robot/dobot_hex_mapping/record \
  run001 run002
```

Supported initial instantActions:

- `standUp`: sends the configured RobotCommand target-state field with `hexplorer.stand_up_state`.
- `standDown`: sends the configured RobotCommand target-state field with `hexplorer.stand_down_state`.
- `walkMode`: sends the configured RobotCommand target-state field with `hexplorer.walk_mode_state`.
- `stop`: publishes zero `/vel_cmd`.
- `velocity`: publishes `/vel_cmd` with `x`, `y`, `yaw`.
- `syncHexplorerMap`: copies selected mapping output directories with backup.
- `getCameraInfo`: logs the latest ROS2 `CameraInfo` snapshot.

Before hardware motion testing, fill [docs/reference/hexplorer-ros2-interface.md](docs/reference/hexplorer-ros2-interface.md) from the onboard robot and confirm the `[hexplorer]` field mapping in [adaptor/config/config.toml](adaptor/config/config.toml).
