"""Integration checks for the unmodified-adapter SEER bridge."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("amr-client-contract/src", "seer_client/src", "adaptor"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from seer_client.bridge import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    SeerAdapterClient,
    SeerSimulatedAdapterClient,
    _apply_seer_pause,
    _disable_jibot_only_runtime_sources,
    _missing_designated_route_edges,
    _parse_route_points,
    install_into_adapter_main,
)
from seer_client.client import SeerClient  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.map_view import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    read_active_route,
    write_map_cache,
)
from seer_client.protocol import ApiPort  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.simulator import SeerSimulatorServer  # pyright: ignore[reportMissingImports]  # noqa: E402
from protocol.vda_2_0_0.vda5050_2_0_0_state import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    ActionStatus,
)
from protocol.vda5050_3_0.messages import ConnectionState  # pyright: ignore[reportMissingImports]  # noqa: E402


def adapter_config():
    return SimpleNamespace(
        factsheet=SimpleNamespace(
            coordinate_unit_position="mm",
            coordinate_unit_orientation="deg",
        ),
        settings=SimpleNamespace(
            map_id="integration-map",
            nearest_node_mode="pathPoint",
        ),
        bms_ros=SimpleNamespace(enabled=True),
    )


class SeerBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_connection_topic_follows_vehicle_link_not_mqtt_link(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        health = {"online": False}
        adapter._is_jibot_link_healthy = lambda: health["online"]
        published = []

        def fake_publish(state, *, topic_name="connection", retain_msg=True):
            published.append((state, topic_name, retain_msg))
            # Mirror the shared Adapter's shutdown latch so this test also proves
            # transient vehicle OFFLINE does not become a permanent shutdown.
            adapter._connection_offline_intent = state == ConnectionState.OFFLINE

        adapter._publish_connection_state = fake_publish

        # The unchanged main.py asks for ONLINE after MQTT setup.  With the
        # controller powered off, SEER must replace any stale retained ONLINE
        # with OFFLINE instead.
        await adapter.publish_connection(
            topic_name="connection",
            retain_msg=True,
            connection_state=ConnectionState.ONLINE,
        )
        self.assertEqual(published[-1][0], ConnectionState.OFFLINE)
        self.assertFalse(adapter._connection_offline_intent)

        # Vehicle recovery flips the retained state to ONLINE.
        health["online"] = True
        adapter._seer_publish_vehicle_connection_state(reason="test recovery")
        self.assertEqual(published[-1][0], ConnectionState.ONLINE)

        # MQTT reconnect while the controller is down must reassert OFFLINE,
        # never blindly publish ONLINE.
        health["online"] = False
        adapter._republish_connection_online()
        self.assertEqual(published[-1][0], ConnectionState.OFFLINE)
        self.assertFalse(adapter._connection_offline_intent)

        # A deliberate shutdown OFFLINE remains latched and cannot be undone by
        # a late broker reconnect callback.
        await adapter.publish_connection(
            topic_name="connection",
            retain_msg=True,
            connection_state=ConnectionState.OFFLINE,
        )
        count = len(published)
        health["online"] = True
        adapter._republish_connection_online()
        self.assertEqual(len(published), count)
        self.assertTrue(adapter._connection_offline_intent)

    async def test_emergency_action_supports_explicit_on_off_and_legacy_toggle(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        calls = []

        class FakeVehicle:
            async def set_soft_emergency(self, enabled):
                calls.append(("set", bool(enabled)))
                return bool(enabled)

            async def toggle_soft_emergency(self):
                calls.append(("toggle",))
                return True

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()
        publishes = []
        adapter.request_state_publish = publishes.append
        adapter._update_instant_action_status = lambda *args, **kwargs: None

        def action(action_id, status=None):
            parameters = (
                []
                if status is None
                else [SimpleNamespace(key="status", value=status)]
            )
            return SimpleNamespace(
                action_id=action_id,
                action_type="seerEmergencySwitch",
                action_parameters=parameters,
            )

        active = await adapter._action_registry.execute(action("emc-1", "on"), adapter)
        released = await adapter._action_registry.execute(
            action("emc-release-1", "off"), adapter
        )
        toggled = await adapter._action_registry.execute(action("legacy-toggle"), adapter)

        self.assertEqual(active.status, ActionStatus.FINISHED)
        self.assertIn("ON", active.description)
        self.assertEqual(released.status, ActionStatus.FINISHED)
        self.assertIn("RELEASED", released.description)
        self.assertEqual(toggled.status, ActionStatus.FINISHED)
        self.assertEqual(calls, [("set", True), ("set", False), ("toggle",)])
        self.assertEqual(
            publishes,
            [
                "SEER software emergency activated",
                "SEER software emergency released",
                "SEER software emergency toggled",
            ],
        )

    async def test_jack_vda_actions_call_controller_jack_methods(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        calls = []

        class FakeNavigation:
            async def wait_jack_until_terminal(self, operation, **_kwargs):
                key = "load" if operation == "JackLoad" else "unload"
                return {
                    "task_status": 4,
                    "move_status_info": json.dumps({
                        "args": {"operation": operation},
                        key: {"operation_status": 3},
                        "status": 3,
                    }),
                }

            def _jack_task_details(self, payload):
                info = json.loads(payload.get("move_status_info", "{}"))
                operation = str(info.get("args", {}).get("operation", ""))
                section = info.get("load" if operation == "JackLoad" else "unload", {})
                return operation, int(section.get("operation_status", 0)), int(payload.get("task_status", 0))

        class FakeVehicle:
            _jack_supported = True
            _robot_model = "AMB-300JZ"
            def __init__(self):
                self.navigation = FakeNavigation()

            async def jack_load(self):
                calls.append("load")

            async def jack_unload(self):
                calls.append("unload")

            async def read_di(self, channel):
                self_channel = int(channel)
                calls.append(("di", self_channel))
                return self_channel == 2

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()

        def action(action_id, action_type):
            return SimpleNamespace(
                action_id=action_id,
                action_type=action_type,
                action_parameters=[],
            )

        loaded = await adapter._action_registry.execute(
            action("jack-load-1", "seerJackLoad"), adapter
        )
        unloaded = await adapter._action_registry.execute(
            action("jack-unload-1", "seerJackUnload"), adapter
        )

        self.assertEqual(loaded.status, ActionStatus.FINISHED)
        self.assertIn("Jack Load completed", loaded.description)
        self.assertEqual(unloaded.status, ActionStatus.FINISHED)
        self.assertIn("Jack Unload completed", unloaded.description)
        self.assertEqual(calls, ["load", "unload", ("di", 2)])

    async def test_selected_path_route_with_delay_still_runs_one_continuous_route(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        calls = []

        class FakeVehicle:
            _emergency = False

            async def get_emergency_state(self):
                return {"emergency": False, "driver_emc": False, "soft_emc": False}

            async def path_navigation_route(self, route_points, *, task_id):
                calls.append(("route", tuple(route_points), task_id))

            async def wait_navigation_terminal(self, *, expected_station, cancel_on_timeout):
                calls.append(("wait", expected_station, cancel_on_timeout))

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()
        adapter._update_instant_action_status = lambda *args, **kwargs: None
        action = SimpleNamespace(
            action_id="segmented-route-1",
            action_type="seerPathNav",
            action_parameters=[
                SimpleNamespace(key="id", value="LM4"),
                SimpleNamespace(key="source_id", value="LM1"),
                SimpleNamespace(key="task_id", value="route-task"),
                SimpleNamespace(key="route_points", value=["LM1", "LM2", "LM3", "LM4"]),
                SimpleNamespace(key="waypoint_delay_sec", value="0.001"),
            ],
        )
        result = await adapter._action_registry.execute(action, adapter)
        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertIn("continuous single API 3066 request", result.description)
        self.assertEqual(
            calls,
            [
                ("route", ("LM1", "LM2", "LM3", "LM4"), "route-task"),
                ("wait", "LM4", True),
            ],
        )

    async def test_legacy_hcl_escaped_route_points_are_accepted(self):
        self.assertEqual(
            _parse_route_points('[\\\"LM1\\\",\\\"LM5\\\",\\\"LM4\\\"]'),
            ("LM1", "LM5", "LM4"),
        )

    async def test_designated_route_reports_missing_directed_map_leg(self):
        raw_map = {
            "header": {"mapName": "test"},
            "advancedPointList": [
                {"instanceName": "LM1", "pos": {"x": 0.0, "y": 0.0}},
                {"instanceName": "LM2", "pos": {"x": 1.0, "y": 0.0}},
                {"instanceName": "LM3", "pos": {"x": 2.0, "y": 0.0}},
            ],
            "advancedCurveList": [
                {
                    "className": "BezierPath",
                    "startPos": {"instanceName": "LM1", "pos": {"x": 0.0, "y": 0.0}},
                    "endPos": {"instanceName": "LM2", "pos": {"x": 1.0, "y": 0.0}},
                    "controlPos1": {"x": 0.3, "y": 0.0},
                    "controlPos2": {"x": 0.7, "y": 0.0},
                }
            ],
            "advancedLineList": [],
            "normalPosList": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "map.json"
            write_map_cache(cache, raw_map)
            with patch.dict(os.environ, {"SEER_MAP_CACHE_PATH": str(cache)}):
                self.assertEqual(
                    _missing_designated_route_edges(("LM1", "LM2", "LM3")),
                    (("LM2", "LM3"),),
                )

    async def test_selected_path_route_uses_one_designated_route_request(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        calls = []
        terminal_entered = asyncio.Event()
        release_terminal = asyncio.Event()

        class FakeVehicle:
            _emergency = False

            async def get_emergency_state(self):
                return {"emergency": False, "driver_emc": False, "soft_emc": False}

            async def path_navigation_route(self, route_points, *, task_id):
                calls.append(("route", tuple(route_points), task_id))

            async def wait_navigation_terminal(self, *, expected_station, cancel_on_timeout):
                calls.append(("wait", expected_station, cancel_on_timeout))
                terminal_entered.set()
                await release_terminal.wait()

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()
        adapter._update_instant_action_status = lambda *args, **kwargs: None
        action = SimpleNamespace(
            action_id="selected-route-1",
            action_type="seerPathNav",
            action_parameters=[
                SimpleNamespace(key="id", value="LM5"),
                SimpleNamespace(key="source_id", value="LM1"),
                SimpleNamespace(key="task_id", value="route-task"),
                SimpleNamespace(
                    key="route_points",
                    value=json.dumps(["LM1", "LM8", "LM6", "LM5"]),
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            {"SEER_ACTIVE_ROUTE_PATH": str(Path(temporary) / "active-route.json")},
        ):
            active_route_path = Path(os.environ["SEER_ACTIVE_ROUTE_PATH"])
            execution = asyncio.create_task(
                adapter._action_registry.execute(action, adapter)
            )
            await asyncio.wait_for(terminal_entered.wait(), timeout=1.0)
            self.assertEqual(
                read_active_route(active_route_path),
                ("LM1", "LM8", "LM6", "LM5"),
            )
            release_terminal.set()
            result = await execution
            self.assertEqual(read_active_route(active_route_path), ())

        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertIn("LM1 -> LM8 -> LM6 -> LM5", result.description)
        self.assertIn("single API 3066", result.description)
        self.assertEqual(
            calls,
            [
                ("route", ("LM1", "LM8", "LM6", "LM5"), "route-task"),
                ("wait", "LM5", True),
            ],
        )


    async def test_self_position_path_nav_allows_off_point_robot_to_named_target(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        calls = []

        class FakeVehicle:
            _emergency = False

            async def get_emergency_state(self):
                return {"emergency": False, "driver_emc": False, "soft_emc": False}

            async def path_navigation(self, target_id, *, source_id, task_id):
                calls.append(("path", target_id, source_id, task_id))

            async def wait_navigation_terminal(self, *, expected_station, cancel_on_timeout):
                calls.append(("wait", expected_station, cancel_on_timeout))
                return {"task_status": 4}

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()
        adapter._update_instant_action_status = lambda *args, **kwargs: None
        action = SimpleNamespace(
            action_id="self-position-path-1",
            action_type="seerPathNav",
            action_parameters=[
                SimpleNamespace(key="navigation_mode", value="path"),
                SimpleNamespace(key="id", value="LM1"),
                SimpleNamespace(key="source_id", value="SELF_POSITION"),
                SimpleNamespace(key="task_id", value="off-point-path"),
                SimpleNamespace(key="route_points", value=""),
            ],
        )

        result = await adapter._action_registry.execute(action, adapter)
        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(
            calls,
            [
                ("path", "LM1", None, "off-point-path"),
                ("wait", "LM1", True),
            ],
        )
        self.assertIn("AUTO_CURRENT_POSITION -> LM1", result.description)

    async def test_map_free_navigation_uses_native_pose_and_api_3051(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        calls = []

        class FakeVehicle:
            _emergency = False

            async def get_emergency_state(self):
                return {"emergency": False, "driver_emc": False, "soft_emc": False}

            def position_from_native(self, value):
                return float(value) * 1000.0

            def angle_from_native(self, value):
                return math.degrees(float(value))

            async def free_nav(self, x, y, theta):
                calls.append(("free", x, y, theta))

            async def wait_navigation_terminal(self, *, expected_pose, cancel_on_timeout):
                calls.append(("wait", tuple(expected_pose), cancel_on_timeout))

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()
        adapter._update_instant_action_status = lambda *args, **kwargs: None
        action = SimpleNamespace(
            action_id="free-map-1",
            action_type="seerPathNav",
            action_parameters=[
                SimpleNamespace(key="id", value="LM5"),
                SimpleNamespace(key="navigation_mode", value="free"),
                SimpleNamespace(key="free_nav_x", value="1.25"),
                SimpleNamespace(key="free_nav_y", value="-0.5"),
                SimpleNamespace(key="free_nav_theta", value="1.57079632679"),
            ],
        )

        result = await adapter._action_registry.execute(action, adapter)

        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertIn("API 3051 freeGo", result.description)
        self.assertEqual(calls[0][:3], ("free", 1250.0, -500.0))
        self.assertAlmostEqual(calls[0][3], 90.0, places=5)
        self.assertEqual(calls[1], ("wait", (1.25, -0.5, 1.57079632679), True))

    async def test_off_path_reentry_runs_free_nav_then_designated_path(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        calls = []

        class FakeVehicle:
            _emergency = False

            async def get_emergency_state(self):
                return {"emergency": False, "driver_emc": False, "soft_emc": False}

            def position_from_native(self, value):
                return float(value) * 1000.0

            def angle_from_native(self, value):
                return math.degrees(float(value))

            async def free_nav(self, x, y, theta):
                calls.append(("free", x, y, theta))

            async def path_navigation_route(self, route_points, *, task_id):
                calls.append(("route", tuple(route_points), task_id))

            async def wait_navigation_terminal(self, **kwargs):
                if "expected_pose" in kwargs:
                    calls.append(("wait_pose", tuple(kwargs["expected_pose"]), kwargs["cancel_on_timeout"]))
                else:
                    calls.append(("wait_station", kwargs.get("expected_station"), kwargs["cancel_on_timeout"]))

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()
        adapter._update_instant_action_status = lambda *args, **kwargs: None
        action = SimpleNamespace(
            action_id="reentry-map-1",
            action_type="seerPathNav",
            action_parameters=[
                SimpleNamespace(key="id", value="LM2"),
                SimpleNamespace(key="navigation_mode", value="reentry"),
                SimpleNamespace(key="route_points", value=["LM1", "LM2"]),
                SimpleNamespace(key="reentry_id", value="LM1"),
                SimpleNamespace(key="reentry_x", value="1.25"),
                SimpleNamespace(key="reentry_y", value="-0.5"),
                SimpleNamespace(key="reentry_theta", value="1.57079632679"),
                SimpleNamespace(key="task_id", value="reentry-task"),
            ],
        )

        result = await adapter._action_registry.execute(action, adapter)

        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertIn("API 3051 + API 3066", result.description)
        self.assertEqual(calls[0][:3], ("free", 1250.0, -500.0))
        self.assertAlmostEqual(calls[0][3], 90.0, places=5)
        self.assertEqual(calls[1], ("wait_pose", (1.25, -0.5, 1.57079632679), True))
        self.assertEqual(calls[2], ("route", ("LM1", "LM2"), "reentry-task"))
        self.assertEqual(calls[3], ("wait_station", "LM2", True))

    async def test_off_path_target_point_reentry_finishes_after_free_nav(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        calls = []

        class FakeVehicle:
            _emergency = False
            async def get_emergency_state(self):
                return {"emergency": False, "driver_emc": False, "soft_emc": False}
            def position_from_native(self, value): return float(value) * 1000.0
            def angle_from_native(self, value): return math.degrees(float(value))
            async def free_nav(self, x, y, theta): calls.append(("free", x, y, theta))
            async def wait_navigation_terminal(self, **kwargs): calls.append(("wait", kwargs))

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()
        adapter._update_instant_action_status = lambda *args, **kwargs: None
        action = SimpleNamespace(
            action_id="reentry-same-1", action_type="seerPathNav",
            action_parameters=[
                SimpleNamespace(key="id", value="LM7"),
                SimpleNamespace(key="navigation_mode", value="reentry"),
                SimpleNamespace(key="route_points", value=["LM7"]),
                SimpleNamespace(key="reentry_id", value="LM7"),
                SimpleNamespace(key="reentry_x", value="1.0"),
                SimpleNamespace(key="reentry_y", value="2.0"),
                SimpleNamespace(key="reentry_theta", value="0.0"),
            ],
        )
        result = await adapter._action_registry.execute(action, adapter)
        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertIn("re-entered target point LM7", result.description)
        self.assertEqual(len(calls), 2)


    async def test_vda_multinode_plain_order_uses_one_continuous_3066_route(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        class FakeVehicle:
            _station = "LM1"

            def __init__(self):
                self.routes = []
                self.waited = []

            async def path_navigation_route(self, route, *, task_id=""):
                self.routes.append((tuple(route), task_id))

            async def wait_navigation_terminal(self, **kwargs):
                self.waited.append(dict(kwargs))

        config = copy.deepcopy(get_config())
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        adapter._vehicle = FakeVehicle()
        adapter._dock_segment_rule = lambda _node_id: None
        adapter._move_motion_rule = lambda _node_id: None
        adapter._is_dock_work_node = lambda _node_id: False

        nodes = [
            SimpleNamespace(node_id="LM1", sequence_id=0, released=True, actions=[]),
            SimpleNamespace(node_id="LM2", sequence_id=2, released=True, actions=[]),
            SimpleNamespace(node_id="LM3", sequence_id=4, released=True, actions=[]),
            SimpleNamespace(node_id="LM4", sequence_id=6, released=True, actions=[]),
        ]
        edges = [
            SimpleNamespace(edge_id="E12", sequence_id=1, released=True, actions=[]),
            SimpleNamespace(edge_id="E23", sequence_id=3, released=True, actions=[]),
            SimpleNamespace(edge_id="E34", sequence_id=5, released=True, actions=[]),
        ]
        adapter.order = SimpleNamespace(order_id="route-order-1", nodes=nodes, edges=edges)
        adapter.state = SimpleNamespace(last_node_id="LM1", last_node_sequence_id=0)

        self.assertIsNone(await adapter._send_node_motion(nodes[1]))
        self.assertEqual(
            adapter._vehicle.routes,
            [(('LM1', 'LM2', 'LM3', 'LM4'), 'vda-route-order-1')],
        )

        # Later VDA nodes stay visible in the unchanged order, but no new SEER
        # goto is sent for them while the one API 3066 route is active.
        self.assertIsNone(await adapter._send_node_motion(nodes[2]))
        self.assertEqual(len(adapter._vehicle.routes), 1)
        await adapter._settle_goto_arrival(nodes[2])
        self.assertEqual(adapter._vehicle.waited, [])

        self.assertIsNone(await adapter._send_node_motion(nodes[3]))
        await adapter._settle_goto_arrival(nodes[3])
        self.assertEqual(
            adapter._vehicle.waited,
            [{"expected_station": "LM4", "cancel_on_timeout": True}],
        )
        self.assertIsNone(getattr(adapter, "_seer_continuous_vda_route", None))

    async def test_client_sends_selected_route_as_one_3066_frame(self):
        server = SeerSimulatorServer(travel_time_sec=0.03)
        ports = await server.start()
        client = SeerClient(
            "127.0.0.1",
            port_map=ports,
            is_simulator=True,
            status_poll_interval_sec=0.01,
        )
        try:
            await client.connect_socket()
            await client.path_navigation_route(
                ("SIM_START", "SIM_UPPER", "SIM_GOAL"),
                task_id="selected-route",
            )
            await client.wait_navigation_terminal(
                expected_station="SIM_GOAL",
                timeout_sec=1.0,
            )
            route_commands = [
                item for item in server.command_log if item["api"] == 3066
            ]
            self.assertEqual(len(route_commands), 1)
            self.assertFalse(any(item["api"] == 3051 for item in server.command_log))
            segments = route_commands[0]["body"]["move_task_list"]
            self.assertEqual(
                [(item["source_id"], item["id"]) for item in segments],
                [("SIM_START", "SIM_UPPER"), ("SIM_UPPER", "SIM_GOAL")],
            )
            self.assertRegex(segments[0]["task_id"], r"^selected-route-\d+-1$")
            self.assertRegex(segments[1]["task_id"], r"^selected-route-\d+-2$")
            self.assertEqual(
                segments[0]["task_id"].rsplit("-", 1)[0],
                segments[1]["task_id"].rsplit("-", 1)[0],
            )

            await client.path_navigation_route(
                ("SIM_GOAL", "SIM_UPPER", "SIM_START"),
                task_id="selected-route",
            )
            await client.wait_navigation_terminal(
                expected_station="SIM_START",
                timeout_sec=1.0,
            )
            all_route_commands = [
                item for item in server.command_log if item["api"] == 3066
            ]
            second_ids = {
                item["task_id"]
                for item in all_route_commands[1]["body"]["move_task_list"]
            }
            self.assertTrue({item["task_id"] for item in segments}.isdisjoint(second_ids))

            await client.free_nav(0.25, 0.5, 0.1)
            await client.wait_navigation_terminal(
                expected_pose=(0.25, 0.5, 0.1),
                timeout_sec=1.0,
            )
            free_command = [
                item
                for item in server.command_log
                if item["api"] == 3051 and "freeGo" in item["body"]
            ][-1]
            self.assertEqual(free_command["body"]["id"], "SELF_POSITION")
            self.assertEqual(
                free_command["body"]["freeGo"],
                {"x": 0.25, "y": 0.5, "theta": 0.1},
            )
        finally:
            await client.disconnect()
            await server.stop()

    async def test_simulator_holds_pose_while_task_is_paused_then_resumes(self):
        server = SeerSimulatorServer(travel_time_sec=0.4)
        ports = await server.start()
        client = SeerClient(
            "127.0.0.1",
            port_map=ports,
            is_simulator=True,
            status_poll_interval_sec=0.02,
        )
        try:
            await client.connect_socket()
            await client.goto_xyz(1.0, 0.0, 0.0)
            await asyncio.sleep(0.08)
            await client.pause_navigation()
            paused_x = server.x
            await asyncio.sleep(0.12)
            self.assertAlmostEqual(server.x, paused_x, places=6)
            self.assertEqual(server.task_status, 3)

            await client.resume_navigation()
            deadline = asyncio.get_running_loop().time() + 1.0
            while server.task_status != 4 and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            self.assertAlmostEqual(server.x, 1.0, places=6)
            self.assertEqual(server.task_status, 4)
            task_apis = [
                item["api"]
                for item in server.command_log
                if item["port"] == int(ApiPort.TASK)
            ]
            self.assertIn(3001, task_apis)
            self.assertIn(3002, task_apis)
            self.assertNotIn(3003, task_apis)
        finally:
            await client.disconnect()
            await server.stop()

    async def test_pause_and_resume_use_native_seer_task_commands_without_cancel(self):
        calls = []

        class FakeVehicle:
            async def pause_navigation(self):
                calls.append("pause")

            async def resume_navigation(self):
                calls.append("resume")

        updates = []
        publishes = []
        motion_action = SimpleNamespace(action_status=ActionStatus.RUNNING)
        adapter = SimpleNamespace(
            _vehicle=FakeVehicle(),
            _motion_paused=False,
            state=SimpleNamespace(paused=False, action_states=[motion_action]),
            _update_instant_action_status=lambda *args, **kwargs: updates.append(
                (args, kwargs)
            ),
            request_state_publish=publishes.append,
        )

        await _apply_seer_pause(adapter, "pause-1", True)
        self.assertEqual(calls, ["pause"])
        self.assertTrue(adapter._motion_paused)
        self.assertTrue(adapter.state.paused)
        self.assertEqual(motion_action.action_status, ActionStatus.PAUSED)
        self.assertIn("API 3001", updates[-1][1]["result_description"])

        await _apply_seer_pause(adapter, "resume-1", False)
        self.assertEqual(calls, ["pause", "resume"])
        self.assertFalse(adapter._motion_paused)
        self.assertFalse(adapter.state.paused)
        self.assertEqual(motion_action.action_status, ActionStatus.RUNNING)
        self.assertIn("API 3002", updates[-1][1]["result_description"])
        self.assertEqual(publishes, ["SEER task paused", "SEER task resumed"])

    def test_latest_adapter_raw_jibot_action_probe_is_disabled_for_seer(self):
        adapter_main = SimpleNamespace()
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter  # pyright: ignore[reportMissingImports]

        action = SimpleNamespace(action_type="seerEmergencySwitch")
        self.assertFalse(Adapter._is_jibot_command_instant_action(None, action))
        self.assertIsNone(
            Adapter._command_from_jibot_action_type(None, "jibotUmGoto")
        )
        self.assertEqual(adapter_main.JIBOT.COMMAND_SPECS, {})

    def test_jibot_hardware_recipes_are_removed_with_disabled_modules(self):
        pio_recipe = SimpleNamespace(
            action_type="ezioElevatorOpen",
            steps=[SimpleNamespace(extension="pioInit")],
            cleanup=[SimpleNamespace(extension="pioDisconnect")],
        )
        facility_recipe = SimpleNamespace(
            action_type="elevatorUp",
            steps=[SimpleNamespace(extension="elevatorEnter")],
            cleanup=[],
        )
        custom_recipe = SimpleNamespace(
            action_type="customSafeRecipe",
            steps=[SimpleNamespace(extension="customSafeAction")],
            cleanup=[],
        )
        config = adapter_config()
        config.action_modules = []
        config.recipes = [pio_recipe, facility_recipe, custom_recipe]

        _disable_jibot_only_runtime_sources(config)

        self.assertEqual(
            [entry.module for entry in config.action_modules],
            [
                "extensions.clamp",
                "extensions.pio",
                "extensions.ezio",
                "extensions.facility",
            ],
        )
        self.assertTrue(all(not entry.enabled for entry in config.action_modules))
        self.assertEqual(config.recipes, [custom_recipe])

    async def test_simulator_uses_adapter_units_and_initial_state(self):
        config = adapter_config()
        client = SeerSimulatedAdapterClient(
            config=config,
            initial_position={"x": 1500, "y": -500, "theta": 90},
            initial_battery=55,
        )
        await client.connect_socket()
        try:
            await client._poll_once()
            self.assertAlmostEqual(client.x, 1500.0)
            self.assertAlmostEqual(client.y, -500.0)
            self.assertAlmostEqual(client.th, 90.0)
            self.assertAlmostEqual(client.battery, 55.0)
            self.assertFalse(config.bms_ros.enabled)
            self.assertEqual(config.settings.nearest_node_mode, "headingGoal")
        finally:
            await client.disconnect()

    async def test_real_client_path_uses_configured_five_tcp_ports(self):
        server = SeerSimulatorServer(
            travel_time_sec=0.05,
            initial_x=1.25,
            initial_y=-0.5,
            initial_angle=0.0,
            initial_battery=80.0,
        )
        ports = await server.start()
        env = {
            "SEER_STATE_PORT": str(ports[ApiPort.STATE]),
            "SEER_CONTROL_PORT": str(ports[ApiPort.CONTROL]),
            "SEER_TASK_PORT": str(ports[ApiPort.TASK]),
            "SEER_CONFIG_PORT": str(ports[ApiPort.CONFIG]),
            "SEER_OTHER_PORT": str(ports[ApiPort.OTHER]),
            "SEER_STATUS_POLL_INTERVAL_SEC": "0.02",
        }
        client = None
        try:
            with patch.dict(os.environ, env, clear=False):
                config = adapter_config()
                client = SeerAdapterClient("127.0.0.1", config=config)
                await client.connect_socket()
                await client._poll_once()
                self.assertTrue(client.is_connected())
                self.assertAlmostEqual(client.x, 1250.0)
                self.assertAlmostEqual(client.y, -500.0)

                await client.goto_xyz(2000.0, 1000.0, 90.0)
                await asyncio.sleep(0.12)
                await client._poll_once()
                self.assertAlmostEqual(client.x, 2000.0)
                self.assertAlmostEqual(client.y, 1000.0)
                self.assertAlmostEqual(client.th, 90.0)
                self.assertEqual(config.settings.nearest_node_mode, "headingGoal")
                self.assertIsNone(client.get_path_point_pose("P1"))
        finally:
            if client is not None:
                await client.disconnect()
            await server.stop()


if __name__ == "__main__":
    unittest.main()
