/**
 * @fileoverview VDA5050 v3 ZoneSet 메시지 TypeScript 타입 정의
 * @description 맵에 부속된 zone 집합 (v3 신규)
 * @see zoneSet.schema (vda5050_v3/json_schemas/zoneSet.schema)
 */

import type { Vda5050ActionParameter } from './vda5050-order.types';

// ============================================================
// Constants
// ============================================================

/**
 * Zone 카테고리
 */
export const Vda5050ZoneType = {
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
export type Vda5050ZoneType =
	(typeof Vda5050ZoneType)[keyof typeof Vda5050ZoneType];

/**
 * RELEASE zone 만료 시 동작
 */
export const Vda5050ZoneReleaseLossBehavior = {
	STOP: 'STOP',
	CONTINUE: 'CONTINUE',
	EVACUATE: 'EVACUATE',
} as const;
export type Vda5050ZoneReleaseLossBehavior =
	(typeof Vda5050ZoneReleaseLossBehavior)[keyof typeof Vda5050ZoneReleaseLossBehavior];

/**
 * DIRECTED zone 제한 강도
 */
export const Vda5050DirectedLimitation = {
	SOFT: 'SOFT',
	RESTRICTED: 'RESTRICTED',
	STRICT: 'STRICT',
} as const;
export type Vda5050DirectedLimitation =
	(typeof Vda5050DirectedLimitation)[keyof typeof Vda5050DirectedLimitation];

/**
 * BIDIRECTED zone 제한 강도
 */
export const Vda5050BidirectedLimitation = {
	SOFT: 'SOFT',
	RESTRICTED: 'RESTRICTED',
} as const;
export type Vda5050BidirectedLimitation =
	(typeof Vda5050BidirectedLimitation)[keyof typeof Vda5050BidirectedLimitation];

/**
 * Zone Action blocking type
 */
export const Vda5050ZoneActionBlockingType = {
	NONE: 'NONE',
	SOFT: 'SOFT',
	SINGLE: 'SINGLE',
	HARD: 'HARD',
} as const;
export type Vda5050ZoneActionBlockingType =
	(typeof Vda5050ZoneActionBlockingType)[keyof typeof Vda5050ZoneActionBlockingType];

// ============================================================
// Interfaces
// ============================================================

/**
 * VDA5050 v3 ZoneSet 메시지
 */
export interface Vda5050ZoneSetMessage {
	headerId: number;
	timestamp: string;
	version: string;
	manufacturer: string;
	serialNumber: string;
	zoneSet: Vda5050ZoneSet;
}

/**
 * Zone set
 */
export interface Vda5050ZoneSet {
	mapId: string;
	zoneSetId: string;
	zoneSetDescriptor?: string;
	zones: Vda5050Zone[];
}

/**
 * Zone (kind에 따라 부가 필드 존재)
 * 추가 필드는 zoneType 별로 if/then 조건부
 */
export interface Vda5050Zone {
	zoneId: string;
	zoneType: Vda5050ZoneType;
	zoneDescriptor?: string;
	vertices: Vda5050Vertex[];
	// — RELEASE
	releaseLossBehavior?: Vda5050ZoneReleaseLossBehavior;
	// — SPEED_LIMIT
	maximumSpeed?: number;
	// — ACTION
	entryActions?: Vda5050ZoneAction[];
	duringActions?: Vda5050ZoneAction[];
	exitActions?: Vda5050ZoneAction[];
	// — PRIORITY
	priorityFactor?: number;
	// — PENALTY
	penaltyFactor?: number;
	// — DIRECTED
	direction?: number;
	directedLimitation?: Vda5050DirectedLimitation;
	// — BIDIRECTED
	bidirectedLimitation?: Vda5050BidirectedLimitation;
}

export interface Vda5050Vertex {
	x: number;
	y: number;
}

export interface Vda5050ZoneAction {
	actionType: string;
	actionDescriptor?: string;
	blockingType: Vda5050ZoneActionBlockingType;
	actionParameters?: Vda5050ActionParameter[];
	retriable?: boolean;
}
