# Interactive Sound Output Finder Design

## Goal

Extend `scripts/test-sound-devices.sh` with an opt-in interactive mode that
plays a test signal through each discovered output and records the operator's
audibility answer. The script must not change `config/config.toml`, PulseAudio
configuration, kernel driver binding, or persistent mixer state.

## Interface

Add `--interactive` to the existing command. Existing invocations retain their
current behavior. Interactive mode uses the existing `--track`, `--seconds`,
`PLAYER`, and `ALSA_CARD` inputs.

After each playback attempt, accept one command from the controlling terminal:

- `y`: record the output as audible and continue scanning.
- `n` or an empty response: record it as inaudible and continue.
- `r`: replay the same output and ask again.
- `q`: stop scanning and print results collected so far.

The prompt must read from `/dev/tty`, because player stdin remains redirected
from `/dev/null`. If no controlling terminal is available, interactive mode
must exit with status 2 and a clear error before attempting playback.

## Discovery and Playback Flow

The script prints the configured sink from `config/config.toml` when that file
and key are readable. This value is context only and is never written.

It then enumerates `pactl list short sinks`. Each PulseAudio sink is played with
the existing command form:

```bash
PULSE_SINK="$sink_name" timeout "${SECONDS_PER_SINK}s" \
  "$PLAYER" -really-quiet -noconsolecontrols -ao "pulse::${sink_name}" "$TRACK"
```

After the PulseAudio scan, it enumerates ALSA playback hardware from
`aplay -l`. Each unique `card,device` pair is tested with `speaker-test` through
`plughw:<card>,<device>`. ALSA tests use a generated 1 kHz tone rather than the
MP3 file so they do not depend on an additional decoder or conversion step.
`pasuspender` is used when available to release devices held by PulseAudio.

Missing `pactl` does not prevent ALSA discovery in interactive mode. Missing
`aplay` or `speaker-test` produces a diagnostic and skips the ALSA phase. A
failed playback command is reported, but the operator is still asked whether
anything was heard because some players return non-zero after partial output.

`--prepare-rt5651` remains explicitly opt-in. Interactive mode does not enable
mixer routes automatically because that changes live hardware state.

## Result Classification

The final summary lists every audible PulseAudio sink and ALSA device.

- When a PulseAudio sink is audible, print the exact candidate line
  `sink = "<sink-name>"` without editing configuration.
- When only ALSA is audible, report that hardware playback works but the
  corresponding PulseAudio card/sink must be restored or created.
- When nothing is audible, report that no working output was confirmed and
  recommend checking codec enumeration, amplifier power, mixer routes, and
  wiring.
- Quitting early is labeled as a partial scan, not a definitive failure.

The interactive scan returns 0 when at least one audible output was recorded,
1 after a completed scan with no audible output, and 130 when the operator
quits. Preflight/usage errors return 2.

## Testing

Add subprocess-based tests around the real shell script with fake executables
placed first in `PATH`. Tests provide a pseudo-terminal so `/dev/tty` behavior
is exercised rather than replaced by an implementation-specific mock.

Cover these behaviors:

1. Existing non-interactive behavior and options remain available.
2. PulseAudio answers support `n`, `r`, and `y`; an audible sink is summarized
   with the exact TOML candidate.
3. ALSA devices are scanned after PulseAudio and an ALSA-only success is
   classified correctly.
4. `q` stops further playback, prints a partial-scan summary, and returns 130.
5. Interactive mode without a controlling terminal fails before playback.
6. A full scan with no affirmative answer returns 1 and prints the hardware
   troubleshooting guidance.

## Scope

This change does not repair or rebind rt5651, change mixer controls, select a
sink automatically, modify service configuration, or attempt to infer whether
sound was audible without operator confirmation.
