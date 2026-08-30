# PIO station_id / channel Ownership Design

## Goal

Move `station_id` and `channel` out of `extension "pio"` and into the facility
blocks that actually know them: `extension "airshower"` and each
`extension "elevator"` motion rule. Reject the old keys at boot rather than
ignoring them.

## Why

`station_id` and `channel` together select **which facility the robot talks
to** over the PIO broadcast. That is a per-facility fact, not a per-robot one.
Holding them on `extension "pio"` forces one global value for every facility,
so the air shower and both elevator stations must share it — which they do not.

Today `extension "pio"` ships `station_id = "123456"`, an example value. Any
caller that does not override it broadcasts to a station that does not exist.
The elevator path already escaped this by putting `pio_station_id` on each
motion rule and, since the recipe rework, on each recipe's `pioInit`. The air
shower did not: `utils/airshower.py:153-154` still reads
`pio_config.station_id` / `.channel`, so it pairs with `123456` today.

`channel` has the same shape one level down. It is the other half of the BC
address, but only `station_id` was ever moved, so a site with per-floor
channels cannot express it. `recipes.hcl` already writes `channel = 250` beside
each `stationId`, and `config/recipes.hcl`'s comment calls that a copy that
must be kept in sync by hand. Giving each facility its own `channel` removes
the copy.

The two values move together because they are one address. Splitting them
would leave a config where the station is per-facility and the channel is
global — the failure mode this change exists to end.

## Design

### Ownership after this change

| Value | Owner |
|---|---|
| air shower station / channel | `extension "airshower"`: `pio_station_id`, `channel` |
| elevator channel | `extension "elevator"`: `channel` — one per elevator system |
| elevator station | each `motion_rules` entry: `pio_station_id` — one per floor (unchanged) |
| recipe station / channel | each recipe's `pioInit` parameters (already the case) |
| `media`, `port`, `vehicle_num` | stays on `extension "pio"` — these describe **this robot**, not the facility |

The elevator splits across two levels on purpose. `channel` is the radio the
elevator system listens on; `pio_station_id` picks which client on that radio,
which is per floor. `motion_rules` is a node-segment trigger table — six entries
for two stations — so putting the channel there would write `250` six times and
then need an "all rules for one station must agree" check to paper over the
duplication. The station/channel-are-one-address rule still holds, at the
facility level: one elevator, one channel, several floors.

### Boot refusal, not silent ignore

`load_extensions` rejects unknown keys, so a robot whose `extensions.hcl` still
says `station_id` under `extension "pio"` would die with a bare `TypeError`
from `PioConfig(**section)` and no clue what to fix. A named guard is added
beside the existing `pio_port` guard in `config.py`:

```python
# station_id/channel은 상대 설비를 고르는 값이라 설비 블록이 소유한다(2026-08-15).
_MOVED_PIO_KEYS = {
    "station_id": 'extension "airshower"의 pio_station_id, '
                  'extension "elevator" motion_rules의 pio_station_id',
    "channel": 'extension "airshower"의 channel, '
               'extension "elevator"의 channel',
}
```

Raising is deliberate and follows the precedent in `config/extensions.py:31`:
silently ignoring a moved key leaves an operator editing the `pio` block and
watching nothing change.

### No fallbacks

`pio_link_params` currently falls back to `pio_config.station_id`/`.channel`
when a caller omits them. That fallback is what let the air shower and the
`pioPing` panel address `123456` without anyone noticing. It is removed: a
missing or empty station/channel raises at the call site. `media`, `port` and
`vehicle_num` keep their fallbacks — they are robot-owned.

`known_station_ids` stops seeding itself with `pio_config.station_id` and
collects from the air shower plus the motion rules instead. `pioPing`'s
`stationId`/`channel` become required, losing the "비우면 config 값"
placeholder and the empty first choice.

### Elevator channel threading

`EVWorkflow` takes `channel` as a constructor argument instead of reading
`pio_config.channel`. `_run_elevator` supplies it from
`config.elevator_config.channel`.

`elevator_stations()` and `resolve_station()` keep their current shapes —
the channel is not per station, so it does not belong in the station lookup.
Both `_run_elevator` branches (a single `station`, or the legacy
`floorPin` + `pioStationId`) read the same block-level channel, so the legacy
path needs no special handling.

## Values

`extension "airshower"` gets `pio_station_id = "000030"` and `channel = 250`.

`000030` is the site-confirmed value. The move initially carried over `"123456"`
— the example value the air shower resolved to through the old global fallback,
which kept the change behaviour-preserving while the real station was unknown.
It was marked rather than guessed, because a wrong station id silently addresses
another facility. Six digits with the leading zeros intact: the string goes into
the BC payload verbatim, so `"00030"` and `"000030"` are different stations.

`extension "elevator"` gets `channel = 250`, matching today's global value.

## Out of scope

Fixing the air shower's station id. Changing any wiring or timing.

## Testing

TDD: break each assertion first, then fix.

| File | Change |
|---|---|
| `test_action_modules.py` | `pioPing` required set gains `stationId`/`channel`; drop the `stations[0] == pio_config.station_id` assertion |
| `test_recipes_config.py` | recipe `channel` compared against the motion rule's channel, not `pio_config.channel` |
| `test_recipe_acceptance.py` | same owner change |
| `test_pio_output_mapping.py`, `test_pio_select_timing.py` | drop `PioConfig(station_id=…, channel=…)` arguments |
| `test_extensions_config.py` | sample HCL updated |
| `test_config.py` | **new**: a leftover `station_id`/`channel` under `extension "pio"` raises `ExtensionsError`, modelled on the existing `pio_port` guard test |

Verification: the seven files above, then the full suite.

## Deployment

Config and code must ship together. After this change a robot whose
`extensions.hcl` still carries the old keys refuses to boot — that is the
intended behaviour, and it means a code-only deploy strands the robot. Update
`extensions.hcl` on each robot in the same pass, and restart.

`192.168.101.61` currently has `station_id = "123456"` and `channel = 250` under
`extension "pio"`; both lines must be removed and the facility blocks filled in
before the adapter is restarted with this code.
