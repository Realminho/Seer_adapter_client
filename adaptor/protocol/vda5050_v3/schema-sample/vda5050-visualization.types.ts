/**
 * @fileoverview VDA5050 v3 Visualization 메시지 TypeScript 타입 정의
 * @description Mobile robot → MC 시각화 전용 (고빈도 위치/속도)
 *              v3: referenceStateHeaderId, plannedPath, intermediatePath, mobileRobotPosition 포함
 * @see visualization.schema (vda5050_v3/json_schemas/visualization.schema)
 */

import type {
	Vda5050IntermediatePath,
	Vda5050MobileRobotPosition,
	Vda5050PlannedPath,
	Vda5050Velocity,
} from './vda5050-common.types';

/**
 * VDA5050 v3 Visualization 메시지
 */
export interface Vda5050Visualization {
	headerId: number;
	timestamp: string;
	version: string;
	manufacturer: string;
	serialNumber: string;
	/** 참조하는 state 메시지의 headerId (v3 신규) */
	referenceStateHeaderId: number;
	/** 계획 경로 (v3 신규) */
	plannedPath?: Vda5050PlannedPath;
	/** 중간 경로 polyline (v3 신규) */
	intermediatePath?: Vda5050IntermediatePath;
	/** Mobile robot 현재 위치 */
	mobileRobotPosition?: Vda5050MobileRobotPosition;
	/** Mobile robot 속도 */
	velocity?: Vda5050Velocity;
}
