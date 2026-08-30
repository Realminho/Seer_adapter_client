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
