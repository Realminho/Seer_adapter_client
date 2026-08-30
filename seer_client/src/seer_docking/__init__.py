"""SEER AMR AprilTag docking reference package."""

from .controller import AnalyticDockingController, DockPhase
from .estimator import PlanarDockEKF
from .geometry import base_pose_in_dock, base_pose_from_marker_center_axis, wrap_angle
from .model import DockParams, DockState, VelocityCommand, step_unicycle
from .motors import WheelMotorValues, differential_wheel_values
from .visual_servo import (
    CameraVisualServoController,
    VisualDockObservation,
    VisualTarget,
    visual_target_for_base_distance,
)

__all__ = [
    "AnalyticDockingController",
    "DockPhase",
    "DockParams",
    "DockState",
    "PlanarDockEKF",
    "VelocityCommand",
    "WheelMotorValues",
    "CameraVisualServoController",
    "VisualDockObservation",
    "VisualTarget",
    "base_pose_in_dock",
    "base_pose_from_marker_center_axis",
    "differential_wheel_values",
    "step_unicycle",
    "visual_target_for_base_distance",
    "wrap_angle",
]
