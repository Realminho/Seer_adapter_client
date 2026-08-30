# PIO station_id / channel Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `station_id` and `channel` off `extension "pio"` onto the facility blocks that own them, and refuse to boot when the old keys linger.

**Architecture:** Three green steps then a form-contract step. Add the new fields first (nothing reads them), switch every consumer over (old fields still present but unused), then delete the old fields and add the boot guard. The `pioPing` WebUI contract follows separately.

**Tech Stack:** Python 3.12, pytest, HCL config.

Spec: `docs/superpowers/specs/2026-08-15-pio-station-channel-ownership.md`

## Global Constraints

- Tests run with `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" <path>`; the script `cd`s into `adaptor/`, so test paths are relative to `adaptor/`. **The `--python` flag is required** — without it the script lands on a `python3` without `hcl2`.
- **Run every command in the foreground.** Do not background a test run.
- The full suite has 7 pre-existing failures unrelated to this work: `test_adapter_jibot_v3_order.py` pio_init/pio_ping ×6, `test_goto_nearest_node.py::test_timeout_stops_robot_and_fails`. Do not chase them. Your gate: no new failures.
- `media`, `port`, `vehicle_num` stay on `extension "pio"` and keep their fallbacks — they describe this robot, not the facility. Only `station_id` and `channel` move.
- Air shower values are carried over unchanged: `pio_station_id = "123456"`, `channel = 250`. `123456` is the example value it resolves to today; carrying it over is behaviour-preserving. Mark it, do not guess a replacement.
- `extension "elevator"` gets one block-level `channel = 250`. It does **not** go on the `motion_rules` entries: that table is keyed by node segment (six entries, two stations), so a per-rule channel would be written six times and need an agreement check. The channel is the elevator system's radio; the per-floor `pio_station_id` stays where it is.
- Never use `git add -A`, `git add .`, or `git commit -a` — the working tree has unrelated uncommitted work (`adaptor/web/render.py`, `adaptor/extensions/*/panel.html`, `adaptor/tests/test_action_modules.py`, `adaptor/tests/test_action_module_panels_render.py`, `docs/guide/web-ui.md`). Run `git status --short` before each commit and stage only the files the task names.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `adaptor/config/config.py` | dataclass fields, assembly, boot guard | 1, 3 |
| `adaptor/config/extensions.hcl` + `.example` + root `extensions.hcl` | the three shipped copies | 1, 3 |
| `adaptor/utils/airshower.py` | reads its own station/channel | 2 |
| `adaptor/utils/elevator.py` | takes `channel` as an argument | 2 |
| `adaptor/extensions/facility/__init__.py` | station→(pin, channel) resolution | 2 |
| `adaptor/extensions/pio/__init__.py` | link params, station list, pioPing spec | 2, 4 |
| `adaptor/extensions/pio/panel.html` | pioPing help text | 4 |
| `adaptor/tests/*` | per task | 1-4 |

---

### Task 1: Add the new fields alongside the old ones

Nothing reads them yet, so the tree stays green throughout.

**Files:**
- Modify: `adaptor/config/config.py` — `AirShowerPioConfig` (line 481), `ElevatorPioConfig` (line 503)
- Modify: `adaptor/config/extensions.hcl`, `adaptor/config/extensions.hcl.example`, `extensions.hcl` (repo root)
- Test: `adaptor/tests/test_extensions_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AirShowerPioConfig.pio_station_id: str`, `AirShowerPioConfig.channel: int`, `ElevatorPioConfig.channel: int`. Task 2 reads all three. `ElevatorMotionRule` is **unchanged**.

- [ ] **Step 1: Write the failing test**

In `adaptor/tests/test_extensions_config.py`, add `pio_station_id`/`channel` to the sample's `extension "airshower"` block and `channel = 250` to its `extension "elevator"` block, then add:

```python
    def test_facility_blocks_own_their_station_and_channel(self):
        data = self._load()
        air = data["air_shower_pio"]
        self.assertEqual(air["pio_station_id"], "123456")
        self.assertEqual(air["channel"], 250)
        # 엘리베이터는 시스템 하나에 층 station이 여럿이라 channel은 블록에 하나다.
        self.assertEqual(data["elevator_pio"]["channel"], 250)
```

Check the real section names first — `load_extensions` promotes `extension "airshower"` and `extension "elevator"` into their own sections (`_extension_section`). Use whatever keys the loader actually produces; run the loader once and print the section names if unsure.

- [ ] **Step 2: Run the test to verify it fails**

Run: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" tests/test_extensions_config.py -k facility_blocks -v`
Expected: FAIL — the new keys are rejected or absent.

- [ ] **Step 3: Add the dataclass fields**

In `adaptor/config/config.py`, `AirShowerPioConfig` (line 481) gains two required fields. Put them first, before `failure`, so the required/defaulted ordering stays valid:

```python
class AirShowerPioConfig:
    # 상대 설비를 고르는 BC 주소다. extension "pio"가 아니라 이 설비가 소유한다 —
    # 에어샤워와 엘리베이터는 서로 다른 station에 붙는다.
    pio_station_id: str
    channel: int
    failure: int
```

`ElevatorPioConfig` (line 503) gains `channel`. Put it first, before `open_door_pin`, so required/defaulted ordering stays valid:

```python
class ElevatorPioConfig:
    # 이 엘리베이터 시스템이 듣는 무선 채널이다. station은 층마다 다르지만
    # (motion_rules의 pio_station_id) 채널은 설비 하나에 하나다.
    channel: int
    open_door_pin: int
    close_door_pin: int
```

`ElevatorMotionRule` is **not** changed, and neither is its assembly site.

- [ ] **Step 4: Add the keys to all three HCL copies**

In each of `adaptor/config/extensions.hcl`, `adaptor/config/extensions.hcl.example`, and the repo-root `extensions.hcl`:

`extension "airshower"` gains, at the top of the block:

```hcl
  # 이 설비의 BC 주소다. extension "pio"의 station_id/channel에서 옮겨 왔다 —
  # 에어샤워와 엘리베이터는 같은 station을 쓰지 않는다.
  pio_station_id = "123456"   # ← 예전 extension "pio" 값 그대로다. 현장 실제 값 확인 필요
  channel        = 250
```

`extension "elevator"` gains, at the top of the block:

```hcl
  # 이 엘리베이터 시스템이 듣는 무선 채널이다. extension "pio"의 channel에서
  # 옮겨 왔다. station은 층마다 다르지만(motion_rules의 pio_station_id) 채널은
  # 설비 하나에 하나다.
  channel = 250
```

`motion_rules` entries are left exactly as they are.

In `.example`, also extend the comment above `motion_rules` to say that the station picks which client on `channel` the robot pairs with.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" tests/test_extensions_config.py tests/test_config.py tests/test_recipes_config.py tests/test_recipe_acceptance.py -q`
Expected: PASS

Then the full suite: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" -q`
Expected: 7 pre-existing failures, no new ones.

- [ ] **Step 6: Commit**

```bash
git add adaptor/config/config.py adaptor/config/extensions.hcl \
        adaptor/config/extensions.hcl.example extensions.hcl \
        adaptor/tests/test_extensions_config.py
git commit -m "feat(pio): give the facility blocks their own station and channel"
```

---

### Task 2: Switch every consumer to the facility-owned values

The old `PioConfig` fields still exist after this task; nothing reads them.

**Files:**
- Modify: `adaptor/utils/airshower.py:153-154`
- Modify: `adaptor/utils/elevator.py` — `EVWorkflow.__init__` (line ~141), line ~192
- Modify: `adaptor/extensions/facility/__init__.py` — `_run_elevator` (line 77), `elevator_stations` (line 101), `resolve_station` (line 127)
- Modify: `adaptor/extensions/pio/__init__.py` — `pio_link_params` (line ~147), `known_station_ids` (line 1002)
- Test: `adaptor/tests/test_recipes_config.py`, `adaptor/tests/test_recipe_acceptance.py`

**Interfaces:**
- Consumes: the three fields from Task 1.
- Produces: `EVWorkflow(floor_pin, pio_station_id, action, *, channel, config_data=None, pio=None, ezi_io=None)`. `elevator_stations()` and `resolve_station()` keep their current signatures — the channel is not per station.

- [ ] **Step 1: Write the failing tests**

In `adaptor/tests/test_recipes_config.py`, the door-recipe loop currently asserts
`door.steps[0].parameters["channel"] == config.pio_config.channel`. Replace that
assertion with a comparison against the motion rule that owns the same station:

```python
        # channel은 이제 설비가 소유한다. recipe가 적은 값과 어긋나면 recipe는
        # 옛 채널로 BC를 보내고 조용히 성공한 척한다.
        assert door.steps[0].parameters["channel"] == config.elevator_config.channel
```

Apply the same substitution in `adaptor/tests/test_recipe_acceptance.py` where it
reads `config.pio_config.channel`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" tests/test_recipes_config.py tests/test_recipe_acceptance.py -q`
Expected: FAIL.

Task 1 already added `elevator_config.channel`, so this substitution may pass
straight away. If it does, make it fail first — temporarily assert the wrong
value, confirm the RED, then restore. Do not skip the RED step; the point is to
prove the assertion is actually reached.

- [ ] **Step 3: Air shower reads its own values**

`adaptor/utils/airshower.py:153-154`:

```python
        self.station_id = self.config_data.air_shower_config.pio_station_id
        self.channel = self.config_data.air_shower_config.channel
```

Leave the `media` / `port` / `vehicle_num` lines around them alone.

- [ ] **Step 4: Elevator takes channel as an argument**

`adaptor/utils/elevator.py`, `EVWorkflow.__init__` — add a keyword-only `channel`:

```python
    def __init__(
        self,
        floor_pin: int,
        pio_station_id: str,
        action: str,
        *,
        channel: int,
        config_data=None,
        pio=None,
        ezi_io=None,
    ):
```

and at line ~192 replace `self.channel = self.config_data.pio_config.channel` with:

```python
        self.channel = channel
```

- [ ] **Step 5: Facility passes the block-level channel**

In `adaptor/extensions/facility/__init__.py`, `elevator_stations` and
`resolve_station` are **unchanged** — the channel is not per station, so it has no
place in the station lookup.

Only `_run_elevator` changes: both branches read the same block-level channel, so
the legacy `floorPin` + `pioStationId` path needs no special handling.

```python
    chosen = ctx.params.get("station") or ctx.params.get("stationId")
    if chosen:
        floor_pin, station = resolve_station(ctx.adapter.config, chosen)
    else:
        floor_pin = _required_int(ctx.params, "floorPin", "floor_pin")
        station = _required_text(ctx.params, "pioStationId", "pio_station_id")
    workflow = EVWorkflow(
        floor_pin,
        station,
        action,
        # 채널은 설비 하나에 하나다. station은 그 채널 위의 어느 층인지를 고른다.
        channel=ctx.adapter.config.elevator_config.channel,
        config_data=ctx.adapter.config,
        pio=get_pio_client(ctx.adapter),
        ezi_io=_ezi_client(ctx.adapter),
    )
```

- [ ] **Step 6: PIO link params stop falling back**

In `adaptor/extensions/pio/__init__.py`, `pio_link_params`, replace the two
fallback entries with values that must be supplied:

```python
    station_id = params.get("stationId")
    channel = params.get("channel")
    if station_id is None or str(station_id).strip() == "":
        raise ValueError(
            "PIO pairing requires stationId; extension \"pio\"의 station_id "
            "폴백은 설비 블록으로 옮겨졌다"
        )
    if channel is None or str(channel).strip() == "":
        raise ValueError(
            "PIO pairing requires channel; extension \"pio\"의 channel "
            "폴백은 설비 블록으로 옮겨졌다"
        )
```

and use them in the returned dict (`"station_id": station_id, "channel": channel`).
`media`, `bc_port`, `oht_number`, `select_pin`, `go_pin` keep their fallbacks.

`known_station_ids` (line 1002) drops the `pio_config.station_id` seed and adds the
air shower. Update the docstring's first two lines to match:

```python
def known_station_ids(config: Any) -> Tuple[str, ...]:
    """설정이 아는 설비 station 목록.

    station은 설비마다 다르다 — extension "airshower"의 pio_station_id와
    elevator motion rule의 pio_station_id다. 자유 입력이면 어긋난 값을 넣고도
    pairing 실패만 보게 되므로 목록으로 고르게 한다.

    facility extension을 import하지 않고 config에서 직접 읽는다. 그쪽을 꺼도
    PIO 패널은 그대로 떠야 한다.
    """
    ids: list = []
    air = str(
        getattr(getattr(config, "air_shower_config", None), "pio_station_id", "") or ""
    ).strip()
    if air:
        ids.append(air)
```

Keep the existing motion-rule loop and de-duplication that follow.

- [ ] **Step 7: Run the tests**

Run: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" tests/test_recipes_config.py tests/test_recipe_acceptance.py tests/test_facility_actions.py tests/test_action_modules.py -q`

If `tests/test_facility_actions.py` does not exist, run whatever test files reference
`elevator_stations`, `resolve_station` or `EVWorkflow` — find them with
`grep -rln "elevator_stations\|resolve_station\|EVWorkflow" adaptor/tests/`.

Then the full suite. Expected: the 7 pre-existing failures only. Any test that
constructs `EVWorkflow` or unpacks `elevator_stations` pairs must be updated in this
task — that is part of the work, not a separate concern.

- [ ] **Step 8: Commit**

```bash
git add adaptor/utils/airshower.py adaptor/utils/elevator.py \
        adaptor/extensions/facility/__init__.py adaptor/extensions/pio/__init__.py \
        adaptor/tests/test_recipes_config.py adaptor/tests/test_recipe_acceptance.py
git commit -m "refactor(pio): read station and channel from the facility that owns them"
```

Add any other test files you had to touch to the `git add` line.

---

### Task 3: Delete the old fields and refuse the old keys

**Files:**
- Modify: `adaptor/config/config.py` — `PioConfig` lines 87-88, plus the guard beside the `pio_port` guard (~line 915)
- Modify: `adaptor/config/extensions.hcl`, `adaptor/config/extensions.hcl.example`, `extensions.hcl` (repo root)
- Test: `adaptor/tests/test_config.py`, `adaptor/tests/test_pio_output_mapping.py`, `adaptor/tests/test_pio_select_timing.py`

**Interfaces:**
- Consumes: Task 2 removed every reader.
- Produces: `PioConfig` no longer has `station_id` or `channel`.

- [ ] **Step 1: Write the failing test**

In `adaptor/tests/test_config.py`, model this on the existing `pio_port` guard test
(around lines 605-616 — find it with `grep -n "pio_port" adaptor/tests/test_config.py`):

```python
    def test_moved_pio_station_and_channel_are_refused(self):
        """옮겨진 키가 남아 있으면 조용히 무시하지 않고 부팅을 막는다.

        무시하면 운영자가 extension "pio"를 고쳐도 값이 안 바뀌는 상태가 된다.
        """
        for key, line in (
            ("station_id", '  station_id = "123456"'),
            ("channel", "  channel = 250"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(Exception) as caught:
                    self._load_with_pio_line(line)
                message = str(caught.exception)
                self.assertIn(key, message)
                self.assertIn("airshower", message)
```

Write `_load_with_pio_line` (or inline the equivalent) to build an `extensions.hcl`
that is valid apart from the extra line inside `extension "pio"`, mirroring how the
`pio_port` test constructs its input.

- [ ] **Step 2: Run the test to verify it fails**

Run: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" tests/test_config.py -k moved_pio -v`
Expected: FAIL — no error is raised, or the message does not name the new owner.

- [ ] **Step 3: Add the guard**

In `adaptor/config/config.py`, beside the existing `pio_port` guard:

```python
# station_id/channel은 상대 설비를 고르는 값이라 설비 블록이 소유한다(2026-08-15).
# 로더가 미지의 키를 거부하므로 그냥 두면 PioConfig가 TypeError로 죽어 무엇을
# 고쳐야 하는지 알 수 없다.
_MOVED_PIO_KEYS = {
    "station_id": 'extension "airshower"의 pio_station_id, '
                  'extension "elevator" motion_rules의 pio_station_id',
    "channel": 'extension "airshower"의 channel, '
               'extension "elevator" motion_rules의 channel',
}
```

and, where the `pio_port` check runs:

```python
    for _key, _owner in _MOVED_PIO_KEYS.items():
        if _key in config_dict["pio"]:
            raise ExtensionsError(
                f'extensions.hcl: extension "pio"의 {_key}는 {_owner}로 '
                "옮겨졌습니다. 해당 줄을 지우고 설비 블록에 적으세요."
            )
```

Guard the `config_dict["pio"]` lookup the same way the neighbouring `pio_port`
check does — do not assume the section exists.

- [ ] **Step 4: Remove the fields and the shipped lines**

`adaptor/config/config.py`: delete `station_id: str` (line 87) and `channel: int`
(line 88) from `PioConfig`.

In all three HCL copies, delete the `station_id` and `channel` lines from
`extension "pio"`, and clean the block's opening comment so it no longer promises
"직렬·무선 채널 정보" — it now describes the serial link and this robot's identity
only.

`adaptor/tests/test_pio_output_mapping.py` (lines ~29-30) and
`adaptor/tests/test_pio_select_timing.py` (lines ~34-35) construct `PioConfig(...)`
with `station_id=` / `channel=`; drop those arguments.

- [ ] **Step 5: Run the tests**

Run: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" tests/test_config.py tests/test_pio_output_mapping.py tests/test_pio_select_timing.py tests/test_extensions_config.py -q`
Expected: PASS

Then the full suite. Expected: the 7 pre-existing failures only.

- [ ] **Step 6: Commit**

```bash
git add adaptor/config/config.py adaptor/config/extensions.hcl \
        adaptor/config/extensions.hcl.example extensions.hcl \
        adaptor/tests/test_config.py adaptor/tests/test_pio_output_mapping.py \
        adaptor/tests/test_pio_select_timing.py
git commit -m "feat(pio): refuse station_id and channel left on the pio block"
```

---

### Task 4: the PIO panel asks for the station and channel

**Why this task is load-bearing.** Task 2's review found that removing the
`pio_link_params` fallback kills the shipped WebUI manual PIO panel. The panel
cards for `pioInit` and `pioPing` (`adaptor/extensions/pio/panel.html:5-19`)
render **no** `stationId`/`channel` inputs, and `render.py`'s `actions_page()`
suppresses the schema-driven fallback form for any action a hand-written panel
already covers. So:

- `pioInit` — `action_specs` already marks both required, so `server.py`'s
  `_post_action` rejects every click with `missing required parameter` before
  the handler runs. Dead button.
- `pioPing` — declared optional, so it passes the form gate and then raises
  inside `pio_link_params`. Dead button.
- `pioScenario` — never declared `stationId`/`channel` at all and has no panel
  card, so no WebUI path can supply them. Dead.

This task closes all three. The regression is knowingly carried between Task 2
and here: the branch ships as one unit and is not deployed mid-plan.

**Files:**
- Modify: `adaptor/extensions/pio/__init__.py` — `action_specs` (~lines 1073-1090)
- Modify: `adaptor/extensions/pio/panel.html` (lines ~27-33)
- Test: `adaptor/tests/test_action_modules.py`

**Interfaces:**
- Consumes: `known_station_ids` from Task 2.
- Produces: nothing further.

Note: `adaptor/extensions/pio/panel.html` and `adaptor/tests/test_action_modules.py`
carry unrelated uncommitted work from another effort. Read `git diff` on both before
editing, change only the `pioPing` parts, and stage only these files.

- [ ] **Step 1: Write the failing test**

In `adaptor/tests/test_action_modules.py`, the `pioPing` required set (around lines
417 and 425) gains `stationId` and `channel`. The assertion around lines 437-439 that
`stations[0] == config.pio_config.station_id` must go — `pio_config` no longer has
one. Replace it with a check that the offered stations come from the facilities:

```python
        # station 목록은 이제 설비에서 온다. 빈 첫 항목("config 값 사용")은 없다.
        assert "" not in stations
        assert config.air_shower_config.pio_station_id in stations
        for rule in config.elevator_config.elevator_motion_rules:
            assert rule.pio_station_id in stations
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" tests/test_action_modules.py -k pio -v`
Expected: FAIL — `stationId`/`channel` are not required, and the empty choice is present.

- [ ] **Step 3: Make them required**

In `action_specs`, the `pioPing` parameters for `stationId` and `channel` become
`required=True`, lose the `placeholder="비우면 config 값"` text, and `stationId`'s
choices drop the empty first entry (`("",) + stations` becomes `stations`). Delete the
comment above the block that reads `# 점검용이라 비우면 전부 config 값으로 pairing을 시도한다.`

`pioScenario`'s spec never declared `stationId`/`channel`. Add them with the same
shape as `pioInit`'s (required, `stationId` offering `known_station_ids(config)`),
so the WebUI can supply what `pio_link_params` now demands.

- [ ] **Step 4: Give the panel cards real input fields**

This is the part that makes the buttons work again. In
`adaptor/extensions/pio/panel.html`, the `pioInit` card (~lines 5-11) and the
`pioPing` card (~lines 12-19) post only `csrf_token` / `return_to` /
`action_type` / `confirm`. Add `stationId` and `channel` inputs to both, following
the field markup the other cards in this file already use, and add a `pioScenario`
card with the same two fields if one is missing.

Read `adaptor/web/render.py`'s `actions_page()` and `_run_form` first to match the
field naming the server expects — `server.py`'s `_post_action` reads the posted
names and checks them against `action_specs()`'s required list, so a mismatched
name reads as "missing required parameter".

The panel's `pioPing` description (~lines 27-33) says the fields may be left blank
to use the config values. Replace that with a line saying both are required and
naming where an operator finds them (`extension "airshower"`, the elevator
`motion_rules`, or the recipe's `pioInit`).

**Verify by rendering, not by reading.** After the change, confirm that a
`pioInit` post carrying `stationId`/`channel` passes `_post_action`'s required
check — `tests/test_action_module_panels_render.py` is the existing harness for
panel rendering; extend it rather than inventing a new one.

- [ ] **Step 5: Run the tests**

Run: `scripts/run-tests.sh --python "$PWD/adaptor/.venv/bin/python" tests/test_action_modules.py tests/test_action_module_panels_render.py -q`
Expected: PASS

Then the full suite. Expected: the 7 pre-existing failures only.

- [ ] **Step 6: Commit**

```bash
git add adaptor/extensions/pio/__init__.py adaptor/extensions/pio/panel.html \
        adaptor/tests/test_action_modules.py
git commit -m "feat(pio): require stationId and channel on pioPing"
```

---

## Deployment note

After Task 3 a robot whose `extensions.hcl` still carries `station_id` or `channel`
under `extension "pio"` refuses to boot with a message naming the new owner. That is
intended. `192.168.101.61` has both lines today and has **not** been restarted since
its config was updated, so its `extensions.hcl` must be updated in the same pass as
this code. Do not restart it with this code until the config is fixed.
