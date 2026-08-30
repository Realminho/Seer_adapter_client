# Bluetooth/USB 조이스틱 연결 및 키 매핑 확인

이 절차는 조이스틱 입력값만 읽으며 AMR을 움직이지 않는다. 실제 이동 명령 연결 전 반드시
정지 버튼, 데드맨 스위치, 속도 제한을 별도로 설계하고 낮은 속도에서 검증한다.

실제 adapter runtime 활성화와 운영 설정은
[8BitDo 조이스틱 AMR 주행 설정](joystick-runtime-setup.md)을 따른다. 현재 runtime 지원 범위는
Ultimate 2의 R2/L2/왼쪽 스틱/R1/−/+ 및 Micro의 D-pad/R이다. 아래 +/−는 `actions_enabled`와
무관하게 항상 주행 속도 배율을 즉시 조절하고, 16-action 표(D-pad+A/B/X/Y)는
`actions_enabled = true`일 때만 action registry로 실제 dispatch된다.

## Ultimate 2 권장 연결 및 버튼 배치

AMR 운전에는 Ultimate 2 전용 2.4 GHz 동글을 사용한다. 이 동글은 Linux에서 USB 입력 장치로
보이므로 Bluetooth 컨트롤러가 없는 PC에서도 사용할 수 있다. `--device`를 생략하면 도구가
USB 연결 장치를 우선하며, 이름 또는 USB vendor ID(`2dc8`)가 8BitDo인 장치를 가장 먼저 고른다.

```bash
# 동글 연결 및 조이스틱 전원을 켠 뒤 자동 탐지
python3 scripts/joystick_input_test.py --list
python3 scripts/joystick_input_test.py \
  --mapping scripts/joystick-mapping.example.json
```

Xbox 배열 Ultimate 2의 권장 물리 배치는 다음과 같다.

| 물리 입력 | 권장 기능 | 이유 |
|---|---|---|
| R2 | `forward` | R2 압력으로 전진 속도 제어 |
| L2 | `reverse` | L2 압력으로 후진 속도 제어 |
| 왼쪽 스틱 좌/우 | `steering` | 전후 속도와 독립된 방향 변경 |
| 왼쪽 스틱 상/하 | 미사용 | 트리거 전후진과 충돌 방지 |
| R1 | `manual_stop` | 운전 중 빠르게 누를 수 있는 별도 정지 |
| + | 주행 속도 배율 증가 | `speed_step_percent`만큼 올림, 최대 `speed_max_percent`(기본 100%) |
| - | 주행 속도 배율 감소 | `speed_step_percent`만큼 내림, 최소 `speed_min_percent`(기본 20%) |

실제 AMR 제어에서는 R2/L2 값을 직접 적용한다. 두 trigger가 동시에 눌림, R1 입력, 동글 분리,
300~500ms 입력 timeout 시 항상 `manualStop`을 실행한다. 왼쪽 스틱 Y축은 무시하고 X축만
조향에 사용한다.

### Deadman과 브레이크의 의미

PL/PR/R4는 2.4 GHz X-input 모드에서 독립 Linux 입력으로 노출되지 않아 Deadman 및 motor
toggle 입력에서 제외한다. 따라서 별도 Deadman 버튼은 없으며 R1 정지, 입력 heartbeat timeout,
동글 분리 정지를 반드시 유지해야 한다. 독립 브레이크 해제 입력도 현재 배치에서는 사용하지
않는다. Motor ON/OFF는 D-pad 위+A/B, Dock 시작/종료는 D-pad 위+X/Y 조합으로 실행한다.

### D-pad + A/B/X/Y 16-action 권장 매핑

D-pad는 단독으로 동작하지 않고 누른 방향을 유지한 채 A/B/X/Y를 눌러 action을 선택한다.

| D-pad | A | B | X | Y |
|---|---|---|---|---|
| 위 | `enableMotor` | `disableMotor` | `startCharging` | `stopCharging` |
| 오른쪽 | `clamp` | `unclamp` | `clampOn` | `clampOff` |
| 아래 | `loading` | `stopLoading` | `unloading` | `stopUnloading` |
| 왼쪽 | `clampHome` | `clampStop` | `pioElevatorOpen1f` recipe | `pioElevatorClose1f` recipe |

이 표는 action 슬롯 설정이다. `actions_enabled = true`일 때 이 조합은 FMS instant action과
같은 경로로 action registry에 실제 dispatch되어 모터·Dock·recipe를 실행한다. 기본값인
`actions_enabled = false`에서는 조합을 눌러도 아무 일도 일어나지 않는다.

### 키와 1~16 action 설정 변경

운영 매핑의 원본은 `config/extensions.hcl`의 `joystick`과 `joystick_action` 블록이다. Python
코드를 수정하지 않고 실제 측정한 축·버튼 번호를 바꿀 수 있다.

```hcl
joystick "ultimate2" {
  enabled            = true
  forward_axis       = 5
  reverse_axis       = 2
  steering_axis      = 0
  dpad_x_axis        = 6
  dpad_y_axis        = 7
  a_button           = 0
  b_button           = 1
  x_button           = 2
  y_button           = 3
  speed_down_button  = 6
  speed_up_button    = 7
}
```

슬롯 번호와 물리 조합은 고정되어 있어 action만 바꾸면 된다.

```text
1~4   = D-pad 위    + A/B/X/Y
5~8   = D-pad 오른쪽 + A/B/X/Y
9~12  = D-pad 아래  + A/B/X/Y
13~16 = D-pad 왼쪽  + A/B/X/Y
```

Extension action은 `target="extension"`, recipe는 `target="recipe"`로 표시한다. 실행 경로는
둘 다 action registry이므로 `action`에는 WebUI/recipe에서 사용하는 동일한 actionType을 쓴다.
필요하면 `parameters`도 함께 고정할 수 있다.

```hcl
joystick_action "5" {
  target     = "extension"
  action     = "ezioWriteOut"
  parameters = { index = 3, state = "on" }
}

joystick_action "16" {
  target = "recipe"
  action = "pioElevatorClose1f"
}
```

슬롯은 1~16만 허용되고 중복 슬롯, 잘못된 target, 비어 있는 action은 설정 로딩 단계에서
거부한다. `joystick.enabled=true`일 때 존재하지 않거나 비활성화된 extension/recipe action을
참조하면 adapter 시작도 중단되므로 현장에서 눌렀을 때 아무 반응이 없는 상태로 남지 않는다.

예제 JSON의 번호는 일반적인 X-input 배열을 시작점으로 제공할 뿐 확정값이 아니다. 아래의
원시 입력 시험에서 실제 번호를 확인한 후 현장용 매핑 파일로 복사해 고정한다.

## 1. Bluetooth 지원 확인

### 8BitDo Micro

Micro의 Switch 모드 Bluetooth 연결은 Linux에서 `Pro Controller`(`057e:2009`)라는 evdev
장치로 보이며 환경에 따라 `/dev/input/js*`는 생성되지 않을 수 있다. 재연결로 바뀌는
`eventN` 번호 대신 다음 명령으로 자동 선택한다.

```bash
sudo python3 scripts/joystick_input_test.py --event-role micro
```

Micro는 D-pad를 디지털 주행 입력으로 사용한다. 위=전진, 아래=후진, 왼쪽=좌회전,
오른쪽=우회전이며 D-pad를 놓아 중립으로 돌아오면 `manualStop`을 실행한다. 대각선 입력은
허용하지 않고 정지 처리한다. 측정한 매핑은
`scripts/joystick-micro-mapping.example.json`에 기록돼 있다. 이 JSON은 현재 runtime이 직접
읽는 운영 설정이 아니라 측정값 참고 파일이다.

측정된 Switch Bluetooth 모드 입력은 다음과 같다.

| 입력 | evdev 입력 |
|---|---|
| L / L2 / R2 / R | key 310 / 312 / 313 / 311 |
| 위 / 오른쪽 / 아래 / 왼쪽 | abs 1=-32767 / abs 0=32767 / abs 1=32767 / abs 0=-32767 |
| − / + / 별 / 하트 | key 314 / 315 / 309 / 316 |
| X / Y / A / B | key 307 / 308 / 305 / 304 |

Micro의 runtime 버튼 기능은 R=`manual_stop`까지만 연결돼 있다. −/+, L/L2/R2, 별, 하트,
A/B/X/Y는 이 표에 측정 번호만 기록될 뿐, Micro는 D-pad 이외의 버튼을 action 실행에 연결하지
않는다. Ultimate 2의 −/+ 주행 속도 조절과 D-pad+A/B/X/Y slot action은 이것과 별개이며
연결돼 있다(위 Ultimate 2 절 참고).

### Micro 실제 주행 서비스 활성화

Micro 주행은 adapter 프로세스 안에서 기존 `manualDrive`와 동일한 속도·watchdog 설정을
사용한다. 먼저 simulator 또는 바퀴를 띄운 상태에서 `adaptor/config/extensions.hcl`의 두 값을
켠다.

```hcl
joystick "ultimate2" {
  enabled       = true
  micro_enabled = true
}
```

adapter 실행 계정에는 `/dev/input/event*` 읽기 권한이 필요하다. 권한 오류가 표시되면 해당
계정을 `input` 그룹에 추가한 뒤 다시 로그인하거나 서비스를 재시작한다.

```bash
sudo usermod -aG input "$USER"
```

서비스는 Micro를 재연결할 때 바뀐 `eventN` 번호를 자동으로 다시 찾는다. D-pad를 누르는 동안
`manual_control.heartbeat_ms` 간격으로 `UmDrive`를 재전송하며, 중립·대각선·R 버튼·장치 분리·
입력 오류에는 `UmStop`을 보낸다. active order 또는 loading/unloading 작업 중에는 주행을
거부하고 D-pad를 완전히 놓았다가 다시 눌러야 한다.

### Ultimate 2 실제 주행 서비스 활성화

Ultimate 2를 주행기로 사용할 때는 같은 `joystick` 블록에서 Micro를 끄고 Ultimate 2를 켠다.
두 컨트롤러를 동시에 주행기로 켜면 설정 로딩 단계에서 거부한다.

```hcl
joystick "ultimate2" {
  enabled           = true
  ultimate2_enabled = true
  micro_enabled     = false

  forward_axis      = 5 # R2
  reverse_axis      = 2 # L2
  steering_axis     = 0 # left stick X
  stop_button       = 5 # R1
  trigger_threshold = 0.1
  steering_deadzone = 0.15
}
```

R2/L2 압력은 `manual_control.drive_trans`의 비율로, 왼쪽 스틱 X축은
`manual_control.drive_rot`의 비율로 적용한다. R1, 양쪽 trigger 동시 입력, 축 중립, 장치 분리,
입력 오류에는 `UmStop`을 보낸다. R1 또는 동시 trigger로 정지한 뒤에는 모든 축을 중립으로
돌려야 다시 주행할 수 있다. 연결된 controller가 없고 receiver만 `2dc8:3109`로 보이면 서비스는
정지 상태로 기다리며, controller 연결 후 `2dc8:310b`의 `/dev/input/jsN`을 자동으로 찾는다.

다음 중 하나라도 `hci0` 같은 컨트롤러를 표시하면 Bluetooth 어댑터가 인식된 것이다.

```bash
python3 scripts/joystick_input_test.py --list
bluetoothctl list
rfkill list bluetooth
lsusb | grep -i bluetooth
```

`rfkill`에 `Soft blocked: yes`가 나오면 `sudo rfkill unblock bluetooth`로 해제한다.
내장 Bluetooth가 없으면 Linux 호환 USB Bluetooth 동글을 꽂은 뒤 위 명령을 다시 실행한다.
동글도 최종적으로 `hci0`로 보이므로 이후 절차는 내장 어댑터와 같다.

## 2. Bluetooth 페어링

```bash
bluetoothctl
power on
agent on
default-agent
scan on
# 표시된 조이스틱 주소를 사용한다.
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
quit
```

조이스틱을 페어링 모드로 둔 상태에서 실행한다. 연결 후 `--list` 결과에
`/dev/input/js0 ... connection=bluetooth`가 표시되는지 확인한다.

## 3. USB 연결

유선 USB 케이블이나 2.4 GHz 전용 USB 리시버를 연결한 뒤 다음을 실행한다.

```bash
python3 scripts/joystick_input_test.py --list
```

`/dev/input/js0 ... connection=usb`가 표시되면 준비된 것이다. 충전 전용 케이블은 입력 장치를
만들지 않으므로 데이터 통신 지원 케이블을 사용한다. 장치가 없으면 `lsusb`, `dmesg -w`,
`ls -l /dev/input/`으로 커널 인식 여부와 권한을 확인한다.

## 4. 실제 키/축 번호 확인

Ultimate 2는 안내형 학습 모드로 확인하는 것이 가장 쉽다.

```bash
python3 scripts/joystick_input_test.py --learn
```

표준 입력 외의 원시 HID 이벤트를 진단할 때는 `--list`에 표시되는 Receiver Keyboard의
`eventN` 장치를 직접 확인할 수 있다. `evtest` 패키지는 필요 없다.

```bash
sudo python3 scripts/joystick_input_test.py --list
sudo python3 scripts/joystick_input_test.py --event-role keyboard
```

`event16` 같은 번호는 재연결할 때 바뀔 수 있으므로 `--event-role keyboard` 사용을
권장한다. 버튼을 눌렀을 때
`EV_KEY code=N value=1 pressed`가 나오면 해당 버튼은 joystick이 아니라 keyboard HID로
전달되는 것이다. Controller와 Receiver Keyboard 양쪽에서 아무 이벤트도 없으면 Ultimate
Software V2에서 일반 버튼으로 remap한 뒤 onboard profile에 저장해야 한다.

Keyboard에서 나오지 않으면 Controller 원시 이벤트도 확인한다.

```bash
sudo python3 scripts/joystick_input_test.py --event-role controller
```

화면에 `[PL]`, `[PR]`, `[R4]`처럼 하나씩 표시되면 Enter를 누른 뒤 해당 키만 조작한다.
모든 단계가 끝나면 측정 표와 `extensions.hcl`에 복사할 `joystick "ultimate2"` 블록이 출력된다.
독립 입력으로 노출되지 않는 추가 버튼은 10초 뒤 감지 실패로 표시되며 `s`로 해당 단계를
건너뛸 수 있다. 장치 권한 오류가 나면 `sudo`로 같은 명령을 실행한다.

현재 8BitDo Ultimate 2 Wireless Controller(2.4 GHz, USB ID `2dc8:310b`)에서 확인한 값은
다음과 같다.

```text
R1=button[5], +=button[7], -=button[6]
A=button[1], B=button[0], X=button[3], Y=button[2]
D-pad X=axis[6], D-pad Y=axis[7]
R2=axis[5], L2=axis[2], 왼쪽 스틱 좌/우=axis[0]
```

PL·PR·R4는 기본 2.4 GHz X-input 프로필에서 독립 joystick event가 나오지 않아 현재 운영
매핑에서는 사용하지 않는다. `--learn`은 확인된 R1, +/−, A/B/X/Y와 축만 측정한다.

연속 raw event가 필요할 때만 다음 명령을 사용한다.

```bash
python3 scripts/joystick_input_test.py --device /dev/input/js0
```

스틱과 버튼을 하나씩 조작해 출력되는 `axis[N]`, `button[N]`, 원시 `value`를 기록한다.
축 중앙값은 대략 0, 양 끝은 -32767/32767이고 버튼은 뗐을 때 0, 눌렀을 때 1이다.
권한 오류가 나면 임시로 `sudo`를 쓰기보다 사용자를 배포판의 입력 장치 접근 그룹에 추가하거나
udev 규칙을 구성하는 것을 권장한다.

## 5. 논리 동작 매핑

장치마다 번호가 다르므로 `scripts/joystick-mapping.example.json`을 복사해 방금 기록한 번호에
맞춘다. 매핑 결과까지 함께 확인한다.

```bash
cp scripts/joystick-mapping.example.json /tmp/my-joystick.json
python3 scripts/joystick_input_test.py \
  --device /dev/input/js0 \
  --mapping /tmp/my-joystick.json
```

권장 논리 동작은 다음과 같다.

| 입력 | 논리 동작 | 이후 AMR 연결 후보 |
|---|---|---|
| 좌 스틱 상/하 | `drive` | `manualDrive`의 직진 속도 |
| 좌 스틱 좌/우 | `turn` | `manualDrive`의 회전 속도 |
| 항상 누르는 버튼 | `deadman` | 누른 동안만 주행 허용 |
| 쉽게 누를 수 있는 버튼 | `manual_stop` | 즉시 `manualStop` |
| L1/R1 등 | `mode_previous`, `mode_next` | 허용된 이동 모드 선택 |
| A 등 | `confirm` | 선택한 모드 확정 |

`deadzone`은 스틱을 놓았을 때 생기는 미세 입력을 제거한다. 기본값 `0.15`에서 시작해 장치에
맞게 조정한다. 실제 AMR 연결 단계에서는 조이스틱 프로그램이 끊기거나 입력 heartbeat가
일정 시간 오지 않으면 반드시 `manualStop`을 보내는 watchdog을 둔다.
