# Clamp servo auto ON → move → OFF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 클램프 이동 액션이 servo가 꺼져 있어도 한 번에 끝까지 수행되도록(ON 확인 → 이동 → 이동 완료 확인 → OFF) 고치고, 자동 OFF를 기본 정책으로 만든다.

**Architecture:** `adaptor/extensions/clamp/__init__.py`의 servo 헬퍼 두 개를 "ACK만 믿는" 방식에서 "`get_axis_status()` 플래그를 확인하는" 방식으로 교체한다. servo ON은 `FFLAG_SERVOON`, 이동 완료는 `FFLAG_MOTIONING`으로 판정하고 각각 타임아웃을 둔다. 타임아웃 값은 `EziConfig`에 새 필드로 추가하고, 폴링 주기는 기존 `ezi_motor_poll_interval_sec`를 재사용한다.

**Tech Stack:** Python 3.11+ / asyncio / dataclasses / unittest(+pytest runner) / python-hcl2

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-07-29-clamp-servo-auto-cycle-design.md`. 충돌 시 설계 문서가 이긴다.
- 테스트 실행은 항상 `adaptor/` 디렉터리에서: `cd adaptor && uv run pytest -q`. `pyproject.toml`의 `[tool.pytest.ini_options]`가 `testpaths = ["tests"]`, `pythonpath = ["."]`로 잡혀 있어 다른 위치에서 돌리면 import가 깨진다.
- `EziConfig`는 `config.py:646`에서 `EziConfig(**config_dict["ezi"])`로 조립된다. HCL `extension "ezi"` 블록에 키를 추가하려면 **먼저** 같은 이름의 dataclass 필드가 있어야 한다. 순서를 지키지 않으면 부팅이 `TypeError`로 죽는다.
- `clamp_servo_policy`의 유효값은 `auto_on_keep_on`, `auto_on_auto_off`, `manual` 세 개뿐이다. 새 값을 추가하지 않는다.
- 리포 루트의 `config.toml` / `extensions.hcl` / `robots.hcl`은 git에 없는 로컬 실행용 파일이다. 절대 수정하지 않는다. 수정 대상은 `adaptor/config/` 아래 파일뿐이다.
- 주석과 문서 문자열은 기존 파일의 관례를 따른다(이 리포는 한국어 주석 + 영어 식별자).
- 각 Task는 테스트가 실패하는 것을 눈으로 확인한 뒤 구현하고, 마지막에 커밋한다.

## File Structure

| 파일 | 역할 | 변경 |
|---|---|---|
| `adaptor/config/config.py` | `EziConfig` dataclass — 타임아웃/정책 기본값의 단일 출처 | 수정 (Task 1) |
| `adaptor/tests/test_config.py` | config 기본값 계약 | 수정 (Task 1) |
| `adaptor/tests/test_adapter_jibot_v3_order.py` | `FakeClampMotor` + 클램프 액션 동작 테스트 | 수정 (Task 2~5) |
| `adaptor/extensions/clamp/__init__.py` | servo 게이트 헬퍼 + 액션 실행 흐름 | 수정 (Task 3~5) |
| `adaptor/config/extensions.hcl` | 실제 로딩되는 기본 설정 | 수정 (Task 6) |
| `adaptor/config/extensions.hcl.example` | 운영자용 설정 예시/주석 | 수정 (Task 6) |

Task 1이 설정 필드를, Task 2가 테스트 더블을 먼저 깔고, Task 3~5가 그 위에서 동작을 바꾼다. Task 6은 설정 파일 값·주석 정리다.

---

### Task 1: `EziConfig`에 타임아웃 필드 추가 + 기본 정책 전환

**Files:**
- Modify: `adaptor/config/config.py:39` (`clamp_servo_policy` 기본값), `adaptor/config/config.py:55` 부근 (필드 3개 추가)
- Test: `adaptor/tests/test_config.py:351-358` (기존 `test_clamp_servo_policy_default` 수정) + 신규 테스트 1개

**Interfaces:**
- Consumes: 없음 (첫 Task)
- Produces: `EziConfig` 필드 4개 —
  - `clamp_servo_policy: str = "auto_on_auto_off"`
  - `clamp_servo_on_timeout_sec: float = 3.0`
  - `clamp_motion_start_timeout_sec: float = 1.0`
  - `clamp_motion_timeout_sec: float = 30.0`

- [ ] **Step 1: 기존 기본값 테스트를 새 기대값으로 바꾸고, 타임아웃 기본값 테스트를 추가**

`adaptor/tests/test_config.py`에서 기존 `test_clamp_servo_policy_default`를 아래로 **교체**한다(함수 이름은 그대로 두고 본문만 바꾼다):

```python
def test_clamp_servo_policy_default():
    from config.config import get_config, EziConfig

    assert (
        EziConfig.__dataclass_fields__["clamp_servo_policy"].default
        == "auto_on_auto_off"
    )
    assert get_config().ezi_config.clamp_servo_policy == "auto_on_auto_off"


def test_clamp_servo_timeout_defaults():
    """clamp servo 게이트가 쓰는 대기 한계값의 기본값 계약."""
    from config.config import get_config, EziConfig

    fields = EziConfig.__dataclass_fields__
    assert fields["clamp_servo_on_timeout_sec"].default == 3.0
    assert fields["clamp_motion_start_timeout_sec"].default == 1.0
    assert fields["clamp_motion_timeout_sec"].default == 30.0

    ec = get_config().ezi_config
    assert ec.clamp_servo_on_timeout_sec == 3.0
    assert ec.clamp_motion_start_timeout_sec == 1.0
    assert ec.clamp_motion_timeout_sec == 30.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd adaptor && uv run pytest tests/test_config.py::test_clamp_servo_policy_default tests/test_config.py::test_clamp_servo_timeout_defaults -q
```

Expected: FAIL — `test_clamp_servo_policy_default`는 `'auto_on_keep_on' != 'auto_on_auto_off'`로, `test_clamp_servo_timeout_defaults`는 `KeyError: 'clamp_servo_on_timeout_sec'`로 깨진다.

> `get_config()` 단언까지 통과하려면 Task 6에서 `extensions.hcl`도 고쳐야 한다. 이 Task 안에서 `extensions.hcl`의 `clamp_servo_policy` 값 한 줄만 먼저 `"auto_on_auto_off"`로 바꿔 테스트를 초록으로 만든다(주석 정리는 Task 6에서 마저 한다).

- [ ] **Step 3: `EziConfig` 필드 수정 및 추가**

`adaptor/config/config.py`에서 `clamp_servo_policy` 줄을 바꾸고 그 아래에 타임아웃 3개를 추가한다:

```python
    # servo 정책 기본값은 "동작이 끝나면 항상 OFF"다. EZI 드라이브는 servo가 실제로
    # 여자되기 전에 도착한 move를 조용히 버리므로, 정책과 무관하게 클램프 이동은
    # FFLAG_SERVOON / FFLAG_MOTIONING을 확인하며 진행한다(extensions/clamp 참고).
    clamp_servo_policy: str = "auto_on_auto_off"
    clamp_servo_on_timeout_sec: float = 3.0        # servo ON 플래그 대기 한계
    clamp_motion_start_timeout_sec: float = 1.0    # 이동 시작 대기 한계(초과해도 실패 아님)
    clamp_motion_timeout_sec: float = 30.0         # 이동 완료 대기 한계
```

`adaptor/config/extensions.hcl`의 `clamp_servo_policy = "auto_on_keep_on"`을 `clamp_servo_policy = "auto_on_auto_off"`로 바꾼다.

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd adaptor && uv run pytest tests/test_config.py -q
```

Expected: PASS (전체 `test_config.py`가 초록)

- [ ] **Step 5: 커밋**

```bash
git add adaptor/config/config.py adaptor/config/extensions.hcl adaptor/tests/test_config.py
git commit -m "feat(clamp): default the servo policy to auto-off and add wait limits"
```

---

### Task 2: `FakeClampMotor`에 축 상태 대본 주입

**Files:**
- Modify: `adaptor/tests/test_adapter_jibot_v3_order.py:274-310` (`FakeClampMotor`)

**Interfaces:**
- Consumes: 없음
- Produces: 테스트 더블 API —
  - `FakeClampMotor(move_comm_status=0, move_returns_none=False, servo_on=False, axis_status_script=None)`
  - `axis_status_script`: `dict` 리스트. 각 원소는 `{"servo_on": bool, "motioning": bool}` 형태이며 `get_axis_status()` 호출마다 앞에서부터 하나씩 소비된다. 소진되면 **마지막 원소를 계속 반환**한다.
  - `axis_status_script=None`이면 `servo_enable(enable)` 호출이 내부 `servo_on` 상태를 즉시 갱신하고, `motioning`은 항상 `False`다(= 이동이 즉시 끝나는 기본 더블).
  - `get_axis_status()` 호출도 `self.calls`에 `("get_axis_status",)`로 기록된다.
  - `axis_status_none=True`면 `get_axis_status()`가 `None`을 반환한다(무응답 모사).

- [ ] **Step 1: 더블 자체를 검증하는 테스트를 추가**

`adaptor/tests/test_adapter_jibot_v3_order.py`의 클램프 테스트 클래스 안(기존 `test_clamp_servo_policy_manual_does_not_toggle_servo_for_move` 바로 앞)에 추가한다:

```python
    def test_fake_clamp_motor_axis_status_script_holds_last_entry(self) -> None:
        # 테스트 더블 계약: 대본을 앞에서부터 소비하고, 소진되면 마지막 상태를 유지한다.
        async def scenario() -> None:
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": False, "motioning": False},
                    {"servo_on": True, "motioning": True},
                    {"servo_on": True, "motioning": False},
                ]
            )

            seen = [
                (
                    (await motor.get_axis_status())["active_flags"]["FFLAG_SERVOON"],
                    (await motor.get_axis_status())["active_flags"]["FFLAG_MOTIONING"],
                )
            ]
            self.assertEqual(seen[0], (False, True))

            last = await motor.get_axis_status()
            self.assertEqual(last["active_flags"]["FFLAG_SERVOON"], True)
            self.assertEqual(last["active_flags"]["FFLAG_MOTIONING"], False)

            again = await motor.get_axis_status()
            self.assertEqual(again["active_flags"]["FFLAG_MOTIONING"], False)
            self.assertEqual(again["communication_status"], 0)

        asyncio.run(scenario())

    def test_fake_clamp_motor_tracks_servo_enable_without_script(self) -> None:
        async def scenario() -> None:
            motor = FakeClampMotor()
            status = await motor.get_axis_status()
            self.assertFalse(status["active_flags"]["FFLAG_SERVOON"])

            await motor.servo_enable(True)
            status = await motor.get_axis_status()
            self.assertTrue(status["active_flags"]["FFLAG_SERVOON"])
            self.assertFalse(status["active_flags"]["FFLAG_MOTIONING"])

        asyncio.run(scenario())
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -k fake_clamp_motor -q
```

Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'axis_status_script'`

- [ ] **Step 3: `FakeClampMotor`를 확장**

`adaptor/tests/test_adapter_jibot_v3_order.py`의 `FakeClampMotor` 클래스에서 `__init__`을 아래로 바꾸고, `servo_enable`을 바꾸고, `get_axis_status`를 새로 추가한다(나머지 메서드는 그대로 둔다):

```python
class FakeClampMotor:
    def __init__(
        self,
        move_comm_status=0,
        move_returns_none=False,
        servo_on=False,
        axis_status_script=None,
        axis_status_none=False,
    ) -> None:
        self.calls = []
        # Model the EZI drive's per-command result: 0 = accepted, nonzero =
        # rejected (servo off / alarm / ...), None return = comms timeout.
        self._move_comm_status = move_comm_status
        self._move_returns_none = move_returns_none
        # 축 상태 대본. 한 번에 하나씩 소비하고 소진되면 마지막 상태를 유지한다.
        # None이면 servo_enable() 호출이 곧바로 반영되고 이동은 즉시 끝난 것으로 본다.
        self._axis_status_script = list(axis_status_script or [])
        self._axis_status_none = axis_status_none
        self._servo_on = servo_on

    async def servo_enable(self, enable=True):
        self.calls.append(("servo_enable", enable))
        if not self._axis_status_script:
            self._servo_on = bool(enable)
        return {"communication_status": 0}

    async def get_axis_status(self):
        self.calls.append(("get_axis_status",))
        if self._axis_status_none:
            return None
        if self._axis_status_script:
            entry = (
                self._axis_status_script.pop(0)
                if len(self._axis_status_script) > 1
                else self._axis_status_script[0]
            )
        else:
            entry = {"servo_on": self._servo_on, "motioning": False}
        flags = {name: False for name in EziMotorClient.AXIS_FLAGS}
        flags["FFLAG_SERVOON"] = bool(entry.get("servo_on"))
        flags["FFLAG_MOTIONING"] = bool(entry.get("motioning"))
        return {"communication_status": 0, "active_flags": flags}
```

`EziMotorClient`는 이 테스트 모듈에 아직 import되어 있지 않다. 파일 상단 import 블록의
`from protocol.vda_2_0_0.vda5050_2_0_0_state import (...)` 블록 바로 뒤에 추가한다:

```python
from utils.ezi_motor import EziMotorClient
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -k fake_clamp_motor -q
```

Expected: PASS (2 passed)

- [ ] **Step 5: 기존 클램프 테스트가 안 깨졌는지 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -q
```

Expected: PASS. 만약 `test_clamp_servo_policy_auto_off_disables_servo_after_move`가 깨지면 Task 1에서 정책 기본값을 바꾼 영향이니, 여기서는 고치지 말고 Task 4에서 처리한다 — 실패 목록을 메모해 둔다.

- [ ] **Step 6: 커밋**

```bash
git add adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "test(clamp): let the fake motor script axis-status flags"
```

---

### Task 3: servo ON을 플래그로 확인한 뒤 move를 보낸다

**Files:**
- Modify: `adaptor/extensions/clamp/__init__.py:99-122` (`_enable_servo_for_motion` / `_disable_servo_after_motion`)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py` (클램프 테스트 클래스)

**Interfaces:**
- Consumes: Task 1의 `EziConfig.clamp_servo_on_timeout_sec`, Task 2의 `FakeClampMotor(servo_on=..., axis_status_script=..., axis_status_none=...)`
- Produces:
  - `async def _servo_on_and_wait(adapter, action_type) -> bool` — servo가 켜졌음을 확인하고, "동작 후 servo를 꺼야 하는지"(`policy == "auto_on_auto_off"`)를 반환한다. `manual`이면 아무 호출도 하지 않고 `False`를 반환한다.
  - `async def _axis_flags(adapter, action_type) -> dict` — `get_axis_status()`를 호출해 `active_flags`를 돌려주고, 무응답이면 `RuntimeError`를 던진다.
  - `_disable_servo_after_motion(adapter, action_type, enabled)`는 시그니처 그대로 유지한다.
  - 이 Task에서는 아직 이동 완료를 기다리지 않는다(Task 4에서 추가).

- [ ] **Step 1: 실패하는 테스트를 추가**

기존 `test_clamp_action_enables_servo_before_moving` 바로 뒤에 추가한다:

```python
    def test_clamp_waits_for_servo_flag_before_moving(self) -> None:
        # 드라이브가 servo ON을 보고하기 전에 도착한 move는 조용히 버려진다.
        # servo_enable ACK만 믿지 말고 FFLAG_SERVOON이 설 때까지 기다려야 한다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            # Task 4에서 이동 완료 대기가 붙어도 테스트가 느려지지 않게 미리 줄여 둔다.
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": False, "motioning": False},
                    {"servo_on": False, "motioning": False},
                    {"servo_on": True, "motioning": False},
                ]
            )
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            types = [call[0] for call in motor.calls]
            # servo_enable 뒤에 상태 폴링이 있고, 그 뒤에야 move가 나간다.
            self.assertLess(types.index("servo_enable"), types.index("move_single_axis_abs_pos"))
            polls_before_move = [
                index
                for index, name in enumerate(types)
                if name == "get_axis_status"
                and index > types.index("servo_enable")
                and index < types.index("move_single_axis_abs_pos")
            ]
            self.assertGreaterEqual(len(polls_before_move), 2)

        asyncio.run(scenario())

    def test_clamp_skips_servo_enable_when_already_on(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
            motor = FakeClampMotor(servo_on=True)
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertNotIn(("servo_enable", True), motor.calls)
            self.assertIn(
                ("move_single_axis_abs_pos", 8500, adapter.config.ezi_config.motor_speed),
                motor.calls,
            )

        asyncio.run(scenario())

    def test_clamp_fails_without_moving_when_servo_never_turns_on(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.clamp_servo_on_timeout_sec = 0.2
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(axis_status_script=[{"servo_on": False, "motioning": False}])
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            types = [call[0] for call in motor.calls]
            self.assertNotIn("move_single_axis_abs_pos", types)

        asyncio.run(scenario())

    def test_clamp_fails_when_axis_status_is_unreachable(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            motor = FakeClampMotor(axis_status_none=True)
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            self.assertNotIn(
                "move_single_axis_abs_pos", [call[0] for call in motor.calls]
            )

        asyncio.run(scenario())
```

이어서 기존 `test_clamp_servo_policy_manual_does_not_toggle_servo_for_move`에 단언을 하나
추가한다. `manual`은 servo를 켜지 않을 뿐 아니라 **상태 조회조차 하지 않아야** 한다.
기존 두 `assertNotIn` 아래에 넣는다:

```python
            self.assertNotIn("get_axis_status", [call[0] for call in motor.calls])
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -k "waits_for_servo_flag or skips_servo_enable or servo_never_turns_on or axis_status_is_unreachable or policy_manual" -q
```

Expected: FAIL — 지금 코드는 상태를 조회하지 않으므로 `ValueError: substring not found`(`types.index("get_axis_status")` 없음) 또는 `AssertionError`가 난다.

- [ ] **Step 3: 헬퍼 구현**

`adaptor/extensions/clamp/__init__.py`에서 `import asyncio`를 추가하고(파일 상단 `import json` 위), `_enable_servo_for_motion`을 아래 두 함수로 교체한다. `_disable_servo_after_motion`은 그대로 둔다.

```python
async def _axis_flags(adapter: Any, action_type: str) -> Dict[str, bool]:
    """축 상태 플래그를 읽고, 무응답이면 실행 오류로 올린다."""
    status = await adapter._ezi_motor.get_axis_status()
    if not status or "active_flags" not in status:
        raise RuntimeError(
            f"{action_type} axis status: no response from EZI motor (timeout)"
        )
    return status["active_flags"]


async def _servo_on_and_wait(adapter: Any, action_type: str) -> bool:
    """servo가 실제로 켜진 것을 확인한 뒤 반환한다.

    EZI 드라이브는 servo가 여자되기 전에 도착한 move를 조용히 버린다. ACK만 믿고
    바로 move를 보내면 첫 실행이 "servo만 켜고 끝"으로 보인다.

    Returns:
        동작 후 servo를 꺼야 하는지 여부(policy가 auto_on_auto_off인지).
    """
    policy = _clamp_servo_policy(adapter)
    if policy == "manual":
        return False

    ezi = adapter.config.ezi_config
    poll = float(getattr(ezi, "ezi_motor_poll_interval_sec", 0.1))
    timeout = float(getattr(ezi, "clamp_servo_on_timeout_sec", 3.0))
    auto_off = policy == "auto_on_auto_off"

    if (await _axis_flags(adapter, action_type))["FFLAG_SERVOON"]:
        return auto_off

    _raise_if_motor_rejected(
        await adapter._ezi_motor.servo_enable(True),
        f"{action_type} servo_enable",
    )

    waited = 0.0
    while True:
        if (await _axis_flags(adapter, action_type))["FFLAG_SERVOON"]:
            return auto_off
        if waited >= timeout:
            raise RuntimeError(
                f"{action_type}: servo did not turn on within {timeout}s"
            )
        await asyncio.sleep(poll)
        waited += poll
```

이어서 이동 액션 5곳의 호출부를 바꾼다. `execute_clamp_action` 안의
`auto_off = await _enable_servo_for_motion(adapter, action.action_type)`를 모두
`auto_off = await _servo_on_and_wait(adapter, action.action_type)`로 바꾼다
(`clamp`/`unclamp` 분기, `clampMin`/`clampMax`/`clampHome` 분기, `clampMoveTo` 분기 — 총 3군데).

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -k "clamp" -q
```

Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add adaptor/extensions/clamp/__init__.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "fix(clamp): confirm the servo is energised before issuing a move"
```

---

### Task 4: 이동이 끝난 뒤에 servo를 끈다

**Files:**
- Modify: `adaptor/extensions/clamp/__init__.py` (`_wait_motion_done` 추가 + 이동 분기 3군데)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: Task 1의 `clamp_motion_start_timeout_sec` / `clamp_motion_timeout_sec`, Task 3의 `_axis_flags`
- Produces: `async def _wait_motion_done(adapter, action_type) -> None` — move ACK 직후에 호출한다. `FFLAG_MOTIONING`이 설 때까지 시작 대기(초과해도 성공으로 통과), 그 뒤 내려갈 때까지 완료 대기(초과하면 `move_stop()` 후 `RuntimeError`).

- [ ] **Step 1: 실패하는 테스트를 추가**

```python
    def test_clamp_disables_servo_only_after_motion_completes(self) -> None:
        # auto_on_auto_off가 move ACK 직후 servo를 끊으면 이동이 중간에 죽는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            motor = FakeClampMotor(
                axis_status_script=[
                    {"servo_on": False, "motioning": False},   # 최초 확인
                    {"servo_on": True, "motioning": False},    # servo ON 확인
                    {"servo_on": True, "motioning": True},     # 이동 시작
                    {"servo_on": True, "motioning": True},     # 이동 중
                    {"servo_on": True, "motioning": False},    # 이동 완료
                ]
            )
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clamp", params={"position": "1234"}).actions[0],
            )

            types = [call[0] for call in motor.calls]
            move_at = types.index("move_single_axis_abs_pos")
            off_at = motor.calls.index(("servo_enable", False))
            # move 이후 servo OFF 이전에 상태 폴링이 있어야 한다 = 완료를 기다렸다.
            polls_between = [
                index
                for index, name in enumerate(types)
                if name == "get_axis_status" and move_at < index < off_at
            ]
            self.assertGreaterEqual(len(polls_between), 2)
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_passes_when_motion_never_starts(self) -> None:
        # 이미 목표 위치면 MOTIONING이 한 번도 서지 않는다. 실패가 아니다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            motor = FakeClampMotor(servo_on=True)
            adapter.set_ezi_motor(motor)

            description = await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertIn("clampMoveTo finished", description)
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_stops_and_fails_when_motion_never_ends(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.05
            adapter.config.ezi_config.clamp_motion_timeout_sec = 0.1
            motor = FakeClampMotor(
                axis_status_script=[{"servo_on": True, "motioning": True}]
            )
            adapter.set_ezi_motor(motor)

            with self.assertRaises(RuntimeError):
                await execute_clamp_action(
                    adapter,
                    make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
                )

            types = [call[0] for call in motor.calls]
            self.assertIn("move_stop", types)
            # 실패해도 servo는 반드시 내려간다.
            self.assertEqual(motor.calls[-1], ("servo_enable", False))
            self.assertLess(types.index("move_stop"), len(types) - 1)

        asyncio.run(scenario())

    def test_clamp_keep_on_policy_leaves_servo_energised(self) -> None:
        # auto_on_keep_on은 이동 완료를 기다리되 servo를 끄지 않는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_keep_on"
            adapter.config.ezi_config.ezi_motor_poll_interval_sec = 0.01
            adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.02
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampMoveTo", params={"position": "8500"}).actions[0],
            )

            self.assertIn(("servo_enable", True), motor.calls)
            self.assertNotIn(("servo_enable", False), motor.calls)

        asyncio.run(scenario())
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -k "after_motion_completes or motion_never_starts or motion_never_ends or keep_on_policy" -q
```

Expected: FAIL — 지금은 move 직후 곧바로 `servo_enable(False)`라 `polls_between`이 비고, `move_stop` 호출도 없다.

- [ ] **Step 3: `_wait_motion_done` 구현 및 연결**

`adaptor/extensions/clamp/__init__.py`의 `_servo_on_and_wait` 아래에 추가한다:

```python
async def _wait_motion_done(adapter: Any, action_type: str) -> None:
    """move 명령이 받아들여진 뒤 실제 이동이 끝날 때까지 기다린다.

    FFLAG_INPOSITION은 move 직후에도 *이전* 위치 기준으로 서 있을 수 있어 완료 판정에
    쓰지 않는다. FFLAG_MOTIONING이 서고 내려가는 것으로 판정하면 리미트 이동이나 원점
    복귀처럼 목표 좌표를 모르는 동작에도 그대로 통한다.
    """
    ezi = adapter.config.ezi_config
    poll = float(getattr(ezi, "ezi_motor_poll_interval_sec", 0.1))
    start_timeout = float(getattr(ezi, "clamp_motion_start_timeout_sec", 1.0))
    move_timeout = float(getattr(ezi, "clamp_motion_timeout_sec", 30.0))

    # 시작 대기: 끝내 안 움직이면 이미 목표 위치로 보고 성공 처리한다.
    waited = 0.0
    started = False
    while waited < start_timeout:
        if (await _axis_flags(adapter, action_type))["FFLAG_MOTIONING"]:
            started = True
            break
        await asyncio.sleep(poll)
        waited += poll
    if not started:
        return

    # 완료 대기: 초과하면 모터를 세우고 실패로 올린다.
    waited = 0.0
    while True:
        if not (await _axis_flags(adapter, action_type))["FFLAG_MOTIONING"]:
            return
        if waited >= move_timeout:
            await adapter._ezi_motor.move_stop()
            raise RuntimeError(
                f"{action_type}: motion did not finish within {move_timeout}s"
            )
        await asyncio.sleep(poll)
        waited += poll
```

그리고 `execute_clamp_action`의 이동 분기 3군데에서, `_raise_if_motor_rejected(...)`로 move ACK를 확인한 **직후** `await _wait_motion_done(adapter, action.action_type)`를 넣는다. 대상은 아래 5개 호출 지점이다.

- `clamp` / `unclamp`의 `move_single_axis_abs_pos`
- `clampMin`/`clampMax`/`clampHome` 분기의 `move_single_axis_abs_pos`(설정 위치가 있을 때)
- 같은 분기의 `goto_limit_minus`, `goto_limit_plus`, `goto_origin`

예를 들어 `clamp`/`unclamp` 분기는 이렇게 된다:

```python
        auto_off = await _servo_on_and_wait(adapter, action.action_type)
        try:
            _raise_if_motor_rejected(
                await adapter._ezi_motor.move_single_axis_abs_pos(position, speed),
                f"{action.action_type} move",
            )
            await _wait_motion_done(adapter, action.action_type)
            return f"{action.action_type} finished: position={position} speed={speed}"
        finally:
            await _disable_servo_after_motion(adapter, action.action_type, auto_off)
```

`clampMin`/`clampMax`/`clampHome` 분기도 같은 방식으로, 각 `_raise_if_motor_rejected(...)` 바로 뒤·`return` 바로 앞에 `await _wait_motion_done(adapter, action.action_type)`를 넣는다.

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -k "clamp" -q
```

Expected: PASS. 기존 `test_clamp_servo_policy_auto_off_disables_servo_after_move` / `..._after_failed_move` / `test_clamp_action_moves_to_configured_position` 도 함께 초록이어야 한다. `FakeClampMotor`의 기본 더블은 `motioning=False`라 시작 대기 후 곧바로 통과하는데, `clamp_motion_start_timeout_sec` 기본값 1.0초 × 폴링 0.1초 때문에 각 테스트가 1초씩 걸린다. 기존 테스트가 느려지면 해당 테스트에 `adapter.config.ezi_config.clamp_motion_start_timeout_sec = 0.01`과 `ezi_motor_poll_interval_sec = 0.005`를 넣어 줄인다.

- [ ] **Step 5: 전체 테스트 확인**

```bash
cd adaptor && uv run pytest -q
```

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add adaptor/extensions/clamp/__init__.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "fix(clamp): wait for the motion to finish before cutting the servo"
```

---

### Task 5: `clampTeach`도 servo 사이클로 감싼다

**Files:**
- Modify: `adaptor/extensions/clamp/__init__.py:224-228` (`clampTeach` 분기)
- Test: `adaptor/tests/test_adapter_jibot_v3_order.py`

**Interfaces:**
- Consumes: Task 3의 `_servo_on_and_wait`, 기존 `_disable_servo_after_motion`
- Produces: 없음 (기존 함수 재사용)

- [ ] **Step 1: 실패하는 테스트를 추가**

```python
    def test_clamp_teach_runs_inside_the_servo_cycle(self) -> None:
        # teach는 원점 탐색으로 모터를 실제로 움직인다. servo가 꺼져 있으면 무반응이다.
        # 자체 폴링 루프를 갖고 있으므로 이동 완료 대기는 걸지 않는다.
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "auto_on_auto_off"
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampTeach").actions[0],
            )

            types = [call[0] for call in motor.calls]
            self.assertLess(
                types.index("servo_enable"),
                types.index("initialized_open_close_encoder_position"),
            )
            self.assertEqual(motor.calls[-1], ("servo_enable", False))

        asyncio.run(scenario())

    def test_clamp_teach_leaves_servo_alone_when_manual(self) -> None:
        async def scenario() -> None:
            adapter = self._make_adapter()
            adapter.config.ezi_config.clamp_servo_policy = "manual"
            motor = FakeClampMotor()
            adapter.set_ezi_motor(motor)

            await execute_clamp_action(
                adapter,
                make_instant_action("clampTeach").actions[0],
            )

            types = [call[0] for call in motor.calls]
            self.assertNotIn("servo_enable", types)
            self.assertNotIn("get_axis_status", types)
            self.assertIn("initialized_open_close_encoder_position", types)

        asyncio.run(scenario())
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -k clamp_teach -q
```

Expected: FAIL — `test_clamp_teach_runs_inside_the_servo_cycle`이 `ValueError: 'servo_enable' is not in list`로 깨진다.

- [ ] **Step 3: `clampTeach` 분기를 감싼다**

`adaptor/extensions/clamp/__init__.py`의 `clampTeach` 분기를 아래로 교체한다:

```python
    if action.action_type == "clampTeach":
        # teach는 자체 폴링 루프로 원점 탐색이 끝날 때까지 기다리므로
        # _wait_motion_done을 겹쳐 걸지 않는다. servo 게이트만 씌운다.
        auto_off = await _servo_on_and_wait(adapter, action.action_type)
        try:
            result = await adapter._ezi_motor.initialized_open_close_encoder_position(
                origin_encoder_offset=offset
            )
            return f"clampTeach finished: {result}"
        finally:
            await _disable_servo_after_motion(adapter, action.action_type, auto_off)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd adaptor && uv run pytest tests/test_adapter_jibot_v3_order.py -k clamp -q
```

Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add adaptor/extensions/clamp/__init__.py adaptor/tests/test_adapter_jibot_v3_order.py
git commit -m "fix(clamp): energise the servo for clampTeach too"
```

---

### Task 6: 설정 파일 주석과 예시 갱신

**Files:**
- Modify: `adaptor/config/extensions.hcl:38-43`, `adaptor/config/extensions.hcl.example:68-70`
- Test: `adaptor/tests/test_config.py` (Task 1에서 추가한 것으로 충분 — 새 테스트 없음)

**Interfaces:**
- Consumes: Task 1의 `EziConfig` 필드 4개
- Produces: 없음

- [ ] **Step 1: `adaptor/config/extensions.hcl`의 servo 블록 교체**

Task 1에서 값만 바꿔 둔 자리를 아래 전체로 교체한다:

```hcl
  # 클램프 이동 전후 servo 정책:
  #   auto_on_auto_off: 이동 전에 켜고 이동이 끝나면 끔(기본값)
  #   auto_on_keep_on: 이동 전에 켜고 이동 후에도 유지
  #   manual: 자동 제어하지 않고 clampOn/clampOff action으로만 제어
  # 어느 정책이든 이동은 servo ON과 이동 완료를 드라이브 상태 플래그로 확인하며 진행한다.
  clamp_servo_policy = "auto_on_auto_off"
  clamp_servo_on_timeout_sec = 3.0      # servo ON 확인 대기 한계(초). 초과하면 이동을 보내지 않고 실패
  clamp_motion_start_timeout_sec = 1.0  # 이동 시작 대기 한계(초). 초과해도 실패가 아니라 "이미 목표 위치"로 통과
  clamp_motion_timeout_sec = 30.0       # 이동 완료 대기 한계(초). 초과하면 모터를 세우고 실패
```

- [ ] **Step 2: `adaptor/config/extensions.hcl.example`의 servo 주석 교체**

`# servo 정책: 이동 후 유지 / 이동 후 자동 끄기 / clampOn·Off 수동 제어.` 와 `clamp_servo_policy = "auto_on_keep_on"` 두 줄을 아래로 교체한다:

```hcl
  # servo 정책: auto_on_auto_off(기본, 이동 후 끔) / auto_on_keep_on(이동 후 유지) /
  # manual(clampOn·clampOff로만 수동 제어).
  clamp_servo_policy = "auto_on_auto_off"
  clamp_servo_on_timeout_sec = 3.0      # servo ON 확인 대기 한계(초)
  clamp_motion_start_timeout_sec = 1.0  # 이동 시작 대기 한계(초); 초과해도 실패 아님
  clamp_motion_timeout_sec = 30.0       # 이동 완료 대기 한계(초); 초과 시 move_stop 후 실패
```

- [ ] **Step 3: 설정이 실제로 로딩되는지 확인**

```bash
cd adaptor && uv run pytest tests/test_config.py tests/test_extensions_config.py tests/test_configio.py -q
```

Expected: PASS. HCL에 dataclass 필드가 없는 키를 넣으면 `TypeError: __init__() got an unexpected keyword argument`로 여기서 잡힌다.

- [ ] **Step 4: 전체 테스트 확인**

```bash
cd adaptor && uv run pytest -q
```

Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add adaptor/config/extensions.hcl adaptor/config/extensions.hcl.example
git commit -m "docs(config): document the clamp servo policy and wait limits"
```

---

## 실기 확인 (선택)

단위 테스트는 드라이브를 모사할 뿐이다. 실제 로봇에서는 다음을 확인한다.

1. `clampOff`로 servo를 내린다.
2. `clamp`를 **한 번** 누른다 → 모터가 실제로 닫히고, 끝나면 servo가 꺼진다.
3. `unclamp`를 한 번 누른다 → 열리고 servo가 꺼진다.
4. 로그에 `clamp finished: position=... speed=...`가 실제 이동이 끝난 시점에 찍히는지 본다.

`clamp_servo_on_timeout_sec = 3.0`이 부족하면(대형 드라이브·알람 리셋 필요) 값을 늘리거나 `clampOn` 후 상태를 확인한다. 이동이 30초를 넘는 장비라면 `clamp_motion_timeout_sec`를 올린다.
