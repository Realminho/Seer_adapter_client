# 8BitDo 조이스틱 AMR 주행 설정

이 문서는 8BitDo Ultimate 2 또는 Micro 한 대를 JIBOT 수동주행 입력으로 활성화하는 운영
절차다. 두 컨트롤러를 동시에 주행기로 활성화할 수 없으며, 설정 변경 후 adapter를 재시작해야
한다.

## 현재 runtime 지원 범위

| 컨트롤러 | 입력 | 동작 |
|---|---|---|
| Ultimate 2 | R2 / L2 | 아날로그 전진 / 후진 |
| Ultimate 2 | 왼쪽 스틱 X | 아날로그 좌우 회전 |
| Ultimate 2 | R1 | 즉시 정지 |
| Micro | D-pad 위/아래 | 디지털 전진 / 후진 |
| Micro | D-pad 왼쪽/오른쪽 | 디지털 좌우 회전 |
| Micro | R | 즉시 정지 |

| Ultimate 2 | D-pad 방향 + A/B/X/Y | slot 1~16 action (`actions_enabled = true`일 때) |
| Ultimate 2 | − / + | 주행 속도 배율 단계 조절 |

장치 분리, 입력 오류, watchdog timeout, 중립 및 위험 조합은 `UmStop`으로 처리한다.

slot action은 `actions_enabled`로 묶여 있고 기본값이 `false`다. slot은 모터·클램프·충전
같은 실제 동작을 실행하므로 로봇마다 slot 목록을 검토한 뒤 켠다. 주행 중(트리거를 당기고 있는
동안)에는 slot이 무시된다 — 조종 중인 로봇에 다른 동작을 걸지 않기 위해서다. D-pad를 대각선으로
둔 상태도 어느 방향인지 확정할 수 없으므로 무시한다.

Micro는 D-pad 자체가 주행 입력이라 같은 chord 방식을 쓸 수 없다. Micro의 나머지 버튼 action은
여전히 실행 루프에 연결되어 있지 않다.

## 1. 실제 적용되는 설정 파일 확인

기본 파일은 다음 두 개다.

```text
조이스틱 장치·키 설정: adaptor/config/extensions.hcl
주행 속도·watchdog:    adaptor/config/jibot-config.toml
```

`adaptor/config/robots.hcl`의 해당 `robot` 블록에 다음처럼 `extensions`가 지정돼 있으면 기본
`extensions.hcl` 대신 그 파일을 수정한다. 상대 경로 기준은 `robots.hcl`이 있는 디렉터리다.

```hcl
robot "HN-SH6-TR-001" {
  extensions = "HN-SH6-TR-001-extensions.hcl"
}
```

저장소 최상위의 `extensions.hcl`은 기본 runtime 경로가 아니다. 별도 실행 옵션으로 해당 파일을
지정하지 않았다면 `adaptor/config/extensions.hcl`을 수정한다.

## 2. 공통 주행 속도 설정

`adaptor/config/jibot-config.toml`의 기존 `[manual_control]` 블록을 수정한다. 같은 이름의 블록을
추가로 만들면 안 된다.

최초 시험 권장값:

```toml
[manual_control]
enabled = true
drive_trans = 50
drive_rot = 10
drive_speed = 50
drive_lat = 0
heartbeat_ms = 300
watchdog_ms = 800
step_distance_mm = 500
default_move_speed = 100
move_obs_avoid_dist = 1000
move_side_avoid_dist = 50
```

- `drive_trans`: 최대 전진·후진 명령 크기
- `drive_rot`: 최대 좌우 회전 명령 크기
- `drive_speed`: JIBOT `UmDrive` 속도 (최대치)
- `heartbeat_ms`: 누르는 동안 명령을 재전송하는 간격
- `watchdog_ms`: 마지막 명령 이후 자동 정지 시간. `heartbeat_ms`보다 크게 설정한다.

Ultimate 2는 trigger와 stick 입력 비율을 `drive_trans`, `drive_rot`, `drive_speed` 셋 다에
곱한다. `drive_speed`에는 trigger와 stick 중 **더 큰 쪽**을 쓴다 — 제자리 회전에는 당길 trigger가
없고, 주행 중 살짝 꺾은 stick이 trigger가 요구한 속도를 깎아내리면 안 되기 때문이다. Micro는
D-pad 방식이므로 설정한 값을 그대로 사용한다.

## 3-A. Ultimate 2 활성화

`adaptor/config/extensions.hcl`의 기존 `joystick "ultimate2"` 블록에서 다음 값들을 확인한다.
블록을 하나 더 추가하지 말고 기존 블록을 수정한다.

```hcl
joystick "ultimate2" {
  enabled              = true
  ultimate2_enabled    = true
  micro_enabled        = false

  vendor_id            = "2dc8"
  product_id           = "310b"
  device_name_contains = "8BitDo"

  forward_axis         = 5 # R2
  reverse_axis         = 2 # L2
  steering_axis        = 0 # left stick X
  stop_button          = 5 # R1
  trigger_threshold    = 0.1
  steering_deadzone    = 0.15
  joystick_reconnect_sec = 1.0

  # D-pad+ABXY slot action. 기본값 false.
  actions_enabled      = false

  # joydev가 없는 커널에서 xboxdrv가 만드는 evdev fallback.
  xboxdrv_device_name        = "Xbox Gamepad (userspace driver)"
  xboxdrv_forward_axis_code  = 9  # ABS_GAS / R2
  xboxdrv_reverse_axis_code  = 10 # ABS_BRAKE / L2
  xboxdrv_steering_axis_code = 0  # ABS_X / left stick X
  xboxdrv_stop_button_code   = 311 # BTN_TR / R1
  xboxdrv_dpad_x_code        = 16  # ABS_HAT0X
  xboxdrv_dpad_y_code        = 17  # ABS_HAT0Y
  xboxdrv_a_button_code      = 304 # BTN_A
  xboxdrv_b_button_code      = 305 # BTN_B
  xboxdrv_x_button_code      = 307 # BTN_X
  xboxdrv_y_button_code      = 308 # BTN_Y
  xboxdrv_speed_down_button_code = 314 # BTN_SELECT
  xboxdrv_speed_up_button_code   = 315 # BTN_START
  xboxdrv_receiver_guard     = true

  # Micro 값은 비활성 상태에서도 그대로 둔다.
  micro_device_name       = "Pro Controller"
  micro_dpad_x_code       = 0
  micro_dpad_y_code       = 1
  micro_neutral_threshold = 1000
  micro_stop_button       = 311
  micro_reconnect_sec     = 1.0
}
```

Ultimate 2는 전용 2.4GHz receiver 연결을 권장한다. 다음 결과가 `310b`와 `/dev/input/jsN`을
표시해야 한다.

```bash
sudo python3 scripts/joystick_input_test.py --list
```

`2dc8:3109`만 보이면 receiver만 있고 컨트롤러 본체는 연결되지 않은 상태다. 컨트롤러를 켜거나
깨운 뒤 `310b`로 바뀌는지 확인한다.

## 3-B. Micro 활성화

같은 `joystick "ultimate2"` 블록에서 활성 컨트롤러 두 값만 반대로 설정한다. 블록 이름은 현재
설정 스키마의 profile 이름이며 Micro를 사용할 때도 블록을 새로 만들지 않는다.

```hcl
joystick "ultimate2" {
  enabled           = true
  ultimate2_enabled = false
  micro_enabled     = true

  micro_device_name       = "Pro Controller"
  micro_dpad_x_code       = 0
  micro_dpad_y_code       = 1
  micro_neutral_threshold = 1000
  micro_stop_button       = 311 # R
  micro_reconnect_sec     = 1.0

  # Ultimate 2 값은 비활성 상태에서도 그대로 둔다.
  vendor_id            = "2dc8"
  product_id           = "310b"
  device_name_contains = "8BitDo"
}
```

Bluetooth 연결 상태를 확인한다.

```bash
bluetoothctl devices Connected
sudo python3 scripts/joystick_input_test.py --list
```

정상 연결이면 `Pro Controller` 장치가 표시되며 runtime은 재연결 때 변경되는 `eventN` 번호를
이름과 USB modalias(`057e:2009`)로 다시 찾는다.

## 3-C. slot action과 속도 버튼 (Ultimate 2 전용)

기본값은 꺼져 있다. slot은 모터·클램프·충전 같은 실제 동작을 실행하므로, 이 로봇의 slot 목록을
확인한 뒤에만 켠다.

```hcl
joystick "ultimate2" {
  actions_enabled = true
}
```

slot 번호는 D-pad 방향과 A/B/X/Y 조합으로 고정돼 있다.

| slot | 조합 | slot | 조합 |
|---|---|---|---|
| 1~4 | Up + A/B/X/Y | 9~12 | Down + A/B/X/Y |
| 5~8 | Right + A/B/X/Y | 13~16 | Left + A/B/X/Y |

각 slot에 붙는 action은 같은 파일의 `joystick_action` 블록에서 정한다. `enabled = false`인 slot과
`joystick_action`이 없는 slot은 눌러도 아무 일도 하지 않고 로그만 남는다.

```hcl
joystick_action "6" { target = "extension"
  action = "unclamp" }
```

`action` 이름이 registry에 없으면 **adapter가 기동을 거부한다.** 오타를 실차에서 발견하지 않도록
일부러 부팅 시점에 막는다.

동작 규칙:

- **주행 중에는 slot이 무시된다.** 트리거를 놓고 정지한 뒤에 눌러야 한다.
- D-pad 대각선은 방향을 확정할 수 없으므로 무시한다.
- 버튼을 뗄 때가 아니라 누를 때 한 번 실행된다.
- `−`/`+`는 주행 속도 배율을 `speed_step_percent`만큼 내리고 올린다. 범위는
  `speed_min_percent`~`speed_max_percent`이고 어댑터가 뜰 때 `speed_start_percent`에서
  시작한다. 배율은 `drive_trans`·`drive_rot`·`drive_speed`에 똑같이 곱해지므로 직진·회전
  비율은 그대로다. **`actions_enabled`와 무관하게 동작한다** — 그 스위치는 모터·클램프를
  실제로 돌리는 slot 때문에 있는 것이고, 이미 하고 있는 주행의 크기를 바꾸는 것은 그 범주가
  아니다. 주행 중에 눌러도 heartbeat를 기다리지 않고 즉시 반영된다.
- 조이스틱은 더 이상 음량을 바꾸지 않는다. 음량은 WebUI의 `setSoundVolume`으로 조절한다.

실행된 action은 FMS가 보내온 것과 같은 instant-action 경로를 그대로 탄다. 따라서 order/작업 중
차단 규칙과 상태 보고가 동일하게 적용되며, `instantActionStates`에 `joystick-<action>-<n>` 형태의
id로 나타난다.

Micro는 D-pad가 주행 입력이라 같은 chord를 쓸 수 없다. Micro의 나머지 버튼 action은 아직 실행
루프에 연결되어 있지 않다.

## 4. 입력 장치 권한

터미널에서 `sudo`로 테스트한 것과 systemd adapter가 입력을 읽는 것은 별개다. 먼저 서비스
실행 계정을 확인한다.

```bash
systemctl show amr-adaptor.service -p User -p Group
```

`User=`가 비어 있으면 일반적으로 root로 실행된다. 별도 계정이 표시되면 그 계정을 `input`
그룹에 추가한다.

```bash
sudo usermod -aG input <서비스계정>
getent group input
```

서비스 계정 변경을 반영하려면 서비스를 재시작해야 한다. 터미널에서 직접 실행한다면 현재
사용자를 추가한 뒤 로그아웃·로그인한다.

```bash
sudo usermod -aG input "$USER"
```

## 4-A. 커널 드라이버 준비 상태 확인

다음 명령은 커널 설정, 모듈 파일, 현재 USB 바인딩과 `/dev/input` 노드를 한 번에 확인한다.

```bash
sudo python3 scripts/joystick_input_test.py --driver-check
```

Ultimate 2의 2.4GHz receiver는 USB interface 0을 XInput 장치로 제공한다. Linux에서는 보통
`xpad`가 이 interface를 입력 장치로 만들고 `joydev`가 현재 runtime이 읽는 `/dev/input/jsN`을
제공한다. 따라서 `lsusb`에 receiver가 보이는 것만으로는 준비가 끝난 것이 아니다.

```text
Ultimate 2 runtime: READY
```

가 나와야 기본 js backend를 사용할 수 있다. `joydev`가 없는 대신 `uinput`과 xboxdrv를 사용할
수 있는 커널은 아래 evdev fallback을 사용할 수 있다.

```bash
sudo xboxdrv --device-by-id 2dc8:310b --type xbox360 --silent
```

xboxdrv가 `Xbox Gamepad (userspace driver)`라는 가상 `/dev/input/eventN`을 만들면 runtime은
정상 Ultimate 2 `/dev/input/jsN`을 우선 찾고, 없을 때 위 설정의 장치명과 evdev code로 가상
장치를 자동 선택한다. xboxdrv는 adapter보다 먼저 실행되고 계속 살아 있어야 한다.

### receiver의 두 가지 USB product id

Ultimate 2 receiver는 controller 연결 상태에 따라 **다른 USB 장치로 재열거된다**.

| controller 상태 | `lsusb -d 2dc8:` |
| --- | --- |
| 꺼짐 / 잠듦 | `2dc8:3109` |
| 연결됨 | `2dc8:310b` |

xboxdrv가 쓰는 `310b`는 controller가 깨어 있을 때만 존재한다. 그런데 xboxdrv는 시작할 때 USB
handle을 한 번만 잡고 다시 잡지 않으며, 그 장치가 사라져도 **스스로 종료하지 않는다**. 그래서
controller가 잠들면 xboxdrv는 살아 있지만 이벤트를 하나도 만들지 못하고, 가상
`Xbox Gamepad (userspace driver)`만 껍데기로 남는다. adapter는 이 껍데기에 정상적으로 붙어
`connected via xboxdrv evdev` 로그까지 찍으므로, 로그만 보고 정상이라고 판단하면 안 된다.

프로세스 생존을 감시하는 `Restart=always`로는 이 상태를 감지할 수 없다. `310b`의 존재 자체를
감시해야 하며, 그 역할을 `scripts/amr-xboxdrv-run.sh`가 한다. 이 스크립트는 `310b`가 나타날
때까지 기다렸다가 xboxdrv를 실행하고, `310b`가 사라지면 xboxdrv를 정리한 뒤 종료한다. 종료
후에는 unit의 `Restart=always`가 다시 대기 상태로 돌려놓는다. 즉 controller를 껐다 켜는 모든
사이클에서 bridge가 자동 복구된다.

### 껍데기 장치와 주행 정지 (`xboxdrv_receiver_guard`)

supervisor가 껍데기를 걷어내기까지는 poll 간격만큼 시간이 걸린다. **그 사이에 트리거를 당긴 채
controller가 잠들면 로봇이 계속 굴러간다.** 가상 장치는 그대로 열려 있고 이벤트만 끊기는데,
runtime은 heartbeat마다 마지막 축 값으로 `UmDrive`를 다시 보내기 때문이다. adapter의
`watchdog_ms`는 매 `UmDrive`마다 다시 무장되므로 이 상황에서는 작동하지 않는다.

그래서 두 겹으로 막는다.

1. `scripts/amr-xboxdrv-run.sh`의 poll 간격 기본값은 `0.25`초다. 이 값이 곧 노출 시간이므로
   `AMR_XBOXDRV_POLL_SEC`로 올릴 때는 그만큼 위험이 늘어난다는 뜻이다.
2. runtime은 evdev fallback으로 주행하는 동안 heartbeat마다 `vendor_id:product_id`가 USB 버스에
   아직 있는지 다시 확인하고, 없으면 즉시 정지하고 중립까지 latch한다(`xboxdrv_receiver_guard`,
   기본값 `true`).

“이벤트가 안 들어오면 정지”로는 이 문제를 풀 수 없다. **트리거를 일정하게 당기고 있어도 이벤트는
안 들어오기** 때문에, 무음을 신호로 삼으면 정상 주행이 끊긴다. 그래서 무음이 아니라 receiver의
존재 자체를 본다.

`/sys/bus/usb/devices`를 읽을 수 없는 호스트에서는 시작 시 경고를 남기고 가드를 끈다. 켜져 있는
동안 개별 장치를 읽지 못하면 “없음”으로 간주해 정지 쪽으로 실패한다.

### 설치

bridge는 정식 서비스 설치에 포함돼 있다. 로봇에서 아래를 실행하면 unit 설치, `input` 그룹
추가, enable, 재시작까지 함께 처리된다.

```bash
scripts/setup-adaptor-service.sh --jibot
```

`xboxdrv` 바이너리가 없으면 저장소에 번들된 오프라인 `.deb`를 먼저 설치한다. 로봇은
인터넷이 없어 apt로는 받을 수 없고, 업데이트마다 `scripts/`가 통째로 업로드되므로
`scripts/offline-debs/xboxdrv/`의 `.deb`가 함께 실려 온다. `apt-get` 폴백은 없다.

`dpkg -i`가 의존성 오류를 내면 빌드 머신에서 `scripts/fetch-offline-debs.sh`의
`PACKAGES` 목록에 그 패키지를 추가하고 다시 실행한 뒤 커밋·재배포한다. 자세한 내용은
[scripts/offline-debs/xboxdrv/README.md](../../scripts/offline-debs/xboxdrv/README.md)를
참고한다.

다음 두 경우에는 경고만 남기고 bridge를 건너뛴다.

- 커널에 `xpad`가 있다. bridge는 커널이 receiver를 직접 다루지 못할 때 쓰는 우회로인데,
  `xpad`가 있으면 같은 USB interface를 커널이 먼저 잡는다. 이 상태로 xboxdrv를 띄우면
  interface를 못 잡아 crash-loop 하거나, 반대로 뺏어 와서 runtime이 우선하는
  `/dev/input/jsN` 경로를 없앤다. 둘 다 원하는 결과가 아니다. `xpad` 때문에 건너뛸 때는
  오프라인 `.deb` 설치도 함께 건너뛴다 — 쓰지 않을 패키지를 로봇에 넣지 않는다. `xpad`가
  있는데도 이 receiver에는 bind되지 않는 것이 확인된 호스트라면 `--with-xboxdrv`로 강제
  설치한다. 이때는 이 게이트가 풀리므로 번들 `.deb`도 함께 설치한다.
- 번들 `.deb` 설치가 실패했거나, 설치 후에도 `xboxdrv` 바이너리가 없다. `dpkg -i`는
  의존성이 빠져도 패키지를 풀어 놓고 설정(configure)만 거부하므로, 실패해도
  `/usr/bin/xboxdrv`는 디스크에 남는다. 그래서 바이너리 존재만 보지 않고 `dpkg` 실패
  자체를 skip 조건으로 본다. 반쯤 설치된 바이너리로 unit을 띄우면 실행 즉시 링커 오류로
  (`libX11.so.6: cannot open shared object file`) `RestartSec=2`마다 crash-loop 한다.

joystick을 쓰지 않는 로봇에서 명시적으로 제외하려면 `--no-xboxdrv`를 준다. 이때는 오프라인
`.deb` 설치도 하지 않는다.

unit의 `ExecStart`는 배포된 `scripts/amr-xboxdrv-run.sh`를 가리키므로, 이후
`scripts/update-jibot-adapter-over-ssh.sh --restart`만으로 supervisor 수정본까지 반영된다.
이때 bridge도 함께 멈췄다 시작한다.

설치 후 서비스 제어는 다른 unit들과 같은 경로를 쓴다. bridge가 adapter보다 먼저 뜬다.

```bash
scripts/adaptor-services.sh restart
scripts/adaptor-services.sh status
```

`Before=amr-adaptor.service`는 두 서비스가 함께 부팅될 때 bridge를 먼저 시작하며, adapter도
장치명으로 계속 재탐색하므로 `eventN` 번호가 바뀌어도 상관없다.

controller를 켠 뒤 실제로 이벤트가 흐르는지는 가상 장치를 직접 읽어 확인한다. 연결 로그만으로는
위의 껍데기 상태와 구분되지 않는다.

```bash
sudo python3 - <<'PY'
import struct
fmt = struct.Struct("llHHi")
with open("/dev/input/event6", "rb", buffering=0) as f:
    for _ in range(20):
        _s, _u, t, c, v = fmt.unpack(f.read(fmt.size))
        if t in (1, 3):
            print(t, c, v)
PY
```

여기서 “AMR 커널 업체의 정확한 커널 모듈 패키지”란 8BitDo가 제공하는 별도 드라이버가 아니다.
AMR 제조사 또는 OS image 제작자가 해당 장비의 커스텀 커널과 같은 소스·설정·컴파일러로 빌드한
`*.ko` 파일 묶음을 뜻한다. Linux kernel module은 보통 실행 중인 kernel release/ABI와 맞아야
하므로 다른 Ubuntu PC의 `xpad.ko`를 복사하거나 임의의 `linux-modules-extra`를 설치하면 안 된다.

커널 xpad 없이 xboxdrv가 libusb로 receiver protocol을 해석하고 uinput evdev를 만들 수 있다.
이 방식도 USB/uinput 접근 권한, 제품·firmware별 protocol 호환성, xboxdrv 프로세스 수명주기를
별도로 관리해야 하므로 “어떤 Linux 장비에서나 무설치로 동작”하는 해법은 아니다.

호환성 관점에서 선택지는 다음과 같다.

1. `xpad`가 포함된 일반 PC/Ubuntu kernel 또는 AMR용 커널 모듈을 사용한다.
2. AMR 커널에 `CONFIG_JOYSTICK_XPAD`를 포함해 다시 빌드한다.
3. 표준 HID로 노출되는 다른 controller/연결 방식을 사용한다. 이 경우에도 해당 HID driver와
   evdev가 커널에 있어야 한다.
4. xboxdrv와 runtime의 evdev fallback을 사용한다. 현재 검증된 mapping은 R2=9, L2=10,
   left stick X=0, R1=311이다.

## 5. 재시작 및 로그 확인

실차에서는 바퀴를 띄우거나 충분한 안전 공간을 확보하고 비상정지 수단을 준비한 뒤 진행한다.

```bash
sudo systemctl restart amr-adaptor.service
sudo journalctl -u amr-adaptor.service -f
```

정상 연결 로그:

```text
[JOYSTICK ULTIMATE2] connected: /dev/input/js0
```

또는 xboxdrv fallback 로그:

```text
[JOYSTICK ULTIMATE2] connected via xboxdrv evdev: /dev/input/event5
```

또는:

```text
[JOYSTICK MICRO] connected: /dev/input/event18
```

`event18`과 `js0` 번호는 재연결할 때 바뀔 수 있으며 정상이다.

## 6. 저속 시험 순서

1. R1(Ultimate 2) 또는 R(Micro)을 눌렀을 때 정지하는지 확인한다.
2. 전진을 짧게 누르고 놓았을 때 즉시 정지하는지 확인한다.
3. 후진과 좌우 회전을 각각 확인한다.
4. 입력을 유지한 상태에서 컨트롤러 전원을 끄고 watchdog/분리 정지를 확인한다.
5. Ultimate 2는 R2와 L2를 동시에 눌렀을 때 정지하는지 확인한다.
6. Micro는 대각선 입력 후 D-pad를 완전히 놓기 전까지 정지 상태인지 확인한다.
7. 검증 후에만 `drive_trans`, `drive_rot`, `drive_speed`를 단계적으로 올린다.

## 7. 설정 오류 및 문제 해결

AMR 배포본에서 다음 오류가 발생하면 `scripts/`와 `utils/`가 같은 `adaptor` 디렉터리에 있는
평탄화 구조인데 구버전 테스트 스크립트를 사용 중인 것이다.

```text
ModuleNotFoundError: No module named 'utils'
```

최신 `scripts/joystick_input_test.py`는 저장소 구조(`adaptor/utils`)와 AMR 배포 구조(`utils`)를
모두 자동 탐지한다. 코드를 업데이트하기 전 임시로 실행해야 한다면 `~/adaptor`에서 다음처럼
Python 경로를 전달한다.

```bash
cd ~/adaptor
sudo env PYTHONPATH="$PWD" python3 ./scripts/joystick_input_test.py --list
```

두 드라이버를 모두 켜면 adapter가 시작을 거부한다.

```text
enable only one driving controller at a time
```

다음 로그는 실행 계정에 입력 권한이 없다는 뜻이다.

```text
[JOYSTICK ...] permission denied: /dev/input/...
```

서비스가 연결 로그 없이 대기하면 다음을 확인한다.

```bash
sudo python3 scripts/joystick_input_test.py --list
ls -l /dev/input/js* /dev/input/event* 2>/dev/null
sudo journalctl -u amr-adaptor.service -n 100 --no-pager
```

설정을 다시 끄려면 다음 세 값을 모두 false로 바꾸고 adapter를 재시작한다.

```hcl
enabled           = false
ultimate2_enabled = false
micro_enabled     = false
```
