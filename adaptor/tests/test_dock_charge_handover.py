"""도킹 성공 후 충전을 relay hold 로 인계하고 ModeCharge 를 빠져나오는 경로.

왜 필요한지: ModeCharge 를 유지한 채 두면 충전이 끊긴 순간 JIBOT 이 스스로 접근
단계(`nrunto N`)로 되돌아가는데, 이미 충전기에 밀착해 있어 전방이 막혀 `#brake`
로 고착된다. 2026-08-25 HN-SH6-TR-002 에서 05:06:02~06:20:13 74분 연속 관측
(`mode=ModeCharge status=nrunto 1#brake` 873표본, 그 사이 어댑터 비폴링 TX 0건).
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
for _p in (ADAPTER_ROOT, ADAPTER_ROOT.parent / "jibot-client" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from adapter_jibot import Adapter
from config.config import MotionRule
from utils.charge_circuit import FakeChargeCircuit


class _DockVehicle:
    """UmDock 으로 충전이 시작되고, UmStop 이력을 남기는 최소 JIBOT 대역."""

    def __init__(self, charging_survives_stop: bool = True) -> None:
        self._x = 10661.0
        self._y = -2547.0
        self._th = 0.0
        self._charging = False
        self._status = "Stopped"
        self._motor_flag = 1
        self._map_nodes = {"1_01CH": (10661.0, -2547.0, 0.0)}
        self.dock_calls = 0
        self.um_stop_calls = 0
        self._charging_survives_stop = charging_survives_stop

    async def um_dock(self, gap: int = -1, **params) -> None:
        self.dock_calls += 1
        self._charging = True
        self._status = "charging"

    async def um_stop(self, gap: int = -1) -> None:
        self.um_stop_calls += 1
        # relay hold 가 유지되면 UmStop 으로 ModeCharge 만 빠지고 충전은 남는다.
        if not self._charging_survives_stop:
            self._charging = False
            self._status = "Stopped"

    async def enable_motor(self) -> None:
        pass


def _adapter(charging_survives_stop: bool = True) -> Adapter:
    adapter = Adapter(config=None)
    adapter._vehicle = _DockVehicle(charging_survives_stop)
    adapter.config.motion_rules = [MotionRule(to="1_01CH", mode="dock")]
    adapter.config.charge_circuit.enabled = True
    adapter.config.charge_circuit.verify_timeout_sec = 0.2
    adapter.config.dock.dock_wait_poll_interval_sec = 0.01
    adapter.config.dock.stop_charging_repeat_count = 2
    adapter.config.dock.stop_charging_repeat_gap_sec = 0.0
    adapter.config.dock.handover_hold_settle_sec = 0.0
    adapter.config.dock.handover_verify_timeout_sec = 1.0
    adapter.config.dock.charging_start_poll_interval_sec = 0.02
    adapter.set_charge_circuit(FakeChargeCircuit())
    return adapter


def _node() -> SimpleNamespace:
    return SimpleNamespace(node_id="1_01CH", sequence_id=2)


class _TelemetryVehicle(_DockVehicle):
    """상태 스냅샷이 주기적으로만 들어오는 JIBOT 을 모사한다.

    실차의 `_charging` 은 UmGetLocState 가 도착할 때만 갱신되는 캐시값이고, 그
    주기는 약 5초다(2026-08-25 실측 06:30:10/:16/:21/:26/:31). 즉시 반영되는
    더블로는 "낡은 표본으로 인계 성공을 선언하는" 결함을 잡을 수 없다.
    """

    def __init__(self, period: float = 0.2) -> None:
        super().__init__()
        self.period = period
        self._physical_charging = False
        self._last_rx = time.monotonic()
        self.sampling = True
        self._task = None

    def seconds_since_last_rx(self) -> float:
        return time.monotonic() - self._last_rx

    async def _sample_loop(self) -> None:
        while True:
            await asyncio.sleep(self.period)
            if not self.sampling:
                continue
            self._last_rx = time.monotonic()
            self._charging = self._physical_charging
            self._status = "charging" if self._physical_charging else "Stopped"

    def start(self) -> None:
        self._task = asyncio.create_task(self._sample_loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def um_dock(self, gap: int = -1, **params) -> None:
        self.dock_calls += 1
        self._physical_charging = True
        # 캐시는 다음 표본에서야 따라온다.
        self._charging = True
        self._status = "charging"
        self._last_rx = time.monotonic()

    async def um_stop(self, gap: int = -1) -> None:
        self.um_stop_calls += 1
        if not self._charging_survives_stop:
            self._physical_charging = False


class DockChargeHandoverTest(unittest.TestCase):
    def test_dock_success_hands_charge_to_relay_and_leaves_mode_charge(self) -> None:
        """충전이 붙으면 relay hold 를 잡고 UmStop 으로 ModeCharge 를 벗어난다."""
        async def scenario() -> None:
            adapter = _adapter()
            reason = await adapter._run_dock(_node())
            self.assertIsNone(reason)
            self.assertTrue(adapter._charge_circuit.is_holding)
            self.assertTrue(adapter._charge_in_place_active)
            self.assertGreaterEqual(adapter._vehicle.um_stop_calls, 1)
            self.assertTrue(adapter._is_vehicle_charging())
        asyncio.run(scenario())

    def test_handover_failure_falls_back_to_redock_and_warns(self) -> None:
        """인계가 깨지면 재도킹으로 충전을 되살리고 WARNING 으로 남긴다.

        아무것도 안 하면 로봇이 충전기 위에서 방전만 한다 — 이 변경 이전보다 나쁘다.
        """
        async def scenario() -> None:
            adapter = _adapter(charging_survives_stop=False)
            adapter.state = SimpleNamespace(errors=[], instant_action_states=[])
            adapter.request_state_publish = lambda *_a, **_k: None
            reason = await adapter._run_dock(_node())
            self.assertIsNone(reason)                      # 노드는 성공 (충전 중)
            self.assertEqual(adapter._vehicle.dock_calls, 2)   # 최초 + 폴백
            self.assertTrue(adapter._is_vehicle_charging())
            self.assertFalse(adapter._charge_circuit.is_holding)
            self.assertFalse(adapter._charge_in_place_active)
            refs = [
                ref.reference_value
                for err in adapter.state.errors
                for ref in err.error_references
            ]
            self.assertIn("chargeHandover", refs)
        asyncio.run(scenario())

    def test_handover_failure_with_dead_charger_reports(self) -> None:
        """폴백 재도킹으로도 충전이 안 붙으면 사유를 돌려준다 (조용히 넘어가지 않음)."""
        async def scenario() -> None:
            adapter = _adapter(charging_survives_stop=False)
            adapter.state = SimpleNamespace(errors=[], instant_action_states=[])
            adapter.request_state_publish = lambda *_a, **_k: None
            adapter.config.dock.fail_timeout_sec = 0.2
            vehicle = adapter._vehicle

            async def dead_dock(gap: int = -1, **params) -> None:
                vehicle.dock_calls += 1                    # 충전이 붙지 않는 충전기

            vehicle.um_dock = dead_dock
            vehicle._charging = False
            reason = await adapter._run_dock(_node())
            self.assertIsNotNone(reason)
            self.assertFalse(adapter._charge_circuit.is_holding)
        asyncio.run(scenario())

    def test_transient_charge_drop_during_mode_charge_exit_is_not_a_failure(self) -> None:
        """JModeCharge 가 빠져나가며 릴레이를 한 번 여는 건 정상이다.

        hold 가 다시 닫을 때까지 한두 표본은 충전이 꺼져 보일 수 있는데, 그 순간을
        실패로 읽으면 정상 인계를 실패로 보고하게 된다.
        """
        async def scenario() -> None:
            adapter = _adapter()
            vehicle = adapter._vehicle
            original_stop = vehicle.um_stop

            async def stop_then_recover(gap: int = -1) -> None:
                await original_stop(gap)
                vehicle._charging = False          # OpenChargingCircuit 순간
                await asyncio.sleep(0.05)
                vehicle._charging = True           # hold 가 다시 닫음
                vehicle._status = "charging"

            vehicle.um_stop = stop_then_recover
            reason = await adapter._run_dock(_node())
            self.assertIsNone(reason)
            self.assertTrue(adapter._charge_circuit.is_holding)
        asyncio.run(scenario())

    def test_no_handover_when_charge_circuit_disabled(self) -> None:
        """relay 를 쥘 수 없으면 인계하지 않는다. UmStop 을 보내면 충전만 끊긴다."""
        async def scenario() -> None:
            adapter = _adapter()
            adapter.config.charge_circuit.enabled = False
            reason = await adapter._run_dock(_node())
            self.assertIsNone(reason)
            self.assertEqual(adapter._vehicle.um_stop_calls, 0)
            self.assertFalse(adapter._charge_circuit.is_holding)
        asyncio.run(scenario())

    def test_handover_can_be_turned_off_by_config(self) -> None:
        """현장에서 되돌릴 수 있어야 한다 (기본은 켜짐)."""
        async def scenario() -> None:
            adapter = _adapter()
            self.assertTrue(adapter.config.dock.handover_charge_to_relay_after_dock)
            adapter.config.dock.handover_charge_to_relay_after_dock = False
            reason = await adapter._run_dock(_node())
            self.assertIsNone(reason)
            self.assertEqual(adapter._vehicle.um_stop_calls, 0)
        asyncio.run(scenario())

    def test_already_charging_skip_still_does_not_move(self) -> None:
        """이미 충전 중이면 UmDock 을 안 보내는 기존 규약은 그대로다."""
        async def scenario() -> None:
            adapter = _adapter()
            adapter._vehicle._charging = True
            reason = await adapter._run_dock(_node())
            self.assertIsNone(reason)
            self.assertEqual(adapter._vehicle.dock_calls, 0)
        asyncio.run(scenario())


class HandoverTelemetryFreshnessTest(unittest.TestCase):
    """판정은 UmStop **이후 새로 도착한** 표본으로만 내려야 한다."""

    def _adapter_with_telemetry(self, survives: bool = True, period: float = 0.2):
        adapter = _adapter()
        vehicle = _TelemetryVehicle(period)
        vehicle._charging_survives_stop = survives
        adapter._vehicle = vehicle
        adapter.config.dock.handover_verify_timeout_sec = 3.0
        adapter.config.dock.charging_start_poll_interval_sec = 0.02
        return adapter, vehicle

    def test_stale_cache_alone_cannot_confirm_the_handover(self) -> None:
        """새 표본이 안 들어오면, 캐시가 charging 이어도 성공으로 보면 안 된다.

        이걸 허용하면 릴레이가 열린 채 ModeCharge 만 빠져나온 상태를 성공으로
        보고하고 SOC 가 떨어질 때까지 아무도 모른다.
        """
        async def scenario() -> None:
            adapter, vehicle = self._adapter_with_telemetry()
            vehicle.start()
            try:
                await vehicle.um_dock()
                vehicle.sampling = False        # UmStop 이후 새 프레임 없음
                since = time.monotonic()
                self.assertTrue(adapter._is_vehicle_charging())   # 캐시는 여전히 True
                held, saw_fresh = await adapter._wait_until_charge_relay_holds(since)
                self.assertFalse(held)
                self.assertFalse(saw_fresh)   # 관측 자체가 없었음을 구분해야 한다
            finally:
                vehicle.stop()
        asyncio.run(scenario())

    def test_confirm_waits_for_fresh_samples(self) -> None:
        """성공 판정도 최소 표본 개수만큼의 새 프레임을 본 뒤에 나와야 한다."""
        async def scenario() -> None:
            adapter, vehicle = self._adapter_with_telemetry(period=0.2)
            vehicle.start()
            try:
                await vehicle.um_dock()
                since = time.monotonic()
                started = time.monotonic()
                held, _ = await adapter._wait_until_charge_relay_holds(since)
                self.assertTrue(held)
                elapsed = time.monotonic() - started
                # 표본 2개를 기다렸다면 최소 1주기는 지나야 한다.
                self.assertGreaterEqual(elapsed, 0.2)
            finally:
                vehicle.stop()
        asyncio.run(scenario())

    def test_relay_recovering_after_one_refresh_still_succeeds(self) -> None:
        """릴레이 재폐로가 한 표본 주기보다 늦게 반영돼도 인계는 성공이다."""
        async def scenario() -> None:
            adapter, vehicle = self._adapter_with_telemetry(survives=False, period=0.2)
            vehicle.start()
            try:
                await vehicle.um_dock()

                async def relay_recovers() -> None:
                    await asyncio.sleep(0.5)      # 한 표본 주기보다 길게
                    vehicle._physical_charging = True

                await adapter._send_leave_mode_charge_stops("1_01CH")
                since = time.monotonic()
                asyncio.create_task(relay_recovers())
                held, _ = await adapter._wait_until_charge_relay_holds(since)
                self.assertTrue(held)
            finally:
                vehicle.stop()
        asyncio.run(scenario())

    def test_silent_link_does_not_trigger_a_redock(self) -> None:
        """링크가 조용해서 확인을 못 한 것과, 표본이 '충전 아님'이라 한 것은 다르다.

        전자에서 재도킹을 걸면 상태를 볼 수 없는 로봇에 UmDock 을 쏘게 된다.
        """
        async def scenario() -> None:
            adapter, vehicle = self._adapter_with_telemetry()
            adapter.state = SimpleNamespace(errors=[], instant_action_states=[])
            adapter.request_state_publish = lambda *_a, **_k: None
            adapter.config.dock.handover_hold_settle_sec = 0.0
            vehicle.start()
            try:
                await vehicle.um_dock()
                dock_calls_before = vehicle.dock_calls
                vehicle.sampling = False       # 인계 창 내내 새 프레임 없음
                reason, may_redock = await adapter._handover_dock_charge_to_relay(
                    _node()
                )
                self.assertIsNotNone(reason)
                self.assertIn("no JIBOT telemetry", reason)
                self.assertFalse(may_redock)
                self.assertEqual(vehicle.dock_calls, dock_calls_before)
            finally:
                vehicle.stop()
        asyncio.run(scenario())

    def test_relay_never_recovering_fails(self) -> None:
        async def scenario() -> None:
            adapter, vehicle = self._adapter_with_telemetry(survives=False, period=0.2)
            vehicle.start()
            try:
                await vehicle.um_dock()
                await adapter._send_leave_mode_charge_stops("1_01CH")
                since = time.monotonic()
                held, saw_fresh = await adapter._wait_until_charge_relay_holds(since)
                self.assertFalse(held)
            finally:
                vehicle.stop()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
