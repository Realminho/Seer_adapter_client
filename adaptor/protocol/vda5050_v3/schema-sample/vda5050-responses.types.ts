/**
 * @fileoverview VDA5050 v3 Responses 메시지 TypeScript 타입 정의
 * @description Fleet control → mobile robot 응답 (v3 신규)
 *              State의 zoneRequests / edgeRequests에 대한 grant/revoke/reject
 * @see responses.schema (vda5050_v3/json_schemas/responses.schema)
 */

/**
 * Grant type
 * - GRANTED: 요청 승인
 * - QUEUED: 큐에 추가, 아직 권한 미부여
 * - REVOKED: 기존 승인 취소
 * - REJECTED: 거절
 */
export const Vda5050GrantType = {
	GRANTED: 'GRANTED',
	QUEUED: 'QUEUED',
	REVOKED: 'REVOKED',
	REJECTED: 'REJECTED',
} as const;
export type Vda5050GrantType =
	(typeof Vda5050GrantType)[keyof typeof Vda5050GrantType];

/**
 * VDA5050 v3 Responses 메시지
 */
export interface Vda5050Responses {
	headerId: number;
	timestamp: string;
	version: string;
	manufacturer: string;
	serialNumber: string;
	responses: Vda5050ResponseEntry[];
}

/**
 * 응답 엔트리
 */
export interface Vda5050ResponseEntry {
	/** 요청 ID (state의 zoneRequest/edgeRequest의 requestId와 매칭) */
	requestId: string;
	/** 부여 결과 */
	grantType: Vda5050GrantType;
	/** 임대 만료 시각 (ISO 8601 UTC) */
	leaseExpiry?: string;
}
