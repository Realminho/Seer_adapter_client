"""In-process multi-port SEER TCP simulator used by the real SeerClient.

The simulator speaks the same 16-byte Robokit request/response protocol as a
physical SEER controller.  It deliberately sits behind ``SeerClient`` instead
of replacing it, so simulation exercises framing, port routing, polling, state
mapping and adapter integration as well as motion behaviour.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Deque, Dict, Mapping, Optional, Tuple

from .protocol import (
    ERROR_RESPONSE_TYPE,
    HEADER_SIZE,
    ApiNumber,
    ApiPort,
    pack_message,
    response_type_for,
    unpack_header,
)


@dataclass(frozen=True)
class SimulatorFault:
    """One-shot protocol/network fault injected before a response is sent."""

    kind: str
    delay_sec: float = 0.0
    ret_code: int = -1
    message: str = "injected simulator fault"


class SeerSimulatorServer:
    """Small stateful SEER controller simulator with five TCP API ports."""

    PORTS = (
        ApiPort.STATE,
        ApiPort.CONTROL,
        ApiPort.TASK,
        ApiPort.CONFIG,
        ApiPort.OTHER,
    )

    def __init__(
        self,
        host: str = "127.0.0.1",
        *,
        travel_time_sec: float = 0.25,
        initial_x: float = 0.0,
        initial_y: float = 0.0,
        initial_angle: float = 0.0,
        initial_battery: float = 80.0,
        initial_map: str = "simulation",
        di_channel_count: int = 24,
        do_channel_count: int = 16,
        landmarks: Optional[Mapping[str, Tuple[float, float, float]]] = None,
        map_data: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.host = host
        self.travel_time_sec = max(0.01, float(travel_time_sec))
        self.x = float(initial_x)
        self.y = float(initial_y)
        self.angle = float(initial_angle)
        self.battery = max(0.0, min(100.0, float(initial_battery)))
        self.current_map = str(initial_map)
        self.di_channel_count = max(1, min(256, int(di_channel_count)))
        self.do_channel_count = max(1, min(256, int(do_channel_count)))
        self.available_maps = [self.current_map]
        self.landmarks = dict(landmarks or {})
        self.map_data = dict(map_data or self._default_map_data())
        self.task_status = 0
        self.running_status = 0
        self.move_status_info = ""
        self.target_id = ""
        self.station = ""
        self.unfinished_path: list[str] = []
        self.blocked = False
        self.block_reason = ""
        self.physical_emergency = False
        self.driver_emergency = False
        self.soft_emergency = False
        self.charging = False
        self.motor_enabled = True
        self.mode = "auto"
        self.vx = 0.0
        self.vy = 0.0
        self.w = 0.0
        self.digital_inputs: Dict[int, bool] = {2: True}
        self.digital_outputs: Dict[int, bool] = {}
        self.jack_loaded = False
        self.jack_enabled = True
        self.robot_model = "AMB-300JZ"
        self.robot_model_data: Mapping[str, Any] = {
            "modelName": self.robot_model,
            "deviceTypes": [{
                "name": "jack",
                "devices": [{
                    "name": "jack",
                    "isEnabled": True,
                    "deviceParams": [{
                        "key": "type",
                        "comboParam": {"childKey": "byController", "childParams": []},
                    }],
                }],
            }],
        }
        self.command_log: list[Dict[str, Any]] = []

        self._servers: Dict[ApiPort, asyncio.AbstractServer] = {}
        self._port_map: Dict[ApiPort, int] = {}
        self._clients: Dict[ApiPort, set[asyncio.StreamWriter]] = defaultdict(set)
        self._faults: Dict[Tuple[ApiPort, int], Deque[SimulatorFault]] = defaultdict(deque)
        self._motion_task: Optional[asyncio.Task[Any]] = None
        self._velocity_task: Optional[asyncio.Task[Any]] = None
        self._jack_task: Optional[asyncio.Task[Any]] = None

    @property
    def port_map(self) -> Mapping[ApiPort, int]:
        return dict(self._port_map)

    async def start(self) -> Mapping[ApiPort, int]:
        """Bind five loopback ports and return the logical-to-TCP port map."""

        if self._servers:
            return self.port_map
        try:
            for port_id in self.PORTS:
                await self._start_port(port_id, 0)
        except Exception:
            await self.stop()
            raise
        return self.port_map

    async def _start_port(self, port_id: ApiPort, tcp_port: int) -> int:
        server = await asyncio.start_server(
            lambda reader, writer, p=port_id: self._serve(p, reader, writer),
            self.host,
            int(tcp_port),
        )
        actual = int(server.sockets[0].getsockname()[1])
        self._servers[port_id] = server
        self._port_map[port_id] = actual
        return actual

    async def suspend_port(self, port_id: ApiPort) -> None:
        """Stop accepting and close active clients for one logical port."""

        port_id = ApiPort(port_id)
        server = self._servers.pop(port_id, None)
        clients = tuple(self._clients.get(port_id, ()))
        for writer in clients:
            writer.close()
            transport = getattr(writer, "transport", None)
            if transport is not None:
                with suppress(Exception):
                    transport.abort()
        self._clients[port_id].clear()
        await asyncio.sleep(0)
        if server is not None:
            server.close()
            # Some Python/event-loop combinations wait for active client
            # callbacks as part of wait_closed().  They have already been
            # aborted above, so a bounded wait prevents a test fault from
            # hanging the entire simulator.
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(server.wait_closed(), timeout=0.5)

    async def resume_port(self, port_id: ApiPort) -> int:
        """Rebind a suspended logical port to its previous TCP number."""

        port_id = ApiPort(port_id)
        if port_id in self._servers:
            return self._port_map[port_id]
        return await self._start_port(port_id, self._port_map[port_id])

    def inject_fault(
        self,
        port_id: ApiPort,
        api: int,
        kind: str,
        *,
        count: int = 1,
        delay_sec: float = 0.0,
        ret_code: int = -1,
        message: str = "injected simulator fault",
    ) -> None:
        """Queue deterministic one-shot faults for a port/API pair.

        Supported kinds: delay, disconnect, partial_body, wrong_sequence,
        wrong_type and ret_code.
        """

        allowed = {
            "delay", "disconnect", "partial_body", "wrong_sequence",
            "wrong_type", "ret_code",
        }
        if kind not in allowed:
            raise ValueError(f"unsupported simulator fault kind: {kind}")
        if count < 1:
            raise ValueError("fault count must be at least one")
        fault = SimulatorFault(
            kind=kind,
            delay_sec=max(0.0, float(delay_sec)),
            ret_code=int(ret_code),
            message=str(message),
        )
        self._faults[(ApiPort(port_id), int(api))].extend(fault for _ in range(count))

    def clear_faults(self) -> None:
        self._faults.clear()

    def _pop_fault(self, port_id: ApiPort, api: int) -> Optional[SimulatorFault]:
        queue = self._faults.get((port_id, int(api)))
        if not queue:
            return None
        fault = queue.popleft()
        if not queue:
            self._faults.pop((port_id, int(api)), None)
        return fault

    async def stop(self) -> None:
        await self._cancel_motion(set_canceled=False)
        if self._jack_task is not None:
            self._jack_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._jack_task
            self._jack_task = None
        for port_id in tuple(self._servers):
            await self.suspend_port(port_id)
        self._servers.clear()
        self._port_map.clear()
        self._faults.clear()

    async def _serve(
        self,
        port_id: ApiPort,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._clients[port_id].add(writer)
        try:
            while True:
                header = await reader.readexactly(HEADER_SIZE)
                body_length, sequence, message_type = unpack_header(header)
                raw = await reader.readexactly(body_length) if body_length else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
                self.command_log.append(
                    {"port": int(port_id), "api": int(message_type), "body": body}
                )
                fault = self._pop_fault(port_id, int(message_type))
                if fault is not None and fault.delay_sec > 0:
                    await asyncio.sleep(fault.delay_sec)
                if fault is not None and fault.kind == "disconnect":
                    writer.close()
                    return
                try:
                    payload = await self._dispatch(port_id, int(message_type), body)
                    response_type = response_type_for(message_type)
                except Exception as exc:  # simulator should return a protocol error
                    payload = {"ret_code": -1, "error": str(exc)}
                    response_type = ERROR_RESPONSE_TYPE
                response_sequence = sequence
                if fault is not None:
                    if fault.kind == "ret_code":
                        payload = {"ret_code": fault.ret_code, "err_msg": fault.message}
                        response_type = response_type_for(message_type)
                    elif fault.kind == "wrong_sequence":
                        response_sequence = (sequence + 1) & 0xFFFF
                    elif fault.kind == "wrong_type":
                        response_type = int(message_type)
                frame = pack_message(response_sequence, response_type, payload)
                if fault is not None and fault.kind == "partial_body":
                    cutoff = max(HEADER_SIZE, len(frame) - max(1, len(frame) // 4))
                    writer.write(frame[:cutoff])
                    await writer.drain()
                    writer.close()
                    return
                writer.write(frame)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self._clients[port_id].discard(writer)
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(
        self, port_id: ApiPort, message_type: int, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        api = ApiNumber(message_type)
        if port_id == ApiPort.STATE:
            return self._state_response(api)
        if port_id == ApiPort.CONTROL:
            return await self._control_response(api, body)
        if port_id == ApiPort.TASK:
            return await self._task_response(api, body)
        if port_id == ApiPort.CONFIG:
            return self._config_response(api, body)
        if port_id == ApiPort.OTHER:
            return await self._other_response(api, body)
        raise ValueError(f"unsupported simulator port: {port_id}")

    def _default_map_data(self) -> Mapping[str, Any]:
        route_landmarks = dict(self.landmarks)
        using_demo_graph = not route_landmarks
        if using_demo_graph:
            route_landmarks = {
                "SIM_START": (self.x, self.y, self.angle),
                "SIM_UPPER": (self.x + 2.0, self.y + 1.2, 0.0),
                "SIM_LOWER": (self.x + 2.0, self.y - 1.2, 0.0),
                "SIM_GOAL": (self.x + 4.0, self.y, 0.0),
            }
        points = list(route_landmarks.values()) + [(self.x, self.y, self.angle)]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        min_x, max_x = min(xs) - 2.0, max(xs) + 2.0
        min_y, max_y = min(ys) - 2.0, max(ys) + 2.0
        normal = []
        for step in range(81):
            ratio = step / 80.0
            x = min_x + (max_x - min_x) * ratio
            y = min_y + (max_y - min_y) * ratio
            normal.extend(
                (
                    {"x": x, "y": min_y},
                    {"x": x, "y": max_y},
                    {"x": min_x, "y": y},
                    {"x": max_x, "y": y},
                )
            )
        advanced = [
            {
                "className": "LocationMark",
                "instanceName": name,
                "pos": {"x": pose[0], "y": pose[1]},
                "dir": pose[2],
            }
            for name, pose in route_landmarks.items()
        ]

        if using_demo_graph:
            links = (
                ("SIM_START", "SIM_UPPER"),
                ("SIM_UPPER", "SIM_GOAL"),
                ("SIM_START", "SIM_LOWER"),
                ("SIM_LOWER", "SIM_GOAL"),
            )
        else:
            names = list(route_landmarks)
            links = tuple(zip(names, names[1:]))

        curves = []
        for start_name, end_name in links:
            for source_name, target_name in (
                (start_name, end_name),
                (end_name, start_name),
            ):
                source = route_landmarks[source_name]
                target = route_landmarks[target_name]
                c1 = (
                    source[0] + (target[0] - source[0]) / 3.0,
                    source[1] + (target[1] - source[1]) / 3.0,
                )
                c2 = (
                    source[0] + 2.0 * (target[0] - source[0]) / 3.0,
                    source[1] + 2.0 * (target[1] - source[1]) / 3.0,
                )
                curves.append(
                    {
                        "className": "DegenerateBezier",
                        "instanceName": f"{source_name}-{target_name}",
                        "startPos": {
                            "instanceName": source_name,
                            "pos": {"x": source[0], "y": source[1]},
                        },
                        "endPos": {
                            "instanceName": target_name,
                            "pos": {"x": target[0], "y": target[1]},
                        },
                        "controlPos1": {"x": c1[0], "y": c1[1]},
                        "controlPos2": {"x": c2[0], "y": c2[1]},
                    }
                )
        return {
            "header": {
                "mapName": self.current_map,
                "minPos": {"x": min_x, "y": min_y},
                "maxPos": {"x": max_x, "y": max_y},
                "resolution": 0.05,
            },
            "normalPosList": normal,
            "advancedPointList": advanced,
            "advancedLineList": [],
            "advancedCurveList": curves,
        }

    def _config_response(
        self, api: ApiNumber, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if api != ApiNumber.CONFIG_DOWNLOAD_MAP:
            raise ValueError(f"unsupported CONFIG API: {int(api)}")
        name = str(body.get("map_name", "")).strip()
        if not name:
            raise ValueError("map_name is required")
        if name != self.current_map:
            raise ValueError(f"map not found: {name}")
        return dict(self.map_data)

    def _state_response(self, api: ApiNumber) -> Mapping[str, Any]:
        if api == ApiNumber.STATUS_MODEL:
            return dict(self.robot_model_data)
        if api == ApiNumber.STATUS_JACK:
            return {
                "ret_code": 0,
                "jack_enable": bool(self.jack_enabled),
                "jack_mode": True,
                "jack_state": 1 if self.jack_loaded else 3,
                "jack_error_code": 0,
                "jack_isFull": bool(self.jack_loaded),
                "jack_speed": 0,
                "jack_emc": False,
                "jack_height": 0.05 if self.jack_loaded else 0.0,
                "peripheral_data": [],
            }
        if api in (ApiNumber.STATUS_INFO, ApiNumber.STATUS_ALL1):
            return {
                "ret_code": 0,
                "x": self.x,
                "y": self.y,
                "angle": self.angle,
                "confidence": 1.0,
                "battery_level": self.battery / 100.0,
                "charging": self.charging,
                "vx": self.vx,
                "vy": self.vy,
                "w": self.w,
                "status": "Driving" if self.task_status == 2 or any((self.vx, self.vy, self.w)) else "Stopped",
                "mode": self.mode,
                "motor_enabled": self.motor_enabled,
                "task_status": self.task_status,
                "running_status": self.running_status,
                "move_status_info": self.move_status_info,
                "target_id": self.target_id,
                "blocked": self.blocked,
                "block_reason": self.block_reason,
                "emergency": self.physical_emergency,
                "driver_emc": self.driver_emergency,
                "soft_emc": self.soft_emergency,
                "electric": self.motor_enabled,
                "DI": [
                    {
                        "id": index,
                        "status": self.digital_inputs.get(index, False),
                        "valid": True,
                    }
                    for index in range(self.di_channel_count)
                ],
                "DO": [
                    {
                        "id": index,
                        "status": self.digital_outputs.get(index, False),
                        "valid": True,
                    }
                    for index in range(self.do_channel_count)
                ],
                "current_map": self.current_map,
                "maps": list(self.available_maps),
                "robot": {"model": self.robot_model},
                "robot.model": self.robot_model,
            }
        if api == ApiNumber.STATUS_LOC:
            return {"ret_code": 0, "x": self.x, "y": self.y, "angle": self.angle, "confidence": 1.0}
        if api == ApiNumber.STATUS_SPEED:
            return {"ret_code": 0, "vx": self.vx, "vy": self.vy, "w": self.w}
        if api == ApiNumber.STATUS_RUN:
            return {
                "ret_code": 0,
                "status": "Driving" if self.task_status == 2 or any((self.vx, self.vy, self.w)) else "Stopped",
                "motor_enabled": self.motor_enabled,
            }
        if api == ApiNumber.STATUS_MODE:
            return {"ret_code": 0, "mode": self.mode}
        if api == ApiNumber.STATUS_BATTERY:
            return {
                "ret_code": 0,
                "battery_level": self.battery / 100.0,
                "charging": self.charging,
                "voltage": 48.0,
                "current": -2.0 if self.charging else 1.0,
            }
        if api == ApiNumber.STATUS_TASK:
            return {
                "ret_code": 0,
                "task_status": self.task_status,
                "running_status": self.running_status,
                "move_status_info": self.move_status_info,
                "target_id": self.target_id,
                "unfinished_path": list(self.unfinished_path),
            }
        if api == ApiNumber.STATUS_BLOCK:
            return {
                "ret_code": 0,
                "blocked": self.blocked,
                "block_reason": self.block_reason,
            }
        if api == ApiNumber.STATUS_EMERGENCY:
            return {
                "ret_code": 0,
                "emergency": self.physical_emergency,
                "driver_emc": self.driver_emergency,
                "soft_emc": self.soft_emergency,
                "electric": self.motor_enabled,
            }
        if api == ApiNumber.STATUS_IO:
            return {
                "ret_code": 0,
                "DI": [
                    {
                        "id": index,
                        "status": self.digital_inputs.get(index, False),
                        "valid": True,
                    }
                    for index in range(self.di_channel_count)
                ],
                "DO": [
                    {
                        "id": index,
                        "status": self.digital_outputs.get(index, False),
                        "valid": True,
                    }
                    for index in range(self.do_channel_count)
                ],
            }
        if api == ApiNumber.STATUS_MAP:
            return {
                "ret_code": 0,
                "current_map": self.current_map,
                "maps": list(self.available_maps),
            }
        raise ValueError(f"unsupported STATE API: {int(api)}")

    async def _control_response(
        self, api: ApiNumber, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if api == ApiNumber.CONTROL_STOP:
            # API 2000 stops open-loop velocity only. Path cancellation belongs
            # to API 3003 and is sent separately by SeerControlService.stop().
            if self._velocity_task is not None:
                task = self._velocity_task
                self._velocity_task = None
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self.vx = self.vy = self.w = 0.0
            if self._motion_task is None:
                self.task_status = 0
            return {"ret_code": 0}
        if api == ApiNumber.CONTROL_RELOC:
            await self._cancel_motion(set_canceled=False)
            self.x = float(body.get("x", 0.0))
            self.y = float(body.get("y", 0.0))
            self.angle = float(body.get("angle", 0.0))
            self.task_status = 0
            return {"ret_code": 0}
        if api == ApiNumber.CONTROL_MOTION:
            if self.physical_emergency or self.driver_emergency or self.soft_emergency:
                raise RuntimeError("motion rejected while emergency stop is active")
            await self._cancel_motion(set_canceled=False)
            self.vx = float(body.get("vx", 0.0))
            self.vy = float(body.get("vy", 0.0))
            self.w = float(body.get("w", 0.0))
            self.task_status = 2 if any((self.vx, self.vy, self.w)) else 0
            if self.task_status == 2:
                duration_ms = int(body.get("duration", 500))
                if not 0 <= duration_ms <= 5000:
                    raise ValueError("duration must be in 0..5000 ms")
                self._velocity_task = asyncio.create_task(
                    self._integrate_velocity(duration_ms)
                )
            return {"ret_code": 0}
        if api == ApiNumber.CONTROL_LOADMAP:
            name = str(body.get("map_name", "")).strip()
            if not name:
                raise ValueError("map_name is required")
            self.current_map = name
            if name not in self.available_maps:
                self.available_maps.append(name)
            header = self.map_data.get("header")
            if isinstance(header, dict):
                header["mapName"] = name
            return {"ret_code": 0}
        raise ValueError(f"unsupported CONTROL API: {int(api)}")

    def _station_pose(self, station: str) -> Optional[Tuple[float, float, float]]:
        """Resolve a station from explicit landmarks or the loaded .smap data."""

        target = self.landmarks.get(str(station))
        if target is not None:
            return tuple(float(value) for value in target)
        points = self.map_data.get("advancedPointList", [])
        if isinstance(points, list):
            for point in points:
                if not isinstance(point, Mapping):
                    continue
                name = str(
                    point.get("instanceName")
                    or point.get("name")
                    or point.get("id")
                    or ""
                )
                if name != str(station):
                    continue
                pos = point.get("pos", {})
                if not isinstance(pos, Mapping):
                    pos = {}
                return (
                    float(pos.get("x", point.get("x", self.x))),
                    float(pos.get("y", point.get("y", self.y))),
                    float(point.get("dir", point.get("angle", self.angle))),
                )
        return None

    async def _task_response(
        self, api: ApiNumber, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if (
            api
            in {
                ApiNumber.TASK_GOTARGET,
                ApiNumber.TASK_FREE_GOTO,
                ApiNumber.TASK_GOTARGET_LIST,
                ApiNumber.TASK_TRANSLATE,
                ApiNumber.TASK_TURN,
            }
            and (self.physical_emergency or self.driver_emergency or self.soft_emergency)
        ):
            raise RuntimeError("navigation rejected while emergency stop is active")
        if api == ApiNumber.TASK_GOTARGET:
            station = str(body.get("id", ""))
            script_name = str(body.get("script_name", "") or "").strip()
            script_args = body.get("script_args")
            free_go = body.get("freeGo")
            if script_name == "jackDoMotor.py" and isinstance(script_args, Mapping):
                operation = str(script_args.get("operation", "") or "").strip()
                if operation not in {"JackLoad", "JackUnLoadAndResetShelf"}:
                    raise ValueError(f"unsupported jackDoMotor operation: {operation}")
                await self._start_jack_motion(operation)
            elif isinstance(free_go, Mapping):
                await self._start_pose_motion(
                    float(free_go["x"]),
                    float(free_go["y"]),
                    float(free_go["theta"]),
                    "",
                )
            elif isinstance(script_args, Mapping):
                await self._start_pose_motion(
                    float(script_args["x"]),
                    float(script_args["y"]),
                    float(script_args.get("theta", self.angle)),
                    station,
                )
            else:
                target = self._station_pose(station)
                if target is None:
                    raise ValueError(f"unknown SEER station: {station}")
                await self._start_pose_motion(*target, station)
            return {"ret_code": 0}
        if api == ApiNumber.TASK_GOTARGET_LIST:
            raw_segments = body.get("move_task_list")
            if not isinstance(raw_segments, list) or not raw_segments:
                raise ValueError("move_task_list must be a non-empty array")
            poses = []
            target_ids = []
            previous_target = ""
            task_ids = set()
            for index, segment in enumerate(raw_segments):
                if not isinstance(segment, Mapping):
                    raise ValueError("move_task_list entries must be objects")
                source = str(segment.get("source_id", "")).strip()
                target_id = str(segment.get("id", "")).strip()
                segment_task_id = str(segment.get("task_id", "")).strip()
                if not source or not target_id or not segment_task_id:
                    raise ValueError("API 3066 requires source_id, id and task_id")
                if index and source != previous_target:
                    raise ValueError("API 3066 route segments are not continuous")
                if segment_task_id in task_ids:
                    raise ValueError("API 3066 task_id values must be unique")
                target = self._station_pose(target_id)
                if target is None:
                    raise ValueError(f"unknown SEER station: {target_id}")
                task_ids.add(segment_task_id)
                poses.append(target)
                target_ids.append(target_id)
                previous_target = target_id
            await self._start_route_motion(poses, target_ids)
            return {"ret_code": 0}
        if api == ApiNumber.TASK_FREE_GOTO:
            await self._start_pose_motion(
                float(body["x"]), float(body["y"]), float(body["angle"]), ""
            )
            return {"ret_code": 0}
        if api == ApiNumber.TASK_TRANSLATE:
            distance = abs(float(body["dist"]))
            lateral = "vy" in body and "vx" not in body
            velocity = float(body.get("vy" if lateral else "vx", 0.0))
            if velocity == 0.0:
                raise ValueError("translation velocity must be non-zero")
            signed_distance = math.copysign(distance, velocity)
            heading = self.angle + (math.pi / 2.0 if lateral else 0.0)
            await self._start_pose_motion(
                self.x + signed_distance * math.cos(heading),
                self.y + signed_distance * math.sin(heading),
                self.angle,
                "",
            )
            return {"ret_code": 0}
        if api == ApiNumber.TASK_TURN:
            angle = abs(float(body["angle"]))
            angular_velocity = float(body["vw"])
            if angular_velocity == 0.0:
                raise ValueError("turn angular velocity must be non-zero")
            signed_angle = math.copysign(angle, angular_velocity)
            await self._start_pose_motion(
                self.x, self.y, self.angle + signed_angle, ""
            )
            return {"ret_code": 0}
        if api == ApiNumber.TASK_PAUSE:
            self.task_status = 3
            self.vx = self.vy = self.w = 0.0
            return {"ret_code": 0}
        if api == ApiNumber.TASK_RESUME:
            if self.task_status == 3:
                self.task_status = 2
            return {"ret_code": 0}
        if api == ApiNumber.TASK_CANCEL:
            await self._cancel_motion(set_canceled=True)
            return {"ret_code": 0}
        raise ValueError(f"unsupported TASK API: {int(api)}")

    async def _other_response(
        self, api: ApiNumber, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if api == ApiNumber.OTHER_SETDO:
            self.digital_outputs[int(body["id"])] = bool(body["status"])
            return {"ret_code": 0}
        if api == ApiNumber.OTHER_SOFT_EMERGENCY:
            self.soft_emergency = bool(body["status"])
            if self.soft_emergency:
                await self._cancel_motion(set_canceled=True)
            return {"ret_code": 0}
        if api == ApiNumber.OTHER_SET_MOTOR:
            self.motor_enabled = bool(body["enable"])
            return {"ret_code": 0}
        raise ValueError(f"unsupported OTHER API: {int(api)}")

    async def _start_pose_motion(
        self, target_x: float, target_y: float, target_angle: float, target_id: str
    ) -> None:
        await self._start_route_motion(
            [(float(target_x), float(target_y), float(target_angle))],
            [str(target_id or "")],
        )

    async def _start_jack_motion(self, operation: str) -> None:
        if self._jack_task is not None and not self._jack_task.done():
            raise RuntimeError("simulated jack task is already running")
        self.task_status = 2
        self.running_status = 1
        section_name = "load" if operation == "JackLoad" else "unload"
        self.move_status_info = json.dumps(
            {
                "BlockMotor": {
                    "jack_down_di_status": bool(self.digital_inputs.get(2, False)),
                    "jack_up_di_status": False,
                    "operation": operation,
                    "status": 1,
                },
                "args": {"operation": operation},
                section_name: {"operation_status": 1, "task_id": 0},
                "objectFile": "",
                "status": 1,
                "task_id": 0,
                "task_list_size": 1,
            },
            separators=(",", ":"),
        )
        self._jack_task = asyncio.create_task(
            self._complete_jack_motion(operation, section_name)
        )

    async def _complete_jack_motion(self, operation: str, section_name: str) -> None:
        try:
            await asyncio.sleep(max(0.2, self.travel_time_sec))
            if operation == "JackLoad":
                self.jack_loaded = True
                self.digital_inputs[2] = False
            else:
                self.jack_loaded = False
                self.digital_inputs[2] = True
            self.task_status = 4
            self.running_status = 0
            self.move_status_info = json.dumps(
                {
                    "args": {"operation": operation},
                    section_name: {"operation_status": 3, "task_id": 1},
                    "objectFile": "",
                    "status": 3,
                    "task_id": 1,
                    "task_list_size": 1,
                },
                separators=(",", ":"),
            )
        finally:
            if self._jack_task is asyncio.current_task():
                self._jack_task = None

    async def _start_route_motion(
        self,
        targets: list[Tuple[float, float, float]],
        target_ids: list[str],
    ) -> None:
        await self._cancel_motion(set_canceled=False)
        if not targets or len(targets) != len(target_ids):
            raise ValueError("simulated route targets are invalid")
        self.target_id = str(target_ids[-1] or "")
        self.station = ""
        self.unfinished_path = [str(value) for value in target_ids if value]
        self.task_status = 2
        self._motion_task = asyncio.create_task(
            self._move_route(targets, target_ids)
        )

    async def _move_route(
        self,
        targets: list[Tuple[float, float, float]],
        target_ids: list[str],
    ) -> None:
        try:
            for index, target in enumerate(targets):
                self.unfinished_path = [
                    str(value) for value in target_ids[index:] if value
                ]
                await self._move_segment(*target)
            self.task_status = 4
            self.station = self.target_id
            self.unfinished_path = []
        finally:
            self.vx = self.vy = self.w = 0.0
            if self._motion_task is asyncio.current_task():
                self._motion_task = None

    async def _move_segment(
        self, target_x: float, target_y: float, target_angle: float
    ) -> None:
        start_x, start_y, start_angle = self.x, self.y, self.angle
        steps = max(1, int(self.travel_time_sec / 0.02))
        motion_vx = (target_x - start_x) / self.travel_time_sec
        motion_vy = (target_y - start_y) / self.travel_time_sec
        motion_w = (target_angle - start_angle) / self.travel_time_sec
        step = 0
        while step < steps:
            while self.task_status == 3:
                self.vx = self.vy = self.w = 0.0
                await asyncio.sleep(0.02)
            self.vx, self.vy, self.w = motion_vx, motion_vy, motion_w
            step += 1
            ratio = step / steps
            self.x = start_x + (target_x - start_x) * ratio
            self.y = start_y + (target_y - start_y) * ratio
            self.angle = start_angle + (target_angle - start_angle) * ratio
            await asyncio.sleep(self.travel_time_sec / steps)
        self.x, self.y, self.angle = target_x, target_y, target_angle

    async def _integrate_velocity(self, duration_ms: int = 500) -> None:
        interval = 0.02
        deadline = (
            asyncio.get_running_loop().time() + duration_ms / 1000.0
            if duration_ms > 0
            else None
        )
        try:
            while any((self.vx, self.vy, self.w)):
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    self.vx = self.vy = self.w = 0.0
                    self.task_status = 0
                    break
                self.x += self.vx * interval
                self.y += self.vy * interval
                self.angle += self.w * interval
                await asyncio.sleep(interval)
        finally:
            if self._velocity_task is asyncio.current_task():
                self._velocity_task = None

    async def _cancel_motion(self, *, set_canceled: bool) -> None:
        current = asyncio.current_task()
        for task in (self._motion_task, self._velocity_task):
            if task is not None and task is not current:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._motion_task = None
        self._velocity_task = None
        self.vx = self.vy = self.w = 0.0
        if set_canceled:
            self.task_status = 6
            self.target_id = ""
            self.station = ""
            self.unfinished_path = []

    async def wait_for_command(
        self, api: ApiNumber, *, timeout: float = 1.0
    ) -> Mapping[str, Any]:
        """Return the first matching command, useful for integration tests."""

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            for item in self.command_log:
                if item["api"] == int(api):
                    return item
            await asyncio.sleep(0.01)
        raise TimeoutError(f"simulator did not receive SEER API {int(api)}")
