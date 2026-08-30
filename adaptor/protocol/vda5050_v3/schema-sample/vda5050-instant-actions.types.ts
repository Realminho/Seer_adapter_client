/**
 * @fileoverview VDA5050 v3 InstantActions 메시지 TypeScript 타입 정의
 * @description Master Control → mobile robot 즉시 실행 액션
 *              v3에서 instantAction의 blockingType은 항상 NONE
 * @see instantActions.schema (vda5050_v3/json_schemas/instantActions.schema)
 */

import type { Vda5050ActionParameter } from './vda5050-order.types';

/**
 * InstantAction blockingType (v3: NONE 만 허용)
 */
export const Vda5050InstantBlockingType = {
	NONE: 'NONE',
} as const;
export type Vda5050InstantBlockingType =
	(typeof Vda5050InstantBlockingType)[keyof typeof Vda5050InstantBlockingType];

/**
 * VDA5050 v3 InstantActions 메시지
 */
export interface Vda5050InstantActions {
	headerId: number;
	timestamp: string;
	version: string;
	manufacturer: string;
	serialNumber: string;
	actions: Vda5050InstantAction[];
}

/**
 * Instant Action (blockingType은 항상 NONE)
 */
export interface Vda5050InstantAction {
	actionType: string;
	actionId: string;
	actionDescriptor?: string;
	blockingType: Vda5050InstantBlockingType;
	actionParameters?: Vda5050ActionParameter[];
}
