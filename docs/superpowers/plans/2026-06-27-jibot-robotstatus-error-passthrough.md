# JIBOT /jrobot_status 에러·상태 데이터 관측성 패스스루 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/jrobot_status`(RobotStatus)의 에러·상태 필드 전부를 VDA5050 `information[]`(JIBOT_SAFETY)로 노출하고, fabris-equipments AMR v3 eq의 `EquipmentStatus` 속성으로도 노출한다(순수 관측성, 심각도/errorType 매핑 없음).

**Architecture:** 어댑터는 이미 `/jrobot_status`를 `bms_ros_listener`(rostopic echo -p 자식 프로세스)로 소비한다. 추출 필드를 확장해 vehicle `_robot_safety` 캐시에 담고, `_refresh_jibot_safety_information`이 JIBOT_SAFETY infoReference로 publish한다. fabris v3 eq는 그 infoReference를 selector→@EquipmentStatus로 매핑한다. `system_error_code`만 리포 임베드 카탈로그로 사람이 읽는 name/description으로 디코드한다.

**Tech Stack:** Python 3.11+ (어댑터, pytest/unittest), TypeScript (fabris-equipments, jest/ts-jest, morphism StrictSchema).

## Global Constraints

- 어댑터 테스트는 ROS-sourced 셸에서 collection 크래시하므로 **항상 `cd adaptor && PYTHONPATH=. python -m pytest …`** 로 실행한다.
- fabris 테스트는 **리포 루트에서 jest**로 실행(`cd <fabris> && npx jest <pattern>`); jest 30 → `--testPathPatterns`.
- **신규 errorType enum/토큰 추가 금지** (AMR_STATE cross-repo 계약). 본 작업은 errors[] 미사용, 순수 information/EquipmentStatus.
- **infoReference 키 이름 계약(양 리포 정확히 일치):** `systemErrorCode, systemErrorName, systemErrorDescription, wheelLeftStatus, wheelLeftError, wheelRightStatus, wheelRightError, liftStatus, rotateStatus`.
- **always-emit 원칙:** JIBOT_SAFETY 키 집합은 매 publish 고정. 값이 없으면 키를 빼지 말고 `""`로 publish(소비자 `applyVda5050State`가 `undefined`를 skip하므로 키 누락 시 stale 잔존).
- 매핑(errors[] 승격/FATAL·WARNING/`l_error` ucore 디코드)은 **이번 범위 밖** — 추후 지정.

Spec: `docs/superpowers/specs/2026-06-27-jibot-robotstatus-error-passthrough-design.md`

---

# Part A — 어댑터 (unified-amr-adaptor, Python)

## Task 1: urobot 에러코드 임베드 카탈로그 + 디코드 함수

**Files:**
- Create: `adaptor/utils/jibot_error_catalog.py`
- Test: `adaptor/tests/test_jibot_error_catalog.py`

**Interfaces:**
- Produces: `decode_system_error_code(raw) -> tuple[str, str]` — `(name, description)`. `0`/빈값/비정수 → `("","")`. 알려진 코드 → `("ERROR%04d", description)`. 미지 코드 → `("ERROR%04d", "")`.
- Produces: `ERROR_CODE_CATALOG: dict[int, str]` (코드→설명 스냅샷).

- [ ] **Step 1: 실패 테스트 작성**

Create `adaptor/tests/test_jibot_error_catalog.py`:

```python
import unittest

from utils.jibot_error_catalog import decode_system_error_code, ERROR_CODE_CATALOG


class JibotErrorCatalogTest(unittest.TestCase):
    def test_known_code_200_goal_not_in_map(self):
        name, desc = decode_system_error_code("200")
        self.assertEqual(name, "ERROR0200")
        self.assertEqual(desc, "robot do not find goal name in map")

    def test_zero_is_no_error(self):
        self.assertEqual(decode_system_error_code("0"), ("", ""))
        self.assertEqual(decode_system_error_code(0), ("", ""))

    def test_empty_or_none_is_no_error(self):
        self.assertEqual(decode_system_error_code(""), ("", ""))
        self.assertEqual(decode_system_error_code(None), ("", ""))

    def test_non_integer_is_safe(self):
        self.assertEqual(decode_system_error_code("oops"), ("", ""))

    def test_unknown_code_keeps_name_blank_desc(self):
        self.assertEqual(decode_system_error_code("9999"), ("ERROR9999", ""))

    def test_int_input_accepted(self):
        name, desc = decode_system_error_code(702)
        self.assertEqual(name, "ERROR0702")
        self.assertEqual(desc, "robot main loop stuck")

    def test_catalog_has_core_codes(self):
        for code in (200, 201, 600, 603, 700, 701, 702, 510):
            self.assertIn(code, ERROR_CODE_CATALOG)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_jibot_error_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.jibot_error_catalog'`

- [ ] **Step 3: 카탈로그 모듈 구현**

Create `adaptor/utils/jibot_error_catalog.py`:

```python
"""urobot system_error_code 디코드 카탈로그.

robot의 /usr/local/urobot/params/service/fault.json `error_code` 섹션 스냅샷.
런타임 파일 의존 없이(sim/dev/테스트 결정적) 코드→설명을 제공한다. 이 모듈은
의미만 디코드할 뿐 심각도/errorType은 부여하지 않는다(관측성 전용).
"""

# code(int) -> human-readable description. name은 f"ERROR{code:04d}"로 파생.
ERROR_CODE_CATALOG = {
    0: "",  # no error
    1: "tag mode robot no localization result when init",
    2: "tag mode robot error too large when init",
    3: "tag mode Lateral error too large after approving",
    4: "tag mode error too large after checkpose",
    5: "tag mode id error after checkpose",
    6: "tag mode do not get localization result when approving",
    7: "tag mode do not get localization result when checkpose, camera offline",
    8: "tag mode do not get localization result when checkpose, no code",
    9: "tag mode do not get localization result when checkpose, recognition program error",
    100: "ref mode no localization result when approving",
    101: "ref mode robot no localization result when init",
    102: "ref mode laser data error when approving",
    103: "ref mode robot error too large when init",
    104: "ref mode Longitudinal error too large when final check",
    105: "ref mode robot docking timeout",
    106: "ref mode Lateral error too large after approving",
    107: "ref mode robot dock failed after 3 times",
    200: "robot do not find goal name in map",
    201: "charge mode robot charge failed",
    400: "robot status error after back from machine",
    500: "robot odometry data outtime",
    501: "robot inner imu data outtime",
    502: "robot outer imu data outtime",
    503: "robot front laser data outtime",
    504: "robot back laser data outtime",
    505: "robot top laser data outtime",
    506: "robot left laser data outtime",
    507: "robot right laser data outtime",
    508: "robot cam1 data outtime",
    509: "robot cam2 data outtime",
    510: "robot deep cam data outtime",
    600: "lost by map file error",
    603: "lost by localization failed",
    700: "robot not receive message long time",
    701: "robot not send vel long time",
    702: "robot main loop stuck",
    1000: "received a disable signal of less than 2 seconds",
}
# 주의: code 0(=ERROR0000 "tag mode robot docking timeout")은 운영상 "에러 없음"으로
# 쓰이므로 의도적으로 빈 설명으로 둔다(decode_system_error_code가 0을 no-error 처리).


def decode_system_error_code(raw):
    """system_error_code → (name, description).

    0/빈값/None/비정수 → ("",""). 알려진 비0 코드 → ("ERROR%04d", description).
    미지 비0 코드 → ("ERROR%04d", "").
    """
    try:
        code = int(str(raw).strip())
    except (TypeError, ValueError):
        return "", ""
    if code == 0:
        return "", ""
    return "ERROR%04d" % code, ERROR_CODE_CATALOG.get(code, "")
```

- [ ] **Step 4: 통과 확인**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_jibot_error_catalog.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add adaptor/utils/jibot_error_catalog.py adaptor/tests/test_jibot_error_catalog.py
git commit -m "feat(adaptor): embed urobot system_error_code decode catalog"
```

---

## Task 2: 리스너 SAFETY_FIELDS 확장 + suffix 충돌 수정

**Files:**
- Modify: `adaptor/bms_ros_listener.py` (`SAFETY_FIELDS` 튜플, `find_robot_status_columns`)
- Test: `adaptor/tests/test_bms_ros_listener_safety_parse.py`

**Interfaces:**
- Consumes: 기존 `find_robot_status_columns(header)`, `parse_robot_safety_row(row, columns)`.
- Produces: `parse_robot_safety_row`가 신규 키(`system_error_code, l_status, l_error, r_status, r_error, lift_status, rotate_status`)를 포함한 dict 반환. `r_error`/`motor_error` 컬럼이 서로 다른 인덱스로 매핑.

- [ ] **Step 1: 실패 테스트 추가**

`adaptor/tests/test_bms_ros_listener_safety_parse.py` 상단 import 아래(기존 `find_robot_status_columns`, `parse_robot_safety_row` import 사용)에 추가:

```python
class NewStatusFieldsTest(unittest.TestCase):
    HEADER = (
        "%time,field.system_status,field.system_error_code,field.motor_enable,"
        "field.motor_error,field.l_status,field.l_error,field.r_status,"
        "field.r_error,field.lift_status,field.rotate_status,field.charge"
    )

    def test_new_fields_extracted_to_distinct_columns(self):
        cols = find_robot_status_columns(self.HEADER)
        for field in (
            "system_error_code", "l_status", "l_error",
            "r_status", "r_error", "lift_status", "rotate_status",
        ):
            self.assertIn(field, cols, f"{field} not mapped")
        # r_error must NOT collide with the motor_error column.
        self.assertNotEqual(cols["r_error"], cols["motor_error"])
        self.assertNotEqual(cols["l_error"], cols["r_error"])

    def test_row_values_parsed(self):
        cols = find_robot_status_columns(self.HEADER)
        row = "1700000000.0,Normal,200,1,0,0,0,1,0,0,0,0"
        parsed = parse_robot_safety_row(row, cols)
        self.assertEqual(parsed["system_error_code"], "200")
        self.assertEqual(parsed["r_status"], "1")
        self.assertEqual(parsed["motor_error"], "0")
```

(파일에 `import unittest`가 없으면 추가. 기존 테스트가 `unittest.TestCase`를 쓰면 이미 있음.)

- [ ] **Step 2: 실패 확인**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_bms_ros_listener_safety_parse.py::NewStatusFieldsTest -v`
Expected: FAIL — `KeyError: 'system_error_code'` (또는 `r_error`가 `motor_error`와 동일 인덱스로 AssertionError)

- [ ] **Step 3: SAFETY_FIELDS 확장 + 매칭 수정**

`adaptor/bms_ros_listener.py`의 `SAFETY_FIELDS` 튜플을 다음으로 교체:

```python
SAFETY_FIELDS = (
    "system_status",
    "system_error_code",
    "motor_enable",
    "motor_enable_status",
    "hmi_estop",
    "bumpe_stop",
    "pc_estop",
    "motor_error",
    "pc_enable",
    "charge",
    "l_status",
    "l_error",
    "r_status",
    "r_error",
    "lift_status",
    "rotate_status",
)
```

`find_robot_status_columns` 안의 매칭 줄을 dot-segment 경계 매칭으로 교체
(`r_error`가 `field.motor_error`에 잘못 매칭되는 충돌 제거):

```python
    for i, name in enumerate(col.strip() for col in names):
        for field in SAFETY_FIELDS:
            # full dot-segment match: leaf name이 정확히 field거나 ".field"로 끝남.
            # 단순 endswith는 r_error가 field.motor_error("...r_error")에 오매칭됨.
            if name == field or name.endswith("." + field):
                columns[field] = i
```

- [ ] **Step 4: 통과 확인 (신규 + 기존 회귀)**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_bms_ros_listener_safety_parse.py -v`
Expected: PASS (신규 2 + 기존 전부)

- [ ] **Step 5: 커밋**

```bash
git add adaptor/bms_ros_listener.py adaptor/tests/test_bms_ros_listener_safety_parse.py
git commit -m "feat(adaptor): extract urobot error/status fields from /jrobot_status; fix r_error/motor_error suffix collision"
```

---

## Task 3: 리스너 헤더 인식 일반화 (BMS 컬럼 비의존)

**Files:**
- Modify: `adaptor/bms_ros_listener.py` (`_consume` 헤더 판정)
- Test: `adaptor/tests/test_bms_ros_listener_safety_consume.py`

**Interfaces:**
- Consumes: 기존 `BmsRosListener._consume(stdout)`, `find_bms_columns`, `find_robot_status_columns`.
- Produces: `bms_voltage` 컬럼이 없는 헤더에서도 헤더로 인식하고 safety 컬럼을 매핑.

- [ ] **Step 1: 실패 테스트 추가**

`adaptor/tests/test_bms_ros_listener_safety_consume.py`에 추가(기존 `FakeVehicle`/`FakeCfg`/`FakeStdout` 하니스 재사용 — 이미 파일 상단에 정의됨):

```python
class HeaderWithoutBmsConsumeTest(unittest.TestCase):
    def test_safety_parsed_when_header_lacks_bms_column(self):
        # bms_voltage/current 없는 헤더 + safety 컬럼만. 헤더로 인식해 safety 매핑해야 함.
        header = "%time,field.system_status,field.system_error_code,field.hmi_estop\n"
        row = "1,Normal,200,0\n"
        veh = FakeVehicle()
        listener = BmsRosListener(veh, FakeCfg())
        stdout = FakeStdout([header, row])
        asyncio.run(listener._consume(stdout))
        self.assertIsNotNone(veh.safety)
        self.assertEqual(veh.safety["system_error_code"], "200")
        self.assertEqual(veh.safety["system_status"], "Normal")
```

- [ ] **Step 2: 실패 확인**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_bms_ros_listener_safety_consume.py::HeaderWithoutBmsTest -v`
Expected: FAIL — safety가 비어 있음(헤더가 `"bms_voltage" in line` 게이트를 통과 못 해 매핑 안 됨)

- [ ] **Step 3: 헤더 판정 일반화**

`adaptor/bms_ros_listener.py`의 `_consume` 내부 헤더 분기를 교체. 현재:

```python
            if "bms_voltage" in line:  # CSV header (re)appears on (re)start
                voltage_idx, current_idx = find_bms_columns(line)
                safety_columns = find_robot_status_columns(line)
                continue
```

다음으로 교체(`rostopic echo -p` 헤더는 항상 `%time`으로 시작; 데이터 행은 타임스탬프 숫자로 시작):

```python
            if line.startswith("%time"):
                # CSV 헤더(재시작 시 재등장). 단일 BMS 컬럼에 의존하지 않는다.
                voltage_idx, current_idx = find_bms_columns(line)
                safety_columns = find_robot_status_columns(line)
                continue
```

> `find_bms_columns`는 bms 컬럼이 없으면 이미 `(None, None)`을 반환하고(`bms_ros_listener.py:16`), `parse_bms_row`는 `voltage_idx`가 None이면 None을 반환하므로 BMS 없는 헤더에서도 안전하다(별도 보강 불필요).

- [ ] **Step 4: 통과 확인 (신규 + 기존 회귀)**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_bms_ros_listener_safety_consume.py tests/test_bms_ros_listener_consume.py -v`
Expected: PASS (신규 + 기존 consume 전부)

- [ ] **Step 5: 커밋**

```bash
git add adaptor/bms_ros_listener.py adaptor/tests/test_bms_ros_listener_safety_consume.py
git commit -m "feat(adaptor): recognize /jrobot_status header without depending on BMS column"
```

---

## Task 4: JIBOT_SAFETY info에 urobot 에러/상태 키 surface (+디코드, always-emit)

**Files:**
- Modify: `adaptor/adapter_jibot.py` (import 1줄, `_refresh_jibot_safety_information`)
- Test: `adaptor/tests/test_jibot_safety_info.py`

**Interfaces:**
- Consumes: `decode_system_error_code` (Task 1), `_jibot_safety_if_fresh()`, 기존 `f(name)` 헬퍼.
- Produces: JIBOT_SAFETY infoReference에 `systemErrorCode, systemErrorName, systemErrorDescription, wheelLeftStatus, wheelLeftError, wheelRightStatus, wheelRightError, liftStatus, rotateStatus` 항상 포함(stale/0 → "").

- [ ] **Step 1: 실패 테스트 추가**

`adaptor/tests/test_jibot_safety_info.py`에 추가(기존 `_safety_refs` 헬퍼 재사용):

```python
    def test_system_error_code_decoded_when_fresh(self):
        refs = _safety_refs({
            "system_status": "ERR", "system_error_code": "200",
            "l_status": "0", "l_error": "0", "r_status": "1", "r_error": "0",
            "lift_status": "0", "rotate_status": "0",
        })
        self.assertEqual(refs["systemErrorCode"], "200")
        self.assertEqual(refs["systemErrorName"], "ERROR0200")
        self.assertEqual(refs["systemErrorDescription"], "robot do not find goal name in map")
        self.assertEqual(refs["wheelRightStatus"], "1")
        self.assertEqual(refs["wheelLeftError"], "0")
        self.assertEqual(refs["rotateStatus"], "0")

    def test_error_keys_present_and_blank_when_code_zero(self):
        refs = _safety_refs({"system_status": "Normal", "system_error_code": "0"})
        # 키는 항상 존재, 값만 빈 문자열 (stale-clear 방지).
        self.assertEqual(refs["systemErrorCode"], "0")
        self.assertEqual(refs["systemErrorName"], "")
        self.assertEqual(refs["systemErrorDescription"], "")

    def test_error_keys_present_and_blank_when_stale(self):
        refs = _safety_refs({"system_error_code": "200"}, fresh=False)
        for key in ("systemErrorCode", "systemErrorName", "systemErrorDescription",
                    "wheelLeftStatus", "wheelRightError", "liftStatus", "rotateStatus"):
            self.assertEqual(refs[key], "", f"{key} should be blank when stale")
```

추가로, **200→0 해제 전이** 테스트(키가 사라지지 않고 값만 비는지). 같은 파일에 추가:

```python
    def test_error_clears_on_transition_200_to_0(self):
        import asyncio, time
        from tests._stop_reason_harness import make_adapter, start_state

        async def scenario():
            adapter = make_adapter()
            adapter._vehicle._robot_safety = {"system_error_code": "200"}
            adapter._vehicle._robot_safety_last_update = time.monotonic()
            task = await start_state(adapter)
            try:
                await asyncio.sleep(0.1)
                # 정상 복귀: code 0
                adapter._vehicle._robot_safety = {"system_error_code": "0"}
                adapter._vehicle._robot_safety_last_update = time.monotonic()
                await asyncio.sleep(0.1)
                block = next(i for i in adapter.state.information
                            if getattr(i, "info_type", None) == "JIBOT_SAFETY")
                refs = {r.reference_key: r.reference_value for r in block.info_references}
                return refs
            finally:
                task.cancel()

        refs = asyncio.run(scenario())
        self.assertEqual(refs["systemErrorCode"], "0")
        self.assertEqual(refs["systemErrorName"], "")
        self.assertEqual(refs["systemErrorDescription"], "")
```

- [ ] **Step 2: 실패 확인**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_jibot_safety_info.py -v`
Expected: FAIL — `KeyError: 'systemErrorCode'`

- [ ] **Step 3: 구현**

`adaptor/adapter_jibot.py` import 블록(상단 `from utils.…` 인접)에 추가:

```python
from utils.jibot_error_catalog import decode_system_error_code
```

`_refresh_jibot_safety_information`의 `references = [ ... InfoReference("charge", f("charge")) ]` 리스트 **직후**(아직 `self.state.information = [...]` 재구성 전)에 신규 키 append:

```python
        err_code = f("system_error_code")
        err_name, err_desc = decode_system_error_code(err_code)
        references += [
            InfoReference("systemErrorCode", err_code),
            InfoReference("systemErrorName", err_name),
            InfoReference("systemErrorDescription", err_desc),
            InfoReference("wheelLeftStatus", f("l_status")),
            InfoReference("wheelLeftError", f("l_error")),
            InfoReference("wheelRightStatus", f("r_status")),
            InfoReference("wheelRightError", f("r_error")),
            InfoReference("liftStatus", f("lift_status")),
            InfoReference("rotateStatus", f("rotate_status")),
        ]
```

(`f`는 `safety is None`이면 `""`를 반환하므로 stale 시 자동으로 모든 키가 `""`. `decode_system_error_code("")` → `("","")`.)

- [ ] **Step 4: 통과 확인**

Run: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_jibot_safety_info.py -v`
Expected: PASS (기존 + 신규 4)

- [ ] **Step 5: 커밋**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_jibot_safety_info.py
git commit -m "feat(adaptor): surface urobot error/wheel/lift status in JIBOT_SAFETY info (always-emit, decoded)"
```

---

# Part B — fabris-equipments (AMR v3 eq, TypeScript)

## Task 5: v3 eq에 urobot 상태/에러 EquipmentStatus 추가 (타입+속성+selector)

**Files:**
- Modify: `packages/amr/src/lib/amr/amr-base.equipment.ts` (`Vda5050MappedFields` 타입, `@EquipmentStatus` 속성)
- Modify: `packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts` (`VDA5050_L2M_V3_SCHEMA` selector)
- Test: `packages/amr/src/lib/amr/amr-l2m-v3/__tests__/amr-l2m-v3-jibot-safety.spec.ts`

**Interfaces:**
- Consumes: 어댑터 JIBOT_SAFETY infoReference 키(Global Constraints의 키 계약).
- Produces: eq 인스턴스 속성 `jibotSystemErrorCode, jibotSystemErrorName, jibotSystemErrorDescription, jibotWheelLeftStatus, jibotWheelLeftError, jibotWheelRightStatus, jibotWheelRightError, jibotLiftStatus, jibotRotateStatus` (string).

- [ ] **Step 1: 실패 테스트 추가**

`amr-l2m-v3-jibot-safety.spec.ts`의 `describe('JIBOT_SAFETY mapping', …)` 안에 추가:

```typescript
	it('maps urobot error/wheel/lift status onto equipment status', () => {
		const amr = createTestAmr();
		amr.onReceive({
			inputMessage: {
				operatingMode: 'AUTOMATIC',
				safetyState: { activeEmergencyStop: 'NONE' },
				information: [
					{
						infoType: 'JIBOT_SAFETY',
						infoLevel: 'INFO',
						infoDescription: 'JIBOT safety status',
						infoReferences: [
							{ referenceKey: 'systemErrorCode', referenceValue: '200' },
							{ referenceKey: 'systemErrorName', referenceValue: 'ERROR0200' },
							{ referenceKey: 'systemErrorDescription', referenceValue: 'robot do not find goal name in map' },
							{ referenceKey: 'wheelLeftStatus', referenceValue: '0' },
							{ referenceKey: 'wheelLeftError', referenceValue: '0' },
							{ referenceKey: 'wheelRightStatus', referenceValue: '1' },
							{ referenceKey: 'wheelRightError', referenceValue: '0' },
							{ referenceKey: 'liftStatus', referenceValue: '0' },
							{ referenceKey: 'rotateStatus', referenceValue: '0' },
						],
					},
				],
			} as any,
		});

		expect(amr.jibotSystemErrorCode).toBe('200');
		expect(amr.jibotSystemErrorName).toBe('ERROR0200');
		expect(amr.jibotSystemErrorDescription).toBe('robot do not find goal name in map');
		expect(amr.jibotWheelRightStatus).toBe('1');
		expect(amr.jibotLiftStatus).toBe('0');
		expect(amr.jibotRotateStatus).toBe('0');
	});

	it('clears error name/description when adapter sends blanks (200 -> 0)', () => {
		const amr = createTestAmr();
		const send = (code: string, name: string, desc: string) =>
			amr.onReceive({
				inputMessage: {
					operatingMode: 'AUTOMATIC',
					safetyState: { activeEmergencyStop: 'NONE' },
					information: [
						{
							infoType: 'JIBOT_SAFETY',
							infoLevel: 'INFO',
							infoReferences: [
								{ referenceKey: 'systemErrorCode', referenceValue: code },
								{ referenceKey: 'systemErrorName', referenceValue: name },
								{ referenceKey: 'systemErrorDescription', referenceValue: desc },
							],
						},
					],
				} as any,
			});

		send('200', 'ERROR0200', 'robot do not find goal name in map');
		expect(amr.jibotSystemErrorName).toBe('ERROR0200');
		// 정상 복귀: 어댑터가 빈 문자열을 실어 보냄 → 이전 에러명이 남으면 안 됨.
		send('0', '', '');
		expect(amr.jibotSystemErrorCode).toBe('0');
		expect(amr.jibotSystemErrorName).toBe('');
		expect(amr.jibotSystemErrorDescription).toBe('');
	});
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/lab2m-llm1/workspaces/fabris-equipments && npx jest amr-l2m-v3-jibot-safety`
Expected: FAIL — TS 컴파일/타입 에러(`Property 'jibotSystemErrorCode' does not exist`) 또는 `StrictSchema` 누락 에러

- [ ] **Step 3a: `Vda5050MappedFields` 타입 확장**

`packages/amr/src/lib/amr/amr-base.equipment.ts`의 `export type Vda5050MappedFields = { … }` 안, `jibotSystemStatus?: string;` 인접에 추가:

```typescript
	jibotSystemErrorCode?: string;
	jibotSystemErrorName?: string;
	jibotSystemErrorDescription?: string;
	jibotWheelLeftStatus?: string;
	jibotWheelLeftError?: string;
	jibotWheelRightStatus?: string;
	jibotWheelRightError?: string;
	jibotLiftStatus?: string;
	jibotRotateStatus?: string;
```

- [ ] **Step 3b: `@EquipmentStatus` 클래스 속성 추가**

같은 파일, `jibotSystemStatus: string;` 선언 인접(기존 safety 속성 블록)에 추가:

```typescript
	/** urobot system_error_code 원본 ('0' = 정상) */
	@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
	jibotSystemErrorCode: string;

	/** urobot 에러 코드명 (예: 'ERROR0200'), 정상 시 '' */
	@EquipmentStatus({ persist: 'none', source: 'device', tier: 'important', tags: ['safety', 'jibot'] })
	jibotSystemErrorName: string;

	/** urobot 에러 설명문, 정상 시 '' */
	@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
	jibotSystemErrorDescription: string;

	/** 좌휠 상태 (raw) */
	@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
	jibotWheelLeftStatus: string;

	/** 좌휠 폴트코드 (raw) */
	@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
	jibotWheelLeftError: string;

	/** 우휠 상태 (raw) */
	@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
	jibotWheelRightStatus: string;

	/** 우휠 폴트코드 (raw) */
	@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
	jibotWheelRightError: string;

	/** 리프트 상태 (raw) */
	@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
	jibotLiftStatus: string;

	/** 회전(클램프) 상태 (raw) */
	@EquipmentStatus({ persist: 'none', source: 'device', tags: ['safety', 'jibot'] })
	jibotRotateStatus: string;
```

- [ ] **Step 3c: `VDA5050_L2M_V3_SCHEMA` selector 추가**

`packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts`의 `VDA5050_L2M_V3_SCHEMA` 안, `jibotSystemStatus:` 인접에 추가(기존 selector 패턴 그대로):

```typescript
	jibotSystemErrorCode: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'systemErrorCode'),
	jibotSystemErrorName: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'systemErrorName'),
	jibotSystemErrorDescription: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'systemErrorDescription'),
	jibotWheelLeftStatus: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'wheelLeftStatus'),
	jibotWheelLeftError: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'wheelLeftError'),
	jibotWheelRightStatus: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'wheelRightStatus'),
	jibotWheelRightError: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'wheelRightError'),
	jibotLiftStatus: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'liftStatus'),
	jibotRotateStatus: (data) => getInformationReferenceValue(data, JIBOT_SAFETY_INFO_TYPE, 'rotateStatus'),
```

> `StrictSchema<Vda5050MappedFields, …>`라 3a의 타입 키 9개에 대해 selector가 모두 있어야 컴파일된다(누락 시 TS 에러로 강제). 속성 초기화가 필요하면 기존 jibot* 속성과 동일하게 처리(기존 패턴 따름).

- [ ] **Step 4: 통과 확인**

Run: `cd /home/lab2m-llm1/workspaces/fabris-equipments && npx jest amr-l2m-v3-jibot-safety`
Expected: PASS (기존 + 신규 2)

- [ ] **Step 5: 커밋**

```bash
cd /home/lab2m-llm1/workspaces/fabris-equipments
git add packages/amr/src/lib/amr/amr-base.equipment.ts \
        packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts \
        packages/amr/src/lib/amr/amr-l2m-v3/__tests__/amr-l2m-v3-jibot-safety.spec.ts
git commit -m "feat(amr): expose urobot error/wheel/lift status as EquipmentStatus (v3)"
```

---

# 최종 검증 (전체 회귀)

- [ ] 어댑터: `cd adaptor && PYTHONPATH=. python -m pytest tests/test_jibot_error_catalog.py tests/test_bms_ros_listener_safety_parse.py tests/test_bms_ros_listener_safety_consume.py tests/test_jibot_safety_info.py -v` → 전부 PASS
- [ ] fabris: `cd /home/lab2m-llm1/workspaces/fabris-equipments && npx jest amr-l2m-v3` → 전부 PASS
- [ ] 키 계약 일치 확인: 어댑터 `_refresh_jibot_safety_information`의 InfoReference 키 9개 == fabris `VDA5050_L2M_V3_SCHEMA` selector의 `getInformationReferenceValue(…, '<key>')` 인자 9개.

# 향후 (범위 밖)
노출된 raw 데이터의 errors[] 승격/FATAL·WARNING/errorType/`l_error` ucore 디코드는 사용자가 추후 지정.
