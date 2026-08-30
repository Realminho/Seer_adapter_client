# JIBOT status LED / light control

How the JIBOT lights are driven. Audience: AI agents and developers looking for an
"LED on/off/color" hook.

> **"LED" is ambiguous — there are two separate lights** (verified on bot300):
>
> | Light | What it is | Control path | Controllable? |
> | ----- | ---------- | ------------ | ------------- |
> | **Status signal tower** (tri-color lamp / 삼색등) | The visible red/yellow/green robot-status beacon | `urobot` core → Topcore MCU (`RequestChangeLED`) | **No** — not in public API; disabled on bot300 |
> | **AprilTag camera fill-light** | Illumination LED for the tag-reading camera | ROS topic **`/led`** (`std_msgs/String` = brightness int, or `"off"`) | **Yes** — publish to `/led` (in-band, ROS) |
>
> Most people mean the **status tower** — that one is covered below and is not
> softwarecontrollable here. The camera fill-light is a different LED; see
> [The AprilTag camera fill-light](#the-apriltag-camera-fill-light-led-ros-topic).

## TL;DR (status tower)

- **There is no LED command in the public `Um*` API.** The adaptor and
  `jibot-client` cannot set the status tower. Do not look for `UmSetLED` /
  `UmLight` — they do not exist.
- The status light is **firmware/core-driven**: the `urobot` core decides the
  color/blink from the robot's internal state and pushes it to the **Topcore MCU**
  over serial. The adaptor is not in this path.
- The only output-related public commands (`UmSetOutput`, `UmSetOutputByte`) drive
  **generic digital outputs**, and the status LED is **not** wired to any of them
  (see [Why `UmSetOutput` does not help](#why-umsetoutput-does-not-help)).

## Where LED control actually lives

The `urobot` binary (the onboard server on TCP `7273`) talks to the Topcore MCU
through an internal `UmTopcore` class. The relevant entry points (confirmed via
binary symbols on bot A):

| Symbol | Direction | Purpose |
| ------ | --------- | ------- |
| `JModeOPC::RequestTriColorLamp(TopCore::TopCoreLight)` | Mode → Topcore | High-level "set tri-color tower" entry point |
| `(Um)Topcore::RequestChangeLED(std::string)` | Robot → Topcore | Set the LED state by token string |
| `(Um)Topcore::RequestLightState(TopCore::TopCoreLight)` | Robot ↔ Topcore | Set/read light state (enum) |
| `(Um)Topcore::RequestLightState(unsigned char)` | Robot ↔ Topcore | Set/read light state (raw byte) |
| `jt::JInfoTopcore::RequestBlink()` | Robot → Topcore | Trigger blink behavior |

These symbols appear in both `bin/urobot` (`UmTopcore`) and
`jarvis/lib/jarvis-g/jarvis-g` / `bz_robot/bin/bz_robot` (`JTopcore`,
`jt::JModeOPC`). The status tower is a **tri-color signal lamp (삼색등)**:
`RequestTriColorLamp` is the high-level call; it ends at `RequestChangeLED`.

- **Transport:** serial `/dev/inner1` (→ `ttyUSB1`, `115200` baud) — the Topcore
  link declared in `/usr/local/urobot/params/device/topcore.json` (`"port":
  "/dev/inner1"`).
- **Trigger:** the `urobot` core state machine, not an external request. The light
  reflects robot state (idle / moving / error / connection-lost), so it is
  effectively read-only from outside.

### LED states / blink modes

Tokens found in the `urobot` binary (enum `TopCore::TopCoreLight` and
`JInfoTopcore::RequestBlink`):

- **Colors:** `green`, `orange`, `yellow`, `red`
- **Blink modes:** `greenblink`, `orangeblink`, `yellowblink`, `redblink`,
  `BLINKON` / `BLINKOFF`, `if_blinkon` / `if_blinkoff`, `blinkstate`,
  `isAllLightBlink`, `isGreenLightBlink`
- **Auto behavior:** `"robot lost, all light blink"` — on link loss the firmware
  blinks all lights automatically. Gated by `enable_blink_after_lost` in
  `device/topcore.json` (`enable_blink_after_lost: false` on bot A as inspected).

### Color is state-driven, not settable

The **color cannot be set to an arbitrary value** — it is derived from robot state
by `jt::JInfoTopcore` (in `jarvis-g`). Confirmed from its log strings:

- `"JInfoTopcore, ... send red (stopped: %d, enable: %d), start music: %s"` —
  **stopped / E-stopped → RED** (and starts the alarm/voice music).
- `"JInfoTopcore, send green (stopped: %d, enable: %d), stop music: %s"` —
  **moving → GREEN** (stops the music).
- Related members/flags: `JInfoTopcore::ProcessRedLight()`, `RequestBlink()`,
  `mEStopCount` / `GetEStopCount()`, config `enable_stopped_status_red`
  (`mEnableStopStatusRed`), and a timeout light (`mTimeoutLight`).

The color-set entry points exist (`JTopcoreWrapper::SetTriColorLampState(uint8)`,
`JModeOPC::RequestTriColorLamp(TopCore::TopCoreLight | std::string)`,
`JTopcore::RequestChangeLED(std::string)`) but are **called internally by the state
machine only** — there is no public `Um*` command and no ROS topic/service that
sets the tower color. (Searched: `rostopic`/`rosservice list` for
`color|lamp|tower|tricolor|led|light` → only `/led` camera brightness, plus
state/status topics like `/jrobot_status`, `/bz_robot/safety_mode`.) So changing
the **status-tower color** from outside is not possible.

## Why `UmSetOutput` does not help

The public output commands set **generic digital outputs (DO)**, not the status
light:

| Command | Client method | Semantics |
| ------- | ------------- | --------- |
| `UmSetOutput` | `um_set_output(length, high, low)` | Set a full output word by bit pattern |
| `UmSetOutputByte` | `um_set_output_byte(num, flag)` | Set a single output bit `num` to `flag` |
| `UmGetOutput` | `um_get_output()` | Read current output state |

The route-file `output` command (`cmd: "output"`) confirms the shape — `target:
"all"` + `flags: "...,xx11"` (full word) or `target: "any"` + `position` + `flag`
(single bit), in `/usr/local/urobot/params/routes/routes.samples.json`.

But the only **mapped** outputs on Topcore (`device/topcore.json` → `params.o1`–
`o9`) are **buffer-station signals** — `Arrived_L`, `Arrived_R`, `Arrived_M`,
`Arrived_Low_*`, `Arrived_Upper_*` — **not** the status LED. No DO bit is mapped to
the beacon, so toggling outputs will not change the light.

## Implication for the adaptor

- The adaptor **cannot** expose a working "set LED" VDA5050 instant action today:
  there is no `Um*` command for it, and the LED is not on a generic DO bit.
- Changing the status light would require either a new `urobot`/firmware command
  (vendor-side), or speaking the internal Topcore MCU protocol directly on
  `/dev/inner1` — which bypasses the core state machine and is **not recommended**.

## The AprilTag camera fill-light LED (`/led` ROS topic)

Separate from the status tower, there is a **second LED that _is_ controllable** —
the illumination/fill-light for the AprilTag (QR) localization camera. It is driven
through ROS, not the `Um*` API:

- **Topic:** `/led`, type `std_msgs/String`. **Publishers: none** by default → it
  is a **command input**; publishing to it drives the light.
- **Subscriber / handler:** the nodelet manager `/apriltag_localization_nodelet_manager`
  also hosts the camera nodelet **`qr_camera::MdCameraNodelet`**, whose
  **`ledCallback(std_msgs::String)`** (in `jarvis/lib/libqr_camera_nodelet.so`)
  handles `/led`. (Its sibling control inputs `/config`, `/flat_enable` map to
  `configCallback` / `flatTestCallback`.)
- **What it does (from disassembly of `ledCallback`):** it sets the camera's LED
  **brightness** via `qr_camera::MDCamera::setLedBrightness(int)`:

  | `/led` `data` (string) | Effect |
  | ---------------------- | ------ |
  | a base-10 integer, e.g. `"0"`, `"50"`, `"100"` | `setLedBrightness(<that int>)` |
  | `"off"` | `setLedBrightness(8)` — a fixed low/idle level (literal at `.rodata 0x607a0`) |
  | anything else (not parseable as int) | throws → logs `"Failed to set LED brightness"` |

  So `/led` is a **brightness control, not on/off**. The integer is passed straight
  to the camera SDK (`MDCamera::setLedBrightness(int)`); the valid range/units are
  SDK-defined and not visible here (the `"off"` → `8` mapping suggests small numbers
  are dim). Log line on success: `"Set LED brightness to {}"`.

To control it in-band (ROS), publish a brightness string to `/led`, e.g.:

```bash
# Run ON the robot (ROS env sourced). Active command — confirm before use.
rostopic pub -1 /led std_msgs/String "data: '0'"     # dim / mostly off
rostopic pub -1 /led std_msgs/String "data: 'off'"   # fixed level 8
rostopic pub -1 /led std_msgs/String "data: '100'"   # brighter (range SDK-defined)
```

> **Caution.** This is the **camera fill-light**, not the status beacon. It is an
> active control action (not read-only), and the AprilTag localization stack is
> live during operation, so changing brightness could affect tag detection. The
> integer range is SDK-defined/unverified. Treat any publish as a deliberate,
> consented test, not a probe.

## How this was verified

Read-only SSH inspection of **bot A (`ucore@192.168.3.222`)** on **2026-06-21**
(no robot state changed):

- Public command set: `strings /usr/local/urobot/bin/urobot | grep -oE 'Um[A-Z][A-Za-z]+' | sort -u`
  → 117 commands; only `UmGetOutput` / `UmSetOutput` / `UmSetOutputByte` are
  output-related, **no LED command**. Matches `jibot-client` `COMMAND_SPECS`
  (`jibot-client/src/jibot_client/client.py`).
- LED symbols/strings: `strings /usr/local/urobot/bin/urobot | grep -iE 'led|light'`
  → `RequestChangeLED`, `RequestLightState`, color/blink tokens above.
- Output mapping: `/usr/local/urobot/params/device/topcore.json` (`o1`–`o9` =
  `Arrived_*`), `routes.samples.json` (`cmd: "output"` shape).
- Tri-color lamp symbols: `strings jarvis/lib/jarvis-g/jarvis-g | grep -iE 'led|light'`
  → `JTopcore::RequestChangeLED`, `JModeOPC::RequestTriColorLamp`, `GreenBlink`/`RedBlink`.
- Camera fill-light path: `rostopic info /led` (type `std_msgs/String`, sub
  `/apriltag_localization_nodelet_manager`, no publishers) and `rosnode info` of
  that manager (`/led`, `/opts`, `/config`, `/flat_enable`, `/camera_driver/info`);
  robot model is **bot300** (`/bot300/robot_state_publisher`).
- `/led` semantics: native `objdump -d -C` of
  `jarvis/lib/libqr_camera_nodelet.so` →
  `qr_camera::MdCameraNodelet::ledCallback` does `string::compare(data,"off")`
  (literal at `.rodata 0x607a0`) → `setLedBrightness(8)`, else `strtol(data,10)` →
  `qr_camera::MDCamera::setLedBrightness(int)`, with `__throw_invalid_argument` /
  `__throw_out_of_range` on bad input. Brightness strings: `"Set LED brightness to {}"`,
  `"Failed to set LED brightness"`. All read-only (no publish performed).

## Feasibility probe: can we drive the LED directly? (bot A, 2026-06-21)

Tested read-only against the live robot (FMS-connected, no state changed):

- **`UmGetOutput` works.** A one-shot frame to `127.0.0.1:7273` returned
  `{"#CMD#":"UmGetOutput","num":8,"output":[0,0,0,0]}` — the public DO read path is
  functional, but every output bit is `0` while the status light is presumably lit,
  confirming the beacon is **not** on a generic DO bit. (urobot accepts multiple
  clients, so this did not disturb the adapter/FMS connections.)
- **`RequestChangeLED` is not invokable.** It is an internal `UmTopcore` C++ method,
  not in the 117-entry public command list and not exposed by any route/scene/task
  `cmd`. The only way to exercise it would be raw frames on the Topcore serial
  `/dev/inner1` — bypassing the core.
- **That direct path is blind and pointless on this unit:**
  - The Topcore device is **disabled in config** — `device/topcore.json` has
    `enable: false` (plus `enable_change_monitor`, `enable_device_confirm`,
    `enable_blink_after_lost` all `false`). So `RequestChangeLED` is inactive in the
    running core, and it is unconfirmed the beacon is even wired to this MCU here.
  - The 7273 server `jarvis-g` (pid 16607, user `ucore`) holds **no tty**, and
    `lsof` shows no holder for `/dev/ttyUSB1` — i.e. the port is free, but only
    because the subsystem is off. (`lsof` ran as non-root, so a root holder cannot
    be fully excluded; the `enable: false` config is the decisive evidence.)
  - The wire frame for `RequestChangeLED` (`Robot->Topcore:%s`) is unknown. Learning
    it would need a serial capture while the LED changes, but no traffic flows
    (topcore off) and non-destructive capture tools (`socat`/`interceptty`) are
    absent — only `strace` is installed.

**Conclusion:** on this robot the LED is not softwarecontrollable by any safe path.
If the beacon is lit while `topcore` is `enable: false`, it is most likely driven
autonomously by the motion-controller/MCU firmware from hardware state (E-stop,
motor), with no software hook at all.

See also: [`jibot-onboard-access.md`](jibot-onboard-access.md) (SSH access,
serial links) and
[`jibot-jmanager-reconnect-troubleshooting.md`](jibot-jmanager-reconnect-troubleshooting.md).
