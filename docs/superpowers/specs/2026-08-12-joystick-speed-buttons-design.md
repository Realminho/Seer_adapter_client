# Joystick −/+ Speed Scaling Design

## Goal

Make the Ultimate 2 controller's `−`/`+` buttons (SELECT/START) step the manual
driving speed instead of the sound volume, so an operator can slow the robot
down in a tight aisle without leaving the controller.

## Current behaviour

`−`/`+` submit `setSoundVolume` instant actions through
`JoystickActionDispatcher.step_volume` (`adaptor/utils/joystick_runtime.py:130`).
The dispatcher is gated by `actions_enabled`, the same master switch that guards
the D-pad+ABXY slots, so volume stepping is off by default on every robot.

Driving magnitudes are fixed: `Ultimate2JoystickService._apply` multiplies the
trigger and steering fractions by `manual_control.drive_trans` / `drive_rot` and
passes `drive_speed` through unchanged
(`adaptor/utils/joystick_runtime.py:362`). Nothing on the controller can change
them.

## Design

### Where the state lives

The scale factor is a driving parameter, not an instant action, so
`Ultimate2JoystickService` owns it as `self._speed_percent` — not
`JoystickActionDispatcher`.

The consequence is deliberate: **speed stepping is not gated by
`actions_enabled`.** That switch exists because slots fire real motor, clamp and
charge actions that need a per-robot review. Scaling the speed of a drive the
operator is already performing carries no such risk, and gating it would leave
every robot with dead `−`/`+` buttons until an unrelated switch is flipped.

The value persists for the life of the service — including across device
disconnect/reconnect, since `run()` keeps the instance — and resets to
`speed_start_percent` when the adapter restarts.

### Calculation

`_apply()` scales all three magnitudes by the same factor, so the
straight/turn/speed ratio the robot was tuned with is preserved:

```python
scale = self._speed_percent / 100.0
trans = float(mc.drive_trans) * scale * (self.forward - self.reverse)
rot = -float(mc.drive_rot) * scale * self.steering
await self.adapter._vehicle.um_drive(
    trans, rot, float(mc.drive_speed) * scale, float(mc.drive_lat)
)
```

`drive_lat` is not scaled: it is 0 on every current robot and is a lateral
offset rather than a magnitude.

### Immediate effect while driving

`step_speed(direction)` clamps the new value, logs it, and — when `self._moving`
— awaits `self._apply()` right away instead of waiting for the next heartbeat.
Pressing `−` mid-drive slows the robot within one event loop turn rather than up
to `heartbeat_ms` (300 ms) later.

Pressing at a bound is a no-op: the value is unchanged, so no `um_drive` is
re-sent and the log says nothing.

### Pure helper

`adaptor/utils/joystick.py` gains

```python
def step_speed_percent(current, direction, step, minimum, maximum) -> int
```

keeping the clamping logic testable without a device, matching the existing
split where `joystick.py` holds pure decoding/mapping logic and
`joystick_runtime.py` holds the IO loop.

### Configuration

New keys on the `joystick` block:

| Key | Default | Meaning |
|---|---|---|
| `speed_step_percent` | 20 | Change per press |
| `speed_min_percent` | 20 | Floor — never 0, which would look like a dead controller |
| `speed_max_percent` | 100 | Ceiling — 100 % is the configured `manual_control` magnitude |
| `speed_start_percent` | 100 | Value at adapter start |

`speed_min_percent` must be ≥ 1 and ≤ `speed_max_percent`, and
`speed_start_percent` must fall inside the range; `load_extensions` rejects the
block otherwise, the same way it already rejects unknown joystick fields.

### Renamed keys and backward compatibility

The button fields are renamed to match what they now do:

| Old | New |
|---|---|
| `volume_down_button` | `speed_down_button` |
| `volume_up_button` | `speed_up_button` |
| `xboxdrv_volume_down_button_code` | `xboxdrv_speed_down_button_code` |
| `xboxdrv_volume_up_button_code` | `xboxdrv_speed_up_button_code` |

`load_extensions` still accepts the four old names. When the new name is absent
the old value is used and a deprecation line is printed; when both are present
the new name wins and the old one is reported as ignored. This matters because
`extensions.py` refuses to boot on an unknown field, so a bare rename would take
down every already-deployed robot whose `extensions.hcl` still says
`volume_up_button`.

`volume_step` is different: it has no successor. It is accepted and ignored with
a deprecation line, and removed from the repository's own HCL files.

### What is removed

`JoystickActionDispatcher.step_volume` and the `DEFAULT_VOLUME` constant. The
joystick can no longer change the volume; the WebUI `setSoundVolume` control is
unaffected and remains the way to do it.

## Out of scope

`MicroJoystickService` — the Micro has no `−`/`+` pair, and its D-pad is the
drive input.

## Testing

`adaptor/tests/test_joystick.py`

- `step_speed_percent` steps up and down by `step`
- clamps at both bounds and returns the current value unchanged there
- a step size that would overshoot lands exactly on the bound

`adaptor/tests/test_joystick_runtime.py`

- `+` and `−` scale the `um_drive` arguments on the joydev path
- the same on the xboxdrv evdev path, via the button codes
- a press while moving re-sends `um_drive` immediately, without a heartbeat
- a press at the bound sends nothing
- stepping works with `actions_enabled = false`
- the existing volume-button tests are replaced, not kept

`adaptor/tests/test_extensions_config.py`

- the new `speed_*` keys parse
- a legacy `volume_up_button` is honoured as `speed_up_button`
- `speed_min_percent > speed_max_percent` is rejected

## Documentation

`docs/manual/joystick-runtime-setup.md` §3-C is retitled from "slot action과 음량
버튼" to cover speed stepping, including that it works independently of
`actions_enabled`, and the input table in §1 loses its volume row.
