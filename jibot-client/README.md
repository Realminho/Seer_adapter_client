# jibot-client

Python client for direct TCP communication with JIBOT AMR.

The package intentionally keeps adapter-specific behavior outside of the client
so multiple adapters can reuse the same robot communication layer.

```python
from jibot_client import JIBOT

vehicle = JIBOT("192.168.3.10", 7273)
```

## Recording

Set `JIBOT_RECORD=1` to write JIBOT TCP request/response JSONL logs.

```bash
JIBOT_RECORD=1 JIBOT_RECORD_DIR=logs/jibot python3 main.py
```

Use `JIBOT_RECORD_FILE=/path/to/session.jsonl` to force a specific output file.

## Camera / Video to FMS

Camera video is delivered to the FMS without relaying raw pixels through MQTT.
`web_video_server` (a ROS1 node on the onboard PC) subscribes to ROS image topics
and serves them over HTTP; the client/adaptor is a thin HTTP+MQTT consumer.

```text
ROS image topic --(subscribe)--> web_video_server (HTTP :8080) --HTTP--+--> FMS (live MJPEG/WebRTC, direct)
                                                                       +--> adapter GET /snapshot --> MQTT (event thumbnail)
```

- Live view: FMS sends a `requestVideo` instant action; the adapter replies with
  the stream URL(s) in the action result and `state.information.VIDEO_STREAMS`.
- Event snapshots: on relevant state transitions, the adapter pulls a JPEG from
  `/snapshot` and publishes it as binary to
  `amr/v3/<serial>/event_snapshot/<event>`. Use a `[video].snapshot_topic` that
  is continuously publishing.

Design and rationale: [docs/jibot-video-to-fms-design.md](docs/jibot-video-to-fms-design.md).
Camera topic map: [docs/jibot-camera-topics.md](docs/jibot-camera-topics.md).

### Install amr-camera on the onboard PC

One-time operator step (needs sudo on the onboard PC, so it runs over an interactive
SSH session and asks for the sudo password):

```bash
# Default target ucore@192.168.3.222, port 8080
../scripts/setup-web-video-server-on-onboard.sh ucore@192.168.3.222
```

This verifies `ros-<distro>-web-video-server` is already installed, writes/enables `amr-camera.service`
([../scripts/systemd/amr-camera.service](../scripts/systemd/amr-camera.service)),
and prints test URLs. Override with `SSH_PORT`, `ROS_DISTRO`, `WEB_VIDEO_PORT`.
It does not install packages from the internet.

**Offline robots (the JIBOT onboard has no internet):** install the prebuilt
arm64 debs first, then run the script:

```bash
ssh ucore@192.168.3.222 'mkdir -p /tmp/wvs_debs'
scp ../scripts/offline-debs/web-video-server/*.deb ucore@192.168.3.222:/tmp/wvs_debs/
ssh -t ucore@192.168.3.222 'sudo dpkg -i /tmp/wvs_debs/*.deb'
../scripts/setup-web-video-server-on-onboard.sh ucore@192.168.3.222   # installs amr-camera.service
```

See [../scripts/offline-debs/web-video-server/README.md](../scripts/offline-debs/web-video-server/README.md).

To install and run it through systemd manually on the onboard PC:

```bash
# 1. Install the unit file.
sudo cp ../scripts/systemd/amr-camera.service /etc/systemd/system/amr-camera.service

# 2. Adjust User=, the ROS setup path, and the port if this robot differs.
sudo vi /etc/systemd/system/amr-camera.service

# 3. Reload systemd, enable boot auto-start, and start it now.
sudo systemctl daemon-reload
sudo systemctl enable --now amr-camera.service

# 4. Check status and logs.
systemctl status amr-camera --no-pager
journalctl -u amr-camera -n 50 --no-pager
```

The checked-in unit is a ROS1/noetic reference. For another ROS distro or ROS2,
change `/opt/ros/<distro>/setup.bash`, the `rosrun`/`ros2 run` command, and the
port (`8080`) as needed. The setup script generates this automatically from the
detected ROS install.

### Manage the amr-camera service (SSH)

Status and logs are read-only (no sudo). Start/stop/enable/disable need sudo, so
use `ssh -t` to get a tty for the password. `stop`/`start` change the *current*
state; `disable`/`enable` change *boot auto-start* — they are independent.

```bash
# status / logs (no sudo)
ssh ucore@192.168.3.222 'systemctl status amr-camera --no-pager'
ssh ucore@192.168.3.222 'systemctl is-enabled amr-camera; systemctl is-active amr-camera'
ssh ucore@192.168.3.222 'journalctl -u amr-camera -n 50 --no-pager'

# turn off / on / restart now (sudo)
ssh -t ucore@192.168.3.222 'sudo systemctl stop amr-camera'
ssh -t ucore@192.168.3.222 'sudo systemctl start amr-camera'
ssh -t ucore@192.168.3.222 'sudo systemctl restart amr-camera'

# boot auto-start off / on (does not change the running state)
ssh -t ucore@192.168.3.222 'sudo systemctl disable amr-camera'
ssh -t ucore@192.168.3.222 'sudo systemctl enable amr-camera'

# combined: stop now + no auto-start / start now + auto-start
ssh -t ucore@192.168.3.222 'sudo systemctl disable --now amr-camera'
ssh -t ucore@192.168.3.222 'sudo systemctl enable --now amr-camera'

# remove the service entirely (keeps the installed package)
ssh -t ucore@192.168.3.222 'sudo systemctl disable --now amr-camera; sudo rm /etc/systemd/system/amr-camera.service; sudo systemctl daemon-reload'
```

To run it once **without** the service, `stop` it first (frees port 8080), then
`rosrun web_video_server web_video_server _port:=8080 _address:=0.0.0.0`.

Then configure the `[video]` section in
[../adaptor/config/config.toml](../adaptor/config/config.toml) — in particular set
`web_video_server_public_url` to the onboard host:port the FMS can reach
(e.g. `http://192.168.3.222:8080`) — and restart the adapter.

```bash
# topic list / single frame / live stream (from a host that can reach the onboard PC)
curl -s http://192.168.3.222:8080/ | head
curl -s "http://192.168.3.222:8080/snapshot?topic=/camera/cam2/image_raw" -o frame.jpg
# browser: http://192.168.3.222:8080/stream?topic=/camera/cam2/image_raw
```
Sensitive fields such as `password`, `token`, and `api_key` are masked.
