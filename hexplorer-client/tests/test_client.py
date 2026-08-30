import math
import unittest
from types import SimpleNamespace

from hexplorer_client.client import (
    HexplorerClient,
    HexplorerFieldMapping,
    quaternion_wxyz_to_yaw,
)


class HexplorerClientTest(unittest.TestCase):
    def test_quaternion_wxyz_to_yaw_returns_heading_radians(self) -> None:
        yaw = math.pi / 2
        self.assertAlmostEqual(
            quaternion_wxyz_to_yaw(math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)),
            yaw,
        )

    def test_robot_state_parser_uses_configurable_fields(self) -> None:
        client = HexplorerClient(
            field_mapping=HexplorerFieldMapping(
                position_field="body_position",
                orientation_field="body_orientation",
                mode_field="status_values",
                mode_index=2,
            )
        )

        client._on_robot_state(
            SimpleNamespace(
                body_position=[1.2, 3.4, 0.5],
                body_orientation=[1.0, 0.0, 0.0, 0.0],
                status_values=[0, 1, 4],
            )
        )

        self.assertEqual(client.snapshot.x, 1.2)
        self.assertEqual(client.snapshot.y, 3.4)
        self.assertEqual(client.snapshot.z, 0.5)
        self.assertEqual(client.snapshot.theta, 0.0)
        self.assertEqual(client.snapshot.mode, 4)

    def test_robot_state_parser_honors_xyzw_quaternion_order(self) -> None:
        yaw = math.pi / 2
        # ROS-standard xyzw layout: [x, y, z, w]
        quat_xyzw = [0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)]
        client = HexplorerClient(
            field_mapping=HexplorerFieldMapping(
                orientation_field="ori",
                orientation_quaternion_order="xyzw",
            )
        )

        client._on_robot_state(SimpleNamespace(ori=quat_xyzw))

        self.assertAlmostEqual(client.snapshot.theta, yaw)


if __name__ == "__main__":
    unittest.main()
