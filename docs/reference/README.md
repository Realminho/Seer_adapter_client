# Reference

Audience: AI agents, developers, and implementation lookup.

Use this directory for precise technical references that help reason about the codebase and protocol behavior.

Examples:

- VDA5050 field mapping
- MQTT topic reference
- State/order schema notes
- Robot API mapping
- Code architecture notes
- Data conversion rules
- Sensor/camera topic maps (see `jibot-client/docs/jibot-camera-topics.md`)
- Video/camera delivery to FMS design (see `jibot-client/docs/jibot-video-to-fms-design.md`)
- JIBOT onboard SSH access & network (see `jibot-onboard-access.md`)
- JIBOT "JManager reconnect" / robot-won't-move troubleshooting (see `jibot-jmanager-reconnect-troubleshooting.md`)
- JIBOT onboard full hard-lockup ("PC down", SSH-dead) — confirm, rule-out, watchdog/netconsole recovery (see `jibot-onboard-hard-lockup-troubleshooting.md`)
- JIBOT onboard ROS graph & hardware inventory — every node/topic/message and which device (motors, lasers, cameras, MCUs, LEDs) hangs off which port/bus (see `jibot-ros-inventory.md`)
- JIBOT status LED / light control — why it is not adaptor-controllable (see `jibot-led-control.md`)
- JIBOT charging, docking & BMS telemetry — charge relay (cmd:3), UmDock back-up, startup SOC readiness, where battery data lives (see `jibot-charging-dock-bms.md`)
- JIBOT arrival signals and lastNodeId policy — why `station` is diagnostic only, and which raw task/path fields are exposed in VDA5050 `information` (see `jibot-arrival-and-last-node.md`)

Prefer explicit field names, file paths, schemas, examples, and invariants. These documents should be easy for an AI agent to search and use as context.
