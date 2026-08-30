"""Declarative routing and parameter validation for SEER commands."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, NamedTuple, Tuple

from .protocol import ApiNumber, ApiPort


class CommandSpec(NamedTuple):
    port: ApiPort
    api: ApiNumber
    required: Tuple[str, ...]
    optional: Tuple[str, ...]
    method: str


COMMAND_CATALOG: Mapping[str, CommandSpec] = MappingProxyType(
    {
        # State queries (19204)
        "query_status": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_INFO, (), (), "get_robot_info"),
        "query_run": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_RUN, (), (), "get_robot_info"),
        "query_mode": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_MODE, (), (), "get_robot_info"),
        "query_status_all": CommandSpec(
            ApiPort.STATE,
            ApiNumber.STATUS_ALL1,
            (),
            ("keys", "return_laser", "return_beams3D", "return_mid360", "timeout", "language"),
            "get_robot_info",
        ),
        "query_location": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_LOC, (), (), "get_localization_info"),
        "query_speed": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_SPEED, (), (), "get_robot_info"),
        "query_battery": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_BATTERY, (), (), "get_battery_info"),
        "query_task": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_TASK, (), (), "get_task_status"),
        "query_block": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_BLOCK, (), (), "get_blocked"),
        "query_emergency": CommandSpec(
            ApiPort.STATE,
            ApiNumber.STATUS_EMERGENCY,
            (),
            (),
            "get_emergency_state",
        ),
        "query_io": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_IO, (), (), "read_di"),
        "query_map": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_MAP, (), (), "get_map_info"),
        "query_model": CommandSpec(ApiPort.STATE, ApiNumber.STATUS_MODEL, (), (), "get_robot_model"),

        # Navigation tasks (19206)
        "pause_task": CommandSpec(ApiPort.TASK, ApiNumber.TASK_PAUSE, (), (), "pause_navigation"),
        "resume_task": CommandSpec(ApiPort.TASK, ApiNumber.TASK_RESUME, (), (), "resume_navigation"),
        "cancel_task": CommandSpec(ApiPort.TASK, ApiNumber.TASK_CANCEL, (), (), "cancel_navigation"),
        "free_nav": CommandSpec(
            ApiPort.TASK,
            ApiNumber.TASK_GOTARGET,
            ("id", "freeGo"),
            ("task_id",),
            "free_nav",
        ),
        "goto_station": CommandSpec(
            ApiPort.TASK,
            ApiNumber.TASK_GOTARGET,
            ("id",),
            ("source_id", "task_id", "angle"),
            "goto_point",
        ),
        "goto_pose": CommandSpec(
            ApiPort.TASK,
            ApiNumber.TASK_GOTARGET,
            ("script_name", "script_args"),
            (),
            "goto_xyz",
        ),
        "goto_route": CommandSpec(
            ApiPort.TASK,
            ApiNumber.TASK_GOTARGET_LIST,
            ("move_task_list",),
            (),
            "goto_point",
        ),
        "translate": CommandSpec(
            ApiPort.TASK,
            ApiNumber.TASK_TRANSLATE,
            ("dist",),
            ("vx", "vy", "mode"),
            "move_distance",
        ),
        "turn": CommandSpec(
            ApiPort.TASK,
            ApiNumber.TASK_TURN,
            ("angle", "vw"),
            ("mode",),
            "turn",
        ),

        # Control (19205)
        "stop": CommandSpec(ApiPort.CONTROL, ApiNumber.CONTROL_STOP, (), (), "stop_motion"),
        "reloc": CommandSpec(
            ApiPort.CONTROL,
            ApiNumber.CONTROL_RELOC,
            ("isAuto", "x", "y", "angle"),
            ("length", "home"),
            "localize",
        ),
        "motion": CommandSpec(
            ApiPort.CONTROL,
            ApiNumber.CONTROL_MOTION,
            (),
            ("vx", "vy", "w", "steer", "duration"),
            "drive",
        ),
        "load_map": CommandSpec(
            ApiPort.CONTROL,
            ApiNumber.CONTROL_LOADMAP,
            ("map_name",),
            (),
            "map_switch",
        ),

        # Map configuration/download (19207)
        "download_map": CommandSpec(
            ApiPort.CONFIG,
            ApiNumber.CONFIG_DOWNLOAD_MAP,
            ("map_name",),
            (),
            "download_map",
        ),

        # Digital I/O and motor control (19210)
        "set_do": CommandSpec(
            ApiPort.OTHER,
            ApiNumber.OTHER_SETDO,
            ("id", "status"),
            (),
            "set_do",
        ),
        "set_soft_emergency": CommandSpec(
            ApiPort.OTHER,
            ApiNumber.OTHER_SOFT_EMERGENCY,
            ("status",),
            (),
            "set_soft_emergency",
        ),
        "set_motor": CommandSpec(
            ApiPort.OTHER,
            ApiNumber.OTHER_SET_MOTOR,
            ("motor_name", "enable"),
            (),
            "set_motor",
        ),
        # Lift-type robots execute the controller-side jack ModuleScript
        # through the task port using the standard goto-target script call.
        "jack_load": CommandSpec(
            ApiPort.TASK,
            ApiNumber.TASK_GOTARGET,
            ("script_name", "script_args"),
            (),
            "jack_load",
        ),
        "jack_unload": CommandSpec(
            ApiPort.TASK,
            ApiNumber.TASK_GOTARGET,
            ("script_name", "script_args"),
            (),
            "jack_unload",
        ),
    }
)
