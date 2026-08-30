/**
 * @fileoverview VDA5050 v3 Connection 메시지 샘플
 */

import type { Vda5050Connection } from './vda5050-connection.types';

export const SAMPLE_CONNECTION_ONLINE: Vda5050Connection = {
	headerId: 1,
	timestamp: '2026-02-22T09:00:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	connectionState: 'ONLINE',
};

export const SAMPLE_CONNECTION_OFFLINE: Vda5050Connection = {
	headerId: 99,
	timestamp: '2026-02-22T18:00:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	connectionState: 'OFFLINE',
};

/**
 * v3 신규: 절전 모드 (연결 유지하나 메시지 미발행)
 */
export const SAMPLE_CONNECTION_HIBERNATING: Vda5050Connection = {
	headerId: 100,
	timestamp: '2026-02-22T18:05:00.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-002',
	connectionState: 'HIBERNATING',
};

/**
 * Last will (비정상 단절 시 broker가 자동 발행)
 */
export const SAMPLE_CONNECTION_BROKEN: Vda5050Connection = {
	headerId: 0,
	timestamp: '2026-02-22T15:42:11.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-003',
	connectionState: 'CONNECTION_BROKEN',
};

export const VDA5050_CONNECTION_SAMPLES = {
	online: SAMPLE_CONNECTION_ONLINE,
	offline: SAMPLE_CONNECTION_OFFLINE,
	hibernating: SAMPLE_CONNECTION_HIBERNATING,
	broken: SAMPLE_CONNECTION_BROKEN,
} as const;
