# JIBOT Adapter SSH Update Manual

이 문서는 로컬 PC에서 JIBOT 온보드 PC로 adapter 파일을 배포하는 절차를 설명한다.
대상 스크립트는 `scripts/update-jibot-adapter-over-ssh.sh`이다.

## robots.toml -> robots.hcl 마이그레이션 (일회성)

이 버전부터 어댑터는 `config/robots.hcl`만 읽는다. 변환해야 하는 파일은
로컬 repo가 아니라 **로봇 위의 `~/adaptor/config/robots.toml`**이다(원격
adaptor 경로가 기본값 `~/adaptor`가 아니면 그 경로 기준). 이 repo에는
`adaptor/config/robots.toml`이 이미 삭제되어 없다.

변환기(`scripts/convert-robots-toml-to-hcl.py`)는 repo root `scripts/`에 있고,
이 디렉터리는 업데이트할 때마다 로봇으로 그대로 올라간다(`LOCAL_SCRIPTS_DIR`).
그래서 코드만 먼저 올린 다음 로봇 위에서 변환기를 직접 돌리는 경로를 쓴다.

1. 코드를 먼저 반영한다. `--clean-remote`는 쓰지 않는다(설정 파일 보존).

    ```bash
    scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.222
    ```

2. 로봇 위에서 변환한다. 원격 adaptor 경로가 `~/adaptor`가 아니면 아래 `cd`
   경로를 `--remote-dir`에 준 값으로 바꾼다.

    ```bash
    ssh ucore@192.168.3.222 \
      'cd ~/adaptor && python3 scripts/convert-robots-toml-to-hcl.py config/robots.toml -o config/robots.hcl'
    ```

3. 결과를 확인한다. 주석은 변환되지 않으므로 `config/robots.hcl.example`을
   참고해 필요하면 손으로 정리한다.

    ```bash
    ssh ucore@192.168.3.222 'cat ~/adaptor/config/robots.hcl'
    ```

4. 서비스를 재시작해 반영한다.

    ```bash
    ssh ucore@192.168.3.222 'sudo systemctl restart amr-adaptor.service'
    ```

5. robots.hcl로 정상 기동하는 것을 확인한 뒤에만 로봇 위의 `robots.toml`을
   지운다. 이 스크립트는 `robots.toml`이 남아 있어도 어댑터 기동에는 영향을
   주지 않지만(어댑터는 robots.hcl만 읽는다), `--clean-remote`가 이 파일을
   지우지 않고 한 릴리스 동안 보존하도록 만들어 뒀다(마이그레이션 도중
   실수로 유일한 사본을 날리지 않기 위해서다). 확인이 끝나면 직접 지운다.

    ```bash
    ssh ucore@192.168.3.222 'rm -f ~/adaptor/config/robots.toml'
    ```

## 1. 기본 사용

repo root에서 실행한다.

```bash
scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.222
```

원격 adaptor 경로가 `~/adaptor`가 아니면 host 앞에 `--remote-dir DIR`로 지정한다.

```bash
scripts/update-jibot-adapter-over-ssh.sh --remote-dir /home/ucore/adapter ucore@192.168.3.222
```

옵션 전체 목록은 다음 명령으로 확인한다.

```bash
scripts/update-jibot-adapter-over-ssh.sh --help
```

## 2. 설정 파일 처리 기준

업데이트 스크립트는 코드와 설정 파일을 분리해서 처리한다.

| 파일 | 기본 동작 | 덮어쓰기 옵션 |
| --- | --- | --- |
| `config/config.toml` | 원격 파일이 있으면 보존, 없으면 로컬 기본 파일 생성 | `--config-toml-mode overwrite` |
| `config/robots.hcl` | 원격 파일이 있으면 보존, 없으면 만들지 않음 | `--robots-hcl-mode overwrite` |
| `config/extensions.hcl` | 원격 파일이 있으면 현장 튜닝을 보존, 없으면 로컬 기본 파일 생성 | `--extensions-hcl-mode overwrite` |
| `config/recipes.hcl` | 선택 파일. 원격 파일이 있으면 보존, 없으면 로컬 파일로 생성 | `--recipes-hcl-mode overwrite` |

`config.toml`은 어댑터 코어 설정, `robots.hcl`은 로봇 identity·주소,
`extensions.hcl`은 PIO/EZI/에어샤워/엘리베이터와 액션 모듈 설정을 담는다.
`extensions.hcl`은 기동에 필수이며, 기본값 `keep`은 기존 원격 파일을 덮어쓰지 않는다.
`recipes.hcl`은 선택이며, 존재할 때는 현장 workflow로 보고 보존한다.

**새 recipe를 올릴 때는 두 파일을 함께 올린다.** recipe는 `signal = "elevatorOpen"`
처럼 이름으로 점을 지목하고 그 이름은 `extensions.hcl`의 `output_signals`가 푼다.
recipes.hcl만 덮어쓰면 로봇의 옛 extensions.hcl에 그 이름이 없어 실행 시
`PIO signal "elevatorOpen" is not declared`로 실패한다.

```bash
scripts/update-jibot-adapter-over-ssh.sh --configure-device ucore@192.168.3.222
```

`--configure-device`는 두 파일을 함께 덮는 한 방 스위치이며, 덮기 전에 원격
원본을 `config/extensions.hcl.bak-<날짜시각>` / `config/recipes.hcl.bak-<날짜시각>`로
남긴다. `--extensions-hcl-mode overwrite --recipes-hcl-mode overwrite`를 직접 주는
것과 같다.

단, `extensions.hcl` 덮어쓰기는 그 로봇에서 손으로 맞춘 핀·타이밍까지 로컬 값으로
바꾼다. **로봇마다 다른 값은 `config/robots.hcl`에 두면 덮어써도 살아남는다** —
robot 블록 안에 extensions.hcl과 같은 문법으로 적으면 그 로봇에서만 이긴다.

```hcl
robot "HN-SH6-TR-002" {
  vehicle_ip = "10.8.8.8"

  extension "pio" {
    pio_serial_port = "/dev/ttyUSB0"   # PIO 변환기가 꽂힌 포트
    vehicle_num     = "AMR002"         # 설비에 전달할 이 로봇의 식별자
  }
  extension "ezi" {
    clamp_position   = 30000           # 이 축의 실측값
    unclamp_position = -20000
  }
}
```

`enabled = false`를 적으면 그 블록은 이 기계의 fleet에서 빠진다(기동·목록·단일/다중
판정 모두). 현장 로봇을 전부 한 파일에 적어 두고 기계마다 자기 블록만 켜면
`robots.hcl`도 공통본으로 배포할 수 있다. 켜진 블록이 하나도 없으면 어댑터는
조용히 아무것도 띄우지 않는 대신 기동을 멈춘다.

선택지를 실행 중에 고르려면 `ask`를 쓴다.

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --config-toml-mode ask \
  --robots-hcl-mode ask \
  ucore@192.168.3.222
```

## 3. robots.hcl 배포

원격의 `config/robots.hcl`을 로컬 파일로 덮어써야 할 때만 명시한다.

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --robots-hcl-mode overwrite \
  ucore@192.168.3.222
```

기본값은 `keep`이므로 실수로 로봇별 현장 설정을 덮어쓰지 않는다.

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --robots-hcl-mode keep \
  ucore@192.168.3.222
```

## 4. 업데이트 후 서비스 재시작

업데이트만 하면 실행 중인 systemd 서비스에는 즉시 반영되지 않는다.
JIBOT full-stack daemon은 원격 adapter 폴더에서 `scripts/setup-adaptor-service.sh --jibot`으로 설치한다.
파일 업로드 후 legacy adapter 유닛과 설치된 WebUi를 함께 다시 불러오려면 `--restart`를 지정한다.
`amr-xboxdrv.service`가 설치돼 있으면 함께 멈췄다 다시 시작한다. joystick bridge는
업로드가 덮어쓰는 `scripts/amr-xboxdrv-run.sh`를 그대로 실행하므로, 실행 중에 파일이
바뀌면 셸이 자기 스크립트를 잘못 읽는다.

전체 코드를 표준 원격 경로에 반영하고 full-stack을 다시 불러오는 기본 명령:

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --remote-dir /home/ucore/adapter \
  --restart \
  ucore@192.168.3.222
```

`--restart-cmd`는 일부 유닛만 의도적으로 다시 불러올 때 사용한다. WebUi 변경이 없는
2대 이상 fleet에서 특정 instance만 재시작:

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --restart-cmd "sudo systemctl restart 'amr-adaptor@HN-SH6-TR-001.service'" \
  ucore@192.168.3.222
```

화면 HTML, action panel, WebUi control/test, MQTT live 표시, camera 링크도 변경됐다면
필요한 adapter instance와 `amr-webui.service`를 같이 지정한다.

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --restart-cmd "sudo systemctl restart 'amr-adaptor@HN-SH6-TR-001.service' amr-webui.service" \
  --remote-dir /home/ucore/adapter \
  ucore@192.168.3.222
```

## 5. 정리 업로드

원격에 오래된 adapter 관리 파일이 남아 있을 수 있으면 `--clean-remote`를 사용한다.
가상환경과 로그는 지우지 않는다. `config/config.toml`, `config/robots.hcl`, `config/extensions.hcl`, 선택적인 `config/recipes.hcl` 모두 각 mode 옵션에 따라 처리되고, 기본값은 넷 다 기존 원격 파일 보존이다.
action module/panel은 adaptor tar에 이미 포함된다. 별도 복사가 아니라 `--clean-remote`로 stale source를
정리하고 `--restart`로 실행 중인 adapter/WebUi를 다시 불러오는 것이 필요한 운영 동작이다.

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --clean-remote \
  ucore@192.168.3.222
```

## 6. 업데이트 확인

업데이트 후 원격 장비에서 상태와 로그를 확인한다.

```bash
ssh ucore@192.168.3.222
cd ~/adapter
sudo systemctl status amr-adaptor.service --no-pager
sudo journalctl -u amr-adaptor.service -n 80 --no-pager
```

WebUi를 같이 운영한다면 WebUi도 재시작한다.

```bash
sudo systemctl restart amr-webui
sudo systemctl status amr-webui --no-pager
```

## 7. 자주 쓰는 조합

코드만 업데이트하고 현장 설정 보존:

```bash
scripts/update-jibot-adapter-over-ssh.sh ucore@192.168.3.222
```

코드와 `robots.hcl`을 같이 반영하고 adapter와 설치된 WebUi 재시작:

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --robots-hcl-mode overwrite \
  --restart \
  ucore@192.168.3.222
```

코드, `config.toml`, `robots.hcl`을 모두 로컬 기준으로 반영:

```bash
scripts/update-jibot-adapter-over-ssh.sh \
  --config-toml-mode overwrite \
  --robots-hcl-mode overwrite \
  ucore@192.168.3.222
```

## 8. 실패 시 확인

SSH 연결 실패:

```bash
ssh ucore@192.168.3.222
```

업데이트 후 서비스가 뜨지 않음:

```bash
sudo systemctl status amr-adaptor.service --no-pager
sudo journalctl -u amr-adaptor.service -n 120 --no-pager
```

`robots.hcl`이 반영되지 않음:

```bash
cat ~/adapter/config/robots.hcl
```

원격 파일을 덮어쓴 업데이트였는지 확인한다. 기본 업데이트는 원격 `robots.hcl`을 보존한다.
