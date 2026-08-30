# JIBOT Sound Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play OS-level music on the adapter host that follows the robot's
`workingState`, with volume control, MQTT mp3 upload, and a human sound test.

**Architecture:** A small `SoundPlayer` (subprocess wrapper around `mplayer -ao
pulse`, sink pinned via `PULSE_SINK`) is driven every publish cycle from the
already-implemented `_derive_amr_working_state()`. Volume, a bounded `testSound`,
`stopSound`, and `uploadSound` are exposed as VDA5050 instant actions (advertised
in the factsheet); the Web UI reuses the existing instant-action→MQTT path for a
test button.

**Tech Stack:** Python 3.12, `subprocess`, PulseAudio (`mplayer`, `pactl`),
VDA5050 instant actions, `unittest` + `unittest.mock`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-22-jibot-sound-control-design.md`.
- `workingState`/`workingStateDetail` are already implemented
  (`_derive_amr_working_state()`); this plan **consumes** them, never redefines them.
- Auto-driver is authoritative; the only manual playback is the bounded
  `testSound` override (timed, self-clearing). No persistent manual play mode.
- Playback target sink default: `alsa_output.platform-rt5651-sound.stereo-fallback`
  (empty string = PulseAudio default sink).
- Player default: `mplayer` (already installed on the robot; no new offline deb).
- Sound mapping: `workingStateDetail ∈ {LOADING, UNLOADING, DOCKING}` → `work_music`;
  else `workingState == DRIVING` → `travel_music`; else silence.
- No exception from sound code may propagate into the publish loop.
- Tests use `unittest` and run from the `adaptor/` directory:
  `python -m unittest <module> -v`.
- Every commit message ends with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

- `adaptor/config/config.py` — extend `SoundSettings` dataclass (new fields with defaults).
- `adaptor/config/config.toml` — add the new `[sound_settings]` keys.
- `adaptor/utils/sound.py` — **new** `SoundPlayer` engine (play/stop/volume/mute).
- `adaptor/adapter_jibot.py` — construct `SoundPlayer`, auto-driver hook, instant-action handlers, dispatch, `SUPPORTED_INSTANT_ACTIONS`.
- `adaptor/core/registry.py` — register `testSound`/`stopSound` in `_JIBOT_INSTANT_ACTIONS` (Web UI buttons).
- `adaptor/tests/test_sound_player.py` — **new** unit tests for the engine.
- `adaptor/tests/test_adapter_jibot_v3_order.py` — adapter-level tests (auto-driver, handlers, factsheet).

---

## Task 1: Config — `[sound_settings]` fields

**Files:**
- Modify: `adaptor/config/config.py` (`SoundSettings`, ~line 74; ensure `Optional` imported)
- Modify: `adaptor/config/config.toml` (`[sound_settings]`, ~line 58)
- Test: `adaptor/tests/test_sound_config.py` (create)

**Interfaces:**
- Produces: `SoundSettings(travel_music, work_music, route_prefiX, enabled=True,
  sink="", sound_dir="sounds", player="mplayer", startup_volume=None)`.

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_sound_config.py`:

```python
import unittest

from config.config import SoundSettings


class SoundSettingsTest(unittest.TestCase):
    def test_defaults_apply_when_only_legacy_keys_given(self):
        s = SoundSettings(
            travel_music="travel.mp3",
            work_music="work.mp3",
            route_prefiX=["A_", "B_"],
        )
        self.assertTrue(s.enabled)
        self.assertEqual(s.sink, "")
        self.assertEqual(s.sound_dir, "sounds")
        self.assertEqual(s.player, "mplayer")
        self.assertIsNone(s.startup_volume)

    def test_new_keys_override_defaults(self):
        s = SoundSettings(
            travel_music="t.mp3",
            work_music="w.mp3",
            route_prefiX=[],
            enabled=False,
            sink="sink0",
            sound_dir="/srv/sounds",
            player="mpg123",
            startup_volume=55,
        )
        self.assertFalse(s.enabled)
        self.assertEqual(s.sink, "sink0")
        self.assertEqual(s.sound_dir, "/srv/sounds")
        self.assertEqual(s.player, "mpg123")
        self.assertEqual(s.startup_volume, 55)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_sound_config -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'enabled'`.

- [ ] **Step 3: Extend the dataclass**

In `adaptor/config/config.py`, ensure the typing import includes `Optional`
(e.g. `from typing import List, Optional`), then replace the `SoundSettings`
class body:

```python
@dataclass
class SoundSettings:
    travel_music: str
    work_music: str
    route_prefiX: List[str]
    enabled: bool = True
    sink: str = ""
    sound_dir: str = "sounds"
    player: str = "mplayer"
    startup_volume: Optional[int] = None
```

- [ ] **Step 4: Add the config.toml keys**

In `adaptor/config/config.toml`, replace the `[sound_settings]` block:

```toml
[sound_settings]                      # OS-level sound playback on the adapter host
enabled        = true                 # feature on/off (set false on speaker-less robots)
sink           = "alsa_output.platform-rt5651-sound.stereo-fallback"  # PULSE_SINK; "" = default sink
sound_dir      = "sounds"             # mp3 folder (relative -> resolved against the adaptor dir)
player         = "mplayer"            # playback binary
startup_volume = 70                   # OS sink volume % applied once at startup
travel_music   = "travel.mp3"         # played while DRIVING
work_music     = "work.mp3"           # played while LOADING/UNLOADING/DOCKING
route_prefiX   = ["HANA_TRAVEL_MUSIC_", "HANA_WORK_MUSIC_"]  # unused, kept for compatibility
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd adaptor && python -m unittest tests.test_sound_config -v`
Expected: PASS (2 tests).

Also confirm the full config still loads:
Run: `cd adaptor && python -c "from config.config import get_config; print(get_config().sound_settings)"`
Expected: prints a `SoundSettings(...)` with `enabled=True, sink='alsa_output...', player='mplayer', startup_volume=70`.

- [ ] **Step 6: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml adaptor/tests/test_sound_config.py
git commit -m "feat(sound): add [sound_settings] config fields

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `SoundPlayer` engine

**Files:**
- Create: `adaptor/utils/sound.py`
- Test: `adaptor/tests/test_sound_player.py` (create)

**Interfaces:**
- Consumes: `SoundSettings` values (sink, player, sound_dir, enabled) — passed as plain args.
- Produces:
  - `SoundPlayer(*, sink="", player="mplayer", sound_dir="sounds", base_dir=None, enabled=True)`
  - `.sound_dir` → absolute resolved directory (str)
  - `.play(track: str, loop: bool = True) -> None`
  - `.stop() -> None`
  - `.set_volume(percent: int) -> None`
  - `.set_mute(muted: bool) -> None`
  - `.is_playing() -> bool`

- [ ] **Step 1: Write the failing tests**

Create `adaptor/tests/test_sound_player.py`:

```python
import os
import unittest
from unittest import mock

from utils.sound import SoundPlayer


class _FakeProc:
    def __init__(self):
        self._alive = True
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0

    def kill(self):
        self._alive = False


class SoundPlayerTest(unittest.TestCase):
    def _player(self, **kw):
        kw.setdefault("sink", "sink0")
        kw.setdefault("sound_dir", "/snd")
        return SoundPlayer(**kw)

    def test_play_spawns_expected_mplayer_command_with_sink_env(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3")
        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            ["mplayer", "-really-quiet", "-ao", "pulse", "-loop", "0", "/snd/travel.mp3"],
        )
        self.assertEqual(kwargs["env"]["PULSE_SINK"], "sink0")

    def test_play_without_loop_omits_loop_flag(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3", loop=False)
        self.assertNotIn("-loop", popen.call_args[0][0])

    def test_empty_sink_omits_pulse_sink_env(self):
        p = self._player(sink="")
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("t.mp3")
        self.assertNotIn("PULSE_SINK", popen.call_args[1]["env"])

    def test_same_track_does_not_respawn(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3")
            p.play("travel.mp3")
        self.assertEqual(popen.call_count, 1)

    def test_different_track_stops_and_respawns(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", side_effect=[_FakeProc(), _FakeProc()]) as popen:
            p.play("travel.mp3")
            first = p._proc
            p.play("work.mp3")
        self.assertTrue(first.terminated)
        self.assertEqual(popen.call_count, 2)

    def test_stop_terminates(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()):
            p.play("t.mp3")
            proc = p._proc
            p.stop()
        self.assertTrue(proc.terminated)
        self.assertFalse(p.is_playing())

    def test_set_volume_calls_pactl(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.run") as run:
            p.set_volume(150)  # clamped to 100
        self.assertEqual(
            run.call_args[0][0],
            ["pactl", "set-sink-volume", "sink0", "100%"],
        )

    def test_set_mute_calls_pactl(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.run") as run:
            p.set_mute(True)
        self.assertEqual(run.call_args[0][0], ["pactl", "set-sink-mute", "sink0", "1"])

    def test_missing_binary_disables_without_raising(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", side_effect=FileNotFoundError()):
            p.play("t.mp3")          # must not raise
        with mock.patch("utils.sound.subprocess.Popen") as popen:
            p.play("t.mp3")          # disabled -> no further spawn
            popen.assert_not_called()

    def test_relative_sound_dir_resolved_against_base_dir(self):
        p = SoundPlayer(sink="", sound_dir="sounds", base_dir="/opt/adaptor")
        self.assertEqual(p.sound_dir, "/opt/adaptor/sounds")

    def test_disabled_player_is_noop(self):
        p = SoundPlayer(sink="", sound_dir="/snd", enabled=False)
        with mock.patch("utils.sound.subprocess.Popen") as popen:
            p.play("t.mp3")
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m unittest tests.test_sound_player -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.sound'`.

- [ ] **Step 3: Implement `SoundPlayer`**

Create `adaptor/utils/sound.py`:

```python
"""OS-level sound playback for the adapter host.

Thin wrapper around an external player (mplayer by default) routed through
PulseAudio. One playback process at a time; same-track calls are deduped so the
auto-driver can call play() every cycle without restarting audio. No exception
escapes — a missing player binary disables the player; other failures are logged.
"""

import atexit
import logging
import os
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


class SoundPlayer:
    def __init__(
        self,
        *,
        sink: str = "",
        player: str = "mplayer",
        sound_dir: str = "sounds",
        base_dir: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        self._sink = sink or ""
        self._player = player or "mplayer"
        base = base_dir or os.getcwd()
        self.sound_dir = sound_dir if os.path.isabs(sound_dir) else os.path.join(base, sound_dir)
        self._disabled = not enabled
        self._proc: Optional[subprocess.Popen] = None
        self._current: Optional[str] = None
        atexit.register(self.stop)

    def _resolve(self, track: str) -> str:
        return track if os.path.isabs(track) else os.path.join(self.sound_dir, track)

    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def play(self, track: str, loop: bool = True) -> None:
        if self._disabled:
            return
        path = self._resolve(track)
        if self.is_playing() and self._current == path:
            return
        if not os.path.exists(path):
            logger.warning("sound file not found: %s", path)
            return
        self.stop()
        cmd: List[str] = [self._player, "-really-quiet", "-ao", "pulse"]
        if loop:
            cmd += ["-loop", "0"]
        cmd.append(path)
        env = dict(os.environ)
        if self._sink:
            env["PULSE_SINK"] = self._sink
        try:
            self._proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._current = path
        except FileNotFoundError:
            logger.error("sound player binary not found: %s (sound disabled)", self._player)
            self._disabled = True
        except Exception as exc:  # noqa: BLE001 - never propagate into the loop
            logger.error("sound play failed: %s", exc)

    def stop(self) -> None:
        proc, self._proc, self._current = self._proc, None, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except Exception:
                proc.kill()
        except Exception as exc:  # noqa: BLE001
            logger.error("sound stop failed: %s", exc)

    def _pactl(self, *args: str) -> None:
        target = self._sink or "@DEFAULT_SINK@"
        try:
            subprocess.run(["pactl", *args[:1], target, *args[1:]], timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("pactl %s failed: %s", args, exc)

    def set_volume(self, percent: int) -> None:
        if self._disabled:
            return
        clamped = max(0, min(100, int(percent)))
        self._pactl("set-sink-volume", f"{clamped}%")

    def set_mute(self, muted: bool) -> None:
        if self._disabled:
            return
        self._pactl("set-sink-mute", "1" if muted else "0")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd adaptor && python -m unittest tests.test_sound_player -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add adaptor/utils/sound.py adaptor/tests/test_sound_player.py
git commit -m "feat(sound): add SoundPlayer OS playback engine

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Auto-play driver wired to `workingState`

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`__init__` ~line 102; publish loop after `_refresh_amr_state_information()` ~line 516; new methods)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `SoundPlayer` (Task 2); `self._derive_amr_working_state()` (existing).
- Produces: `self._sound`, `self._sound_test_until`, `_resolve_sound_track()`,
  `_update_sound_for_working_state()`.

- [ ] **Step 1: Write the failing tests**

Add to the `AdapterV3OrderTest` class in
`adaptor/tests/test_adapter_jibot_v3_order.py`:

```python
    def test_resolve_sound_track_maps_working_state(self) -> None:
        adapter = self._make_adapter()
        ss = adapter.config.sound_settings

        cases = {
            ("DRIVING", "NONE"): ss.travel_music,
            ("ACTING", "LOADING"): ss.work_music,
            ("ACTING", "UNLOADING"): ss.work_music,
            ("DRIVING", "DOCKING"): ss.work_music,   # detail wins over DRIVING
            ("IDLE", "NONE"): None,
            ("CHARGING", "NONE"): None,
            ("ERROR", "FAULT"): None,
        }
        for (ws, detail), expected in cases.items():
            with mock.patch.object(
                adapter, "_derive_amr_working_state", return_value=(ws, detail)
            ):
                self.assertEqual(adapter._resolve_sound_track(), expected, (ws, detail))

    def test_resolve_sound_track_none_when_disabled(self) -> None:
        adapter = self._make_adapter()
        adapter.config.sound_settings.enabled = False
        with mock.patch.object(
            adapter, "_derive_amr_working_state", return_value=("DRIVING", "NONE")
        ):
            self.assertIsNone(adapter._resolve_sound_track())

    def test_update_sound_plays_and_stops(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake
        with mock.patch.object(adapter, "_resolve_sound_track", return_value="travel.mp3"):
            adapter._update_sound_for_working_state()
        self.assertEqual(fake.played, ["travel.mp3"])
        with mock.patch.object(adapter, "_resolve_sound_track", return_value=None):
            adapter._update_sound_for_working_state()
        self.assertEqual(fake.stops, 1)

    def test_update_sound_yields_to_active_test_override(self) -> None:
        adapter = self._make_adapter()
        fake = _FakeSound()
        adapter._sound = fake
        adapter._sound_test_until = time.monotonic() + 60
        with mock.patch.object(adapter, "_resolve_sound_track", return_value="travel.mp3"):
            adapter._update_sound_for_working_state()
        self.assertEqual(fake.played, [])  # auto suppressed during test
```

Add this fake near the top of the test module (after the imports / alongside
`FakeVehicle`):

```python
class _FakeSound:
    def __init__(self):
        self.played = []
        self.stops = 0
        self.volumes = []
        self.mutes = []
        self.sound_dir = "/tmp/snd"

    def play(self, track, loop=True):
        self.played.append(track)

    def stop(self):
        self.stops += 1

    def set_volume(self, percent):
        self.volumes.append(percent)

    def set_mute(self, muted):
        self.mutes.append(muted)

    def is_playing(self):
        return bool(self.played)
```

Ensure the test module imports `time` and `from unittest import mock` (add if missing).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_resolve_sound_track_maps_working_state -v`
Expected: FAIL — `AttributeError: 'Adapter' object has no attribute '_resolve_sound_track'`.

- [ ] **Step 3: Construct `SoundPlayer` in `__init__`**

In `adaptor/adapter_jibot.py`, add the import near the other `from ...` lines:

```python
from utils.sound import SoundPlayer
```

In `__init__`, immediately after `self.config = config if config is not None else get_config()` (~line 102), add:

```python
        ss = self.config.sound_settings
        self._sound = SoundPlayer(
            sink=ss.sink,
            player=ss.player,
            sound_dir=ss.sound_dir,
            base_dir=os.path.dirname(os.path.abspath(__file__)),
            enabled=ss.enabled,
        )
        self._sound_test_until = 0.0
        self._sound_started = False
```

- [ ] **Step 4: Add the resolver and driver methods**

Add these methods to the `Adapter` class (e.g. just after
`_refresh_amr_state_information`, ~line 785):

```python
    def _resolve_sound_track(self) -> Optional[str]:
        ss = self.config.sound_settings
        if not getattr(ss, "enabled", True):
            return None
        working_state, detail = self._derive_amr_working_state()
        if detail in ("LOADING", "UNLOADING", "DOCKING"):
            return ss.work_music
        if working_state == "DRIVING":
            return ss.travel_music
        return None

    def _update_sound_for_working_state(self) -> None:
        sound = getattr(self, "_sound", None)
        if sound is None:
            return
        if not self._sound_started:
            self._sound_started = True
            startup_volume = self.config.sound_settings.startup_volume
            if startup_volume is not None:
                try:
                    sound.set_volume(startup_volume)
                except Exception as exc:  # noqa: BLE001
                    print(f"[SOUND] startup volume failed: {exc}")
        if self._sound_test_until > time.monotonic():
            return  # bounded test override active; auto yields
        try:
            track = self._resolve_sound_track()
            if track is None:
                sound.stop()
            else:
                sound.play(track)
        except Exception as exc:  # noqa: BLE001 - never break the publish loop
            print(f"[SOUND] update failed: {exc}")
```

- [ ] **Step 5: Wire the driver into the publish loop**

In `adaptor/adapter_jibot.py`, find (~line 516):

```python
            self._refresh_amr_state_information()
```

Add the call right after it:

```python
            self._refresh_amr_state_information()
            self._update_sound_for_working_state()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_resolve_sound_track_maps_working_state tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_resolve_sound_track_none_when_disabled tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_update_sound_plays_and_stops tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_update_sound_yields_to_active_test_override -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(sound): drive playback from workingState in the publish loop

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Volume / test / stop instant actions + factsheet

**Files:**
- Modify: `adaptor/adapter_jibot.py` (`SUPPORTED_INSTANT_ACTIONS` ~line 1619; dispatch chain ~line 3044; new handlers; class const)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `self._sound` (Task 3), `self._sound_test_until`,
  `_update_instant_action_status(action_id, status, result_description=None)`,
  `ActionStatus`, action objects with `.action_id`, `.action_type`,
  `.action_parameters` (each `.key`, `.value`).
- Produces handlers: `_handle_set_sound_volume_instant_action`,
  `_handle_test_sound_instant_action`, `_handle_stop_sound_instant_action`;
  class const `_SOUND_TEST_SECONDS = 10.0`.

- [ ] **Step 1: Write the failing tests**

Add to `AdapterV3OrderTest`:

```python
    def _instant(self, action_type, params=None):
        from protocol.vda_2_0_0.vda5050_2_0_0_instantActions import (
            InstantActions, Action, ActionParameter,
        )
        action = Action(
            action_type=action_type,
            action_id=f"a-{action_type}",
            blocking_type="NONE",
            action_parameters=[ActionParameter(key=k, value=v) for k, v in (params or {}).items()],
        )
        return action

    def test_set_sound_volume_action(self) -> None:
        adapter = self._make_adapter()
        adapter._sound = _FakeSound()
        adapter.state.instant_action_states.append(
            ActionState(action_id="a-setSoundVolume", action_status=ActionStatus.WAITING,
                        action_type="setSoundVolume")
        )
        adapter._handle_set_sound_volume_instant_action(
            self._instant("setSoundVolume", {"volume": 42, "mute": "false"})
        )
        self.assertEqual(adapter._sound.volumes, [42])
        self.assertEqual(adapter._sound.mutes, [False])
        st = next(s for s in adapter.state.instant_action_states if s.action_id == "a-setSoundVolume")
        self.assertEqual(st.action_status, ActionStatus.FINISHED)

    def test_test_sound_action_sets_override_and_plays(self) -> None:
        adapter = self._make_adapter()
        adapter._sound = _FakeSound()
        adapter.state.instant_action_states.append(
            ActionState(action_id="a-testSound", action_status=ActionStatus.WAITING,
                        action_type="testSound")
        )
        before = time.monotonic()
        adapter._handle_test_sound_instant_action(self._instant("testSound", {"sound": "work"}))
        self.assertEqual(adapter._sound.played, [adapter.config.sound_settings.work_music])
        self.assertGreater(adapter._sound_test_until, before)

    def test_stop_sound_action_clears_override(self) -> None:
        adapter = self._make_adapter()
        adapter._sound = _FakeSound()
        adapter._sound_test_until = time.monotonic() + 60
        adapter.state.instant_action_states.append(
            ActionState(action_id="a-stopSound", action_status=ActionStatus.WAITING,
                        action_type="stopSound")
        )
        adapter._handle_stop_sound_instant_action(self._instant("stopSound"))
        self.assertEqual(adapter._sound.stops, 1)
        self.assertEqual(adapter._sound_test_until, 0.0)

    def test_factsheet_advertises_sound_actions(self) -> None:
        adapter = self._make_adapter()
        fs = adapter._build_factsheet()
        types = {a["actionType"] for a in fs["protocolFeatures"]["agvActions"]}
        self.assertIn("setSoundVolume", types)
        self.assertIn("testSound", types)
        self.assertIn("stopSound", types)
        self.assertIn("uploadSound", types)
```

(Confirm the `Action`/`ActionParameter` import path matches the one already used
in the test module's other instant-action tests; reuse that exact import if it
differs.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_set_sound_volume_action -v`
Expected: FAIL — `AttributeError: ... has no attribute '_handle_set_sound_volume_instant_action'`.

- [ ] **Step 3: Register the action types**

In `SUPPORTED_INSTANT_ACTIONS` (~line 1619), add four entries before the closing
`)`:

```python
        "setSoundVolume",
        "testSound",
        "stopSound",
        "uploadSound",
```

- [ ] **Step 4: Add the test-duration constant**

Near `DOCKING_STATUS_ACTION_TYPE` (~line 1658) add:

```python
    _SOUND_TEST_SECONDS = 10.0
```

- [ ] **Step 5: Add dispatch branches**

In the instant-action dispatch chain (~line 3044, the
`if action.action_type == "cancelOrder": ... elif ...` block), add:

```python
            elif action.action_type == "setSoundVolume":
                self._handle_set_sound_volume_instant_action(action)
            elif action.action_type == "testSound":
                self._handle_test_sound_instant_action(action)
            elif action.action_type == "stopSound":
                self._handle_stop_sound_instant_action(action)
```

(`uploadSound` is dispatched in Task 5.)

- [ ] **Step 6: Implement the handlers**

Add to the `Adapter` class (near the other `_handle_*_instant_action` methods):

```python
    def _handle_set_sound_volume_instant_action(self, action: Any) -> None:
        params = {p.key: p.value for p in action.action_parameters}
        try:
            if "volume" in params:
                self._sound.set_volume(int(float(params["volume"])))
            mute = params.get("mute")
            if mute is not None:
                self._sound.set_mute(str(mute).strip().lower() in ("1", "true", "yes", "on"))
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED, "sound volume updated"
            )
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"setSoundVolume failed: {exc}"
            )

    def _handle_test_sound_instant_action(self, action: Any) -> None:
        params = {p.key: p.value for p in action.action_parameters}
        ss = self.config.sound_settings
        sound = str(params.get("sound", "travel"))
        track = {"travel": ss.travel_music, "work": ss.work_music}.get(sound, sound)
        try:
            seconds = float(params.get("seconds", self._SOUND_TEST_SECONDS))
        except (TypeError, ValueError):
            seconds = self._SOUND_TEST_SECONDS
        self._sound_test_until = time.monotonic() + seconds
        try:
            self._sound.play(track, loop=True)
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED,
                f"testing {track} for {seconds:.0f}s",
            )
        except Exception as exc:  # noqa: BLE001
            self._sound_test_until = 0.0
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"testSound failed: {exc}"
            )

    def _handle_stop_sound_instant_action(self, action: Any) -> None:
        self._sound_test_until = 0.0
        try:
            self._sound.stop()
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED, "sound stopped"
            )
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"stopSound failed: {exc}"
            )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_set_sound_volume_action tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_test_sound_action_sets_override_and_plays tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_stop_sound_action_clears_override tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_factsheet_advertises_sound_actions -v`
Expected: PASS (4 tests).

- [ ] **Step 8: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(sound): setSoundVolume/testSound/stopSound instant actions + factsheet

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `uploadSound` instant action (MQTT mp3 transfer)

**Files:**
- Modify: `adaptor/adapter_jibot.py` (dispatch chain; new handler)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: `self._sound.sound_dir` (absolute dir), `_update_instant_action_status`, `ActionStatus`.
- Produces handler: `_handle_upload_sound_instant_action`.

- [ ] **Step 1: Write the failing tests**

Add to `AdapterV3OrderTest`:

```python
    def test_upload_sound_writes_file(self) -> None:
        import base64, os, tempfile
        adapter = self._make_adapter()
        adapter._sound = _FakeSound()
        with tempfile.TemporaryDirectory() as d:
            adapter._sound.sound_dir = d
            adapter.state.instant_action_states.append(
                ActionState(action_id="a-uploadSound", action_status=ActionStatus.WAITING,
                            action_type="uploadSound")
            )
            data = base64.b64encode(b"ID3-fake-mp3-bytes").decode()
            adapter._handle_upload_sound_instant_action(
                self._instant("uploadSound", {"fileName": "travel.mp3", "data": data})
            )
            with open(os.path.join(d, "travel.mp3"), "rb") as f:
                self.assertEqual(f.read(), b"ID3-fake-mp3-bytes")
            st = next(s for s in adapter.state.instant_action_states if s.action_id == "a-uploadSound")
            self.assertEqual(st.action_status, ActionStatus.FINISHED)

    def test_upload_sound_rejects_non_mp3(self) -> None:
        import base64, tempfile
        adapter = self._make_adapter()
        adapter._sound = _FakeSound()
        with tempfile.TemporaryDirectory() as d:
            adapter._sound.sound_dir = d
            adapter.state.instant_action_states.append(
                ActionState(action_id="a-uploadSound", action_status=ActionStatus.WAITING,
                            action_type="uploadSound")
            )
            adapter._handle_upload_sound_instant_action(
                self._instant("uploadSound", {"fileName": "evil.sh", "data": base64.b64encode(b"x").decode()})
            )
            st = next(s for s in adapter.state.instant_action_states if s.action_id == "a-uploadSound")
            self.assertEqual(st.action_status, ActionStatus.FAILED)

    def test_upload_sound_strips_path_traversal(self) -> None:
        import base64, os, tempfile
        adapter = self._make_adapter()
        adapter._sound = _FakeSound()
        with tempfile.TemporaryDirectory() as d:
            adapter._sound.sound_dir = d
            adapter.state.instant_action_states.append(
                ActionState(action_id="a-uploadSound", action_status=ActionStatus.WAITING,
                            action_type="uploadSound")
            )
            adapter._handle_upload_sound_instant_action(
                self._instant("uploadSound",
                              {"fileName": "../../etc/x.mp3", "data": base64.b64encode(b"x").decode()})
            )
            self.assertTrue(os.path.exists(os.path.join(d, "x.mp3")))

    def test_upload_sound_rejects_bad_base64(self) -> None:
        import tempfile
        adapter = self._make_adapter()
        adapter._sound = _FakeSound()
        with tempfile.TemporaryDirectory() as d:
            adapter._sound.sound_dir = d
            adapter.state.instant_action_states.append(
                ActionState(action_id="a-uploadSound", action_status=ActionStatus.WAITING,
                            action_type="uploadSound")
            )
            adapter._handle_upload_sound_instant_action(
                self._instant("uploadSound", {"fileName": "t.mp3", "data": "!!!not-base64!!!"})
            )
            st = next(s for s in adapter.state.instant_action_states if s.action_id == "a-uploadSound")
            self.assertEqual(st.action_status, ActionStatus.FAILED)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_upload_sound_writes_file -v`
Expected: FAIL — `AttributeError: ... '_handle_upload_sound_instant_action'`.

- [ ] **Step 3: Add the dispatch branch**

In the dispatch chain (Task 4, ~line 3044), add after the `stopSound` branch:

```python
            elif action.action_type == "uploadSound":
                self._handle_upload_sound_instant_action(action)
```

- [ ] **Step 4: Implement the handler**

Add to the `Adapter` class:

```python
    def _handle_upload_sound_instant_action(self, action: Any) -> None:
        import base64
        params = {p.key: p.value for p in action.action_parameters}
        file_name = os.path.basename(str(params.get("fileName", "")).strip())
        if not file_name or not file_name.lower().endswith(".mp3"):
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED,
                "fileName must be a .mp3 basename",
            )
            return
        try:
            raw = base64.b64decode(str(params.get("data", "")), validate=True)
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"invalid base64: {exc}"
            )
            return
        try:
            target_dir = self._sound.sound_dir
            os.makedirs(target_dir, exist_ok=True)
            dest = os.path.join(target_dir, file_name)
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, dest)
            self._update_instant_action_status(
                action.action_id, ActionStatus.FINISHED,
                f"stored {file_name} ({len(raw)} bytes)",
            )
        except Exception as exc:  # noqa: BLE001
            self._update_instant_action_status(
                action.action_id, ActionStatus.FAILED, f"write failed: {exc}"
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd adaptor && python -m unittest tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_upload_sound_writes_file tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_upload_sound_rejects_non_mp3 tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_upload_sound_strips_path_traversal tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.test_upload_sound_rejects_bad_base64 -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "feat(sound): uploadSound instant action (base64 mp3 over MQTT)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Web UI sound-test buttons

**Files:**
- Modify: `adaptor/core/registry.py` (`_JIBOT_INSTANT_ACTIONS`, ~line 177)
- Test: `adaptor/tests/test_registry_sound_actions.py` (create)

**Interfaces:**
- Consumes: `InstantAction(action_type, label, motion=False)` (existing dataclass).
- Produces: `testSound`/`stopSound` entries in the JIBOT spec's `instant_actions`,
  which the dashboard renders as buttons and `WebUi._post_action` publishes over
  MQTT (existing path, no new transport).

- [ ] **Step 1: Write the failing test**

Create `adaptor/tests/test_registry_sound_actions.py`:

```python
import unittest

from core.registry import _JIBOT_INSTANT_ACTIONS


class RegistrySoundActionsTest(unittest.TestCase):
    def test_jibot_exposes_sound_test_buttons(self):
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        self.assertIn("testSound", by_type)
        self.assertIn("stopSound", by_type)
        # parameterless buttons go through the default instant-action POST path
        self.assertFalse(by_type["testSound"].motion)
        self.assertFalse(by_type["stopSound"].motion)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd adaptor && python -m unittest tests.test_registry_sound_actions -v`
Expected: FAIL — `AssertionError: 'testSound' not found in ...`.

- [ ] **Step 3: Register the buttons**

In `adaptor/core/registry.py`, add two entries to `_JIBOT_INSTANT_ACTIONS`
(before the closing `)`, ~line 182):

```python
    InstantAction("testSound", "Sound test", motion=False),
    InstantAction("stopSound", "Stop sound", motion=False),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd adaptor && python -m unittest tests.test_registry_sound_actions -v`
Expected: PASS.

- [ ] **Step 5: Manual verification (no code)**

Confirm the dashboard renders the new buttons (instant-action buttons are
generated from the spec's `instant_actions`, the same list `WebUi._post_action`
validates against). Clicking "Sound test" POSTs `action_type=testSound`, which
`build_instant_actions` + `MqttSender` publish to the adapter — handled by Task 4.
If the dashboard template hardcodes its action list instead of iterating
`spec.instant_actions`, add the two buttons there.

- [ ] **Step 6: Commit**

```bash
git add adaptor/core/registry.py adaptor/tests/test_registry_sound_actions.py
git commit -m "feat(sound): expose testSound/stopSound buttons in the web UI

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Run the full sound + adapter test set**

Run: `cd adaptor && python -m unittest tests.test_sound_config tests.test_sound_player tests.test_registry_sound_actions tests.test_adapter_jibot_v3_order -v`
Expected: all PASS.

---

## Self-Review notes (author check)

- **Spec coverage:** A=Task 2; B=already implemented (consumed in Task 3);
  C=Task 3; D=Task 4; E=Task 5; F=Task 4 (`testSound`/`stopSound`) + Task 6
  (web buttons); config=Task 1. Factsheet exposure=Task 4. All covered.
- **Type consistency:** `_resolve_sound_track()`/`_update_sound_for_working_state()`,
  `_sound`, `_sound_test_until`, `_SOUND_TEST_SECONDS`, `SoundPlayer.sound_dir`,
  and `_update_instant_action_status(action_id, status, result_description)` are
  used identically across tasks.
- **Refinement vs spec:** `testSound` uses a **timed** override
  (`_SOUND_TEST_SECONDS = 10`, overridable by `seconds`) rather than play-once,
  for deterministic auto-resume. Behavior is otherwise as specified.
