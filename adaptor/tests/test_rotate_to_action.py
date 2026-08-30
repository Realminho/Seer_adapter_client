"""rotateTo turns the robot in place to an absolute heading.

FMS needs a way to ask for an arrival heading at one specific node without
touching the map. The map cannot express it — every PathPoint on this site
carries theta 0.00 — and a per-order heading is exactly what a node action is
for. rotateTo is a built-in composable action rather than a site recipe so
every deployment has it without editing recipes.hcl.

기본 경로는 UmDrive(연속 속도) 폐루프다. pose goto 는 제자리 회전이 아니다 —
urobot 이 target=pose 를 늘 경로로 풀어서, 목표 자세로 진입하려고 뒤로 물러났다가
돈다 (2026-08-22 HN-SH6-TR-002 실기). goto 경로는 UmDrive 가 없는 구현(시뮬레이터)과
되돌릴 필요를 위한 폴백으로 남아 있고, 아래 `_run_rotate_to*` 헬퍼가 그 모드를
명시적으로 고정해서 검증한다.
"""

import asyncio
from types import SimpleNamespace

from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from tests._stop_reason_harness import make_adapter, start_state


def _run_rotate_to(params, *, cur_x=1000.0, cur_y=2000.0, cur_th=0.0, settle=None):
    async def scenario():
        adapter = make_adapter()
        # 이 헬퍼가 검증하는 건 goto 폴백 경로다 (기본은 조그).
        adapter.config.settings.rotate_to_mode = "goto"
        adapter._vehicle._x = cur_x
        adapter._vehicle._y = cur_y
        adapter._vehicle._th = cur_th
        statuses = {}
        original = adapter._update_instant_action_status

        def capture(action_id, status, *args, **kw):
            # Keep the description: a FAILED assertion is worthless if the
            # handler failed for an unrelated reason.
            description = kw.get("result_description")
            if description is None and args:
                description = args[0]
            statuses[action_id] = (status, description or "")
            return original(action_id, status, *args, **kw)

        adapter._update_instant_action_status = capture
        task = await start_state(adapter)
        try:
            action = SimpleNamespace(
                action_id="rot-1",
                action_type="rotateTo",
                action_parameters=[
                    SimpleNamespace(key=k, value=v) for k, v in params.items()
                ],
            )
            adapter._handle_rotate_to_instant_action(action)
            if settle is not None:
                await asyncio.sleep(0.05)
                adapter._vehicle._th = settle
            await asyncio.sleep(0.6)
            return adapter, statuses
        finally:
            task.cancel()

    return asyncio.run(scenario())


def test_rotate_to_commands_a_pose_goto_at_the_current_xy():
    adapter, statuses = _run_rotate_to({"thetaDeg": 90}, settle=90.0)

    assert adapter._vehicle.goto_targets == [(1000.0, 2000.0, 90.0)]
    assert statuses["rot-1"][0] is ActionStatus.FINISHED


def test_rotate_to_finishes_only_after_the_heading_settles():
    # A heading that never arrives must not report FINISHED — a HARD-blocking
    # node action reporting success early lets FMS start the next step while the
    # robot is still turning.
    adapter, statuses = _run_rotate_to({"thetaDeg": 90, "timeoutSec": 0.2})

    assert adapter._vehicle.goto_targets == [(1000.0, 2000.0, 90.0)]
    status, description = statuses["rot-1"]
    assert status is ActionStatus.FAILED
    assert "heading" in description


def test_rotate_to_without_a_numeric_heading_fails_and_drives_nothing():
    adapter, statuses = _run_rotate_to({"thetaDeg": ""})

    status, description = statuses["rot-1"]
    assert adapter._vehicle.goto_targets == []
    assert status is ActionStatus.FAILED
    assert "thetaDeg" in description


def test_rotate_to_without_a_known_pose_fails_and_drives_nothing():
    # Rotating in place needs the current x/y to hold position. Substituting 0
    # would drive the robot to the map origin instead of turning it.
    adapter, statuses = _run_rotate_to({"thetaDeg": 90}, cur_x=None, cur_y=None)

    status, description = statuses["rot-1"]
    assert adapter._vehicle.goto_targets == []
    assert status is ActionStatus.FAILED
    assert "pose" in description


def test_rotate_to_is_a_built_in_composable_action():
    # Built-in, not a recipe: a site that never edits recipes.hcl still gets it.
    from core.action_bridge import action_specs

    spec = {s.action_type: s for s in action_specs()}["rotateTo"]

    assert spec.motion is True
    assert spec.cancel_handler is not None


def _run_rotate_to_with_refresh(
    *,
    cached_xy,
    fresh_xy,
    moving_polls=0,
    supports_refresh=True,
):
    """정지 대기 → pose 재조회 → goto 순서를 관찰하는 시나리오.

    로봇은 처음 `moving_polls` 번의 관측 동안 goto 를 물고 있다가 멈추고,
    재조회(um_get_robot_info)가 오면 캐시를 `fresh_xy` 로 갱신한다.
    """
    async def scenario():
        adapter = make_adapter()
        # 잡을 자리(x/y)가 필요한 건 goto 폴백뿐이다. 조그는 heading 만 본다.
        adapter.config.settings.rotate_to_mode = "goto"
        vehicle = adapter._vehicle
        vehicle._x, vehicle._y = cached_xy
        vehicle._th = 0.0
        polls = {"motion": moving_polls, "refresh": 0}

        original_motion = adapter._has_active_automatic_motion

        def has_motion():
            if polls["motion"] > 0:
                polls["motion"] -= 1
                return True
            return original_motion()

        adapter._has_active_automatic_motion = has_motion

        if supports_refresh:
            vehicle._status_snapshot_at = 0.0

            async def um_get_robot_info(gap=-1):
                polls["refresh"] += 1
                # 로봇이 실제로 서 있는 자리. 캐시는 최대 1초 묵어 있었다.
                vehicle._x, vehicle._y = fresh_xy
                vehicle._status_snapshot_at += 1.0

            vehicle.um_get_robot_info = um_get_robot_info

        task = await start_state(adapter)
        try:
            action = SimpleNamespace(
                action_id="rot-1",
                action_type="rotateTo",
                action_parameters=[SimpleNamespace(key="thetaDeg", value=90)],
            )
            adapter._handle_rotate_to_instant_action(action)
            for _ in range(100):
                if vehicle.goto_targets:
                    break
                await asyncio.sleep(0.02)
            vehicle._th = 90.0
            await asyncio.sleep(0.3)
            return adapter, polls
        finally:
            task.cancel()

    return asyncio.run(scenario())


def test_rotate_to_holds_the_pose_read_after_the_refresh_not_the_stale_cache():
    """캐시된 x/y 는 최대 1초 묵은 값이다 (main.py robot_info_loop interval_sec=1).

    그대로 goto 목표로 쓰면 그 사이 굴러간 만큼 목표가 뒤에 찍히고, 로봇은 회전
    전에 그 거리만큼 후진한다. 현장에서 p2 도착 후 rotateTo 가 뒤로 갔다 회전한
    증상이 이것이다. 명령 직전에 다시 읽은 자리를 잡아야 한다.
    """
    adapter, polls = _run_rotate_to_with_refresh(
        cached_xy=(16000.0, 1200.0), fresh_xy=(16377.0, 1666.0)
    )

    assert polls["refresh"] >= 1
    assert adapter._vehicle.goto_targets == [(16377.0, 1666.0, 90.0)]


def test_rotate_to_waits_for_the_drive_to_end_before_reading_the_pose():
    """주행 중에 읽은 자리는 도착 자리가 아니다. 정지를 먼저 확인한다."""
    adapter, polls = _run_rotate_to_with_refresh(
        cached_xy=(16000.0, 1200.0), fresh_xy=(16377.0, 1666.0), moving_polls=3
    )

    assert adapter._vehicle.goto_targets == [(16377.0, 1666.0, 90.0)]


def test_rotate_to_still_works_when_the_vehicle_cannot_refresh():
    """재조회는 best-effort 다. 시뮬레이터처럼 못 하는 구현이면 캐시로 간다."""
    adapter, _polls = _run_rotate_to_with_refresh(
        cached_xy=(1000.0, 2000.0), fresh_xy=(9999.0, 9999.0), supports_refresh=False
    )

    assert adapter._vehicle.goto_targets == [(1000.0, 2000.0, 90.0)]


def _wrap180(deg):
    return (float(deg) + 180.0) % 360.0 - 180.0


def _run_rotate_to_jog(
    *,
    start_th=0.0,
    target=90.0,
    step=8.0,
    mode=None,
    turns=True,
    params=None,
):
    """UmDrive 조그로 도는 시나리오.

    가짜 로봇은 um_drive 를 받을 때마다 `step` 만큼 명령 부호 방향으로 돈다.
    `turns=False` 면 명령을 받아도 안 돈다(타임아웃 경로).
    """
    async def scenario():
        adapter = make_adapter()
        vehicle = adapter._vehicle
        vehicle._x, vehicle._y, vehicle._th = 1000.0, 2000.0, start_th
        adapter.config.settings.rotate_to_tick_sec = 0.01
        if mode is not None:
            adapter.config.settings.rotate_to_mode = mode

        statuses = {}
        original_status = adapter._update_instant_action_status

        def capture(action_id, status, *args, **kw):
            description = kw.get("result_description")
            if description is None and args:
                description = args[0]
            statuses[action_id] = (status, description or "")
            return original_status(action_id, status, *args, **kw)

        adapter._update_instant_action_status = capture

        original_drive = vehicle.um_drive

        async def um_drive(trans, rot, speed, lat, gap=-1):
            await original_drive(trans, rot, speed, lat, gap)
            if turns:
                vehicle._th = _wrap180(vehicle._th + (step if rot > 0 else -step))

        vehicle.um_drive = um_drive

        task = await start_state(adapter)
        try:
            merged = {"thetaDeg": target, "timeoutSec": 1.0}
            merged.update(params or {})
            action = SimpleNamespace(
                action_id="rot-1",
                action_type="rotateTo",
                action_parameters=[
                    SimpleNamespace(key=k, value=v) for k, v in merged.items()
                ],
            )
            adapter._handle_rotate_to_instant_action(action)
            for _ in range(300):
                if "rot-1" in statuses and statuses["rot-1"][0] in (
                    ActionStatus.FINISHED,
                    ActionStatus.FAILED,
                ):
                    break
                await asyncio.sleep(0.01)
            return adapter, statuses
        finally:
            task.cancel()

    return asyncio.run(scenario())


def test_rotate_to_turns_with_a_jog_not_a_pose_goto():
    """pose goto 로는 제자리 회전이 안 된다 — urobot 이 목표 자세로 진입하려고
    뒤로 물러났다가 돈다. 2026-08-22 실기에서 pose 재조회를 넣은 뒤에도 후진이
    그대로였다. UmDrive 는 거리 개념이 없는 연속 속도 명령이라 그 경로 계획을
    타지 않는다."""
    adapter, statuses = _run_rotate_to_jog(start_th=0.0, target=90.0)

    assert adapter._vehicle.goto_targets == []
    assert adapter._vehicle.drive_calls, "UmDrive 가 나가지 않았다"
    assert all(trans == 0 for trans, _rot, _speed, _lat in adapter._vehicle.drive_calls)
    assert statuses["rot-1"][0] is ActionStatus.FINISHED


def test_rotate_to_jog_stops_the_robot_when_the_heading_arrives():
    """조그는 연속 속도 명령이다. 멈추라고 안 하면 계속 돈다."""
    adapter, statuses = _run_rotate_to_jog(start_th=0.0, target=45.0)

    assert statuses["rot-1"][0] is ActionStatus.FINISHED
    assert adapter._vehicle.um_stop_calls >= 1


def test_rotate_to_jog_takes_the_short_way_around():
    """+170 에서 -170 은 짧은 쪽으로 20도다. 340도를 돌면 안 된다."""
    adapter, _statuses = _run_rotate_to_jog(start_th=170.0, target=-170.0, step=4.0)

    first_rot = adapter._vehicle.drive_calls[0][1]
    assert first_rot > 0, f"짧은 쪽(+)이 아니라 {first_rot} 로 돌았다"


def test_rotate_to_jog_slows_down_near_the_target():
    """목표 근처에서 같은 속도로 밀면 지나친다. slow zone 안에서는 느리게."""
    adapter, _statuses = _run_rotate_to_jog(start_th=0.0, target=90.0, step=8.0)

    magnitudes = [abs(rot) for _trans, rot, _speed, _lat in adapter._vehicle.drive_calls]
    assert magnitudes[0] > magnitudes[-1], f"감속하지 않았다: {magnitudes}"


def test_rotate_to_jog_stops_and_fails_when_the_heading_never_arrives():
    adapter, statuses = _run_rotate_to_jog(start_th=0.0, target=90.0, turns=False)

    status, description = statuses["rot-1"]
    assert status is ActionStatus.FAILED
    assert "heading" in description
    assert adapter._vehicle.um_stop_calls >= 1


def test_rotate_to_falls_back_to_a_pose_goto_when_asked():
    """UmDrive 가 없는 구현(시뮬레이터)과 되돌릴 필요를 위해 goto 모드를 남긴다."""
    adapter, statuses = _run_rotate_to_jog(
        start_th=90.0, target=90.0, mode="goto", turns=False
    )

    assert adapter._vehicle.drive_calls == []
    assert adapter._vehicle.goto_targets == [(1000.0, 2000.0, 90.0)]
    assert statuses["rot-1"][0] is ActionStatus.FINISHED


def test_rotate_to_jog_yields_when_the_worker_drives_on():
    """blockingType=NONE 이면 스텝이 빠진 뒤에도 배경에서 돈다.

    그 사이 워커가 다음 노드로 출발하면 조그가 그 UmGoto 와 싸우고, 끝에 보내는
    UmStop 이 남의 주행을 세운다. 양보하고 UmStop 도 보내지 않아야 한다.
    """
    async def scenario():
        adapter = make_adapter()
        vehicle = adapter._vehicle
        vehicle._th = 0.0
        adapter.config.settings.rotate_to_tick_sec = 0.01

        original_drive = vehicle.um_drive

        async def um_drive(trans, rot, speed, lat, gap=-1):
            await original_drive(trans, rot, speed, lat, gap)
            # 워커가 다음 노드 스텝을 시작한 것과 같은 효과.
            adapter._order_node_motion_seq += 1

        vehicle.um_drive = um_drive

        action = SimpleNamespace(action_id="rot-1", action_type="rotateTo")
        return adapter, await adapter._rotate_to_with_jog(action, 90.0, 3.0, 1.0)

    adapter, reason = asyncio.run(scenario())

    assert reason is not None and "superseded" in reason
    assert adapter._vehicle.um_stop_calls == 0, "남의 주행을 세웠다"


def _run_rotate_to_jog_readings(
    readings,
    *,
    target=90.0,
    timeout=1.0,
    params=None,
):
    """heading 표본열을 그대로 먹이는 시나리오.

    실제 회전은 모사하지 않는다. 관심사가 "표본을 어떻게 믿는가"라서, 매 tick
    다음 표본을 그대로 물린다. 표본이 떨어지면 마지막 값을 계속 준다.
    """
    async def scenario():
        adapter = make_adapter()
        vehicle = adapter._vehicle
        vehicle._x, vehicle._y = 1000.0, 2000.0
        adapter.config.settings.rotate_to_tick_sec = 0.01
        adapter.config.settings.rotate_to_settle_sec = 0.0

        seq = list(readings)
        vehicle._th = seq[0]

        async def refresh():
            if seq:
                vehicle._th = seq.pop(0)

        adapter._refresh_vehicle_pose = refresh

        statuses = {}
        original_status = adapter._update_instant_action_status

        def capture(action_id, status, *args, **kw):
            description = kw.get("result_description")
            if description is None and args:
                description = args[0]
            statuses[action_id] = (status, description or "")
            return original_status(action_id, status, *args, **kw)

        adapter._update_instant_action_status = capture

        task = await start_state(adapter)
        try:
            merged = {"thetaDeg": target, "timeoutSec": timeout}
            merged.update(params or {})
            action = SimpleNamespace(
                action_id="rot-1",
                action_type="rotateTo",
                action_parameters=[
                    SimpleNamespace(key=k, value=v) for k, v in merged.items()
                ],
            )
            adapter._handle_rotate_to_instant_action(action)
            for _ in range(400):
                if "rot-1" in statuses and statuses["rot-1"][0] in (
                    ActionStatus.FINISHED,
                    ActionStatus.FAILED,
                ):
                    break
                await asyncio.sleep(0.01)
            return adapter, statuses
        finally:
            task.cancel()

    return asyncio.run(scenario())


def test_rotate_to_jog_does_not_finish_on_a_single_stray_sample():
    """heading 텔레메트리에는 이상표본이 섞인다.

    2026-08-23 62 실기: 정지 상태에서 -83 이 연속으로 오는 중에 -90 표본 하나가
    끼었고, 같은 시각 x 도 27mm 튀었다. 표본 하나로 도달을 확정하면 회전 중에
    스친 그 한 표본에 걸려 조기에 멈춘다. 목표를 스친 표본이 하나뿐이면 계속
    돌아야 한다.
    """
    adapter, statuses = _run_rotate_to_jog_readings(
        [0.0, 45.0, 90.0, 55.0, 55.0, 55.0, 55.0, 55.0], target=90.0, timeout=0.3
    )

    status, _description = statuses["rot-1"]
    assert status is ActionStatus.FAILED, "이상표본 하나로 도달을 확정했다"


def test_rotate_to_jog_rechecks_the_heading_after_it_stops():
    """UmStop 은 감속을 시작시킬 뿐이라, 멈춘 자리는 판정한 자리와 다르다.

    2026-08-23 62 실기: 89도에서 270도(=-90)를 향해 정확히 180도 회전을 걸었는데
    회전 중 -91 표본에서 도달로 판정하고 멈췄고, 실제 안착은 -83 이었다(연속 9표본).
    172도만 돈 셈인데 FINISHED 로 보고됐다. 멈춘 뒤 다시 읽어 확인해야 한다.
    """
    adapter, statuses = _run_rotate_to_jog_readings(
        [0.0, 45.0, 89.0, 89.0, 81.0, 81.0, 81.0, 81.0, 81.0],
        target=90.0,
        timeout=0.3,
    )

    status, _description = statuses["rot-1"]
    assert status is not ActionStatus.FINISHED, (
        "8도 어긋난 채 멈췄는데 도달로 보고했다"
    )
