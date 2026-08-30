"""Tests for the JIBOT map snapshot store."""

import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

import tempfile

from jibot_map_store import load_map_snapshot, save_map_snapshot


class SaveMapSnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "jibot-map.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_preserves_nodes_and_raw(self):
        nodes = {"S1": (1.0, 2.0, 0.5), "S2": (3.0, 4.0, 0.0)}
        raw = {"Objs": {"Goal": [{"name": "S1", "pose": "1.0 2.0 0.5"}]}}

        saved = save_map_snapshot(
            self.path,
            map_id="map-1",
            nodes=nodes,
            raw=raw,
            serial_number="SIM-001",
        )
        self.assertTrue(saved)

        loaded = load_map_snapshot(self.path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["map_id"], "map-1")
        self.assertEqual(loaded["raw"], raw)
        self.assertEqual(loaded["nodes"], nodes)
        # Nodes come back as tuples, not lists.
        self.assertIsInstance(loaded["nodes"]["S1"], tuple)

    def test_two_element_pose_defaults_theta_to_zero(self):
        save_map_snapshot(
            self.path,
            map_id="map-1",
            nodes={"S1": (1.0, 2.0)},
            raw=None,
            serial_number="SIM-001",
        )
        loaded = load_map_snapshot(self.path)
        self.assertEqual(loaded["nodes"]["S1"], (1.0, 2.0, 0.0))

    def test_empty_nodes_not_saved(self):
        saved = save_map_snapshot(
            self.path,
            map_id="map-1",
            nodes={},
            raw={"Objs": {}},
            serial_number="SIM-001",
        )
        self.assertFalse(saved)
        self.assertFalse(self.path.exists())

    def test_save_does_not_clobber_when_only_bad_nodes(self):
        # All node values invalid -> nothing usable -> not saved.
        saved = save_map_snapshot(
            self.path,
            map_id="map-1",
            nodes={"S1": "garbage", "S2": [None, None]},
            raw=None,
            serial_number="SIM-001",
        )
        self.assertFalse(saved)


class LoadMapSnapshotTest(unittest.TestCase):
    def test_missing_file_returns_none(self):
        self.assertIsNone(load_map_snapshot(Path("/nonexistent/jibot-map.json")))

    def test_invalid_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jibot-map.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_map_snapshot(path))

    def test_no_nodes_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jibot-map.json"
            path.write_text('{"mapId": "m", "nodes": {}}', encoding="utf-8")
            self.assertIsNone(load_map_snapshot(path))


if __name__ == "__main__":
    unittest.main()
