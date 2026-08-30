"""In-process simulator for the JIBOT AMR API used by the adapter.

This class mirrors the small part of ``JIBOT`` that the VDA5050 adapter calls,
so order handling and state publishing can be tested without a physical AMR.
"""

import asyncio
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTOR_ROOT = REPO_ROOT / "adaptor"
if str(ADAPTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_ROOT))
CLIENT_SRC = REPO_ROOT / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from config.config import get_config
from jibot_client import JIBOT


class SimulatedJIBOT(JIBOT):
    def __init__(
        self,
        robot_ip="simulator",
        robot_port=7273,
        config=None,
        initial_position: Optional[Dict[str, Any]] = None,
        initial_map: Optional[Dict[str, Any]] = None,
        initial_battery: Optional[float] = None,
        initial_charging: bool = False,
    ):
        config = config or get_config()
        super().__init__(
            robot_ip,
            robot_port,
            config=config,
            charging_status=config.jibot_status.charging,
        )
        self.is_simulator = True
        self._mode = "auto"
        self._status = self.config.jibot_status.stop
        self._battery = self._resolve_initial_battery(initial_battery)
        self._running = False
        self._x = 0.0
        self._y = 0.0
        self._th = 0.0
        self._motor_flag = 1
        self._localization_score = 1.0
        self._station = "SIM"
        self._charging = False
        # Honoured once connect() runs (needs a running loop + _running=True to
        # drive the charger sim). Set via --charging to boot up docked & charging.
        self._initial_charging = bool(initial_charging)

        self._motion_task: Optional[asyncio.Task] = None
        self._charge_task: Optional[asyncio.Task] = None
        # Simulated charger rate: battery gains 1% per tick (1 second).
        self._charge_tick_sec = 1.0
        self._target_node = ""
        speed_mps = float(self.config.settings.speed or 0.5)
        if speed_mps <= 0:
            speed_mps = 0.5
        self._speed_mmps = speed_mps * 1000.0
        position_applied = self._apply_initial_position(initial_position)
        self._apply_initial_map(initial_map, randomize_position=not position_applied)

    def _apply_initial_map(self, initial_map, randomize_position=True):
        """Build the simulator's node map from a saved real-robot map snapshot.

        The simulator has no real localization/map, so without this it relies
        purely on FMS-order node positions. Loading the saved map gives it the
        real station layout so node-based navigation matches the physical map.
        """
        if not initial_map:
            return
        nodes = initial_map.get("nodes") or {}
        loaded = {}
        for name, pose in nodes.items():
            try:
                x, y = float(pose[0]), float(pose[1])
                theta = float(pose[2]) if len(pose) > 2 else 0.0
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            loaded[str(name)] = (x, y, theta)
        if loaded:
            self._map_nodes = loaded
            self._map_raw = initial_map.get("raw")
            if randomize_position:
                self._place_on_random_map_node(loaded)
            print(f"[JIBOT SIM] loaded {len(loaded)} map nodes from saved map")

    def _place_on_random_map_node(self, nodes):
        node_id, pose = random.choice(list(nodes.items()))
        self._station = node_id
        self._x = float(pose[0])
        self._y = float(pose[1])
        self._th = float(pose[2]) if len(pose) > 2 else 0.0

    async def get_map(self, interval_ms=-1, timeout=5.0):
        # The simulator has no socket to query; return the map loaded at startup.
        return self._map_nodes

    def _resolve_initial_battery(self, initial_battery):
        """Pick the starting battery: a clamped explicit value, else random.

        Without an explicit value the simulator starts at a random charge
        (30-100%). An operator-supplied value is honoured but clamped to the
        valid 0-100% range; anything non-numeric falls back to random so a bad
        CLI value never crashes startup.
        """
        if initial_battery is None:
            return float(random.randint(30, 100))
        try:
            return max(0.0, min(100.0, float(initial_battery)))
        except (TypeError, ValueError):
            print(f"[JIBOT SIM] ignored invalid initial battery: {initial_battery}")
            return float(random.randint(30, 100))

    def _apply_initial_position(self, initial_position):
        if not initial_position:
            return False
        try:
            self._x = float(initial_position["x"])
            self._y = float(initial_position["y"])
            self._th = float(initial_position["theta"])
            return True
        except (KeyError, TypeError, ValueError):
            print(f"[JIBOT SIM] ignored invalid initial position: {initial_position}")
            return False

    def is_rx_stale(self, timeout: float) -> bool:
        # The simulator updates state in-process without inbound frames, so the
        # RX watchdog never applies.
        return False

    async def connect_socket(self):
        self._running = True
        print(f"[JIBOT SIM] socket connected ip={self.robot_ip} port={self.robot_port}")

    async def disconnect(self):
        self._running = False
        await self._cancel_motion()
        print("[JIBOT SIM] disconnected")

    async def json_cmd_to_jibot(self, json_cmd):
        command = json_cmd.get("#CMD#", "")
        print(f"[JIBOT SIM] command={command} payload={json_cmd}")
        self._record_tx(self._format_raw_message(json_cmd), json_cmd)
        await self._apply_command(json_cmd)

    async def connect(self):
        self._running = True
        self._status = self.config.jibot_status.stop
        print("[JIBOT SIM] API connected")
        if self._initial_charging:
            # One-shot: boot up docked & charging like a robot left on its
            # charger. Done here (not __init__) because the charger sim needs a
            # running loop and _running=True, both true only after connect.
            self._initial_charging = False
            await self._start_charge_sim()
            print("[JIBOT SIM] started docked & charging (--charging)")

    async def get_robot_info(self, interval_ms):
        return None

    async def get_localization_info(self, interval_ms):
        self._localization_score = 1.0

    async def get_battery_info(self, interval_ms):
        return None

    async def get_motor_state(self, interval_ms):
        return None

    async def stop_motion(self):
        await self.um_stop()

    async def goto_point(self, point, strict=False):
        self._target_node = str(point)
        self._consume_node_battery()
        map_pose = self._map_nodes.get(self._target_node)
        if map_pose is not None:
            x = float(map_pose[0])
            y = float(map_pose[1])
            theta = float(map_pose[2]) if len(map_pose) > 2 else 0.0
            await self._start_motion(
                x,
                y,
                theta,
                duration=self._configured_node_travel_sec(),
            )
            return
        await self._start_short_node_motion()

    async def goto_xyz(self, x, y, z, strict=False):
        await self._start_motion(float(x), float(y), float(z or 0.0))

    async def goto_node_position(self, node_id, x, y, theta, strict=False):
        """Drive to an order-supplied node position.

        The simulator has no real map/localization, so it relies on the
        coordinates carried in the VDA5050 order's nodePosition. The node id is
        recorded as the current station so state reporting stays meaningful.
        """
        self._station = str(node_id)
        self._target_node = str(node_id)
        self._consume_node_battery()
        await self._start_motion(
            float(x),
            float(y),
            float(theta or 0.0),
            duration=self._configured_node_travel_sec(),
        )

    async def disable_motor(self):
        await self.um_set_motor(False)

    async def enable_motor(self):
        await self.um_set_motor(True)

    async def call_routes(self, name, key, id=None):
        print(f"[JIBOT SIM] route name={name} key={key} id={id}")
        self._target_node = str(name)
        self._consume_node_battery()
        await self._start_short_node_motion()

    async def _apply_command(self, json_cmd):
        command = json_cmd.get("#CMD#")
        if command == "UmConnect":
            await self.connect()
            self._emit_response({"#CMD#": "UmConnect", "result": "ok"})
        elif command == "UmStop":
            await self._cancel_motion()
            self._status = self.config.jibot_status.stop
            self._emit_response({"#CMD#": "UmStop", "result": "ok"})
        elif command == "UmSetMotor":
            self._motor_flag = 1 if json_cmd.get("flag") else 0
            self._emit_response({"#CMD#": "UmSetMotor", "flag": self._motor_flag})
        elif command == "UmGetRobotInfo":
            self._emit_response(
                {
                    "#CMD#": "UmGetRobotInfo",
                    "mode": self._mode,
                    "status": self._status,
                    "battery": self._battery,
                    "station": self._station,
                    "x": self._x,
                    "y": self._y,
                    "th": self._th,
                }
            )
        elif command == "UmGetMotorState":
            self._emit_response({"#CMD#": "UmGetMotorState", "flag": self._motor_flag})
        elif command == "UmGetLocState":
            self._emit_response({"#CMD#": "UmGetLocState", "score": self._localization_score})
        elif command == "UmGoto":
            target = json_cmd.get("target")
            if target == "pose":
                await self._start_motion(
                    float(json_cmd.get("poseX") or 0.0),
                    float(json_cmd.get("poseY") or 0.0),
                    float(json_cmd.get("poseTh") or 0.0),
                )
            else:
                self._target_node = str(json_cmd.get("goal") or "")
                self._consume_node_battery()
                await self._start_short_node_motion()
            self._emit_response({"#CMD#": "UmGoto", "result": "accepted"})
        elif command == "UmDock":
            # startCharging: dock and simulate a real charger by raising the
            # battery 1% per second so powerSupply.charging and stateOfCharge
            # both become observable in simulation.
            await self._cancel_motion()
            await self._start_charge_sim()
            self._emit_response({"#CMD#": "UmDock", "result": "ok"})
        elif command == "UmLocalize":
            # initPosition instant action: adopt the commanded pose directly.
            self._x = float(json_cmd.get("poseX") or 0.0)
            self._y = float(json_cmd.get("poseY") or 0.0)
            self._th = float(json_cmd.get("poseTh") or 0.0)
            self._localization_score = 1.0
            self._emit_response({"#CMD#": "UmLocalize", "result": "ok"})

    async def _start_short_node_motion(self):
        await self._cancel_motion()
        self._status = self.config.jibot_status.driving

        async def _finish():
            try:
                await asyncio.sleep(self._node_motion_duration())
                self._status = self.config.jibot_status.stop
            except asyncio.CancelledError:
                raise

        self._motion_task = asyncio.create_task(_finish())

    async def _start_motion(self, target_x, target_y, target_th, duration=None):
        if duration is not None and duration > 0.0:
            await self._start_fixed_duration_motion(
                target_x,
                target_y,
                target_th,
                duration,
            )
            return

        await self._cancel_motion()
        self._status = self.config.jibot_status.driving

        async def _move():
            try:
                while self._running:
                    dx = target_x - float(self._x)
                    dy = target_y - float(self._y)
                    distance = math.hypot(dx, dy)
                    if distance <= 30.0:
                        self._x = target_x
                        self._y = target_y
                        self._th = target_th
                        self._status = self.config.jibot_status.stop
                        return

                    step = min(self._speed_mmps * 0.1, distance)
                    self._x += dx / distance * step
                    self._y += dy / distance * step
                    self._th = target_th
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise

        self._motion_task = asyncio.create_task(_move())

    def _node_motion_duration(self):
        node_travel_sec = self._configured_node_travel_sec()
        return node_travel_sec if node_travel_sec > 0.0 else 1.0

    def _configured_node_travel_sec(self):
        return float(
            getattr(self.config.settings, "simulator_node_travel_sec", 0.0) or 0.0
        )

    async def _start_fixed_duration_motion(self, target_x, target_y, target_th, duration):
        await self._cancel_motion()
        self._status = self.config.jibot_status.driving
        start_x = float(self._x)
        start_y = float(self._y)
        start_th = float(self._th)
        duration = max(float(duration), 0.01)

        async def _move():
            try:
                loop = asyncio.get_running_loop()
                started_at = loop.time()
                while self._running:
                    elapsed = loop.time() - started_at
                    progress = min(1.0, elapsed / duration)
                    self._x = start_x + (target_x - start_x) * progress
                    self._y = start_y + (target_y - start_y) * progress
                    self._th = start_th + (target_th - start_th) * progress
                    if progress >= 1.0:
                        self._x = target_x
                        self._y = target_y
                        self._th = target_th
                        self._status = self.config.jibot_status.stop
                        return
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise

        self._motion_task = asyncio.create_task(_move())

    async def _cancel_motion(self):
        # Driving or stopping means the robot has left the charger, so any
        # in-progress charging ends here too. UmDock re-enables charging right
        # after its own _cancel_motion() call, so docking is unaffected.
        await self._stop_charge_sim()
        if self._motion_task is not None and not self._motion_task.done():
            self._motion_task.cancel()
            try:
                await self._motion_task
            except asyncio.CancelledError:
                pass
        self._motion_task = None

    async def _start_charge_sim(self):
        """Dock onto the charger and raise the battery 1% per second.

        The battery climbs to 100% and then holds, with the charging flag and
        status left on so the robot keeps reporting as docked & charging until
        it drives away (see _cancel_motion).
        """
        await self._stop_charge_sim()
        self._status = self.config.jibot_status.charging
        self._charging = True

        async def _charge():
            try:
                while self._running and float(self._battery) < 100.0:
                    await asyncio.sleep(self._charge_tick_sec)
                    self._battery = min(100.0, float(self._battery) + 1.0)
            except asyncio.CancelledError:
                raise

        self._charge_task = asyncio.create_task(_charge())

    async def _stop_charge_sim(self):
        """Stop the charging simulation and clear the charging flag.

        The caller owns _status: motion helpers set "driving", UmStop sets
        "stop". Reaching 100% is not a stop; that path leaves the task done
        but keeps _charging True via the natural loop exit, not this method.
        """
        if self._charge_task is not None and not self._charge_task.done():
            self._charge_task.cancel()
            try:
                await self._charge_task
            except asyncio.CancelledError:
                pass
        self._charge_task = None
        self._charging = False

    def _consume_node_battery(self):
        self._battery = max(0.0, float(self._battery) - 1.0)

    def _format_raw_message(self, json_cmd):
        json_data = json.dumps(json_cmd, separators=(",", ":"))
        return f"$#{len(json_data)}##{json_data}$~"

    def _emit_response(self, payload):
        self.process_data(self._format_raw_message(payload))
