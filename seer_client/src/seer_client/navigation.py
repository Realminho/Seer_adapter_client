"""SEER task-port navigation operations."""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

if TYPE_CHECKING:
    from .client import SeerClient


class SeerNavigationTaskError(RuntimeError):
    """A SEER navigation task reached a failed or canceled terminal state."""

    def __init__(self, task_status: Any, message: str) -> None:
        super().__init__(message)
        self.task_status = task_status


class SeerNavigationService:
    """Build named-target, coordinate, relative, and task-control requests."""

    def __init__(self, client: "SeerClient") -> None:
        self.client = client

    async def wait_until_terminal(
        self,
        *,
        timeout_sec: float = 300.0,
        poll_interval_sec: float = 0.1,
        expected_station: str = "",
        expected_pose: Optional[Sequence[float]] = None,
        position_tolerance: float = 0.05,
        angle_tolerance: float = 0.05,
        cancel_on_timeout: bool = False,
    ) -> Mapping[str, Any]:
        """Wait until the active SEER task reaches a verified terminal state.

        A successful command response only means that the controller accepted
        the request.  Completion is tied to task_status=4 and, when supplied,
        the expected target/pose.  This keeps VDA actions from finishing before
        the physical task is complete.
        """

        timeout = float(timeout_sec)
        poll = float(poll_interval_sec)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("SEER navigation timeout must be a positive finite number")
        if not math.isfinite(poll) or poll <= 0:
            raise ValueError("SEER navigation poll interval must be positive")
        deadline = asyncio.get_running_loop().time() + timeout
        active_seen = False
        last: Mapping[str, Any] = {}
        while True:
            try:
                last = await self.client.status_service.get_task()
            except (ConnectionError, TimeoutError, OSError, EOFError, ValueError):
                # API 3066/3051 is already accepted by the controller before
                # this completion loop starts. A transient TCP failure must not
                # fail the parent VDA action: SEER may continue driving while
                # the Adapter reconnects. Keep the same deadline, do not resend
                # the motion command, and resume task verification once the
                # shared reconnect monitor restores the ports.
                if asyncio.get_running_loop().time() >= deadline:
                    if cancel_on_timeout:
                        try:
                            await self.cancel()
                        except Exception:
                            pass
                    raise asyncio.TimeoutError(
                        f"SEER navigation did not finish within {timeout:.3f}s"
                    )
                await asyncio.sleep(poll)
                continue
            raw_status = last.get("task_status", self.client._task_status)
            try:
                task_status: Any = int(raw_status)
            except (TypeError, ValueError):
                task_status = raw_status
            if task_status in (1, 2, 3):
                active_seen = True
            elif task_status == 4:
                target = str(
                    last.get("target_id")
                    or self.client._target_id
                    or self.client._station
                    or ""
                )
                wanted = str(expected_station or "").strip()
                if wanted and target and target != wanted:
                    raise SeerNavigationTaskError(
                        task_status,
                        f"SEER task completed at {target!r}, expected {wanted!r}",
                    )
                if expected_pose is not None:
                    if len(expected_pose) != 3:
                        raise ValueError("expected_pose must contain x, y and theta")
                    await self.client.status_service.get_location()
                    ex, ey, eth = (float(value) for value in expected_pose)
                    distance = math.hypot(self.client._x - ex, self.client._y - ey)
                    angle_error = abs(
                        math.atan2(
                            math.sin(self.client._th - eth),
                            math.cos(self.client._th - eth),
                        )
                    )
                    if distance > float(position_tolerance) or angle_error > float(angle_tolerance):
                        raise SeerNavigationTaskError(
                            task_status,
                            "SEER task reported complete outside the expected pose "
                            f"(distance={distance:.6f}, angle_error={angle_error:.6f})",
                        )
                return dict(last)
            elif task_status in (5, 6):
                label = "failed" if task_status == 5 else "canceled"
                raise SeerNavigationTaskError(
                    task_status, f"SEER navigation task {label}"
                )
            elif active_seen and task_status == 0:
                raise SeerNavigationTaskError(
                    task_status, "SEER navigation stopped before reporting completion"
                )

            if asyncio.get_running_loop().time() >= deadline:
                if cancel_on_timeout:
                    try:
                        await self.cancel()
                    except Exception:
                        pass
                raise asyncio.TimeoutError(
                    f"SEER navigation did not finish within {timeout:.3f}s"
                )
            await asyncio.sleep(poll)

    async def goto_station(
        self,
        station_id: str,
        *,
        source_id: str | None = None,
        task_id: str = "",
        angle: Any = None,
    ) -> Any:
        """Run SEER Path Navigation API 3051 to one named station."""

        station = str(station_id)
        if not station:
            raise ValueError("SEER station id must not be empty")
        body = {"id": station}
        source = str(source_id or "").strip()
        # RoboShop-style automatic Path Nav is API 3051 with only the target
        # landmark id.  SELF_POSITION is used by freeGo/script payloads, but
        # sending it as a normal gotarget source can prevent the controller
        # from resolving an off-path/off-point robot pose.  Treat it as AUTO.
        if source and source.upper() != "SELF_POSITION":
            body["source_id"] = source
        if task_id:
            body["task_id"] = str(task_id)
        if angle is not None:
            body["angle"] = self.client.angle_to_native(float(angle))
        result = await self.client.send_command("goto_station", **body)
        self.client._pending_station = station
        self.client._station = ""
        self.client._status = "Driving"
        return result

    async def goto_pose(
        self,
        x: float,
        y: float,
        theta: float,
        *,
        station_id: str = "",
    ) -> Any:
        result = await self.client.send_command(
            "goto_pose",
            script_name="syspy/goPath.py",
            script_args={
                "x": self.client.position_to_native(x),
                "y": self.client.position_to_native(y),
                "theta": self.client.angle_to_native(theta),
                "coordinate": "world",
                "backMode": 0,
            },
        )
        self.client._pending_station = str(station_id or "")
        self.client._station = ""
        self.client._status = "Driving"
        return result

    def _jack_task_details(self, payload: Mapping[str, Any]) -> tuple[str, Any, Any]:
        """Return (operation, operation_status, task_status) from STATUS_TASK."""

        raw_info = payload.get("move_status_info")
        info: Mapping[str, Any] = {}
        if isinstance(raw_info, Mapping):
            info = raw_info
        elif isinstance(raw_info, str) and raw_info.strip():
            try:
                decoded = json.loads(raw_info)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = {}
            if isinstance(decoded, Mapping):
                info = decoded

        args = info.get("args") if isinstance(info.get("args"), Mapping) else {}
        block = (
            info.get("BlockMotor")
            if isinstance(info.get("BlockMotor"), Mapping)
            else {}
        )
        operation = str(
            (args or {}).get("operation")
            or (block or {}).get("operation")
            or ""
        ).strip()
        operation_status: Any = None
        if operation == "JackLoad":
            section = info.get("load")
            if isinstance(section, Mapping):
                operation_status = section.get("operation_status")
        elif operation == "JackUnLoadAndResetShelf":
            section = info.get("unload")
            if isinstance(section, Mapping):
                operation_status = section.get("operation_status")
        if operation_status is None:
            operation_status = info.get("status")
        task_status = payload.get("task_status", self.client._task_status)
        try:
            task_status = int(task_status)
        except (TypeError, ValueError):
            pass
        try:
            operation_status = int(operation_status)
        except (TypeError, ValueError):
            pass
        return operation, operation_status, task_status

    async def wait_jack_until_terminal(
        self,
        operation: str,
        *,
        timeout_sec: float = 30.0,
        poll_interval_sec: float = 0.1,
    ) -> Mapping[str, Any]:
        """Wait for one jack ModuleScript to really finish on the controller.

        The controller first reports TASK ``task_status=2`` / operation status
        ``1`` while the motor is moving, then ``task_status=4`` / operation
        status ``3`` only after the ModuleScript has completed.  Require seeing
        the requested operation active before accepting its terminal state so a
        stale completion from a previous Jack cycle cannot release the next
        Block Builder step early.
        """

        wanted = str(operation or "").strip()
        if wanted not in {"JackLoad", "JackUnLoadAndResetShelf"}:
            raise ValueError(f"unsupported SEER jack operation: {wanted}")
        timeout = float(timeout_sec)
        poll = float(poll_interval_sec)
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise ValueError("SEER jack timeout must be positive")
        if not math.isfinite(poll) or not 0.05 <= poll <= 5.0:
            raise ValueError("SEER jack poll interval must be 0.05..5 seconds")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        active_seen = False
        last: Mapping[str, Any] = {}
        while loop.time() < deadline:
            try:
                last = await self.client.status_service.get_task()
            except (ConnectionError, TimeoutError, OSError, EOFError, ValueError):
                await asyncio.sleep(poll)
                continue

            current_operation, operation_status, task_status = self._jack_task_details(last)
            if current_operation != wanted:
                await asyncio.sleep(poll)
                continue

            if task_status in (1, 2, 3) or operation_status in (1, 2):
                active_seen = True
            if task_status in (5, 6):
                label = "failed" if task_status == 5 else "canceled"
                raise SeerNavigationTaskError(task_status, f"SEER jack task {label}")
            if active_seen and task_status == 4 and operation_status == 3:
                return dict(last)
            await asyncio.sleep(poll)

        raise asyncio.TimeoutError(
            f"SEER {wanted} did not report task_status=4 / operation_status=3 "
            f"within {timeout:.3f}s"
        )

    async def jack_load(self) -> Any:
        """Run the on-controller jackDoMotor.py ModuleScript to raise the jack."""

        return await self.client.send_command(
            "jack_load",
            script_name="jackDoMotor.py",
            script_args={"operation": "JackLoad"},
        )

    async def jack_unload(self) -> Any:
        """Run the on-controller jackDoMotor.py ModuleScript to lower the jack."""

        return await self.client.send_command(
            "jack_unload",
            script_name="jackDoMotor.py",
            script_args={"operation": "JackUnLoadAndResetShelf"},
        )

    async def goto_route(
        self,
        route_points: Sequence[str],
        *,
        task_id: str = "",
    ) -> Any:
        """Send one Designated Path Navigation request (API 3066)."""

        points = tuple(str(point).strip() for point in route_points)
        if len(points) < 2:
            raise ValueError("SEER designated route requires at least two points")
        if len(points) > 32:
            raise ValueError("SEER designated route has more than 32 points")
        if any(not point for point in points):
            raise ValueError("SEER designated route contains an empty point id")
        if any(len(point) > 128 for point in points):
            raise ValueError("SEER designated route point id is too long")
        if any(points[index] == points[index - 1] for index in range(1, len(points))):
            raise ValueError("SEER designated route contains duplicate adjacent points")

        # SEER API 3066 requires every task_id to be globally unique, including
        # IDs used by earlier completed requests. A Block Builder Recipe may be
        # executed repeatedly, so a user value is a readable prefix rather than
        # the complete ID. Always append a per-execution nonce.
        prefix = str(task_id or "").strip() or "seer-route"
        nonce = f"-{time.time_ns()}"
        base_task_id = prefix[: max(1, 128 - len(nonce))] + nonce
        move_task_list = []
        for index, (source, target) in enumerate(zip(points, points[1:]), start=1):
            suffix = f"-{index}" if len(points) > 2 else ""
            segment_task_id = base_task_id[: 128 - len(suffix)] + suffix
            move_task_list.append(
                {
                    "source_id": source,
                    "id": target,
                    "task_id": segment_task_id,
                }
            )
        result = await self.client.send_command(
            "goto_route",
            move_task_list=move_task_list,
        )
        self.client._pending_station = points[-1]
        self.client._station = ""
        self.client._status = "Driving"
        return result

    async def free_nav(
        self,
        x: float,
        y: float,
        theta: float,
        *,
        task_id: str = "",
    ) -> Any:
        """Run official API 3051 Free Navigation using the ``freeGo`` body."""

        body = {
            "id": "SELF_POSITION",
            "freeGo": {
                "x": self.client.position_to_native(x),
                "y": self.client.position_to_native(y),
                "theta": self.client.angle_to_native(theta),
            },
        }
        if task_id:
            body["task_id"] = str(task_id)
        result = await self.client.send_command(
            "free_nav",
            **body,
        )
        self.client._pending_station = ""
        self.client._station = ""
        self.client._status = "Driving"
        return result

    async def pause(self) -> Any:
        result = await self.client.send_command("pause_task")
        self.client._status = "Paused"
        return result

    async def resume(self) -> Any:
        result = await self.client.send_command("resume_task")
        self.client._status = "Driving"
        return result

    async def cancel(self) -> Any:
        result = await self.client.send_command("cancel_task")
        self.client._pending_station = ""
        self.client._target_id = ""
        self.client._station = ""
        self.client._status = "Stopped"
        return result

    async def translate(self, distance: float, speed: float, **kwargs: Any) -> Any:
        native_distance = self.client.position_to_native(distance)
        body = {"dist": abs(native_distance)}
        lateral = bool(kwargs.get("lateral", False))
        if speed is None or float(speed) == 0.0:
            raise ValueError("SEER translation speed must be non-zero")
        native_speed = self.client.position_to_native(speed)
        direction = -1.0 if native_distance < 0.0 else 1.0
        body["vy" if lateral else "vx"] = direction * abs(native_speed)
        if kwargs.get("mode") is not None:
            body["mode"] = kwargs["mode"]
        result = await self.client.send_command("translate", **body)
        self.client._status = "Driving"
        return result

    async def turn(self, angle: float, angular_speed: float, mode: Any = None) -> Any:
        native_angle = self.client.angle_to_native(angle)
        native_speed = self.client.angle_to_native(angular_speed)
        if native_speed == 0.0:
            raise ValueError("SEER turn angular_speed must be non-zero")
        direction = -1.0 if native_angle < 0.0 else 1.0
        body = {
            "angle": abs(native_angle),
            "vw": direction * abs(native_speed),
        }
        if mode is not None:
            body["mode"] = mode
        result = await self.client.send_command("turn", **body)
        self.client._status = "Driving"
        return result
