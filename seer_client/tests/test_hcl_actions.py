from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SEER_CLIENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SEER_CLIENT_ROOT.parent
for source in (SEER_CLIENT_ROOT / "src", REPO_ROOT / "adaptor"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.hcl_config import ensure_hcl_files  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.webui import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    DropInPaths,
    SeerWebUiApplication,
    _suppress_client_disconnect_tracebacks,
)


class SeerHclActionsTests(unittest.TestCase):
    def test_expected_browser_disconnect_does_not_print_server_traceback(self) -> None:
        unexpected = []

        class Server:
            def handle_error(self, request, client_address):
                unexpected.append((request, client_address))

        server = Server()
        _suppress_client_disconnect_tracebacks(SimpleNamespace(_server=server))
        try:
            raise ConnectionAbortedError(10053, "browser canceled request")
        except ConnectionAbortedError:
            server.handle_error(object(), ("127.0.0.1", 12345))
        self.assertEqual(unexpected, [])
        self.assertTrue(server._seer_disconnect_filter)

        try:
            raise ValueError("real handler failure")
        except ValueError:
            server.handle_error("request", ("127.0.0.1", 12345))
        self.assertEqual(len(unexpected), 1)

    def test_fleet_extensions_are_shared_but_recipes_are_isolated_per_amr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "runtime"
            first = DropInPaths.for_robot("SEER-1", runtime_root=root)
            second = DropInPaths.for_robot("SEER-2", runtime_root=root)

            self.assertEqual(first.extensions_path, second.extensions_path)
            self.assertNotEqual(first.recipes_path, second.recipes_path)
            self.assertEqual(first.recipes_path.parent, first.runtime_dir)
            self.assertEqual(second.recipes_path.parent, second.runtime_dir)

    def test_webui_loads_seer_actions_recipes_and_source_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "seer_client.dropin_control.reserve_local_port", return_value=43101
        ):
            root = Path(temp_dir)
            paths = DropInPaths(
                runtime_dir=root / "runtime",
                config_path=root / "runtime" / "config.toml",
                robots_path=root / "runtime" / "robots.toml",
                log_path=root / "runtime" / "seer.log",
                ipc_root=root / "runtime" / "ipc",
                hcl_dir=root / "runtime",
            )
            app = SeerWebUiApplication(
                serial="SEER-HCL-TEST",
                simulator=True,
                vehicle_ip="",
                username="seer",
                password="safe-hcl-test-123",
                paths=paths,
                auto_start=False,
            )
            web = app.build_webui()
            try:
                spec = web._specs["seer"]
                module_types = {
                    action.action_type
                    for module in spec.action_modules
                    for action in module.actions
                }
                recipe_types = {recipe.action_type for recipe in spec.recipes}
                self.assertEqual(
                    module_types,
                    {
                        "seerPathNav",
                        "seerCoordinateNav",
                        "seerTranslate",
                        "seerTurn",
                        "seerSetDO",
                        "seerWait",
                    },
                )
                self.assertEqual(
                    recipe_types, {"seerNavigateToPoint", "seerPulseDO"}
                )
                instant_types = [action.action_type for action in spec.instant_actions]
                self.assertEqual(
                    instant_types[:4],
                    ["stateRequest", "startPause", "stopPause", "factsheetRequest"],
                )
                self.assertEqual(
                    web._hcl_paths()["extensions.hcl"], paths.extensions_path
                )
                self.assertEqual(web._hcl_paths()["recipes.hcl"], paths.recipes_path)
                self.assertEqual(web._validate_config_sources()[0], True)

                from web import render  # pyright: ignore[reportMissingImports]

                page = render.actions_page(spec, web._csrf, {})
                self.assertIn("SEER TCP/IP Actions", page)
                self.assertIn('name="navigation_mode"', page)
                self.assertIn('name="route_points"', page)
                self.assertIn('name="linear_speed_mps"', page)
                self.assertIn('name="angular_speed_deg_s"', page)
                self.assertIn('name="id"', page)
                self.assertNotIn('name="action_type" value="seerJackLoad"', page)
                self.assertNotIn('name="action_type" value="seerJackUnload"', page)
                self.assertIn('name="confirm" required', page)
                emergency_card = render._emergency_forms(
                    spec,
                    web._csrf,
                    return_to="dashboard",
                    snapshot=SimpleNamespace(active_emergency_stop="NONE"),
                )
                self.assertNotIn("seerJackLoad", emergency_card)
                self.assertNotIn("seerJackUnload", emergency_card)

                dashboard = render._control_forms(
                    spec,
                    web._csrf,
                    return_to="dashboard",
                    snapshot=SimpleNamespace(active_emergency_stop="NONE"),
                )
                self.assertIn(">Vehicle actions</h2>", dashboard)
                self.assertIn("SEER - Jack", dashboard)
                self.assertIn('class="action-card seer-jack-vehicle-action is-disabled"', dashboard)
                self.assertIn("Jack 지원 여부 확인 중", dashboard)
                self.assertIn("disabled", dashboard)
                self.assertIn('value="seerCancelActiveAction"', dashboard)
                self.assertIn("Action 취소", dashboard)
                self.assertIn('value="seerResetActionErrors"', dashboard)
                self.assertIn("Action 오류 리셋", dashboard)
                self.assertIn('value="clearInstantActions"', dashboard)
                self.assertIn("Action 기록 지우기", dashboard)
                self.assertIn('name="confirm" required', dashboard)
                self.assertIn("Block Builder", page)
                recipe_action = next(
                    action
                    for action in spec.instant_actions
                    if action.action_type == "seerNavigateToPoint"
                )
                bookmark = render._card_action_form(
                    spec.key, recipe_action, web._csrf, return_to="dashboard"
                )
                self.assertIn(
                    '/adapter/seer/actions#act-seerNavigateToPoint', bookmark
                )
                self.assertIn("즉시 실행하지 않음", bookmark)
                self.assertNotIn('method="post"', bookmark)

                command = list(app.adapter_command())
                ext_index = command.index("--seer-extensions-path") + 1
                recipes_index = command.index("--seer-recipes-path") + 1
                self.assertEqual(command[ext_index], str(paths.extensions_path))
                self.assertEqual(command[recipes_index], str(paths.recipes_path))
            finally:
                web._server.server_close()


if __name__ == "__main__":
    unittest.main()
