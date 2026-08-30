from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from parity_harness import (  # noqa: E402
    ParityResult,
    assert_same_operational_result,
    execute_scenario,
)


class Week1ParityHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_vendor_wire_differences_can_share_fms_visible_result(self):
        jibot_calls = [("UmStop", {"gap": -1})]
        seer_calls = [("stop", {}), ("cancel_navigation", {})]

        async def no_op():
            return None

        jibot = await execute_scenario(
            no_op,
            calls=jibot_calls,
            state={"motion": "stopped", "action": "FINISHED"},
        )
        seer = await execute_scenario(
            no_op,
            calls=seer_calls,
            state={"motion": "stopped", "action": "FINISHED"},
        )
        # The harness intentionally compares the operational/FMS result and
        # does not require vendor-specific wire calls to be identical.
        assert_same_operational_result(self, jibot, seer)
        self.assertNotEqual(jibot.calls, seer.calls)

    async def test_failure_is_normalized_for_parity_comparison(self):
        async def fail():
            raise RuntimeError("blocked")

        result = await execute_scenario(
            fail,
            calls=[("goto", {"id": "A"})],
            state={"motion": "stopped"},
        )
        expected = ParityResult(
            terminal_status="FAILED",
            calls=(("vendorGoto", {"goal": "A"}),),
            state={"motion": "stopped"},
            error="RuntimeError: blocked",
        )
        assert_same_operational_result(self, expected, result)


if __name__ == "__main__":
    unittest.main()
