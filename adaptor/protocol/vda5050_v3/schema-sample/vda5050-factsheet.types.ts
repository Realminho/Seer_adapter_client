/**
 * @fileoverview VDA5050 v3 Factsheet 메시지 TypeScript 타입 정의
 * @description Mobile robot 사양/능력 시트 (mobile robot 용어 통일)
 * @see factsheet.schema (vda5050_v3/json_schemas/factsheet.schema)
 */

// ============================================================
// Constants
// ============================================================

/**
 * Mobile robot kinematics (확장 가능 enum, v3에서 mobileRobotKinematics 명칭)
 */
export const Vda5050MobileRobotKinematics = {
	DIFFERENTIAL: 'DIFFERENTIAL',
	OMNIDIRECTIONAL: 'OMNIDIRECTIONAL',
	THREE_WHEEL: 'THREE_WHEEL',
} as const;
export type Vda5050MobileRobotKinematics =
	(typeof Vda5050MobileRobotKinematics)[keyof typeof Vda5050MobileRobotKinematics];

/**
 * Mobile robot class (확장 가능 enum)
 */
export const Vda5050MobileRobotClass = {
	FORKLIFT: 'FORKLIFT',
	CONVEYOR: 'CONVEYOR',
	TUGGER: 'TUGGER',
	CARRIER: 'CARRIER',
} as const;
export type Vda5050MobileRobotClass =
	(typeof Vda5050MobileRobotClass)[keyof typeof Vda5050MobileRobotClass];

export const Vda5050LocalizationType = {
	NATURAL: 'NATURAL',
	REFLECTOR: 'REFLECTOR',
	RFID: 'RFID',
	DMC: 'DMC',
	SPOT: 'SPOT',
	GRID: 'GRID',
} as const;
export type Vda5050LocalizationType =
	(typeof Vda5050LocalizationType)[keyof typeof Vda5050LocalizationType];

export const Vda5050NavigationType = {
	PHYSICAL_LINE_GUIDED: 'PHYSICAL_LINE_GUIDED',
	VIRTUAL_LINE_GUIDED: 'VIRTUAL_LINE_GUIDED',
	FREELY_NAVIGATING: 'FREELY_NAVIGATING',
} as const;
export type Vda5050NavigationType =
	(typeof Vda5050NavigationType)[keyof typeof Vda5050NavigationType];

/**
 * 지원 zone 타입 (v3 신규)
 */
export const Vda5050SupportedZoneType = {
	BLOCKED: 'BLOCKED',
	LINE_GUIDED: 'LINE_GUIDED',
	RELEASE: 'RELEASE',
	COORDINATED_REPLANNING: 'COORDINATED_REPLANNING',
	SPEED_LIMIT: 'SPEED_LIMIT',
	ACTION: 'ACTION',
	PRIORITY: 'PRIORITY',
	PENALTY: 'PENALTY',
	DIRECTED: 'DIRECTED',
	BIDIRECTED: 'BIDIRECTED',
} as const;
export type Vda5050SupportedZoneType =
	(typeof Vda5050SupportedZoneType)[keyof typeof Vda5050SupportedZoneType];

export const Vda5050WheelType = {
	DRIVE: 'DRIVE',
	CASTER: 'CASTER',
	FIXED: 'FIXED',
	MECANUM: 'MECANUM',
} as const;
export type Vda5050WheelType =
	(typeof Vda5050WheelType)[keyof typeof Vda5050WheelType];

export const Vda5050ParameterSupport = {
	SUPPORTED: 'SUPPORTED',
	REQUIRED: 'REQUIRED',
} as const;
export type Vda5050ParameterSupport =
	(typeof Vda5050ParameterSupport)[keyof typeof Vda5050ParameterSupport];

/**
 * Action scope (v3: ZONE 추가)
 */
export const Vda5050ActionScope = {
	INSTANT: 'INSTANT',
	NODE: 'NODE',
	EDGE: 'EDGE',
	ZONE: 'ZONE',
} as const;
export type Vda5050ActionScope =
	(typeof Vda5050ActionScope)[keyof typeof Vda5050ActionScope];

export const Vda5050ValueDataType = {
	BOOL: 'BOOL',
	NUMBER: 'NUMBER',
	INTEGER: 'INTEGER',
	STRING: 'STRING',
	OBJECT: 'OBJECT',
	ARRAY: 'ARRAY',
} as const;
export type Vda5050ValueDataType =
	(typeof Vda5050ValueDataType)[keyof typeof Vda5050ValueDataType];

// ============================================================
// Interfaces
// ============================================================

/**
 * VDA5050 v3 Factsheet 메시지
 */
export interface Vda5050Factsheet {
	headerId: number;
	timestamp: string;
	version: string;
	manufacturer: string;
	serialNumber: string;
	typeSpecification: Vda5050TypeSpecification;
	physicalParameters: Vda5050PhysicalParameters;
	protocolLimits: Vda5050ProtocolLimits;
	protocolFeatures: Vda5050ProtocolFeatures;
	mobileRobotGeometry: Vda5050MobileRobotGeometry;
	loadSpecification: Vda5050LoadSpecification;
	mobileRobotConfiguration?: Vda5050MobileRobotConfiguration;
}

export interface Vda5050TypeSpecification {
	seriesName: string;
	seriesDescription?: string;
	mobileRobotKinematics: Vda5050MobileRobotKinematics | string;
	mobileRobotClass: Vda5050MobileRobotClass | string;
	maximumLoadMass: number;
	localizationTypes: (Vda5050LocalizationType | string)[];
	navigationTypes: (Vda5050NavigationType | string)[];
	supportedZones?: Vda5050SupportedZoneType[];
}

export interface Vda5050PhysicalParameters {
	minimumSpeed: number;
	maximumSpeed: number;
	minimumAngularSpeed?: number;
	maximumAngularSpeed?: number;
	maximumAcceleration: number;
	maximumDeceleration: number;
	minimumHeight: number;
	maximumHeight: number;
	width: number;
	length: number;
}

export interface Vda5050ProtocolLimits {
	maximumStringLengths: Vda5050MaxStringLens;
	maximumArrayLengths: Vda5050MaxArrayLens;
	timing: Vda5050Timing;
}

export interface Vda5050MaxStringLens {
	maximumMessageLength?: number;
	maximumTopicSerialLength?: number;
	maximumTopicElementLength?: number;
	maximumIdLength?: number;
	idNumericalOnly?: boolean;
	maximumLoadIdLength?: number;
}

export interface Vda5050MaxArrayLens {
	'order.nodes'?: number;
	'order.edges'?: number;
	'node.actions'?: number;
	'edge.actions'?: number;
	'actions.actionsParameters'?: number;
	instantActions?: number;
	'trajectory.knotVector'?: number;
	'trajectory.controlPoints'?: number;
	'zoneSet.zones'?: number;
	'state.nodeStates'?: number;
	'state.edgeStates'?: number;
	'state.loads'?: number;
	'state.actionStates'?: number;
	'state.instantActionStates'?: number;
	'state.zoneActionStates'?: number;
	'state.errors'?: number;
	'state.information'?: number;
	'error.errorReferences'?: number;
	'information.infoReferences'?: number;
}

export interface Vda5050Timing {
	minimumOrderInterval: number;
	minimumStateInterval: number;
	defaultStateInterval?: number;
	visualizationInterval?: number;
}

export interface Vda5050ProtocolFeatures {
	optionalParameters: Vda5050OptionalParameter[];
	mobileRobotActions: Vda5050MobileRobotActionDef[];
}

export interface Vda5050OptionalParameter {
	parameter: string;
	support: Vda5050ParameterSupport;
	description?: string;
}

export interface Vda5050MobileRobotActionDef {
	actionType: string;
	actionDescription?: string;
	actionScopes: Vda5050ActionScope[];
	actionParameters?: Vda5050MobileRobotActionParamDef[];
	actionResult?: string;
	blockingTypes?: ('NONE' | 'SOFT' | 'SINGLE' | 'HARD')[];
	pauseAllowed: string;
	cancelAllowed: string;
}

export interface Vda5050MobileRobotActionParamDef {
	key: string;
	valueDataType: Vda5050ValueDataType;
	description?: string;
	isOptional?: boolean;
}

export interface Vda5050MobileRobotGeometry {
	wheelDefinitions?: Vda5050WheelDefinition[];
	envelopes2d?: Vda5050Envelope2d[];
	envelopes3d?: Vda5050Envelope3d[];
}

export interface Vda5050WheelDefinition {
	type: Vda5050WheelType | string;
	isActiveDriven: boolean;
	isActiveSteered: boolean;
	position: Vda5050WheelPosition;
	diameter: number;
	width: number;
	centerDisplacement?: number;
	constraints?: string;
}

export interface Vda5050WheelPosition {
	x: number;
	y: number;
	theta?: number;
}

export interface Vda5050Envelope2d {
	envelope2dId: string;
	vertices: Vda5050PolygonPoint[];
	description?: string;
}

export interface Vda5050Envelope3d {
	envelope3dId: string;
	format: string;
	data?: Record<string, unknown>;
	url?: string;
	description?: string;
}

export interface Vda5050PolygonPoint {
	x: number;
	y: number;
}

export interface Vda5050LoadSpecification {
	loadPositions?: string[];
	loadSets?: Vda5050LoadSet[];
}

export interface Vda5050LoadSet {
	setName: string;
	loadType: string;
	loadPositions?: string[];
	boundingBoxReference?: { x: number; y: number; z: number; theta?: number };
	loadDimensions?: { length: number; width: number; height?: number };
	maximumWeight?: number;
	minimumLoadhandlingHeight?: number;
	maximumLoadhandlingHeight?: number;
	minimumLoadhandlingDepth?: number;
	maximumLoadhandlingDepth?: number;
	minimumLoadhandlingTilt?: number;
	maximumLoadhandlingTilt?: number;
	maximumSpeed?: number;
	maximumAcceleration?: number;
	maximumDeceleration?: number;
	pickTime?: number;
	dropTime?: number;
	description?: string;
}

/**
 * Mobile robot 구성 정보 (v3: 명칭 통일)
 */
export interface Vda5050MobileRobotConfiguration {
	versions?: Vda5050VersionInfo[];
	network?: Vda5050NetworkConfig;
	batteryCharging?: Vda5050BatteryChargingConfig;
}

export interface Vda5050VersionInfo {
	key: string;
	value: string;
}

export interface Vda5050NetworkConfig {
	dnsServers?: string[];
	ntpServers?: string[];
	localIpAddress?: string;
	netmask?: string;
	defaultGateway?: string;
}

export interface Vda5050BatteryChargingConfig {
	criticalLowChargingLevel?: number;
	minimumDesiredChargingLevel?: number;
	maximumDesiredChargingLevel?: number;
	minimumChargingTime?: number;
}
