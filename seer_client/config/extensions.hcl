# SEER compatibility configuration for develop (14).
# The shared Adapter requires all four facility extension blocks even though
# SEER uses TCP/IP vehicle APIs rather than JIBOT PIO/EZI hardware.

extension "pio" {
  pio_serial_port = ""
  pio_baudrate = 38400
  media = 0
  port = 0
  vehicle_num = ""
}

extension "ezi" {}

extension "airshower" {
  channel        = 0
  pio_station_id = ""
  channel        = 0
  pio_station_id = ""
  channel        = 0
  pio_station_id = ""
  channel        = 0
  pio_station_id = ""
  pio_station_id = ""
  channel = 0
  failure = 0
  occupied = 0
  fun_working = 0
  door_pin = [0, 1]
  timeout_paring_requesting = 0
  timeout_close_requesting = 0
  timeout_open_requesting = 0
  timeout_vacancy_waiting = 0
  timeout_airflow_waiting = 0
  poll_interval_sec = 0.2
}

extension "elevator" {
  channel        = 0
  channel        = 0
  channel        = 0
  channel        = 0
  channel = 0
  open_door_pin = 0
  close_door_pin = 0
  solid_on_second = 0
  elevating_timing_second = 0
  door_open_close_timing_second = 0
  timeout_paring_requesting = 0
  timeout_floor_requesting = 0
  motion_rules = []
}

module "extensions.clamp" { enabled = false }
module "extensions.pio" { enabled = false }
module "extensions.ezio" { enabled = false }
module "extensions.facility" { enabled = false }

action "seerPathNav" { enabled = true runner = "inline" timeout_sec = 305 motion = true }
action "seerCoordinateNav" { enabled = true runner = "inline" timeout_sec = 305 motion = true }
action "seerTranslate" { enabled = true runner = "inline" timeout_sec = 305 motion = true }
action "seerTurn" { enabled = true runner = "inline" timeout_sec = 305 motion = true }
action "seerSetDO" { enabled = true runner = "inline" timeout_sec = 5 motion = false }
action "seerJackLoad" { enabled = true runner = "inline" timeout_sec = 30 motion = true }
action "seerJackUnload" { enabled = true runner = "inline" timeout_sec = 30 motion = true }
action "seerCameraDockPreview" { enabled = true runner = "inline" timeout_sec = 0 motion = false }
action "seerCameraDock" { enabled = true runner = "inline" timeout_sec = 0 motion = true }
action "seerWait" { enabled = true runner = "inline" timeout_sec = 3605 motion = false }
action "seerBlockProgram" { enabled = true runner = "inline" timeout_sec = 0 motion = true }
