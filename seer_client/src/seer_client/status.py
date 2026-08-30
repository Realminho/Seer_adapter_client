"""State polling and SEER-payload-to-JiBot-cache mapping."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional

from .protocol import ApiNumber, ApiPort
from .capabilities import (
    detect_jack_support, detect_jack_support_from_robot_model, jack_model_capability,
)

if TYPE_CHECKING:
    from .client import SeerClient



CONTROLLER_STATUS_SCHEMA = 1


def write_controller_status_cache(path: Path, client: Any) -> None:
    """Publish the direct SEER controller point/state for the WebUI.

    The normal WebUI snapshot is VDA5050 state and its ``lastNodeId`` may lag or
    intentionally differ from Roboshop's direct ``current_station``.  Keep this
    small sidecar so the map can show/use the controller's own point without
    changing the shared adapter.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": CONTROLLER_STATUS_SCHEMA,
        "updated_at": time.time(),
        "current_station": str(getattr(client, "_station", "") or ""),
        "target_id": str(getattr(client, "_target_id", "") or ""),
        "task_status": getattr(client, "_task_status", None),
        "x": float(getattr(client, "_x", 0.0) or 0.0),
        "y": float(getattr(client, "_y", 0.0) or 0.0),
        "theta": float(getattr(client, "_th", 0.0) or 0.0),
        "robot_model": str(getattr(client, "_robot_model", "") or ""),
        "robot_model_loaded": bool(getattr(client, "_robot_model_loaded", False)),
        "robot_model_query_error": str(getattr(client, "_robot_model_query_error", "") or ""),
        "jack_supported": getattr(client, "_jack_supported", None),
        "jack_model_enabled": getattr(client, "_jack_model_enabled", None),
        "jack_runtime_enabled": getattr(client, "_jack_runtime_enabled", None),
        "jack_capability_reason": str(getattr(client, "_jack_capability_reason", "") or ""),
    }
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(str(temporary), str(target))


def read_controller_status_cache(
    path: Path, *, max_age_sec: float = 3.0
) -> Optional[Mapping[str, Any]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema") != CONTROLLER_STATUS_SCHEMA:
        return None
    try:
        age = max(0.0, time.time() - float(payload.get("updated_at", 0.0)))
    except (TypeError, ValueError):
        return None
    if age > float(max_age_sec):
        return None
    return payload

TASK_STATUS_NAMES = {
    0: "Stopped",      # no task
    1: "Waiting",
    2: "Driving",
    3: "Paused",
    4: "Stopped",      # completed
    5: "Failed",
    6: "Canceled",
}


class SeerStatusService:
    """Own state queries and maintain the JiBot-compatible cached attributes."""

    # Retained because the shared adapter and older tests inspect this symbol.
    POLL_QUERIES = (ApiNumber.STATUS_ALL1,)

    STATUS_KEYS = (
        "x", "y", "angle", "confidence", "current_station",
        "battery_level", "charging", "charging_status", "voltage", "current",
        "vx", "vy", "w", "status", "run_status", "running_status",
        "mode", "robot_mode", "operation_mode", "task_status", "target_id",
        "blocked", "block_reason", "emergency", "driver_emc", "soft_emc",
        "electric", "motor_flag", "motor_enabled", "motor_enable_status",
        "current_map", "maps", "map_names", "DI", "DO",
    )

    def __init__(self, client: "SeerClient") -> None:
        self.client = client
        self._next_model_query_at = 0.0
        self._model_retry_sec = 10.0

    async def poll_once(self) -> None:
        conn = self.client._ports.get(ApiPort.STATE)
        if conn is None:
            return
        # API 1100 already contains task, block, emergency and I/O fields.
        # Query it once without laser payloads instead of issuing four rapid
        # requests on the same port every poll cycle.
        response = await conn.request(
            ApiNumber.STATUS_ALL1,
            {
                "keys": list(self.STATUS_KEYS),
                "return_laser": False,
                "return_beams3D": False,
            },
        )
        if isinstance(response, Mapping):
            self.apply(ApiNumber.STATUS_ALL1, response)
            if "DI" in response or "DO" in response:
                self.client.io.cache.update(response)
            # Some older controller builds ignore selected ``keys`` in 1100
            # and omit task/block fields.  Preserve the one-request fast path
            # for current firmware, but fill only the missing groups.  The
            # per-port connection lock and request interval still serialize
            # these fallbacks, avoiding the sequence desync caused by rapid
            # uncoordinated requests.
            missing_task = not any(
                key in response for key in ("task_status", "target_id")
            )
            missing_block = not any(
                key in response for key in ("blocked", "block_reason")
            )
            supplemental = (
                (ApiNumber.STATUS_TASK, ApiNumber.STATUS_BLOCK)
                if missing_task and missing_block
                else ()
            )
            for api in supplemental:
                extra = await conn.request(api, {})
                if isinstance(extra, Mapping):
                    self.apply(api, extra)

            # robot.model is not part of status-all (1100).  SEER exposes it
            # through the dedicated status-model request 1500.  Resolve it once
            # per connection (retrying non-fatally on older/busy controllers) so
            # Jack capability does not stay in the UI's indeterminate state.
            if (
                not bool(getattr(self.client, "_robot_model_loaded", False))
                and bool(getattr(self.client, "_robot_model_query_error", ""))
            ):
                now = time.monotonic()
                if now >= self._next_model_query_at:
                    await self._refresh_robot_model(conn)

            cache_path = str(os.getenv("SEER_STATUS_CACHE_PATH", "") or "").strip()
            if cache_path:
                try:
                    await asyncio.to_thread(
                        write_controller_status_cache, Path(cache_path), self.client
                    )
                except OSError:
                    pass

    async def _refresh_robot_model(self, conn: Any) -> Mapping[str, Any]:
        self._next_model_query_at = time.monotonic() + self._model_retry_sec
        try:
            # API 1500 request body is empty by protocol definition.
            response = await conn.request(ApiNumber.STATUS_MODEL, {})
        except Exception as exc:
            self.client._robot_model_query_error = str(exc)
            return {}
        if not isinstance(response, Mapping) or not response:
            self.client._robot_model_query_error = "API 1500 returned an empty robot.model"
            return {}

        model, _ = detect_jack_support_from_robot_model(response)
        model_enabled, model_reason = jack_model_capability(response)
        self.client._robot_model = model
        self.client._robot_model_loaded = True
        self.client._robot_model_query_error = ""
        self.client._jack_model_enabled = model_enabled

        # API 1027 is the controller's explicit Jack runtime/capability status.
        # It includes ``jack_enable`` which matches RoboShop's enabled/forbidden
        # state.  Final support requires BOTH the model device and API 1027 to
        # say Jack is enabled.  Any unknown/failure is fail-safe disabled.
        runtime_enabled = None
        runtime_reason = "API 1027 Jack 상태 미확인"
        try:
            jack_status = await conn.request(ApiNumber.STATUS_JACK, {})
        except Exception as exc:
            jack_status = {}
            runtime_reason = f"API 1027 조회 실패: {exc}"
        if isinstance(jack_status, Mapping) and jack_status:
            try:
                ret_code = int(jack_status.get("ret_code", 0) or 0)
            except (TypeError, ValueError):
                ret_code = -1
            if ret_code != 0:
                runtime_reason = str(jack_status.get("err_msg", "") or f"API 1027 ret_code={ret_code}")
            elif isinstance(jack_status.get("jack_enable"), bool):
                runtime_enabled = bool(jack_status.get("jack_enable"))
                runtime_reason = f"API 1027 jack_enable={str(runtime_enabled).lower()}"

        self.client._jack_runtime_enabled = runtime_enabled
        self.client._jack_supported = bool(model_enabled is True and runtime_enabled is True)
        if model_enabled is not True:
            self.client._jack_capability_reason = model_reason
        elif runtime_enabled is not True:
            self.client._jack_capability_reason = runtime_reason
        else:
            self.client._jack_capability_reason = f"{model_reason} · {runtime_reason}"
        return response

    async def get_robot_model(self) -> Mapping[str, Any]:
        conn = self.client._ports.get(ApiPort.STATE)
        if conn is None:
            self.client._robot_model_query_error = "STATE port is not connected"
            return {}
        return await self._refresh_robot_model(conn)

    async def query(self, command: str, api: ApiNumber) -> Mapping[str, Any]:
        response = await self.client.send_command(command)
        if isinstance(response, Mapping):
            self.apply(api, response)
            return response
        return {}

    def apply(self, api_number: int, payload: Mapping[str, Any]) -> None:
        api = int(api_number)
        combined = api in (int(ApiNumber.STATUS_INFO), int(ApiNumber.STATUS_ALL1))

        if combined and not bool(getattr(self.client, "_robot_model_loaded", False)):
            # Compatibility only.  The authoritative capability decision comes
            # from dedicated API 1500 robot.model.  Do not overwrite it later
            # with a generic status field or a model-name heuristic.
            model, jack_supported = detect_jack_support(payload)
            if model:
                self.client._robot_model = model
            if jack_supported is not None:
                self.client._jack_supported = jack_supported

        if combined or api == int(ApiNumber.STATUS_LOC):
            self._apply_location(payload)
        if combined or api == int(ApiNumber.STATUS_BATTERY):
            self._apply_battery(payload)
        if combined or api == int(ApiNumber.STATUS_SPEED):
            self._apply_speed(payload)
        if combined or api == int(ApiNumber.STATUS_RUN):
            self._apply_run_state(payload)
        if combined or api == int(ApiNumber.STATUS_MODE):
            self._apply_mode(payload)
        if combined or api == int(ApiNumber.STATUS_TASK):
            self._apply_task(payload)
        if combined or api == int(ApiNumber.STATUS_BLOCK):
            self._apply_block(payload)
        if combined or api == int(ApiNumber.STATUS_EMERGENCY):
            self._apply_emergency(payload)
        if combined or api == int(ApiNumber.STATUS_MAP):
            self._apply_map(payload)

    def _apply_location(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        if "x" in payload:
            c._x = c.position_from_native(float(payload["x"]))
        if "y" in payload:
            c._y = c.position_from_native(float(payload["y"]))
        if "angle" in payload:
            c._th = c.angle_from_native(float(payload["angle"]))
        if "current_station" in payload:
            c._station = str(payload.get("current_station") or "")
        confidence = payload.get("confidence", payload.get("localization_score"))
        if confidence is not None:
            c._localization_score = float(confidence)

    def _apply_battery(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        level = payload.get("battery_level")
        if level is not None:
            numeric_level = float(level)
            c._battery = numeric_level * 100.0 if 0.0 <= numeric_level <= 1.0 else numeric_level
            c._battery_known = True
        if "charging" in payload:
            c._charging = bool(payload["charging"])
        elif "charging_status" in payload:
            c._charging = bool(payload["charging_status"])
        if "voltage" in payload:
            c._battery_voltage = float(payload["voltage"])
        if "current" in payload:
            c._battery_current = float(payload["current"])

    def _apply_speed(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        for source, target in (("vx", "_vx"), ("vy", "_vy")):
            if source in payload:
                setattr(c, target, c.position_from_native(float(payload[source])))
        if "w" in payload:
            c._w = c.angle_from_native(float(payload["w"]))

    def _apply_run_state(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        for key in ("status", "run_status", "running_status"):
            if key in payload:
                c._status = str(payload[key])
                break
        for key in ("motor_flag", "motor_enabled", "motor_enable_status"):
            if key in payload:
                c._motor_flag = bool(payload[key])
                c._motor_flag_source = key
                break
        self._refresh_safety_cache()

    def _apply_mode(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        for key in ("mode", "robot_mode", "operation_mode"):
            if key in payload:
                c._mode = str(payload[key])
                break

    def _apply_task(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        if "target_id" in payload:
            c._target_id = str(payload["target_id"] or "")
        if "task_status" not in payload:
            return
        raw_status = payload["task_status"]
        try:
            task_status: Any = int(raw_status)
        except (TypeError, ValueError):
            task_status = raw_status
        c._task_status = task_status
        c._status = TASK_STATUS_NAMES.get(task_status, str(raw_status))
        if task_status in (1, 2, 3):
            c._station = ""
        elif task_status == 4:
            c._station = c._target_id or c._pending_station
            c._pending_station = ""
        elif task_status in (5, 6):
            c._pending_station = ""

    def _apply_block(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        if "blocked" in payload:
            c._blocked = bool(payload["blocked"])
            c._brake = c._blocked
            if c._blocked:
                c._status = "#brake"
            elif c._status == "#brake":
                c._status = TASK_STATUS_NAMES.get(c._task_status, "Stopped")
        if "block_reason" in payload:
            c._block_reason = str(payload["block_reason"] or "")
        self._refresh_safety_cache()

    def _apply_emergency(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        if "emergency" in payload:
            c._physical_emergency = bool(payload["emergency"])
        if "driver_emc" in payload:
            c._driver_emergency = bool(payload["driver_emc"])
        if "soft_emc" in payload:
            c._soft_emergency = bool(payload["soft_emc"])
        if "electric" in payload:
            c._electric_state = bool(payload["electric"])
            # Some SEER 3.4 firmware reports electric=false while AUTO motion
            # is still accepted. Explicit motor_* fields remain authoritative.
            explicit_motor_keys = ("motor_flag", "motor_enabled", "motor_enable_status")
            if not any(key in payload for key in explicit_motor_keys):
                c._motor_flag = c._electric_state
                c._motor_flag_source = "electric"
        c._emergency = bool(
            c._physical_emergency or c._driver_emergency or c._soft_emergency
        )
        self._refresh_safety_cache()

    def _refresh_safety_cache(self) -> None:
        c = self.client
        if c._physical_emergency:
            system_status = "estop pressed"
        elif c._soft_emergency or c._driver_emergency:
            system_status = "enter estop"
        elif c._blocked:
            system_status = "#brake"
        else:
            system_status = "normal"
        c._robot_safety = {
            "system_status": system_status,
            "hmi_estop": c._physical_emergency,
            "pc_estop": bool(c._soft_emergency or c._driver_emergency),
            "motor_error": False,
            "motor_enable": bool(c._motor_flag),
        }
        c._robot_safety_last_update = time.monotonic()

    def _apply_map(self, payload: Mapping[str, Any]) -> None:
        c = self.client
        if "current_map" in payload:
            c._current_map = str(payload["current_map"] or "")
        maps = payload.get("maps", payload.get("map_names"))
        if isinstance(maps, list):
            c._available_maps = list(maps)

    async def get_robot_info(self) -> Mapping[str, Any]:
        response = await self.client.send_command(
            "query_status_all",
            keys=list(self.STATUS_KEYS),
            return_laser=False,
            return_beams3D=False,
        )
        if isinstance(response, Mapping):
            self.apply(ApiNumber.STATUS_ALL1, response)
            if "DI" in response or "DO" in response:
                self.client.io.cache.update(response)
            return response
        return {}

    async def get_location(self) -> Mapping[str, Any]:
        return await self.query("query_location", ApiNumber.STATUS_LOC)

    async def get_battery(self) -> Mapping[str, Any]:
        return await self.query("query_battery", ApiNumber.STATUS_BATTERY)

    async def get_task(self) -> Mapping[str, Any]:
        return await self.query("query_task", ApiNumber.STATUS_TASK)

    async def get_blocked(self) -> Mapping[str, Any]:
        return await self.query("query_block", ApiNumber.STATUS_BLOCK)

    async def get_emergency(self) -> Mapping[str, Any]:
        return await self.query("query_emergency", ApiNumber.STATUS_EMERGENCY)

    async def get_map(self) -> Mapping[str, Any]:
        return await self.query("query_map", ApiNumber.STATUS_MAP)

    async def get_motor_state(self) -> Optional[bool]:
        await self.get_robot_info()
        return self.client._motor_flag
