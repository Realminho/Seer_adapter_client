"""Tests for tui.systemd pure parsers and metric calculations."""

import subprocess
import unittest
from unittest.mock import patch

from core import systemd


class ParseShowTest(unittest.TestCase):
    def test_parses_key_values(self):
        text = "ActiveState=active\nSubState=running\nMainPID=42\n"
        props = systemd.parse_show(text)
        self.assertEqual(props["ActiveState"], "active")
        self.assertEqual(props["MainPID"], "42")

    def test_value_with_equals_sign(self):
        props = systemd.parse_show("ExecStart={ path=/x ; argv[]=/x }")
        self.assertEqual(props["ExecStart"], "{ path=/x ; argv[]=/x }")


class CpuPercentTest(unittest.TestCase):
    def test_basic_rate(self):
        # 1 CPU-second consumed over 2 wall seconds -> 50%.
        pct = systemd.compute_cpu_percent(0, 100.0, 1_000_000_000, 102.0)
        self.assertAlmostEqual(pct, 50.0)

    def test_missing_baseline_returns_none(self):
        self.assertIsNone(systemd.compute_cpu_percent(None, 100.0, 1_000_000_000, 101.0))
        self.assertIsNone(systemd.compute_cpu_percent(0, None, 1_000_000_000, 101.0))

    def test_counter_reset_returns_none(self):
        # cur < prev means the service restarted; no meaningful rate.
        self.assertIsNone(systemd.compute_cpu_percent(2_000_000_000, 100.0, 1_000_000_000, 101.0))

    def test_zero_interval_returns_none(self):
        self.assertIsNone(systemd.compute_cpu_percent(0, 100.0, 1_000_000_000, 100.0))


class FormatTest(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(systemd.format_bytes(None), "-")
        self.assertEqual(systemd.format_bytes(512), "512 B")
        self.assertEqual(systemd.format_bytes(52428800), "50.0 MB")

    def test_format_uptime(self):
        self.assertEqual(systemd.format_uptime(None), "-")
        self.assertEqual(systemd.format_uptime(-1), "-")
        self.assertEqual(systemd.format_uptime(45), "45s")
        self.assertEqual(systemd.format_uptime(100), "1m 40s")
        self.assertEqual(systemd.format_uptime(3700), "1h 1m")
        self.assertEqual(systemd.format_uptime(90000), "1d 1h")


class SummarizeTest(unittest.TestCase):
    def test_active_with_uptime_and_cpu(self):
        props = {
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "UnitFileState": "enabled",
            "MainPID": "1234",
            "NRestarts": "3",
            "MemoryCurrent": "10485760",
            "CPUUsageNSec": "2000000000",
            "ExecMainStartTimestampMonotonic": "10000000",  # 10 s after boot
        }
        metrics = systemd.summarize(
            props, now_monotonic=70.0, prev_cpu_nsec=0, prev_cpu_wall=None
        )
        self.assertTrue(metrics.exists)
        self.assertTrue(metrics.is_active)
        self.assertEqual(metrics.main_pid, 1234)
        self.assertEqual(metrics.restarts, 3)
        self.assertEqual(metrics.memory_bytes, 10485760)
        self.assertAlmostEqual(metrics.uptime_sec, 60.0, places=1)

    def test_not_found_unit(self):
        metrics = systemd.summarize({"LoadState": "not-found"}, now_monotonic=1.0)
        self.assertFalse(metrics.exists)

    def test_uint64_sentinel_becomes_none(self):
        props = {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "MemoryCurrent": "18446744073709551615",
            "CPUUsageNSec": "18446744073709551615",
            "MainPID": "0",
        }
        metrics = systemd.summarize(props, now_monotonic=1.0)
        self.assertIsNone(metrics.memory_bytes)
        self.assertIsNone(metrics.cpu_nsec)
        self.assertIsNone(metrics.main_pid)
        self.assertIsNone(metrics.uptime_sec)  # not active -> no uptime


class EdgeAgentUnitParsingTest(unittest.TestCase):
    def test_parse_template_instance(self):
        unit = systemd.parse_edge_agent_unit("edge-agent@cell-a.service")

        self.assertEqual(
            unit,
            systemd.EdgeAgentUnit(id="cell-a", unit="edge-agent@cell-a.service"),
        )

    def test_parse_non_template_fallback(self):
        unit = systemd.parse_edge_agent_unit("edge-agent.service")

        self.assertEqual(
            unit,
            systemd.EdgeAgentUnit(id="default", unit="edge-agent.service"),
        )

    def test_parse_ignores_template_and_malformed_names(self):
        self.assertIsNone(systemd.parse_edge_agent_unit("edge-agent@.service"))
        self.assertIsNone(systemd.parse_edge_agent_unit("edge-agent@bad"))
        self.assertIsNone(systemd.parse_edge_agent_unit("other@cell.service"))

    def test_parse_unit_listing_deduplicates_in_order(self):
        text = """edge-agent@cell-b.service loaded active running Edge Agent B
edge-agent.service loaded inactive dead Edge Agent
edge-agent@cell-b.service enabled
edge-agent@.service disabled
bad.service enabled
"""
        units = systemd.parse_edge_agent_units(text)

        self.assertEqual(
            units,
            [
                systemd.EdgeAgentUnit(
                    id="cell-b", unit="edge-agent@cell-b.service"
                ),
                systemd.EdgeAgentUnit(id="default", unit="edge-agent.service"),
            ],
        )


class EdgeAgentDiscoveryTest(unittest.TestCase):
    def test_discovery_merges_list_units_and_unit_files(self):
        calls = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="edge-agent@cell-a.service loaded active running Edge Agent A\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="edge-agent@cell-b.service enabled\nedge-agent.service enabled\n",
                stderr="",
            ),
        ]

        # systemctl이 없는 기계(개발용 macOS)에서는 discover가 subprocess를 부르기
        # 전에 빈 리스트로 빠진다. which를 고정하지 않으면 이 시험은 리눅스에서만
        # 돌고 그 밖에서는 "합쳤다"가 아니라 "아무것도 안 했다"를 통과시킨다.
        with patch("core.systemd.shutil.which", return_value="/bin/systemctl"), patch(
            "core.systemd.subprocess.run", side_effect=calls
        ):
            units = systemd.discover_edge_agent_units(timeout=1.0)

        self.assertEqual(
            units,
            [
                systemd.EdgeAgentUnit(
                    id="cell-a", unit="edge-agent@cell-a.service"
                ),
                systemd.EdgeAgentUnit(
                    id="cell-b", unit="edge-agent@cell-b.service"
                ),
                systemd.EdgeAgentUnit(id="default", unit="edge-agent.service"),
            ],
        )

    def test_discovery_returns_empty_when_systemctl_missing_or_fails(self):
        with patch("core.systemd.shutil.which", return_value=None):
            self.assertEqual(systemd.discover_edge_agent_units(), [])

        # 아래 둘은 systemctl이 있는데 실패하는 경우다. which를 고정하지 않으면
        # systemctl 없는 기계에서 위의 "없음" 경로로 빠져 늘 통과한다.
        failed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom"
        )
        with patch("core.systemd.shutil.which", return_value="/bin/systemctl"), patch(
            "core.systemd.subprocess.run", return_value=failed
        ):
            self.assertEqual(systemd.discover_edge_agent_units(), [])

        with patch(
            "core.systemd.shutil.which", return_value="/bin/systemctl"
        ), patch(
            "core.systemd.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["systemctl"], timeout=1.0),
        ):
            self.assertEqual(systemd.discover_edge_agent_units(timeout=1.0), [])


class SystemctlLifecycleTest(unittest.TestCase):
    """start/stop/restart enqueue a systemd job that blocks until the unit
    settles. Heavy units (urobot's ROS stack) outlast our subprocess timeout,
    so without --no-block the systemctl client gets killed and we report a
    false "timed out" even though systemd finishes the job. --no-block returns
    as soon as the job is enqueued. enable/disable stay synchronous."""

    @staticmethod
    def _ok():
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def _run_verb(self, verb, use_sudo=False, unit="urobot.service"):
        with patch("core.systemd.shutil.which", return_value="/usr/bin/systemctl"), \
                patch("core.systemd.subprocess.run", return_value=self._ok()) as run:
            ok, msg = getattr(systemd.SystemdController(unit, use_sudo=use_sudo), verb)()
        return ok, msg, run.call_args.args[0]

    def test_restart_uses_no_block(self):
        ok, _, argv = self._run_verb("restart")
        self.assertTrue(ok)
        self.assertEqual(argv, ["systemctl", "restart", "--no-block", "urobot.service"])

    def test_start_and_stop_use_no_block(self):
        self.assertEqual(
            self._run_verb("start")[2], ["systemctl", "start", "--no-block", "urobot.service"]
        )
        self.assertEqual(
            self._run_verb("stop")[2], ["systemctl", "stop", "--no-block", "urobot.service"]
        )

    def test_enable_disable_stay_blocking(self):
        self.assertEqual(self._run_verb("enable")[2], ["systemctl", "enable", "urobot.service"])
        self.assertEqual(self._run_verb("disable")[2], ["systemctl", "disable", "urobot.service"])

    def test_no_block_composes_with_sudo(self):
        self.assertEqual(
            self._run_verb("restart", use_sudo=True)[2],
            ["sudo", "-n", "systemctl", "restart", "--no-block", "urobot.service"],
        )


class HostControllerTest(unittest.TestCase):
    def test_reboot_uses_systemctl_without_sudo_when_polkit_mode(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("core.systemd.shutil.which", return_value="/usr/bin/systemctl"), \
                patch("core.systemd.subprocess.run", return_value=proc) as run:
            ok, msg = systemd.HostController(use_sudo=False).reboot()

        self.assertTrue(ok)
        self.assertEqual(msg, "reboot requested")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["systemctl", "reboot"])

    def test_reboot_uses_sudo_when_requested(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("core.systemd.shutil.which", return_value="/usr/bin/systemctl"), \
                patch("core.systemd.subprocess.run", return_value=proc) as run:
            ok, _ = systemd.HostController(use_sudo=True).reboot()

        self.assertTrue(ok)
        self.assertEqual(run.call_args.args[0], ["sudo", "-n", "systemctl", "reboot"])


if __name__ == "__main__":
    unittest.main()
