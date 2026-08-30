# Hexplorer ROS2 Interface Reference

Captured on Hexplorer onboard Ubuntu 22.04 / ROS2 Humble.

This repository checkout is not running on the Hexplorer onboard ROS2 system, so the exact `custom_msg` definitions still need to be captured on the robot before hardware motion testing. The adapter implementation keeps the Dobot-specific field names and mode values configurable so this document can drive final calibration.

## Capture Commands

Run on the Hexplorer onboard PC:

```bash
source /opt/ros/humble/setup.bash
source ~/robot_controller_release/ros2_packages/setup.bash
ros2 interface show custom_msg/msg/RobotState
ros2 interface show custom_msg/msg/RobotCommand
ros2 topic info /robot_state -v
ros2 topic info /robot_cmd -v
ros2 topic info /vel_cmd -v
ros2 topic echo /robot_state --once
```

## Topic Summary

- `/robot_cmd`: expected `custom_msg/msg/RobotCommand`
- `/robot_state`: expected `custom_msg/msg/RobotState`
- `/vel_cmd`: expected `geometry_msgs/msg/Twist`
- `/realsense_camera_node/.../camera_info`: expected `sensor_msgs/msg/CameraInfo`

## RobotCommand

Pending onboard capture from:

```bash
ros2 interface show custom_msg/msg/RobotCommand
```

## RobotState

Pending onboard capture from:

```bash
ros2 interface show custom_msg/msg/RobotState
```

## Confirmed Field Mapping

- Position source field: pending onboard capture, default config uses `pos_body`
- Orientation source field: pending onboard capture, default config uses `ori_body`
- Operating mode/status source field: pending onboard capture, default config uses `temp[10]`
- Battery source field: pending onboard capture
- Localization source field: pending onboard capture

## Confirmed Mode Commands

- Stand down command: pending onboard capture, default config uses `1`
- Stand up command: pending onboard capture, default config uses `2`
- Walk mode command: pending onboard capture, default config uses `4`

## Live RobotState Sample

Pending onboard capture from:

```bash
ros2 topic echo /robot_state --once
```
