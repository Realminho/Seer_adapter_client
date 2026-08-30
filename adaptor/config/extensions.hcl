# extension 전용 설정(PIO/EZI/에어샤워/엘리베이터 튜닝값, 액션 모듈·플러그인
# 등록)이다. config.toml에서 옮겨졌다. config/extensions.py load_extensions()가
# 읽어 config.toml과 같은 섹션 dict로 병합한다.
#
# ezi_io/ezi_motor는 여기 없다 — 로봇별 override인 config/robots.hcl이 채운다.

extension "pio" {
  # PIO master가 설비 측 client와 통신할 때 사용하는 직렬 연결과 이 로봇의
  # 식별 정보다. 상대 설비 station_id/channel은 설비 블록(extension
  # "airshower"/"elevator")이 갖는다 — 2026-08-15.
  # udev가 만드는 안정 경로를 쓴다. /dev/ttyUSBn은 열거 순서가 정하는 이름이라
  # 로봇마다 다르고(같은 PL2303이 1호기 ttyUSB4, 2호기 ttyUSB0) USB를 옮겨 꽂으면
  # 또 바뀐다. 틀려도 조용히 열리기 때문에 증상이 "되는 것 같은데 안 되는" 형태로
  # 나타난다. 이 로봇의 값은 scripts/list-serial-ports.sh 로 확인한다.
  pio_serial_port = "/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0"
  pio_baudrate = 38400                    # 직렬 통신 속도
  media        = 2                        # master/client 사이의 통신 매체 번호
  port         = 0                        # 설비 측 논리 포트
  vehicle_num  = "OHT123"                 # 설비에 전달할 OHT/AMR 식별자

  # PIO 신호선은 직렬이 아니라 EZI IO가 구동한다 — elevator.py의 문/층 핀,
  # airshower.py의 문 핀 모두 ezi_io.turn_on_output()으로 친다. 직렬은 BC
  # pairing 전용이다.
  #
  # 같은 핀 번호가 입력·출력 레지스터에 각각 있고 방향만 다르다:
  #   out N = 로봇 → 설비 (문 열림 요청, 층 선택 …)
  #   in  N = 설비 → 로봇 (문 열림 확인, 층 도착, 점유 …)
  # 8~13은 트레이 센서(tray_slot_pin), 15는 SELECT(출력)/GO(입력)라 0~7만 남는다.
  # PIO out 번호(1-based) → EZI IO 출력 핀(0-based) 짝을 그대로 적는다.
  # output_pins 리스트로도 같은 짝을 만들 수 있지만 그쪽은 "리스트 i번째 = out i+1"
  # 이라는 위치 추론이라, 한 칸 밀려도 아무도 못 잡는다(config.py:95-98이 맵을
  # 권하는 이유다). 둘 다 있으면 이 맵이 이긴다.
  # pioPing이 out 1~8을 전부 훑으므로 8개를 다 적어 둔다.
  output_pin_map = {
    1 = 0   # 1층 호출 — extension "elevator" motion_rules의 floor_pin 0
    2 = 1   # 상층 호출 — floor_pin 1
    3 = 2   # close_door_pin
    4 = 3   # open_door_pin
    5 = 4
    6 = 5
    7 = 6
    8 = 7
  }

  # 설비 신호 이름 → PIO out 번호. recipes.hcl과 WebUI가 숫자 대신 이 이름을 쓴다.
  # out 번호는 1-based, EZI IO 핀은 0-based라 한 칸 밀리기 쉽다. 아래 값은 코드
  # 상수가 아니라 이 현장 배선이며, 아래 extension "elevator"의 door pin과 같은
  # 점을 가리켜야 한다 (utils/elevator.py가 현장에서 그 핀을 직접 친다).
  # 배선이 정말 다르면 두 곳을 함께 고친다 —
  # tests/test_recipe_acceptance.py가 이 짝을 검사해 어긋나면 실패한다.
  output_signals = {
    # 1f에서
    elevator1fOpen = 4
    elevator1fClose = 3

    elevator1f_1f = 1
    elevator1f_2f = 2

    # 2f에서
    elevator2fOpen = 2
    elevator2fClose = 1

    elevator2f_1f = 3
    elevator2f_2f = 4

    # 에어샤워 양쪽 문. 아래 extension "airshower"의 door_pin = [0, 1]과 같은 점을
    # 가리켜야 한다 (PIO out은 1-based, door_pin은 EZI IO 핀이라 0-based):
    #   airShower3lOpen = out1 -> EZI out0 = door_pin[0] (3L쪽 문)
    #   airShower4lOpen = out2 -> EZI out1 = door_pin[1] (4L쪽 문)
    # 어긋나면 tests/test_recipes_config.py가 실패한다 — 한 칸 밀려도 recipe는
    # 반대쪽 문을 누르고 FINISHED로 끝나므로 사람이 눈으로 잡을 수 없다.

    airShower3lOpen = 1
    airShower4lOpen = 2
  }

  # 설비 신호 이름 -> PIO in 번호. 위 output_signals의 입력판이다. 방향이 다르면
  # 맵도 다르다 — 같은 번호가 입력·출력 레지스터에 각각 있고 뜻이 다르다
  # (out2 = 4L 문 열기 요청, in2 = 4L 문 열림 확인). 한 맵에 섞어 두면 출력 이름을
  # 입력 자리에 적어도 그럴듯한 번호가 나와 조용히 엉뚱한 점을 읽는다.
  #
  # 에어샤워 문 열림 확인. **현장 확인 필요** — 출력이 out1/out2라 입력도 대칭이라
  # 본 값이다.
  #
  # 엘리베이터 입력도 출력과 같은 이유로 station마다 뜻이 갈린다 — 같은 네 가닥이
  # 1층 station(000010)과 상층 station(000020)에서 다른 신호다. 그래서 이름에 층을
  # 박고 번호가 겹치는 것은 그대로 둔다.
  input_signals = {
    doorSensor3l = 1
    doorSensor4l = 2

    # 1층에서 (2026-08-27 지시값, 극성 현장 확인 필요)
    elevator1fOpened   = 1   # in1 = 문 열림 확인
    elevator1fCarUpper = 2   # in2 = 카가 상층에 있음(on)

    # 상층에서. **현장 확인 필요** — 1층과 대칭이라고 본 값이며 실측 전이다.
    # 여기 두 줄만 고치면 recipes.hcl은 그대로 둔다.
    elevator2fOpened   = 1   # in1 = 문 열림 확인
    elevator2fCarLower = 2   # in2 = 카가 1층에 있음(on)
  }

  # output_pin_map이 없는 배포를 위한 폴백이다. 위 맵과 같은 짝을 만든다.
  output_pins  = [0, 1, 2, 3, 4, 5, 6, 7]   # PIO out 1~8 → EZI IO digital output
  input_pins   = [0, 1, 2, 3, 4, 5, 6, 7]   # PIO in  1~8 → EZI IO digital input

  # 모든 시간 값의 단위는 초이며 action의 timeoutSec가 있으면 그 값이 우선한다.
  advanced = {
    init_default_timeout_sec     = 2.0   # pioInit의 기본 BC 응답 대기 시간
    read_default_timeout_sec     = 2.0   # pioReadIn의 기본 입력 응답 대기 시간
    write_output_timeout_sec     = 2.0   # pioWriteOut의 기본 출력 응답 대기 시간
    scenario_default_timeout_sec = 2.0   # pioScenario 각 단계의 기본 제한 시간
    call_poll_interval_sec       = 0.05  # 입력 기대값을 확인하는 polling 주기
    # 설비는 첫 BC에 GO를 올려주지 않는다. GO가 올라올 때까지 BC를 다시 보내며
    # 쓰는 총 예산이다 (workflow의 timeout_paring_requesting 0.5분과 같은 30초).
    pair_timeout_sec             = 30.0  # GO가 올라올 때까지 BC를 재시도하는 총 예산
    select_settle_sec            = 0.2   # SELECT on/off 전후 안정화 대기
    # 이 설비는 유효한 BC 응답 뒤 SELECT를 내리면 접속되고 GO는 나중에 올라온다.
    # pair_confirmation="go"인 다른 설비만 after_go를 사용한다.
    pair_confirmation            = "bc_reply" # bc_reply 또는 go
    select_off_timing            = "after_bc" # after_bc 또는 after_go
    # 0.5는 현장 확정값이다(2026-08-18). 설비가 SELECT falling edge로 pairing을
    # 확정하는 것으로 보여, 응답 직후 바로 내리면 놓치는 때가 있다.
    select_off_delay_sec         = 0.5   # BC 응답/GO 확인 후 SELECT OFF까지 추가 유지
    ping_default_timeout_sec     = 5.0   # pioPing의 기본 BC 응답 대기 시간
    ping_output_hold_sec         = 0.2   # pioPing이 출력 1~8을 on/off할 때 유지 시간
    unpair_default_timeout_sec   = 5.0   # pioDisconnect의 GO 내려감 대기 시간
    socket_timeout_sec           = 0.2   # 직렬 포트 한 번 읽기의 timeout
    connect_delay_sec            = 0.5   # 직렬 포트 연결 직후 안정화 대기
    read_frame_poll_sec          = 0.05  # 수신 frame 반복 확인 주기
    read_frames_wait_sec         = 2.0   # frame 수신 함수의 기본 전체 대기 시간
    send_wait_sec                = 2.0   # 송신 후 응답을 기다리는 기본 시간
  }
}

extension "ezi" {
  tray_slot_pin = [8, 9, 10, 11, 12, 13] # 트레이 적재 여부를 읽는 입력 핀
  select = 15                           # PIO SELECT 제어용 digital output 번호
  go = 15                               # PIO GO 확인용 digital input 번호
  do_motor_test = 0                     # 0이면 시험 안 함, 1~5이면 기동 시 모터 개폐 시험
  motor_speed = 20000                   # 클램프 모터 속도 권장 범위: 10000~20000 pps
  close_motor_before_motion = true      # 주행 전 트레이 모터가 닫혔는지 확인
  # 클램프 이동 전후 servo 정책:
  #   auto_on_auto_off: 이동 전에 켜고 이동이 끝나면 끔(기본값)
  #   auto_on_keep_on: 이동 전에 켜고 이동 후에도 유지
  #   manual: 자동 제어하지 않고 clampOn/clampOff action으로만 제어
  # auto_on_* 정책은 이동 전 servo ON과 이동 완료를 드라이브 상태 플래그로 확인하며
  # 진행한다. manual은 이 확인을 모두 건너뛰므로 servo ON과 이동 완료 확인은
  # 운영자 책임이다.
  clamp_servo_policy = "auto_on_auto_off"
  clamp_servo_on_timeout_sec = 3.0      # servo ON 확인 대기 한계(초). 초과하면 이동을 보내지 않고 실패
  # 이동 시작 대기 한계(초). 초과하면 실제 위치·리미트 플래그로 "이미 목표"인지 확인하고,
  # 확인되지 않으면 드라이브가 명령을 씹은 것으로 보고 실패로 올린다.
  clamp_motion_start_timeout_sec = 1.0
  clamp_motion_timeout_sec = 30.0       # 이동 완료 대기 한계(초). 초과하면 모터를 세우고 실패
  # 절대 위치 이동의 도착 판정 허용 오차(엔코더 counts). ±16000 스트로크 기준으로 일부러
  # 넉넉하게 잡았다 — 정밀도 채점이 아니라 "도착"과 "아예 안 움직임"을 가르는 값이다.
  clamp_position_tolerance = 500
  origin_encoder_offset = 16000         # 원점 기준 기본 개폐 이동량
  action_key = ["air_shower_pio", "elevator_pio", "motor"] # EZI가 처리하는 기존 node action key
  # 클램프 목표 위치 선택 우선순위:
  # action의 position > *_position 절대값 > origin_encoder + *_offset.
  # clamp/unclamp을 쓰려면 각각 하나는 **반드시** 설정해야 한다. 설정이 없으면 action이
  # 실행되지 않고 실패한다 — 예전에는 ±origin_encoder_offset으로 넘어갔는데, 그 값은 축의
  # 실제 가동 범위와 무관해서 리미트 밖을 향하면 드라이브가 이동을 조용히 버렸다.
  # 아래 값은 형식 예시일 뿐이므로 반드시 이 로봇의 실측 엔코더 값으로 채울 것.
  # clamp_position   = <실측>            # clamp 절대 닫힘 위치
  # unclamp_position = <실측>            # unclamp 절대 열림 위치
  # origin_encoder   = 0                # offset 계산 기준이 되는 원점
  # clamp_offset     = <실측>            # 닫힘 위치 = 원점 + clamp_offset
  # unclamp_offset   = <실측>            # 열림 위치 = 원점 + unclamp_offset
  # min/max/home은 절대 위치가 없으면 각각 limit sensor 또는 원점 이동을 사용한다.
  # min_position     = 0                # clampMin 절대 목표
  # max_position     = 32000            # clampMax 절대 목표
  # home_position    = 16000            # clampHome 절대 목표
  motor_test_delay_sec = 2.0            # 기동 모터 시험의 단계 사이 대기 시간
  ezi_motor_poll_interval_sec = 0.1     # 모터 상태 확인 polling 주기
  # ezioWaitIn(입력 조건 대기)이 timeoutSec 없이 불릴 때의 제한 시간과 입력을
  # 다시 읽는 주기.
  wait_in_default_timeout_sec = 5.0     # 기대 상태를 기다리는 기본 한계(초)
  wait_in_poll_interval_sec = 0.1       # 입력 재확인 주기(초)
}

extension "airshower" {
  # 이 설비의 BC 주소다. extension "pio"의 station_id/channel에서 옮겨 왔다 —
  # 에어샤워와 엘리베이터는 같은 station을 쓰지 않는다.
  # 앞의 0이 살아 있어야 한다: BC 페이로드에 문자열 그대로 들어간다.
  pio_station_id = "000030"   # 현장 확인값. 엘리베이터는 000010/000020
  channel        = 250

  # 설비가 보내는 상태 코드와 출입문 핀. timeout_* 값의 단위는 분이다.
  failure = 7                            # 설비 실패 상태 코드
  occupied = 2                           # 다른 사용자가 점유 중인 상태 코드
  fun_working = 3                        # 에어 세정 동작 중 상태 코드
  door_pin = [0, 1]                      # 입구·출구 문 핀
  timeout_paring_requesting = 0.5        # PIO master/client pairing 대기(0.5~30분)
  timeout_open_requesting = 3            # 문 열림 요청 완료 대기
  timeout_close_requesting = 3           # 문 닫힘 요청 완료 대기
  timeout_vacancy_waiting = 30           # 내부가 빌 때까지 대기
  timeout_airflow_waiting = 30           # 에어 세정 완료 대기
  poll_interval_sec = 0.2                # 상태 머신 확인 주기(초)
}

extension "elevator" {
  # 이 엘리베이터 시스템이 듣는 무선 채널이다. extension "pio"의 channel에서
  # 옮겨 왔다. station은 층마다 다르지만(motion_rules의 pio_station_id) 채널은
  # 설비 하나에 하나다.
  channel = 250

  # 참고용 기존 workflow 설정이다. recipe의 출력 핀은 pio.output_signals가 정한다.
  # 이 두 핀은 **1층 station(000010) 배선**이다 — 같은 네 가닥이 상층에서는 다른
  # 뜻이라(위 output_signals 참조) 상층 문은 여기에 담기지 않는다. 그래서
  # tests/test_recipe_acceptance.py도 1f 신호만 이 값과 대조한다.
  open_door_pin = 3                      # 문 열림 요청 출력 핀 (현장 실측 2026-08-19)
  close_door_pin = 2                     # 문 닫힘 요청 출력 핀 (현장 실측 2026-08-19)
  solid_on_second = 3                    # 점멸이 아닌 연속 ON으로 인정할 유지 시간(초)
  elevating_timing_second = 45           # 층 사이 이동 예상 시간(초)
  door_open_close_timing_second = 15     # 문 개폐 예상 시간(초)
  timeout_paring_requesting = 0.5        # PIO pairing 대기 제한(분)
  timeout_floor_requesting = 1           # 층 요청 응답 대기 제한(분)

  # 노드 구간별 동작: enter=탑승, inside=층 이동, passed=하차 완료.
  # floor_pin은 층 선택 핀, pio_station_id는 해당 층 설비 식별자다.
  motion_rules = [
    { from = "1_05", to = "2_01", mode = "enter", floor_pin = 0, pio_station_id = "000010" },
    { from = "2_01", to = "3_01", mode = "inside", floor_pin = 1, pio_station_id = "000020" },
    { from = "3_01", to = "3_02", mode = "passed", floor_pin = 1, pio_station_id = "000020" },
    { from = "3_01", to = "2_01", mode = "enter", floor_pin = 1, pio_station_id = "000020" },
    { from = "2_01", to = "1_05", mode = "inside", floor_pin = 0, pio_station_id = "000010" },
    { from = "1_05", to = "1_04", mode = "passed", floor_pin = 0, pio_station_id = "000010" },
  ]
}

# action module은 action_specs()를 제공하고 선택적으로 panel.html을 포함하는
# Python package다. extensions.clamp/pio/ezio는 기본 내장이므로 끌 때만 선언한다.
# block label은 파일 경로가 아닌 고정 package 이름이며, 사용자 package는
# adapter와 WebUI 양쪽의 sys.path/PYTHONPATH에서 import 가능해야 한다.
# module "custom_actions.air_shower" {
#   enabled = true
# }
#
# module "extensions.pio" {
#   enabled = false
# }

# 사용자 instant-action 플러그인 설정. manualDrive, clamp, ezioReadIn,
# ezioWriteOut 같은 내장 action type은 여기서 중복 등록하지 않는다.
#
# inline runner는 같은 process에서 Python 코드를 import한다. timeout_sec 사용 시
# asyncio 취소에 협조할 수 있는 신뢰된 코드에만 사용한다.
# action "customCalibrate" {
#   enabled     = true
#   runner      = "inline"
#   module      = "custom_actions.calibrate"
#   timeout_sec = 30
#   motion      = true
# }
#
# subprocess runner는 코드를 import하지 않고 stdin으로 JSON을 보내며 stdout에서
# {"status":"FINISHED|FAILED","description":"..."} 형식의 JSON을 받는다.
# action "customDoorOpen" {
#   enabled         = true
#   runner          = "subprocess"
#   command         = ["python", "custom_actions/door_open.py"]
#   timeout_sec     = 10
#   motion          = false
#   snapshot_fields = ["config.settings.debug_log"]
# }
#
# 특정 로봇에서 내장 extension 또는 사용자 action을 끄려면 block label과
# enabled=false만 선언한다. photoSensorRead를 끄면 EZIO 배경 센서 task도 시작하지 않는다.
# action "pioReadIn" {
#   enabled = false
# }

# workingState 진입/이탈 시 extension 또는 recipe action을 한 번 실행한다.
# 지원 상태: IDLE, DRIVING, ACTING, CHARGING, PAUSED, BLOCKED, ERROR.
# start 실행 중 이탈하면 취소(cleanup 포함)한 뒤 end를 실행한다.
# state_action "DRIVING" {
#   start = {
#     action = "drivingWarningOn"
#     parameters = { signal = "warningLamp" }
#   }
#   end = {
#     action = "drivingWarningOff"
#     parameters = { signal = "warningLamp" }
#   }
# }

# Ultimate 2 inputs verified through the 2.4 GHz receiver. PL/PR/R4 are not
# exposed as independent Linux input events, so driving uses the triggers directly.
joystick "ultimate2" {
  enabled              = true
  vendor_id            = "2dc8"
  product_id           = "310b" # connected; receiver-only state is 3109
  device_name_contains = "8BitDo"
  forward_axis         = 5
  reverse_axis         = 2
  steering_axis        = 0
  stop_button          = 5
  speed_down_button    = 6
  speed_up_button      = 7
  dpad_x_axis          = 6
  dpad_y_axis          = 7
  a_button             = 1
  b_button             = 0
  x_button             = 3
  y_button             = 2
  trigger_threshold    = 0.1
  # -/+ scale manual driving. 100% is the manual_control magnitude.
  speed_step_percent   = 20
  speed_min_percent    = 20
  speed_max_percent    = 100
  speed_start_percent  = 100
  heartbeat_timeout_ms = 400
  # Only one of ultimate2_enabled/micro_enabled may be true at a time.
  ultimate2_enabled    = true
  steering_deadzone    = 0.15
  joystick_reconnect_sec = 1.0
  # xboxdrv uinput fallback for kernels without joydev (/dev/input/jsN).
  xboxdrv_device_name       = "Xbox Gamepad (userspace driver)"
  xboxdrv_forward_axis_code = 9  # ABS_GAS / R2
  xboxdrv_reverse_axis_code = 10 # ABS_BRAKE / L2
  xboxdrv_steering_axis_code = 0 # ABS_X / left stick X
  xboxdrv_stop_button_code  = 311 # BTN_TR / R1
  xboxdrv_dpad_x_code       = 16  # ABS_HAT0X
  xboxdrv_dpad_y_code       = 17  # ABS_HAT0Y
  xboxdrv_a_button_code     = 304 # BTN_A
  xboxdrv_b_button_code     = 305 # BTN_B
  xboxdrv_x_button_code     = 307 # BTN_X
  xboxdrv_y_button_code     = 308 # BTN_Y
  xboxdrv_speed_down_button_code = 314 # BTN_SELECT
  xboxdrv_speed_up_button_code   = 315 # BTN_START
  # Stop driving once the receiver leaves the USB bus. The xboxdrv pad is
  # virtual, so it goes quiet instead of disappearing with the receiver.
  xboxdrv_receiver_guard    = true
  # Master switch for the D-pad+ABXY slots. 아래 slot 1~12를 검토하고 켰다
  # (2026-08-21). 슬롯은 클램프·엘리베이터 recipe를 실제로 실행하므로, 슬롯 목록을
  # 바꾸는 로봇에서는 다시 false로 두고 검토한 뒤 켠다.
  actions_enabled           = true
  # Bluetooth 8BitDo Micro (Switch mode, evdev Pro Controller 057e:2009).
  # Keep false until low-speed testing is ready; requires read access to /dev/input/event*.
  micro_enabled           = false
  micro_device_name       = "Pro Controller"
  micro_dpad_x_code       = 0
  micro_dpad_y_code       = 1
  micro_neutral_threshold = 1000
  micro_stop_button       = 311 # physical R
  micro_reconnect_sec     = 1.0
}

# Fixed slot order:
#   1..4=Up+A/B/X/Y, 5..8=Right+A/B/X/Y,
#   9..12=Down+A/B/X/Y, 13..16=Left+A/B/X/Y.
# action may name a built-in/extension action or a recipe actionType.
#
# 행마다 뜻을 맞춰 둔다: Up=클램프 리미트+문 열기, Right=문 닫기+1층에서 층 호출,
# Down=상층에서 층 호출+클램프 개폐. Left 행은 쓰지 않는다.
# 층 호출 recipe 이름은 출발층-도착층이다 (pioElevatorMove1f-2f = 1층에서 2층 호출).
joystick_action "1"  { target = "extension"
  action = "clampMin"
  enabled = true }
joystick_action "2"  { target = "extension"
  action = "clampMax"
  enabled = true }
joystick_action "3"  { target = "recipe"
  action = "pioElevatorOpen1f"
  enabled = true }
joystick_action "4"  { target = "recipe"
  action = "pioElevatorOpen2f"
  enabled = true }
joystick_action "5"  { target = "recipe"
  action = "pioElevatorClose1f"
  enabled = true }
joystick_action "6"  { target = "recipe"
  action = "pioElevatorClose2f"
  enabled = true }
joystick_action "7"  { target = "recipe"
  action = "pioElevatorMove1f-1f"
  enabled = true }
joystick_action "8"  { target = "recipe"
  action = "pioElevatorMove1f-2f"
  enabled = true }
joystick_action "9"  { target = "recipe"
  action = "pioElevatorMove2f-1f"
  enabled = true }
joystick_action "10" { target = "recipe"
  action = "pioElevatorMove2f-2f"
  enabled = true }
joystick_action "11" { target = "extension"
  action = "clamp"
  enabled = true }
joystick_action "12" { target = "extension"
  action = "unclamp"
  enabled = true }
# Left 행(13~16)은 비워 둔다. 블록이 없는 슬롯은 눌러도 로그만 남고 아무 일도
# 하지 않으므로, 쓰지 않는 조합에 action을 걸어 두는 것보다 빼 두는 편이 안전하다.
# 모터 전원(enableMotor/disableMotor)과 충전은 WebUI에서 실행한다.
