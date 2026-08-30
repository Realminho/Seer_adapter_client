"""JIBOT 범퍼 정지 표면화: FATAL JIBOT_BUMPER + #disable 2차 경로.

픽스처는 192.168.101.62에서 2026-08-12 11:21:58 범퍼 정지 중 실제 기록된 조합이다
(docs/superpowers/specs/2026-08-12-jibot-bumper-error-surfacing-design.md §1).
"""
import asyncio
import io
import time
import unittest
from contextlib import redirect_stdout

from protocol.vda5050_common import ErrorLevel  # ErrorLevel의 정본은 여기(버전 중립)
from protocol.vda_2_0_0.vda5050_2_0_0_state import Error, ErrorReference, ErrorType
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


class _TrackProbe:
    """Fake for adapter._sound.has_track(): records every filename queried, in
    order, and reports only the given `present` filenames as existing. Lets a
    test assert lookup ORDER without any real sound asset on disk."""

    def __init__(self, present=()):
        self._present = set(present)
        self.queried = []

    def __call__(self, track):
        self.queried.append(track)
        return track in self._present


class SoundCascadeTest(unittest.TestCase):
    """finding I-1: _resolve_sound_track()의 새 stop-reason 티어.

    실제 fault.mp3/bumper.mp3 자산은 아직 없다(사람이 직접 만들 예정) — 그래서
    파일을 만들지 않고 adapter._sound.has_track을 기록형 페이크로 대체해 조회
    "순서"만 검증한다.
    """

    async def _bumper_state_adapter(self):
        """FATAL JIBOT_BUMPER가 붙어 detail=='FAULT'인 상태를 만든 어댑터.

        직접 호출로 has_track 페이크를 주입하고 _resolve_sound_track()을 부르는
        사이에는 await가 없어야 백그라운드 publish 루프와 경합하지 않는다.
        """
        adapter = make_adapter()
        task = await start_state(adapter)
        set_safety(adapter, REAL_BUMPER)
        await asyncio.sleep(0.08)
        self.assertEqual(len(bumper_errors(adapter)), 1)  # 전제 확인
        self.assertEqual(adapter._jibot_stop_reason, "BUMPER")
        return adapter, task

    def test_bumper_queries_stop_reason_before_detail_before_working_state(self):
        async def scenario():
            adapter, task = await self._bumper_state_adapter()
            try:
                probe = _TrackProbe()  # 아무 파일도 없음 -> 무음 유지
                adapter._sound.has_track = probe
                track = adapter._resolve_sound_track()
                self.assertEqual(
                    probe.queried, ["bumper.mp3", "fault.mp3", "error.mp3"]
                )
                self.assertIsNone(track)
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_subtype_absent_falls_back_to_generic_fault_file(self):
        async def scenario():
            adapter, task = await self._bumper_state_adapter()
            try:
                probe = _TrackProbe(present={"fault.mp3"})
                adapter._sound.has_track = probe
                track = adapter._resolve_sound_track()
                self.assertEqual(probe.queried, ["bumper.mp3", "fault.mp3"])
                self.assertEqual(track, "fault.mp3")
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_stop_reason_subtype_wins_over_generic_fault_file(self):
        async def scenario():
            adapter, task = await self._bumper_state_adapter()
            try:
                probe = _TrackProbe(present={"bumper.mp3", "fault.mp3"})
                adapter._sound.has_track = probe
                track = adapter._resolve_sound_track()
                self.assertEqual(probe.queried, ["bumper.mp3"])
                self.assertEqual(track, "bumper.mp3")
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_no_file_anywhere_in_cascade_is_silence(self):
        async def scenario():
            adapter, task = await self._bumper_state_adapter()
            try:
                probe = _TrackProbe()
                adapter._sound.has_track = probe
                track = adapter._resolve_sound_track()
                self.assertIsNone(track)
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_none_stop_reason_never_queried_falls_through_to_fault_file(self):
        """detail=='FAULT'가 stop reason과 무관한 FATAL(JIBOT_CONNECTION_LOST 등)
        에서도 나올 수 있다. 그때 stop reason은 보통 "NONE"이며, none.mp3 같은
        무의미한 조회 없이 곧장 fault.mp3로 떨어져야 한다."""

        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                set_safety(adapter, NORMAL)
                await asyncio.sleep(0.08)
                self.assertEqual(adapter._jibot_stop_reason, "NONE")

                # stop reason과 무관한 FATAL 에러 주입 (예: JIBOT_CONNECTION_LOST).
                # append 직후 await 없이 바로 조회하므로 백그라운드 루프가 끼어들
                # 여지가 없다.
                adapter.state.errors.append(
                    Error(
                        error_type=ErrorType.JIBOT_CONNECTION_LOST,
                        error_level=ErrorLevel.FATAL,
                        error_references=[ErrorReference("reason", "disconnected")],
                        error_description="synthetic FATAL unrelated to stop reason",
                    )
                )
                working_state, detail = adapter._derive_amr_working_state()
                self.assertEqual(detail, "FAULT")  # 전제 확인

                probe = _TrackProbe(present={"fault.mp3"})
                adapter._sound.has_track = probe
                track = adapter._resolve_sound_track()
                self.assertNotIn("none.mp3", probe.queried)
                self.assertEqual(track, "fault.mp3")
            finally:
                task.cancel()

        asyncio.run(scenario())


class MotorDisabledChargingSuppressionTest(unittest.TestCase):
    """finding I-2: 충전 중에는 JIBOT_MOTOR_DISABLED WARNING을 억제한다.

    도킹 후 status는 "charging#disable"로 충전 내내 고정된다(실측값,
    test_jibot_client_bms.py:31). 이 게이트가 없으면 WARNING이 충전하는 동안
    내내(하루 몇 시간) 떠서 범퍼 백스톱으로서의 가치를 잃는다.
    """

    DISABLED_STATUS = "nrunto pose (13870 -2541 0)#disable#slowdown"
    CHARGING_DISABLED_STATUS = "charging#disable"  # 실측값, 지어낸 조합 아님

    def _motor_disabled_errors(self, adapter):
        return [
            e for e in adapter.state.errors
            if e.error_type == ErrorType.JIBOT_MOTOR_DISABLED
        ]

    def test_disable_token_warns_when_not_charging(self):
        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                adapter._vehicle._mode = ""
                adapter._vehicle._status = self.DISABLED_STATUS
                adapter._vehicle._charging = False
                await asyncio.sleep(0.08)
                self.assertEqual(len(self._motor_disabled_errors(adapter)), 1)
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_disable_token_suppressed_while_charging(self):
        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                adapter._vehicle._mode = ""
                adapter._vehicle._status = self.CHARGING_DISABLED_STATUS
                adapter._vehicle._charging = True
                await asyncio.sleep(0.08)
                self.assertEqual(self._motor_disabled_errors(adapter), [])
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_motor_disabled_warning_clears_once_charging_starts(self):
        """도킹 전에 뜬 WARNING이 충전이 시작되면 (무조건 purge라서) 사라진다."""

        async def scenario():
            adapter = make_adapter()
            task = await start_state(adapter)
            try:
                adapter._vehicle._mode = ""
                adapter._vehicle._status = self.DISABLED_STATUS
                adapter._vehicle._charging = False
                await asyncio.sleep(0.08)
                self.assertEqual(len(self._motor_disabled_errors(adapter)), 1)

                adapter._vehicle._status = self.CHARGING_DISABLED_STATUS
                adapter._vehicle._charging = True
                await asyncio.sleep(0.08)
                self.assertEqual(self._motor_disabled_errors(adapter), [])
            finally:
                task.cancel()

        asyncio.run(scenario())


class SoundReplayGapTest(unittest.TestCase):
    """반복 간격 override가 stop-reason 티어가 고른 트랙에도 걸리는지.

    `_resolve_sound_track`이 범퍼일 때 bumper.mp3를 고르므로, override 조회가
    detail(FAULT)만 본다면 파일명이 안 맞아 기본값(0초=연속 반복)으로 떨어진다.
    실제 범퍼 정지는 7분 40초였고 그동안 끊김 없이 울리게 된다.
    """

    async def _bumper_adapter(self):
        adapter = make_adapter()
        task = await start_state(adapter)
        set_safety(adapter, REAL_BUMPER)
        await asyncio.sleep(0.08)
        self.assertEqual(adapter._jibot_stop_reason, "BUMPER")
        return adapter, task

    def test_fault_override_applies_to_the_bumper_track(self):
        async def scenario():
            adapter, task = await self._bumper_adapter()
            try:
                adapter.config.sound_settings.state_replay_gap_overrides = {"fault": 3.0}
                self.assertEqual(adapter._resolve_sound_replay_gap("bumper.mp3"), 3.0)
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_stop_reason_override_wins_over_detail_override(self):
        async def scenario():
            adapter, task = await self._bumper_adapter()
            try:
                adapter.config.sound_settings.state_replay_gap_overrides = {
                    "bumper": 1.5,
                    "fault": 3.0,
                }
                self.assertEqual(adapter._resolve_sound_replay_gap("bumper.mp3"), 1.5)
            finally:
                task.cancel()

        asyncio.run(scenario())

    def test_override_does_not_leak_onto_an_unrelated_track(self):
        # An action track is not chosen by the state cascade, so a state
        # override must not apply to it.
        async def scenario():
            adapter, task = await self._bumper_adapter()
            try:
                adapter.config.sound_settings.state_replay_gap_sec = 0.0
                adapter.config.sound_settings.state_replay_gap_overrides = {"fault": 3.0}
                self.assertEqual(
                    adapter._resolve_sound_replay_gap("action-pioInit.mp3"), 0.0
                )
            finally:
                task.cancel()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
