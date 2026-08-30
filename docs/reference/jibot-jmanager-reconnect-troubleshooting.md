# JIBOT onboard — "JManager reconnect" troubleshooting

What the HMI message **"JManager reconnect …"** means, why the robot can accept a
command yet not move, and how to diagnose it over SSH. Audience: operators and AI
agents. Companion to [`jibot-onboard-access.md`](jibot-onboard-access.md).

First captured while debugging robot **bot A (`ucore@192.168.3.222`)** on
2026-06-21; see [Incident: 2026-06-21](#incident-2026-06-21) for the concrete case.

## TL;DR

- **"JManager" is not our adapter.** It is the **urobot main program's TCP server**
  (`JSrvTcp`) listening on **port 7273** — the same endpoint our VDA5050 adapter
  calls "JIBOT" (`127.0.0.1:7273`).
- The external **HMI touch panel** and the **`bz_robot` motion controller** are both
  clients of that 7273 server. When a client can't be serviced, `bz_robot` writes
  `JManager reconnect …` as its status; the HMI just mirrors it.
- So "JManager reconnect" + "won't move" is almost always a **downstream symptom**,
  not a dead service. The two common root causes:
  1. **E-stop engaged** → motors disabled → commands accepted but no motion.
  2. **Boot-time CPU saturation** → the 7273 server loop overruns its 96 ms budget →
     client sockets reset → reconnect churn. Self-clears once the stack settles.

## Architecture: who talks to 7273

```
external HMI (192.168.3.26) ─┐
VDA5050 adapter (our code) ──┼──► urobot main program : JSrvTcp server : 127.0.0.1:7273  ("JManager" / "JIBOT")
bz_robot (motion control) ──┘         ▲
                                      └─ bz_robot is a *client*; when unserviced it logs "JManager reconnect …"
```

- The server is part of `urobot.service` ("bringup urobot", `/usr/local/urobot`),
  ROS1 Noetic. It is **not** a separate `jmanager` binary and **not** `jfservice`.
- Log line confirming the server: `JSrvTcp … SrvTcp: server started(port: 7273)`.
- A healthy HMI session looks like repeated
  `JSrvClient … ServerC: 192.168.3.26 handle UmGetLaser cost 13ms` (costs in the
  low tens of ms). Costs climbing past the loop budget, or
  `JSocket … recv error[104]: Connection reset by peer`, indicate trouble.

## Why "command dispatched but robot doesn't move"

The high-level command path (HMI/adapter → 7273) can be alive while the robot still
won't move, because motion also requires the motors to be **enabled** and the
control loop to be **healthy**:

- E-stop / disable: `_robot_ros … RROS: recv motor disabled!`, then
  `InfoStatus … ROBOT STATUS: state(Stopped) … odom(… v 0, w 0)`.
- Re-enable (E-stop released): `_robot_ros … RROS: recv motor enabled!`.
- Control-loop watchdog: `error_code … ERROR0702 … "robot main loop stuck"` (fires
  when the loop misses its deadline, e.g. under CPU saturation at startup).

## Diagnostic commands (read-only, no sudo)

Run against the robot over SSH (see [`jibot-onboard-access.md`](jibot-onboard-access.md)
for connection details). The current urobot main log is the richest source.

```bash
# Newest urobot main log
L=$(ls -t /usr/local/urobot/log/log-*.log | head -1)

# Is the 7273 server up and serving the HMI? (low ms cost = healthy)
grep -nE 'SrvTcp: server started|ServerC: .* handle .* cost|reset by peer' "$L" | tail -20

# Motor enable/disable + robot state transitions
grep -nE 'recv motor (en|dis)abled|ROBOT STATUS: state\(' "$L" | tail -15

# Control-loop / fault watchdog
grep -oE 'ERROR[0-9]{4}' "$L" | sort | uniq -c | sort -rn | head      # ERROR0702 = main loop stuck

# Load vs cores (6 cores; load >> 6 sustained = saturation)
echo "nproc=$(nproc) load=$(cut -d' ' -f1-3 /proc/loadavg)"; top -bn1 | head -18

# bz_robot status CSV (field 4 carries "JManager reconnect ...")
tail -n 5 "$(ls -t /usr/local/urobot/logs/bot_log/st_*.txt | head -1)"
```

## Hardware: there is **no NVIDIA GPU / CUDA** on the onboard PC

Confirmed on bot A (`192.168.3.222`), 2026-06-21:

| Check | Result |
| ----- | ------ |
| Architecture | `aarch64` (ARM) |
| Device-tree model | `JRC01_V1.4.3` (Rockchip-class SoC; `dw-apb-uart ff370000.serial`) |
| PCI GPU (`lspci`) | none |
| NVIDIA kernel module / `/proc/driver/nvidia` | absent |
| `nvidia-smi`, `nvcc`, `tegrastats` | not present |
| Jetson/Tegra (`/etc/nv_tegra_release`) | not a Jetson |

Implications:

- **Installing "NVIDIA CUDA drivers" on this board cannot work** — there is no
  NVIDIA GPU. `nvidia-smi: command not found` is the **expected** state, not a sign
  of a broken install. GPU-accelerated TTS via CUDA is not possible here; use a
  CPU path (or the SoC NPU/Mali via its own SDK, not CUDA).
- If you saw `nvidia-persistenced` / `sys-bus-pci-drivers-nvidia.device` while
  debugging, that was the **local workstation**, not the robot. Always confirm the
  SSH target.

## Red herrings (seen failing, but NOT the cause of motion loss)

- **`amr-adaptor.service` inactive** — our VDA5050 adapter is just another 7273
  client. It currently exits cleanly after a failed connect with a Python error
  `unsupported operand type(s) for |: 'type' and 'NoneType'` (a `X | None`
  annotation evaluated at runtime, needs Python ≥ 3.10). Worth its own fix, but
  unrelated to the HMI/robot-motion issue.
- **`jfservice.service` failed** (`status=203/EXEC`, `/usr/local/jfservice` missing)
  — a **firmware-flash serial bridge** (`jftool ⟷ jfservice ⟷ 逻辑core/MCU`), only
  needed when flashing the logic-core firmware. Readme: `/usr/local/JLogService/readme/`.
  Pre-existing, unrelated to motion.
- **`systemd-modules-load.service` failed** — `lp` / `ppdev` / `parport_pc` alias
  "Function not implemented". Cosmetic on this kernel; unrelated.

## Incident: 2026-06-21

**Symptom.** HMI touch panel showed "JManager reconnect …"; `UmGoTo` was accepted but
the robot did not move. Happened after the operator started a large TTS + "NVIDIA
CUDA driver" install, interrupted it, and rebooted.

**Root cause — two overlapping, neither one OS corruption:**

1. **E-stop was engaged.** `RROS: recv motor disabled!`, `state(Stopped)`. Releasing
   it produced `RROS: recv motor enabled!` and motion was restored.
2. **Startup CPU saturation.** Right after the reboot the full perception+nav stack
   came up together; load reached **7–8 on 6 cores**; the 7273 server loop overran
   (`SrvTcp: loop takes too long(148ms > 96ms)`, `run cost 135ms`) and HMI sockets
   dropped (`recv error[104]: Connection reset by peer`) → the "reconnect" churn.
   It **self-cleared within ~1–2 min**; by 20:21 the HMI (`192.168.3.26`) was
   handling `UmGetLaser` / `UmGetRobotInfo` / `UmGetMotorState` in 11–24 ms.

**System integrity after the interrupted install — clean:**

- `dpkg` clean (no half-installed/half-configured packages), no leftover
  apt/dpkg/dkms processes.
- Disk 56 % used; no OOM, hung-task, or NVIDIA/serial errors in `dmesg`.
- USB serial motion boards enumerated normally (`ftdi_sio → ttyUSB2/3`,
  `pl2303 → ttyUSB4`).
- The CUDA install attempt was moot anyway (no NVIDIA GPU — see above).

**Resolution.** Release the E-stop; let the stack settle after reboot; confirm
healthy HMI traffic in the urobot main log. No OS/package repair was required.

**Optional follow-ups (not urgent):** if GPU-accelerated TTS was the goal, it is not
achievable via CUDA on this ARM board — pick a CPU/NPU path. The
`amr-adaptor` Python `|`-union crash and the missing `jfservice` install are
separate, pre-existing items.

## Incident: 2026-06-25

**Symptom.** Reported that **TCP 7273 was not open** on bot A (`192.168.3.222`) and a
question of whether `urobot.service` was running at all.

**State when checked (~15:45):** the problem had **already been recovered by hand**.

- `urobot.service` → **active (running)**, `enabled`, `NRestarts=0`, current Main PID 6285.
- **7273 listening and externally reachable**: `ss -tlnp` shows
  `0.0.0.0:7273 users:(("jarvis-g",pid=7212))`, and a raw TCP connect from the dev
  workstation succeeded. (`jarvis-g` is the 7273 server process; cf. `JSrvTcp` in the
  TL;DR — same endpoint.)

**What actually happened — boot brought 7273 up unhealthy, a human restart fixed it.**
The whole sequence is inside *one* boot (no reboot):

```
14:47:49  boot
14:47:56  urobot.service auto-starts at boot   (1st instance, roslaunch PID 831)
            └─ 7273 NOT serving for the next ~19 min  (matches the mg_main boot-timeout
               pattern: urobot stays `active`, but the 7273 server never reaches a
               healthy serving state — see scripts/robot-host/README.md)
15:06:35  operator `ucore` SSHes in (PWD=/home/ucore/adapter) and runs
            sudo systemctl restart urobot          ← MANUAL recovery, not the watchdog
15:06:40  stop times out → systemd SIGKILLs roslaunch/nodelets → re-started
15:07:30  stopping again → 15:07:35 stop timeout
15:07:51  Started  → current healthy instance (Main PID 6285), 7273 up
```

**Reading the systemd log:**

- `urobot.service: Failed with result 'timeout'` here is a **stop-phase** timeout, not a
  crash. The unit has no graceful `ExecStop` for the ROS stack, so `systemctl restart`
  always SIGKILLs roslaunch — this is the **normal** restart shape on this robot, not a
  fault.
- `NRestarts=0` with `ExecMainStartTimestamp=15:07:51` is the tell that the 15:07 restart
  was **manual/scripted** (`systemctl restart`), not a systemd auto-restart (those would
  increment `NRestarts`).

**Root cause.** The first (boot) urobot instance did not get the 7273 server into a
serving state — the documented `mg_main` "Bot start failed!" boot-timeout (urobot stays
`active`, 7273 + `map` dead, no self-clear). It sat dead for ~19 min and was only cleared
when a person manually restarted urobot.

**The auto-recovery watchdog was not effective here.** `scripts/robot-host/install-urobot-watchdog.sh`
(measures A-2 boot-delay + B-2 60 s watchdog timer) is designed to auto-restart urobot
when 7273 stays down past `MIN_UPTIME` (~150 s) — yet the robot sat dead ~19 min until a
human intervened, and `systemctl list-units` showed no watchdog unit. Strong evidence the
watchdog is **not installed / not armed on `.222`** (to be confirmed next dig).

**Secondary findings (separate from 7273, did NOT block bringup):**

- **Left/right side lasers (`m04b_node`, PIDs 6736/6753) cannot open their serial port** —
  `serialOpen(): cannot open serial port. No such file or directory`, repeating every ~3 s
  in the *current* healthy instance. Five `ttyUSB*` exist (FTDI Quad `ttyUSB0–3` +
  PL2303 `ttyUSB4`), so the m04b config points at a device path that isn't present →
  side obstacle detection / localization degraded. udev-mapping or wiring issue.
- **Load average 7.34 on 6 cores** at check time — consistent with the boot-saturation
  story; partly inflated by the lasers' 3 s serial-retry spin.

### Open threads for the next dig

1. **Confirm the watchdog is missing on `.222`, then deploy it.**
   ```bash
   systemctl list-timers 'urobot-watchdog*' --no-pager        # expect: none → not installed
   sudo /usr/local/sbin/urobot-watchdog; echo exit=$?         # command-not-found → not installed
   # deploy (idempotent), see scripts/robot-host/README.md:
   scp scripts/robot-host/install-urobot-watchdog.sh ucore@192.168.3.222:~/
   sudo ~/install-urobot-watchdog.sh
   ```
2. **Directly confirm the boot-time 7273 failure** in the urobot *main* log (different path
   from the per-run jarvis logs — the `jarvis_g-*.log` grep returned nothing this time):
   ```bash
   L=$(ls -t /usr/local/urobot/log/log-*.log | head -1)
   grep -nE 'Bot start failed|exit\[-1\]|SrvTcp: server started|port: 7273' "$L"
   ```
   Look for `JSignal: exit[-1] … "Bot start failed!" … run time 51s` in the 14:47–15:06 window.
3. **Side-laser serial mapping** — which device path does `m04b` expect vs. what `ls
   /dev/serial/by-id` and `lsusb` show; is a udev rule / by-id symlink missing.
