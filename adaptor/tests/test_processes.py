import subprocess
import unittest
from unittest.mock import patch

from core import processes


class ProcessParsingTest(unittest.TestCase):
    def test_parse_elapsed_seconds(self):
        self.assertEqual(processes.parse_elapsed_seconds("42"), 42)
        self.assertEqual(processes.parse_elapsed_seconds("03:04"), 184)
        self.assertEqual(processes.parse_elapsed_seconds("02:03:04"), 7384)
        self.assertEqual(processes.parse_elapsed_seconds("1-02:03:04"), 93784)
        self.assertIsNone(processes.parse_elapsed_seconds("-"))

    def test_extract_serial_from_main_py_args(self):
        self.assertEqual(
            processes.extract_serial(["python", "main.py", "--id", "HN-SH6-TR-002"]),
            "HN-SH6-TR-002",
        )
        self.assertEqual(
            processes.extract_serial(["python", "main.py", "--serial-number=HN-SH6-TR-003"]),
            "HN-SH6-TR-003",
        )
        self.assertEqual(
            processes.extract_serial(["python", "main.py", "--robot", "HN-SH6-TR-004"]),
            "HN-SH6-TR-004",
        )
        self.assertEqual(
            processes.extract_serial(["python", "/path/to/main.py", "--serial-number=HN"]),
            "HN",
        )
        self.assertEqual(
            processes.extract_serial(["/path/to/main.py", "--id", "HN"]),
            "HN",
        )
        self.assertIsNone(processes.extract_serial(["python", "main.py"]))
        self.assertIsNone(processes.extract_serial(["python", "/tmp/notmain.py", "--id", "BAD"]))
        self.assertIsNone(
            processes.extract_serial(
                ["python", "helper.py", "--id", "BAD", "--input", "/tmp/main.py"]
            )
        )

    def test_parse_ps_rows_detects_main_py_processes(self):
        text = """  PID  PPID %CPU   RSS     ELAPSED COMMAND
 1234     1  2.5 51200       03:04 /home/u/adaptor/venvJIBOT/bin/python /home/u/adaptor/main.py --id HN-SH6-TR-002 --mqtt-host 192.168.3.108 --mqtt-port 11883
 5678     1  0.1 10240       00:10 /usr/bin/python unrelated.py
"""

        found = processes.parse_ps(text)

        self.assertIn("HN-SH6-TR-002", found)
        metric = found["HN-SH6-TR-002"]
        self.assertEqual(metric.pid, 1234)
        self.assertEqual(metric.ppid, 1)
        self.assertEqual(metric.cpu_percent, 2.5)
        self.assertEqual(metric.memory_bytes, 51200 * 1024)
        self.assertEqual(metric.uptime_sec, 184)
        self.assertIn("--mqtt-host", metric.args)

    def test_parse_ps_rows_keep_manual_mqtt_overrides(self):
        text = """  PID  PPID %CPU   RSS     ELAPSED COMMAND
 1234     1  2.5 51200       03:04 /usr/bin/python /home/u/adaptor/main.py --mqtt-host 192.168.2.108 --mqtt-port 11883 --simulator --id HN-SH6-TR-001
"""

        found = processes.parse_ps(text)

        metric = found["HN-SH6-TR-001"]
        self.assertEqual(metric.mqtt_host, "192.168.2.108")
        self.assertEqual(metric.mqtt_port, 11883)


class ProcessScannerTest(unittest.TestCase):
    def test_poll_parses_successful_ps_stdout(self):
        stdout = """  PID  PPID %CPU   RSS     ELAPSED COMMAND
 1234     1  2.5 51200       03:04 /usr/bin/python /home/u/adaptor/main.py --id HN
"""
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)

        with patch("core.processes.subprocess.run", return_value=completed) as run:
            found = processes.ProcessScanner(timeout=1.5).poll()

        self.assertIn("HN", found)
        self.assertEqual(found["HN"].pid, 1234)
        run.assert_called_once_with(
            ["ps", "-eo", "pid,ppid,pcpu,rss,etime,args"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )

    def test_poll_returns_empty_when_ps_missing(self):
        with patch("core.processes.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(processes.ProcessScanner().poll(), {})

    def test_poll_returns_empty_when_ps_times_out(self):
        error = subprocess.TimeoutExpired(cmd=["ps"], timeout=3.0)
        with patch("core.processes.subprocess.run", side_effect=error):
            self.assertEqual(processes.ProcessScanner().poll(), {})

    def test_poll_returns_empty_when_ps_fails(self):
        stdout = """  PID  PPID %CPU   RSS     ELAPSED COMMAND
 1234     1  2.5 51200       03:04 /usr/bin/python /home/u/adaptor/main.py --id HN
"""
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout)

        with patch("core.processes.subprocess.run", return_value=completed):
            self.assertEqual(processes.ProcessScanner().poll(), {})


if __name__ == "__main__":
    unittest.main()
