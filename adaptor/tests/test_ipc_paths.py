import json
import unittest
from pathlib import Path

from core import ipc_paths


class IpcPathsTest(unittest.TestCase):
    def test_paths_are_under_runtime_root_keyed_by_serial(self):
        self.assertEqual(ipc_paths.runtime_dir("S1"), ipc_paths.RUNTIME_ROOT / "S1")
        self.assertEqual(ipc_paths.state_path("S1").name, "state.json")
        self.assertEqual(ipc_paths.health_path("S1").name, "health.json")
        self.assertEqual(ipc_paths.control_sock_path("S1").name, "control.sock")
        self.assertTrue(str(ipc_paths.state_path("S1")).endswith("amr-adaptor/S1/state.json"))

    def test_atomic_write_json_replaces_via_tmp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.json"
            ipc_paths.atomic_write_json(target, {"a": 1, "updated_at": 2.0})
            self.assertEqual(json.loads(target.read_text()), {"a": 1, "updated_at": 2.0})
            # no leftover tmp file
            self.assertEqual([p.name for p in Path(d).iterdir()], ["state.json"])

    def test_io_path_is_under_runtime_root(self):
        self.assertEqual(ipc_paths.io_path("S1").name, "io.json")
        self.assertTrue(str(ipc_paths.io_path("S1")).endswith("amr-adaptor/S1/io.json"))

    def test_runtime_dir_rejects_traversal_and_sanitizes(self):
        with self.assertRaises(ValueError):
            ipc_paths.runtime_dir("..")
        with self.assertRaises(ValueError):
            ipc_paths.runtime_dir("")
        # path separators collapse to a single safe component (no escape)
        self.assertEqual(ipc_paths.runtime_dir("a/b").name, "a_b")
        self.assertEqual(ipc_paths.runtime_dir("S1"), ipc_paths.RUNTIME_ROOT / "S1")


if __name__ == "__main__":
    unittest.main()
