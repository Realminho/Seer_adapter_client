# JIBOT Arrival Signals and lastNodeId

## Summary

Use the robot pose and map/order node coordinates for arrival and `lastNodeId`.
Do not use JIBOT `station` as the current position or as an arrival signal.

`station` is still useful as raw robot telemetry, but recordings show it can be
set before the robot has reached the station coordinates.

## Capture Evidence

Recording: `logs/jibot/f1_60-arrival-complete.jsonl`

Command:

```bash
python3 scripts/capture-jibot-arrival-signals.py \
  --host 192.168.3.222 \
  --goal F1_60 \
  --duration 80 \
  --interval 1 \
  --record-file logs/jibot/f1_60-arrival-complete.jsonl
```

Observed sequence:

| Phase | `UmGetRobotInfo` | `UmGetCurTask` | `UmGetPath` |
| --- | --- | --- | --- |
| Before move | `mode=Stop`, `status=Stopped`, `station=F1_60`, pose around `(13003,4672)` | `value.cmd=stop` | `num=0` |
| Immediately after `UmGoto F1_60` | `mode=MRosGoto`, `status=nrunto F1_60`, `station=F1_60`, pose still around `(13003,4672)` | `value.cmd=goto`, `value.goal=F1_60` | `num=3`, path end around `(12837,4782)` |
| Moving | `station=""`, `status=nrunto F1_60`, pose moves toward the path end | `value.cmd=goto`, `value.goal=F1_60` | `num=3` |
| Arrived | `mode=Stop`, `status=Stopped`, `station=F1_60`, pose around `(12835,4783)` | `value.cmd=stop` | `num=0` |

The first moving sample proves `station=F1_60` can appear while the robot is
still at the old pose. Therefore `station` is not authoritative for arrival.

## Adapter Policy

- Active order node arrival is pose-based: the current robot `(x, y)` must enter
  the node reach zone.
- JIBOT task/path diagnostics are secondary signals. When pose reaches the node,
  the adapter still completes the VDA5050 node/edge/order step, but it reports a
  warning if non-empty diagnostics clearly point somewhere else.
- Pose-based `lastNodeId` capture follows `last_node_capture_mode`:
  `proximity` always mirrors the nearest map node regardless of distance;
  `settled` captures only while idle, stopped, outside manual control, and
  within `settings.idle_last_node_reach_xy`; `disabled` does not capture from
  pose.
- `settled` also *releases*: once the pose leaves the captured node by more
  than `settings.last_node_release_xy`, `lastNodeId` is cleared (log line
  `[LAST NODE RELEASE]`). Without it a manual jog left the robot metres away
  still publishing the node it started from, because `settled` only ever wrote
  `lastNodeId` and never took it back. The release is skipped while an order is
  active — there `lastNodeId` is order progress and legitimately stays at the
  reached node while the robot drives to the next one — and it is off when the
  value is `0`. Capture (`idle_last_node_reach_xy`) and release
  (`last_node_release_xy`) must not be equal: the gap between them is the
  hysteresis band. The empty-`lastNodeId` seed
  (`use_nearest_node_as_last_node_when_missing`) is capped by the release
  radius for the same reason, whatever `missing_last_node_reach_xy` says.
- `station` is not used to advance `lastNodeId`.
- `UmGetCurTask`, `UmGetTaskInfo`, and `UmGetPath` have no direct VDA5050 state
  fields, so selected values are exposed in `state.information`.
- If the adapter cannot publish a non-empty `lastNodeId`, it reports
  `errorType=LAST_NODE_ID_MISSING` with `errorLevel=CRITICAL`. The error clears
  automatically once pose/order processing restores `lastNodeId`.

## Arrival Warnings

`state.errors[]` may contain `errorType=JIBOT_ARRIVAL_SIGNAL_MISMATCH` with
`errorLevel=WARNING` after a node is pose-reached. This does not block order
progress. It means at least one non-empty JIBOT diagnostic disagreed with the
completed node:

- `UmGetCurTask.data.value.goal` points to another goal.
- `UmGetRobotInfo.status`, `UmGetCurTask.data.status`, or `UmGetTaskInfo.status`
  has an `nrunto <goal>` hint for another goal.
- `UmGetPath.points[-1]` is outside the node reach zone.

Pose gotos are compared as coordinates, not names. A goto sent as a pose (the
`move` segment fallback, `[ORDER NODE GOTO_PATH_POINT_POSE]`) makes JIBOT answer
with `value.target="pose"`, `value.goal="none"` and
`status="nrunto pose (x y th)"`, so the goal *name* can never equal the nodeId.
The adapter reads `value.x/value.y` and the `(x y th)` in the status instead and
warns only when that pose falls outside the node reach zone; the reference value
is then `x,y` rather than a station name. Comparing the names is what made every
fallback-goto arrival report a four-label mismatch.

If the robot stops outside the reach zone, the adapter does not complete the
node. It reports `JIBOT_NODE_UNREACHED` with `command=UmGoto`. For a JIBOT map
`Dock` destination this commonly means the node was incorrectly treated as a
normal goto target; declare it as `mode="dock"` in `motion_rules` so the order
worker selects `UmDock`. `station` remains diagnostic only and an empty value is
not itself the failure cause.

## VDA5050 `information` References

The adapter publishes these under `infoType=JIBOT_STATUS`:

| Reference key | Meaning |
| --- | --- |
| `jibotMode` | Raw `UmGetRobotInfo.mode` / status snapshot mode |
| `jibotStatus` | Raw `UmGetRobotInfo.status` / status snapshot status |
| `jibotStation` | Raw `UmGetRobotInfo.station`; diagnostic only |
| `jibotMotorState` | `running` or `stopped` from `UmGetMotorState.flag` |
| `jibotLocalizationScore` | Raw `UmGetLocState.score` |
| `jibotBattery` | Raw robot battery SOC |
| `jibotCurTaskCommand` | `UmGetCurTask.data.value.cmd` |
| `jibotCurTaskGoal` | `UmGetCurTask.data.value.goal` |
| `jibotCurTaskStatus` | `UmGetCurTask.data.status` |
| `jibotTaskInfoStatus` | `UmGetTaskInfo.status` |
| `jibotPathNum` | `UmGetPath.num` |
| `jibotPathEnd` | Last `UmGetPath.points[]` entry as JSON |

## Diagnostic Script

Use `scripts/capture-jibot-arrival-signals.py` to capture the raw fields around
future robot behaviors. The raw JSONL is more reliable than console status
snapshots when deciding whether a JIBOT field has arrival semantics.
