# JIBOT 범퍼 정지 에러 표면화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 범퍼 정지(`bumper Trigger!`)를 FATAL `JIBOT_BUMPER` 에러로 표면화하고, `#disable` 토큰으로 ROS 독립 2차 감지 경로와 stop-reason 전이 로그를 확보한다.

**Architecture:** 기존 `_refresh_jibot_motor_fault_errors`의 purge-then-append 패턴을 그대로 복제해 매 사이클 set/clear가 자동으로 맞도록 한다. `#disable`는 `_is_jibot_lost`와 동일한 토큰 헬퍼 형태로 추가하고 기존 `_refresh_jibot_status_errors` 경로에 얹는다. 프로덕션 코드 변경은 `adapter_jibot.py`와 `vda5050_2_0_0_state.py` 두 파일에 한정된다.

**Tech Stack:** Python 3 / asyncio, `unittest`, VDA5050 2.0.0 state 모델

**선행 문서:** `docs/superpowers/specs/2026-08-12-jibot-bumper-error-surfacing-design.md`

## Global Constraints

- 테스트 실행은 저장소 루트가 아니라 `adaptor/`에서: `cd adaptor && .venv/bin/python -m unittest <module> -v`
- 픽스처는 192.168.101.62 실측 조합을 **그대로** 쓴다 (스펙 §1). 임의 값 금지.
- `bumpe_stop` 플래그는 **분류에 쓰지 않는다** (스펙 §1.4에서 신뢰 불가로 종결). 원시값 전달만.
- 기존 23개 테스트(`test_jibot_stop_reason`, `test_jibot_stop_reason_apply`, `test_jibot_safety_info`)는 계속 통과해야 한다.
- `adaptor/tests/test_adapter_jibot_v3_order.py`는 **건드리지 않는다.** 다른 작업의 미커밋 변경 +363줄이 올라와 있어 충돌 위험이 있다. 신규 테스트는 전용 파일에 쓴다.
- 신규 errorType 토큰은 eq(`fabris-equipments`)와의 계약이다. eq는 모르는 errorType을 무시하므로 어댑터 단독 배포는 안전하다.
- **줄 번호는 `origin/develop` 기준의 참고값이다.** 파일이 크고 자주 바뀌므로 반드시 **심볼 이름으로 찾아서** 수정한다. 줄 번호가 어긋나 있어도 심볼이 있으면 정상이다.

### 이 변경이 바꾸는 외부 관측 동작 (배포 전 eq/ACS 공유 필요)

범퍼 정지 중 `_derive_amr_working_state()` 결과가 바뀐다:

| | 변경 전 | 변경 후 |
|---|---|---|
| `workingState` | `BLOCKED` | **`ERROR`** |
| `detail` | `BRAKE` | **`FAULT`** |

`_derive_amr_working_state`에서 `has_fatal`(1212에서 계산)이 `detail` 분기(1226)와 `working_state` 분기(1238) 양쪽 모두에서 `field_violation and not driving`보다 위에 있기 때문이다. 스펙 §3.2는 detail 변경만 적었으나 `workingState`도 함께 바뀐다. FATAL 선택의 직접적 귀결이며 Task 1에서 테스트로 고정한다.

---

### Task 1: `JIBOT_BUMPER` FATAL 에러 set/clear

**Files:**
- Modify: `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py:55` (ErrorType enum, `JIBOT_ARRIVAL_SIGNAL_MISMATCH` 다음 줄)
- Modify: `adaptor/adapter_jibot.py:2232` (`_refresh_jibot_motor_fault_errors` 뒤에 신규 메서드)
- Modify: `adaptor/adapter_jibot.py:2250` (`_apply_jibot_stop_reason` 두 분기에 호출 추가)
- Test: `adaptor/tests/test_jibot_bumper_errors.py` (신규)

**Interfaces:**
- Consumes: `_derive_jibot_stop_reason()` → `"BUMPER"` 문자열 (기존, 변경 없음), `_stop_reason_harness.make_adapter/start_state` (기존)
- Produces: `ErrorType.JIBOT_BUMPER`, `Adapter._refresh_jibot_bumper_errors(active: bool) -> None` — Task 2/3이 같은 테스트 파일과 같은 메서드를 공유한다

- [ ] **Step 1: 실패하는 테스트 작성**

`adaptor/tests/test_jibot_bumper_errors.py` 신규 생성:

```python
"""JIBOT 범퍼 정지 표면화: FATAL JIBOT_BUMPER + #disable 2차 경로.

픽스처는 192.168.101.62에서 2026-08-12 11:21:58 범퍼 정지 중 실제 기록된 조합이다
(docs/superpowers/specs/2026-08-12-jibot-bumper-error-surfacing-design.md §1).
"""
import asyncio
import time
import unittest

from protocol.vda5050_common import ErrorLevel  # ErrorLevel의 정본은 여기(버전 중립)
from protocol.vda_2_0_0.vda5050_2_0_0_state import ErrorType
from tests._stop_reason_harness import make_adapter, start_state

# st_2026-08-12__10-59-36.txt의 921개 범퍼 행 중 919개가 이 조합이었다.
REAL_BUMPER = {
    "system_status": "bumper Trigger!",
    "system_error_code": "0",
    "motor_enable": "0",
    "bumpe_stop": "1",
    "hmi_estop": "0",
    "motor_error": "0",
    "pc_estop": "0",
    "pc_enable": "1",
}
NORMAL = {
    "system_status": "Normal...",
    "system_error_code": "0",
    "motor_enable": "1",
    "bumpe_stop": "0",
    "hmi_estop": "0",
    "motor_error": "0",
    "pc_estop": "0",
    "pc_enable": "1",
}


def set_safety(adapter, safety):
    """Cache a /jrobot_status safety dict on the vehicle as freshly received."""
    adapter._vehicle._robot_safety = safety
    adapter._vehicle._robot_safety_last_update = time.monotonic()


def bumper_errors(adapter):
    return [
        e for e in adapter.state.errors
        if e.error_type == ErrorType.JIBOT_BUMPER
    ]


class BumperErrorTest(unittest.TestCase):
    def test_bumper_sets_and_clears_fatal_error(self):
        """범퍼 중 FATAL JIBOT_BUMPER 1건, 해제되면 자동 소멸."""

        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                set_safety(adapter, NORMAL)
                await asyncio.sleep(0.08)
                self.assertEqual(bumper_errors(adapter), [])

                set_safety(adapter, REAL_BUMPER)
                await asyncio.sleep(0.08)
                errs = bumper_errors(adapter)
                self.assertEqual(len(errs), 1)
                self.assertEqual(errs[0].error_level, ErrorLevel.FATAL)

                set_safety(adapter, NORMAL)
                await asyncio.sleep(0.08)
                self.assertEqual(bumper_errors(adapter), [])
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_bumper_error_cleared_when_safety_goes_stale(self):
        """범퍼 래치 중 ROS 리스너가 죽어도 에러가 영구 래치되지 않는다.

        reason이 None이 되어 legacy fallback 분기로 빠지는데, 거기서 명시적으로
        정리하지 않으면 FATAL 에러가 영원히 남는다 (스펙 §3.1 주의 항목).
        """

        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                set_safety(adapter, REAL_BUMPER)
                await asyncio.sleep(0.08)
                self.assertEqual(len(bumper_errors(adapter)), 1)

                # 리스너 사망 모사: 마지막 수신 시각을 stale 윈도우 밖으로 밀어낸다.
                adapter._vehicle._robot_safety_last_update = time.monotonic() - 999.0
                await asyncio.sleep(0.08)
                self.assertEqual(bumper_errors(adapter), [])
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_bumper_makes_working_state_error_and_detail_fault(self):
        """FATAL 범퍼 에러가 workingState/detail을 바꾸는 것을 고정한다.

        has_fatal이 working_state(1287)와 detail(1268) 양쪽에서 field_violation
        조건보다 우선이라 BLOCKED/BRAKE가 아니라 ERROR/FAULT가 된다.
        """

        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                adapter._vehicle._status = "Driving"
                set_safety(adapter, REAL_BUMPER)
                await asyncio.sleep(0.08)
                working_state, detail = adapter._derive_amr_working_state()
                self.assertEqual(working_state, "ERROR")
                self.assertEqual(detail, "FAULT")
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_motor_fault_takes_priority_over_bumper(self):
        """우선순위 MOTOR_FAULT > BUMPER: 두 에러가 동시에 뜨지 않는다."""

        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                set_safety(adapter, dict(REAL_BUMPER, motor_error="1"))
                await asyncio.sleep(0.08)
                types = [e.error_type for e in adapter.state.errors]
                self.assertIn(ErrorType.JIBOT_MOTOR_FAULT, types)
                self.assertNotIn(ErrorType.JIBOT_BUMPER, types)
            finally:
                task.cancel()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_errors -v`
Expected: FAIL — `AttributeError: JIBOT_BUMPER` (ErrorType에 해당 멤버 없음)

- [ ] **Step 3: ErrorType에 멤버 추가**

`adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py`의 `JIBOT_ARRIVAL_SIGNAL_MISMATCH` 줄 **바로 다음**에 추가:

```python
    JIBOT_BUMPER = "JIBOT_BUMPER"  # Bumper contact stop reported via /jrobot_status ("bumper Trigger!")
```

- [ ] **Step 4: `_refresh_jibot_bumper_errors` 구현**

`adaptor/adapter_jibot.py`에서 `_refresh_jibot_motor_fault_errors`가 끝나는 지점(`_apply_jibot_stop_reason` 정의 직전)에 추가:

```python
    def _refresh_jibot_bumper_errors(self, active):
        """Set/clear a FATAL JIBOT_BUMPER state error each cycle.

        A bumper stop is physical contact, not an ordinary obstacle wait, so it
        is surfaced as FATAL: eq/ACS then reads workingState=ERROR detail=FAULT
        instead of BLOCKED/BRAKE.
        """
        if self.state is None:
            return
        self.state.errors = [
            e for e in self.state.errors
            if getattr(e, "error_type", None) != ErrorType.JIBOT_BUMPER
        ]
        if active:
            self.state.errors.append(
                Error(
                    error_type=ErrorType.JIBOT_BUMPER,
                    error_level=ErrorLevel.FATAL,
                    error_references=[ErrorReference("reason", "bumperTrigger")],
                    error_description="JIBOT reported a bumper contact stop (bumper Trigger!)",
                )
            )
```

- [ ] **Step 5: `_apply_jibot_stop_reason`에 배선**

같은 파일 `_apply_jibot_stop_reason` 안에서 두 군데를 고친다.

legacy fallback 분기 — `self._refresh_jibot_motor_fault_errors(False)` 바로 다음 줄에 추가:

```python
            self._refresh_jibot_motor_fault_errors(False)
            self._refresh_jibot_bumper_errors(False)
            return
```

메서드 끝 — `self._refresh_jibot_motor_fault_errors(reason == "MOTOR_FAULT")` 바로 다음 줄에 추가:

```python
        self._refresh_jibot_motor_fault_errors(reason == "MOTOR_FAULT")
        self._refresh_jibot_bumper_errors(reason == "BUMPER")
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_errors -v`
Expected: PASS (4 tests)

- [ ] **Step 7: 기존 테스트 회귀 확인**

Run: `cd adaptor && .venv/bin/python -m unittest tests.test_jibot_stop_reason tests.test_jibot_stop_reason_apply tests.test_jibot_safety_info -v`
Expected: PASS (23 tests). 실패하면 detail/workingState 기대값을 가진 기존 테스트가 있다는 뜻이니 위 표에 맞춰 갱신한다.

- [ ] **Step 8: 커밋**

```bash
git add adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py adaptor/adapter_jibot.py adaptor/tests/test_jibot_bumper_errors.py
git commit -m "feat: surface JIBOT bumper stops as a FATAL error"
```

---

### Task 2: `#disable` 토큰 → `JIBOT_MOTOR_DISABLED` WARNING

**Files:**
- Modify: `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` (ErrorType enum, Task 1이 추가한 `JIBOT_BUMPER` 다음 줄)
- Modify: `adaptor/adapter_jibot.py:1050` (`_is_jibot_lost` 뒤에 토큰 헬퍼 추가)
- Modify: `adaptor/adapter_jibot.py:2387` (`_refresh_jibot_status_errors`의 purge 목록 + 발행)
- Test: `adaptor/tests/test_jibot_bumper_errors.py` (Task 1이 만든 파일에 클래스 추가)

**Interfaces:**
- Consumes: `_jibot_text_contains(*needles)` (기존, 1014), `_build_jibot_status_error(error_type, reason, description, error_level=ErrorLevel.WARNING)` (기존, 2438)
- Produces: `ErrorType.JIBOT_MOTOR_DISABLED`, `Adapter._is_jibot_motor_disabled() -> bool`

- [ ] **Step 1: 실패하는 테스트 작성**

`adaptor/tests/test_jibot_bumper_errors.py` 끝의 `if __name__ == "__main__":` **앞에** 클래스를 추가:

```python
class MotorDisabledTokenTest(unittest.TestCase):
    # 범퍼 지속 중 7273 UmGetCurTask가 실제로 보낸 status.
    DISABLED_STATUS = "nrunto pose (13870 -2541 0)#disable#slowdown"
    PLAIN_STATUS = "nrunto pose (13870 -2541 0)#slowdown"

    def test_is_jibot_motor_disabled_reads_the_token(self):
        adapter = make_adapter()
        adapter._vehicle._mode = ""
        adapter._vehicle._status = self.DISABLED_STATUS
        self.assertTrue(adapter._is_jibot_motor_disabled())

        adapter._vehicle._status = self.PLAIN_STATUS
        self.assertFalse(adapter._is_jibot_motor_disabled())

    def test_disable_token_surfaces_warning_and_clears(self):
        """#disable는 ROS 리스너와 무관한 7273 경로라 리스너가 죽어도 뜬다.

        범퍼 전용 신호가 아니라 "모터 꺼짐"만 뜻하므로 WARNING이다.
        """

        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                adapter._vehicle._mode = ""
                adapter._vehicle._status = self.PLAIN_STATUS
                await asyncio.sleep(0.08)
                self.assertFalse(
                    any(
                        e.error_type == ErrorType.JIBOT_MOTOR_DISABLED
                        for e in adapter.state.errors
                    )
                )

                adapter._vehicle._status = self.DISABLED_STATUS
                await asyncio.sleep(0.08)
                errs = [
                    e for e in adapter.state.errors
                    if e.error_type == ErrorType.JIBOT_MOTOR_DISABLED
                ]
                self.assertEqual(len(errs), 1)
                self.assertEqual(errs[0].error_level, ErrorLevel.WARNING)

                adapter._vehicle._status = self.PLAIN_STATUS
                await asyncio.sleep(0.08)
                self.assertFalse(
                    any(
                        e.error_type == ErrorType.JIBOT_MOTOR_DISABLED
                        for e in adapter.state.errors
                    )
                )
            finally:
                task.cancel()

        asyncio.run(scenario())
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_errors.MotorDisabledTokenTest -v`
Expected: FAIL — `AttributeError: JIBOT_MOTOR_DISABLED`

- [ ] **Step 3: ErrorType에 멤버 추가**

Task 1이 추가한 `JIBOT_BUMPER` 줄 바로 다음:

```python
    JIBOT_MOTOR_DISABLED = "JIBOT_MOTOR_DISABLED"  # JIBOT task status reports the motor disabled ("#disable")
```

- [ ] **Step 4: 토큰 헬퍼 구현**

`adaptor/adapter_jibot.py`에서 `_is_jibot_lost` 메서드가 끝난 직후에 추가:

```python
    def _is_jibot_motor_disabled(self) -> bool:
        """True when JIBOT's own task status reports the motor disabled ("#disable").

        Arrives over TCP 7273, so this still works when the ROS listener is down
        and the /jrobot_status stop-reason subdivision is unavailable. Says only
        that the motor is off — not why — so callers must not read it as a bumper.
        """
        return self._jibot_text_contains("#disable", " disable")
```

- [ ] **Step 5: `_refresh_jibot_status_errors`에 배선**

purge 목록에 한 줄 추가:

```python
            not in (
                ErrorType.JIBOT_CONFLICT,
                ErrorType.JIBOT_AVOIDANCE,
                ErrorType.JIBOT_LOCALIZATION_LOST,
                ErrorType.JIBOT_MOTOR_DISABLED,
            )
```

그리고 같은 메서드 안, `_is_jibot_lost()` 블록 **다음에** 발행 블록 추가:

```python
        # "#disable" means JIBOT itself considers the motor off. Unlike the
        # /jrobot_status subdivision this arrives over TCP 7273, so it survives a
        # dead ROS listener. WARNING (not FATAL): it does not identify the cause,
        # and a routine manual disable raises it too.
        if self._is_jibot_motor_disabled():
            self.state.errors.append(
                self._build_jibot_status_error(
                    ErrorType.JIBOT_MOTOR_DISABLED,
                    "disable",
                    "JIBOT reported the drive motor disabled (#disable)",
                )
            )
```

> 기존 `JIBOT_AVOIDANCE` 발행은 **그대로 둔다.** `#slowdown`이 실제로 함께 떠 있었으므로 보고 자체가 거짓은 아니고, 이번 변경 목적은 신호 추가이지 기존 경로 수정이 아니다 (스펙 §3.3).

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_errors -v`
Expected: PASS (6 tests)

- [ ] **Step 7: 커밋**

```bash
git add adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py adaptor/adapter_jibot.py adaptor/tests/test_jibot_bumper_errors.py
git commit -m "feat: surface the JIBOT #disable token as a motor-disabled warning"
```

---

### Task 3: stop reason 전이 로그

**Files:**
- Modify: `adaptor/adapter_jibot.py:2257` (`_apply_jibot_stop_reason`의 `self._jibot_stop_reason = reason` 캐시 갱신 줄)
- Test: `adaptor/tests/test_jibot_bumper_errors.py` (클래스 추가)

**Interfaces:**
- Consumes: `Adapter._jibot_stop_reason` (기존 캐시 필드, 348에서 `None`으로 초기화)
- Produces: 없음 (stdout 부수효과만)

- [ ] **Step 1: 실패하는 테스트 작성**

`adaptor/tests/test_jibot_bumper_errors.py`의 `if __name__ == "__main__":` **앞에** 추가. 파일 상단 import에 `import io`와 `from contextlib import redirect_stdout`을 함께 넣는다:

```python
class StopReasonLogTest(unittest.TestCase):
    """publish 루프가 ~3Hz라 매 사이클 출력하면 journal이 도배된다.

    변화 시에만 남겨야 한다. 이번 조사에서 journal 전체에 bumper가 0건이라
    로봇 자체 기록까지 뒤져야 시각을 특정할 수 있었다.
    """

    def _idle_adapter(self):
        """상태만 만들고 publish 루프는 멈춘 어댑터.

        루프가 계속 돌면 우리가 직접 호출한 출력과 섞여 캡처가 불안정해진다.
        """

        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return adapter

        return asyncio.run(scenario())

    def _apply_capturing(self, adapter):
        buf = io.StringIO()
        with redirect_stdout(buf):
            adapter._apply_jibot_stop_reason()
        return buf.getvalue()

    def test_logs_only_on_transition(self):
        adapter = self._idle_adapter()

        set_safety(adapter, NORMAL)
        self.assertIn("[JIBOT STOP REASON] None -> NONE", self._apply_capturing(adapter))

        # 같은 사유가 반복되면 아무것도 남기지 않는다.
        self.assertEqual(self._apply_capturing(adapter).strip(), "")

        set_safety(adapter, REAL_BUMPER)
        self.assertIn("[JIBOT STOP REASON] NONE -> BUMPER", self._apply_capturing(adapter))

        self.assertEqual(self._apply_capturing(adapter).strip(), "")
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_errors.StopReasonLogTest -v`
Expected: FAIL — 캡처된 출력이 비어 있어 `assertIn`이 실패

- [ ] **Step 3: 전이 로그 구현**

`adaptor/adapter_jibot.py` `_apply_jibot_stop_reason` 안에서 아래 한 줄을

```python
        self._jibot_stop_reason = reason  # cached for operatingMode + info block
```

다음으로 교체한다:

```python
        # Log on transition only: the publish loop runs ~3Hz, so logging every
        # cycle would flood the journal. Before this, a bumper stop left no trace
        # in the journal at all and had to be reconstructed from the robot's own
        # /usr/local/urobot/logs/bot_log recording.
        if reason != self._jibot_stop_reason:
            print(f"[JIBOT STOP REASON] {self._jibot_stop_reason} -> {reason}")
        self._jibot_stop_reason = reason  # cached for operatingMode + info block
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_errors -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 전체 회귀 확인**

Run: `cd adaptor && .venv/bin/python -m unittest tests.test_jibot_bumper_errors tests.test_jibot_stop_reason tests.test_jibot_stop_reason_apply tests.test_jibot_safety_info -v`
Expected: PASS (30 tests)

- [ ] **Step 6: 커밋**

```bash
git add adaptor/adapter_jibot.py adaptor/tests/test_jibot_bumper_errors.py
git commit -m "feat: log JIBOT stop-reason transitions"
```

---

## 배포 후 확인 (수동, 로봇 필요)

구현 후 192.168.101.62에 배포한다면 범퍼를 물리적으로 눌러 아래를 확인한다. 자동 테스트로는 검증 불가한 항목이다.

```bash
ssh ucore@192.168.101.62 'journalctl -u amr-adaptor -f | grep "JIBOT STOP REASON"'
# 기대: "[JIBOT STOP REASON] NONE -> BUMPER" → 해제 시 "... -> MANUAL" → "... -> NONE"
```

`sudo`는 비밀번호를 요구하므로 쓰지 않는다 (`ucore`가 `systemd-journal` 그룹이라 그냥 읽힌다).

## 미해결 (이 계획 범위 밖)

- **범퍼 래치 중 `UmDrive` 송신 여부 미확인.** 스펙 §7.4. 확인하려던 중 로봇이 네트워크에서 떨어졌다. 래치 중에도 주행 명령을 계속 밀어넣고 있었다면 별도 조사·수정이 필요하다.
- **eq 쪽 `JIBOT_BUMPER` 렌더링/대응** — 별도 티켓.
- **`workingState` BLOCKED → ERROR 전환의 ACS 정책 영향** — 배포 전 협의 필요.
