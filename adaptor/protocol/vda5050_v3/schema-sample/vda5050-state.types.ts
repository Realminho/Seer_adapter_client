/**
 * @fileoverview VDA5050 v3 State 메시지 TypeScript 타입 정의
 * @description Mobile robot → Master Control 방향 상태 메시지 (v3)
 * @see state.schema (vda5050_v3/json_schemas/state.schema)
 */

import type {
	Vda5050IntermediatePath,
	Vda5050MobileRobotPosition,
	Vda5050PlannedPath,
	Vda5050Trajectory,
	Vda5050Translation,
	Vda5050Velocity,
} from './vda5050-common.types';

// ============================================================
// Constants
// ============================================================

/**
 * Mobile robot 운행 모드 (v3)
 * v2 대비 STARTUP, INTERVENED 추가 / TEACH_IN (밑줄 포함)
 */
export const Vda5050OperatingMode = {
	STARTUP: 'STARTUP',
	AUTOMATIC: 'AUTOMATIC',
	SEMIAUTOMATIC: 'SEMIAUTOMATIC',
	INTERVENED: 'INTERVENED',
	MANUAL: 'MANUAL',
	SERVICE: 'SERVICE',
	TEACH_IN: 'TEACH_IN',
} as const;
export type Vda5050OperatingMode =
	(typeof Vda5050OperatingMode)[keyof typeof Vda5050OperatingMode];

/**
 * Action 실행 상태 (v3)
 * v2 대비 RETRIABLE 추가
 */
export const Vda5050ActionStatus = {
	WAITING: 'WAITING',
	INITIALIZING: 'INITIALIZING',
	RUNNING: 'RUNNING',
	PAUSED: 'PAUSED',
	RETRIABLE: 'RETRIABLE',
	FINISHED: 'FINISHED',
	FAILED: 'FAILED',
} as const;
export type Vda5050ActionStatus =
	(typeof Vda5050ActionStatus)[keyof typeof Vda5050ActionStatus];

/**
 * 에러 심각도 (v3 4단계)
 * - WARNING: 경고 (정상 운행 가능, 신규 주문 수락)
 * - URGENT: 즉시 주의 필요, 운행 가능
 * - CRITICAL: 즉시 주의 필요, 현재 주문 불가, 신규 주문 가능
 * - FATAL: 사용자 개입 필요, 운행 불가
 */
export const Vda5050ErrorLevel = {
	WARNING: 'WARNING',
	URGENT: 'URGENT',
	CRITICAL: 'CRITICAL',
	FATAL: 'FATAL',
} as const;
export type Vda5050ErrorLevel =
	(typeof Vda5050ErrorLevel)[keyof typeof Vda5050ErrorLevel];

export const Vda5050InfoLevel = {
	INFO: 'INFO',
	DEBUG: 'DEBUG',
} as const;
export type Vda5050InfoLevel =
	(typeof Vda5050InfoLevel)[keyof typeof Vda5050InfoLevel];

/**
 * 비상 정지 유형 (v3: AUTOACK 제거)
 */
export const Vda5050EStopType = {
	MANUAL: 'MANUAL',
	REMOTE: 'REMOTE',
	NONE: 'NONE',
} as const;
export type Vda5050EStopType =
	(typeof Vda5050EStopType)[keyof typeof Vda5050EStopType];

export const Vda5050MapStatus = {
	ENABLED: 'ENABLED',
	DISABLED: 'DISABLED',
} as const;
export type Vda5050MapStatus =
	(typeof Vda5050MapStatus)[keyof typeof Vda5050MapStatus];

/**
 * Zone Set 상태 (v3 신규)
 */
export const Vda5050ZoneSetStatus = {
	ENABLED: 'ENABLED',
	DISABLED: 'DISABLED',
} as const;
export type Vda5050ZoneSetStatus =
	(typeof Vda5050ZoneSetStatus)[keyof typeof Vda5050ZoneSetStatus];

/**
 * Request type (zoneRequest)
 */
export const Vda5050ZoneRequestType = {
	ACCESS: 'ACCESS',
	REPLANNING: 'REPLANNING',
} as const;
export type Vda5050ZoneRequestType =
	(typeof Vda5050ZoneRequestType)[keyof typeof Vda5050ZoneRequestType];

/**
 * Request type (edgeRequest)
 */
export const Vda5050EdgeRequestType = {
	CORRIDOR: 'CORRIDOR',
} as const;
export type Vda5050EdgeRequestType =
	(typeof Vda5050EdgeRequestType)[keyof typeof Vda5050EdgeRequestType];

/**
 * Request 상태 (zone/edge 공통)
 */
export const Vda5050RequestStatus = {
	REQUESTED: 'REQUESTED',
	GRANTED: 'GRANTED',
	REVOKED: 'REVOKED',
	EXPIRED: 'EXPIRED',
} as const;
export type Vda5050RequestStatus =
	(typeof Vda5050RequestStatus)[keyof typeof Vda5050RequestStatus];

// ============================================================
// Interfaces
// ============================================================

/**
 * VDA5050 v3 State 메시지
 */
export interface Vda5050State {
	headerId: number;
	timestamp: string;
	version: string;
	manufacturer: string;
	serialNumber: string;
	/** Mobile robot에 저장된 맵 목록 */
	maps?: Vda5050Map[];
	/** Mobile robot에 저장된 zone set 목록 (v3 신규) */
	zoneSets?: Vda5050ZoneSetState[];
	/** 현재/이전 주문 ID (없으면 빈 문자열) */
	orderId: string;
	/** 주문 업데이트 ID (없으면 0) */
	orderUpdateId: number;
	/** 마지막 도달/현재 노드 ID */
	lastNodeId: string;
	/** 마지막 도달/현재 노드 sequenceId */
	lastNodeSequenceId: number;
	/** 남은 Node 상태 배열 */
	nodeStates: Vda5050NodeState[];
	/** 남은 Edge 상태 배열 */
	edgeStates: Vda5050EdgeState[];
	/** 계획 경로 (v3 신규) */
	plannedPath?: Vda5050PlannedPath;
	/** 중간 경로 polyline (v3 신규) */
	intermediatePath?: Vda5050IntermediatePath;
	/** Mobile robot 현재 위치 */
	mobileRobotPosition?: Vda5050MobileRobotPosition;
	/** Mobile robot 속도 */
	velocity?: Vda5050Velocity;
	/** 적재물 목록 */
	loads?: Vda5050Load[];
	/** 주행/회전 여부 */
	driving: boolean;
	/** 일시 정지 여부 */
	paused?: boolean;
	/** 새 Base 요청 필요 여부 */
	newBaseRequest?: boolean;
	/** 활성 zone request 목록 (v3 신규) */
	zoneRequests?: Vda5050ZoneRequest[];
	/** 활성 edge request 목록 (v3 신규) */
	edgeRequests?: Vda5050EdgeRequest[];
	/** 마지막 노드 이후 거리 (m) */
	distanceSinceLastNode?: number;
	/** 일반 Action 실행 상태 */
	actionStates: Vda5050ActionState[];
	/** Instant Action 실행 상태 */
	instantActionStates: Vda5050ActionState[];
	/** Zone Action 실행 상태 (v3 신규) */
	zoneActionStates?: Vda5050ActionState[];
	/** 전원 공급 상태 */
	powerSupply: Vda5050PowerSupply;
	/** 운행 모드 */
	operatingMode: Vda5050OperatingMode;
	/** 활성 에러 목록 */
	errors: Vda5050Error[];
	/** 정보 메시지 목록 */
	information?: Vda5050Information[];
	/** 안전 상태 */
	safetyState: Vda5050SafetyState;
}

/**
 * 맵 정보
 */
export interface Vda5050Map {
	mapId: string;
	mapVersion: string;
	mapStatus: Vda5050MapStatus;
	mapDescriptor?: string;
}

/**
 * Zone set 상태 (v3 신규)
 */
export interface Vda5050ZoneSetState {
	zoneSetId: string;
	mapId: string;
	zoneSetStatus: Vda5050ZoneSetStatus;
}

/**
 * Node 상태
 */
export interface Vda5050NodeState {
	nodeId: string;
	sequenceId: number;
	nodeDescriptor?: string;
	released: boolean;
	nodePosition?: Vda5050StateNodePosition;
}

/**
 * Node 상태 위치 (state 전용 간소화 형식)
 */
export interface Vda5050StateNodePosition {
	x: number;
	y: number;
	theta?: number;
	mapId: string;
}

/**
 * Edge 상태
 */
export interface Vda5050EdgeState {
	edgeId: string;
	sequenceId: number;
	edgeDescriptor?: string;
	released: boolean;
	trajectory?: Vda5050Trajectory;
}

/**
 * Action 실행 상태
 */
export interface Vda5050ActionState {
	actionId: string;
	actionStatus: Vda5050ActionStatus;
	actionType?: string;
	actionDescriptor?: string;
	actionResult?: string;
}

/**
 * 전원 공급 상태 (v3: batteryCurrent 추가)
 */
export interface Vda5050PowerSupply {
	stateOfCharge: number;
	charging: boolean;
	batteryVoltage?: number;
	batteryCurrent?: number;
	batteryHealth?: number;
	range?: number;
}

/** @deprecated v3에서는 Vda5050PowerSupply 사용 */
export type Vda5050BatteryState = Vda5050PowerSupply;

/**
 * 에러 정보 (v3: translation 지원)
 */
export interface Vda5050Error {
	errorType: string;
	errorLevel: Vda5050ErrorLevel;
	errorReferences?: Vda5050ErrorReference[];
	errorDescription?: string;
	errorDescriptionTranslations?: Vda5050Translation[];
	errorHint?: string;
	errorHintTranslations?: Vda5050Translation[];
}

export interface Vda5050ErrorReference {
	referenceKey: string;
	referenceValue: string;
}

/**
 * 정보 메시지 (v3: infoDescriptor 명칭으로 변경)
 */
export interface Vda5050Information {
	infoType: string;
	infoLevel: Vda5050InfoLevel;
	infoReferences?: Vda5050InfoReference[];
	infoDescriptor?: string;
}

export interface Vda5050InfoReference {
	referenceKey: string;
	referenceValue: string;
}

/**
 * 안전 상태
 */
export interface Vda5050SafetyState {
	activeEmergencyStop: Vda5050EStopType;
	fieldViolation: boolean;
}

/**
 * 적재물 정보
 */
export interface Vda5050Load {
	loadId?: string;
	loadType?: string;
	loadPosition?: string;
	boundingBoxReference?: Vda5050BoundingBoxReference;
	loadDimensions?: Vda5050LoadDimensions;
	weight?: number;
}

export interface Vda5050BoundingBoxReference {
	x: number;
	y: number;
	z: number;
	theta?: number;
}

export interface Vda5050LoadDimensions {
	length: number;
	width: number;
	height?: number;
}

/**
 * Zone Request (v3 신규)
 * Mobile robot이 fleet control에 보내는 zone 요청
 */
export interface Vda5050ZoneRequest {
	requestId: string;
	requestType: Vda5050ZoneRequestType;
	zoneId: string;
	zoneSetId: string;
	requestStatus: Vda5050RequestStatus;
	trajectory?: Vda5050Trajectory;
}

/**
 * Edge Request (v3 신규)
 */
export interface Vda5050EdgeRequest {
	requestId: string;
	requestType: Vda5050EdgeRequestType;
	edgeId: string;
	sequenceId: number;
	requestStatus: Vda5050RequestStatus;
}
