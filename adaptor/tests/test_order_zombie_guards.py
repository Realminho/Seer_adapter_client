"""좀비 오더 방지 가드 테스트.

2026-08-25 06:58:59 HN-SH6-TR-002 에서 실제로 난 사고를 고정한다. 리부팅 직후
JIBOT TCP(7273) 가 아직 안 열린 상태에서 FMS 가 p39 오더를 보냈고, 어댑터가 그걸
수락한 뒤 첫 UmGoto 에서 writer 가 None 이라 워커가 죽었다. 오더는 active 인 채로
남아 뒤이은 4노드 오더가 통째로 ORDER_CURRENT_NOT_FINISHED 로 거절됐고, 사람이
cancelOrder 를 보낸 뒤에도 그 경고가 state 에 남아 FMS 는 계속 미완료로 인식했다.
"""

import asyncio
import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_ROOT.parent
for p in (ADAPTER_ROOT, REPO_ROOT / "jibot-simulator", REPO_ROOT / "jibot-client" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from adapter_jibot import Adapter
from protocol.vda_2_0_0.vda5050_2_0_0_state import ErrorType

from test_adapter_jibot_v3_order import (
    FakeVehicle,
    make_cancel_order_action,
    make_order,
)


class DisconnectedVehicle(FakeVehicle):
    """TCP 가 끊긴 JIBOT. 명령을 보내려 하면 실차와 같은 예외가 난다."""

    def is_connected(self) -> bool:
        return False


class CrashingVehicle(FakeVehicle):
    """연결은 살아 있다고 보고하지만 첫 주행 명령에서 죽는 JIBOT.

    링크가 주행 도중 끊긴 경우를 모사한다. 수락 시점 가드로는 못 막는 경로다.
    """

    async def goto_point(self, *args, **kwargs):
        raise AttributeError("'NoneType' object has no attribute 'write'")

    async def goto_xyz(self, *args, **kwargs):
        raise AttributeError("'NoneType' object has no attribute 'write'")


class OrderZombieGuardTest(unittest.TestCase):
    async def _start_state(self, adapter: Adapter) -> asyncio.Task:
        adapter._loop = asyncio.get_running_loop()
        task = asyncio.create_task(adapter.publish_state("state", interval_sec=0.05))
        for _ in range(100):
            if adapter.state is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(adapter.state)
        return task

    @staticmethod
    def _error_types(adapter: Adapter):
        return [
            getattr(error, "error_type", None) for error in adapter.state.errors
        ]

    def test_order_is_rejected_while_the_jibot_link_is_down(self) -> None:
        """링크가 없으면 수락하지 않는다 — 수락하면 첫 명령에서 워커가 죽는다."""

        async def scenario() -> None:
            adapter = Adapter()
            adapter.set_vehicle(DisconnectedVehicle())
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(make_order("order-link-down"))
                await asyncio.sleep(0.1)

                # 오더가 active 로 남지 않아야 다음 오더가 거절되지 않는다
                self.assertEqual(adapter.state.order_id, "")
                self.assertEqual(adapter.order_queue.qsize(), 0)
                self.assertIsNone(adapter.order_worker_task)
                self.assertIn(ErrorType.UNKNOWN_ERROR, self._error_types(adapter))
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_a_second_order_is_accepted_after_a_link_down_rejection(self) -> None:
        """거절된 오더가 뒤 오더를 막지 않는다(사고의 핵심 증상)."""

        async def scenario() -> None:
            adapter = Adapter()
            vehicle = DisconnectedVehicle()
            adapter.set_vehicle(vehicle)
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(make_order("order-link-down"))
                await asyncio.sleep(0.1)

                vehicle.is_connected = lambda: True
                adapter._handle_v3_order(make_order("order-after-recovery"))
                await asyncio.sleep(0.1)

                self.assertEqual(adapter.state.order_id, "order-after-recovery")
                # 수락 경로가 errors 를 비우므로 거절 경고도 남지 않는다
                self.assertNotIn(
                    ErrorType.ORDER_CURRENT_NOT_FINISHED, self._error_types(adapter)
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_worker_crash_drops_the_order_instead_of_leaving_it_active(self) -> None:
        """워커가 예외로 죽어도 오더는 끝난다 — 주행 중 링크 끊김 경로."""

        async def scenario() -> None:
            adapter = Adapter()
            adapter.set_vehicle(CrashingVehicle())
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(make_order("order-crash"))
                for _ in range(200):
                    if adapter.state.order_id == "":
                        break
                    await asyncio.sleep(0.01)

                self.assertEqual(adapter.state.order_id, "")
                self.assertEqual(adapter.order_queue.qsize(), 0)
                self.assertIn(ErrorType.UNKNOWN_ERROR, self._error_types(adapter))

                # 크래시 처리가 FATAL 을 남기므로, 그것이 다음 오더를 막지 않는지까지
                # 본다. 막으면 좀비 오더를 FATAL 잠김으로 바꾼 것뿐이라 같은 사고다.
                adapter.set_vehicle(FakeVehicle())
                adapter._handle_v3_order(make_order("order-after-crash"))
                await asyncio.sleep(0.1)
                self.assertEqual(adapter.state.order_id, "order-after-crash")
            finally:
                state_task.cancel()

        asyncio.run(scenario())

    def test_cancel_order_clears_the_order_scoped_rejection_warning(self) -> None:
        """취소하면 그 오더에만 의미 있던 거절 경고도 같이 사라진다.

        안 지우면 orderId="" 인데 "Current order is still in progress" 가 계속
        발행돼 FMS 가 로봇을 영구 미완료로 본다.
        """

        async def scenario() -> None:
            adapter = Adapter()
            adapter.set_vehicle(FakeVehicle())
            state_task = await self._start_state(adapter)
            try:
                adapter._handle_v3_order(make_order("order-1"))
                await asyncio.sleep(0.1)

                adapter._handle_v3_order(make_order("order-2"))
                await asyncio.sleep(0.05)
                self.assertIn(
                    ErrorType.ORDER_CURRENT_NOT_FINISHED, self._error_types(adapter)
                )

                adapter.instant_actions_accept_procedure(make_cancel_order_action())
                for _ in range(200):
                    if adapter.state.order_id == "":
                        break
                    await asyncio.sleep(0.01)

                self.assertEqual(adapter.state.order_id, "")
                self.assertNotIn(
                    ErrorType.ORDER_CURRENT_NOT_FINISHED, self._error_types(adapter)
                )
            finally:
                state_task.cancel()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
