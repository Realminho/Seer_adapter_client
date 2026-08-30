# JIBOT onboard ROS graph & hardware inventory

A no-purpose, whole-system inventory of the JIBOT (bot300) onboard PC: what ROS
nodes run, every topic and what data it carries, and which physical devices
(motors, lasers, cameras, MCUs, LEDs) hang off which port/bus. Audience: AI agents
and developers who need to find "where does X come from" without re-SSHing.

All values captured **read-only** on **bot A (`ucore@192.168.3.222`)** on
**2026-06-23** (live ROS reads via `rostopic`/`rosnode`, plus `/usr/local/urobot`
config). Robot had just rebooted (`up 0 min`, load ~7) but the stack was fully up
(`urobot.service` active, 31 nodes registered). Nothing was published/commanded.

See also: [`jibot-onboard-access.md`](jibot-onboard-access.md) (SSH/network),
[`jibot-led-control.md`](jibot-led-control.md) (the two LEDs),
[`jibot-charging-dock-bms.md`](jibot-charging-dock-bms.md) (charge relay / BMS),
[`jibot-client/docs/jibot-camera-topics.md`](../../jibot-client/docs/jibot-camera-topics.md)
(camera devices/IPs), [`jibot-jmanager-reconnect-troubleshooting.md`](jibot-jmanager-reconnect-troubleshooting.md).

## How to reproduce

```bash
ssh ucore@192.168.3.222
source /opt/ros/noetic/setup.bash
source /usr/local/urobot/jarvis/setup.bash   # puts jarvis_msgs/store_motor on the msg path
export ROS_MASTER_URI=http://localhost:11311
rosnode list ; rostopic list -v ; rosmsg show jarvis_msgs/RobotStatus
```

> `jarvis_msgs` / `store_motor` are **custom** packages. Without sourcing the
> urobot workspace, `rosmsg show` / `rostopic echo` fail with *"unknown package
> jarvis_msgs"* / *"Cannot load message class"*. Some message types (`Battery`,
> `Io`, `store_motor/{Rotate,VelSteer,OdomSteer}`) are **built into the node
> binaries only** — no `.msg` in `share/`, so `rosmsg show` cannot render them even
> after sourcing; read their data live off the topic instead.

## Open items (TODO)

Gaps from the 2026-06-23 read-only capture, blocked by two limits (binary-only
msgs can't `rosmsg show`; no root → no `lsof`). Resolve next time on bot A:

- [ ] **Fill binary-only message defs via live echo.** No `.msg` in `share/`, so
  capture fields off the wire instead: `rostopic echo -n1 <topic>` for
  `jarvis_msgs/Battery` (`/jbattery`,`/jbms`), `jarvis_msgs/Io` (`/jio`),
  `store_motor/Rotate` (`/jrotate`), `store_motor/VelSteer` (`/jvel_steer`),
  `store_motor/OdomSteer` (`/jodom_steer`) → add to "Custom message definitions".
- [ ] **Confirm the motor MCU tty (root `lsof`).** `rosserial_server` (PID 1220)
  owns the serial link but the exact `/dev/ttyUSB*` is unconfirmed (currently
  *guessed* `inner0`/`inner2` from `robot_jarvis.json` baud 9600). Run
  `sudo lsof -p 1220` / `sudo lsof /dev/ttyUSB*` → update the serial/USB device map.

## Platform (brief)

ARM Cortex-A53 6-core, 3.7 GiB RAM, **no GPU**, Ubuntu 20.04, kernel 5.10
`aarch64`, ROS1 **Noetic** (master `localhost:11311`). Robot software is
`urobot.service` (`/usr/local/urobot`), params under `params/{device,routes,map}`.
Full platform notes in [`jibot-onboard-access.md`](jibot-onboard-access.md).

## Running nodes (31)

| Node | Role |
| ---- | ---- |
| `rosserial_server` | **MCU bridge** — serial link to the motor-controller MCU; publishes all `/jmotor_*`, `/jrobot_status`, `/jbattery`, `/jodom`, `/jimu`, `/jio`, `/jsonar`, `/jfault`, `/jinfo`; subscribes the down-commands `/jvel`, `/jcmd`, `/jtrack`, `/jrotate` |
| `store_motor` | Steering/kinematics + odom model (`VelSteer`/`OdomSteer`/`Rotate`); consumes motor telemetry |
| `urobot_bridge` | urobot core ↔ ROS bridge |
| `bz_robot_daemon`, `bz_robot_ros_plugin` | Navigation / planner (`bz_robot/*` maps, paths, safety_mode); publishes `/cmd_vel`, `/jcmd` |
| `neo_localization_nodelet`, `localization_nodelet_manager` | Lidar/AMCL localization (`/amcl_pose`, `/particlecloud`, `/map_pose`) |
| `side_localization_node` | Fuses AprilTag/side info into localization |
| `apriltag_localization_nodelet_manager` | **QR/AprilTag camera + tag detection** (cam2/cam3 driver, `/led` fill-light, `/pose_in_tag`) |
| `depth_detector_node` | Berxel depth camera → `depth_raw` + `laserscan_kinect` floor/obstacle scan |
| `ekf_se_local`, `odom_filter`, `tf2odom` | Odometry fusion (EKF) + TF |
| `front_laser`, `back_laser`, `left_laser`, `right_laser` (+ `*_noise_filter`, `*_scan_filter`, `relay_*`) | 2D lidar drivers + speckle/scan filters |
| `robot_monitor_nodelet` | Health/diagnostics |
| `bot300/robot_state_publisher` | URDF joints → TF |
| `web_video_server` | MJPEG/HTTP camera streaming on `:8080` |
| `rosout`, `rosserial_server`, `rostopic_*` | infra |

## Serial / USB device map

`lsusb`: FTDI **FT4232H Quad** RS232-HS (4 ports → `ttyUSB0–3`, aliased
`/dev/inner0–3`) + Prolific **PL2303** (`ttyUSB4`) + 2× Realtek **RTL8152**
USB-Ethernet + VIA Labs USB hubs. All serial in group `dialout`.

| Port | Device | Baud | Purpose | Config |
| ---- | ------ | ---- | ------- | ------ |
| `/dev/inner1` (ttyUSB1) | **Topcore MCU** | 115200 | Buffer-station DI/DO (`Arrived_*`, `i1–i8`), **status tri-color LED** (firmware-driven, not externally controllable). `enable:false` on this unit | `device/topcore.json` |
| `/dev/inner3` (ttyUSB3) | **Magnetic/line strip sensor** (`strip_config`) | 9600 | Docking line-following alignment (`position_front 393.25`, `device_id 1`) | `routes/task.json` → `strip1` |
| `/dev/ttyUSB4` (PL2303) | **PIO serial converter** (see note) | 38400 | Facility parallel-I/O link (`BC=` handshake) driving the adapter's `pio*` actions | `adaptor/config/extensions.hcl` → `extension "pio"` (`pio_serial_port`) |
| `/dev/ttyS1` (SoC UART) | Secondary line/strip sensor | — | Line alignment | `routes/*.json` |
| `/dev/inner0` (ttyUSB0), `/dev/inner2` (ttyUSB2) | Motion logic core / peripheral | 9600 | Drive MCU presumed (`device/robot_jarvis.json`). `lsof` ran non-root so holder unconfirmed | `device/robot_jarvis.json` |

> **ttyUSB4 correction (2026-07-29, `192.168.101.61`).** This row previously read
> "Track sensor", taken from the `track` block in `routes/task.json`, which does
> name `/dev/ttyUSB4`. Measured on the unit: unplugging and replugging the PIO
> cable produced `pl2303 converter now attached to ttyUSB4`, and
> `sudo lsof /dev/ttyUSB4` showed **no holder** — nothing in the ROS stack opens
> it. So on this unit the PL2303 is the PIO converter and the `track` docking
> method is inactive. `routes/task.json` still names the port, so treat that
> block as stale config rather than evidence of a live sensor. Other units may
> differ — re-measure before relying on either reading.

Other buses: `/dev/i2c-{0,1,4,9,10}`, one input device `/dev/input/event0` (E-stop/button presumed).

> The motor-controller MCU that `rosserial_server` talks to is on **one of these
> serial ports**; the exact tty wasn't confirmable read-only (no root `lsof`), but
> `robot_jarvis.json` baud 9600 + the FTDI quad point at `inner0`/`inner2`.

## Network interfaces

| IF | Address | Role |
| -- | ------- | ---- |
| `wlan0` | 192.168.3.222/22 | **FMS LAN** — SSH path, adapter↔robot |
| `eth0` | 10.8.8.8/24 | **Camera LAN** — Berxel depth cam `10.8.8.51:5163`, IP cams `10.8.8.111/112`, Dobot arm `10.8.8.89` |
| `eth02` | 192.168.2.10/24 | Peripheral bus — callbutton `192.168.2.132` (`enable:false`) |

USB NICs (RTL8152 ×2) provide the `eth*` interfaces. Camera/IP details in
[`jibot-camera-topics.md`](../../jibot-client/docs/jibot-camera-topics.md).

## Sensors & actuators (hardware inventory)

**Motors (4 axes)** — all reported via `rosserial_server`:
- **Left + right drive wheels** (differential drive) → `/jmotor_l`, `/jmotor_r`;
  per-side status in `/jrobot_status` `l_*`/`r_*`.
- **Lift motor** (load lift/lower) → `/jmotor_lift`, `lift_status`/`lift_IO_status`.
- **Rotate motor** (turntable) → `/jmotor_rotate`, `rotate_status`/`rotate_IO_status`.

**Range/perception:**
- 2D lidars: **front** & **back** (±120°, 721 pts, ~40 m / ~30 m), **left** &
  **right** (vertical-mount, ±90°, ~4 m, edge-virtualized) — `device/laser_*.json`.
- **Down-slope laser** (fall/edge guard, masks ±100–135°) — `laser_down_slopg.json`.
- **Top depth laser** = Berxel depth cam → `laserscan_kinect` — `laser_top.json`.
- **2 ultrasonic sonars** (rear, `count:2`, p1/p2) → `/jsonar` (`int8[8]`, 127=no echo).
- **IMU** (`serial_imu`) → `/ucore/imu/data_raw` ~100 Hz + `/jimu`.

**Cameras:** 2 side IP cams (cam2/cam3, mono8) + Berxel Hawk depth cam +
**AprilTag/QR localization camera** with controllable **fill-light LED** (`/led`).
Full device/IP map in [`jibot-camera-topics.md`](../../jibot-client/docs/jibot-camera-topics.md).

**Configured but disabled on this unit:** Topcore (`enable:false`), callbutton
(`enable:false`), OPC server (`enable:false`, `10.54.65.231:34339`), Dobot arm
(`arm.json`, `auto_init:false`, `10.8.8.89`).

## ROS topic catalog

~200 topics. Grouped by function; `j*` = jarvis (MCU/core), `bz_robot/*` = nav.

### Odometry / localization
| Topic | Type | Note |
| ----- | ---- | ---- |
| `/jodom`, `/jodom1`, `/jodom_filtered` | `jarvis_msgs/Odom` | raw + filtered wheel odom |
| `/jarvis/odom`, `/odometry/filtered` | `nav_msgs/Odometry` | EKF-fused |
| `/jloc_result`, `/jloc_result_raw` | `jarvis_msgs/LocResult` | localization result |
| `/amcl_pose`, `/map_pose`, `/particlecloud` | `geometry_msgs/*`, `PoseArray` | AMCL |
| `/robot_pose`, `/robot_pose_filter` | `geometry_msgs/Vector3Stamped` | x,y,θ pose |
| `/tf`, `/tf_static` | `tf2_msgs/TFMessage` | 4 TF publishers |

### Lidar / range (all `sensor_msgs/LaserScan` unless noted)
`/front/scan`, `/back/scan`, `/left/scan`, `/right/scan` (+ `_raw`, `_filtered`,
speckle filters), `/loc/scan` (localization), `/laser_filtered`,
`/depth_detector_node/.../laserscan_kinect_nodelet/scan` (depth→scan).
`/jsonar` (`jarvis_msgs/Sonar`) = ultrasonics.

### Motors / drive
| Topic | Type | Direction |
| ----- | ---- | --------- |
| `/jmotor_l`, `/jmotor_r`, `/jmotor_lift`, `/jmotor_rotate` | `jarvis_msgs/Motor` | telemetry (up) |
| `/jvel`, `/jpre_vel`, `/cmd_vel` | `jarvis_msgs/Vel`, `geometry_msgs/Twist` | velocity cmd (down) |
| `/jrobot_vel`, `/jfollow_vel` | `jarvis_msgs/RobotVel`, `FollowVel` | planner velocity |
| `/jrotate`, `/jrotate_res` | `store_motor/Rotate`, `jarvis_msgs/RotateRes` | turntable rotate cmd/result |
| `/jvel_steer`, `/jodom_steer` | `store_motor/VelSteer`, `OdomSteer` | steering model |

### Status / power / safety
| Topic | Type | Note |
| ----- | ---- | ---- |
| `/jrobot_status` | `jarvis_msgs/RobotStatus` | **most complete** — motors+BMS+IO+estop |
| `/jinfo` | `jarvis_msgs/Info` | battery%, soh, distance, lifter_state, rotate_angle |
| `/jbattery`, `/jbms` | `jarvis_msgs/Battery` | BMS (built-in msg; see charging doc) |
| `/jio` | `jarvis_msgs/Io` | digital IO (built-in msg) |
| `/jfault`, `/jerror_code`, `/lost_info` | `Fault`, `String` | faults |
| `/jimu`, `/jimu_all`, `/ucore/imu/data_raw` | `jarvis_msgs/Imu`, `sensor_msgs/Imu` | IMU ~100 Hz |
| `/diagnostics` | `diagnostic_msgs/DiagnosticArray` | health |

### Navigation / map
`/map`, `/map_tile`, `/jmap`, `/bz_robot/{global,local,obstacle}_map`,
`/bz_robot/{global,local}_path`, `/foot_print`, `/bz_robot/safety_mode`,
`/bz_robot/turntable_safety_mode`, `/move_base_simple/goal`.

### AprilTag / docking
`/tag_detections_ukf` (`apriltag_ros/AprilTagDetectionArray`), `/pose_in_tag`
(`PoseStamped`, robot pose rel. tag), `/pose_in_tag_state` (`String`),
`/tracked_pose`, `/bundle_detection_image`, `/led`/`/config`/`/flat_enable`
(camera control), `/check_id_service`.

### Command / misc
`/jcmd` (`jarvis_msgs/Cmd` — includes charge relay `cmd:3`), `/jtask`/`/jtrack`
(`Json`/`Cmd`), `/jvoice` (`String`, sound/TTS), `/initialpose`, `/set_pose`.

## Custom message definitions (key)

```
jarvis_msgs/Motor:                 jarvis_msgs/Info:
  float32 current                    int32 input
  int32   rpm                        int32 output
  int32   encoder                    int8  battery        # SOC %
  int32   target_encoder             int32 soh
  int32   target_rpm                 float32 distance     # odometer
  uint16  status_word                int8  lifter_state
                                     int16 rotate_angle
jarvis_msgs/Sonar:  int8[8] data     jarvis_msgs/Fault: int16 system; int32 driver
```

`jarvis_msgs/RobotStatus` (the one-stop telemetry message) carries:
`system_status` (str), `system_error_code`, `v_set/w_set`, `odom_v/odom_w/odom_theta`,
`motor_enable(_status)`, `acc/dec`, **`charge`**, **`EQ`** (SOC %), **`bms_voltage`**,
**`bms_current`** (signed: − idle, + charging), per-motor `l_*`/`r_*`
(`status,error,IQ,enc,enc_set,rpm,rpm_set`), `lift_*`, `rotate_*`, `Outputset`,
`InputState`, and the E-stop/enable bits **`motorboolestop, bumpe_stop, hmi_estop,
motor_error, pc_estop, pc_enable`** (per `store_motor/RobotStatus.msg`).

## Live telemetry sample (idle, parked)

```
/jrobot_status: system_status:"Normal..." error:0  motor_enable:1  charge:0  EQ:76
                bms_voltage:52.99V  bms_current:-1.40A (idle drain)
                l_status:1 l_enc:12532024  r_status:1 r_enc:-9753183  rpm:0
/jinfo:         battery:75  soh:100  distance:6000  lifter_state:0  rotate_angle:0
/jmotor_l:      current:0.035  rpm:0  encoder:12532024  status_word:1
/jmotor_lift:   encoder:0  target_rpm:50  status_word:0
/jsonar:        [127,127,0,0,0,0,0,0]    # 127 = no echo / max range
Rates:          lidar ~30 Hz, IMU ~100 Hz
```

## Deep dive: motor control flow

The drive/lift/rotate chain is **MCU-centric**, bridged by `rosserial_server`
(PID 1220), which owns the serial link to the motor-controller MCU.

**Down-path (commands → wheels):**
```
planner/core ──(/jvel, /jcmd, /jtrack, /jrotate)──▶ rosserial_server ──serial──▶ motor MCU
```
- `/jvel` (`jarvis_msgs/Vel`) = the velocity setpoint the MCU drives the wheels to.
- `/jcmd` (`jarvis_msgs/Cmd = int8 cmd; CmdData data`) = discrete commands, incl.
  the **charge relay `cmd:3`** (see [`jibot-charging-dock-bms.md`](jibot-charging-dock-bms.md)).
  Published by `bz_robot_ros_plugin` and `jarvis_g`; consumed by `rosserial_server`
  **and** `store_motor`.
- `/jrotate` → turntable rotate; `/jtrack` → tracking/docking moves.

**Up-path (telemetry):** `rosserial_server` publishes everything the MCU reports —
`/jmotor_l`, `/jmotor_r`, `/jmotor_lift`, `/jmotor_rotate`, `/jrobot_status`,
`/jodom`, `/jimu`, `/jbattery`, `/jio`, `/jsonar`, `/jfault`, `/jinfo`,
`/jcmd_res`, `/jrotate_res`, `/jtrack_res`.

**`store_motor`** (PID 1506) is the **kinematics/odom model**: it subscribes the
motor telemetry + `/jvel_steer`/`/jodom_steer`/`/jrotate` and maps between
robot-frame velocity and per-wheel/steer commands (differential-drive +
turntable). It publishes only `/rosout` directly — its outputs flow back through
the steer/odom topics and services.

**Motor parameters** (`device/robot_jarvis.json`): `baudrate:9600`,
`size_length:894`, `size_width:650`, `size_radius:613`, `speed_trans_max:500`,
`speed_rotate_max:90`, `speed_trans_acc/dec:1000`, `rotate_ratio:1`,
`loop_time:100` ms. The four motors surface as `l_*`/`r_*`/`lift_*`/`rotate_*`
blocks in `RobotStatus`; servo-enable is `motor_enable`/per-motor `status` (see
the EZI-clamp note: a drive silently drops a move if its servo is OFF).

## Deep dive: AprilTag / line docking pipeline

Docking alignment is **multi-modal** — several independent sensing methods, each
with its own config block in `routes/task.json`, fused by the navigation core:

| Method | Sensor / source | Topic(s) | task.json block |
| ------ | --------------- | -------- | --------------- |
| **AprilTag** | QR/AprilTag camera (`qr_camera` nodelet) | `/tag_detections_ukf`, `/pose_in_tag`, `/pose_in_tag_state`, `/tracked_pose` | `tag` |
| **Magnetic/line strip** | strip sensor on `/dev/inner3` (+ `/dev/ttyS1`) | (serial, into core) | `strip1` |
| **Track** | track sensor named on `/dev/ttyUSB4` — but that port is the PIO converter and unheld on `192.168.101.61` (2026-07-29), so this method looks inactive there; see the serial map note | (serial, into core) | `track` |
| **Triangle marker** | lidar `/front/scan` geometry | `/robot_in_triangle` | `triangle` |
| **Reflector** | lidar reflectors (charge dock) | `JActRefBack` ~1 m back-up | (charging doc) |

**AprilTag manager** (`apriltag_localization_nodelet_manager`) hosts both the
`qr_camera` camera driver (cam2/cam3 + `/led` fill-light) and the tag detector. It
publishes:
- `/pose_in_tag` (`geometry_msgs/PoseStamped`) — robot pose **relative to the tag**
  (the docking alignment error), plus `/pose_in_tag_state` (string state).
- `/tag_detections_ukf` (`apriltag_ros/AprilTagDetectionArray`) — UKF-fused tag
  detections, consumed by `side_localization_node`.
- `/bundle_detection_image` — debug overlay; `/tracked_pose` — tracked target.
- Service `/check_id_service` — validate the expected tag/station id.
- Control inputs: `/led` (fill-light brightness — see
  [`jibot-led-control.md`](jibot-led-control.md)), `/config`, `/flat_enable`, `/opts`.

**`tag` block acceptance tolerances** (`routes/task.json`): `ref_dist:3000`,
`ref_accept_dist:15`, `ref_accept_offset:20`, `ref_accept_theta:2`,
`ref_angle_error:80`, `ref_count:6`, `ref_edge_left/right:±550`,
`online_distance_to_stop:3600`, `camName:"/front/scan"`, `speed:100`. The
charge-dock reflector back-up (`JActRefBack`, default 1000 mm) is a separate seat
maneuver documented in [`jibot-charging-dock-bms.md`](jibot-charging-dock-bms.md).
