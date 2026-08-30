# NOTE: 이 파일은 어떤 코드도 읽지 않는다. 실제로 로드되는 것은
#   adaptor/config/{config.toml,robots.hcl,extensions.hcl} 뿐이고
#   (adaptor/config/config.py 가 __file__ 기준 절대경로로 연다),
#   로봇 배포도 adaptor/ 만 올라간다
#   (scripts/update-jibot-adapter-over-ssh.sh LOCAL_ADAPTER_DIR="adaptor").
#   여기 값을 고쳐도 실행에 반영되지 않는다 — 현장 기록용 사본으로만 볼 것.
#   반영하려면 adaptor/config/ 쪽 같은 이름 파일을 고칠 것.

# extension 전용 설정(PIO/EZI/에어샤워/엘리베이터 튜닝값, 액션 모듈·플러그인
# 등록)이다. config.toml에서 옮겨졌다. config/extensions.py load_extensions()가
# 읽어 config.toml과 같은 섹션 dict로 병합한다.
#
# ezi_io/ezi_motor는 여기 없다 — 로봇별 override인 config/robots.hcl이 채운다.

extension "pio" {
  pio_serial_port     = "/dev/ttyUSB4"   # PIO port "/dev/ttyUSB0~4"
  pio_baudrate = 38400                    # PIO baudrate
  media        = 2                        # media between master and client
  port         = 0                        # port between master and client
  vehicle_num  = "OHT123"                 # OHT_num or AMR_num

  advanced = {                                  # PIO timing knobs (Task 5.5/5.6b); all defaults match prior literals
    init_default_timeout_sec     = 2.0   # _pio_init fallback when action omits timeoutSec
    read_default_timeout_sec     = 2.0   # _pio_read_inputs fallback when action omits timeoutSec
    write_output_timeout_sec     = 2.0   # _pio_write_output default wait_sec
    scenario_default_timeout_sec = 2.0   # _execute_pio_scenario step fallback when action omits timeoutSec
    call_poll_interval_sec       = 0.05  # _wait_pio_input poll cadence (seconds)
    socket_timeout_sec           = 0.2   # PIOMaster serial.Serial(timeout=...) read timeout (seconds)
    connect_delay_sec            = 0.5   # PIOMaster sleep after serial.connect() (seconds)
    read_frame_poll_sec          = 0.05  # PIOMaster sleep cadence inside read_frames loop (seconds)
    read_frames_wait_sec         = 2.0   # PIOMaster default wait_sec for read_frames (seconds)
    send_wait_sec                = 2.0   # PIOMaster default wait_sec for send_and_read / send_* helpers (seconds)
  }
}

extension "ezi" {
  tray_slot_pin = [8, 9, 10, 11, 12, 13] # Tray slot Input Pin
  select = 15                           # PIO control SELECT Digital Output number
  go = 15                               # PIO control GO Digital INPUT number
  do_motor_test = 0                     # testing purpose, Open/Close motor, NO test ( default = 0 ), if ON/OFF, then 1~5
  motor_speed = 20000                   # recommandation 10000~20000 pps
  close_motor_before_motion = true      # before every motion, check tray motor to close
  # Clamp move servo policy:
  #   "auto_on_keep_on"  = turn servo ON before clamp moves, leave it ON after move (default)
  #   "auto_on_auto_off" = turn servo ON before clamp moves, turn it OFF after success/failure
  #   "manual"           = never auto-toggle servo; use clampOn/clampOff explicitly
  clamp_servo_policy = "auto_on_keep_on"
  origin_encoder_offset = 16000         # open_encoder_value = origin_encoder + origin_encoder_offset, close_encoder_value = origin_encoder - origin_encoder_offset
  action_key = ["air_shower_pio", "elevator_pio", "motor"]       # for node action key related to the ezi control such as PIO and MOTOR
  # --- clamp/unclamp target tuning (all optional; uncomment to override) -------
  # Resolution per action (first set wins): instant-action `position` param >
  # absolute *_position > origin_encoder + *_offset. None set -> the action fails
  # (no ±origin_encoder_offset fallback: it ignored the axis travel).
  clamp_position   = 35000            # absolute close target (clamp)
  # unclamp_position = -16000           # absolute open target (unclamp)
  # origin_encoder   = 0                # homed origin reference for *_offset below
  # clamp_offset     = -16000           # close = origin_encoder + clamp_offset
  # unclamp_offset   = 16000            # open  = origin_encoder + unclamp_offset
  # --- min/max/home actions (absolute target if set, else hardware limits/origin)
  # min_position     = 0                # clampMin abs target (else -limit sensor)
  # max_position     = 32000            # clampMax abs target (else +limit sensor)
  # home_position    = 16000            # clampHome abs target (else origin move)
  motor_test_delay_sec = 2.0             # Delay (s) between steps in the motor-test sequence (do_motor_test > 0)
  ezi_motor_poll_interval_sec = 0.1      # Poll interval (s) for EziMotorClient status-polling loops
}

extension "airshower" {
  # 이 설비의 BC 주소다. extension "pio"의 station_id/channel에서 옮겨 왔다 —
  # 에어샤워와 엘리베이터는 같은 station을 쓰지 않는다.
  # 앞의 0이 살아 있어야 한다: BC 페이로드에 문자열 그대로 들어간다.
  pio_station_id = "000030"   # 현장 확인값. 엘리베이터는 000010/000020
  channel        = 250

  failure = 7
  occupied = 2                           # if other party is using the air shower
  fun_working = 3                        # air shower working ( air cleaning )
  door_pin = [0, 1]                      # air shower two doors' pins
  timeout_paring_requesting = 0.5        # timeout in MINUTES to wait for PIO master & client paring (0.5~30)
  timeout_open_requesting = 3            # timeout in MINUTES to wait for door open requesting (0.5~30)
  timeout_close_requesting = 3           # timeout in MINUTES to wait for door close requesting (0.5~30)
  timeout_vacancy_waiting = 30           # timeout in MINUTES to wait for vacancy to pass (0.5~30)
  timeout_airflow_waiting = 30           # timeout in MINUTES to wait for air shower flow (0.5~30)
  poll_interval_sec = 0.2                # ASWorkflow state-machine while-loop poll cadence (seconds)
}

extension "elevator" {
  # 이 엘리베이터 시스템이 듣는 무선 채널이다. extension "pio"의 channel에서
  # 옮겨 왔다. station은 층마다 다르지만(motion_rules의 pio_station_id) 채널은
  # 설비 하나에 하나다.
  channel = 250

  open_door_pin = 4                      # Door open Pin
  close_door_pin = 3                     # Door close Pin
  solid_on_second = 3                    # to trace a requested pin number is SOLID ON ( not blinking ) ( for a given second is solid ON )
  elevating_timing_second = 45           # Elevating timing in SECOND ( travelling time between floors)
  door_open_close_timing_second = 15     # Door opening and closing time in SECOND
  timeout_paring_requesting = 0.5        # timeout in MINUTES to wait for PIO master & client paring (0.5~30)
  timeout_floor_requesting = 1           # timeout in MINUTES to wait for floor requesting (0.5~30)

  motion_rules = [
    { from = "1_05", to = "2_01", mode = "enter", floor_pin = 0, pio_station_id = "000010" },
    { from = "2_01", to = "3_01", mode = "inside", floor_pin = 1, pio_station_id = "000020" },
    { from = "3_01", to = "3_02", mode = "passed", floor_pin = 1, pio_station_id = "000020" },
    { from = "3_01", to = "2_01", mode = "enter", floor_pin = 1, pio_station_id = "000020" },
    { from = "2_01", to = "1_05", mode = "inside", floor_pin = 0, pio_station_id = "000010" },
    { from = "1_05", to = "1_04", mode = "passed", floor_pin = 0, pio_station_id = "000010" },
  ]
}

# Action modules are importable Python packages that provide action_specs()
# and may include a panel.html resource. First-party extensions.clamp,
# extensions.pio, and extensions.ezio are built in; list them only to disable
# one. The block label is a fixed Python package name, not a filesystem path.
# Custom packages must be import-light and on sys.path/PYTHONPATH for both the
# adapter and WebUi units.
# module "custom_actions.air_shower" {
#   enabled = true
# }
#
# module "extensions.pio" {
#   enabled = false
# }

# Custom instant-action plugins. Built-in action types such as manualDrive,
# clamp, ezioReadIn, and ezioWriteOut are handled by the adapter first and
# should not be repeated here.
#
# Inline runner: imports Python code in-process. Use only trusted code that can
# cooperate with asyncio cancellation when timeout_sec is set.
# action "customCalibrate" {
#   enabled     = true
#   runner      = "inline"
#   module      = "custom_actions.calibrate"
#   timeout_sec = 30
#   motion      = true
# }
#
# Subprocess runner: does not import plugin code; sends JSON on stdin and
# expects {"status":"FINISHED|FAILED","description":"..."} on stdout.
# action "customDoorOpen" {
#   enabled         = true
#   runner          = "subprocess"
#   command         = ["python", "custom_actions/door_open.py"]
#   timeout_sec     = 10
#   motion          = false
#   snapshot_fields = ["config.settings.debug_log"]
# }
#
# To disable a first-party extension or custom action for this robot, declare
# only its block label and set enabled=false.
# Disabling photoSensorRead also prevents the EZI IO background sensor polling
# task from starting.
# action "pioReadIn" {
#   enabled = false
# }

# workingState 진입/이탈 시 extension 또는 recipe action을 한 번 실행한다.
# start 실행 중 이탈하면 취소(cleanup 포함)한 뒤 end를 실행한다.
# state_action "DRIVING" {
#   start = { action = "drivingWarningOn", parameters = { signal = "warningLamp" } }
#   end   = { action = "drivingWarningOff", parameters = { signal = "warningLamp" } }
# }

# Ultimate 2 inputs verified through the 2.4 GHz receiver. PL/PR/R4 are not
# exposed as independent Linux input events, so driving uses the triggers directly.
joystick "ultimate2" {
  enabled              = false
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
  ultimate2_enabled    = false
  steering_deadzone    = 0.15
  joystick_reconnect_sec = 1.0
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
joystick_action "1"  { target = "extension"
  action = "enableMotor" }
joystick_action "2"  { target = "extension"
  action = "disableMotor" }
joystick_action "3"  { target = "action"
  action = "startCharging" }
joystick_action "4"  { target = "action"
  action = "stopCharging" }
joystick_action "5"  { target = "extension"
  action = "clamp" }
joystick_action "6"  { target = "extension"
  action = "unclamp" }
joystick_action "7"  { target = "extension"
  action = "clampOn" }
joystick_action "8"  { target = "extension"
  action = "clampOff" }
joystick_action "9"  { target = "action"
  action = "loading" }
joystick_action "10" { target = "action"
  action = "stopLoading" }
joystick_action "11" { target = "action"
  action = "unloading" }
joystick_action "12" { target = "action"
  action = "stopUnloading" }
joystick_action "13" { target = "extension"
  action = "clampHome" }
joystick_action "14" { target = "extension"
  action = "clampStop" }
joystick_action "15" { target = "recipe"
  action = "pioElevatorOpen" }
joystick_action "16" { target = "recipe"
  action = "pioElevatorClose" }
