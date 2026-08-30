# JIBOT Camera & Depth Topic Reference

Captured live on 2026-06-09 by SSH into the onboard PC. Documents the two side
cameras and the front depth camera, their ROS topics, physical devices, and how
to view them.

## Onboard System

- **SSH host**: `192.168.3.222` (user `ucore`, hostname resolves to `ubuntu`, pubkey auth)
- **ROS**: **ROS1 Noetic**, master at `http://localhost:11311` (i.e. on the onboard PC itself)
- **Robot id**: `bot300` (`/bot300/robot_description`)
- This repo's dev machine has **no ROS installed** — all inspection must be done on
  the onboard PC or a PC joined to its ROS graph.

### Network interfaces (onboard PC)

| Interface | Address | Role |
|---|---|---|
| `eth0` | `10.8.8.8/24` | Internal **camera LAN** (all three cameras live here) |
| `eth02` | `192.168.2.10/24` | Internal bus (e.g. `192.168.2.30` peer on port 11411) |
| `wlan0` | `192.168.3.222/22` | Management / SSH access network |

Nodes advertise themselves under the hostname `ubuntu`. A remote ROS subscriber
must map `ubuntu -> 192.168.3.222` in `/etc/hosts` and set
`ROS_MASTER_URI=http://192.168.3.222:11311`.

## Camera Summary (all live as of capture)

| Position | Topic | Type / Encoding | Resolution | Rate | Physical device |
|---|---|---|---|---|---|
| Side | `/camera/cam2/image_raw` | `sensor_msgs/Image` · `mono8` (grayscale) | 640×512 | ~5.5 Hz | IP camera **10.8.8.111** |
| Side | `/camera/cam3/image_raw` | `sensor_msgs/Image` · `mono8` (grayscale) | 640×512 | ~5.5 Hz | IP camera **10.8.8.112** |
| Front | `/depth_detector_node/berxel_cam_nodelet/depth/depth_raw` | `sensor_msgs/Image` · `32FC1` (float, meters) | 640×400 | ~10 Hz | Berxel Hawk **10.8.8.51:5163** |

> Topic names end at `image_raw` / `depth_raw`. Earlier copied strings with a
> `_mouse_left` suffix were a mouse-selection paste artifact, not part of the name.

Each `image_raw` also exposes the standard `image_transport` variants
(`/compressed`, `/compressedDepth`, `/theora`) and a matching `camera_info` topic.

## Side Cameras (cam2 / cam3)

- Driver: `qr_camera` nodelet; published by `/apriltag_localization_nodelet_manager`.
- IP mapping (from `/usr/local/urobot/jarvis/share/qr_camera/config/`):
  - `cam2.yaml` → `ip: "10.8.8.111"`
  - `cam3.yaml` → `ip: "10.8.8.112"`
- Both IPs reachable (ARP `REACHABLE`, vendor MAC `dc:a5:01:*`).
- Output is **grayscale (`mono8`)** by sensor design — not a color/decoding issue.

## Front Depth Camera (Berxel Hawk P150E)

- **Not a USB device** — absent from `lsusb`, no `/dev/video*`.
- It is a **network camera at `10.8.8.51`**, port **5163**:
  - Config: `/usr/local/urobot/jarvis/share/bot300/param/berxel-hawk-P150E/berxel-hawk-P150E.yaml` → `ip_addr: '10.8.8.51'`
  - `depth_detector_node` (pid 1149) holds an `ESTAB` TCP connection `10.8.8.8:* -> 10.8.8.51:5163`.
  - Berxel net driver present: `/usr/local/urobot/jarvis/lib/libBerxelNetDriver.so`.
- **Port scan of 10.8.8.51**: only `5163` open. `80`, `554` (RTSP), `8080`, `8554` all **closed**.
  - → No standard RTSP/HTTP stream. Direct access requires the **Berxel SDK/driver**
    speaking the proprietary protocol on port 5163, and the client must be on the
    internal `10.8.8.0/24` camera LAN (i.e. on the robot or a routed host).
  - For practical viewing, prefer the ROS topic (below) over direct device access.

## Viewing

`rqt_image_view` is installed on the onboard PC (`/opt/ros/noetic/bin/rqt_image_view`).

**Option A — run on the robot via X11 forwarding (simplest):**

```bash
ssh -X 192.168.3.222
source /opt/ros/noetic/setup.bash
rqt_image_view          # pick the topic from the top-left dropdown (don't type it)
```

**Option B — remote ROS PC as a master client:**

```bash
export ROS_MASTER_URI=http://192.168.3.222:11311
echo "192.168.3.222 ubuntu" | sudo tee -a /etc/hosts   # nodes advertise as 'ubuntu'
rosrun rqt_image_view rqt_image_view
```

**Depth caveat:** `depth_raw` is `32FC1` (meters). In `rqt_image_view` it renders
almost black. View depth in **RViz** (Image / DepthCloud display) or apply a
colormap instead.

## Verification Commands (on the onboard PC)

```bash
source /opt/ros/noetic/setup.bash
rostopic list | grep -E "image_raw|depth_raw"
rostopic info /camera/cam2/image_raw                       # type + publishers
rostopic echo /camera/cam2/image_raw --noarr -n1           # encoding/width/height
rostopic hz /camera/cam2/image_raw                         # publish rate
rostopic hz /depth_detector_node/berxel_cam_nodelet/depth/depth_raw
```
