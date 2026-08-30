# Hexplorer VDA5050 Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Hexplorer adapter that runs on the robot onboard Ubuntu 22.04/ROS2 Humble system, talks to Hexplorer through ROS2 topics, and exposes the robot to ACS through the repository's existing VDA5050 MQTT interface.

**Architecture:** Keep ACS-facing communication as VDA5050 MQTT and isolate Dobot/Hexplorer-specific ROS2 details inside a new Hexplorer client and adapter. The initial adapter publishes VDA5050 connection/state and receives VDA5050 instantActions for mode, velocity, map sync, and camera metadata; VDA5050 order execution is intentionally deferred until Hexplorer waypoint/navigation semantics are confirmed from the onboard ROS2 interfaces.

**Tech Stack:** Python 3.10, ROS2 Humble `rclpy`, `geometry_msgs`, `sensor_msgs`, Dobot `custom_msg`, existing `adaptor/protocol/vda5050_3_0`, existing `adaptor/utils/cls_mqtt.py`, `pytest` where available.

---

## File Structure

- Create `hexplorer-client/README.md`
  - Documents onboard runtime requirements, ROS2 setup commands, supported topics, and smoke-test commands.
- Create `hexplorer-client/pyproject.toml`
  - Defines a small local Python package named `hexplorer-client`.
- Create `hexplorer-client/src/hexplorer_client/__init__.py`
  - Exports the public client API.
- Create `hexplorer-client/src/hexplorer_client/client.py`
  - Owns ROS2 node lifecycle, publishers, subscribers, cached robot state, velocity commands, robot mode commands, and camera info capture.
- Create `hexplorer-client/src/hexplorer_client/map_files.py`
  - Copies Hexplorer mapping record/result directories into adapter-local storage with timestamped backups.
- Create `hexplorer-client/tests/test_map_files.py`
  - Verifies map sync backup/copy behavior without ROS2.
- Create `adaptor/adapter_hexplorer.py`
  - Translates VDA5050 instantActions into Hexplorer client calls and publishes VDA5050 v3 state/connection messages.
- Create `adaptor/main_hexplorer.py`
  - Runtime entrypoint for onboard Hexplorer execution.
- Modify `adaptor/config/config.py`
  - Add optional Hexplorer config dataclass with ROS topic names and map/camera paths.
- Modify `adaptor/config/config.toml`
  - Add a `[hexplorer]` section with default topic names and file sync paths.
- Modify `README.md`
  - Add Hexplorer adapter entry and the recommended VDA5050-over-ROS2 architecture note.

## Task 0: Confirm Onboard ROS2 Interfaces

**Files:**
- Create: `docs/reference/hexplorer-ros2-interface.md`

- [ ] **Step 1: Capture custom message definitions on Hexplorer**

Run on the Hexplorer onboard PC:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
ros2 interface show custom_msg/msg/RobotState
ros2 interface show custom_msg/msg/RobotCommand
ros2 topic info /robot_state -v
ros2 topic info /robot_cmd -v
ros2 topic info /vel_cmd -v
```

Expected:

- `RobotState` output confirms the exact field names for body position, body orientation, operating mode/status, battery if present, and localization if present.
- `RobotCommand` output confirms whether the command field is exactly `target_state` and whether the mode values match the user guide.
- `/vel_cmd` is `geometry_msgs/msg/Twist`.

- [ ] **Step 2: Capture one live RobotState sample**

Run on the Hexplorer onboard PC:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
ros2 topic echo /robot_state --once
```

Expected: one complete `RobotState` message is printed.

- [ ] **Step 3: Write the interface reference**

Create `docs/reference/hexplorer-ros2-interface.md`:

````markdown
# Hexplorer ROS2 Interface Reference

Captured on Hexplorer onboard Ubuntu 22.04 / ROS2 Humble.

## Topic Summary

- `/robot_cmd`: `custom_msg/msg/RobotCommand`
- `/robot_state`: `custom_msg/msg/RobotState`
- `/vel_cmd`: `geometry_msgs/msg/Twist`
- `/realsense_camera_node/.../camera_info`: `sensor_msgs/msg/CameraInfo`

## RobotCommand

Paste the exact output of:

```bash
ros2 interface show custom_msg/msg/RobotCommand
```

## RobotState

Paste the exact output of:

```bash
ros2 interface show custom_msg/msg/RobotState
```

## Confirmed Field Mapping

- Position source field:
- Orientation source field:
- Operating mode/status source field:
- Battery source field:
- Localization source field:

## Confirmed Mode Commands

- Stand down command:
- Stand up command:
- Walk mode command:

## Live RobotState Sample

Paste one sanitized output of:

```bash
ros2 topic echo /robot_state --once
```
````

- [ ] **Step 4: Commit**

```bash
git add docs/reference/hexplorer-ros2-interface.md
git commit -m "docs: record hexplorer ros2 interface"
```

## Task 1: Package Skeleton and Map Sync Utility

**Files:**
- Create: `hexplorer-client/pyproject.toml`
- Create: `hexplorer-client/src/hexplorer_client/__init__.py`
- Create: `hexplorer-client/src/hexplorer_client/map_files.py`
- Create: `hexplorer-client/tests/test_map_files.py`

- [ ] **Step 1: Write failing tests for map sync**

Create `hexplorer-client/tests/test_map_files.py`:

```python
from pathlib import Path

from hexplorer_client.map_files import sync_hexplorer_maps


def test_sync_hexplorer_maps_copies_selected_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    (source / "record_001").mkdir(parents=True)
    (source / "record_001" / "metadata.yaml").write_text("bag: one\n", encoding="utf-8")

    result = sync_hexplorer_maps(
        source_dir=source,
        target_dir=target,
        backup_root=backup,
        include_names=("record_001",),
    )

    assert (target / "record_001" / "metadata.yaml").read_text(encoding="utf-8") == "bag: one\n"
    assert result.copied == [target / "record_001"]
    assert result.backup_dir is None


def test_sync_hexplorer_maps_backs_up_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    (source / "record_002").mkdir(parents=True)
    (source / "record_002" / "metadata.yaml").write_text("bag: new\n", encoding="utf-8")
    (target / "record_002").mkdir(parents=True)
    (target / "record_002" / "metadata.yaml").write_text("bag: old\n", encoding="utf-8")

    result = sync_hexplorer_maps(
        source_dir=source,
        target_dir=target,
        backup_root=backup,
        include_names=("record_002",),
    )

    assert (target / "record_002" / "metadata.yaml").read_text(encoding="utf-8") == "bag: new\n"
    assert result.backup_dir is not None
    assert (result.backup_dir / "record_002" / "metadata.yaml").read_text(encoding="utf-8") == "bag: old\n"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
PYTHONPATH=hexplorer-client/src pytest hexplorer-client/tests/test_map_files.py -v
```

Expected: FAIL because `hexplorer_client.map_files` does not exist.

- [ ] **Step 3: Add package metadata**

Create `hexplorer-client/pyproject.toml`:

```toml
[project]
name = "hexplorer-client"
version = "0.1.0"
description = "ROS2 client helpers for Dobot Hexplorer"
requires-python = ">=3.10"
dependencies = []

[tool.pytest.ini_options]
pythonpath = ["src"]
```

- [ ] **Step 4: Implement map sync utility**

Create `hexplorer-client/src/hexplorer_client/map_files.py`:

```python
from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class HexplorerMapSyncResult:
    source_dir: Path
    target_dir: Path
    backup_dir: Path | None
    copied: list[Path]


def sync_hexplorer_maps(
    *,
    source_dir: Path,
    target_dir: Path,
    backup_root: Path,
    include_names: Iterable[str],
) -> HexplorerMapSyncResult:
    source_dir = source_dir.expanduser()
    target_dir = target_dir.expanduser()
    backup_root = backup_root.expanduser()
    names = tuple(include_names)
    if not names:
        raise ValueError("include_names must contain at least one map or record directory name")
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Hexplorer map source directory does not exist: {source_dir}")

    existing = [target_dir / name for name in names if (target_dir / name).exists()]
    backup_dir = None
    if existing:
        backup_dir = backup_root / datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir.mkdir(parents=True, exist_ok=False)
        for path in existing:
            shutil.copytree(path, backup_dir / path.name)

    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in names:
        source_path = source_dir / name
        if not source_path.exists():
            raise FileNotFoundError(f"Hexplorer map item does not exist: {source_path}")
        target_path = target_dir / name
        if target_path.exists():
            shutil.rmtree(target_path)
        shutil.copytree(source_path, target_path)
        copied.append(target_path)

    return HexplorerMapSyncResult(
        source_dir=source_dir,
        target_dir=target_dir,
        backup_dir=backup_dir,
        copied=copied,
    )
```

Create `hexplorer-client/src/hexplorer_client/__init__.py`:

```python
from .map_files import HexplorerMapSyncResult, sync_hexplorer_maps

__all__ = ["HexplorerMapSyncResult", "sync_hexplorer_maps"]
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
PYTHONPATH=hexplorer-client/src pytest hexplorer-client/tests/test_map_files.py -v
```

Expected: 2 passed.

Commit:

```bash
git add hexplorer-client
git commit -m "feat: add hexplorer map sync utility"
```

## Task 2: ROS2 Hexplorer Client

**Files:**
- Create: `hexplorer-client/src/hexplorer_client/client.py`
- Create: `hexplorer-client/README.md`

- [ ] **Step 1: Implement a ROS2 client wrapper**

Create `hexplorer-client/src/hexplorer_client/client.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HexplorerRobotSnapshot:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    theta: float = 0.0
    mode: int | None = None
    localized: bool = True
    raw_state: Any = None
    camera_info: dict[str, Any] | None = None


@dataclass(frozen=True)
class HexplorerTopics:
    robot_command: str = "/robot_cmd"
    robot_state: str = "/robot_state"
    velocity_command: str = "/vel_cmd"
    camera_info: str = "/realsense_camera_node/sn408122070053/camera_info"


@dataclass(frozen=True)
class HexplorerFieldMapping:
    position_field: str = "pos_body"
    orientation_field: str = "ori_body"
    mode_field: str = "temp"
    mode_index: int = 10
    command_target_state_field: str = "target_state"


class HexplorerClient:
    def __init__(
        self,
        topics: HexplorerTopics | None = None,
        field_mapping: HexplorerFieldMapping | None = None,
    ) -> None:
        self.topics = topics or HexplorerTopics()
        self.field_mapping = field_mapping or HexplorerFieldMapping()
        self.snapshot = HexplorerRobotSnapshot()
        self._node = None
        self._velocity_publisher = None
        self._robot_command_publisher = None

    def start(self) -> None:
        import rclpy
        from custom_msg.msg import RobotCommand
        from geometry_msgs.msg import Twist
        from sensor_msgs.msg import CameraInfo
        from custom_msg.msg import RobotState

        if not rclpy.ok():
            rclpy.init()

        self._node = rclpy.create_node("hexplorer_vda5050_client")
        self._velocity_publisher = self._node.create_publisher(Twist, self.topics.velocity_command, 1)
        self._robot_command_publisher = self._node.create_publisher(RobotCommand, self.topics.robot_command, 1)
        self._node.create_subscription(RobotState, self.topics.robot_state, self._on_robot_state, 1)
        self._node.create_subscription(CameraInfo, self.topics.camera_info, self._on_camera_info, 10)

    def spin_once(self, timeout_sec: float = 0.1) -> None:
        import rclpy

        if self._node is not None:
            rclpy.spin_once(self._node, timeout_sec=timeout_sec)

    def stop(self) -> None:
        import rclpy

        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        if rclpy.ok():
            rclpy.shutdown()

    def set_robot_state(self, target_state: int) -> None:
        from custom_msg.msg import RobotCommand

        if self._robot_command_publisher is None:
            raise RuntimeError("HexplorerClient.start() must be called before set_robot_state()")
        command = RobotCommand()
        setattr(command, self.field_mapping.command_target_state_field, int(target_state))
        self._robot_command_publisher.publish(command)

    def publish_velocity(self, x: float, y: float, yaw: float) -> None:
        from geometry_msgs.msg import Twist

        if self._velocity_publisher is None:
            raise RuntimeError("HexplorerClient.start() must be called before publish_velocity()")
        twist = Twist()
        twist.linear.x = float(x)
        twist.linear.y = float(y)
        twist.angular.z = float(yaw)
        self._velocity_publisher.publish(twist)

    def stop_motion(self) -> None:
        self.publish_velocity(0.0, 0.0, 0.0)

    def _on_robot_state(self, message: Any) -> None:
        pos_body = getattr(message, self.field_mapping.position_field, None)
        ori_body = getattr(message, self.field_mapping.orientation_field, None)
        temp = getattr(message, self.field_mapping.mode_field, None)
        if pos_body is not None and len(pos_body) >= 2:
            self.snapshot.x = float(pos_body[0])
            self.snapshot.y = float(pos_body[1])
            self.snapshot.z = float(pos_body[2]) if len(pos_body) > 2 else 0.0
        if ori_body is not None and len(ori_body) >= 4:
            self.snapshot.theta = quaternion_wxyz_to_yaw(
                float(ori_body[0]),
                float(ori_body[1]),
                float(ori_body[2]),
                float(ori_body[3]),
            )
        if temp is not None and len(temp) > self.field_mapping.mode_index:
            self.snapshot.mode = int(temp[self.field_mapping.mode_index])
        self.snapshot.raw_state = message

    def _on_camera_info(self, message: Any) -> None:
        self.snapshot.camera_info = {
            "frame_id": getattr(message.header, "frame_id", ""),
            "width": int(message.width),
            "height": int(message.height),
            "k": list(message.k),
            "d": list(message.d),
            "r": list(message.r),
            "p": list(message.p),
            "distortion_model": str(message.distortion_model),
        }


def quaternion_wxyz_to_yaw(w: float, x: float, y: float, z: float) -> float:
    import math

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)
```

- [ ] **Step 2: Document onboard runtime**

Create `hexplorer-client/README.md`:

````markdown
# Hexplorer Client

Runs on the Dobot Hexplorer onboard Ubuntu 22.04 system with ROS2 Humble.

Before running the adapter:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
```

Expected ROS2 topics from the user guide:

- `/robot_cmd`: `custom_msg/msg/RobotCommand`
- `/robot_state`: `custom_msg/msg/RobotState`
- `/vel_cmd`: `geometry_msgs/msg/Twist`
- `/realsense_camera_node/.../camera_info`: `sensor_msgs/msg/CameraInfo`

The ACS should communicate through VDA5050 MQTT. ROS2 is used only inside the adapter process.

Before finalizing `HexplorerFieldMapping`, fill `docs/reference/hexplorer-ros2-interface.md` from the onboard robot and verify that the defaults match the actual `custom_msg` definitions. If they do not match, update `adaptor/config/config.toml` and `adaptor/config/config.py` field mapping defaults before running the adapter.
````

- [ ] **Step 3: Run import-only check on onboard robot**

Run on Hexplorer onboard PC:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
PYTHONPATH=hexplorer-client/src python3 -c "from hexplorer_client.client import HexplorerClient; c=HexplorerClient(); c.start(); c.spin_once(0.1); c.stop(); print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add hexplorer-client
git commit -m "feat: add ros2 hexplorer client"
```

## Task 3: Config Support

**Files:**
- Modify: `adaptor/config/config.py`
- Modify: `adaptor/config/config.toml`

- [ ] **Step 1: Add Hexplorer config dataclass**

Modify `adaptor/config/config.py` to add:

```python
@dataclass
class HexplorerConfig:
    robot_command_topic: str = "/robot_cmd"
    robot_state_topic: str = "/robot_state"
    velocity_command_topic: str = "/vel_cmd"
    camera_info_topic: str = "/realsense_camera_node/sn408122070053/camera_info"
    position_field: str = "pos_body"
    orientation_field: str = "ori_body"
    mode_field: str = "temp"
    mode_index: int = 10
    command_target_state_field: str = "target_state"
    stand_down_state: int = 1
    stand_up_state: int = 2
    walk_mode_state: int = 4
    map_source_dir: str = "/home/robot/Documents"
    map_target_dir: str = "./hexplorer/maps"
    map_backup_dir: str = "./backups/hexplorer-maps"
```

Add `hexplorer: HexplorerConfig` to the root config dataclass and construct it from `data.get("hexplorer", {})`. Confirm all defaults against `docs/reference/hexplorer-ros2-interface.md` before running on hardware.

- [ ] **Step 2: Add config defaults**

Append to `adaptor/config/config.toml`:

```toml
[hexplorer]
robot_command_topic = "/robot_cmd"
robot_state_topic = "/robot_state"
velocity_command_topic = "/vel_cmd"
camera_info_topic = "/realsense_camera_node/sn408122070053/camera_info"
position_field = "pos_body"
orientation_field = "ori_body"
mode_field = "temp"
mode_index = 10
command_target_state_field = "target_state"
stand_down_state = 1
stand_up_state = 2
walk_mode_state = 4
map_source_dir = "/home/robot/Documents"
map_target_dir = "./hexplorer/maps"
map_backup_dir = "./backups/hexplorer-maps"
```

- [ ] **Step 3: Run config import check**

Run:

```bash
python3 -c "import sys; sys.path.insert(0, 'adaptor'); from config.config import get_config; c=get_config(); print(c.hexplorer.robot_state_topic)"
```

Expected: prints `/robot_state`.

- [ ] **Step 4: Commit**

```bash
git add adaptor/config/config.py adaptor/config/config.toml
git commit -m "feat: add hexplorer adapter config"
```

## Task 4: VDA5050 Hexplorer Adapter

**Files:**
- Create: `adaptor/adapter_hexplorer.py`
- Create: `adaptor/tests/test_adapter_hexplorer_state.py`

- [ ] **Step 1: Implement adapter skeleton**

Create `adaptor/adapter_hexplorer.py`:

```python
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ADAPTER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = ADAPTER_ROOT.parent
CLIENT_SRC = REPO_ROOT / "hexplorer-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from config.config import get_config
from hexplorer_client.client import HexplorerClient, HexplorerFieldMapping, HexplorerTopics
from protocol.vda5050_3_0.messages import (
    Connection,
    ConnectionState,
    EmergencyStop,
    Header,
    MobileRobotPosition,
    OperatingMode,
    PowerSupply,
    SafetyState,
    State,
)
from protocol.vda5050_3_0.transport import Vda5050V3Transport
from utils.cls_mqtt import MQTTClient
from utils.utils import get_timestamp


class HexplorerAdapter:
    def __init__(self, client: HexplorerClient | None = None) -> None:
        self.config = get_config()
        topics = HexplorerTopics(
            robot_command=self.config.hexplorer.robot_command_topic,
            robot_state=self.config.hexplorer.robot_state_topic,
            velocity_command=self.config.hexplorer.velocity_command_topic,
            camera_info=self.config.hexplorer.camera_info_topic,
        )
        field_mapping = HexplorerFieldMapping(
            position_field=self.config.hexplorer.position_field,
            orientation_field=self.config.hexplorer.orientation_field,
            mode_field=self.config.hexplorer.mode_field,
            mode_index=self.config.hexplorer.mode_index,
            command_target_state_field=self.config.hexplorer.command_target_state_field,
        )
        self.client = client or HexplorerClient(topics, field_mapping)
        self.mqtt = MQTTClient()
        self.vda3 = Vda5050V3Transport(self.mqtt)
        self.header_id = 0
        self.command_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    def connect_mqtt(self) -> None:
        self.mqtt.connect()

    def disconnect_mqtt(self) -> None:
        self.mqtt.disconnect()

    async def run(self) -> None:
        self.client.start()
        self.connect_mqtt()
        self.publish_connection(ConnectionState.ONLINE)
        self.vda3.subscribe_instant_actions(self.handle_vda_message, qos=0)
        while True:
            await asyncio.to_thread(self.client.spin_once, 0.05)
            await self._drain_command_queue()
            self.publish_state()
            await asyncio.sleep(float(self.config.settings.state_publish_delay))

    def publish_connection(self, state: ConnectionState) -> None:
        self.header_id += 1
        connection = Connection(
            header=Header(
                header_id=self.header_id,
                timestamp=get_timestamp(),
                version=self.config.vehicle.vda_full_version,
                manufacturer="dobot",
                serial_number=self.config.vehicle.serial_number,
            ),
            connection_state=state,
        )
        self.vda3.publish_connection(connection, qos=1, retain=True)

    def handle_vda_message(self, topic: str, payload: Any) -> None:
        topic_type = topic.rsplit("/", 1)[-1]
        if topic_type == "instantActions":
            self._handle_instant_actions(payload)

    def _handle_instant_actions(self, payload: Any) -> None:
        actions = payload.get("actions", []) if isinstance(payload, dict) else []
        for action in actions:
            action_type = action.get("actionType")
            params = {p.get("key"): p.get("value") for p in action.get("actionParameters", [])}
            if action_type == "standUp":
                self.command_queue.put_nowait(("setRobotState", {"state": self.config.hexplorer.stand_up_state}))
            elif action_type == "standDown":
                self.command_queue.put_nowait(("setRobotState", {"state": self.config.hexplorer.stand_down_state}))
            elif action_type == "walkMode":
                self.command_queue.put_nowait(("setRobotState", {"state": self.config.hexplorer.walk_mode_state}))
            elif action_type == "stop":
                self.command_queue.put_nowait(("velocity", {"x": 0.0, "y": 0.0, "yaw": 0.0}))
            elif action_type == "velocity":
                self.command_queue.put_nowait(
                    (
                        "velocity",
                        {
                            "x": float(params.get("x", 0.0)),
                            "y": float(params.get("y", 0.0)),
                            "yaw": float(params.get("yaw", 0.0)),
                        },
                    )
                )

    async def _drain_command_queue(self) -> None:
        while not self.command_queue.empty():
            command, params = await self.command_queue.get()
            if command == "setRobotState":
                self.client.set_robot_state(int(params["state"]))
            elif command == "velocity":
                self.client.publish_velocity(
                    float(params["x"]),
                    float(params["y"]),
                    float(params["yaw"]),
                )

    def publish_state(self) -> None:
        self.header_id += 1
        snapshot = self.client.snapshot
        position = MobileRobotPosition(
            x=snapshot.x,
            y=snapshot.y,
            theta=snapshot.theta,
            map_id=self.config.settings.map_id,
            localized=snapshot.localized,
            localization_score=1.0 if snapshot.localized else 0.0,
        )
        state = self._build_state(position)
        self.vda3.publish_state(state, qos=0)

    def _build_state(self, position: MobileRobotPosition) -> Any:
        return State(
            header=Header(
                header_id=self.header_id,
                timestamp=get_timestamp(),
                version=self.config.vehicle.vda_full_version,
                manufacturer="dobot",
                serial_number=self.config.vehicle.serial_number,
            ),
            order_id="",
            order_update_id=0,
            last_node_id="",
            last_node_sequence_id=0,
            node_states=[],
            edge_states=[],
            driving=False,
            action_states=[],
            instant_action_states=[],
            power_supply=PowerSupply(state_of_charge=0.0, charging=False),
            operating_mode=OperatingMode.AUTOMATIC,
            errors=[],
            safety_state=SafetyState(
                active_emergency_stop=EmergencyStop.NONE,
                field_violation=False,
            ),
            information=[],
            mobile_robot_position=position,
        )
```

- [ ] **Step 2: Add ROS-free state construction test**

Create `adaptor/tests/test_adapter_hexplorer_state.py`:

```python
from protocol.vda5050_3_0.messages import MobileRobotPosition


def test_build_state_matches_vda5050_v3_dataclasses() -> None:
    from adapter_hexplorer import HexplorerAdapter

    adapter = HexplorerAdapter(client=None)
    adapter.header_id = 7
    state = adapter._build_state(
        MobileRobotPosition(
            x=1.0,
            y=2.0,
            theta=0.5,
            map_id="lab2m",
            localized=True,
            localization_score=1.0,
        )
    )

    payload = state.to_dict()

    assert payload["headerId"] == 7
    assert payload["mobileRobotPosition"]["theta"] == 0.5
    assert payload["powerSupply"]["stateOfCharge"] == 0.0
    assert payload["safetyState"]["activeEmergencyStop"] == "NONE"
    assert payload["driving"] is False
    assert payload["instantActionStates"] == []
```

- [ ] **Step 3: Run state test and syntax check**

Run:

```bash
PYTHONPATH=adaptor:hexplorer-client/src pytest adaptor/tests/test_adapter_hexplorer_state.py -v
python3 -m py_compile adaptor/adapter_hexplorer.py
```

Expected: test passes, compile check has no output, and exit code is 0.

- [ ] **Step 4: Commit**

```bash
git add adaptor/adapter_hexplorer.py adaptor/tests/test_adapter_hexplorer_state.py
git commit -m "feat: add hexplorer vda5050 adapter skeleton"
```

## Task 5: Runtime Entrypoint

**Files:**
- Create: `adaptor/main_hexplorer.py`

- [ ] **Step 1: Add Hexplorer main script**

Create `adaptor/main_hexplorer.py`:

```python
from __future__ import annotations

import asyncio

from adapter_hexplorer import HexplorerAdapter
from protocol.vda5050_3_0.messages import ConnectionState


async def main() -> None:
    adapter = HexplorerAdapter()
    try:
        await adapter.run()
    finally:
        adapter.publish_connection(ConnectionState.OFFLINE)
        adapter.disconnect_mqtt()
        adapter.client.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run syntax check**

Run:

```bash
python3 -m py_compile adaptor/main_hexplorer.py
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run onboard smoke command**

Run on Hexplorer onboard PC:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
PYTHONPATH=../hexplorer-client/src python3 main_hexplorer.py
```

Expected: MQTT connects, ROS2 client starts, and state publishes repeatedly.

- [ ] **Step 4: Commit**

```bash
git add adaptor/main_hexplorer.py
git commit -m "feat: add hexplorer adapter entrypoint"
```

## Task 6: Map and Camera Instant Actions

**Files:**
- Modify: `adaptor/adapter_hexplorer.py`

- [ ] **Step 1: Add action handlers**

Modify `_handle_instant_actions()` in `adaptor/adapter_hexplorer.py` so it handles:

```python
elif action_type == "syncHexplorerMap":
    include_names = params.get("includeNames", ["record"])
    if isinstance(include_names, str):
        include_names = [include_names]
    self.command_queue.put_nowait(
        (
            "syncHexplorerMap",
            {
                "sourceDir": params.get("sourceDir", self.config.hexplorer.map_source_dir),
                "targetDir": params.get("targetDir", self.config.hexplorer.map_target_dir),
                "backupDir": params.get("backupDir", self.config.hexplorer.map_backup_dir),
                "includeNames": tuple(include_names),
            },
        )
    )
elif action_type == "getCameraInfo":
    self.command_queue.put_nowait(("getCameraInfo", {}))
```

Modify `_drain_command_queue()` so it handles the new queued commands:

```python
elif command == "syncHexplorerMap":
    from hexplorer_client.map_files import sync_hexplorer_maps

    result = sync_hexplorer_maps(
        source_dir=Path(params["sourceDir"]),
        target_dir=Path(params["targetDir"]),
        backup_root=Path(params["backupDir"]),
        include_names=tuple(params["includeNames"]),
    )
    print(f"[HEXPLORER MAP] copied={result.copied} backup={result.backup_dir}")
elif command == "getCameraInfo":
    print(f"[HEXPLORER CAMERA] {self.client.snapshot.camera_info}")
```

- [ ] **Step 2: Run syntax check**

Run:

```bash
python3 -m py_compile adaptor/adapter_hexplorer.py
```

Expected: no output and exit code 0.

- [ ] **Step 3: Test instantActions manually through MQTT**

Publish:

```json
{
  "headerId": 1,
  "timestamp": "2026-06-06T00:00:00.000Z",
  "version": "3.0.0",
  "manufacturer": "dobot",
  "serialNumber": "HN-SH6-TR-001",
  "actions": [
    {
      "actionType": "getCameraInfo",
      "actionId": "camera-info-001",
      "blockingType": "NONE"
    }
  ]
}
```

Expected: adapter logs current camera info or `None` if no camera_info message has arrived yet.

- [ ] **Step 4: Commit**

```bash
git add adaptor/adapter_hexplorer.py
git commit -m "feat: add hexplorer map and camera actions"
```

## Task 7: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add Hexplorer adapter section**

Add to `README.md`:

````markdown
## Hexplorer Adapter

The Hexplorer integration should run on the robot onboard Ubuntu 22.04/ROS2 Humble system.

Recommended architecture:

```text
ACS / Fleet Manager
  <-> VDA5050 MQTT
adaptor/main_hexplorer.py
  <-> ROS2 topics
Hexplorer onboard control nodes
```

ACS should continue to use VDA5050. The adapter converts VDA5050 commands into Hexplorer ROS2 topic messages.
Initial support is limited to VDA5050 `instantActions`. VDA5050 `order` execution is deferred until Hexplorer waypoint/navigation topics and map semantics are confirmed on the onboard robot.

Basic onboard setup:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
cd adaptor
python3 main_hexplorer.py
```

Supported initial instantActions:

- `standUp`: sends the configured RobotCommand target-state field with `hexplorer.stand_up_state`
- `standDown`: sends the configured RobotCommand target-state field with `hexplorer.stand_down_state`
- `walkMode`: sends the configured RobotCommand target-state field with `hexplorer.walk_mode_state`
- `stop`: publishes zero `/vel_cmd`
- `velocity`: publishes `/vel_cmd` with `x`, `y`, `yaw`
- `syncHexplorerMap`: copies selected mapping output directories with backup
- `getCameraInfo`: logs the latest ROS2 `CameraInfo` snapshot
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document hexplorer adapter"
```

## Task 8: Final Verification

**Files:**
- No source file changes.

- [ ] **Step 1: Run non-ROS tests**

Run:

```bash
PYTHONPATH=hexplorer-client/src pytest hexplorer-client/tests/test_map_files.py -v
PYTHONPATH=adaptor:hexplorer-client/src pytest adaptor/tests/test_adapter_hexplorer_state.py -v
python3 -m py_compile adaptor/adapter_hexplorer.py adaptor/main_hexplorer.py
```

Expected: map sync tests pass, VDA5050 state construction test passes through `State.to_dict()`, and compile checks succeed.

- [ ] **Step 2: Run onboard ROS2 smoke test**

Run on Hexplorer:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
ros2 topic list
PYTHONPATH=hexplorer-client/src python3 -c "from hexplorer_client.client import HexplorerClient; c=HexplorerClient(); c.start(); c.spin_once(1.0); print(c.snapshot); c.stop()"
```

Expected:

- `ros2 topic list` includes `/robot_cmd`, `/robot_state`, `/vel_cmd`.
- The Python smoke command prints `HexplorerRobotSnapshot(...)`.

- [ ] **Step 3: Run adapter smoke test**

Run on Hexplorer:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
cd adaptor
PYTHONPATH=../hexplorer-client/src python3 main_hexplorer.py
```

Expected:

- MQTT connects to configured broker.
- A retained VDA5050 connection message publishes to `amr/v3/<serialNumber>/connection` with `connectionState=ONLINE`.
- VDA5050 state publishes to `amr/v3/<serialNumber>/state`.
- `getCameraInfo` instantAction logs the latest camera metadata after camera topic messages arrive.
- On shutdown, a retained VDA5050 connection message publishes with `connectionState=OFFLINE`.

## Self-Review

- Spec coverage: The plan covers onboard ROS2 interface capture, VDA5050 as the ACS-facing interface, instantAction-based ROS2 command mapping, VDA5050 connection/state publishing, map file sync, camera info capture, runtime entrypoint, docs, and verification. VDA5050 order execution is explicitly deferred until Hexplorer waypoint/navigation semantics are confirmed.
- Placeholder scan: The plan no longer hardcodes unverified Dobot custom message fields as facts; Task 0 captures the real onboard definitions, and Task 3 exposes field/mode mapping through config. The configurable camera serial in the default topic is copied from the user guide and can be overridden in `config.toml`.
- Type consistency: `HexplorerClient`, `HexplorerFieldMapping`, `HexplorerTopics`, `sync_hexplorer_maps`, and `HexplorerAdapter` names are introduced before use and reused consistently. VDA5050 `State`, `PowerSupply`, and `SafetyState` construction now matches `adaptor/protocol/vda5050_3_0/messages.py`.
