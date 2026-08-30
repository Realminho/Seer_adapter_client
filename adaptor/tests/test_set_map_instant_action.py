"""setMap instant-action 핸들러 단위 테스트.

실로봇 모드: write_jibot_map 호출 + FINISHED 보고 확인.
시뮬레이터 모드: _map_raw에 변환된 raw 저장 + FINISHED 보고 확인.
payload 없음: FAILED 보고 확인.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_ROOT.parent
for p in (ADAPTER_ROOT, REPO_ROOT / "jibot-simulator", REPO_ROOT / "jibot-client" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from adapter_jibot import Adapter
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus
from protocol.vda5050_3_0.messages import InstantActions


# ---------------------------------------------------------------------------
# 최소 uamap fixture (common_amr_map.from_jibot_snapshot 출력과 동일한 구조)
# ---------------------------------------------------------------------------
JIBOT_RAW = {
    "Header": "umcl-map",
    "MapName": "lab2m",
    "MapRes": 20,
    "MinPose": "0 0",
    "MaxPose": "20000 10000",
    "Objs": {
        "Goal": [{"name": "G1", "pose": "5953 4854 90.0"}],
        "Dock": [{"name": "D1", "pose": "7290 7268 0.0"}],
        "PathPoint": [
            {"name": "p1", "pose": "1000 1000 0.0", "vertex": "p2", "costs": [1]},
            {"name": "p2", "pose": "2000 1000 0.0", "vertex": ""},
        ],
        "AvoidArea": [{"name": "a1", "points": "0 0 1000 1000", "pose": "0 0 0"}],
    },
}

# uamap payload (from_jibot_snapshot 결과와 동일한 구조)
from common_amr_map import from_jibot_snapshot
UAMAP_PAYLOAD = from_jibot_snapshot({"raw": JIBOT_RAW, "mapId": "lab2m"})


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------

class FakeVehicle:
    """테스트용 최소 vehicle stub."""

    def __init__(self, *, is_simulator: bool = False) -> None:
        self.is_simulator = is_simulator
        self._x = 0.0
        self._y = 0.0
        self._th = 0.0
        self._battery = 80.0
        self._localization_score = 1.0
        self._charging = False
        self._motor_flag = 1
        self._mode = "auto"
        self._status = "Stopped"
        self._map_nodes: Dict[str, Any] = {}
        self._map_raw: Optional[dict] = None
        self.robot_ip = "127.0.0.1"
        self.robot_port = 7273

    def is_connected(self) -> bool:
        return True

    def is_rx_stale(self, timeout: float) -> bool:
        return False

    def seconds_since_last_rx(self) -> float:
        return 0.0


class StateStub:
    """adapter.state 최소 stub."""

    def __init__(self) -> None:
        self.order_id = ""
        self.order_update_id = 0
        self.node_states = []
        self.edge_states = []
        self.action_states = []
        self.instant_action_states = []
        self.errors = []
        self.information = []


def _make_adapter(*, is_simulator: bool = False) -> Adapter:
    adapter = Adapter()
    vehicle = FakeVehicle(is_simulator=is_simulator)
    adapter.set_vehicle(vehicle)
    adapter.state = StateStub()
    return adapter


def _make_set_map_instant_actions(payload: Any, map_id: str = "lab2m") -> InstantActions:
    """setMap instant-action 메시지 생성."""
    return InstantActions.from_dict(
        {
            "headerId": 20,
            "timestamp": "2026-06-19T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-TEST-001",
            "actions": [
                {
                    "actionType": "setMap",
                    "actionId": "ia-setmap-1",
                    "blockingType": "NONE",
                    "actionParameters": [
                        {"key": "payload", "value": payload},
                    ],
                }
            ],
        }
    )


def _make_set_map_instant_actions_no_payload() -> InstantActions:
    """payload 파라미터 없는 setMap instant-action."""
    return InstantActions.from_dict(
        {
            "headerId": 21,
            "timestamp": "2026-06-19T00:00:00.000Z",
            "version": "3.0.0",
            "manufacturer": "jibot",
            "serialNumber": "HN-TEST-001",
            "actions": [
                {
                    "actionType": "setMap",
                    "actionId": "ia-setmap-2",
                    "blockingType": "NONE",
                    "actionParameters": [],
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class SetMapPayloadMissingTest(unittest.TestCase):
    """payload 파라미터 없으면 즉시 FAILED."""

    def test_missing_payload_reports_failed(self) -> None:
        adapter = _make_adapter()
        ia = _make_set_map_instant_actions_no_payload()
        adapter.instant_actions_accept_procedure(ia)

        # 상태 배열을 직접 읽는 대신 _update_instant_action_status가 FAILED로
        # 마킹했는지를 mock으로 감시한다.
        adapter2 = _make_adapter()
        statuses = []
        original_update = adapter2._update_instant_action_status

        def capture_status(action_id, status, **kwargs):
            statuses.append(status)
            original_update(action_id, status, **kwargs)

        adapter2._update_instant_action_status = capture_status
        ia2 = _make_set_map_instant_actions_no_payload()
        adapter2.instant_actions_accept_procedure(ia2)

        self.assertIn(ActionStatus.FAILED, statuses, "payload 없으면 FAILED 보고 필요")


class SetMapSimulatorTest(unittest.TestCase):
    """시뮬레이터 모드: _map_raw 설정 + FINISHED."""

    def test_simulator_stores_map_raw_and_finishes(self) -> None:
        async def scenario() -> None:
            adapter = _make_adapter(is_simulator=True)
            adapter._loop = asyncio.get_running_loop()

            statuses = []
            original_update = adapter._update_instant_action_status

            def capture_status(action_id, status, **kwargs):
                statuses.append(status)
                original_update(action_id, status, **kwargs)

            adapter._update_instant_action_status = capture_status

            ia = _make_set_map_instant_actions(UAMAP_PAYLOAD)
            adapter.instant_actions_accept_procedure(ia)
            # 동기 경로이므로 await 필요 없지만 루프 순환 허용
            await asyncio.sleep(0)

            # FINISHED 보고 확인
            self.assertIn(ActionStatus.FINISHED, statuses,
                          "시뮬레이터 setMap은 FINISHED 보고 필요")

            # _map_raw에 raw dict가 설정됐는지 확인
            vehicle: FakeVehicle = adapter._vehicle
            self.assertIsNotNone(vehicle._map_raw,
                                 "_map_raw가 변환된 JIBOT raw dict로 설정되어야 함")
            # raw dict는 JIBOT 형식: Objs, Header 등 포함
            self.assertIn("Objs", vehicle._map_raw)

        asyncio.run(scenario())


class SetMapRealRobotTest(unittest.TestCase):
    """실로봇 모드: write_jibot_map 호출 + FINISHED."""

    def test_real_robot_calls_write_jibot_map_and_finishes(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                map_dir = Path(tmp) / "map"
                backup_root = Path(tmp) / "backups"

                adapter = _make_adapter(is_simulator=False)
                adapter._loop = asyncio.get_running_loop()

                statuses = []
                original_update = adapter._update_instant_action_status

                def capture_status(action_id, status, **kwargs):
                    statuses.append(status)
                    original_update(action_id, status, **kwargs)

                adapter._update_instant_action_status = capture_status

                # write_jibot_map을 패치해 tmp dir로 리다이렉트
                write_calls = []
                import jibot_map_writer as jmw

                original_write = jmw.write_jibot_map

                def fake_write(map_id, raw_map, *, map_dir=map_dir, backup_root=backup_root, keep=10, timestamp=None):
                    write_calls.append({"map_id": map_id, "raw_map": raw_map, "timestamp": timestamp})
                    return original_write(map_id, raw_map, map_dir=map_dir, backup_root=backup_root, keep=keep, timestamp=timestamp)

                with patch("adapter_jibot.write_jibot_map", fake_write):
                    ia = _make_set_map_instant_actions(UAMAP_PAYLOAD)
                    adapter.instant_actions_accept_procedure(ia)
                    # _run_on_adapter_loop으로 예약된 코루틴 실행 대기
                    await asyncio.sleep(0.1)

                # write_jibot_map 호출 확인
                self.assertEqual(len(write_calls), 1,
                                 "write_jibot_map이 정확히 1회 호출되어야 함")
                call = write_calls[0]
                self.assertEqual(call["map_id"], adapter._current_map_id)
                self.assertIn("Objs", call["raw_map"],
                              "raw_map은 JIBOT 포맷이어야 함")
                self.assertIsNotNone(call["timestamp"],
                                     "timestamp가 주입되어야 함")

                # FINISHED 보고 확인
                self.assertIn(ActionStatus.FINISHED, statuses,
                              "실로봇 setMap은 FINISHED 보고 필요")

        asyncio.run(scenario())

    def test_real_robot_uses_payload_map_id_if_present(self) -> None:
        """payload.map.mapId가 있으면 그것을 map_id로 사용."""
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                map_dir = Path(tmp) / "map"
                backup_root = Path(tmp) / "backups"

                adapter = _make_adapter(is_simulator=False)
                adapter._loop = asyncio.get_running_loop()
                # payload에 명시적 mapId 포함
                payload_with_map_id = dict(UAMAP_PAYLOAD)
                payload_with_map_id["map"] = dict(payload_with_map_id.get("map", {}))
                payload_with_map_id["map"]["mapId"] = "custom-map-999"

                write_calls = []
                import jibot_map_writer as jmw

                def fake_write(map_id, raw_map, **kwargs):
                    write_calls.append({"map_id": map_id})
                    map_dir.mkdir(parents=True, exist_ok=True)
                    return map_dir / f"{map_id}.json"

                with patch("adapter_jibot.write_jibot_map", fake_write):
                    ia = _make_set_map_instant_actions(payload_with_map_id)
                    adapter.instant_actions_accept_procedure(ia)
                    await asyncio.sleep(0.1)

                self.assertEqual(len(write_calls), 1)
                self.assertEqual(write_calls[0]["map_id"], "custom-map-999")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
