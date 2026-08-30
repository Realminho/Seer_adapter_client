import re
import json
import asyncio
import time
from types import MappingProxyType

from .recorder import recorder_from_env


class JIBOT:
    # JIBOT API command list and required request fields.
    # JIBOT API 명령 목록과 요청 시 필요한 필드 목록이다.
    #
    # Source: command list already encoded from prior prompt/session material.
    # 출처: 이전 prompt/session 자료에서 코드화된 명령 리스트를 기준으로 정리했다.
    #
    # 보완 필요 / Needs confirmation:
    # - This table validates field presence only; value type/range rules are not
    #   fully documented here.
    # - 여기서는 필드 존재 여부만 검증한다. 값의 타입/범위 규칙은 JIBOT
    #   문서 또는 실기 응답으로 추가 확인이 필요하다.
    # - UmConnect credentials (user/password/device_type) come from the
    #   JIBOT() constructor args, sourced from the [jibot_client] config section.
    # - UmConnect 인증 정보(user/password/device_type)는 JIBOT() 생성자 인자로
    #   전달되며, [jibot_client] 설정 섹션에서 가져온다.
    COMMAND_SPECS = MappingProxyType({
        "AdvGetParamsRoutes": ("routes",),
        "AdvGetRoutes": ("routes",),
        "AdvGetTemplateRoutes": ("routes",),
        "AdvSetParamsRoutes": ("routes",),
        "AdvSetRoutes": ("routes",),
        "AdvSetTemplateRoutes": ("routes",),
        "AdvTrackManually": ("num", "mode"),
        "GetPolygons": (),
        "SetMap": ("name",),
        "UmConnect": (),
        "UmDock": (),
        "UmDrive": ("trans", "rot", "speed", "lat"),
        "UmGetBatteryInfo": (),
        "UmGetClientLog": (),
        "UmGetConfig": (),
        "UmGetCurTask": (),
        "UmGetInput": (),
        "UmGetLaser": ("index",),
        "UmGetLocState": (),
        "UmGetMap": (),
        "UmGetMapName": (),
        "UmGetMappingState": (),
        "UmGetMotorInfo": (),
        "UmGetMotorState": (),
        "UmGetName": (),
        "UmGetOutput": (),
        "UmGetPath": (),
        "UmGetPathPlanningClearances": (),
        "UmGetRailInfo": (),
        "UmGetRobotInfo": (),
        "UmGetRobotSize": (),
        "UmGetRoutes": (),
        "UmGetSafeDrive": (),
        "UmGetTaskInfo": (),
        "UmGetVirtualIO": (),
        "UmGoto": ("target",),
        "UmIdle": (),
        "UmLocalize": ("target", "goal", "poseX", "poseY", "poseTh"),
        "UmMapping": ("name", "flag", "target"),
        "UmRMSGetConfig": (),
        "UmRMSGetMap": (),
        "UmRMSGetMapName": (),
        "UmRMSGetRoutes": (),
        "UmRMSSetCancelJob": ("section",),
        "UmRMSSetConfig": ("section", "objs"),
        "UmRMSSetMap": ("section",),
        "UmRMSSetRoutes": ("routes",),
        "UmReloadConfig": (),
        "UmRoutes": ("routes", "key", "id"),
        "UmSchedulerList": ("size", "list"),
        "UmSchedulerThis": ("name", "key", "content"),
        "UmSetConfig": ("section", "objs"),
        "UmSetMotor": ("flag",),
        "UmSetOutput": ("length", "high", "low"),
        "UmSetOutputByte": ("num", "flag"),
        "UmSetRoutes": ("routes",),
        "UmSetSafeDrive": ("flag",),
        "UmSetVolume": ("volume",),
        "UmStop": (),
        "error": (),
    })

    COMMAND_OPTIONAL_PARAMS = MappingProxyType({
        "UmGoto": ("goal", "poseX", "poseY", "poseTh", "strict"),
        # urobot's UmDock handler (JModeCharge::Start) reads these JSON keys.
        # All optional: a bare UmDock keeps the default dock approach.
        # detect_charging_signal lets the robot charge in place (skip the
        # reflector back-up) when it already senses the charger.
        "UmDock": (
            "goal",
            "detect_charging_signal",
            "dock_move_additional_dist",
            "dock_rotate_additional_angle",
            "need_turn_around",
            "need_heading",
            "use_avoid_area",
            "disable_motor_secs",
            "clearance_back_min",
            "clearance_front_min",
        ),
    })

    COMMAND_METHODS = MappingProxyType({
        "AdvGetParamsRoutes": "adv_get_params_routes",
        "AdvGetRoutes": "adv_get_routes",
        "AdvGetTemplateRoutes": "adv_get_template_routes",
        "AdvSetParamsRoutes": "adv_set_params_routes",
        "AdvSetRoutes": "adv_set_routes",
        "AdvSetTemplateRoutes": "adv_set_template_routes",
        "AdvTrackManually": "adv_track_manually",
        "GetPolygons": "get_polygons",
        "SetMap": "set_map",
        "UmConnect": "um_connect",
        "UmDock": "um_dock",
        "UmDrive": "um_drive",
        "UmGetBatteryInfo": "um_get_battery_info",
        "UmGetClientLog": "um_get_client_log",
        "UmGetConfig": "um_get_config",
        "UmGetCurTask": "um_get_cur_task",
        "UmGetInput": "um_get_input",
        "UmGetLaser": "um_get_laser",
        "UmGetLocState": "um_get_loc_state",
        "UmGetMap": "um_get_map",
        "UmGetMapName": "um_get_map_name",
        "UmGetMappingState": "um_get_mapping_state",
        "UmGetMotorInfo": "um_get_motor_info",
        "UmGetMotorState": "um_get_motor_state",
        "UmGetName": "um_get_name",
        "UmGetOutput": "um_get_output",
        "UmGetPath": "um_get_path",
        "UmGetPathPlanningClearances": "um_get_path_planning_clearances",
        "UmGetRailInfo": "um_get_rail_info",
        "UmGetRobotInfo": "um_get_robot_info",
        "UmGetRobotSize": "um_get_robot_size",
        "UmGetRoutes": "um_get_routes",
        "UmGetSafeDrive": "um_get_safe_drive",
        "UmGetTaskInfo": "um_get_task_info",
        "UmGetVirtualIO": "um_get_virtual_io",
        "UmGoto": "um_goto",
        "UmIdle": "um_idle",
        "UmLocalize": "um_localize",
        "UmMapping": "um_mapping",
        "UmRMSGetConfig": "um_rms_get_config",
        "UmRMSGetMap": "um_rms_get_map",
        "UmRMSGetMapName": "um_rms_get_map_name",
        "UmRMSGetRoutes": "um_rms_get_routes",
        "UmRMSSetCancelJob": "um_rms_set_cancel_job",
        "UmRMSSetConfig": "um_rms_set_config",
        "UmRMSSetMap": "um_rms_set_map",
        "UmRMSSetRoutes": "um_rms_set_routes",
        "UmReloadConfig": "um_reload_config",
        "UmRoutes": "um_routes",
        "UmSchedulerList": "um_scheduler_list",
        "UmSchedulerThis": "um_scheduler_this",
        "UmSetConfig": "um_set_config",
        "UmSetMotor": "um_set_motor",
        "UmSetOutput": "um_set_output",
        "UmSetOutputByte": "um_set_output_byte",
        "UmSetRoutes": "um_set_routes",
        "UmSetSafeDrive": "um_set_safe_drive",
        "UmSetVolume": "um_set_volume",
        "UmStop": "um_stop",
        "error": "error",
    })

    def __init__(
        self,
        robot_ip,
        robot_port=7273,
        config=None,
        charging_status="charging",
        recorder=None,
        user="test",
        password="test",
        device_type="pc",
        command_timeout=3.0,
        recv_buffer_bytes=32768,
        status_log_interval_sec=5.0,
        battery_log_interval_sec=30.0,
    ):
        self.robot_ip = robot_ip
        self.robot_port = robot_port
        self.user = user
        self.password = password
        self.device_type = device_type
        self.command_timeout = command_timeout
        self.recv_buffer_bytes = recv_buffer_bytes
        self.status_log_interval_sec = status_log_interval_sec
        self.battery_log_interval_sec = battery_log_interval_sec

        self.config = config
        self.charging_status = charging_status
        self.recorder = recorder or recorder_from_env(
            metadata={
                "robot_ip": robot_ip,
                "robot_port": robot_port,
            }
        )
        self._last_status_log_at = 0.0
        self._last_battery_info_log_at = 0.0
        # Monotonic timestamp of the most recent inbound JIBOT frame. Used by the
        # adapter to detect a silent (half-open) link where the socket is alive
        # but the robot has stopped streaming status.
        self._last_rx_at = 0.0
        self._tx_sequence = 0
        self._rx_sequence = 0

        self.reader = None
        self.writer = None

        self._mode = ""
        self._status = ""
        self._battery = None
        self._battery_known = False
        self._battery_voltage = None
        self._battery_current = None
        self._battery_health = None
        self._battery_temperatures = []
        self._battery_cells = None
        self._running = False
        self._status_snapshot_at = 0.0
        self._x = 0.0
        self._y = 0.0
        self._th = 0.0
        self._motor_flag = True
        self._localization_score = 0.0
        self._station = ""
        self._charging = False
        # Latest BMS reading from the ROS /jrobot_status listener (bms_ros_listener);
        # monotonic timestamp of the last successful set_bms, for staleness checks.
        self._bms_last_update = 0.0
        # Latest /jrobot_status safety/motor fields from bms_ros_listener
        # (system_status, motor_enable, hmi_estop, ...); monotonic stamp for
        # staleness. Empty until the ROS listener feeds it (on-robot only).
        self._robot_safety = {}
        self._robot_safety_last_update = 0.0

        # Real AMR over TCP. Overridden to True by SimulatedJIBOT so the adapter
        # can adapt behaviour (e.g. drive to order-supplied node positions and
        # advertise simulation mode in state.information).
        self.is_simulator = False

        # Map node positions keyed by node name: {name: (x, y, theta)} in the
        # same millimetre frame the robot reports its pose in. Populated from
        # the UmGetMap response so the adapter can map a pose to a node id.
        self._map_nodes = {}
        self._map_node_categories = {}
        # PathPoint objects are graph geometry rather than addressable goal
        # nodes. Keep their poses separately so callers can explicitly fall
        # back to pose-based navigation without exposing them as named goals.
        self._map_path_points = {}
        # The raw UmGetMap response, kept so the adapter can persist the full
        # map for the simulator to rebuild from.
        self._map_raw = None
        self._laser_raw = None
        self._laser_updated_at = 0.0
        self._cur_task = None
        self._task_info = None
        self._path = None

        self._receive_task = None
        self._status_task = None
        self._response_queue = asyncio.Queue()

    # -------------------------
    # CONNECTION
    # -------------------------
    async def connect_socket(self):
        """Open the JIBOT TCP socket and start the receive loop.

        JIBOT TCP 소켓을 열고 수신 루프를 시작한다.
        """
        print(f"[JIBOT TCP CONNECTING] ip={self.robot_ip} port={self.robot_port}")
        self.reader, self.writer = await asyncio.open_connection(
            self.robot_ip, self.robot_port
        )
        self._running = True
        # Seed the RX watchdog so a fresh connection is not seen as stale before
        # the first status frame arrives.
        self._last_rx_at = time.monotonic()
        self._receive_task = asyncio.create_task(self.receive_data_from_server())
        print(f"[JIBOT TCP CONNECTED] ip={self.robot_ip} port={self.robot_port}")

    def is_connected(self) -> bool:
        """Return True while the JIBOT TCP receive loop is alive.

        JIBOT TCP 수신 루프가 살아있는 동안 True를 반환한다.

        The flag is cleared by ``disconnect()`` and whenever
        ``receive_data_from_server`` exits (EOF or socket error), so a dropped
        connection is reflected here without an explicit disconnect call.
        ``disconnect()`` 호출 시, 또는 ``receive_data_from_server``가 EOF/소켓
        오류로 종료될 때 플래그가 내려가므로, 명시적 disconnect 없이 끊긴 연결도
        여기에 반영된다.
        """
        return bool(self._running)

    def seconds_since_last_rx(self) -> float:
        """Seconds since the last inbound JIBOT frame (monotonic).

        마지막 JIBOT 수신 프레임 이후 경과 시간(초, monotonic).
        """
        return time.monotonic() - self._last_rx_at

    def is_rx_stale(self, timeout: float) -> bool:
        """True when a connected link has received nothing for ``timeout`` seconds.

        연결된 링크가 ``timeout`` 초 동안 아무 것도 수신하지 못했을 때 True.

        JIBOT streams status frames continuously, so a gap longer than
        ``timeout`` means the link is silent (e.g. half-open TCP) even though the
        socket still looks alive. Returns False when disconnected — that case is
        already covered by ``is_connected()`` — or when ``timeout`` is disabled.
        JIBOT는 status 프레임을 계속 스트리밍하므로, ``timeout``보다 긴 공백은
        소켓이 살아있어도 링크가 침묵(half-open TCP 등)임을 뜻한다. 연결이 끊긴
        경우(이미 ``is_connected()``가 처리)나 ``timeout``이 비활성일 때는 False.
        """
        if not self._running or timeout <= 0:
            return False
        return self.seconds_since_last_rx() > timeout

    async def disconnect(self):
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()

        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

        if self.recorder:
            self.recorder.close()

    async def _close_stale_connection(self):
        """Tear down a dead socket and receive loop before reconnecting.

        재연결 전에 죽은 소켓과 수신 루프를 정리한다.
        """
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        self._receive_task = None

        if self.writer is not None:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                # The socket is already gone; nothing more to clean up.
                pass

        self.reader = None
        self.writer = None

    async def reconnect(self):
        """Re-open the JIBOT TCP socket and re-run the API login.

        JIBOT TCP 소켓을 다시 열고 API 로그인을 재실행한다.

        Used by the adapter's reconnection supervisor after a dropped or failed
        connection so the robot link recovers without restarting the adapter.
        끊기거나 실패한 연결 이후 adapter의 재연결 감시 루프가 호출하며, adapter를
        재시작하지 않고 로봇 링크를 복구한다.
        """
        await self._close_stale_connection()
        await self.connect_socket()
        await self.connect()

    # -------------------------
    # SEND JSON COMMAND
    # -------------------------
    async def json_cmd_to_jibot(self, json_cmd):
        """Serialize and send one JIBOT JSON command frame.

        JIBOT JSON 명령 1개를 직렬화해서 TCP 프레임으로 전송한다.

        Frame format / 프레임 형식:
            $#<json byte length>##<compact json>$~

        보완 필요 / Needs confirmation:
            The current length check uses Python string length after JSON
            serialization. This is correct for the observed ASCII command
            payloads, but should be confirmed against the official JIBOT spec
            if non-ASCII command values are sent.
            현재 길이 계산은 JSON 문자열 길이를 사용한다. 관측된 ASCII 명령
            payload에서는 맞지만, 비 ASCII 값 전송 시 공식 JIBOT 스펙 확인이
            필요하다.
        """
        json_data = json.dumps(json_cmd, separators=(",", ":"))
        total_length = len(json_data.encode("utf-8"))
        formatted_message = f"$#{total_length}##{json_data}$~"
        self._record_tx(formatted_message, json_cmd)
        self._log_tx(json_cmd, total_length, json_data, formatted_message)

        self.writer.write(formatted_message.encode("utf-8"))
        await self.writer.drain()

    def validate_command_params(self, command, params):
        """Validate command names and required fields before TCP transmission.

        TCP 전송 전에 명령명과 필수 필드를 검증한다.
        """
        if command not in self.COMMAND_SPECS:
            raise ValueError(f"Unsupported JIBOT command: {command}")

        required_params = set(self.COMMAND_SPECS[command])
        optional_params = set(self.COMMAND_OPTIONAL_PARAMS.get(command, ()))
        allowed_params = required_params | optional_params
        given_params = set(params.keys())
        missing_params = sorted(required_params - given_params)
        unknown_params = sorted(given_params - allowed_params)

        if missing_params:
            raise ValueError(
                f"{command} missing required params: {', '.join(missing_params)}"
            )

        if unknown_params:
            raise ValueError(
                f"{command} got unknown params: {', '.join(unknown_params)}"
            )

    def build_command(self, command, gap=-1, **params):
        """Build a JIBOT payload with #CMD# and #GAP#.

        #CMD#와 #GAP#을 포함한 JIBOT payload를 만든다.

        #GAP# is passed through to the robot as the command interval/period
        value used by the native API. 보완 필요 / Needs confirmation: the exact
        unit and semantics of each #GAP# value should be verified with JIBOT
        documentation or recordings for every command.
        #GAP#은 JIBOT native API의 명령 주기/간격 값으로 전달된다. 보완 필요:
        명령별 정확한 단위와 의미는 JIBOT 문서 또는 녹화 데이터로 확인해야 한다.
        """
        params = self._normalize_command_params(command, params)
        params = self._strip_empty_optional_command_params(command, params)
        self.validate_command_params(command, params)

        json_cmd = {
            "#CMD#": command,
            "#GAP#": gap,
        }
        json_cmd.update(params)
        return json_cmd

    def _normalize_command_params(self, command, params):
        if command != "UmGoto":
            return params

        normalized = dict(params)
        target = normalized.get("target")
        target_text = str(target).strip().lower() if target is not None else ""

        if target_text == "":
            if not self._is_empty_command_value(normalized.get("goal")):
                target_text = "goal"
                normalized["target"] = "goal"
            elif any(
                not self._is_empty_command_value(normalized.get(key))
                for key in ("poseX", "poseY", "poseTh")
            ):
                target_text = "pose"
                normalized["target"] = "pose"

        if target_text not in ("goal", "pose"):
            if self._is_empty_command_value(normalized.get("goal")):
                normalized["goal"] = target
            normalized["target"] = "goal"
            target_text = "goal"

        if target_text == "goal":
            for key in ("poseX", "poseY", "poseTh"):
                normalized.pop(key, None)
        elif target_text == "pose" and self._is_empty_command_value(normalized.get("goal")):
            normalized.pop("goal", None)

        return normalized

    def _strip_empty_optional_command_params(self, command, params):
        optional_params = set(self.COMMAND_OPTIONAL_PARAMS.get(command, ()))
        if not optional_params:
            return params

        return {
            key: value
            for key, value in params.items()
            if key not in optional_params
            or not self._is_empty_optional_command_param(command, key, value)
        }

    @staticmethod
    def _is_empty_command_value(value):
        return value is None or (isinstance(value, str) and value.strip() == "")

    def _is_empty_optional_command_param(self, command, key, value):
        if self._is_empty_command_value(value):
            return True

        if command == "UmGoto":
            if key == "strict":
                return value is False or self._is_zero_command_value(value)

        return False

    @staticmethod
    def _is_zero_command_value(value):
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return value == 0
        if isinstance(value, str):
            try:
                return float(value.strip()) == 0
            except ValueError:
                return False
        return False

    async def send_command(self, command, gap=-1, **params):
        """Validate, build, and send one supported JIBOT command.

        지원되는 JIBOT 명령 1개를 검증, 생성, 전송한다.
        """
        json_cmd = self.build_command(command, gap=gap, **params)
        await self.json_cmd_to_jibot(json_cmd)

    async def send_command_and_wait(self, command, gap=-1, timeout=None, accept_errors=False, **params):
        """Send a command and wait for the next matching #CMD# response.

        명령을 전송하고 같은 #CMD# 값을 가진 다음 응답을 기다린다.
        """
        resolved_timeout = self.command_timeout if timeout is None else timeout
        await self.send_command(command, gap=gap, **params)
        return await self.wait_for_response(
            command=command, timeout=resolved_timeout, accept_errors=accept_errors
        )

    async def wait_for_response(self, command=None, timeout=3.0, accept_errors=False):
        """Return the next queued response, optionally filtered by #CMD#.

        큐에 쌓인 다음 응답을 반환하며, 필요하면 #CMD#로 필터링한다.
        """
        deadline = asyncio.get_running_loop().time() + float(timeout)
        skipped_responses = []

        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None

                try:
                    response = await asyncio.wait_for(
                        self._response_queue.get(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    # No frame arrived in time. Resolve to None so callers
                    # (get_map / get_battery_info) take their documented
                    # "response is None" fallback instead of an exception
                    # escaping into robot_info_loop and killing the poll task.
                    return None
                if (
                    command is None
                    or response.get("#CMD#") == command
                    or (accept_errors and response.get("#CMD#") == "error")
                ):
                    return response
                skipped_responses.append(response)
        finally:
            for response in skipped_responses:
                self._response_queue.put_nowait(response)

    def get_available_commands(self):
        return {
            command: {
                "method": self.COMMAND_METHODS[command],
                "params": list(params),
            }
            for command, params in self.COMMAND_SPECS.items()
        }

    def get_available_functions(self):
        return sorted(self.COMMAND_METHODS.values())

    def get_command_spec(self, command):
        if command not in self.COMMAND_SPECS:
            raise ValueError(f"Unsupported JIBOT command: {command}")

        return {
            "command": command,
            "method": self.COMMAND_METHODS[command],
            "params": list(self.COMMAND_SPECS[command]),
        }

    # -------------------------
    # HIGH LEVEL COMMANDS
    # -------------------------
    async def connect(self):
        await self.um_connect()

    async def call_routes(self, name, key, id=None):
        await self.um_routes(name, key, id)

    async def get_robot_info(self, interval_ms):
        await self.um_get_robot_info(gap=interval_ms)

    async def get_localization_info(self, interval_ms):
        await self.um_get_loc_state(gap=interval_ms)

    async def get_battery_info(self, interval_ms, timeout=1.0):
        response = await self.send_command_and_wait(
            "UmGetBatteryInfo",
            gap=interval_ms,
            timeout=timeout,
        )
        if response is None:
            print(
                "[JIBOT BATTERY INFO NO RESPONSE] "
                f"timeout={timeout}s gap={interval_ms}"
            )
            return None
        return response

    async def get_motor_state(self, interval_ms):
        await self.um_get_motor_state(gap=interval_ms)

    async def get_laser(self, index=0, interval_ms=200, timeout=1.0):
        response = await self.send_command_and_wait(
            "UmGetLaser",
            gap=interval_ms,
            timeout=timeout,
            index=index,
        )
        if response is None:
            print(f"[JIBOT GET LASER NO RESPONSE] timeout={timeout}s")
            return self._laser_raw
        self._update_laser_raw(response)
        return self._laser_raw

    async def stop_motion(self):
        await self.um_stop()

    async def get_map(self, interval_ms=-1, timeout=5.0):
        """Fetch the map and cache its node positions.

        Returns the {name: (x, y, theta)} node map, or the cached copy if the
        request times out. The map response is large and pretty-printed, so a
        generous timeout is used.
        """
        response = await self.send_command_and_wait(
            "UmGetMap", gap=interval_ms, timeout=timeout
        )
        if response is None:
            print(f"[JIBOT GET MAP NO RESPONSE] timeout={timeout}s")
            return self._map_nodes
        # process_data already parses the response off the receive loop, but
        # parse here too in case this path observed it first.
        self._update_map_nodes(response)
        return self._map_nodes

    # Map object categories that represent addressable nodes (stations, docks,
    # goals). PathPoint/AvoidArea are internal geometry, not VDA nodes.
    _MAP_NODE_CATEGORIES = ("Goal", "GoalWithHeading", "Dock")

    def _update_map_nodes(self, response):
        """Parse addressable nodes and PathPoint poses from an UmGetMap response."""
        objs = response.get("Objs")
        if not isinstance(objs, dict):
            return
        # Keep the full response so the adapter can snapshot the raw map.
        self._map_raw = response

        nodes = {}
        categories = {}
        for category in self._MAP_NODE_CATEGORIES:
            for obj in objs.get(category, []) or []:
                name = obj.get("name")
                pose = obj.get("pose")
                if not name or not pose:
                    continue
                parts = str(pose).split()
                if len(parts) < 2:
                    continue
                try:
                    x = float(parts[0])
                    y = float(parts[1])
                    theta = float(parts[2]) if len(parts) > 2 else 0.0
                except ValueError:
                    continue
                nodes[name] = (x, y, theta)
                categories[name] = category

        path_points = {}
        for obj in objs.get("PathPoint", []) or []:
            name = obj.get("name")
            pose = obj.get("pose")
            if not name or not pose:
                continue
            parts = str(pose).split()
            if len(parts) < 2:
                continue
            try:
                x = float(parts[0])
                y = float(parts[1])
                theta = float(parts[2]) if len(parts) > 2 else 0.0
            except ValueError:
                continue
            path_points[name] = (x, y, theta)

        if nodes:
            self._map_nodes = nodes
            self._map_node_categories = categories
        self._map_path_points = path_points
        if nodes or path_points:
            print(
                "[JIBOT MAP UPDATED] source=UmGetMap "
                f"nodes={len(nodes)} pathPoints={len(path_points)}"
            )

    def get_path_point_pose(self, name):
        """Return a PathPoint's raw map pose, or None when it is not present."""
        return self._map_path_points.get(name)

    def _update_laser_raw(self, response):
        self._laser_raw = response
        self._laser_updated_at = time.time()
        data = response.get("data")
        scanner_count = len(data) if isinstance(data, list) else 0
        print(f"[JIBOT LASER UPDATED] scanners={scanner_count}")

    async def goto_point(self, point, strict=False):
        await self.um_goto("goal", point, None, None, None, strict)
    
    async def goto_xyz(self, x, y, z, strict=False):
        """Drive to an absolute pose. ``z`` is the arrival heading in degrees.

        ``z`` is required. A ``None`` here would be stripped by
        _strip_empty_optional_command_params, and urobot silently ignores a
        ``target=pose`` UmGoto with no ``poseTh`` — no error frame (UmGoto is
        ``ret:none``), no route, the robot simply never moves. That failure is
        invisible on the wire, so it is rejected here instead: a caller that
        does not care about the heading passes the robot's current one.
        """
        if z is None:
            raise ValueError(
                "goto_xyz requires a heading: urobot silently ignores a "
                "target=pose UmGoto with no poseTh. Pass the robot's current "
                "heading to arrive without turning."
            )
        await self.um_goto("pose", None, x, y, z, strict)

    @staticmethod
    def build_move_route(
        distance,
        speed,
        *,
        obs_avoid_dist=1000,
        side_avoid_dist=50,
        flag=1,
        io=1,
        use_io=False,
        note=1,
        route_name="manual_move",
        step_key="a",
    ):
        """Build a one-step ``cmd:"move"`` route (relative-distance jog).

        ``move`` is not a top-level Um* command; it only runs as a route step.
        Shape mirrors the observed routes_ali.json "move" route. Verify the
        exact run path (UmSetRoutes+UmRoutes vs UmSchedulerThis) on the robot.
        """
        return {
            route_name: {
                step_key: {
                    "cmd": "move",
                    "distance": int(distance),
                    "speed": int(speed),
                    "flag": flag,
                    "io": io,
                    "obs_avoid_dist": obs_avoid_dist,
                    "side_avoid_dist": side_avoid_dist,
                    "use_io": use_io,
                    "note": note,
                }
            }
        }

    async def disable_motor(self):
        await self.um_set_motor(False)
    
    async def enable_motor(self):
        await self.um_set_motor(True)

    # -------------------------
    # COMMAND WRAPPERS
    # -------------------------
    async def um_connect(self, gap=-1):
        """Send the JIBOT API login/connect command.

        JIBOT API 로그인/연결 명령을 보낸다.

        Credentials (user, password, device_type) come from the JIBOT()
        constructor args, sourced from the [jibot_client] config section.
        """
        json_cmd_connect = {
            "#CMD#": "UmConnect",
            "#GAP#": gap,
            "user": self.user,
            "password": self.password,
            "device_type": self.device_type,
        }
        await self.json_cmd_to_jibot(json_cmd_connect)

    async def adv_get_params_routes(self, routes, gap=-1):
        await self.send_command("AdvGetParamsRoutes", gap=gap, routes=routes)

    async def adv_get_routes(self, routes, gap=-1):
        await self.send_command("AdvGetRoutes", gap=gap, routes=routes)

    async def adv_get_template_routes(self, routes, gap=-1):
        await self.send_command("AdvGetTemplateRoutes", gap=gap, routes=routes)

    async def adv_set_params_routes(self, routes, gap=-1):
        await self.send_command("AdvSetParamsRoutes", gap=gap, routes=routes)

    async def adv_set_routes(self, routes, gap=-1):
        await self.send_command("AdvSetRoutes", gap=gap, routes=routes)

    async def adv_set_template_routes(self, routes, gap=-1):
        await self.send_command("AdvSetTemplateRoutes", gap=gap, routes=routes)

    async def adv_track_manually(self, num, mode, gap=-1):
        await self.send_command("AdvTrackManually", gap=gap, num=num, mode=mode)

    async def get_polygons(self, gap=-1):
        await self.send_command("GetPolygons", gap=gap)

    async def set_map(self, name, gap=-1):
        await self.send_command("SetMap", gap=gap, name=name)

    async def get_map_name(self, timeout=3.0, gap=-1):
        response = await self.send_command_and_wait(
            "UmGetMapName", gap=gap, timeout=timeout
        )
        if not isinstance(response, dict):
            return None
        value = response.get("name") or response.get("mapName") or response.get("MapName")
        return value.strip() if isinstance(value, str) and value.strip() else None

    async def um_dock(self, gap=-1, **params):
        await self.send_command("UmDock", gap=gap, **params)

    async def um_drive(self, trans, rot, speed, lat, gap=-1):
        await self.send_command("UmDrive", gap=gap, trans=trans, rot=rot, speed=speed, lat=lat)

    async def um_get_battery_info(self, gap=-1):
        await self.send_command("UmGetBatteryInfo", gap=gap)

    async def um_get_client_log(self, gap=-1):
        await self.send_command("UmGetClientLog", gap=gap)

    async def um_get_config(self, gap=-1):
        await self.send_command("UmGetConfig", gap=gap)

    async def um_get_cur_task(self, gap=-1):
        await self.send_command("UmGetCurTask", gap=gap)

    async def um_get_input(self, gap=-1):
        await self.send_command("UmGetInput", gap=gap)

    async def um_get_laser(self, index, gap=-1):
        await self.send_command("UmGetLaser", gap=gap, index=index)

    async def um_get_loc_state(self, gap=-1):
        await self.send_command("UmGetLocState", gap=gap)

    async def um_get_map(self, gap=-1):
        await self.send_command("UmGetMap", gap=gap)

    async def um_get_map_name(self, gap=-1):
        await self.send_command("UmGetMapName", gap=gap)

    async def um_get_mapping_state(self, gap=-1):
        await self.send_command("UmGetMappingState", gap=gap)

    async def um_get_motor_info(self, gap=-1):
        await self.send_command("UmGetMotorInfo", gap=gap)

    async def um_get_motor_state(self, gap=-1):
        await self.send_command("UmGetMotorState", gap=gap)

    async def um_get_name(self, gap=-1):
        await self.send_command("UmGetName", gap=gap)

    async def um_get_output(self, gap=-1):
        await self.send_command("UmGetOutput", gap=gap)

    async def um_get_path(self, gap=-1):
        await self.send_command("UmGetPath", gap=gap)

    async def um_get_path_planning_clearances(self, gap=-1):
        await self.send_command("UmGetPathPlanningClearances", gap=gap)

    async def um_get_rail_info(self, gap=-1):
        await self.send_command("UmGetRailInfo", gap=gap)

    async def um_get_robot_info(self, gap=-1):
        await self.send_command("UmGetRobotInfo", gap=gap)

    async def um_get_robot_size(self, gap=-1):
        await self.send_command("UmGetRobotSize", gap=gap)

    async def um_get_routes(self, gap=-1):
        await self.send_command("UmGetRoutes", gap=gap)

    async def um_get_safe_drive(self, gap=-1):
        await self.send_command("UmGetSafeDrive", gap=gap)

    async def um_get_task_info(self, gap=-1):
        await self.send_command("UmGetTaskInfo", gap=gap)

    async def um_get_virtual_io(self, gap=-1):
        await self.send_command("UmGetVirtualIO", gap=gap)

    async def um_goto(
        self,
        target,
        goal=None,
        poseX=None,
        poseY=None,
        poseTh=None,
        strict=None,
        gap=-1,
    ):
        await self.send_command(
            "UmGoto",
            gap=gap,
            target=target,
            goal=goal,
            poseX=poseX,
            poseY=poseY,
            poseTh=poseTh,
            strict=strict,
        )

    async def um_idle(self, gap=-1):
        await self.send_command("UmIdle", gap=gap)

    async def um_localize(self, target, goal, poseX, poseY, poseTh, gap=-1):
        await self.send_command(
            "UmLocalize",
            gap=gap,
            target=target,
            goal=goal,
            poseX=poseX,
            poseY=poseY,
            poseTh=poseTh,
        )

    async def localize(
        self,
        target="pose",
        goal=None,
        poseX=None,
        poseY=None,
        poseTh=None,
    ):
        """Re-localize through the vendor-neutral AMR client contract."""
        await self.um_localize(
            target=target,
            goal=goal,
            poseX=poseX,
            poseY=poseY,
            poseTh=poseTh,
        )

    async def um_mapping(self, name, flag, target, gap=-1):
        await self.send_command("UmMapping", gap=gap, name=name, flag=flag, target=target)

    async def um_rms_get_config(self, gap=-1):
        await self.send_command("UmRMSGetConfig", gap=gap)

    async def um_rms_get_map(self, gap=-1):
        await self.send_command("UmRMSGetMap", gap=gap)

    async def um_rms_get_map_name(self, gap=-1):
        await self.send_command("UmRMSGetMapName", gap=gap)

    async def um_rms_get_routes(self, gap=-1):
        await self.send_command("UmRMSGetRoutes", gap=gap)

    async def um_rms_set_cancel_job(self, section, gap=-1):
        await self.send_command("UmRMSSetCancelJob", gap=gap, section=section)

    async def um_rms_set_config(self, section, objs, gap=-1):
        await self.send_command("UmRMSSetConfig", gap=gap, section=section, objs=objs)

    async def um_rms_set_map(self, section, gap=-1):
        await self.send_command("UmRMSSetMap", gap=gap, section=section)

    async def um_rms_set_routes(self, routes, gap=-1):
        await self.send_command("UmRMSSetRoutes", gap=gap, routes=routes)

    async def um_reload_config(self, gap=-1):
        await self.send_command("UmReloadConfig", gap=gap)

    async def um_routes(self, routes, key, id=None, gap=-1):
        await self.send_command("UmRoutes", gap=gap, routes=routes, key=key, id=id)

    async def um_scheduler_list(self, size, list, gap=-1):
        await self.send_command("UmSchedulerList", gap=gap, size=size, list=list)

    async def um_scheduler_this(self, name, key, content, gap=-1):
        await self.send_command("UmSchedulerThis", gap=gap, name=name, key=key, content=content)

    async def um_set_config(self, section, objs, gap=-1):
        await self.send_command("UmSetConfig", gap=gap, section=section, objs=objs)

    async def um_set_motor(self, flag, gap=-1):
        await self.send_command("UmSetMotor", gap=gap, flag=flag)

    async def um_set_output(self, length, high, low, gap=-1):
        await self.send_command("UmSetOutput", gap=gap, length=length, high=high, low=low)

    async def um_set_output_byte(self, num, flag, gap=-1):
        await self.send_command("UmSetOutputByte", gap=gap, num=num, flag=flag)

    async def um_set_routes(self, routes, gap=-1):
        if not isinstance(routes, str):
            routes = json.dumps(routes, separators=(",", ":"))
        await self.send_command("UmSetRoutes", gap=gap, routes=routes)

    async def move_distance(
        self,
        distance,
        speed,
        *,
        obs_avoid_dist=1000,
        side_avoid_dist=50,
        flag=1,
        io=1,
        use_io=False,
        note=1,
        run_mode="scheduler",
        route_name="manual_move",
        step_key="a",
        gap=-1,
    ):
        """Run a relative-distance move via a JIBOT route step.

        ``scheduler`` and ``set_routes`` exercise the JIBOT route-step APIs.
        Distance is in mm (signed: +forward / -backward). Do not implement this
        as timed UmDrive; accurate distance movement needs the robot's route
        executor or a closed-loop controller with pose feedback.
        """
        route = self.build_move_route(
            distance,
            speed,
            obs_avoid_dist=obs_avoid_dist,
            side_avoid_dist=side_avoid_dist,
            flag=flag,
            io=io,
            use_io=use_io,
            note=note,
            route_name=route_name,
            step_key=step_key,
        )
        if run_mode == "set_routes":
            await self.um_set_routes(route, gap=gap)
            await self.call_routes(route_name, step_key)
            return
        if run_mode != "scheduler":
            raise ValueError(f"Unsupported move_distance run_mode: {run_mode}")
        await self.um_scheduler_this(route_name, step_key, route[route_name], gap=gap)

    async def um_set_safe_drive(self, flag, gap=-1):
        await self.send_command("UmSetSafeDrive", gap=gap, flag=flag)

    async def um_set_volume(self, volume, gap=-1):
        await self.send_command("UmSetVolume", gap=gap, volume=volume)

    async def um_stop(self, gap=-1):
        await self.send_command("UmStop", gap=gap)

    async def error(self, gap=-1):
        await self.send_command("error", gap=gap)

    # -------------------------
    # RECEIVE LOOP
    # -------------------------
    async def receive_data_from_server(self):
        """Read TCP data and split complete JIBOT frames by the '$~' terminator.

        TCP 데이터를 읽고 '$~' 종료자를 기준으로 JIBOT 프레임을 분리한다.
        """
        buffer = ""

        try:
            while self._running:
                data = await self.reader.read(self.recv_buffer_bytes)
                if not data:
                    break

                # Any inbound bytes mean the JIBOT link is alive; refresh the
                # watchdog so the adapter does not treat it as silent.
                self._last_rx_at = time.monotonic()

                buffer += data.decode("utf-8")

                # handle multiple messages in buffer
                while "$~" in buffer:
                    end_index = buffer.find("$~") + 2
                    raw_message = buffer[:end_index]
                    buffer = buffer[end_index:]

                    self.process_data(raw_message)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print("Receive error:", e)
        finally:
            # The receive loop only exits when the JIBOT TCP connection is gone
            # (EOF, socket error, or cancellation), so mark the client as
            # disconnected. is_connected() then reports the dropped link.
            # 수신 루프는 JIBOT TCP 연결이 끊겼을 때만 종료되므로 클라이언트를
            # 연결 해제 상태로 표시한다. 이후 is_connected()가 끊김을 보고한다.
            self._running = False

    # -------------------------
    # PROCESS DATA
    # -------------------------
    def process_data(self, data):
        """Parse one JIBOT response frame and update cached robot state.

        JIBOT 응답 프레임 1개를 파싱하고 캐시된 로봇 상태를 갱신한다.
        """
        response = self.parse_string(data)
        self._record_rx(data, response)

        if response is None:
            print(f"[JIBOT RX PARSE FAILED] raw={data!r}")
            return

        self._response_queue.put_nowait(response)

        if response.get("#CMD#") == "UmGetRobotInfo":
            self._update_status_snapshot(response)

            self._charging = self._is_charging_status(self._status)


        if response.get("#CMD#") == "UmGetMap":
            self._update_map_nodes(response)

        if response.get("#CMD#") == "UmGetLaser":
            self._update_laser_raw(response)

        if response.get("#CMD#") == "UmGetCurTask":
            self._cur_task = response

        if response.get("#CMD#") == "UmGetTaskInfo":
            self._task_info = response

        if response.get("#CMD#") == "UmGetPath":
            self._path = response

        if response.get("#CMD#") == "UmGetMotorState":
            self._motor_flag = response.get("flag")

        
        if response.get("#CMD#") == "UmGetLocState":
            self._update_status_snapshot(response)
            self._localization_score = response.get("score")

        # NOTE: UmGetBatteryInfo over 7273 is a firmware STUB on this robot
        # (returns nested zeros). Real BMS voltage/current come from the ROS
        # /jrobot_status listener (adaptor/bms_ros_listener.py). Kept for
        # forward-compat if firmware ever populates it.
        if response.get("#CMD#") == "UmGetBatteryInfo":
            self._battery_health = response.get("soh")
            self._battery_voltage = response.get("vol")
            self._battery_current = (
                response.get("current")
                or response.get("amp")
                or response.get("amps")
                or response.get("cur")
            )
            self._battery_temperatures = [
                value
                for value in (
                    response.get("tem1"),
                    response.get("tem2"),
                    response.get("tem3"),
                )
                if value is not None
            ]
            self._battery_cells = (
                response.get("cells")
                or response.get("cell")
                or response.get("cell_voltages")
                or response.get("cellVoltages")
            )
            self._log_battery_info(response)

        # print(f"JibotInfo: {self.config.jibot_status.charging}")
        # print(f"JibotInfo: {self._mode} {self._status} {self._battery} {self._motor_flag} {self._station} {self._x} {self._y} {self._th}")
        # print(f"JibotInfo: {self._status}")
        # print(f"JibotInfo: {self._charging}")
        self._log_rx(response, raw=data)

    def _is_charging_status(self, status):
        charging_status = str(self.charging_status or "").strip().lower()
        vehicle_status = str(status or "").strip().lower()
        return bool(charging_status) and (
            vehicle_status == charging_status
            or vehicle_status.startswith(f"{charging_status}#")
        )

    def set_bms(self, voltage, current):
        """Cache the latest BMS voltage/current (volts, signed amps) from ROS.

        UmGetBatteryInfo over 7273 is a stub on this firmware, so these come from
        the /jrobot_status ROS listener. The existing VDA5050 getters read
        _battery_voltage / _battery_current.
        """
        self._battery_voltage = voltage
        self._battery_current = current
        self._bms_last_update = time.monotonic()

    def clear_bms(self):
        """Drop cached BMS values (stream stale or down) so VDA5050 stops
        publishing stale voltage/current."""
        self._battery_voltage = None
        self._battery_current = None

    def set_robot_safety(self, safety):
        """Cache the latest /jrobot_status safety/motor fields (dict of str).

        Fed by bms_ros_listener alongside set_bms. Stored as a copy so the
        listener can reuse its row dict. Stamped for staleness checks.
        """
        self._robot_safety = dict(safety)
        self._robot_safety_last_update = time.monotonic()

    def clear_robot_safety(self):
        """Drop cached safety fields (stream stale or down)."""
        self._robot_safety = {}

    def _update_status_snapshot(self, response):
        # 갱신 시각을 남긴다. 어댑터가 명령 직전에 pose 를 다시 읽을 때, 응답
        # 큐(다른 대기와 공유하는 자원)를 건드리지 않고 "새 프레임이 들어왔는가"
        # 만 확인할 수 있어야 하기 때문이다.
        self._status_snapshot_at = time.monotonic()
        for attr, key in (
            ("_mode", "mode"),
            ("_status", "status"),
            ("_station", "station"),
            ("_x", "x"),
            ("_y", "y"),
            ("_th", "th"),
        ):
            if key in response:
                setattr(self, attr, response.get(key))

        if "battery" in response:
            self._battery = response.get("battery")
            self._battery_known = True

    def _log_tx(self, payload, payload_length, json_data=None, raw_message=None):
        command = payload.get("#CMD#", "-")
        gap = payload.get("#GAP#", "-")
        status_polling_commands = {
            "UmGetRobotInfo",
            "UmGetMotorState",
            "UmGetLocState",
            "UmGetBatteryInfo",
        }
        if command in status_polling_commands:
            return

        params = {
            key: value
            for key, value in payload.items()
            if key not in ("#CMD#", "#GAP#", "password")
        }
        safe_payload = {
            key: value
            for key, value in payload.items()
            if key != "password"
        }
        if "password" in payload:
            json_text = "<redacted>"
            raw_text = "<redacted>"
        else:
            json_text = json_data
            raw_text = raw_message
        print(
            f"[JIBOT TX] command={command} gap={gap} "
            f"bytes={payload_length} params={params} payload={safe_payload} "
            f"json={json_text} raw={raw_text}"
        )

    def _log_rx(self, response, raw=None):
        command = response.get("#CMD#", "-")
        if command == "UmGetMap":
            result = response.get("result", response.get("error", ""))
            print(f"[JIBOT RX] command={command} result={result} map_updated=true")
            return
        if command == "UmGetLaser":
            result = response.get("result", response.get("error", ""))
            print(f"[JIBOT RX] command={command} result={result} laser_updated=true")
            return

        status_polling_commands = {
            "UmGetRobotInfo",
            "UmGetMotorState",
            "UmGetLocState",
            "UmGetBatteryInfo",
        }
        if command not in status_polling_commands:
            result = response.get("result", response.get("error", ""))
            print(
                f"[JIBOT RX] command={command} result={result} "
                f"payload={response} raw={raw}"
            )
            return

        now = time.monotonic()
        if now - self._last_status_log_at < self.status_log_interval_sec:
            return

        self._last_status_log_at = now
        print(
            f"[JIBOT RX] command={command} status_snapshot=true "
            f"mode={self._mode} status={self._status} battery={self._battery} "
            f"battery_voltage={self._battery_voltage} battery_current={self._battery_current} "
            f"battery_health={self._battery_health} battery_temps={self._battery_temperatures} "
            f"battery_cells={self._battery_cells} "
            f"motor={self._motor_flag} localization_score={self._localization_score} "
            f"station={self._station} pos=({self._x}, {self._y}, {self._th})"
        )

    def _log_battery_info(self, response):
        command = response.get("#CMD#", "-")
        if response.get("error") or response.get("state") is False:
            print(f"[JIBOT BATTERY INFO FAILED] command={command} payload={response}")
            return

        known_values = [
            self._battery_health,
            self._battery_voltage,
            self._battery_current,
            self._battery_cells,
            *self._battery_temperatures,
        ]
        if all(value is None for value in known_values):
            print(f"[JIBOT BATTERY INFO UNRECOGNIZED] payload={response}")
            return

        now = time.monotonic()
        if now - self._last_battery_info_log_at < self.battery_log_interval_sec:
            return

        self._last_battery_info_log_at = now
        print(
            "[JIBOT BATTERY INFO] "
            f"soh={self._battery_health} vol={self._battery_voltage} "
            f"current={self._battery_current} temps={self._battery_temperatures} "
            f"cells={self._battery_cells}"
        )

    # -------------------------
    # PARSE
    # -------------------------
    def parse_string(self, s):
        """Parse a raw JIBOT frame into a JSON dict.

        원시 JIBOT 프레임을 JSON dict로 파싱한다.

        Expected format / 기대 형식: $#<length>##<json>$~
        """
        # re.DOTALL so '.' also matches newlines: the UmGetMap response is
        # pretty-printed with CR/LF, and without DOTALL the frame never parses
        # (the map, and therefore node positions, would be silently dropped).
        pattern = r"\$#(\d+)##(.+?)\$~"
        match = re.match(pattern, s, re.DOTALL)

        if not match:
            return None

        json_length = int(match.group(1))
        json_data = match.group(2)

        if len(json_data) != json_length:
            return None

        try:
            return json.loads(json_data)
        except json.JSONDecodeError:
            return None

    def _record_tx(self, raw_message, payload):
        if not self.recorder:
            return

        self._tx_sequence += 1
        self.recorder.record_message(
            direction="tx",
            sequence=self._tx_sequence,
            raw=raw_message,
            payload=payload,
        )

    def _record_rx(self, raw_message, payload):
        if not self.recorder:
            return

        self._rx_sequence += 1
        self.recorder.record_message(
            direction="rx",
            sequence=self._rx_sequence,
            raw=raw_message,
            payload=payload,
        )
