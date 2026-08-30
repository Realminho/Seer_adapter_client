/**
 * @fileoverview VDA5050 v3 Responses 샘플 데이터
 */

import type { Vda5050Responses } from './vda5050-responses.types';

// ============================================================
// 샘플 1: Zone access 승인 (60초 임대)
// ============================================================

export const SAMPLE_RESPONSE_GRANTED: Vda5050Responses = {
	headerId: 1,
	timestamp: '2026-02-22T11:00:01.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	responses: [
		{
			requestId: 'zr-001',
			grantType: 'GRANTED',
			leaseExpiry: '2026-02-22T11:01:01.000Z',
		},
	],
};

// ============================================================
// 샘플 2: 큐에 보류 + 다른 요청 즉시 거절
// ============================================================

export const SAMPLE_RESPONSE_QUEUED_REJECTED: Vda5050Responses = {
	headerId: 2,
	timestamp: '2026-02-22T11:00:02.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-002',
	responses: [
		{
			requestId: 'zr-002',
			grantType: 'QUEUED',
		},
		{
			requestId: 'er-002',
			grantType: 'REJECTED',
		},
	],
};

// ============================================================
// 샘플 3: 기존 승인 취소
// ============================================================

export const SAMPLE_RESPONSE_REVOKED: Vda5050Responses = {
	headerId: 3,
	timestamp: '2026-02-22T11:00:30.000Z',
	version: '3.0.0',
	manufacturer: 'Lab2Market',
	serialNumber: 'MR-001',
	responses: [
		{
			requestId: 'zr-001',
			grantType: 'REVOKED',
		},
	],
};

export const VDA5050_RESPONSES_SAMPLES = {
	granted: SAMPLE_RESPONSE_GRANTED,
	queuedRejected: SAMPLE_RESPONSE_QUEUED_REJECTED,
	revoked: SAMPLE_RESPONSE_REVOKED,
} as const;
