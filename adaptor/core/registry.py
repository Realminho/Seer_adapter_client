"""Static description of the adaptors the WebUi manages.

Each :class:`AdaptorSpec` ties together the systemd unit, the working
directory / launch script, the MQTT identity used to monitor and command it,
and the per-adaptor lists of test suites, diagnostics, and instant actions.

JIBOT identity comes from ``config/robots.hcl``. The default host service is
always ``amr-adaptor.service``; one fleet entry produces one spec and multiple
entries produce multiple specs managed by the same service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from core.action_registry import ActionParameterSpec
from core.systemd import EdgeAgentUnit, discover_edge_agent_units

ADAPTER_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Diagnostic:
    """A test suite or diagnostic command runnable from the Tests view."""

    label: str
    # argv executed with the adapter directory as cwd. ``{py}`` is substituted
    # with the interpreter running the WebUi so children share the same venv.
    argv: Tuple[str, ...]
    note: str = ""
    requires_manual: bool = False


@dataclass(frozen=True)
class InstantAction:
    """A VDA5050 instant action exposed as a button in the Control view."""

    action_type: str
    label: str
    # Motion-affecting actions require an explicit confirmation keystroke.
    motion: bool = False
    parameters: Tuple[ActionParameterSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RecipeView:
    """recipes.hcl 항목 하나를 WebUI가 쓸 형태로 담는다."""

    action_type: str
    label: str
    motion: bool
    variables: Tuple[str, ...]
    step_count: int
    cleanup_count: int


@dataclass(frozen=True)
class ActionModuleView:
    module: str
    title: str
    actions: Tuple[InstantAction, ...]
    panel_template: Optional[str] = None


@dataclass(frozen=True)
class AdaptorSpec:
    key: str
    display_name: str
    unit: str
    workdir: Path
    exec_script: str
    manufacturer: str
    serial: str
    vda_full_version: str
    topic_prefix: str
    mqtt_host: str = ""
    mqtt_port: int = 0
    vehicle_host: str = ""
    vehicle_port: int = 0
    runtime_kind: str = "systemd"
    monitor_kind: str = "vda5050"
    test_suites: Tuple[Diagnostic, ...] = field(default_factory=tuple)
    diagnostics: Tuple[Diagnostic, ...] = field(default_factory=tuple)
    instant_actions: Tuple[InstantAction, ...] = field(default_factory=tuple)
    action_modules: Tuple[ActionModuleView, ...] = field(default_factory=tuple)
    #: recipes.hcl이 정의한 액션. 모듈이 아니므로 action_modules와 따로 둔다 —
    #: Actions 페이지가 두 묶음을 구분해 렌더하고, 각 recipe의 변수 이름으로
    #: 실행 폼을 만든다.
    recipes: Tuple["RecipeView", ...] = field(default_factory=tuple)

    @property
    def runnable(self) -> Tuple[Diagnostic, ...]:
        return self.test_suites + self.diagnostics


def _topic_prefix(vda_interface: str, vda_version: str, serial: str) -> str:
    """Mirror ``utils/mqtt_client.MQTTClient`` topic prefix construction."""
    return f"{vda_interface}/{vda_version}/{serial}"


# JIBOT diagnostics/tests/instant-actions are identical for every robot.
_JIBOT_TEST_SUITES = (
    Diagnostic(
        "unittest: jibot v3 order",
        ("{py}", "-m", "unittest", "tests.test_adapter_jibot_v3_order", "-v"),
    ),
    Diagnostic(
        "unittest: video streamer",
        ("{py}", "-m", "unittest", "tests.test_video", "-v"),
    ),
)


def _test_id(name: str) -> str:
    return f"tests.test_adapter_jibot_v3_order.AdapterV3OrderTest.{name}"


def _unittest_method_diag(label: str, method: str, note: str) -> Diagnostic:
    """A fake-hardware diagnostic that runs a single test method on its own."""
    return Diagnostic(
        label,
        ("{py}", "-m", "unittest", _test_id(method), "-v"),
        note,
        requires_manual=True,
    )


def _jibot_hardware_diagnostics(config) -> Tuple[Diagnostic, ...]:
    sensor_pins = ",".join(str(pin) for pin in config.ezi_config.tray_slot_pin)
    return (
        _unittest_method_diag(
            "unittest: clamp - factsheet contents",
            "test_factsheet_contents",
            "Factsheet advertises clamp/unclamp/clampTeach/clampOn/clampOff actions.",
        ),
        _unittest_method_diag(
            "unittest: clamp - instant action moves to position",
            "test_clamp_instant_action_moves_to_position",
            "clamp instant action drives the fake motor to position/speed.",
        ),
        _unittest_method_diag(
            "unittest: clamp - order action handler",
            "test_order_clamp_action_uses_shared_hardware_handler",
            "Order clamp action shares the instant-action hardware handler.",
        ),
        _unittest_method_diag(
            "unittest: pio - scenario out/in/delay steps",
            "test_pio_scenario_executes_out_in_delay_steps",
            "pioScenario runs OUT/IN/DELAY steps against the fake PIO client.",
        ),
        _unittest_method_diag(
            "unittest: pio - scenario input timeout step",
            "test_pio_scenario_reports_input_timeout_step",
            "pioScenario reports an input-wait timeout step.",
        ),
        _unittest_method_diag(
            "unittest: pio - read in returns inputs 1-8",
            "test_pio_read_in_returns_inputs_one_through_eight",
            "pioReadIn returns input bits 1 through 8.",
        ),
        Diagnostic(
            "unittest: EZI IO / photo sensors",
            (
                "{py}",
                "-m",
                "unittest",
                _test_id("test_ezio_read_in_returns_all_inputs"),
                _test_id("test_ezio_input_read_retries_once_after_empty_response"),
                _test_id("test_photo_sensor_read_updates_state_loads_from_ezio"),
                _test_id("test_photo_sensor_read_clears_loads_when_all_sensors_are_off"),
                _test_id("test_photo_sensor_read_result_reports_six_sensor_values"),
                _test_id("test_manage_tray_slot_requests_state_publish_when_sensor_values_change"),
                _test_id("test_manage_tray_slot_reports_ezio_input_failure_in_state_errors"),
                "-v",
            ),
            "Focused fake-hardware EZI IO and tray photo-sensor tests.",
            requires_manual=True,
        ),
        Diagnostic(
            "pytest: EZI IO utilities",
            (
                "{py}",
                "-m",
                "pytest",
                "../tests/test_ezi_io_client.py",
                "../tests/test_ezi_io_input_test.py",
                "-q",
            ),
            "Runs EZI IO client and sensor formatter tests without touching hardware.",
            requires_manual=True,
        ),
        Diagnostic(
            "diagnostic: EZI IO input read",
            (
                "{py}",
                "../scripts/ezi_io_input_test.py",
                config.ezi_config.ezi_io,
                "--pins",
                sensor_pins,
            ),
            "Read-only hardware diagnostic: reads EZI IO inputs and tray sensors.",
            requires_manual=True,
        ),
    )


_JIBOT_INSTANT_ACTIONS = (
    InstantAction("stateRequest", "Request state", motion=False),
    InstantAction("factsheetRequest", "Request factsheet", motion=False),
    InstantAction("startPause", "Pause (startPause)", motion=True),
    InstantAction("stopPause", "Resume (stopPause)", motion=True),
    InstantAction("startCharging", "Start charging", motion=True),
    # stopCharging ends whichever charge mode is active (in-place release or
    # dock-charge UmStop). Absent from the factsheet agvActions (core.factsheet),
    # but the adapter handles it, so the WebUi may send it. motion=False: a stop
    # must never be confirm/busy-gated (operator must always be able to stop).
    InstantAction("stopCharging", "Stop charging", motion=False),
    InstantAction("cancelOrder", "Cancel order", motion=True),
    # clearErrors empties sticky state.errors on operator/FMS request (e.g. a
    # lingering JIBOT_GOTO_REJECTED). The adapter already handles it and the
    # factsheet advertises it; registering it here lets /action accept it and the
    # WebUi render a Clear-errors button. motion=False: clearing the error list
    # moves nothing, so it is never confirm/busy-gated.
    InstantAction("clearErrors", "Clear errors", motion=False),
    InstantAction("testSound", "Sound test", motion=False),
    InstantAction("stopSound", "Stop sound", motion=False),
    # Volume/mute carries a `volume` (0-100) or `mute` param from the WebUI's
    # Sound panel. Not a robot motion -> no confirm keystroke (motion=False).
    InstantAction("setSoundVolume", "Sound volume", motion=False),
    InstantAction("manualDrive", "Manual drive (jog)", motion=True),
    InstantAction("manualMove", "Manual move (distance)", motion=True),
    # WebUI test hook: run the configured dock/move motion_rule for a `to` node
    # (renders to/from inputs); reuses the order-worker orchestrators.
    InstantAction("jibotMotionRule", "Run motion rule (dock/move)", motion=True),
    InstantAction("manualStop", "Manual stop", motion=False),
    InstantAction("enableMotor", "Enable motor", motion=True),
    InstantAction("disableMotor", "Disable motor (test)", motion=True),
    # Drive to the nearest map node and set lastNodeId (off-graph re-anchor).
    # motion=True: the robot moves, so the WebUI requires a confirm keystroke
    # (enforced by a dedicated gate in _post_action).
    InstantAction("gotoNearestNode", "Go to nearest node (set lastNodeId)", motion=True),
    # Turn in place to an absolute heading. The map cannot express a per-order
    # arrival angle (every PathPoint on a surveyed map tends to carry 0.00), so
    # this is how ACS and the WebUI ask for one. Degrees, JIBOT's native unit —
    # the name says so because VDA5050 carries headings in radians elsewhere.
    InstantAction(
        "rotateTo",
        "Rotate in place (heading)",
        motion=True,
        parameters=(
            ActionParameterSpec(
                "thetaDeg",
                input_type="number",
                placeholder="목표 heading e.g. 90",
                label="heading (도)",
            ),
            ActionParameterSpec(
                "toleranceDeg",
                input_type="number",
                placeholder="기본 3",
                label="허용 오차 (도)",
            ),
            ActionParameterSpec(
                "timeoutSec",
                input_type="number",
                placeholder="기본 30",
                label="제한 시간 (초)",
            ),
        ),
    ),
    InstantAction(
        "localize",
        "Localize robot",
        motion=True,
        parameters=(
            ActionParameterSpec(
                "target",
                placeholder="goal, pose, or auto (inferred when blank)",
                choices=("", "goal", "pose", "auto"),
                label="기준 (비우면 자동)",
            ),
            ActionParameterSpec(
                "goal", placeholder="map node e.g. p2", label="맵 노드",
            ),
            # target=pose only. Takes the anchor pose from the map so a recipe
            # needs no mm coordinates that go stale on a re-survey. Goal/Dock
            # first, then PathPoint — a PathPoint gives position only and needs
            # theta below. x/y/theta override it field by field.
            ActionParameterSpec(
                "node",
                placeholder="맵 노드 이름 e.g. 2_01 또는 p2 (pose를 맵에서 읽음)",
                label="기준 노드 (pose 자동)",
            ),
            ActionParameterSpec(
                "x", input_type="number", placeholder="pose X", label="pose X",
            ),
            ActionParameterSpec(
                "y", input_type="number", placeholder="pose Y", label="pose Y",
            ),
            ActionParameterSpec(
                "theta", input_type="number", placeholder="pose theta",
                label="pose 방향(theta)",
            ),
            ActionParameterSpec(
                "mapId", placeholder="optional map id", label="맵 id (선택)",
            ),
        ),
    ),
)


EDGE_AGENT_ROOT = ADAPTER_ROOT.parents[1] / "dobot" / "edge-agent"


def _edge_tool(edge_root: Path, name: str) -> str:
    candidate = edge_root / ".venv" / "bin" / name
    if candidate.exists():
        return str(candidate)
    return name


def _edge_agent_diagnostics(edge_root: Path) -> Tuple[Diagnostic, ...]:
    if not edge_root.exists():
        return ()
    pytest = _edge_tool(edge_root, "pytest")
    ruff = _edge_tool(edge_root, "ruff")
    return (
        Diagnostic(
            "edge-agent: core/local API tests",
            (pytest, "tests/test_agent.py", "tests/test_local_api.py"),
        ),
        Diagnostic(
            "edge-agent: sparkplug connector tests",
            (pytest, "tests/test_sparkplug_connector.py"),
        ),
        Diagnostic("edge-agent: ruff", (ruff, "check", ".")),
    )


def make_edge_agent_spec(
    unit: EdgeAgentUnit,
    edge_root: Path = EDGE_AGENT_ROOT,
) -> AdaptorSpec:
    is_default = unit.unit == "edge-agent.service"
    key = "edge-agent" if is_default else f"edge-agent:{unit.id}"
    display = "Edge Agent" if is_default else f"Edge Agent {unit.id}"
    serial = "edge-agent" if is_default else unit.id
    return AdaptorSpec(
        key=key,
        display_name=display,
        unit=unit.unit,
        workdir=edge_root,
        exec_script="",
        manufacturer="dobot",
        serial=serial,
        vda_full_version="",
        topic_prefix="",
        runtime_kind="systemd",
        monitor_kind="none",
        diagnostics=_edge_agent_diagnostics(edge_root),
    )


def _jibot_spec(config, *, key: str, display_name: str, unit: str) -> AdaptorSpec:
    """Build one JIBOT spec from an already-resolved ``Config``.

    topic_prefix는 adaptor가 실제로 publish하는 것과 정확히 같아야 하므로,
    ``config``는 main.py와 같은 경로(get_config + 로봇 override)로 만든 것을
    넘긴다. ``--robot``으로 띄운 인스턴스의 systemd 유닛/MQTT 토픽과 1:1 매칭.

    mqtt_host/mqtt_port는 이 로봇의 broker로 채워 둔다 — 대시보드의 라이브
    모니터가 로봇마다 다른 broker에 붙을 수 있게 한다(없으면 config.toml broker).
    """
    from config.recipes import recipe_variables
    from core.action_modules import discover_action_modules

    serial = config.vehicle.serial_number
    disabled_action_types = {
        action.action_type
        for action in getattr(config, "actions", [])
        if not bool(getattr(action, "enabled", True))
    }
    builtin_action_types = {action.action_type for action in _JIBOT_INSTANT_ACTIONS}
    builtin_actions = tuple(
        action
        for action in _JIBOT_INSTANT_ACTIONS
        if action.action_type not in disabled_action_types
    )
    discovered_modules = discover_action_modules(config)
    module_views = tuple(
        ActionModuleView(
            module=module.module,
            title=module.title,
            actions=tuple(
                InstantAction(
                    spec.action_type,
                    spec.label or spec.action_type,
                    spec.motion,
                    spec.parameters,
                )
                for spec in module.specs
            ),
            panel_template=module.panel_template,
        )
        for module in discovered_modules
    )
    module_actions = tuple(
        action for module in module_views for action in module.actions
    )
    seen_action_types = builtin_action_types | {
        action.action_type for action in module_actions
    }
    configured_actions = []
    for action in getattr(config, "actions", []):
        if (
            bool(getattr(action, "enabled", True))
            and action.action_type not in seen_action_types
        ):
            configured_actions.append(
                InstantAction(action.action_type, action.action_type, motion=action.motion)
            )
            seen_action_types.add(action.action_type)
    recipe_views = tuple(
        RecipeView(
            action_type=recipe.action_type,
            label=recipe.label or recipe.action_type,
            motion=recipe.motion,
            variables=recipe_variables(recipe),
            step_count=len(recipe.steps),
            cleanup_count=len(recipe.cleanup),
        )
        for recipe in getattr(config, "recipes", ())
        if recipe.enabled and recipe.action_type not in seen_action_types
    )
    recipe_actions = tuple(
        InstantAction(
            view.action_type,
            view.label,
            motion=view.motion,
            parameters=tuple(ActionParameterSpec(name) for name in view.variables),
        )
        for view in recipe_views
    )
    return AdaptorSpec(
        key=key,
        display_name=display_name,
        unit=unit,
        workdir=ADAPTER_ROOT,
        exec_script="run-main.sh",
        manufacturer=config.vehicle.manufacturer or "jibot",
        serial=serial,
        vda_full_version=config.vehicle.vda_full_version,
        topic_prefix=_topic_prefix(
            config.mqtt_broker.vda_interface, config.vehicle.vda_version, serial
        ),
        mqtt_host=config.mqtt_broker.host,
        mqtt_port=config.mqtt_broker.port,
        vehicle_host=config.vehicle.vehicle_ip,
        vehicle_port=config.vehicle.vehicle_port,
        test_suites=_JIBOT_TEST_SUITES,
        diagnostics=(
            Diagnostic(
                "smoke test (simulator)",
                ("{py}", "main.py", "--simulator", "--vehicle-smoke-test"),
                "Connects to the local JIBOT simulator and prints a state snapshot.",
            ),
            Diagnostic(
                "smoke test (real vehicle)",
                ("{py}", "main.py", "--vehicle-smoke-test"),
                "Connects to the configured vehicle IP/port and prints a snapshot.",
            ),
            Diagnostic(
                "comms self-test",
                ("{py}", "../scripts/test-jibot-communication.py"),
                "Runs the fake-server + simulator communication checks.",
            ),
            *_jibot_hardware_diagnostics(config),
        ),
        instant_actions=(
            builtin_actions
            + module_actions
            + tuple(configured_actions)
            + recipe_actions
        ),
        action_modules=module_views,
        recipes=recipe_views,
    )


def _default_jibot_spec(config) -> AdaptorSpec:
    """Build the default single JIBOT spec from config.toml."""
    return _jibot_spec(
        config,
        key="jibot",
        display_name="JIBOT Adapter",
        unit="amr-adaptor.service",
    )


def _jibot_specs_from_fleet() -> List[AdaptorSpec]:
    """Build JIBOT specs from robots.hcl, matching amr-adaptor.service."""
    from config.config import get_config
    from config.fleet import DEFAULT_FLEET_PATH, load_fleet, resolve_robot_path, robot_overrides

    # load_fleet() below is called with no explicit path, so it (and thus
    # every robot dict it returns) is anchored at DEFAULT_FLEET_PATH. Resolve
    # each robot's "config" the same way main.py/web/server.py do -- against
    # the fleet file's parent, not the process CWD -- so a relative
    # `config = "robot-a.toml"` picks up the same file the WebUI validated.
    fleet_path = DEFAULT_FLEET_PATH
    robots = load_fleet(fleet_path)
    specs: List[AdaptorSpec] = []
    multi = len(robots) > 1
    for robot in robots:
        effective = get_config(
            config_path=resolve_robot_path(robot, "config", fleet_path),
            overrides=robot_overrides(robot),
            extensions_path=resolve_robot_path(robot, "extensions", fleet_path),
            recipes_path=resolve_robot_path(robot, "recipes", fleet_path),
        )
        rid = robot["id"]
        specs.append(
            _jibot_spec(
                effective,
                key=f"jibot:{rid}" if multi else "jibot",
                display_name=f"JIBOT {rid}" if multi else "JIBOT Adapter",
                unit="amr-adaptor.service",
            )
        )
    return specs


def _hexplorer_spec(config, *, key: str, display_name: str, unit: str) -> AdaptorSpec:
    """Build one Hexplorer spec from an already-resolved ``Config``."""
    serial = config.vehicle.serial_number
    prefix = _topic_prefix(
        config.mqtt_broker.vda_interface, config.vehicle.vda_version, serial
    )
    return AdaptorSpec(
        key=key,
        display_name=display_name,
        unit=unit,
        workdir=ADAPTER_ROOT,
        exec_script="run-hexplorer.sh",
        manufacturer="dobot",
        serial=serial,
        vda_full_version=config.vehicle.vda_full_version,
        topic_prefix=prefix,
        mqtt_host=config.mqtt_broker.host,
        mqtt_port=config.mqtt_broker.port,
        vehicle_host=config.vehicle.vehicle_ip,
        vehicle_port=config.vehicle.vehicle_port,
        test_suites=(
            Diagnostic(
                "unittest: hexplorer state",
                ("{py}", "-m", "unittest", "tests.test_adapter_hexplorer_state", "-v"),
            ),
        ),
        diagnostics=(),
        instant_actions=(
            InstantAction("getCameraInfo", "Get camera info", motion=False),
            InstantAction("stop", "Stop (zero velocity)", motion=True),
            InstantAction("standUp", "Stand up", motion=True),
            InstantAction("standDown", "Stand down", motion=True),
            InstantAction("walkMode", "Walk mode", motion=True),
        ),
    )


def build_registry(config) -> List[AdaptorSpec]:
    """Build adaptor specs from the host's configured adapters.

    config.toml의 [adapter] 구성을 반영한다:
      - [[adapter.instances]]가 있으면 인스턴스마다 amr-adaptor@<name>.service.
      - 없으면 [adapter].vendor가 hexplorer면 단일 hexplorer(amr-adaptor.service),
        그 외에는 robots.hcl 기반 JIBOT(amr-adaptor.service).
    edge-agent는 기존대로 discover해서 덧붙인다.
    """
    from config.config import get_config

    specs: List[AdaptorSpec] = []
    instances = config.adapter.instances

    if instances:
        for inst in instances:
            unit = f"amr-adaptor@{inst.name}.service"
            effective = get_config(config_path=inst.config) if inst.config else config
            if inst.vendor == "hexplorer":
                specs.append(
                    _hexplorer_spec(
                        effective,
                        key=f"hexplorer:{inst.name}",
                        display_name=f"Hexplorer {inst.name}",
                        unit=unit,
                    )
                )
            else:
                specs.append(
                    _jibot_spec(
                        effective,
                        key=f"jibot:{inst.name}",
                        display_name=f"JIBOT {inst.name}",
                        unit=unit,
                    )
                )
    elif config.adapter.vendor == "hexplorer":
        specs.append(
            _hexplorer_spec(
                config,
                key="hexplorer",
                display_name="Hexplorer Adapter",
                unit="amr-adaptor.service",
            )
        )
    else:
        specs.extend(_jibot_specs_from_fleet())

    edge_agents = [make_edge_agent_spec(unit) for unit in discover_edge_agent_units()]
    return [*specs, *edge_agents]
