# JIBOT /jrobot_status 에러·상태 데이터 관측성 패스스루

작성일: 2026-06-27
상태: 설계 승인됨 (구현 계획 대기)
범위: 2개 리포 — `unified-amr-adaptor`(어댑터) + `fabris-equipments`(AMR v3 eq)

## 1. 배경 / 동기

2026-06-26 HN-SH6-TR-002(192.168.3.222) 현장 사고: order가 충전 노드 `F1_10_S1CH`로
보냈으나 로봇이 안 움직이고 어댑터는 오진 경고(`NODE_UNREACHED`, "map goal/path
mismatch")만 냈다. 근본 원인은 urobot이 `ERROR0200`("robot do not find goal name in
map")을 내부적으로 띄웠지만, UmGoto가 fire-and-forget(ret:none)이라 TCP로 거부 프레임이
안 돌아왔고, 어댑터/HMI 어디에도 그 에러코드가 노출되지 않은 것. (상세: 메모리
`charger-goto-vs-dock-silent-fail`)

urobot은 `/jrobot_status`(jarvis_msgs/RobotStatus) ROS 토픽에 풍부한 에러·상태 데이터를
싣는다(에러코드 카탈로그 `fault.json` 기준 ~38개 ERROR0xxx + 휠 모터/리프트/회전 상태).
어댑터는 이 토픽을 **이미 구독**하지만(`bms_ros_listener.py`) safety 플래그 일부만 뽑고,
`system_error_code`·휠 모터 폴트 등 에러 데이터는 버린다.

## 2. 목표 / 비목표

**목표**
- `/jrobot_status`의 에러·상태 필드 전부를 VDA5050 `information[]`(JIBOT_SAFETY 블록)으로 노출.
- 동일 데이터를 `fabris-equipments` AMR v3 eq의 `EquipmentStatus` 속성으로 노출.
- `systemErrorCode` 숫자에 사람이 읽는 이름/설명 보강(임베드 카탈로그 디코드).

**비목표 (이번에 안 함 — 나중에 사용자가 지정)**
- 에러코드 → VDA5050 `errors[]` 승격, FATAL/WARNING 심각도 부여, errorType 매핑.
- 어떤 코드를 ERROR로 취급해 주문을 멈출지 정책.
- `l_error`/`r_error`의 ucore hex 테이블(`E0101-2` 등) 디코딩 — raw 값만 노출.
- 어댑터 goto 수락(nrunto/MRosGoto) positive 확인, NODE_UNREACHED 메시지 정정,
  충전기 dock 라우팅(config) — 별개 작업(B는 사용자가 직접).

## 3. 데이터 출처 — jarvis_msgs/RobotStatus 관련 필드

`rosmsg show jarvis_msgs/RobotStatus`(.222 라이브 확인)에서 노출 대상:

| RobotStatus 필드 | 타입 | 의미 |
|---|---|---|
| `system_error_code` | uint16 | urobot 고수준 에러코드. 0=정상. 200 ↔ "ERROR0200" |
| `l_status` / `l_error` | uint16 | 좌휠 모터 상태 / 폴트코드 |
| `r_status` / `r_error` | uint16 | 우휠 모터 상태 / 폴트코드 |
| `lift_status` | uint16 | 리프트 기구 상태 |
| `rotate_status` | uint16 | 회전(클램프 계열) 기구 상태 |

기존에 이미 뽑던 필드(유지): `system_status`, `motor_enable`, `hmi_estop`,
`bumpe_stop`, `pc_estop`, `motor_error`, `pc_enable`, `charge`.

에러코드 디코드 카탈로그 출처: `/usr/local/urobot/params/service/fault.json`의
`error_code` 섹션 스냅샷(리포에 임베드). 주요 코드:

```
0000-0009 tag mode 도킹 에러 / 0100-0107 ref mode 도킹 에러
0200 robot do not find goal name in map      0201 charge mode robot charge failed
0400 robot status error after back from machine
0500-0510 센서 data outtime(odom/imu/laser x5/cam x3/deep cam)
0600 lost by map file error                  0603 lost by localization failed
0700 robot not receive message long time     0701 robot not send vel long time
0702 robot main loop stuck                   1000 disable signal < 2s
```

## 4. 아키텍처 / 데이터 흐름

```
/jrobot_status (rostopic echo -p, 자식 프로세스 — 이미 구동)
  │  CSV 행
  ▼
bms_ros_listener.py
  · SAFETY_FIELDS 튜플에 신규 필드 추가(동일 RobotStatus·동일 캐시라 별도 set 불필요)
  · parse_robot_safety_row → vehicle.set_robot_safety(dict)  ← 캐시(_robot_safety)
  ▼
adapter_jibot.py :: _refresh_jibot_safety_information()
  · _jibot_safety_if_fresh()로 캐시 읽음(staleness 윈도우)
  · JIBOT_SAFETY Information.infoReferences에 신규 키 append
  · systemErrorCode != 0 이면 임베드 카탈로그로 name/description 보강
  ▼
MQTT VDA5050 state.information[]  (JIBOT_SAFETY 블록)
  │  amr/v3/{equipmentCode}/state
  ▼
fabris-equipments :: amr-l2m-v3-base.equipment.ts
  · selector: getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, '<key>')
  · @EquipmentStatus({...}) 속성으로 노출
```

각 유닛 책임 경계:
- **listener**: ROS CSV → 필드 dict 캐시. urobot 의미론 모름(문자열 패스스루).
- **_refresh_jibot_safety_information**: 캐시 → JIBOT_SAFETY infoReference. 디코드 보강.
- **임베드 카탈로그(신규 작은 모듈)**: `code:int → (name:str, description:str)`. 순수 함수, 단독 테스트.
- **fabris v3 base eq**: infoReference → EquipmentStatus. 어댑터 내부 모름.

## 5. 어댑터 변경 상세 (unified-amr-adaptor)

### 5.1 `adaptor/bms_ros_listener.py`
- `SAFETY_FIELDS` 튜플에 `system_error_code, l_status, l_error, r_status, r_error,
  lift_status, rotate_status` 추가. 기존 `find_robot_status_columns`/`parse_robot_safety_row`가
  suffix 매칭이라 튜플에 이름만 추가하면 됨(CSV 컬럼명 `field.<name>` suffix 매칭).
- **헤더 재인식 일반화 (리뷰 #3):** 현재 `_consume()`은 헤더 행을 `"bms_voltage" in line`
  일 때만 재매핑한다(`bms_ros_listener.py:119`). `/jrobot_status`(RobotStatus)는 항상
  `bms_voltage`를 포함하므로 지금은 동작하지만, 헤더 판정을 단일 BMS 컬럼에 묶는 건 취약하다
  (펌웨어/토픽 변형에서 BMS 컬럼이 빠지면 신규 safety 필드가 전혀 파싱 안 됨). 헤더 판정을
  `rostopic echo -p`의 헤더 형식(`%time`로 시작 또는 `field.` 포함)으로 일반화하고, BMS·safety
  컬럼 매핑을 그 안에서 독립적으로 갱신한다.
- `clear_robot_safety`(stale/EOF) 동작은 그대로 — 신규 키도 함께 비워짐.

### 5.2 `adaptor/adapter_jibot.py :: _refresh_jibot_safety_information`
- 기존 `f(name)` 헬퍼로 신규 키 추가:
  `systemErrorCode, systemErrorName, systemErrorDescription, wheelLeftStatus,
   wheelLeftError, wheelRightStatus, wheelRightError, liftStatus, rotateStatus`.
- **모든 신규 키는 항상 reference로 송출한다 (리뷰 #1 — stale-clear 버그 방지).**
  fabris `applyVda5050State()`는 `value === undefined`면 skip하고 이전 값을 유지한다
  (`amr-base.equipment.ts:1385`). 따라서 "에러가 있을 때만 name/description reference를
  추가"하면, 코드가 `200→0`으로 정상 복귀할 때 reference가 사라져 selector가 undefined를
  반환 → 장비 속성에 이전 에러명(`ERROR0200`)이 **그대로 남는다**. 그러므로:
  - `systemErrorCode`: 항상 송출. fresh면 raw 값(`"0"`, `"200"`…), stale/absent면 `""`.
  - `systemErrorName`/`systemErrorDescription`: 항상 송출.
    - fresh & code 정수 & ≠0: 카탈로그 디코드. 알려진 코드 → (`"ERROR0200"`, `"robot do
      not find goal name in map"`). 미지 코드 → (`"ERROR{n:04d}"`, `""`).
    - fresh & code==0: 둘 다 `""` (정상 = 에러 없음).
    - stale/absent/비정수: 둘 다 `""`.
- 휠/리프트/회전 키: fresh면 raw 값, stale/absent면 `""`.
- 요지: **키 집합은 매 publish마다 고정**, 값만 비운다 → eq가 stale 값을 유지하지 않음.

### 5.3 신규 `adaptor/.../jibot_error_catalog.py`
- `decode_system_error_code(value) -> (name, description)`. fault.json `error_code`
  섹션 스냅샷을 dict 리터럴로 임베드. 런타임 파일 의존 없음(sim/dev/테스트 결정적).

## 6. fabris-equipments 변경 상세 — 2파일 3곳 (리뷰 #2)

검증 결과 JIBOT safety 속성은 selector 한 곳이 아니라 다음 3곳에 걸쳐 선언된다.
신규 키 each마다 세 곳을 모두 추가해야 한다.

**(a) `packages/amr/src/lib/amr/amr-base.equipment.ts` — `Vda5050MappedFields` 타입**
(기존 `jibotSystemStatus?: string` 인접, ~85行대). 신규 키를 optional 필드로 추가:
```ts
jibotSystemErrorCode?: string; jibotSystemErrorName?: string; jibotSystemErrorDescription?: string;
jibotWheelLeftStatus?: string; jibotWheelLeftError?: string;
jibotWheelRightStatus?: string; jibotWheelRightError?: string;
jibotLiftStatus?: string; jibotRotateStatus?: string;
```

**(b) `amr-base.equipment.ts` — `@EquipmentStatus` 클래스 속성**
(기존 `jibotSystemStatus: string` 인접, ~440行대). 각 키에 데코레이터+필드 추가
(기존 `{ persist: 'none', source: 'device', tags: ['safety','jibot'] }` 패턴):
```ts
@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety','jibot'] })
jibotSystemErrorCode: string;
// systemErrorName/Description, wheel*, lift/rotate 동일 패턴
```

**(c) `packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts` — selector**
`VDA5050_L2M_V3_SCHEMA: StrictSchema<Vda5050MappedFields, …>`(86行)에 항목 추가:
```ts
jibotSystemErrorCode: (d) => getInformationReferenceValue(d, JIBOT_SAFETY_INFO_TYPE, 'systemErrorCode'),
jibotSystemErrorName: (d) => getInformationReferenceValue(d, JIBOT_SAFETY_INFO_TYPE, 'systemErrorName'),
jibotSystemErrorDescription: (d) => getInformationReferenceValue(d, JIBOT_SAFETY_INFO_TYPE, 'systemErrorDescription'),
jibotWheelLeftStatus / jibotWheelLeftError / jibotWheelRightStatus / jibotWheelRightError,
jibotLiftStatus / jibotRotateStatus,
```
`StrictSchema`라 `Vda5050MappedFields`에 키를 추가하면 selector 누락 시 **컴파일 에러**로
강제된다(안전망). infoReference 부재 시 selector는 `undefined` 반환 → (a)에서 optional이라
이전 값 유지 위험이 있으나, 어댑터가 5.2처럼 키를 **항상 `""`로라도 송출**하므로
값은 항상 갱신된다.
- 신규 errorType enum/토큰 추가 **없음** → AMR_STATE cross-repo 계약 영향 없음.

## 7. 에러 처리 / 엣지

- `/jrobot_status` 부재(sim/dev) 또는 stale: 캐시 None → 모든 키 빈 문자열. 정상.
- listener 자식 프로세스 재시작 시 CSV 헤더 재등장 → 컬럼 재매핑(기존 로직).
- 신규 필드가 일부 펌웨어/맵에서 누락: suffix 매칭 실패 시 해당 키만 빈값(나머지 영향 없음).
- 값은 전부 문자열 패스스루 — 파싱 실패 모드 없음(디코드만 int 변환, 실패 시 raw 유지).

## 8. 테스트 (TDD)

**어댑터**
- `jibot_error_catalog`: 200→("ERROR0200", "robot do not find goal name in map"),
  0→정상 처리, 미지 코드→("ERROR9999",""), 비정수→안전 처리.
- `bms_ros_listener`: CSV 헤더+행에서 신규 필드 추출; **BMS 컬럼이 빠진 헤더에서도**
  헤더로 인식하고 safety 컬럼을 매핑(리뷰 #3 일반화 검증); 컬럼 누락 시 graceful.
- `_refresh_jibot_safety_information`: 신규 키 노출, systemErrorCode 디코드 보강,
  stale 시 빈값.
- **에러 해제 전이 (리뷰 #4):** fresh `system_error_code=200` 후 fresh `0`이 들어오면
  `systemErrorCode="0"`, `systemErrorName=""`, `systemErrorDescription=""`로 publish되는지
  (키가 사라지지 않고 값만 비는지) 검증. stale→fresh, fresh→stale 전이도 동일 확인.

**fabris-equipments**
- v3 base eq: JIBOT_SAFETY infoReference 담긴 샘플 state → 각 EquipmentStatus 속성 매핑
  (`Vda5050MappedFields` 타입 + `@EquipmentStatus` 속성 + `VDA5050_L2M_V3_SCHEMA` selector 3곳).
- **에러 해제 전이 (리뷰 #4):** `systemErrorName="ERROR0200"` 적용 후, 다음 state가
  `systemErrorName=""`를 실으면 속성이 `""`로 갱신되어 **이전 에러명이 남지 않는지** 검증
  (undefined가 아니라 `""`로 와야 `applyVda5050State`의 skip을 통과해 덮어씀).
- infoReference가 아예 없을 때 selector→undefined→이전 값 유지가 되는 경계도 명시(어댑터가
  항상 키를 송출하므로 운영상 발생 안 하지만 계약으로 문서화).

## 9. 향후 (이번 범위 밖, 사용자 지정 예정)

노출된 raw 데이터를 어디에/어떻게 매핑할지(errors[] 승격, FATAL/WARNING, errorType,
주문 정지 정책, l_error/r_error ucore 디코드)는 사용자가 추후 지정. 본 작업은 그 입력이
되는 **관측 가능성**만 확보한다.
