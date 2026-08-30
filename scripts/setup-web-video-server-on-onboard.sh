#!/usr/bin/env bash
# Installs and service-enables web_video_server on a robot onboard PC so the FMS
# can pull live camera streams (and the adapter can pull event snapshots) over
# HTTP. web_video_server exposes every ROS image topic at:
#   http://<host>:9001/stream?topic=/camera/cam2/image_raw     (live MJPEG)
#   http://<host>:9001/snapshot?topic=/camera/cam2/image_raw   (single JPEG)
#
# Video pixels never go through MQTT; only stream URLs and small event snapshots
# touch the broker. See jibot-client/docs/jibot-video-to-fms-design.md.
#
# Portability: works without code changes on Ubuntu + ROS1 (e.g. noetic) and
# Ubuntu + ROS2 (e.g. humble). The ROS distro is auto-detected from /opt/ros and
# the systemd unit is generated for ROS1 (rosrun + ROS_MASTER_URI) or ROS2
# (ros2 run). Override detection with WEB_VIDEO_ROS_DISTRO=<distro>. Requires
# the web_video_server package to be installed already, plus systemd.
#
# apt and systemd steps need sudo on the onboard PC. The remote installer is
# uploaded as a file first, then run over an interactive SSH session (a tty) so
# sudo can prompt for the password without stdin being tied up by a heredoc.
set -euo pipefail

REMOTE_HOST="${1:-ucore@192.168.3.222}"
SSH_PORT="${SSH_PORT:-22}"
# Empty => auto-detect the ROS distro on the onboard PC. Use a dedicated
# override name so the local shell's ROS_DISTRO does not leak into the robot.
REMOTE_ROS_DISTRO="${WEB_VIDEO_ROS_DISTRO:-}"
WEB_VIDEO_PORT="${WEB_VIDEO_PORT:-9001}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/setup-web-video-server-on-onboard.sh [user@host]

Defaults:
  user@host        ucore@192.168.3.222
Environment:
  SSH_PORT=22
  WEB_VIDEO_ROS_DISTRO=<empty>
               auto-detect on the onboard PC (set to pin, e.g. noetic/humble)
  WEB_VIDEO_PORT=9001

What it does on the onboard PC (needs sudo there):
  1. detect the ROS distro + ROS version (1 or 2)
  2. verify ros-<distro>-web-video-server is already installed
  3. install a systemd unit generated for ROS1 or ROS2
  4. enable + start the service, then print status and test URLs
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

# Reject copy-paste artifacts (em-dash, quotes, spaces) so a bad arg fails fast
# with a clear message instead of "Could not resolve hostname".
if [[ ! "${REMOTE_HOST}" =~ ^[A-Za-z0-9._@-]+$ ]]; then
  echo "ERROR: invalid host argument: '${REMOTE_HOST}'" >&2
  echo "Expected [user@]host, e.g. ucore@192.168.3.222" >&2
  echo >&2
  usage >&2
  exit 2
fi

echo "[web_video_server] target=${REMOTE_HOST} distro=${REMOTE_ROS_DISTRO:-auto} port=${WEB_VIDEO_PORT}"

# The remote installer. ROS_DISTRO (maybe empty) and WEB_VIDEO_PORT are passed on
# the run line; the distro and ROS version are auto-detected when unset.
REMOTE_SCRIPT="$(cat <<'REMOTE'
set -euo pipefail

# --- detect the ROS distro -------------------------------------------------
if [[ -z "${ROS_DISTRO:-}" ]]; then
  mapfile -t FOUND < <(ls -1 /opt/ros 2>/dev/null || true)
  if [[ "${#FOUND[@]}" -eq 0 ]]; then
    echo "[remote] ERROR: no ROS install found under /opt/ros" >&2
    exit 1
  elif [[ "${#FOUND[@]}" -gt 1 ]]; then
    echo "[remote] ERROR: multiple ROS distros found (${FOUND[*]}); rerun with ROS_DISTRO=<one>" >&2
    exit 1
  fi
  ROS_DISTRO="${FOUND[0]}"
fi

SETUP="/opt/ros/${ROS_DISTRO}/setup.bash"
if [[ ! -f "${SETUP}" ]]; then
  echo "[remote] ERROR: ${SETUP} not found" >&2
  exit 1
fi

# --- detect ROS 1 vs ROS 2 (ROS_VERSION is exported by setup.bash) ---------
# ROS setup/profile scripts are not nounset/errexit clean, so relax around it.
set +eu
# shellcheck disable=SC1090
source "${SETUP}"
ROS_VER="${ROS_VERSION:-1}"
set -eu
echo "[remote] ROS_DISTRO=${ROS_DISTRO} ROS_VERSION=${ROS_VER}"

PKG="ros-${ROS_DISTRO}-web-video-server"
if dpkg -s "${PKG}" >/dev/null 2>&1; then
  echo "[remote] ${PKG} already installed; continuing."
else
  echo "[remote] ERROR: ${PKG} is not installed." >&2
  echo "[remote] This robot is expected to be offline; install the bundled offline debs first:" >&2
  echo "[remote]   scripts/offline-debs/web-video-server/  (see its README)" >&2
  exit 1
fi

# --- generate the ExecStart line for the detected ROS version --------------
if [[ "${ROS_VER}" == "2" ]]; then
  MASTER_ENV="# ROS2 has no master"
  EXEC_LINE="/bin/bash -lc 'source ${SETUP} && exec ros2 run web_video_server web_video_server --ros-args -p port:=${WEB_VIDEO_PORT} -p address:=0.0.0.0'"
else
  MASTER_ENV="Environment=ROS_MASTER_URI=http://localhost:11311"
  EXEC_LINE="/bin/bash -lc 'source ${SETUP} && exec rosrun web_video_server web_video_server _port:=${WEB_VIDEO_PORT} _address:=0.0.0.0'"
fi

echo "[remote] writing systemd unit ..."
sudo tee /etc/systemd/system/amr-camera.service >/dev/null <<UNIT
[Unit]
Description=AMR camera (ROS image topics over HTTP)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
${MASTER_ENV}
ExecStart=${EXEC_LINE}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

echo "[remote] enabling + starting service ..."
sudo systemctl daemon-reload
# Free port ${WEB_VIDEO_PORT} from any ad-hoc/manual instance so the managed
# service can bind it. Do not use `pkill -f web_video_server`: this installer is
# named setup_web_video_server and would match itself.
if command -v fuser >/dev/null 2>&1; then
  sudo fuser -k "${WEB_VIDEO_PORT}/tcp" >/dev/null 2>&1 || true
  sleep 1
fi
sudo systemctl enable --now amr-camera.service
sleep 2
sudo systemctl --no-pager --full status amr-camera.service | head -n 12 || true

HOSTIP=$(hostname -I | awk '{print $1}')
echo
echo "[remote] web_video_server should be up. Test endpoints:"
echo "  http://${HOSTIP}:${WEB_VIDEO_PORT}/                                      (topic list)"
echo "  http://${HOSTIP}:${WEB_VIDEO_PORT}/snapshot?topic=/camera/cam2/image_raw"
echo "  http://${HOSTIP}:${WEB_VIDEO_PORT}/stream?topic=/camera/cam2/image_raw"
REMOTE
)"

REMOTE_TMP="/tmp/setup_web_video_server.$$.sh"

# Step 1: upload the installer (no tty needed; stdin is the script text).
printf '%s\n' "${REMOTE_SCRIPT}" | ssh -p "${SSH_PORT}" "${REMOTE_HOST}" "cat > '${REMOTE_TMP}'"

# Step 2: run it interactively so the remote sudo can prompt on a real tty.
ssh -t -p "${SSH_PORT}" "${REMOTE_HOST}" \
  "ROS_DISTRO='${REMOTE_ROS_DISTRO}' WEB_VIDEO_PORT='${WEB_VIDEO_PORT}' bash '${REMOTE_TMP}'; status=\$?; rm -f '${REMOTE_TMP}'; exit \$status"

echo "[web_video_server] done. Update adapter config [video].web_video_server_public_url to the printed host:port if it differs."
