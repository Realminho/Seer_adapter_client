"""Tests for core.monitor.FileMonitor (file-based read path for the WebUi)."""

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from core import ipc_paths, monitor


class FileMonitorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "state.json"
        self.health = self.dir / "health.json"
        self.io = self.dir / "io.json"
        patcher_s = mock.patch.object(ipc_paths, "state_path", return_value=self.state)
        patcher_h = mock.patch.object(ipc_paths, "health_path", return_value=self.health)
        patcher_i = mock.patch.object(ipc_paths, "io_path", return_value=self.io)
        patcher_s.start()
        patcher_h.start()
        patcher_i.start()
        self.addCleanup(patcher_s.stop)
        self.addCleanup(patcher_h.stop)
        self.addCleanup(patcher_i.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write_state(self, age_sec, **over):
        payload = {
            "safetyState": {"activeEmergencyStop": "NONE"},
            "powerSupply": {"stateOfCharge": 50.0},
            "updated_at": time.time() - age_sec,
        }
        payload.update(over)
        self.state.write_text(json.dumps(payload))

    def test_fresh_state_is_parsed_and_alive(self):
        self._write_state(age_sec=1.0)
        m = monitor.FileMonitor("S1", stale_after=15.0)
        snap = m.get_snapshot()
        self.assertEqual(snap.battery_soc, 50.0)
        self.assertTrue(m.broker_connected)
        self.assertTrue(m.adapter_online)
        self.assertIsNone(m.last_error)

    def test_stale_state_marks_not_alive(self):
        self._write_state(age_sec=60.0)
        m = monitor.FileMonitor("S1", stale_after=15.0)
        m.get_snapshot()
        self.assertFalse(m.broker_connected)
        self.assertFalse(m.adapter_online)

    def test_missing_file_returns_empty_snapshot(self):
        m = monitor.FileMonitor("S1")
        snap = m.get_snapshot()
        self.assertIsNone(snap.battery_soc)
        self.assertFalse(m.broker_connected)

    def test_corrupt_json_sets_last_error(self):
        self.state.write_text("{not json")
        m = monitor.FileMonitor("S1")
        m.get_snapshot()
        self.assertIsNotNone(m.last_error)

    def test_acs_broker_connected_read_from_health(self):
        self.health.write_text(json.dumps({"acs_broker_connected": True}))
        m = monitor.FileMonitor("S1")
        self.assertTrue(m.acs_broker_connected)
        self.health.write_text(json.dumps({"acs_broker_connected": False}))
        self.assertFalse(m.acs_broker_connected)

    def test_get_io_reads_io_json(self):
        self.io.write_text(json.dumps({
            "updated_at": 5.0,
            "ezio": {"configured": True, "connected": True, "inputs": [1, 0]},
            "pio": {"configured": True, "connected": False},
        }))
        m = monitor.FileMonitor("S1")
        io = m.get_io()
        self.assertTrue(io.ezio.configured)
        self.assertEqual(io.ezio.inputs, [1, 0])
        self.assertTrue(io.pio.configured)

    def test_get_io_missing_file_is_empty(self):
        m = monitor.FileMonitor("S1")
        io = m.get_io()
        self.assertIsNone(io.ezio)
        self.assertIsNone(io.pio)


if __name__ == "__main__":
    unittest.main()
