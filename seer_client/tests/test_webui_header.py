from __future__ import annotations

import html
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


SEER_CLIENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SEER_CLIENT_ROOT.parent
for source in (SEER_CLIENT_ROOT / "src", REPO_ROOT / "adaptor"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.webui import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    _decorate_seer_adapter_navigation,
    _seer_nickname_ip_label,
    _seer_file_monitor,
)


class _Render:
    @staticmethod
    def esc(value) -> str:
        return html.escape(str(value), quote=True)


class SeerWebUiHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SimpleNamespace(manufacturer="seer", key="seer:SEER-REAL-001")
        self.source = (
            '<header class="topbar">'
            '<nav class="topnav">'
            '<a class="nav-link" href="/actions">Actions</a>'
            '<a class="nav-link" href="/factsheet">Factsheet</a>'
            '</nav>'
            '<div class="head-actions">'
            '<a class="btn ghost" href="/adapter/seer:SEER-REAL-001/actions">actions</a>'
            '<a class="btn ghost" href="/adapter/seer:SEER-REAL-001/tests">tests</a>'
            '<a class="btn ghost" href="/adapter/seer:SEER-REAL-001/logs">logs</a>'
            '<div class="model-title">SEER Adaptor - SEER-REAL-001</div>'
            '<a class="btn">Refresh 1s</a>'
            '</div></header>'
        )

    def test_moves_tests_and_logs_after_factsheet_and_removes_local_duplicates(self) -> None:
        output = _decorate_seer_adapter_navigation(
            _Render, self.source, self.spec, current="dashboard"
        )

        self.assertIn('<a class="nav-link" href="/actions">Actions</a>', output)
        self.assertNotIn('href="/adapter/seer:SEER-REAL-001/actions"', output)
        self.assertNotIn('class="btn ghost" href="/adapter/seer:SEER-REAL-001/tests"', output)
        self.assertNotIn('class="btn ghost" href="/adapter/seer:SEER-REAL-001/logs"', output)
        self.assertLess(output.index("Factsheet"), output.index(">Tests</a>"))
        self.assertLess(output.index(">Tests</a>"), output.index(">Logs</a>"))
        self.assertLess(output.index(">Logs</a>"), output.index("</nav>"))
        self.assertIn("SEER Adaptor - SEER-REAL-001", output)
        self.assertIn("Refresh 1s", output)
        self.assertIn(
            '<div class="head-actions seer-detail-heading">',
            output,
        )

    def test_places_seer_title_group_after_navigation(self) -> None:
        output = _decorate_seer_adapter_navigation(
            _Render, self.source, self.spec, current="dashboard"
        )

        self.assertLess(output.index("</nav>"), output.index("seer-detail-heading"))
        self.assertLess(
            output.index("seer-detail-heading"),
            output.index("SEER Adaptor - SEER-REAL-001"),
        )

    def test_marks_robot_scoped_page_as_current(self) -> None:
        output = _decorate_seer_adapter_navigation(
            _Render, self.source, self.spec, current="logs"
        )

        self.assertIn(
            'href="/adapter/seer:SEER-REAL-001/logs" aria-current="page">Logs</a>',
            output,
        )
        self.assertNotIn(
            'href="/adapter/seer:SEER-REAL-001/tests" aria-current="page"',
            output,
        )

    def test_file_monitor_marks_live_adapter_offline_when_vehicle_link_is_lost(self) -> None:
        from core import ipc_paths  # pyright: ignore[reportMissingImports]

        serial = "SEER-OFFLINE-001"
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_root = ipc_paths.RUNTIME_ROOT
            ipc_paths.RUNTIME_ROOT = Path(temp_dir)
            try:
                runtime = ipc_paths.ensure_runtime_dir(serial)
                state = {
                    "headerId": 1,
                    "timestamp": "2026-08-24T00:00:00Z",
                    "version": "3.0.0",
                    "manufacturer": "seer",
                    "serialNumber": serial,
                    "driving": False,
                    "errors": [
                        {
                            "errorType": "JIBOT_CONNECTION_LOST",
                            "errorLevel": "FATAL",
                        }
                    ],
                    "updated_at": time.time(),
                }
                (runtime / "state.json").write_text(
                    json.dumps(state), encoding="utf-8"
                )
                monitor = _seer_file_monitor(serial)
                snapshot = monitor.get_snapshot()
                self.assertEqual(snapshot.connection_state, "OFFLINE")
                self.assertFalse(monitor.adapter_online)
                self.assertFalse(monitor.broker_connected)

                state["errors"] = []
                state["updated_at"] = time.time()
                (runtime / "state.json").write_text(
                    json.dumps(state), encoding="utf-8"
                )
                snapshot = monitor.get_snapshot()
                self.assertEqual(snapshot.connection_state, "ONLINE")
                self.assertTrue(monitor.adapter_online)
            finally:
                ipc_paths.RUNTIME_ROOT = previous_root

    def test_nickname_ip_label_uses_display_name_and_controller_ip(self) -> None:
        spec = SimpleNamespace(
            display_name="Minho_AMR", serial="SEER-REAL-001", vehicle_host="192.168.43.103"
        )
        self.assertEqual(
            _seer_nickname_ip_label(spec),
            "Minho_AMR - 192.168.43.103",
        )

    def test_leaves_non_seer_output_unchanged(self) -> None:
        jibot = SimpleNamespace(manufacturer="jibot", key="jibot")
        self.assertEqual(
            _decorate_seer_adapter_navigation(
                _Render, self.source, jibot, current="dashboard"
            ),
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
