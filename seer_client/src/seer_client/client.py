"""JiBot-compatible asynchronous client for SEER (Robokit) AMRs."""

from __future__ import annotations

import asyncio
import math
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Sequence

# ``run_adapter.py``/``manual_test.py`` add the sibling contract ``src`` path,
# and editable installs expose it normally. Pylance cannot infer that runtime
# path bootstrap when the whole repository is opened without installation.
from amr_client_contract import (  # pyright: ignore[reportMissingImports]
    SupportsManualDrive,
    SupportsRelocation,
    SupportsSimulation,
    SupportsTelemetryInjection,
)

from .commands import COMMAND_CATALOG
from .connection import SeerPortConnection
from .control import SeerControlService
from .diagnostics import validate_runtime_settings
from .drive_limits import ManualDriveLimits
from .io import SeerIOService
from .map_view import write_map_cache
from .navigation import SeerNavigationService
from .protocol import PROTOCOL_VERSION, ApiNumber, ApiPort
from .recorder import recorder_from_env
from .status import SeerStatusService

DEFAULT_PORTS = (
    ApiPort.STATE,
    ApiPort.CONTROL,
    ApiPort.TASK,
    ApiPort.CONFIG,
    ApiPort.OTHER,
)


class SeerClient(
    SupportsSimulation,
    SupportsManualDrive,
    SupportsRelocation,
    SupportsTelemetryInjection,
):
    """SEER client with the method/state surface used by the JiBot adaptor.

    Communication is split by function into ``status``, ``navigation``,
    ``control`` and ``io`` services. Public methods delegate to those services so
    existing JiBot-style simulator callers can keep using one client object.
    """

    # Newer shared Adapter releases inspect these JIBOT class catalogs while
    # classifying arbitrary VDA5050 actions. SEER intentionally exposes no raw
    # JIBOT command passthrough; empty catalogs keep that probe safe.
    COMMAND_SPECS: Mapping[str, Any] = {}
    COMMAND_METHODS: Mapping[str, str] = {}

    def __init__(
        self,
        robot_ip: str,
        *,
        ports: Sequence[ApiPort] = DEFAULT_PORTS,
        port_map: Optional[Mapping[Any, int]] = None,
        motor_names: Sequence[str] = (),
        config: Any = None,
        recorder: Any = None,
        command_timeout: float = 3.0,
        recv_chunk_bytes: int = 1024,
        status_poll_interval_sec: float = 0.2,
        status_log_interval_sec: float = 5.0,
        battery_log_interval_sec: float = 30.0,
        allow_legacy_echo_response: bool = True,
        protocol_version: int = PROTOCOL_VERSION,
        min_request_interval_sec: float = 0.0,
        is_simulator: bool = False,
        adapter_position_unit: str = "m",
        adapter_orientation_unit: str = "rad",
    ) -> None:
        self.vendor_name = "SEER"
        self.robot_ip = robot_ip
        self.command_timeout = float(command_timeout)
        self.recv_chunk_bytes = int(recv_chunk_bytes)
        self.status_poll_interval_sec = float(status_poll_interval_sec)
        self.status_log_interval_sec = float(status_log_interval_sec)
        self.battery_log_interval_sec = float(battery_log_interval_sec)
        self.allow_legacy_echo_response = bool(allow_legacy_echo_response)
        self.protocol_version = int(protocol_version)
        self.min_request_interval_sec = max(0.0, float(min_request_interval_sec))
        self.navigation_timeout_sec = float(
            os.getenv("SEER_NAVIGATION_TIMEOUT_SEC", "300") or "300"
        )
        if not math.isfinite(self.navigation_timeout_sec) or self.navigation_timeout_sec <= 0:
            raise ValueError("SEER_NAVIGATION_TIMEOUT_SEC must be positive")
        self.adapter_position_unit = str(adapter_position_unit).strip().lower()
        self.adapter_orientation_unit = str(adapter_orientation_unit).strip().lower()
        if self.adapter_position_unit not in {"m", "cm", "mm"}:
            raise ValueError(
                "adapter_position_unit must be one of: m, cm, mm"
            )
        if self.adapter_orientation_unit not in {"rad", "deg"}:
            raise ValueError(
                "adapter_orientation_unit must be one of: rad, deg"
            )
        self.config = config
        self.recorder = recorder or recorder_from_env(metadata={"robot_ip": robot_ip})

        self._port_ids = tuple(ApiPort(port) for port in ports)
        self._port_map: Dict[ApiPort, int] = {}
        for key, value in dict(port_map or {}).items():
            port_key = ApiPort[key] if isinstance(key, str) else ApiPort(key)
            self._port_map[port_key] = int(value)
        self._ports: Dict[ApiPort, SeerPortConnection] = {}

        # JiBot-compatible cache read directly by the existing adaptor.
        self._x = 0.0
        self._y = 0.0
        self._th = 0.0
        self._battery: Optional[float] = None
        self._battery_known = False
        self._status = "Stopped"
        self._mode = "auto"
        self._station = ""
        self._charging = False
        self._motor_flag = True
        # Provenance matters on real SEER firmware: STATUS 1100 ``electric``
        # is not consistently equivalent to an explicit drive-motor enable flag.
        self._motor_flag_source = "default"
        self._electric_state: Optional[bool] = None
        self._localization_score = 0.0

        self._vx = 0.0
        self._vy = 0.0
        self._w = 0.0
        self._speed = 0.0
        self._emergency = False
        self._soft_emergency = False
        # SEER can pause an active task while software emergency is asserted
        # and resume it when the switch is released.  Keep a local safety
        # latch so release is allowed only after TASK_CANCEL was accepted.
        self._emergency_cancel_confirmed = False
        self._physical_emergency = False
        self._driver_emergency = False
        self._brake = False
        self._blocked = False
        self._block_reason = ""
        self._task_status: Optional[int] = None
        self._target_id = ""
        self._pending_station = ""
        self._current_map = ""
        self._available_maps = []
        self._robot_model = ""
        self._robot_model_loaded = False
        self._robot_model_query_error = ""
        self._jack_supported = None
        self._jack_model_enabled = None
        self._jack_runtime_enabled = None
        self._jack_capability_reason = "Jack 지원 여부 확인 중"
        self._map_nodes: Dict[str, Any] = {}
        # ZIP (5)'s adapter probes this optional JIBOT-only geometry cache.
        # Keep an empty compatibility surface; SEER uses heading/order nodes.
        self._map_path_points: Dict[str, Any] = {}
        self._map_raw: Mapping[str, Any] = {}
        self._battery_voltage: Optional[float] = None
        self._battery_current: Optional[float] = None
        self._robot_safety: Dict[str, Any] = {}
        self._robot_safety_last_update = 0.0
        self._bms_last_update = 0.0

        self._poll_task: Optional[asyncio.Task] = None
        self._map_task: Optional[asyncio.Task] = None
        self._running = False
        self.is_simulator = bool(is_simulator)
        # main.robot_info_loop can avoid issuing the same STATE queries again.
        self.handles_status_polling = True
        cache_path = str(os.getenv("SEER_MAP_CACHE_PATH", "")).strip()
        self._map_cache_path = Path(cache_path) if cache_path else None
        self._map_refresh_interval_sec = max(
            5.0, float(os.getenv("SEER_MAP_REFRESH_INTERVAL_SEC", "30"))
        )
        # Map metadata (STATE 1300) is tiny, but CONFIG 4011 returns the full
        # .smap JSON and can take much longer on a busy/wireless controller.
        # Keep map transfer timeouts/retries independent from motion commands.
        self._map_download_timeout_sec = max(
            10.0, float(os.getenv("SEER_MAP_DOWNLOAD_TIMEOUT_SEC", "30"))
        )
        self._map_download_retries = max(
            1, int(os.getenv("SEER_MAP_DOWNLOAD_RETRIES", "3"))
        )
        self._io_poll_interval_sec = max(
            0.5, float(os.getenv("SEER_IO_POLL_INTERVAL_SEC", "1.0"))
        )
        self._next_io_poll_at = 0.0
        self.manual_drive_limits = ManualDriveLimits.from_env()
        self.startup_diagnostics = validate_runtime_settings(
            port_map=self._port_map,
            protocol_version=self.protocol_version,
            command_timeout=self.command_timeout,
            recv_chunk_bytes=self.recv_chunk_bytes,
            status_poll_interval_sec=self.status_poll_interval_sec,
            min_request_interval_sec=self.min_request_interval_sec,
            navigation_timeout_sec=self.navigation_timeout_sec,
            motor_names=motor_names,
            manual_drive_limits=self.manual_drive_limits,
        )
        for warning in self.startup_diagnostics.warnings:
            print(f"[SEER CONFIG WARNING] {warning}")

        # Function-specific classes requested for maintainability.
        self.status_service = SeerStatusService(self)
        self.navigation = SeerNavigationService(self)
        self.control = SeerControlService(self)
        self.io = SeerIOService(self, motor_names=motor_names)

    # ------------------------------------------------------------------
    # Unit boundary
    # ------------------------------------------------------------------
    def position_to_native(self, value: float) -> float:
        """Convert an adapter position/distance value to SEER metres."""

        factor = {"m": 1.0, "cm": 0.01, "mm": 0.001}[self.adapter_position_unit]
        return float(value) * factor

    def position_from_native(self, value: float) -> float:
        """Convert SEER metres to the position unit advertised by the adapter."""

        factor = {"m": 1.0, "cm": 0.01, "mm": 0.001}[self.adapter_position_unit]
        return float(value) / factor

    def angle_to_native(self, value: float) -> float:
        """Convert an adapter heading/angular-rate value to SEER radians."""

        numeric = float(value)
        return math.radians(numeric) if self.adapter_orientation_unit == "deg" else numeric

    def angle_from_native(self, value: float) -> float:
        """Convert SEER radians to the orientation unit advertised by the adapter."""

        numeric = float(value)
        return math.degrees(numeric) if self.adapter_orientation_unit == "deg" else numeric

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    async def connect_socket(self) -> None:
        if self.is_connected():
            return
        await self._teardown_ports()
        try:
            for port_id in self._port_ids:
                self._ports[port_id] = await self._open_port(port_id)
        except Exception:
            await self._teardown_ports()
            raise
        self._running = True
        if ApiPort.STATE in self._ports:
            # robot.model is a dedicated STATE API (1500), not a field in 1100.
            # Resolve it once on connection so Jack capability is known before
            # the operator can press a hardware-action button.  Failure is
            # non-fatal and the status loop retries later.
            await self.status_service.get_robot_model()
            self._poll_task = asyncio.create_task(
                self._poll_loop(), name="seer-status-poll"
            )
        if ApiPort.CONFIG in self._ports:
            self._map_task = asyncio.create_task(
                self._map_cache_loop(), name="seer-map-cache"
            )

    async def _open_port(self, port_id: ApiPort) -> SeerPortConnection:
        tcp_port = self._port_map.get(port_id, int(port_id))
        conn = SeerPortConnection(
            self.robot_ip,
            tcp_port,
            command_timeout=self.command_timeout,
            recv_chunk_bytes=self.recv_chunk_bytes,
            recorder=self.recorder,
            allow_legacy_echo_response=self.allow_legacy_echo_response,
            protocol_version=self.protocol_version,
            min_request_interval_sec=self.min_request_interval_sec,
        )
        await conn.connect()
        return conn

    async def connect(self) -> None:
        """SEER requires no post-socket login; retained for JiBot parity."""

        return None

    async def _teardown_ports(self) -> None:
        self._running = False
        background_tasks = tuple(
            task for task in (self._poll_task, self._map_task) if task is not None
        )
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        self._map_task = None
        for conn in list(self._ports.values()):
            await conn.disconnect()
        self._ports.clear()

    async def disconnect(self) -> None:
        await self._teardown_ports()
        if self.recorder is not None:
            self.recorder.close()

    async def reconnect(self) -> None:
        await self._teardown_ports()
        await self.connect_socket()
        await self.connect()

    def is_connected(self) -> bool:
        """Return vehicle-link health from the STATE channel only.

        SEER exposes independent TCP ports for state, motion, task, config and
        auxiliary commands.  A temporary CONFIG/TASK socket reconnect must not
        make the shared Adapter declare the whole vehicle link lost while the
        STATE stream is still healthy.  Non-state ports reconnect on demand in
        :class:`SeerPortConnection`.
        """

        state = self._ports.get(ApiPort.STATE)
        if state is not None:
            return bool(self._running and state.is_connected())
        return bool(
            self._running
            and self._ports
            and any(conn.is_connected() for conn in self._ports.values())
        )

    def seconds_since_last_rx(self) -> float:
        """Compatibility view: age of the vehicle STATE channel."""

        state = self._ports.get(ApiPort.STATE)
        if state is not None:
            return state.seconds_since_last_rx()
        if not self._ports:
            return float("inf")
        return min(conn.seconds_since_last_rx() for conn in self._ports.values())

    def critical_reconnect_count(self) -> int:
        """Return reconnects that can affect the currently executing block.

        CONFIG is intentionally excluded because a background map refresh may
        reconnect independently and should not stop a running Recipe.
        """

        critical = (ApiPort.STATE, ApiPort.CONTROL, ApiPort.TASK, ApiPort.OTHER)
        return sum(
            int(getattr(self._ports.get(port_id), "_reconnect_count", 0) or 0)
            for port_id in critical
            if self._ports.get(port_id) is not None
        )

    def seconds_since_last_rx_by_port(self) -> Dict[str, float]:
        return {
            port_id.name: conn.seconds_since_last_rx()
            for port_id, conn in self._ports.items()
        }

    def port_health(self, *, stale_after: Optional[float] = None) -> Dict[str, Any]:
        """Return health for each of SEER's logical API ports."""

        threshold = (
            max(self.command_timeout * 2.0, self.status_poll_interval_sec * 5.0)
            if stale_after is None
            else float(stale_after)
        )
        return {
            port_id.name: conn.health_snapshot(
                stale_after=threshold if port_id == ApiPort.STATE else None
            )
            for port_id, conn in self._ports.items()
        }

    def connection_health(self, *, stale_after: Optional[float] = None) -> Dict[str, Any]:
        """Aggregate multi-port health without hiding partial failures."""

        ports = self.port_health(stale_after=stale_after)
        state = ports.get(ApiPort.STATE.name)
        if not ports or state is None or not state["connected"] or state["stale"]:
            overall = "OFFLINE"
        elif any(not item["connected"] for item in ports.values()):
            overall = "DEGRADED"
        else:
            overall = "ONLINE"
        return {"state": overall, "ports": ports}

    def is_rx_stale(self, timeout: float) -> bool:
        return bool(
            self._running
            and timeout > 0
            and self.seconds_since_last_rx() > float(timeout)
        )

    # ------------------------------------------------------------------
    # Catalog dispatch
    # ------------------------------------------------------------------
    async def send_command(
        self, command: str, *, timeout: Optional[float] = None, **body: Any
    ) -> Any:
        spec = COMMAND_CATALOG.get(command)
        if spec is None:
            raise ValueError(f"Unsupported SEER command: {command}")
        self.validate_command_params(command, body)
        conn = self._ports.get(spec.port)
        if conn is None:
            raise ConnectionError(
                f"SEER port {spec.port.name} ({int(spec.port)}) not connected"
            )
        resolved = self.command_timeout if timeout is None else timeout
        return await conn.request(spec.api, body, timeout=resolved)

    def validate_command_params(
        self, command: str, params: Mapping[str, Any]
    ) -> None:
        spec = COMMAND_CATALOG.get(command)
        if spec is None:
            raise ValueError(f"Unsupported SEER command: {command}")
        required = set(spec.required)
        allowed = required | set(spec.optional)
        given = set(params)
        missing = sorted(required - given)
        unknown = sorted(given - allowed)
        if missing:
            raise ValueError(f"{command} missing required params: {', '.join(missing)}")
        if unknown:
            raise ValueError(f"{command} got unknown params: {', '.join(unknown)}")

    def get_available_commands(self) -> list[str]:
        return sorted(COMMAND_CATALOG)

    def get_available_functions(self) -> list[str]:
        return sorted({spec.method for spec in COMMAND_CATALOG.values()})

    def get_command_spec(self, command: str) -> Any:
        return COMMAND_CATALOG.get(command)

    # ------------------------------------------------------------------
    # JiBot-compatible high-level methods
    # ------------------------------------------------------------------
    async def get_robot_info(self, interval_ms: float = -1) -> Mapping[str, Any]:
        del interval_ms
        return await self.status_service.get_robot_info()

    async def get_robot_model(self) -> Mapping[str, Any]:
        """Fetch the controller robot.model through SEER status API 1500."""
        return await self.status_service.get_robot_model()

    async def get_localization_info(
        self, interval_ms: float = -1
    ) -> Mapping[str, Any]:
        del interval_ms
        return await self.status_service.get_location()

    async def get_battery_info(
        self, interval_ms: float = -1, timeout: float = 1.0
    ) -> Mapping[str, Any]:
        del interval_ms
        response = await self.send_command("query_battery", timeout=timeout)
        if isinstance(response, Mapping):
            self._process_status(ApiNumber.STATUS_BATTERY, response)
            return response
        return {}

    async def get_motor_state(self, interval_ms: float = -1) -> Optional[bool]:
        del interval_ms
        return await self.status_service.get_motor_state()

    async def goto_point(self, point: str, strict: bool = False) -> None:
        del strict
        await self.navigation.goto_station(point)

    async def path_navigation(
        self,
        target_id: str,
        *,
        source_id: str | None = None,
        task_id: str = "",
    ) -> None:
        """Run API 3051 to a named target; omit source for controller auto-routing."""

        await self.navigation.goto_station(
            target_id,
            source_id=source_id,
            task_id=task_id,
        )

    async def path_navigation_route(
        self,
        route_points: Sequence[str],
        *,
        task_id: str = "",
    ) -> None:
        """Send a complete selected route in one SEER API 3066 request."""

        await self.navigation.goto_route(route_points, task_id=task_id)

    async def wait_navigation_terminal(
        self,
        *,
        expected_station: str = "",
        expected_pose: Optional[Sequence[float]] = None,
        timeout_sec: Optional[float] = None,
        cancel_on_timeout: bool = False,
    ) -> Mapping[str, Any]:
        return await self.navigation.wait_until_terminal(
            timeout_sec=(
                self.navigation_timeout_sec if timeout_sec is None else float(timeout_sec)
            ),
            expected_station=expected_station,
            expected_pose=expected_pose,
            cancel_on_timeout=cancel_on_timeout,
        )

    async def goto_xyz(
        self, x: float, y: float, z: float, strict: bool = False
    ) -> None:
        del strict
        await self.navigation.goto_pose(x, y, z)

    async def goto_node_position(
        self,
        node_id: str,
        x: float,
        y: float,
        theta: float,
        strict: bool = False,
    ) -> None:
        del strict
        await self.navigation.goto_pose(x, y, theta, station_id=str(node_id))

    async def stop_motion(self) -> None:
        await self.control.stop()

    async def move_distance(
        self, distance: float, speed: float, **kwargs: Any
    ) -> None:
        await self.navigation.translate(distance, speed, **kwargs)

    async def turn(self, angle: float, angular_speed: float, mode: Any = None) -> None:
        await self.navigation.turn(angle, angular_speed, mode)

    async def enable_motor(self) -> None:
        await self.io.set_all_motors(True)

    async def disable_motor(self) -> None:
        await self.io.set_all_motors(False)

    async def set_motor(self, motor_name: str, enable: bool) -> Any:
        return await self.io.set_motor(motor_name, enable)

    async def dock(self, **params: Any) -> None:
        del params
        raise NotImplementedError(
            "SEER docking is not exposed because the supplied API defines no "
            "vendor-confirmed docking command"
        )

    async def localize(
        self,
        target: str,
        goal: Optional[str],
        poseX: Optional[float],
        poseY: Optional[float],
        poseTh: Optional[float],
    ) -> None:
        del goal
        if str(target).lower() == "auto":
            await self.control.relocate(auto=True, x=0.0, y=0.0, angle=0.0)
            return
        if poseX is None or poseY is None or poseTh is None:
            raise ValueError("Manual SEER localization requires poseX, poseY and poseTh")
        await self.control.relocate(
            auto=False, x=poseX, y=poseY, angle=poseTh
        )

    async def drive(
        self, trans: float, rot: float, speed: float, lat: float
    ) -> None:
        del speed
        await self.control.drive(vx=trans, vy=lat, w=rot)

    # ------------------------------------------------------------------
    # SEER-specific operations
    # ------------------------------------------------------------------
    async def relocation(
        self,
        *,
        auto: bool = True,
        x: float = 0.0,
        y: float = 0.0,
        angle: float = 0.0,
    ) -> None:
        await self.control.relocate(auto=auto, x=x, y=y, angle=angle)

    async def map_switch(self, map_name: str) -> None:
        await self.control.switch_map(map_name)
        await self.refresh_map_cache(force_map_name=map_name)

    async def set_map(self, name: str, gap: float = -1) -> None:
        del gap
        await self.map_switch(name)

    async def get_map_name(self, timeout: float = 3.0, gap: float = -1) -> str:
        del gap
        response = await self.send_command("query_map", timeout=timeout)
        if isinstance(response, Mapping):
            self._process_status(ApiNumber.STATUS_MAP, response)
        return self._current_map

    async def free_nav(self, x: float, y: float, theta: float) -> None:
        await self.navigation.free_nav(x, y, theta)

    async def pause_navigation(self) -> None:
        await self.navigation.pause()

    async def resume_navigation(self) -> None:
        await self.navigation.resume()

    async def cancel_navigation(self) -> None:
        await self.navigation.cancel()

    async def set_do(self, do_id: int, status: bool) -> None:
        await self.io.set_do(do_id, status)

    async def read_di(self, di_id: int) -> Optional[bool]:
        return await self.io.read_di(di_id)

    async def jack_load(self) -> Any:
        return await self.navigation.jack_load()

    async def jack_unload(self) -> Any:
        return await self.navigation.jack_unload()

    async def get_task_status(self) -> Mapping[str, Any]:
        return await self.status_service.get_task()

    async def get_blocked(self) -> Mapping[str, Any]:
        return await self.status_service.get_blocked()

    async def get_emergency_state(self) -> Mapping[str, Any]:
        return await self.status_service.get_emergency()

    async def set_soft_emergency(self, status: bool) -> bool:
        enabled = bool(status)
        if enabled:
            # Emergency activation must never wait for task control.  Assert
            # API 6004 first, then permanently cancel the suspended Path Nav
            # so releasing the switch cannot resume the previous movement.
            await self.io.set_soft_emergency(True)
            self._emergency_cancel_confirmed = False
            try:
                await self.navigation.cancel()
            except Exception as exc:
                raise RuntimeError(
                    "SEER emergency is ACTIVE, but navigation cancellation failed; "
                    "emergency release is blocked until cancellation succeeds"
                ) from exc
            self._emergency_cancel_confirmed = True
        else:
            # After a client/WebUI restart the controller may already be in
            # software emergency while the in-memory latch is unknown.  Retry
            # TASK_CANCEL before release and fail closed if it is not accepted.
            if self._soft_emergency and not self._emergency_cancel_confirmed:
                try:
                    await self.navigation.cancel()
                except Exception as exc:
                    raise RuntimeError(
                        "SEER emergency remains ACTIVE because navigation "
                        "cancellation could not be confirmed"
                    ) from exc
                self._emergency_cancel_confirmed = True
            await self.io.set_soft_emergency(False)
            self._emergency_cancel_confirmed = False
        return self._soft_emergency

    async def toggle_soft_emergency(self) -> bool:
        """Re-read live state, then toggle SEER software emergency output."""

        await self.get_emergency_state()
        return await self.set_soft_emergency(not self._soft_emergency)

    async def get_map_info(self) -> Mapping[str, Any]:
        return await self.status_service.get_map()

    async def download_map(self, map_name: Optional[str] = None) -> Mapping[str, Any]:
        """Download a SEER ``.smap`` JSON body through CONFIG API 4011."""

        name = str(map_name or self._current_map or "").strip()
        if not name:
            await self.get_map_info()
            name = str(self._current_map or "").strip()
        if not name:
            raise ValueError("SEER controller did not report a current map name")
        response = await self.send_command(
            "download_map",
            timeout=max(self._map_download_timeout_sec, self.command_timeout),
            map_name=name,
        )
        if not isinstance(response, Mapping):
            raise ValueError("SEER map download returned a non-object JSON body")
        if not isinstance(response.get("header"), Mapping):
            raise ValueError("SEER map download response has no map header")
        self._map_raw = dict(response)
        self._apply_downloaded_map_nodes(response)
        return response

    def _apply_downloaded_map_nodes(self, raw: Mapping[str, Any]) -> None:
        """Expose named SEER advanced points to the unmodified adapter.

        The adapter uses ``_map_nodes`` to resolve nearest/lastNodeId. SEER map
        coordinates are native m/rad, while that cache must use factsheet units.
        """

        result: Dict[str, Any] = {}
        points = raw.get("advancedPointList")
        if not isinstance(points, list):
            self._map_nodes = result
            return
        for item in points:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("instanceName", "")).strip()
            pos = item.get("pos")
            if not name or not isinstance(pos, Mapping):
                continue
            try:
                x = self.position_from_native(float(pos["x"]))
                y = self.position_from_native(float(pos["y"]))
                theta = self.angle_from_native(float(item.get("dir", 0.0)))
            except (KeyError, TypeError, ValueError):
                continue
            result[name] = (x, y, theta)
        self._map_nodes = result

    async def refresh_map_cache(
        self, *, force_map_name: Optional[str] = None
    ) -> Optional[Mapping[str, Any]]:
        """Download and atomically publish this AMR's live map cache.

        STATE API 1300 only reports map metadata.  The WebUI needs CONFIG API
        4011, which transfers the complete .smap JSON.  Retry only this
        read-only operation so a transient wireless/config-port delay does not
        leave a member without ``seer-map.json``.
        """

        if ApiPort.CONFIG not in self._ports:
            return None
        requested = str(force_map_name or "").strip()
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self._map_download_retries + 1):
            try:
                name = requested
                if not name:
                    await self.get_map_info()
                    name = str(self._current_map or "").strip()
                if not name:
                    raise ValueError("SEER controller did not report a current map name")
                started = time.monotonic()
                raw = await self.download_map(name)
                if self._map_cache_path is not None:
                    await asyncio.to_thread(write_map_cache, self._map_cache_path, raw)
                elapsed = time.monotonic() - started
                point_count = len(raw.get("advancedPointList") or []) if isinstance(raw, Mapping) else 0
                print(
                    "[SEER MAP REFRESHED] "
                    f"ip={self.robot_ip} map={name} points={point_count} "
                    f"elapsed={elapsed:.2f}s cache={self._map_cache_path or '-'}"
                )
                return raw
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # read-only API; safe to retry
                last_exc = exc
                print(
                    "[SEER MAP DOWNLOAD RETRY] "
                    f"ip={self.robot_ip} attempt={attempt}/{self._map_download_retries} "
                    f"error={type(exc).__name__}: {exc!r}"
                )
                if attempt < self._map_download_retries:
                    await asyncio.sleep(min(2.0, 0.5 * attempt))
        assert last_exc is not None
        raise last_exc

    def get_path_point_pose(self, name: str) -> Any:
        """Return a cached PathPoint pose when present (ZIP 5 compatibility)."""

        return self._map_path_points.get(str(name))

    # ------------------------------------------------------------------
    # JiBot Um* aliases used by legacy startup/test code
    # ------------------------------------------------------------------
    async def um_connect(self, gap: float = -1) -> None:
        del gap
        await self.connect()

    async def um_drive(
        self, trans: float, rot: float, speed: float, lat: float, gap: float = -1
    ) -> None:
        del gap, speed
        # SEER WebUI values are m/s and deg/s. API 2010 receives rad/s.
        limits = self.manual_drive_limits
        limits.validate_command(
            vx_mps=float(trans), vy_mps=float(lat), angular_deg_s=float(rot)
        )
        await self.control.drive_native(
            vx_mps=float(trans),
            vy_mps=float(lat),
            w_rad_s=math.radians(float(rot)),
            duration_ms=limits.motion_duration_ms,
        )

    async def um_get_battery_info(self, gap: float = -1) -> Mapping[str, Any]:
        return await self.get_battery_info(gap)

    async def um_get_loc_state(self, gap: float = -1) -> Mapping[str, Any]:
        return await self.get_localization_info(gap)

    async def um_get_motor_state(self, gap: float = -1) -> Optional[bool]:
        return await self.get_motor_state(gap)

    async def um_get_robot_info(self, gap: float = -1) -> Mapping[str, Any]:
        return await self.get_robot_info(gap)

    async def um_goto(
        self,
        target: str,
        goal: Optional[str] = None,
        poseX: Optional[float] = None,
        poseY: Optional[float] = None,
        poseTh: Optional[float] = None,
        strict: Optional[bool] = None,
        gap: float = -1,
    ) -> None:
        del gap
        if str(target).lower() in {"goal", "point", "station"}:
            if goal is None:
                raise ValueError("SEER station navigation requires goal")
            await self.goto_point(goal, bool(strict))
            return
        if poseX is None or poseY is None or poseTh is None:
            raise ValueError("SEER pose navigation requires poseX, poseY and poseTh")
        await self.goto_xyz(poseX, poseY, poseTh, bool(strict))

    async def um_localize(
        self,
        target: str,
        goal: Optional[str],
        poseX: Optional[float],
        poseY: Optional[float],
        poseTh: Optional[float],
        gap: float = -1,
    ) -> None:
        del gap
        await self.localize(target, goal, poseX, poseY, poseTh)

    async def um_set_motor(self, flag: bool, gap: float = -1) -> None:
        del gap
        if flag:
            await self.enable_motor()
        else:
            await self.disable_motor()

    async def um_stop(self, gap: float = -1) -> None:
        del gap
        await self.stop_motion()

    async def um_dock(self, gap: float = -1, **params: Any) -> None:
        del gap
        await self.dock(**params)

    # ------------------------------------------------------------------
    # Status polling and cache views
    # ------------------------------------------------------------------
    _POLL_QUERIES = SeerStatusService.POLL_QUERIES

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[SEER POLL ERROR] {exc}")
            await asyncio.sleep(self.status_poll_interval_sec)

    async def _poll_once(self) -> None:
        await self.status_service.poll_once()

    async def _map_cache_loop(self) -> None:
        while self._running:
            try:
                await self.refresh_map_cache()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[SEER MAP ERROR] {exc}")
            await asyncio.sleep(self._map_refresh_interval_sec)

    def _process_status(self, api_number: int, payload: Mapping[str, Any]) -> None:
        self.status_service.apply(api_number, payload)

    def _apply_loc(self, payload: Mapping[str, Any]) -> None:
        self.status_service._apply_location(payload)

    def _apply_battery(self, payload: Mapping[str, Any]) -> None:
        self.status_service._apply_battery(payload)

    @property
    def x(self) -> float:
        return self._x

    @property
    def y(self) -> float:
        return self._y

    @property
    def th(self) -> float:
        return self._th

    @property
    def battery(self) -> Optional[float]:
        return self._battery

    @property
    def status(self) -> str:
        return self._status

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def station(self) -> str:
        return self._station

    @property
    def charging(self) -> bool:
        return self._charging

    @property
    def motor_flag(self) -> bool:
        return self._motor_flag

    @property
    def localization_score(self) -> float:
        return self._localization_score

    # ------------------------------------------------------------------
    # Out-of-band telemetry injection (same surface as JiBot)
    # ------------------------------------------------------------------
    def set_bms(self, voltage: float, current: float) -> None:
        self._battery_voltage = float(voltage)
        self._battery_current = float(current)
        self._bms_last_update = time.monotonic()

    def clear_bms(self) -> None:
        self._battery_voltage = None
        self._battery_current = None

    def set_robot_safety(self, safety: Mapping[str, Any]) -> None:
        self._robot_safety = dict(safety)
        self._robot_safety_last_update = time.monotonic()
        for key in ("motor_enable_status", "motor_enabled", "motor_flag"):
            if key in safety:
                self._motor_flag = bool(safety[key])
                self._motor_flag_source = f"robot_safety:{key}"
                break

    def clear_robot_safety(self) -> None:
        self._robot_safety = {}


if TYPE_CHECKING:
    from amr_client_contract import AmrClient  # pyright: ignore[reportMissingImports]

    def _assert_conforms(client: SeerClient) -> "AmrClient":
        return client
