"""Hardware-free acceptance tests for shipped site recipe examples."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from config.config import get_config
from core.action_registry import ActionRegistry, ActionResult, ActionSpec
from extensions.pio import pio_output_pin, resolve_pio_output_index
from extensions.recipes import action_specs as recipe_action_specs
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl.example"
SHIPPED = Path(__file__).resolve().parents[1] / "config" / "recipes.hcl"


def _action(action_type, **params):
    return SimpleNamespace(
        action_id="acceptance",
        action_type=action_type,
        _owner_order_id="order-acceptance",
        action_parameters=[
            SimpleNamespace(key=key, value=value) for key, value in params.items()
        ],
    )


def _registry(config, calls, *, fail_at=None):
    action_types = {
        step.extension
        for recipe in config.recipes
        for step in (*recipe.steps, *recipe.cleanup)
    }

    async def record(ctx):
        calls.append(
            (
                ctx.action.action_type,
                ctx.params,
                getattr(ctx.action, "_owner_order_id", None),
            )
        )
        if ctx.action.action_type == fail_at:
            return ActionResult(ActionStatus.FAILED, "acceptance fault")
        return ActionResult(ActionStatus.FINISHED, "ok")

    extensions = tuple(
        ActionSpec(action_type, handler=record)
        for action_type in sorted(action_types)
    )
    recipes = recipe_action_specs(config.recipes, extensions)
    registry = ActionRegistry((*extensions, *recipes))
    return registry, SimpleNamespace(_action_registry=registry)


def test_elevator_example_runs_hcl_steps_in_order_with_typed_parameters():
    config = get_config(recipes_path=EXAMPLE)
    calls = []
    registry, adapter = _registry(config, calls)
    result = asyncio.run(
        registry.execute(
            _action(
                "elevatorTrip",
                sourceFloorPin=1,
                sourcePioStationId="000010",
                targetFloorPin=2,
                targetPioStationId="000020",
                targetMapId="floor-2",
                enterDistanceMm=1250.5,
                exitDistanceMm=900,
                moveSpeed=180,
            ),
            adapter,
        )
    )
    assert result.status == ActionStatus.FINISHED
    assert [kind for kind, _params, _owner in calls] == [
        "elevatorEnter",
        "manualMove",
        "elevatorInside",
        "switchMap",
        "manualMove",
        "elevatorPassed",
        "manualStop",
        "pioDisconnect",
    ]
    assert calls[0][1] == {"floorPin": 1, "pioStationId": "000010"}
    assert calls[1][1]["distance"] == 1250.5
    assert calls[3][1] == {"mapId": "floor-2"}
    assert {owner for _kind, _params, owner in calls} == {"order-acceptance"}


def test_air_shower_example_fails_fast_but_always_cleans_up():
    config = get_config(recipes_path=EXAMPLE)
    calls = []
    registry, adapter = _registry(config, calls, fail_at="airShowerInside")
    result = asyncio.run(
        registry.execute(
            _action(
                "airShowerPassage",
                entryDoorPin=0,
                exitDoorPin=1,
                enterDistanceMm=1000,
                exitDistanceMm=1000,
                moveSpeed=150,
            ),
            adapter,
        )
    )
    assert result.status == ActionStatus.FAILED
    assert "acceptance fault" in result.description
    assert [kind for kind, _params, _owner in calls] == [
        "airShowerEnter",
        "manualMove",
        "airShowerInside",
        "manualStop",
        "pioDisconnect",
    ]


def _resolved_door_pin(config, signal):
    """signal 이름 -> PIO out 번호 -> EZI IO 출력 핀. recipe가 의지하는 사슬 전체.

    어댑터가 실제로 쓰는 resolver를 그대로 부른다. 여기서 산술을 다시 적으면
    output_pin_map/output_pins 중 어느 쪽이 이기는지 같은 규칙이 갈라져, 테스트는
    통과하면서 현장에서는 다른 점을 친다.
    """
    adapter = SimpleNamespace(config=config)
    index = resolve_pio_output_index(adapter, {"signal": signal})
    return pio_output_pin(adapter, index)


def _pressed_signal(calls, slice_):
    """The single signal name the recorded pioWriteOut calls all press."""
    signals = {params["signal"] for _kind, params, _owner in calls[slice_]}
    assert len(signals) == 1, f"expected one signal, got {sorted(signals)}"
    return signals.pop()


def test_shipped_elevator_door_recipes_pulse_the_configured_pio_signal():
    """Open/close press their own signal on->off, and release it in cleanup.

    The steps are pioWriteOut, not ezioWriteOut: the elevator sees a PIO signal
    and the point is driven through EZI IO internally (extensions/pio
    pio_write_output -> ezi_io.turn_on_output). recipes.hcl carries only the
    signal *name*, so this walks the resolution chain — name ->
    output_signals -> output_pins -> EZI IO pin. The legacy extension
    "elevator" workflow is reference-only and is not a recipe configuration.

    The recipe name carries the floor the robot stands on, so pioInit must pair
    with that floor's station instead of falling back to extension "pio".

    Door signals are per-floor because the same four lines mean different things
    depending on the station they are paired with: at 1F out1/out2 are the floor
    calls and out3/out4 the doors, at the upper station it is the other way
    round. The legacy extension "elevator" block describes the 1F assignment
    only, so it can cross-check the 1f signals and nothing more.
    """
    config = get_config(recipes_path=SHIPPED)
    door_pins = {
        "elevator1fOpen": config.elevator_config.open_door_pin,
        "elevator1fClose": config.elevator_config.close_door_pin,
    }
    rule_stations = {
        str(rule.pio_station_id)
        for rule in config.elevator_config.elevator_motion_rules
    }
    # 문 열기는 센서 판정이 붙어 pioScenario 하나로 바뀌었다(아래 별도 테스트).
    # 여기 남은 것은 아직 눈감은 pulse인 닫기다.
    for action_type, signal_name, station_id in (
        ("pioElevatorClose1f", "elevator1fClose", "000010"),
        ("pioElevatorClose2f", "elevator2fClose", "000020"),
    ):
        calls = []
        registry, adapter = _registry(config, calls)
        result = asyncio.run(registry.execute(_action(action_type), adapter))

        assert result.status == ActionStatus.FINISHED
        assert [kind for kind, _params, _owner in calls] == [
            "pioInit",
            "pioWriteOut",
            "pioWriteOut",
            "pioWriteOut",
            "pioDisconnect",
        ]
        # pioInit이 BC 상대를 들고 가야 그 층 설비와 pairing한다. 비우면 조용히
        # extension "pio"의 station_id(예시값)로 폴백해 엉뚱한 설비를 부른다 —
        # 눌러야 할 버튼은 맞는데 아무 문도 열리지 않는 형태로 어긋난다.
        assert calls[0][1] == {
            "stationId": station_id,
            "channel": config.elevator_config.channel,
        }
        # 앞의 0이 사라지면 BC 페이로드가 달라진다(payload는 문자열 보간이다).
        assert station_id in rule_stations, (
            f"{action_type}의 station {station_id}이 extension \"elevator\" "
            f"motion_rules의 station {sorted(rule_stations)}에 없다"
        )
        signal = _pressed_signal(calls, slice(1, 4))
        assert signal == signal_name
        # 이름이 정말 그 문의 버튼에 닿는지가 이 테스트의 알맹이다. out 번호는
        # 1-based, EZI IO 핀은 0-based라 한 칸 밀리기 쉽고, 밀려도 recipe는
        # 성공으로 끝난다 — 문 열기가 닫힘 버튼을 눌러도 FINISHED다. 현장 배선이
        # 정말 다르면 output_signals와 extension "elevator"의 door pin을 함께
        # 고쳐야 하고, 그때 이 단정도 같이 갱신한다.
        #
        # 대조할 두 번째 출처가 있는 것은 1f뿐이다 — extension "elevator"는 1층
        # station의 배선만 담고 있다. 상층은 열기/닫기가 서로 다른 점을 치는지만
        # 본다(둘이 같은 점이면 한쪽이 반대 버튼을 누르고 있는 것이다).
        if signal in door_pins:
            assert _resolved_door_pin(config, signal) == door_pins[signal], (
                f"{signal}이 EZI IO out{_resolved_door_pin(config, signal)}을 치는데 "
                f"extension \"elevator\"의 문 핀은 out{door_pins[signal]}이다"
            )
        else:
            floor = signal_name.removeprefix("elevator")[:2]
            assert _resolved_door_pin(config, f"elevator{floor}Open") != (
                _resolved_door_pin(config, f"elevator{floor}Close")
            ), f"{floor}의 열기와 닫기가 같은 점을 친다"
        assert [params["state"] for _kind, params, _owner in calls[1:4]] == [
            "on",
            "off",
            "off",
        ]


def test_shipped_elevator_door_recipes_release_the_button_when_the_body_fails():
    """A failed press must not leave the momentary button latched on."""
    config = get_config(recipes_path=SHIPPED)
    calls = []
    registry, adapter = _registry(config, calls, fail_at="pioWriteOut")
    result = asyncio.run(registry.execute(_action("pioElevatorClose1f"), adapter))

    assert result.status == ActionStatus.FAILED
    assert [kind for kind, _params, _owner in calls] == [
        "pioInit",
        "pioWriteOut",
        "pioWriteOut",
        "pioDisconnect",
    ]
    release = calls[-2][1]
    assert release["state"] == "off"
    assert release["signal"] == "elevator1fClose"


def test_shipped_elevator_open_recipes_branch_on_the_car_position_sensor():
    """열기 recipe는 조건 입력을 보고 갈래를 고르고 열림 입력으로 끝난다.

    여기서 보는 것은 recipes.hcl에 적힌 scenario의 모양이다 — 어느 신호를 조건으로
    읽는지, 두 갈래가 각각 무엇을 누르는지, 마지막에 열림 입력을 기다리는지.
    갈래 실행 자체는 extensions/pio 단위 테스트가 본다.
    """
    config = get_config(recipes_path=SHIPPED)
    for action_type, station_id, condition, call_signal, door_signal, opened in (
        (
            "pioElevatorOpen1f", "000010",
            "elevator1fCarUpper", "elevator1f_1f", "elevator1fOpen", "elevator1fOpened",
        ),
        (
            "pioElevatorOpen2f", "000020",
            "elevator2fCarLower", "elevator2f_2f", "elevator2fOpen", "elevator2fOpened",
        ),
    ):
        calls = []
        registry, adapter = _registry(config, calls)
        result = asyncio.run(registry.execute(_action(action_type), adapter))

        assert result.status == ActionStatus.FINISHED
        # 본문은 pioScenario 하나다. 분기와 입력 대기가 한 pairing 안에 있어야 한다.
        assert [kind for kind, _params, _owner in calls] == [
            "pioScenario",
            "pioWriteOut",
            "pioWriteOut",
            "pioDisconnect",
        ]
        params = calls[0][1]
        assert params["stationId"] == station_id
        assert params["channel"] == config.elevator_config.channel

        branch, wait = params["scenario"]
        assert branch["type"] == "if"
        assert branch["signal"] == condition
        # 카가 다른 층이면 부르고, 여기 있으면 문만 연다. 두 갈래가 바뀌면 이미
        # 서 있는 층으로 카를 다시 불러 아무 일도 일어나지 않는다.
        assert {step["signal"] for step in branch["then"] if "signal" in step} == {
            call_signal
        }
        assert {step["signal"] for step in branch["otherwise"] if "signal" in step} == {
            door_signal
        }
        # 두 갈래 모두 마지막에 출력을 내린다(momentary 버튼).
        for taken in ("then", "otherwise"):
            states = [step["state"] for step in branch[taken] if "state" in step]
            assert states == ["on", "off"], taken

        assert wait["type"] == "in"
        assert wait["signal"] == opened
        assert wait["state"] == "on"

        # cleanup은 분기할 수 없으므로 두 갈래의 출력을 모두 내려야 한다.
        released = {params["signal"]: params["state"] for _k, params, _o in calls[1:3]}
        assert released == {call_signal: "off", door_signal: "off"}
