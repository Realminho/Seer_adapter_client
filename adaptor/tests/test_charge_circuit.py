import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from utils.charge_circuit import NullChargeCircuit, FakeChargeCircuit
from utils.charge_circuit import SubprocessChargeCircuit


class NullChargeCircuitTest(unittest.TestCase):
    def test_null_is_noop_but_tracks_state(self):
        cc = NullChargeCircuit()
        self.assertFalse(cc.is_holding)
        cc.start_hold()
        self.assertTrue(cc.is_holding)
        cc.stop_hold()
        self.assertFalse(cc.is_holding)


class FakeChargeCircuitTest(unittest.TestCase):
    def test_fake_records_calls(self):
        cc = FakeChargeCircuit()
        cc.start_hold()
        cc.start_hold()  # idempotent: still one hold
        self.assertTrue(cc.is_holding)
        self.assertEqual(cc.start_calls, 1)
        cc.stop_hold()
        self.assertFalse(cc.is_holding)
        self.assertEqual(cc.stop_calls, 1)


class _Cfg:
    jcmd_topic = "/jcmd"
    publish_rate_hz = 2
    ros_setup = "/usr/local/urobot/jarvis/setup.bash"
    ros_master_uri = "http://localhost:11311"


class _FakeProc:
    def __init__(self, wait_raises=None):
        self.terminated = False
        self.killed = False
        self.waited = False
        self._wait_raises = wait_raises

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self._wait_raises = None  # the kill settles it; the next wait returns

    def wait(self, timeout=None):
        self.waited = True
        self.wait_timeout = timeout
        if self._wait_raises is not None:
            raise self._wait_raises
        return 0


class SubprocessChargeCircuitTest(unittest.TestCase):
    def setUp(self):
        self.spawned = []

        def spawn(cmd):
            p = _FakeProc()
            self.spawned.append((cmd, p))
            return p

        self.spawn = spawn

    def test_hold_command_closes_relay(self):
        cc = SubprocessChargeCircuit(_Cfg(), spawn=self.spawn)
        cmd = cc.hold_command()
        joined = " ".join(cmd)
        self.assertIn("rostopic pub -r 2 /jcmd jarvis_msgs/Cmd", joined)
        self.assertIn("arg_int8: 1", joined)
        self.assertIn("source /usr/local/urobot/jarvis/setup.bash", joined)
        self.assertIn("ROS_MASTER_URI=http://localhost:11311", joined)

    def test_off_command_opens_relay_once(self):
        cc = SubprocessChargeCircuit(_Cfg(), spawn=self.spawn)
        joined = " ".join(cc.off_command())
        self.assertIn("rostopic pub -1 /jcmd jarvis_msgs/Cmd", joined)
        self.assertIn("arg_int8: 0", joined)

    def test_start_then_stop_lifecycle(self):
        cc = SubprocessChargeCircuit(_Cfg(), spawn=self.spawn)
        self.assertFalse(cc.is_holding)
        cc.start_hold()
        self.assertTrue(cc.is_holding)
        self.assertEqual(len(self.spawned), 1)  # the hold publisher
        cc.start_hold()  # idempotent: no second spawn
        self.assertEqual(len(self.spawned), 1)
        cc.stop_hold()
        self.assertFalse(cc.is_holding)
        hold_proc = self.spawned[0][1]
        self.assertTrue(hold_proc.terminated)       # hold killed
        self.assertEqual(len(self.spawned), 2)      # off publisher spawned


class OpenRelayTimeoutTest(unittest.TestCase):
    """A slow relay-OFF publish must not be left running.

    `rostopic pub -1` latches for ~3s after sourcing the ROS env, so on a busy
    board it can outlive the timeout with the publish still in flight. Waiting
    without killing leaves a process that still talks to /jcmd.
    """

    def _circuit(self, off_raises):
        self.spawned = []

        def spawn(cmd):
            # Only the relay-OFF one-shot is armed to time out; the hold
            # publisher must still tear down normally.
            p = _FakeProc(wait_raises=off_raises if self.spawned else None)
            self.spawned.append(p)
            return p

        return SubprocessChargeCircuit(_Cfg(), spawn=spawn)

    def test_timed_out_open_relay_is_killed(self):
        cc = self._circuit(subprocess.TimeoutExpired(cmd="rostopic", timeout=15.0))
        cc.start_hold()
        cc.stop_hold()
        off_proc = self.spawned[1]
        self.assertTrue(off_proc.killed, "orphaned relay-OFF publisher was not killed")
        self.assertFalse(cc.is_holding)

    def test_failed_open_relay_is_killed(self):
        cc = self._circuit(OSError("boom"))
        cc.start_hold()
        cc.stop_hold()
        self.assertTrue(self.spawned[1].killed)
        self.assertFalse(cc.is_holding)

    def test_default_off_timeout_leaves_room_for_the_latch(self):
        # 5s was not enough on the real board: the publish landed ~1.5s past
        # the deadline, so the fallback used when a config omits the field has
        # to cover sourcing the ROS env plus the ~3s latch.
        cc = self._circuit(None)
        cc.start_hold()
        cc.stop_hold()
        self.assertGreaterEqual(self.spawned[1].wait_timeout, 10.0)


if __name__ == "__main__":
    unittest.main()
