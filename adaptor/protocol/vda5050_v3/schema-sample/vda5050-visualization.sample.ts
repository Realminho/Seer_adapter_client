/**
 * @fileoverview VDA5050 v3 Visualization 샘플 데이터
 */

import type { Vda5050Visualization } from './vda5050-visualization.types';

// ============================================================
// 샘플 1: 위치 + 속도 (가장 일반적)
// ============================================================

export const SAMPLE_VISUALIZATION_POSITION: Vda5050Visualization = {
	headerId: 1000,
	timestamp: '2026-02-22T10:05:30.100Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	referenceStateHeaderId: 100,
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
};

// ============================================================
// 샘플 2: 계획 경로 + 중간 경로 ETA polyline
// ============================================================

export const SAMPLE_VISUALIZATION_PLANNED_PATH: Vda5050Visualization = {
	headerId: 1010,
	timestamp: '2026-02-22T10:05:31.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	referenceStateHeaderId: 100,
	mobileRobotPosition: {
		x: 10.0,
		y: 4.0,
		theta: 1.5708,
		mapId: 'floor-1',
		localized: true,
		localizationScore: 0.97,
	},
	velocity: { vx: 0.0, vy: 1.0, omega: 0.0 },
	plannedPath: {
		trajectory: {
			degree: 1,
			knotVector: [0, 0, 1, 1],
			controlPoints: [
				{ x: 10.0, y: 4.0 },
				{ x: 10.0, y: 15.0 },
			],
		},
		traversedNodes: ['storage-cell-2-1-3'],
	},
	intermediatePath: {
		polyline: [
			{ x: 10.0, y: 6.0, theta: 1.5708, eta: '2026-02-22T10:05:35.000Z' },
			{ x: 10.0, y: 10.0, theta: 1.5708, eta: '2026-02-22T10:05:42.000Z' },
			{ x: 10.0, y: 15.0, theta: 1.5708, eta: '2026-02-22T10:05:50.000Z' },
		],
	},
};

// ============================================================
// 샘플 3: 정지 (속도 0) — 최소 필드만
// ============================================================

export const SAMPLE_VISUALIZATION_IDLE: Vda5050Visualization = {
	headerId: 1500,
	timestamp: '2026-02-22T18:30:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-003',
	referenceStateHeaderId: 500,
	mobileRobotPosition: {
		x: -5.0,
		y: 0.0,
		theta: 3.14159,
		mapId: 'floor-1',
		localized: true,
		localizationScore: 0.99,
	},
	velocity: { vx: 0.0, vy: 0.0, omega: 0.0 },
};

export const VDA5050_VISUALIZATION_SAMPLES = {
	position: SAMPLE_VISUALIZATION_POSITION,
	plannedPath: SAMPLE_VISUALIZATION_PLANNED_PATH,
	idle: SAMPLE_VISUALIZATION_IDLE,
} as const;
