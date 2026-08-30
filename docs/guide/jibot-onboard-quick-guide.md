# JIBOT Onboard Quick Guide

This guide captures the field fixes needed to deploy the adapter on a JIBOT
onboard computer without rediscovering the same failure modes.

## Expected Runtime Shape

Default JIBOT installs use one adapter service:

```bash
amr-adaptor.service
amr-webui.service
```

Do not use `amr-adaptor@<robot>.service` unless `config/config.toml` explicitly
defines `[[adapter.instances]]`. `config/robots.hcl` is inventory data; it must
not create systemd instances by itself.

## First Checks

```bash
cd ~/adapter
date
systemctl list-unit-files "amr-adaptor*.service" --no-pager
./scripts/adaptor-services.sh status
```

If HTTPS reports `SSL certificate problem: certificate is not yet valid`, fix the
board clock before installing anything:

```bash
sudo date -s '2026-06-29 15:00:00'
date
```

Use the current real time for the timestamp. Some boards do not support
`timedatectl set-ntp true`.

## SSH Deploy

The deploy script enables the legacy SSH algorithms required by older JIBOT
OpenSSH servers, so this should work directly:

```bash
./scripts/update-jibot-adapter-over-ssh.sh --restart ucore@192.168.3.221
```

If the service has never been installed:

```bash
./scripts/update-jibot-adapter-over-ssh.sh \
  --restart-cmd 'cd ~/adapter && scripts/setup-adaptor-service.sh --jibot --no-venv --no-camera --no-start' \
  ucore@192.168.3.221
```

That command needs the remote `ucore` sudo password because it writes
`/etc/systemd/system/*.service`.

## Python Runtime

Do not replace the system Python on a commercial board. Install Python 3.11 under
the deploy user and run the adapter through `~/adapter/.venv`.

Online setup, after the board clock is correct:

```bash
cd ~/adapter
curl https://mise.run/bash | sh
~/.local/bin/mise install python@3.11
PY="$(~/.local/bin/mise where python@3.11)/bin/python"
"$PY" -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install --no-index --find-links=offline_packages -r requirements.txt
```

Verify:

```bash
cd ~/adapter
.venv/bin/python --version
.venv/bin/python -c 'import paho.mqtt.client, tomli'
```

The adapter requires Python `>=3.11`. If `.venv` is missing, the launchers fall
back to system `python3`; on many boards that is Python 3.8 and the services
exit with status `1/FAILURE`.

## Service Repair

If the service was installed while a stale `~/adapter/adaptor/` directory existed,
it may point at the wrong working directory. Check:

```bash
systemctl cat amr-adaptor.service --no-pager | sed -n '/WorkingDirectory=/p;/ExecStart=/p'
```

Correct default values are:

```text
WorkingDirectory=/home/ucore/adapter
ExecStart=/home/ucore/adapter/run-adapter.sh
```

Reinstall the unit if needed:

```bash
cd ~/adapter
scripts/setup-adaptor-service.sh --jibot --no-venv --no-camera --no-start
sudo systemctl daemon-reload
sudo systemctl restart amr-adaptor.service amr-webui.service
```

## WebUI "adapter offline — not delivered"

This message means WebUI could not connect to:

```text
/run/amr-adaptor/<serial>/control.sock
```

Check the serials:

```bash
find /run/amr-adaptor -maxdepth 3 -printf '%y %p\n'
grep -n '^robot ' ~/adapter/config/robots.hcl
cd ~/adapter
.venv/bin/python - <<'PY'
from config.config import get_config
from core.registry import build_registry
for spec in build_registry(get_config()):
    print(spec.key, spec.unit, spec.serial)
PY
```

For the default single service, the socket serial should match
`config/robots.hcl` `robot` block label. Refresh the browser after
changing service layout or serials; stale pages can still submit to an old robot
key.

Direct delivery smoke test:

```bash
cd ~/adapter
.venv/bin/python - <<'PY'
from core import ipc_paths
from web.senders import UdsSender

serial = "HN-SH6-TR-001"  # replace with config/robots.hcl robot block label
payload = {
    "headerId": 1,
    "timestamp": "2026-06-29T00:00:00.000Z",
    "version": "3.0.0",
    "manufacturer": "jibot",
    "serialNumber": serial,
    "actions": [],
}
print(ipc_paths.control_sock_path(serial))
print(UdsSender(ipc_paths.control_sock_path(serial)).send(payload, {"source": "smoke"}))
PY
```

Expected result:

```text
(True, 'delivered')
```

## Sound Output

The adapter plays sound through the board's PulseAudio sinks. On some boards the
default sink is HDMI or SPDIF, which can make WebUI sound actions look successful
while no speaker output is audible.

List and probe every sink:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --seconds 3
```

To identify the working output with operator confirmation, use the interactive
scan. It tests every PulseAudio sink first and then every direct ALSA playback
device:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --interactive --seconds 3
```

Answer `y` when the test sound is audible, `n` (or Enter) when it is not, `r`
to replay the same output, or `q` to stop with a partial result. An audible
PulseAudio result prints the exact candidate `sink = "..."` line without
changing the configuration. If only an ALSA device is audible, the hardware
path works but its PulseAudio card/sink must be restored or created. The scan
does not change mixer routes; `--prepare-rt5651` remains a separate, explicitly
state-changing option.

When a sink is audible, copy its name into `config/config.toml`:

```toml
[sound_settings]
sink = "alsa_output.platform-rt5651-sound.stereo-fallback"
```

The same value is editable from WebUI `/config`: open the `[sound_settings]`
section, update the `sink` row, save, then restart the adapter service. New
scalar config keys should follow this pattern so operators can manage them from
WebUI by default.

Then restart the adapter:

```bash
sudo systemctl restart amr-adaptor.service
```

The WebUI `setSoundVolume` action and startup `startup_volume` use the same
configured sink. `scripts/test-sound-devices.sh --list-only` is useful when you
only need to see sink names without playing audio.

If the action is delivered and PulseAudio shows a sink-input on the expected
sink but there is still no audible output, check the rt5651 mixer/route. On the
JIBOT board we saw healthy `mplayer` decoding and a PulseAudio sink-input on
`alsa_output.platform-rt5651-sound.stereo-fallback`, but no sound until the
rt5651 output route switches were enabled.

Show the focused mixer controls:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --show-mixer --list-only
```

Enable the known rt5651 route and re-run the PulseAudio sink probe:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --prepare-rt5651 --seconds 3
```

Bypass PulseAudio and test ALSA card 0 directly:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --prepare-rt5651 --direct-alsa --seconds 5
```

If `--direct-alsa` is also silent, the fault is below the adapter and
PulseAudio: check board-specific rt5651 routing, speaker/amp power, and wiring.
If a board uses a different ALSA card number for rt5651, run with
`ALSA_CARD=<n>`.

## Known Non-Fatal Warnings

Locale warnings such as this are noisy but not the adapter failure:

```text
bash: warning: setlocale: LC_ALL: cannot change locale (en_US.UTF-8)
```

Fix later by generating the locale on the board, or avoid exporting unavailable
locales from the SSH client environment.
