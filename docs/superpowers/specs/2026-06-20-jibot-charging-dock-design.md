# JIBOT charging and dock-position design

## Goal

JIBOT must not use configured route files for charger entry. When FMS sends a
charging destination, the adaptor treats that destination as the FMS-visible
charge position, but sends JIBOT `UmDock` so the robot uses its onboard dock
node and charger approach behavior.

The same dock approach is also needed for non-charging dock work, where the
robot must remain at the docked conveyor/cobot transfer position. In that case
the adaptor lets JIBOT dock, waits until charging starts, then stops charging so
the vehicle remains physically positioned for transfer work.

## Terms

- `c`: FMS-visible destination position. For charge orders this is the actual
  charger position. For dock-work orders this is the physical transfer position.
- `b`: JIBOT map node whose type is `dock`. JIBOT uses this internally during
  `UmDock`; the adaptor does not navigate to it with `goto`.
- `a`: the previous route position before docking starts.
- `UmDock`: the only robot command used for final docking from `a` through
  JIBOT's internal dock node `b` to the physical position `c`.

## Current Problem

The adaptor currently supports charge nodes through `[charge].nodes` and
`[charge].routes`, then calls the configured route instead of a normal goto.
That makes charging depend on JIBOT route definitions such as `routes.json`.
Route-file management duplicates FMS map intent and creates another artifact
that can drift when node names or site geometry change.

The actual JIBOT behavior is different: once docking is requested with
`UmDock`, the robot can move about one meter through its onboard dock behavior
and find the charger or docked transfer position. Therefore the adaptor should
not call `goto_point(c)` or any route command for these destinations.

## Design

Replace charge-route execution with dock-profile execution.

Configuration will describe FMS-visible dock destinations and what should happen
after JIBOT reports charging:

```toml
[charge]
nodes = ["F1_M01_041_S1CH", "F2_M01_091_S2CH"]

[dock]
nodes = ["F1_M01_030_S1CV", "F2_M01_080_S2CV"]
stop_charging_on_arrival = true
```

`charge.nodes` are FMS destinations where charging should continue. `dock.nodes`
are FMS destinations where the same JIBOT `UmDock` approach is needed, but the
vehicle should not remain charging. The existing `charge.routes` setting becomes
legacy and is no longer used by the order worker.

When processing a node step:

1. If the node is a normal node, send the existing normal motion command.
2. If the node is in `charge.nodes`, send `UmDock` instead of `goto` or route.
3. If the node is in `dock.nodes`, send `UmDock` instead of `goto` or route.
4. Wait for a docking-complete signal based on robot state, preferably charging
   status or battery charging flag. Do not wait for `station == nodeId`, because
   the JIBOT dock process may not report the FMS-visible destination as its
   station.
5. For charge nodes, keep charging and mark the FMS node reached.
6. For dock-work nodes, after charging is detected, issue the available JIBOT
   stop-charging command if one exists. If the JIBOT API still has no supported
   stop-charging command, report the node action as failed with a clear
   diagnostic rather than silently pretending charging was stopped.

`startCharging` order actions remain supported for compatibility, but they must
not send a second `UmDock` when the node motion already initiated docking. In
that case the action can finish with a result such as `Docking already started
by node motion`.

## State Reporting

The adaptor keeps the FMS order model stable:

- `lastNodeId` is set to the original FMS destination `c` after the docking
  completion condition is satisfied.
- The completed node is removed from `nodeStates` as today.
- `powerSupply.charging` continues to follow robot status for charge nodes.
- Dock-work nodes should end with the robot physically docked and charging
  stopped, or with a failed action/state diagnostic if stop-charging is
  unsupported.

## Error Handling

- If `UmDock` is rejected or raises an exception, fail the node/action and
  publish a VDA5050 error containing the command and destination node.
- If docking never reaches charging state, continue waiting like normal motion
  waits today. Cancellation or a new order interrupts the worker.
- If a dock-work node requires charging to be stopped but the robot API cannot
  stop charging, fail explicitly and leave enough diagnostic text for FMS and
  operators to understand why the robot is still charging.

## Testing

Add unit coverage in the JIBOT v3 order tests:

- Charge node sends `UmDock`, not `goto_point` and not `call_routes`.
- Charge node completes when robot charging state becomes true, then reports the
  original FMS node as reached.
- Dock-work node sends `UmDock`, waits for charging, then calls stop charging.
- Dock-work node fails clearly if stop charging is required but unsupported.
- `startCharging` action on a node whose motion already sent `UmDock` does not
  send a duplicate `UmDock`.

Simulator charging tests should continue to prove that `UmDock` starts charging
and the battery level increases for charge nodes.
