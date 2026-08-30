/**
 * @fileoverview VDA5050 v3 Factsheet 샘플 데이터
 */

import type { Vda5050Factsheet } from './vda5050-factsheet.types';

// ============================================================
// 샘플 1: Differential drive 운반 mobile robot
// ============================================================

export const SAMPLE_FACTSHEET_DIFFERENTIAL_CARRIER: Vda5050Factsheet = {
	headerId: 1,
	timestamp: '2026-02-22T09:00:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	typeSpecification: {
		seriesName: 'L2M-Carrier-500',
		seriesDescription: '500kg 운반 differential mobile robot',
		mobileRobotKinematics: 'DIFFERENTIAL',
		mobileRobotClass: 'CARRIER',
		maximumLoadMass: 500,
		localizationTypes: ['NATURAL', 'REFLECTOR'],
		navigationTypes: ['FREELY_NAVIGATING'],
		supportedZones: ['BLOCKED', 'SPEED_LIMIT', 'RELEASE', 'PRIORITY'],
	},
	physicalParameters: {
		minimumSpeed: 0.05,
		maximumSpeed: 2.0,
		minimumAngularSpeed: 0.05,
		maximumAngularSpeed: 1.5,
		maximumAcceleration: 0.5,
		maximumDeceleration: 1.0,
		minimumHeight: 0.3,
		maximumHeight: 0.5,
		width: 0.7,
		length: 1.2,
	},
	protocolLimits: {
		maximumStringLengths: {
			maximumMessageLength: 65536,
			maximumIdLength: 64,
			idNumericalOnly: false,
			maximumLoadIdLength: 32,
		},
		maximumArrayLengths: {
			'order.nodes': 100,
			'order.edges': 100,
			'node.actions': 10,
			'edge.actions': 10,
			'state.nodeStates': 100,
			'state.edgeStates': 100,
			'state.errors': 20,
		},
		timing: {
			minimumOrderInterval: 1.0,
			minimumStateInterval: 0.5,
			defaultStateInterval: 1.0,
			visualizationInterval: 0.1,
		},
	},
	protocolFeatures: {
		optionalParameters: [
			{
				parameter: 'order.nodes.nodePosition.allowedDeviationXY',
				support: 'SUPPORTED',
				description: '위치 허용 ellipse 지원',
			},
			{
				parameter: 'order.edges.corridor',
				support: 'SUPPORTED',
				description: 'Dynamic corridor 지원 (release 옵션 포함)',
			},
		],
		mobileRobotActions: [
			{
				actionType: 'pick',
				actionDescription: '화물 픽업',
				actionScopes: ['NODE'],
				actionParameters: [
					{ key: 'stationType', valueDataType: 'STRING', isOptional: false },
					{ key: 'loadType', valueDataType: 'STRING', isOptional: true },
				],
				blockingTypes: ['HARD'],
				pauseAllowed: 'true',
				cancelAllowed: 'false',
			},
			{
				actionType: 'drop',
				actionDescription: '화물 하역',
				actionScopes: ['NODE'],
				blockingTypes: ['HARD'],
				pauseAllowed: 'true',
				cancelAllowed: 'false',
			},
			{
				actionType: 'cancelOrder',
				actionDescription: '현재 주문 취소',
				actionScopes: ['INSTANT'],
				blockingTypes: ['NONE'],
				pauseAllowed: 'false',
				cancelAllowed: 'false',
			},
			{
				actionType: 'reduceSpeed',
				actionDescription: 'Zone 진입 시 속도 감속',
				actionScopes: ['ZONE'],
				actionParameters: [{ key: 'speed', valueDataType: 'NUMBER' }],
				blockingTypes: ['NONE'],
				pauseAllowed: 'false',
				cancelAllowed: 'true',
			},
		],
	},
	mobileRobotGeometry: {
		wheelDefinitions: [
			{
				type: 'DRIVE',
				isActiveDriven: true,
				isActiveSteered: false,
				position: { x: 0.0, y: 0.3 },
				diameter: 0.2,
				width: 0.05,
			},
			{
				type: 'DRIVE',
				isActiveDriven: true,
				isActiveSteered: false,
				position: { x: 0.0, y: -0.3 },
				diameter: 0.2,
				width: 0.05,
			},
			{
				type: 'CASTER',
				isActiveDriven: false,
				isActiveSteered: false,
				position: { x: -0.5, y: 0.0 },
				diameter: 0.1,
				width: 0.04,
				centerDisplacement: 0.02,
			},
		],
		envelopes2d: [
			{
				envelope2dId: 'main-body',
				vertices: [
					{ x: 0.6, y: 0.35 },
					{ x: 0.6, y: -0.35 },
					{ x: -0.6, y: -0.35 },
					{ x: -0.6, y: 0.35 },
				],
				description: '본체 외곽 polygon',
			},
		],
	},
	loadSpecification: {
		loadPositions: ['front'],
		loadSets: [
			{
				setName: 'DEFAULT',
				loadType: 'PALLET',
				loadPositions: ['front'],
				maximumWeight: 500,
				maximumSpeed: 1.5,
				pickTime: 5.0,
				dropTime: 5.0,
				description: '기본 팔레트 핸들링 셋',
			},
		],
	},
	mobileRobotConfiguration: {
		versions: [
			{ key: 'softwareVersion', value: 'v1.3.2' },
			{ key: 'plcSoftChecksum', value: '0x4297F30C' },
		],
		network: {
			dnsServers: ['10.0.0.1'],
			ntpServers: ['10.0.0.10'],
			localIpAddress: '10.0.10.21',
			netmask: '255.255.255.0',
			defaultGateway: '10.0.10.1',
		},
		batteryCharging: {
			criticalLowChargingLevel: 15,
			minimumDesiredChargingLevel: 30,
			maximumDesiredChargingLevel: 95,
			minimumChargingTime: 600,
		},
	},
};

// ============================================================
// 샘플 2: Forklift mobile robot
// ============================================================

export const SAMPLE_FACTSHEET_FORKLIFT: Vda5050Factsheet = {
	headerId: 1,
	timestamp: '2026-02-22T09:00:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-FL-001',
	typeSpecification: {
		seriesName: 'L2M-Forklift-1500',
		mobileRobotKinematics: 'THREE_WHEEL',
		mobileRobotClass: 'FORKLIFT',
		maximumLoadMass: 1500,
		localizationTypes: ['NATURAL'],
		navigationTypes: ['FREELY_NAVIGATING'],
	},
	physicalParameters: {
		minimumSpeed: 0.05,
		maximumSpeed: 1.5,
		maximumAcceleration: 0.3,
		maximumDeceleration: 0.8,
		minimumHeight: 1.8,
		maximumHeight: 4.5,
		width: 1.1,
		length: 2.4,
	},
	protocolLimits: {
		maximumStringLengths: { maximumIdLength: 64 },
		maximumArrayLengths: { 'order.nodes': 50, 'state.errors': 10 },
		timing: { minimumOrderInterval: 1.0, minimumStateInterval: 0.5 },
	},
	protocolFeatures: {
		optionalParameters: [],
		mobileRobotActions: [
			{
				actionType: 'pick',
				actionScopes: ['NODE'],
				actionParameters: [
					{ key: 'lhd', valueDataType: 'STRING' },
					{ key: 'height', valueDataType: 'NUMBER' },
				],
				blockingTypes: ['HARD'],
				pauseAllowed: 'true',
				cancelAllowed: 'false',
			},
		],
	},
	mobileRobotGeometry: {},
	loadSpecification: {
		loadPositions: ['fork'],
		loadSets: [
			{
				setName: 'EPAL',
				loadType: 'EPAL',
				maximumWeight: 1500,
				minimumLoadhandlingHeight: 0.05,
				maximumLoadhandlingHeight: 4.0,
				pickTime: 12.0,
				dropTime: 8.0,
			},
		],
	},
};

export const VDA5050_FACTSHEET_SAMPLES = {
	differentialCarrier: SAMPLE_FACTSHEET_DIFFERENTIAL_CARRIER,
	forklift: SAMPLE_FACTSHEET_FORKLIFT,
} as const;
