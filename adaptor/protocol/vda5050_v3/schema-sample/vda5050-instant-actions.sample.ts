/**
 * @fileoverview VDA5050 v3 InstantActions 샘플 데이터
 */

import type { Vda5050InstantActions } from './vda5050-instant-actions.types';

// ============================================================
// 샘플 1: stateRequest (즉시 state 메시지 요청)
// ============================================================

export const SAMPLE_INSTANT_STATE_REQUEST: Vda5050InstantActions = {
	headerId: 50,
	timestamp: '2026-02-22T10:30:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	actions: [
		{
			actionType: 'stateRequest',
			actionId: 'iact-001',
			actionDescriptor: '즉시 state 메시지 요청',
			blockingType: 'NONE',
		},
	],
};

// ============================================================
// 샘플 2: cancelOrder + startPause + factsheetRequest 묶음
// ============================================================

export const SAMPLE_INSTANT_CANCEL_PAUSE: Vda5050InstantActions = {
	headerId: 51,
	timestamp: '2026-02-22T10:31:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-002',
	actions: [
		{
			actionType: 'cancelOrder',
			actionId: 'iact-010',
			actionDescriptor: '현재 주문 취소',
			blockingType: 'NONE',
		},
		{
			actionType: 'startPause',
			actionId: 'iact-011',
			actionDescriptor: '일시 정지',
			blockingType: 'NONE',
		},
		{
			actionType: 'factsheetRequest',
			actionId: 'iact-012',
			blockingType: 'NONE',
		},
	],
};

// ============================================================
// 샘플 3: initPosition (긴급 위치 보정)
// ============================================================

export const SAMPLE_INSTANT_INIT_POSITION: Vda5050InstantActions = {
	headerId: 52,
	timestamp: '2026-02-22T10:32:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-003',
	actions: [
		{
			actionType: 'initPosition',
			actionId: 'iact-020',
			actionDescriptor: '위치 강제 보정',
			blockingType: 'NONE',
			actionParameters: [
				{ key: 'x', value: 10.5 },
				{ key: 'y', value: 4.2 },
				{ key: 'theta', value: 1.5708 },
				{ key: 'mapId', value: 'floor-1' },
				{ key: 'lastNodeId', value: 'waypoint-A1' },
			],
		},
	],
};

// ============================================================
// 샘플 4: clearInstantActions / clearZoneActions (v3)
// ============================================================

export const SAMPLE_INSTANT_CLEAR: Vda5050InstantActions = {
	headerId: 53,
	timestamp: '2026-02-22T10:33:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	actions: [
		{
			actionType: 'clearInstantActions',
			actionId: 'iact-030',
			actionDescriptor: 'Instant action 상태 초기화',
			blockingType: 'NONE',
		},
		{
			actionType: 'clearZoneActions',
			actionId: 'iact-031',
			actionDescriptor: 'Zone action 상태 초기화 (v3 신규)',
			blockingType: 'NONE',
		},
	],
};

export const VDA5050_INSTANT_ACTIONS_SAMPLES = {
	stateRequest: SAMPLE_INSTANT_STATE_REQUEST,
	cancelPause: SAMPLE_INSTANT_CANCEL_PAUSE,
	initPosition: SAMPLE_INSTANT_INIT_POSITION,
	clear: SAMPLE_INSTANT_CLEAR,
} as const;
