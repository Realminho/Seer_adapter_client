import asyncio
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

from core.action_registry import (
    ActionRegistry,
    ActionResult,
    ActionSpec,
    build_registry_from_config,
    build_subprocess_payload,
)
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


def test_action_spec_label_defaults_empty_and_roundtrips():
    from core.action_registry import ActionParameterSpec, ActionSpec

    assert ActionSpec(action_type="x").label == ""
    assert ActionSpec(action_type="x", label="Air shower start").label == "Air shower start"
    parameter = ActionParameterSpec("doorPin", required=True, input_type="number")
    assert ActionSpec(action_type="x", parameters=(parameter,)).parameters == (parameter,)


class _Param:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _Action:
    def __init__(self, action_type="customPing", action_id="a1", params=None):
        self.action_type = action_type
        self.action_id = action_id
        self.action_parameters = [
            _Param(key, value) for key, value in (params or {}).items()
        ]


class _Adapter:
    def __init__(self):
        self.status_updates = []
        self.scheduled = []
        self.config = SimpleNamespace(
            vehicle=SimpleNamespace(serial_number="HN"),
            settings=SimpleNamespace(debug_log=False),
            mqtt_broker=SimpleNamespace(host="127.0.0.1", password="secret"),
            web_ui=SimpleNamespace(token="ui-token"),
        )
        self.state = SimpleNamespace(
            agv_position=SimpleNamespace(x=1.0, y=2.0, theta=3.0, map_id="M1"),
            order_id="order-1",
            paused=True,
        )
        self._current_map_id = "M1"
        self._last_node_id = "N1"
        self._last_node_sequence_id = 4
        self._work_in_progress = "loading"

    def _update_instant_action_status(self, action_id, status, result_description=None):
        self.status_updates.append((action_id, status, result_description))

    def _run_on_adapter_loop(self, coro_factory):
        self.scheduled.append(coro_factory)


class ActionRegistryTest(unittest.TestCase):
    def test_rejects_non_terminal_action_result(self):
        with self.assertRaises(ValueError):
            ActionResult(ActionStatus.RUNNING, "still running")

    def test_rejects_duplicate_action_type(self):
        registry = ActionRegistry()
        registry.register(ActionSpec(action_type="customPing", handler=lambda ctx: None))
        with self.assertRaises(ValueError):
            registry.register(ActionSpec(action_type="customPing", handler=lambda ctx: None))

    def test_inline_dispatch_updates_terminal_status_immediately(self):
        adapter = _Adapter()
        registry = ActionRegistry()
        registry.register(
            ActionSpec(
                action_type="customPing",
                handler=lambda ctx: ActionResult(
                    ActionStatus.FINISHED, f"pong:{ctx.params['value']}"
                ),
            )
        )

        handled = registry.dispatch(_Action(params={"value": "42"}), adapter)

        self.assertTrue(handled)
        self.assertEqual(adapter.scheduled, [])
        self.assertEqual(
            adapter.status_updates,
            [("a1", ActionStatus.FINISHED, "pong:42")],
        )

    def test_inline_exception_reports_failed(self):
        adapter = _Adapter()
        registry = ActionRegistry()

        def boom(ctx):
            raise RuntimeError("bad custom action")

        registry.register(ActionSpec(action_type="customPing", handler=boom))

        self.assertTrue(registry.dispatch(_Action(), adapter))
        self.assertEqual(adapter.status_updates[0][1], ActionStatus.FAILED)
        self.assertIn("bad custom action", adapter.status_updates[0][2])

    def test_inline_invalid_result_reports_failed(self):
        adapter = _Adapter()
        registry = ActionRegistry()
        registry.register(ActionSpec(action_type="customPing", handler=lambda ctx: "ok"))

        self.assertTrue(registry.dispatch(_Action(), adapter))
        self.assertEqual(adapter.status_updates[0][1], ActionStatus.FAILED)
        self.assertIn("ActionResult", adapter.status_updates[0][2])

    def test_async_inline_dispatch_marks_running_and_schedules_completion(self):
        adapter = _Adapter()
        registry = ActionRegistry()

        async def handle(ctx):
            await asyncio.sleep(0)
            return ActionResult(ActionStatus.FINISHED, "async done")

        registry.register(ActionSpec(action_type="customAsync", handler=handle))

        handled = registry.dispatch(_Action("customAsync"), adapter)
        self.assertTrue(handled)
        self.assertEqual(adapter.status_updates, [("a1", ActionStatus.RUNNING, None)])
        self.assertEqual(len(adapter.scheduled), 1)

        asyncio.run(adapter.scheduled[0]())
        self.assertEqual(adapter.status_updates[-1], ("a1", ActionStatus.FINISHED, "async done"))

    def test_async_inline_timeout_reports_failed(self):
        adapter = _Adapter()
        registry = ActionRegistry()

        async def handle(ctx):
            await asyncio.sleep(5)
            return ActionResult(ActionStatus.FINISHED, "late")

        registry.register(
            ActionSpec(action_type="customAsync", handler=handle, timeout_sec=0.01)
        )

        handled = registry.dispatch(_Action("customAsync"), adapter)
        self.assertTrue(handled)
        self.assertEqual(adapter.status_updates, [("a1", ActionStatus.RUNNING, None)])

        asyncio.run(adapter.scheduled[0]())
        self.assertEqual(adapter.status_updates[-1][1], ActionStatus.FAILED)
        self.assertIn("timeout", adapter.status_updates[-1][2])

    def test_async_inline_without_running_loop_fails_without_sticking_running(self):
        adapter = _Adapter()
        adapter._loop = None
        registry = ActionRegistry()

        async def handle(ctx):
            return ActionResult(ActionStatus.FINISHED, "done")

        registry.register(ActionSpec(action_type="customAsync", handler=handle))

        handled = registry.dispatch(_Action("customAsync"), adapter)

        self.assertTrue(handled)
        self.assertEqual(adapter.scheduled, [])
        self.assertEqual(adapter.status_updates[-1][1], ActionStatus.FAILED)
        self.assertIn("event loop", adapter.status_updates[-1][2])

    def test_subprocess_success_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = os.path.join(tmp, "ok.py")
            hang = os.path.join(tmp, "hang.py")
            with open(ok, "w", encoding="utf-8") as f:
                f.write(
                    "import json, sys\n"
                    "json.load(sys.stdin)\n"
                    "print(json.dumps({'status': 'FINISHED', 'description': 'ok'}))\n"
                )
            with open(hang, "w", encoding="utf-8") as f:
                f.write("import time\n" "time.sleep(5)\n")

            adapter = _Adapter()
            registry = ActionRegistry()
            registry.register(
                ActionSpec(
                    action_type="customProc",
                    runner="subprocess",
                    command=[sys.executable, ok],
                    timeout_sec=1,
                )
            )
            registry.dispatch(_Action("customProc"), adapter)
            asyncio.run(adapter.scheduled.pop()())
            self.assertEqual(adapter.status_updates[-1], ("a1", ActionStatus.FINISHED, "ok"))

            registry = ActionRegistry()
            adapter = _Adapter()
            registry.register(
                ActionSpec(
                    action_type="customProc",
                    runner="subprocess",
                    command=[sys.executable, hang],
                    timeout_sec=0.01,
                )
            )
            registry.dispatch(_Action("customProc"), adapter)
            asyncio.run(adapter.scheduled.pop()())
            self.assertEqual(adapter.status_updates[-1][1], ActionStatus.FAILED)
            self.assertIn("timeout", adapter.status_updates[-1][2])

    def test_subprocess_running_stdout_is_failed_not_stuck(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "running.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write(
                    "import json\n"
                    "print(json.dumps({'status': 'RUNNING', 'description': 'bad'}))\n"
                )
            adapter = _Adapter()
            registry = ActionRegistry()
            registry.register(
                ActionSpec(
                    action_type="customProc",
                    runner="subprocess",
                    command=[sys.executable, script],
                    timeout_sec=1,
                )
            )
            registry.dispatch(_Action("customProc"), adapter)
            asyncio.run(adapter.scheduled.pop()())
            self.assertEqual(adapter.status_updates[-1][1], ActionStatus.FAILED)
            self.assertIn("terminal", adapter.status_updates[-1][2])

    def test_build_subprocess_payload_uses_minimal_snapshot(self):
        adapter = _Adapter()
        payload = build_subprocess_payload(_Action(params={"x": "1"}), adapter)

        self.assertEqual(payload["action_id"], "a1")
        self.assertEqual(payload["params"], {"x": "1"})
        self.assertEqual(
            set(payload["snapshot"]),
            {
                "serial_number",
                "simulation",
                "current_map_id",
                "last_node_id",
                "last_node_sequence_id",
                "pose",
                "order_id",
                "paused",
                "work_in_progress",
            },
        )
        self.assertNotIn("mqtt_broker", json.dumps(payload))

    def test_build_subprocess_payload_denies_sensitive_opt_in_snapshot_fields(self):
        adapter = _Adapter()

        payload = build_subprocess_payload(
            _Action(),
            adapter,
            snapshot_fields=[
                "config.settings.debug_log",
                "config.mqtt_broker.host",
                "config.web_ui.token",
            ],
        )

        self.assertEqual(payload["snapshot"]["config.settings.debug_log"], False)
        self.assertNotIn("config.mqtt_broker.host", payload["snapshot"])
        self.assertNotIn("config.web_ui.token", payload["snapshot"])

    def test_build_registry_from_config_skips_bad_inline_module(self):
        registry = build_registry_from_config(
            [
                SimpleNamespace(
                    action_type="badInline",
                    runner="inline",
                    module="missing_custom_action_module",
                    command=[],
                    timeout_sec=0,
                    motion=False,
                    snapshot_fields=[],
                ),
                SimpleNamespace(
                    action_type="goodProc",
                    runner="subprocess",
                    module=None,
                    command=[sys.executable, "-c", "print('ok')"],
                    timeout_sec=1,
                    motion=False,
                    snapshot_fields=[],
                ),
            ]
        )

        self.assertFalse(registry.has("badInline"))
        self.assertTrue(registry.has("goodProc"))

    def test_build_registry_from_config_skips_builtin_action_type(self):
        registry = build_registry_from_config(
            [
                SimpleNamespace(
                    action_type="manualDrive",
                    runner="subprocess",
                    module=None,
                    command=[sys.executable, "-c", "print('ok')"],
                    timeout_sec=1,
                    motion=True,
                    snapshot_fields=[],
                )
            ]
        )

        self.assertFalse(registry.has("manualDrive"))

    def test_build_registry_from_config_keeps_first_party_action_over_config(self):
        registry = build_registry_from_config(
            [
                SimpleNamespace(
                    action_type="pioReadIn",
                    runner="subprocess",
                    module=None,
                    command=[sys.executable, "-c", "print('bad')"],
                    timeout_sec=1,
                    motion=True,
                    snapshot_fields=[],
                )
            ],
            first_party_specs=[
                ActionSpec(
                    action_type="pioReadIn",
                    handler=lambda ctx: ActionResult(ActionStatus.FINISHED, "first"),
                )
            ],
        )

        adapter = _Adapter()
        self.assertTrue(registry.dispatch(_Action("pioReadIn"), adapter))
        self.assertEqual(adapter.status_updates[-1], ("a1", ActionStatus.FINISHED, "first"))

    def test_build_registry_from_config_skips_disabled_custom_action(self):
        registry = build_registry_from_config(
            [
                SimpleNamespace(
                    action_type="customDoorOpen",
                    enabled=False,
                    runner="subprocess",
                    module=None,
                    command=[sys.executable, "-c", "print('ok')"],
                    timeout_sec=1,
                    motion=False,
                    snapshot_fields=[],
                )
            ]
        )

        self.assertFalse(registry.has("customDoorOpen"))

    def test_build_registry_from_config_disables_first_party_action(self):
        registry = build_registry_from_config(
            [
                SimpleNamespace(
                    action_type="pioReadIn",
                    enabled=False,
                    runner="subprocess",
                    module=None,
                    command=[sys.executable, "-c", "print('bad')"],
                    timeout_sec=1,
                    motion=True,
                    snapshot_fields=[],
                )
            ],
            first_party_specs=[
                ActionSpec(
                    action_type="pioReadIn",
                    handler=lambda ctx: ActionResult(ActionStatus.FINISHED, "first"),
                ),
                ActionSpec(
                    action_type="pioWriteOut",
                    handler=lambda ctx: ActionResult(ActionStatus.FINISHED, "first"),
                ),
            ],
        )

        self.assertFalse(registry.has("pioReadIn"))
        self.assertTrue(registry.has("pioWriteOut"))

    def test_action_types_lists_enabled_registered_actions(self):
        registry = ActionRegistry(
            [
                ActionSpec(action_type="customA", handler=lambda ctx: None),
                ActionSpec(action_type="customB", handler=lambda ctx: None),
            ]
        )

        self.assertEqual(registry.action_types(), ("customA", "customB"))


if __name__ == "__main__":
    unittest.main()
