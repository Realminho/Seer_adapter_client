# Simulator map store + runtime pose CLI

Date: 2026-06-11

## Goal

Replace the "last position drives the simulator" behaviour as the *primary*
mechanism with a map-driven one: when the real robot reports its map, persist
it, and have the simulator build itself from that saved map. The current pose
becomes a runtime CLI input. The existing pose save/load stays usable as the
fallback source for the simulator's initial position.

## Background (current state)

- `adaptor/jibot_position_store.py` — `save_position_snapshot` / `load_position_snapshot`
  persist `{x, y, theta, mapId, serialNumber}` to `runtime/jibot-position.json`.
- `robot_info_loop` (real robot only) periodically calls `get_map()` and
  `save_position_snapshot(...)`.
- `main.py` on `--simulator` loads the position snapshot and passes it as
  `SimulatedJIBOT(initial_position=...)`.
- `jibot-client` `get_map()` → `_update_map_nodes()` parses `UmGetMap` `Objs`
  into `_map_nodes = {name: (x, y, theta)}`. The raw response is discarded.
- The adapter resolves node coordinates via `_map_nodes()`, which for the
  simulator prefers `_fms_map_nodes` (set from FMS orders), else
  `vehicle._map_nodes`.

## Design

### 1. `adaptor/jibot_map_store.py` (new)

Mirrors `jibot_position_store.py`.

- `save_map_snapshot(path, *, map_id, nodes, raw, serial_number) -> bool`
  - `nodes`: `{name: (x, y, theta)}` (tuples serialised as JSON lists).
  - `raw`: the full `UmGetMap` response dict (or `None`).
  - Returns `False` when `nodes` is empty (nothing worth persisting).
  - Atomic write (`.tmp` + `replace`), same as the position store.
  - Payload:
    ```json
    {
      "serialNumber": "...",
      "mapId": "...",
      "nodes": { "S1": [x, y, th], ... },
      "raw": { "Objs": { ... } },
      "updatedAt": "...Z"
    }
    ```
- `load_map_snapshot(path) -> {"map_id", "nodes": {name: (x, y, theta)}, "raw"} | None`
  - Returns `None` on missing file / parse error / empty nodes.
  - Converts node lists back to `(x, y, theta)` tuples.

### 2. Persist the raw map in the client

`jibot-client` `_update_map_nodes(response)` additionally stores
`self._map_raw = response` (initialised to `None` / `{}` in `__init__`) so the
adapter can persist the raw map alongside parsed nodes.

### 3. Save the map in `robot_info_loop` (real robot only)

After the existing `get_map()` call returns, persist the map:

```python
nodes = await get_map()
if map_store_path is not None and nodes:
    save_map_snapshot(
        map_store_path,
        map_id=position_map_id,
        nodes=nodes,
        raw=getattr(vehicle, "_map_raw", None),
        serial_number=position_serial_number,
    )
```

Position snapshot saving is unchanged. The map is saved on the same refresh
cadence the map fetch already uses.

### 4. Simulator builds from the saved map

`SimulatedJIBOT.__init__` gains `initial_map: Optional[Dict] = None`:

- `self._map_nodes = {name: tuple(v) for name, v in initial_map["nodes"].items()}`
- `self._map_raw = initial_map.get("raw")`
- A `get_map()` override returns `self._map_nodes` (no socket round-trip).

The adapter's `_map_nodes()` then resolves real node positions from
`vehicle._map_nodes` whenever FMS has not supplied `_fms_map_nodes`. FMS-order
nodes continue to override at runtime.

### 5. Runtime pose CLI

Add `--x`, `--y`, `--theta` (all `type=float`) to `main.py`.

Initial-pose resolution for the simulator:

1. Base = `load_position_snapshot(...)` if present, else `{x:0, y:0, theta:0}`.
2. Override each axis individually with whichever of `--x/--y/--theta` was given.

So partial flags work, and with no flags the saved-position fallback is used —
the existing pose feature stays usable.

### 6. Paths

- `MAP_STORE_PATH = ADAPTER_ROOT / "runtime" / "jibot-map.json"`
- `POSITION_STORE_PATH` unchanged.

### 7. Manual

Update `adaptor/readme.md` (How to RUN, Korean style):

- The real robot auto-saves its map to `runtime/jibot-map.json`; `--simulator`
  loads it so the simulator runs on the real map layout.
- `--x/--y/--theta` set the simulator's start pose at runtime; without them the
  last saved position (or origin) is used.

## Testing

- `jibot_map_store`: round-trip save/load; empty-nodes → `False` / `None`;
  missing file → `None`; tuple reconstruction.
- `SimulatedJIBOT(initial_map=...)` populates `_map_nodes` and `get_map()`
  returns them.
- `main.py` pose resolution: CLI overrides per-axis over the snapshot base
  (unit-test the resolver helper).

## Out of scope

- Changing how FMS-order nodes feed the adapter.
- Map editing / multi-map management.
