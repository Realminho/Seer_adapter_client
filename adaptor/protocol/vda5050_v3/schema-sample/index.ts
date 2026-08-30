/**
 * @fileoverview VDA5050 v3 TypeScript 타입 및 샘플 데이터 통합 re-export
 * @description VDA5050 v3 표준 (mobile robot 용어 통일) 8개 토픽 타입과 샘플 데이터.
 *              Order, State, InstantActions, Connection, Visualization, Factsheet,
 *              Responses (v3 신규), ZoneSet (v3 신규).
 */

// ---- Common Types ----
export type {
	Vda5050Header,
	Vda5050MobileRobotPosition,
	Vda5050Velocity,
	Vda5050ControlPoint,
	Vda5050Trajectory,
	Vda5050PolylinePoint,
	Vda5050PlannedPath,
	Vda5050IntermediatePath,
	Vda5050Translation,
} from './vda5050-common.types';

// ---- Order ----
export {
	Vda5050BlockingType,
	Vda5050OrientationType,
	Vda5050CorridorReferencePoint,
	Vda5050ReleaseLossBehavior,
} from './vda5050-order.types';
export type {
	Vda5050Order,
	Vda5050Node,
	Vda5050NodePosition,
	Vda5050AllowedDeviation,
	Vda5050Edge,
	Vda5050Corridor,
	Vda5050Action,
	Vda5050ActionParameter,
} from './vda5050-order.types';
export { VDA5050_ORDER_SAMPLES } from './vda5050-order.sample';

// ---- State ----
export {
	Vda5050OperatingMode,
	Vda5050ActionStatus,
	Vda5050ErrorLevel,
	Vda5050InfoLevel,
	Vda5050EStopType,
	Vda5050MapStatus,
	Vda5050ZoneSetStatus,
	Vda5050ZoneRequestType,
	Vda5050EdgeRequestType,
	Vda5050RequestStatus,
} from './vda5050-state.types';
export type {
	Vda5050State,
	Vda5050Map,
	Vda5050ZoneSetState,
	Vda5050NodeState,
	Vda5050StateNodePosition,
	Vda5050EdgeState,
	Vda5050ActionState,
	Vda5050PowerSupply,
	Vda5050BatteryState,
	Vda5050Error,
	Vda5050ErrorReference,
	Vda5050Information,
	Vda5050InfoReference,
	Vda5050SafetyState,
	Vda5050Load,
	Vda5050BoundingBoxReference,
	Vda5050LoadDimensions,
	Vda5050ZoneRequest,
	Vda5050EdgeRequest,
} from './vda5050-state.types';
export { VDA5050_STATE_SAMPLES } from './vda5050-state.sample';

// ---- Instant Actions ----
export { Vda5050InstantBlockingType } from './vda5050-instant-actions.types';
export type {
	Vda5050InstantActions,
	Vda5050InstantAction,
} from './vda5050-instant-actions.types';
export { VDA5050_INSTANT_ACTIONS_SAMPLES } from './vda5050-instant-actions.sample';

// ---- Connection ----
export { Vda5050ConnectionState } from './vda5050-connection.types';
export type { Vda5050Connection } from './vda5050-connection.types';
export { VDA5050_CONNECTION_SAMPLES } from './vda5050-connection.sample';

// ---- Visualization ----
export type { Vda5050Visualization } from './vda5050-visualization.types';
export { VDA5050_VISUALIZATION_SAMPLES } from './vda5050-visualization.sample';

// ---- Factsheet ----
export {
	Vda5050MobileRobotKinematics,
	Vda5050MobileRobotClass,
	Vda5050LocalizationType,
	Vda5050NavigationType,
	Vda5050SupportedZoneType,
	Vda5050WheelType,
	Vda5050ParameterSupport,
	Vda5050ActionScope,
	Vda5050ValueDataType,
} from './vda5050-factsheet.types';
export type {
	Vda5050Factsheet,
	Vda5050TypeSpecification,
	Vda5050PhysicalParameters,
	Vda5050ProtocolLimits,
	Vda5050MaxStringLens,
	Vda5050MaxArrayLens,
	Vda5050Timing,
	Vda5050ProtocolFeatures,
	Vda5050OptionalParameter,
	Vda5050MobileRobotActionDef,
	Vda5050MobileRobotActionParamDef,
	Vda5050MobileRobotGeometry,
	Vda5050WheelDefinition,
	Vda5050WheelPosition,
	Vda5050Envelope2d,
	Vda5050Envelope3d,
	Vda5050PolygonPoint,
	Vda5050LoadSpecification,
	Vda5050LoadSet,
	Vda5050MobileRobotConfiguration,
	Vda5050VersionInfo,
	Vda5050NetworkConfig,
	Vda5050BatteryChargingConfig,
} from './vda5050-factsheet.types';
export { VDA5050_FACTSHEET_SAMPLES } from './vda5050-factsheet.sample';

// ---- Responses (v3 신규) ----
export { Vda5050GrantType } from './vda5050-responses.types';
export type {
	Vda5050Responses,
	Vda5050ResponseEntry,
} from './vda5050-responses.types';
export { VDA5050_RESPONSES_SAMPLES } from './vda5050-responses.sample';

// ---- ZoneSet (v3 신규) ----
export {
	Vda5050ZoneType,
	Vda5050ZoneReleaseLossBehavior,
	Vda5050DirectedLimitation,
	Vda5050BidirectedLimitation,
	Vda5050ZoneActionBlockingType,
} from './vda5050-zone-set.types';
export type {
	Vda5050ZoneSetMessage,
	Vda5050ZoneSet,
	Vda5050Zone,
	Vda5050Vertex,
	Vda5050ZoneAction,
} from './vda5050-zone-set.types';
export { VDA5050_ZONE_SET_SAMPLES } from './vda5050-zone-set.sample';
