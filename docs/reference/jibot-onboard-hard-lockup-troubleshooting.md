# JIBOT onboard — full hard-lockup ("PC down", SSH-dead) troubleshooting

What it means when a JIBOT onboard PC **goes completely down** — unreachable by
SSH, frozen until a human power-cycles it — how to confirm it forensically after
the fact, what it is *not*, and how to add auto-recovery and crash capture.
Audience: operators and AI agents. Companion to
[`jibot-onboard-access.md`](jibot-onboard-access.md) and
[`jibot-jmanager-reconnect-troubleshooting.md`](jibot-jmanager-reconnect-troubleshooting.md).

First captured on **bot A (`ucore@192.168.3.222`)** on 2026-06-22; see
[Incident: 2026-06-22](#incident-2026-06-22).

## TL;DR

- **This is a different failure from "JManager reconnect."** In the JManager case
  SSH still works and only the robot won't move. Here the **whole host freezes** —
  SSH, ping, everything — and it **stays dead until someone power-cycles it**.
- The forensic signature is a **hard lockup**: the kernel journal *and* the
  MCU-fed `bz_robot` status CSV stop at the **same second, mid-line**, with **no**
  kernel panic / OOM / thermal trip / shutdown sequence. Two independent logging
  paths dying together ⇒ the whole SoC wedged instantly.
- On the 2026-06-22 case, **battery and power were healthy at the instant of the
  freeze** (90 %, 53.11 V, flat) — so it was *not* a flat battery or brownout.
- It stays down for hours because **nothing auto-recovers it**: the hardware
  watchdog exists but is not armed (`RuntimeWatchdogSec=0`), and `ramoops`/pstore
  can't capture a crash dump (device-tree has no `reserved-memory` region).
- **Fixes are operational, not a code change:** arm the systemd hardware watchdog
  (auto-reboot in ~1 min instead of hours) and set up netconsole (remote kernel
  log) to capture the next event. Root cause is hardware/firmware → escalate to
  the JRC01 board vendor.

## Hard lockup vs. "JManager reconnect" — which one is it?

| Symptom | Hard lockup (this doc) | "JManager reconnect" ([other doc](jibot-jmanager-reconnect-troubleshooting.md)) |
| --- | --- | --- |
| SSH to the robot | **dead** (no route / timeout) | works fine |
| `ping` | **no reply** | replies |
| HMI | frozen / blank | shows "JManager reconnect …" |
| Robot motion | n/a (PC is off) | accepts commands, won't move |
| Recovery | **needs a power-cycle** | self-clears (settle) or release E-stop |
| Root cause | SoC/kernel hard freeze (hardware) | boot CPU saturation or E-stop (software/operational) |

If SSH still answers, you are in the *other* doc, not this one.

## Confirming a hard lockup after the fact

Once the box is back up (someone rebooted it), confirm what happened over SSH.
These confirmation commands are **read-only and need no sudo** (the *recovery*
steps further down do need sudo). Persistent journald is on (`/var/log/journal`),
so the **previous** boot is still inspectable.

```bash
# 1) Boot timeline. A hard lockup shows a GAP: boot -1 "ends" (last log) long
#    before boot 0 "begins" (the manual reboot). The end time of boot -1 is the
#    moment of the freeze.
journalctl --list-boots | tail -5
#   -1 ... 2026-06-21 22:09:07 — 2026-06-22 04:58:50   <- froze at 04:58:50
#    0 ... 2026-06-22 08:16:31 — ...                    <- manual reboot, ~3h18m later

# 2) Every boot shown as "still running" = no clean shutdown was ever recorded
#    = each reboot was a hard down, not an orderly restart.
last -x | grep -E 'reboot|shutdown' | head

# 3) The smoking gun is ABSENCE: scan the dying boot's kernel log for a cause.
#    A true software crash leaves a trace here; a hard SoC lockup leaves nothing.
journalctl -b -1 -k --no-pager | grep -iE \
  'out of memory|oom-killer|kernel panic|Oops|BUG:|hung_task|soft lockup|thermal.*trip|throttl|under.?voltage|brownout' | tail
journalctl -b -1 -k --no-pager -o short-iso | tail -40   # last kernel lines before freeze
journalctl -b -1     --no-pager -o short-iso | tail -30   # last lines of ALL units

# 4) Rule out a flat battery / power sag. The bz_robot status CSV survives reboots
#    and is fed by the MCU/BMS at ~1 Hz. Pick the file whose name (= its start time)
#    brackets the freeze. Usually that is the 2nd-newest (the newest belongs to the
#    current, post-reboot boot) — but after several reboots, list them and choose by
#    hand rather than trusting the position.
ls -t /usr/local/urobot/logs/bot_log/st_*.txt | head     # filenames encode start time
PREVCSV=$(ls -t /usr/local/urobot/logs/bot_log/st_*.txt | sed -n '2p')   # adjust if needed
#    Last columns are: …,flag,battery%,bms_voltage,current.
tail -3 "$PREVCSV" | awk -F',' '{print $2"  bat="$(NF-2)"  V="$(NF-1)}'
#   Jun-22-04:58:51  bat=90  V=53.11   <- healthy at the instant of the freeze
```

**Interpretation.** If (a) boot -1 ends mid-line with no panic/OOM/thermal trace,
(b) the CSV's last battery/voltage is healthy and flat, and (c) the journal and the
CSV stop within the same second — it is a **hard SoC/kernel lockup**, not power and
not a software crash you can pin from the logs.

**If step 3 *does* return a hit** — an `oom-killer`, `kernel panic`, `Oops`,
`hung_task`, or thermal-trip line — then you are **not** in this doc's hard-lockup
case: you have a log-traceable cause, so investigate that specific trace instead.
And if SSH was in fact still alive and only motion was lost, you are in the *soft*
case — see [`jibot-jmanager-reconnect-troubleshooting.md`](jibot-jmanager-reconnect-troubleshooting.md).

## What it is NOT (ruled out on 2026-06-22, with evidence)

| Hypothesis | Evidence against |
| --- | --- |
| Flat battery / brownout | CSV last row 04:58:51 = **90 %, 53.11 V**, flat all session (no sag). |
| Out of memory | 2.9 GiB available, swap 0 B used, no `oom-killer` in journal. |
| Thermal shutdown | Zones 44–47 °C; no thermal-trip events in boot -1. |
| Kernel panic / Oops | None logged — and `ramoops` can't capture one anyway (see below). |
| "JManager reconnect" (soft) | That mode keeps SSH alive; this had **SSH fully dead**. |
| OS/package corruption | `dpkg` clean, disk 57 %, no half-installed packages. |

## Why it stays down for hours (the recovery gap)

This is the actionable part. The board *has* a hardware watchdog but **nothing
arms it**, and there is **no crash capture**, so a freeze waits for a human.

```bash
systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec
#   RuntimeWatchdogUSec=0      <- systemd is NOT petting /dev/watchdog (disabled)
#   RebootWatchdogUSec=10min   <- only guards the reboot phase, not normal running
systemctl is-active watchdog.service        # inactive (no watchdog daemon either)
ls -l /dev/watchdog*                         # /dev/watchdog + watchdog0 (dw_wdt) DO exist
dmesg | grep -i ramoops
#   ramoops: failed to locate DT /reserved-memory resource
#   ramoops: probe of ramoops failed with error -22   <- no pstore crash dump possible
```

- `dw_wdt ff848000.watchdog: No valid TOPs array specified` — the DesignWare
  watchdog registers but its timeout table comes from the device tree; expect the
  effective max timeout to be **clamped** by the hardware (verify after enabling).
- `ramoops` fails because the device tree has no `reserved-memory` region for it,
  so `/sys/fs/pstore` is mounted but empty after a crash. Fixing it is a DT/firmware
  change → vendor.

## Recovery & mitigation

Priority order: **(A) watchdog** is the reliable win (auto-recovery, minimal
disruption); **(B) netconsole** is best-effort capture; **(C)** escalate.

### A. Arm the systemd hardware watchdog (recommended)

Converts "frozen for hours, needs a human" into "auto-reboot in ~1 min". Does **not**
fix the root cause. Run on the robot; `daemon-reexec` re-executes PID 1 and does
**not** restart services.

```bash
sudo cp /etc/systemd/system.conf /etc/systemd/system.conf.bak
# Set the key whether it is present (commented or not) OR missing entirely. A bare
# sed would silently do nothing if the line is absent — guard against that no-op:
if grep -qE '^#?RuntimeWatchdogSec=' /etc/systemd/system.conf; then
  sudo sed -i 's/^#\?RuntimeWatchdogSec=.*/RuntimeWatchdogSec=60s/' /etc/systemd/system.conf
else
  echo 'RuntimeWatchdogSec=60s' | sudo tee -a /etc/systemd/system.conf
fi
sudo systemctl daemon-reexec

# verify
systemctl show -p RuntimeWatchdogUSec        # success => RuntimeWatchdogUSec=1min
journalctl -b | grep -i watchdog | tail       # success => "Using hardware watchdog 'DesignWare…', /dev/watchdog0"
```

- **Reading the verify output.** systemd renames `RuntimeWatchdogSec`→`…USec` and
  reprints `60s` as `1min` — that reformat is **normal, not a failure**. *Success* =
  `RuntimeWatchdogUSec=1min` (or a smaller HW-clamped value) **and** a
  `Using hardware watchdog …/dev/watchdog0` line in the journal. *"Failed to
  register"* = `RuntimeWatchdogUSec` still shows `0`, **or** no `Using hardware
  watchdog` line appears.
- If `dw_wdt` clamps 60 s to a smaller max, that is fine for auto-recovery. If it
  failed to register, lower to `RuntimeWatchdogSec=30s` and retry; if it is *still*
  `0`, confirm nothing else holds the device (`sudo fuser /dev/watchdog`) and that
  `watchdog.service` is inactive.
- A genuine end-to-end test means deliberately hard-freezing the kernel, which is
  destructive — **not** advisable on a production robot. Treat "armed + the journal
  line" as the practical confirmation.
- Do **not** also start `watchdog.service` — it would fight systemd for
  `/dev/watchdog`. Leave it inactive.
- Rollback: `sudo cp /etc/systemd/system.conf.bak /etc/systemd/system.conf && sudo systemctl daemon-reexec`.

### B. netconsole — capture the next freeze remotely (best-effort)

Streams kernel `printk` over UDP to another PC. On this robot the only path to the
FMS LAN is **wlan0** (eth0 is the internal `10.8.8.x` camera LAN), so capture is
**best-effort**: if the Wi-Fi chip freezes with the SoC, the final messages may not
make it out — but the moments *before* the freeze (e.g. `dma-pl330` / USB errors)
often will.

Receiver (the workstation, e.g. `192.168.3.124`, iface `enp16s0`, MAC
`10:ff:e0:b9:73:de` — substitute your own: find the receiver's iface/IP with
`ip -br addr` and its MAC with `ip link show <iface>`; the robot's own wlan0 IP
comes from `ip -br addr show wlan0`):

```bash
mkdir -p ~/jibot-netconsole
socat -u UDP-RECV:6666 OPEN:$HOME/jibot-netconsole/kmsg-222.log,creat,append
# or: nc -u -l 6666 | tee -a ~/jibot-netconsole/kmsg-222.log
# (ensure inbound 6666/udp is not firewalled on the receiver)
```

Sender (robot, after wlan0 is up) — format is
`src-port@src-ip/src-dev,tgt-port@tgt-ip/tgt-mac`:

```bash
sudo modprobe netconsole \
  "netconsole=6666@192.168.3.222/wlan0,6666@192.168.3.124/10:ff:e0:b9:73:de"
echo "netconsole test from 222 $(date)" | sudo tee /dev/kmsg   # should appear on the receiver
sudo modprobe -r netconsole                                    # to stop
```

> **Boot persistence is finicky over Wi-Fi.** Loading `netconsole` at
> `modules-load` time fails because wlan0 has no IP yet. Survive reboots by adding
> the target via **configfs** from a systemd oneshot ordered after the network is
> online (unit below), not via `/etc/modules-load.d`. The most reliable capture of
> all is a **debug-UART serial console** to a second PC — needs the board pinout
> (vendor).

Persistent target — write `/etc/systemd/system/netconsole-fms.service` (edit the
`remote_ip`/`remote_mac` to your receiver):

```ini
[Unit]
Description=netconsole kernel log -> FMS workstation
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/sbin/modprobe configfs
ExecStartPre=/sbin/modprobe netconsole
# wlan0 may associate a few seconds after network-online; brief grace then enable
ExecStart=/bin/bash -c 'sleep 10; cd /sys/kernel/config/netconsole && mkdir -p fms && cd fms && \
  echo wlan0 > dev_name && echo 6666 > local_port && echo 6666 > remote_port && \
  echo 192.168.3.124 > remote_ip && echo 10:ff:e0:b9:73:de > remote_mac && echo 1 > enabled'
ExecStop=/bin/bash -c 'echo 0 > /sys/kernel/config/netconsole/fms/enabled; rmdir /sys/kernel/config/netconsole/fms'

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now netconsole-fms.service
systemctl status netconsole-fms.service     # active (exited); if it raced wlan0: sudo systemctl restart netconsole-fms
```

### C. Escalate to the board vendor

Recurring unexplained SoC lockups on the **JRC01 (Rockchip-class) carrier board**
are a hardware/firmware matter. Provide the vendor: the boot-gap timeline, the
"clean" (cause-free) kernel log, the `dma-pl330 … Bad Desc` recurrence, and ask
them to wire up `ramoops`/`reserved-memory` so a panic leaves a dump.

## Incident: 2026-06-22

**Symptom.** Operator reported "the remote PC is down again." bot A
(`192.168.3.222`) was unreachable by SSH; the operator power-cycled it, after which
SSH/ping recovered.

**Timeline (reconstructed from the persistent journal + CSV):**

- 2026-06-21 22:09:07 — boot -1 starts.
- 2026-06-22 **04:58:50–51** — **hard freeze.** The kernel journal (last line
  04:58:50) and the `bz_robot` CSV (`st_2026-06-22__04-09-41.txt`, last row
  04:58:51) — two independent streams — stop within the same second. Battery 90 %,
  53.11 V.
- ~3 h 18 m with the host completely down (SSH-dead).
- 2026-06-22 ~08:16 — operator's manual power-cycle (boot 0); stack came back,
  load settled from ~8 to ~6 (normal for this 6-core board), motors enabled,
  serial ports `ttyUSB0–4` re-enumerated, `urobot.service` + VDA5050 adapter up.

**Root cause.** A hardware-level SoC/kernel hard lockup. The logs contain no
recoverable software cause (no panic/OOM/thermal), power was healthy, and the two
independent log streams died together — the signature of an instantaneous wedge.
Per systematic debugging, this is the "environmental/hardware, not log-traceable"
case: document, add auto-recovery + capture, escalate.

**Leads (not proven cause):**

- `dma-pl330 ff6d0000.dma-controller: fill_queue: Bad Desc(N)` recurs across boots,
  including the dying boot (Bad Desc 10/11/12 at 22:15) and again post-reboot — a
  low-level DMA-controller anomaly worth flagging to the vendor.
- Repeated `urobot-start … serialOpen(): cannot open serial port. No such file` in
  the minutes before the freeze (one `/dev/ttyUSB*` had dropped off the bus). All
  five ports re-enumerate fine on the current boot.

**Pre-existing failed units seen (mostly unrelated to the lockup):**

- Worth a look: `dmesg.service` (saves boot kernel log — ironic given the goal),
  `isc-dhcp-server` (likely the internal camera LAN DHCP), `wpa_supplicant@wlan0`
  (a stale template instance — wlan0 is in fact connected, `TP_Link_AMR_5G`, ~−51 dBm).
- Known-cosmetic (see the JManager doc): `jfservice.service` (firmware-flash bridge),
  `systemd-modules-load.service` (`lp`/`ppdev` aliases).

## Related docs

- SSH access & network, hardware/platform facts: [`jibot-onboard-access.md`](jibot-onboard-access.md).
- The *soft* "won't move / JManager reconnect" failure: [`jibot-jmanager-reconnect-troubleshooting.md`](jibot-jmanager-reconnect-troubleshooting.md).
- Charging / BMS telemetry (where battery & voltage come from): [`jibot-charging-dock-bms.md`](jibot-charging-dock-bms.md).
