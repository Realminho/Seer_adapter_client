from __future__ import annotations

import base64
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


SIM_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SIM_ROOT.parent
ADAPTOR_ROOT = REPO_ROOT / "adaptor"
for source in (
    REPO_ROOT,
    ADAPTOR_ROOT,
    REPO_ROOT / "seer_client" / "src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_simulator import SimulatedSEER  # pyright: ignore[reportMissingImports]
from seer_client.bridge import install_into_adapter_main  # pyright: ignore[reportMissingImports]
from seer_client.webui import DropInPaths, SeerWebUiApplication  # pyright: ignore[reportMissingImports]
from seer_client.vda5050_console import Vda5050Identity, Vda5050MessageFactory  # pyright: ignore[reportMissingImports]


class SeerDropInWebUiTest(unittest.TestCase):
    def _paths(self, temp: str) -> DropInPaths:
        root = Path(temp)
        return DropInPaths(
            runtime_dir=root / "runtime",
            config_path=root / "runtime" / "config.toml",
            robots_path=root / "runtime" / "robots.toml",
            log_path=root / "runtime" / "adapter.log",
            ipc_root=root / "ipc",
        )

    def test_simulator_path_preserves_recipe_execution_action_id(self):
        factory = Vda5050MessageFactory(Vda5050Identity(serial_number="SEER-SIM-001"))
        payload = factory.instant_action("Demo", action_id="Demo-001")
        self.assertEqual(payload["actions"][0]["actionId"], "Demo-001")

        pause = factory.instant_action("startPause")
        resume = factory.instant_action("stopPause")
        self.assertEqual(pause["actions"][0]["actionId"], "startPause-001")
        self.assertEqual(resume["actions"][0]["actionId"], "stopPause-001")

    def test_bridge_injects_dedicated_seer_simulator(self):
        adapter_main = mock.Mock()
        with mock.patch.dict(sys.modules):
            install_into_adapter_main(adapter_main)
            injected = sys.modules["cls_jibot_simulator"].SimulatedJIBOT
        self.assertEqual(adapter_main.JIBOT.__name__, "SeerAdapterClient")
        self.assertIs(injected, SimulatedSEER)

    def test_dropin_command_selects_simulator_without_fleet_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            app = SeerWebUiApplication(
                serial="SEER-DROPIN",
                simulator=True,
                vehicle_ip="",
                username="operator",
                password="safe-seer-1234",
                port=0,
                x=1000,
                y=-500,
                theta=90,
                battery=55,
                auto_start=False,
                paths=self._paths(temp),
            )
            command = list(app.adapter_command())
        self.assertIn("--simulator", command)
        self.assertIn("--seer-state-port", command)
        self.assertIn("--config", command)
        self.assertEqual(command[command.index("--id") + 1], "SEER-DROPIN")

    def test_original_webui_renders_seer_with_local_controller(self):
        with tempfile.TemporaryDirectory() as temp:
            app = SeerWebUiApplication(
                serial="SEER-DROPIN",
                simulator=True,
                vehicle_ip="",
                username="operator",
                password="safe-seer-1234",
                port=0,
                auto_start=False,
                paths=self._paths(temp),
            )
            web = app.build_webui()
            spec = web._specs["seer"]
            self.assertEqual(spec.manufacturer, "seer")
            self.assertEqual(spec.display_name, "SEER Adapter")
            self.assertNotIn("startCharging", {a.action_type for a in spec.instant_actions})

            web.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{web.port}/adapter/seer"
                )
                token = base64.b64encode(b"operator:safe-seer-1234").decode("ascii")
                request.add_header("Authorization", f"Basic {token}")
                with urllib.request.urlopen(request, timeout=3) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("SEER Adapter", page)
                self.assertIn("SEER Adapter - 127.0.0.1", page)
            finally:
                web.stop()
                app.controller.stop()


if __name__ == "__main__":
    unittest.main()
