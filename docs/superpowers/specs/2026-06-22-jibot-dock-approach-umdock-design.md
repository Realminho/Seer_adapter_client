# JIBOT dock approach via UmDock (`_BEFORE` waypoint)

Date: 2026-06-22
Status: Approved (design)
Scope: `unified-amr-adaptor` — jibot adapter

## Problem

ACS sends an order whose target is a charger node (e.g. `F2_90_S2CH`). On the
JIBOT map this charger has a companion approach waypoint `F2_90_S2CH_BEFORE`. The
robot must drive to the approach waypoint **via UmGoto**, and the final leg into
the charger must happen **via UmDock** (reflector-aligned seating), not UmGoto.

The current adapter does not do this:

- `_is_charge_node` reads `config.charge.nodes`, which is stale
  (`F2_M01_091_S2CH`) and does not match the live map name `F2_90_S2CH`.
- `_is_jibot_map_dock_node` keys off the map "Dock" category. In the current map
  the categories are inverted relative to the names: `F2_90_S2CH` (the real
  charger) is a `Goal`, while `F2_90_S2CH_BEFORE` (the approach) is the `Dock`
  object. So detection misfires.
- `_wait_until_docking_complete` waits on charging **forever** with no timeout.

Observed failure (live, bot A `192.168.3.222`, 2026-06-22): robot ended at
`(17099,4583)` ≈ `F2_90_S2CH` `(17361,4591)`, `mode=Stop`, `status=Stopped`,
`is_charged=false`, `battery=97%` — i.e. parked at the charger but never seated /
never charging, and the order stuck in `DOCKING` because the completion wait
never returns (`_docking_started_node_ids` never clears).

## Goal

When an order targets a charger node that has a configured approach waypoint:

1. UmGoto to the approach (`_BEFORE`) node and wait for pose arrival.
2. UmDock to seat into the charger.
3. Wait for charging to start, OR fail out after a timeout so a bad seat rejects
   the order step instead of hanging in `DOCKING` forever.

The approach→charger mapping is a JIBOT hardware characteristic and lives in a
new JIBOT-specific config file `jibot-config.toml`.

## Non-goals

- Fixing the stale `config.charge.nodes` (tracked separately).
- Fixing the inverted map Dock/Goal categories or the residual
  `_is_jibot_map_dock_node("F2_90_S2CH_BEFORE")==True` quirk. ACS never sends
  `_BEFORE` as an order node, so it is harmless in practice.
- Instant-action `startCharging` (in-place / non-order charge) — separate path.

## Design

### A. Config: new `adaptor/config/jibot-config.toml`

A new home for JIBOT hardware-quirk mappings. Initial contents:

```toml
# JIBOT 하드웨어 특성 매핑.
# 충전기 진입은 UmGoto가 아니라 approach(_BEFORE) 노드까지 UmGoto 후
# 거기서 UmDock으로 seating. ACS는 충전기 노드만 보냄(approach는 어댑터 내부).
[dock_approach]
nodes = { F2_90_S2CH = "F2_90_S2CH_BEFORE" }   # charger node -> approach node
fail_timeout_sec = 30.0                         # reject if not charging within Ns after UmDock
```

`config.py`:

- New dataclass:
  ```python
  @dataclass
  class DockApproachConfig:
      nodes: Dict[str, str] = field(default_factory=dict)
      fail_timeout_sec: float = 30.0
  ```
- Add `dock_approach: DockApproachConfig` to `Config`.
- Loader (`get_config`): after loading the base TOML, if
  `jibot-config.toml` exists next to `config.py`, deep-merge it into
  `config_dict`, then apply CLI `overrides` last (CLI still wins). Build
  `DockApproachConfig(**config_dict.get("dock_approach", {}))`. File absent =>
  defaults (`nodes={}`), so behaviour is unchanged where the file is not
  deployed (backward compatible).

Merge order: `config.toml` (or per-instance `config_path`) → `jibot-config.toml`
→ CLI overrides.

### B. Adapter flow

New detection helper:

```python
def _dock_approach_node(self, node_id: str) -> Optional[str]:
    """Approach (_BEFORE) waypoint for a charger node, or None.
    From jibot-config.toml [dock_approach].nodes."""
    return (getattr(self.config.dock_approach, "nodes", {}) or {}).get(node_id)
```

`_process_v3_node_step` gains a branch at the top (before `_send_node_motion`):

```python
approach = self._dock_approach_node(node.node_id)
if approach is not None:
    reason = await self._run_approach_then_dock(node, approach)  # sets its own error per phase
    if reason is not None:
        print(f"[ORDER NODE APPROACH-DOCK FAILED] id={node.node_id} reason={reason}")
        return False
    self._clear_jibot_goto_rejected_error()      # clear any stale goto error on success
    return self._finalize_v3_node_step(step, node)
# ...existing flow unchanged...
```

`_finalize_v3_node_step(step, node) -> bool` is extracted from the existing
finalize block (set `_last_node_id` / `_last_node_sequence_id`, update
`state.last_node_*`, finish placeholder action, request publish, return True) so
both the new and existing paths share it.

Orchestrator — the whole sequence isolated in one method. It **owns error
surfacing per phase** (goto-phase failures → FATAL `_set_jibot_goto_rejected_error`;
dock-phase failures → `_set_jibot_dock_fail_error`) and returns a reason string
(for logging) or `None`. Every command send is wrapped so a transport exception
becomes a clean rejected step, not a crashed worker (see review revision R1).

```python
async def _run_approach_then_dock(self, node, approach) -> Optional[str]:
    # 0. validate approach coords BEFORE dispatching any motion (R3).
    #    Dock must not fall back to send-and-complete: missing coords = hard fail.
    coords = self._map_nodes().get(approach)
    if not coords:
        reason = f"approach node '{approach}' missing from map"
        self._set_jibot_dock_fail_error(node, reason)
        return reason

    # 1. drive to approach via UmGoto (by name); wrap the send (R1).
    print(f"[ORDER NODE APPROACH GOTO] id={node.node_id} approach={approach}")
    try:
        await self._vehicle.goto_point(approach)
    except Exception as exc:                       # transport/send failure
        reason = f"approach goto send failed: {exc}"
        self._set_jibot_goto_rejected_error(node, reason)   # FATAL (R4)
        return reason
    reason = await self._await_goto_ack(node)      # explicit error frame only
    if reason is not None:
        self._set_jibot_goto_rejected_error(node, reason)   # FATAL (R4)
        return reason

    # 2. wait pose arrival at the approach waypoint. No hard timeout (obstacle
    #    brake legitimately waits), but KEEP the stopped-and-unreached diagnostic
    #    so a silently-ignored no-ack goto surfaces instead of hanging blind (R2).
    dev = self._effective_reach_deviation_xy(
        float(getattr(self.config.settings, "default_node_deviation_xy", 10.0)))
    await self._wait_until_pose_reached(node, coords[0], coords[1], dev, label=approach)

    # 3. UmDock to seat into the charger; wrap the send (R1).
    print(f"[ORDER NODE APPROACH DOCK] id={node.node_id} command=UmDock")
    try:
        await self._vehicle.um_dock()
    except Exception as exc:
        reason = f"UmDock send failed: {exc}"
        self._set_jibot_dock_fail_error(node, reason)
        return reason
    self._docking_started_node_ids.add(str(node.node_id))
    self._sync_docking_action_state()              # surface DOCKING immediately

    # 4. wait charging or fail-timeout (the only place the new dock-fail error fires)
    if not await self._wait_until_dock_charge_or_timeout(node):
        self._docking_started_node_ids.discard(str(node.node_id))
        timeout = self.config.dock_approach.fail_timeout_sec
        reason = f"dock failed: not charging within {timeout}s after UmDock"
        self._set_jibot_dock_fail_error(node, reason)
        return reason
    return None
```

New helpers:

- `_wait_until_pose_reached(self, node, x, y, deviation, label, poll_sec=0.2) -> None`
  — coordinate-based arrival for an internal waypoint. It does **not** emit the
  order-specific arrival-mismatch warning (that compares the JIBOT cur-task goal
  against a VDA node, irrelevant for an internal approach hop), but it **does
  keep the stopped-and-unreached diagnostic** (R2): when the robot is stopped,
  not in obstacle-wait, and outside the zone for ≥1 s, it calls the existing
  `_set_jibot_node_unreached_error(node, x, y, vx, vy, dev, station)` so a stuck
  approach is visible. Shares the reach-zone geometry with
  `_wait_until_node_position_reached` via an extracted
  `_pose_in_reach_zone(vx, vy, tx, ty, dev)` (respects `settings.reach_zone_shape`).
  No hard timeout, consistent with the existing node waiter (cancelOrder/new
  order cancels the worker).
- `_wait_until_dock_charge_or_timeout(self, node) -> bool`:
  ```python
  deadline = time.monotonic() + self.config.dock_approach.fail_timeout_sec
  while time.monotonic() < deadline:
      if self._is_vehicle_charging():
          return True
      await asyncio.sleep(0.2)
  return False
  ```

### C. Failure handling (in scope)

The orchestrator surfaces the **phase-appropriate** error itself before
returning a reason, and `_process_v3_order_queue` turns the `False` return into a
requeue-and-hold (it does **not** catch exceptions — hence R1's command-send
wrapping). Failure taxonomy:

| Phase | Failure | Error | Level | FM behaviour |
| --- | --- | --- | --- | --- |
| approach coords | node missing from map | `_set_jibot_dock_fail_error` | FATAL | held / re-sent |
| approach goto | send raises, or rejection frame | `_set_jibot_goto_rejected_error` (existing) | FATAL | held until FM re-sends (soft-hold) |
| dock send | `um_dock()` raises | `_set_jibot_dock_fail_error` | FATAL | held / re-sent |
| dock seating | not charging within `fail_timeout_sec` | `_set_jibot_dock_fail_error` | FATAL | held / re-sent (retry dock) |

On the seating timeout the orchestrator discards the node from
`_docking_started_node_ids` (so `DOCKING` clears) — same anti-hang shape as the
recent in-place-charge fix (`d9908b4`). The robot itself times back to `Stop`
after a failed UmDock (per `jibot-charging-dock-bms.md`), so no extra UmStop is
required.

Error surfacing: add `_set_jibot_dock_fail_error(node, reason)` (mirrors the
existing `_set_jibot_dock_work_error` / `_set_jibot_goto_rejected_error`
helpers). **Level = FATAL** so the FM re-sends the order and the approach→dock is
retried (the user asked for reject *and retry*), matching the FM-keeps-order-on-
FATAL contract. The approach-goto failure deliberately reuses the **existing**
FATAL `_set_jibot_goto_rejected_error` (command=`UmGoto`) rather than the new
dock-fail error, keeping goto rejections uniform (R4).

### D. Testing

- **config**: `jibot-config.toml` is deep-merged; `DockApproachConfig.nodes` /
  `fail_timeout_sec` populated; absent file => defaults.
- **detection**: `_dock_approach_node` returns the approach only for mapped
  nodes, `None` otherwise.
- **happy path** (mocked vehicle + map): order node `F2_90_S2CH` →
  `goto_point("F2_90_S2CH_BEFORE")` called, then arrival, then `um_dock()`
  called; charging asserted → step completes, `_last_node_id == "F2_90_S2CH"`.
  Assert call ordering (goto before dock).
- **fail timeout**: charging never asserts → step returns `False`,
  `_docking_started_node_ids` does not contain the node, dock-fail (FATAL) error
  surfaced.
- **command-send exception** (R1): `goto_point`/`um_dock` raise → step returns
  `False`, the matching error is published, the worker does not crash (the queue
  keeps running).
- **missing approach coords** (R3): approach not in `_map_nodes()` → **no**
  `goto_point` call is made, step returns `False`, dock-fail error surfaced.
- **approach goto rejected** (R4): `_await_goto_ack` returns a reason →
  `_set_jibot_goto_rejected_error` (FATAL, command=`UmGoto`) surfaced, **not** the
  dock-fail error.
- **approach unreached** (R2): robot stops short of the approach zone → the
  stopped-and-unreached diagnostic fires (no silent infinite wait).

## Files touched

- `adaptor/config/jibot-config.toml` (new)
- `adaptor/config/config.py` (`DockApproachConfig`, `Config.dock_approach`,
  loader merge)
- `adaptor/adapter_jibot.py` (`_dock_approach_node`,
  `_run_approach_then_dock`, `_wait_until_pose_reached`,
  `_wait_until_dock_charge_or_timeout`, `_pose_in_reach_zone`,
  `_finalize_v3_node_step`, `_set_jibot_dock_fail_error`, new branch in
  `_process_v3_node_step`)
- `adaptor/tests/` (new tests)

## Review revisions

Folded in from design review (2026-06-22):

- **R1** — command sends (`goto_point`, `um_dock`) are wrapped in `try/except`.
  `_process_v3_order_queue` (`adapter_jibot.py:2615`) wraps the step in
  `try/finally` only, so an unhandled send exception would crash the worker
  instead of holding the order. The orchestrator converts each to a
  phase-appropriate error + reason.
- **R2** — `_wait_until_pose_reached` keeps the stopped-and-unreached diagnostic.
  UmGoto is no-ack by design (`adapter_jibot.py:3043`), so a silently dropped
  approach goto would otherwise hang with no signal.
- **R3** — approach coords are validated **before** any motion command is
  dispatched. Dock does not fall back to send-and-complete; missing coords is a
  hard fail (no command sent).
- **R4** — approach goto rejection reuses the existing FATAL
  `_set_jibot_goto_rejected_error` (`adapter_jibot.py:1717`), not a new WARNING,
  keeping goto-rejection handling uniform and command=`UmGoto` accurate.

## Risks / open items

- Approach-node coordinates: `F2_90_S2CH_BEFORE` appears in both the `Dock` and
  `Goal` map objects (~43 mm apart); `_map_nodes` keeps the last-written (Dock,
  `15782,4588`). UmGoto is issued **by name** so the robot resolves its own
  goal; our pose-wait only needs approximate coords for arrival. Acceptable.
- Residual: `_is_jibot_map_dock_node("F2_90_S2CH_BEFORE")` stays `True`. Harmless
  because `_BEFORE` is never an ACS order node. Left as out-of-scope cleanup.
