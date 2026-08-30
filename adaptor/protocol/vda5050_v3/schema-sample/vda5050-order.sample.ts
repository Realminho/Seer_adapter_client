/**
 * @fileoverview VDA5050 v3 Order 메시지 샘플 데이터
 * @description 창고/공장 mobile robot 운용 시나리오별 샘플
 */

import type { Vda5050Order } from './vda5050-order.types';

// ============================================================
// 샘플 1: 기본 이동 주문 (3-Node A → B → C)
// ============================================================

export const SAMPLE_BASIC_TRANSPORT_ORDER: Vda5050Order = {
	headerId: 1,
	timestamp: '2026-02-22T10:00:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	orderId: 'ORD-2026-0001',
	orderUpdateId: 0,
	orderDescription: '입고 → 적재 기본 운반',
	nodes: [
		{
			nodeId: 'inbound-port-01',
			sequenceId: 0,
			nodeDescriptor: '입고 포트 1',
			released: true,
			nodePosition: { x: 0.0, y: 0.0, theta: 0.0, mapId: 'floor-1' },
			actions: [
				{
					actionType: 'pick',
					actionId: 'act-001',
					actionDescriptor: '캐리어 픽업',
					blockingType: 'HARD',
					retriable: true,
					actionParameters: [
						{ key: 'stationType', value: 'floor' },
						{ key: 'loadType', value: 'PALLET' },
					],
				},
			],
		},
		{
			nodeId: 'waypoint-A1',
			sequenceId: 2,
			nodeDescriptor: '통로 A-1 경유지',
			released: true,
			nodePosition: { x: 10.0, y: 0.0, mapId: 'floor-1' },
			actions: [],
		},
		{
			nodeId: 'storage-cell-2-1-3',
			sequenceId: 4,
			nodeDescriptor: '적재 위치 2-1-3',
			released: true,
			nodePosition: {
				x: 10.0,
				y: 15.0,
				theta: 1.5708,
				allowedDeviationXY: { a: 0.5, b: 0.5, theta: 0 },
				allowedDeviationTheta: 0.1,
				mapId: 'floor-1',
			},
			actions: [
				{
					actionType: 'drop',
					actionId: 'act-002',
					actionDescriptor: '캐리어 적재',
					blockingType: 'HARD',
					actionParameters: [
						{ key: 'stationType', value: 'rack' },
						{ key: 'height', value: 1.2 },
					],
				},
			],
		},
	],
	edges: [
		{
			edgeId: 'edge-inbound-to-waypoint',
			sequenceId: 1,
			edgeDescriptor: '입고 포트 → 경유지 A1',
			released: true,
			maximumSpeed: 1.5,
			length: 10.0,
			actions: [],
		},
		{
			edgeId: 'edge-waypoint-to-storage',
			sequenceId: 3,
			edgeDescriptor: '경유지 A1 → 적재 위치',
			released: true,
			maximumSpeed: 1.0,
			maxRotationSpeed: 0.5,
			length: 15.0,
			actions: [],
		},
	],
};

// ============================================================
// 샘플 2: Base + Horizon 분리 주문
// ============================================================

export const SAMPLE_BASE_HORIZON_ORDER: Vda5050Order = {
	headerId: 5,
	timestamp: '2026-02-22T10:05:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-002',
	orderId: 'ORD-2026-0002',
	orderUpdateId: 0,
	nodes: [
		{
			nodeId: 'charging-station-01',
			sequenceId: 0,
			nodeDescriptor: '충전 스테이션 1',
			released: true,
			nodePosition: { x: -5.0, y: 0.0, theta: 0.0, mapId: 'floor-1' },
			actions: [],
		},
		{
			nodeId: 'junction-B2',
			sequenceId: 2,
			nodeDescriptor: '분기점 B-2',
			released: true,
			nodePosition: { x: 5.0, y: 0.0, mapId: 'floor-1' },
			actions: [],
		},
		{
			nodeId: 'outbound-port-03',
			sequenceId: 4,
			nodeDescriptor: '출고 포트 3 (Horizon)',
			released: false,
			nodePosition: { x: 15.0, y: -5.0, theta: -1.5708, mapId: 'floor-1' },
			actions: [
				{
					actionType: 'drop',
					actionId: 'act-010',
					actionDescriptor: '캐리어 출고 하차',
					blockingType: 'HARD',
				},
			],
		},
	],
	edges: [
		{
			edgeId: 'edge-charge-to-junction',
			sequenceId: 1,
			released: true,
			maximumSpeed: 2.0,
			length: 10.0,
			actions: [],
		},
		{
			edgeId: 'edge-junction-to-outbound',
			sequenceId: 3,
			released: false,
			maximumSpeed: 1.0,
			orientation: -1.5708,
			orientationType: 'TANGENTIAL',
			length: 11.18,
			actions: [],
		},
	],
};

// ============================================================
// 샘플 3: NURBS 궤적 + Corridor (v3 release 옵션)
// ============================================================

export const SAMPLE_TRAJECTORY_ORDER: Vda5050Order = {
	headerId: 12,
	timestamp: '2026-02-22T10:10:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-003',
	orderId: 'ORD-2026-0003',
	orderUpdateId: 0,
	nodes: [
		{
			nodeId: 'corridor-start',
			sequenceId: 0,
			released: true,
			nodePosition: { x: 0.0, y: 0.0, theta: 0.0, mapId: 'floor-2' },
			actions: [],
		},
		{
			nodeId: 'corridor-end',
			sequenceId: 2,
			released: true,
			nodePosition: {
				x: 10.0,
				y: 10.0,
				theta: 1.5708,
				allowedDeviationXY: { a: 0.3, b: 0.3, theta: 0 },
				mapId: 'floor-2',
			},
			actions: [
				{
					actionType: 'honk',
					actionId: 'act-020',
					actionDescriptor: '코너 도달 경고음',
					blockingType: 'NONE',
					actionParameters: [
						{ key: 'duration', value: 1.0 },
						{ key: 'signal', value: 'short' },
					],
				},
			],
		},
	],
	edges: [
		{
			edgeId: 'edge-curved-corridor',
			sequenceId: 1,
			edgeDescriptor: '곡선 코너 (2차 NURBS) + 동적 corridor',
			released: true,
			maximumSpeed: 0.8,
			reachOrientationBeforeEntering: false,
			trajectory: {
				degree: 2,
				knotVector: [0, 0, 0, 1, 1, 1],
				controlPoints: [
					{ x: 0.0, y: 0.0, weight: 1.0 },
					{ x: 10.0, y: 0.0, weight: 0.707 },
					{ x: 10.0, y: 10.0, weight: 1.0 },
				],
			},
			corridor: {
				leftWidth: 0.5,
				rightWidth: 0.5,
				corridorReferencePoint: 'KINEMATIC_CENTER',
				releaseRequired: true,
				releaseLossBehavior: 'STOP',
			},
			actions: [],
		},
	],
};

// ============================================================
// 샘플 4: Edge Action (이동 중 작업)
// ============================================================

export const SAMPLE_EDGE_ACTION_ORDER: Vda5050Order = {
	headerId: 20,
	timestamp: '2026-02-22T10:15:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	orderId: 'ORD-2026-0004',
	orderUpdateId: 0,
	nodes: [
		{
			nodeId: 'inspection-start',
			sequenceId: 0,
			released: true,
			nodePosition: { x: 0.0, y: 20.0, mapId: 'floor-1' },
			actions: [
				{
					actionType: 'initPosition',
					actionId: 'act-030',
					actionDescriptor: '위치 초기화',
					blockingType: 'HARD',
					actionParameters: [
						{ key: 'x', value: 0.0 },
						{ key: 'y', value: 20.0 },
						{ key: 'theta', value: 0.0 },
						{ key: 'mapId', value: 'floor-1' },
						{ key: 'lastNodeId', value: 'inspection-start' },
					],
				},
			],
		},
		{
			nodeId: 'inspection-end',
			sequenceId: 2,
			released: true,
			nodePosition: { x: 30.0, y: 20.0, mapId: 'floor-1' },
			actions: [
				{
					actionType: 'waitForTrigger',
					actionId: 'act-033',
					actionDescriptor: '외부 트리거 대기',
					blockingType: 'SOFT',
					actionParameters: [
						{ key: 'triggerType', value: 'cloud' },
						{ key: 'timeout', value: 30000 },
					],
				},
			],
		},
	],
	edges: [
		{
			edgeId: 'edge-inspection-corridor',
			sequenceId: 1,
			edgeDescriptor: '검수 통로 (이동 중 스캔)',
			released: true,
			maximumSpeed: 0.5,
			length: 30.0,
			actions: [
				{
					actionType: 'detectObject',
					actionId: 'act-031',
					actionDescriptor: '바코드 연속 스캔',
					blockingType: 'NONE',
					retriable: true,
					actionParameters: [
						{ key: 'detectionRange', value: 2.0 },
						{ key: 'sensorType', value: 'barcode' },
					],
				},
				{
					actionType: 'logReport',
					actionId: 'act-032',
					actionDescriptor: '환경 센서 측정',
					blockingType: 'NONE',
					actionParameters: [
						{ key: 'reportType', value: 'environment' },
						{ key: 'sensors', value: ['temperature', 'humidity', 'dust'] },
					],
				},
			],
		},
	],
};

// ============================================================
// 샘플 5: 주문 업데이트 (Horizon 확정)
// ============================================================

export const SAMPLE_ORDER_UPDATE: Vda5050Order = {
	headerId: 8,
	timestamp: '2026-02-22T10:06:30.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-002',
	orderId: 'ORD-2026-0002',
	orderUpdateId: 1,
	nodes: [
		{
			nodeId: 'junction-B2',
			sequenceId: 2,
			nodeDescriptor: '분기점 B-2 (업데이트 기준점)',
			released: true,
			nodePosition: { x: 5.0, y: 0.0, mapId: 'floor-1' },
			actions: [],
		},
		{
			nodeId: 'buffer-station-02',
			sequenceId: 4,
			nodeDescriptor: '버퍼 스테이션 2',
			released: true,
			nodePosition: { x: 12.0, y: 3.0, theta: 0.0, mapId: 'floor-1' },
			actions: [],
		},
		{
			nodeId: 'outbound-port-05',
			sequenceId: 6,
			nodeDescriptor: '출고 포트 5',
			released: true,
			nodePosition: { x: 20.0, y: 0.0, theta: 0.0, mapId: 'floor-1' },
			actions: [
				{
					actionType: 'drop',
					actionId: 'act-011',
					blockingType: 'HARD',
					actionParameters: [{ key: 'stationType', value: 'conveyor' }],
				},
			],
		},
	],
	edges: [
		{
			edgeId: 'edge-junction-to-buffer',
			sequenceId: 3,
			released: true,
			maximumSpeed: 1.5,
			length: 7.62,
			actions: [],
		},
		{
			edgeId: 'edge-buffer-to-outbound',
			sequenceId: 5,
			released: true,
			maximumSpeed: 2.0,
			length: 8.54,
			actions: [],
		},
	],
};

// ============================================================
// 샘플 6: 단일 Node 주문 (제자리 충전)
// ============================================================

export const SAMPLE_SINGLE_NODE_ORDER: Vda5050Order = {
	headerId: 30,
	timestamp: '2026-02-22T10:20:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-003',
	orderId: 'ORD-2026-0005',
	orderUpdateId: 0,
	nodes: [
		{
			nodeId: 'charging-station-01',
			sequenceId: 0,
			released: true,
			nodePosition: { x: -5.0, y: 0.0, theta: 3.14159, mapId: 'floor-1' },
			actions: [
				{
					actionType: 'startCharging',
					actionId: 'act-040',
					blockingType: 'HARD',
				},
				{
					actionType: 'waitForTrigger',
					actionId: 'act-041',
					blockingType: 'HARD',
					actionParameters: [
						{ key: 'triggerType', value: 'battery' },
						{ key: 'threshold', value: 80 },
					],
				},
				{
					actionType: 'stopCharging',
					actionId: 'act-042',
					blockingType: 'HARD',
				},
			],
		},
	],
	edges: [],
};

export const VDA5050_ORDER_SAMPLES = {
	basicTransport: SAMPLE_BASIC_TRANSPORT_ORDER,
	baseHorizon: SAMPLE_BASE_HORIZON_ORDER,
	trajectory: SAMPLE_TRAJECTORY_ORDER,
	edgeAction: SAMPLE_EDGE_ACTION_ORDER,
	orderUpdate: SAMPLE_ORDER_UPDATE,
	singleNode: SAMPLE_SINGLE_NODE_ORDER,
} as const;
