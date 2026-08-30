/**
 * @fileoverview VDA5050 v3 공통 타입 정의
 * @description State, Visualization 등 여러 스키마에서 공유하는 mobile robot 공통 타입
 * @see VDA5050 v3 표준 (mobile robot 용어 통일)
 */

// ============================================================
// Common Interfaces
// ============================================================

/**
 * VDA5050 메시지 헤더
 * 모든 VDA5050 토픽 메시지의 공통 헤더 필드
 */
export interface Vda5050Header {
	/** 메시지 헤더 ID (토픽별 순차 증가) */
	headerId: number;
	/** 타임스탬프 (ISO 8601: YYYY-MM-DDTHH:mm:ss.fffZ) */
	timestamp: string;
	/** 프로토콜 버전 [Major].[Minor].[Patch] */
	version: string;
	/** Mobile robot 제조사 */
	manufacturer: string;
	/** Mobile robot 시리얼 번호 */
	serialNumber: string;
}

/**
 * VDA5050 v3 Mobile Robot 위치
 * 맵 좌표계 기반 mobile robot 현재 위치 정보 (State, Visualization 공통)
 */
export interface Vda5050MobileRobotPosition {
	/** X 좌표 (맵 좌표계 기준, m) */
	x: number;
	/** Y 좌표 (맵 좌표계 기준, m) */
	y: number;
	/** 절대 방향 (rad, -PI ~ PI) */
	theta: number;
	/** 맵 고유 식별자 */
	mapId: string;
	/** 위치 localization 여부 */
	localized: boolean;
	/**
	 * 위치 추정 품질 점수 (SLAM 기반 차량용)
	 * 0.0: 위치 불명, 1.0: 위치 확정
	 */
	localizationScore?: number;
	/** 위치 편차 범위 (m) */
	deviationRange?: number;
}

/**
 * VDA5050 Mobile Robot 속도
 * 차량 좌표계 기준 현재 속도 정보
 */
export interface Vda5050Velocity {
	/** X축 방향 속도 (m/s) */
	vx?: number;
	/** Y축 방향 속도 (m/s) */
	vy?: number;
	/** Z축 회전 속도 (rad/s) */
	omega?: number;
}

/**
 * NURBS 제어점
 * (Order trajectory, State trajectory, Visualization plannedPath 공통)
 */
export interface Vda5050ControlPoint {
	/** X 좌표 (월드 좌표계, m) */
	x: number;
	/** Y 좌표 (월드 좌표계, m) */
	y: number;
	/** 가중치 (곡선 당김 강도, 기본값: 1.0) */
	weight?: number;
}

/**
 * NURBS 궤적
 * Order edge trajectory / State edgeState trajectory / Visualization plannedPath 공통
 */
export interface Vda5050Trajectory {
	/** NURBS 차수 (기본값: 1) */
	degree?: number;
	/** 매듭 벡터 (크기 = 제어점 수 + degree + 1, 각 값 0~1) */
	knotVector?: number[];
	/** 제어점 배열 (시작점/끝점 포함) */
	controlPoints: Vda5050ControlPoint[];
}

/**
 * Polyline 끝점 (intermediatePath segment)
 */
export interface Vda5050PolylinePoint {
	/** X 좌표 (m) */
	x: number;
	/** Y 좌표 (m) */
	y: number;
	/** 절대 방향 (rad, -PI ~ PI) */
	theta?: number;
	/** 도착 예상 시각 (ISO 8601 UTC) */
	eta: string;
}

/**
 * 계획 경로 (plannedPath)
 * 현재 활성 주문 내 일부 경로의 NURBS 표현
 */
export interface Vda5050PlannedPath {
	/** NURBS 궤적 */
	trajectory: Vda5050Trajectory;
	/** 계획 경로에 포함된 nodeId 배열 */
	traversedNodes?: string[];
}

/**
 * 중간 경로 (intermediatePath)
 * 센서로 감지 가능한 인접 waypoint들의 ETA polyline
 */
export interface Vda5050IntermediatePath {
	/** Polyline segment 끝점 배열 */
	polyline: Vda5050PolylinePoint[];
}

/**
 * 다국어 번역 (errorDescription / errorHint 등)
 */
export interface Vda5050Translation {
	/** ISO 639-1 언어 코드 */
	translationKey: string;
	/** 해당 언어 번역문 */
	translationValue: string;
}
