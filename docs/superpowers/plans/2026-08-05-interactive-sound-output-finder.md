# Interactive Sound Output Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a non-mutating interactive scan that identifies operator-confirmed audible PulseAudio sinks and direct ALSA devices.

**Architecture:** Extend the existing Bash probe with small discovery, playback, prompt, and summary functions while preserving its default behavior. Exercise the real script through Python subprocess tests with fake audio commands and a pseudo-terminal, then copy only the finished script to the robot's `/tmp` for an operator-assisted hardware scan.

**Tech Stack:** Bash 4+, PulseAudio `pactl`, ALSA `aplay`/`speaker-test`, Python 3 `pytest`, `pty` and `subprocess` from the standard library.

## Global Constraints

- `--interactive` is opt-in; every existing invocation retains its current behavior.
- The scan must not edit configuration, bind drivers, or change persistent mixer state.
- Prompts read from `/dev/tty`; player stdin remains `/dev/null`.
- Answers are `y` (audible), `n`/empty (inaudible), `r` (replay), and `q` (partial quit).
- Exit codes are 0 for at least one audible output, 1 for a completed scan with none, 2 for usage/preflight failure, and 130 for operator quit.
- `--prepare-rt5651` remains the only opt-in path that changes mixer routes.

---

### Task 1: Interactive PulseAudio scan and prompt protocol

**Files:**
- Create: `tests/test_sound_device_probe_interactive.py`
- Modify: `scripts/test-sound-devices.sh`

**Interfaces:**
- Consumes: existing `--track`, `--seconds`, `PLAYER`, and `pactl list short sinks` behavior.
- Produces: `--interactive`; Bash functions `ask_audible`, `play_pulse_sink`, and `print_interactive_summary`; arrays `AUDIBLE_PULSE_SINKS` and `AUDIBLE_ALSA_DEVICES`; flags `INTERACTIVE` and `SCAN_QUIT`.

- [ ] **Step 1: Add the pseudo-terminal test harness and failing PulseAudio test**

Create a test helper that builds fake commands in `tmp_path / "bin"`, launches
the real script with `pty.openpty()`, writes answers to the PTY master, and
returns decoded output and status. The first test must provide two fake sinks,
answer `n` for the first, `r` then `y` for the second, and assert:

```python
assert result.returncode == 0
assert playback_log.read_text().splitlines() == [
    "pulse:sink-one",
    "pulse:sink-two",
    "pulse:sink-two",
]
assert 'sink = "sink-two"' in result.output
assert "Confirmed audible PulseAudio sinks" in result.output
```

The fake `pactl` prints tab-separated `sink-one` and `sink-two`; fake `mplayer`
appends `pulse:${PULSE_SINK}` to `$PLAYBACK_LOG`; fake `aplay` reports no devices
so Task 1 isolates PulseAudio behavior.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
pytest -q tests/test_sound_device_probe_interactive.py::test_interactive_replays_and_records_audible_pulse_sink
```

Expected: FAIL because `--interactive` is an unknown argument and no prompt or
summary exists.

- [ ] **Step 3: Implement the minimum PulseAudio interactive flow**

Add `INTERACTIVE=0`, `SCAN_QUIT=0`, result arrays, usage text, and argument
parsing for `--interactive`. Before playback, verify `/dev/tty` can be opened
for reading and writing; otherwise print `ERROR: --interactive requires a controlling terminal`
and exit 2.

Implement `ask_audible` as a loop that reads a single answer from `/dev/tty`,
normalizes it to lowercase, returns 0 for `y`, 1 for `n`/empty, 2 for `r`, and
sets `SCAN_QUIT=1` for `q`. Implement `play_pulse_sink` so `r` repeats the exact
playback, `y` appends the sink once, and `q` stops enumeration. Avoid piping the
enumeration loop so array changes remain in the parent shell.

Implement `print_interactive_summary` to list audible sinks and print the exact
candidate TOML lines. Return 0, 1, or 130 according to the global constraints.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
pytest -q tests/test_sound_device_probe_interactive.py::test_interactive_replays_and_records_audible_pulse_sink
```

Expected: PASS with three playback log entries in the asserted order.

- [ ] **Step 5: Add and pass quit/no-TTY/no-success tests**

Add tests asserting:

```python
assert quit_result.returncode == 130
assert "Partial scan" in quit_result.output
assert no_tty_result.returncode == 2
assert "requires a controlling terminal" in no_tty_result.output
assert none_result.returncode == 1
assert "No audible output was confirmed" in none_result.output
```

For the quit case, expose two sinks, answer `q` at the first prompt, and verify
the playback log contains only the first sink. Run:

```bash
pytest -q tests/test_sound_device_probe_interactive.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add tests/test_sound_device_probe_interactive.py scripts/test-sound-devices.sh
git commit -m "feat(scripts): add interactive PulseAudio sound scan"
```

---

### Task 2: Direct ALSA discovery and result classification

**Files:**
- Modify: `tests/test_sound_device_probe_interactive.py`
- Modify: `scripts/test-sound-devices.sh`

**Interfaces:**
- Consumes: Task 1 prompt protocol, `AUDIBLE_ALSA_DEVICES`, and summary function.
- Produces: Bash functions `discover_alsa_devices` and `play_alsa_device`; ALSA identifiers in `plughw:<card>,<device>` form.

- [ ] **Step 1: Write the failing ALSA-only success test**

Configure fake `pactl` to expose one sink and fake `aplay -l` to emit:

```text
card 0: hdmisound [hdmi-sound], device 0: i2s-hifi [i2s-hifi]
card 2: rt5651 [realtek,rt5651-codec], device 0: fe410000.i2s [rt5651-aif1]
```

Answer `n`, `n`, `y`. Assert:

```python
assert result.returncode == 0
assert "alsa:plughw:0,0" in playback_log.read_text()
assert "alsa:plughw:2,0" in playback_log.read_text()
assert "Hardware playback works, but its PulseAudio sink must be restored" in result.output
assert "plughw:2,0" in result.output
```

- [ ] **Step 2: Run the ALSA test and verify RED**

Run:

```bash
pytest -q tests/test_sound_device_probe_interactive.py::test_interactive_classifies_alsa_only_success
```

Expected: FAIL because the script does not enumerate `aplay -l` or invoke
`speaker-test` for every card/device pair.

- [ ] **Step 3: Implement ALSA discovery and playback**

Parse only lines beginning with `card <number>:` and containing
`, device <number>:`. Deduplicate the generated `plughw:<card>,<device>` values.
For each device run a bounded 1 kHz stereo tone:

```bash
timeout "${SECONDS_PER_SINK}s" speaker-test \
  -D "$device" -t sine -f 1000 -c 2 -l 1
```

Wrap it in `pasuspender --` when available. Redirect stdin from `/dev/null`,
report non-zero playback without aborting, and apply the Task 1 `y/n/r/q`
protocol. Missing `aplay` or `speaker-test` prints a skip diagnostic rather than
blocking the PulseAudio phase.

Extend the summary so ALSA-only success prints the required PulseAudio recovery
message. If neither phase succeeds, mention codec enumeration, amplifier power,
mixer routes, and wiring.

- [ ] **Step 4: Run interactive tests and verify GREEN**

Run:

```bash
pytest -q tests/test_sound_device_probe_interactive.py
```

Expected: all interactive tests PASS.

- [ ] **Step 5: Verify legacy behavior remains intact**

Run:

```bash
pytest -q \
  tests/test_adaptor_service_scripts.py::test_sound_device_probe_script_exercises_each_pulseaudio_sink \
  tests/test_adaptor_service_scripts.py::test_sound_device_probe_script_can_prepare_rt5651_routes
bash -n scripts/test-sound-devices.sh
```

Expected: both pytest cases PASS and `bash -n` exits 0.

- [ ] **Step 6: Commit Task 2**

```bash
git add tests/test_sound_device_probe_interactive.py scripts/test-sound-devices.sh
git commit -m "feat(scripts): probe direct ALSA outputs interactively"
```

---

### Task 3: Operator documentation and robot-assisted scan

**Files:**
- Modify: `docs/guide/jibot-onboard-quick-guide.md`

**Interfaces:**
- Consumes: completed `scripts/test-sound-devices.sh --interactive` interface.
- Produces: operator command and interpretation guide; no production-code interface.

- [ ] **Step 1: Add the interactive scan guide**

Document:

```bash
cd ~/adapter
scripts/test-sound-devices.sh --interactive --seconds 3
```

Explain `y/n/r/q`, the PulseAudio sink candidate output, ALSA-only recovery
classification, and that `--prepare-rt5651` changes mixer state and is not
enabled automatically.

- [ ] **Step 2: Run all focused local verification**

Run:

```bash
bash -n scripts/test-sound-devices.sh
pytest -q tests/test_sound_device_probe_interactive.py tests/test_adaptor_service_scripts.py
git diff --check
```

Expected: syntax check exits 0, all selected tests PASS, and diff check is clean.

- [ ] **Step 3: Copy the script to a temporary path on 192.168.101.62**

Do not overwrite the deployed script. Run:

```bash
scp scripts/test-sound-devices.sh ucore@192.168.101.62:/tmp/test-sound-devices-interactive.sh
ssh -t ucore@192.168.101.62 \
  'chmod u+x /tmp/test-sound-devices-interactive.sh && cd /home/ucore/adaptor && /tmp/test-sound-devices-interactive.sh --interactive --track sounds/driving.mp3 --seconds 3'
```

Expected: the operator can answer each prompt and the script prints either an
audible sink candidate, an ALSA-only classification, or a completed no-output
diagnosis. No deployed file or configuration is changed.

- [ ] **Step 4: Record the observed working path**

Update the current `WORKING/2026-08-05/*.md` recovery log with the exact audible
sink/device, exit classification, and whether rt5651 appeared in `aplay -l`.
Do not write the candidate into remote `config/config.toml`.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/guide/jibot-onboard-quick-guide.md
git commit -m "docs: explain interactive sound output scan"
```
