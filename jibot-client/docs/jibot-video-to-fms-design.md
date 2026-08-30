# JIBOT Video-to-FMS Design (Decision Record)

Status: **Recommended** · Captured 2026-06-09 from live onboard inspection.
Companion to [`jibot-camera-topics.md`](jibot-camera-topics.md).

## Goal

Deliver camera/video information from the JIBOT robot to the FMS via the
adaptor, as efficiently as possible. Two distinct needs:

1. **On-demand live video** — occasionally an operator requests a live view from
   the FMS UI.
2. **Event snapshots** — when the robot reports `waiting` / `brake` / `slowdown`
   / `watch` etc., push a thumbnail/snapshot so the FMS can show "what the robot
   saw".

## Hard constraints (verified onboard)

- **FMS link is VDA5050 v3 over MQTT** (broker `192.168.3.108:11883`, topic
  prefix `amr/v3/HN-SH6-TR-001`). MQTT is the **control plane**, not a video bus.
- **Cameras exist only as ROS1 (Noetic) topics** on the onboard PC; see
  `jibot-camera-topics.md`. The adaptor is deployed on the **same host**
  (`/home/ucore/adapter` on `192.168.3.222`), and reaches the camera LAN
  (`10.8.8.0/24`).
- **No camera exposes a standard direct stream.** Port scans:
  - Side cams `10.8.8.111` / `10.8.8.112`: **no TCP ports open** (80/443/554/8554/3956 all closed). Proprietary industrial SDK cameras (config has only `ip/exptime/fps/intrinsics`, `release: true`).
  - Front Berxel `10.8.8.51`: only port `5163` open (proprietary protocol; no RTSP/HTTP).
- **All three cameras are single-source devices already owned by ROS driver
  nodes** that the robot needs: `/qr_camera2_nodelet`, `/qr_camera3_nodelet`
  (AprilTag localization), `/depth_detector_node` (obstacle/depth detection).
- **rospy is pinned to the distro Python (3.8)**; Python 3.8 is EOL (2024-10).
  `jibot-client` declares `requires-python = ">=3.9"`. So an adaptor venv cannot
  simultaneously be 3.8 (for rospy) and satisfy `jibot-client`.
- Frame sizes make raw MQTT relay a non-starter: side `mono8` 640×512 ≈ 328 KB ×
  5.5 Hz × 2 ≈ **3.6 MB/s**; front depth `32FC1` 640×400 ≈ 1 MB × 10 Hz ≈
  **10 MB/s**. Pushing this through the broker would starve the VDA5050 control
  channel.

## Decision

**Separate the data plane (video) from the control plane (MQTT/VDA5050).**
The adaptor never relays pixels; it advertises where to get them.

```
[data plane — video, never through MQTT]
  ROS image topics ──> web_video_server (onboard :8080)
    • live:     http://<onboard>:8080/stream?topic=/camera/cam2/image_raw   (MJPEG/WebRTC)
    • snapshot: http://<onboard>:8080/snapshot?topic=/camera/cam2/image_raw  (single JPEG)
    ↑ encodes ONLY while a client is connected → idle CPU ≈ 0

[control plane — adaptor / VDA5050 MQTT]
  FMS ──instantAction("requestVideo")──> adaptor
  adaptor ──state.information[{ stream_url }]──> FMS
  FMS pulls the HTTP/WebRTC stream out-of-band (broker not involved)

[event snapshots]
  adaptor state loop detects waiting/brake/slowdown transition
    ──HTTP GET /snapshot──> JPEG
    ──publish (binary)──> side MQTT topic  amr/v3/<sn>/event_snapshot  (QoS0, retain=false)
```

### 1. Live video — `web_video_server`, on demand
Install `ros-noetic-web-video-server` and run it as a service. It is a C++ ROS
node that lazily encodes only when a client connects, so it costs nothing while
idle — ideal for occasional viewing. FMS requests a view (VDA5050 instantAction),
the adaptor returns the stream URL in `state.information`, and the FMS opens the
HTTP/WebRTC stream directly.

### 2. Event snapshots — adaptor stays a thin HTTP+MQTT client
The transition events are **already detected by the adaptor** (the
`slowdown/watch/break/nrunto` strings in `config.toml [jibot_status]` flow
through `publish_state` in `adaptor/adapter_jibot.py`). On a relevant transition
the adaptor does **one HTTP GET** to `/snapshot?...&quality=40`, then publishes
the JPEG as a **binary** payload to a **side topic** (not the VDA5050 `state`
message), QoS 0, no retain, throttled/deduped. No rospy, no ROS dependency in the
adaptor venv.

## Why this is the most efficient option

### Receive path: web_video_server vs. rospy-in-adaptor vs. sidecar

| | ① web_video_server + thin adaptor (3.12) **[chosen]** | ② adaptor venv→3.8 + rospy | ③ separate py3.8 ROS sidecar |
|---|---|---|---|
| Live video | direct, on-demand, idle CPU 0 | still needs a separate streamer | sidecar handles it |
| Snapshot | HTTP GET on event | rospy cached frame | sidecar cached frame |
| Idle CPU | **0** (no client → no encode) | constant encode if topic subscribed | constant subscribe cost |
| Adaptor Python | **3.12 (modern)** | **3.8 (EOL); conflicts with jibot-client ≥3.9** | 3.12 |
| asyncio mixing | none (HTTP client) | rospy↔asyncio thread bridge | none |
| Tools / new code | 1 tool, minimal | 2 paths, rospy node+bridge | 2 procs, sidecar+IPC |

The Python-version question is **not** the real fork: live streaming must avoid
Python regardless of version, so downgrading the venv only touches the snapshot
path — where HTTP GET is already comparable while keeping the adaptor on a
supported Python. `web_video_server` *is* the optimized C++ "sidecar," so option ③
collapses into option ①.

### Direct-from-camera vs. ROS topic

A common intuition is "fewer hops → direct camera access is more efficient."
**That holds only when the camera natively serves a compressed, multi-client
stream (RTSP/WebRTC/MJPEG-HTTP).** On this robot neither condition holds:

- No camera exposes a standard stream (side = proprietary SDK, front = Berxel
  5163). Direct access yields raw frames you must re-encode anyway — no win.
- The cameras are single-session devices **already owned by navigation/safety
  ROS nodes**. A second direct session would either fail (device busy) or steal
  the device and break AprilTag localization / obstacle detection.
- The ROS topic is already the shared, decoded distribution point; a
  `web_video_server` subscriber adds ≈ 0 device load. Direct access adds a second
  device session.

→ **For this robot, going through the ROS topic (one more subscriber) is more
efficient and safer than direct camera access.** Direct access would only win if a
camera offered a native multi-client compressed stream — which these do not.

## Open implementation choices

- **Snapshot source topic** is **config-driven** (`[video].snapshot_topic`).
  Must be a topic that is *always publishing*, else the snapshot GET times out:
  `/bundle_detection_image` is richer but only publishes during detection, so the
  default was set to the always-on `/camera/cam2/image_raw`. Front depth (`32FC1`)
  is always on but renders dark.
- **No front RGB today.** The berxel front camera publishes only depth; its RGB
  sensor is disabled (`color_enable=false`). Enabling color on the robot stack
  exposes `/depth_detector_node/berxel_cam_nodelet/rgb/rgb_raw` (640×400) — but
  that restarts `depth_detector_node`, which also does obstacle detection, so it
  is a deliberate robot-stack change, not an adapter setting.
- **Binary payload, not base64** for snapshots (MQTT payloads are binary-safe) →
  saves ~33%.
- For lower live-view bandwidth, prefer **WebRTC/H.264** transport in
  `web_video_server` over MJPEG.
- **Throttle/debounce** event snapshots so a flapping `slowdown` state cannot spam
  the broker.

## Implementation (this repo)

Status: **implemented** (adapter side). The onboard `web_video_server` install is
a one-time operator step.

### Onboard `web_video_server`
- `scripts/setup-web-video-server-on-onboard.sh [user@host]` — installs
  `ros-<distro>-web-video-server` and a systemd unit, then enables it. Needs sudo
  on the onboard PC, so it uploads the installer and runs it over an interactive
  SSH session (asks for the sudo password). Default target `ucore@192.168.3.222`,
  port `8080`.
  - **Portable across ROS versions without code changes:** the ROS distro is
    auto-detected from `/opt/ros`, and the unit is generated for ROS1
    (`rosrun` + `ROS_MASTER_URI`) or ROS2 (`ros2 run`). Pin with `ROS_DISTRO=`
    when several distros are installed. Assumes `apt` + `systemd` (Ubuntu).
  - On the onboard PC this installs just **2 small packages**
    (`ros-<distro>-web-video-server` + `ros-<distro>-async-web-server-cpp`);
    nothing upgraded or removed.
  - **The JIBOT onboard has no internet**, so `apt` fails with a mirror/DNS error.
    Prebuilt arm64 debs are committed at `../../scripts/offline-debs/web-video-server/`;
    `sudo dpkg -i` them, then run the setup script (it skips apt when the package
    is already installed and just installs the systemd service). All other deps
    are already present (`libboost-regex1.71.0` provides the `-icu66` virtual).
- [`scripts/systemd/amr-camera.service`](../../scripts/systemd/amr-camera.service) — reference copy of the ROS1 unit
  (sources ROS, binds `0.0.0.0:8080`, `ROS_MASTER_URI=http://localhost:11311`).
  The installer generates the ROS1 or ROS2 variant automatically.

### Adapter
- Config: `[video]` section in [`../../adaptor/config/config.toml`](../../adaptor/config/config.toml), parsed by
  `VideoConfig` in `adaptor/config/config.py`. Key fields:
  - `web_video_server_url` — local base the adapter pulls snapshots from (`127.0.0.1:8080`).
  - `web_video_server_public_url` — FMS-reachable base advertised for live streams (`192.168.3.222:8080`); falls back to the local base when empty.
  - `stream_type`, `stream_topics`, `snapshot_events`, `snapshot_topic`,
    `snapshot_quality`, `snapshot_mqtt_topic`, `snapshot_debounce_sec`,
    `snapshot_http_timeout_sec`.
- `adaptor/utils/video.py` (`VideoStreamer`): builds stream/snapshot URLs and
  publishes event snapshots. Uses only `urllib` (stdlib) — no rospy, no new deps;
  the blocking HTTP GET runs in a worker thread.
- Event snapshots: `adapter_jibot.py` `_schedule_event_snapshots()` is called
  each state cycle. It edge-triggers on `snapshot_events` substrings appearing in
  the JIBOT mode/status, and fires a background snapshot task (time-debounced in
  the streamer). Snapshot JPEG is published **binary** to
  `amr/v3/<serial>/<snapshot_mqtt_topic>/<event>` (QoS 0, retain false).
- Live video: `requestVideo` instant action
  (`_handle_request_video_instant_action`) returns the stream URL(s) in the
  action result and also keeps a `VIDEO_STREAMS` entry in `state.information`.
  Optional `topic`/`camera` actionParameter requests a single camera.

### MQTT topics introduced
| Direction | Topic | Payload |
|---|---|---|
| AGV → FMS | `amr/v3/<serial>/event_snapshot/<event>` | binary JPEG (event thumbnail) |
| FMS → AGV | `amr/v3/<serial>/instantActions` (`requestVideo`) | request live stream URL(s) |
| AGV → FMS | `amr/v3/<serial>/state` (`information[].VIDEO_STREAMS`, action result) | stream URL(s) |

### Tests
[`../../adaptor/tests/test_video.py`](../../adaptor/tests/test_video.py) — URL building (local vs public base, stream type,
quality), snapshot publish (binary, per-event topic), debounce, disabled no-op.

### Operator steps to go live
1. Install web_video_server (offline debs on the JIBOT onboard — see above), then
   run `scripts/setup-web-video-server-on-onboard.sh ucore@192.168.3.222` to
   install `amr-camera.service`.
2. Confirm `[video].web_video_server_public_url` matches the FMS-reachable host:port.
3. Restart the adapter; the FMS can issue `requestVideo` and subscribe to
   `amr/v3/<serial>/event_snapshot/#`.

### Manage the service
`amr-camera.service` is controlled with systemctl over SSH (status/logs need
no sudo; start/stop/enable/disable need `ssh -t … sudo …`). `stop`/`start` change
the running state; `disable`/`enable` change boot auto-start (independent). Full
command list is in [jibot-client/README.md](../README.md#manage-the-amr-camera-service-ssh).

### Verified (2026-06-09)
Installed offline and ran `web_video_server` on the JIBOT onboard; from another
machine `http://192.168.3.222:8080` returned live frames: `cam2` 640×512 JPEG
(~21 KB), `cam3` (~17 KB), berxel depth (~15 KB), and a proper
`multipart/x-mixed-replace` MJPEG stream. `/bundle_detection_image` timed out at
idle (not publishing) — hence the always-on default for `snapshot_topic`.
