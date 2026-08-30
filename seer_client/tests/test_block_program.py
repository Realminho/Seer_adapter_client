from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
for source in ("seer_client/src", "adaptor"):
    path = str(REPO_ROOT / source)
    if path not in sys.path:
        sys.path.insert(0, path)

from core.action_registry import ActionResult  # pyright: ignore[reportMissingImports]  # noqa: E402
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.block_program import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    BlockProgramExecutor,
    VARIABLE_MARKER,
    encode_program,
    make_program_handler,
    program_variables,
)


class BlockProgramTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeat_if_else_and_omitted_variable_default_execute(self):
        calls = []

        class Registry:
            async def execute(self, action, _adapter):
                params = {
                    item.key: item.value for item in action.action_parameters
                }
                calls.append((action.action_type, params))
                return ActionResult(ActionStatus.FINISHED, "ok")

        class Vehicle:
            _battery = 75.0

            async def get_battery_info(self):
                return {"battery_level": 75.0}

        nodes = (
            {
                "kind": "repeat",
                "count": {VARIABLE_MARKER: "repeat_count", "default": 2},
                "children": [
                    {
                        "kind": "set_do",
                        "action_type": "seerSetDO",
                        "parameters": {"id": 1, "status": "on"},
                        "delay_sec": 0,
                    }
                ],
                "delay_sec": 0,
            },
            {
                "kind": "if_else",
                "condition": {
                    "source": "battery",
                    "channel": 0,
                    "operator": ">=",
                    "expected": {VARIABLE_MARKER: "minimum_battery", "default": 50},
                },
                "children": [
                    {
                        "kind": "wait",
                        "action_type": "seerWait",
                        "parameters": {"seconds": 0},
                        "delay_sec": 0,
                    }
                ],
                "else_children": [
                    {
                        "kind": "set_do",
                        "action_type": "seerSetDO",
                        "parameters": {"id": 1, "status": "off"},
                        "delay_sec": 0,
                    }
                ],
                "delay_sec": 0,
            },
        )
        encoded = encode_program(nodes)
        self.assertEqual(program_variables(encoded), ("repeat_count", "minimum_battery"))
        adapter = SimpleNamespace(
            _vehicle=Vehicle(),
            _action_registry=Registry(),
            _set_active_action_step=lambda *_args: None,
        )
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="nested-1"),
            adapter=adapter,
            params={},
        )
        result = await make_program_handler(encoded)(context)
        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(
            calls,
            [
                ("seerSetDO", {"id": 1, "status": "on"}),
                ("seerSetDO", {"id": 1, "status": "on"}),
                ("seerWait", {"seconds": 0}),
            ],
        )

    async def test_di_signal_container_runs_children_then_system_blocks(self):
        calls = []

        class Registry:
            async def execute(self, action, _adapter):
                params = {item.key: item.value for item in action.action_parameters}
                calls.append((action.action_type, params))
                return ActionResult(ActionStatus.FINISHED, "ok")

        class Vehicle:
            def __init__(self):
                self.di_values = [False, True]
                self.maps = []
                self.relocations = []
                self.motor_enabled = True

            async def read_di(self, _channel):
                if len(self.di_values) > 1:
                    return self.di_values.pop(0)
                return self.di_values[0]

            async def map_switch(self, name):
                self.maps.append(name)

            async def relocation(self, **values):
                self.relocations.append(values)

            async def disable_motor(self):
                self.motor_enabled = False

            async def get_emergency_state(self):
                return {"emergency": False}

            @staticmethod
            def position_from_native(value):
                return value

            @staticmethod
            def angle_from_native(value):
                return value

        vehicle = Vehicle()
        nodes = (
            {
                "kind": "on_di",
                "parameters": {
                    "channel": 2,
                    "mode": "rising",
                    "timeout_sec": 1,
                    "poll_interval_sec": 0.05,
                },
                "children": [
                    {
                        "kind": "set_do",
                        "action_type": "seerSetDO",
                        "parameters": {"id": 3, "status": "on"},
                        "delay_sec": 0,
                    }
                ],
                "delay_sec": 0,
            },
            {
                "kind": "switch_map",
                "parameters": {"map_name": "factory_2"},
                "delay_sec": 0,
            },
            {
                "kind": "relocate",
                "parameters": {
                    "mode": "manual",
                    "x": 1.0,
                    "y": 2.0,
                    "theta_deg": 90.0,
                },
                "delay_sec": 0,
            },
            {
                "kind": "set_motor",
                "parameters": {"target": "all", "motor_name": "", "status": "off"},
                "delay_sec": 0,
            },
        )
        adapter = SimpleNamespace(
            _vehicle=vehicle,
            _action_registry=Registry(),
            _set_active_action_step=lambda *_args: None,
        )
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="signal-1"),
            adapter=adapter,
            params={},
        )
        result = await make_program_handler(encode_program(nodes))(context)
        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(calls, [("seerSetDO", {"id": 3, "status": "on"})])
        self.assertEqual(vehicle.maps, ["factory_2"])
        self.assertFalse(vehicle.motor_enabled)
        self.assertEqual(vehicle.relocations[0]["x"], 1.0)
        self.assertAlmostEqual(vehicle.relocations[0]["angle"], 1.57079632679)

    async def test_repeat_until_and_runtime_variables_are_executable(self):
        calls = []

        class Registry:
            async def execute(self, action, _adapter):
                calls.append(action.action_type)
                return ActionResult(ActionStatus.FINISHED, "ok")

        class Vehicle:
            def __init__(self):
                self.values = [False, False, True]

            async def read_di(self, _channel):
                return self.values.pop(0) if len(self.values) > 1 else self.values[0]

        nodes = (
            {
                "kind": "set_variable",
                "parameters": {"name": "wait_value", "value": 0},
                "delay_sec": 0,
            },
            {
                "kind": "change_variable",
                "parameters": {"name": "wait_value", "amount": 1},
                "delay_sec": 0,
            },
            {
                "kind": "if",
                "condition": {
                    "source": "variable",
                    "variable_name": "wait_value",
                    "operator": "==",
                    "expected": 1,
                },
                "children": [
                    {
                        "kind": "wait",
                        "action_type": "seerWait",
                        "parameters": {"seconds": 0},
                        "delay_sec": 0,
                    }
                ],
                "delay_sec": 0,
            },
            {
                "kind": "repeat_until",
                "condition": {
                    "source": "di",
                    "channel": 1,
                    "operator": "==",
                    "expected": {VARIABLE_MARKER: "wait_value", "default": 1},
                },
                "max_count": 5,
                "children": [
                    {
                        "kind": "wait",
                        "action_type": "seerWait",
                        "parameters": {"seconds": 0},
                        "delay_sec": 0,
                    }
                ],
                "delay_sec": 0,
            },
        )
        adapter = SimpleNamespace(
            _vehicle=Vehicle(),
            _action_registry=Registry(),
            _set_active_action_step=lambda *_args: None,
        )
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="repeat-until-1"),
            adapter=adapter,
            params={},
        )
        result = await make_program_handler(encode_program(nodes))(context)
        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(calls, ["seerWait", "seerWait", "seerWait"])

    async def test_entry_style_counter_reaches_four_and_repeat_until_stops(self):
        nodes = (
            {
                "kind": "set_variable",
                "parameters": {"name": "result", "value": 0},
                "delay_sec": 0,
            },
            {
                "kind": "repeat_until",
                "condition": {
                    "source": "variable",
                    "variable_name": "result",
                    "operator": "==",
                    "expected": 4,
                },
                "max_count": 10,
                "children": [
                    {
                        "kind": "change_variable",
                        "parameters": {"name": "result", "amount": 1},
                        "delay_sec": 0,
                    }
                ],
                "delay_sec": 0,
            },
        )
        adapter = SimpleNamespace(_set_active_action_step=lambda *_args: None)
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="counter-until-four"),
            adapter=adapter,
            params={},
        )
        executor = BlockProgramExecutor(context, nodes)
        result = await executor.run()

        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(executor.variables["result"], 4)

    async def test_signal_send_wait_calculation_judgment_data_and_function(self):
        calls = []

        class Registry:
            async def execute(self, action, _adapter):
                params = {item.key: item.value for item in action.action_parameters}
                calls.append((action.action_type, params))
                return ActionResult(ActionStatus.FINISHED, "ok")

        class Vehicle:
            _battery = 72.0

            def __init__(self):
                self.di_values = [False, True]

            async def read_di(self, _channel):
                return self.di_values.pop(0) if len(self.di_values) > 1 else self.di_values[0]

            async def get_battery_info(self):
                return {"battery_level": self._battery}

        nodes = (
            {
                "kind": "define_function",
                "parameters": {"function_name": "handshake"},
                "children": [
                    {
                        "kind": "send_do_wait_di",
                        "parameters": {
                            "do_id": 2,
                            "do_status": "on",
                            "di_channel": 3,
                            "di_mode": "rising",
                            "timeout_sec": 1,
                            "poll_interval_sec": 0.05,
                            "final_do_status": "off",
                        },
                        "delay_sec": 0,
                    },
                    {
                        "kind": "calculate",
                        "parameters": {
                            "result_name": "sum",
                            "left": 2,
                            "operator": "add",
                            "right": 3,
                        },
                        "delay_sec": 0,
                    },
                    {
                        "kind": "judge",
                        "parameters": {
                            "result_name": "passed",
                            "left": "$sum",
                            "operator": ">=",
                            "right": 5,
                        },
                        "delay_sec": 0,
                    },
                    {
                        "kind": "logic",
                        "parameters": {
                            "result_name": "ready",
                            "left": "$passed",
                            "operator": "and",
                            "right": True,
                        },
                        "delay_sec": 0,
                    },
                    {
                        "kind": "read_state",
                        "parameters": {
                            "source": "battery",
                            "channel": 0,
                            "result_name": "battery_now",
                        },
                        "delay_sec": 0,
                    },
                ],
                "delay_sec": 0,
            },
            {
                "kind": "call_function",
                "parameters": {"function_name": "handshake"},
                "delay_sec": 0,
            },
        )
        adapter = SimpleNamespace(
            _vehicle=Vehicle(),
            _action_registry=Registry(),
            _set_active_action_step=lambda *_args: None,
        )
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="function-1"),
            adapter=adapter,
            params={},
        )
        executor = BlockProgramExecutor(context, nodes)
        result = await executor.run()

        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(
            calls,
            [
                ("seerSetDO", {"id": 2, "status": "on"}),
                ("seerSetDO", {"id": 2, "status": "off"}),
            ],
        )
        self.assertEqual(executor.variables["sum"], 5)
        self.assertEqual(executor.variables["passed"], 1)
        self.assertEqual(executor.variables["ready"], 1)
        self.assertEqual(executor.variables["battery_now"], 72.0)

    async def test_forever_continue_break_and_restart_are_bounded_and_executable(self):
        class Registry:
            async def execute(self, _action, _adapter):
                return ActionResult(ActionStatus.FINISHED, "ok")

        nodes = (
            {
                "kind": "change_variable",
                "parameters": {"name": "runs", "amount": 1},
                "delay_sec": 0,
            },
            {
                "kind": "if",
                "condition": {
                    "source": "variable",
                    "variable_name": "runs",
                    "operator": "<",
                    "expected": 2,
                },
                "children": [{"kind": "restart_program", "parameters": {}, "delay_sec": 0}],
                "else_children": [],
                "delay_sec": 0,
            },
            {
                "kind": "set_variable",
                "parameters": {"name": "loop_count", "value": 0},
                "delay_sec": 0,
            },
            {
                "kind": "forever",
                "children": [
                    {
                        "kind": "change_variable",
                        "parameters": {"name": "loop_count", "amount": 1},
                        "delay_sec": 0,
                    },
                    {
                        "kind": "if_else",
                        "condition": {
                            "source": "variable",
                            "variable_name": "loop_count",
                            "operator": "<",
                            "expected": 2,
                        },
                        "children": [
                            {"kind": "continue_loop", "parameters": {}, "delay_sec": 0}
                        ],
                        "else_children": [
                            {"kind": "break_loop", "parameters": {}, "delay_sec": 0}
                        ],
                        "delay_sec": 0,
                    },
                ],
                "delay_sec": 0,
            },
        )
        adapter = SimpleNamespace(
            _vehicle=SimpleNamespace(),
            _action_registry=Registry(),
            _set_active_action_step=lambda *_args: None,
        )
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="flow-1"),
            adapter=adapter,
            params={"runs": 0},
        )
        executor = BlockProgramExecutor(context, nodes)
        result = await executor.run()

        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(executor.variables["runs"], 2)
        self.assertEqual(executor.variables["loop_count"], 2)
        self.assertIn("1 restarts", result.description)


    async def test_runtime_snapshot_tracks_current_block_variables_and_completion(self):
        nodes = (
            {
                "kind": "set_variable",
                "parameters": {"name": "result", "value": 0},
                "delay_sec": 0,
            },
            {
                "kind": "repeat_until",
                "condition": {
                    "source": "variable",
                    "variable_name": "result",
                    "operator": "==",
                    "expected": 4,
                },
                "max_count": 10,
                "children": [
                    {
                        "kind": "change_variable",
                        "parameters": {"name": "result", "amount": 1},
                        "delay_sec": 0,
                    }
                ],
                "delay_sec": 0,
            },
        )
        adapter = SimpleNamespace(_set_active_action_step=lambda *_args: None)
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="runtime-counter", action_type="runtimeCounter"),
            adapter=adapter,
            params={},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "seer-block-runtime.json"
            with mock.patch.dict(os.environ, {"SEER_BLOCK_RUNTIME_PATH": str(runtime_path)}):
                result = await BlockProgramExecutor(
                    context, nodes, recipe_name="runtimeCounter"
                ).run()
            payload = json.loads(runtime_path.read_text(encoding="utf-8"))

        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(payload["status"], "finished")
        self.assertEqual(payload["recipe_name"], "runtimeCounter")
        self.assertEqual(payload["variables"]["result"], 4)
        self.assertIn("r.1", payload["completed_trace_ids"])
        self.assertIn("r.2", payload["completed_trace_ids"])
        self.assertIn("r.2.c.1", payload["completed_trace_ids"])
        self.assertEqual(payload["block_counts"]["r.2.c.1"], 4)
        self.assertTrue(payload["condition"]["result"])

    async def test_runtime_completion_badges_reset_for_each_loop_iteration_and_function_call(self):
        nodes = (
            {
                "kind": "define_function",
                "parameters": {"function_name": "bump"},
                "children": [
                    {
                        "kind": "change_variable",
                        "parameters": {"name": "result", "amount": 1},
                        "delay_sec": 0,
                    }
                ],
            },
            {
                "kind": "set_variable",
                "parameters": {"name": "result", "value": 0},
                "delay_sec": 0,
            },
            {
                "kind": "repeat",
                "count": 2,
                "children": [
                    {
                        "kind": "call_function",
                        "parameters": {"function_name": "bump"},
                        "delay_sec": 0,
                    }
                ],
                "delay_sec": 0,
            },
        )
        adapter = SimpleNamespace(_set_active_action_step=lambda *_args: None)
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="runtime-loop", action_type="runtimeLoop"),
            adapter=adapter,
            params={},
        )
        executor = BlockProgramExecutor(context, nodes, recipe_name="runtimeLoop")
        snapshots = []

        def capture(status="running", *, error=""):
            snapshots.append(executor._runtime_payload(status, error=error))

        executor._publish_runtime = capture
        result = await executor.run()

        self.assertEqual(result.status, ActionStatus.FINISHED)
        second_iteration = next(
            payload
            for payload in snapshots
            if payload["phase"] == "loop"
            and payload["loops"]
            and payload["loops"][-1]["trace_id"] == "r.3"
            and payload["loops"][-1]["iteration"] == 2
        )
        self.assertNotIn("r.3.c.1", second_iteration["completed_trace_ids"])
        self.assertNotIn("r.1.c.1", second_iteration["completed_trace_ids"])
        self.assertEqual(executor.variables["result"], 2)

    async def test_runtime_snapshot_exposes_long_running_action_before_completion(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class Registry:
            async def execute(self, _action, _adapter):
                started.set()
                await release.wait()
                return ActionResult(ActionStatus.FINISHED, "ok")

        nodes = (
            {
                "kind": "path_nav",
                "action_type": "seerPathNav",
                "parameters": {"id": "LM1"},
                "delay_sec": 0,
            },
        )
        adapter = SimpleNamespace(
            _action_registry=Registry(),
            _set_active_action_step=lambda *_args: None,
        )
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="runtime-path", action_type="pathRecipe"),
            adapter=adapter,
            params={},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "seer-block-runtime.json"
            with mock.patch.dict(os.environ, {"SEER_BLOCK_RUNTIME_PATH": str(runtime_path)}):
                task = asyncio.create_task(
                    BlockProgramExecutor(context, nodes, recipe_name="pathRecipe").run()
                )
                await asyncio.wait_for(started.wait(), timeout=1.0)
                running = json.loads(runtime_path.read_text(encoding="utf-8"))
                self.assertEqual(running["status"], "running")
                self.assertEqual(running["phase"], "action")
                self.assertEqual(running["current_trace_id"], "r.1")
                self.assertEqual(running["current_kind"], "path_nav")
                release.set()
                result = await task
                finished = json.loads(runtime_path.read_text(encoding="utf-8"))

        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(finished["status"], "finished")
        self.assertIn("r.1", finished["completed_trace_ids"])

    async def test_can_start_from_selected_top_level_block(self):
        calls = []

        class Registry:
            async def execute(self, action, _adapter):
                calls.append(action.action_type)
                return ActionResult(ActionStatus.FINISHED, "ok")

        nodes = (
            {"kind": "wait", "action_type": "seerWait", "parameters": {"seconds": 0}, "delay_sec": 0},
            {"kind": "set_do", "action_type": "seerSetDO", "parameters": {"id": 1, "status": "on"}, "delay_sec": 0},
            {"kind": "wait", "action_type": "seerWait", "parameters": {"seconds": 0}, "delay_sec": 0},
        )
        adapter = SimpleNamespace(
            _action_registry=Registry(),
            _set_active_action_step=lambda *_args: None,
        )
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="start-from", action_type="startRecipe"),
            adapter=adapter,
            params={"_seer_start_trace_id": "r.2"},
        )
        result = await make_program_handler(encode_program(nodes), recipe_name="startRecipe")(context)
        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(calls, ["seerSetDO", "seerWait"])

    async def test_link_recovery_finishes_current_block_then_stops_program(self):
        calls = []

        class Vehicle:
            def __init__(self):
                self.reconnects = 0

            def critical_reconnect_count(self):
                return self.reconnects

        vehicle = Vehicle()

        class Registry:
            async def execute(self, action, _adapter):
                calls.append(action.action_type)
                if len(calls) == 1:
                    vehicle.reconnects += 1
                return ActionResult(ActionStatus.FINISHED, "ok")

        nodes = (
            {"kind": "path_nav", "action_type": "seerPathNav", "parameters": {"id": "LM1"}, "delay_sec": 0},
            {"kind": "set_do", "action_type": "seerSetDO", "parameters": {"id": 1, "status": "on"}, "delay_sec": 0},
        )
        adapter = SimpleNamespace(
            _vehicle=vehicle,
            _action_registry=Registry(),
            _set_active_action_step=lambda *_args: None,
        )
        context = SimpleNamespace(
            action=SimpleNamespace(action_id="link-stop", action_type="linkRecipe"),
            adapter=adapter,
            params={},
        )
        executor = BlockProgramExecutor(context, nodes, recipe_name="linkRecipe")
        result = await executor.run()
        self.assertEqual(result.status, ActionStatus.FINISHED)
        self.assertEqual(calls, ["seerPathNav"])
        self.assertTrue(executor.stop_after_current)
        self.assertIn("현재 블록 완료 후", result.description)
