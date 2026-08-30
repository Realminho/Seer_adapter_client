# web_video_server offline .deb (arm64 / ROS Noetic)

The JIBOT onboard PC has **no internet access**, so its `apt-get install
ros-noetic-web-video-server` fails with a DNS/mirror error. These prebuilt
**arm64** packages let you install offline.

Contents:
- `ros-noetic-async-web-server-cpp_*_arm64.deb` — HTTP server lib (dependency)
- `ros-noetic-web-video-server_*_arm64.deb` — the server itself

The setup script installs every `*.deb` in this directory and does **not** fall
back to `apt-get`, because the JIBOT onboard PC is expected to be offline.

Some robot images already include dependencies such as `roscpp`,
`image_transport`, `cv_bridge`, `ffmpeg`/`libav*`, OpenCV 4.2, and boost. If
`dpkg -i` reports a missing dependency, copy the matching arm64/focal `.deb`
file into this same directory and re-run setup. For example, images without
`ffmpeg` need the Ubuntu `ffmpeg`, `libavcodec-dev`, `libavformat-dev`,
`libavutil-dev`, `libswscale-dev`, and any dependency packages those require
staged here too. `libboost-regex1.71.0` provides the
`libboost-regex1.71.0-icu66` virtual name the package asks for.

## Automatic install via adapter setup

`scripts/update-jibot-adapter-over-ssh.sh` uploads the repository `scripts/`
directory with the adapter bundle. After upload, running
`scripts/setup-adaptor-service.sh` on the onboard PC installs these bundled
`.deb` files locally. It will not try `apt-get install
ros-noetic-web-video-server`.

The update script checks this directory before upload. If any required
`web_video_server`/`ffmpeg` package pattern is missing, it stops before opening
SSH and prints the missing filenames.

## Manual install on the onboard PC

```bash
# 1) copy the debs over (from a machine that can reach the onboard PC)
ssh ucore@192.168.3.222 'mkdir -p /tmp/wvs_debs'
scp scripts/offline-debs/web-video-server/*.deb ucore@192.168.3.222:/tmp/wvs_debs/
#   (scp not available? stream each: cat file.deb | ssh ucore@HOST 'cat > /tmp/wvs_debs/file.deb')

# 2) install offline (no network needed), including any staged ffmpeg/libav debs
ssh -t ucore@192.168.3.222 'sudo dpkg -i /tmp/wvs_debs/*.deb'
```

Success looks like `Setting up ros-noetic-web-video-server ...` with no
dependency errors.

Then enable the service and configure the adapter — see
[jibot-client/docs/jibot-video-to-fms-design.md](../../../jibot-client/docs/jibot-video-to-fms-design.md)
and the repo README "Camera / Video to FMS" section.

## Refreshing these files

These were fetched from `packages.ros.org` (which hosts arm64 for focal). To
update, read the exact `Filename:` from the arm64 index and download:

```bash
curl -fsSL http://packages.ros.org/ros/ubuntu/dists/focal/main/binary-arm64/Packages.gz \
  | zcat | grep -A12 '^Package: ros-noetic-web-video-server$' | grep Filename
```
