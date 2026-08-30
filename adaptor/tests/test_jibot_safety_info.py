import asyncio
import time
import unittest

from tests._stop_reason_harness import make_adapter, start_state


def _safety_refs(safety, fresh=True):
    async def scenario():
        adapter = make_adapter()
        adapter._vehicle._robot_safety = safety
        adapter._vehicle._robot_safety_last_update = time.monotonic() if fresh else 0.0
        task = await start_state(adapter)
        try:
            await asyncio.sleep(0.1)
            blocks = [
                i for i in adapter.state.information
                if getattr(i, "info_type", None) == "JIBOT_SAFETY"
            ]
            assert len(blocks) == 1, f"expected exactly one JIBOT_SAFETY block, got {len(blocks)}"
            return {r.reference_key: r.reference_value for r in blocks[0].info_references}
        finally:
            task.cancel()
    return asyncio.run(scenario())


class JibotSafetyInfoTest(unittest.TestCase):
    def test_block_populated_when_fresh(self):
        refs = _safety_refs({
            "system_status": "Press ON to Enable.", "motor_enable": "0",
            "hmi_estop": "0", "bumpe_stop": "1", "pc_estop": "0",
            "motor_error": "0", "pc_enable": "1", "charge": "0",
        })
        self.assertEqual(refs["stopReason"], "MANUAL")
        self.assertEqual(refs["systemStatus"], "Press ON to Enable.")
        self.assertEqual(refs["bumperEstop"], "1")
        self.assertEqual(refs["motorEnable"], "0")
        self.assertEqual(refs["hmiEstop"], "0")
        self.assertEqual(refs["pcEstop"], "0")
        self.assertEqual(refs["motorError"], "0")
        self.assertEqual(refs["pcEnable"], "1")
        self.assertEqual(refs["charge"], "0")

    def test_block_empty_strings_when_stale(self):
        refs = _safety_refs({}, fresh=False)
        self.assertEqual(refs["stopReason"], "")
        self.assertEqual(refs["systemStatus"], "")
        self.assertEqual(refs["hmiEstop"], "")

    def test_system_error_code_decoded_when_fresh(self):
        refs = _safety_refs({
            "system_status": "ERR", "system_error_code": "200",
            "l_status": "0", "l_error": "0", "r_status": "1", "r_error": "0",
            "lift_status": "0", "rotate_status": "0",
        })
        self.assertEqual(refs["systemErrorCode"], "200")
        self.assertEqual(refs["systemErrorName"], "ERROR0200")
        self.assertEqual(refs["systemErrorDescription"], "robot do not find goal name in map")
        self.assertEqual(refs["wheelRightStatus"], "1")
        self.assertEqual(refs["wheelLeftError"], "0")
        self.assertEqual(refs["rotateStatus"], "0")

    def test_error_keys_present_and_blank_when_code_zero(self):
        refs = _safety_refs({"system_status": "Normal", "system_error_code": "0"})
        # 키는 항상 존재, 값만 빈 문자열 (stale-clear 방지).
        self.assertEqual(refs["systemErrorCode"], "0")
        self.assertEqual(refs["systemErrorName"], "")
        self.assertEqual(refs["systemErrorDescription"], "")

    def test_error_keys_present_and_blank_when_stale(self):
        refs = _safety_refs({"system_error_code": "200"}, fresh=False)
        for key in ("systemErrorCode", "systemErrorName", "systemErrorDescription",
                    "wheelLeftStatus", "wheelRightError", "liftStatus", "rotateStatus"):
            self.assertEqual(refs[key], "", f"{key} should be blank when stale")

    def test_error_clears_on_transition_200_to_0(self):
        import asyncio, time
        from tests._stop_reason_harness import make_adapter, start_state

        async def scenario():
            adapter = make_adapter()
            adapter._vehicle._robot_safety = {"system_error_code": "200"}
            adapter._vehicle._robot_safety_last_update = time.monotonic()
            task = await start_state(adapter)
            try:
                await asyncio.sleep(0.1)
                # 정상 복귀: code 0
                adapter._vehicle._robot_safety = {"system_error_code": "0"}
                adapter._vehicle._robot_safety_last_update = time.monotonic()
                await asyncio.sleep(0.1)
                block = next(i for i in adapter.state.information
                            if getattr(i, "info_type", None) == "JIBOT_SAFETY")
                refs = {r.reference_key: r.reference_value for r in block.info_references}
                return refs
            finally:
                task.cancel()

        refs = asyncio.run(scenario())
        self.assertEqual(refs["systemErrorCode"], "0")
        self.assertEqual(refs["systemErrorName"], "")
        self.assertEqual(refs["systemErrorDescription"], "")
