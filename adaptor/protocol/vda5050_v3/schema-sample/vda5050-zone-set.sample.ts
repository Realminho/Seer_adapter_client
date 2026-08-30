/**
 * @fileoverview VDA5050 v3 ZoneSet 샘플 데이터
 */

import type { Vda5050ZoneSetMessage } from './vda5050-zone-set.types';

// ============================================================
// 샘플 1: 다양한 zoneType 혼합 (BLOCKED/SPEED_LIMIT/RELEASE/ACTION)
// ============================================================

export const SAMPLE_ZONE_SET_MIXED: Vda5050ZoneSetMessage = {
	headerId: 1,
	timestamp: '2026-02-22T08:00:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	zoneSet: {
		mapId: 'floor-1',
		zoneSetId: 'zs-floor1-default',
		zoneSetDescriptor: '1층 기본 zone set',
		zones: [
			{
				zoneId: 'z-blocked-maintenance',
				zoneType: 'BLOCKED',
				zoneDescriptor: '유지보수 영역 (출입 금지)',
				vertices: [
					{ x: 25.0, y: 0.0 },
					{ x: 30.0, y: 0.0 },
					{ x: 30.0, y: 5.0 },
					{ x: 25.0, y: 5.0 },
				],
			},
			{
				zoneId: 'z-speed-limit-aisle',
				zoneType: 'SPEED_LIMIT',
				zoneDescriptor: '협소 통로 감속 구역',
				vertices: [
					{ x: 5.0, y: 10.0 },
					{ x: 15.0, y: 10.0 },
					{ x: 15.0, y: 12.0 },
					{ x: 5.0, y: 12.0 },
				],
				maximumSpeed: 0.5,
			},
			{
				zoneId: 'z-release-intersection',
				zoneType: 'RELEASE',
				zoneDescriptor: '교차로 (release 필요)',
				vertices: [
					{ x: 9.0, y: 9.0 },
					{ x: 11.0, y: 9.0 },
					{ x: 11.0, y: 11.0 },
					{ x: 9.0, y: 11.0 },
				],
				releaseLossBehavior: 'STOP',
			},
			{
				zoneId: 'z-action-rack-zone-A',
				zoneType: 'ACTION',
				zoneDescriptor: '랙 진입/이탈 시 자동 액션',
				vertices: [
					{ x: 18.0, y: 4.0 },
					{ x: 22.0, y: 4.0 },
					{ x: 22.0, y: 8.0 },
					{ x: 18.0, y: 8.0 },
				],
				entryActions: [
					{
						actionType: 'reduceSpeed',
						actionDescriptor: '랙 진입 시 감속',
						blockingType: 'NONE',
						actionParameters: [{ key: 'speed', value: 0.3 }],
					},
				],
				duringActions: [
					{
						actionType: 'enableScanner',
						blockingType: 'NONE',
					},
				],
				exitActions: [
					{
						actionType: 'restoreSpeed',
						blockingType: 'NONE',
					},
				],
			},
		],
	},
};

// ============================================================
// 샘플 2: PRIORITY/PENALTY (경로 가중치)
// ============================================================

export const SAMPLE_ZONE_SET_WEIGHTED: Vda5050ZoneSetMessage = {
	headerId: 2,
	timestamp: '2026-02-22T08:00:01.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-002',
	zoneSet: {
		mapId: 'floor-1',
		zoneSetId: 'zs-floor1-routing',
		zones: [
			{
				zoneId: 'z-priority-main-aisle',
				zoneType: 'PRIORITY',
				zoneDescriptor: '주요 통로 (선호 경로)',
				vertices: [
					{ x: 0.0, y: 0.0 },
					{ x: 30.0, y: 0.0 },
					{ x: 30.0, y: 2.0 },
					{ x: 0.0, y: 2.0 },
				],
				priorityFactor: 0.8,
			},
			{
				zoneId: 'z-penalty-personnel-area',
				zoneType: 'PENALTY',
				zoneDescriptor: '작업자 통행 구역 (가능하면 회피)',
				vertices: [
					{ x: 0.0, y: 18.0 },
					{ x: 30.0, y: 18.0 },
					{ x: 30.0, y: 22.0 },
					{ x: 0.0, y: 22.0 },
				],
				penaltyFactor: 0.7,
			},
		],
	},
};

// ============================================================
// 샘플 3: DIRECTED / BIDIRECTED (단/양방향 통행)
// ============================================================

export const SAMPLE_ZONE_SET_DIRECTED: Vda5050ZoneSetMessage = {
	headerId: 3,
	timestamp: '2026-02-22T08:00:02.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-003',
	zoneSet: {
		mapId: 'floor-1',
		zoneSetId: 'zs-floor1-direction',
		zones: [
			{
				zoneId: 'z-directed-corridor-east',
				zoneType: 'DIRECTED',
				zoneDescriptor: '동쪽 일방통행 통로',
				vertices: [
					{ x: 0.0, y: 14.0 },
					{ x: 30.0, y: 14.0 },
					{ x: 30.0, y: 16.0 },
					{ x: 0.0, y: 16.0 },
				],
				direction: 0.0,
				directedLimitation: 'STRICT',
			},
			{
				zoneId: 'z-bidirected-aisle',
				zoneType: 'BIDIRECTED',
				zoneDescriptor: '양방향 통로 (X축)',
				vertices: [
					{ x: 0.0, y: 24.0 },
					{ x: 30.0, y: 24.0 },
					{ x: 30.0, y: 26.0 },
					{ x: 0.0, y: 26.0 },
				],
				direction: 0.0,
				bidirectedLimitation: 'SOFT',
			},
		],
	},
};

export const VDA5050_ZONE_SET_SAMPLES = {
	mixed: SAMPLE_ZONE_SET_MIXED,
	weighted: SAMPLE_ZONE_SET_WEIGHTED,
	directed: SAMPLE_ZONE_SET_DIRECTED,
} as const;
