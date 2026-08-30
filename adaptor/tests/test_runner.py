"""Functional tests for tui.runner.StreamProcess (spawns real subprocesses)."""

import sys
import time
import unittest

from core.runner import StreamProcess


def _wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class StreamProcessTest(unittest.TestCase):
    def test_captures_output_and_exit_code(self):
        sp = StreamProcess()
        sp.start([sys.executable, "-c", "print('hello-stream')"], label="echo")
        self.assertTrue(_wait_until(lambda: sp.returncode is not None), "process never finished")
        self.assertEqual(sp.returncode, 0)
        self.assertFalse(sp.running)
        joined = "\n".join(sp.lines())
        self.assertIn("hello-stream", joined)
        self.assertIn("[exit 0]", joined)

    def test_nonzero_exit(self):
        sp = StreamProcess()
        sp.start([sys.executable, "-c", "import sys; sys.exit(3)"])
        self.assertTrue(_wait_until(lambda: sp.returncode is not None))
        self.assertEqual(sp.returncode, 3)

    def test_failed_launch(self):
        sp = StreamProcess()
        sp.start(["/nonexistent/binary/xyzzy"])
        self.assertEqual(sp.returncode, 127)
        self.assertFalse(sp.running)

    def test_rapid_restart_state_is_consistent(self):
        # Start a long-lived process, then immediately relaunch a short one.
        # The fixed pump must not let the old process clobber the new state.
        sp = StreamProcess()
        sp.start([sys.executable, "-c", "import time; time.sleep(30)"], label="sleeper")
        sp.start([sys.executable, "-c", "print('second')"], label="quick")
        self.assertTrue(_wait_until(lambda: sp.returncode is not None))
        self.assertEqual(sp.returncode, 0)  # the quick process, not the killed sleeper
        joined = "\n".join(sp.lines())
        self.assertIn("second", joined)
        self.assertNotIn("sleeper", "".join(sp.label))  # label is the latest
        # exactly one exit marker for the current process
        self.assertEqual(joined.count("[exit"), 1)

    def test_stop_is_idempotent(self):
        sp = StreamProcess()
        sp.stop()  # nothing running
        sp.start([sys.executable, "-c", "print('x')"])
        self.assertTrue(_wait_until(lambda: sp.returncode is not None))
        sp.stop()
        sp.stop()
        self.assertFalse(sp.running)


if __name__ == "__main__":
    unittest.main()
