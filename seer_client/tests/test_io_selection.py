from __future__ import annotations

import html
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SEER_CLIENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SEER_CLIENT_ROOT.parent
for source in (SEER_CLIENT_ROOT / "src", REPO_ROOT / "adaptor"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.webui import _render_seer_io_page  # noqa: E402


class _Render:
    @staticmethod
    def esc(value) -> str:
        return html.escape(str(value), quote=True)

    @staticmethod
    def _flash(_q) -> str:
        return ""

    @staticmethod
    def _with_poll(body: str, _refresh: int) -> str:
        return body

    @staticmethod
    def page(_title, body, **kwargs) -> str:
        return f"BRAND={kwargs.get('brand_title', '')}\n{body}"


class SeerIoSelectionTests(unittest.TestCase):
    def test_io_page_selects_one_amr_and_keeps_other_amrs_as_selector_only(self) -> None:
        first = SimpleNamespace(
            key="seer:one", display_name="AMR-ONE", serial="SEER-1", vehicle_host="192.168.43.103"
        )
        second = SimpleNamespace(
            key="seer:two", display_name="AMR-TWO", serial="SEER-2", vehicle_host="192.168.2.105"
        )
        output = _render_seer_io_page(
            _Render, [first, second], "csrf", {"robot": second.key, "refresh": "1"}
        )
        self.assertIn("AMR-ONE - 192.168.43.103", output)
        self.assertIn("AMR-TWO - 192.168.2.105", output)
        self.assertIn("BRAND=SEER I/O · AMR-TWO - 192.168.2.105", output)
        self.assertIn('class="seer-io-robot is-current"', output)
        self.assertEqual(output.count("상태 API 1013"), 1)
        self.assertIn("SEER-2 · 상태 API 1013", output)
        self.assertNotIn("SEER-1 · 상태 API 1013", output)


if __name__ == "__main__":
    unittest.main()
