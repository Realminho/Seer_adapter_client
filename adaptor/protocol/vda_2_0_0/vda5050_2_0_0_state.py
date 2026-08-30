from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum
from protocol.vda5050_common import AgvPosition, BoundingBoxReference, ErrorLevel, HeaderId, LoadDimensions, NodePosition, Trajectory, Velocity
from protocol.amr_fault_taxonomy import (
    VENDOR_ERROR_TYPE_REFERENCE_KEY,
    VENDOR_NAME_REFERENCE_KEY,
    resolve_common_error_type,
    resolve_vendor_name,
)

class OperatingMode(str, Enum):
    STARTUP = "STARTUP"
    AUTOMATIC = "AUTOMATIC"
    SEMIAUTOMATIC = "SEMIAUTOMATIC"
    INTERVENED = "INTERVENED"
    MANUAL = "MANUAL"
    SERVICE = "SERVICE"
    TEACHIN = "TEACHIN"

class ActionStatus(str, Enum):
    WAITING = "WAITING"  # Action was received but node not yet reached
    INITIALIZING = "INITIALIZING"  # Action was triggered, preparatory measures are initiated
    RUNNING = "RUNNING"  # The action is running
    PAUSED = "PAUSED"  # The action is paused
    FINISHED = "FINISHED"  # The action is finished
    FAILED = "FAILED"  # Action could not be finished

class EStop(str, Enum):
    AUTOACK = "AUTOACK"  # Auto-acknowledgeable e-stop is activated
    MANUAL = "MANUAL"  # E-stop has to be acknowledged manually at the vehicle
    REMOTE = "REMOTE"  # Facility e-stop has to be acknowledged remotely
    NONE = "NONE"  # No e-stop activated

# DEPRECATED: ErrorLevel moved to protocol.vda5050_common (canonical VDA5050
# v3 enum, four levels: WARNING/URGENT/CRITICAL/FATAL). The old definition here
# only had WARNING/FATAL, which silently made a v3 CRITICAL error impossible to
# express. It is re-exported (imported above) purely so legacy
# `from ...vda5050_2_0_0_state import ErrorLevel` keeps working; new code should
# import ErrorLevel from protocol.vda5050_common.

class ErrorType(str, Enum):
    """Internal error vocabulary an adapter raises. NOT what goes on the wire.

    어댑터가 raise 하는 내부 오류 어휘. wire 로 나가는 값이 아니다.

    Vendor-specific members (``JIBOT_*``, ``EZI_*``) are intentional: they carry
    the precise diagnosis at the point of failure. :meth:`Error.to_dict`
    translates them to the vendor-neutral :class:`~protocol.amr_fault_taxonomy.
    CommonErrorType` before publishing, and preserves the original in
    ``errorReferences.vendorErrorType``. So FMS never sees a brand name in
    ``errorType`` while operators keep the exact vendor code.
    벤더 고유 멤버(``JIBOT_*``, ``EZI_*``)는 의도된 것이다 — 실패 지점의 정밀한 진단을
    담는다. :meth:`Error.to_dict` 가 발행 직전에 벤더 중립 코드로 번역하고 원문은
    ``errorReferences.vendorErrorType`` 에 보존한다. FMS 는 ``errorType`` 에서 기종
    이름을 볼 일이 없고, 운영자는 정확한 벤더 코드를 그대로 본다.

    Adding a vendor member requires one row in
    :data:`~protocol.amr_fault_taxonomy.VENDOR_ERROR_ALIASES`; without it the raw
    value is published as-is (visible, not silently dropped) so the gap surfaces.
    벤더 멤버를 추가하면 :data:`~protocol.amr_fault_taxonomy.VENDOR_ERROR_ALIASES` 에
    한 줄을 추가한다. 빠뜨리면 원문이 그대로 발행되어(조용히 버리지 않음) 누락이 드러난다.
    """

    ORDER_JSON_PAYLOAD_INVALID = "ORDER_JSON_PAYLOAD_INVALID"  # Payload is invalid
    ORDER_CURRENT_NOT_FINISHED = "ORDER_CURRENT_NOT_FINISHED"  # Current order not finished
    ORDER_UPDATE_ID_INVALID = "ORDER_UPDATE_ID_INVALID"  # OrderupdateId is not correct +1
    ORDER_START_NODE_INVALID = "ORDER_START_NODE_INVALID"  # Requested start node is not the end node of the current order
    ORDER_START_SEQUENCE_ID_INVALID = "ORDER_START_SEQUENCE_ID_INVALID"  # Requested start sequence ID is not the last sequence ID of the current order

    ACTION_JSON_PAYLOAD_INVALID = "ACTION_JSON_PAYLOAD_INVALID"  # Payload is invalid
    ACTION_NOT_FOUND = "ACTION_NOT_FOUND"  # Requested action type is not registered
    INVALID_INSTANT_ACTION = "INVALID_INSTANT_ACTION"  # Instant action violates protocol rules
    ORDER_ACTION_FAILED = "ORDER_ACTION_FAILED"  # A node/edge action of the running order ended FAILED

    JIBOT_CONFLICT = "JIBOT_CONFLICT"  # JIBOT reported path/traffic conflict
    JIBOT_AVOIDANCE = "JIBOT_AVOIDANCE"  # JIBOT reported obstacle avoidance
    JIBOT_CONNECTION_LOST = "JIBOT_CONNECTION_LOST"  # Adapter lost the JIBOT TCP connection
    JIBOT_LOCALIZATION_LOST = "JIBOT_LOCALIZATION_LOST"  # JIBOT reported localization/path lost (status string "lost")
    JIBOT_NODE_UNREACHED = "JIBOT_NODE_UNREACHED"  # JIBOT stopped before the target node was reached
    JIBOT_GOTO_REJECTED = "JIBOT_GOTO_REJECTED"  # JIBOT rejected or did not acknowledge a node goto
    JIBOT_DOCK_FAILED = "JIBOT_DOCK_FAILED"  # UmDock did not lead to charging within the timeout
    JIBOT_MOTOR_FAULT = "JIBOT_MOTOR_FAULT"  # Drive motor reported a fault (motor_error) via /jrobot_status
    JIBOT_ARRIVAL_SIGNAL_MISMATCH = "JIBOT_ARRIVAL_SIGNAL_MISMATCH"  # JIBOT task/path diagnostics disagree with the reached order node
    JIBOT_BUMPER = "JIBOT_BUMPER"  # Bumper contact stop reported via /jrobot_status ("bumper Trigger!")
    JIBOT_MOTOR_DISABLED = "JIBOT_MOTOR_DISABLED"  # JIBOT task status reports the motor disabled ("#disable")
    EZI_IO_INPUT_FAILED = "EZI_IO_INPUT_FAILED"  # Adapter could not read EZI IO inputs
    LAST_NODE_ID_MISSING = "LAST_NODE_ID_MISSING"  # Adapter cannot currently publish a valid lastNodeId
    CONFIG_LOAD_FAILED = "CONFIG_LOAD_FAILED"  # Adapter could not load its config TOML; running in degraded config-error mode

    UNKNOWN_ERROR = "UNKNOWN_ERROR"  # Unknown error occurred

class InfoType(str, Enum):
    ORDER_NEW_ACCEPTED = "ORDER_NEW_ACCEPTED"  # Order accepted
    ORDER_NEW_REJECTED = "ORDER_NEW_REJECTED"  # Order rejected

    ORDER_UPDATE_ACCEPTED = "ORDER_UPDATE_ACCEPTED"  # Order update accepted
    ORDER_UPDATE_REJECTED = "ORDER_UPDATE_REJECTED"  # Order update rejected

    ACTION_ORDER_CANCEL_ACCEPTED = "ACTION_ORDER_CANCEL_ACCEPTED"  # Cancel order accepted
    ACTION_ORDER_CANCEL_REJECTED = "ACTION_ORDER_CANCEL_REJECTED"  # Cancel order rejected

    ACTION_START_CHARGE_ACCEPTED = "ACTION_START_CHARGE_ACCEPTED"  # Start charge action accepted
    ACTION_START_CHARGE_REJECTED = "ACTION_START_CHARGE_REJECTED"  # Start charge action rejected
    ACTION_STOP_CHARGE_ACCEPTED = "ACTION_STOP_CHARGE_ACCEPTED"  # Stop charge action accepted
    ACTION_STOP_CHARGE_REJECTED = "ACTION_STOP_CHARGE_REJECTED"  # Stop charge action rejected

    ACTION_STOP_PAUSE_ACCEPTED = "ACTION_STOP_PAUSE_ACCEPTED"  # Stop pause action accepted
    ACTION_STOP_PAUSE_REJECTED = "ACTION_STOP_PAUSE_REJECTED"  # Stop pause action rejected
    ACTION_START_PAUSE_ACCEPTED = "ACTION_START_PAUSE_ACCEPTED"  # Start pause action accepted
    ACTION_START_PAUSE_REJECTED = "ACTION_START_PAUSE_REJECTED"  # Start pause action rejected


class InfoLevel(str, Enum):
    INFO = "INFO"  # Used for visualization
    DEBUG = "DEBUG"  # Used for debugging

@dataclass
class NodeState:
    """Information about a node that the AGV still has to drive over"""
    node_id: str
    sequence_id: int
    released: bool
    node_description: Optional[str] = None
    node_position: Optional[NodePosition] = None
    
    def to_dict(self) -> Dict:
        result = {
            "nodeId": self.node_id,
            "sequenceId": self.sequence_id,
            "released": self.released
        }
        if self.node_description is not None:
            result["nodeDescription"] = self.node_description
        if self.node_position is not None:
            result["nodePosition"] = self.node_position.to_dict()
        return result

@dataclass
class EdgeState:
    """Information about an edge that the AGV still has to drive over"""
    edge_id: str
    sequence_id: int
    released: bool
    start_node_id: str  # Added start node ID
    end_node_id: str    # Added end node ID
    edge_description: Optional[str] = None
    trajectory: Optional[Trajectory] = None
    
    def to_dict(self) -> Dict:
        result = {
            "edgeId": self.edge_id,
            "sequenceId": self.sequence_id,
            "released": self.released,
            "startNodeId": self.start_node_id, # Added for completeness if needed
            "endNodeId": self.end_node_id,     # Added for completeness if needed
        }
        if self.edge_description is not None:
            result["edgeDescription"] = self.edge_description
        if self.trajectory is not None:
            result["trajectory"] = self.trajectory.to_dict()
        return result

@dataclass
class ActionState:
    """Information about an action"""
    action_id: str
    action_status: ActionStatus
    action_type: Optional[str] = None
    action_description: Optional[str] = None
    result_description: Optional[str] = None
    
    def to_dict(self) -> Dict:
        result = {
            "actionId": self.action_id,
            "actionStatus": self.action_status.value
        }
        if self.action_type is not None:
            result["actionType"] = self.action_type
        if self.action_description is not None:
            result["actionDescription"] = self.action_description
        if self.result_description is not None:
            result["resultDescription"] = self.result_description
        return result


@dataclass
class InstantActionState:
    """Information about an Instantaction"""
    action_id: str
    action_status: ActionStatus
    action_type: Optional[str] = None
    action_description: Optional[str] = None
    result_description: Optional[str] = None
    
    def to_dict(self) -> Dict:
        result = {
            "actionId": self.action_id,
            "actionStatus": self.action_status.value
        }
        if self.action_type is not None:
            result["actionType"] = self.action_type
        if self.action_description is not None:
            result["actionDescription"] = self.action_description
        if self.result_description is not None:
            result["resultDescription"] = self.result_description
        return result

@dataclass
class Load:
    """Load object that describes the load if the AGV has information about it"""
    load_id: Optional[str] = None
    load_type: Optional[str] = None
    load_position: Optional[str] = None
    bounding_box_reference: Optional[BoundingBoxReference] = None
    load_dimensions: Optional[LoadDimensions] = None
    weight: Optional[float] = None
    
    def to_dict(self) -> Dict:
        result = {}
        if self.load_id is not None:
            result["loadId"] = self.load_id
        if self.load_type is not None:
            result["loadType"] = self.load_type
        if self.load_position is not None:
            result["loadPosition"] = self.load_position
        if self.bounding_box_reference is not None:
            result["boundingBoxReference"] = self.bounding_box_reference.to_dict()
        if self.load_dimensions is not None:
            result["loadDimensions"] = self.load_dimensions.to_dict()
        if self.weight is not None:
            result["weight"] = self.weight
        return result

@dataclass
class BatteryState:
    """Contains all battery-related information"""
    battery_charge: float
    charging: bool
    battery_voltage: Optional[float] = None
    battery_health: Optional[int] = None
    reach: Optional[float] = None
    
    def to_dict(self) -> Dict:
        result = {
            "batteryCharge": self.battery_charge,
            "charging": self.charging
        }
        if self.battery_voltage is not None:
            result["batteryVoltage"] = self.battery_voltage
        if self.battery_health is not None:
            result["batteryHealth"] = self.battery_health
        if self.reach is not None:
            result["reach"] = self.reach
        return result

    def to_v3_power_supply_dict(self) -> Dict:
        result = {
            "stateOfCharge": self.battery_charge,
            "charging": self.charging
        }
        if self.battery_voltage is not None:
            result["batteryVoltage"] = self.battery_voltage
        if self.battery_health is not None:
            result["batteryHealth"] = self.battery_health
        if self.reach is not None:
            result["range"] = self.reach
        return result

@dataclass
class ErrorReference:
    """Object that holds the error reference as key-value pairs"""
    reference_key: str
    reference_value: str
    
    def to_dict(self) -> Dict:
        return {
            "referenceKey": self.reference_key,
            "referenceValue": self.reference_value
        }

@dataclass
class Error:
    """An error object"""
    error_type: ErrorType
    error_level: ErrorLevel
    error_references: List[ErrorReference]
    error_description: Optional[str] = None

    def to_dict(self) -> Dict:
        """Serialize to the VDA5050 wire shape, normalizing ``errorType``.

        VDA5050 wire 형태로 직렬화하면서 ``errorType`` 을 정규화한다.

        Adapters raise vendor-specific members (``JIBOT_*``) because that is
        where the diagnosis lives, but fleet control must not branch on a robot
        brand. This is the single serialization choke point for both the v2 and
        the v3 publish paths, so translating here covers every error site
        without touching any raise site.
        어댑터는 진단 정보를 담은 벤더 고유 멤버(``JIBOT_*``)를 그대로 raise 하지만,
        교통 제어가 기종 이름으로 분기해서는 안 된다. 이 메서드가 v2/v3 발행 경로가
        공유하는 유일한 직렬화 지점이라 여기서 번역하면 raise 지점을 하나도 건드리지
        않고 모든 오류가 정규화된다.

        The vendor original is preserved as a ``vendorErrorType`` reference (with
        ``vendorName`` when inferable) so operator messages and logs keep the
        precise vendor diagnosis.
        벤더 원문은 ``vendorErrorType`` 참조로 보존한다(추정 가능하면 ``vendorName``
        도 함께). 운영자 메시지와 로그의 진단 정밀도는 유지된다.

        :returns: VDA5050 ``error`` object with a vendor-neutral ``errorType``.
            벤더 중립 ``errorType`` 을 가진 VDA5050 ``error`` 객체.
        """
        common_error_type, vendor_error_type = resolve_common_error_type(self.error_type)
        references = [ref.to_dict() for ref in self.error_references]

        if vendor_error_type:
            # Never overwrite a reference an adapter set deliberately.
            # 어댑터가 의도적으로 설정한 참조는 덮지 않는다.
            existing_keys = {ref.get("referenceKey") for ref in references}
            if VENDOR_ERROR_TYPE_REFERENCE_KEY not in existing_keys:
                references.append({
                    "referenceKey": VENDOR_ERROR_TYPE_REFERENCE_KEY,
                    "referenceValue": vendor_error_type,
                })
            vendor_name = resolve_vendor_name(vendor_error_type)
            if vendor_name and VENDOR_NAME_REFERENCE_KEY not in existing_keys:
                references.append({
                    "referenceKey": VENDOR_NAME_REFERENCE_KEY,
                    "referenceValue": vendor_name,
                })

        result = {
            "errorType": common_error_type,
            "errorLevel": self.error_level.value,
            "errorReferences": references
        }
        if self.error_description is not None:
            result["errorDescription"] = self.error_description
        return result

@dataclass
class InfoReference:
    """Object that holds the info reference as key-value pairs"""
    reference_key: str
    reference_value: str
    
    def to_dict(self) -> Dict:
        return {
            "referenceKey": self.reference_key,
            "referenceValue": self.reference_value
        }

@dataclass
class Information:
    """An information object"""
    info_type: InfoType
    info_level: InfoLevel
    info_references: List[InfoReference]
    info_description: Optional[str] = None
    
    def to_dict(self) -> Dict:
        result = {
            "infoType": self.info_type,
            "infoLevel": self.info_level.value,
            "infoReferences": [ref.to_dict() for ref in self.info_references]
        }
        if self.info_description is not None:
            result["infoDescription"] = self.info_description
        return result

@dataclass
class SafetyState:
    """Object that holds information about the safety status"""
    e_stop: EStop
    field_violation: bool
    
    def to_dict(self) -> Dict:
        return {
            "eStop": self.e_stop.value,
            "fieldViolation": self.field_violation
        }

    def to_v3_dict(self) -> Dict:
        active_emergency_stop = self.e_stop.value
        if self.e_stop == EStop.AUTOACK:
            active_emergency_stop = EStop.NONE.value
        return {
            "activeEmergencyStop": active_emergency_stop,
            "fieldViolation": self.field_violation
        }

@dataclass
class State:
    """All encompassing state of the AGV"""
    header_id: HeaderId
    timestamp: str
    version: str
    manufacturer: str
    serial_number: str
    order_id: str
    order_update_id: int
    last_node_id: str
    last_node_sequence_id: int
    driving: bool
    operating_mode: OperatingMode
    node_states: List[NodeState]
    edge_states: List[EdgeState]
    action_states: List[ActionState]
    instant_action_states: List[InstantActionState]
    battery_state: BatteryState
    errors: List[Error]
    information: List[Information]
    loads: List[Load]
    safety_state: SafetyState
    agv_position: Optional[AgvPosition] = None
    velocity: Optional[Velocity] = None
    zone_set_id: Optional[str] = None
    paused: Optional[bool] = None
    new_base_request: Optional[bool] = None
    distance_since_last_node: Optional[float] = None
    
    def to_dict(self) -> Dict:
        if self._is_v3():
            return self._to_v3_dict()

        result = {
            "headerId": self.header_id,
            "timestamp": self.timestamp,
            "version": self.version,
            "manufacturer": self.manufacturer,
            "serialNumber": self.serial_number,
            "orderId": self.order_id,
            "orderUpdateId": self.order_update_id,
            "lastNodeId": self.last_node_id,
            "lastNodeSequenceId": self.last_node_sequence_id,
            "driving": self.driving,
            "operatingMode": self.operating_mode.value,
            "nodeStates": [node.to_dict() for node in self.node_states],
            "edgeStates": [edge.to_dict() for edge in self.edge_states],
            "actionStates": [action.to_dict() for action in self.action_states],
            "instantActionStates": [action.to_dict() for action in self.instant_action_states],
            "batteryState": self.battery_state.to_dict(),
            "errors": [error.to_dict() for error in self.errors],
            "information": [info.to_dict() for info in self.information],
            "loads": [load.to_dict() for load in self.loads],
            "safetyState": self.safety_state.to_dict()
        }
        if self.agv_position is not None:
            agv_position = self.agv_position.to_dict()
            result["agvPosition"] = agv_position
            result["mobileRobotPosition"] = agv_position
        if self.velocity is not None:
            result["velocity"] = self.velocity.to_dict()
        if self.zone_set_id is not None:
            result["zoneSetId"] = self.zone_set_id
        if self.paused is not None:
            result["paused"] = self.paused
        if self.new_base_request is not None:
            result["newBaseRequest"] = self.new_base_request
        if self.distance_since_last_node is not None:
            result["distanceSinceLastNode"] = self.distance_since_last_node
        return result

    def _is_v3(self) -> bool:
        try:
            return int(str(self.version).split(".", 1)[0]) >= 3
        except (TypeError, ValueError):
            return False

    def _to_v3_dict(self) -> Dict:
        result = {
            "headerId": self.header_id,
            "timestamp": self.timestamp,
            "version": self.version,
            "manufacturer": self.manufacturer,
            "serialNumber": self.serial_number,
            "orderId": self.order_id,
            "orderUpdateId": self.order_update_id,
            "lastNodeId": self.last_node_id,
            "lastNodeSequenceId": self.last_node_sequence_id,
            "nodeStates": [node.to_dict() for node in self.node_states],
            "edgeStates": [edge.to_dict() for edge in self.edge_states],
            "driving": self.driving,
            "actionStates": [action.to_dict() for action in self.action_states],
            "instantActionStates": [
                action.to_dict() for action in self.instant_action_states
            ],
            "powerSupply": self.battery_state.to_v3_power_supply_dict(),
            "operatingMode": self._v3_operating_mode(),
            "errors": [error.to_dict() for error in self.errors],
            "information": [info.to_dict() for info in self.information],
            "loads": [load.to_dict() for load in self.loads],
            "safetyState": self.safety_state.to_v3_dict()
        }
        if self.agv_position is not None:
            result["mobileRobotPosition"] = self._mobile_robot_position_dict()
        if self.velocity is not None:
            result["velocity"] = self.velocity.to_dict()
        if self.paused is not None:
            result["paused"] = self.paused
        if self.new_base_request is not None:
            result["newBaseRequest"] = self.new_base_request
        if self.distance_since_last_node is not None:
            result["distanceSinceLastNode"] = self.distance_since_last_node
        return result

    def _v3_operating_mode(self) -> str:
        if self.operating_mode == OperatingMode.TEACHIN:
            return "TEACH_IN"
        return self.operating_mode.value

    def _mobile_robot_position_dict(self) -> Dict[str, Any]:
        position = {
            "x": self.agv_position.x,
            "y": self.agv_position.y,
            "theta": self.agv_position.theta,
            "mapId": self.agv_position.map_id,
            "localized": self.agv_position.position_initialized
        }
        if self.agv_position.localization_score is not None:
            position["localizationScore"] = self.agv_position.localization_score
        if self.agv_position.deviation_range is not None:
            position["deviationRange"] = self.agv_position.deviation_range
        return position
