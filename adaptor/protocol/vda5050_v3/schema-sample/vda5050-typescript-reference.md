# VDA5050 v3 TypeScript Reference

VDA5050 v3 표준 8개 토픽의 TypeScript 타입 정의 및 샘플 데이터 모음.
v2 대비 mobile robot 용어 통일, RELEASE/CORRIDOR 의미론, zone 시스템 도입.

## 파일 구조

| 파일 | 역할 |
|------|------|
| `index.ts` | 전체 타입/상수/샘플 통합 re-export |
| `vda5050-common.types.ts` | Header, MobileRobotPosition, Velocity, Trajectory, PlannedPath, IntermediatePath, Translation |
| `vda5050-order.types.ts` + `.sample.ts` | Order 메시지 (MC → mobile robot) |
| `vda5050-state.types.ts` + `.sample.ts` | State 메시지 (mobile robot → MC) |
| `vda5050-instant-actions.types.ts` + `.sample.ts` | Instant Actions (즉시 실행, blockingType=NONE 강제) |
| `vda5050-connection.types.ts` + `.sample.ts` | Connection (last will, ONLINE/OFFLINE/HIBERNATING/CONNECTION_BROKEN) |
| `vda5050-visualization.types.ts` + `.sample.ts` | Visualization (고빈도 위치/경로 시각화) |
| `vda5050-factsheet.types.ts` + `.sample.ts` | Factsheet (mobile robot 사양/능력) |
| `vda5050-responses.types.ts` + `.sample.ts` | **v3 신규** — fleet control 응답 (zone/edge request grant) |
| `vda5050-zone-set.types.ts` + `.sample.ts` | **v3 신규** — 맵 부속 zone 정의 (BLOCKED/SPEED_LIMIT/RELEASE/ACTION/PRIORITY/PENALTY/DIRECTED/BIDIRECTED) |

## v2 → v3 주요 변경점

### 용어 통일
- `AGV` → `mobile robot` (모든 필드/타입명에 반영)
- `Vda5050AgvPosition` → `Vda5050MobileRobotPosition`
- `agvKinematic` / `agvClass` → `mobileRobotKinematics` / `mobileRobotClass`
- `agvGeometry` → `mobileRobotGeometry`, `agvActions` → `mobileRobotActions`
- `agvConfig` → `mobileRobotConfiguration`

### 새로운 enum 값
- `Vda5050OperatingMode`: `STARTUP`, `INTERVENED` 추가, `TEACHIN` → `TEACH_IN`
- `Vda5050ActionStatus`: `RETRIABLE` 추가
- `Vda5050ErrorLevel`: 4단계 (`WARNING` / `URGENT` / `CRITICAL` / `FATAL`)
- `Vda5050EStopType`: `AUTOACK` 제거 → `MANUAL` / `REMOTE` / `NONE`
- `Vda5050ConnectionState`: `HIBERNATING` 추가, `CONNECTION_BROKEN` (밑줄 포함)
- `Vda5050CorridorReferencePoint`: `KINEMATIC_CENTER` (밑줄 포함)
- `Vda5050ActionScope`: `ZONE` 추가
- 액션 `blockingType`: `SINGLE` 추가

### 신규 메시지 타입
- **Responses** (`/responses` 토픽): zoneRequest/edgeRequest 응답 (`GRANTED`/`QUEUED`/`REVOKED`/`REJECTED`)
- **ZoneSet** (`/zoneSet` 토픽): 맵별 zone 집합 (10가지 zoneType)

### State 메시지 추가 필드
- `maps` / `zoneSets`: mobile robot에 저장된 맵/zone set 목록
- `plannedPath`: 현재 활성 경로 NURBS
- `intermediatePath`: 인접 waypoint ETA polyline
- `zoneRequests` / `edgeRequests`: 활성 요청 목록
- `zoneActionStates`: zone action 실행 상태
- `errorDescriptionTranslations` / `errorHintTranslations`: 다국어 번역
- `powerSupply.batteryCurrent`: 전류 측정치 추가

### Order/Edge 추가 필드
- `edge.maximumSpeed` / `maximumMobileRobotHeight` / `minimumLoadHandlingDeviceHeight`
- `edge.reachOrientationBeforeEntering`
- `edge.corridor.releaseRequired` / `releaseLossBehavior`
- `action.retriable`
- `orderDescription` (시각화용)
- v2의 `edge.startNodeId` / `endNodeId`는 v3 schema에 없음 (sequenceId로 관리)

### Visualization 메시지
- `referenceStateHeaderId` (필수): 어떤 state 메시지를 보강하는지 명시
- `plannedPath` / `intermediatePath` / `mobileRobotPosition` 사용

### Factsheet 변경
- `protocolFeatures.mobileRobotActions`: action 정의 컬렉션 (v2의 `agvActions`)
- `mobileRobotGeometry`: wheel/envelope 정의
- `mobileRobotConfiguration.batteryCharging`: 충전 정책 신규
- `typeSpecification.supportedZones`: 지원 zone 타입 명시

## 사용 예

```ts
import {
	VDA5050_ORDER_SAMPLES,
	VDA5050_STATE_SAMPLES,
	VDA5050_ZONE_SET_SAMPLES,
	VDA5050_RESPONSES_SAMPLES,
	type Vda5050Order,
	type Vda5050State,
	Vda5050BlockingType,
	Vda5050ConnectionState,
} from './schema-sample';

const order: Vda5050Order = VDA5050_ORDER_SAMPLES.basicTransport;
const state: Vda5050State = VDA5050_STATE_SAMPLES.driving;

// as const enum 패턴
const blocking: Vda5050BlockingType = Vda5050BlockingType.HARD;
```

## 코딩 규칙

- TypeScript `enum` 미사용. `as const` 객체 + `typeof[keyof]` 타입 추론 패턴 적용 (CLAUDE.md 규칙).
- 모든 exported 타입/상수에 JSDoc.
- 샘플 데이터는 `Vda5050_*_SAMPLES` 객체로 묶어 export.
- 필드명은 schema의 camelCase 그대로 유지 (`mobileRobotKinematics`, `corridorReferencePoint` 등).

## 참조

- 스키마 원본: `../json_schemas/`
- v2 샘플 비교: `../../vda5050_v2/schema-sample/`
- VDA5050 v3 사양 문서 (외부)
