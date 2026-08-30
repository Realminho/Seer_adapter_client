import importlib
import sys
import uuid
from types import SimpleNamespace

import core.action_modules as action_modules
from core.action_modules import (
    DiscoveredModule,
    default_module_names,
    discover_action_modules,
)
from core.action_registry import ActionSpec, build_registry_from_config
from core.registry import _JIBOT_INSTANT_ACTIONS
from config.config import get_config


class _Cfg:
    def __init__(self, action_modules=()):
        self.actions = []
        self.action_modules = list(action_modules)


class _Act:
    def __init__(self, action_type, enabled=True):
        self.action_type = action_type
        self.enabled = enabled


def _write_module(tmp_path, monkeypatch, request, source):
    name = f"test_actions_{uuid.uuid4().hex}"
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    importlib.invalidate_caches()
    request.addfinalizer(lambda: sys.modules.pop(name, None))
    return name


def _write_package(tmp_path, monkeypatch, request, source, panel=None):
    name = f"test_actions_{uuid.uuid4().hex}"
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text(source, encoding="utf-8")
    if panel is not None:
        (package / "panel.html").write_text(panel, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    importlib.invalidate_caches()
    request.addfinalizer(lambda: sys.modules.pop(name, None))
    return name


def test_loads_package_panel_exactly(tmp_path, monkeypatch, request):
    panel = "<section>Panel</section>\n"
    name = _write_package(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="panelAction"),)
""",
        panel,
    )

    discovered = discover_action_modules(_Cfg(), default_modules=(name,))

    assert discovered[0].panel_template == panel


def test_package_without_panel_has_no_template(tmp_path, monkeypatch, request):
    name = _write_package(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="noPanelAction"),)
""",
    )

    discovered = discover_action_modules(_Cfg(), default_modules=(name,))

    assert discovered[0].panel_template is None


def test_partial_action_disable_suppresses_existing_panel(
    tmp_path, monkeypatch, request
):
    name = _write_package(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (
        ActionSpec(action_type="keepPanelAction"),
        ActionSpec(action_type="dropPanelAction"),
    )
""",
        "<section>Hidden</section>",
    )
    config = _Cfg()
    config.actions = [_Act("dropPanelAction", enabled=False)]

    discovered = discover_action_modules(config, default_modules=(name,))

    assert discovered[0].panel_suppressed is True
    assert discovered[0].panel_template is None


def test_panel_resource_failure_is_logged_without_skipping_module(
    tmp_path, monkeypatch, request, capsys
):
    name = _write_package(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="brokenPanelAction"),)
""",
    )

    def fail(_module_name):
        raise RuntimeError("resource unavailable")

    monkeypatch.setattr("core.action_modules.resources.files", fail)

    discovered = discover_action_modules(_Cfg(), default_modules=(name,))

    assert tuple(item.module for item in discovered) == (name,)
    assert discovered[0].panel_template is None
    assert (
        f"[ACTION MODULE PANEL SKIP] {name}: resource unavailable"
        in capsys.readouterr().out
    )


def test_discovers_module_and_collects_specs(tmp_path, monkeypatch, request):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

MODULE_TITLE = "Demo actions"

def action_specs():
    return (
        ActionSpec(action_type="demoOne", label="Demo one"),
        ActionSpec(action_type="demoTwo", label="Demo two"),
    )
""",
    )

    discovered = discover_action_modules(_Cfg(), default_modules=(name,))

    assert discovered == (
        DiscoveredModule(
            module=name,
            title="Demo actions",
            specs=(
                ActionSpec(action_type="demoOne", label="Demo one"),
                ActionSpec(action_type="demoTwo", label="Demo two"),
            ),
        ),
    )


def test_default_module_names_are_stable():
    assert default_module_names() == (
        "extensions.clamp",
        "extensions.pio",
        "extensions.ezio",
        "extensions.facility",
    )


def test_default_config_discovers_first_party_action_modules():
    modules = {module.module for module in discover_action_modules(get_config())}

    assert {
        "extensions.clamp",
        "extensions.pio",
        "extensions.ezio",
    } <= modules


def test_config_can_disable_default_pio_action_module():
    config = get_config(
        overrides={
            "action_modules": [
                {"module": "extensions.pio", "enabled": False},
            ]
        }
    )

    modules = {module.module for module in discover_action_modules(config)}

    assert "extensions.pio" not in modules
    assert "extensions.clamp" in modules


def test_first_party_action_specs_flattens_defaults():
    helper = getattr(action_modules, "first_party_action_specs", None)
    assert helper is not None

    specs = helper(_Cfg())
    action_types = tuple(spec.action_type for spec in specs)

    extension_types = tuple(
        spec.action_type
        for module in discover_action_modules(_Cfg())
        for spec in module.specs
    )
    assert action_types == extension_types + (
        "manualMove",
        "jibotMotionRule",
        "gotoNearestNode",
        # Turn in place to an absolute heading — built-in so every site has it
        # without a recipe. Bridged alongside the other motion primitives.
        "rotateTo",
        "manualStop",
        "switchMap",
        "localize",
    )
    assert {"clampOn", "clampMoveTo", "pioInit", "ezioReadIn"} <= set(
        action_types
    )
    registry = build_registry_from_config([], first_party_specs=specs)
    assert registry.has("clampOn")
    assert registry.has("pioInit")


def test_jibot_instant_actions_leave_clamp_types_to_discovery():
    action_types = {action.action_type for action in _JIBOT_INSTANT_ACTIONS}

    assert not {
        "clampOn",
        "clampOff",
        "clamp",
        "unclamp",
        "clampStop",
        "clampMin",
        "clampMax",
        "clampHome",
        "clampMoveTo",
    } & action_types
    assert {"stateRequest", "manualDrive"} <= action_types


def test_real_clamp_package_has_specs_and_panel(monkeypatch):
    from importlib import resources
    from string import Template

    clamp = importlib.import_module("extensions.clamp")
    specs = tuple(clamp.action_specs())
    expected_types = (
        "clamp",
        "unclamp",
        "clampTeach",
        "clampOn",
        "clampOff",
        "clampStop",
        "clampMin",
        "clampMax",
        "clampHome",
        "clampMoveTo",
    )
    panel = resources.files("extensions.clamp").joinpath("panel.html")

    assert clamp.CLAMP_ACTION_TYPES == expected_types
    assert tuple(spec.action_type for spec in specs) == expected_types
    assert all(
        spec.motion and spec.handler is clamp.handle_clamp_action and spec.label
        for spec in specs
    )
    assert clamp.MODULE_TITLE == "Clamp"
    assert panel.is_file()
    assert hasattr(clamp, "execute_clamp_action")

    monkeypatch.delitem(clamp._CLAMP_LABELS, "clampTeach")
    assert clamp.action_specs()[2].label == "clampTeach"

    html = panel.read_text(encoding="utf-8")
    # 패널은 모듈이 선언한 액션을 빠짐없이 담는다. 빠지면 WebUi 가 "Clamp — 그 외"
    # 그룹에 생성 폼으로 내주기는 하지만, 그건 드리프트를 알리는 신호지 정상이 아니다.
    assert all(
        html.count(
            f'<input type="hidden" name="action_type" value="{action_type}">'
        )
        == 1
        for action_type in expected_types
    )
    marker = '<input type="hidden" name="action_type" value="clampMoveTo">'
    start = html.rfind("<form ", 0, html.index(marker))
    clamp_move_form = html[start : html.index("</form>", start)]
    # 입력 칸은 ActionSpec 이 소유한다 — 패널은 자리만 잡는다.
    assert "$field_clampMoveTo_position" in clamp_move_form
    assert '<input type="checkbox" name="confirm" required>' in clamp_move_form
    identifiers = {
        match.group("named") or match.group("braced")
        for match in Template.pattern.finditer(html)
        if match.group("named") or match.group("braced")
    }
    declared_fields = {
        f"field_{spec.action_type}_{parameter.name}"
        for spec in specs
        for parameter in spec.parameters
    }
    assert identifiers == {
        "adapter_key",
        "csrf_token",
        "return_to",
    } | declared_fields


def test_real_pio_package_has_specs_and_panel(monkeypatch):
    import re
    from importlib import resources
    from string import Template

    pio = importlib.import_module("extensions.pio")
    specs = tuple(pio.action_specs())
    expected_types = (
        "pioInit",
        "pioReadIn",
        "pioWriteOut",
        "pioDisconnect",
        "pioScenario",
        "pioPing",
    )
    expected_labels = (
        "PIO init (연결)",
        "PIO read inputs",
        "PIO write output",
        "PIO disconnect",
        "PIO scenario 실행",
        "PIO 연결 확인",
    )
    panel = resources.files("extensions.pio").joinpath("panel.html")

    assert pio.PIO_ACTION_TYPES == expected_types
    assert tuple(spec.action_type for spec in specs) == expected_types
    assert tuple(spec.label for spec in specs) == expected_labels
    assert all(not spec.motion and spec.handler is pio.handle_pio_action for spec in specs)
    assert pio.MODULE_TITLE == "PIO"
    assert panel.is_file()
    assert all(
        hasattr(pio, name)
        for name in (
            "execute_pio_action",
            "is_pio_action",
            "action_specs",
            "summarize_pio_result",
        )
    )

    monkeypatch.delitem(pio._PIO_LABELS, "pioWriteOut")
    assert pio.action_specs()[2].label == "pioWriteOut"

    html = panel.read_text(encoding="utf-8")
    # 순서는 UI 판단이지만, 모듈이 선언한 액션은 하나도 빠지지 않아야 한다.
    panel_types = re.findall(
        r'<input type="hidden" name="action_type" value="([^"]+)">', html
    )
    assert sorted(panel_types) == sorted(expected_types)
    assert len(panel_types) == len(set(panel_types))
    marker = '<input type="hidden" name="action_type" value="pioWriteOut">'
    start = html.rfind("<form ", 0, html.index(marker))
    write_form = html[start : html.index("</form>", start)]
    write_fields = set(
        re.findall(
            r'<(?:input|select|textarea)\b[^>]*\bname="([^"]+)"', write_form
        )
    )
    assert not {"value", "name"} & write_fields
    # index/state/signal 은 패널이 손으로 그리지 않는다. signal 선택지는
    # extensions.hcl 의 output_signals 에서 오므로 패널에 박으면 설정을 고쳐도
    # 따라오지 않는다 — 스키마 필드로 끌어온다.
    assert "$field_pioWriteOut_signal" in write_form
    assert "$field_pioWriteOut_index" in write_form
    assert "$field_pioWriteOut_state" in write_form
    assert "<option" not in write_form
    assert '<input type="checkbox" name="confirm" required>' in write_form
    identifiers = {
        match.group("named") or match.group("braced")
        for match in Template.pattern.finditer(html)
        if match.group("named") or match.group("braced")
    }
    declared_fields = {
        f"field_{spec.action_type}_{parameter.name}"
        for spec in specs
        for parameter in spec.parameters
    }
    assert identifiers == {
        "adapter_key",
        "csrf_token",
        "return_to",
    } | declared_fields


def _pio_parameter_names(action_type: str):
    pio = importlib.import_module("extensions.pio")
    spec = next(s for s in pio.action_specs() if s.action_type == action_type)
    return tuple(parameter.name for parameter in spec.parameters)


def test_pio_init_exposes_the_bc_frame_fields_but_not_the_robot_id():
    """BC 대상은 실행마다 달라 운영자가 넣어야 하고, oht 번호는 로봇 고유값이라
    extensions.hcl의 vehicle_num에서 온다."""
    names = _pio_parameter_names("pioInit")
    assert names == ("media", "stationId", "channel", "port", "timeoutSec")
    assert "ohtNumber" not in names


def test_pio_init_requires_the_bc_target_so_placeholder_config_cannot_ship_silently():
    pio = importlib.import_module("extensions.pio")
    spec = next(s for s in pio.action_specs() if s.action_type == "pioInit")
    required = {p.name for p in spec.parameters if p.required}
    assert required == {"media", "stationId", "channel", "port"}


def test_pio_init_station_id_is_a_number_input_instead_of_a_select():
    """설정 두 곳이 서로 다른 station을 말할 수 있다.

    pioInit은 실행 대상을 직접 지정하므로 config 목록이 있어도 숫자 입력으로
    보여 준다. 점검용 pioPing도 이제 station/channel을 직접 받는다 — fallback이
    사라졌으므로 비워 둘 수 없다.
    """
    pio = importlib.import_module("extensions.pio")
    config = get_config()

    stations = pio.known_station_ids(config)
    # station 목록은 이제 설비에서 온다. 빈 첫 항목("config 값 사용")은 없다.
    assert "" not in stations
    assert config.air_shower_config.pio_station_id in stations
    for rule in config.elevator_config.elevator_motion_rules:
        assert rule.pio_station_id in stations

    spec = next(
        s for s in pio.action_specs(config) if s.action_type == "pioInit"
    )
    station = next(p for p in spec.parameters if p.name == "stationId")
    assert station.input_type == "number"
    assert station.choices == ()
    assert station.required is True

    # ping도 이제 station/channel을 직접 받아야 한다 — config로 떨어질 곳이 없다.
    ping = next(
        s for s in pio.action_specs(config) if s.action_type == "pioPing"
    )
    ping_station = next(p for p in ping.parameters if p.name == "stationId")
    assert ping_station.choices == stations
    assert ping_station.required is True
    ping_channel = next(p for p in ping.parameters if p.name == "channel")
    assert ping_channel.required is True


def test_pio_ping_station_can_be_typed_when_the_list_does_not_have_it():
    """station 목록은 힌트일 뿐이다 — 설정에 없는 설비도 시험할 수 있어야 한다.

    닫힌 select 였을 때는 설정에 없는 station 을 시험할 방법이 없었고, 설비
    블록이 하나도 없는 로봇에서는 목록이 비어 required select 가 아무것도 고를
    수 없는 칸이 됐다(값이 없으니 폼 제출 자체가 막힌다).
    """
    pio = importlib.import_module("extensions.pio")
    config = get_config()

    for action_type in ("pioPing", "pioScenario"):
        spec = next(
            s for s in pio.action_specs(config) if s.action_type == action_type
        )
        station = next(p for p in spec.parameters if p.name == "stationId")
        assert station.editable is True, action_type
        assert station.choices == pio.known_station_ids(config), action_type
        assert station.placeholder, action_type

    # on/off 처럼 값이 정말 그 목록뿐인 칸은 닫힌 select 로 남는다.
    write = next(
        s for s in pio.action_specs(config) if s.action_type == "pioWriteOut"
    )
    state = next(p for p in write.parameters if p.name == "state")
    assert state.choices == ("on", "off")
    assert state.editable is False


def test_every_pio_parameter_carries_a_human_label():
    """패널 카드는 좁아서 이름만 보인다 — media/port 가 무엇인지 알 수 없다."""
    pio = importlib.import_module("extensions.pio")

    for spec in pio.action_specs(get_config()):
        for parameter in spec.parameters:
            assert parameter.label, f"{spec.action_type}.{parameter.name}"


def test_pio_action_specs_still_work_without_config():
    """config를 못 받는 옛 호출 형태에서도 자유 입력으로 뜬다."""
    pio = importlib.import_module("extensions.pio")

    spec = next(s for s in pio.action_specs() if s.action_type == "pioInit")
    station = next(p for p in spec.parameters if p.name == "stationId")
    assert station.choices == ()


def test_pio_read_in_takes_no_serial_parameters():
    """입력은 EZI IO에서 읽는다. 직렬 채널/대기시간을 물을 이유가 없다."""
    assert _pio_parameter_names("pioReadIn") == ()


def test_real_ezio_package_has_specs_without_panel():
    from importlib import resources

    ezio = importlib.import_module("extensions.ezio")
    specs = tuple(ezio.action_specs())
    expected_types = (
        "ezioReadIn",
        "photoSensorRead",
        "ezioWriteOut",
        "ezioWaitIn",
    )

    assert ezio.EZIO_ACTION_TYPES == expected_types
    assert tuple(spec.action_type for spec in specs) == expected_types
    assert all(
        not spec.motion and spec.handler is ezio.handle_ezio_action for spec in specs
    )
    assert ezio.MODULE_TITLE == "EZIO"
    assert not resources.files("extensions.ezio").joinpath("panel.html").is_file()
    assert all(
        hasattr(ezio, name)
        for name in (
            "execute_ezio_action",
            "is_ezio_action",
            "action_specs",
            "start_sensor_service",
            "summarize_ezio_result",
        )
    )


def test_ezio_wait_in_asks_for_the_pin_state_and_an_optional_timeout():
    """조건 대기는 핀·기대 상태가 없으면 성립하지 않는다. 제한 시간은 config
    기본값이 있어 비워도 된다."""
    ezio = importlib.import_module("extensions.ezio")
    spec = next(s for s in ezio.action_specs() if s.action_type == "ezioWaitIn")
    assert tuple(p.name for p in spec.parameters) == ("index", "state", "timeoutSec")
    assert {p.name for p in spec.parameters if p.required} == {"index", "state"}


def test_config_suppresses_defaults_and_appends_unique_enabled_names(
    tmp_path, monkeypatch, request
):
    source = """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type=__name__),)
"""
    first = _write_module(tmp_path, monkeypatch, request, source)
    second = _write_module(tmp_path, monkeypatch, request, source)
    added = _write_module(tmp_path, monkeypatch, request, source)
    disabled = _write_module(tmp_path, monkeypatch, request, source)
    config = _Cfg(
        (
            SimpleNamespace(module=first, enabled=False),
            SimpleNamespace(module=second, enabled=True),
            SimpleNamespace(module=added, enabled=True),
            SimpleNamespace(module=added, enabled=True),
            SimpleNamespace(module=disabled, enabled=False),
        )
    )

    discovered = discover_action_modules(config, default_modules=(first, second))

    assert tuple(item.module for item in discovered) == (second, added)
    assert tuple(item.title for item in discovered) == (second, added)


def test_skips_import_call_and_invalid_spec_failures(
    tmp_path, monkeypatch, request, capsys
):
    missing = f"missing_actions_{uuid.uuid4().hex}"
    broken = _write_module(
        tmp_path,
        monkeypatch,
        request,
        "def action_specs():\n    raise RuntimeError('broken')\n",
    )
    empty = _write_module(
        tmp_path, monkeypatch, request, "def action_specs():\n    return ()\n"
    )
    invalid = _write_module(
        tmp_path,
        monkeypatch,
        request,
        "def action_specs():\n    return ('wrong',)\n",
    )
    good = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="good"),)
""",
    )
    config = _Cfg(
        SimpleNamespace(module=name, enabled=True)
        for name in (missing, broken, empty, invalid, good)
    )

    discovered = discover_action_modules(config, default_modules=())

    assert tuple(item.module for item in discovered) == (good,)
    output = capsys.readouterr().out
    for name in (missing, broken, empty, invalid):
        assert f"[ACTION MODULE SKIP] {name}:" in output


def test_skips_module_when_title_lookup_fails_and_continues(
    tmp_path, monkeypatch, request, capsys
):
    bad_title = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="badTitle"),)

def __getattr__(name):
    if name == "MODULE_TITLE":
        raise RuntimeError("title unavailable")
    raise AttributeError(name)
""",
    )
    good = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="good"),)
""",
    )

    discovered = discover_action_modules(
        _Cfg(), default_modules=(bad_title, good)
    )

    assert tuple(item.module for item in discovered) == (good,)
    assert (
        f"[ACTION MODULE SKIP] {bad_title}: title unavailable"
        in capsys.readouterr().out
    )


def test_duplicate_type_across_modules_rejects_second(
    tmp_path, monkeypatch, request, capsys
):
    first = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="dup"),)
""",
    )
    second = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (
        ActionSpec(action_type="dup"),
        ActionSpec(action_type="unique_b"),
    )
""",
    )
    third = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="unique_b"),)
""",
    )

    discovered = discover_action_modules(
        _Cfg(), default_modules=(first, second, third), reserved_types=set()
    )

    assert tuple(item.module for item in discovered) == (first, third)
    output = capsys.readouterr().out
    assert second in output
    assert "action_type already registered" in output
    assert "dup" in output


def test_module_shadowing_reserved_type_is_rejected(
    tmp_path, monkeypatch, request, capsys
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="stateRequest"),)
""",
    )

    discovered = discover_action_modules(
        _Cfg(),
        default_modules=(name,),
        reserved_types={"stateRequest"},
    )

    assert discovered == ()
    output = capsys.readouterr().out
    assert name in output
    assert "action_type already registered" in output
    assert "stateRequest" in output


def test_none_uses_builtin_reserved_types_but_empty_set_does_not(
    tmp_path, monkeypatch, request
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="builtinType"),)
""",
    )
    monkeypatch.setattr(
        "core.action_modules._builtin_instant_action_types",
        lambda: {"builtinType"},
    )

    default_reserved = discover_action_modules(
        _Cfg(), default_modules=(name,)
    )
    explicitly_empty = discover_action_modules(
        _Cfg(), default_modules=(name,), reserved_types=set()
    )

    assert default_reserved == ()
    assert tuple(item.module for item in explicitly_empty) == (name,)


def test_duplicate_type_within_module_is_rejected(
    tmp_path, monkeypatch, request, capsys
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (
        ActionSpec(action_type="dup"),
        ActionSpec(action_type="dup"),
    )
""",
    )

    discovered = discover_action_modules(
        _Cfg(), default_modules=(name,), reserved_types=set()
    )

    assert discovered == ()
    output = capsys.readouterr().out
    assert name in output
    assert "duplicate action_type within module" in output


def test_import_failure_skips_only_that_module(tmp_path, monkeypatch, request):
    broken = _write_module(
        tmp_path,
        monkeypatch,
        request,
        "raise RuntimeError('heavy import failed')\n",
    )
    good = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="good"),)
""",
    )

    discovered = discover_action_modules(
        _Cfg(), default_modules=(broken, good), reserved_types=set()
    )

    assert tuple(item.module for item in discovered) == (good,)


def test_disabled_action_is_filtered_and_suppresses_panel(
    tmp_path, monkeypatch, request
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (
        ActionSpec(action_type="keepMe"),
        ActionSpec(action_type="dropMe"),
    )
""",
    )
    config = _Cfg()
    config.actions = [_Act("dropMe", enabled=False)]

    discovered = discover_action_modules(config, default_modules=(name,))

    assert tuple(spec.action_type for spec in discovered[0].specs) == ("keepMe",)
    assert discovered[0].panel_suppressed is True


def test_disabled_module_spec_is_filtered_and_suppresses_panel(
    tmp_path, monkeypatch, request
):
    name = _write_package(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (
        ActionSpec(action_type="keepMe"),
        ActionSpec(action_type="dropMe", enabled=False),
    )
""",
        "<section>Hidden</section>",
    )

    discovered = discover_action_modules(_Cfg(), default_modules=(name,))

    assert tuple(spec.action_type for spec in discovered[0].specs) == ("keepMe",)
    assert discovered[0].panel_suppressed is True
    assert discovered[0].panel_template is None


def test_all_disabled_module_specs_skip_module(
    tmp_path, monkeypatch, request, capsys
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (
        ActionSpec(action_type="dropMe", enabled=False),
        ActionSpec(action_type="alsoDropMe", enabled=False),
    )
""",
    )

    discovered = discover_action_modules(_Cfg(), default_modules=(name,))

    assert discovered == ()
    assert (
        f"[ACTION MODULE SKIP] {name}: all actions disabled"
        in capsys.readouterr().out
    )


def test_no_disabled_actions_keeps_specs_and_panel(
    tmp_path, monkeypatch, request
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (
        ActionSpec(action_type="keepMe"),
        ActionSpec(action_type="alsoKeepMe"),
    )
""",
    )

    discovered = discover_action_modules(_Cfg(), default_modules=(name,))

    assert tuple(spec.action_type for spec in discovered[0].specs) == (
        "keepMe",
        "alsoKeepMe",
    )
    assert discovered[0].panel_suppressed is False


def test_all_disabled_actions_skip_module(
    tmp_path, monkeypatch, request, capsys
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (
        ActionSpec(action_type="dropMe"),
        ActionSpec(action_type="alsoDropMe"),
    )
""",
    )
    config = _Cfg()
    config.actions = [
        _Act("dropMe", enabled=False),
        _Act("alsoDropMe", enabled=False),
    ]

    discovered = discover_action_modules(config, default_modules=(name,))

    assert discovered == ()
    assert (
        f"[ACTION MODULE SKIP] {name}: all actions disabled"
        in capsys.readouterr().out
    )


def test_enabled_config_action_does_not_disable_module_action(
    tmp_path, monkeypatch, request
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="keepMe"),)
""",
    )
    config = _Cfg()
    config.actions = [_Act("keepMe", enabled=True)]

    discovered = discover_action_modules(config, default_modules=(name,))

    assert tuple(spec.action_type for spec in discovered[0].specs) == ("keepMe",)
    assert discovered[0].panel_suppressed is False


def test_title_failure_does_not_reserve_action_types(
    tmp_path, monkeypatch, request
):
    bad_title = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="same"),)

def __getattr__(name):
    if name == "MODULE_TITLE":
        raise RuntimeError("title unavailable")
    raise AttributeError(name)
""",
    )
    valid = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="same"),)
""",
    )

    discovered = discover_action_modules(
        _Cfg(), default_modules=(bad_title, valid), reserved_types=set()
    )

    assert tuple(item.module for item in discovered) == (valid,)


def test_config_without_actions_discovers_module(tmp_path, monkeypatch, request):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="keepMe"),)
""",
    )

    discovered = discover_action_modules(
        SimpleNamespace(action_modules=[]), default_modules=(name,)
    )

    assert tuple(item.module for item in discovered) == (name,)


def test_config_action_without_enabled_defaults_enabled(
    tmp_path, monkeypatch, request
):
    name = _write_module(
        tmp_path,
        monkeypatch,
        request,
        """
from core.action_registry import ActionSpec

def action_specs():
    return (ActionSpec(action_type="keepMe"),)
""",
    )
    config = _Cfg()
    config.actions = [SimpleNamespace(action_type="keepMe")]

    discovered = discover_action_modules(config, default_modules=(name,))

    assert tuple(spec.action_type for spec in discovered[0].specs) == ("keepMe",)
