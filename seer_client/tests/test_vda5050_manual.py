"""VDA5050 manual console and SEER WebUI envelope regression tests."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in (REPO_ROOT / "seer_client" / "src", REPO_ROOT / "adaptor"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from protocol.vda5050_3_0.messages import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    InstantActions,
    Order,
)
from seer_client.vda5050_console import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    Vda5050Identity,
    Vda5050MessageFactory,
    Vda5050MqttConsole,
)
from seer_client.vda5050_trace import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    Vda5050MqttSender,
    Vda5050TraceStore,
    Vda5050WebSender,
    render_vda5050_page,
)


class _PublishResult:
    rc = 0


class _FakeMqttClient:
    def __init__(self) -> None:
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.subscriptions = []
        self.published = []
        self.connected_to = None

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def connect(self, host, port, keepalive=60):
        self.connected_to = (host, port, keepalive)

    def loop_start(self):
        self.on_connect(self, None, {}, 0)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return _PublishResult()




class _FakeLocalVdaSender:
    def __init__(self) -> None:
        self.sent = []

    def send_vda(self, suffix, payload, meta):
        self.sent.append((suffix, json.loads(json.dumps(payload)), dict(meta)))
        return True, "delivered via Local VDA5050"


class Vda5050FactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = Vda5050Identity(serial_number="SEER-SIM-001")
        self.factory = Vda5050MessageFactory(self.identity)

    def test_instant_action_is_accepted_by_unchanged_adapter_v3_parser(self) -> None:
        payload = self.factory.instant_action(
            "seerPathNav",
            {"id": "LM4", "source_id": "LM1", "navigation_mode": "path"},
        )
        parsed = InstantActions.from_dict(payload)
        self.assertEqual(parsed.header.serial_number, "SEER-SIM-001")
        self.assertEqual(parsed.actions[0].action_type, "seerPathNav")
        self.assertEqual(parsed.actions[0].action_parameters[0].key, "id")

    def test_default_action_id_is_readable_per_action_type(self) -> None:
        pause1 = self.factory.instant_action("startPause")
        resume1 = self.factory.instant_action("stopPause")
        pause2 = self.factory.instant_action("startPause")
        self.assertEqual(pause1["actions"][0]["actionId"], "startPause-001")
        self.assertEqual(resume1["actions"][0]["actionId"], "stopPause-001")
        self.assertEqual(pause2["actions"][0]["actionId"], "startPause-002")

    def test_action_counter_persists_when_counter_path_is_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            counter_path = Path(temp_dir) / "action-id-counts.json"
            first = Vda5050MessageFactory(
                self.identity, action_counter_path=counter_path
            )
            self.assertEqual(
                first.instant_action("startPause")["actions"][0]["actionId"],
                "startPause-001",
            )
            second = Vda5050MessageFactory(
                self.identity, action_counter_path=counter_path
            )
            self.assertEqual(
                second.instant_action("startPause")["actions"][0]["actionId"],
                "startPause-002",
            )

    def test_default_order_id_is_readable_per_order_type(self) -> None:
        self.assertEqual(self.factory.next_order_id("PathNav"), "PathNav-001")
        self.assertEqual(self.factory.next_order_id("FreeNav"), "FreeNav-001")
        self.assertEqual(self.factory.next_order_id("PathNav"), "PathNav-002")

    def test_order_counter_persists_when_counter_path_is_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            counter_path = Path(temp_dir) / "order-id-counts.json"
            first = Vda5050MessageFactory(
                self.identity, order_counter_path=counter_path
            )
            self.assertEqual(first.next_order_id("PathNav"), "PathNav-001")
            second = Vda5050MessageFactory(
                self.identity, order_counter_path=counter_path
            )
            self.assertEqual(second.next_order_id("PathNav"), "PathNav-002")

    def test_instant_action_accepts_explicit_recipe_execution_id(self) -> None:
        payload = self.factory.instant_action("Demo", action_id="Demo-001")
        self.assertEqual(payload["actions"][0]["actionId"], "Demo-001")
        InstantActions.from_dict(payload)

    def test_order_is_accepted_by_unchanged_adapter_v3_parser(self) -> None:
        payload = self.factory.order("manual-1", ["LM1", "LM5", "LM4"])
        parsed = Order.from_dict(payload)
        self.assertEqual(parsed.order_id, "manual-1")
        self.assertEqual([node.sequence_id for node in parsed.nodes], [0, 2, 4])
        self.assertEqual([edge.sequence_id for edge in parsed.edges], [1, 3])

    def test_retargets_old_jibot_instant_action_sample(self) -> None:
        payload = self.factory.retarget(
            "instantActions",
            {
                "headerId": 1,
                "timestamp": "old",
                "version": "3.0.0",
                "manufacturer": "jibot",
                "serialNumber": "OLD",
                "instantActions": [
                    {
                        "actionId": 1,
                        "actionType": "startPause",
                        "blockingType": "NONE",
                        "actionParameter": [],
                    }
                ],
            },
        )
        self.assertEqual(payload["manufacturer"], "seer")
        self.assertEqual(payload["serialNumber"], "SEER-SIM-001")
        self.assertIn("actions", payload)
        self.assertIn("actionParameters", payload["actions"][0])
        InstantActions.from_dict(payload)


class Vda5050ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.output = []
        self.mqtt = _FakeMqttClient()
        self.console = Vda5050MqttConsole(
            Vda5050Identity(serial_number="SEER-SIM-001"),
            host="127.0.0.1",
            port=1883,
            allow_write=True,
            output=self.output.append,
            mqtt_client=self.mqtt,
        )
        self.console.connect()

    async def asyncTearDown(self) -> None:
        self.console.close()

    async def test_connect_subscribes_whole_robot_topic_tree(self) -> None:
        self.assertEqual(self.mqtt.subscriptions, [("amr/v3/SEER-SIM-001/#", 0)])

    async def test_goto_prints_and_publishes_vda_order_json(self) -> None:
        self.assertTrue(await self.console.execute("goto LM4 LM1"))
        topic, raw, qos, retain = self.mqtt.published[-1]
        payload = json.loads(raw)
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/order")
        self.assertEqual(payload["orderId"], "PathNav-001")
        self.assertEqual(qos, 0)
        self.assertFalse(retain)
        self.assertEqual([node["nodeId"] for node in payload["nodes"]], ["LM1", "LM4"])
        self.assertEqual([node["sequenceId"] for node in payload["nodes"]], [0, 2])
        self.assertEqual(payload["edges"][0]["startNodeId"], "LM1")
        self.assertEqual(payload["edges"][0]["endNodeId"], "LM4")
        self.assertIn("[VDA5050 MQTT TX]", "\n".join(self.output))
        Order.from_dict(payload)

    async def test_goto_route_matches_fms_nodes_edges_order(self) -> None:
        self.assertTrue(await self.console.execute("goto_route CP12 LM11 LM1"))
        topic, raw, _, _ = self.mqtt.published[-1]
        payload = json.loads(raw)
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/order")
        self.assertEqual(payload["orderId"], "PathNav-001")
        self.assertEqual(
            [node["nodeId"] for node in payload["nodes"]],
            ["CP12", "LM11", "LM1"],
        )
        self.assertEqual([node["sequenceId"] for node in payload["nodes"]], [0, 2, 4])
        self.assertEqual([edge["sequenceId"] for edge in payload["edges"]], [1, 3])
        self.assertEqual(payload["edges"][0]["startNodeId"], "CP12")
        self.assertEqual(payload["edges"][0]["endNodeId"], "LM11")
        self.assertEqual(payload["edges"][1]["startNodeId"], "LM11")
        self.assertEqual(payload["edges"][1]["endNodeId"], "LM1")
        Order.from_dict(payload)

    async def test_goto_xyz_is_vda_instant_action_for_real_and_simulator_compatibility(self) -> None:
        self.assertTrue(await self.console.execute("goto_xyz 1000 500 90"))
        topic, raw, _, _ = self.mqtt.published[-1]
        payload = json.loads(raw)
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/instantActions")
        action = payload["actions"][0]
        self.assertEqual(action["actionType"], "seerCoordinateNav")
        self.assertEqual(
            {item["key"]: item["value"] for item in action["actionParameters"]},
            {"x": 1000.0, "y": 500.0, "theta_deg": 90.0},
        )
        InstantActions.from_dict(payload)

    async def test_order_action_uses_vda_node_action_shape(self) -> None:
        self.assertTrue(
            await self.console.execute(
                "order_action action-order LM4 seerWait seconds=1.5"
            )
        )
        topic, raw, _, _ = self.mqtt.published[-1]
        payload = json.loads(raw)
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/order")
        action = payload["nodes"][0]["actions"][0]
        self.assertEqual(action["actionType"], "seerWait")
        self.assertIn("actionId", action)
        self.assertEqual(action["blockingType"], "NONE")
        self.assertEqual(action["actionParameters"], [{"key": "seconds", "value": 1.5}])
        Order.from_dict(payload)

    async def test_edge_action_uses_vda_edge_action_shape(self) -> None:
        self.assertTrue(
            await self.console.execute(
                "edge_action edge-order LM1 LM4 seerWait seconds=0.5"
            )
        )
        topic, raw, _, _ = self.mqtt.published[-1]
        payload = json.loads(raw)
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/order")
        action = payload["edges"][0]["actions"][0]
        self.assertEqual(action["actionType"], "seerWait")
        self.assertEqual(action["actionParameters"], [{"key": "seconds", "value": 0.5}])
        Order.from_dict(payload)

    async def test_read_only_mode_allows_request_but_blocks_motion(self) -> None:
        read_only = Vda5050MqttConsole(
            Vda5050Identity(serial_number="SEER-SIM-001"),
            host="127.0.0.1",
            port=1883,
            allow_write=False,
            mqtt_client=self.mqtt,
        )
        read_only.publish_action("stateRequest")
        with self.assertRaises(PermissionError):
            read_only.publish_action("seerTurn", {"angle_deg": 90})

    async def test_unsolicited_state_is_silent_but_last_shows_saved_message(self) -> None:
        message = SimpleNamespace(
            topic="amr/v3/SEER-SIM-001/state",
            payload=b'{"headerId":7,"driving":false}',
        )
        self.console._on_message(self.mqtt, None, message)
        self.assertNotIn("[VDA5050 MQTT RX]", "\n".join(self.output))
        await self.console.execute("last state")
        combined = "\n".join(self.output)
        self.assertIn("[VDA5050 MQTT LAST]", combined)
        self.assertIn('"headerId": 7', combined)

    async def test_watch_temporarily_shows_live_rx_then_restores_quiet(self) -> None:
        message = SimpleNamespace(
            topic="amr/v3/SEER-SIM-001/state",
            payload=b'{"headerId":8,"driving":true}',
        )
        watch_task = asyncio.create_task(self.console.execute("watch 0.01"))
        await asyncio.sleep(0)
        self.console._on_message(self.mqtt, None, message)
        await watch_task
        first_count = "\n".join(self.output).count("[VDA5050 MQTT RX]")
        self.assertEqual(first_count, 1)
        self.console._on_message(self.mqtt, None, message)
        second_count = "\n".join(self.output).count("[VDA5050 MQTT RX]")
        self.assertEqual(second_count, 1)
        self.assertIn("quiet mode restored", "\n".join(self.output))

    async def test_status_shows_only_the_next_state_once(self) -> None:
        message = SimpleNamespace(
            topic="amr/v3/SEER-SIM-001/state",
            payload=b'{"headerId":9,"driving":false}',
        )
        await self.console.execute("status")
        self.console._on_message(self.mqtt, None, message)
        self.console._on_message(self.mqtt, None, message)
        combined = "\n".join(self.output)
        self.assertEqual(combined.count("[VDA5050 MQTT RX]"), 1)
        topic, raw, _, _ = self.mqtt.published[-1]
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/instantActions")
        self.assertEqual(
            json.loads(raw)["actions"][0]["actionType"], "stateRequest"
        )

    async def test_emc_and_release_publish_explicit_status(self) -> None:
        await self.console.execute("emc")
        active = json.loads(self.mqtt.published[-1][1])
        await self.console.execute("emc_release")
        released = json.loads(self.mqtt.published[-1][1])
        self.assertEqual(active["actions"][0]["actionType"], "seerEmergencySwitch")
        self.assertEqual(
            active["actions"][0]["actionParameters"],
            [{"key": "status", "value": "on"}],
        )
        self.assertEqual(
            released["actions"][0]["actionParameters"],
            [{"key": "status", "value": "off"}],
        )


    async def test_jack_aliases_publish_vda5050_instant_actions(self) -> None:
        self.assertTrue(await self.console.execute("jack_load"))
        topic, raw, _, _ = self.mqtt.published[-1]
        payload = json.loads(raw)
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/instantActions")
        self.assertEqual(payload["actions"][0]["actionType"], "seerJackLoad")
        self.assertEqual(payload["actions"][0]["actionParameters"], [])
        InstantActions.from_dict(payload)

        self.assertTrue(await self.console.execute("jack_unload"))
        _topic, raw, _, _ = self.mqtt.published[-1]
        payload = json.loads(raw)
        self.assertEqual(payload["actions"][0]["actionType"], "seerJackUnload")
        self.assertEqual(payload["actions"][0]["actionParameters"], [])
        InstantActions.from_dict(payload)


class Vda5050WebTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mqtt = _FakeMqttClient()
        self.trace = Vda5050TraceStore(
            Vda5050Identity(serial_number="SEER-SIM-001"),
            host="127.0.0.1",
            port=1883,
            mqtt_client=self.mqtt,
        )
        self.trace.start()

    def tearDown(self) -> None:
        self.trace.stop()

    def test_webui_sender_defaults_to_fms_mqtt_publish(self) -> None:
        local = _FakeLocalVdaSender()
        sender = Vda5050WebSender(self.trace, local)
        payload = self.trace.factory.instant_action("seerTurn", {"angle_deg": 90.0})
        delivered, text = sender.send(payload, {"source": "webui"})
        self.assertTrue(delivered, text)
        self.assertEqual(local.sent, [])
        topic, raw, qos, retain = self.mqtt.published[-1]
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/instantActions")
        self.assertEqual(json.loads(raw), payload)
        self.assertEqual(qos, 0)
        self.assertFalse(retain)
        last = self.trace.snapshot()["records"][0]
        self.assertEqual(last["direction"], "WEBUI MQTT TX")
        self.assertIn("MQTT broker", last["transport"])
        InstantActions.from_dict(json.loads(raw))

    def test_webui_sender_defaults_path_nav_order_to_fms_mqtt(self) -> None:
        local = _FakeLocalVdaSender()
        sender = Vda5050WebSender(self.trace, local)
        payload = self.trace.factory.order(
            "path-nav-001",
            ["LM7", "LM3", "LM4", "LM1"],
        )
        delivered, text = sender.send_order(payload, {"source": "path-nav"})
        self.assertTrue(delivered, text)
        self.assertEqual(local.sent, [])
        topic, raw, qos, retain = self.mqtt.published[-1]
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/order")
        self.assertEqual(json.loads(raw), payload)
        self.assertEqual(qos, 0)
        self.assertFalse(retain)
        Order.from_dict(json.loads(raw))

    def test_webui_sender_rewrites_shared_webui_uuid_to_readable_action_id(self) -> None:
        local = _FakeLocalVdaSender()
        sender = Vda5050WebSender(self.trace, local)
        payload = self.trace.factory.instant_action(
            "startPause",
            action_id="1b2d3e12-52c0-4e9a-b011-c1975d5d40ac",
        )
        delivered, text = sender.send(payload, {"source": "shared-webui"})
        self.assertTrue(delivered, text)
        self.assertEqual(local.sent, [])
        topic, raw, _, _ = self.mqtt.published[-1]
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/instantActions")
        self.assertEqual(
            json.loads(raw)["actions"][0]["actionId"],
            "startPause-001",
        )
        # send_exact clones before normalization; an explicitly assembled source
        # object is never mutated in the caller.
        self.assertEqual(
            payload["actions"][0]["actionId"],
            "1b2d3e12-52c0-4e9a-b011-c1975d5d40ac",
        )

    def test_webui_local_sender_delivers_complete_order_with_actions(self) -> None:
        local = _FakeLocalVdaSender()
        sender = Vda5050WebSender(self.trace, local, mode="local")
        wait = {
            "actionId": "wait-1",
            "actionType": "seerWait",
            "blockingType": "HARD",
            "actionParameters": [{"key": "seconds", "value": 1.0}],
        }
        payload = self.trace.factory.order(
            "local-order",
            ["LM7", "LM3", "LM4", "LM1"],
            node_actions={"LM3": [wait]},
        )
        delivered, text = sender.send_order(payload, {"confirmed": True})
        self.assertTrue(delivered, text)
        self.assertEqual(self.mqtt.published, [])
        suffix, wire, meta = local.sent[0]
        self.assertEqual(suffix, "order")
        self.assertEqual(wire, payload)
        self.assertTrue(meta["confirmed"])
        Order.from_dict(wire)

    def test_vda_page_shows_mqtt_default_and_local_fallback(self) -> None:
        local = _FakeLocalVdaSender()
        sender = Vda5050WebSender(self.trace, local)

        class FakeRender:
            @staticmethod
            def page(title, body, **kwargs):
                del title, kwargs
                return body

        spec = SimpleNamespace(
            key="seer:SEER-SIM-001",
            serial="SEER-SIM-001",
            display_name="SEER SIM",
        )
        body = render_vda5050_page(
            FakeRender,
            traces={spec.key: self.trace},
            senders={spec.key: sender},
            specs=[spec],
            csrf="csrf-token",
            query={"robot": spec.key},
        )
        self.assertIn("WebUI 명령 전송 모드", body)
        self.assertIn('value="local"', body)
        self.assertIn('value="mqtt" checked', body)
        self.assertIn("FMS MQTT (기본/권장)", body)
        self.assertIn("항상 MQTT broker로 발행", body)
        self.assertIn('>SEER SIM</option>', body)
        self.assertNotIn('>SEER-SIM-001</option>', body)

    def test_webui_sender_can_switch_to_fms_mqtt(self) -> None:
        local = _FakeLocalVdaSender()
        sender = Vda5050WebSender(self.trace, local, mode="local")
        sender.set_mode("mqtt")
        payload = self.trace.factory.instant_action("seerTurn", {"angle_deg": 90.0})
        delivered, text = sender.send(payload, {"source": "webui"})
        self.assertTrue(delivered, text)
        self.assertEqual(local.sent, [])
        topic, raw, qos, retain = self.mqtt.published[-1]
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/instantActions")
        self.assertEqual(json.loads(raw), payload)
        self.assertEqual(qos, 0)
        self.assertFalse(retain)

    def test_webui_and_fms_share_same_mqtt_topic_tree(self) -> None:
        sender = Vda5050MqttSender(self.trace)
        payload = self.trace.factory.instant_action("seerTurn", {"angle_deg": 90.0})
        delivered, text = sender.send(payload, {"source": "webui"})
        self.assertTrue(delivered, text)
        topic, raw, qos, retain = self.mqtt.published[-1]
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/instantActions")
        self.assertEqual(json.loads(raw), payload)
        self.assertEqual(qos, 0)
        self.assertFalse(retain)
        InstantActions.from_dict(json.loads(raw))
        last = self.trace.snapshot()["records"][0]
        self.assertEqual(last["direction"], "WEBUI MQTT TX")
        self.assertIn("FMS-identical", last["transport"])

    def test_webui_can_publish_complete_order_without_ipc_wrapper(self) -> None:
        payload = Vda5050MessageFactory(
            Vda5050Identity(serial_number="SEER-SIM-001")
        ).order("web-order", ["LM1", "LM4"])
        sent = self.trace.publish_exact("order", payload)
        self.assertEqual(sent, payload)
        topic, raw, _, _ = self.mqtt.published[-1]
        self.assertEqual(topic, "amr/v3/SEER-SIM-001/order")
        self.assertEqual(json.loads(raw), payload)
        Order.from_dict(json.loads(raw))

    def test_webui_order_preserves_node_and_edge_actions(self) -> None:
        factory = Vda5050MessageFactory(Vda5050Identity(serial_number="SEER-SIM-001"))
        wait = {
            "actionId": "wait-1",
            "actionType": "seerWait",
            "blockingType": "HARD",
            "actionParameters": [{"key": "seconds", "value": 1.0}],
        }
        payload = factory.order(
            "web-action-order",
            ["LM1", "LM4"],
            node_actions={"LM4": [wait]},
            edge_actions={("LM1", "LM4"): [wait]},
        )
        self.trace.publish_exact("order", payload)
        wire = json.loads(self.mqtt.published[-1][1])
        self.assertEqual(wire["nodes"][1]["actions"][0], wait)
        self.assertEqual(wire["edges"][0]["actions"][0], wait)
        Order.from_dict(wire)

    def test_publish_exact_rejects_wrong_robot_identity(self) -> None:
        payload = self.trace.factory.instant_action("stateRequest")
        payload["serialNumber"] = "OTHER-ROBOT"
        with self.assertRaises(ValueError):
            self.trace.publish_exact("instantActions", payload)

    def test_raw_test_publisher_retargets_a_sample_then_uses_mqtt(self) -> None:
        payload = self.trace.publish_test(
            "order",
            {
                "headerId": 1,
                "timestamp": "old",
                "version": "3.0.0",
                "manufacturer": "jibot",
                "serialNumber": "OLD",
                "orderId": "web-order",
                "orderUpdateId": 0,
                "nodes": [{"nodeId": "LM1", "sequenceId": 0, "released": True, "actions": []}],
                "edges": [],
            },
        )
        self.assertEqual(payload["manufacturer"], "seer")
        self.assertEqual(payload["serialNumber"], "SEER-SIM-001")
        self.assertEqual(self.mqtt.published[-1][0], "amr/v3/SEER-SIM-001/order")
        Order.from_dict(json.loads(self.mqtt.published[-1][1]))


if __name__ == "__main__":
    unittest.main()
