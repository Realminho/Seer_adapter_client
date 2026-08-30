from __future__ import annotations

from dataclasses import dataclass
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
    # Component order of the orientation quaternion array. ROS convention is
    # "xyzw"; some custom_msg definitions use "wxyz". Confirm onboard.
    orientation_quaternion_order: str = "wxyz"


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
        from custom_msg.msg import RobotCommand, RobotState
        from geometry_msgs.msg import Twist
        from sensor_msgs.msg import CameraInfo

        if not rclpy.ok():
            rclpy.init()

        self._node = rclpy.create_node("hexplorer_vda5050_client")
        self._velocity_publisher = self._node.create_publisher(
            Twist,
            self.topics.velocity_command,
            1,
        )
        self._robot_command_publisher = self._node.create_publisher(
            RobotCommand,
            self.topics.robot_command,
            1,
        )
        self._node.create_subscription(
            RobotState,
            self.topics.robot_state,
            self._on_robot_state,
            1,
        )
        self._node.create_subscription(
            CameraInfo,
            self.topics.camera_info,
            self._on_camera_info,
            10,
        )

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
            raise RuntimeError(
                "HexplorerClient.start() must be called before set_robot_state()"
            )
        command = RobotCommand()
        setattr(
            command,
            self.field_mapping.command_target_state_field,
            int(target_state),
        )
        self._robot_command_publisher.publish(command)

    def publish_velocity(self, x: float, y: float, yaw: float) -> None:
        from geometry_msgs.msg import Twist

        if self._velocity_publisher is None:
            raise RuntimeError(
                "HexplorerClient.start() must be called before publish_velocity()"
            )
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
        mode_values = getattr(message, self.field_mapping.mode_field, None)
        if pos_body is not None and len(pos_body) >= 2:
            self.snapshot.x = float(pos_body[0])
            self.snapshot.y = float(pos_body[1])
            self.snapshot.z = float(pos_body[2]) if len(pos_body) > 2 else 0.0
        if ori_body is not None and len(ori_body) >= 4:
            w, x, y, z = _quaternion_components(
                ori_body, self.field_mapping.orientation_quaternion_order
            )
            self.snapshot.theta = quaternion_wxyz_to_yaw(w, x, y, z)
        if (
            mode_values is not None
            and len(mode_values) > self.field_mapping.mode_index
        ):
            self.snapshot.mode = int(mode_values[self.field_mapping.mode_index])
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


def _quaternion_components(
    values: Any, order: str
) -> tuple[float, float, float, float]:
    """Return (w, x, y, z) from a quaternion array given its component order."""
    layouts = {
        "wxyz": ("w", "x", "y", "z"),
        "xyzw": ("x", "y", "z", "w"),
    }
    layout = layouts.get(order.lower())
    if layout is None:
        raise ValueError(
            f"Unsupported quaternion order: {order!r} (expected 'wxyz' or 'xyzw')"
        )
    index = {name: pos for pos, name in enumerate(layout)}
    return (
        float(values[index["w"]]),
        float(values[index["x"]]),
        float(values[index["y"]]),
        float(values[index["z"]]),
    )


def quaternion_wxyz_to_yaw(w: float, x: float, y: float, z: float) -> float:
    import math

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)
