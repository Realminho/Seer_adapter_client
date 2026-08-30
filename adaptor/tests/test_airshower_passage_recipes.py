# adaptor/tests/test_airshower_passage_recipes.py
"""에어샤워 통과 4단계 recipe가 방향마다 스스로 성립하는지 보는 구조 검사.

한 방향은 네 개가 한 벌이다:
    OpenIn  -> CloseIn -> OpenOut -> CloseOut
    pairing을 OpenIn이 걸고(pair = true) 넷이 이어 쓰다가 CloseOut이 놓는다
    (disconnect = true). 중간 셋은 pair = false로 그 링크를 빌려 쓴다.

여기서 보는 것은 실행 결과가 아니라 배포 config의 값이다. 아래 어긋남들은 실행
시점에 "되는데 안 되는" 형태로만 나타나서 사람 눈으로는 못 잡는다 — 링크 없이
출력만 나가도 recipe는 FINISHED로 끝나고, 반대쪽 문을 눌러도 FINISHED로 끝난다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import get_config

DIRECTIONS = ("3l-4l", "4l-3l")
STAGES = ("OpenIn", "CloseIn", "OpenOut", "CloseOut")


def _scenarios():
    """{recipe 이름: pioScenario 파라미터} — 배포 config에서 그대로."""
    found = {}
    for recipe in get_config().recipes:
        if not recipe.action_type.startswith("airShower"):
            continue
        for step in recipe.steps:
            if step.extension == "pioScenario":
                found[recipe.action_type] = step.parameters
    return found


def _flag(params, key, default):
    value = params.get(key, default)
    return str(value).strip().lower() in {"1", "true", "on"}


def _door(params):
    """scenario가 건드리는 문 신호 이름. 한 recipe는 문 하나만 건드린다."""
    doors = {
        entry["signal"]
        for entry in params["scenario"]
        if isinstance(entry, dict) and entry.get("type") == "out"
    }
    assert len(doors) == 1, doors
    return doors.pop()


def _confirm(params):
    """문 열림 확인 단계의 입력 신호 이름. 없으면 None."""
    for entry in params["scenario"]:
        if isinstance(entry, dict) and entry.get("type") == "in":
            return entry["signal"]
    return None


class PassageShapeTest(unittest.TestCase):
    def test_both_directions_ship_all_four_stages(self):
        scenarios = _scenarios()
        for direction in DIRECTIONS:
            for stage in STAGES:
                self.assertIn(f"airShower{direction}{stage}", scenarios)


class PairingChainTest(unittest.TestCase):
    """링크를 누가 걸고 누가 놓는지 — 방향마다 정확히 하나씩이어야 한다."""

    def test_exactly_one_stage_opens_the_link_and_one_closes_it(self):
        """둘이 걸면 중간에 SELECT가 다시 토글되고, 아무도 안 놓으면 물린 채 남는다."""
        scenarios = _scenarios()
        for direction in DIRECTIONS:
            stages = {s: scenarios[f"airShower{direction}{s}"] for s in STAGES}
            pairs = [s for s, p in stages.items() if _flag(p, "pair", True)]
            releases = [
                s for s, p in stages.items() if _flag(p, "disconnect", True)
            ]
            self.assertEqual(pairs, ["OpenIn"], f"{direction}: pair = true인 단계")
            self.assertEqual(
                releases, ["CloseOut"], f"{direction}: disconnect = true인 단계"
            )

    def test_every_stage_carries_its_own_bc_address(self):
        """pair를 켜야 할 일이 생겼을 때 한 단어만 고치면 되도록.

        진출 절반만 걸리는 운용에서는 OpenOut이 스스로 걸어야 한다. stationId가
        없으면 pio_link_params가 그 자리에서 raise해서 켤 수조차 없다.
        """
        scenarios = _scenarios()
        for direction in DIRECTIONS:
            for stage in STAGES:
                params = scenarios[f"airShower{direction}{stage}"]
                name = f"airShower{direction}{stage}"
                self.assertIn("stationId", params, name)
                self.assertIn("channel", params, name)


class DoorWiringTest(unittest.TestCase):
    """어느 문을 누르고 어느 입력으로 확인하는지 — 한 칸만 밀려도 FINISHED로 끝난다."""

    def test_each_stage_pairs_its_open_and_close_on_the_same_door(self):
        scenarios = _scenarios()
        for direction in DIRECTIONS:
            def door(stage):
                return _door(scenarios[f"airShower{direction}{stage}"])

            self.assertEqual(door("OpenIn"), door("CloseIn"), direction)
            self.assertEqual(door("OpenOut"), door("CloseOut"), direction)
            self.assertNotEqual(
                door("OpenIn"), door("OpenOut"),
                f"{direction}: 들어간 문으로 다시 나가려 한다",
            )

    def test_the_confirm_input_watches_the_door_that_was_pressed(self):
        """out1을 눌러 놓고 in2를 보면, 문이 안 열려도 열렸다고 읽는다.

        같은 점 번호(out N <-> in N)로 짝지어져 있는지를 config에서 확인한다 —
        번호는 extension "pio"의 output_signals/input_signals에서만 푼다.
        """
        config = get_config()
        outputs = config.pio_config.output_signals
        inputs = config.pio_config.input_signals
        scenarios = _scenarios()

        checked = []
        for direction in DIRECTIONS:
            for stage in ("OpenIn", "OpenOut"):
                name = f"airShower{direction}{stage}"
                params = scenarios[name]
                confirm = _confirm(params)
                self.assertIsNotNone(confirm, f"{name}: 문 열림 확인 단계가 없다")
                checked.append(name)
                self.assertEqual(
                    outputs[_door(params)],
                    inputs[confirm],
                    f"{name}: {_door(params)}를 눌러 놓고 {confirm}을 본다",
                )
        self.assertEqual(len(checked), 4, checked)

    def test_the_close_stages_do_not_wait_on_an_input(self):
        """닫기는 확인할 신호가 없다. 기다리면 60초를 버리고 HARD라 오더가 선다."""
        scenarios = _scenarios()
        for direction in DIRECTIONS:
            for stage in ("CloseIn", "CloseOut"):
                name = f"airShower{direction}{stage}"
                self.assertIsNone(_confirm(scenarios[name]), name)


if __name__ == "__main__":
    unittest.main()
