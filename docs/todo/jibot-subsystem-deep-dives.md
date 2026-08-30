# JIBOT subsystem deep-dive backlog

Scoped-but-not-executed deep dives into individual JIBOT onboard subsystems. These
came out of the 2026-06-23 whole-system inventory
([`docs/reference/jibot-ros-inventory.md`](../reference/jibot-ros-inventory.md)),
which mapped **what exists**; these items investigate **how each subsystem actually
works** (data flow, frame alignment, parameters, acceptance tolerances) and turn
the findings into reference docs.

All are **read-only SSH** on bot A (`ucore@192.168.3.222`); source both
`/opt/ros/noetic/setup.bash` and `/usr/local/urobot/jarvis/setup.bash` first (see
the inventory doc's "How to reproduce"). None of these require commanding the
robot — but D3's live observation is only meaningful while the robot is actually
docking.

> Inventory-level gaps (binary-only message defs, motor-MCU tty confirmation) are
> tracked separately in the inventory doc's **"Open items (TODO)"** section, not
> here.

Priority is by **adaptor payoff**, not curiosity: D3 and D2 connect to features
already in the tree and to the open
[blocking questions](next-steps.md#blocking-questions-answer-first) (pose units,
localizationScore scale, operatingMode); D1 is mostly diagnostic understanding.

---

## D3 — AprilTag / line docking pipeline (priority: high)

**Goal.** Document how the multi-modal dock alignment (AprilTag / magnetic strip /
track / triangle / reflector) is sequenced and how the tag-based alignment reaches
its acceptance tolerances — so dock failures can be diagnosed by sensor/fallback
rather than guessed.

**Why it matters (adaptor).** Directly supports the shipped dock-approach feature
(`UmDock` approach, `JIBOT_DOCK_FAILED`, reflector back-up, charge flow). Gives a
"which sensor/stage failed" diagnostic instead of an opaque timeout. Related:
[`docs/reference/jibot-charging-dock-bms.md`](../reference/jibot-charging-dock-bms.md),
[[jibot-dock-charge-mechanics]], [[jibot-umgoto-fire-and-forget-no-ack]].

**Where.**
- Topics: `/pose_in_tag` (PoseStamped, robot pose rel. tag = alignment error),
  `/pose_in_tag_state` (String), `/tracked_pose`, `/tag_detections_ukf`,
  `/bundle_detection_image`; service `/check_id_service`.
- Config: `routes/task.json` blocks `tag` (`ref_dist:3000`, `ref_accept_dist:15`,
  `ref_accept_offset:20`, `ref_accept_theta:2`, `ref_count:6`,
  `ref_edge_left/right:±550`, `online_distance_to_stop:3600`,
  `camName:"/front/scan"`), `strip1` (`/dev/inner3`), `track` (`/dev/ttyUSB4`),
  `triangle`. Node `apriltag_localization_nodelet_manager`.
  Note: on `192.168.101.61` (2026-07-29) `/dev/ttyUSB4` is the PIO converter and
  no process holds it, so the `track` block there is stale config, not a live
  sensor — see `reference/jibot-ros-inventory.md` "Serial / USB device map".

**Approach.**
1. Static: fully decode the `tag`/`strip1`/`track`/`triangle` blocks in
   `task.json` (+ `task0409.json`, `task_ali_dock_0610.json`) — what each
   parameter means, which sensor each maneuver uses.
2. Binary: trace the dock state machine in `jarvis-g` / `urobot`
   (`JModeCharge`, `JActRefBack`, reflector vs tag vs strip fallback order) via
   `strings`/`objdump -C` (read-only, as in the LED/charging docs).
3. Live (only during a real dock): `rostopic echo /pose_in_tag`,
   `/pose_in_tag_state` to watch the alignment error converge toward the accept
   tolerances; note which method is active in each phase.

**Done when.** A reference doc has: a dock sequence diagram (phase → active sensor
→ fallback), a parameter table for the alignment tolerances, and the
failure→cause mapping. Feeds blocking-question Q7 (order action types) if docking
shows up as an order action.

---

## D2 — EKF odometry & localization chain (priority: medium-high)

**Goal.** Document how wheel odometry (`/jodom`) + IMU
(`/ucore/imu/data_raw`, ~100 Hz) fuse in `ekf_se_local` into
`/odometry/filtered`, and how that flows into localization (AMCL /
`neo_localization`) and the pose the adaptor reports to FMS.

**Why it matters (adaptor).** Validates `AgvPosition` accuracy and the shipped
"localization-lost / DOCKING" state surfacing (commit `9df01d6`). Directly answers
[blocking questions](next-steps.md#blocking-questions-answer-first) **Q1 (pose
units)** — `robot_jarvis.json` works in mm; `/jrobot_status.odom_theta` observed in
rad (~-9.9e-05) — and **Q2 (localizationScore scale/`493.0`)**. Helps debug pose
jumps / false localization-lost.

**Where.**
- Nodes: `ekf_se_local` (robot_localization), `odom_filter`, `tf2odom`,
  `neo_localization_nodelet`, `localization_nodelet_manager`,
  `side_localization_node`.
- Topics: `/jodom`→`/jarvis/odom`, `/odometry/filtered`, `/amcl_pose`,
  `/map_pose`, `/particlecloud`, `/jloc_result(_raw)`, `/robot_pose(_filter)`.

**Approach.**
1. `rosparam dump` the `ekf_se_local` config — which `odom0`/`imu0` axes are
   fused/trusted, covariances, `world_frame`/`odom_frame`/`base_link_frame`.
2. Compare `/jodom` vs `/odometry/filtered` live (drift, theta convention/units).
3. Identify the authoritative pose source the adaptor should read and what
   `localization_score` (`493.0`) actually represents (range/meaning).

**Done when.** A reference doc / inventory section has the odom→EKF→localization
data-flow, the EKF trust/covariance settings, confirmed pose units + theta
convention, and a recommendation for Q1/Q2. Always available (data flows at idle).

---

## D1 — Lidar TF / mount alignment (priority: low)

**Goal.** Verify the 6 scans (front/back/left/right/down-slope/top-depth) are
mounted where the device JSONs claim and fuse correctly into one obstacle picture.

**Why it matters (adaptor).** Low direct payoff — the adaptor doesn't process raw
lidar. Worth doing only if/when footprint or safety-field reporting to FMS is
planned (P3-3 `distanceSinceLastNode`/footprint-adjacent). Otherwise diagnostic
only.

**Where.**
- Config: `device/laser_{jarvis,back,left,right,top,down_slopg}.json`
  (`pos_x`/`pos_y`/`pos_th`, ranges, `angle_ignore`).
- Topics/TF: `/front,back,left,right/scan(_raw,_filtered)`,
  `/tf`, `/tf_static`; nodes `*_laser`, `*_scan_filter`, `bot300/robot_state_publisher`.

**Approach.**
1. `rosrun tf tf_echo base_link <laser_frame>` per lidar → compare to the JSON
   `pos_*`. Dump the tree (`rosrun tf view_frames`).
2. Confirm left/right "edge virtualize" and down-slope `angle_ignore`
   (±100–135° mask) take effect in the published scans.

**Done when.** A sensor→frame→fusion alignment table + TF tree, appended to the
inventory doc. Defer unless a footprint/safety-field need appears.

---

## Suggested order

1. **D3** (static + binary now; live during the next real dock) — highest adaptor
   payoff, ties to dock/charge work.
2. **D2** — answers Q1/Q2 blocking questions; data always available.
3. **D1** — only when footprint/safety-field reporting is on the roadmap.
