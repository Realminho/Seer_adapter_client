"""Jibot VDA5050 adapter implementation.

Translates ACS MQTT commands into robot actions and publishes VDA5050 state/connection messages.
"""

import asyncio
import json
import time
import math
from typing import Any, Dict, Optional, List, Set, Tuple
from datetime import datetime


from protocol.vda5050_common import AgvPosition
from protocol.vda_2_0_0.vda5050_2_0_0_connection import Connection, ConnectionState
from protocol.vda_2_0_0.vda5050_2_0_0_state import State, Load, NodeState, EdgeState, ActionState, BatteryState, SafetyState, OperatingMode, ActionStatus, EStop, Error, ErrorLevel, ErrorType, ErrorReference, Information, InfoLevel, InfoType, InfoReference
# from protocol.vda_2_0_0.vda5050_2_0_0_visualization import Visualization
from protocol.vda_2_0_0.vda5050_2_0_0_action import Action, ActionParameter, ActionParameterValue, BlockingType
from protocol.vda_2_0_0.vda5050_2_0_0_order import Order
from protocol.vda_2_0_0.vda5050_2_0_0_instant_actions import InstantActions

from utils.mqtt_client import MQTTClient
from utils.cls_jibot_robot import JIBOT
from utils.ezi_io import EZIIOClient
from utils.ezi_motor import EziMotorClient
import utils.helpers as utils

from config.config import get_config

class Adapter:

    def __init__(self) -> None:
        self.publish_state_task: Optional[asyncio.Task[Any]] = None
        self.subscribe_task: Optional[asyncio.Task[Any]] = None
        self.execute_path_task: Optional[asyncio.Task[Any]] = None
        self._pending_execute_path_interval: Optional[float] = None
        self.load_task: Optional[asyncio.Task[Any]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None


        self._mqtt = MQTTClient()

        # Last sequence received from cmd/sync; used as base for increment loop
        self._status_sequence: int = 0
        
        # Track current and last node values for status payload
        # self._current_next_node: Optional[str] = None
        # self._last_node_value: Optional[str] = None

        self._vehicle: Optional[JIBOT] = None

        self._ezi_io: Optional[EZIIOClient] = None

        self._ezi_motor: Optional[EziMotorClient] = None

        self.config = get_config()

        self.connection: Optional[Connection] = None
        self.header_id: int = 0 # Header ID for connection

        self.state_header_id: int = 0 # Header ID for state

        self.state: Optional[State] = None
        self.order: Optional[Order] = None

        self.error: Optional[Error] = None

        self._last_node_id: str = ""
        self._last_node_sequence_id: int = 0

        self._current_next_node: Optional[str] = None

        self._charge_node: bool = False

        # initialized tray slot
        tray_slot_pins = self.config.ezi_config.tray_slot_pin
        self.loads: List[Load] = [
            Load(
                load_id=f"tray-{i:03}",
                load_type="NULL",
                load_position=f"slot{i}",
            )
            for i in range(1, len(tray_slot_pins) + 1)
        ]

        # self.error_reference[ErrorReference] = None


    def connect_mqtt(self) -> None: 
        self._mqtt.connect()

    def disconnect_mqtt(self) -> None:
        self._mqtt.disconnect()

    # -------------------------
    # setter method
    # -------------------------
    def set_vehicle(self, vehicle: JIBOT) -> None:
        self._vehicle = vehicle

    def set_ezi_io(self, io: EZIIOClient ) -> None:
        self._ezi_io = io

    def set_ezi_motor(self, motor: EziMotorClient) -> None:
        self._ezi_motor = motor

    
    # -------------------------
    # Run Adapter
    # -------------------------
    async def run_adapter(self):

        # Motion control ( Order accept/update )
        self._loop = asyncio.get_running_loop()

        # Publish State AMR==>ACS
        self.publish_state_task = asyncio.create_task(
            self.publish_state(
                "state",
                interval_sec=float(self.config.settings.state_publish_delay),
            )
        )

        # Subscribe ( Order and InstantAction )
        self.subscribe_task = asyncio.create_task(
            self.subscribe_acs_cmd(interval_sec=1)
        )


        # Manage Load ( Tray-slot By IO detection)
        self.load_task = asyncio.create_task(
            self.manage_tray_slot(
                interval_sec=float(self.config.settings.state_publish_delay),
            )
        )

    # Manage Load ( Tray-slot By IO detection)
    async def manage_tray_slot(self, interval_sec: float = 1) -> None:
        tray_slot_pins = self.config.ezi_config.tray_slot_pin

        while True:
            if self._ezi_io is not None:
                io = await self._ezi_io.get_input()
                if io:
                    inputs = io["inputs"]
                    for slot, pin in enumerate(tray_slot_pins):
                        if pin < len(inputs) and inputs[pin] == 1:
                            self.loads[slot].load_type = "TRAY"
                        else:
                            self.loads[slot].load_type = "NULL"
                    if self.state is not None:
                        self.state.loads = self.loads

            await asyncio.sleep(interval_sec)
        



    def _schedule_execute_path(
        self, interval_sec: float = 1, *, force_restart: bool = False
    ) -> None:
        """Schedule path execution on the adapter event loop."""
        if self._loop is None or not self._loop.is_running():
            print("Adapter event loop is not ready; cannot start _execute_path.")
            return

        def _start_task() -> None:
            if self.execute_path_task is not None and not self.execute_path_task.done():
                if force_restart:
                    self._pending_execute_path_interval = interval_sec
                    self.execute_path_task.cancel()
                else:
                    print("_execute_path is already running; ignore duplicated order start.")
                return

            self.execute_path_task = asyncio.create_task(
                self._execute_path(interval_sec=interval_sec)
            )

            def _on_done(task: asyncio.Task) -> None:
                if task.cancelled():
                    pending = self._pending_execute_path_interval
                    if pending is not None:
                        self._pending_execute_path_interval = None
                        self.execute_path_task = None
                        self._schedule_execute_path(pending)
                    return
                exc = task.exception()
                if exc is not None:
                    print(f"Error during motion execution: {exc}")

            self.execute_path_task.add_done_callback(_on_done)

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            _start_task()
        else:
            self._loop.call_soon_threadsafe(_start_task)
 
        
    # -------------------------
    # PUBLISH STATE
    # -------------------------
    async def publish_state(self, topic_name: str, interval_sec: float = 1):
        print("publish_state is ready!")

       # AgvPosition
        agv_position = AgvPosition(
            x=self._vehicle._x,
            y=self._vehicle._y,
            position_initialized=True,
            theta=self._vehicle._th,
            map_id=self.config.settings.map_id,
            deviation_range=None,
            map_description=None,
            localization_score=self._vehicle._localization_score,
        )

        
        self.state = State(
            header_id=self.state_header_id,
            timestamp=utils.get_timestamp(),
            version=self.config.vehicle.vda_full_version,
            manufacturer=self.config.vehicle.manufacturer,
            serial_number=self.config.vehicle.serial_number,
            driving=False,
            distance_since_last_node=None,
            operating_mode=OperatingMode.AUTOMATIC,
            node_states=[], 
            edge_states=[],
            last_node_id=self._last_node_id,
            order_id=self.order.order_id if self.order else "",
            order_update_id=self.order.order_update_id if self.order else 0,
            last_node_sequence_id= self._last_node_sequence_id,
            action_states=[],
            instant_action_states=[],
            information=[],
            loads=self.loads,
            errors=[],
            battery_state=BatteryState(
                battery_charge=0.0,
                battery_voltage=self._vehicle._battery,
                battery_health=None,
                charging=self._vehicle._charging,
                reach=None
            ),
            safety_state=SafetyState(
                e_stop=EStop.NONE,
                field_violation=False
            ),
            paused=None,
            new_base_request=None,
            agv_position=agv_position,
            velocity=None,
            zone_set_id=None
        )


        while True:
            # counter_state += 1
            self.state_header_id += 1

            if self._vehicle is not None:

                vx, vy, vth = self._vehicle._x, self._vehicle._y, self._vehicle._th
                agv_position.x = float(vx) if vx is not None else 0.0
                agv_position.y = float(vy) if vy is not None else 0.0
                agv_position.theta = float(vth) if vth is not None else 0.0
                agv_position.localization_score = float(self._vehicle._localization_score) if self._vehicle._localization_score is not None else 0.0
                
                
                
                bat = self._vehicle._battery
                self.state.battery_state.battery_voltage = (
                    float(bat) if bat is not None else 0.0
                )


                # charging
                self.state.battery_state.charging = self._vehicle._charging


                # safety_state
                self.state.safety_state.e_stop = (
                    EStop.AUTOACK if self._vehicle._motor_flag == 0 else EStop.NONE
                )

                # operation_mode
                motions = self.config.jibot_status.motion
                status = self._vehicle._status
                if any(m.lower() in status.lower() for m in motions):
                    self.state.operating_mode = OperatingMode.AUTOMATIC
                else:
                    self.state.operating_mode = OperatingMode.MANUAL


                # driving
                if any(m.lower() in status.lower() for m in motions):
                    self.state.driving = True
                elif self.config.jibot_status.driving == self._vehicle._status:
                    self.state.driving = True
                else:
                    self.state.driving = False

            self._update_last_node_from_position(
                agv_position.x,
                agv_position.y,
                agv_position.theta,
            )
            self.state.last_node_id = self._last_node_id
            self.state.last_node_sequence_id = self._last_node_sequence_id


            self.state.header_id = self.state_header_id
            self.state.timestamp = utils.get_timestamp()

            self._mqtt.publish(
                topic_name,
                payload=self.state.to_dict(),
                qos=0,
                retain=False,
            )

           
            await asyncio.sleep(interval_sec)

    # -------------------------
    # PUBLISH CONNECTION
    # -------------------------
    async def publish_connection(self, topic_name: str, interval_sec: int = 1, retain_msg: bool = False, connection_state: ConnectionState = ConnectionState.CONNECTION_BROKEN):
        
        self.header_id += 1
        self.connection = Connection(
                header_id=self.header_id,
                timestamp=utils.get_timestamp(),
                version=self.config.vehicle.vda_full_version,
                manufacturer=self.config.vehicle.manufacturer,
                serial_number=self.config.vehicle.serial_number,
                connection_state=connection_state
        )

        self._mqtt.publish( topic_name, payload=self.connection.to_dict(), qos=0,retain=retain_msg)

    def configure_connection_last_will(self, topic_name: str, connection_state: ConnectionState = ConnectionState.OFFLINE) -> None:
        self.header_id += 1
        self.connection = Connection(
                header_id=self.header_id,
                timestamp=utils.get_timestamp(),
                version=self.config.vehicle.vda_full_version,
                manufacturer=self.config.vehicle.manufacturer,
                serial_number=self.config.vehicle.serial_number,
                connection_state=connection_state
        )

        self._mqtt.set_last_will(topic_name, payload=self.connection.to_dict(), qos=0, retain=True)

    # -------------------------
    # SUBSCRIBE ORDER & INSTANT ACTIONS
    # -------------------------   
    async def subscribe_acs_cmd(self, interval_sec: int = 1):
        print("subscribe ACS Command is ready!")
        
        self._mqtt.subscribe(
            "order",
            qos=0,
            callback=self.handle_incoming_acs_cmd,
            use_prefix=True
        )
        self._mqtt.subscribe(
            "instantActions",
            qos=0,
            callback=self.handle_incoming_acs_cmd,
            use_prefix=True
        )

        while True:
            await asyncio.sleep(interval_sec)


    # -------------------------
    # HANDLE INCOMING ORDER & INSTANT ACTIONS
    # -------------------------   
    def handle_incoming_acs_cmd(self, topic: str, payload: Any) -> None:
        # check JSON payload valid
        try:
            raw_payload = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
            data = json.loads(raw_payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.order_reject(
                True,
                ErrorLevel.WARNING,
                ErrorType.ORDER_JSON_PAYLOAD_INVALID,
                "Failed to decode or parse JSON payload",
                error_hint="Check if the payload is a valid JSON string and properly encoded in UTF-8",
            )
            return
        
        if topic.endswith("/order"):
            # Convert payload dict to Order instance before processing
            try:
                # print(data)
                order_obj = Order.from_dict(data)
            except Exception as exc:
                # print(f"Failed to parse order payload: {exc}")
                self.order_reject(True, ErrorLevel.WARNING, ErrorType.UNKNOWN_ERROR, f"Failed to parse order payload: {exc}", error_hint="Check if the order payload structure matches the expected format")
                return
            # Validate order payload
            self.order_accept_procedure(order_obj)
            


        elif topic.endswith("/instantActions"):
            # Convert payload dict to InstantActions instance before processing
            try:
                instant_actions_obj = InstantActions.from_dict(data)
            except Exception as exc:
                # print(f"Failed to parse instant actions payload: {exc}")
                self.order_reject(False, ErrorLevel.WARNING, ErrorType.UNKNOWN_ERROR, f"Failed to parse instant actions payload: {exc}", error_hint="Check if the instant actions payload structure matches the expected format")
                return
            # Validate instant actions payload
            self.instant_actions_accept_procedure(instant_actions_obj)
        
        
        else:
            pass
            # print(f"Received ACS command from unsupported topic '{topic}'")

    def instant_actions_accept_procedure(self, instant_actions_request: InstantActions) -> None:
        """Process incoming instant actions request"""
        if self.state is not None:
            self._append_instant_action_states(instant_actions_request)

        self.manage_instant_actions(instant_actions_request)

        



    def _all_node_actions_waiting_or_failed(self) -> bool:
        """True if every action on current node_states has ActionState WAITING or FAILED."""
        if self.state is None:
            return False
        
        status_by_action_id = {a.action_id: a.action_status for a in self.state.action_states}
        for node in self.state.node_states:
            actions = getattr(node, "actions", None) or []
            for act in actions:
                aid = getattr(act, "action_id", None)
                if not aid:
                    continue
                st = status_by_action_id.get(aid)
                if st is None or st not in (ActionStatus.FINISHED, ActionStatus.FAILED):
                    return False
        return True


    
    def _all_node_actions_finished_or_failed(self) -> bool:
        """True if every action on current node_states has ActionState FINISHED or FAILED."""
        if self.state is None:
            return False
        
        # status_by_action_id = {a.action_id: a.action_status for a in self.state.action_states}
        for node in self.state.action_states:
            if node.action_status not in (ActionStatus.FINISHED, ActionStatus.FAILED):
                return False
        return True

    # -------------------------
    # ORDER VALIDATION PROCEDURE
    # -------------------------   
    def order_accept_procedure(self, order_request: Order) -> None:
        """Process incoming order request"""

        # 1. NEW ORDER (no active order)
        if self.state.order_id == "":
            self.order_accept(order_request)
            return


        # 2. NEW ORDER (different order_id)
        if order_request.order_id != self.state.order_id:
            # Allow only if current order is effectively finished
            if self._all_node_actions_finished_or_failed():
                self.state.action_states = []
                self.order_accept(order_request)
                return
            else:
                self.order_reject(
                    True,
                    ErrorLevel.WARNING,
                    ErrorType.ORDER_CURRENT_NOT_FINISHED,
                    "Current Order is still in progress!",
                    error_hint="Cancel the current Order first"
                )
                return


        # 3. ORDER UPDATE (same order_id)

        # --- 3.1 Enforce exact +1 increment ---
        expected_update_id = self.state.order_update_id + 1

        if order_request.order_update_id != expected_update_id:
            self.order_reject(
                False,
                ErrorLevel.WARNING,
                ErrorType.ORDER_UPDATE_ID_INVALID,
                f"OrderUpdateId must be exactly {expected_update_id}",
                error_hint=f"Expected={expected_update_id}, received={order_request.order_update_id}"
            )
            return

        # --- 3.2 Validate node continuity ---
        if not order_request.nodes:
            self.order_reject(
                False,
                ErrorLevel.WARNING,
                ErrorType.ORDER_START_NODE_INVALID,
                "Order must contain at least one node",
            )
            return

        start_node = order_request.nodes[0]

        # print(
        #     f"[ORDER UPDATE] received={start_node.node_id}, "
        #     f"current={self.state.last_node_id}"
        # )

        if self.state.last_node_id != start_node.node_id:
            self.order_reject(
                False,
                ErrorLevel.WARNING,
                ErrorType.ORDER_START_NODE_INVALID,
                "Start node ID does not match last node ID of current order",
                error_hint=f"Expected={self.state.last_node_id}, received={start_node.node_id}"
            )
            return

        # --- 3.3 Validate sequence continuity ---
        if self.state.last_node_sequence_id != start_node.sequence_id:
            self.order_reject(
                False,
                ErrorLevel.WARNING,
                ErrorType.ORDER_START_SEQUENCE_ID_INVALID,
                "Start sequence ID does not match last sequence ID of current order",
                error_hint=f"Expected={self.state.last_node_sequence_id}, received={start_node.sequence_id}"
            )
            return


        # 4. ACCEPT UPDATE
        self.order_update_accept(order_request)

    # -------------------------
    # NEW ORDER ACCEPT PROCEDURE 
    # -------------------------   
    def order_accept(self, order_request: Order) -> None:
        """Accept an order"""
        self.order = order_request

        # reset header id to 1 when accepting a new order
        self.state_header_id = 0
        
        if self.state is not None:

            self.state.order_id = order_request.order_id
            self.state.order_update_id = order_request.order_update_id
            self.state.node_states = order_request.nodes
            self.state.edge_states = order_request.edges

            self.state.action_states = []  # Clear action states when accepting a new order
            self.state.action_states = self.build_action_states(self.order)

            self.state.errors = [] # Clear previous errors when accepting a new order
            self.state.information = [] # Clear previous information when accepting a new order

        self.add_state_info(
            InfoType.ORDER_NEW_ACCEPTED,
            description=f"Order {order_request.order_id} accepted",
            info_level= InfoLevel.INFO,
            info_reference_key= "orderId",
            info_reference_value = order_request.order_id,
        )

        # After order has accepted, Control Real-Motion to AMR, Call JIBOT API
        self._schedule_execute_path(interval_sec=1, force_restart=True)
               



    def _angle_error(self,target: float, current: float) -> float:
        """Smallest absolute angle difference in radians."""
        return abs((target - current + math.pi) % (2 * math.pi) - math.pi)

    def _update_node_action_states(self, node_id: str, action_status: ActionStatus) -> None:
        """Update action states bound to a specific node ID."""
        if self.state is None:
            return

        for action_state in self.state.action_states:
            if (
                action_state.action_description == "Node"
                and action_state.action_type == node_id
            ):
                action_state.action_status = action_status

    def _update_related_edge_action_states(self, node_id: str, action_status: ActionStatus) -> None:
        """Update edge action states for edges ending at this node."""
        if self.state is None:
            return

        related_edge_ids = {
            edge_state.edge_id
            for edge_state in self.state.edge_states
            if getattr(edge_state, "end_node_id", None) == node_id
        }
        if not related_edge_ids:
            return

        for action_state in self.state.action_states:
            if (
                action_state.action_description == "Edge"
                and action_state.action_type in related_edge_ids
            ):
                action_state.action_status = action_status

    def _is_active_action_status(self, action_status: ActionStatus) -> bool:
        status_value = (
            action_status.value
            if isinstance(action_status, ActionStatus)
            else str(action_status)
        )
        return status_value in {
            ActionStatus.INITIALIZING.value,
            ActionStatus.WAITING.value,
            ActionStatus.RUNNING.value,
            ActionStatus.PAUSED.value,
            "INITIALIZATION",  # Compatibility with external/non-standard status naming
        }

    def _is_terminal_action_status(self, action_status: ActionStatus) -> bool:
        status_value = (
            action_status.value
            if isinstance(action_status, ActionStatus)
            else str(action_status)
        )
        return status_value in {ActionStatus.FINISHED.value, ActionStatus.FAILED.value}

    def _all_action_states_terminal(self) -> bool:
        if self.state is None or not self.state.action_states:
            return True
        return all(
            self._is_terminal_action_status(action_state.action_status)
            for action_state in self.state.action_states
        )

    def _get_active_node_ids_from_action_states(self) -> Set[str]:
        if self.state is None:
            return set()

        active_node_ids: Set[str] = set()
        edge_end_by_id = {
            edge_state.edge_id: getattr(edge_state, "end_node_id", None)
            for edge_state in self.state.edge_states
        }

        for action_state in self.state.action_states:
            if not self._is_active_action_status(action_state.action_status):
                continue

            if action_state.action_description == "Node":
                active_node_ids.add(action_state.action_type)
            elif action_state.action_description == "Edge":
                end_node_id = edge_end_by_id.get(action_state.action_type)
                if end_node_id:
                    active_node_ids.add(end_node_id)

        return active_node_ids

    def _find_node_start_index(self, node_ids: Set[str]) -> Optional[int]:
        if self.state is None or not self.state.node_states or not node_ids:
            return None

        for index, node in enumerate(self.state.node_states):
            if node.node_id in node_ids:
                return index
        return None

    def _is_path_released(self, node: NodeState) -> bool:
        if not node.released:
            print(f"Node {node.node_id} is not released. Stopping path execution.")
            return False

        related_edges = [
            edge for edge in self.state.edge_states
            if edge.start_node_id == node.node_id or edge.end_node_id == node.node_id
        ]

        for edge in related_edges:
            if not edge.released:
                print(f"Edge {edge.edge_id} is not released. Stopping path execution.")
                return False

        return True
    


    def get_sequence_by_node(self, node_id: str) -> Optional[int]:
        for node in self.state.node_states:
            if node.node_id == node_id:
                return node.sequence_id
        return None

    def _get_allowed_deviation_xy(self, deviation_xy: Any) -> float:
        if deviation_xy is None:
            return 0.0
        if isinstance(deviation_xy, dict):
            return max(
                abs(float(deviation_xy.get("a", 0.0) or 0.0)),
                abs(float(deviation_xy.get("b", 0.0) or 0.0)),
            )
        return abs(float(deviation_xy))

    def _find_node_at_position(
        self,
        x: float,
        y: float,
        theta: Optional[float] = None,
    ) -> Optional[Tuple[str, int]]:
        """Return the closest node whose nodePosition contains the current robot pose."""
        if self.state is None or not self.state.node_states:
            return None

        zone_shape = str(getattr(self.config.settings, "reach_zone_shape", "square")).strip().lower()
        if zone_shape not in {"square", "circle"}:
            zone_shape = "square"

        zone_scale = float(getattr(self.config.settings, "reach_zone_scale", 1.0) or 1.0)
        matches: List[Tuple[float, str, int]] = []

        for node in self.state.node_states:
            pos = node.node_position
            if pos is None:
                continue

            deviation_xy = self._get_allowed_deviation_xy(pos.allowed_deviation_xy) * zone_scale
            if deviation_xy <= 0.0:
                deviation_xy = 0.01

            dx = x - float(pos.x)
            dy = y - float(pos.y)
            distance_xy = math.hypot(dx, dy)

            if zone_shape == "circle":
                inside_xy = distance_xy <= deviation_xy
            else:
                inside_xy = abs(dx) <= deviation_xy and abs(dy) <= deviation_xy

            if not inside_xy:
                continue

            if (
                theta is not None
                and pos.theta is not None
                and pos.allowed_deviation_theta is not None
            ):
                if abs(float(theta) - float(pos.theta)) > abs(float(pos.allowed_deviation_theta)):
                    continue

            matches.append((distance_xy, node.node_id, node.sequence_id))

        if not matches:
            return None

        _, node_id, sequence_id = min(matches, key=lambda item: item[0])
        return node_id, sequence_id

    def _update_last_node_from_position(
        self,
        x: float,
        y: float,
        theta: Optional[float] = None,
    ) -> None:
        matched_node = self._find_node_at_position(x, y, theta)
        if matched_node is None:
            return

        self._last_node_id, self._last_node_sequence_id = matched_node


    # Node Released False=>True True=>False updating according to requested sequence_id
    def update_node_by_sequence(self, updated_node: NodeState):
        for node in self.state.node_states:
            if node.sequence_id == updated_node.sequence_id:
                node.released = updated_node.released
                return node

        return None

    # Edge Released False=>True True=>False updating according to requested sequence_id
    def update_edge_by_sequence(self, updated_edge: EdgeState):
        for edge in self.state.edge_states:
            if edge.sequence_id == updated_edge.sequence_id:
                edge.released = updated_edge.released
                return edge

        return None

    # Append ( A->B->C  into A->B->C->D->E)
    # Update ( A->B->C  into A->B->C )  update relaesed True=>False False=>True
    def update_or_append(self, requested_order: Order) -> bool:
        last_node = self.state.node_states[-1]

        if requested_order.nodes:
            if requested_order.nodes[0].sequence_id == last_node.sequence_id:
                return False

            if requested_order.nodes[0].sequence_id < last_node.sequence_id:
                return True
        # default update
        return True
            



    # -------------------------
    # MOTION API
    # -------------------------
    async def _execute_path(self, interval_sec: float = 0.1) -> None:
        """Monitor action states and execute motion from related nodes."""

        if self.state is None or not self.state.node_states:
            return

        while True:
            if self._all_action_states_terminal():
                print("All action states are FINISHED or FAILED.")
                return

            active_node_ids = self._get_active_node_ids_from_action_states()
            start_index = self._find_node_start_index(active_node_ids)

            if start_index is None:
                await asyncio.sleep(interval_sec)
                continue

            for node in self.state.node_states[start_index:]:
                if self._all_action_states_terminal():
                    print("All action states are FINISHED or FAILED.")
                    return

                if node.node_id not in active_node_ids:
                    continue

                pos = node.node_position
                if pos is None:
                    continue

                target_x = pos.x
                target_y = pos.y
                current_theta = getattr(self._vehicle, "_th", 0.0)
                target_theta = pos.theta if pos.theta is not None else current_theta
                if target_theta is None:
                    target_theta = 0.0

                deviation_xy = self._get_allowed_deviation_xy(pos.allowed_deviation_xy)

                deviation_theta = (
                    pos.allowed_deviation_theta
                    if hasattr(pos, "allowed_deviation_theta")
                    and pos.allowed_deviation_theta is not None
                    else 0.05
                )

                try:


                    # check released True/False
                    if not self._is_path_released(node):
                        return

                    # print(f"Moving to -> {node.node_id}")
                    self._current_next_node = node.node_id
                    self._update_node_action_states(node.node_id, ActionStatus.RUNNING)
                    self._update_related_edge_action_states(node.node_id, ActionStatus.RUNNING)
                    
                    # check charging Node, If charging node then change Route "cmd" for docking instead of goto
                    for node_name, route_name in zip(
                        self.config.charge.nodes,
                        self.config.charge.routes
                    ):
                        if node.node_id == node_name:
                            self._charge_node = True
                            print(f"Moving to -> Charging {node.node_id}")
                            await self._vehicle.call_routes(route_name, "a")
                            self._charge_node = False
                                    
                                      
                    if not self._charge_node:
                        print(f"Moving to -> {node.node_id}")
                        result = self._vehicle.goto_xyz(
                            target_x,
                            target_y,
                            target_theta
                        )

                    if asyncio.iscoroutine(result):
                        await result

                except Exception as exc:
                    print(f"goto_xyz error: {exc}")
                    return

                await self._wait_until_reached(
                    node.node_id,
                    pos.x,
                    pos.y,
                    pos.theta,
                    deviation_xy,
                    deviation_theta
                )

                active_node_ids = self._get_active_node_ids_from_action_states()

            await asyncio.sleep(interval_sec)

    # -------------------------
    # MOTION CONTROL ( to wait until reached, execute path )
    # -------------------------
    async def _wait_until_reached(self, node_id: str, target_x: float, target_y: float, target_theta: float, deviation_xy: float, deviation_theta: float):
        zone_shape = str(getattr(self.config.settings, "reach_zone_shape", "square")).strip().lower()
        if zone_shape not in {"square", "circle"}:
            print(f"Invalid reach_zone_shape '{zone_shape}', fallback to 'square'")
            zone_shape = "square"

        zone_scale = float(getattr(self.config.settings, "reach_zone_scale", 1.0) or 1.0)
        effective_deviation_xy = abs(float(deviation_xy)) * zone_scale
        if effective_deviation_xy <= 0.0:
            effective_deviation_xy = 0.01
        # print(
        #     f"Node {node_id} zone config: shape={zone_shape}, "
        #     f"deviation={deviation_xy}, scale={zone_scale}, effective={effective_deviation_xy}"
        # )

        zone_min_x = target_x - effective_deviation_xy
        zone_max_x = target_x + effective_deviation_xy
        zone_min_y = target_y - effective_deviation_xy
        zone_max_y = target_y + effective_deviation_xy

        # 4 points of the square zone centered at target.
        zone_points = [
            (zone_min_x, zone_min_y),  # bottom-left
            (zone_max_x, zone_min_y),  # bottom-right
            (zone_max_x, zone_max_y),  # top-right
            (zone_min_x, zone_max_y),  # top-left
        ]
        # print(f"Node {node_id} zone points: {zone_points}")

        def is_inside_zone(x: float, y: float) -> bool:
            if zone_shape == "circle":
                return math.hypot(x - target_x, y - target_y) <= effective_deviation_xy
            return zone_min_x <= x <= zone_max_x and zone_min_y <= y <= zone_max_y

        def segment_intersects_zone(
            x1: float, y1: float, x2: float, y2: float
        ) -> bool:
            if zone_shape == "circle":
                dx = x2 - x1
                dy = y2 - y1
                seg_len_sq = dx * dx + dy * dy

                # Stationary sample point.
                if seg_len_sq == 0.0:
                    return math.hypot(x1 - target_x, y1 - target_y) <= effective_deviation_xy

                # Closest point on segment to circle center.
                t = ((target_x - x1) * dx + (target_y - y1) * dy) / seg_len_sq
                t = max(0.0, min(1.0, t))
                closest_x = x1 + t * dx
                closest_y = y1 + t * dy
                return math.hypot(closest_x - target_x, closest_y - target_y) <= effective_deviation_xy

            # Axis-aligned rectangle segment test (slab method)
            dx = x2 - x1
            dy = y2 - y1
            t0 = 0.0
            t1 = 1.0

            p = (-dx, dx, -dy, dy)
            q = (
                x1 - zone_min_x,
                zone_max_x - x1,
                y1 - zone_min_y,
                zone_max_y - y1,
            )

            for pi, qi in zip(p, q):
                if pi == 0.0:
                    if qi < 0.0:
                        return False
                else:
                    t = qi / pi
                    if pi < 0.0:
                        t0 = max(t0, t)
                    else:
                        t1 = min(t1, t)
                    if t0 > t1:
                        return False

            return True

        if zone_shape == "square":
            print(
                f"Node {node_id} square zone points: "
                f"{zone_points[0]}, {zone_points[1]}, {zone_points[2]}, {zone_points[3]}"
            )
        else:
            print(
                f"Node {node_id} circle zone center/radius: "
                f"({target_x}, {target_y}) / {effective_deviation_xy}"
            )
        prev_vx = None
        prev_vy = None

        while True:
            if self._all_action_states_terminal() or not self.state.order_id:
                return

            vx = float(self._vehicle._x)
            vy = float(self._vehicle._y)

            # distance_xy = math.hypot(target_x - vx, target_y - vy)
            # print(f"Distance to node {node_id}: {distance_xy:.2f} (target: {target_x:.2f}, {target_y:.2f}, current: {vx:.2f}, {vy:.2f})")

            if is_inside_zone(vx, vy):
                print(f"{node_id} Node reached (inside zone).")
                self._last_node_id = node_id
                self._last_node_sequence_id = self.get_sequence_by_node(node_id) 
                self._update_node_action_states(node_id, ActionStatus.FINISHED)
                self._update_related_edge_action_states(node_id, ActionStatus.FINISHED)
                break

            # if (
            #     prev_vx is not None
            #     and prev_vy is not None
            #     and segment_intersects_zone(prev_vx, prev_vy, vx, vy)
            # ):
            #     print(f"{node_id} Node reached (passed through zone).")
            #     self._update_node_action_states(node_id, ActionStatus.FINISHED)
            #     break

            prev_vx = vx
            prev_vy = vy

            await asyncio.sleep(0.1)


    # -------------------------
    # UPDATE ORDER ACCEPT PROCEDURE 
    # -------------------------
    def order_update_accept(self, order_request: Order) -> None:
        """Accept an order"""
        self.order = order_request
        if self.state is not None:
            self.state.order_id = order_request.order_id
            self.state.order_update_id = order_request.order_update_id


            #  check append is required or not 
            if self.update_or_append(order_request):
                # UPDATE ( Released True => False ) 
                
                # --- Update state's order if requested order has same sequence id ( Released True => False ) 
                if order_request.nodes:
                    for node in order_request.nodes:
                        self.update_node_by_sequence(node)


                # --- Update state's order if requested order has same sequence id ( Released True => False )
                if order_request.edges:
                    for edge in order_request.edges:
                        self.update_edge_by_sequence(edge)
            
            else:
                # APPEND ( A->B->C => A->B->C->D->E)          
                # --- Append nodes (skip first node to avoid duplication) ---
                if order_request.nodes:
                    if len(order_request.nodes) > 1:
                        self.state.node_states.extend(order_request.nodes[1:])

                # --- Append edges (edges do NOT need skipping) ---
                if order_request.edges:
                    self.state.edge_states.extend(order_request.edges)




            # --- Optional: append only NEW action states ---
            new_action_states = self.build_action_states(order_request)

            # Avoid duplicating action states (important)
            existing_ids = {a.action_id for a in self.state.action_states}
            filtered = [a for a in new_action_states if a.action_id not in existing_ids]

            self.state.action_states.extend(filtered)

        self.add_state_info(
            InfoType.ORDER_UPDATE_ACCEPTED,
            description=f"Order {order_request.order_id} accepted",
            info_level= InfoLevel.INFO,
            info_reference_key= "orderId",
            info_reference_value = order_request.order_id,
        )

        # Resume or start motion for appended route/actions. If _execute_path already
        # exited (all previous actions terminal), a new update with WAITING actions
        # must schedule again. If a task is still running, _schedule_execute_path no-ops
        # and the loop will pick up new action_states on its next outer iteration.
        if self.state is not None and not self._all_action_states_terminal():
            self._schedule_execute_path(interval_sec=1)

    
    # -------------------------
    # BUILD ACTION STATES
    # ------------------------- 
    def build_action_states(self, order_request: Order) -> List[ActionState]:
        action_states: List[ActionState] = []

        combined = []

        # Collect nodes
        for node in order_request.nodes:
            combined.append(("node", node.sequence_id, node))

        # Collect edges
        for edge in order_request.edges:
            combined.append(("edge", edge.sequence_id, edge))

        # Sort by sequence_id
        combined.sort(key=lambda x: x[1])

        # Build action states
        for source_type, sequence_id, obj in combined:
            actions = getattr(obj, "actions", []) or []
            is_node = source_type == "node"
            related_id = obj.node_id if is_node else obj.edge_id
            related_description = "Node" if is_node else "Edge"


            if actions:
                for action in actions:
                    action_states.append(
                        ActionState(
                            action_id=f"{source_type}_{sequence_id}_{action.action_id}",
                            action_status=ActionStatus.WAITING,
                            action_type=related_id,
                            action_description=related_description
                        )
                    )
            else:
                # No action → create placeholder
                action_states.append(
                    ActionState(
                        action_id=f"{source_type}_{sequence_id}",
                        action_status=ActionStatus.WAITING,
                        action_type=related_id,
                        action_description=related_description
                    )
                )

        return action_states
    

    # -------------------------
    # ORDER REJECT PROCEDURE 
    # ------------------------- 
    def order_reject(
        self,
        is_new_order: bool,
        error_level: ErrorLevel,
        error_type: ErrorType,
        description: str = "",
        error_hint: Optional[str] = None,
    ) -> None:
        """Reject an order and append an Error object to state.errors."""
        if self.state is None:
            return

        # Combine description and optional hint into a single description field
        full_description = description
        if error_hint:
            full_description = f"{description} Hint: {error_hint}"

        error_ref = ErrorReference(
            reference_key=self.state.order_id or "N/A",
            reference_value=description or "Order rejected",
        )
        error_ref_datetime = ErrorReference(
            reference_key="timestamp",
            reference_value=datetime.now().isoformat(),
        )

        error_obj = Error(
            error_type=error_type,
            error_level=error_level,
            error_references=[error_ref, error_ref_datetime],
            error_description=full_description or None,
        )

        self.state.errors.append(error_obj)

        if is_new_order:
            self.add_state_info(
                InfoType.ORDER_NEW_REJECTED,
                description=full_description or None,
                info_level= InfoLevel.DEBUG,
                info_reference_key= "orderId",
                info_reference_value = self.order.order_id if self.order else "",
            )
        else:
            self.add_state_info(
                InfoType.ORDER_UPDATE_REJECTED,
                description=full_description or None,
                info_level= InfoLevel.DEBUG,
                info_reference_key= "orderId",
                info_reference_value = self.order.order_id if self.order else "",
            )


    # -------------------------
    # ADD INFO TO STATE PROCEDURE
    # ------------------------- 
    def add_state_info(
        self,
        info_type: InfoType,
        description: str = "",
        info_level: InfoLevel = InfoLevel.INFO,
        info_reference_key: Optional[str] = None,
        info_reference_value: Optional[str] = None,
    ) -> None:
        """Append an info object to state.infos."""
        if self.state is None:
            return

        full_description = description


        info_ref = InfoReference(
            reference_key=info_reference_key,
            reference_value=info_reference_value,
        )
        
        info_ref_datetime = InfoReference(
            reference_key="timestamp",
            reference_value=datetime.now().isoformat(),
        )

        info_obj = Information(
            info_type=info_type,
            info_level=info_level,
            info_description=full_description or None,
            info_references=[info_ref, info_ref_datetime],
        )

        self.state.information.append(info_obj)


    # -------------------------
    # Instant Actions
    # -------------------------
    def _append_instant_action_states(self, request: InstantActions) -> None:
        """Add or update instant actions in state.instant_action_states (published as instantActionStates)."""
        if self.state is None:
            return

        by_id = {s.action_id: s for s in self.state.instant_action_states}
        for action in request.instant_actions:
            action_id = str(action.action_id)
            entry = ActionState(
                action_id=action_id,
                action_status=ActionStatus.RUNNING,
                action_type=action.action_type,
                action_description=action.action_description,
            )
            if action_id in by_id:
                existing = by_id[action_id]
                existing.action_status = ActionStatus.RUNNING
                existing.action_type = entry.action_type
                existing.action_description = entry.action_description
            else:
                self.state.instant_action_states.append(entry)
                by_id[action_id] = entry

    def _set_instant_action_status(
        self, action: Action, status: ActionStatus
    ) -> None:
        if self.state is None:
            return
        action_id = str(action.action_id)
        for entry in self.state.instant_action_states:
            if entry.action_id == action_id:
                entry.action_status = status
                return

    # Manage InstantAction
    # According to Type of Action, Action method will be different
    def manage_instant_actions(self, request: InstantActions):
        # clearInstantActions must run last so other actions in the same message stay in state
        actions = sorted(
            request.instant_actions,
            key=lambda a: a.action_type == "clearInstantActions",
        )
        for action in actions:

            handler = getattr(
                self,
                f"_instant_{action.action_type}",
                None
            )

            if handler:
                try:
                    handler(action)
                    self._set_instant_action_status(action, ActionStatus.FINISHED)
                except Exception as exc:
                    print(f"Instant action {action.action_type} failed: {exc}")
                    self._set_instant_action_status(action, ActionStatus.FAILED)
            else:
                print(
                    f"Unknown instant action: {action.action_type}"
                )
                self._set_instant_action_status(action, ActionStatus.FAILED)


    def _instant_clearInstantActions(self, action):
        if self.state is not None:
            self.state.instant_action_states = []

    def _instant_stopCharging(self, action):
        print("stop Charging")


    def _instant_startCharging(self, action):
        print("start Charging")
        pass

    # Manage to STOP motion, change state.node/edge.action into "PAUSED"
    def _instant_startPause(self, action):

        # print("start Pause")

        # Stop motion on the adapter loop (JIBOT socket is bound to that loop)
        self.stop_motion()

        # Update Node/Edge as "PAUSED"
        self._update_node_action_states(self._current_next_node, ActionStatus.PAUSED)
        self._update_related_edge_action_states(self._current_next_node, ActionStatus.PAUSED)

        self.stop_execute_path()

    # Manage to stop motion
    def stop_motion(self):
        if self._loop is not None and self._vehicle is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._vehicle.stop_motion(), self._loop
            )
            try:
                future.result(timeout=5)
            except Exception as exc:
                print(f"stop_motion error: {exc}")

    # Manage to stop execute_path
    def stop_execute_path(self):
        if self.execute_path_task and not self.execute_path_task.done():
            self._loop.call_soon_threadsafe(
                self.execute_path_task.cancel
            )

    async def _stop_execute_path_async(self) -> None:
        """Cancel _execute_path and wait until the task has finished."""
        if self.execute_path_task is None or self.execute_path_task.done():
            self.execute_path_task = None
            return
        self.execute_path_task.cancel()
        try:
            await self.execute_path_task
        except asyncio.CancelledError:
            pass
        self.execute_path_task = None

    def stop_execute_path_and_wait(self, timeout: float = 5) -> None:
        """Stop _execute_path from MQTT/instant-action thread; blocks until done."""
        if self._loop is None:
            return
        if self.execute_path_task is None or self.execute_path_task.done():
            self.execute_path_task = None
            return
        future = asyncio.run_coroutine_threadsafe(
            self._stop_execute_path_async(), self._loop
        )
        try:
            future.result(timeout=timeout)
        except Exception as exc:
            print(f"stop_execute_path_and_wait error: {exc}")

        
    # Manage to RESTART motion, find current state' actionStates has PAUSED Node, then restart from their
    # If No actionStates has PAUSED, then nothing to perform
    def _instant_stopPause(self, action):

        if self.state is not None and not self._all_action_states_terminal():
            self._schedule_execute_path(interval_sec=1)
        

    def _instant_cancelOrder(self, action):
        # print("Cancel Order")

        self.stop_motion()

        self._pending_execute_path_interval = None
        self.stop_execute_path_and_wait()

        self.order = None
        self._current_next_node = None
        # self._last_node_id = ""   # last node id should be kept for inform ACS about last node
        self._last_node_sequence_id = 0

        if self.state is not None:
            self.state.order_id = ""
            self.state.order_update_id = 0
            self.state.last_node_id = ""
            self.state.last_node_sequence_id = 0
            self.state.node_states = []
            self.state.edge_states = []
            # self.state.action_states = []   # instant action states should be cleared when "clearInstantActions" is called
            self.state.errors = []
            self.state.information = []

    







    
