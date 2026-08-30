"""어댑터 페이지와 extension 화면의 경계.

extension 모듈의 액션은 어댑터 페이지(`_control_forms`)에 어떤 형태로도 나오지
않고, 오직 `/actions` 가 모듈의 ActionSpec 을 그대로 읽어 렌더한다. 그래서 모듈이
늘거나 줄어도 어댑터 페이지 코드는 그대로다.
"""

from pathlib import Path

from core.action_registry import ActionParameterSpec
from core.registry import ActionModuleView, AdaptorSpec, InstantAction, RecipeView
from web import render


def _spec(*, actions=(), modules=(), recipes=(), key="adapter"):
    return AdaptorSpec(
        key=key,
        display_name="Adapter",
        unit="adapter.service",
        workdir=Path("."),
        exec_script="run.sh",
        manufacturer="test",
        serial="serial",
        vda_full_version="3.0.0",
        topic_prefix="test/v3/serial",
        instant_actions=actions,
        action_modules=modules,
        recipes=recipes,
    )


def test_control_forms_omit_module_actions_entirely():
    module_action = InstantAction("moduleAction", "Module action")
    regular_action = InstantAction("regularAction", "Regular action")
    module = ActionModuleView("extensions.sample", "Sample", (module_action,))
    spec = _spec(actions=(module_action, regular_action), modules=(module,))

    out = render._control_forms(spec, "csrf")

    assert "moduleAction" not in out          # 전용 패널로도, Vehicle actions 로도
    assert "Sample" not in out
    assert 'value="regularAction"' in out     # 어댑터 내장 액션은 그대로


def test_control_forms_omit_recipes():
    recipe_action = InstantAction("elevatorUp", "Elevator up", True)
    regular_action = InstantAction("regularAction", "Regular action")
    recipe = RecipeView(
        action_type="elevatorUp", label="Elevator up", motion=True,
        variables=("targetMapId",), step_count=3, cleanup_count=1,
    )
    spec = _spec(actions=(recipe_action, regular_action), recipes=(recipe,))

    out = render._control_forms(spec, "csrf")

    assert "elevatorUp" not in out
    assert 'value="regularAction"' in out


def test_actions_page_renders_every_module_action_from_its_spec():
    """모듈이 여럿이어도 discovery 순서대로 한 번씩만 나온다."""
    first = InstantAction("firstAction", "First")
    second = InstantAction("secondAction", "Second")
    modules = (
        ActionModuleView("extensions.first", "First module", (first,)),
        ActionModuleView("extensions.second", "Second module", (second,)),
    )
    spec = _spec(actions=(first, second), modules=modules)

    out = render.actions_page(spec, "csrf", {})

    assert out.count('value="firstAction"') == 1
    assert out.count('value="secondAction"') == 1
    assert out.index("First module") < out.index("Second module")


def test_actions_page_renders_declared_parameters_without_a_panel_file():
    """panel.html 이 없어도 파라미터 칸이 나온다 — ActionSpec 이 유일한 출처다."""
    action = InstantAction(
        "moduleAction", "Module action",
        parameters=(ActionParameterSpec(
            "index", required=True, input_type="number", placeholder="1-8",
        ),),
    )
    module = ActionModuleView("extensions.sample", "Sample", (action,))
    spec = _spec(actions=(action,), modules=(module,))

    out = render.actions_page(spec, "csrf", {})

    assert 'name="index"' in out
    assert 'type="number"' in out
    assert 'placeholder="1-8"' in out
    assert "required" in out


def test_actions_page_renders_the_modules_own_panel():
    """모듈이 panel.html 을 주면 그 HTML 을 그대로 쓴다 — UI 는 모듈 소유다."""
    action = InstantAction("moduleAction", "Module action")
    module = ActionModuleView(
        "extensions.sample", "Sample", (action,),
        '<section data-panel="sample">$adapter_key|${csrf_token}|$return_to</section>',
    )
    spec = _spec(actions=(action,), modules=(module,))

    out = render.actions_page(spec, 'csrf<&"', {}, )

    assert out.count('data-panel="sample"') == 1
    assert "adapter|csrf&lt;&amp;&quot;|/adapter/adapter/actions" in out


def test_panel_covered_actions_are_not_also_generated():
    covered = InstantAction("clampOn", "Servo ON")
    module = ActionModuleView(
        "extensions.clamp", "Clamp", (covered,),
        '<section data-panel="clamp">'
        '<form><input name="action_type" value="clampOn"></form></section>',
    )
    spec = _spec(actions=(covered,), modules=(module,))

    out = render.actions_page(spec, "csrf", {})

    assert out.count('value="clampOn"') == 1     # 패널 것 하나뿐


def test_actions_the_panel_misses_still_get_a_generated_form():
    """panel.html 이 모듈보다 뒤처져도 액션이 화면에서 사라지지 않는다."""
    covered = InstantAction("clampOn", "Servo ON")
    missed = InstantAction("clampTeach", "Clamp teach")
    module = ActionModuleView(
        "extensions.clamp", "Clamp", (covered, missed),
        '<section data-panel="clamp">'
        '<form><input name="action_type" value="clampOn"></form></section>',
    )
    spec = _spec(actions=(covered, missed), modules=(module,))

    out = render.actions_page(spec, "csrf", {})

    assert 'data-panel="clamp"' in out
    assert out.count('value="clampOn"') == 1
    assert 'value="clampTeach"' in out           # 보완 폼으로 나온다
    assert "Clamp — 그 외" in out


def test_fully_covered_module_gets_no_leftover_group():
    covered = InstantAction("clampOn", "Servo ON")
    module = ActionModuleView(
        "extensions.clamp", "Clamp", (covered,),
        '<section data-panel="clamp">'
        '<form><input name="action_type" value="clampOn"></form></section>',
    )
    spec = _spec(actions=(covered,), modules=(module,))

    assert "그 외" not in render.actions_page(spec, "csrf", {})


def test_broken_panel_falls_back_to_generated_forms(capsys):
    action = InstantAction("moduleAction", "Module action")
    module = ActionModuleView(
        "extensions.broken", "Broken", (action,), "<div>${unknown}</div>",
    )
    spec = _spec(actions=(action,), modules=(module,))

    out = render.actions_page(spec, "csrf", {})

    assert 'value="moduleAction"' in out          # 액션은 살아 있다
    assert "Broken" in out
    assert "[ACTION MODULE PANEL RENDER FALLBACK] extensions.broken:" in (
        capsys.readouterr().out
    )


def test_whitespace_panel_is_treated_as_absent():
    action = InstantAction("moduleAction", "Module action")
    module = ActionModuleView("extensions.sample", "Sample", (action,), " \n\t")
    spec = _spec(actions=(action,), modules=(module,))

    out = render.actions_page(spec, "csrf", {})

    assert 'value="moduleAction"' in out
    assert "그 외" not in out                      # 보완이 아니라 정상 그룹이다


def test_shipped_panels_render_and_cover_their_module(capsys):
    """저장소가 싣고 다니는 panel.html 이 실제로 렌더되는지.

    패널이 깨지면 조용히 생성 폼으로 폴백해서 화면은 멀쩡해 보인다(액션은
    '— 그 외'로 다 나온다). 그래서 눈으로는 못 잡는다 — 폴백 로그와 액션 커버리지
    두 가지로 잡는다. 선언한 파라미터도 패널 안에 칸이 있어야 한다.
    """
    from config.config import get_config
    from core.registry import build_registry

    spec = next(s for s in build_registry(get_config()) if s.manufacturer == "jibot")
    shipped = [m for m in spec.action_modules if m.panel_template]
    assert shipped, "panel.html 을 가진 first-party 모듈이 하나도 없다"

    for module in shipped:
        panel = render._module_panel(spec.key, module, "tok", "/back")
        assert panel, f"{module.module}: 패널이 렌더되지 않았다"
        for action in module.actions:
            marker = f'name="action_type" value="{action.action_type}"'
            assert marker in panel, f"{module.module}: {action.action_type} 누락"
            form = panel.split(marker, 1)[1].split("</form>", 1)[0]
            for parameter in getattr(action, "parameters", ()) or ():
                assert f'name="{parameter.name}"' in form, (
                    f"{module.module}: {action.action_type}.{parameter.name} 칸 누락"
                )
                # 칸만 있고 이름표가 없으면 어느 칸이 무엇인지 화면에서 알 수 없다.
                assert f'class="ap-key" title="{parameter.name}' in form, (
                    f"{module.module}: {action.action_type}.{parameter.name} 이름표 누락"
                )
    assert "FALLBACK" not in capsys.readouterr().out


def test_shipped_panels_take_choices_from_config_not_hardcoded():
    """설정에서 오는 선택지는 패널에 박지 않고 스키마 필드로 끌어와야 한다.

    extensions.hcl 의 output_signals 를 고쳤는데 드롭다운이 그대로면, 패널을 또
    손으로 고쳐야 한다 — 없애려던 문제가 그대로 돌아온다.
    """
    from config.config import get_config
    from core.registry import build_registry

    spec = next(s for s in build_registry(get_config()) if s.manufacturer == "jibot")
    pio = next(m for m in spec.action_modules if m.module == "extensions.pio")
    write = next(a for a in pio.actions if a.action_type == "pioWriteOut")
    signal = next(p for p in write.parameters if p.name == "signal")
    assert signal.choices, "이 테스트는 signal 이 choices 를 선언해야 의미가 있다"

    panel = render._module_panel(spec.key, pio, "tok", "/back")
    form = panel.split('value="pioWriteOut"', 1)[1].split("</form>", 1)[0]
    for choice in signal.choices:
        assert f'<option value="{choice}"' in form
    # 선택지가 템플릿이 아니라 스키마에서 왔다는 확인
    assert all(
        f"<option" not in line or "signal" not in line
        for line in (pio.panel_template or "").splitlines()
    )


def test_panel_fields_show_which_parameter_they_are():
    """패널 칸은 이름표를 달고 나온다.

    패널이 안내를 placeholder 하나에 맡기던 때는 첫 칸이 media 인지 stationId
    인지 화면만 보고는 알 수 없었다 — select 에는 placeholder 가 없고, 값을 채우면
    placeholder 도 사라진다. 이름표는 패널이 아니라 스키마에서 온다.
    """
    action = InstantAction(
        "moduleAction", "Module action",
        parameters=(
            ActionParameterSpec("media", label="BC 매체 번호"),
            ActionParameterSpec("bare"),
        ),
    )
    module = ActionModuleView(
        "extensions.sample", "Sample", (action,),
        '<form><input name="action_type" value="moduleAction">'
        "$field_moduleAction_media$field_moduleAction_bare</form>",
    )

    panel = render._module_panel("adapter", module, "tok", "/back")

    assert 'title="media · BC 매체 번호">media <span class="ap-hint">' in panel
    assert "· BC 매체 번호</span>" in panel
    assert 'title="bare">bare</span>' in panel      # label 이 없으면 이름만


def test_editable_choice_parameter_can_be_typed_into():
    """목록이 힌트일 뿐인 칸(stationId)은 골라도 되고 쳐도 된다."""
    parameter = ActionParameterSpec(
        "stationId", required=True, choices=("000010", "000020"), editable=True,
        placeholder="예: 000030",
    )

    out = render._param_field(parameter, None, "pioPing")

    assert "<select" not in out
    assert 'list="dl-pioPing-stationId"' in out
    assert '<datalist id="dl-pioPing-stationId">' in out
    assert '<option value="000010">' in out
    assert 'placeholder="예: 000030"' in out
    assert "required" in out


def test_closed_choice_parameter_stays_a_select():
    """on/off 처럼 값이 그 목록뿐인 칸은 그대로 드롭다운이다."""
    out = render._param_field(ActionParameterSpec("state", choices=("on", "off")))

    assert "<select" in out
    assert "<datalist" not in out


def test_datalist_ids_do_not_collide_between_actions():
    """한 화면에 같은 이름의 파라미터가 여러 액션에 있다(stationId).

    id 가 겹치면 브라우저는 첫 datalist 만 쓰므로, 다른 액션의 후보가 조용히
    엉뚱한 칸에 붙는다.
    """
    parameter = ActionParameterSpec("stationId", choices=("1",), editable=True)

    ping = render._param_field(parameter, None, "pioPing")
    scenario = render._param_field(parameter, None, "pioScenario")

    assert 'id="dl-pioPing-stationId"' in ping
    assert 'id="dl-pioScenario-stationId"' in scenario


def test_generated_run_card_labels_carry_the_parameter_description():
    action = InstantAction(
        "moduleAction", "Module action",
        parameters=(ActionParameterSpec("port", label="BC 포트"),),
    )
    module = ActionModuleView("extensions.sample", "Sample", (action,))
    spec = _spec(actions=(action,), modules=(module,))

    out = render.actions_page(spec, "csrf", {})

    assert "BC 포트" in out
    assert ">port <span" in out                       # 전송되는 키도 그대로 보인다


def test_actions_page_escapes_module_supplied_text():
    action = InstantAction("moduleAction", 'Label <&">')
    module = ActionModuleView("extensions.sample", 'Sample <&">', (action,))
    spec = _spec(actions=(action,), modules=(module,))

    out = render.actions_page(spec, 'csrf<&"', {})

    assert 'Sample <&">' not in out
    assert "Sample &lt;&amp;&quot;&gt;" in out
    assert 'value="csrf&lt;&amp;&quot;"' in out
