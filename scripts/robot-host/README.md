# JIBOT robot-host reliability scripts

systemd-level fixes installed **on the JIBOT onboard PC** (e.g. `192.168.3.222` /
`.223`). They sit in `/etc/systemd/system` drop-ins and a local watchdog — the
vendor urobot stack (`/usr/local/urobot`, `urobot.launch`, `configs.json`) is
**never modified**, so each script fully reverts.

## `install-urobot-watchdog.sh`

### The problem it fixes

On a cold boot the whole stack (`urobot.service` → ~31 ROS nodes + `mg_main`)
spins up at once on a 6-core SoC and the OS boot burst overlaps it. CPU hits
~6.0 load, and the bringup-critical path (`bz_robot` / MCU "robot ready"
handshake) gets starved by non-essential vision nodes (`depth_detector`,
apriltag/qr cameras). `mg_main` waits ~51 s for "robot ready", times out, logs

```
JSignal: exit[-1] without report: reason:"Bot start failed!", run time 51s
```

and **exits**. It does not retry. Crucially, `urobot.service` stays `active`
(roslaunch keeps running), so **systemd never sees a failure** — the robot sits
dead for hours with:

- **TCP 7273 (JSrvTcp / "JManager") not listening** → the VDA5050 adapter can't
  connect (`[JIBOT CONNECT FAILED] ...`).
- **no `map` TF frame** → no localization (`tf2odomNode: ... "map" ... does not exist`).

Both symptoms share this one root cause.

### What it installs

| | Measure | Effect |
| --- | --- | --- |
| **A-2** | `TimeoutStartSec=180` + `ExecStartPre=/bin/sleep 30` drop-in on `urobot.service` | starts urobot after the OS boot burst settles, without tripping short vendor start timeouts |
| **B-2** | `urobot-watchdog` script + oneshot service + 60 s timer | restarts urobot if 7273 never comes up after the bringup window, with a per-boot cap so a real hardware/E-stop fault isn't masked by an endless restart loop |

A-2 reduces the chance of the failure; B-2 auto-recovers when it still happens.

### Install (on the robot)

```bash
# from a machine that can reach the onboard PC
scp scripts/robot-host/install-urobot-watchdog.sh ucore@192.168.3.222:~/

# on the robot
sudo ~/install-urobot-watchdog.sh
```

Idempotent — safe to re-run. Tunables via env:

```bash
BOOT_DELAY=45 START_TIMEOUT=240 MIN_UPTIME=180 MAX_RESTARTS=2 sudo -E ~/install-urobot-watchdog.sh
```

- `BOOT_DELAY` (30) — A-2 delay, seconds.
- `START_TIMEOUT` (180) — systemd start timeout, seconds; must be longer than
  `BOOT_DELAY` because `ExecStartPre` counts against `TimeoutStartSec`.
- `MIN_UPTIME` (150) — seconds urobot must be `active` before the watchdog judges
  it (the bringup window; don't go below ~120, bringup needs ~90 s after active).
- `MAX_RESTARTS` (3) — auto-restarts per boot before it stops and logs an alert.

> A-2 applies on the **next** urobot (re)start or reboot. B-2 is live immediately.

### Verify

```bash
systemctl list-timers urobot-watchdog* --no-pager
sudo /usr/local/sbin/urobot-watchdog; echo exit=$?   # 7273 up -> exit=0
journalctl -t urobot-watchdog --no-pager -n 20
```

A healthy boot leaves the watchdog log empty; a recovered boot shows
`restart #1/3 of urobot`. After 3 failed restarts it logs
`... suspect E-stop/MCU/hardware, manual check needed` and stops.

### Uninstall (full revert)

```bash
sudo ~/install-urobot-watchdog.sh --uninstall
# drop the boot delay now instead of next reboot:
sudo systemctl restart urobot
```

### How the watchdog decides

1. **7273 listening?** → healthy, reset counters, exit.
2. urobot `activating`? → a legit start is in progress, don't touch.
3. urobot `active` but 7273 down → only act once it's been active ≥ `MIN_UPTIME`
   (gives bringup time, and after a restart the young service is skipped — this
   is what prevents a restart storm).
4. urobot `failed`/`inactive` → down, recover immediately.
5. Restart, up to `MAX_RESTARTS` per boot (counter in `/run`, cleared on reboot
   and on the next healthy check).
