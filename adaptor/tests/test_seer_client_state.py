"""Tests for SeerClient._process_status: SEER response -> cached state.

SeerClient._process_status 테스트: SEER 응답 -> 캐시 상태.

Pure mapping (no I/O): feed a parsed status payload + its API number, assert the
cached state attributes / contract properties update. Field names are taken from
the vendor reference (seer/SamDisplay/custom_package/seer_commu.py): x/y/angle,
battery_level/voltage/current/charging, task_status/target_id, blocked/
block_reason, current_map.
순수 매핑(I/O 없음): 파싱된 status 페이로드 + API 번호를 넣고 캐시 상태 속성/계약
프로퍼티가 갱신되는지 확인. 필드명은 벤더 참조에서 확인.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _src in ("amr-client-contract/src", "seer-client/src"):
    _p = str(REPO_ROOT / _src)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from seer_client import SeerClient  # noqa: E402
from seer_client.protocol import ApiNumber  # noqa: E402


class ProcessStatusTest(unittest.TestCase):
    def setUp(self):
        self.c = SeerClient("127.0.0.1")

    def test_loc_updates_pose(self):
        self.c._process_status(ApiNumber.STATUS_LOC, {"x": 1.5, "y": -2.0, "angle": 0.75})
        self.assertEqual((self.c.x, self.c.y, self.c.th), (1.5, -2.0, 0.75))

    def test_battery_maps_level_to_percent_and_caches_volts(self):
        self.c._process_status(
            ApiNumber.STATUS_BATTERY,
            {"battery_level": 0.42, "charging": True, "voltage": 48.1, "current": -3.2},
        )
        self.assertAlmostEqual(self.c.battery, 42.0)  # 0..1 fraction -> percent
        self.assertTrue(self.c.charging)
        self.assertEqual(self.c._battery_voltage, 48.1)
        self.assertEqual(self.c._battery_current, -3.2)

    def test_task_updates_status_and_target(self):
        self.c._process_status(ApiNumber.STATUS_TASK, {"task_status": 4, "target_id": "LM5"})
        self.assertEqual(self.c._task_status, 4)
        self.assertEqual(self.c._target_id, "LM5")

    def test_block_updates_blocked_and_reason(self):
        self.c._process_status(
            ApiNumber.STATUS_BLOCK, {"blocked": True, "block_reason": "obstacle ahead"}
        )
        self.assertTrue(self.c._blocked)
        self.assertEqual(self.c._block_reason, "obstacle ahead")

    def test_map_updates_current_map(self):
        self.c._process_status(
            ApiNumber.STATUS_MAP, {"current_map": "floor2", "maps": ["floor1", "floor2"]}
        )
        self.assertEqual(self.c._current_map, "floor2")

    def test_all1_updates_pose_and_battery_together(self):
        self.c._process_status(
            ApiNumber.STATUS_ALL1,
            {"x": 1.0, "y": 2.0, "angle": 0.5, "battery_level": 0.25, "charging": False},
        )
        self.assertEqual((self.c.x, self.c.y, self.c.th), (1.0, 2.0, 0.5))
        self.assertAlmostEqual(self.c.battery, 25.0)
        self.assertFalse(self.c.charging)

    def test_missing_fields_leave_state_unchanged(self):
        self.c._x = 9.9
        self.c._process_status(ApiNumber.STATUS_LOC, {})  # no x/y/angle
        self.assertEqual(self.c.x, 9.9)

    def test_unknown_api_number_is_noop(self):
        self.c._process_status(9999, {"x": 7.0})
        self.assertEqual(self.c.x, 0.0)


if __name__ == "__main__":
    unittest.main()
