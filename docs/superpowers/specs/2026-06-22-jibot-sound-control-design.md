# JIBOT Sound Control — Design

## Problem / Goal

Add OS-level sound control to the JIBOT adapter:

1. **Volume control function** — set/mute the adapter host's audio output volume.
2. **MP3 playback function** — play an mp3 on the adapter host.
3. **`workingState`-driven playback** — automatically play music that matches the
   robot's current activity (travel music while driving, work music while
   loading/unloading/docking).
4. **Factsheet exposure** — advertise the sound actions so the eq/ACS can discover them.
5. **MP3 upload over MQTT** — let the eq push new mp3 files to the adapter via a
   VDA5050 instant action (same shape as `setMap`), not a separate web server.
6. **Human sound test** — a diagnostic so a person can confirm audio output,
   triggerable from both a Web UI button and an MQTT instant action.

Playback happens on the **adapter host OS**, not the robot. The robot-side
`UmSetVolume` jibot command already exists and is out of scope here.

## Background — current state of the code

- **OS audio is validated.** On the adapter host (`ucore@ubuntu`,
  `ucore@192.168.3.10`) playback must target the onboard analog sink
  `alsa_output.platform-rt5651-sound.stereo-fallback`. PulseAudio defaults to a
  dead HDMI sink, which makes `mplayer` print `Audio device got stuck!`. Pin the
  sink with `PULSE_SINK`. `mplayer` is installed and plays mp3 + loop + pulse;
  `mpg123`/`ffplay` are not guaranteed present. No new offline deb is needed if
  we use `mplayer`.
- **`[sound_settings]` exists but is unused.** `config.toml` has
  `travel_music`/`work_music`/`route_prefiX`; `SoundSettings`
  (`config/config.py:74`) parses them. No code plays them. `route_prefiX`
  (`HANA_TRAVEL_MUSIC_`/`HANA_WORK_MUSIC_`) was a robot-route idea and stays
  unused (kept for config compatibility).
- **`workingState` is already implemented (no new work).**
  `_derive_amr_working_state()` (`adapter_jibot.py:696`) returns
  `(workingState, workingStateDetail)` and `_refresh_amr_state_information()`
  (`:759`) publishes them as an `AMR_STATE` info block every publish cycle
  (called from the state loop at `:510`). This is the trigger source for sound.
  - `workingState`: `ERROR` > `BLOCKED` > `PAUSED` > `CHARGING` > `DRIVING` > `ACTING` > `IDLE`
  - `workingStateDetail`: `EMERGENCY` > `LOST` > `FAULT` > `LOADING` > `UNLOADING` > `DOCKING` > `NONE`
- **Instant action infrastructure exists.** `SUPPORTED_INSTANT_ACTIONS`
  (`adapter_jibot.py:1619`) feeds the factsheet `agvActions`
  (`_build_factsheet`, `:1669`); incoming instant actions are dispatched by an
  `action.action_type ==` chain (`:2969`–`:3009`). `setMapSnapshot`
  (`_handle_set_map_snapshot_instant_action`, `:887`) is the precedent for
  receiving structured payloads through `action.action_parameters` over MQTT.

## Components

`workingState` (component B in earlier discussion) is **done**; this design
consumes it. Remaining: A (engine), C (auto-play), D (instant actions +
factsheet), E (MQTT upload), plus config.

### A. `SoundPlayer` engine — `adaptor/utils/sound.py` (new)

A small, single-purpose class managing one playback subprocess. No adapter or
VDA5050 knowledge; constructed with plain config values so it is unit-testable.

Constructor inputs: `sink`, `player` (binary, default `mplayer`), `sound_dir`,
`enabled`.

Methods:
- `play(track: str, loop: bool = True) -> None`
  - Resolve `track` to a path: if not absolute, join with `sound_dir`
    (`sound_dir` itself resolved against the adapter root when relative).
  - **Dedup:** if the same track is already playing, return without restarting
    (so calling every publish cycle causes no audio gap).
  - Otherwise stop the current process and spawn:
    `mplayer -really-quiet -ao pulse -loop 0 <path>` with env
    `PULSE_SINK=<sink>` (omit `PULSE_SINK` when `sink` is empty → default sink).
    `-loop 0` = play forever; `loop=False` omits `-loop` (play once).
  - Track the current track name + `Popen` handle.
- `stop() -> None` — terminate the process (terminate, then kill after a short
  grace), clear current track.
- `set_volume(percent: int) -> None` — `pactl set-sink-volume <sink|@DEFAULT_SINK@> <clamped 0–100>%`.
- `set_mute(muted: bool) -> None` — `pactl set-sink-mute <sink|@DEFAULT_SINK@> 1|0`.
- `is_playing() -> bool`, `current_track -> Optional[str]`.
- `shutdown() -> None` — stop and release. `SoundPlayer` also registers
  `atexit.register(self.stop)` so process-exit is covered automatically; `shutdown()`
  exists for explicit teardown (the adapter has no dedicated teardown hook today,
  so atexit is the primary cleanup mechanism in practice).

Subprocess policy: player via `subprocess.Popen` (non-blocking spawn). `pactl`
via `subprocess.run(..., timeout=...)` — short and only on transitions, so brief
blocking in the async loop is acceptable. All calls wrapped: a missing player
binary (`FileNotFoundError`) logs once and sets an internal `_disabled` flag so
later calls are no-ops (no log spam); a missing mp3 file logs a warning and
skips; `pactl` failures are logged and ignored. **No exception ever propagates
into the state loop.**

### B. `workingState` (already implemented — consumed, not built)

See Background. Sound reads `_derive_amr_working_state()`.

### C. Auto-play driver — `adapter_jibot.py`

Sound-track resolution from the computed working state:

```
def _resolve_sound_track() -> Optional[str]:
    if not config.sound_settings.enabled:
        return None
    working_state, detail = self._derive_amr_working_state()
    if detail in ("LOADING", "UNLOADING", "DOCKING"):
        return config.sound_settings.work_music
    if working_state == "DRIVING":
        return config.sound_settings.travel_music
    return None
```

Detail is checked before `DRIVING` so dock-approach motion
(`detail=DOCKING`, `workingState=DRIVING`) plays **work** music (per decision),
while a plain drive (`detail=NONE`) plays **travel** music. `LOADING`/
`UNLOADING` and `DRIVING` are mutually exclusive in practice (motion is blocked
during load/unload). All other states (`IDLE`/`CHARGING`/`PAUSED`/`BLOCKED`/
`ERROR`) → silence.

`_update_sound_for_working_state()`: if `_resolve_sound_track()` is `None` →
`self._sound.stop()`; else `self._sound.play(track)`. `SoundPlayer.play` dedups,
so this is called **every publish cycle** right after
`_refresh_amr_state_information()` (`:510`); no explicit edge detection needed.

**Auto is authoritative** (per decision): the workingState driver is the single
operational source of sound and runs every cycle. The only thing that suspends
it is a **bounded test override** (component F) for human verification, which is
self-clearing — when the test ends (one-shot finishes or its timeout expires)
the auto-driver resumes on the next cycle. `_update_sound_for_working_state()`
returns early only while `self._sound_test_until` is in the future / a one-shot
test is still playing. There is no persistent manual play mode.

Init: `SoundPlayer` is created in `__init__` from `config.sound_settings`; if
`startup_volume` is set it is applied once at startup. `SoundPlayer` registers
`atexit` for process-exit cleanup; `shutdown()` can be called explicitly if a
teardown hook exists (the adapter currently has none, so atexit is primary).

### D. Sound instant actions + factsheet — `adapter_jibot.py`

New instant actions, added to `SUPPORTED_INSTANT_ACTIONS` (`:1619`) so they
appear in factsheet `agvActions` automatically, and dispatched in the
`action.action_type` chain (`:2969`):

| actionType | parameters | effect |
|---|---|---|
| `testSound` | optional `sound` (`"travel"`/`"work"`/`fileName`, default `travel`); optional `seconds` (default = play once) | **bounded test override** for human verification (component F) |
| `stopSound` | — | stop any test playback now, clear the test override (auto resumes) |
| `setSoundVolume` | `volume` (0–100); optional `mute` (bool) | OS sink volume/mute via pactl |
| `uploadSound` | see component E | store an mp3 |

There is no operational `playSound` (auto is authoritative); manual playback is
only the bounded `testSound`. `setSoundVolume` is named distinctly from the
robot-side `UmSetVolume` (jibotCommand) to avoid confusion: this one controls the
**adapter host OS sink**. Each handler sets the instant-action status to
`FINISHED`/`FAILED` via `_update_instant_action_status`, consistent with existing
handlers. The MP3 *play function* itself is `SoundPlayer.play` (component A).

### E. MQTT mp3 upload — `uploadSound` instant action

Modeled on `setMapSnapshot`: parameters arrive as `action.action_parameters`
key/value pairs over MQTT (no separate web server).

Parameters:
- `fileName` — e.g. `"travel.mp3"`.
- `data` — base64-encoded mp3 bytes.
- `encoding` — optional, default `"base64"`.

`_handle_upload_sound_instant_action(action)`:
1. Validate `fileName`: take `os.path.basename` only (reject path traversal),
   require an allowed extension (`.mp3`; allow-list configurable).
2. Base64-decode `data`; `FAILED` on decode error.
3. Write atomically to `sound_dir/<fileName>` (temp file + `os.replace`),
   creating `sound_dir` if missing.
4. `FINISHED` with byte count; `FAILED` with reason otherwise.

**Size constraint / risk:** the whole mp3 travels in one MQTT message,
base64-inflated ~1.33×. Keep files small and confirm the broker's max message
size accommodates them. Chunked/segmented upload is **out of scope** (see below).

### F. Human sound test — `testSound` (MQTT) + Web UI button

A diagnostic so a person can confirm audio actually comes out, triggerable from
**both** a Web UI button and an MQTT instant action — which converge on one
handler.

- **Adapter side:** `testSound` is handled like any instant action. It plays the
  requested track as a **bounded override**: a one-shot (play once) by default,
  or for `seconds` if given. It records `self._sound_test_until` (or a one-shot
  flag) so the auto-driver yields (component C) until the test ends, then auto
  resumes. `stopSound` ends it early.
- **Web UI side (no new transport):** the dashboard already turns a button into
  a VDA5050 instant action over MQTT — `_post_action` (`web/server.py:542`)
  builds the payload via `core.control.build_instant_actions` and publishes it
  through `MqttSender` (`self._senders[key]`). So the "사운드 테스트" button just
  needs: `testSound` (and `stopSound`) registered in the adapter spec's
  `instant_actions` (`core/registry.py`) and a button in the dashboard render
  that POSTs `action_type=testSound`. Auth/CSRF are inherited.
- Parameterless `testSound`/`stopSound` work through the existing
  `build_instant_actions` (which passes `action_type`+`action_id`, no
  parameters). Passing `sound`/`seconds`/`volume` from the Web UI form would
  require extending `build_instant_actions` to carry `actionParameters`; the
  default-parameters button needs no such change. Parameterized Web UI controls
  are a follow-up, not required for the test button.

### Config — `config.toml` `[sound_settings]` + `SoundSettings`

Add fields, all with defaults so existing config files keep loading:

```toml
[sound_settings]
enabled        = true                                                 # feature on/off
sink           = "alsa_output.platform-rt5651-sound.stereo-fallback"  # PULSE_SINK; "" = default sink
sound_dir      = "sounds"                                             # mp3 dir (relative → adapter root)
player         = "mplayer"                                            # playback binary
startup_volume = 70                                                   # applied once at startup
travel_music   = "travel.mp3"
work_music     = "work.mp3"
route_prefiX   = ["HANA_TRAVEL_MUSIC_", "HANA_WORK_MUSIC_"]           # unused, kept for compatibility
```

`SoundSettings` (`config/config.py:74`) gains: `enabled: bool = True`,
`sink: str = ""`, `sound_dir: str = "sounds"`, `player: str = "mplayer"`,
`startup_volume: Optional[int] = None`. Existing `travel_music`/`work_music`/
`route_prefiX` are kept.

## Data flow

```
publish loop (every cycle, adapter_jibot.py:~510)
  ├─ _refresh_amr_state_information()      # computes (workingState, detail), publishes AMR_STATE
  └─ _update_sound_for_working_state()     # NEW: resolve track, SoundPlayer.play/stop (dedup)

incoming instant action (MQTT)  ── dispatch chain (:2969)
  ├─ testSound / stopSound       → SoundPlayer + bounded test override (auto yields)
  ├─ setSoundVolume              → pactl sink volume/mute
  └─ uploadSound                 → write mp3 to sound_dir

Web UI "사운드 테스트" button ── _post_action (web/server.py:542)
  └─ build_instant_actions(testSound) → MqttSender → same testSound handler above
```

## Error handling

- Player binary missing → log once, `_disabled`, no-op afterwards.
- mp3 file missing → log warning, skip (auto-driver simply plays nothing).
- `pactl` failure → log, ignore.
- `uploadSound` bad base64 / bad filename / write error → instant action `FAILED`
  with reason; never crashes the loop.
- All `SoundPlayer` calls from the state loop are exception-safe.

## Testing

- `adaptor/tests/test_sound_player.py` (new), `subprocess` mocked:
  - `play` spawns the expected `mplayer` argv incl. loop flag, resolved path, and
    `PULSE_SINK` env; empty sink omits the env.
  - same track twice → one spawn (dedup); different track → stop + respawn.
  - `stop` terminates; `set_volume`/`set_mute` call `pactl` with correct args.
  - missing binary → graceful disable, no raise.
  - path resolution (relative `sound_dir`, absolute track).
- Adapter tests (reuse `FakeVehicle` style in `test_adapter_jibot_v3_order.py`,
  inject a fake `SoundPlayer` capturing `play`/`stop`):
  - `_resolve_sound_track` for each `(workingState, detail)`: `DRIVING/NONE`→travel,
    `*/LOADING`/`*/UNLOADING`/`*/DOCKING`→work, others→None, `enabled=false`→None.
  - auto-driver calls `play`/`stop` accordingly each cycle.
  - `testSound` sets a bounded override so the auto-driver yields; after the
    one-shot/timeout the auto-driver resumes (auto wins). `stopSound` ends it
    early. `setSoundVolume` → correct status + engine calls.
  - `uploadSound`: valid base64 writes file under a temp `sound_dir`; invalid
    base64 → FAILED; path traversal (`../x`) rejected; non-`.mp3` rejected.
  - factsheet `agvActions` includes the new action types
    (`testSound`/`stopSound`/`setSoundVolume`/`uploadSound`).
- Web UI: `testSound`/`stopSound` registered in the adapter spec's
  `instant_actions`; the button POST routes through `_post_action` →
  `build_instant_actions` → `MqttSender` (existing path, exercised by web tests).

## Implementation order

A (engine) → C (auto-play) → D (instant actions + factsheet) → E (upload) →
F (test: MQTT `testSound` then Web UI button). Config change lands with A.

## Out of scope

- Robot-side speaker volume (`UmSetVolume` jibotCommand) integration.
- Web-UI multipart file upload (replaced by `uploadSound` over MQTT).
- Parameterized Web UI sound controls (volume slider, track picker) — needs
  `build_instant_actions` to carry `actionParameters`; follow-up.
- Chunked/segmented upload for large mp3 files.
- Alert/notification sounds for `ERROR`/`BLOCKED` states.
- Persistent manual play mode (auto is authoritative; only bounded test override).
- Test-override recovery across adapter restart.

## Resolved decisions

1. **Auto wins.** The workingState auto-driver is authoritative; the only manual
   path is the bounded, self-clearing `testSound` override. No persistent
   `playSound` override.
2. **Upload over MQTT, single message** — confirmed acceptable; mp3s are small
   enough for one base64 MQTT message. Chunked upload stays out of scope.
3. **`sound_dir`** defaults to `sounds/` under the adapter root — confirmed.
4. **`DOCKING` → work.mp3** — confirmed (detail checked before `DRIVING`).
5. **Sound test** triggerable from both a Web UI button and an MQTT `testSound`
   instant action, converging on one adapter handler.
