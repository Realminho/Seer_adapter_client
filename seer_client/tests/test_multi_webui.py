from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


SEER_CLIENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SEER_CLIENT_ROOT.parent
for source in (
    SEER_CLIENT_ROOT / "src",
    REPO_ROOT / "adaptor",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.fleet import SeerRobotConfig  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.webui import SeerFleetWebUiApplication  # pyright: ignore[reportMissingImports]  # noqa: E402


class SeerMultiWebUiTests(unittest.TestCase):
    def test_builds_one_webui_with_isolated_robot_runtime(self) -> None:
        robots = (
            SeerRobotConfig(
                serial="SEER-SIM-001",
                simulator=True,
                x=1000.0,
                battery=70.0,
            ),
            SeerRobotConfig(
                serial="SEER-SIM-002",
                simulator=True,
                x=-500.0,
                battery=55.0,
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "seer_client.dropin_control.reserve_local_ports",
            return_value=(41001, 41002),
        ):
            root = Path(temp_dir)
            app = SeerFleetWebUiApplication(
                robots=robots,
                username="seer",
                password="safe-fleet-secret-123",
                port=0,
                runtime_root=root / "runtime",
                ipc_root=root / "ipc",
            )
            web = app.build_webui()
            try:
                self.assertEqual(
                    set(web._specs),
                    {"seer:SEER-SIM-001", "seer:SEER-SIM-002"},
                )
                self.assertEqual(set(app.controllers), set(web._specs))
                self.assertEqual(set(app.monitors), set(web._specs))
                self.assertEqual(set(app.senders), set(web._specs))
                self.assertEqual(set(web._seer_member_bindings), set(web._specs))
                self.assertNotEqual(
                    app.members[0].paths.config_path,
                    app.members[1].paths.config_path,
                )
                self.assertNotEqual(
                    app.members[0].paths.log_path,
                    app.members[1].paths.log_path,
                )
                self.assertEqual(
                    app.members[0].paths.extensions_path,
                    app.members[1].paths.extensions_path,
                )
                self.assertNotEqual(
                    app.members[0].paths.recipes_path,
                    app.members[1].paths.recipes_path,
                )
                self.assertEqual(
                    set(web._seer_recipe_paths),
                    {"seer:SEER-SIM-001", "seer:SEER-SIM-002"},
                )
                self.assertEqual(
                    web._seer_recipe_paths["seer:SEER-SIM-001"],
                    app.members[0].paths.recipes_path,
                )
                self.assertEqual(
                    web._seer_recipe_paths["seer:SEER-SIM-002"],
                    app.members[1].paths.recipes_path,
                )
                self.assertEqual(
                    app.members[0].paths.ipc_root,
                    app.members[1].paths.ipc_root,
                )
                self.assertEqual(app.members[0].control_ipc_port, 41001)
                self.assertEqual(app.members[1].control_ipc_port, 41002)
                self.assertEqual(
                    web._seer_member_bindings["seer:SEER-SIM-001"]["control_ipc_port"],
                    41001,
                )
                self.assertEqual(
                    web._seer_member_bindings["seer:SEER-SIM-002"]["control_ipc_port"],
                    41002,
                )
                command_1 = list(app.members[0].adapter_command())
                command_2 = list(app.members[1].adapter_command())
                self.assertIn("SEER-SIM-001", command_1)
                self.assertIn("SEER-SIM-002", command_2)
                manifest = app.robots_path.read_text(encoding="utf-8")
                self.assertIn('id = "SEER-SIM-001"', manifest)
                self.assertIn('id = "SEER-SIM-002"', manifest)
                for member in app.members:
                    runtime_config = member.paths.config_path.read_text(
                        encoding="utf-8"
                    )
                    self.assertIn("state_publish_delay = 1.0", runtime_config)
                self.assertEqual(app._validate_configs()[0], True)
                self.assertEqual(app._validate_robots()[0], True)
            finally:
                web._server.server_close()

    def test_real_ip_selection_keeps_102_and_103_bound_to_distinct_members(self) -> None:
        robots = (
            SeerRobotConfig(
                serial="SEER-IP-192-168-43-102",
                simulator=False,
                vehicle_ip="192.168.43.102",
                auto_start=False,
            ),
            SeerRobotConfig(
                serial="SEER-IP-192-168-43-103",
                simulator=False,
                vehicle_ip="192.168.43.103",
                auto_start=False,
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "seer_client.dropin_control.reserve_local_ports",
            return_value=(43102, 43103),
        ):
            root = Path(temp_dir)
            app = SeerFleetWebUiApplication(
                robots=robots,
                username="seer",
                password="safe-fleet-secret-123",
                auto_start=False,
                runtime_root=root / "runtime",
                ipc_root=root / "ipc",
            )
            web = app.build_webui()
            try:
                expected = (
                    ("seer:SEER-IP-192-168-43-102", "192.168.43.102", 43102),
                    ("seer:SEER-IP-192-168-43-103", "192.168.43.103", 43103),
                )
                for member, (key, ip, ipc_port) in zip(app.members, expected):
                    self.assertEqual(member.vehicle_ip, ip)
                    self.assertEqual(member.control_ipc_port, ipc_port)
                    self.assertEqual(web._specs[key].vehicle_host, ip)
                    self.assertEqual(web._seer_member_bindings[key]["vehicle_ip"], ip)
                    self.assertEqual(web._seer_member_bindings[key]["control_ipc_port"], ipc_port)
                    self.assertEqual(web._seer_vda_traces[key].identity.serial_number, key.split(":", 1)[1])
                    self.assertEqual(web._seer_vda_senders[key].local_sender.port, ipc_port)
                    command = list(member.adapter_command())
                    vehicle_arg = command.index("--vehicle-ip")
                    self.assertEqual(command[vehicle_arg + 1], ip)
                    runtime_config = member.paths.config_path.read_text(encoding="utf-8")
                    self.assertIn(f'vehicle_ip = "{ip}"', runtime_config)
            finally:
                web._server.server_close()

    def test_percent_encoded_103_manual_request_reaches_selected_sender(self) -> None:
        robots = (
            SeerRobotConfig(
                serial="SEER-IP-192-168-43-102", simulator=False,
                vehicle_ip="192.168.43.102", auto_start=False,
            ),
            SeerRobotConfig(
                serial="SEER-IP-192-168-43-103", simulator=False,
                vehicle_ip="192.168.43.103", auto_start=False,
            ),
        )

        class Handler:
            path = "/adapter/seer%3ASEER-IP-192-168-43-103/manual"
            headers = {}
            client_address = ("127.0.0.1", 12345)
            response = None
            def _json(self, status, payload):
                self.response = (status, payload)
            def _redirect(self, target):
                self.response = (302, target)

        class LocalCapture:
            def __init__(self, port):
                self.port = port
                self.calls = []
            def send_vda(self, suffix, payload, meta):
                self.calls.append((suffix, payload, meta))
                return True, "selected-103"

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "seer_client.dropin_control.reserve_local_ports",
            return_value=(43202, 43203),
        ):
            root = Path(temp_dir)
            app = SeerFleetWebUiApplication(
                robots=robots,
                username="seer",
                password="safe-fleet-secret-123",
                auto_start=False,
                webui_vda_transport="local",
                runtime_root=root / "runtime",
                ipc_root=root / "ipc",
            )
            web = app.build_webui()
            try:
                key = "seer:SEER-IP-192-168-43-103"
                local = LocalCapture(43203)
                web._seer_vda_senders[key].local_sender = local
                handler = Handler()
                web._dispatch_post(
                    handler,
                    {"action_type": "manualStop", "confirm": "on", "armed": "on"},
                )
                self.assertEqual(handler.response[0], 200)
                self.assertEqual(handler.response[1]["text"], "selected-103")
                self.assertEqual(len(local.calls), 1)
                self.assertEqual(local.calls[0][0], "instantActions")
                self.assertEqual(local.calls[0][1]["serialNumber"], "SEER-IP-192-168-43-103")
            finally:
                web._server.server_close()

    def test_103_path_nav_order_uses_only_103_transport(self) -> None:
        robots = (
            SeerRobotConfig(
                serial="SEER-IP-192-168-43-102", simulator=False,
                vehicle_ip="192.168.43.102", auto_start=False,
            ),
            SeerRobotConfig(
                serial="SEER-IP-192-168-43-103", simulator=False,
                vehicle_ip="192.168.43.103", auto_start=False,
            ),
        )

        class Handler:
            path = "/adapter/seer:SEER-IP-192-168-43-103/action"
            headers = {}
            client_address = ("127.0.0.1", 12345)
            response = None
            def _redirect(self, target):
                self.response = (302, target)
            def _json(self, status, payload):
                self.response = (status, payload)

        class LocalCapture:
            def __init__(self, port):
                self.port = port
                self.calls = []
            def send_vda(self, suffix, payload, meta):
                self.calls.append((suffix, payload, meta))
                return True, "captured"

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "seer_client.dropin_control.reserve_local_ports",
            return_value=(43302, 43303),
        ):
            root = Path(temp_dir)
            app = SeerFleetWebUiApplication(
                robots=robots,
                username="seer",
                password="safe-fleet-secret-123",
                auto_start=False,
                webui_vda_transport="local",
                runtime_root=root / "runtime",
                ipc_root=root / "ipc",
            )
            web = app.build_webui()
            try:
                key102 = "seer:SEER-IP-192-168-43-102"
                key103 = "seer:SEER-IP-192-168-43-103"
                local102 = LocalCapture(43302)
                local103 = LocalCapture(43303)
                web._seer_vda_senders[key102].local_sender = local102
                web._seer_vda_senders[key103].local_sender = local103
                handler = Handler()
                web._dispatch_post(
                    handler,
                    {
                        "action_type": "vdaOrderRoute",
                        "navigation_mode": "path",
                        "id": "LM1",
                        "source_id": "",
                        "confirm": "on",
                    },
                )
                self.assertEqual(handler.response[0], 302)
                self.assertEqual(len(local102.calls), 0)
                self.assertEqual(len(local103.calls), 1)
                suffix, payload, _meta = local103.calls[0]
                self.assertEqual(suffix, "order")
                self.assertEqual(payload["serialNumber"], "SEER-IP-192-168-43-103")
                self.assertEqual(payload["orderId"], "PathNav-001")
                self.assertEqual(payload["nodes"][0]["nodeId"], "LM1")
            finally:
                web._server.server_close()

    def test_multi_key_manual_url_and_focus_switch_do_not_stop_motion(self) -> None:
        from seer_client.webui import _SEER_AWARE_JOG_JS

        self.assertIn("encodeURIComponent(job.key||'').replace(/%3A/gi, ':')", _SEER_AWARE_JOG_JS)
        self.assertNotIn("addEventListener('blur'", _SEER_AWARE_JOG_JS)
        self.assertNotIn("visibilitychange", _SEER_AWARE_JOG_JS)
        self.assertIn("HEARTBEAT_MS=250", _SEER_AWARE_JOG_JS)
        self.assertIn("if(moving||lastPayloadKey){ send('manualStop'); }", _SEER_AWARE_JOG_JS)
        self.assertNotIn("directStop", _SEER_AWARE_JOG_JS)

    def test_global_no_auto_start_and_per_robot_auto_start(self) -> None:
        robots = (
            SeerRobotConfig(serial="SEER-1", simulator=True),
            SeerRobotConfig(serial="SEER-2", simulator=True, auto_start=False),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            globally_stopped = SeerFleetWebUiApplication(
                robots=robots,
                username="seer",
                password="safe-fleet-secret-123",
                auto_start=False,
                runtime_root=root / "stopped",
                ipc_root=root / "ipc-stopped",
            )
            self.assertEqual(
                [member.auto_start for member in globally_stopped.members],
                [False, False],
            )
            per_robot = SeerFleetWebUiApplication(
                robots=robots,
                username="seer",
                password="safe-fleet-secret-123",
                auto_start=True,
                runtime_root=root / "per-robot",
                ipc_root=root / "ipc-per-robot",
            )
            self.assertEqual(
                [member.auto_start for member in per_robot.members],
                [True, False],
            )


if __name__ == "__main__":
    unittest.main()

class SeerPerAmrRecipeRuntimeTests(unittest.TestCase):
    def test_recipe_delete_isolated_to_selected_amr(self) -> None:
        import json
        from seer_client.recipe_builder import delete_recipe, managed_recipe_names, save_recipe

        robots = (
            SeerRobotConfig(serial="SEER-SIM-001", simulator=True),
            SeerRobotConfig(serial="SEER-SIM-002", simulator=True),
        )
        definition = json.dumps(
            {
                "name": "isolatedRecipe",
                "label": "Isolated Recipe",
                "blocks": [{"kind": "wait", "values": {"seconds": "1"}, "variables": {}}],
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "seer_client.dropin_control.reserve_local_ports",
            return_value=(42001, 42002),
        ):
            root = Path(temp_dir)
            app = SeerFleetWebUiApplication(
                robots=robots,
                username="seer",
                password="safe-fleet-secret-123",
                auto_start=False,
                runtime_root=root / "runtime",
                ipc_root=root / "ipc",
            )
            web = app.build_webui()
            try:
                key1 = "seer:SEER-SIM-001"
                key2 = "seer:SEER-SIM-002"
                path1 = web._seer_recipe_paths[key1]
                path2 = web._seer_recipe_paths[key2]
                save_recipe(path1, definition, validate=web._seer_recipe_validators[key1])
                save_recipe(path2, definition, validate=web._seer_recipe_validators[key2])
                delete_recipe(path1, "isolatedRecipe", validate=web._seer_recipe_validators[key1])
                self.assertNotIn("isolatedRecipe", managed_recipe_names(path1.read_text(encoding="utf-8")))
                self.assertIn("isolatedRecipe", managed_recipe_names(path2.read_text(encoding="utf-8")))
            finally:
                web._server.server_close()

    def test_paused_running_recipe_is_restored_on_builder_reentry(self) -> None:
        import json
        from types import SimpleNamespace
        from seer_client.recipe_builder import save_recipe

        robot = SeerRobotConfig(serial="SEER-SIM-PAUSED", simulator=True)
        definition = json.dumps(
            {
                "name": "pausedRecipe",
                "label": "Paused Recipe",
                "blocks": [
                    {"kind": "wait", "values": {"seconds": "1"}, "variables": {}}
                ],
            }
        )

        class Handler:
            headers = {}
            client_address = ("127.0.0.1", 12345)
            response = None
            def __init__(self, path, query=None):
                self.path = path
                self._query_value = dict(query or {})
            def _query(self):
                return dict(self._query_value)
            def _html(self, status, payload):
                self.response = (status, payload)
            def _json(self, status, payload):
                self.response = (status, payload)

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "seer_client.dropin_control.reserve_local_ports",
            return_value=(42101,),
        ):
            root = Path(temp_dir)
            app = SeerFleetWebUiApplication(
                robots=(robot,),
                username="seer",
                password="safe-fleet-secret-123",
                auto_start=False,
                runtime_root=root / "runtime",
                ipc_root=root / "ipc",
            )
            web = app.build_webui()
            try:
                key = "seer:SEER-SIM-PAUSED"
                recipe_path = web._seer_recipe_paths[key]
                save_recipe(
                    recipe_path,
                    definition,
                    validate=web._seer_recipe_validators[key],
                )
                runtime_path = app.members[0].paths.runtime_dir / "seer-block-runtime.json"
                runtime_path.write_text(
                    json.dumps(
                        {
                            "schema": 1,
                            "status": "running",
                            "phase": "action",
                            "recipe_name": "pausedRecipe",
                            "updated_at": time.time(),
                        }
                    ),
                    encoding="utf-8",
                )
                web._monitor_snapshot = lambda _key: SimpleNamespace(
                    paused=True, working_state="PAUSED"
                )

                page_handler = Handler("/recipe-builder")
                web._dispatch_get(page_handler)
                self.assertEqual(page_handler.response[0], 200)
                self.assertIn('value="pausedRecipe"', page_handler.response[1])
                self.assertIn('value="Paused Recipe"', page_handler.response[1])

                runtime_handler = Handler(
                    "/recipe-builder/runtime", {"robot": key}
                )
                web._dispatch_get(runtime_handler)
                self.assertEqual(runtime_handler.response[0], 200)
                self.assertEqual(runtime_handler.response[1]["status"], "running")
                self.assertEqual(runtime_handler.response[1]["recipe_name"], "pausedRecipe")
                self.assertTrue(runtime_handler.response[1]["paused"])
            finally:
                web._server.server_close()

    def test_builder_action_routes_only_to_selected_amr_sender(self) -> None:
        import json
        from types import SimpleNamespace
        from seer_client.recipe_builder import save_recipe
        from seer_client.webui import _send_selected_builder_action

        class Factory:
            def instant_action(self, action_type, parameters=None, *, action_id=None):
                return {
                    "serialNumber": "SERIAL-1",
                    "actions": [{
                        "actionId": action_id,
                        "actionType": action_type,
                        "actionParameters": parameters or {},
                    }],
                }

        class Sender:
            mode = "mqtt"
            def __init__(self):
                self.calls = []
            def send(self, payload, meta):
                self.calls.append((payload, meta))
                return True, "delivered"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            p1 = root / "one.hcl"
            p2 = root / "two.hcl"
            p1.write_text("", encoding="utf-8")
            p2.write_text("", encoding="utf-8")
            definition = json.dumps(
                {
                    "name": "selectedRecipe",
                    "label": "Selected Recipe",
                    "blocks": [{"kind": "wait", "values": {"seconds": "1"}, "variables": {}}],
                }
            )
            save_recipe(p1, definition)
            save_recipe(p2, definition)
            sender1 = Sender()
            sender2 = Sender()
            web = SimpleNamespace(
                _seer_vda_senders={"one": sender1, "two": sender2},
                _seer_vda_traces={
                    "one": SimpleNamespace(factory=Factory()),
                    "two": SimpleNamespace(factory=Factory()),
                },
                _seer_recipe_paths={"one": p1, "two": p2},
            )
            ok, _message = _send_selected_builder_action(web, "two", "selectedRecipe")
            self.assertTrue(ok)
            ok, _message = _send_selected_builder_action(web, "two", "selectedRecipe")
            self.assertTrue(ok)
            self.assertEqual(sender1.calls, [])
            self.assertEqual(len(sender2.calls), 2)
            self.assertEqual(sender2.calls[0][0]["actions"][0]["actionType"], "selectedRecipe")
            self.assertEqual(sender2.calls[0][0]["actions"][0]["actionId"], "selectedRecipe-001")
            self.assertEqual(sender2.calls[1][0]["actions"][0]["actionId"], "selectedRecipe-002")

            # A new WebUI object using the same AMR-local recipes path must keep
            # the execution sequence instead of restarting at 001.
            sender3 = Sender()
            web2 = SimpleNamespace(
                _seer_vda_senders={"two": sender3},
                _seer_vda_traces={"two": SimpleNamespace(factory=Factory())},
                _seer_recipe_paths={"two": p2},
            )
            ok, _message = _send_selected_builder_action(web2, "two", "selectedRecipe")
            self.assertTrue(ok)
            self.assertEqual(sender3.calls[0][0]["actions"][0]["actionId"], "selectedRecipe-003")
