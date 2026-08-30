/**
 * @fileoverview VDA5050 v3 Connection 메시지 TypeScript 타입 정의
 * @description Mobile robot의 last will 및 연결 상태 메시지
 *              v3에서 HIBERNATING 추가, CONNECTION_BROKEN (밑줄 포함)
 * @see connection.schema (vda5050_v3/json_schemas/connection.schema)
 */

/**
 * 연결 상태 (v3)
 * - ONLINE: 활성 연결
 * - OFFLINE: 정상 종료된 비활성
 * - HIBERNATING: 연결은 유지하나 메시지 미발행 (절전, v3 신규)
 * - CONNECTION_BROKEN: 비정상 단절 (last will)
 */
export const Vda5050ConnectionState = {
	ONLINE: 'ONLINE',
	OFFLINE: 'OFFLINE',
	HIBERNATING: 'HIBERNATING',
	CONNECTION_BROKEN: 'CONNECTION_BROKEN',
} as const;
export type Vda5050ConnectionState =
	(typeof Vda5050ConnectionState)[keyof typeof Vda5050ConnectionState];

/**
 * VDA5050 v3 Connection 메시지
 */
export interface Vda5050Connection {
	headerId: number;
	timestamp: string;
	version: string;
	manufacturer: string;
	serialNumber: string;
	connectionState: Vda5050ConnectionState;
}
