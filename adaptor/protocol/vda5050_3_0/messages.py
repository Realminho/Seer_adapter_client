from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


def _strip_none(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


class BlockingType(str, Enum):
    NONE = "NONE"
    SOFT = "SOFT"
    SINGLE = "SINGLE"
    HARD = "HARD"


class ConnectionState(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    HIBERNATING = "HIBERNATING"
    CONNECTION_BROKEN = "CONNECTION_BROKEN"


class OperatingMode(str, Enum):
    STARTUP = "STARTUP"
    AUTOMATIC = "AUTOMATIC"
    SEMIAUTOMATIC = "SEMIAUTOMATIC"
    INTERVENED = "INTERVENED"
    MANUAL = "MANUAL"
    SERVICE = "SERVICE"
    TEACH_IN = "TEACH_IN"


class ActionStatus(str, Enum):
    WAITING = "WAITING"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RETRIABLE = "RETRIABLE"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class EmergencyStop(str, Enum):
    MANUAL = "MANUAL"
    REMOTE = "REMOTE"
    NONE = "NONE"


@dataclass
class Header:
    header_id: int
    timestamp: str
    version: str
    manufacturer: str
    serial_number: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Header":
        return cls(
            header_id=data["headerId"],
            timestamp=data["timestamp"],
            version=data["version"],
            manufacturer=data["manufacturer"],
            serial_number=data["serialNumber"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "headerId": self.header_id,
            "timestamp": self.timestamp,
            "version": self.version,
            "manufacturer": self.manufacturer,
            "serialNumber": self.serial_number,
        }


@dataclass
class ActionParameter:
    key: str
    value: Any

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionParameter":
        return cls(key=data["key"], value=data.get("value"))

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "value": self.value}


@dataclass
class Action:
    action_type: str
    action_id: str
    blocking_type: BlockingType
    action_parameters: List[ActionParameter] = field(default_factory=list)
    action_descriptor: Optional[str] = None
    retriable: Optional[bool] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Action":
        raw_parameters = (
            data.get("actionParameters")
            if "actionParameters" in data
            else data.get("actionParameter", [])
        )
        return cls(
            action_type=data["actionType"],
            action_id=str(data["actionId"]),
            blocking_type=BlockingType(data["blockingType"]),
            action_descriptor=(
                data.get("actionDescriptor")
                or data.get("actionDescription")
            ),
            retriable=data.get("retriable"),
            action_parameters=[
                ActionParameter.from_dict(param)
                for param in raw_parameters
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return _strip_none(
            {
                "actionType": self.action_type,
                "actionId": self.action_id,
                "actionDescriptor": self.action_descriptor,
                "blockingType": self.blocking_type.value,
                "actionParameters": [
                    param.to_dict() for param in self.action_parameters
                ],
                "retriable": self.retriable,
            }
        )


@dataclass
class NodePosition:
    x: float
    y: float
    map_id: str
    theta: Optional[float] = None
    allowed_deviation_xy: Optional[Any] = None
    allowed_deviation_theta: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodePosition":
        return cls(
            x=data["x"],
            y=data["y"],
            theta=data.get("theta"),
            map_id=data["mapId"],
            allowed_deviation_xy=data.get("allowedDeviationXY"),
            allowed_deviation_theta=data.get("allowedDeviationTheta"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _strip_none(
            {
                "x": self.x,
                "y": self.y,
                "theta": self.theta,
                "mapId": self.map_id,
                "allowedDeviationXY": self.allowed_deviation_xy,
                "allowedDeviationTheta": self.allowed_deviation_theta,
            }
        )


@dataclass
class Node:
    node_id: str
    sequence_id: int
    released: bool
    actions: List[Action] = field(default_factory=list)
    node_descriptor: Optional[str] = None
    node_position: Optional[NodePosition] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Node":
        return cls(
            node_id=data["nodeId"],
            sequence_id=data["sequenceId"],
            released=data["released"],
            node_descriptor=data.get("nodeDescriptor"),
            node_position=(
                NodePosition.from_dict(data["nodePosition"])
                if "nodePosition" in data
                else None
            ),
            actions=[
                Action.from_dict(action_data)
                for action_data in data.get("actions", [])
            ],
        )

    @property
    def node_description(self) -> Optional[str]:
        return self.node_descriptor

    def to_dict(self) -> Dict[str, Any]:
        return _strip_none(
            {
                "nodeId": self.node_id,
                "sequenceId": self.sequence_id,
                "nodeDescriptor": self.node_descriptor,
                "released": self.released,
                "nodePosition": (
                    self.node_position.to_dict()
                    if self.node_position is not None
                    else None
                ),
                "actions": [action.to_dict() for action in self.actions],
            }
        )


@dataclass
class Edge:
    edge_id: str
    sequence_id: int
    released: bool
    actions: List[Action] = field(default_factory=list)
    start_node_id: str = ""
    end_node_id: str = ""
    edge_descriptor: Optional[str] = None
    maximum_speed: Optional[float] = None
    maximum_mobile_robot_height: Optional[float] = None
    minimum_load_handling_device_height: Optional[float] = None
    orientation: Optional[float] = None
    orientation_type: Optional[str] = None
    direction: Optional[str] = None
    reach_orientation_before_entering: Optional[bool] = None
    max_rotation_speed: Optional[float] = None
    length: Optional[float] = None
    trajectory: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], nodes: List[Node]) -> "Edge":
        start_node_id, end_node_id = _edge_node_ids(data, nodes)
        return cls(
            edge_id=data["edgeId"],
            sequence_id=data["sequenceId"],
            released=data["released"],
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            edge_descriptor=data.get("edgeDescriptor"),
            maximum_speed=data.get("maximumSpeed"),
            maximum_mobile_robot_height=data.get("maximumMobileRobotHeight"),
            minimum_load_handling_device_height=data.get(
                "minimumLoadHandlingDeviceHeight"
            ),
            orientation=data.get("orientation"),
            orientation_type=data.get("orientationType"),
            direction=data.get("direction"),
            reach_orientation_before_entering=data.get(
                "reachOrientationBeforeEntering"
            ),
            max_rotation_speed=data.get("maxRotationSpeed"),
            length=data.get("length"),
            trajectory=data.get("trajectory"),
            actions=[
                Action.from_dict(action_data)
                for action_data in data.get("actions", [])
            ],
        )

    @property
    def edge_description(self) -> Optional[str]:
        return self.edge_descriptor

    @property
    def max_speed(self) -> Optional[float]:
        return self.maximum_speed

    @property
    def rotation_allowed(self) -> Optional[bool]:
        return self.reach_orientation_before_entering

    def to_dict(self) -> Dict[str, Any]:
        return _strip_none(
            {
                "edgeId": self.edge_id,
                "sequenceId": self.sequence_id,
                "edgeDescriptor": self.edge_descriptor,
                "released": self.released,
                "maximumSpeed": self.maximum_speed,
                "maximumMobileRobotHeight": self.maximum_mobile_robot_height,
                "minimumLoadHandlingDeviceHeight": (
                    self.minimum_load_handling_device_height
                ),
                "orientation": self.orientation,
                "orientationType": self.orientation_type,
                "direction": self.direction,
                "reachOrientationBeforeEntering": (
                    self.reach_orientation_before_entering
                ),
                "maxRotationSpeed": self.max_rotation_speed,
                "trajectory": self.trajectory,
                "length": self.length,
                "actions": [action.to_dict() for action in self.actions],
            }
        )


def _edge_node_ids(edge_data: Dict[str, Any], nodes: List[Node]) -> Tuple[str, str]:
    if "startNodeId" in edge_data and "endNodeId" in edge_data:
        return edge_data["startNodeId"], edge_data["endNodeId"]

    edge_sequence_id = edge_data["sequenceId"]
    previous_nodes = [
        node for node in nodes if node.sequence_id < edge_sequence_id
    ]
    next_nodes = [
        node for node in nodes if node.sequence_id > edge_sequence_id
    ]
    if not previous_nodes or not next_nodes:
        raise ValueError(
            "VDA5050 v3.0 edge requires neighboring nodes by sequenceId when "
            "startNodeId/endNodeId are omitted"
        )

    start_node = max(previous_nodes, key=lambda node: node.sequence_id)
    end_node = min(next_nodes, key=lambda node: node.sequence_id)
    return start_node.node_id, end_node.node_id


@dataclass
class Order:
    header: Header
    order_id: str
    order_update_id: int
    nodes: List[Node]
    edges: List[Edge]
    order_description: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Order":
        nodes = [Node.from_dict(node_data) for node_data in data.get("nodes", [])]
        edges = [
            Edge.from_dict(edge_data, nodes)
            for edge_data in data.get("edges", [])
        ]
        return cls(
            header=Header.from_dict(data),
            order_id=data["orderId"],
            order_update_id=data["orderUpdateId"],
            order_description=data.get("orderDescription"),
            nodes=nodes,
            edges=edges,
        )

    @property
    def header_id(self) -> int:
        return self.header.header_id

    @property
    def timestamp(self) -> str:
        return self.header.timestamp

    @property
    def version(self) -> str:
        return self.header.version

    @property
    def manufacturer(self) -> str:
        return self.header.manufacturer

    @property
    def serial_number(self) -> str:
        return self.header.serial_number

    def to_dict(self) -> Dict[str, Any]:
        result = self.header.to_dict()
        result.update(
            _strip_none(
                {
                    "orderId": self.order_id,
                    "orderUpdateId": self.order_update_id,
                    "orderDescription": self.order_description,
                    "nodes": [node.to_dict() for node in self.nodes],
                    "edges": [edge.to_dict() for edge in self.edges],
                }
            )
        )
        return result


@dataclass
class InstantActions:
    header: Header
    actions: List[Action]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstantActions":
        return cls(
            header=Header.from_dict(data),
            actions=[
                Action.from_dict(action_data)
                for action_data in data.get("actions", [])
            ],
        )

    @property
    def instant_actions(self) -> List[Action]:
        return self.actions

    def to_dict(self) -> Dict[str, Any]:
        result = self.header.to_dict()
        result["actions"] = [action.to_dict() for action in self.actions]
        return result


@dataclass
class Connection:
    header: Header
    connection_state: ConnectionState

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Connection":
        return cls(
            header=Header.from_dict(data),
            connection_state=ConnectionState(data["connectionState"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        result = self.header.to_dict()
        result["connectionState"] = self.connection_state.value
        return result


@dataclass
class MobileRobotPosition:
    x: float
    y: float
    theta: float
    map_id: str
    localized: bool
    localization_score: Optional[float] = None
    deviation_range: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _strip_none(
            {
                "x": self.x,
                "y": self.y,
                "theta": self.theta,
                "mapId": self.map_id,
                "localized": self.localized,
                "localizationScore": self.localization_score,
                "deviationRange": self.deviation_range,
            }
        )


@dataclass
class ActionState:
    action_id: str
    action_status: ActionStatus
    action_type: Optional[str] = None
    action_descriptor: Optional[str] = None
    action_result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _strip_none(
            {
                "actionId": self.action_id,
                "actionType": self.action_type,
                "actionDescriptor": self.action_descriptor,
                "actionStatus": self.action_status.value,
                "actionResult": self.action_result,
            }
        )


@dataclass
class PowerSupply:
    state_of_charge: float
    charging: bool
    battery_voltage: Optional[float] = None
    battery_current: Optional[float] = None
    battery_health: Optional[float] = None
    range: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _strip_none(
            {
                "stateOfCharge": self.state_of_charge,
                "batteryVoltage": self.battery_voltage,
                "batteryCurrent": self.battery_current,
                "batteryHealth": self.battery_health,
                "charging": self.charging,
                "range": self.range,
            }
        )


@dataclass
class SafetyState:
    active_emergency_stop: EmergencyStop
    field_violation: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activeEmergencyStop": self.active_emergency_stop.value,
            "fieldViolation": self.field_violation,
        }


@dataclass
class Load:
    load_id: Optional[str] = None
    load_type: Optional[str] = None
    load_position: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _strip_none(
            {
                "loadId": self.load_id,
                "loadType": self.load_type,
                "loadPosition": self.load_position,
            }
        )


@dataclass
class State:
    header: Header
    order_id: str
    order_update_id: int
    last_node_id: str
    last_node_sequence_id: int
    node_states: List[Any]
    edge_states: List[Any]
    driving: bool
    action_states: List[ActionState]
    instant_action_states: List[ActionState]
    power_supply: PowerSupply
    operating_mode: OperatingMode
    errors: List[Dict[str, Any]]
    safety_state: SafetyState
    information: List[Dict[str, Any]] = field(default_factory=list)
    loads: List[Load] = field(default_factory=list)
    mobile_robot_position: Optional[MobileRobotPosition] = None
    paused: Optional[bool] = None
    new_base_request: Optional[bool] = None
    distance_since_last_node: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        result = self.header.to_dict()
        result.update(
            _strip_none(
                {
                    "orderId": self.order_id,
                    "orderUpdateId": self.order_update_id,
                    "lastNodeId": self.last_node_id,
                    "lastNodeSequenceId": self.last_node_sequence_id,
                    "nodeStates": [_message_to_dict(node) for node in self.node_states],
                    "edgeStates": [_message_to_dict(edge) for edge in self.edge_states],
                    "mobileRobotPosition": (
                        self.mobile_robot_position.to_dict()
                        if self.mobile_robot_position is not None
                        else None
                    ),
                    "loads": [load.to_dict() for load in self.loads],
                    "driving": self.driving,
                    "paused": self.paused,
                    "newBaseRequest": self.new_base_request,
                    "distanceSinceLastNode": self.distance_since_last_node,
                    "actionStates": [
                        action.to_dict() for action in self.action_states
                    ],
                    "instantActionStates": [
                        action.to_dict() for action in self.instant_action_states
                    ],
                    "powerSupply": self.power_supply.to_dict(),
                    "operatingMode": self.operating_mode.value,
                    "errors": self.errors,
                    "information": self.information,
                    "safetyState": self.safety_state.to_dict(),
                }
            )
        )
        return result


def _message_to_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
