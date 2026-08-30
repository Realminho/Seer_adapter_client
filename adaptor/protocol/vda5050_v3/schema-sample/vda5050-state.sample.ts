/**
 * @fileoverview VDA5050 v3 State 메시지 샘플 데이터
 */

import type { Vda5050State } from './vda5050-state.types';

// ============================================================
// 샘플 1: 정상 주행 중 + 계획 경로
// ============================================================

export const SAMPLE_STATE_DRIVING: Vda5050State = {
	headerId: 100,
	timestamp: '2026-02-22T10:05:30.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	maps: [
		{
			mapId: 'floor-1',
			mapVersion: '1.2.0',
			mapStatus: 'ENABLED',
			mapDescriptor: '1층 물류 구역',
		},
	],
	zoneSets: [
		{ zoneSetId: 'zs-floor1-default', mapId: 'floor-1', zoneSetStatus: 'ENABLED' },
	],
	orderId: 'ORD-2026-0001',
	orderUpdateId: 0,
	lastNodeId: 'waypoint-A1',
	lastNodeSequenceId: 2,
	nodeStates: [
		{
			nodeId: 'storage-cell-2-1-3',
			sequenceId: 4,
			nodeDescriptor: '적재 위치 2-1-3',
			released: true,
			nodePosition: { x: 10.0, y: 15.0, theta: 1.5708, mapId: 'floor-1' },
		},
	],
	edgeStates: [
		{
			edgeId: 'edge-waypoint-to-storage',
			sequenceId: 3,
			edgeDescriptor: '경유지 A1 → 적재 위치',
			released: true,
		},
	],
	plannedPath: {
		trajectory: {
			degree: 1,
			knotVector: [0, 0, 1, 1],
			controlPoints: [
				{ x: 10.0, y: 3.5 },
				{ x: 10.0, y: 15.0 },
			],
		},
		traversedNodes: ['storage-cell-2-1-3'],
	},
	intermediatePath: {
		polyline: [
			{ x: 10.0, y: 5.0, theta: 1.5708, eta: '2026-02-22T10:05:35.000Z' },
			{ x: 10.0, y: 10.0, theta: 1.5708, eta: '2026-02-22T10:05:42.000Z' },
			{ x: 10.0, y: 15.0, theta: 1.5708, eta: '2026-02-22T10:05:50.000Z' },
		],
	},
	mobileRobotPosition: {
		x: 10.0,
		y: 3.5,
		theta: 1.5708,
		mapId: 'floor-1',
		localized: true,
		localizationScore: 0.97,
		deviationRange: 0.015,
	},
	velocity: { vx: 0.0, vy: 1.0, omega: 0.0 },
	loads: [
		{
			loadId: 'PLT-2026-0042',
			loadType: 'PALLET',
			loadPosition: 'front',
			boundingBoxReference: { x: 0.0, y: 0.0, z: 0.5 },
			loadDimensions: { length: 1.2, width: 0.8, height: 0.15 },
			weight: 250.0,
		},
	],
	driving: true,
	paused: false,
	newBaseRequest: false,
	distanceSinceLastNode: 3.5,
	actionStates: [
		{
			actionId: 'act-001',
			actionStatus: 'FINISHED',
			actionType: 'pick',
			actionDescriptor: '캐리어 픽업',
			actionResult: 'PLT-2026-0042 픽업 완료',
		},
		{
			actionId: 'act-002',
			actionStatus: 'WAITING',
			actionType: 'drop',
			actionDescriptor: '캐리어 적재',
		},
	],
	instantActionStates: [],
	zoneActionStates: [],
	powerSupply: {
		stateOfCharge: 65.0,
		charging: false,
		batteryVoltage: 48.2,
		batteryCurrent: -8.4,
		batteryHealth: 92,
		range: 1200.0,
	},
	operatingMode: 'AUTOMATIC',
	errors: [],
	information: [
		{
			infoType: 'estimatedArrival',
			infoLevel: 'INFO',
			infoDescriptor: '목적지 도착 예상: 약 12초',
		},
	],
	safetyState: { activeEmergencyStop: 'NONE', fieldViolation: false },
};

// ============================================================
// 샘플 2: CRITICAL 에러 + RETRIABLE Action + 다국어
// ============================================================

export const SAMPLE_STATE_ERROR: Vda5050State = {
	headerId: 250,
	timestamp: '2026-02-22T14:23:45.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-002',
	orderId: 'ORD-2026-0010',
	orderUpdateId: 0,
	lastNodeId: 'junction-B2',
	lastNodeSequenceId: 2,
	nodeStates: [
		{ nodeId: 'outbound-port-03', sequenceId: 4, released: true },
	],
	edgeStates: [
		{ edgeId: 'edge-junction-to-outbound', sequenceId: 3, released: true },
	],
	mobileRobotPosition: {
		x: 8.2,
		y: -1.5,
		theta: -1.2,
		mapId: 'floor-1',
		localized: true,
		localizationScore: 0.4,
		deviationRange: 0.15,
	},
	driving: false,
	paused: false,
	actionStates: [
		{
			actionId: 'act-010',
			actionStatus: 'RETRIABLE',
			actionType: 'drop',
			actionDescriptor: '캐리어 출고 하차 (재시도 가능)',
		},
	],
	instantActionStates: [],
	powerSupply: {
		stateOfCharge: 42.0,
		charging: false,
		batteryVoltage: 46.8,
		batteryHealth: 88,
	},
	operatingMode: 'AUTOMATIC',
	errors: [
		{
			errorType: 'laserScannerContaminated',
			errorLevel: 'CRITICAL',
			errorReferences: [
				{ referenceKey: 'sensor', referenceValue: 'frontLidar' },
				{ referenceKey: 'orderId', referenceValue: 'ORD-2026-0010' },
			],
			errorDescription: '전방 레이저 스캐너 오염으로 위치 추정 불가',
			errorDescriptionTranslations: [
				{ translationKey: 'en', translationValue: 'Front laser scanner contaminated, localization impossible' },
			],
			errorHint: '레이저 스캐너 렌즈를 청소한 후 재시작',
			errorHintTranslations: [
				{ translationKey: 'en', translationValue: 'Clean laser scanner lens and restart' },
			],
		},
		{
			errorType: 'positionLost',
			errorLevel: 'WARNING',
			errorDescription: 'localizationScore 0.4 미만 — 위치 신뢰도 저하',
		},
	],
	safetyState: { activeEmergencyStop: 'NONE', fieldViolation: false },
};

// ============================================================
// 샘플 3: 충전 중 Idle + STARTUP/INTERVENED 미사용 (AUTOMATIC)
// ============================================================

export const SAMPLE_STATE_CHARGING_IDLE: Vda5050State = {
	headerId: 500,
	timestamp: '2026-02-22T18:30:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-003',
	maps: [
		{ mapId: 'floor-1', mapVersion: '1.2.0', mapStatus: 'ENABLED' },
		{
			mapId: 'floor-2',
			mapVersion: '1.0.1',
			mapStatus: 'DISABLED',
			mapDescriptor: '2층 맵 (비활성)',
		},
	],
	orderId: '',
	orderUpdateId: 0,
	lastNodeId: 'charging-station-01',
	lastNodeSequenceId: 0,
	nodeStates: [],
	edgeStates: [],
	mobileRobotPosition: {
		x: -5.0,
		y: 0.0,
		theta: 3.14159,
		mapId: 'floor-1',
		localized: true,
		localizationScore: 0.99,
	},
	velocity: { vx: 0.0, vy: 0.0, omega: 0.0 },
	loads: [],
	driving: false,
	actionStates: [],
	instantActionStates: [],
	powerSupply: {
		stateOfCharge: 35.0,
		charging: true,
		batteryVoltage: 50.1,
		batteryCurrent: 12.5,
		batteryHealth: 95,
		range: 650.0,
	},
	operatingMode: 'AUTOMATIC',
	errors: [],
	information: [
		{
			infoType: 'chargingProgress',
			infoLevel: 'INFO',
			infoDescriptor: '충전 중 — 완충 예상 시간: 약 45분',
			infoReferences: [{ referenceKey: 'targetCharge', referenceValue: '95' }],
		},
	],
	safetyState: { activeEmergencyStop: 'NONE', fieldViolation: false },
};

// ============================================================
// 샘플 4: Zone/Edge Request 활성 (v3 신규)
// ============================================================

export const SAMPLE_STATE_WITH_REQUESTS: Vda5050State = {
	headerId: 700,
	timestamp: '2026-02-22T11:00:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	orderId: 'ORD-2026-0020',
	orderUpdateId: 0,
	lastNodeId: 'intersection-01',
	lastNodeSequenceId: 4,
	nodeStates: [{ nodeId: 'rack-zone-A', sequenceId: 6, released: true }],
	edgeStates: [{ edgeId: 'edge-intersection-to-rack', sequenceId: 5, released: true }],
	mobileRobotPosition: {
		x: 20.0,
		y: 5.0,
		theta: 0.0,
		mapId: 'floor-1',
		localized: true,
		localizationScore: 0.95,
	},
	driving: false,
	paused: false,
	zoneRequests: [
		{
			requestId: 'zr-001',
			requestType: 'ACCESS',
			zoneId: 'zone-rack-A',
			zoneSetId: 'zs-floor1-default',
			requestStatus: 'REQUESTED',
		},
	],
	edgeRequests: [
		{
			requestId: 'er-001',
			requestType: 'CORRIDOR',
			edgeId: 'edge-intersection-to-rack',
			sequenceId: 5,
			requestStatus: 'GRANTED',
		},
	],
	actionStates: [],
	instantActionStates: [],
	zoneActionStates: [
		{
			actionId: 'zact-001',
			actionStatus: 'RUNNING',
			actionType: 'reduceSpeed',
			actionDescriptor: 'Zone 진입 시 속도 감속',
		},
	],
	powerSupply: { stateOfCharge: 78.0, charging: false },
	operatingMode: 'AUTOMATIC',
	errors: [],
	safetyState: { activeEmergencyStop: 'NONE', fieldViolation: false },
};

export const VDA5050_STATE_SAMPLES = {
	driving: SAMPLE_STATE_DRIVING,
	error: SAMPLE_STATE_ERROR,
	chargingIdle: SAMPLE_STATE_CHARGING_IDLE,
	withRequests: SAMPLE_STATE_WITH_REQUESTS,
} as const;
