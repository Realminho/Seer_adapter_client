# JIBOT motor-off 정지 사유 세분화 + eq 전달 + 모터 enable/disable

- 날짜: 2026-06-25
- 상태: 설계 승인됨 (구현 대기)
- 범위: `unified-amr-adaptor` (어댑터) + `fabris-equipments` (AMR v3 eq)

## 1. 배경 / 문제

JIBOT 어댑터는 **실제 비상정지 신호를 직접 읽지 않는다.** `safetyState.eStop`은 오직 7273
`UmGetMotorState`의 `flag`(0/1, 사유 없음) 한 비트로 파생된다:

- `client.py`: `_motor_flag = response.get("flag")`
- `adapter_jibot.py:556`: `e_stop = EStop.AUTOACK if _motor_flag == 0 else EStop.NONE`
- `_derive_amr_working_state` (`adapter_jibot.py:1031,1049`): `estopped → detail="EMERGENCY", workingState="ERROR"`
- `_build_v3_emergency_stop:2153`: `AUTOACK → activeEmergencyStop=MANUAL`

따라서 **모터가 비활성인 모든 사유**(수동 미활성/물리 e-stop/범퍼/SW정지/모터고장)가 전부
하나의 `EMERGENCY`로 뭉뚱그려진다.

라이브 확인(2026-06-25, 로봇 호스트 `rostopic echo /jrobot_status`):
`system_status="Press ON to Enable."`, `motor_enable=0`, `hmi_estop=0`, `pc_estop=0`,
`motor_error=0`, `charge=0` → 즉 **물리 비상정지가 아니라 "모터 미활성(수동/ON 대기)" 상태**인데도
화면상 emergency로 표시됨.

세분화에 필요한 신호는 이미 ROS `/jrobot_status`(`jarvis_msgs/RobotStatus`)에 전부 있고,
어댑터의 `bms_ros_listener.py`가 **이미 이 토픽을 구독**하지만 BMS 전압/전류 컬럼만 쓰고
안전 필드는 버린다.

## 2. 목표

1. 어댑터가 `/jrobot_status` 안전 필드로 정지 사유를 **stopReason 토큰**으로 분류한다.
2. 그 분류로 기존 `safetyState`/`operatingMode`/`errors` 파생을 **실제로 수정**하여,
   수동/미활성 정지가 더 이상 EMERGENCY로 표시되지 않게 한다.
3. 원시 안전 필드 + stopReason 토큰을 새 MQTT `JIBOT_SAFETY` information 블록으로 전달한다.
4. eq(fabris-equipments)에서 이 값들을 `@EquipmentStatus`로 선언/표시하고,
   **기존 MANUAL/EMERGENCY 상태를 재사용**한다(새 상태 머신 룰 없음).
5. 모터 `enableMotor`(복구) 동작을 검증하고 `disableMotor`(재수동화/테스트) instant action을 추가한다.

### Non-goals

- eq에 새로운 AmrState enum 값을 추가하지 않는다 (기존 MANUAL/EMERGENCY/BLOCKED/ERROR 재사용).
- 7273 프로토콜(`UmGetMotorState`)을 바꾸지 않는다 (세분화는 `/jrobot_status`로만).
- `bumpe_stop` 폴라리티를 이번에 확정하지 않는다 (텍스트 기준 분류로 회피, §7 참고).

## 3. stopReason 토큰 (크로스레포 계약)

분류 1차 기준은 **`system_status` 텍스트**(폴라리티에 안전), 플래그는 원시 데이터 + 보조 판정.

**어댑터가 세팅하는 VDA5050 신호** → eq는 기존 매핑으로 상태를 파생한다. eq의 `AmrState`(장비 상태)와
`AmrWorkingState`(작업 상태)는 **다른 enum**임에 주의(아래 §3.1).

| stopReason | 트리거 (system_status / flag) | 어댑터가 세팅하는 신호 |
|---|---|---|
| `NONE` | `Normal...`, motor_enable=1 | (정상) e_stop=NONE, fieldViolation=false, op_mode=AUTOMATIC |
| `MANUAL` | `Press ON to Enable.` (motor_enable=0, estop 없음) | `operatingMode=MANUAL`, e_stop=NONE |
| `EMERGENCY` | `Estop Pressed!` / `hmi_estop=1` | `safetyState.eStop=MANUAL` |
| `PROTECTIVE_STOP` | `Enter Estop!` / `pc_estop=1` | `safetyState.eStop=REMOTE` |
| `BUMPER` | `bumper Trigger!` | `safetyState.fieldViolation=true` **+ driving=false** |
| `MOTOR_FAULT` | `motor_error=1` | FATAL `ErrorType.JIBOT_MOTOR_FAULT` 추가 |

우선순위(높은 것 우선): `EMERGENCY` > `PROTECTIVE_STOP` > `MOTOR_FAULT` > `BUMPER` > `MANUAL` > `NONE`.

토큰 문자열은 양쪽 레포에서 **정확히 일치**해야 한다(불일치 시 eq는 기존처럼 로컬 파생으로 silent fallback).
권장: §8처럼 동일 토큰 표를 양쪽 테스트 fixture에 고정한다.

### 3.1 eq 상태 매핑 (참고 — 신규 룰 없이 기존 경로 재사용)

eq `AmrState`(장비 상태)에는 **BLOCKED가 없다**: `STARTUP/MANUAL/READY/ERROR/EMERGENCY/BATTERY_LOW/NOT_CONNECTED`.
`BLOCKED`는 `AmrWorkingState`(`IDLE/DRIVING/ACTING/CHARGING/PAUSED/BLOCKED/ERROR`)에만 있다. 따라서:

| 어댑터 신호 | eq AmrState | eq workingState |
|---|---|---|
| `operatingMode=MANUAL` | `MANUAL` | (변동 없음) |
| `eStop ∈ {MANUAL,REMOTE}` | `EMERGENCY` | `ERROR` (detail=EMERGENCY) |
| `fieldViolation=true & !driving` | (비-EMERGENCY; 구현 시 eq 확인) | `BLOCKED` |
| FATAL error | `ERROR` | `ERROR` (detail=FAULT) |

## 4. 어댑터 설계 (unified-amr-adaptor)

### 4.1 `/jrobot_status` 안전 필드 수집 — `bms_ros_listener.py`

- `find_bms_columns` → **`find_robot_status_columns`로 일반화**(또는 wrapper 유지)해 다음 컬럼도 suffix 매칭:
  `system_status, motor_enable, motor_enable_status, hmi_estop, bumpe_stop, pc_estop, motor_error, pc_enable, charge`.
  BMS 2개 컬럼은 기존 의미 유지.
- **파싱은 `split(",")` 대신 Python `csv` 모듈로 전환**: 안전 필드가 이제 계약이므로, `system_status`에
  콤마/따옴표가 들어와도 안전하게 처리. (`rostopic echo -p`는 콤마 분리 CSV 행을 출력; `csv.reader`로
  한 행 파싱 후 컬럼 인덱싱.) 문자열은 따옴표/공백 strip.
- `_consume` 루프: BMS는 `vehicle.set_bms(...)` 유지, 추가로 `vehicle.set_robot_safety({...})` 호출.
- `vehicle`(jibot_client.JIBOT)에 `set_robot_safety(dict)` / `clear_robot_safety()` + freshness 타임스탬프
  (`_robot_safety`, `_robot_safety_last_update`) 추가. BMS의 `set_bms`/`clear_bms` 패턴을 그대로 따른다.
  타임아웃(`stale_after_sec`)·EOF 시 `clear_bms`와 함께 `clear_robot_safety`도 호출.
- 헤더 재출현 감지는 기존 `"bms_voltage" in line` 그대로 사용(같은 헤더에 모든 컬럼 존재).

### 4.2 stopReason 파생 — `adapter_jibot.py`

- `_jibot_safety_fresh() -> bool`: `_robot_safety_last_update`가 `stale_after_sec` 이내인가.
- `_derive_jibot_stop_reason() -> str`: §3 표/우선순위대로 토큰 반환. fresh하지 않으면 `None` 반환(= 데이터 없음).
- 분류는 `system_status` 텍스트 정규화(소문자/부분일치) 우선, 그다음 플래그.

### 4.3 기존 파생 수정 (실제 수정)

상태 루프(현재 `adapter_jibot.py:555~565` 부근)에서:

- `safety_state.e_stop`:
  - reason==`EMERGENCY` → `EStop.MANUAL`
  - reason==`PROTECTIVE_STOP` → `EStop.REMOTE`
  - reason in (`MANUAL`,`BUMPER`,`MOTOR_FAULT`,`NONE`) → `EStop.NONE`
  - **freshness fallback**: stop_reason가 `None`(데이터 stale/없음)이면 기존 동작:
    `EStop.AUTOACK if motor_flag==0 else EStop.NONE` (보수적, dev/sim 무영향).
- `field_violation`: reason==`BUMPER`이면 `True`로 OR (기존 obstacle-wait 로직과 합집합).
  **추가로 reason==`BUMPER`이면 `driving=False`로 강제**: eq/어댑터 BLOCKED은 `fieldViolation && !driving`
  조건이고, `_derive_driving()`는 `bumper Trigger!` 텍스트를 obstacle-wait(`#brake`)으로 보지 않아 자동
  보장이 안 된다. 상태 루프에서 bumper일 때 `self.state.driving = False`로 덮는다(기존 link-unhealthy
  강제 false와 같은 위치/방식).
- `_derive_operating_mode()`: reason==`MANUAL`이면 `OperatingMode.MANUAL` 반환
  (startup 체크는 우선). **이 매핑은 한 곳(헬퍼)에 격리**해 나중에 끄기 쉽게 한다.
- errors: reason==`MOTOR_FAULT`이면 FATAL state error set/clear. **전용 `ErrorType.JIBOT_MOTOR_FAULT`를
  `vda5050_2_0_0_state.py` enum에 추가**(기존 `JIBOT_*` 컨벤션; UNKNOWN_ERROR 재사용 금지 — 다른 오류와 충돌).
  전용 refresh 메서드(예: `_refresh_jibot_motor_fault_errors`)에서 매 사이클 set/clear하고, 다른
  `_refresh_*_errors`처럼 자기 error_type만 필터링해 교체한다.
- `_derive_amr_working_state`는 위 신호(e_stop/field_violation/errors/operating_mode)에서 자동 반영되므로
  추가 분기 불필요. (MANUAL은 ERROR가 아니라 operatingMode로 표현 → detail은 NONE.)

### 4.4 MQTT `JIBOT_SAFETY` information 블록 (순수 추가)

`JIBOT_STATUS`/`AMR_STATE`처럼 새 `info_type="JIBOT_SAFETY"` 블록을 매 publish마다 refresh.
infoReferences:

- `stopReason` (파생 토큰)
- `systemStatus` (원시 텍스트)
- `motorEnable`, `hmiEstop`, `bumperEstop`, `pcEstop`, `motorError`, `pcEnable`, `charge` (원시 0/1)

**stale/없음 정책 (명시):** 블록은 **항상 발행**한다. 안전데이터가 stale/없음이면 `stopReason=""` 및 모든
원시 필드도 `""`(빈 문자열)로 발행한다. 블록을 생략하면 eq morphism이 `undefined`를 받아 `@EquipmentStatus`
이전 값을 **유지(stale)**할 위험이 있으므로, 빈 문자열로 명시적으로 덮어 이전 값을 지운다.

`_refresh_jibot_status_errors`/`_refresh_amr_state_information`와 같은 위치/방식으로 `_refresh_jibot_safety_information()` 추가.

### 4.5 모터 enable/disable instant action

- `enableMotor` (기존): `_handle_enable_motor_instant_action` → `vehicle.enable_motor()` → `um_set_motor(True)`.
  체인 검증만(코드 변경 없음). `manual_control.enabled` 게이트 유지.
- `disableMotor` (신규) — **두 카탈로그 모두 등록**:
  - `core/factsheet.py` `INSTANT_ACTION_TYPES`에 `"disableMotor"` 추가(VDA5050 supported actions / factsheet).
  - `core/registry.py` InstantAction 목록에 `InstantAction("disableMotor", "Disable motor", motion=True)` 추가
    — **WebUI 액션 카탈로그/버튼·확인키·노출 정책의 소스**. (enableMotor가 `registry.py:227`에 있는 것과 동일.)
  - `adapter_jibot.py` 디스패치(`action.action_type == "disableMotor"`)에 `_handle_disable_motor_instant_action` 연결.
  - 핸들러: `manual_control.enabled` 게이트 → `vehicle.disable_motor()` → `um_set_motor(False)`,
    RUNNING→FINISHED/FAILED 보고(enableMotor 핸들러 미러).
  - WebUI(`web/render.py`): 빠른제어에 disableMotor 버튼 추가(`_EMERGENCY_ACTION_TYPES` 인근).

## 5. eq 설계 (fabris-equipments, TypeScript)

대상 파일:
- `packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts` (info 타입 상수 + morphism 스키마 + 헬퍼)
- `packages/amr/src/lib/amr/amr-base.equipment.ts` 또는 v3 equipment (`@EquipmentStatus` 선언 위치는 기존 jibot* 변수와 동일 패턴)

변경:
1. `const JIBOT_SAFETY_INFO_TYPE = 'JIBOT_SAFETY';` 추가.
2. `@EquipmentStatus` 변수 선언(기존 `jibotMode`/`eStop` 패턴):
   - `jibotStopReason: string`  — `{ dataType:'string', persist:'none', source:'device', tier:'essential', tags:['safety','jibot'], range:{ criticalValues:['EMERGENCY','PROTECTIVE_STOP','MOTOR_FAULT'] } }`
   - `jibotSystemStatus: string` — `tier:'important', tags:['safety','jibot']`
   - `motorEnable, hmiEstop, bumperEstop, pcEstop, motorError: string` — `tags:['safety','jibot']`
3. morphism 스키마에 매핑 추가:
   `jibotStopReason: (d) => getInformationReferenceValue(d, JIBOT_SAFETY_INFO_TYPE, 'stopReason')`, …각 키.
4. **상태머신 신규 룰 없음**: 어댑터가 `operatingMode=MANUAL`/`activeEmergencyStop`/`fieldViolation`/FATAL을
   세팅하므로 기존 `operatingMode→AmrState.MANUAL`, `eStop→EMERGENCY`, `fieldViolation→BLOCKED`,
   `fatal→ERROR` 경로가 그대로 정확한 상태를 만든다. 새 변수는 가시성/표시·이력용.

## 6. 데이터 흐름

```
로봇 펌웨어(motor MCU) ──/jrobot_status(jarvis_msgs/RobotStatus)──▶ bms_ros_listener(child rostopic)
  └─ set_bms(...)                         (기존)
  └─ set_robot_safety({system_status, motor_enable, hmi_estop, ...})  (신규)
        │
adapter_jibot 상태루프: _derive_jibot_stop_reason()
  ├─ safety_state.e_stop / field_violation / operating_mode / errors  (실제 수정, freshness fallback)
  └─ _refresh_jibot_safety_information() → information[ JIBOT_SAFETY ]
        │  MQTT VDA5050 v3 state
        ▼
fabris-equipments AMR v3 eq: morphism schema → @EquipmentStatus
  ├─ jibotStopReason / jibotSystemStatus / motorEnable / hmiEstop / ...   (표시)
  └─ operatingMode=MANUAL→AmrState.MANUAL, eStop→EMERGENCY, fieldViolation→BLOCKED (기존 재사용)

복구: enableMotor(um_set_motor True) → motor_enable=1 → reason=NONE → AUTOMATIC
재수동화/테스트: disableMotor(um_set_motor False) → motor_enable=0 → reason=MANUAL → MANUAL
```

## 7. 위험 / 미해결

1. **`operatingMode=MANUAL`은 플릿 전역 효과** — FMS/ACS가 해당 로봇에 오더를 보류할 수 있음.
   모터 꺼진 로봇엔 디스패치 안 하는 것이 맞으므로 **의도된 동작**. 매핑을 헬퍼 한 곳에 격리해
   필요 시 "operatingMode 유지 + eq-룰" 방식으로 전환 가능하게 둔다.
2. **`bumpe_stop` 폴라리티 미확정** — 라이브에서 `Press ON to Enable.`인데 `bumpe_stop=1` 관측.
   그래서 분류는 `system_status` 텍스트(`bumper Trigger!`) 기준, 원시 `bumperEstop`는 그대로 전달.
   신뢰 전 로봇에서 범퍼 0↔1 토글로 폴라리티 확인 권장.
3. **세분화는 온보드 ROS 리스너가 도는 환경에서만 활성**. dev/sim/리스너다운 시 freshness fallback으로
   기존 `motor_flag` 동작 유지.
4. **토큰 계약 동기화** — 토큰 문자열이 양쪽에서 일치해야 함. 불일치 시 eq는 silent fallback.

## 8. 테스트 (TDD)

**토큰 표 고정:** §3의 stopReason 토큰 표를 **양쪽 레포 테스트 fixture에 동일하게** 박아 계약을 고정한다
(공유 npm/pip 패키지는 과함). 한쪽이 토큰을 바꾸면 그쪽 테스트가 깨지도록.

어댑터:
- `find_robot_status_columns`/csv 파싱 안전 컬럼 추출 (헤더/행 fixture; 콤마·따옴표 포함 `system_status` 케이스).
- `_derive_jibot_stop_reason` 매핑표 — 각 system_status/flag 조합 + 우선순위.
- e_stop/operating_mode/field_violation/errors 파생 + **freshness fallback**(stale → AUTOACK).
- bumper일 때 `driving=False` 강제 → workingState=BLOCKED 확인.
- `JIBOT_MOTOR_FAULT` FATAL error set/clear.
- `JIBOT_SAFETY` 블록이 v3 state에 정확히 실리는지 + **stale 시 빈 문자열 발행**.
- `disableMotor` 핸들러 RUNNING→FINISHED/FAILED, 게이트 off 시 FAILED. registry/factsheet 등록 확인.

eq:
- morphism 스키마가 `JIBOT_SAFETY` infoReferences를 각 `@EquipmentStatus`로 매핑.
- `operatingMode=MANUAL → AmrState.MANUAL` 유지(회귀 방지).
- 알 수 없는 stopReason 토큰 → silent fallback.

## 9. 영향 파일 (요약)

어댑터: `adaptor/bms_ros_listener.py`, `adaptor/adapter_jibot.py`,
`jibot-client/src/jibot_client/client.py`(set_robot_safety/clear_robot_safety),
`adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py`(`ErrorType.JIBOT_MOTOR_FAULT`),
`adaptor/core/factsheet.py`(INSTANT_ACTION_TYPES), `adaptor/core/registry.py`(InstantAction disableMotor),
`adaptor/web/render.py`, 해당 테스트.

eq: `packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts`,
`packages/amr/src/lib/amr/amr-base.equipment.ts`(또는 v3 equipment), 해당 테스트.
