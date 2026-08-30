# JIBOT onboard PC — SSH access & network

How to reach the JIBOT onboard PCs over SSH, what the network looks like, and how
to run the management scripts against them. Audience: operators and AI agents.

## Robots

There are two JIBOT onboard PCs, both reached as user `ucore` over Wi-Fi
(`wlan0`, the FMS LAN). They run **ROS1 Noetic** (master `localhost:11311`).

| Robot | SSH target        | Notes                                                        |
| ----- | ----------------- | ----------------------------------------------------------- |
| bot A | `ucore@192.168.3.222` | Modern OpenSSH; pubkey works directly. Hostname resolves to `ubuntu`. |
| bot B | `ucore@192.168.3.223` | **Legacy SSH** — needs extra crypto options every connection (see below). |

> The IPs above are the *current* `wlan0` addresses. To change a robot's address,
> use [`scripts/change-jibot-network-over-ssh.sh`](../../scripts/change-jibot-network-over-ssh.sh)
> (edits netplan `wlan0` + `wpa_supplicant`, see the adaptor readme).

## Hardware / platform

Confirmed on **bot A (`192.168.3.222`)** on 2026-06-21 (bot B presumed similar but
not verified):

| Item | Value |
| ---- | ----- |
| Board (device-tree model) | `JRC01_V1.4.3 (Linux Opensource)` — custom ARM carrier |
| CPU | **ARM Cortex-A53, 6 cores** (`aarch64`) — modest, low-power |
| RAM / swap | **3.7 GiB** + 2.0 GiB swap |
| Storage | 29 GB `/dev/root` (eMMC/SD), ~56 % used |
| OS / kernel | **Ubuntu 20.04.6 LTS**, kernel **5.10.198 `aarch64`**, hostname `ubuntu` |
| GPU | **none** — see below |

- **No NVIDIA GPU and no CUDA.** No PCI GPU (`lspci`), no `nvidia` kernel module,
  no `/proc/driver/nvidia`, and `nvidia-smi` / `nvcc` / `tegrastats` are all absent;
  it is **not** a Jetson/Tegra (`/etc/nv_tegra_release` missing). Installing "NVIDIA
  CUDA drivers" on this board **cannot work**, and `nvidia-smi: command not found` is
  the **expected** state, not a broken install. GPU-accelerated TTS via CUDA is not
  possible here — use a CPU path (or the SoC's own NPU via its SDK, not CUDA).
- **Performance implication.** 6× Cortex-A53 + 3.7 GiB is modest, so the full
  perception+nav stack keeps the cores hot (load typically ~6–8). Right after a
  reboot, when everything spins up at once, the control loop can briefly miss its
  deadline (`ERROR0702 "robot main loop stuck"`) and the onboard 7273 server can
  drop client sockets until the stack settles. See
  [`jibot-jmanager-reconnect-troubleshooting.md`](jibot-jmanager-reconnect-troubleshooting.md).
- **Serial links (motion / peripherals).** A 4-port FTDI (`Quad RS232-HS`) provides
  `/dev/ttyUSB0`–`ttyUSB3`, plus a Prolific PL2303 (`USB-Serial Controller D`) on
  `/dev/ttyUSB4`; all on the SoC xHCI controller, group `dialout`. These carry the
  logic-core/MCU and peripheral links. (The firmware-flash bridge `jfservice` uses
  one of these as "COM1" — see the troubleshooting doc.)

## Network layout (per robot)

- `wlan0` — FMS LAN, e.g. `192.168.3.222/22`, gateway `192.168.3.120`. This is the
  SSH path. **Subnet is `/22`, not `/24`.**
- `eth0` — internal camera LAN `10.8.8.0/24` (onboard `10.8.8.8`). IP cameras and
  the Berxel depth camera live here; see
  [`jibot-client/docs/jibot-camera-topics.md`](../../jibot-client/docs/jibot-camera-topics.md).
- Robot software: `urobot.service` (systemd, "bringup urobot"), installed under
  `/usr/local/urobot` (params at `/usr/local/urobot/params/{map,routes}`).
- Adapter deploy dir varies per robot (e.g. `~/jibotadpt` or `~/adapter`); pass the
  right one to the scripts with `--remote-dir` / the positional arg.

## Connecting

### bot A (192.168.3.222) — normal

```bash
ssh ucore@192.168.3.222
```

### bot B (192.168.3.223) — legacy crypto required

Its SSH server only offers old algorithms, so a modern client must opt back in.
The two **required** options are the key-exchange and cipher; the `ssh-rsa` ones
are harmless extras (only needed if an RSA key/host key is involved):

```bash
ssh \
  -o KexAlgorithms=+diffie-hellman-group14-sha1 \
  -o Ciphers=+aes128-cbc \
  -o HostKeyAlgorithms=+ssh-rsa \
  -o PubkeyAcceptedKeyTypes=+ssh-rsa \
  ucore@192.168.3.223
```

#### Make it permanent (recommended): `~/.ssh/config`

Add a host block so plain `ssh ucore@192.168.3.223` and all the management scripts
work without repeating `-o` flags:

```ssh-config
Host 192.168.3.223 jibot223
    HostName 192.168.3.223
    User ucore
    KexAlgorithms +diffie-hellman-group14-sha1
    Ciphers +aes128-cbc
    HostKeyAlgorithms +ssh-rsa
    PubkeyAcceptedAlgorithms +ssh-rsa
```

(`PubkeyAcceptedAlgorithms` is the current name; older OpenSSH calls it
`PubkeyAcceptedKeyTypes` — use whichever your `ssh -V` accepts.)

## Installing your key with `ssh-copy-id`

Pubkey auth needs your public key in the robot's `~/.ssh/authorized_keys`. Copy it
once (you'll be asked for the `ucore` password a single time):

```bash
# bot A
ssh-copy-id -i ~/.ssh/id_ed25519.pub ucore@192.168.3.222

# bot B — the legacy crypto options are still needed for the copy itself,
# because the transport is negotiated before authentication
ssh-copy-id -i ~/.ssh/id_ed25519.pub \
  -o KexAlgorithms=+diffie-hellman-group14-sha1 \
  -o Ciphers=+aes128-cbc \
  -o HostKeyAlgorithms=+ssh-rsa \
  -o PubkeyAcceptedKeyTypes=+ssh-rsa \
  ucore@192.168.3.223
```

If you added the `~/.ssh/config` block above, bot B simplifies to:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub jibot223
```

After this, `ssh ucore@192.168.3.223` connects with the key (the legacy crypto
options are still required on every connection — they are about the transport, not
the key — which is why the `~/.ssh/config` block is the cleanest fix).

Use `-i <path>.pub` to pick a specific key; without it, `ssh-copy-id` copies your
default identity (`~/.ssh/id_ed25519.pub` or `~/.ssh/id_rsa.pub`).

## Running the management scripts against bot B

All the `*-over-ssh.sh` scripts accept `SSH_OPTS` for extra ssh flags, so they can
reach bot B even without a `~/.ssh/config` block:

```bash
SSH_OPTS="-o KexAlgorithms=+diffie-hellman-group14-sha1 -o Ciphers=+aes128-cbc \
  -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedKeyTypes=+ssh-rsa" \
  scripts/update-jibot-adapter-config.sh --video-host 192.168.3.99 \
    --host ucore@192.168.3.223 --remote-dir '~/jibotadpt' --dry-run
```

With the `~/.ssh/config` block in place, drop `SSH_OPTS` entirely.

## Related scripts & docs

- [`scripts/change-jibot-network-over-ssh.sh`](../../scripts/change-jibot-network-over-ssh.sh) — change `wlan0` IP/subnet/gateway + Wi-Fi SSID/PSK on the robot.
- [`scripts/update-jibot-adapter-config.sh`](../../scripts/update-jibot-adapter-config.sh) — change the adapter `config.toml` video URL, local and/or remote. Robot control IPs live in `config/robots.hcl`.
- [`scripts/update-jibot-adapter-over-ssh.sh`](../../scripts/update-jibot-adapter-over-ssh.sh) — deploy the adapter over SSH.
- [`scripts/fetch-jibot-params-over-ssh.sh`](../../scripts/fetch-jibot-params-over-ssh.sh) — pull `urobot` map/route params.
- Camera/topic map: [`jibot-client/docs/jibot-camera-topics.md`](../../jibot-client/docs/jibot-camera-topics.md).
- Adapter install/run + the network/config scripts in detail: [`adaptor/readme.md`](../../adaptor/readme.md).
- "JManager reconnect" / robot-won't-move diagnosis: [`jibot-jmanager-reconnect-troubleshooting.md`](jibot-jmanager-reconnect-troubleshooting.md).
