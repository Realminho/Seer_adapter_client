import asyncio
import unittest

from bms_ros_listener import BmsRosListener


class FakeVehicle:
    def __init__(self):
        self.safety = None
        self.cleared = 0

    def set_bms(self, v, c):
        pass

    def clear_bms(self):
        pass

    def set_robot_safety(self, safety):
        self.safety = safety

    def clear_robot_safety(self):
        self.cleared += 1


class FakeCfg:
    stale_after_sec = 30.0


class FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0).encode("utf-8")
        return b""  # EOF


HEADER = (
    "%time,field.system_status,field.motor_enable,field.hmi_estop,"
    "field.bumpe_stop,field.pc_estop,field.motor_error,field.pc_enable,"
    "field.bms_voltage,field.bms_current,field.charge\n"
)


class SafetyConsumeTest(unittest.TestCase):
    def test_consume_sets_robot_safety(self):
        veh = FakeVehicle()
        listener = BmsRosListener(veh, FakeCfg())
        row = "1,Press ON to Enable.,0,0,1,0,0,1,52.9,-0.8,0\n"
        stdout = FakeStdout([HEADER, row])
        asyncio.run(listener._consume(stdout))
        self.assertIsNotNone(veh.safety)
        self.assertEqual(veh.safety["system_status"], "Press ON to Enable.")
        self.assertEqual(veh.safety["hmi_estop"], "0")
        self.assertEqual(veh.cleared, 1)  # EOF triggers clear


class HeaderWithoutBmsConsumeTest(unittest.TestCase):
    def test_safety_parsed_when_header_lacks_bms_column(self):
        # bms_voltage/current 없는 헤더 + safety 컬럼만. 헤더로 인식해 safety 매핑해야 함.
        header = "%time,field.system_status,field.system_error_code,field.hmi_estop\n"
        row = "1,Normal,200,0\n"
        veh = FakeVehicle()
        listener = BmsRosListener(veh, FakeCfg())
        stdout = FakeStdout([header, row])
        asyncio.run(listener._consume(stdout))
        self.assertIsNotNone(veh.safety)
        self.assertEqual(veh.safety["system_error_code"], "200")
        self.assertEqual(veh.safety["system_status"], "Normal")


if __name__ == "__main__":
    unittest.main()
