"""Regression tests for JIBOT.wait_for_response timeout handling.

A missing response must resolve to ``None`` (so callers like get_map /
get_battery_info hit their ``if response is None`` fallback), NOT raise a
TimeoutError. A raised TimeoutError escapes robot_info_loop's narrow
``except (BrokenPipeError, ConnectionError, OSError)`` and kills the polling
task -- the "jibot client errors and terminates" failure seen in simulator mode
when get_map ran against a client that never answers.
"""

import asyncio
import contextlib
import io
import sys
import unittest
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[2] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from jibot_client import JIBOT


class WaitForResponseTimeoutTest(unittest.TestCase):
    def _make_client(self) -> JIBOT:
        return JIBOT("127.0.0.1", 7273)

    def test_returns_none_on_timeout_instead_of_raising(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            # Empty queue: no frame will ever arrive.
            result = await client.wait_for_response(command="UmGetMap", timeout=0.05)
            self.assertIsNone(result)

        asyncio.run(scenario())

    def test_unmatched_response_is_requeued_and_timeout_returns_none(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            client._response_queue.put_nowait({"#CMD#": "Other"})
            result = await client.wait_for_response(command="UmGetMap", timeout=0.05)
            self.assertIsNone(result)
            # The non-matching response must be left in the queue for later.
            self.assertEqual(client._response_queue.get_nowait(), {"#CMD#": "Other"})

        asyncio.run(scenario())

    def test_returns_matching_response(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            client._response_queue.put_nowait({"#CMD#": "UmGetMap", "Objs": {}})
            result = await client.wait_for_response(command="UmGetMap", timeout=1.0)
            self.assertEqual(result, {"#CMD#": "UmGetMap", "Objs": {}})

        asyncio.run(scenario())

    def test_loc_state_updates_cached_station_status_and_pose(self) -> None:
        client = self._make_client()
        payload = (
            '{"#CMD#":"UmGetLocState","mode":"Stop","status":"Stopped",'
            '"station":"F2_90_S2CH","x":14097,"y":3696,"th":-36,"score":350}'
        )

        client.process_data(f"$#{len(payload)}##{payload}$~")

        self.assertEqual(client._mode, "Stop")
        self.assertEqual(client._status, "Stopped")
        self.assertEqual(client._station, "F2_90_S2CH")
        self.assertEqual(client._x, 14097)
        self.assertEqual(client._y, 3696)
        self.assertEqual(client._th, -36)
        self.assertEqual(client._localization_score, 350)

    def test_task_and_path_responses_update_cached_diagnostics(self) -> None:
        client = self._make_client()
        cur_task = (
            '{"#CMD#":"UmGetCurTask","data":{"status":"nrunto F1_60",'
            '"value":{"cmd":"goto","goal":"F1_60","target":"goal"}}}'
        )
        task_info = '{"#CMD#":"UmGetTaskInfo","status":"nrunto F1_60"}'
        path = (
            '{"#CMD#":"UmGetPath","num":2,'
            '"points":[{"x":1,"y":2},{"x":3,"y":4}]}'
        )

        client.process_data(f"$#{len(cur_task)}##{cur_task}$~")
        client.process_data(f"$#{len(task_info)}##{task_info}$~")
        client.process_data(f"$#{len(path)}##{path}$~")

        self.assertEqual(client._cur_task["data"]["value"]["goal"], "F1_60")
        self.assertEqual(client._task_info["status"], "nrunto F1_60")
        self.assertEqual(client._path["num"], 2)
        self.assertEqual(client._path["points"][-1], {"x": 3, "y": 4})

    def test_status_log_names_localization_score_explicitly(self) -> None:
        client = self._make_client()
        payload = (
            '{"#CMD#":"UmGetLocState","mode":"Stop","status":"Stopped",'
            '"station":"F2_90_S2CH","x":14097,"y":3696,"th":-36,"score":350}'
        )
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            client.process_data(f"$#{len(payload)}##{payload}$~")

        log = out.getvalue()
        self.assertIn("localization_score=350", log)
        self.assertNotIn(" loc=350 ", log)

    def test_map_update_preserves_node_categories(self) -> None:
        client = self._make_client()

        client._update_map_nodes(
            {
                "#CMD#": "UmGetMap",
                "Objs": {
                    "Goal": [{"name": "G1", "pose": "1 2 0"}],
                    "Dock": [{"name": "D1", "pose": "3 4 0"}],
                    "PathPoint": [{"name": "p40", "pose": "5 6 90"}],
                },
            }
        )

        self.assertEqual(client._map_nodes["D1"], (3.0, 4.0, 0.0))
        self.assertEqual(client._map_node_categories["G1"], "Goal")
        self.assertEqual(client._map_node_categories["D1"], "Dock")
        self.assertNotIn("p40", client._map_nodes)
        self.assertEqual(client.get_path_point_pose("p40"), (5.0, 6.0, 90.0))
        self.assertIsNone(client.get_path_point_pose("missing"))

    def test_um_goto_pose_preserves_zero_coordinates_and_heading(self) -> None:
        client = self._make_client()

        command = client.build_command(
            "UmGoto",
            target="pose",
            poseX=0,
            poseY=0.0,
            poseTh="0",
            strict=False,
        )

        self.assertEqual(command["target"], "pose")
        self.assertEqual(command["poseX"], 0)
        self.assertEqual(command["poseY"], 0.0)
        self.assertEqual(command["poseTh"], "0")
        self.assertNotIn("strict", command)

    def test_error_frame_returned_when_accept_errors(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            client._response_queue.put_nowait({"#CMD#": "error", "msg": "robot in stop mode"})
            result = await client.wait_for_response(
                command="UmGoto", timeout=0.05, accept_errors=True
            )
            self.assertEqual(result, {"#CMD#": "error", "msg": "robot in stop mode"})

        asyncio.run(scenario())

    def test_error_frame_skipped_and_requeued_by_default(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            client._response_queue.put_nowait({"#CMD#": "error", "msg": "x"})
            result = await client.wait_for_response(command="UmGoto", timeout=0.05)
            self.assertIsNone(result)
            # Default behavior unchanged: the error frame is left for other readers.
            self.assertEqual(
                client._response_queue.get_nowait(), {"#CMD#": "error", "msg": "x"}
            )

        asyncio.run(scenario())

    def test_accept_errors_still_returns_normal_match(self) -> None:
        async def scenario() -> None:
            client = self._make_client()
            client._response_queue.put_nowait({"#CMD#": "UmGoto", "result": "accepted"})
            result = await client.wait_for_response(
                command="UmGoto", timeout=0.05, accept_errors=True
            )
            self.assertEqual(result, {"#CMD#": "UmGoto", "result": "accepted"})

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
