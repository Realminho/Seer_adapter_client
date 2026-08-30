import unittest

from core.processes import ProcessMetrics
from core.status import compose_runtime_status
from core.systemd import ServiceMetrics


class RuntimeStatusTest(unittest.TestCase):
    def test_active_systemd_wins(self):
        svc = ServiceMetrics(
            exists=True,
            active_state="active",
            sub_state="running",
            main_pid=10,
            cpu_percent=1.2,
            memory_bytes=1000,
            uptime_sec=30,
            restarts=0,
        )
        proc = ProcessMetrics(
            pid=20,
            ppid=1,
            cpu_percent=3.0,
            memory_bytes=2000,
            uptime_sec=40,
            args="python main.py --id A",
        )

        status = compose_runtime_status(svc, proc)

        self.assertEqual(status.source, "systemd")
        self.assertEqual(status.state_text, "active")
        self.assertEqual(status.pid, 10)
        self.assertEqual(status.cpu_percent, 1.2)
        self.assertEqual(status.memory_bytes, 1000)
        self.assertEqual(status.uptime_sec, 30)
        self.assertEqual(status.restarts, 0)

    def test_manual_process_used_when_unit_missing(self):
        svc = ServiceMetrics(exists=False, active_state="unknown")
        proc = ProcessMetrics(
            pid=20,
            ppid=1,
            cpu_percent=3.0,
            memory_bytes=2048,
            uptime_sec=40,
            args="python main.py --id A",
        )

        status = compose_runtime_status(svc, proc)

        self.assertEqual(status.source, "process")
        self.assertEqual(status.state_text, "manual process")
        self.assertEqual(status.pid, 20)
        self.assertEqual(status.memory_bytes, 2048)
        self.assertEqual(status.uptime_sec, 40)
        self.assertIsNone(status.restarts)

    def test_missing_unit_without_process_is_not_installed(self):
        svc = ServiceMetrics(exists=False, active_state="unknown")

        status = compose_runtime_status(svc, None)

        self.assertEqual(status.source, "none")
        self.assertEqual(status.state_text, "not installed")
        self.assertIsNone(status.pid)

    def test_installed_inactive_unit_without_process_stays_inactive(self):
        svc = ServiceMetrics(
            exists=True,
            active_state="inactive",
            sub_state="dead",
            enabled_state="enabled",
        )

        status = compose_runtime_status(svc, None)

        self.assertEqual(status.source, "systemd")
        self.assertEqual(status.state_text, "inactive")

    def test_manual_process_used_when_unit_is_installed_but_inactive(self):
        svc = ServiceMetrics(exists=True, active_state="inactive", sub_state="dead", enabled_state="enabled")
        proc = ProcessMetrics(pid=20, ppid=1, cpu_percent=3.0, memory_bytes=2048, uptime_sec=40, args="python main.py --id A")

        status = compose_runtime_status(svc, proc)

        self.assertEqual(status.source, "process")
        self.assertEqual(status.state_text, "manual process")
        self.assertEqual(status.pid, 20)

    def test_manual_process_used_when_unit_failed(self):
        svc = ServiceMetrics(exists=True, active_state="failed", sub_state="failed", enabled_state="enabled")
        proc = ProcessMetrics(pid=20, ppid=1, cpu_percent=3.0, memory_bytes=2048, uptime_sec=40, args="python main.py --id A")

        status = compose_runtime_status(svc, proc)

        self.assertEqual(status.source, "process")
        self.assertEqual(status.state_text, "manual process")
        self.assertEqual(status.pid, 20)

    def test_failed_unit_without_process_stays_failed(self):
        svc = ServiceMetrics(exists=True, active_state="failed", sub_state="failed", enabled_state="enabled")

        status = compose_runtime_status(svc, None)

        self.assertEqual(status.source, "systemd")
        self.assertEqual(status.state_text, "failed")


if __name__ == "__main__":
    unittest.main()
