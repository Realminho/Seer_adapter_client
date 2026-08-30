from dataclasses import dataclass

from core import configio
from core.registry import ActionModuleView
from web import render


@dataclass
class _Spec:
    key: str
    display_name: str
    monitor_kind: str = "vda5050"
    unit: str = "amr-adaptor.service"
    manufacturer: str = "jibot"
    serial: str = "HN-SH6-TR-001"
    vda_full_version: str = "3.0.0"
    topic_prefix: str = "amr/v3/HN-SH6-TR-001"
    mqtt_host: str = "192.168.3.108"
    mqtt_port: int = 11883
    vehicle_host: str = "10.0.0.11"
    vehicle_port: int = 7273
    runtime_kind: str = "systemd"
    exec_script: str = "run-main.sh"
    instant_actions: tuple = ()
    action_modules: tuple = ()
    recipes: tuple = ()
    runnable: tuple = ()


@dataclass
class _Action:
    action_type: str
    label: str
    motion: bool


@dataclass
class _Metrics:
    exists: bool = True
    active_state: str = "active"
    enabled_state: str = "enabled"
    uptime_sec: int = 120
    cpu_percent: float = 1.5
    memory_bytes: int = 1048576
    restarts: int = 0


@dataclass
class _Snap:
    connection_state: str = "ONLINE"
    operating_mode: str = "AUTOMATIC"
    active_emergency_stop: str = "NONE"
    field_violation: bool = False
    driving: bool = True
    paused: bool = False
    battery_soc: float = 87.0
    charging: bool = False
    x: float = 1.0
    y: float = 2.0
    theta: float = 0.0
    map_id: str = "lab2m"
    errors: tuple = ()
    localization_score: float = 0.0
    last_node_id: str = "N-001"
    order_id: str = ""
    order_update_id: int = 0
    node_states: tuple = ()
    edge_states: tuple = ()
    action_states: tuple = ()
    header_id: int = 17


class _Diag:
    def __init__(self, label, requires_manual=False, note=""):
        self.label = label
        self.argv = ("{py}", "-m", "pytest")
        self.note = note
        self.requires_manual = requires_manual


def test_esc_escapes_html():
    assert render.esc('<a>&"') == "&lt;a&gt;&amp;&quot;"


def test_csrf_field_present():
    assert 'name="csrf_token"' in render._csrf_field("tok123")
    assert "tok123" in render._csrf_field("tok123")


def test_adapter_list_renders_rows():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows)
    assert "JIBOT" in out
    assert "/adapter/jibot" in out
    assert "active" in out
    assert "live state" in out


def test_adapter_list_renders_startup_error_notice():
    out = render.adapter_list_page(
        [],
        startup_error=(
            "어댑터가 정상적으로 켜지지 않았습니다. "
            "robots.hcl 설정 오류로 amr-adaptor.service가 시작되지 못했습니다."
        ),
    )

    assert "어댑터 기동 오류" in out
    assert "어댑터가 정상적으로 켜지지 않았습니다" in out
    assert "robots.hcl" in out


def test_detail_omits_live_for_monitorless():
    spec = _Spec("hex", "Hexplorer", monitor_kind="none")
    out = render.adapter_detail_page(spec, _Metrics(), None, {})
    assert "Hexplorer" in out
    assert "battery" not in out.lower()  # no live section


def test_detail_renders_with_none_service_metrics():
    # Real ServiceMetrics fields are Optional and are None when the unit is
    # inactive / systemd does not report them. The status-strip must not do
    # arithmetic on None (regression: memory_bytes None -> TypeError -> HTTP 500).
    spec = _Spec("jibot", "JIBOT")
    metrics = _Metrics(
        memory_bytes=None, uptime_sec=None, cpu_percent=None, restarts=None
    )
    out = render.adapter_detail_page(spec, metrics, _Snap(), {})
    assert "Memory" in out
    assert "—" in out  # None values shown as a dash, not crashing


def test_live_errors_render_type_as_title_before_description():
    errors = (
        {
            "errorType": "CONFIG_LOAD_FAILED",
            "errorDescription": "config.toml 파일을 읽을 수 없습니다.",
        },
    )

    out = render._mqtt_live_table(_Spec("jibot", "JIBOT"), _Snap(errors=errors))

    title_at = out.index('class="error-title">CONFIG_LOAD_FAILED')
    description_at = out.index(
        'class="error-description">config.toml 파일을 읽을 수 없습니다.'
    )
    assert title_at < description_at


def test_live_error_plain_text_keeps_description_with_fallback_title():
    out = render._mqtt_live_table(
        _Spec("jibot", "JIBOT"), _Snap(errors=("legacy error text",))
    )

    assert 'class="error-title">Error' in out
    assert 'class="error-description">legacy error text' in out


def test_verb_form_has_no_confirm_checkbox():
    # confirm removed for service-control verbs (and camera, which reuses this form)
    for verb in ("stop", "restart", "disable"):
        out = render._verb_form("jibot", verb, "tok")
        assert 'name="confirm"' not in out


def test_config_page_renders_field_descriptions():
    sections = [
        ("dock", [("fail_timeout_sec", "num", 90.0)], []),
        ("sound_settings", [("sink", "str", "alsa_output.platform-rt5651-sound.stereo-fallback")], []),
        ("mqtt_broker", [("host", "str", "127.0.0.1")], []),
    ]
    out = render.config_page(sections, "tok", {})
    assert "[dock].fail_timeout_sec" in out
    assert "UmDock" in out
    assert "[sound_settings].sink" in out
    assert "scripts/test-sound-devices.sh" in out
    assert "[mqtt_broker].host" in out


def _form_containing(html: str, needle: str) -> str:
    """Return the <form>...</form> that contains `needle` (forms don't nest)."""
    i = html.index(needle)
    start = html.rfind("<form", 0, i)
    end = html.index("</form>", i) + len("</form>")
    return html[start:end]


def test_control_motion_action_has_no_confirm():
    spec = _Spec("jibot", "JIBOT", instant_actions=(_Action("startPause", "Pause", True),))
    out = render.control_page(spec, _Metrics(), "tok", {})
    assert 'name="csrf_token"' in out
    assert "startPause" in out
    # The motion vehicle action (startPause) fires on a single click: ITS OWN card
    # carries no confirm checkbox. gotoNearestNode / host reboot / urobot restart /
    # motion-rule deliberately keep a confirm gate (the robot moves / host reboots)
    # and are separate cards, so we scope the assertion to the startPause form.
    card = _form_containing(out, 'value="startPause"')
    assert 'name="confirm"' not in card


def test_control_localize_form_has_goal_and_confirm():
    spec = _Spec(
        "jibot",
        "JIBOT",
        instant_actions=(_Action("localize", "Localize robot", True),),
    )

    out = render.control_page(spec, _Metrics(), "tok", {})
    form = _form_containing(out, 'value="localize"')

    assert 'name="target" value="goal"' in form
    assert 'name="goal"' in form
    assert 'name="confirm"' in form
    assert "실제 이동 없음" in form


def test_control_renders_volume_presets_and_input():
    # setSoundVolume registered -> control page shows a Sound group with preset
    # buttons (50/70/100 + mute) AND a free-entry numeric input, all posting
    # action_type=setSoundVolume with a volume/mute param to /action.
    spec = _Spec(
        "jibot", "JIBOT",
        instant_actions=(
            _Action("testSound", "Sound test", False),
            _Action("stopSound", "Stop sound", False),
            _Action("setSoundVolume", "Sound volume", False),
        ),
    )
    out = render.control_page(spec, _Metrics(), "tok", {})
    assert "setSoundVolume" in out
    # preset buttons every 10%: 0,10,20,...,100
    for pct in range(0, 101, 10):
        assert f'name="volume" value="{pct}"' in out
    # mute / unmute presets
    assert 'name="mute" value="1"' in out
    assert 'name="mute" value="0"' in out
    # free-entry numeric input for an arbitrary volume
    assert 'type="number"' in out
    assert 'name="volume"' in out


def test_clamp_move_to_position_input_lives_on_the_actions_page():
    # clampMoveTo 의 엔코더 위치 칸은 ActionSpec 의 parameters 에서 나온다.
    # 어댑터/제어 페이지에는 어떤 형태로도 나오지 않는다.
    from core.action_registry import ActionParameterSpec
    from types import SimpleNamespace as NS

    position = ActionParameterSpec(
        "position", required=True, input_type="number", placeholder="엔코더 절대 위치",
    )
    module = NS(
        module="extensions.clamp", title="Clamp", panel_template=None,
        actions=(NS(action_type="clampMoveTo", label="Clamp move to position",
                    motion=True, parameters=(position,)),),
    )
    spec = NS(key="jibot", display_name="JIBOT",
              action_modules=(module,), recipes=())

    out = render.actions_page(spec, "tok", {})
    assert 'value="clampMoveTo"' in out
    assert 'name="position"' in out
    assert 'type="number"' in out
    assert out.count('value="clampMoveTo"') == 1

    control = render.control_page(
        _Spec("jibot", "JIBOT",
              instant_actions=(_Action("clampMoveTo", "Clamp move to position", True),),
              action_modules=(ActionModuleView(
                  "extensions.clamp", "Clamp",
                  (_Action("clampMoveTo", "Clamp move to position", True),),
              ),)),
        _Metrics(), "tok", {},
    )
    assert "clampMoveTo" not in control


def test_control_volume_actions_not_duplicated_in_vehicle_actions():
    # Sound actions render only under the dedicated Sound group, not also as
    # bare buttons in the generic Vehicle actions group.
    spec = _Spec(
        "jibot", "JIBOT",
        instant_actions=(
            _Action("stateRequest", "Request state", False),
            _Action("testSound", "Sound test", False),
            _Action("setSoundVolume", "Sound volume", False),
        ),
    )
    out = render.control_page(spec, _Metrics(), "tok", {})
    # testSound appears once (Sound group), not twice (Sound + Vehicle actions)
    assert out.count('value="testSound"') == 1
    # the non-sound action still renders in Vehicle actions
    assert "stateRequest" in out


def _jibot_full_spec():
    return _Spec(
        "jibot", "JIBOT",
        instant_actions=(
            _Action("manualStop", "Manual Stop", False),
            _Action("stopCharging", "Stop charging", False),
            _Action("enableMotor", "Enable Motor", True),
            _Action("startPause", "Pause", True),
            _Action("setSoundVolume", "Sound volume", False),
        ),
    )


def test_detail_renders_big_red_stop_and_stop_charging():
    out = render.adapter_detail_page(_jibot_full_spec(), _Metrics(), _Snap(), {}, csrf="tok")
    # oversized red STOP posts manualStop (UmStop)
    assert "btn estop" in out
    assert 'value="manualStop"' in out
    assert ">STOP<" in out
    # stop charging quick button present
    assert 'value="stopCharging"' in out
    # emergency-only actions are NOT duplicated in the generic Vehicle actions wrap
    assert out.count('value="manualStop"') == 1
    assert out.count('value="stopCharging"') == 1
    assert out.count('value="enableMotor"') == 1


def test_emergency_forms_includes_disable_motor():
    """disableMotor appears in quick-control list when spec exposes it."""
    spec = _Spec(
        "jibot", "JIBOT",
        instant_actions=(
            _Action("manualStop", "Manual Stop", False),
            _Action("enableMotor", "Enable Motor", True),
            _Action("disableMotor", "Disable Motor", True),
        ),
    )
    out = render._emergency_forms(spec, "tok")
    assert "disableMotor" in out


def test_topbar_carries_serial_pills_and_actions():
    # The adapter title (serial), live pills, and the action buttons all live in
    # the top AppShell header (.topbar); the old separate .page-head is gone.
    spec = _Spec("jibot", "HN-SH6-TR-001")
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    header = out.split("</header>")[0]
    assert "<strong>HN-SH6-TR-001</strong>" in header   # serial as brand title
    assert "ONLINE" in header                            # connection pill in topbar
    assert "/adapter/jibot/tests" in header              # action button moved up
    assert "/adapter/jibot/logs" in header
    assert "/adapter/jibot?refresh=" in header           # refresh button moved up
    assert 'class="page-head"' not in out                # old page-head removed


def test_detail_topbar_shows_robot_id_title_after_logs():
    spec = _Spec("jibot", "JIBOT Adapter", serial="HN-SH6-TR-001")
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    header = out.split("</header>")[0]
    title = '<div class="model-title">JIBOT Adaptor - HN-SH6-TR-001</div>'
    assert title in header
    assert header.index('/adapter/jibot/logs') < header.index(title)
    assert header.index(title) < header.index('/adapter/jibot?refresh=')


def test_appshell_logo_consistent_across_pages():
    # The brand mark is the MW logo asset, applied via the shared page() wrapper,
    # so every page's AppShell header is consistent (no bespoke text mark).
    logo = "/assets/mw-logo-blue-small.png"
    spec = _Spec("jibot", "JIBOT")
    assert logo in render.adapter_list_page([(spec, _Metrics(), _Snap())])
    assert logo in render.adapter_detail_page(spec, _Metrics(), _Snap(), {})
    assert logo in render.config_page([("mqtt_broker", [("host", "str", "x")], [])], "tok", {})
    assert "brand-logo" in render.factsheet_page("{}", {})
    assert ">AMR</span>" not in render.adapter_list_page([(spec, _Metrics(), _Snap())])


def test_motor_control_card_title_desc_on_off():
    # Motor power is a title/description/button card (not a bare quick button),
    # carrying both Motor ON (enableMotor) and Motor OFF (disableMotor).
    spec = _Spec(
        "jibot", "JIBOT",
        instant_actions=(
            _Action("enableMotor", "Enable Motor", True),
            _Action("disableMotor", "Disable Motor", True),
        ),
    )
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert "모터 전원" in out                              # card title
    assert "motor-card" in out                            # action-card style
    assert 'value="enableMotor"' in out and "Motor ON" in out
    assert 'value="disableMotor"' in out and "Motor OFF" in out
    # both motor controls render exactly once (in the card, not duplicated)
    assert out.count('value="enableMotor"') == 1
    assert out.count('value="disableMotor"') == 1


def test_service_and_vehicle_actions_render_as_cards_with_label_and_desc():
    # Service control + Vehicle actions are compact cards carrying BOTH a label
    # and a description plus the action button (not button-only).
    spec = _Spec("jibot", "JIBOT",
                 instant_actions=(_Action("cancelOrder", "Cancel order", True),))
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert 'class="action-card"' in out
    # service verb card: title + description + button
    assert "<strong>Start service</strong>" in out
    assert "adapter process 시작" in out          # verb description text
    assert ">Start</button>" in out
    # vehicle action card: action label as title + meta description + button
    assert "<strong>Cancel order</strong>" in out
    assert "현재 order 취소" in out                # action description text
    assert ">Cancel</button>" in out


def test_recovery_group_holds_cancel_order_and_clear_errors():
    # cancelOrder + clearErrors render together in a dedicated "주문 / 복구"
    # group (operator recovery), NOT in the generic Vehicle actions wrap.
    spec = _Spec(
        "jibot", "JIBOT",
        instant_actions=(
            _Action("stateRequest", "Request state", False),
            _Action("cancelOrder", "Cancel order", True),
            _Action("clearErrors", "Clear errors", False),
        ),
    )
    out = render._control_forms(spec, "tok")
    # dedicated recovery group heading
    assert ">주문 / 복구</h2>" in out
    # both actions render as cards
    assert 'value="cancelOrder"' in out
    assert 'value="clearErrors"' in out
    assert "<strong>Clear errors</strong>" in out
    assert ">Clear errors</button>" in out
    # each appears exactly once (not duplicated into Vehicle actions)
    assert out.count('value="cancelOrder"') == 1
    assert out.count('value="clearErrors"') == 1
    # the recovery actions live under the recovery group, not Vehicle actions
    rg = out.find(">주문 / 복구</h2>")
    seg = out[rg:rg + 1500]
    assert 'value="cancelOrder"' in seg
    assert 'value="clearErrors"' in seg
    # a non-recovery action still renders in the generic Vehicle actions group
    assert ">Vehicle actions</h2>" in out
    assert "stateRequest" in out


def test_clear_errors_single_click_no_confirm():
    # Clearing the sticky error list moves nothing -> single click, no confirm.
    spec = _Spec("jibot", "JIBOT",
                 instant_actions=(_Action("clearErrors", "Clear errors", False),))
    out = render._control_forms(spec, "tok")
    rg = out.find(">주문 / 복구</h2>")
    seg = out[rg:rg + 800]
    assert 'value="clearErrors"' in seg
    assert 'name="confirm"' not in seg


def test_drive_sound_goto_motion_host_render_as_action_cards():
    # Drive / Sound / Goto / Motion-rule / Host controls render in the SAME
    # vertical .action-card style (title + desc + optional field + button) as the
    # Service/Vehicle/Clamp groups, flowing inside .cmd-wrap — not as the old
    # one-row .command-form. Each group's section carries .action-card and no
    # leftover .command-form bleeds into the control body.
    clamp_action = _Action("clampMoveTo", "Clamp move to", True)
    spec = _Spec(
        "jibot", "JIBOT",
        instant_actions=(
            _Action("setSoundVolume", "Sound volume", False),
            clamp_action,
            _Action("jibotMotionRule", "Motion rule", True),
            _Action("manualDrive", "Manual drive", False),
            _Action("manualMove", "Manual move", True),
        ),
        action_modules=(ActionModuleView(
            "clamp", "Clamp", (clamp_action,),
            '<section class="command-group"><div class="command-head"><h2>Clamp</h2></div>'
            '<div class="command-body"><div class="cmd-wrap">'
            '<form class="action-card"><input name="action_type" '
            'value="clampMoveTo"></form></div></div></section>',
        ),),
    )
    out = render._control_forms(
        spec, "tok", host_reboot_enabled=True, urobot_restart_enabled=True
    )
    for heading in (
        "Drive (수동)", "Sound", "Goto (UmGoto)",
        "Motion rule (dock/move)", "Host / robot",
    ):
        i = out.find(f">{heading}</h2>")
        assert i != -1, f"missing group: {heading}"
        seg = out[i:i + 1500]
        assert "action-card" in seg, f"{heading} not card-style"
        assert "cmd-wrap" in seg, f"{heading} not in cmd-wrap"
    assert "Clamp" not in out          # extension 그룹은 /actions 소관
    # the old single-row command-form layout is fully retired from the control body
    assert "command-form" not in out


def test_detail_status_strip_shows_working_state_and_last_node():
    snap = _Snap(last_node_id="N-9")
    snap.working_state = "DRIVING"
    out = render.adapter_detail_page(_Spec("jibot", "JIBOT"), _Metrics(), snap, {})
    assert "Working" in out
    assert "DRIVING" in out
    assert "Last node" in out
    assert "N-9" in out


def test_sound_preset_nearest_current_volume_is_pressed():
    spec = _Spec("jibot", "JIBOT",
                 instant_actions=(_Action("setSoundVolume", "Sound volume", False),))
    out = render.adapter_detail_page(
        spec, _Metrics(), _Snap(), {}, csrf="tok",
        sound_state={"volume": 63, "muted": False},
    )
    # nearest preset to 63 is 60 -> pressed (is-active); 70 is not
    assert 'name="volume" value="60"><button class="btn is-active">60%</button>' in out
    assert 'name="volume" value="70"><button class="btn is-active">70%</button>' not in out


def test_sound_panel_is_single_compact_card():
    # Sound collapses into ONE compact card: Play/Stop as a transport button row,
    # the volume presets, and the free-entry input — not four separate tall cards.
    spec = _Spec(
        "jibot", "JIBOT",
        instant_actions=(
            _Action("testSound", "Sound test", False),
            _Action("stopSound", "Stop sound", False),
            _Action("setSoundVolume", "Sound volume", False),
        ),
    )
    out = render._control_forms(spec, "tok")
    # exactly one compact sound card wraps everything
    assert out.count("sound-card") == 1
    # Play / Stop render in a compact transport row (not two tall action-cards)
    assert "sound-transport" in out
    assert 'value="testSound"' in out
    assert 'value="stopSound"' in out
    # the free-entry volume input rides inline in the same card (compact set row)
    assert "sound-set" in out
    assert 'type="number"' in out
    # Play still appears exactly once (compact transport, not also a big card)
    assert out.count('value="testSound"') == 1


def test_sound_mute_marks_mute_button_pressed():
    spec = _Spec("jibot", "JIBOT",
                 instant_actions=(_Action("setSoundVolume", "Sound volume", False),))
    out = render.adapter_detail_page(
        spec, _Metrics(), _Snap(), {}, csrf="tok",
        sound_state={"volume": 50, "muted": True},
    )
    assert "btn danger is-active" in out


def test_detail_shows_video_link_when_url_given():
    spec = _Spec("jibot", "JIBOT")
    out = render.adapter_detail_page(
        spec, _Metrics(), _Snap(), {}, video_url="http://192.168.3.222:8080"
    )
    assert ">camera<" in out
    assert 'href="/camera"' in out


def test_detail_omits_video_link_without_url():
    spec = _Spec("jibot", "JIBOT")
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {})
    assert ">video<" not in out


def test_detail_renders_localization_and_order():
    spec = _Spec("jibot", "JIBOT")  # monitor_kind defaults to vda5050
    snap = _Snap()
    snap.localization_score = 0.95
    snap.order_id = "ord-7"
    snap.order_update_id = 3
    out = render.adapter_detail_page(spec, _Metrics(), snap, {})
    assert "localization" in out
    assert "order" in out
    assert "live state" in out


def test_detail_renders_adapter_ip_and_mqtt_info():
    spec = _Spec("jibot", "JIBOT")

    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {})

    assert "adapter info" in out
    assert "amr-adaptor.service" in out
    assert "jibot" in out
    assert "HN-SH6-TR-001" in out
    assert "3.0.0" in out
    assert "10.0.0.11:7273" in out
    assert "192.168.3.108:11883" in out
    assert "amr/v3/HN-SH6-TR-001" in out


def test_detail_renders_extended_mqtt_live_state():
    snap = _Snap(
        driving=True,
        paused=False,
        charging=True,
        field_violation=True,
        map_id="floor-1",
        last_node_id="N-42",
        node_states=({"nodeId": "N-43"}, {"nodeId": "N-44"}),
        edge_states=({"edgeId": "E-1"},),
        action_states=({"actionId": "A-1", "actionStatus": "RUNNING"},),
        header_id=99,
    )

    out = render.adapter_detail_page(_Spec("jibot", "JIBOT"), _Metrics(), snap, {})

    assert "map" in out
    assert "floor-1" in out
    assert "last node" in out
    assert "N-42" in out
    assert "driving" in out
    assert "paused" in out
    assert "charging" in out
    assert "field violation" in out
    assert "nodes/edges/actions" in out
    assert "2 / 1 / 1" in out
    assert "header id" in out
    assert "99" in out


def test_detail_renders_control_and_test_sections():
    spec = _Spec(
        "jibot",
        "JIBOT",
        instant_actions=(_Action("startPause", "Pause", False),),
        runnable=(
            _Diag(
                "unittest: clamp actions",
                requires_manual=True,
                note="Focused fake-hardware clamp action tests.",
            ),
            _Diag(
                "unittest: PIO actions",
                requires_manual=True,
                note="Focused fake-hardware PIO action tests.",
            ),
        ),
    )

    out = render.adapter_detail_page(
        spec,
        _Metrics(),
        _Snap(active_emergency_stop="MANUAL"),
        {},
        csrf="tok",
        host_reboot_enabled=True,
    )

    assert 'action="/adapter/jibot/control"' in out
    assert 'action="/adapter/jibot/action"' in out
    assert 'action="/adapter/jibot/tests/run"' in out
    assert 'action="/adapter/jibot/host/reboot"' in out
    assert 'name="return_to" value="dashboard"' in out
    assert "unittest: clamp actions" in out
    assert "unittest: PIO actions" in out
    assert "Focused fake-hardware clamp action tests." in out
    assert "Focused fake-hardware PIO action tests." in out


def test_detail_enables_manual_diagnostics_with_live_state():
    # Emergency-stop interlock removed — manual diagnostics run with a live
    # adapter regardless of e-stop / operating mode.
    spec = _Spec(
        "jibot",
        "JIBOT",
        runnable=(_Diag("unittest: clamp actions", requires_manual=True),),
    )

    out = render.adapter_detail_page(
        spec, _Metrics(), _Snap(operating_mode="AUTOMATIC"), {}, csrf="tok"
    )

    assert "unittest: clamp actions" in out
    assert "requires emergency stop" not in out
    assert "<button disabled" not in out


def test_control_page_shows_mqtt_live_snapshot():
    spec = _Spec("jibot", "JIBOT")
    out = render.control_page(spec, _Metrics(), "tok", {}, snapshot=_Snap())
    assert "live state" in out
    assert "battery" in out


def test_control_page_shows_host_reboot_when_enabled():
    spec = _Spec("jibot", "JIBOT")
    out = render.control_page(spec, _Metrics(), "tok", {}, host_reboot_enabled=True)
    assert "host" in out
    assert "reboot OS" in out
    assert 'action="/adapter/jibot/host/reboot"' in out
    assert "confirm" in out


def test_tests_page_shows_mqtt_live_snapshot():
    spec = _spec_with_diags()
    out = render.tests_page(spec, None, "tok", {}, snapshot=_Snap())
    assert "live state" in out
    assert "battery" in out


def test_tests_page_shows_adapter_and_acs_status():
    spec = _spec_with_diags()
    snap = _Snap()
    snap.adapter_online = False
    snap.acs_broker_connected = False
    snap.state_age_sec = 42.4
    snap.state_last_error = "state file missing"

    out = render.tests_page(spec, None, "tok", {}, snapshot=snap)

    assert "adapter" in out
    assert "offline/stale" in out
    assert "ACS broker" in out
    assert "disconnected" in out
    assert "state age" in out
    assert "42.4s" in out
    assert "state file missing" in out


def test_tests_page_shows_instant_action_result():
    spec = _spec_with_diags()
    snap = _Snap()
    snap.instant_action_states = [
        {"actionType": "clamp", "actionStatus": "FAILED", "resultDescription": "busy"}
    ]

    out = render.tests_page(spec, None, "tok", {}, snapshot=snap)

    assert "clamp" in out
    assert "FAILED" in out
    assert "busy" in out


class _Runner:
    def __init__(self, lines, running=False, returncode=0, label="pytest"):
        self._lines = lines
        self.running = running
        self.returncode = returncode
        self.label = label

    def lines(self):
        return self._lines


def _spec_with_diags():
    s = _Spec("jibot", "JIBOT")
    s.runnable = (
        type("D", (), {"label": "unit tests", "argv": ("{py}", "-m", "pytest"), "note": ""})(),
    )
    return s


def test_tests_page_lists_runnables_and_run_form():
    spec = _spec_with_diags()
    out = render.tests_page(spec, None, "tok", {})
    assert "unit tests" in out
    assert 'action="/adapter/jibot/tests/run"' in out
    assert 'name="index"' in out
    assert 'name="csrf_token"' in out


def test_tests_page_enables_manual_diagnostic_with_live_state():
    # Emergency-stop interlock removed — runnable with a live adapter.
    spec = _Spec("jibot", "JIBOT")
    spec.runnable = (_Diag("clamp hardware check", requires_manual=True),)

    out = render.tests_page(spec, None, "tok", {}, snapshot=_Snap(operating_mode="AUTOMATIC"))

    assert "clamp hardware check" in out
    assert "requires emergency stop" not in out
    assert "<button disabled" not in out


def test_tests_page_enables_manual_diagnostic_in_emergency_stop():
    spec = _Spec("jibot", "JIBOT")
    spec.runnable = (_Diag("clamp hardware check", requires_manual=True),)

    out = render.tests_page(
        spec, None, "tok", {}, snapshot=_Snap(active_emergency_stop="MANUAL")
    )

    assert "requires emergency stop" not in out
    assert "<button disabled" not in out


def test_tests_page_disables_manual_diagnostic_when_adapter_offline():
    spec = _Spec("jibot", "JIBOT")
    spec.runnable = (_Diag("clamp hardware check", requires_manual=True),)
    snap = _Snap(active_emergency_stop="MANUAL")
    snap.adapter_online = False

    out = render.tests_page(spec, None, "tok", {}, snapshot=snap)

    assert "requires live adapter state" in out
    assert "<button disabled" in out


def test_tests_page_disables_manual_diagnostic_when_state_is_stale():
    spec = _Spec("jibot", "JIBOT")
    spec.runnable = (_Diag("clamp hardware check", requires_manual=True),)
    snap = _Snap(active_emergency_stop="MANUAL")
    snap.adapter_online = True
    snap.state_fresh = False

    out = render.tests_page(spec, None, "tok", {}, snapshot=snap)

    assert "requires fresh adapter state" in out
    assert "<button disabled" in out


def test_tests_page_enables_manual_diagnostic_in_emergency_operating_mode():
    spec = _Spec("jibot", "JIBOT")
    spec.runnable = (_Diag("clamp hardware check", requires_manual=True),)

    out = render.tests_page(
        spec, None, "tok", {}, snapshot=_Snap(operating_mode="EMERGENCY")
    )

    assert "requires emergency stop" not in out
    assert "<button disabled" not in out


def test_tests_page_shows_output_and_stop_while_running():
    spec = _spec_with_diags()
    runner = _Runner(["$ pytest", "collected 1 item", "PASSED"], running=True, label="unit tests")
    out = render.tests_page(spec, runner, "tok", {})
    assert "PASSED" in out
    assert "running" in out
    assert 'action="/adapter/jibot/tests/stop"' in out


def test_tests_page_shows_exit_code_when_done():
    spec = _spec_with_diags()
    runner = _Runner(["[exit 0]"], running=False, returncode=0, label="unit tests")
    out = render.tests_page(spec, runner, "tok", {})
    assert "exit 0" in out
    assert "/tests/stop" not in out


def test_tests_page_idle_when_returncode_none():
    spec = _spec_with_diags()
    runner = _Runner([], running=False, returncode=None, label="unit tests")
    out = render.tests_page(spec, runner, "tok", {})
    assert "idle" in out
    assert "/tests/stop" not in out
    assert "exit None" not in out


def test_adapter_list_shows_position():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]  # _Snap default x=1.0, y=2.0
    out = render.adapter_list_page(rows)
    assert "pos" in out
    assert "1.0" in out and "2.0" in out


def test_adapter_list_renders_camera_row_with_systemctl_metrics():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    cam = _Metrics(active_state="active", enabled_state="enabled",
                   uptime_sec=4242, memory_bytes=777777, restarts=5)
    out = render.adapter_list_page(rows, camera_metrics=cam)
    # camera service exposes its systemctl-derived state on the dashboard row
    assert 'href="/camera"' in out
    assert "4242" in out       # uptime
    assert "777777" in out     # memory (raw bytes)
    assert "restarts" in out   # restart-count label only the camera row carries
    assert '<span class="status success">' in out
    assert "실행 중" in out


def test_adapter_list_marks_stopped_camera():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows, camera_metrics=_Metrics(active_state="inactive"))

    assert '<span class="status danger">' in out
    assert "중지됨" in out


def test_adapter_list_omits_camera_row_without_metrics():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows)
    # no camera_metrics -> no camera row (systemctl markers absent)
    assert "restarts" not in out


def test_adapter_list_renders_camera_service_control_buttons():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    cam = _Metrics(active_state="active", enabled_state="enabled")
    out = render.adapter_list_page(rows, camera_metrics=cam, csrf="tok")
    # the camera service verbs post to the same control endpoint the /camera page uses
    assert 'action="/camera/control"' in out
    assert 'name="csrf_token"' in out
    assert 'value="start"' in out
    assert 'value="restart"' in out
    # posting from the dashboard returns to the dashboard, not /camera
    assert 'name="return_to" value="dashboard"' in out


def test_adapter_list_omits_camera_service_control_without_metrics():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows, csrf="tok")
    # no camera deployed -> no service-control form on the dashboard
    assert 'action="/camera/control"' not in out


def test_adapter_list_renders_flash_message():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows, q={"msg": "restart ok"})
    # control result redirects back to "/" with a flash -> dashboard must show it
    assert "restart ok" in out


def test_error_flash_renders_title_before_description():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows, q={"err": "confirm required"})

    assert out.index('class="error-title">Error') < out.index(
        'class="error-description">confirm required'
    )


def test_adapter_list_updates_data_in_place_not_full_reload():
    # live IO/state panels update by swapping only #content in place — NOT a
    # whole-page meta refresh (which reloads the whole screen / jumps scroll).
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows)
    assert 'http-equiv="refresh"' not in out          # no full-page reload
    assert "getElementById('content')" in out          # partial in-place swap
    assert "window.__amrSetPoll(5)" in out             # default 5s, via reusable poller
    assert "__amrSetPoll(this.value)" in out           # interval input wired
    assert 'type="number"' in out                      # the seconds input


def test_adapter_list_poll_honors_query_and_zero_pauses():
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out3 = render.adapter_list_page(rows, q={"refresh": "3"})
    assert "window.__amrSetPoll(3)" in out3
    out0 = render.adapter_list_page(rows, q={"refresh": "0"})  # 0 = paused
    assert "window.__amrSetPoll(0)" in out0   # script present but no active timer
    assert 'http-equiv="refresh"' not in out0


# --- unified in-place polling across all view pages (interval input + function) ---

def _polls(out: str) -> bool:
    return "window.__amrSetPoll" in out and "getElementById('content')" in out


def test_detail_page_polls_in_place_not_meta_refresh():
    out = render.adapter_detail_page(_Spec("jibot", "JIBOT"), _Metrics(), _Snap(), {})
    assert 'http-equiv="refresh"' not in out
    assert _polls(out)
    assert "__amrSetPoll(this.value)" in out   # interval input


def test_logs_page_polls_in_place_not_meta_refresh():
    out = render.logs_page(_Spec("jibot", "JIBOT"), ["a"], {})
    assert 'http-equiv="refresh"' not in out
    assert _polls(out)


def test_camera_page_polls_in_place():
    out = render.camera_page(_Metrics(), [], "tok", {})
    assert 'http-equiv="refresh"' not in out
    assert _polls(out)


def test_control_page_polls_in_place():
    out = render.control_page(_Spec("jibot", "JIBOT"), _Metrics(), "tok", {})
    assert _polls(out)


def test_tests_page_polls_in_place_not_meta_refresh():
    out = render.tests_page(_Spec("jibot", "JIBOT"), None, "tok", {})
    assert 'http-equiv="refresh"' not in out
    assert _polls(out)


def test_view_pages_honor_refresh_query_and_zero_pauses():
    out = render.camera_page(_Metrics(), [], "tok", {"refresh": "8"})
    assert "window.__amrSetPoll(8)" in out
    out0 = render.logs_page(_Spec("jibot", "JIBOT"), ["a"], {"refresh": "0"})
    assert "window.__amrSetPoll(0)" in out0


def test_interactive_pages_do_not_poll():
    # config/factsheet forms must NOT auto-swap #content, or in-progress input
    # would be wiped.
    sections = [("mqtt_broker", [("host", "str", "127.0.0.1")], [])]
    assert "__amrSetPoll" not in render.config_page(sections, "tok", {})
    assert "__amrSetPoll" not in render.factsheet_page("{}", {})


def test_embedded_jog_survives_poll_via_delegation():
    # The jog is embedded in the polling adapter page; it must bind DOCUMENT-level
    # delegated listeners (guarded once) so the #content poll-swap can re-create
    # the pad without wiping the handlers.
    spec = _jibot_spec_with_manual()
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert "window.__amrJog" in out                  # one-time delegation guard
    assert "addEventListener('mousedown'" in out     # delegated on document, not per-element


def test_config_page_renders_fields():
    sections = [
        ("mqtt_broker", [("host", "str", "127.0.0.1"), ("port", "int", 1883)], []),
        ("video", [], [("stream_topics", ["/a", "/b"])]),
    ]
    out = render.config_page(sections, "tok", {})
    assert 'name="csrf_token"' in out
    assert 'name="section"' in out and 'value="mqtt_broker"' in out
    assert 'name="key"' in out and 'value="host"' in out
    assert 'name="value"' in out
    assert "127.0.0.1" in out
    assert "[mqtt_broker]" in out          # section heading
    assert 'action="/config"' in out
    assert "stream_topics" in out          # read-only list shown
    assert "저장" in out                    # per-field save button


def test_config_page_explains_robot_overrides():
    sections = [("mqtt_broker", [("host", "str", "127.0.0.1")], [])]
    out = render.config_page(sections, "tok", {})
    assert 'href="/robots"' in out
    assert "robot override" in out


def test_robots_page_renders_comment_preserving_editor_and_override_help():
    text = 'robot "R-1" {\n  mqtt_host = "192.0.2.1"\n}\n'
    out = render.robots_page(text, "/tmp/robots.hcl", "tok", {})
    assert 'action="/robots"' in out
    assert 'name="text"' in out
    assert 'robot &quot;R-1&quot; {' in out
    assert "mqtt_host" in out
    assert "config.toml 기본값" in out


def test_config_page_renders_source_metadata_and_badges():
    loc = configio.ConfigLocation(
        path="adaptor/config/config.toml",
        line=42,
        key_path="[mqtt_broker].host",
    )
    row = configio.ConfigScalar(
        section="mqtt_broker",
        key="host",
        kind="str",
        value="127.0.0.1",
        location=loc,
        badges=("robot override", "advanced"),
    )
    sections = [("mqtt_broker", [row], [])]

    out = render.config_page(sections, "tok", {})

    assert "adaptor/config/config.toml:42" in out
    assert "[mqtt_broker].host" in out
    assert "robot override" in out
    assert "advanced" in out
    assert "robots.hcl" in out
    assert "jibot-config.toml" in out


def test_logs_page_renders_lines():
    spec = _Spec("jibot", "JIBOT")
    out = render.logs_page(spec, ["line one", "<b>oops</b>"], {})
    assert "line one" in out
    assert "&lt;b&gt;oops&lt;/b&gt;" in out   # log lines escaped
    assert 'href="/adapter/jibot"' in out     # dashboard back-link (refresh is now the poll input)


def test_camera_page_renders_service_and_topics():
    rows = [
        (
            "/camera/cam2/image_raw",
            "http://192.168.3.222:8080/stream?topic=/camera/cam2/image_raw",
            "http://127.0.0.1:8080/snapshot?topic=/camera/cam2/image_raw&quality=40",
        )
    ]

    out = render.camera_page(_Metrics(), rows, "tok", {})

    assert "amr-camera" in out
    assert "active" in out
    assert "/camera/cam2/image_raw" in out
    assert "stream" in out
    assert "snapshot" in out
    assert 'action="/camera/control"' in out
    assert 'name="csrf_token"' in out
    assert '<span class="status success">' in out
    assert "실행 중" in out


def test_camera_page_marks_stopped_service():
    out = render.camera_page(_Metrics(active_state="inactive"), [], "tok", {})

    assert '<span class="status danger">' in out
    assert "중지됨" in out


def test_camera_page_handles_disabled_config():
    out = render.camera_page(_Metrics(active_state="inactive"), [], "tok", {}, enabled=False)

    assert "camera disabled" in out
    assert "stream" not in out


def test_runs_urobot_only_for_jibot_specs():
    assert render._runs_urobot(_Spec("jibot", "JIBOT")) is True
    assert render._runs_urobot(_Spec("jibot:ROBOT-A", "JIBOT A")) is True
    assert render._runs_urobot(_Spec("hex", "Hexplorer")) is False


def test_motor_label_states():
    assert render._motor_label(_Snap(active_emergency_stop=None)) == "알 수 없음"
    assert render._motor_label(_Snap(active_emergency_stop="NONE")) == "정상 (활성)"
    # v2 motor-off (AUTOACK) 와 v3 motor-off (MANUAL) 둘 다 "정지"
    assert render._motor_label(_Snap(active_emergency_stop="AUTOACK")) == "정지 (비활성)"
    assert render._motor_label(_Snap(active_emergency_stop="MANUAL")) == "정지 (비활성)"


def test_detail_shows_motor_row_for_jibot():
    spec = _Spec("jibot", "JIBOT")
    out = render.adapter_detail_page(
        spec, _Metrics(), _Snap(active_emergency_stop="MANUAL"), {}
    )
    assert "모터(추정)" in out
    assert "정지 (비활성)" in out


def test_detail_omits_motor_row_for_non_jibot():
    spec = _Spec("hex", "Hexplorer")
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {})
    assert "모터(추정)" not in out


def test_list_summary_includes_motor_for_jibot():
    spec = _Spec("jibot", "JIBOT")
    rows = [(spec, _Metrics(), _Snap(active_emergency_stop="MANUAL"))]
    out = render.adapter_list_page(rows)
    assert "모터" in out
    assert "정지 (비활성)" in out


def test_control_forms_shows_urobot_for_jibot_when_enabled():
    out = render._control_forms(
        _Spec("jibot", "JIBOT"), "tok", urobot_restart_enabled=True
    )
    assert "restart urobot" in out
    assert 'action="/adapter/jibot/urobot/restart"' in out
    assert "confirm" in out


def test_control_forms_hides_urobot_for_non_jibot():
    out = render._control_forms(
        _Spec("hex", "Hexplorer"), "tok", urobot_restart_enabled=True
    )
    assert "restart urobot" not in out


def test_control_forms_hides_urobot_when_disabled():
    out = render._control_forms(
        _Spec("jibot", "JIBOT"), "tok", urobot_restart_enabled=False
    )
    assert "restart urobot" not in out


def test_detail_renders_urobot_button_when_enabled():
    out = render.adapter_detail_page(
        _Spec("jibot", "JIBOT"), _Metrics(), _Snap(), {}, csrf="tok",
        urobot_restart_enabled=True,
    )
    assert "restart urobot" in out


def test_motor_label_prefers_authoritative_motor_state():
    # eStop says running, but the authoritative fetched flag says stopped -> stopped wins
    s = _Snap(active_emergency_stop="NONE")
    s.motor_state = "stopped"
    assert render._motor_label(s) == "정지 (비활성)"
    s2 = _Snap(active_emergency_stop="MANUAL")
    s2.motor_state = "running"
    assert render._motor_label(s2) == "정상 (활성)"


def test_detail_motor_row_authoritative_drops_estimate_label():
    s = _Snap(active_emergency_stop="NONE")
    s.motor_state = "running"
    out = render.adapter_detail_page(_Spec("jibot", "JIBOT"), _Metrics(), s, {})
    assert '<span class="label">모터</span>' in out  # authoritative card label (no 추정)
    assert "모터(추정)" not in out
    assert "정상 (활성)" in out


def test_detail_motor_row_falls_back_to_estimate_without_motor_state():
    out = render.adapter_detail_page(
        _Spec("jibot", "JIBOT"), _Metrics(), _Snap(active_emergency_stop="MANUAL"), {}
    )
    assert "모터(추정)" in out          # inferred title retained when no fetched flag


# ---------------------------------------------------------------------------
# Task 9: manual_page + _manual_enabled
# ---------------------------------------------------------------------------

def _jibot_spec_with_manual():
    """JIBOT spec with manualDrive/manualMove/manualStop/cancelOrder instant actions."""
    return _Spec(
        "jibot",
        "JIBOT",
        instant_actions=(
            _Action("manualDrive", "Manual Drive", True),
            _Action("manualMove", "Manual Move", True),
            _Action("manualStop", "Manual Stop", False),
            _Action("cancelOrder", "Cancel Order", True),
            _Action("enableMotor", "Enable Motor", False),
        ),
    )


def _hexplorer_spec():
    """Non-JIBOT spec without manualDrive."""
    return _Spec("hex", "Hexplorer", instant_actions=())


def test_jog_embedded_in_adapter_page():
    # The manual jog is embedded in the adapter console Drive group (no separate
    # /manual page). The D-pad arrows are positioned by direction.
    spec = _jibot_spec_with_manual()
    html = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert "Drive (수동)" in html          # Drive group heading
    assert "jog-pad" in html               # D-pad grid
    for d in ("up", "down", "left", "right"):
        assert f'data-dir="{d}"' in html   # each arrow tagged by direction
    assert "jog-stop" in html              # center STOP
    assert "manualDrive" in html           # heartbeat posts manualDrive (jog JS)
    assert "manualMove" in html            # move-distance form
    assert 'name="armed"' in html          # arm toggle
    assert "/manual" in html               # POST heartbeat endpoint (jog JS)
    assert "ArrowUp" in html               # keyboard arrows in inline JS


def test_manual_move_renders_advanced_route_step_fields():
    spec = _jibot_spec_with_manual()
    html = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    for name in (
        "flag",
        "io",
        "obs_avoid_dist",
        "side_avoid_dist",
        "use_io",
        "note",
        "run_mode",
    ):
        assert f'name="{name}"' in html
    assert 'name="flag" value="1"' in html
    assert 'name="io" value="1"' in html
    assert 'name="obs_avoid_dist" value="1000"' in html
    assert 'name="side_avoid_dist" value="50"' in html
    assert 'name="note" value="1"' in html
    assert 'value="scheduler" selected' in html
    assert 'value="set_routes"' in html


def test_manual_enabled_false_for_non_jibot():
    assert render._manual_enabled(_hexplorer_spec()) is False


def test_manual_enabled_true_for_jibot_spec():
    assert render._manual_enabled(_jibot_spec_with_manual()) is True


def test_jog_js_uses_data_key_selector():
    """JS must read key from .manual[data-key], NOT document.body."""
    spec = _jibot_spec_with_manual()
    html = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert "querySelector('.manual')" in html or 'querySelector(".manual")' in html
    assert "document.body.getAttribute" not in html


def test_jog_sends_both_axes_with_config_magnitudes():
    """Each arrow must send BOTH trans and rot (unused axis = 0) using the
    configured jog magnitudes. Sending only the pressed axis let the adapter
    fill the other one with its config default, which rotated/translated the
    robot the wrong way (up->turn, left->forward)."""
    spec = _jibot_spec_with_manual()
    html = render.adapter_detail_page(
        spec, _Metrics(), _Snap(), {}, csrf="tok", drive_trans=200.0, drive_rot=30.0
    )
    # magnitudes rendered into the pad for the JS to read
    assert 'data-trans="200.0"' in html
    assert 'data-rot="30.0"' in html
    # JS sends both axes, with 0 on the unused one
    assert "up:{trans:TRANS,rot:0}" in html
    assert "down:{trans:-TRANS,rot:0}" in html
    assert "left:{trans:0,rot:ROT}" in html
    assert "right:{trans:0,rot:-ROT}" in html
    # the old single-axis mapping (which leaked the adapter default) is gone
    assert "up:{trans:1}" not in html


def test_jog_sends_adjustable_speed_with_config_default():
    spec = _jibot_spec_with_manual()
    html = render.adapter_detail_page(
        spec,
        _Metrics(),
        _Snap(),
        {},
        csrf="tok",
        drive_trans=200.0,
        drive_rot=30.0,
        drive_speed=140.0,
    )
    assert 'data-speed="140.0"' in html
    assert 'name="speed" value="140.0"' in html
    assert "var SPEED=parseFloat(speedEl&&speedEl.value)||DEFAULT_SPEED;" in html
    assert "payload={trans:m.trans, rot:m.rot, speed:SPEED}" in html


def test_jog_shows_known_speed_range_hint():
    spec = _jibot_spec_with_manual()
    html = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert "전진/후진 0-500" in html
    assert "회전 0-90" in html
    assert "UmDrive speed 기본 200" in html
    assert "별도 최대값 미확인" in html


def test_control_page_embeds_jog_for_jibot():
    spec = _jibot_spec_with_manual()
    out = render.control_page(spec, _Metrics(), "tok", {})
    assert "jog-pad" in out                          # jog embedded in Drive group
    assert 'href="/adapter/jibot/manual"' not in out  # no standalone manual-page link


def test_control_page_no_manual_link_for_non_jibot():
    spec = _hexplorer_spec()
    out = render.control_page(spec, _Metrics(), "tok", {})
    assert "/adapter/hex/manual" not in out


def test_extension_module_actions_never_render_on_the_adapter_page():
    # 어댑터 페이지는 로봇 자체 제어만 담는다. extension 액션은 /actions 소관이라
    # 전용 그룹으로도, Vehicle actions 로도 새지 않아야 한다.
    actions = (
        _Action("clampOn", "Servo ON", True),
        _Action("clamp", "Clamp", True),
        _Action("clampMoveTo", "Clamp move to", True),
        _Action("startPause", "Pause", True),  # 로봇 내장 액션은 그대로 남는다
    )
    spec = _Spec(
        "jibot", "JIBOT", instant_actions=actions,
        action_modules=(ActionModuleView(
            "extensions.clamp", "Clamp", actions[:3],
            '<section class="command-group"><div class="command-head"><h2>Clamp</h2></div>'
            '<div class="command-body"><div class="cmd-wrap">'
            '<form class="action-card"><input name="action_type" value="clampOn"></form>'
            '</div></div></section>',
        ),),
    )
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert "<h2>Clamp</h2>" not in out             # 모듈 패널 없음
    assert 'value="clampOn"' not in out            # Vehicle actions 로도 안 샘
    assert 'value="clampMoveTo"' not in out
    assert "<h2>Vehicle actions</h2>" in out       # 내장 액션 그룹은 유지
    assert 'value="startPause"' in out


def test_recipes_never_render_on_the_adapter_page():
    # recipe 는 instant_actions 에 섞여 들어오지만 파라미터 칸이 없는 맨 버튼으로
    # 노출되면 눌러도 'missing recipe parameter' 로 실패한다. /actions 전용이다.
    from core.registry import RecipeView

    actions = (
        _Action("elevatorUp", "Elevator up", True),
        _Action("startPause", "Pause", True),
    )
    spec = _Spec(
        "jibot", "JIBOT", instant_actions=actions,
        recipes=(RecipeView(
            action_type="elevatorUp", label="Elevator up", motion=True,
            variables=("targetMapId",), step_count=6, cleanup_count=2,
        ),),
    )
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert 'value="elevatorUp"' not in out
    assert 'value="startPause"' in out


# ---------------------------------------------------------------------------
# EZIO/PIO 라이브 상태 패널 — extension 화면(/actions)이 extension 설정을 따라 낸다
# ---------------------------------------------------------------------------

def _io(**ez):
    from types import SimpleNamespace as NS
    ezio = NS(configured=True, ip="10.8.8.87", port=3002, connected=True,
              board="EZI-IO X", inputs=[1, 0] + [0] * 14, inputs_updated_at=100.0,
              outputs=[0] * 16, outputs_updated_at=99.0, error="")
    for k, v in ez.items():
        setattr(ezio, k, v)
    pio = NS(configured=True, port="/dev/ttyUSB0", baudrate=19200, connected=False,
             inputs={"1": "on"}, inputs_updated_at=50.0,
             outputs={"2": "on"}, outputs_updated_at=51.0, error="")
    return NS(updated_at=100.0, ezio=ezio, pio=pio)


def _io_spec(*modules):
    """discover 된 모듈만 담은 Actions 화면용 spec.

    모듈 이름은 실제 discovery 와 같은 dotted path 다 — 상태 패널이 이름의 끝
    조각으로 io 속성을 찾기 때문에 여기서 규약이 지켜지는지도 같이 확인된다.
    """
    from types import SimpleNamespace as NS

    default = {
        "extensions.ezio": ("EZIO", ("ezioWriteOut",)),
        "extensions.pio": ("PIO", ("pioWriteOut",)),
    }
    views = []
    for name in modules:
        if isinstance(name, tuple):
            name, action_types = name
            title = default[name][0]
        else:
            title, action_types = default[name]
        views.append(NS(
            module=name, title=title, panel_template=None,
            actions=tuple(
                NS(action_type=t, label=t, motion=False, parameters=())
                for t in action_types
            ),
        ))
    return NS(key="jibot", display_name="JIBOT",
              action_modules=tuple(views), recipes=())


def test_actions_page_renders_ezio_pio_status_panels():
    spec = _io_spec("extensions.ezio", "extensions.pio")
    out = render.actions_page(spec, "tok", {}, io=_io())
    assert "EZIO" in out
    assert "PIO" in out
    assert "10.8.8.87" in out          # ezio ip
    assert "/dev/ttyUSB0" in out       # pio port
    assert "EZI-IO X" in out           # board


def test_actions_page_pio_renders_in8_out8_circles():
    # PIO shows 8 input + 8 output pins as circles (● on / ○ off / ◌ unknown).
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=_io())
    assert 'title="in 1: on"' in out
    assert 'class="status success io-on"' in out
    assert 'title="out 2: on"' in out
    assert "●" in out                              # at least one ON circle
    assert 'title="in 8: ' in out                  # 8th input pin rendered
    assert 'title="out 8: ' in out                 # 8th output pin rendered
    assert "◌" in out                              # unknown output pins as ◌


def test_actions_page_pio_out_unknown_when_never_commanded():
    io = _io()
    io.pio.outputs = None                          # nothing commanded yet
    io.pio.outputs_updated_at = None
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=io)
    assert 'title="out 1: ' in out
    assert 'title="out 8: ' in out
    assert "◌" in out


def test_actions_page_pio_skeleton_without_io():
    # 모듈이 discover 됐으면 io.json 이 아직 없어도 골격은 낸다("데이터 없음").
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {})
    assert "PIO" in out
    assert "데이터 없음" in out
    assert "◌" in out
    assert "/dev/ttyUSB0" not in out   # no port detail without data


def test_actions_page_status_panel_follows_extension_config():
    # ezio 를 [[action_modules]] enabled=false 로 끄면 discovery 에서 빠지고,
    # 따라서 EZIO 상태 패널도 사라진다. jibot 인지 여부와 무관하다.
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=_io())
    assert "PIO" in out
    assert "EZIO" not in out
    assert "10.8.8.87" not in out      # ezio ip 도 같이 사라진다


def test_actions_page_no_status_panel_for_module_without_io_state():
    # IO 상태가 없는 모듈(clamp)은 상태 패널 없이 액션 카드만 낸다.
    from types import SimpleNamespace as NS

    spec = NS(key="jibot", display_name="JIBOT", recipes=(), action_modules=(NS(
        module="extensions.clamp", title="Clamp", panel_template=None,
        actions=(NS(action_type="clampOn", label="Servo ON", motion=False,
                    parameters=()),),
    ),))
    out = render.actions_page(spec, "tok", {}, io=_io())
    assert "clampOn" in out
    assert "데이터 없음" not in out
    assert "/io/out" not in out


def test_actions_page_out_badges_are_clickable_toggle_forms():
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=_io())
    assert 'action="/adapter/jibot/io/out"' in out
    assert 'name="board" value="pio"' in out
    assert 'name="index" value="2"' in out      # pio out pin 2
    assert 'name="state" value="off"' in out    # pin2 is on -> toggle to off
    assert 'name="state" value="on"' in out     # off/unknown pins -> toggle to on
    assert 'name="csrf_token" value="tok"' in out


def test_actions_page_ezio_out_badges_are_clickable():
    out = render.actions_page(_io_spec("extensions.ezio"), "tok", {}, io=_io())
    assert 'name="board" value="ezio"' in out   # ezio out pins clickable too
    assert 'name="index" value="0"' in out      # ezio is 0-indexed


def test_out_badges_read_only_when_write_action_disabled():
    # [[actions]] 로 pioWriteOut 을 끄면 모듈은 남지만 write 액션이 없다.
    # 그러면 out 배지는 표시 전용이어야 한다 — 누를 수 있는데 서버가 거절하는
    # 상태를 만들지 않는다.
    spec = _io_spec(("extensions.pio", ("pioReadIn",)))
    out = render.actions_page(spec, "tok", {}, io=_io())
    assert 'title="out 2: on"' in out           # 상태는 그대로 보인다
    assert "/io/out" not in out                 # 토글 폼은 없다


def test_actions_page_in_badges_are_not_clickable():
    # IN badges stay display-only spans, never buttons/forms.
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=_io())
    assert 'title="in 1: on"' in out            # in pin still a display badge
    assert "/io/in" not in out


def test_actions_page_ezio_input_on_badges_are_high_contrast():
    out = render.actions_page(_io_spec("extensions.ezio"), "tok", {}, io=_io())
    assert '<span class="status success io-on">0:ON</span>' in out
    assert ".status.io-on" in out


def test_actions_page_out_badges_return_to_the_actions_page():
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=_io())
    assert 'name="return_to" value="/adapter/jibot/actions"' in out


def test_actions_page_io_panel_unconfigured_shows_no_data_not_port():
    # 모듈은 켜져 있는데 보드가 미구성이면 '데이터 없음'만 낸다.
    io = _io()
    io.ezio.configured = False
    io.pio.configured = False
    spec = _io_spec("extensions.ezio", "extensions.pio")
    out = render.actions_page(spec, "tok", {}, io=io)
    assert "PIO" in out                # header still shown
    assert "데이터 없음" in out          # no-data indicator
    assert "EZI-IO X" not in out       # no board detail when unconfigured
    assert "/dev/ttyUSB0" not in out   # no port detail when unconfigured


def test_actions_page_pio_disconnected_with_inputs_not_error():
    # pioScenario finally-disconnect: connected=false + inputs present is normal.
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=_io())
    assert 'title="in 1: on"' in out
    assert "●" in out
    # CRITICAL: disconnected is NOT an error.
    body = out.split("</style>", 1)[1]   # drop inlined CSS (.status.danger etc.)
    assert 'class="err"' not in body            # no error paragraph
    assert 'class="status danger"' not in body  # disconnect pill is not danger
    assert "idle" in body                       # neutral/idle connection label
    assert 'class="status neutral"' in body     # rendered with the neutral style


def test_actions_page_pio_error_shows_error_styling():
    # An actual `error` field (not a mere disconnect) DOES get error styling.
    io = _io()
    io.pio.error = "serial open failed"
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=io)
    assert 'class="err error-notice"' in out
    assert out.index('class="error-title">PIO Error') < out.index(
        'class="error-description">serial open failed'
    )
    assert "serial open failed" in out


def test_adapter_list_page_has_no_io_panel():
    # IO 상태는 extension 화면 소관이다. 대시보드는 어댑터 목록만 낸다.
    rows = [(_Spec("jibot", "JIBOT"), _Metrics(), _Snap())]
    out = render.adapter_list_page(rows, csrf="tok")
    assert "PIO" not in out
    assert "EZIO" not in out
    assert "/io/out" not in out


def test_adapter_detail_page_has_no_io_panel():
    spec = _Spec("jibot", "JIBOT")
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {}, csrf="tok")
    assert "PIO" not in out
    assert "EZIO" not in out
    assert "/io/out" not in out


def _spec_with_recipes():
    from core.action_registry import ActionParameterSpec
    from core.registry import RecipeView
    from types import SimpleNamespace

    action = SimpleNamespace(
        action_type="pioWriteOut",
        label="PIO write",
        motion=False,
        parameters=(
            ActionParameterSpec(
                "index", required=True, input_type="number", placeholder="1-8",
            ),
            ActionParameterSpec("state", required=True),
        ),
    )
    module = SimpleNamespace(title="PIO", actions=(action,), panel_template=None)
    return SimpleNamespace(
        key="jibot",
        display_name="JIBOT",
        action_modules=(module,),
        recipes=(
            RecipeView(
                action_type="elevatorUp",
                label="Elevator up",
                motion=True,
                variables=("targetMapId", "moveSpeed"),
                step_count=6,
                cleanup_count=2,
            ),
        ),
    )


def test_actions_page_lists_extensions_and_recipes():
    out = render.actions_page(_spec_with_recipes(), "tok", {})
    assert "Extensions" in out
    assert "Recipes" in out
    assert "pioWriteOut" in out
    assert "elevatorUp" in out


def test_actions_page_builds_named_inputs_for_recipe_variables():
    """recipe 변수는 정의에서 뽑히므로 운영자가 이름을 외울 필요가 없다."""
    out = render.actions_page(_spec_with_recipes(), "tok", {})
    assert 'name="targetMapId"' in out
    assert 'name="moveSpeed"' in out


def test_actions_page_builds_only_declared_extension_parameters():
    """extension 스키마에 선언된 파라미터 이름만 표시한다."""
    out = render.actions_page(_spec_with_recipes(), "tok", {})
    assert '>index<' in out
    assert 'name="index"' in out
    assert '>state<' in out
    assert 'name="state"' in out
    assert 'name="__k0"' not in out


def test_actions_page_marks_motion_recipes():
    out = render.actions_page(_spec_with_recipes(), "tok", {})
    assert "motion" in out


def test_actions_page_posts_to_the_existing_action_endpoint():
    """새 전송 경로를 만들지 않는다 — confirm/감사/CSRF가 그대로 걸려야 한다."""
    out = render.actions_page(_spec_with_recipes(), "tok", {})
    assert 'action="/adapter/jibot/action"' in out
    assert "tok" in out


def test_actions_page_empty_recipes_points_at_the_example():
    from types import SimpleNamespace

    spec = _spec_with_recipes()
    spec = SimpleNamespace(**{**spec.__dict__, "recipes": ()})
    out = render.actions_page(spec, "tok", {})
    assert "recipes.hcl.example" in out


def test_top_nav_offers_actions():
    """액션 페이지 진입점이 어댑터 상세 헤더 버튼 하나뿐이라 찾기 어려웠다."""
    out = render.page("anything", "<p>body</p>")
    assert '<a class="nav-link" href="/actions"' in out
    assert ">Actions</a>" in out


def test_actions_page_marks_the_actions_nav_current():
    out = render.actions_page(_spec_with_recipes(), "tok", {})
    assert '<a class="nav-link" href="/actions" aria-current="page">' in out


def test_actions_page_gates_destructive_actions_with_confirm():
    # 이 게이트는 panel.html 의 required 속성으로만 있었다. 패널이 어댑터
    # 페이지에서 사라지면 /actions 가 유일한 실행 창구이므로 여기서 낸다.
    out = render.actions_page(_io_spec("extensions.pio"), "tok", {}, io=_io())
    write = out.split('value="pioWriteOut"', 1)[1].split("</form>", 1)[0]
    assert 'name="confirm"' in write
    assert "required" in write


def test_actions_page_does_not_gate_ordinary_actions():
    spec = _io_spec(("extensions.pio", ("pioReadIn",)))
    out = render.actions_page(spec, "tok", {}, io=_io())
    read = out.split('value="pioReadIn"', 1)[1].split("</form>", 1)[0]
    assert 'name="confirm"' not in read


def test_actions_index_page_links_every_adapter():
    """어댑터가 여럿이면 전역 /actions는 로봇을 고르게 한다."""
    rows = [_Spec("jibot", "JIBOT"), _Spec("jibot2", "JIBOT 2")]
    out = render.actions_index_page(rows, {})
    assert 'href="/adapter/jibot/actions"' in out
    assert 'href="/adapter/jibot2/actions"' in out
    assert "JIBOT 2" in out


def test_actions_index_page_without_adapters_says_so():
    out = render.actions_index_page([], {})
    assert "어댑터가 없습니다" in out


def _anchor_spec():
    """POST -> 302 -> GET이라 매번 새 문서를 받아 스크롤이 맨 위로 간다.

    return_to의 fragment는 리다이렉트를 통과하도록 이미 구현돼 있었지만
    (test_action_return_to_replaces_stale_flash_query_and_preserves_fragment)
    아무도 앵커를 붙이지 않아 쓰이지 않고 있었다.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        key="jibot",
        display_name="JIBOT",
        action_modules=(
            SimpleNamespace(
                title="PIO",
                actions=(
                    SimpleNamespace(
                        action_type="pioInit", label="PIO init", motion=False
                    ),
                ),
                panel_template=None,
            ),
        ),
        recipes=(),
    )


def test_run_form_carries_an_anchor_id():
    assert 'id="act-pioInit"' in render.actions_page(_anchor_spec(), "tok", {})


def test_run_form_return_to_targets_that_anchor():
    assert "actions#act-pioInit" in render.actions_page(_anchor_spec(), "tok", {})


def test_acted_card_is_marked():
    """어느 카드를 눌렀는지 보이게 한다."""
    out = render.actions_page(_anchor_spec(), "tok", {"act": "pioInit"})
    assert "run-card acted" in out


def test_other_cards_are_not_marked():
    out = render.actions_page(_anchor_spec(), "tok", {"act": "somethingElse"})
    assert "run-card acted" not in out


def test_no_act_query_marks_nothing():
    assert "run-card acted" not in render.actions_page(_anchor_spec(), "tok", {})


def _choice_spec():
    """on/off처럼 값이 두 개뿐인 파라미터는 자유 입력이면 오타로 실패한다.

    기존 PIO 패널은 이미 <select>로 준다(extensions/pio/panel.html). Actions
    페이지만 텍스트 박스여서 운영자가 "on"을 정확히 타이핑해야 했다.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        key="jibot",
        display_name="JIBOT",
        action_modules=(
            SimpleNamespace(
                title="PIO",
                actions=(
                    SimpleNamespace(
                        action_type="pioWriteOut",
                        label="PIO write",
                        motion=False,
                        parameters=(
                            SimpleNamespace(
                                name="state", required=True, input_type="text",
                                placeholder="on 또는 off", value_type="string",
                                choices=("on", "off"),
                            ),
                            SimpleNamespace(
                                name="index", required=True, input_type="number",
                                placeholder="1-8", value_type="string", choices=(),
                            ),
                        ),
                    ),
                ),
                panel_template=None,
            ),
        ),
        recipes=(),
    )


def test_enumerated_parameter_renders_a_select():
    out = render.actions_page(_choice_spec(), "tok", {})
    assert '<select class="ap-val" name="state"' in out
    assert '<option value="on">on</option>' in out
    assert '<option value="off">off</option>' in out


def test_free_parameter_still_renders_an_input():
    out = render.actions_page(_choice_spec(), "tok", {})
    assert '<input class="ap-val" type="number" name="index"' in out


def test_remembered_value_prefills_the_input():
    """실행 후 리다이렉트로 폼이 새로 그려지면 입력값이 사라진다."""
    out = render.actions_page(
        _choice_spec(), "tok", {}, remembered={"pioWriteOut": {"index": "3"}}
    )
    assert 'name="index"' in out
    assert 'value="3"' in out


def test_remembered_value_selects_the_matching_option():
    out = render.actions_page(
        _choice_spec(), "tok", {}, remembered={"pioWriteOut": {"state": "off"}}
    )
    assert '<option value="off" selected>off</option>' in out


def test_nothing_remembered_leaves_fields_empty():
    out = render.actions_page(_choice_spec(), "tok", {})
    assert 'value="3"' not in out
    # 첫 option이 기본 선택이므로 selected 표시가 붙으면 안 된다.
    assert "<option value=" in out
    assert " selected>" not in out


def _snapshot_with_instant_actions(count):
    from types import SimpleNamespace

    return SimpleNamespace(
        instant_action_states=[
            {
                "actionId": f"ia-{index}",
                "actionType": f"probe{index}",
                "actionStatus": "FINISHED",
            }
            for index in range(count)
        ]
    )


def test_instant_action_cards_show_only_the_most_recent_outcomes():
    """터미널 instant action state는 이제 보존되므로 카드가 쌓인다.

    어댑터는 최대 50건까지 들고 있을 수 있는데, 패널이 그걸 전부 그리면
    텔레메트리 카드가 화면을 밀어낸다. 최근 것만 보여준다.
    """
    out = render._instant_action_cards(_snapshot_with_instant_actions(25))

    assert out.count("action probe") == render.MAX_INSTANT_ACTION_CARDS
    assert "action probe24" in out
    assert "action probe14" not in out


def test_instant_action_cards_render_all_when_under_the_limit():
    out = render._instant_action_cards(_snapshot_with_instant_actions(3))

    assert out.count("action probe") == 3
    assert "action probe0" in out


def _progress_snap(**over):
    snap = _Snap(
        order_id="ORD-77",
        order_update_id=2,
        node_states=(
            {"nodeId": "N-002", "sequenceId": 2, "released": True},
            {"nodeId": "N-004", "sequenceId": 4, "released": False,
             "nodePosition": {"x": 3.0, "y": 4.0, "theta": 1.5, "mapId": "lab2m"}},
        ),
        edge_states=({"edgeId": "E-003", "sequenceId": 3, "released": True},),
        action_states=(
            {"actionId": "a1", "actionType": "pick", "actionStatus": "FINISHED"},
            {"actionId": "a2", "actionType": "drop", "actionStatus": "RUNNING"},
            {"actionId": "a3", "actionType": "charge", "actionStatus": "WAITING"},
        ),
    )
    for key, value in over.items():
        setattr(snap, key, value)
    return snap


def test_progress_page_lists_every_order_action_with_status():
    out = render.order_progress_page(_Spec("jibot", "JIBOT"), _progress_snap(), {})

    # 끝난 액션도 목록에 남아 있어야 "하나씩 클리어"가 보인다.
    assert "pick" in out and "drop" in out and "charge" in out
    assert "is-done" in out      # FINISHED
    assert "is-active" in out    # RUNNING
    assert 'ck-idx">1<' in out  # 순번이 붙는다


def test_progress_page_counts_cleared_actions():
    out = render.order_progress_page(_Spec("jibot", "JIBOT"), _progress_snap(), {})
    assert "Actions cleared" in out
    assert "<small> / 3</small>" in out


def test_progress_page_marks_failed_action():
    snap = _progress_snap(
        action_states=({"actionId": "a1", "actionType": "pick", "actionStatus": "FAILED",
                        "actionResult": "gripper jam"},)
    )
    out = render.order_progress_page(_Spec("jibot", "JIBOT"), snap, {})
    assert "is-failed" in out
    assert "gripper jam" in out


def test_progress_page_orders_path_by_sequence_id():
    out = render.order_progress_page(_Spec("jibot", "JIBOT"), _progress_snap(), {})
    assert out.index("N-002") < out.index("E-003") < out.index("N-004")


def test_progress_page_shows_stage_and_active_step():
    snap = _progress_snap()
    snap.working_state = "ACTING"
    snap.working_state_detail = "LOADING"
    snap.active_action_type = "clampMoveTo"
    snap.active_step_action_type = "pioWriteOut"
    out = render.order_progress_page(_Spec("jibot", "JIBOT"), snap, {})
    assert "ACTING" in out and "LOADING" in out
    assert "clampMoveTo" in out and "pioWriteOut" in out


def test_progress_page_empty_order_says_so():
    out = render.order_progress_page(_Spec("jibot", "JIBOT"), _Snap(), {})
    assert "진행 중인 오더 액션이 없음" in out
    assert "남은 경로가 없음" in out


def test_progress_page_instant_actions_are_not_a_checklist():
    # instantActionStates 는 오더 진행이 아니라 이력이라 번호 붙은 체크리스트로
    # 렌더하면 안 된다.
    snap = _progress_snap(action_states=(), node_states=(), edge_states=())
    snap.instant_action_states = (
        {"actionId": "i1", "actionType": "enableMotor", "actionStatus": "FINISHED"},
    )
    out = render.order_progress_page(_Spec("jibot", "JIBOT"), snap, {})
    assert "enableMotor" in out
    assert "telemetry-item" in out
    assert "checklist" not in out.split('id="content">', 1)[1]


def test_progress_page_without_snapshot_is_not_an_error():
    out = render.order_progress_page(_Spec("hex", "Hexplorer", monitor_kind="none"), None, {})
    assert "라이브 상태를 제공하지 않음" in out


def test_detail_page_links_progress():
    out = render.adapter_detail_page(_Spec("jibot", "JIBOT"), _Metrics(), _Snap(), {})
    assert "/adapter/jibot/progress" in out
