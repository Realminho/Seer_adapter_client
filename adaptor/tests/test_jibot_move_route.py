import sys
import asyncio
import json
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[2] / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))

from jibot_client import JIBOT


def test_build_move_route_matches_observed_shape():
    route = JIBOT.build_move_route(-2500, 200)
    assert route == {
        "manual_move": {
            "a": {
                "cmd": "move",
                "distance": -2500,
                "speed": 200,
                "flag": 1,
                "io": 1,
                "obs_avoid_dist": 1000,
                "side_avoid_dist": 50,
                "use_io": False,
                "note": 1,
            }
        }
    }


def test_build_move_route_coerces_to_int_and_honors_opts():
    route = JIBOT.build_move_route(
        500.0, 100.0, obs_avoid_dist=800, side_avoid_dist=30, route_name="r", step_key="s"
    )
    step = route["r"]["s"]
    assert step["distance"] == 500 and isinstance(step["distance"], int)
    assert step["speed"] == 100 and isinstance(step["speed"], int)
    assert step["obs_avoid_dist"] == 800
    assert step["side_avoid_dist"] == 30


def test_move_distance_honors_route_step_options():
    j = JIBOT(robot_ip="127.0.0.1")
    calls = []

    async def fake_send(command, gap=-1, **params):
        calls.append((command, params))

    j.send_command = fake_send
    asyncio.run(
        j.move_distance(
            -600,
            150,
            flag=2,
            io=3,
            obs_avoid_dist=900,
            side_avoid_dist=40,
            use_io=True,
            note=7,
            run_mode="scheduler",
        )
    )

    step = calls[0][1]["content"]["a"]
    assert step["flag"] == 2
    assert step["io"] == 3
    assert step["obs_avoid_dist"] == 900
    assert step["side_avoid_dist"] == 40
    assert step["use_io"] is True
    assert step["note"] == 7


def test_move_distance_runs_scheduler_this_by_default():
    j = JIBOT(robot_ip="127.0.0.1")
    calls = []

    async def fake_send(command, gap=-1, **params):
        calls.append((command, params))

    j.send_command = fake_send
    asyncio.run(j.move_distance(-2500, 200, run_mode="scheduler"))

    assert len(calls) == 1
    assert calls[0][0] == "UmSchedulerThis"
    assert calls[0][1]["name"] == "manual_move"
    assert calls[0][1]["key"] == "a"
    assert calls[0][1]["content"] == JIBOT.build_move_route(-2500, 200)["manual_move"]


def test_move_distance_can_use_set_routes_mode():
    j = JIBOT(robot_ip="127.0.0.1")
    calls = []

    async def fake_send(command, gap=-1, **params):
        calls.append((command, params))

    j.send_command = fake_send
    asyncio.run(j.move_distance(-2500, 200, run_mode="set_routes"))

    assert calls[0][0] == "UmSetRoutes"
    assert json.loads(calls[0][1]["routes"]) == JIBOT.build_move_route(-2500, 200)
    assert calls[1][0] == "UmRoutes"
    assert calls[1][1]["routes"] == "manual_move"
    assert calls[1][1]["key"] == "a"


def test_move_distance_uploads_routes_as_json_string():
    j = JIBOT(robot_ip="127.0.0.1")
    commands = []

    async def fake_json_cmd_to_jibot(json_cmd):
        commands.append(json_cmd)

    j.json_cmd_to_jibot = fake_json_cmd_to_jibot
    asyncio.run(j.move_distance(-600, 150, run_mode="set_routes"))

    routes = commands[0]["routes"]
    assert commands[0]["#CMD#"] == "UmSetRoutes"
    assert isinstance(routes, str)
    assert json.loads(routes) == JIBOT.build_move_route(-600, 150)
