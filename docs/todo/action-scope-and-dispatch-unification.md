# Action scope declaration and dispatch unification

**Status:** not started. Investigated 2026-08-14, re-verified against `develop`
2026-08-22; implementation deferred.

Give every action one declaration of *which scopes it is valid in*
(INSTANT / NODE / EDGE), and make the factsheet, the WebUi catalog and the
order path all read that one declaration — instead of today's three separate
lists that already disagree.

---

## Why this exists

An order failed at charger node `1_01CH` with:

```
actionStates: [{ "actionId": "20260813-388-1-1:1",
                 "actionStatus": "FAILED",
                 "actionResult": "Unsupported order action type: stopCharging" }]
errors:       [{ "errorType": "ACTION_NOT_FOUND",     "errorLevel": "WARNING" },
               { "errorType": "ORDER_ACTION_FAILED",  "errorLevel": "FATAL"   }]
```

`ORDER_ACTION_FAILED` is FATAL (`_set_order_action_failed_error`), so the whole
order stops. `stopCharging` is not a one-off gap — it is one of 33.

### Reproduction

`_dispatch_order_action()` invoked directly, `owner_id="1_01CH"`:

```
order action startCharging  -> FINISHED: UmDock sent; charging follows robot status
order action stopCharging   -> FAILED:   Unsupported order action type: stopCharging
```

## Where the code is

The two intake paths do not share a dispatch table.

| Path | Entry point | Shape |
|---|---|---|
| instant | `instant_actions_accept_procedure` `adaptor/adapter_jibot.py:5798` | 39-branch `if/elif` on `action_type`, registry lookup last |
| order | `_dispatch_order_action` `adaptor/adapter_jibot.py:4371` | 5 checks, then "Unsupported" fallthrough at `:4408` |

The order path re-implements two of the 39 branches by hand — `switchMap`
(`:4387`) and `startCharging` (`:4391`, no JIBOT command name, so it maps to
`UmDock` via `_dock_for_order_action`). Everything else survives only if it
happens to be caught by `is_hardware_action()`, `_action_registry.has()` or
`_is_jibot_command_instant_action()`.

`stopCharging` is implemented for instant only:
`instant_actions_accept_procedure:5909` -> `_handle_stop_charging_instant_action:7183`
-> `_run_stop_charging:7224` (UmStop + relay release + `charging=false` verify).

## Three lists, already disagreeing

The single fact "which actions does this AGV support" is written down in three
places. Counts verified by script against `develop` on 2026-08-22.

| List | Location | Size | Role |
|---|---|---|---|
| `INSTANT_ACTION_TYPES` + `ACTION_SCOPES` | `adaptor/core/factsheet.py` | 50 | what the factsheet **advertises** to the FMS |
| `InstantAction(...)` catalog | `adaptor/core/registry.py` | 23 | what the **WebUi** renders as buttons |
| `elif` chain + `_dispatch_order_action` | `adaptor/adapter_jibot.py` | 39 + 2 | what actually **runs** |

Observed disagreements:

- **7 in the WebUi catalog are absent from the factsheet** — `stopCharging`,
  `jibotMotionRule`, `getCameraInfo`, `standUp`, `standDown`, `stop`,
  `walkMode`. The `core/registry.py:215` comment states this is deliberate for
  `stopCharging` ("the adapter handles it, so the WebUi may send it"), but the
  FMS cannot distinguish "deliberately WebUi-only" from "unsupported".
- **17 are advertised INSTANT-only yet work as order actions** — `startCharging`,
  `clamp`, `unclamp`, `clampOn`, `clampOff`, `clampStop`, `clampTeach`,
  `pioInit`, `pioReadIn`, `pioWriteOut`, `pioScenario`, `pioDisconnect`,
  `ezioReadIn`, `photoSensorRead`, `manualMove`, `manualStop`,
  `gotoNearestNode`. The published contract is narrower than reality.
- **Only 2 declare a NODE/EDGE scope** — `ACTION_SCOPES` in
  `adaptor/core/factsheet.py:66` holds `switchMap: [INSTANT, NODE]` and
  `jibotCommand: [INSTANT, NODE, EDGE]`; every other entry defaults to
  `["INSTANT"]`.

Consequence: an FMS obeying the factsheet may place only `jibotCommand` and
`switchMap` in an order, which would break the clamp/pio order actions that
production depends on. The factsheet is not currently usable as a contract.

## Of the 39 instant branches, 33 fail as order actions

Handled on the order path (6): `switchMap`, `startCharging` (hardcoded);
`manualStop`, `manualMove`, `jibotMotionRule`, `gotoNearestNode` (registered).

Fail with "Unsupported order action type" (33):

```
cancelOrder, chargeInPlace, clearErrors, clearInstantActions, clearZoneActions,
disableMotor, enableMotor, factsheetRequest, getMap, getParameters,
initPosition, loading, localize, logReport, manualDrive, requestLaser,
requestVideo, setMap, setMapSnapshot, setParameters, setSoundVolume,
startPause, stateRequest, stopCharging, stopLaser, stopLoading, stopPause,
stopSound, stopUnloading, syncJibotParams, testSound, unloading, uploadSound
```

Several of these (`cancelOrder`, `stateRequest`, `factsheetRequest`,
`clearErrors`, `clearInstantActions`, `clearZoneActions`, `startPause`,
`stopPause`) are protocol control flow and **must** stay instant-only. The
failure is correct for them; what is missing is a declaration saying so, and an
error that says "instant-only" rather than "unsupported".

## Why this surfaced now

Before commit `34ef96c`, the start node of a new order was dropped by the
traversed-prefix prune and its actions were silently discarded. That commit
added `_is_start_node_with_pending_actions()` (`adaptor/adapter_jibot.py:3719`)
plus the `actions_only` step flag, so a departure order's start-node actions now
run. The `1_01CH` order carried `[startCharging, stopCharging, unclamp]` on its
start node; only now do they execute, and the middle one fails.

The action ids in that order (`<orderId>:<index>`) are the FMS's own scheme —
the adapter never appends to `node.actions`, and its own synthesized ids use
`{source}-{type}-{headerId}` (`submit_local_instant_action`) or
`__jibot_docking__`. The FMS sent all three.

## Proposed order of work

Declaration first, migration last. Steps 1-2 do not touch handlers, so the only
behavior change is the added scope check.

1. **One declaration.** Add `scopes` (INSTANT / NODE / EDGE) to `ActionSpec`
   (`adaptor/core/action_registry.py:71`). Register the 39 hardcoded types
   there too, keeping their existing handlers in place — registration only.
2. **Three consumers read it.** Drive the factsheet `agvActions`/`actionScopes`,
   the WebUi catalog, and a scope check in `_dispatch_order_action` from that
   one table. The three-way disagreement disappears and the FMS can trust the
   factsheet. `stopCharging` is resolved here — either it gains NODE scope, or
   it is rejected as instant-only with an accurate message.
3. **Migrate handlers into registry runners**, one at a time. Not urgent.
   `manualStop` and `cancelOrder` are checked *outside* the busy gate on purpose
   (`adapter_jibot.py:5851`, so an operator can always stop) — move them last or
   leave them.
4. **Split the vendor-neutral core out of the adapter.** See below.

## Related: what this means for a SEER adapter

Measured on `develop`, 2026-08-22:

| | |
|---|---|
| `adaptor/adapter_jibot.py` | 8,504 lines |
| `adaptor/adapter_hexplorer.py` | 279 lines, written standalone |
| `_vehicle.*` call sites in `adapter_jibot.py` | 21 distinct methods, 44 sites |
| `adapter._vehicle` references in `adaptor/extensions/` | **0** |

The extensions are already vendor-neutral: `clamp`, `pio` and `ezio` talk to
chassis peripherals they own (`adapter._ezi_io`, `adapter._ezi_motor`,
`adapter._pio_client`) and never to the AMR vendor's motion controller. Moving
more JIBOT behavior into extensions is not the lever.

The obstacle is the opposite: the vendor-neutral logic — VDA5050 state, the
order queue, action dispatch, arrival verification, error surfacing — lives
inline in `adapter_jibot.py` and cannot be reused. `adapter_hexplorer.py`
re-implemented the VDA5050 boundary in 279 lines rather than share it. A SEER
adapter starting today would do the same.

Actual JIBOT-specific surface is small (21 methods: goto, stop, dock, localize,
map, pose). Extracting the neutral core and defining a per-vehicle driver
interface is the work that makes a third adapter cheap — and action dispatch is
one of the largest neutral pieces, which is why step 4 follows steps 1-3 rather
than being a separate project.

## Related documents

- [action-error-level-gating.md](action-error-level-gating.md) — the other
  missing "may this action run right now?" declaration (error level, not scope).
- [vda5050-compliance-gaps.md](vda5050-compliance-gaps.md) — protocol behavior
  gaps; the order-action execution items there are the origin of this path.
