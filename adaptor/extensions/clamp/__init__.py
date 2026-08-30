"""EZI 모터를 사용하는 클램프 action extension."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, Iterable

from core.action_registry import (
    ActionParameterSpec,
    ActionResult,
    ActionSpec,
    action_params,
)
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


CLAMP_ACTION_TYPES = (
    "clamp",
    "unclamp",
    "clampTeach",
    "clampOn",
    "clampOff",
    "clampStop",
    "clampMin",
    "clampMax",
    "clampHome",
    "clampMoveTo",
)

MODULE_TITLE = "Clamp"

_CLAMP_LABELS = {
    "clampOn": "Servo ON (clampOn)",
    "clampOff": "Servo OFF (clampOff)",
    "clamp": "Clamp / close (clamp)",
    "unclamp": "Unclamp / open (unclamp)",
    "clampStop": "Clamp stop (clampStop)",
    "clampMin": "Clamp min (clampMin)",
    "clampMax": "Clamp max (clampMax)",
    "clampHome": "Clamp home (clampHome)",
    "clampMoveTo": "Clamp move to position (clampMoveTo)",
    "clampTeach": "Clamp teach (clampTeach)",
}

CLAMP_SERVO_POLICIES = {
    "auto_on_keep_on",
    "auto_on_auto_off",
    "manual",
}


def is_clamp_action(action_type: str) -> bool:
    """action type이 클램프 기능에 속하는지 확인한다."""
    return action_type in CLAMP_ACTION_TYPES


def _raise_if_motor_rejected(result: Any, what: str) -> None:
    """EZI 모터 무응답이나 통신 거부를 실행 오류로 변환한다."""
    if result is None:
        raise RuntimeError(f"{what}: no response from EZI motor (timeout)")
    comm = result.get("communication_status") if isinstance(result, dict) else None
    if comm not in (None, 0):
        raise RuntimeError(f"{what}: EZI drive rejected command (status={comm})")


def _resolve_clamp_target(
    adapter: Any,
    params: Dict[str, Any],
    *,
    abs_position: Any,
    offset: Any,
    position_key: str,
    offset_key: str,
) -> int:
    """action 파라미터와 설정 우선순위에 따라 목표 encoder 위치를 정한다.

    설정된 목표가 하나도 없으면 실패시킨다. 예전에는 `±origin_encoder_offset`으로
    넘어갔는데, 그 값은 축의 실제 가동 범위와 아무 관계가 없다 — 62에서 unclamp이
    -16000을 향했고 축은 이미 마이너스 하드웨어 리미트(엔코더 0)에 물려 있어서
    드라이브가 매번 이동을 조용히 버렸다. 아무도 설정하지 않은 값은 목표가 아니다.
    """
    if "position" in params:
        return int(params["position"])
    if abs_position is not None:
        return int(abs_position)
    if offset is not None:
        return int(adapter.config.ezi_config.origin_encoder) + int(offset)
    raise ValueError(
        f"{position_key} is not configured: set extension \"ezi\" "
        f"{position_key} (absolute encoder target) or {offset_key} "
        f"(relative to origin_encoder) in extensions.hcl, or pass a position "
        f"parameter with the action"
    )


def _clamp_servo_policy(adapter: Any) -> str:
    """설정된 servo 운용 정책을 읽고 지원 값인지 검증한다."""
    policy = getattr(
        adapter.config.ezi_config,
        "clamp_servo_policy",
        "auto_on_keep_on",
    )
    if policy not in CLAMP_SERVO_POLICIES:
        raise ValueError(
            "Unsupported clamp_servo_policy: "
            f"{policy}. Expected one of {sorted(CLAMP_SERVO_POLICIES)}"
        )
    return policy


class _MotorReadError(RuntimeError):
    """상태 읽기 1회가 실패했다(무응답·형식 오류).

    폴링 루프 안에서는 연속 `_MAX_CONSECUTIVE_READ_FAILURES`번까지 재시도하고, 그
    밖에서는 다른 RuntimeError와 똑같이 실행 오류로 올라간다.
    """


# 폴링 중 연속으로 봐주는 상태 읽기 실패 횟수. EziMotorClient는 재시도 없는 UDP 단발
# 교환이라, 30초 이동을 0.1초 주기로 폴링하면 ~300번을 주고받는다. 손실률 0.1%에서도
# 한 번의 유실로 긴 이동이 실패하면(=이동 중에 모터 전원을 끊으면) 실사용이 안 된다.
_MAX_CONSECUTIVE_READ_FAILURES = 3


async def _axis_flags(adapter: Any, action_type: str) -> Dict[str, bool]:
    """축 상태 플래그를 읽고, 무응답이면 실행 오류로 올린다."""
    status = await adapter._ezi_motor.get_axis_status()
    if not status or "active_flags" not in status:
        raise _MotorReadError(
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

    # 알람이 걸린 드라이브는 servo ON을 거부한다. auto_off가 기본이 되면서 모든 액션이
    # servo를 다시 켜야 하므로, 래치된 알람 하나가 클램프 전체를 막고 clampOn으로도
    # 풀리지 않는다. ezi_motor.py 헤더의 벤더 예제대로 alarm_reset을 먼저 보낸다.
    # 알람이 없을 때도 무해하고, 이 명령이 거부되더라도 뒤따르는 servo ON 확인이 진짜
    # 실패를 잡으므로 결과는 따로 막지 않는다.
    await adapter._ezi_motor.alarm_reset()

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


async def _actual_position(adapter: Any, action_type: str) -> int:
    """현재 엔코더 위치를 읽고, 무응답이면 실행 오류로 올린다."""
    result = await adapter._ezi_motor.get_actual_position()
    if not isinstance(result, dict) or result.get("position") is None:
        raise _MotorReadError(
            f"{action_type} actual position: no response from EZI motor (timeout)"
        )
    comm = result.get("communication_status")
    if comm not in (None, 0):
        raise _MotorReadError(
            f"{action_type} actual position: EZI drive rejected command (status={comm})"
        )
    return int(result["position"])


async def _motion_arrived(
    adapter: Any,
    action_type: str,
    flags: Dict[str, bool],
    *,
    target_position: Any = None,
    success_flag: Any = None,
) -> bool:
    """이번 명령이 실제로 목표에 닿았는지 확인한다.

    FFLAG_MOTIONING이 내려간 것은 도착의 증거가 아니다. 드라이브가 과부하 알람으로
    이동을 중단해도 똑같이 내려간다. 그래서 명령별로 "도착의 증거"를 따로 본다.

    - 절대 위치 이동(`clamp`/`unclamp`/`clampMoveTo`, 설정 좌표를 쓰는
      `clampMin`/`clampMax`/`clampHome`): 실제 엔코더 위치가 목표의
      `clamp_position_tolerance` 안에 들어왔는지.
    - 리미트 이동: `goto_limit_minus`는 FFLAG_HWNEGALMT, `goto_limit_plus`는
      FFLAG_HWPOSILMT.

    FFLAG_INPOSITION은 쓰지 않는다. move 직후에도 *이전* 위치 기준으로 True가 남아 있어
    stale True를 도착으로 오판한다(FFLAG_ORIGINRETOK와 같은 함정). 위치 비교는 이번 명령이
    실제로 요청한 목표와 맞대므로 그 문제가 없다.
    """
    if success_flag is not None:
        return bool(flags[success_flag])
    if target_position is None:
        raise ValueError(
            f"{action_type}: arrival check needs target_position or success_flag"
        )
    tolerance = abs(
        int(getattr(adapter.config.ezi_config, "clamp_position_tolerance", 500))
    )
    actual = await _actual_position(adapter, action_type)
    return abs(actual - int(target_position)) <= tolerance


async def _raise_if_drive_faulted(
    adapter: Any,
    action_type: str,
    flags: Dict[str, bool],
    *,
    target_position: Any = None,
    success_flag: Any = None,
) -> bool:
    """폴링 1회분의 오류 플래그를 판정한다. 도착이 확인되면 True를 돌려준다.

    FFLAG_EMGSTOP은 무조건 실패다. FFLAG_ERRORALL은 **도착이 확인되지 않았을 때만**
    실패로 본다 — `clampMin`/`clampMax`는 하드웨어 리미트를 치는 것이 목적이라 리미트
    플래그와 함께 ERRORALL이 서는 것이 정상 동작이다.
    """
    if flags["FFLAG_EMGSTOP"]:
        raise RuntimeError(f"{action_type}: EZI drive reported an emergency stop")
    if not flags["FFLAG_ERRORALL"]:
        return False
    if await _motion_arrived(
        adapter,
        action_type,
        flags,
        target_position=target_position,
        success_flag=success_flag,
    ):
        return True
    raise RuntimeError(
        f"{action_type}: EZI drive raised an alarm before reaching the target"
        f" (target={target_position if success_flag is None else success_flag})"
    )


async def _confirm_arrival(
    adapter: Any,
    action_type: str,
    poll: float,
    *,
    target_position: Any = None,
    success_flag: Any = None,
) -> bool:
    """도착 여부를 확인한다. 일시적인 상태 읽기 실패는 정해진 횟수만큼 재시도한다."""
    failures = 0
    while True:
        try:
            return await _motion_arrived(
                adapter,
                action_type,
                await _axis_flags(adapter, action_type),
                target_position=target_position,
                success_flag=success_flag,
            )
        except _MotorReadError:
            failures += 1
            if failures > _MAX_CONSECUTIVE_READ_FAILURES:
                raise
            await asyncio.sleep(poll)


async def _describe_axis(
    adapter: Any,
    action_type: str,
    *,
    target_position: Any = None,
    success_flag: Any = None,
) -> str:
    """Best-effort 'target=… actual=… limits=…' for a failure message.

    A move the drive silently drops is almost always a target outside the axis
    travel, so the numbers that prove it belong in the error itself rather than
    in a hand-run probe. Never raises: this only decorates an error that is
    already on its way up.
    """
    if success_flag is not None:
        target = f"target={success_flag}"
    else:
        target = f"target={target_position}"

    try:
        actual = f"actual={await _actual_position(adapter, action_type)}"
    except Exception:
        actual = "actual=unknown"

    limits = ""
    try:
        flags = await _axis_flags(adapter, action_type)
        hit = [
            name
            for name in ("FFLAG_HWNEGALMT", "FFLAG_HWPOSILMT",
                         "FFLAG_SWNEGALMT", "FFLAG_SWPOSILMT")
            if flags.get(name)
        ]
        if hit:
            limits = f" limitsActive={','.join(hit)}"
        if not flags.get("FFLAG_SERVOON", True):
            limits += " servoOn=False"
    except Exception:
        pass

    return f"{target} {actual}{limits}"


async def _wait_motion_done(
    adapter: Any,
    action_type: str,
    *,
    target_position: Any = None,
    success_flag: Any = None,
) -> None:
    """move 명령이 받아들여진 뒤 실제 이동이 끝날 때까지 기다린다.

    FFLAG_MOTIONING이 서고 내려가는 것으로 이동의 시작과 끝을 본다. 다만 내려간 것만으로
    완료로 보지 않고 `_motion_arrived`로 도착을 확인한다(위 docstring 참고). 호출자는
    절대 위치 이동이면 `target_position=`, 리미트 이동이면 `success_flag=`를 준다.

    원점 복귀(`clampHome`의 `goto_origin()` 경로)는 탐색 → 후퇴 → Z펄스의 다단계
    시퀀스라 중간에 MOTIONING이 잠깐 내려갈 수 있어 이 헬퍼를 쓰지 않는다 — 대신
    `_wait_origin_done`을 쓴다.

    manual 정책에서는 이 대기 자체를 건너뛴다. 그래서 action은 move ACK 시점에 곧바로
    완료로 보고되고, 이동이 중간에 멈춰도 감지·중단되지 않는다 — servo와 마찬가지로
    모션도 운영자가 직접 책임진다.
    """
    if _clamp_servo_policy(adapter) == "manual":
        return

    ezi = adapter.config.ezi_config
    poll = float(getattr(ezi, "ezi_motor_poll_interval_sec", 0.1))
    start_timeout = float(getattr(ezi, "clamp_motion_start_timeout_sec", 1.0))
    move_timeout = float(getattr(ezi, "clamp_motion_timeout_sec", 30.0))

    # 시작 대기: 끝내 안 움직이면 "이미 목표 위치"인지 확인한다. 가정하지 않는다 —
    # 늦게 출발하는 드라이브를 성공으로 통과시키면 모터가 움직이기 직전에 servo를 끊는다.
    waited = 0.0
    started = False
    failures = 0
    while waited < start_timeout:
        try:
            flags = await _axis_flags(adapter, action_type)
            if await _raise_if_drive_faulted(
                adapter,
                action_type,
                flags,
                target_position=target_position,
                success_flag=success_flag,
            ):
                return
        except _MotorReadError:
            failures += 1
            if failures > _MAX_CONSECUTIVE_READ_FAILURES:
                raise
        else:
            failures = 0
            if flags["FFLAG_MOTIONING"]:
                started = True
                break
        await asyncio.sleep(poll)
        waited += poll
    if not started:
        if await _confirm_arrival(
            adapter,
            action_type,
            poll,
            target_position=target_position,
            success_flag=success_flag,
        ):
            return
        raise RuntimeError(
            f"{action_type}: motion never started within {start_timeout}s and the axis"
            f" is not at the target — the EZI drive appears to have ignored the command"
            f" ({await _describe_axis(adapter, action_type, target_position=target_position, success_flag=success_flag)})"
        )

    # 완료 대기: MOTIONING이 내려가면 도착까지 확인한다. 초과하면 모터를 세우고 실패로
    # 올린다.
    waited = 0.0
    failures = 0
    while True:
        try:
            flags = await _axis_flags(adapter, action_type)
            if await _raise_if_drive_faulted(
                adapter,
                action_type,
                flags,
                target_position=target_position,
                success_flag=success_flag,
            ):
                return
            if not flags["FFLAG_MOTIONING"]:
                if await _motion_arrived(
                    adapter,
                    action_type,
                    flags,
                    target_position=target_position,
                    success_flag=success_flag,
                ):
                    return
                raise RuntimeError(
                    f"{action_type}: motion stopped before reaching the target"
                    f" (target={target_position if success_flag is None else success_flag})"
                )
        except _MotorReadError:
            failures += 1
            if failures > _MAX_CONSECUTIVE_READ_FAILURES:
                raise
        else:
            failures = 0
        if waited >= move_timeout:
            await adapter._ezi_motor.move_stop()
            raise RuntimeError(
                f"{action_type}: motion did not finish within {move_timeout}s"
            )
        await asyncio.sleep(poll)
        waited += poll


async def _wait_origin_done(adapter: Any, action_type: str) -> None:
    """`goto_origin()`(clampHome)이 실제로 끝날 때까지 기다린다.

    FASTECH 원점 복귀는 탐색 → 후퇴 → Z펄스의 다단계 시퀀스라 중간 단계 사이에
    FFLAG_MOTIONING이 잠깐 내려갈 수 있다. `_wait_motion_done`처럼 MOTIONING으로
    판정하면 그 틈에 완료로 오판해서 원점 복귀 도중에 servo를 끊는다. 그래서 원점
    복귀 전용 플래그인 FFLAG_ORIGINRETURNING(진행 중)/FFLAG_ORIGINRETOK(성공)로
    판정한다.

    FFLAG_ORIGINRETOK는 *이전* 성공한 원점 복귀의 값이 래치되어 남아 있을 수 있다.
    `goto_origin()` 직후 이 플래그만 바로 읽으면 stale True를 완료로 오판할 수 있는데,
    이는 FFLAG_INPOSITION을 완료 판정에 못 쓰는 것과 같은 함정이다. 그래서 먼저
    FFLAG_ORIGINRETURNING이 서는 것을 확인한 뒤에야(=이번 복귀가 실제로 시작한 뒤에야)
    완료 판정을 시작한다.

    manual 정책에서는 이 대기 자체를 건너뛴다. 그래서 action은 move ACK 시점에 곧바로
    완료로 보고되고, 원점 복귀가 중간에 멈춰도 감지·중단되지 않는다 — servo와 마찬가지로
    모션도 운영자가 직접 책임진다.
    """
    if _clamp_servo_policy(adapter) == "manual":
        return

    ezi = adapter.config.ezi_config
    poll = float(getattr(ezi, "ezi_motor_poll_interval_sec", 0.1))
    start_timeout = float(getattr(ezi, "clamp_motion_start_timeout_sec", 1.0))
    move_timeout = float(getattr(ezi, "clamp_motion_timeout_sec", 30.0))

    # 시작 대기: 끝내 안 서면 이미 원점인지 ORIGINRETOK로 확인한다. 확인되지 않으면
    # 명령이 씹힌 것으로 보고 실패로 올린다.
    waited = 0.0
    started = False
    failures = 0
    while waited < start_timeout:
        try:
            flags = await _axis_flags(adapter, action_type)
        except _MotorReadError:
            failures += 1
            if failures > _MAX_CONSECUTIVE_READ_FAILURES:
                raise
        else:
            failures = 0
            if flags["FFLAG_ORIGINRETURNING"]:
                started = True
                break
        await asyncio.sleep(poll)
        waited += poll
    if not started:
        if await _confirm_arrival(
            adapter, action_type, poll, success_flag="FFLAG_ORIGINRETOK"
        ):
            return
        raise RuntimeError(
            f"{action_type}: origin return never started within {start_timeout}s and"
            f" ORIGINRETOK is not set — the EZI drive appears to have ignored the command"
        )

    # 완료 대기: ORIGINRETURNING이 내려가면 ORIGINRETOK로 성공 여부를 확인한다.
    # 초과하면 모터를 세우고 실패로 올린다.
    waited = 0.0
    failures = 0
    while True:
        try:
            flags = await _axis_flags(adapter, action_type)
            if not flags["FFLAG_ORIGINRETURNING"]:
                if not flags["FFLAG_ORIGINRETOK"]:
                    raise RuntimeError(
                        f"{action_type}: origin return ended without ORIGINRETOK"
                    )
                return
        except _MotorReadError:
            failures += 1
            if failures > _MAX_CONSECUTIVE_READ_FAILURES:
                raise
        else:
            failures = 0
        if waited >= move_timeout:
            await adapter._ezi_motor.move_stop()
            raise RuntimeError(
                f"{action_type}: origin return did not finish within {move_timeout}s"
            )
        await asyncio.sleep(poll)
        waited += poll


async def _disable_servo_after_motion(
    adapter: Any,
    action_type: str,
    enabled: bool,
) -> None:
    """이동 후 자동 해제 정책인 경우에만 servo를 끈다."""
    if not enabled:
        return
    # 이 헬퍼는 finally에서 호출된다. 본문 예외가 풀리는 중이라면 servo OFF 실패가 그
    # 예외를 덮어써서는 안 된다 — 운영자가 진짜 원인 대신 엉뚱한 서브시스템을 보게 된다.
    # 덮을 예외가 없을 때(정상 종료)는 평소대로 올린다.
    in_flight = sys.exc_info()[1]
    try:
        _raise_if_motor_rejected(
            await adapter._ezi_motor.servo_enable(False),
            f"{action_type} servo_disable",
        )
    except Exception as exc:
        if in_flight is None:
            raise
        print(
            f"[CLAMP SERVO OFF FAILED] {action_type}: {exc}"
            f" (원래 오류를 그대로 올린다: {in_flight})"
        )


async def execute_clamp_action(adapter: Any, action: Any) -> str:
    """클램프 action을 EZI 모터 명령으로 실행하고 결과 설명을 반환한다."""
    if adapter._ezi_motor is None:
        raise ValueError("EZI clamp motor is not initialized")

    ezi = adapter.config.ezi_config
    params = action_params(action)
    speed = int(params.get("speed", ezi.motor_speed))
    offset = int(ezi.origin_encoder_offset)

    if action.action_type == "clampOn":
        _raise_if_motor_rejected(
            await adapter._ezi_motor.servo_enable(True), "clampOn"
        )
        return "clampOn finished: servo enabled"

    if action.action_type == "clampOff":
        _raise_if_motor_rejected(
            await adapter._ezi_motor.servo_enable(False), "clampOff"
        )
        return "clampOff finished: servo disabled"

    if action.action_type == "clampStop":
        await adapter._ezi_motor.move_stop()
        return "clampStop finished: motor stopped"

    if action.action_type in ("clamp", "unclamp"):
        if action.action_type == "clamp":
            position = _resolve_clamp_target(
                adapter,
                params,
                abs_position=ezi.clamp_position,
                offset=ezi.clamp_offset,
                position_key="clamp_position",
                offset_key="clamp_offset",
            )
        else:
            position = _resolve_clamp_target(
                adapter,
                params,
                abs_position=ezi.unclamp_position,
                offset=ezi.unclamp_offset,
                position_key="unclamp_position",
                offset_key="unclamp_offset",
            )
        auto_off = await _servo_on_and_wait(adapter, action.action_type)
        try:
            _raise_if_motor_rejected(
                await adapter._ezi_motor.move_single_axis_abs_pos(position, speed),
                f"{action.action_type} move",
            )
            await _wait_motion_done(
                adapter, action.action_type, target_position=position
            )
            return f"{action.action_type} finished: position={position} speed={speed}"
        finally:
            await _disable_servo_after_motion(adapter, action.action_type, auto_off)

    if action.action_type in ("clampMin", "clampMax", "clampHome"):
        cfg_pos = {
            "clampMin": ezi.min_position,
            "clampMax": ezi.max_position,
            "clampHome": ezi.home_position,
        }[action.action_type]
        auto_off = await _servo_on_and_wait(adapter, action.action_type)
        try:
            if "position" in params or cfg_pos is not None:
                position = int(params.get("position", cfg_pos))
                _raise_if_motor_rejected(
                    await adapter._ezi_motor.move_single_axis_abs_pos(position, speed),
                    f"{action.action_type} move",
                )
                await _wait_motion_done(
                    adapter, action.action_type, target_position=position
                )
                return f"{action.action_type} finished: position={position} speed={speed}"
            if action.action_type == "clampMin":
                _raise_if_motor_rejected(
                    await adapter._ezi_motor.goto_limit_minus(speed), "clampMin"
                )
                await _wait_motion_done(
                    adapter, action.action_type, success_flag="FFLAG_HWNEGALMT"
                )
                return f"clampMin finished: moved to - limit (speed={speed})"
            if action.action_type == "clampMax":
                _raise_if_motor_rejected(
                    await adapter._ezi_motor.goto_limit_plus(speed), "clampMax"
                )
                await _wait_motion_done(
                    adapter, action.action_type, success_flag="FFLAG_HWPOSILMT"
                )
                return f"clampMax finished: moved to + limit (speed={speed})"
            _raise_if_motor_rejected(
                await adapter._ezi_motor.goto_origin(), "clampHome"
            )
            await _wait_origin_done(adapter, action.action_type)
            return "clampHome finished: moved to origin"
        finally:
            await _disable_servo_after_motion(adapter, action.action_type, auto_off)

    if action.action_type == "clampMoveTo":
        if "position" not in params:
            raise ValueError("clampMoveTo requires a position")
        position = int(params["position"])
        auto_off = await _servo_on_and_wait(adapter, action.action_type)
        try:
            _raise_if_motor_rejected(
                await adapter._ezi_motor.move_single_axis_abs_pos(position, speed),
                "clampMoveTo move",
            )
            await _wait_motion_done(
                adapter, action.action_type, target_position=position
            )
            return f"clampMoveTo finished: position={position} speed={speed}"
        finally:
            await _disable_servo_after_motion(adapter, action.action_type, auto_off)

    if action.action_type == "clampTeach":
        # teach는 자체 폴링 루프로 원점 탐색이 끝날 때까지 기다리므로
        # _wait_motion_done을 겹쳐 걸지 않는다. servo 게이트만 씌운다.
        #
        # 다만 그 루프에는 시간 제한이 없다(ezi_motor.initialized_open_close_encoder_position은
        # `while not await self.is_origin_sensor_on()`으로 돌고, is_origin_sensor_on()은
        # 모터가 응답하지 않아도 예외 없이 False를 준다). teach가 servo를 켜게 된 뒤로는
        # 그 무한 대기가 모터에 전류를 물린 채 finally에 닿지 못하게 하고, order로 들어온
        # teach라면 order 큐까지 영영 붙잡는다. ezi_motor는 다른 호출자가 있어 손대지 않고
        # 여기서 시간을 자른다.
        teach_timeout = float(getattr(ezi, "clamp_motion_timeout_sec", 30.0))
        auto_off = await _servo_on_and_wait(adapter, action.action_type)
        try:
            try:
                result = await asyncio.wait_for(
                    adapter._ezi_motor.initialized_open_close_encoder_position(
                        origin_encoder_offset=offset
                    ),
                    teach_timeout,
                )
            except asyncio.TimeoutError:
                await adapter._ezi_motor.move_stop()
                raise RuntimeError(
                    f"clampTeach: teach did not finish within {teach_timeout}s"
                    " (origin sensor never reported on)"
                )
            return f"clampTeach finished: {result}"
        finally:
            await _disable_servo_after_motion(adapter, action.action_type, auto_off)

    raise ValueError(f"Unsupported clamp action: {action.action_type}")


async def handle_clamp_action(ctx: Any) -> ActionResult:
    """클램프 실행 결과와 예외를 표준 ActionResult로 변환한다."""
    try:
        description = await execute_clamp_action(ctx.adapter, ctx.action)
    except Exception as exc:
        print(
            f"[CLAMP ACTION FAILED] actionId={ctx.action.action_id} "
            f"type={ctx.action.action_type}: {exc}"
        )
        return ActionResult(
            ActionStatus.FAILED,
            f"{ctx.action.action_type} failed: {exc}",
        )
    print(
        f"[CLAMP ACTION DONE] "
        f"{json.dumps({'actionId': ctx.action.action_id, 'type': ctx.action.action_type, 'description': description}, ensure_ascii=False)}"
    )
    return ActionResult(ActionStatus.FINISHED, description)


def action_specs() -> Iterable[ActionSpec]:
    """클램프 action별 handler와 UI 표시 정보를 등록한다."""
    return tuple(
        ActionSpec(
            action_type=action_type,
            handler=handle_clamp_action,
            motion=True,
            label=_CLAMP_LABELS.get(action_type, action_type),
            parameters=(
                (
                    ActionParameterSpec(
                        "position",
                        required=True,
                        input_type="number",
                        placeholder="엔코더 절대 위치",
                        label="목표 위치(엔코더)",
                    ),
                )
                if action_type == "clampMoveTo"
                else ()
            ),
        )
        for action_type in CLAMP_ACTION_TYPES
    )
