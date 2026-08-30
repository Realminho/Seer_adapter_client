"""Runtime bridge that attaches SeerClient to the unmodified JIBOT adapter.

The repository's original ``adaptor/main.py`` constructs a global ``JIBOT``
class and imports ``SimulatedJIBOT`` only when ``--simulator`` is selected.
This module replaces those two runtime construction seams without editing any
file outside ``seer_client``.
"""

from __future__ import annotations

import asyncio
import json
import math
import inspect
import os
import random
import sys
import types
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .capabilities import capability_report, filter_factsheet_actions
from .block_program import make_program_handler, program_variables
from .client import SeerClient
from .drive_limits import ManualDriveLimits
from .jack_status import cache_path_from_env, write_jack_status
from .map_view import clear_active_route, read_map_cache, write_active_route
from .navigation import SeerNavigationTaskError
from .platform_compat import install_fcntl_compat
from .protocol import ApiPort
from .simulator import SeerSimulatorServer


install_fcntl_compat()


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

# The shared Adapter discovers these JIBOT hardware modules before it builds
# recipe actions.  A SEER process supplies its own IO/motor/navigation actions,
# so exposing the JIBOT clamp/PIO/facility handlers would be both misleading
# and unsafe.  Keep the module names and the action types that recipes may
# reference together so newer Adapter releases can validate the reduced SEER
# registry without failing during startup.
JIBOT_ONLY_ACTION_MODULES = (
    "extensions.clamp",
    "extensions.pio",
    "extensions.ezio",
    "extensions.facility",
)
JIBOT_ONLY_RECIPE_EXTENSIONS = frozenset(
    {
        "clamp",
        "unclamp",
        "clampTeach",
        "clampOn",
        "clampOff",
        "clampStop",
        "clampMin",
        "clampMax",
        "clampHome",
        "clampMoveTo",
        "pioInit",
        "pioReadIn",
        "pioWriteOut",
        "pioDisconnect",
        "pioScenario",
        "ezioReadIn",
        "photoSensorRead",
        "ezioWriteOut",
        "airShowerEnter",
        "airShowerInside",
        "airShowerPassed",
        "elevatorEnter",
        "elevatorInside",
        "elevatorPassed",
    }
)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if not value else int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if not value else float(value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in TRUE_VALUES


def _parse_route_points(value: Any) -> Tuple[str, ...]:
    """Validate an optional WebUI-selected list of SEER point names."""

    if value is None or value == "":
        return ()
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            # Recipes created by older Block Builder releases wrote a JSON
            # array inside an HCL string. python-hcl2 preserves the escaped
            # quotes, e.g. ``[\\\"LM1\\\",\\\"LM2\\\"]``. Accept that legacy
            # representation so an existing Recipe starts moving immediately;
            # newly saved Recipes use a native HCL list.
            try:
                parsed = json.loads(value.replace('\\\"', '"'))
            except (TypeError, ValueError):
                raise ValueError("SEER route_points must be a JSON array") from exc
    if not isinstance(parsed, (list, tuple)):
        raise ValueError("SEER route_points must be a JSON array")
    if len(parsed) > 32:
        raise ValueError("SEER selected route has more than 32 points")
    points = tuple(str(item).strip() for item in parsed)
    if any(not point for point in points):
        raise ValueError("SEER selected route contains an empty point id")
    if any(len(point) > 128 for point in points):
        raise ValueError("SEER selected route point id is too long")
    if any(points[index] == points[index - 1] for index in range(1, len(points))):
        raise ValueError("SEER selected route contains consecutive duplicate points")
    return points


def _missing_designated_route_edges(
    points: Sequence[str],
) -> Tuple[Tuple[str, str], ...]:
    """Return route legs absent from the current directed SEER map cache.

    A missing or incomplete cache must not block a real controller. When a
    usable curve graph is available, however, rejecting an invalid leg here
    gives the operator a precise message instead of a controller-side API 3066
    rejection that previously looked like a successful ``delivered`` request.
    """

    cache_value = str(os.getenv("SEER_MAP_CACHE_PATH", "") or "").strip()
    if len(points) < 2 or not cache_value:
        return ()
    model = read_map_cache(Path(cache_value))
    if not isinstance(model, Mapping):
        return ()
    direct_edges = {
        (
            str(item.get("start_name", "") or "").strip(),
            str(item.get("end_name", "") or "").strip(),
        )
        for item in (model.get("curves", ()) or ())
        if isinstance(item, Mapping)
    }
    direct_edges.discard(("", ""))
    if not direct_edges:
        return ()
    return tuple(
        (source, target)
        for source, target in zip(points, points[1:])
        if (source, target) not in direct_edges
    )


def _motor_names() -> Sequence[str]:
    return tuple(
        item.strip()
        for item in os.getenv("SEER_MOTOR_NAMES", "").split(",")
        if item.strip()
    )


def _port_map() -> Mapping[ApiPort, int]:
    return {
        ApiPort.STATE: _env_int("SEER_STATE_PORT", 19204),
        ApiPort.CONTROL: _env_int("SEER_CONTROL_PORT", 19205),
        ApiPort.TASK: _env_int("SEER_TASK_PORT", 19206),
        ApiPort.CONFIG: _env_int("SEER_CONFIG_PORT", 19207),
        ApiPort.OTHER: _env_int("SEER_OTHER_PORT", 19210),
    }


def _adapter_units(config: Any) -> Tuple[str, str]:
    factsheet = getattr(config, "factsheet", None)
    position = getattr(factsheet, "coordinate_unit_position", "mm")
    orientation = getattr(factsheet, "coordinate_unit_orientation", "deg")
    return str(position), str(orientation)


def _disable_jibot_only_runtime_sources(config: Any) -> None:
    """Disable only the runtime ROS feed that belongs to JIBOT.

    This mutates the in-memory config object, not ``config.toml``. SEER battery,
    motor and safety fields arrive through Robokit STATE APIs.
    """

    bms_ros = getattr(config, "bms_ros", None)
    if bms_ros is not None:
        bms_ros.enabled = False

    vehicle = getattr(config, "vehicle", None)
    if vehicle is not None:
        vehicle.manufacturer = "seer"
    factsheet = getattr(config, "factsheet", None)
    if factsheet is not None:
        factsheet.series_name = "SEER"
    # Configured action modules in the supplied Adapter target JIBOT clamp/PIO
    # and facility devices. Keep them out of a SEER process without touching
    # config.toml.
    if hasattr(config, "action_modules"):
        config.action_modules = [
            SimpleNamespace(module=name, enabled=False)
            for name in JIBOT_ONLY_ACTION_MODULES
        ]

    # Adapter (7) validates every recipe against the action modules that remain
    # enabled.  The shipped elevator recipes reference pioInit/ezioWriteOut, so
    # disabling the JIBOT modules without dropping those in-memory recipes
    # terminates startup with "references unavailable extension". Preserve any
    # custom recipe that only uses non-JIBOT actions.
    recipes = getattr(config, "recipes", None)
    if recipes is not None:
        config.recipes = [
            recipe
            for recipe in recipes
            if not any(
                str(getattr(step, "extension", ""))
                in JIBOT_ONLY_RECIPE_EXTENSIONS
                for step in (
                    *(getattr(recipe, "steps", ()) or ()),
                    *(getattr(recipe, "cleanup", ()) or ()),
                )
            )
        ]
    charge_circuit = getattr(config, "charge_circuit", None)
    if charge_circuit is not None:
        charge_circuit.enabled = False
    video = getattr(config, "video", None)
    if video is not None:
        video.enabled = False

    # develop (14) validates enabled joystick slot actions during Adapter
    # construction.  The shared JIBOT profile references clamp/facility actions
    # that are intentionally disabled for SEER, so leaving that profile enabled
    # would abort SEER startup before the TCP vehicle bridge is attached.  SEER
    # manual driving is provided by the WebUI/manualDrive path instead.
    joystick = getattr(config, "joystick", None)
    if joystick is not None:
        joystick.enabled = False
        if hasattr(joystick, "actions_enabled"):
            joystick.actions_enabled = False

    # Reuse the adapter's manualDrive envelope with SEER-native linear speed
    # (m/s) and operator-facing angular speed (deg/s), in memory only.
    manual_control = getattr(config, "manual_control", None)
    if manual_control is not None:
        limits = ManualDriveLimits.from_env()
        manual_control.drive_trans = limits.default_linear_mps
        manual_control.drive_rot = limits.default_angular_deg_s
        manual_control.drive_speed = limits.default_linear_mps
        # The shared Adapter's JIBOT watchdog defaults to 800 ms.  A 650 ms
        # browser heartbeat leaves only 150 ms of scheduling/network margin and
        # becomes visibly stop/go once multiple SEER members are being polled.
        # SEER API 2010 already has its own finite motion duration, so use a
        # wider adapter-side fail-safe while the UI refreshes at 250 ms.
        manual_control.watchdog_ms = max(
            int(getattr(manual_control, "watchdog_ms", 0) or 0),
            int(limits.motion_duration_ms) + 800,
        )

    # ZIP (5) defaults nearest-node handling to JIBOT map PathPoint geometry.
    # SEER does not expose that JIBOT-only map category through the supplied
    # Robokit APIs, so use addressable/order nodes for this process only.  The
    # on-disk config.toml remains byte-for-byte unchanged.
    settings = getattr(config, "settings", None)
    nearest_mode = str(
        getattr(settings, "nearest_node_mode", "") or ""
    ).strip().lower()
    if settings is not None:
        if nearest_mode in {
            "pathpoint",
            "path_point",
            "path-point",
        }:
            settings.nearest_node_mode = "headingGoal"
        # order_motor_auto_enable is a JIBOT-specific pre-dispatch guard.  On
        # SEER, STATE firmware variants can report the electric/motor flag
        # differently and the guard can reject Path Nav before API 3051 is ever
        # sent (especially noticeable when two IPs are polled independently).
        # Let the SEER controller itself accept/reject 3051 instead.  Explicit
        # Enable/Disable Motor actions remain available when motor_names exist.
        if hasattr(settings, "order_motor_auto_enable"):
            settings.order_motor_auto_enable = False


def _position_to_native(value: float, unit: str) -> float:
    return float(value) * {"m": 1.0, "cm": 0.01, "mm": 0.001}[unit.lower()]


def _angle_to_native(value: float, unit: str) -> float:
    import math

    numeric = float(value)
    return math.radians(numeric) if unit.lower() == "deg" else numeric


def _pose_from_value(value: Any) -> Optional[Tuple[float, float, float]]:
    if isinstance(value, Mapping):
        pose = value.get("nodePosition") or value.get("pose") or value
        if isinstance(pose, Mapping) and "x" in pose and "y" in pose:
            return (
                float(pose["x"]),
                float(pose["y"]),
                float(pose.get("theta", pose.get("th", pose.get("angle", 0.0)))),
            )
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1]), float(value[2] if len(value) > 2 else 0.0)
    return None


def _map_nodes(initial_map: Any) -> Dict[str, Tuple[float, float, float]]:
    if not isinstance(initial_map, Mapping):
        return {}
    raw_nodes = initial_map.get("nodes")
    result: Dict[str, Tuple[float, float, float]] = {}
    if isinstance(raw_nodes, Mapping):
        iterable = raw_nodes.items()
    elif isinstance(raw_nodes, list):
        iterable = (
            (
                str(item.get("id", item.get("name", item.get("node_id", "")))),
                item,
            )
            for item in raw_nodes
            if isinstance(item, Mapping)
        )
    else:
        return result
    for node_id, value in iterable:
        pose = _pose_from_value(value)
        if node_id and pose is not None:
            result[str(node_id)] = pose
    return result


class SeerAdapterClient(SeerClient):
    """Real SEER client accepting the constructor shape used for JIBOT."""

    def __init__(
        self,
        robot_ip: str,
        robot_port: int = 7273,
        **kwargs: Any,
    ) -> None:
        del robot_port
        config = kwargs.get("config")
        _disable_jibot_only_runtime_sources(config)
        position_unit, orientation_unit = _adapter_units(config)
        super().__init__(
            robot_ip,
            port_map=_port_map(),
            motor_names=_motor_names(),
            config=config,
            command_timeout=_env_float(
                "SEER_COMMAND_TIMEOUT_SEC", float(kwargs.get("command_timeout", 3.0))
            ),
            recv_chunk_bytes=_env_int(
                "SEER_RECV_BUFFER_BYTES", int(kwargs.get("recv_buffer_bytes", 4096))
            ),
            status_poll_interval_sec=_env_float("SEER_STATUS_POLL_INTERVAL_SEC", 0.2),
            status_log_interval_sec=float(kwargs.get("status_log_interval_sec", 5.0)),
            battery_log_interval_sec=float(kwargs.get("battery_log_interval_sec", 30.0)),
            allow_legacy_echo_response=_env_bool(
                "SEER_ALLOW_LEGACY_ECHO_RESPONSE", False
            ),
            protocol_version=_env_int("SEER_PROTOCOL_VERSION", 1),
            min_request_interval_sec=_env_float(
                "SEER_MIN_REQUEST_INTERVAL_SEC", 0.1
            ),
            is_simulator=False,
            adapter_position_unit=position_unit,
            adapter_orientation_unit=orientation_unit,
        )


class SeerSimulatedAdapterClient(SeerClient):
    """SEER protocol simulator accepting ``SimulatedJIBOT`` constructor args."""

    def __init__(
        self,
        robot_ip: str = "simulator",
        robot_port: int = 7273,
        *,
        config: Any = None,
        initial_position: Any = None,
        initial_map: Any = None,
        initial_battery: Any = None,
        initial_charging: bool = False,
        **kwargs: Any,
    ) -> None:
        del robot_ip, robot_port, kwargs
        _disable_jibot_only_runtime_sources(config)
        position_unit, orientation_unit = _adapter_units(config)
        nodes = _map_nodes(initial_map)
        pose = _pose_from_value(initial_position)
        if pose is None and nodes:
            pose = random.choice(list(nodes.values()))
        if pose is None:
            pose = (0.0, 0.0, 0.0)
        native_pose = (
            _position_to_native(pose[0], position_unit),
            _position_to_native(pose[1], position_unit),
            _angle_to_native(pose[2], orientation_unit),
        )
        native_nodes = {
            node_id: (
                _position_to_native(node_pose[0], position_unit),
                _position_to_native(node_pose[1], position_unit),
                _angle_to_native(node_pose[2], orientation_unit),
            )
            for node_id, node_pose in nodes.items()
        }
        map_id = str(getattr(getattr(config, "settings", None), "map_id", "simulation"))
        battery = (
            random.uniform(30.0, 100.0)
            if initial_battery is None
            else max(0.0, min(100.0, float(initial_battery)))
        )
        self._simulator_server = SeerSimulatorServer(
            travel_time_sec=_env_float("SEER_SIM_TRAVEL_TIME_SEC", 0.25),
            initial_x=native_pose[0],
            initial_y=native_pose[1],
            initial_angle=native_pose[2],
            initial_battery=battery,
            initial_map=map_id,
            di_channel_count=_env_int("SEER_SIM_DI_CHANNELS", 24),
            do_channel_count=_env_int("SEER_SIM_DO_CHANNELS", 16),
            landmarks=native_nodes,
        )
        self._simulator_server.charging = bool(initial_charging)
        super().__init__(
            "127.0.0.1",
            port_map={},
            motor_names=_motor_names(),
            config=config,
            command_timeout=_env_float("SEER_COMMAND_TIMEOUT_SEC", 3.0),
            status_poll_interval_sec=_env_float("SEER_STATUS_POLL_INTERVAL_SEC", 0.05),
            allow_legacy_echo_response=False,
            protocol_version=_env_int("SEER_PROTOCOL_VERSION", 1),
            min_request_interval_sec=0.0,
            is_simulator=True,
            adapter_position_unit=position_unit,
            adapter_orientation_unit=orientation_unit,
        )
        self._map_nodes = dict(nodes)
        self._current_map = map_id

    async def connect_socket(self) -> None:
        self._port_map = dict(await self._simulator_server.start())
        await super().connect_socket()

    async def disconnect(self) -> None:
        try:
            await super().disconnect()
        finally:
            await self._simulator_server.stop()


async def _apply_seer_pause(adapter: Any, action_id: str, paused: bool) -> None:
    """Pause or resume the active SEER task without cancelling it.

    The shared JIBOT adapter implements ``startPause`` with ``stop_motion()``,
    which maps to a task cancellation in :class:`SeerClient`. SEER has native
    task pause/resume commands, so its runtime bridge must use API 3001/3002
    and leave API 3003 exclusively to an explicit cancel operation.
    """

    from protocol.vda_2_0_0.vda5050_2_0_0_state import (  # pyright: ignore[reportMissingImports]
        ActionStatus,
    )

    command_name = "pause_navigation" if paused else "resume_navigation"
    command = getattr(getattr(adapter, "_vehicle", None), command_name, None)
    action_name = "startPause" if paused else "stopPause"
    api = 3001 if paused else 3002
    request_publish = getattr(adapter, "request_state_publish", None)

    if not callable(command):
        adapter._update_instant_action_status(
            action_id,
            ActionStatus.FAILED,
            result_description="SEER vehicle is not connected",
        )
        if callable(request_publish):
            request_publish(f"SEER {action_name} failed")
        return

    try:
        await command()
    except Exception as exc:
        adapter._update_instant_action_status(
            action_id,
            ActionStatus.FAILED,
            result_description=f"SEER {action_name} failed: {exc}",
        )
        if callable(request_publish):
            request_publish(f"SEER {action_name} failed")
        print(f"[SEER {action_name.upper()} FAILED] actionId={action_id}: {exc}")
        return

    adapter._motion_paused = paused
    state = getattr(adapter, "state", None)
    if state is not None:
        state.paused = paused
        for action_state in getattr(state, "action_states", []) or []:
            if paused and action_state.action_status == ActionStatus.RUNNING:
                action_state.action_status = ActionStatus.PAUSED
            elif not paused and action_state.action_status == ActionStatus.PAUSED:
                action_state.action_status = ActionStatus.RUNNING

    state_text = "paused" if paused else "resumed"
    adapter._update_instant_action_status(
        action_id,
        ActionStatus.FINISHED,
        result_description=f"SEER task {state_text} (API {api})",
    )
    if callable(request_publish):
        request_publish(f"SEER task {state_text}")
    print(f"[SEER {action_name.upper()}] task {state_text} via API {api}")


def _install_seer_emergency_action() -> None:
    """Install SEER-only actions and native pause/resume on the Adapter."""

    from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
    from core.action_registry import (  # pyright: ignore[reportMissingImports]
        ActionParameterSpec,
        ActionResult,
        ActionSpec,
    )
    from protocol.vda_2_0_0.vda5050_2_0_0_state import (  # pyright: ignore[reportMissingImports]
        ActionStatus,
    )

    def handle_start_pause(self, action_id: str) -> None:
        self._update_instant_action_status(action_id, ActionStatus.RUNNING)
        self._run_on_adapter_loop(
            lambda: _apply_seer_pause(self, action_id, True)
        )

    def handle_stop_pause(self, action_id: str) -> None:
        self._update_instant_action_status(action_id, ActionStatus.RUNNING)
        self._run_on_adapter_loop(
            lambda: _apply_seer_pause(self, action_id, False)
        )

    def is_raw_jibot_command(self, action: Any) -> bool:
        # Adapter (8) checks raw JIBOT command aliases before consulting the
        # extension registry. A SEER process must never enter that path because
        # build_command/send_command_and_wait are vendor-specific JIBOT APIs.
        del self, action
        return False

    def command_from_jibot_action_type(self, action_type: str) -> Optional[str]:
        del self, action_type
        return None

    if not getattr(Adapter, "_seer_pause_handlers_installed", False):
        Adapter._handle_start_pause_instant_action = handle_start_pause
        Adapter._handle_stop_pause_instant_action = handle_stop_pause
        Adapter._is_jibot_command_instant_action = is_raw_jibot_command
        Adapter._command_from_jibot_action_type = command_from_jibot_action_type
        Adapter._seer_pause_handlers_installed = True

    # The shared VDA5050 Adapter executes every released node as an individual
    # goto and then waits for the robot to settle at that node.  That is correct
    # for JIBOT, but it defeats SEER API 3066: a plain multi-node VDA order turns
    # into stop -> orient -> start at every intermediate node.  Keep the VDA5050
    # order untouched and collapse only the SEER southbound motion into one 3066
    # request.  Intermediate VDA nodes are still completed by pose as the robot
    # passes them; only the final node waits for the navigation task to settle.
    if not getattr(Adapter, "_seer_continuous_vda_route_installed", False):
        original_send_node_motion = Adapter._send_node_motion
        original_wait_until_node_position_reached = Adapter._wait_until_node_position_reached
        original_settle_goto_arrival = Adapter._settle_goto_arrival
        original_clear_order_queue = Adapter._clear_order_queue

        def _active_continuous_route(self):
            active = getattr(self, "_seer_continuous_vda_route", None)
            order = getattr(self, "order", None)
            if not isinstance(active, dict) or order is None:
                return None
            if str(active.get("order_id", "")) != str(getattr(order, "order_id", "")):
                return None
            route = tuple(active.get("route", ()) or ())
            return active if len(route) >= 3 else None

        def _continuous_route_candidate(self, node):
            order = getattr(self, "order", None)
            state = getattr(self, "state", None)
            vehicle = getattr(self, "_vehicle", None)
            if order is None or state is None or vehicle is None:
                return ()

            nodes = sorted(
                tuple(getattr(order, "nodes", ()) or ()),
                key=lambda item: int(getattr(item, "sequence_id", 0)),
            )
            edges = sorted(
                tuple(getattr(order, "edges", ()) or ()),
                key=lambda item: int(getattr(item, "sequence_id", 0)),
            )
            if len(nodes) < 3:
                return ()
            if any(
                not bool(getattr(item, "released", False))
                or bool(getattr(item, "actions", ()) or ())
                for item in (*nodes, *edges)
            ):
                return ()

            # Preserve all special Adapter semantics. Dock/charge/move rules and
            # any order action still use the original per-node execution path.
            for item in nodes[1:]:
                node_id = str(getattr(item, "node_id", "") or "")
                if not node_id:
                    return ()
                try:
                    if self._dock_segment_rule(node_id) is not None:
                        return ()
                    if self._move_motion_rule(node_id) is not None:
                        return ()
                    if self._is_dock_work_node(node_id):
                        return ()
                except Exception:
                    return ()

            last_id = str(getattr(state, "last_node_id", "") or "").strip()
            try:
                last_seq = int(getattr(state, "last_node_sequence_id", 0) or 0)
            except (TypeError, ValueError):
                last_seq = 0
            start_index = next(
                (
                    index
                    for index, item in enumerate(nodes)
                    if str(getattr(item, "node_id", "") or "") == last_id
                    and int(getattr(item, "sequence_id", 0)) == last_seq
                ),
                None,
            )
            if start_index is None:
                station = str(getattr(vehicle, "_station", "") or "").strip()
                start_index = next(
                    (
                        index
                        for index, item in enumerate(nodes)
                        if str(getattr(item, "node_id", "") or "") == station
                    ),
                    None,
                )
            if start_index is None:
                return ()

            remaining = tuple(
                str(getattr(item, "node_id", "") or "").strip()
                for item in nodes[start_index:]
            )
            if len(remaining) < 3 or any(not value for value in remaining):
                return ()
            # The first queued motion node must be the first target after the
            # current/last VDA node. Otherwise the order is already mid-flight or
            # has a nonstandard prefix and must stay on the original safe path.
            if str(getattr(node, "node_id", "") or "") != remaining[1]:
                return ()
            return remaining

        async def seer_send_node_motion(self, node):
            active = _active_continuous_route(self)
            node_id = str(getattr(node, "node_id", "") or "")
            if active is not None and node_id in tuple(active.get("route", ()))[1:]:
                print(
                    f"[SEER VDA CONTINUOUS] reuse API 3066 order={active['order_id']} "
                    f"node={node_id}"
                )
                return None

            route = _continuous_route_candidate(self, node)
            route_method = getattr(getattr(self, "_vehicle", None), "path_navigation_route", None)
            if route and callable(route_method):
                order_id = str(getattr(getattr(self, "order", None), "order_id", "") or "")
                try:
                    await route_method(route, task_id=f"vda-{order_id}")
                except Exception as exc:
                    print(
                        f"[SEER VDA CONTINUOUS REJECTED] order={order_id} "
                        f"route={' -> '.join(route)} error={exc}"
                    )
                    return f"SEER continuous VDA route rejected: {exc}"
                order_obj = getattr(self, "order", None)
                target_by_id = {}
                for item in tuple(getattr(order_obj, "nodes", ()) or ()):
                    item_id = str(getattr(item, "node_id", "") or "")
                    if item_id not in route:
                        continue
                    try:
                        resolved = self._resolve_node_target(item)
                    except Exception:
                        resolved = None
                    if resolved is not None:
                        target_by_id[item_id] = tuple(resolved)
                self._seer_continuous_vda_route = {
                    "order_id": order_id,
                    "route": route,
                    "final_node": route[-1],
                    "targets": target_by_id,
                }
                print(
                    f"[SEER VDA CONTINUOUS] API 3066 order={order_id} "
                    f"route={' -> '.join(route)}"
                )
                return None

            return await original_send_node_motion(self, node)

        async def seer_wait_until_node_position_reached(self, node, target, poll_sec=None):
            active = _active_continuous_route(self)
            node_id = str(getattr(node, "node_id", "") or "")
            route = tuple(active.get("route", ()) or ()) if active is not None else ()
            if active is None or node_id not in route[1:-1]:
                return await original_wait_until_node_position_reached(
                    self, node, target, poll_sec=poll_sec
                )

            # API 3066 may smooth a corner and legitimately miss the shared
            # Adapter's narrow per-node reach zone.  For intermediate VDA nodes,
            # consider the node reached when the robot either enters the zone or
            # crosses the waypoint plane toward the next route point.  Never
            # resend an old single-node goto while the continuous task is active.
            if poll_sec is None:
                poll_sec = getattr(
                    self.config.settings, "node_position_poll_interval_sec", 0.2
                )
            tx, ty, deviation_xy = target
            effective = self._effective_reach_deviation_xy(deviation_xy)
            index = route.index(node_id)
            next_id = route[index + 1]
            targets = active.get("targets", {}) if isinstance(active, dict) else {}
            next_target = targets.get(next_id) if isinstance(targets, dict) else None
            min_distance = float("inf")
            pass_radius = max(float(effective) * 2.0, 350.0)

            while True:
                vehicle = getattr(self, "_vehicle", None)
                station = str(getattr(vehicle, "_station", "") or "").strip()
                if station == node_id or station in route[index + 1:]:
                    print(f"[SEER VDA CONTINUOUS NODE PASS] node={node_id} station={station}")
                    return None

                vx = self._optional_float(getattr(vehicle, "_x", None))
                vy = self._optional_float(getattr(vehicle, "_y", None))
                if vx is not None and vy is not None:
                    if self._pose_in_reach_zone(vx, vy, tx, ty, effective):
                        print(f"[SEER VDA CONTINUOUS NODE REACHED] node={node_id}")
                        return None
                    distance = ((vx - tx) ** 2 + (vy - ty) ** 2) ** 0.5
                    min_distance = min(min_distance, distance)
                    if next_target is not None:
                        nx, ny, _ndev = next_target
                        dx, dy = nx - tx, ny - ty
                        if abs(dx) + abs(dy) > 1e-9:
                            crossed = (vx - tx) * dx + (vy - ty) * dy >= 0.0
                            if crossed and min_distance <= pass_radius:
                                print(
                                    f"[SEER VDA CONTINUOUS NODE PASSED] node={node_id} "
                                    f"min_gap={min_distance:.1f}mm"
                                )
                                return None
                    # Curved/corner-cut paths may not cross the exact waypoint
                    # plane close enough.  Once the robot has approached within
                    # the relaxed pass radius and is clearly moving away, count
                    # the intermediate node as traversed.
                    if min_distance <= pass_radius and distance >= min_distance + 80.0:
                        print(
                            f"[SEER VDA CONTINUOUS NODE PASSED] node={node_id} "
                            f"corner_gap={min_distance:.1f}mm"
                        )
                        return None

                task_status = getattr(vehicle, "_task_status", None)
                if task_status in (5, 6):
                    raise RuntimeError(
                        f"SEER continuous route ended before intermediate node {node_id} "
                        f"(task_status={task_status})"
                    )
                if task_status == 4:
                    # The controller completed the 3066 route.  Every
                    # intermediate VDA node on that accepted route is therefore
                    # traversed even if corner smoothing skipped its exact pose.
                    print(
                        f"[SEER VDA CONTINUOUS NODE PASS] node={node_id} "
                        "controller route already complete"
                    )
                    return None
                await asyncio.sleep(float(poll_sec))

        async def seer_settle_goto_arrival(self, node):
            active = _active_continuous_route(self)
            node_id = str(getattr(node, "node_id", "") or "")
            if active is None or node_id not in tuple(active.get("route", ()))[1:]:
                return await original_settle_goto_arrival(self, node)

            final_node = str(active.get("final_node", "") or "")
            if node_id != final_node:
                print(
                    f"[SEER VDA CONTINUOUS PASS] node={node_id}; "
                    "no intermediate settle/orientation wait"
                )
                return None

            waiter = getattr(getattr(self, "_vehicle", None), "wait_navigation_terminal", None)
            try:
                if callable(waiter):
                    await waiter(expected_station=final_node, cancel_on_timeout=True)
                else:
                    await original_settle_goto_arrival(self, node)
            finally:
                self._seer_continuous_vda_route = None
            return None

        def seer_clear_order_queue(self, *args, **kwargs):
            clear_current_step = kwargs.get("clear_current_step", True)
            if clear_current_step:
                self._seer_continuous_vda_route = None
            return original_clear_order_queue(self, *args, **kwargs)

        Adapter._send_node_motion = seer_send_node_motion
        Adapter._wait_until_node_position_reached = seer_wait_until_node_position_reached
        Adapter._settle_goto_arrival = seer_settle_goto_arrival
        Adapter._clear_order_queue = seer_clear_order_queue
        Adapter._seer_continuous_vda_route_installed = True

    if getattr(Adapter, "_seer_emergency_action_installed", False):
        return
    original_init = Adapter.__init__
    original_build_factsheet = Adapter._build_factsheet

    def value_or_default(context, name: str, fallback: Any = "") -> Any:
        value = context.params.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            value = context.params.get(f"default_{name}", fallback)
        return value

    def finite_number(context, name: str, fallback: Any = "") -> float:
        value = float(value_or_default(context, name, fallback))
        if not math.isfinite(value):
            raise ValueError(f"SEER {name} must be a finite number")
        return value

    async def emergency_active(vehicle: Any) -> bool:
        emergency_getter = getattr(vehicle, "get_emergency_state", None)
        emergency_state = {}
        if callable(emergency_getter):
            emergency_state = await emergency_getter()
        active = bool(getattr(vehicle, "_emergency", False))
        if isinstance(emergency_state, Mapping):
            active = active or any(
                bool(emergency_state.get(key))
                for key in ("emergency", "driver_emc", "soft_emc")
            )
        return active

    async def toggle_emergency(context):
        vehicle = getattr(context.adapter, "_vehicle", None)
        if vehicle is None:
            return ActionResult(ActionStatus.FAILED, "SEER vehicle is not connected")
        requested = value_or_default(context, "status", None)
        if requested is None:
            toggle = getattr(vehicle, "toggle_soft_emergency", None)
            if not callable(toggle):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER vehicle does not support emergency toggle",
                )
            enabled = await toggle()
            publish_reason = "SEER software emergency toggled"
        else:
            normalized = str(requested).strip().lower()
            if normalized in {"1", "true", "on", "enable", "enabled"}:
                desired = True
            elif normalized in {"0", "false", "off", "release", "released"}:
                desired = False
            else:
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER emergency status must be on or off",
                )
            setter = getattr(vehicle, "set_soft_emergency", None)
            if not callable(setter):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER vehicle does not support explicit emergency control",
                )
            enabled = await setter(desired)
            publish_reason = (
                "SEER software emergency activated"
                if desired
                else "SEER software emergency released"
            )
        request_publish = getattr(context.adapter, "request_state_publish", None)
        if callable(request_publish):
            request_publish(publish_reason)
        state = "ON" if enabled else "RELEASED"
        return ActionResult(
            ActionStatus.FINISHED,
            f"SEER software emergency switch {state} (API 6004)",
        )

    async def path_navigation(context):
        vehicle = getattr(context.adapter, "_vehicle", None)
        if vehicle is None:
            return ActionResult(ActionStatus.FAILED, "SEER vehicle is not connected")
        target_id = str(value_or_default(context, "id", "")).strip()
        navigation_mode = str(
            value_or_default(context, "navigation_mode", "path") or "path"
        ).strip().lower()
        source_id = str(value_or_default(context, "source_id", "") or "").strip()
        auto_source = not source_id or source_id.upper() == "SELF_POSITION"
        task_id = str(value_or_default(context, "task_id", "") or "").strip()
        try:
            waypoint_delay_sec = finite_number(context, "waypoint_delay_sec", 0.0)
        except (TypeError, ValueError):
            return ActionResult(
                ActionStatus.FAILED,
                "SEER Path Nav waypoint delay must be numeric",
            )
        if not 0.0 <= waypoint_delay_sec <= 3600.0:
            return ActionResult(
                ActionStatus.FAILED,
                "SEER Path Nav waypoint delay must be 0..3600 seconds",
            )
        print(
            "[SEER PATH NAV ACTION] "
            f"mode={navigation_mode} source={(source_id or 'AUTO')!r} target={target_id!r} "
            f"task_prefix={task_id!r} waypoint_delay={waypoint_delay_sec:g}s"
        )
        if not target_id:
            return ActionResult(ActionStatus.FAILED, "SEER Path Nav target id is required")
        if len(target_id) > 128 or len(source_id) > 128 or len(task_id) > 128:
            return ActionResult(ActionStatus.FAILED, "SEER Path Nav point id is too long")

        if navigation_mode == "reentry":
            if not hasattr(vehicle, "free_nav"):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER vehicle does not support Free Nav re-entry",
                )
            route_points_value = value_or_default(context, "route_points", "")
            try:
                selected_route = _parse_route_points(route_points_value)
            except ValueError as exc:
                return ActionResult(ActionStatus.FAILED, str(exc))
            reentry_id = str(value_or_default(context, "reentry_id", "") or "").strip()
            if not reentry_id:
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER path re-entry requires a named entry point",
                )
            if not selected_route or selected_route[0] != reentry_id:
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER path re-entry route must start at the selected entry point",
                )
            if selected_route[-1] != target_id:
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER path re-entry route must end at the requested target id",
                )
            missing_edges = _missing_designated_route_edges(selected_route)
            if missing_edges:
                detail = ", ".join(
                    f"{source} -> {target}" for source, target in missing_edges
                )
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER path re-entry has no direct map connection: " + detail,
                )
            try:
                native_x = finite_number(context, "reentry_x")
                native_y = finite_number(context, "reentry_y")
                native_theta = finite_number(context, "reentry_theta")
            except (TypeError, ValueError):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER path re-entry requires numeric entry x, y and theta",
                )
            if not all(math.isfinite(value) for value in (native_x, native_y, native_theta)):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER path re-entry coordinates must be finite",
                )
            if await emergency_active(vehicle):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER path re-entry is blocked while emergency stop is active",
                )
            report = getattr(context, "report", None)
            if callable(report):
                report(
                    f"SEER path re-entry: Free Nav current pose -> {reentry_id}; then "
                    + " -> ".join(selected_route)
                )
            position_from_native = getattr(vehicle, "position_from_native", float)
            angle_from_native = getattr(vehicle, "angle_from_native", float)
            await vehicle.free_nav(
                position_from_native(native_x),
                position_from_native(native_y),
                angle_from_native(native_theta),
            )
            await vehicle.wait_navigation_terminal(
                expected_pose=(native_x, native_y, native_theta),
                cancel_on_timeout=True,
            )
            if len(selected_route) == 1:
                return ActionResult(
                    ActionStatus.FINISHED,
                    f"SEER Free Nav re-entered target point {target_id} (API 3051 freeGo)",
                )
            if await emergency_active(vehicle):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER Path Nav stopped after re-entry because emergency stop is active",
                )
            route_method = getattr(vehicle, "path_navigation_route", None)
            if not callable(route_method):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER vehicle does not support API 3066 after Free Nav re-entry",
                )
            active_route_path_value = str(
                os.getenv("SEER_ACTIVE_ROUTE_PATH", "") or ""
            ).strip()
            active_route_path = Path(active_route_path_value) if active_route_path_value else None
            active_route_token = None
            if active_route_path is not None:
                try:
                    active_route_token = write_active_route(active_route_path, selected_route)
                except (OSError, ValueError) as exc:
                    if callable(report):
                        report(f"SEER active route display unavailable: {exc}")
            try:
                await route_method(selected_route, task_id=task_id)
                await vehicle.wait_navigation_terminal(
                    expected_station=target_id,
                    cancel_on_timeout=True,
                )
            finally:
                if active_route_path is not None and active_route_token is not None:
                    clear_active_route(active_route_path, token=active_route_token)
            return ActionResult(
                ActionStatus.FINISHED,
                "SEER path re-entry reached: Free Nav -> "
                + " -> ".join(selected_route)
                + " (API 3051 + API 3066)",
            )

        if navigation_mode == "free":
            if not hasattr(vehicle, "free_nav"):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER vehicle does not support Free Nav",
                )
            try:
                native_x = finite_number(context, "free_nav_x")
                native_y = finite_number(context, "free_nav_y")
                native_theta = finite_number(context, "free_nav_theta")
            except (TypeError, ValueError):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER Free Nav requires numeric x, y and theta",
                )
            if not all(math.isfinite(value) for value in (native_x, native_y, native_theta)):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER Free Nav coordinates must be finite",
                )
            if await emergency_active(vehicle):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER Free Nav is blocked while emergency stop is active",
                )
            position_from_native = getattr(vehicle, "position_from_native", float)
            angle_from_native = getattr(vehicle, "angle_from_native", float)
            await vehicle.free_nav(
                position_from_native(native_x),
                position_from_native(native_y),
                angle_from_native(native_theta),
            )
            await vehicle.wait_navigation_terminal(
                expected_pose=(native_x, native_y, native_theta),
                cancel_on_timeout=True,
            )
            return ActionResult(
                ActionStatus.FINISHED,
                f"SEER Free Nav reached {target_id}: "
                f"({native_x:.3f}, {native_y:.3f}, {native_theta:.3f}) "
                "(API 3051 freeGo)",
            )
        if navigation_mode != "path":
            return ActionResult(
                ActionStatus.FAILED,
                "SEER navigation_mode must be path, free or reentry",
            )
        route_points_value = value_or_default(context, "route_points", "")
        print(f"[SEER PATH NAV ACTION] route_points={route_points_value!r}")
        try:
            selected_route = _parse_route_points(
                route_points_value
            )
        except ValueError as exc:
            print(f"[SEER PATH NAV ACTION FAILED] {exc}")
            return ActionResult(ActionStatus.FAILED, str(exc))
        if selected_route:
            print(
                "[SEER PATH NAV ACTION] designated_route="
                + " -> ".join(selected_route)
            )
        if selected_route and selected_route[-1] != target_id:
            return ActionResult(
                ActionStatus.FAILED,
                "SEER selected route must end at the requested target id",
            )

        if selected_route and len(selected_route) == 1:
            return ActionResult(
                ActionStatus.FINISHED,
                f"SEER Path Nav already at selected target: {target_id}",
            )
        missing_edges = _missing_designated_route_edges(selected_route)
        if missing_edges:
            detail = ", ".join(
                f"{source} -> {target}" for source, target in missing_edges
            )
            message = (
                "SEER API 3066 designated route has no direct map connection: "
                + detail
            )
            print(f"[SEER PATH NAV ACTION FAILED] {message}")
            return ActionResult(ActionStatus.FAILED, message)
        if await emergency_active(vehicle):
            return ActionResult(
                ActionStatus.FAILED,
                "SEER Path Nav is blocked while emergency stop is active",
            )

        if selected_route:
            if source_id == "SELF_POSITION" or selected_route[0] != source_id:
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER API 3066 selected route must start at the robot's current named point",
                )
            route_method = getattr(vehicle, "path_navigation_route", None)
            if not callable(route_method):
                return ActionResult(
                    ActionStatus.FAILED,
                    "SEER vehicle does not support API 3066 designated routes",
                )
            report = getattr(context, "report", None)
            if callable(report):
                report(
                    "SEER designated route API 3066: "
                    + " -> ".join(selected_route)
                    + f"; waypoint delay {waypoint_delay_sec:g}s"
                )
            active_route_path_value = str(
                os.getenv("SEER_ACTIVE_ROUTE_PATH", "") or ""
            ).strip()
            active_route_path = (
                Path(active_route_path_value) if active_route_path_value else None
            )
            active_route_token = None
            if active_route_path is not None:
                try:
                    active_route_token = write_active_route(
                        active_route_path, selected_route
                    )
                except (OSError, ValueError) as exc:
                    if callable(report):
                        report(f"SEER active route display unavailable: {exc}")
            try:
                # Always submit the complete designated route as one API 3066
                # request.  The controller can then blend/continue through
                # intermediate points instead of the Builder waiting for each
                # point and issuing a brand-new motion command.  Keep the old
                # waypoint_delay_sec field readable for stored Recipes, but do
                # not split the route anymore.
                if waypoint_delay_sec > 0.0 and callable(report):
                    report(
                        "SEER waypoint delay is ignored for continuous Path Nav; "
                        "the full route is sent as one API 3066 request"
                    )
                await route_method(selected_route, task_id=task_id)
                await vehicle.wait_navigation_terminal(
                    expected_station=target_id,
                    cancel_on_timeout=True,
                )
                description_suffix = " (continuous single API 3066 request)"
            except asyncio.TimeoutError as exc:
                # The navigation lifecycle timeout explicitly cancels the task,
                # so its marker is terminal. A socket/read timeout is unknown
                # state and must survive until reconnection reconciliation.
                if "navigation did not finish within" in str(exc).lower():
                    if active_route_path is not None and active_route_token is not None:
                        clear_active_route(active_route_path, token=active_route_token)
                else:
                    if callable(report):
                        report(
                            "SEER communication timeout; designated route display "
                            "retained until navigation state is known again"
                        )
                raise
            except (OSError, EOFError, ValueError):
                # The controller may keep executing the already accepted route.
                # Leave the marker intact; the WebUI reconciles it against live
                # state after reconnection and clears only on a confirmed stop.
                if callable(report):
                    report(
                        "SEER connection lost; designated route display retained "
                        "until navigation state is known again"
                    )
                raise
            else:
                if active_route_path is not None and active_route_token is not None:
                    clear_active_route(active_route_path, token=active_route_token)
            return ActionResult(
                ActionStatus.FINISHED,
                "SEER designated Path Nav reached: "
                + " -> ".join(selected_route)
                + description_suffix,
            )

        if not hasattr(vehicle, "path_navigation"):
            return ActionResult(ActionStatus.FAILED, "SEER vehicle is not connected")
        await vehicle.path_navigation(
            target_id,
            source_id=None if auto_source else source_id,
            task_id=task_id,
        )
        await vehicle.wait_navigation_terminal(
            expected_station=target_id,
            cancel_on_timeout=True,
        )
        source_label = "AUTO_CURRENT_POSITION" if auto_source else source_id
        return ActionResult(
            ActionStatus.FINISHED,
            f"SEER Path Nav reached: {source_label} -> {target_id} "
            "(API 3051 target-only auto route)",
        )

    async def coordinate_navigation(context):
        vehicle = getattr(context.adapter, "_vehicle", None)
        if vehicle is None or not hasattr(vehicle, "free_nav"):
            return ActionResult(ActionStatus.FAILED, "SEER vehicle is not connected")
        try:
            x_m = finite_number(context, "x", 0.0)
            y_m = finite_number(context, "y", 0.0)
            theta_deg = finite_number(context, "theta_deg", 0.0)
        except (TypeError, ValueError) as exc:
            return ActionResult(ActionStatus.FAILED, str(exc))
        if await emergency_active(vehicle):
            return ActionResult(
                ActionStatus.FAILED,
                "SEER coordinate navigation is blocked while emergency stop is active",
            )
        native_theta = math.radians(theta_deg)
        position_from_native = getattr(vehicle, "position_from_native", float)
        angle_from_native = getattr(vehicle, "angle_from_native", float)
        await vehicle.free_nav(
            position_from_native(x_m),
            position_from_native(y_m),
            angle_from_native(native_theta),
        )
        await vehicle.wait_navigation_terminal(
            expected_pose=(x_m, y_m, native_theta),
            cancel_on_timeout=True,
        )
        return ActionResult(
            ActionStatus.FINISHED,
            f"SEER coordinate navigation reached ({x_m:.3f}, {y_m:.3f}, "
            f"{theta_deg:.1f} deg) (API 3051 freeGo; speed uses controller profile)",
        )

    async def translate(context):
        vehicle = getattr(context.adapter, "_vehicle", None)
        if vehicle is None or not hasattr(vehicle, "move_distance"):
            return ActionResult(ActionStatus.FAILED, "SEER vehicle is not connected")
        try:
            distance_m = finite_number(context, "distance_m", 0.1)
            speed_mps = finite_number(context, "linear_speed_mps", 0.05)
        except (TypeError, ValueError) as exc:
            return ActionResult(ActionStatus.FAILED, str(exc))
        axis = str(value_or_default(context, "lateral", "forward") or "forward").lower()
        if axis not in ("forward", "lateral"):
            return ActionResult(ActionStatus.FAILED, "SEER lateral must be forward or lateral")
        limits = ManualDriveLimits.from_env().validated()
        if not 0.01 <= abs(speed_mps) <= limits.max_linear_mps:
            return ActionResult(
                ActionStatus.FAILED,
                f"SEER linear speed must be 0.01..{limits.max_linear_mps:g} m/s",
            )
        if await emergency_active(vehicle):
            return ActionResult(
                ActionStatus.FAILED,
                "SEER translation is blocked while emergency stop is active",
            )
        position_from_native = getattr(vehicle, "position_from_native", float)
        await vehicle.move_distance(
            position_from_native(distance_m),
            position_from_native(abs(speed_mps)),
            lateral=axis == "lateral",
        )
        await vehicle.wait_navigation_terminal(cancel_on_timeout=True)
        return ActionResult(
            ActionStatus.FINISHED,
            f"SEER {'lateral' if axis == 'lateral' else 'straight'} translation "
            f"{distance_m:.3f} m at {abs(speed_mps):.3f} m/s (API 3055)",
        )

    async def turn(context):
        vehicle = getattr(context.adapter, "_vehicle", None)
        if vehicle is None or not hasattr(vehicle, "turn"):
            return ActionResult(ActionStatus.FAILED, "SEER vehicle is not connected")
        try:
            angle_deg = finite_number(context, "angle_deg", 90.0)
            speed_deg_s = finite_number(context, "angular_speed_deg_s", 5.0)
        except (TypeError, ValueError) as exc:
            return ActionResult(ActionStatus.FAILED, str(exc))
        print(
            f"[SEER TURN ACTION] angle={angle_deg:g}deg "
            f"angular_speed={abs(speed_deg_s):g}deg/s"
        )
        limits = ManualDriveLimits.from_env().validated()
        if not 1.0 <= abs(speed_deg_s) <= limits.max_angular_deg_s:
            return ActionResult(
                ActionStatus.FAILED,
                f"SEER angular speed must be 1..{limits.max_angular_deg_s:g} deg/s",
            )
        if await emergency_active(vehicle):
            return ActionResult(
                ActionStatus.FAILED,
                "SEER turn is blocked while emergency stop is active",
            )
        angle_from_native = getattr(vehicle, "angle_from_native", float)
        await vehicle.turn(
            angle_from_native(math.radians(angle_deg)),
            angle_from_native(math.radians(abs(speed_deg_s))),
        )
        await vehicle.wait_navigation_terminal(cancel_on_timeout=True)
        return ActionResult(
            ActionStatus.FINISHED,
            f"SEER turn {angle_deg:.1f} deg at {abs(speed_deg_s):.1f} deg/s (API 3056)",
        )

    async def wait_step(context):
        try:
            seconds = finite_number(context, "seconds", 1.0)
        except (TypeError, ValueError) as exc:
            return ActionResult(ActionStatus.FAILED, str(exc))
        if not 0.0 <= seconds <= 3600.0:
            return ActionResult(ActionStatus.FAILED, "SEER wait must be 0..3600 seconds")
        await asyncio.sleep(seconds)
        return ActionResult(ActionStatus.FINISHED, f"SEER recipe waited {seconds:g}s")

    async def camera_docking(context, *, live: bool):
        """Run RealSense/AprilTag docking as a normal SEER Action.

        The docking loop deliberately reuses ``context.adapter._vehicle``.  It
        never creates a second SEER TCP client, so API 2010/2000 share the same
        persistent CONTROL connection and STATE freshness/health owned by the
        normal ``seer_client``.
        """

        vehicle = getattr(context.adapter, "_vehicle", None)
        if vehicle is None or not hasattr(vehicle, "control"):
            return ActionResult(ActionStatus.FAILED, "SEER vehicle is not connected")
        try:
            runtime_default = 300.0
            max_runtime_s = finite_number(context, "max_runtime_s", runtime_default)
        except (TypeError, ValueError) as exc:
            return ActionResult(ActionStatus.FAILED, str(exc))
        if not 1.0 <= max_runtime_s <= 1800.0:
            return ActionResult(
                ActionStatus.FAILED,
                "SEER camera docking max_runtime_s must be 1..1800 seconds",
            )

        stage_mode = str(value_or_default(context, "stage_mode", "") or "").strip().lower()
        if stage_mode not in {"", "centerline", "yaw", "straight"}:
            return ActionResult(
                ActionStatus.FAILED,
                "SEER camera docking stage_mode must be centerline, yaw, or straight",
            )
        if stage_mode and not live:
            return ActionResult(ActionStatus.FAILED, "manual docking stages require LIVE mode")

        adapter = context.adapter
        action_id = str(getattr(context.action, "action_id", "") or "camera-docking")

        try:
            # Heavy camera dependencies stay optional for users who do not use
            # Camera Docking.  Import them only when the Action actually runs.
            from .docking_action import default_config_path, run_camera_docking
        except Exception as exc:
            return ActionResult(
                ActionStatus.FAILED,
                "SEER Camera Docking dependencies are unavailable; install "
                f"seer_client[docking]. Detail: {type(exc).__name__}: {exc}",
            )

        # PREVIEW and LIVE both need exclusive ownership of the same RealSense.
        # When the operator presses LIVE while PREVIEW is running, treat that as
        # an intentional handoff instead of starting a second camera pipeline.
        # Cancel and *await cleanup* of only the preview task before LIVE opens
        # the camera.  Never cancel a LIVE action or an unrelated action here.
        active = getattr(adapter, "_seer_active_instant_tasks", None)
        active = active if isinstance(active, dict) else {}
        current_task = asyncio.current_task()
        preview_tasks = []
        live_tasks = []
        for other_id, item in list(active.items()):
            if str(other_id) == action_id or not isinstance(item, dict):
                continue
            task = item.get("task")
            action_type = str(item.get("action_type", "") or "")
            if task is None or task is current_task or task.done():
                continue
            if action_type == "seerCameraDockPreview":
                preview_tasks.append((str(other_id), task))
            elif action_type == "seerCameraDock":
                live_tasks.append((str(other_id), task))

        if live_tasks:
            return ActionResult(
                ActionStatus.FAILED,
                f"SEER Camera Docking LIVE is already active ({live_tasks[0][0]})",
            )
        if not live and preview_tasks:
            return ActionResult(
                ActionStatus.FAILED,
                f"SEER Camera Docking PREVIEW is already active ({preview_tasks[0][0]})",
            )
        if live and preview_tasks:
            report = getattr(context, "report", None)
            if callable(report):
                report("SEER Camera Docking PREVIEW -> LIVE handoff: closing preview camera first")
            for preview_id, task in preview_tasks:
                try:
                    adapter._update_instant_action_status(
                        preview_id,
                        ActionStatus.FINISHED,
                        "Replaced by Camera Docking LIVE",
                    )
                except Exception:
                    pass
                task.cancel()
            for preview_id, task in preview_tasks:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=4.0)
                except asyncio.CancelledError:
                    # A cancelled PREVIEW completes by raising CancelledError.
                    # If the LIVE action itself was cancelled while waiting, do
                    # not swallow that cancellation and accidentally start motion.
                    if current_task is not None and current_task.cancelling():
                        raise
                except asyncio.TimeoutError:
                    return ActionResult(
                        ActionStatus.FAILED,
                        "Camera PREVIEW did not release RealSense within 4 seconds; "
                        "LIVE was not started",
                    )
                except Exception:
                    # A failed preview still runs its finally cleanup.  Once the
                    # task is terminal it is safe for LIVE to acquire the lease.
                    pass
            await asyncio.sleep(0.05)

        active_id = str(getattr(adapter, "_seer_camera_dock_action_id", "") or "")
        if active_id and active_id != action_id:
            # A task that is no longer present in the tracked registry is stale
            # bookkeeping. Clear it after the awaited PREVIEW handoff; otherwise
            # preserve the exclusivity guard.
            active_ids = {str(key) for key in active.keys()}
            if active_id not in active_ids:
                adapter._seer_camera_dock_action_id = ""
            else:
                return ActionResult(
                    ActionStatus.FAILED,
                    f"SEER camera docking is already active ({active_id})",
                )

        adapter._seer_camera_dock_action_id = action_id
        mode_text = (f"LIVE/STAGE-{stage_mode.upper()}" if stage_mode else ("LIVE" if live else "PREVIEW"))
        report = getattr(context, "report", None)
        if callable(report):
            report(f"SEER Camera Docking {mode_text} starting")
        try:
            result = await run_camera_docking(
                vehicle,
                live=live,
                action_id=action_id,
                max_runtime_s=max_runtime_s,
                config_path=default_config_path(),
                manual_stage=stage_mode or None,
            )
        except asyncio.CancelledError:
            # ``seerCancelActiveAction`` cancels this coroutine.  The docking
            # runtime's finally block issues API 2000/zero through SeerClient.
            raise
        finally:
            if str(getattr(adapter, "_seer_camera_dock_action_id", "") or "") == action_id:
                adapter._seer_camera_dock_action_id = ""

        if live and not result.success:
            return ActionResult(
                ActionStatus.FAILED,
                f"SEER Camera Docking {result.phase}: {result.message}",
            )
        return ActionResult(
            ActionStatus.FINISHED,
            f"SEER Camera Docking {mode_text} {result.phase}: {result.message}",
        )

    async def camera_dock_preview(context):
        return await camera_docking(context, live=False)

    async def camera_dock_live(context):
        return await camera_docking(context, live=True)

    async def refresh_map_action(context):
        """Force one live map download for the Adapter that received this action."""

        vehicle = getattr(context.adapter, "_vehicle", None)
        refresher = getattr(vehicle, "refresh_map_cache", None) if vehicle is not None else None
        if not callable(refresher):
            return ActionResult(ActionStatus.FAILED, "SEER vehicle map refresh is unavailable")
        try:
            raw = await refresher()
        except Exception as exc:
            return ActionResult(ActionStatus.FAILED, f"SEER map refresh failed: {exc}")
        map_name = ""
        if isinstance(raw, Mapping):
            header = raw.get("header")
            if isinstance(header, Mapping):
                map_name = str(
                    header.get("mapName")
                    or header.get("map_name")
                    or header.get("name")
                    or ""
                ).strip()
        return ActionResult(
            ActionStatus.FINISHED,
            "SEER map cache refreshed" + (f" · {map_name}" if map_name else ""),
        )

    async def set_digital_output(context):
        vehicle = getattr(context.adapter, "_vehicle", None)
        if vehicle is None or not hasattr(vehicle, "set_do"):
            return ActionResult(ActionStatus.FAILED, "SEER vehicle is not connected")
        try:
            channel_id = int(value_or_default(context, "id", 0))
        except (TypeError, ValueError):
            return ActionResult(ActionStatus.FAILED, "SEER DO id must be an integer")
        if not 0 <= channel_id <= 65535:
            return ActionResult(ActionStatus.FAILED, "SEER DO id is out of range")
        raw_status = str(value_or_default(context, "status", "off")).strip().lower()
        if raw_status not in {"true", "false", "1", "0", "on", "off"}:
            return ActionResult(ActionStatus.FAILED, "SEER DO status must be true/false")
        enabled = raw_status in {"true", "1", "on"}
        await vehicle.set_do(channel_id, enabled)
        # Refresh API 1013 immediately so the redirected IO page reflects the
        # controller-confirmed output instead of only the requested value.
        io_service = getattr(vehicle, "io", None)
        if io_service is not None and hasattr(io_service, "query"):
            await io_service.query()
        return ActionResult(
            ActionStatus.FINISHED,
            f"SEER DO{channel_id} {'ON' if enabled else 'OFF'} (API 6001)",
        )

    def _jack_wait_settings() -> tuple[float, float]:
        try:
            timeout = float(os.getenv("SEER_JACK_TASK_TIMEOUT_SEC", "30") or 30)
        except (TypeError, ValueError):
            timeout = 30.0
        try:
            poll = float(os.getenv("SEER_JACK_TASK_POLL_SEC", "0.3") or 0.3)
        except (TypeError, ValueError):
            poll = 0.3
        return max(1.0, min(300.0, timeout)), max(0.05, min(2.0, poll))

    async def _wait_jack_di2_down(vehicle, *, timeout_sec: float = 3.0) -> bool:
        reader = getattr(vehicle, "read_di", None)
        if not callable(reader):
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, float(timeout_sec))
        while loop.time() < deadline:
            try:
                if bool(await reader(2)):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.1)
        return False

    def _tracked_instant_handler(handler):
        """Track cancellable SEER instant-action coroutines on the Adapter.

        Order-owned actions are deliberately excluded: VDA5050 ``cancelOrder``
        owns their lifecycle.  The WebUI's Action Cancel button only targets
        standalone/instant SEER actions such as Jack and Block Builder recipes.
        """

        async def tracked(context):
            result = handler(context)
            if not inspect.isawaitable(result):
                return result
            owner_order_id = getattr(context.action, "_owner_order_id", None)
            if owner_order_id:
                return await result
            adapter = context.adapter
            task = asyncio.current_task()
            action_id = str(getattr(context.action, "action_id", "") or "")
            action_type = str(getattr(context.action, "action_type", "") or "")
            active = getattr(adapter, "_seer_active_instant_tasks", None)
            if not isinstance(active, dict):
                active = {}
                setattr(adapter, "_seer_active_instant_tasks", active)
            if task is not None and action_id:
                active[action_id] = {"task": task, "action_type": action_type}
            try:
                return await result
            finally:
                if action_id and active.get(action_id, {}).get("task") is task:
                    active.pop(action_id, None)

        return tracked

    async def cancel_active_action(context):
        """Cancel standalone SEER Action/Recipe and the controller task."""

        adapter = context.adapter
        if getattr(adapter, "order", None) is not None:
            return ActionResult(
                ActionStatus.FAILED,
                "VDA5050 order is active; use Order 취소(cancelOrder) instead",
            )

        active = getattr(adapter, "_seer_active_instant_tasks", None)
        entries = list(active.items()) if isinstance(active, dict) else []
        current = asyncio.current_task()
        cancelled_ids = []
        for action_id, item in entries:
            task = item.get("task") if isinstance(item, dict) else None
            if task is None or task is current or task.done():
                continue
            cancelled_ids.append(str(action_id))
            try:
                adapter._update_instant_action_status(
                    str(action_id),
                    ActionStatus.FINISHED,
                    "Cancelled by operator",
                )
            except Exception:
                pass
            task.cancel()

        vehicle = getattr(adapter, "_vehicle", None)
        navigation = getattr(vehicle, "navigation", None) if vehicle is not None else None
        cancel_task = getattr(navigation, "cancel", None)
        controller_message = ""
        if callable(cancel_task):
            try:
                await cancel_task()
                controller_message = "SEER TASK_CANCEL sent"
            except Exception as exc:
                controller_message = f"SEER TASK_CANCEL failed: {exc}"

        if entries:
            await asyncio.gather(
                *(
                    item.get("task")
                    for _action_id, item in entries
                    if isinstance(item, dict)
                    and item.get("task") is not None
                    and item.get("task") is not current
                ),
                return_exceptions=True,
            )

        cache_path = cache_path_from_env()
        write_jack_status(
            cache_path,
            phase="unknown",
            operation="",
            message="사용자가 현재 Action을 취소했습니다",
        )
        details = ", ".join(cancelled_ids) if cancelled_ids else "tracked action 없음"
        if controller_message:
            details += f"; {controller_message}"
        return ActionResult(ActionStatus.FINISHED, f"Action cancel complete: {details}")

    async def reset_action_errors(context):
        """Clear stale Action outcomes/errors and failed Builder runtime UI."""

        adapter = context.adapter
        state = getattr(adapter, "state", None)
        if state is not None:
            if hasattr(state, "instant_action_states"):
                state.instant_action_states = []
            if hasattr(state, "errors"):
                state.errors = []
            # Completed/failed order actionStates belong to an order.  Only
            # clear them when there is no active order, otherwise the FMS must
            # retain the current order's action lifecycle.
            if getattr(adapter, "order", None) is None and hasattr(state, "action_states"):
                state.action_states = []

        runtime_path = str(os.getenv("SEER_BLOCK_RUNTIME_PATH", "") or "").strip()
        if runtime_path:
            path = Path(runtime_path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict) or str(payload.get("status", "")) != "running":
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

        write_jack_status(
            cache_path_from_env(),
            phase="unknown",
            operation="",
            message="Action 오류/기록을 초기화했습니다",
        )
        request_publish = getattr(adapter, "request_state_publish", None)
        if callable(request_publish):
            request_publish("SEER action errors reset")
        return ActionResult(
            ActionStatus.FINISHED,
            "Cleared Action states, sticky errors, and stale Block Builder failure state",
        )

    def _jack_capability_error(vehicle) -> Optional[str]:
        if vehicle is None:
            return "SEER vehicle is not connected"
        supported = getattr(vehicle, "_jack_supported", None)
        model = str(getattr(vehicle, "_robot_model", "") or "").strip()
        suffix = f" ({model})" if model else ""
        reason = str(getattr(vehicle, "_jack_capability_reason", "") or "").strip()
        detail = f": {reason}" if reason else ""
        if supported is False:
            return f"이 AMR은 Jack 기능이 비활성/미지원입니다{suffix}{detail}"
        if supported is not True:
            return f"Jack 호환 여부가 확인되지 않아 동작을 차단했습니다{suffix}{detail}"
        return None

    async def jack_load_action(context):
        vehicle = getattr(context.adapter, "_vehicle", None)
        capability_error = _jack_capability_error(vehicle)
        if capability_error:
            return ActionResult(ActionStatus.FAILED, capability_error)
        method = getattr(vehicle, "jack_load", None) if vehicle is not None else None
        navigation = getattr(vehicle, "navigation", None) if vehicle is not None else None
        waiter = getattr(navigation, "wait_jack_until_terminal", None)
        if not callable(method) or not callable(waiter):
            return ActionResult(ActionStatus.FAILED, "SEER Jack Load is unavailable")

        cache_path = cache_path_from_env()
        write_jack_status(
            cache_path, phase="moving_up", operation="JackLoad",
            message="Jack 상승 명령 전송 · 완료 신호 대기",
        )
        try:
            await method()
            timeout, poll = _jack_wait_settings()
            task = await waiter(
                "JackLoad", timeout_sec=timeout, poll_interval_sec=poll
            )
            operation, operation_status, task_status = navigation._jack_task_details(task)
        except asyncio.CancelledError:
            write_jack_status(
                cache_path, phase="unknown", operation="JackLoad",
                message="Jack 상승 Action이 사용자에 의해 취소되었습니다",
            )
            raise
        except SeerNavigationTaskError as exc:
            if getattr(exc, "task_status", None) == 6:
                write_jack_status(
                    cache_path, phase="unknown", operation="JackLoad",
                    message="Jack 상승 Action이 취소되었습니다",
                )
                return ActionResult(ActionStatus.FINISHED, "SEER Jack Load cancelled")
            write_jack_status(
                cache_path, phase="failed", operation="JackLoad", message=str(exc)
            )
            return ActionResult(ActionStatus.FAILED, f"SEER Jack Load failed: {exc}")
        except Exception as exc:
            write_jack_status(
                cache_path, phase="failed", operation="JackLoad", message=str(exc)
            )
            return ActionResult(ActionStatus.FAILED, f"SEER Jack Load failed: {exc}")

        write_jack_status(
            cache_path,
            phase="up",
            operation=operation or "JackLoad",
            task_status=task_status,
            operation_status=operation_status,
            message="Jack 상승 완료",
        )
        return ActionResult(
            ActionStatus.FINISHED,
            "SEER Jack Load completed (task_status=4, operation_status=3)",
        )

    async def jack_unload_action(context):
        vehicle = getattr(context.adapter, "_vehicle", None)
        capability_error = _jack_capability_error(vehicle)
        if capability_error:
            return ActionResult(ActionStatus.FAILED, capability_error)
        method = getattr(vehicle, "jack_unload", None) if vehicle is not None else None
        navigation = getattr(vehicle, "navigation", None) if vehicle is not None else None
        waiter = getattr(navigation, "wait_jack_until_terminal", None)
        if not callable(method) or not callable(waiter):
            return ActionResult(ActionStatus.FAILED, "SEER Jack Unload is unavailable")

        cache_path = cache_path_from_env()
        operation_name = "JackUnLoadAndResetShelf"
        write_jack_status(
            cache_path, phase="moving_down", operation=operation_name,
            message="Jack 하강 명령 전송 · 완료 신호 대기",
        )
        try:
            await method()
            timeout, poll = _jack_wait_settings()
            task = await waiter(
                operation_name, timeout_sec=timeout, poll_interval_sec=poll
            )
            operation, operation_status, task_status = navigation._jack_task_details(task)
            # DI2 is the physical lower-limit signal on the tested vehicle.
            # Require it after the task completion so a recipe never continues
            # while the jack is still above the fully-down position.
            if not await _wait_jack_di2_down(vehicle):
                raise RuntimeError("Jack task completed but DI2 did not become ON")
        except asyncio.CancelledError:
            write_jack_status(
                cache_path, phase="unknown", operation=operation_name,
                message="Jack 하강 Action이 사용자에 의해 취소되었습니다",
            )
            raise
        except SeerNavigationTaskError as exc:
            if getattr(exc, "task_status", None) == 6:
                write_jack_status(
                    cache_path, phase="unknown", operation=operation_name,
                    message="Jack 하강 Action이 취소되었습니다",
                )
                return ActionResult(ActionStatus.FINISHED, "SEER Jack Unload cancelled")
            write_jack_status(
                cache_path, phase="failed", operation=operation_name, message=str(exc)
            )
            return ActionResult(ActionStatus.FAILED, f"SEER Jack Unload failed: {exc}")
        except Exception as exc:
            write_jack_status(
                cache_path, phase="failed", operation=operation_name, message=str(exc)
            )
            return ActionResult(ActionStatus.FAILED, f"SEER Jack Unload failed: {exc}")

        write_jack_status(
            cache_path,
            phase="down",
            operation=operation or operation_name,
            task_status=task_status,
            operation_status=operation_status,
            message="Jack 하강 완료 · DI2 ON",
        )
        return ActionResult(
            ActionStatus.FINISHED,
            "SEER Jack Unload completed (task_status=4, operation_status=3, DI2=ON)",
        )

    @wraps(original_init)
    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        configured = {
            str(getattr(action, "action_type", "")): bool(
                getattr(action, "enabled", True)
            )
            for action in getattr(self.config, "actions", ())
        }

        def install(
            spec: ActionSpec, *, configurable: bool = True, track: bool = True
        ) -> None:
            enabled = configured.get(spec.action_type, True) if configurable else True
            if track and spec.handler is not None:
                spec = ActionSpec(
                    action_type=spec.action_type,
                    enabled=spec.enabled,
                    runner=spec.runner,
                    handler=_tracked_instant_handler(spec.handler),
                    cancel_handler=spec.cancel_handler,
                    module=spec.module,
                    command=list(spec.command),
                    timeout_sec=spec.timeout_sec,
                    motion=spec.motion,
                    snapshot_fields=list(spec.snapshot_fields),
                    label=spec.label,
                    parameters=spec.parameters,
                )
            registry_specs = getattr(self._action_registry, "_specs", None)
            if not isinstance(registry_specs, dict):
                if enabled and not self._action_registry.has(spec.action_type):
                    self._action_registry.register(spec)
                return
            if enabled:
                # HCL action blocks are registered by the unchanged Adapter
                # before the SEER vehicle bridge is installed. Replace that
                # schema placeholder with the real SEER TCP/IP handler so
                # Recipes execute the vendor command instead of a no-op.
                registry_specs[spec.action_type] = spec
            else:
                registry_specs.pop(spec.action_type, None)

        install(
            ActionSpec(
                action_type="seerEmergencySwitch",
                label="SEER software emergency switch",
                motion=False,
                timeout_sec=5.0,
                handler=toggle_emergency,
                parameters=(
                    ActionParameterSpec(
                        "status",
                        placeholder="optional: on/off; empty toggles",
                        choices=("on", "off"),
                    ),
                ),
            ),
            configurable=False,
        )
        install(
            ActionSpec(
                action_type="seerPathNav",
                label="SEER Path Navigation",
                motion=True,
                # The vehicle applies its own timeout to every navigation leg.
                # Keep the outer action unbounded so an operator-selected pause
                # between waypoints is not cut off by the single-route timeout.
                timeout_sec=0.0,
                handler=path_navigation,
                parameters=(
                    ActionParameterSpec("id", required=True, placeholder="LM1"),
                    ActionParameterSpec(
                        "navigation_mode", required=True, choices=("path", "free", "reentry")
                    ),
                    ActionParameterSpec("source_id", placeholder="SELF_POSITION"),
                    ActionParameterSpec("task_id", placeholder="optional task id"),
                    ActionParameterSpec(
                        "route_points",
                        placeholder='["LM1", "LM5", "LM4"]',
                        value_type="json",
                    ),
                    ActionParameterSpec("free_nav_x", input_type="number"),
                    ActionParameterSpec("free_nav_y", input_type="number"),
                    ActionParameterSpec("free_nav_theta", input_type="number"),
                    ActionParameterSpec("reentry_id", placeholder="LM1"),
                    ActionParameterSpec("reentry_x", input_type="number"),
                    ActionParameterSpec("reentry_y", input_type="number"),
                    ActionParameterSpec("reentry_theta", input_type="number"),
                    ActionParameterSpec(
                        "waypoint_delay_sec",
                        input_type="number",
                        placeholder="0",
                    ),
                ),
            )
        )
        install(
            ActionSpec(
                action_type="seerCoordinateNav",
                label="SEER Coordinate Navigation",
                motion=True,
                timeout_sec=_env_float("SEER_NAVIGATION_TIMEOUT_SEC", 300.0) + 5.0,
                handler=coordinate_navigation,
                parameters=(
                    ActionParameterSpec("x", input_type="number"),
                    ActionParameterSpec("y", input_type="number"),
                    ActionParameterSpec("theta_deg", input_type="number"),
                ),
            )
        )
        install(
            ActionSpec(
                action_type="seerTranslate",
                label="SEER Straight Translation",
                motion=True,
                timeout_sec=_env_float("SEER_NAVIGATION_TIMEOUT_SEC", 300.0) + 5.0,
                handler=translate,
                parameters=(
                    ActionParameterSpec("distance_m", input_type="number"),
                    ActionParameterSpec("linear_speed_mps", input_type="number"),
                    ActionParameterSpec("lateral", choices=("forward", "lateral")),
                ),
            )
        )
        install(
            ActionSpec(
                action_type="seerTurn",
                label="SEER Turn",
                motion=True,
                timeout_sec=_env_float("SEER_NAVIGATION_TIMEOUT_SEC", 300.0) + 5.0,
                handler=turn,
                parameters=(
                    ActionParameterSpec("angle_deg", input_type="number"),
                    ActionParameterSpec("angular_speed_deg_s", input_type="number"),
                ),
            )
        )
        install(
            ActionSpec(
                action_type="seerRefreshMap",
                label="SEER map refresh",
                motion=False,
                timeout_sec=20.0,
                handler=refresh_map_action,
                parameters=(),
            ),
            configurable=False,
            track=False,
        )
        install(
            ActionSpec(
                action_type="seerSetDO",
                label="SEER Digital Output",
                motion=False,
                timeout_sec=5.0,
                handler=set_digital_output,
                parameters=(
                    ActionParameterSpec("id", required=True, input_type="number"),
                    ActionParameterSpec(
                        "status", required=True, choices=("on", "off")
                    ),
                ),
            )
        )
        install(
            ActionSpec(
                action_type="seerCancelActiveAction",
                label="SEER 현재 Action 취소",
                motion=False,
                timeout_sec=8.0,
                handler=cancel_active_action,
                parameters=(),
            ),
            configurable=False,
            track=False,
        )
        install(
            ActionSpec(
                action_type="seerResetActionErrors",
                label="SEER Action 오류 리셋",
                motion=False,
                timeout_sec=5.0,
                handler=reset_action_errors,
                parameters=(),
            ),
            configurable=False,
            track=False,
        )
        install(
            ActionSpec(
                action_type="seerJackLoad",
                label="SEER Jack Load (Raise)",
                motion=True,
                timeout_sec=30.0,
                handler=jack_load_action,
                parameters=(),
            )
        )
        install(
            ActionSpec(
                action_type="seerJackUnload",
                label="SEER Jack Unload (Lower)",
                motion=True,
                timeout_sec=30.0,
                handler=jack_unload_action,
                parameters=(),
            )
        )
        install(
            ActionSpec(
                action_type="seerCameraDockPreview",
                label="SEER Camera Docking Preview",
                motion=False,
                timeout_sec=0.0,
                handler=camera_dock_preview,
                parameters=(
                    ActionParameterSpec(
                        "max_runtime_s", input_type="number", placeholder="300"
                    ),
                ),
            )
        )
        install(
            ActionSpec(
                action_type="seerCameraDock",
                label="SEER Camera Docking Start",
                motion=True,
                timeout_sec=0.0,
                handler=camera_dock_live,
                parameters=(
                    ActionParameterSpec(
                        "max_runtime_s", input_type="number", placeholder="300"
                    ),
                ),
            )
        )
        install(
            ActionSpec(
                action_type="seerWait",
                label="SEER Recipe Wait",
                motion=False,
                timeout_sec=3605.0,
                handler=wait_step,
                parameters=(ActionParameterSpec("seconds", input_type="number"),),
            )
        )
        install(
            ActionSpec(
                action_type="seerBlockProgram",
                label="SEER nested block program",
                motion=True,
                timeout_sec=0.0,
                handler=make_program_handler(),
                parameters=(
                    ActionParameterSpec("program_b64", required=True),
                ),
            )
        )
        # A structured builder Recipe is represented in HCL as one safe
        # seerBlockProgram step so the unchanged Adapter loader can validate
        # it. Replace only that generated Recipe's linear wrapper: the custom
        # handler receives the parent parameters directly and can therefore
        # apply each block's stored default when a variable is omitted.
        registry_specs = getattr(self._action_registry, "_specs", None)
        if isinstance(registry_specs, dict):
            for recipe in getattr(self.config, "recipes", ()):
                steps = tuple(getattr(recipe, "steps", ()) or ())
                recipe_type = str(recipe.action_type)
                if len(steps) == 1 and getattr(steps[0], "extension", "") == "seerBlockProgram":
                    parameters = getattr(steps[0], "parameters", {}) or {}
                    encoded = str(parameters.get("program_b64", "") or "")
                    if not encoded:
                        continue
                    variables = program_variables(encoded)
                    registry_specs[recipe_type] = ActionSpec(
                        action_type=recipe_type,
                        label=str(getattr(recipe, "label", "") or recipe_type),
                        motion=bool(getattr(recipe, "motion", False)),
                        timeout_sec=0.0,
                        handler=_tracked_instant_handler(make_program_handler(encoded, recipe_name=recipe_type)),
                        parameters=(
                            *(
                                ActionParameterSpec(
                                    name,
                                    required=False,
                                    placeholder="비우면 블록에 저장된 기본값 사용",
                                )
                                for name in variables
                            ),
                            ActionParameterSpec(
                                "_seer_start_trace_id",
                                required=False,
                                placeholder="Block Builder 내부 시작 위치",
                            ),
                        ),
                    )
                    continue

                # Older/flat Builder Recipes remain normal individual HCL
                # steps. Supply their stored default_FIELD values at the
                # parent context so the unchanged Recipe resolver does not
                # reject an omitted ${var.NAME} before reaching the SEER step.
                defaults: Dict[str, Any] = {}
                for step in steps:
                    parameters = getattr(step, "parameters", {}) or {}
                    for field_name, value in parameters.items():
                        if not (
                            isinstance(value, str)
                            and value.startswith("${var.")
                            and value.endswith("}")
                        ):
                            continue
                        variable_name = value[6:-1]
                        default_key = f"default_{field_name}"
                        if variable_name and default_key in parameters:
                            defaults.setdefault(variable_name, parameters[default_key])
                original_recipe_spec = registry_specs.get(recipe_type)
                if not defaults or original_recipe_spec is None or original_recipe_spec.handler is None:
                    continue

                def handler_with_defaults(original_handler, stored_defaults):
                    async def handle(context):
                        merged = dict(stored_defaults)
                        merged.update(
                            {
                                key: value
                                for key, value in context.params.items()
                                if value is not None
                                and not (isinstance(value, str) and not value.strip())
                            }
                        )
                        nested_context = SimpleNamespace(
                            action=context.action,
                            adapter=context.adapter,
                            params=merged,
                            report=context.report,
                        )
                        return await original_handler(nested_context)

                    return handle

                registry_specs[recipe_type] = ActionSpec(
                    action_type=recipe_type,
                    label=original_recipe_spec.label,
                    motion=original_recipe_spec.motion,
                    timeout_sec=original_recipe_spec.timeout_sec,
                    handler=_tracked_instant_handler(
                        handler_with_defaults(original_recipe_spec.handler, defaults)
                    ),
                    parameters=original_recipe_spec.parameters,
                )
        sound_enabled = bool(
            getattr(getattr(self.config, "sound_settings", None), "enabled", False)
        )
        video_enabled = bool(
            getattr(getattr(self.config, "video", None), "enabled", False)
        )
        self._seer_capability_report = capability_report(
            registry_actions=self._action_registry.action_types(),
            simulator=bool(self._is_simulator()),
            sound_enabled=sound_enabled,
            video_enabled=video_enabled,
        )

    @wraps(original_build_factsheet)
    def patched_build_factsheet(self):
        factsheet = original_build_factsheet(self)
        from core.factsheet import ACTION_SCOPES  # pyright: ignore[reportMissingImports]

        return filter_factsheet_actions(
            factsheet, self, action_scopes=ACTION_SCOPES
        )

    Adapter.__init__ = patched_init
    Adapter._build_factsheet = patched_build_factsheet
    Adapter._seer_emergency_action_installed = True



def _install_seer_vehicle_connection_state() -> None:
    """Publish VDA5050 connectionState from the real SEER vehicle link.

    The shared Adapter intentionally treats an ACS/MQTT reconnect as proof that
    the robot is ONLINE.  That assumption is valid only when the broker and the
    robot are the same link.  In the SEER drop-in they are independent: Mosquitto
    can be reachable while the controller at ``vehicle_ip`` is powered off.

    Keep the shared Adapter unchanged on disk, but for a SEER process:

    * an explicit startup ONLINE is translated to ONLINE only when the STATE
      channel is connected and fresh, otherwise retained OFFLINE is published;
    * an MQTT reconnect re-publishes the *current vehicle-link* state instead of
      blindly publishing ONLINE;
    * the existing per-state-cycle JIBOT connection check becomes the transition
      detector, so OFFLINE/ONLINE follows STATE loss/recovery automatically.

    Temporary vehicle OFFLINE must not set Adapter's graceful-shutdown latch;
    otherwise a later vehicle recovery could never publish ONLINE again.
    """

    from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
    from protocol.vda5050_3_0.messages import (  # pyright: ignore[reportMissingImports]
        ConnectionState,
    )

    if getattr(Adapter, "_seer_vehicle_connection_state_installed", False):
        return

    original_publish_connection = Adapter.publish_connection
    original_refresh_connection_errors = Adapter._refresh_jibot_connection_errors

    def _vehicle_state(self):
        try:
            healthy = bool(self._is_jibot_link_healthy())
        except Exception:
            healthy = False
        return ConnectionState.ONLINE if healthy else ConnectionState.OFFLINE

    def _publish_vehicle_state(
        self,
        *,
        force: bool = False,
        reason: str = "vehicle link",
    ) -> None:
        desired = _vehicle_state(self)
        previous = getattr(self, "_seer_vehicle_connection_state", None)
        if not force and previous == desired:
            return
        # A deliberate OFFLINE from adaptor/main.py's shutdown path is latched
        # by the shared Adapter.  Never undo that intent on a late callback.
        if bool(getattr(self, "_connection_offline_intent", False)):
            return
        try:
            self._publish_connection_state(
                desired,
                topic_name="connection",
                retain_msg=True,
            )
        except Exception as exc:  # MQTT outages are handled by paho/LWT.
            if not getattr(self, "_seer_connection_publish_error_logged", False):
                print(f"[SEER VEHICLE CONNECTION PUBLISH FAILED] {exc}")
                self._seer_connection_publish_error_logged = True
            return

        self._seer_connection_publish_error_logged = False
        # _publish_connection_state() treats any OFFLINE as graceful shutdown.
        # This OFFLINE is only a controller-link state, so clear that latch.
        if desired == ConnectionState.OFFLINE:
            self._connection_offline_intent = False
        self._seer_vehicle_connection_state = desired
        print(
            "[SEER VEHICLE CONNECTION] "
            f"state={desired.value} reason={reason}"
        )

    async def publish_connection(
        self,
        topic_name: str,
        interval_sec: int = 1,
        retain_msg: bool = True,
        connection_state=ConnectionState.CONNECTION_BROKEN,
    ):
        # The unchanged main.py asks for ONLINE once after MQTT setup.  For SEER
        # that means "publish the real controller-link state now", not "broker
        # connected".  Force the publish so an old retained ONLINE is replaced.
        if connection_state == ConnectionState.ONLINE:
            _publish_vehicle_state(self, force=True, reason="startup")
            return None

        # Explicit OFFLINE is the normal graceful shutdown path.  Preserve the
        # shared Adapter behavior/latch exactly for that case.
        result = await original_publish_connection(
            self,
            topic_name=topic_name,
            interval_sec=interval_sec,
            retain_msg=retain_msg,
            connection_state=connection_state,
        )
        self._seer_vehicle_connection_state = connection_state
        return result

    def republish_connection_online(self) -> None:
        """MQTT reconnect callback: reassert actual vehicle state, not ONLINE."""

        if bool(getattr(self, "_connection_offline_intent", False)):
            return
        try:
            self._call_on_adapter_loop(
                lambda: _publish_vehicle_state(
                    self, force=True, reason="mqtt reconnect"
                )
            )
        except Exception as exc:
            if not getattr(self, "_seer_connection_republish_error_logged", False):
                print(f"[SEER VEHICLE CONNECTION REPUBLISH FAILED] {exc}")
                self._seer_connection_republish_error_logged = True

    def refresh_connection_errors(self) -> None:
        # Keep all original VDA state error behavior, then mirror the same health
        # decision onto the retained VDA5050 connection topic.  This method is
        # already called every state publish cycle, so no extra polling thread is
        # needed and transitions are detected quickly.
        original_refresh_connection_errors(self)
        _publish_vehicle_state(self, reason="state link transition")

    Adapter._seer_vehicle_connection_state = _vehicle_state
    Adapter._seer_publish_vehicle_connection_state = _publish_vehicle_state
    Adapter.publish_connection = publish_connection
    Adapter._republish_connection_online = republish_connection_online
    Adapter._refresh_jibot_connection_errors = refresh_connection_errors
    Adapter._seer_vehicle_connection_state_installed = True

def install_into_adapter_main(adapter_main: Any) -> None:
    """Install real/simulator construction hooks into imported original main."""

    adapter_main.JIBOT = SeerAdapterClient
    if (
        hasattr(adapter_main, "resolve_instance")
        and not getattr(adapter_main, "_seer_hcl_paths_installed", False)
    ):
        original_resolve_instance = adapter_main.resolve_instance

        @wraps(original_resolve_instance)
        def resolve_instance(cli_args):
            resolved = list(original_resolve_instance(cli_args))
            extensions_path = str(os.getenv("SEER_EXTENSIONS_PATH", "") or "").strip()
            recipes_path = str(os.getenv("SEER_RECIPES_PATH", "") or "").strip()
            if extensions_path:
                resolved[3] = extensions_path
            if recipes_path:
                resolved[4] = recipes_path
            return tuple(resolved)

        adapter_main.resolve_instance = resolve_instance
        adapter_main._seer_hcl_paths_installed = True
    _install_seer_emergency_action()
    _install_seer_vehicle_connection_state()
    # ``run_adapter.py`` makes the repository-level ``seer_simulator`` package
    # importable. Import lazily here to avoid a bridge <-> simulator cycle while
    # keeping the original adapter's legacy class name untouched.
    try:
        from seer_simulator import SimulatedSEER  # pyright: ignore[reportMissingImports]
    except ImportError:
        # Package-only users may not have the repository-level simulator
        # folder. Keep the embedded class as a compatible fallback.
        SimulatedSEER = SeerSimulatedAdapterClient
    simulator_module = types.ModuleType("cls_jibot_simulator")
    simulator_module.SimulatedJIBOT = SimulatedSEER
    sys.modules["cls_jibot_simulator"] = simulator_module
