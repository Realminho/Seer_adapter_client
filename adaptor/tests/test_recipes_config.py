import unittest
from pathlib import Path

import pytest

from config.recipes import RecipesError, load_recipes, recipe_variables
from config.config import RecipeConfig, RecipeStep, get_config
from core.action_modules import first_party_action_specs


def test_missing_default_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("config.recipes.DEFAULT_RECIPES_PATH", tmp_path / "missing.hcl")
    assert load_recipes() == []


def test_missing_explicit_path_fails(tmp_path):
    """기본 경로 부재와 명시 경로 부재는 다르게 다뤄야 한다.

    recipes.hcl을 안 만든 것은 의도일 수 있지만, robots.hcl에
    recipes = "..." 라고 적어 놓고 그 파일이 없는 것은 오타나 배포 누락이다.
    조용히 recipe 0개로 뜨면 ACS가 부르는 액션이 통째로 사라진다.
    """
    with pytest.raises(RecipesError):
        load_recipes(tmp_path / "missing.hcl")


def test_loads_ordered_steps_and_cleanup(tmp_path: Path):
    path = tmp_path / "recipes.hcl"
    path.write_text(
        '''recipe "trip" {
          timeout_sec = 30
          step "init" {}
          step "write" { parameters = { index = var.pin } retry = 1 }
          cleanup "disconnect" { timeout_sec = 2 }
        }''',
        encoding="utf-8",
    )
    recipe = load_recipes(path)[0]
    assert [step["extension"] for step in recipe["steps"]] == ["init", "write"]
    assert recipe["steps"][1]["parameters"]["index"] == "${var.pin}"
    assert recipe["cleanup"][0]["timeout_sec"] == 2


def test_step_delay_sec_is_parsed(tmp_path: Path):
    """momentary 버튼 pulse 폭처럼 "이 step 뒤에 쉬어라"를 선언으로 적는다."""
    path = tmp_path / "recipes.hcl"
    path.write_text(
        '''recipe "pulse" {
          step "write" { parameters = { state = "on" } delay_sec = 0.2 }
          cleanup "off" { delay_sec = 0.5 }
        }''',
        encoding="utf-8",
    )
    recipe = load_recipes(path)[0]
    assert recipe["steps"][0]["delay_sec"] == 0.2
    assert recipe["cleanup"][0]["delay_sec"] == 0.5


def test_step_delay_sec_defaults_to_no_wait(tmp_path: Path):
    path = tmp_path / "recipes.hcl"
    path.write_text('recipe "x" { step "a" {} }', encoding="utf-8")
    assert load_recipes(path)[0]["steps"][0]["delay_sec"] == 0.0


@pytest.mark.parametrize(
    "body, message",
    [
        ('recipe "x" {}', "at least one step"),
        ('recipe "x" { step "a" { retry = -1 } }', "retry"),
        (
            'recipe "x" { step "a" { delay_sec = -1 } }',
            "delay_sec must be a non-negative number",
        ),
        (
            'recipe "x" { step "a" { delay_sec = "soon" } }',
            "delay_sec must be a non-negative number",
        ),
        ('recipe "x" { step "a" {} } recipe "x" { step "b" {} }', "duplicate"),
    ],
)
def test_invalid_recipe_rejected(tmp_path, body, message):
    path = tmp_path / "recipes.hcl"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(RecipesError, match=message):
        load_recipes(path)


def test_shipped_example_loads_and_all_references_resolve():
    path = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl.example"
    recipes = load_recipes(path)
    assert [recipe["action_type"] for recipe in recipes] == [
        "airShowerPassage",
        "elevatorTrip",
    ]
    config = get_config(recipes_path=path)
    specs = first_party_action_specs(config)
    by_type = {spec.action_type: spec for spec in specs}
    assert by_type["airShowerPassage"].motion is True
    assert by_type["elevatorTrip"].timeout_sec == 0


def test_shipped_recipes_use_site_pins_and_resolve():
    path = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl"
    config = get_config(recipes_path=path)
    by_name = {recipe.action_type: recipe for recipe in config.recipes}
    assert set(by_name) == {
        # 문 recipe는 pairing할 station이 층마다 달라 층별로 나뉜다.
        "pioElevatorOpen1f",
        "pioElevatorOpen2f",
        "pioElevatorClose1f",
        "pioElevatorClose2f",
        # 층 호출은 출발층-도착층 2x2다. 앞이 station, 뒤가 누를 out.
        "pioElevatorMove1f-1f",
        "pioElevatorMove1f-2f",
        "pioElevatorMove2f-1f",
        "pioElevatorMove2f-2f",
        # 상태 머신 한 단계씩만 돌리는 단위 시험용. elevatorUp/Down은 주행이
        # 묶여 있어 "열고/타고/닫고"를 따로 확인할 수 없다.
        "elevatorStepEnter",
        "elevatorStepInside",
        "elevatorStepPassed",
        "airShowerPassage",
        "elevatorUp",
        "elevatorDown",
        # 주행을 FMS에 맡기고 PIO만 담당하는 통과 4단계 x 방향 2개. 깜박임(Pass*)은
        # blockingType NONE으로, 문 열기(Open*)는 HARD로 걸린다.
        "airShower3l-4lOpenIn",
        "airShower3l-4lPassIn",
        "airShower3l-4lOpenOut",
        "airShower3l-4lPassOut",
        "airShower4l-3lOpenIn",
        "airShower4l-3lPassIn",
        "airShower4l-3lOpenOut",
        "airShower4l-3lPassOut",
        "airShowerRelease",
        "airShower3l-4l",
        "localization1f",
        "localization2f",
    }

    # 단위 recipe는 어느 층에서 시험하느냐에 따라 station이 달라지므로
    # 리터럴로 고정하지 않고 액션 파라미터로 받는다.
    for name in ("elevatorStepEnter", "elevatorStepInside", "elevatorStepPassed"):
        unit = by_name[name]
        assert len(unit.steps) == 1
        assert unit.steps[0].parameters == {"station": "${var.station}"}
        assert unit.motion is False
        # 실패해도 설비를 쥔 채 끝나지 않는다
        assert [c.extension for c in unit.cleanup] == ["pioDisconnect"]

    # 문 recipe의 pioInit은 BC 상대(station+channel)를 리터럴로 들고 간다. 비우면
    # extension "pio" 값으로 폴백해 이 현장에 없는 설비를 부른다.
    for name, station in (
        ("pioElevatorOpen1f", "000010"),
        ("pioElevatorOpen2f", "000020"),
        ("pioElevatorClose1f", "000010"),
        ("pioElevatorClose2f", "000020"),
    ):
        door = by_name[name]
        assert door.steps[0].extension == "pioInit"
        # 앞의 0이 살아 있어야 한다 — BC 페이로드에 문자열 그대로 들어간다.
        assert door.steps[0].parameters == {"stationId": station, "channel": 250}
        # channel은 이제 설비가 소유한다. recipe가 적은 값과 어긋나면 recipe는
        # 옛 채널로 BC를 보내고 조용히 성공한 척한다.
        assert door.steps[0].parameters["channel"] == config.elevator_config.channel

    air = by_name["airShowerPassage"]
    assert air.steps[0].parameters == {"doorPin": 0}
    assert air.steps[2].parameters == {"doorPin": 1}

    # floorPin과 pioStationId를 따로 적으면 짝이 어긋날 수 있다. station 하나로
    # 고르면 extension이 elevator_motion_rules에서 floor pin을 끌어온다.
    up = by_name["elevatorUp"]
    assert up.steps[0].parameters == {"station": "000010"}
    assert up.steps[2].parameters == {"station": "000020"}

    down = by_name["elevatorDown"]
    assert down.steps[0].parameters["station"] == "000020"
    assert down.steps[2].parameters["station"] == "000010"

    # 레시피가 쓰는 station이 실제로 풀려야 한다.
    from extensions.facility import resolve_station

    for recipe in (up, down):
        for step in recipe.steps:
            station = step.parameters.get("station")
            if station:
                resolve_station(config, station)

    specs = first_party_action_specs(config)
    registered = {spec.action_type for spec in specs}
    assert set(by_name) <= registered


def test_shipped_door_recipes_hold_the_button_for_the_elevator_pulse_width():
    """문 버튼은 momentary다. delay가 없으면 눌림 유지 시간이 통신 왕복 시간으로
    줄어 설비가 인식하지 못한다. 현장 값은 pulse 1초, 그 뒤 1초다.

    utils/elevator.py:481-490의 상태 머신은 같은 자리에서 0.2초 pulse에
    door_open_close_timing_second(15초) 대기를 쓴다. 두 경로가 다른 값을 내고
    있으므로, 현장에서 한쪽을 조정하면 다른 쪽도 같이 본다.
    """
    path = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl"
    by_name = {
        recipe.action_type: recipe
        for recipe in get_config(recipes_path=path).recipes
    }
    # 닫기는 아직 step 두 개짜리 눈감은 pulse다.
    for name in ("pioElevatorClose1f", "pioElevatorClose2f"):
        press, release = by_name[name].steps[1], by_name[name].steps[2]
        assert press.parameters["state"] == "on"
        assert press.delay_sec == 1
        assert release.parameters["state"] == "off"
        assert release.delay_sec == 1

    # 열기는 pioScenario 하나 안에서 갈래마다 pulse를 만든다. 유지 시간은
    # step의 delay_sec가 아니라 갈래 안의 delay 단계다 — 값이 0이면 눌림이
    # 통신 왕복 시간으로 줄어 설비가 요청으로 읽지 못한다.
    for name in ("pioElevatorOpen1f", "pioElevatorOpen2f"):
        branch = by_name[name].steps[0].parameters["scenario"][0]
        assert branch["type"] == "if"
        for taken in ("then", "otherwise"):
            steps = branch[taken]
            assert [step["state"] for step in steps if "state" in step] == ["on", "off"]
            holds = [step["sec"] for step in steps if step["type"] == "delay"]
            assert holds and all(sec >= 1 for sec in holds), (name, taken)


def test_shipped_elevator_move_recipes_use_fixed_floor_outputs():
    """층 호출 recipe가 station(출발층)과 신호 이름을 자체로 고정한다.

    이름은 출발층-도착층이고, station은 **앞**(로봇이 서 있는 층), 신호 이름은
    elevator{출발}_{도착}이다. 둘을 바꿔 쓰면 recipe는 엉뚱한 버튼을 누르고도
    조용히 FINISHED로 끝나므로 여기서 짝을 고정한다.

    out 번호를 여기에 적지 않는 것은 그것이 station마다 다르기 때문이다 — 같은
    네 가닥이 1층에서는 out1/out2가 층 호출이고 상층에서는 out3/out4다. 번호는
    extension "pio"의 output_signals 한 곳에서만 풀고, 여기서는 이름만 본다.
    """
    path = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl"
    config = get_config(recipes_path=path)
    by_name = {item.action_type: item for item in config.recipes}

    for name, station in (
        ("pioElevatorMove1f-1f", "000010"),
        ("pioElevatorMove1f-2f", "000010"),
        ("pioElevatorMove2f-1f", "000020"),
        ("pioElevatorMove2f-2f", "000020"),
    ):
        source, target = name.removeprefix("pioElevatorMove").split("-")
        signal = f"elevator{source}_{target}"
        recipe = by_name[name]
        assert recipe.steps[0].extension == "pioInit"
        assert recipe.steps[0].parameters == {"stationId": station, "channel": 250}
        press, release = recipe.steps[1], recipe.steps[2]
        assert press.parameters == {"signal": signal, "state": "on"}
        assert release.parameters == {"signal": signal, "state": "off"}
        # 층 요청은 문 버튼보다 훨씬 오래 누른다(현장 값 10초). 1f-1f만 11초인데
        # 이는 현장 값이며 다른 셋과 다르다.
        assert press.delay_sec >= 10
        assert release.delay_sec == 1
        assert recipe.cleanup[0].parameters == {"signal": signal, "state": "off"}
        assert recipe_variables(recipe) == ()

    # 한 station에 붙은 네 가닥(문 2 + 층 호출 2)은 서로 다른 out이어야 한다.
    # output_signals에서 한 칸 밀리면 문 열기가 층 호출을 누르는데, 실행은 조용히
    # 성공하므로 사람이 눈으로 잡을 수 없다.
    signals = config.pio_config.output_signals
    for floor in ("1f", "2f"):
        names = (
            f"elevator{floor}Open",
            f"elevator{floor}Close",
            f"elevator{floor}_1f",
            f"elevator{floor}_2f",
        )
        outs = [signals[n] for n in names]
        assert len(set(outs)) == len(outs), (
            f"{floor} station의 신호가 같은 out을 가리킨다: "
            f"{dict(zip(names, outs))}"
        )


def _scenario_steps(recipe, kind):
    """recipe의 단일 pioScenario step에서 주어진 type의 단계만 뽑는다."""
    assert [step.extension for step in recipe.steps] == ["pioScenario"]
    scenario = recipe.steps[0].parameters["scenario"]
    return [step for step in scenario if step["type"] == kind]


def test_shipped_air_shower_pass_recipes_always_stop_by_themselves():
    """깜박임 recipe는 반드시 스스로 끝나야 한다.

    FMS는 이 recipe를 blockingType NONE으로 걸고 그 사이에 주행을 시킨다. 도착해도
    돌고 있는 액션은 취소되지 않고, 다음 HARD 액션이 이 배경 task가 끝나기를 기다린다
    (adapter_jibot.py _execute_order_action_sequence). 종료 조건이 빠지면 오더가
    그 자리에서 멈추는데, 증상이 "실패"가 아니라 "안 움직임"이라 잡기 어렵다.
    """
    path = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl"
    config = get_config(recipes_path=path)
    by_name = {recipe.action_type: recipe for recipe in config.recipes}

    for name in (
        "airShower3l-4lPassIn",
        "airShower3l-4lPassOut",
        "airShower4l-3lPassIn",
        "airShower4l-3lPassOut",
    ):
        blinks = _scenario_steps(by_name[name], "blink")
        assert len(blinks) == 1, name
        blink = blinks[0]
        assert blink.get("untilIndex") or blink.get("count"), (
            f"{name}의 blink에 untilIndex도 count도 없다 — 끝나지 않는 액션이다"
        )
        # until 핀이 엉뚱한 곳을 가리켜도 끝나도록 상한을 함께 둔다.
        assert blink["count"] > 0, name
        assert blink["timeoutSec"] > 0, name


def test_shipped_air_shower_recipes_press_the_door_of_the_side_they_name():
    """이름의 층과 누르는 문이 어긋나면 반대쪽 문을 누르고 FINISHED로 끝난다.

    signal 이름 -> output_signals -> EZI IO 핀까지 풀어서 extension "airshower"의
    door_pin과 맞춰 본다. 두 곳이 다른 점을 가리키면 recipe는 성공으로 보고하면서
    로봇 앞의 문은 열리지 않는다.
    """
    from types import SimpleNamespace

    from extensions.pio import pio_output_pin, resolve_pio_output_index

    path = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl"
    config = get_config(recipes_path=path)
    by_name = {recipe.action_type: recipe for recipe in config.recipes}
    adapter = SimpleNamespace(config=config)
    entry_pin, exit_pin = config.air_shower_config.door_pin

    def resolved(signal):
        return pio_output_pin(adapter, resolve_pio_output_index(adapter, {"signal": signal}))

    assert resolved("airShower3lOpen") == entry_pin
    assert resolved("airShower4lOpen") == exit_pin

    # 들어갈 때 누르는 문과 나갈 때 누르는 문, 그리고 그 문의 열림 확인 입력.
    for name, signal, sensor_index in (
        ("airShower3l-4lOpenIn", "airShower3lOpen", 1),
        ("airShower3l-4lPassIn", "airShower3lOpen", None),
        ("airShower3l-4lOpenOut", "airShower4lOpen", 2),
        ("airShower3l-4lPassOut", "airShower4lOpen", None),
        ("airShower4l-3lOpenIn", "airShower4lOpen", 2),
        ("airShower4l-3lPassIn", "airShower4lOpen", None),
        ("airShower4l-3lOpenOut", "airShower3lOpen", 1),
        ("airShower4l-3lPassOut", "airShower3lOpen", None),
    ):
        recipe = by_name[name]
        assert recipe.motion is False, f"{name}은 주행을 하지 않는다 — 주행은 FMS 몫이다"
        scenario = recipe.steps[0].parameters["scenario"]
        assert {step["signal"] for step in scenario if "signal" in step} == {signal}, name
        if sensor_index is not None:
            waits = _scenario_steps(recipe, "in")
            assert [step["index"] for step in waits] == [sensor_index], name
        # momentary 버튼을 누른 채로 두지 않는다.
        assert recipe.cleanup[0].parameters == {"signal": signal, "state": "off"}, name


def test_shipped_air_shower_recipes_pair_once_and_release_at_the_end():
    """pairing은 첫 recipe만 걸고 마지막만 놓는다.

    매번 다시 걸면 SELECT를 올렸다 내리고 BC를 새로 보내는 데 0.7~2.7초가 들고,
    그동안 문을 잡아 두는 깜박임이 끊긴다. 반대로 중간에서 놓으면 다음 recipe가
    붙어 있지 않은 설비에 신호를 보낸다.
    """
    path = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl"
    config = get_config(recipes_path=path)
    by_name = {recipe.action_type: recipe for recipe in config.recipes}
    station = config.air_shower_config.pio_station_id

    for direction in ("airShower3l-4l", "airShower4l-3l"):
        first = by_name[f"{direction}OpenIn"].steps[0].parameters
        # 앞의 0이 살아 있어야 한다 — BC 페이로드에 문자열 그대로 들어간다.
        assert first["stationId"] == station
        assert first["channel"] == config.air_shower_config.channel
        assert "pair" not in first

        for stage in ("PassIn", "OpenOut", "PassOut"):
            following = by_name[f"{direction}{stage}"].steps[0].parameters
            assert following["pair"] is False, f"{direction}{stage}가 pairing을 다시 건다"

        for stage in ("OpenIn", "PassIn", "OpenOut"):
            recipe = by_name[f"{direction}{stage}"]
            assert recipe.steps[0].parameters["disconnect"] is False
            assert "pioDisconnect" not in [c.extension for c in recipe.cleanup], (
                f"{direction}{stage}가 절차 도중에 pairing을 놓는다"
            )

        last = by_name[f"{direction}PassOut"]
        assert [c.extension for c in last.cleanup][-1] == "pioDisconnect"

    # 절차가 깨져 pairing이 남았을 때의 복구 경로.
    release = by_name["airShowerRelease"]
    assert [step.extension for step in release.steps] == [
        "pioWriteOut",
        "pioWriteOut",
        "pioDisconnect",
    ]


class RecipeVariablesTest(unittest.TestCase):
    """recipe는 파라미터 선언부가 없고 step 안에서 ${var.X}로 참조만 한다.

    WebUI 실행 폼이 입력칸을 만들려면 정의에서 이름을 되짚어야 한다.
    """

    def _recipe(self, steps, cleanup=()):
        return RecipeConfig(
            action_type="R",
            steps=[RecipeStep(**s) for s in steps],
            cleanup=[RecipeStep(**c) for c in cleanup],
        )

    def test_collects_whole_string_references(self):
        recipe = self._recipe(
            [{"extension": "a", "parameters": {"index": "${var.doorPin}"}}]
        )
        self.assertEqual(recipe_variables(recipe), ("doorPin",))

    def test_collects_embedded_references(self):
        """실행기가 문자열 중간 삽입도 치환하므로 여기서도 잡아야 한다."""
        recipe = self._recipe(
            [{"extension": "a", "parameters": {"name": "dock-${var.slot}-x"}}]
        )
        self.assertEqual(recipe_variables(recipe), ("slot",))

    def test_walks_nested_containers(self):
        recipe = self._recipe(
            [
                {
                    "extension": "a",
                    "parameters": {
                        "scenario": [{"index": "${var.pin}", "state": "on"}],
                        "nested": {"deep": "${var.floor}"},
                    },
                }
            ]
        )
        self.assertEqual(recipe_variables(recipe), ("pin", "floor"))

    def test_includes_cleanup_and_preserves_order_without_duplicates(self):
        recipe = self._recipe(
            [
                {"extension": "a", "parameters": {"x": "${var.first}"}},
                {"extension": "b", "parameters": {"y": "${var.second}", "z": "${var.first}"}},
            ],
            [{"extension": "c", "parameters": {"w": "${var.third}"}}],
        )
        self.assertEqual(recipe_variables(recipe), ("first", "second", "third"))

    def test_recipe_without_variables(self):
        recipe = self._recipe([{"extension": "a", "parameters": {"index": 3}}])
        self.assertEqual(recipe_variables(recipe), ())
