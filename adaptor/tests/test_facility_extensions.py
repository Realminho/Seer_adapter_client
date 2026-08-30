import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from core.action_registry import ActionRegistry
from extensions.facility import action_specs
from protocol.vda_2_0_0.vda5050_2_0_0_state import ActionStatus


def _action(kind, **params):
    return SimpleNamespace(
        action_id="facility-1",
        action_type=kind,
        action_parameters=[SimpleNamespace(key=k, value=v) for k, v in params.items()],
    )


class _Workflow:
    instances = []
    result = (True, SimpleNamespace(value="none"), "")

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    async def workflow_sequence(self):
        return self.result


def _adapter():
    return SimpleNamespace(
        config=SimpleNamespace(
            ezi_config=SimpleNamespace(ezi_io="10.0.0.2"),
            elevator_config=SimpleNamespace(channel=250),
        ),
        _pio_client=object(),
        _pio_client_factory=None,
        _ezi_io=object(),
    )


def test_air_shower_reuses_adapter_owned_clients():
    _Workflow.instances.clear()
    adapter = _adapter()
    registry = ActionRegistry(action_specs())
    with patch("extensions.facility.ASWorkflow", _Workflow):
        result = asyncio.run(
            registry.execute(_action("airShowerEnter", doorPin=1), adapter)
        )
    assert result.status == ActionStatus.FINISHED
    workflow = _Workflow.instances[0]
    assert workflow.args == (1, "ENTER")
    assert workflow.kwargs["pio"] is adapter._pio_client
    assert workflow.kwargs["ezi_io"] is adapter._ezi_io
    assert workflow.kwargs["config_data"] is adapter.config


def test_elevator_maps_workflow_failure_description():
    class Failed(_Workflow):
        result = (False, SimpleNamespace(value="pairing failed"), "no GO")

    Failed.instances.clear()
    adapter = _adapter()
    registry = ActionRegistry(action_specs())
    with patch("extensions.facility.EVWorkflow", Failed):
        result = asyncio.run(
            registry.execute(
                _action("elevatorInside", floorPin=2, pioStationId="000020"),
                adapter,
            )
        )
    assert result.status == ActionStatus.FAILED
    assert result.description == "pairing failed: no GO"
    workflow = Failed.instances[0]
    assert workflow.args == (2, "000020", "INSIDE")


def test_facility_parameters_are_required():
    adapter = _adapter()
    registry = ActionRegistry(action_specs())
    result = asyncio.run(registry.execute(_action("elevatorEnter"), adapter))
    assert result.status == ActionStatus.FAILED
    assert "missing parameter: floorPin" in result.description


# --- station 짝짓기 -------------------------------------------------------

def _cfg_with_rules():
    from types import SimpleNamespace

    rule = lambda pin, sid, mode: SimpleNamespace(
        from_="a", to="b", mode=mode, floor_pin=pin, pio_station_id=sid
    )
    return SimpleNamespace(
        elevator_config=SimpleNamespace(
            channel=250,
            elevator_motion_rules=(
                rule(0, "000010", "enter"),
                rule(1, "000020", "inside"),
                rule(1, "000020", "enter"),
                rule(0, "000010", "passed"),
            )
        ),
        air_shower_config=SimpleNamespace(door_pin=[0, 1]),
    )


def test_elevator_stations_are_derived_from_motion_rules():
    """floorPin과 pioStationId는 짝이 맞아야 하고 그 짝은 config에 있다."""
    from extensions.facility import elevator_stations

    assert elevator_stations(_cfg_with_rules()) == (("000010", 0), ("000020", 1))


def test_elevator_station_parameter_offers_those_choices():
    from extensions.facility import action_specs

    spec = next(
        s for s in action_specs(_cfg_with_rules()) if s.action_type == "elevatorEnter"
    )
    station = next(p for p in spec.parameters if p.name == "station")
    assert station.choices == ("000010", "000020")


def test_air_shower_door_pin_offers_configured_pins():
    from extensions.facility import action_specs

    spec = next(
        s for s in action_specs(_cfg_with_rules()) if s.action_type == "airShowerEnter"
    )
    door = next(p for p in spec.parameters if p.name == "doorPin")
    assert door.choices == ("0", "1")


def test_station_resolves_both_values():
    from extensions.facility import resolve_station

    assert resolve_station(_cfg_with_rules(), "000020") == (1, "000020")


def test_unknown_station_is_rejected():
    from extensions.facility import resolve_station

    try:
        resolve_station(_cfg_with_rules(), "999999")
    except ValueError as exc:
        assert "999999" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_station_mapped_to_two_pins_is_a_config_error():
    """1:1이 아니면 조용히 아무거나 고르면 안 된다 — 잘못된 층으로 간다."""
    from types import SimpleNamespace

    from extensions.facility import elevator_stations

    rule = lambda pin, sid: SimpleNamespace(
        from_="a", to="b", mode="enter", floor_pin=pin, pio_station_id=sid
    )
    cfg = SimpleNamespace(
        elevator_config=SimpleNamespace(
            elevator_motion_rules=(rule(0, "000010"), rule(1, "000010"))
        ),
        air_shower_config=SimpleNamespace(door_pin=[0]),
    )
    try:
        elevator_stations(cfg)
    except ValueError as exc:
        assert "000010" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_legacy_floor_pin_and_station_still_accepted(monkeypatch):
    """현장 ACS가 이미 floorPin/pioStationId로 보내고 있을 수 있다.

    station 하나로 고르는 새 방식을 들이면서 옛 방식을 끊으면 배포된
    통합이 조용히 깨진다.
    """
    import asyncio
    from types import SimpleNamespace

    import extensions.facility as facility

    captured = {}

    class FakeWorkflow:
        def __init__(self, floor_pin, station, action, **kwargs):
            captured["args"] = (floor_pin, station, action)

        async def workflow_sequence(self):
            return True, None, ""

    monkeypatch.setattr(facility, "EVWorkflow", FakeWorkflow)
    monkeypatch.setattr(facility, "get_pio_client", lambda adapter: None)
    monkeypatch.setattr(facility, "_ezi_client", lambda adapter: None)

    ctx = SimpleNamespace(
        params={"floorPin": "1", "pioStationId": "000020"},
        adapter=SimpleNamespace(config=_cfg_with_rules()),
        action=SimpleNamespace(action_type="elevatorEnter"),
    )
    asyncio.run(facility._run_elevator(ctx, "ENTER"))
    assert captured["args"] == (1, "000020", "ENTER")


def test_station_parameter_drives_the_workflow(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import extensions.facility as facility

    captured = {}

    class FakeWorkflow:
        def __init__(self, floor_pin, station, action, **kwargs):
            captured["args"] = (floor_pin, station, action)

        async def workflow_sequence(self):
            return True, None, ""

    monkeypatch.setattr(facility, "EVWorkflow", FakeWorkflow)
    monkeypatch.setattr(facility, "get_pio_client", lambda adapter: None)
    monkeypatch.setattr(facility, "_ezi_client", lambda adapter: None)

    ctx = SimpleNamespace(
        params={"station": "000020"},
        adapter=SimpleNamespace(config=_cfg_with_rules()),
        action=SimpleNamespace(action_type="elevatorEnter"),
    )
    asyncio.run(facility._run_elevator(ctx, "ENTER"))
    # floor pin은 설정에서 끌어온 값이어야 한다.
    assert captured["args"] == (1, "000020", "ENTER")

