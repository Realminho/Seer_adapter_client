/**
 * @fileoverview VDA5050 v3 Order 메시지 TypeScript 타입 정의
 * @description Master Control → mobile robot 방향 주문 메시지 모델 (v3)
 * @see order.schema (vda5050_v3/json_schemas/order.schema)
 */

import type { Vda5050Trajectory } from './vda5050-common.types';

// ============================================================
// Constants (as const 패턴)
// ============================================================

/**
 * Action 블로킹 유형
 * - NONE: 이동 중 및 다른 Action과 병렬 실행 가능
 * - SOFT: 다른 Action과 병렬 가능, 이동 중 실행 불가
 * - SINGLE: 이동 허용, 다른 Action 동시 실행 불가
 * - HARD: 단독 실행 (다른 Action, 이동 모두 불가)
 */
export const Vda5050BlockingType = {
	/** 이동 중 및 다른 Action과 병렬 실행 가능 */
	NONE: 'NONE',
	/** 다른 Action과 병렬 가능, 이동 중 실행 불가 */
	SOFT: 'SOFT',
	/** 이동 허용, 다른 Action 동시 실행 불가 */
	SINGLE: 'SINGLE',
	/** 단독 실행 (다른 Action, 이동 모두 불가) */
	HARD: 'HARD',
} as const;
export type Vda5050BlockingType =
	(typeof Vda5050BlockingType)[keyof typeof Vda5050BlockingType];

/**
 * Edge orientation 해석 방식
 * - GLOBAL: 프로젝트 맵 좌표계 기준 절대 방향
 * - TANGENTIAL: Edge 접선 방향 기준 (0 = 정방향, PI = 역방향, 기본값)
 */
export const Vda5050OrientationType = {
	GLOBAL: 'GLOBAL',
	TANGENTIAL: 'TANGENTIAL',
} as const;
export type Vda5050OrientationType =
	(typeof Vda5050OrientationType)[keyof typeof Vda5050OrientationType];

/**
 * Corridor 기준점 (v3: 밑줄 포함 enum)
 * - KINEMATIC_CENTER: 차량 운동학적 중심 기준
 * - CONTOUR: 차량 외곽선 기준
 */
export const Vda5050CorridorReferencePoint = {
	KINEMATIC_CENTER: 'KINEMATIC_CENTER',
	CONTOUR: 'CONTOUR',
} as const;
export type Vda5050CorridorReferencePoint =
	(typeof Vda5050CorridorReferencePoint)[keyof typeof Vda5050CorridorReferencePoint];

/**
 * Corridor 릴리즈 만료 시 동작
 * - STOP: 즉시 정지
 * - RETURN: 진입 지점으로 복귀
 */
export const Vda5050ReleaseLossBehavior = {
	STOP: 'STOP',
	RETURN: 'RETURN',
} as const;
export type Vda5050ReleaseLossBehavior =
	(typeof Vda5050ReleaseLossBehavior)[keyof typeof Vda5050ReleaseLossBehavior];

// ============================================================
// Interfaces
// ============================================================

/**
 * VDA5050 v3 Order 메시지
 * Master Control → mobile robot 주문 메시지
 */
export interface Vda5050Order {
	/** 메시지 헤더 ID */
	headerId: number;
	/** 타임스탬프 (ISO 8601 UTC) */
	timestamp: string;
	/** 프로토콜 버전 */
	version: string;
	/** Mobile robot 제조사 */
	manufacturer: string;
	/** Mobile robot 시리얼 번호 */
	serialNumber: string;
	/** 주문 ID */
	orderId: string;
	/** 주문 업데이트 ID (orderId 내 0부터) */
	orderUpdateId: number;
	/** 주문 설명 (시각화 전용) */
	orderDescription?: string;
	/** 주문 이행을 위해 순회할 Node 배열 (최소 1개) */
	nodes: Vda5050Node[];
	/** 주문 이행을 위해 순회할 Edge 배열 (Node 1개만일 경우 빈 배열) */
	edges: Vda5050Edge[];
}

/**
 * VDA5050 v3 Node
 */
export interface Vda5050Node {
	/** 노드 식별자 (동일 order 내 중복 가능) */
	nodeId: string;
	/** 순서 번호 (Node/Edge 통합 시퀀스) */
	sequenceId: number;
	/** 노드 추가 설명 */
	nodeDescriptor?: string;
	/** true: Base, false: Horizon */
	released: boolean;
	/** 맵 상의 위치 (라인 유도 차량 등 위치 불필요 시 생략) */
	nodePosition?: Vda5050NodePosition;
	/** 노드에서 실행할 Action 배열 */
	actions: Vda5050Action[];
}

/**
 * VDA5050 v3 Node 위치
 */
export interface Vda5050NodePosition {
	/** X 좌표 (m) */
	x: number;
	/** Y 좌표 (m) */
	y: number;
	/** 절대 방향 (rad, -PI ~ PI) */
	theta?: number;
	/** 허용 위치 편차 ellipse */
	allowedDeviationXY?: Vda5050AllowedDeviation;
	/** 허용 방향 편차 (rad, 0 ~ PI) */
	allowedDeviationTheta?: number;
	/** 맵 고유 식별자 */
	mapId: string;
}

/**
 * Node 위치 허용 편차 ellipse
 */
export interface Vda5050AllowedDeviation {
	/** semi-major axis (m) */
	a: number;
	/** semi-minor axis (m) */
	b: number;
	/** ellipse 회전각 (rad, -PI/2 ~ PI/2) */
	theta: number;
}

/**
 * VDA5050 v3 Edge
 * v3에서는 startNodeId/endNodeId 없이 sequenceId로 관리됨
 */
export interface Vda5050Edge {
	/** Edge 식별자 (중복 가능) */
	edgeId: string;
	/** 순서 번호 */
	sequenceId: number;
	/** Edge 추가 설명 */
	edgeDescriptor?: string;
	/** true: Base, false: Horizon */
	released: boolean;
	/** 최대 속도 (m/s) */
	maximumSpeed?: number;
	/** Mobile robot 최대 높이 (적재물 포함, m) */
	maximumMobileRobotHeight?: number;
	/** 적재 핸들링 장치 최소 높이 (m) */
	minimumLoadHandlingDeviceHeight?: number;
	/** Mobile robot 방향 (rad, -PI ~ PI) */
	orientation?: number;
	/** 방향 해석 방식 (기본 TANGENTIAL) */
	orientationType?: Vda5050OrientationType;
	/** 분기점 방향 (라인/와이어 유도용) */
	direction?: string;
	/** Edge 진입 전 desired orientation 도달 필요 여부 (기본 false) */
	reachOrientationBeforeEntering?: boolean;
	/** 최대 회전 속도 (rad/s) */
	maxRotationSpeed?: number;
	/** NURBS 궤적 */
	trajectory?: Vda5050Trajectory;
	/** 경로 길이 (m) */
	length?: number;
	/** Corridor (궤적 이탈 허용 범위) */
	corridor?: Vda5050Corridor;
	/** Edge 상에서 실행할 Action 배열 */
	actions: Vda5050Action[];
}

/**
 * VDA5050 v3 Corridor
 * v3에서 releaseRequired / releaseLossBehavior 추가
 */
export interface Vda5050Corridor {
	/** 궤적 좌측 허용 폭 (m) */
	leftWidth: number;
	/** 궤적 우측 허용 폭 (m) */
	rightWidth: number;
	/** 경계 기준점 */
	corridorReferencePoint?: Vda5050CorridorReferencePoint;
	/** Fleet control 승인 필요 여부 (미정의 시 불필요) */
	releaseRequired?: boolean;
	/** 릴리즈 만료/취소 시 동작 (기본 STOP) */
	releaseLossBehavior?: Vda5050ReleaseLossBehavior;
}

/**
 * VDA5050 v3 Action
 */
export interface Vda5050Action {
	/** Action 유형 */
	actionType: string;
	/** Action 고유 ID (UUID 권장) */
	actionId: string;
	/** Action 추가 설명 */
	actionDescriptor?: string;
	/** 블로킹 유형 */
	blockingType: Vda5050BlockingType;
	/** Action 파라미터 배열 */
	actionParameters?: Vda5050ActionParameter[];
	/** 실패 시 RETRIABLE 진입 가능 여부 (기본 false) */
	retriable?: boolean;
}

/**
 * VDA5050 Action Parameter
 */
export interface Vda5050ActionParameter {
	/** 파라미터 키 */
	key: string;
	/** 파라미터 값 */
	value: string | number | boolean | unknown[] | Record<string, unknown>;
}
