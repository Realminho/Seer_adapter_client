import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from bms_ros_listener import BmsRosListener


class FakeConfig:
    enabled = True
    topic = "/jrobot_status"
    ros_setup = "/x/setup.bash"
    ros_master_uri = "http://localhost:11311"
    stale_after_sec = 0.05
    restart_backoff_sec = 0.01


class FakeVehicle:
    def __init__(self):
        self.set_calls = []
        self.clear_calls = 0

    def set_bms(self, voltage, current):
        self.set_calls.append((voltage, current))

    def clear_bms(self):
        self.clear_calls += 1

    def set_robot_safety(self, safety):
        pass

    def clear_robot_safety(self):
        pass


class ScriptedStream:
    """async readline() that yields queued byte lines then EOF (b'')."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class BlockingStream:
    async def readline(self):
        await asyncio.Event().wait()  # never returns -> triggers wait_for timeout


HEADER = b"%time,field.charge,field.EQ,field.bms_voltage,field.bms_current,field.system_status\n"


class ConsumeTest(unittest.IsolatedAsyncioTestCase):
    async def test_header_then_rows_cache_values(self):
        vehicle = FakeVehicle()
        listener = BmsRosListener(vehicle, FakeConfig())
        stream = ScriptedStream([
            HEADER,
            b"1700000000,1,88,54.6,7.2,Normal\n",
            b"1700000001,1,88,54.7,7.1,Normal\n",
        ])
        await listener._consume(stream)
        self.assertEqual(vehicle.set_calls, [(54.6, 7.2), (54.7, 7.1)])

    async def test_data_before_header_ignored(self):
        vehicle = FakeVehicle()
        listener = BmsRosListener(vehicle, FakeConfig())
        stream = ScriptedStream([b"1700000000,1,88,54.6,7.2,Normal\n"])
        await listener._consume(stream)
        self.assertEqual(vehicle.set_calls, [])

    async def test_staleness_clears(self):
        vehicle = FakeVehicle()
        listener = BmsRosListener(vehicle, FakeConfig())
        task = asyncio.ensure_future(listener._consume(BlockingStream()))
        await asyncio.sleep(0.12)  # > stale_after_sec, allows >=1 timeout cycle
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.assertGreaterEqual(vehicle.clear_calls, 1)


if __name__ == "__main__":
    unittest.main()
