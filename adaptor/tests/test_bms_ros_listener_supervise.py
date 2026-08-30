import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from bms_ros_listener import BmsRosListener, start_bms_listener


class FakeConfig:
    enabled = True
    topic = "/jrobot_status"
    ros_setup = "/x/setup.bash"
    ros_master_uri = "http://localhost:11311"
    stale_after_sec = 0.05
    restart_backoff_sec = 0.0


class FakeVehicle:
    def __init__(self):
        self.clear_calls = 0

    def set_bms(self, voltage, current):
        pass

    def clear_bms(self):
        self.clear_calls += 1


class EofStdout:
    async def readline(self):
        return b""  # immediate EOF -> _run_once returns at once


class FakeProc:
    def __init__(self):
        self.stdout = EofStdout()
        self.returncode = None  # alive, like a real child -> _terminate calls terminate()
        self.terminated = False

    def terminate(self):
        self.terminated = True

    async def wait(self):
        return 0


class _StopLoop(Exception):
    pass


class CommandTest(unittest.TestCase):
    def test_command_sources_ros_and_echoes_topic(self):
        listener = BmsRosListener(FakeVehicle(), FakeConfig())
        cmd = listener.command()
        self.assertEqual(cmd[0], "bash")
        self.assertEqual(cmd[1], "-lc")
        self.assertIn("source /x/setup.bash", cmd[2])
        self.assertIn("export ROS_MASTER_URI=http://localhost:11311", cmd[2])
        self.assertIn("exec rostopic echo -p /jrobot_status", cmd[2])


class RunSupervisionTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_restarts_until_sleep_stops_it(self):
        vehicle = FakeVehicle()
        spawned = []

        async def fake_spawn():
            proc = FakeProc()
            spawned.append(proc)
            return proc

        calls = {"n": 0}

        async def fake_sleep(_):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise _StopLoop

        listener = BmsRosListener(vehicle, FakeConfig(), spawn=fake_spawn, sleep=fake_sleep)
        with self.assertRaises(_StopLoop):
            await listener.run()

        self.assertEqual(len(spawned), 3)               # respawned each cycle
        self.assertTrue(all(p.terminated for p in spawned))
        self.assertGreaterEqual(vehicle.clear_calls, 3)  # cleared between cycles

    async def test_run_disabled_does_not_spawn(self):
        vehicle = FakeVehicle()
        spawned = []

        async def fake_spawn():
            spawned.append(1)
            return FakeProc()

        cfg = FakeConfig()
        cfg.enabled = False
        listener = BmsRosListener(vehicle, cfg, spawn=fake_spawn)
        await listener.run()
        self.assertEqual(spawned, [])


class StartHelperTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_for_simulator(self):
        self.assertIsNone(start_bms_listener(FakeVehicle(), FakeConfig(), simulator=True))

    async def test_returns_none_when_disabled(self):
        cfg = FakeConfig()
        cfg.enabled = False
        self.assertIsNone(start_bms_listener(FakeVehicle(), cfg, simulator=False))

    async def test_returns_task_when_enabled(self):
        task = start_bms_listener(FakeVehicle(), FakeConfig(), simulator=False)
        self.assertIsNotNone(task)
        task.cancel()  # cancel before it yields -> no real subprocess spawned
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    unittest.main()
