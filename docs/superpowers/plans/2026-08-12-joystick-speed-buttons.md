# Joystick −/+ Speed Scaling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Ultimate 2 controller's `−`/`+` buttons step the manual driving speed instead of the sound volume.

**Architecture:** `Ultimate2JoystickService` gains a `_speed_percent` scale factor that `_apply()` multiplies into `drive_trans`, `drive_rot` and `drive_speed`. The clamping arithmetic lives as a pure function in `utils/joystick.py`; the config layer renames the four `volume_*` button keys to `speed_*` and keeps the old names working as aliases so already-deployed robots still boot.

**Tech Stack:** Python 3.12, pytest, HCL config (`adaptor/config/extensions.hcl`).

Spec: `docs/superpowers/specs/2026-08-12-joystick-speed-buttons-design.md`

## Global Constraints

- Speed stepping is **not** gated by `actions_enabled`. That switch guards slot actions only.
- Default speed range: `speed_step_percent = 20`, `speed_min_percent = 20`, `speed_max_percent = 100`, `speed_start_percent = 100`.
- `speed_min_percent` is never 0 — a 0 % scale looks like a dead controller.
- `drive_lat` is **not** scaled.
- `MicroJoystickService` is out of scope — the Micro has no `−`/`+` pair.
- Tests run with `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" <path>`; the script `cd`s into `adaptor/`, so test paths are relative to `adaptor/` (e.g. `tests/test_joystick.py`). The `--python` flag is required: the script's own fallback finds no venv at the repo root and lands on a `python3` without `hcl2`, which makes `tests/test_extensions_config.py` fail at collection.
- The joystick volume path is removed entirely; the WebUI `setSoundVolume` control is untouched.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `adaptor/utils/joystick.py` | pure `step_speed_percent` clamping | 1 |
| `adaptor/tests/test_joystick.py` | unit tests for the helper | 1 |
| `adaptor/config/config.py` | `JoystickConfig` field rename + new speed fields | 2 |
| `adaptor/config/extensions.py` | allowlist, legacy aliases, range validation | 2 |
| `adaptor/tests/test_extensions_config.py` | config parsing tests | 2 |
| `adaptor/utils/joystick_runtime.py` | scale state, `step_speed`, `_apply` scaling, button wiring | 3 |
| `adaptor/tests/test_joystick_runtime.py` | runtime tests | 3 |
| `adaptor/config/extensions.hcl`, `.example`, `extensions.hcl` | shipped config | 4 |
| `scripts/joystick_input_test.py` | `--learn` output emits the new key names | 4 |
| `docs/manual/joystick-runtime-setup.md`, `docs/manual/joystick-input-test.md` | operator docs | 4 |

---

### Task 1: Pure speed-stepping helper

**Files:**
- Modify: `adaptor/utils/joystick.py` (insert between `chord_slot` ending at line 183 and `validate_joystick_actions` at line 186)
- Test: `adaptor/tests/test_joystick.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `step_speed_percent(current: int, direction: int, step: int, minimum: int, maximum: int) -> int`. `direction` is `+1` for `+` and `-1` for `−`. Returns `current` unchanged when the press cannot move the value, so callers can skip re-sending a drive command.

- [ ] **Step 1: Write the failing tests**

Append to `adaptor/tests/test_joystick.py`:

```python
def test_step_speed_percent_steps_both_ways():
    assert step_speed_percent(60, 1, 20, 20, 100) == 80
    assert step_speed_percent(60, -1, 20, 20, 100) == 40


def test_step_speed_percent_clamps_to_the_configured_range():
    assert step_speed_percent(90, 1, 20, 20, 100) == 100
    assert step_speed_percent(30, -1, 20, 20, 100) == 20


def test_step_speed_percent_at_a_bound_returns_the_current_value():
    # The caller uses "unchanged" to mean "do not re-send a drive command".
    assert step_speed_percent(100, 1, 20, 20, 100) == 100
    assert step_speed_percent(20, -1, 20, 20, 100) == 20


def test_step_speed_percent_ignores_a_zero_direction_or_step():
    assert step_speed_percent(60, 0, 20, 20, 100) == 60
    assert step_speed_percent(60, 1, 0, 20, 100) == 60
```

Add `step_speed_percent` to the existing `from utils.joystick import (...)` block at the top of the file, keeping the list alphabetical (it goes after `normalize_axis`, before `validate_joystick_actions` — check the actual list and insert in place).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run-tests.sh tests/test_joystick.py -k step_speed -v`
Expected: FAIL — `ImportError: cannot import name 'step_speed_percent'`

- [ ] **Step 3: Write the implementation**

In `adaptor/utils/joystick.py`, after `chord_slot`:

```python
def step_speed_percent(
    current: int, direction: int, step: int, minimum: int, maximum: int
) -> int:
    """Clamp one -/+ press into the configured manual-drive speed range.

    Returning ``current`` unchanged at a bound is the signal the runtime uses
    to skip re-sending a drive command that would be identical to the last one.
    """
    if direction == 0 or step <= 0:
        return current
    target = current + (step if direction > 0 else -step)
    return max(minimum, min(maximum, target))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `scripts/run-tests.sh tests/test_joystick.py -v`
Expected: PASS (the whole file, not just the new tests)

- [ ] **Step 5: Commit**

```bash
git add adaptor/utils/joystick.py adaptor/tests/test_joystick.py
git commit -m "feat: add pure speed-percent stepping helper"
```

---

### Task 2: Config schema — rename the volume keys, add the speed range

**Files:**
- Modify: `adaptor/config/config.py:747-748`, `:756`, `:772-773`
- Modify: `adaptor/config/extensions.py:162-196`
- Test: `adaptor/tests/test_extensions_config.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `JoystickConfig` fields `speed_down_button`, `speed_up_button`, `xboxdrv_speed_down_button_code`, `xboxdrv_speed_up_button_code`, `speed_step_percent`, `speed_min_percent`, `speed_max_percent`, `speed_start_percent`. `volume_step` no longer exists. Task 3 reads all of these off `adapter.config.joystick`.

Note: `adaptor/tests/test_joystick_runtime.py` builds its own `SimpleNamespace` config and does not touch `JoystickConfig`, so it keeps passing through this task.

- [ ] **Step 1: Write the failing tests**

In `adaptor/tests/test_extensions_config.py`, add the four speed keys to the `joystick "ultimate2"` block inside `SAMPLE` (currently at lines 70-79) and delete its `volume_step = 5` line:

```hcl
    joystick "ultimate2" {
      enabled             = true
      vendor_id           = "2dc8"
      product_id          = "3109"
      forward_axis        = 5
      reverse_axis        = 2
      steering_axis       = 0
      speed_step_percent  = 20
      speed_min_percent   = 20
      speed_max_percent   = 100
      speed_start_percent = 100
      heartbeat_timeout_ms = 400
    }
```

Add a second module-level sample next to `SAMPLE` (use the same `dedent(...).strip()` form the file already uses for `SAMPLE`):

```python
LEGACY_JOYSTICK = dedent(
    """
    joystick "ultimate2" {
      enabled                         = true
      volume_up_button                = 9
      volume_down_button              = 8
      volume_step                     = 5
      xboxdrv_volume_up_button_code   = 400
      xboxdrv_volume_down_button_code = 401
    }
    """
).strip()
```

Add these test methods to the same class the other `test_joystick_*` methods live in:

```python
    def test_joystick_speed_range_keys_parse(self):
        data = self._load()
        self.assertEqual(data["joystick"]["speed_step_percent"], 20)
        self.assertEqual(data["joystick"]["speed_min_percent"], 20)
        self.assertEqual(data["joystick"]["speed_max_percent"], 100)
        self.assertEqual(data["joystick"]["speed_start_percent"], 100)

    def test_legacy_volume_keys_map_onto_the_speed_buttons(self):
        # Already-deployed robots still say volume_*; an unknown field would
        # stop them booting, so the old names stay accepted as aliases.
        data = self._load(LEGACY_JOYSTICK)
        self.assertEqual(data["joystick"]["speed_up_button"], 9)
        self.assertEqual(data["joystick"]["speed_down_button"], 8)
        self.assertEqual(data["joystick"]["xboxdrv_speed_up_button_code"], 400)
        self.assertEqual(data["joystick"]["xboxdrv_speed_down_button_code"], 401)
        self.assertNotIn("volume_up_button", data["joystick"])
        self.assertNotIn("volume_step", data["joystick"])

    def test_new_speed_button_key_wins_over_the_legacy_name(self):
        body = LEGACY_JOYSTICK.replace(
            "volume_up_button                = 9",
            "volume_up_button                = 9\n  speed_up_button                 = 3",
        )
        self.assertEqual(self._load(body)["joystick"]["speed_up_button"], 3)

    def test_joystick_speed_range_validation(self):
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE.replace("speed_min_percent   = 20", "speed_min_percent   = 120"))
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE.replace("speed_min_percent   = 20", "speed_min_percent   = 0"))
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE.replace("speed_start_percent = 100", "speed_start_percent = 10"))
        with self.assertRaises(extensions.ExtensionsError):
            self._load(SAMPLE.replace("speed_step_percent  = 20", "speed_step_percent  = 0"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run-tests.sh tests/test_extensions_config.py -v`
Expected: FAIL — the speed keys are rejected as `unknown field(s)`.

- [ ] **Step 3: Update the dataclass**

In `adaptor/config/config.py`, inside `JoystickConfig`:

- line 747-748: rename `volume_down_button` → `speed_down_button`, `volume_up_button` → `speed_up_button` (defaults 6 and 7 stay).
- line 756: replace `volume_step: int = 5` with:

```python
    # -/+ scale the manual drive. 100 % is the configured manual_control
    # magnitude; the floor is never 0, which would read as a dead controller.
    speed_step_percent: int = 20
    speed_min_percent: int = 20
    speed_max_percent: int = 100
    speed_start_percent: int = 100
```

- line 772-773: rename `xboxdrv_volume_down_button_code` → `xboxdrv_speed_down_button_code` and `xboxdrv_volume_up_button_code` → `xboxdrv_speed_up_button_code` (defaults 314 and 315 stay).
- line 779-781: the comment above `actions_enabled` says "and the volume buttons". Change that phrase to "only" so it reads: `Master switch for the D-pad+ABXY slot actions.`

- [ ] **Step 4: Update the parser**

In `adaptor/config/extensions.py`, add module-level constants next to the other module constants near the top:

```python
#: Renamed when -/+ stopped changing the volume. Deployed robots still carry the
#: old names and extensions.hcl rejects unknown fields, so a bare rename would
#: stop them booting.
_LEGACY_JOYSTICK_KEYS = {
    "volume_down_button": "speed_down_button",
    "volume_up_button": "speed_up_button",
    "xboxdrv_volume_down_button_code": "xboxdrv_speed_down_button_code",
    "xboxdrv_volume_up_button_code": "xboxdrv_speed_up_button_code",
}

#: Accepted and dropped: the joystick no longer touches the volume at all.
_DROPPED_JOYSTICK_KEYS = ("volume_step",)
```

Then, after line 167 (`joystick["profile"] = ...`) and before the `allowed_joystick` set:

```python
    for legacy, current in _LEGACY_JOYSTICK_KEYS.items():
        if legacy not in joystick:
            continue
        value = joystick.pop(legacy)
        if current in joystick:
            print(f"[EXTENSIONS] {file_path}: '{legacy}' ignored; '{current}' wins")
            continue
        joystick[current] = value
        print(f"[EXTENSIONS] {file_path}: '{legacy}' is deprecated; rename it to '{current}'")
    for dropped in _DROPPED_JOYSTICK_KEYS:
        if dropped in joystick:
            joystick.pop(dropped)
            print(
                f"[EXTENSIONS] {file_path}: '{dropped}' no longer does anything; "
                "-/+ step the drive speed"
            )
```

(These are the first `print` calls in this module. That is deliberate — the message has to reach `journalctl` at boot, and raising would defeat the point of keeping deployed robots bootable.)

Update `allowed_joystick` (lines 168-184): replace `"volume_down_button"`, `"volume_up_button"`, `"volume_step"`, `"xboxdrv_volume_down_button_code"`, `"xboxdrv_volume_up_button_code"` with `"speed_down_button"`, `"speed_up_button"`, `"speed_step_percent"`, `"speed_min_percent"`, `"speed_max_percent"`, `"speed_start_percent"`, `"xboxdrv_speed_down_button_code"`, `"xboxdrv_speed_up_button_code"`.

Then, after the `micro_enabled`/`ultimate2_enabled` check that ends at line 195:

```python
    speed_step = int(joystick.get("speed_step_percent", 20))
    speed_min = int(joystick.get("speed_min_percent", 20))
    speed_max = int(joystick.get("speed_max_percent", 100))
    speed_start = int(joystick.get("speed_start_percent", 100))
    if speed_step < 1:
        raise ExtensionsError(
            f"joystick in {file_path}: speed_step_percent must be at least 1"
        )
    if speed_min < 1 or speed_min > speed_max:
        raise ExtensionsError(
            f"joystick in {file_path}: speed_min_percent must be between 1 and "
            "speed_max_percent"
        )
    if not speed_min <= speed_start <= speed_max:
        raise ExtensionsError(
            f"joystick in {file_path}: speed_start_percent must be between "
            "speed_min_percent and speed_max_percent"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `scripts/run-tests.sh tests/test_extensions_config.py -v`
Expected: PASS

Then the full suite, because `extensions.hcl` and `extensions.hcl.example` still carry the legacy names and other tests load them:

Run: `scripts/run-tests.sh`
Expected: PASS (deprecation lines appear in the captured output — that is the alias working)

- [ ] **Step 6: Commit**

```bash
git add adaptor/config/config.py adaptor/config/extensions.py adaptor/tests/test_extensions_config.py
git commit -m "feat: add joystick speed range config and alias the volume keys"
```

---

### Task 3: Runtime — -/+ scale the drive instead of the volume

**Files:**
- Modify: `adaptor/utils/joystick_runtime.py:13-21` (imports), `:30-33` (`DEFAULT_VOLUME`), `:130-142` (`step_volume`), `:287-311` (`__init__`), `:345-375` (`_apply`), `:376-408` (`_handle_event`), `:409-438` (`_handle_evdev_event`)
- Test: `adaptor/tests/test_joystick_runtime.py`

**Interfaces:**
- Consumes: `step_speed_percent` from Task 1; the `speed_*` config fields from Task 2.
- Produces: `Ultimate2JoystickService._speed_percent: int` and `async def step_speed(self, direction: int) -> None`. Nothing outside this file calls them.

- [ ] **Step 1: Update the test fixture and write the failing tests**

In `adaptor/tests/test_joystick_runtime.py`, in `_adapter()` (lines 18-79):

- drop the `startup_volume=40` keyword argument and the `sound = SimpleNamespace(...)` line, and drop `sound_settings=sound` from the `config=SimpleNamespace(...)` call — nothing reads them any more.
- replace `volume_down_button=6,` / `volume_up_button=7,` with `speed_down_button=6,` / `speed_up_button=7,`
- replace `volume_step=5,` with:

```python
        speed_step_percent=20,
        speed_min_percent=20,
        speed_max_percent=100,
        speed_start_percent=100,
```

- replace `xboxdrv_volume_down_button_code=314,` / `xboxdrv_volume_up_button_code=315,` with `xboxdrv_speed_down_button_code=314,` / `xboxdrv_speed_up_button_code=315,`

Delete `test_ultimate2_volume_buttons_step_and_clamp` (lines 372-395) and add:

```python
def test_ultimate2_speed_buttons_scale_the_drive():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("button", 6, 1))  # - : 100 -> 80
        assert service._speed_percent == 80
        await service._handle_event(_js_event("axis", 5, 32767))
        adapter._vehicle.um_drive.assert_awaited_with(160.0, 0.0, 80.0, 0.0)

        # Pressing + mid-drive re-sends immediately; the heartbeat is not the
        # thing that applies it.
        await service._handle_event(_js_event("button", 7, 1))  # + : 80 -> 100
        adapter._vehicle.um_drive.assert_awaited_with(200.0, 0.0, 100.0, 0.0)

    asyncio.run(scenario())


def test_ultimate2_speed_clamps_and_sends_nothing_at_the_bound():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_event(_js_event("axis", 5, 32767))
        adapter._vehicle.um_drive.assert_awaited_once_with(200.0, 0.0, 100.0, 0.0)

        # Already at speed_max_percent: no change, so no second um_drive.
        await service._handle_event(_js_event("button", 7, 1))
        assert service._speed_percent == 100
        assert adapter._vehicle.um_drive.await_count == 1

        for _ in range(5):
            await service._handle_event(_js_event("button", 6, 1))
        assert service._speed_percent == 20
        adapter._vehicle.um_drive.assert_awaited_with(40.0, 0.0, 20.0, 0.0)

    asyncio.run(scenario())


def test_ultimate2_speed_buttons_work_while_slot_actions_are_off():
    async def scenario():
        adapter = _adapter(actions=[_slot(1, "enableMotor")], actions_enabled=False)
        service = Ultimate2JoystickService(adapter)

        # actions_enabled guards slots that run motors and clamps. Scaling a
        # drive the operator is already performing is not that.
        await service._handle_event(_js_event("button", 6, 1))
        assert service._speed_percent == 80
        adapter.submit_local_instant_action.assert_not_called()

    asyncio.run(scenario())


def test_ultimate2_evdev_speed_buttons_use_the_xboxdrv_codes():
    async def scenario():
        adapter = _adapter()
        service = Ultimate2JoystickService(adapter)

        await service._handle_evdev_event(EV_KEY, 314, 1)
        assert service._speed_percent == 80
        await service._handle_evdev_event(EV_ABS, 9, 255)
        adapter._vehicle.um_drive.assert_awaited_with(160.0, 0.0, 80.0, 0.0)

    asyncio.run(scenario())
```

In `test_ultimate2_slot_actions_stay_off_until_enabled` (lines 356-369), delete the two lines under the `# Volume rides the same switch.` comment and the comment itself — that is no longer true. The test keeps its first half (a chord fires nothing while `actions_enabled` is false).

In `test_ultimate2_evdev_chord_and_volume_use_the_xboxdrv_codes` (lines 398-415), rename it to `test_ultimate2_evdev_chord_uses_the_xboxdrv_codes` and delete the trailing two statements that press `EV_KEY, 315` and assert `setSoundVolume`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run-tests.sh tests/test_joystick_runtime.py -v`
Expected: FAIL — `AttributeError: 'Ultimate2JoystickService' object has no attribute '_speed_percent'`

- [ ] **Step 3: Implement the runtime change**

In `adaptor/utils/joystick_runtime.py`:

Add `step_speed_percent` to the `from utils.joystick import (...)` block (lines 13-21), keeping the list alphabetical.

Delete the `DEFAULT_VOLUME` constant and its three comment lines (lines 30-33).

Delete `JoystickActionDispatcher.step_volume` entirely (lines 130-142). Leave `fire_slot` and `_submit` untouched.

In `Ultimate2JoystickService.__init__`, after `self.actions = JoystickActionDispatcher(adapter)`:

```python
        self._speed_percent = int(self.config.speed_start_percent)
```

Add this method right after `_fire_chord`:

```python
    async def step_speed(self, direction: int) -> None:
        """Scale manual driving with -/+ and apply it without waiting.

        Not gated by ``actions_enabled``: that switch guards slots that run
        motors, clamps and charging. Changing the scale of a drive the operator
        is already performing carries none of that, and gating it would leave
        every robot with dead -/+ buttons until an unrelated switch is flipped.
        """
        target = step_speed_percent(
            self._speed_percent,
            direction,
            int(self.config.speed_step_percent),
            int(self.config.speed_min_percent),
            int(self.config.speed_max_percent),
        )
        if target == self._speed_percent:
            return
        self._speed_percent = target
        print(f"[JOYSTICK ULTIMATE2] drive speed {target}%")
        if self._moving:
            await self._apply()
```

In `_apply()`, replace the three lines from `mc = self.adapter.config.manual_control` through the `um_drive` call with:

```python
        mc = self.adapter.config.manual_control
        scale = self._speed_percent / 100.0
        trans = float(mc.drive_trans) * scale * (self.forward - self.reverse)
        rot = -float(mc.drive_rot) * scale * self.steering
        try:
            await self.adapter._vehicle.um_drive(
                trans, rot, float(mc.drive_speed) * scale, float(mc.drive_lat)
            )
```

`drive_lat` stays unscaled.

In `_handle_event`, replace the two volume branches (lines 384-387) with:

```python
            elif event.number == int(self.config.speed_up_button):
                await self.step_speed(1)
            elif event.number == int(self.config.speed_down_button):
                await self.step_speed(-1)
```

In `_handle_evdev_event`, replace the two volume branches (lines 415-418) with:

```python
            elif code == int(self.config.xboxdrv_speed_up_button_code):
                await self.step_speed(1)
            elif code == int(self.config.xboxdrv_speed_down_button_code):
                await self.step_speed(-1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `scripts/run-tests.sh tests/test_joystick_runtime.py -v`
Expected: PASS

Run: `scripts/run-tests.sh`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add adaptor/utils/joystick_runtime.py adaptor/tests/test_joystick_runtime.py
git commit -m "feat: step manual drive speed with the joystick -/+ buttons"
```

---

### Task 4: Ship the config, the learn helper and the docs

**Files:**
- Modify: `adaptor/config/extensions.hcl:220-221`, `:229`, `:247-248`, `:252-253`
- Modify: `adaptor/config/extensions.hcl.example:230-231`, `:239`, `:257-258`, `:262-263`
- Modify: `extensions.hcl:158-159`, `:167`
- Modify: `scripts/joystick_input_test.py:417-419`
- Modify: `docs/manual/joystick-runtime-setup.md` (§1 table, `:106`, `:121-122`, §3-C at `:180-216`)
- Modify: `docs/manual/joystick-input-test.md:81-82`

**Interfaces:**
- Consumes: the key names from Task 2.
- Produces: nothing code-facing.

- [ ] **Step 1: Update `adaptor/config/extensions.hcl`**

In the `joystick "ultimate2"` block:

- line 220-221: `volume_down_button   = 6` → `speed_down_button    = 6`, `volume_up_button     = 7` → `speed_up_button      = 7`
- line 229: replace `volume_step          = 5` with:

```hcl
  # -/+ scale manual driving. 100% is the manual_control magnitude.
  speed_step_percent   = 20
  speed_min_percent    = 20
  speed_max_percent    = 100
  speed_start_percent  = 100
```

- line 247-248: `xboxdrv_volume_down_button_code = 314 # BTN_SELECT` → `xboxdrv_speed_down_button_code = 314 # BTN_SELECT`, same for the `_up_` line with 315 / BTN_START
- line 252-253: the comment reads `Master switch for the D-pad+ABXY slots and the volume buttons below.` Drop `and the volume buttons below` — `-/+` no longer rides this switch.

- [ ] **Step 2: Apply the same edits to `adaptor/config/extensions.hcl.example`**

Same four edits at lines 230-231, 239, 257-258, 262-263.

- [ ] **Step 3: Update the repo-root `extensions.hcl`**

This copy has no `xboxdrv_*` keys. Only two edits: lines 158-159 rename to `speed_down_button` / `speed_up_button`, and line 167 `volume_step          = 5` becomes the same four `speed_*_percent` lines as Step 1.

- [ ] **Step 4: Update the `--learn` output**

In `scripts/joystick_input_test.py`, in the `fields` dict at lines 417-419, rename the two keys so the snippet it prints for operators is pasteable:

```python
        "speed_up_button": buttons.get("+"),
        "speed_down_button": buttons.get("-"),
```

- [ ] **Step 5: Update the docs**

`docs/manual/joystick-runtime-setup.md`:

- §1 input table: change the `−`/`+` row from 음량 to `주행 속도 배율 단계 조절`.
- line 106: the comment `# D-pad+ABXY slot action과 음량 버튼. 기본값 false.` becomes `# D-pad+ABXY slot action. 기본값 false.`
- lines 121-122: rename `xboxdrv_volume_*_button_code` to `xboxdrv_speed_*_button_code`.
- §3-C (lines 180-223): retitle to `slot action과 속도 버튼 (Ultimate 2 전용)`, and replace the `−`/`+` bullet at lines 214-216 with:

```md
- `−`/`+`는 주행 속도 배율을 `speed_step_percent`만큼 내리고 올린다. 범위는
  `speed_min_percent`~`speed_max_percent`이고 어댑터가 뜰 때 `speed_start_percent`에서
  시작한다. 배율은 `drive_trans`·`drive_rot`·`drive_speed`에 똑같이 곱해지므로 직진·회전
  비율은 그대로다. **`actions_enabled`와 무관하게 동작한다** — 그 스위치는 모터·클램프를
  실제로 돌리는 slot 때문에 있는 것이고, 이미 하고 있는 주행의 크기를 바꾸는 것은 그 범주가
  아니다. 주행 중에 눌러도 heartbeat를 기다리지 않고 즉시 반영된다.
```

Add this line directly under that bullet:

```md
- 조이스틱은 더 이상 음량을 바꾸지 않는다. 음량은 WebUI의 `setSoundVolume`으로 조절한다.
```

`docs/manual/joystick-input-test.md` lines 81-82: rename the two keys in the sample snippet to `speed_down_button` / `speed_up_button`.

- [ ] **Step 6: Verify the shipped config still loads**

Run: `scripts/run-tests.sh`
Expected: PASS, and this time with **no** `[EXTENSIONS] ... deprecated` lines from the repo's own HCL files — every shipped file now uses the new names. `tests/test_extensions_config.py::test_legacy_volume_keys_map_onto_the_speed_buttons` still exercises the alias.

- [ ] **Step 7: Commit**

```bash
git add adaptor/config/extensions.hcl adaptor/config/extensions.hcl.example extensions.hcl \
        scripts/joystick_input_test.py docs/manual/joystick-runtime-setup.md \
        docs/manual/joystick-input-test.md
git commit -m "docs: ship the joystick speed keys and retire the volume buttons"
```

---

## Deployment note

`192.168.101.62` already runs `actions_enabled = true` with slots 5/6 enabled, and its `extensions.hcl` still uses `volume_down_button` / `volume_up_button` / `volume_step`. The alias path in Task 2 is what keeps it booting after this change; it will print three deprecation lines at startup until its config is updated. Deploy with `scripts/update-jibot-adapter-over-ssh.sh` (which keeps the remote `extensions.hcl` by default) and restart the adapter to apply.
