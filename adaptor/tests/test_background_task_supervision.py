"""A dead adapter background task must be loud, not silent.

Regression cover for the 2026-08-18 incident on HN-SH6-TR-001: a partial deploy
left ``protocol/`` behind ``adapter_jibot.py``, so ``publish_state()`` raised
AttributeError on its first cycle. The Task died, asyncio swallowed the
exception ("Task exception was never retrieved" only surfaces at GC), and the
process stayed up -- JIBOT polling alive, connection=ONLINE -- while publishing
zero state for 16 minutes. The FMS froze on the last state it had received, so
its LAST_NODE_ID_MISSING error could never clear.
"""

import asyncio
import contextlib
import io
import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_ROOT.parent
for p in (ADAPTER_ROOT, REPO_ROOT / "jibot-simulator", REPO_ROOT / "jibot-client" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from adapter_jibot import Adapter


class BackgroundTaskSupervisionTest(unittest.IsolatedAsyncioTestCase):
    def _adapter_with_recorded_exit(self):
        """Adapter whose process-exit seam records instead of calling os._exit."""
        adapter = Adapter()
        lost = []
        adapter._terminate_after_task_loss = lambda name: lost.append(name)
        return adapter, lost

    async def test_supervised_task_raising_terminates_the_adapter(self):
        adapter, lost = self._adapter_with_recorded_exit()

        async def boom():
            raise AttributeError("type object 'ErrorType' has no attribute 'X'")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            task = adapter._supervise_task(asyncio.create_task(boom()), "publish_state")
            with contextlib.suppress(AttributeError):
                await task  # task completes; its done-callbacks are now scheduled
            await asyncio.sleep(0)  # ...and this turn runs them

        self.assertEqual(lost, ["publish_state"])
        # The traceback has to reach the journal: a silent death is the bug.
        output = buffer.getvalue()
        self.assertIn("publish_state", output)
        self.assertIn("AttributeError", output)

    async def test_supervised_task_returning_early_terminates_the_adapter(self):
        """The supervised loops are all `while True`; returning is also a fault."""
        adapter, lost = self._adapter_with_recorded_exit()

        async def returns_early():
            return None

        with contextlib.redirect_stdout(io.StringIO()):
            await adapter._supervise_task(
                asyncio.create_task(returns_early()), "subscribe_acs_cmd"
            )
            await asyncio.sleep(0)

        self.assertEqual(lost, ["subscribe_acs_cmd"])

    async def test_cancelled_task_does_not_terminate_the_adapter(self):
        """Shutdown cancels these tasks; that must not be reported as a fault."""
        adapter, lost = self._adapter_with_recorded_exit()

        async def forever():
            await asyncio.Event().wait()

        task = asyncio.create_task(forever())
        await asyncio.sleep(0)
        adapter._supervise_task(task, "publish_state")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        self.assertEqual(lost, [])

    async def test_run_adapter_supervises_the_state_publish_loop(self):
        """The wiring that would have caught the incident.

        run_adapter() must not hand publish_state to a bare create_task: when
        that coroutine dies the adapter has to go down so systemd restarts it
        and the FMS sees the Last Will, instead of freezing on a stale state.
        """
        adapter, lost = self._adapter_with_recorded_exit()

        async def dying_publish_state(topic_name, interval_sec=1):
            raise AttributeError("simulated partial-deploy mismatch")

        adapter.publish_state = dying_publish_state

        with contextlib.redirect_stdout(io.StringIO()):
            await adapter.run_adapter()
            await asyncio.sleep(0.05)
            for task in (
                adapter.publish_state_task,
                adapter.subscribe_task,
                adapter.control_server_task,
                adapter.connection_monitor_task,
            ):
                if task is not None:
                    task.cancel()
            await asyncio.sleep(0)

        self.assertIn("publish_state", lost)


if __name__ == "__main__":
    unittest.main()
